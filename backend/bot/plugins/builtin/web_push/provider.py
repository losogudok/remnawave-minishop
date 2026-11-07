"""Core notification-channel provider backed by durable Web Push deliveries."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import sessionmaker

from bot.services.notification_channels import (
    ChannelOutcomeStatus,
    NotificationChannelOutcome,
    NotificationOccurrence,
)
from bot.services.user_notification_policy import (
    push_notifications_allowed,
    push_notifications_selected,
)

from .payload import normalize_push_url, push_tag_for, sanitize_push_content
from .store import WebPushStore, utc_milliseconds

logger = logging.getLogger(__name__)

PUSH_CHANNEL_NAME = "push"


def web_push_configured(settings: Any) -> bool:
    try:
        return bool(getattr(settings, "web_push_configured", False))
    except Exception:
        return False


class WebPushChannelProvider:
    """Persist a notification for each active device; the worker sends it.

    ``SENT`` means the occurrence is durably accepted for delivery, not that
    the browser displayed it. Actual endpoint outcomes are recorded separately
    by ``WebPushDeliveryWorker`` with retries and revocation.
    """

    name = PUSH_CHANNEL_NAME

    def __init__(self, settings: Any, session_factory: sessionmaker) -> None:
        self.settings = settings
        self.store = WebPushStore(settings, session_factory)

    def is_configured(self, settings: Any) -> bool:
        return web_push_configured(settings)

    def destinations_for_user(self, settings: Any, user: Any) -> tuple[str, ...]:
        """Compatibility fallback; fan-out uses ``resolve_destinations``."""

        del settings, user
        return ()

    async def resolve_destinations(self, settings: Any, user: Any) -> tuple[str, ...]:
        del settings
        user_id = int(getattr(user, "user_id", 0) or 0)
        if user_id <= 0:
            return ()
        if not push_notifications_allowed(user):
            return ()
        try:
            rows = await self.store.list_active_subscriptions(user_id)
        except Exception:
            logger.exception("Failed to load Web Push subscriptions for user %s.", user_id)
            return ()
        return tuple(str(row.id) for row in rows if row.id)

    async def deliver(
        self,
        *,
        settings: Any,
        user: Any,
        occurrence: NotificationOccurrence,
        destination: str,
    ) -> NotificationChannelOutcome:
        channel = PUSH_CHANNEL_NAME
        user_id = int(getattr(user, "user_id", 0) or 0)
        if user_id <= 0 or int(occurrence.user_id or 0) != user_id:
            return _outcome(channel, destination, ChannelOutcomeStatus.SKIPPED, "user_mismatch")
        if occurrence.sensitive or occurrence.admin_only:
            return _outcome(channel, destination, ChannelOutcomeStatus.SKIPPED, "ineligible")
        if not push_notifications_selected(settings, occurrence.category):
            return _outcome(channel, destination, ChannelOutcomeStatus.SKIPPED, "provider_disabled")
        if not push_notifications_allowed(user):
            return _outcome(
                channel,
                destination,
                ChannelOutcomeStatus.SKIPPED,
                "preference_disabled",
            )
        if not web_push_configured(settings):
            return _outcome(channel, destination, ChannelOutcomeStatus.SKIPPED, "not_configured")

        try:
            subscription = await self.store.find_subscription_by_id(str(destination))
        except Exception:
            logger.exception(
                "Failed to load Web Push subscription for user %s occurrence %s.",
                user_id,
                occurrence.occurrence_id,
            )
            return _outcome(channel, destination, ChannelOutcomeStatus.FAILED, "storage_error")
        if (
            subscription is None
            or int(subscription.user_id) != user_id
            or subscription.revoked_at is not None
        ):
            return _outcome(
                channel, destination, ChannelOutcomeStatus.SKIPPED, "subscription_revoked"
            )
        if subscription.expiration_time is not None and int(
            subscription.expiration_time
        ) <= utc_milliseconds(datetime.now(UTC)):
            return _outcome(
                channel, destination, ChannelOutcomeStatus.SKIPPED, "subscription_expired"
            )

        fallback_title = str(settings.WEBAPP_TITLE or "")
        title, body = sanitize_push_content(
            occurrence.push_title,
            occurrence.push_body,
            fallback_title=fallback_title,
        )
        url = normalize_push_url(occurrence.push_url, settings)
        try:
            queued = await self.store.enqueue_notification(
                user_id=user_id,
                subscription_id=str(subscription.id),
                occurrence_id=occurrence.occurrence_id,
                title=title,
                body=body,
                url=url,
                tag=push_tag_for(occurrence.occurrence_id),
                now=datetime.now(UTC),
            )
        except Exception:
            logger.exception(
                "Failed to enqueue Web Push delivery for user %s occurrence %s.",
                user_id,
                occurrence.occurrence_id,
            )
            return _outcome(channel, destination, ChannelOutcomeStatus.FAILED, "storage_error")
        return _outcome(
            channel,
            destination,
            ChannelOutcomeStatus.SENT,
            "queued" if queued else "duplicate",
        )


def _outcome(
    channel: str,
    destination: str,
    status: ChannelOutcomeStatus,
    reason: str,
) -> NotificationChannelOutcome:
    return NotificationChannelOutcome(
        channel=channel,
        destination=destination,
        status=status,
        reason=reason,
    )


__all__ = ["PUSH_CHANNEL_NAME", "WebPushChannelProvider", "web_push_configured"]
