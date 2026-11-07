"""Durable Web Push delivery worker with leases, retries and revocation."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from bot.services.user_notification_policy import push_notifications_allowed
from db.dal import user_dal
from db.web_push_models import WebPushDelivery, WebPushSubscription

from .callbacks import report_pending_broadcast_callbacks
from .locking import subscription_transaction
from .payload import encode_push_payload, normalize_push_url
from .provider import web_push_configured
from .sender import PushDeliveryStatus, PushSendResult, WebPushSender
from .store import (
    DELIVERY_FAILED,
    DELIVERY_QUEUED,
    DELIVERY_RUNNING,
    DELIVERY_SENT,
    DELIVERY_SKIPPED,
    ClaimedDelivery,
    WebPushStorageError,
    WebPushStore,
    utc_milliseconds,
)

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 2.0
DELIVERY_CONCURRENCY = 8


def _utc_now() -> datetime:
    return datetime.now(UTC)


class PushSender(Protocol):
    async def send(
        self,
        *,
        endpoint: str,
        subscription_keys: dict[str, str],
        payload: bytes,
        now: int | None = None,
    ) -> PushSendResult: ...

    async def close(self) -> None: ...


class WebPushDeliveryWorker:
    def __init__(
        self,
        settings: Any,
        session_factory: sessionmaker,
        *,
        sender: PushSender | None = None,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.store = WebPushStore(settings, session_factory)
        self.sender = sender or WebPushSender(settings)

    async def run(self) -> None:
        try:
            while True:
                try:
                    await self._tick()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Web Push delivery worker tick failed.")
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
        finally:
            await self.sender.close()

    async def _tick(self) -> None:
        # Finish Core callback reporting before claiming new sends. Terminal
        # rows are never claimed again, so this cannot resend the push.
        try:
            await report_pending_broadcast_callbacks(self.settings, self.session_factory)
        except Exception:
            logger.exception("Web Push broadcast callback outbox flush failed.")
        deliveries = await self.store.claim_due_deliveries(now=_utc_now())
        if not deliveries:
            return
        semaphore = asyncio.Semaphore(DELIVERY_CONCURRENCY)

        async def run_one(delivery: ClaimedDelivery) -> None:
            async with semaphore:
                try:
                    await self._deliver(delivery)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Web Push delivery %s failed unexpectedly.", delivery.id)
                    await self._retry_or_fail(delivery, "internal_error")

        await asyncio.gather(*(run_one(delivery) for delivery in deliveries))

    async def _deliver(self, delivery: ClaimedDelivery) -> None:
        # Discovery is advisory only: no object loaded here authorizes a send.
        await self._load_context(delivery)
        async with subscription_transaction(
            self.session_factory, delivery.subscription_id
        ) as guard:
            session = guard.session
            row = await session.scalar(
                select(WebPushDelivery)
                .where(
                    WebPushDelivery.id == delivery.id,
                    WebPushDelivery.state == DELIVERY_RUNNING,
                    WebPushDelivery.lease_token == delivery.lease_token,
                )
                .with_for_update()
            )
            if row is None:
                return
            subscription = await session.get(WebPushSubscription, delivery.subscription_id)
            user = await user_dal.get_user_by_id(session, int(row.user_id))
            reason = self._authorization_failure(row, subscription, user)
            if reason:
                self._finish(row, DELIVERY_SKIPPED, reason)
                return
            assert subscription is not None
            if subscription.expiration_time is not None and int(
                subscription.expiration_time
            ) <= utc_milliseconds(_utc_now()):
                await self.store.revoke_subscription_id(
                    str(subscription.id), reason="subscription_expired", session=session
                )
                return
            await self._send_authorized(session, row, subscription)

    def _authorization_failure(
        self, delivery: WebPushDelivery, subscription: WebPushSubscription | None, user: Any
    ) -> str:
        if subscription is None:
            return "subscription_missing"
        if subscription.revoked_at is not None:
            return "subscription_revoked"
        if int(subscription.user_id) != int(delivery.user_id):
            return "account_mismatch"
        if user is None:
            return "user_inactive"
        if bool(getattr(user, "is_banned", False)):
            return "user_banned"
        if not push_notifications_allowed(user):
            return "preference_disabled"
        if not bool(self.settings.USER_NOTIFICATION_PUSH_ENABLED) or not web_push_configured(
            self.settings
        ):
            return "provider_disabled"
        return ""

    async def _send_authorized(
        self, session: AsyncSession, row: WebPushDelivery, subscription: WebPushSubscription
    ) -> None:
        try:
            endpoint = self.store.decrypt_endpoint(subscription.endpoint_ciphertext)
            keys = self.store.decrypt_keys(subscription.keys_ciphertext)
        except (WebPushStorageError, UnicodeDecodeError):
            logger.warning(
                "Web Push subscription %s cannot be decrypted; revoking it.", subscription.id
            )
            await self.store.revoke_subscription_id(
                str(subscription.id), reason="storage_key_rotated", session=session
            )
            return
        payload = encode_push_payload(
            _PayloadView(
                title=row.title,
                body=row.body,
                url=normalize_push_url(row.url, self.settings),
                tag=row.tag,
            )
        )
        # The same transaction/lock is held until HTTP and result persistence finish.
        result = await self.sender.send(endpoint=endpoint, subscription_keys=keys, payload=payload)
        if result.status == PushDeliveryStatus.SENT:
            self._finish(row, DELIVERY_SENT)
            row.sent_at = _utc_now()
            subscription.last_seen_at = _utc_now()
        elif result.status == PushDeliveryStatus.GONE:
            await self.store.revoke_subscription_id(
                str(subscription.id),
                reason="endpoint_gone",
                exclude_delivery_id=str(row.id),
                session=session,
            )
            self._finish(row, DELIVERY_FAILED, "endpoint_gone")
        elif result.status == PushDeliveryStatus.TERMINAL:
            self._finish(row, DELIVERY_FAILED, result.error_code or "delivery_rejected")
        elif row.attempts >= row.max_attempts:
            self._finish(row, DELIVERY_FAILED, result.error_code or "transient_error")
        else:
            self._finish(row, DELIVERY_QUEUED)
            row.next_attempt_at = _utc_now() + timedelta(
                seconds=min(2 ** max(1, int(row.attempts)), 60 * 60)
            )

    @staticmethod
    def _finish(row: WebPushDelivery, state: str, reason: str = "") -> None:
        row.state = state
        row.error_code = reason[:64] or None
        row.lease_token = None
        row.lease_until = None
        row.updated_at = _utc_now()

    async def _load_context(
        self, delivery: ClaimedDelivery
    ) -> tuple[WebPushSubscription | None, Any | None]:
        async with self.session_factory() as session:
            subscription = (
                await session.execute(
                    select(WebPushSubscription).where(
                        WebPushSubscription.id == delivery.subscription_id
                    )
                )
            ).scalar_one_or_none()
            user = await user_dal.get_user_by_id(session, int(delivery.user_id))
            return subscription, user

    async def _skip(self, delivery: ClaimedDelivery, reason: str) -> None:
        await self.store.mark_skipped(delivery.id, delivery.lease_token, reason)

    async def _mark_failed(self, delivery: ClaimedDelivery, reason: str) -> None:
        await self.store.mark_failed(delivery.id, delivery.lease_token, reason)

    async def _retry_or_fail(self, delivery: ClaimedDelivery, reason: str) -> None:
        if delivery.attempts >= delivery.max_attempts:
            await self._mark_failed(delivery, reason)
            return
        await self.store.reschedule(
            delivery.id,
            delivery.lease_token,
            attempts=delivery.attempts,
        )


class _PayloadView:
    title: str
    body: str
    url: str
    tag: str | None

    def __init__(self, *, title: str, body: str, url: str, tag: str | None) -> None:
        self.title = title
        self.body = body
        self.url = url
        self.tag = tag


__all__ = ["POLL_INTERVAL_SECONDS", "WebPushDeliveryWorker"]
