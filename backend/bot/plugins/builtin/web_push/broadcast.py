"""Web Push delivery channel for admin broadcasts (ONB-09).

The provider resolves every active browser subscription of the selected
audience as its own delivery destination, converts the rendered Telegram HTML
into the compact plain-text payload a service worker can display, and writes a
durable row through :func:`enqueue_broadcast_push`. No HTTP request is made
here: the plugin worker owns endpoint delivery, retries and revocation.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import or_, select

from bot.services.broadcast_channels import (
    BroadcastChannelDescriptor,
    BroadcastChannelDispatch,
    BroadcastChannelMessage,
    BroadcastChannelProvider,
    BroadcastDispatchContext,
    BroadcastRecipient,
)
from db.models import User
from db.web_push_models import WebPushSubscription

from .payload import PUSH_BODY_MAX_LENGTH, PUSH_TITLE_MAX_LENGTH, normalize_push_url
from .provider import web_push_configured
from .store import WebPushStore, utc_milliseconds

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import sessionmaker

    from config.settings import Settings

logger = logging.getLogger(__name__)

PUSH_CHANNEL_NAME = "push"
# The service worker accepts a 120-character title and a 200-character body.
# The composer treats their sum as the plain-text budget for this channel.
PUSH_TEXT_MAX_LENGTH = PUSH_TITLE_MAX_LENGTH + PUSH_BODY_MAX_LENGTH

_BLOCK_BREAK_RE = re.compile(r"(?i)<br\s*/?>|</(?:p|div|blockquote|pre|li|h[1-6])\s*>")
_TAG_RE = re.compile(r"<[^>]*>")
_TRAILING_SPACE_RE = re.compile(r"[ \t]+$")

_BROADCAST_SESSION_FACTORY: sessionmaker | None = None


def configure_broadcast_session_factory(session_factory: sessionmaker) -> None:
    """Remember the Core session factory used by broadcast callbacks.

    Revocation and account-reassignment paths can flush the Core callback
    outbox without having a request-scoped session factory of their own.
    """

    global _BROADCAST_SESSION_FACTORY
    _BROADCAST_SESSION_FACTORY = session_factory


def get_broadcast_session_factory() -> sessionmaker | None:
    """Return the factory configured by :class:`WebPushPlugin`."""

    return _BROADCAST_SESSION_FACTORY


def plaintext_for_push_broadcast(value: str | None) -> str:
    """Strip Telegram HTML while keeping the author's paragraph breaks."""

    text = str(value or "")
    text = _BLOCK_BREAK_RE.sub("\n", text)
    text = html.unescape(_TAG_RE.sub("", text))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_TRAILING_SPACE_RE.sub("", line) for line in text.split("\n")]
    # Keep a leading blank line: the first-line title rule must then fall back
    # to the app name instead of silently promoting the next paragraph.
    return "\n".join(lines).rstrip("\n")


def split_broadcast_push_text(value: str | None, fallback_title: str) -> tuple[str, str]:
    """Split plain text into the first-line title and the remaining body.

    A first line longer than the service-worker title limit cannot be shown as
    a title: the app name becomes the title and the whole text becomes the body.
    The same rule applies when the text begins with an empty line. Truncation to
    the final 120/200 limits is applied again by the durable store.
    """

    plain = plaintext_for_push_broadcast(value)
    if not plain.strip():
        return fallback_title, ""
    lines = plain.split("\n")
    first_line = lines[0].strip()
    if not first_line or len(first_line) > PUSH_TITLE_MAX_LENGTH:
        return fallback_title, plain.strip()
    return first_line, "\n".join(lines[1:]).strip()


def push_click_url(message: BroadcastChannelMessage, settings: Settings) -> str:
    """Return the first URL button target, normalized to a safe same-origin path."""

    for button in message.buttons:
        if button.kind == "url":
            return normalize_push_url(button.url, settings)
    return "/"


class WebPushBroadcastChannelProvider(BroadcastChannelProvider):
    """Endpoint-level broadcast provider backed by durable Web Push rows."""

    name = PUSH_CHANNEL_NAME

    def __init__(self, settings: Settings, session_factory: sessionmaker) -> None:
        self.settings = settings
        self.session_factory = session_factory

    def describe(self, settings: Settings) -> BroadcastChannelDescriptor:
        enabled = bool(settings.WEB_PUSH_ENABLED)
        configured = web_push_configured(settings)
        kill_switch = bool(settings.USER_NOTIFICATION_PUSH_ENABLED)
        if not enabled:
            reason = "push_disabled"
        elif not configured:
            reason = "push_not_configured"
        elif not kill_switch:
            reason = "user_notification_push_disabled"
        else:
            reason = ""
        return BroadcastChannelDescriptor(
            name=self.name,
            label_key="broadcast_channel_push",
            fallback_label="Web Push",
            order=30,
            available=not reason,
            reason=reason,
            supports_html=False,
            supports_subject=False,
            supports_image=False,
            supports_buttons=True,
            max_text_length=PUSH_TEXT_MAX_LENGTH,
            supports_push_preview=True,
            destination_level="endpoint",
        )

    async def resolve_recipients(
        self,
        session: AsyncSession,
        *,
        user_ids: list[int],
        exclude_blocked_telegram: bool,
    ) -> list[BroadcastRecipient]:
        del exclude_blocked_telegram
        if not user_ids:
            return []

        now_ms = utc_milliseconds(datetime.now(UTC))
        normalized_user_ids = [int(user_id) for user_id in dict.fromkeys(user_ids)]
        recipients: list[BroadcastRecipient] = []
        chunk_size = 900
        for start in range(0, len(normalized_user_ids), chunk_size):
            chunk = normalized_user_ids[start : start + chunk_size]
            rows = (
                await session.execute(
                    select(
                        WebPushSubscription.id,
                        WebPushSubscription.user_id,
                        User.language_code,
                        User.is_banned,
                        User.push_notifications_enabled,
                    )
                    .join(User, User.user_id == WebPushSubscription.user_id)
                    .where(
                        WebPushSubscription.user_id.in_(chunk),
                        WebPushSubscription.revoked_at.is_(None),
                        or_(
                            WebPushSubscription.expiration_time.is_(None),
                            WebPushSubscription.expiration_time > now_ms,
                        ),
                        User.is_banned.is_(False),
                        User.push_notifications_enabled.is_(True),
                    )
                    .order_by(WebPushSubscription.user_id, WebPushSubscription.id)
                )
            ).all()
            for subscription_id, user_id, language_code, is_banned, push_enabled in rows:
                if bool(is_banned) or not bool(push_enabled):
                    continue
                recipients.append(
                    BroadcastRecipient(
                        user_id=int(user_id),
                        destination=str(subscription_id),
                        destination_key=str(subscription_id),
                        language_code=str(language_code) if language_code else None,
                    )
                )
        recipients.sort(key=lambda recipient: (recipient.user_id, recipient.destination_key))
        return recipients

    async def dispatch(self, context: BroadcastDispatchContext) -> BroadcastChannelDispatch:
        store = WebPushStore(context.settings, context.session_factory)
        fallback_title = str(context.settings.WEBAPP_TITLE or "")
        broadcast_id = int(context.broadcast.broadcast_id)
        queued = 0
        failed = 0
        seen_deliveries: set[int] = set()

        for message in context.messages:
            if message.delivery_id in seen_deliveries:
                continue
            seen_deliveries.add(message.delivery_id)
            title, body = split_broadcast_push_text(message.text, fallback_title)
            url = push_click_url(message, context.settings)
            try:
                await store.enqueue_broadcast_push(
                    broadcast_id=broadcast_id,
                    broadcast_delivery_id=message.delivery_id,
                    user_id=message.user_id,
                    subscription_id=message.destination,
                    title=title,
                    body=body,
                    url=url,
                )
            except Exception:
                logger.exception(
                    "Failed to enqueue Web Push broadcast delivery %s.", message.delivery_id
                )
                failed += 1
                # Persist the terminal failure before the immediate Core
                # update: if that update also fails, the callback outbox still
                # finishes the delivery row without sending anything.
                try:
                    await store.record_broadcast_failure(
                        broadcast_id=broadcast_id,
                        broadcast_delivery_id=message.delivery_id,
                        user_id=message.user_id,
                        error_code="enqueue_failed",
                    )
                except Exception:
                    logger.exception(
                        "Failed to persist enqueue failure for Web Push broadcast delivery %s.",
                        message.delivery_id,
                    )
                try:
                    await context.on_result(message.delivery_id, False, "enqueue_failed")
                except Exception:
                    # A Core status write must not abort the remaining devices.
                    logger.exception(
                        "Failed to record enqueue failure for Web Push broadcast delivery %s.",
                        message.delivery_id,
                    )
                continue
            # The unique broadcast_delivery_id makes a repeated enqueue safe;
            # one persisted Core delivery row is still exactly one push device.
            queued += 1
            try:
                await context.on_queued(message.delivery_id)
            except Exception:
                # The push is already durable. Leave the Core row for the
                # worker callback outbox, which reports the terminal result;
                # one status-write failure must not drop later devices.
                logger.exception(
                    "Failed to record queued Web Push broadcast delivery %s.",
                    message.delivery_id,
                )

        return BroadcastChannelDispatch(queued=queued, failed=failed)


__all__ = [
    "PUSH_CHANNEL_NAME",
    "PUSH_TEXT_MAX_LENGTH",
    "WebPushBroadcastChannelProvider",
    "configure_broadcast_session_factory",
    "get_broadcast_session_factory",
    "plaintext_for_push_broadcast",
    "push_click_url",
    "split_broadcast_push_text",
]
