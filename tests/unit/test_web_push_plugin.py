"""Provider, routes, storage and worker tests for the built-in web_push plugin."""

from __future__ import annotations

import asyncio
import json
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.plugins.builtin.web_push.crypto import b64url_encode, public_key_bytes
from bot.plugins.builtin.web_push.payload import (
    build_push_payload,
    normalize_push_url,
    sanitize_push_content,
)
from bot.plugins.builtin.web_push.provider import WebPushChannelProvider
from bot.plugins.builtin.web_push.sender import PushDeliveryStatus, PushSendResult
from bot.plugins.builtin.web_push.store import (
    DELIVERY_QUEUED,
    DELIVERY_SENT,
    DELIVERY_SKIPPED,
    WebPushStore,
    enqueue_broadcast_push,
)
from bot.plugins.builtin.web_push.worker import WebPushDeliveryWorker
from bot.services.notification_channels import (
    ChannelOutcomeStatus,
    NotificationOccurrence,
)
from bot.services.user_notification_policy import UserNotificationCategory
from db.base import Base
from db.broadcast_models import AdminBroadcast, AdminBroadcastDelivery
from db.models import User
from db.web_push_models import WebPushDelivery, WebPushSubscription

ENDPOINT = "https://fcm.googleapis.com/fcm/send/device-token-1"
SECOND_ENDPOINT = "https://updates.push.services.mozilla.com/wpush/v2/device-token-2"
APPLICATION_SERVER_KEYS = {
    "p256dh": (
        "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
    ),
    "auth": "BTBZMqHH6r4Tts7J_aSIgg",
}


def make_settings(**overrides: Any) -> Any:
    private = ec.generate_private_key(ec.SECP256R1())
    values: dict[str, Any] = {
        "WEB_PUSH_ENABLED": True,
        "WEB_PUSH_VAPID_PUBLIC_KEY": b64url_encode(public_key_bytes(private)),
        "WEB_PUSH_VAPID_PRIVATE_KEY": SecretStr(
            b64url_encode(private.private_numbers().private_value.to_bytes(32, "big"))
        ),
        "WEB_PUSH_VAPID_SUBJECT": "mailto:ops@example.com",
        "WEB_PUSH_DELIVERY_TTL_SECONDS": 3600,
        "WEBAPP_TITLE": "Minishop",
        "WEBAPP_ENABLED": True,
        "PWA_ENABLED": True,
        "SUBSCRIPTION_MINI_APP_URL": "https://app.example.test/",
        "USER_NOTIFICATION_PUSH_ENABLED": True,
        "USER_NOTIFICATION_PREFERENCES_ENABLED": True,
        "web_push_configured": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_occurrence(**overrides: Any) -> NotificationOccurrence:
    values: dict[str, Any] = {
        "occurrence_id": "payment-succeeded:1",
        "category": UserNotificationCategory.PAYMENTS,
        "user_id": 1,
        "push_title": "Payment received",
        "push_body": "Your subscription is active.",
        "push_url": "/devices?tab=1",
    }
    values.update(overrides)
    return NotificationOccurrence(**values)


class FakeSender:
    def __init__(self, result: PushSendResult) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    async def send(
        self,
        *,
        endpoint: str,
        subscription_keys: dict[str, str],
        payload: bytes,
        now: int | None = None,
    ) -> PushSendResult:
        self.calls.append(
            {
                "endpoint": endpoint,
                "keys": subscription_keys,
                "payload": payload,
                "now": now,
            }
        )
        return self.result

    async def close(self) -> None:
        self.closed = True


class WebPushPluginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.settings = make_settings()
        self.store = WebPushStore(self.settings, self.factory)
        async with self.factory() as session:
            session.add_all([User(user_id=1), User(user_id=2)])
            await session.commit()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def add_subscription(
        self,
        user_id: int = 1,
        endpoint: str = ENDPOINT,
        *,
        label: str = "Phone",
    ) -> str:
        record, _outcome, _skipped = await self.store.upsert_subscription(
            user_id=user_id,
            endpoint=endpoint,
            subscription_keys=APPLICATION_SERVER_KEYS,
            label=label,
            user_agent="Test Browser",
        )
        return str(record.id)

    async def test_provider_async_destinations_and_sync_compatibility(self) -> None:
        subscription_id = await self.add_subscription()
        provider = WebPushChannelProvider(self.settings, self.factory)
        assert provider.name == "push"
        assert provider.destinations_for_user(self.settings, SimpleNamespace(user_id=1)) == ()

        destinations = await provider.resolve_destinations(
            self.settings, SimpleNamespace(user_id=1, push_notifications_enabled=True)
        )
        assert destinations == (subscription_id,)
        opted_out = await provider.resolve_destinations(
            self.settings, SimpleNamespace(user_id=1, push_notifications_enabled=False)
        )
        assert opted_out == ()

    async def test_reassignment_after_context_load_prevents_http(self) -> None:
        subscription_id = await self.add_subscription()
        await self.store.enqueue_notification(
            user_id=1,
            subscription_id=subscription_id,
            occurrence_id="ownership-race",
            title="Private A",
            body="Private A",
            url="/",
            tag=None,
        )
        sender = FakeSender(PushSendResult(PushDeliveryStatus.SENT))
        worker = WebPushDeliveryWorker(self.settings, self.factory, sender=sender)
        original = worker._load_context

        async def load_then_reassign(delivery):
            context = await original(delivery)
            await self.add_subscription(user_id=2)
            return context

        with patch.object(worker, "_load_context", load_then_reassign):
            await worker._tick()
        self.assertEqual(sender.calls, [])
        async with self.factory() as session:
            row = (await session.execute(select(WebPushDelivery))).scalar_one()
        self.assertEqual(row.state, DELIVERY_SKIPPED)
        self.assertEqual(row.error_code, "account_reassigned")

    async def test_send_holds_lock_against_reassignment_and_revocation(self) -> None:
        for action in ("reassign", "revoke"):
            await self._assert_send_lock(action)

    async def _assert_send_lock(self, action: str) -> None:
        endpoint = ENDPOINT + action
        subscription_id = await self.add_subscription(endpoint=endpoint)
        await self.store.enqueue_notification(
            user_id=1,
            subscription_id=subscription_id,
            occurrence_id=action,
            title="A",
            body="Private",
            url="/",
            tag=None,
        )
        entered = asyncio.Event()
        release = asyncio.Event()
        sender = FakeSender(PushSendResult(PushDeliveryStatus.SENT))
        original = sender.send

        async def blocked_send(**kwargs):
            entered.set()
            await release.wait()
            return await original(**kwargs)

        worker = WebPushDeliveryWorker(self.settings, self.factory, sender=sender)
        with patch.object(sender, "send", blocked_send):
            sending = asyncio.create_task(worker._tick())
            await asyncio.wait_for(entered.wait(), 2)
            changing = asyncio.create_task(
                self.add_subscription(user_id=2, endpoint=endpoint)
                if action == "reassign"
                else self.store.revoke_subscription(user_id=1, device_id=subscription_id)
            )
            try:
                with self.assertRaises(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(changing), 0.05)
            finally:
                release.set()
                await asyncio.gather(sending, changing)
        self.assertEqual(len(sender.calls), 1)

    async def test_waiting_enrollment_waits_for_worker_subscription_lock(self) -> None:
        from contextlib import asynccontextmanager

        from bot.plugins.builtin.web_push import store as store_module

        sender = FakeSender(PushSendResult(PushDeliveryStatus.SENT))
        original_send = sender.send
        entered = asyncio.Event()
        release_send = asyncio.Event()
        waiter_started = asyncio.Event()
        release_waiter = asyncio.Event()

        async def blocked_send(**kwargs):
            entered.set()
            await release_send.wait()
            return await original_send(**kwargs)

        original_transaction = store_module.subscription_transaction

        @asynccontextmanager
        async def delayed_transaction(factory, key):
            current = asyncio.current_task()
            if (
                current is not None
                and current.get_name() == "enrollment-waiter"
                and str(key).startswith("endpoint:")
            ):
                waiter_started.set()
                await release_waiter.wait()
            async with original_transaction(factory, key) as guard:
                yield guard

        async def enroll_as_b():
            return await self.store.upsert_subscription(
                user_id=2,
                endpoint=ENDPOINT,
                subscription_keys=APPLICATION_SERVER_KEYS,
                label="B",
            )

        with (
            patch.object(store_module, "subscription_transaction", delayed_transaction),
            patch.object(sender, "send", blocked_send),
        ):
            enrollment = asyncio.create_task(enroll_as_b(), name="enrollment-waiter")
            await asyncio.wait_for(waiter_started.wait(), 2)
            # The row is created while the enrollment request is still queued.
            subscription_id = await self.add_subscription(user_id=1)
            await self.store.enqueue_notification(
                user_id=1,
                subscription_id=subscription_id,
                occurrence_id="enrollment-lock-order",
                title="A",
                body="Private",
                url="/",
                tag=None,
            )
            worker = WebPushDeliveryWorker(self.settings, self.factory, sender=sender)
            sending = asyncio.create_task(worker._tick())
            await asyncio.wait_for(entered.wait(), 2)
            release_waiter.set()
            try:
                with self.assertRaises(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(enrollment), 0.05)
                async with self.factory() as session:
                    stored = (
                        await session.execute(
                            select(WebPushSubscription).where(
                                WebPushSubscription.id == subscription_id
                            )
                        )
                    ).scalar_one()
                    delivery = (await session.execute(select(WebPushDelivery))).scalar_one()
                # The waiter holds the endpoint lock but must still wait for
                # the subscription-id lock held through the HTTP send.
                self.assertEqual(stored.user_id, 1)
                self.assertEqual(delivery.user_id, 1)
                self.assertEqual(delivery.state, "running")
            finally:
                release_send.set()
                await asyncio.gather(sending, enrollment)
        async with self.factory() as session:
            stored = (
                await session.execute(
                    select(WebPushSubscription).where(WebPushSubscription.id == subscription_id)
                )
            ).scalar_one()
            delivery = (await session.execute(select(WebPushDelivery))).scalar_one()
        self.assertEqual(stored.user_id, 2)
        self.assertEqual(delivery.state, DELIVERY_SENT)
        self.assertEqual(len(sender.calls), 1)

    async def test_eight_unrelated_subscriptions_send_concurrently(self) -> None:
        for index in range(8):
            subscription_id = await self.add_subscription(endpoint=f"{ENDPOINT}-{index}")
            await self.store.enqueue_notification(
                user_id=1,
                subscription_id=subscription_id,
                occurrence_id=f"concurrent-{index}",
                title="A",
                body="Body",
                url="/",
                tag=None,
            )
        entered = asyncio.Event()
        count = 0
        sender = FakeSender(PushSendResult(PushDeliveryStatus.SENT))
        original = sender.send

        async def barrier_send(**kwargs):
            nonlocal count
            count += 1
            if count == 8:
                entered.set()
            await asyncio.wait_for(entered.wait(), 2)
            return await original(**kwargs)

        worker = WebPushDeliveryWorker(self.settings, self.factory, sender=sender)
        with patch.object(sender, "send", barrier_send):
            await worker._tick()
        self.assertEqual(count, 8)
        self.assertEqual(len(sender.calls), 8)

    async def test_provider_enqueues_sanitized_payload_once(self) -> None:
        subscription_id = await self.add_subscription()
        provider = WebPushChannelProvider(self.settings, self.factory)
        user = SimpleNamespace(user_id=1, push_notifications_enabled=True)
        occurrence = make_occurrence()
        first = await provider.deliver(
            settings=self.settings,
            user=user,
            occurrence=occurrence,
            destination=subscription_id,
        )
        second = await provider.deliver(
            settings=self.settings,
            user=user,
            occurrence=occurrence,
            destination=subscription_id,
        )
        self.assertEqual(first.status, ChannelOutcomeStatus.SENT)
        self.assertEqual(second.status, ChannelOutcomeStatus.SENT)
        self.assertEqual(second.reason, "duplicate")
        async with self.factory() as session:
            rows = list((await session.execute(select(WebPushDelivery))).scalars())
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.url, "/devices?tab=1")
        self.assertEqual(row.occurrence_id, occurrence.occurrence_id)
        self.assertLessEqual(len(row.title), 120)
        self.assertLessEqual(len(row.body), 200)
        payload = build_push_payload(row)
        self.assertEqual(payload["title"], row.title)
        self.assertEqual(payload["url"], "/devices?tab=1")
        self.assertNotIn("token", json.dumps(payload))

    async def test_provider_rechecks_ownership_and_preferences(self) -> None:
        subscription_id = await self.add_subscription(user_id=1)
        provider = WebPushChannelProvider(self.settings, self.factory)
        outcome = await provider.deliver(
            settings=self.settings,
            user=SimpleNamespace(user_id=2, push_notifications_enabled=True),
            occurrence=make_occurrence(user_id=1),
            destination=subscription_id,
        )
        self.assertEqual(outcome.reason, "user_mismatch")
        outcome = await provider.deliver(
            settings=self.settings,
            user=SimpleNamespace(user_id=2, push_notifications_enabled=True),
            occurrence=make_occurrence(user_id=2),
            destination=subscription_id,
        )
        self.assertEqual(outcome.reason, "subscription_revoked")
        outcome = await provider.deliver(
            settings=self.settings,
            user=SimpleNamespace(user_id=1, push_notifications_enabled=False),
            occurrence=make_occurrence(),
            destination=subscription_id,
        )
        self.assertEqual(outcome.reason, "preference_disabled")
        async with self.factory() as session:
            count = len(list((await session.execute(select(WebPushDelivery))).scalars()))
        self.assertEqual(count, 0)

    async def test_account_switch_reassigns_endpoint_and_skips_old_rows(self) -> None:
        old_subscription = await self.add_subscription(user_id=1)
        await self.store.enqueue_notification(
            user_id=1,
            subscription_id=old_subscription,
            occurrence_id="old:1",
            title="Old",
            body="Old account",
            url="/",
            tag=None,
        )
        record, outcome, skipped = await self.store.upsert_subscription(
            user_id=2,
            endpoint=ENDPOINT,
            subscription_keys=APPLICATION_SERVER_KEYS,
            label="New account",
        )
        self.assertEqual(outcome, "reassigned")
        self.assertEqual(record.id, old_subscription)
        self.assertEqual(skipped, ())
        async with self.factory() as session:
            subscription = (
                await session.execute(
                    select(WebPushSubscription).where(WebPushSubscription.id == old_subscription)
                )
            ).scalar_one()
            delivery = (await session.execute(select(WebPushDelivery))).scalar_one()
        self.assertEqual(subscription.user_id, 2)
        self.assertEqual(delivery.state, DELIVERY_SKIPPED)
        self.assertEqual(delivery.error_code, "account_reassigned")

    async def test_revoke_requires_owner_and_preserves_other_devices(self) -> None:
        first = await self.add_subscription(user_id=1, endpoint=ENDPOINT)
        second = await self.add_subscription(user_id=1, endpoint=SECOND_ENDPOINT, label="Laptop")
        wrong = await self.store.revoke_subscription(user_id=2, device_id=first)
        self.assertFalse(wrong.found)
        revoked = await self.store.revoke_subscription(user_id=1, endpoint=ENDPOINT)
        self.assertTrue(revoked.found)
        self.assertEqual(revoked.device_id, first)
        rows = await self.store.list_subscriptions(1)
        self.assertEqual([row.id for row in rows], [second])
        self.assertNotEqual(first, second)

    async def test_worker_success_marks_sent_and_touches_subscription(self) -> None:
        subscription_id = await self.add_subscription()
        await self.store.enqueue_notification(
            user_id=1,
            subscription_id=subscription_id,
            occurrence_id="worker-success:1",
            title="Hello",
            body="Body",
            url="/",
            tag="tag",
        )
        sender = FakeSender(PushSendResult(PushDeliveryStatus.SENT))
        worker = WebPushDeliveryWorker(self.settings, self.factory, sender=sender)
        await worker._tick()
        self.assertEqual(len(sender.calls), 1)
        self.assertEqual(sender.calls[0]["endpoint"], ENDPOINT)
        self.assertEqual(sender.calls[0]["keys"], APPLICATION_SERVER_KEYS)
        async with self.factory() as session:
            delivery = (await session.execute(select(WebPushDelivery))).scalar_one()
            subscription = (
                await session.execute(
                    select(WebPushSubscription).where(WebPushSubscription.id == subscription_id)
                )
            ).scalar_one()
        self.assertEqual(delivery.state, DELIVERY_SENT)
        self.assertIsNotNone(delivery.sent_at)
        self.assertGreaterEqual(subscription.last_seen_at, delivery.created_at)

    async def test_worker_gone_revokes_subscription(self) -> None:
        subscription_id = await self.add_subscription()
        await self.store.enqueue_notification(
            user_id=1,
            subscription_id=subscription_id,
            occurrence_id="worker-gone:1",
            title="Hello",
            body="Body",
            url="/",
            tag=None,
        )
        sender = FakeSender(PushSendResult(PushDeliveryStatus.GONE, "endpoint_gone"))
        worker = WebPushDeliveryWorker(self.settings, self.factory, sender=sender)
        await worker._tick()
        async with self.factory() as session:
            delivery = (await session.execute(select(WebPushDelivery))).scalar_one()
            subscription = (
                await session.execute(
                    select(WebPushSubscription).where(WebPushSubscription.id == subscription_id)
                )
            ).scalar_one()
        self.assertEqual(delivery.state, "failed")
        self.assertEqual(delivery.error_code, "endpoint_gone")
        self.assertIsNotNone(subscription.revoked_at)

    async def test_worker_transient_retry_then_success(self) -> None:
        subscription_id = await self.add_subscription()
        await self.store.enqueue_notification(
            user_id=1,
            subscription_id=subscription_id,
            occurrence_id="worker-retry:1",
            title="Hello",
            body="Body",
            url="/",
            tag=None,
        )
        sender = FakeSender(PushSendResult(PushDeliveryStatus.RETRY, "network_error"))
        worker = WebPushDeliveryWorker(self.settings, self.factory, sender=sender)
        await worker._tick()
        async with self.factory() as session:
            delivery = (await session.execute(select(WebPushDelivery))).scalar_one()
            self.assertEqual(delivery.state, DELIVERY_QUEUED)
            self.assertEqual(delivery.attempts, 1)
            delivery.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()
        sender.result = PushSendResult(PushDeliveryStatus.SENT)
        await worker._tick()
        async with self.factory() as session:
            delivery = (await session.execute(select(WebPushDelivery))).scalar_one()
        self.assertEqual(delivery.state, DELIVERY_SENT)
        self.assertEqual(delivery.attempts, 2)
        self.assertEqual(len(sender.calls), 2)

    async def test_expired_subscriptions_are_excluded_and_not_delivered(self) -> None:
        yesterday_ms = int((datetime.now(UTC) - timedelta(days=1)).timestamp() * 1000)
        record, _outcome, _skipped = await self.store.upsert_subscription(
            user_id=1,
            endpoint=ENDPOINT,
            subscription_keys=APPLICATION_SERVER_KEYS,
            label="Expired",
            expiration_time=yesterday_ms,
        )
        self.assertEqual(await self.store.list_active_subscriptions(1), ())
        self.assertEqual(await self.store.list_subscriptions(1), ())
        provider = WebPushChannelProvider(self.settings, self.factory)
        self.assertEqual(
            await provider.resolve_destinations(
                self.settings,
                SimpleNamespace(user_id=1, push_notifications_enabled=True),
            ),
            (),
        )
        await self.store.enqueue_notification(
            user_id=1,
            subscription_id=record.id,
            occurrence_id="expired:1",
            title="Hello",
            body="Body",
            url="/",
            tag=None,
        )
        sender = FakeSender(PushSendResult(PushDeliveryStatus.SENT))
        worker = WebPushDeliveryWorker(self.settings, self.factory, sender=sender)
        await worker._tick()
        async with self.factory() as session:
            delivery = (await session.execute(select(WebPushDelivery))).scalar_one()
            subscription = (
                await session.execute(
                    select(WebPushSubscription).where(WebPushSubscription.id == record.id)
                )
            ).scalar_one()
        self.assertEqual(delivery.state, DELIVERY_SKIPPED)
        self.assertEqual(delivery.error_code, "subscription_expired")
        self.assertIsNotNone(subscription.revoked_at)
        self.assertEqual(sender.calls, [])

    async def test_worker_skips_preference_disabled_without_sending(self) -> None:
        subscription_id = await self.add_subscription()
        await self.store.enqueue_notification(
            user_id=1,
            subscription_id=subscription_id,
            occurrence_id="worker-pref:1",
            title="Hello",
            body="Body",
            url="/",
            tag=None,
        )
        async with self.factory() as session:
            user = (await session.execute(select(User).where(User.user_id == 1))).scalar_one()
            user.push_notifications_enabled = False
            await session.commit()
        sender = FakeSender(PushSendResult(PushDeliveryStatus.SENT))
        worker = WebPushDeliveryWorker(self.settings, self.factory, sender=sender)
        await worker._tick()
        async with self.factory() as session:
            delivery = (await session.execute(select(WebPushDelivery))).scalar_one()
        self.assertEqual(delivery.state, DELIVERY_SKIPPED)
        self.assertEqual(delivery.error_code, "preference_disabled")
        self.assertEqual(sender.calls, [])

    async def test_worker_never_delivers_another_accounts_row(self) -> None:
        subscription_id = await self.add_subscription(user_id=2)
        await self.store.enqueue_notification(
            user_id=1,
            subscription_id=subscription_id,
            occurrence_id="worker-owner:1",
            title="Hello",
            body="Body",
            url="/",
            tag=None,
        )
        sender = FakeSender(PushSendResult(PushDeliveryStatus.SENT))
        worker = WebPushDeliveryWorker(self.settings, self.factory, sender=sender)
        await worker._tick()
        async with self.factory() as session:
            delivery = (await session.execute(select(WebPushDelivery))).scalar_one()
        self.assertEqual(delivery.state, DELIVERY_SKIPPED)
        self.assertEqual(delivery.error_code, "account_mismatch")
        self.assertEqual(sender.calls, [])

    async def test_broadcast_enqueue_dedupe_and_worker_callback(self) -> None:
        subscription_id = await self.add_subscription()
        now = datetime.now(UTC)
        async with self.factory() as session:
            session.add(
                AdminBroadcast(
                    broadcast_id=5,
                    status="running",
                    target="all",
                    channels=["push"],
                    texts={"en": "Hello"},
                    scheduled_at=now,
                )
            )
            session.add(
                AdminBroadcastDelivery(
                    delivery_id=9,
                    broadcast_id=5,
                    user_id=1,
                    channel="push",
                    destination=subscription_id,
                    status="pending",
                )
            )
            await session.commit()
        queued = await self.store.enqueue_broadcast_push(
            broadcast_id=5,
            broadcast_delivery_id=9,
            user_id=1,
            subscription_id=subscription_id,
            title="Broadcast",
            body="Body",
            url="/news",
        )
        duplicate = await enqueue_broadcast_push(
            settings=self.settings,
            session_factory=self.factory,
            broadcast_id=5,
            broadcast_delivery_id=9,
            user_id=1,
            subscription_id=subscription_id,
            title="Broadcast",
            body="Body",
            url="/news",
        )
        self.assertTrue(queued)
        self.assertFalse(duplicate)
        sender = FakeSender(PushSendResult(PushDeliveryStatus.SENT))
        worker = WebPushDeliveryWorker(self.settings, self.factory, sender=sender)
        await worker._tick()
        async with self.factory() as session:
            delivery = (await session.execute(select(WebPushDelivery))).scalar_one()
            broadcast_delivery = (
                await session.execute(
                    select(AdminBroadcastDelivery).where(AdminBroadcastDelivery.delivery_id == 9)
                )
            ).scalar_one()
        self.assertEqual(delivery.state, DELIVERY_SENT)
        self.assertIsNone(delivery.callback_reported_at)
        self.assertEqual(broadcast_delivery.status, "pending")
        self.assertEqual(len(sender.calls), 1)

        # The next tick flushes the outbox without resending the push.
        await worker._tick()
        async with self.factory() as session:
            delivery = (await session.execute(select(WebPushDelivery))).scalar_one()
            broadcast_delivery = (
                await session.execute(
                    select(AdminBroadcastDelivery).where(AdminBroadcastDelivery.delivery_id == 9)
                )
            ).scalar_one()
        self.assertEqual(delivery.state, DELIVERY_SENT)
        self.assertIsNotNone(delivery.callback_reported_at)
        self.assertEqual(broadcast_delivery.status, "sent")
        self.assertEqual(len(sender.calls), 1)

    async def test_broadcast_callback_failure_is_retried_without_resending(self) -> None:
        from db.dal import broadcast_dal

        subscription_id = await self.add_subscription()
        now = datetime.now(UTC)
        async with self.factory() as session:
            session.add(
                AdminBroadcast(
                    broadcast_id=7,
                    status="running",
                    target="all",
                    channels=["push"],
                    texts={"en": "Hello"},
                    scheduled_at=now,
                )
            )
            session.add(
                AdminBroadcastDelivery(
                    delivery_id=11,
                    broadcast_id=7,
                    user_id=1,
                    channel="push",
                    destination=subscription_id,
                    status="pending",
                )
            )
            await session.commit()
        await self.store.enqueue_broadcast_push(
            broadcast_id=7,
            broadcast_delivery_id=11,
            user_id=1,
            subscription_id=subscription_id,
            title="Broadcast",
            body="Body",
            url="/news",
        )
        sender = FakeSender(PushSendResult(PushDeliveryStatus.SENT))
        worker = WebPushDeliveryWorker(self.settings, self.factory, sender=sender)
        await worker._tick()
        original = broadcast_dal.mark_delivery_result
        attempts = {"count": 0}

        async def fail_once(session, delivery_id, *, success, error=None):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("core callback unavailable")
            await original(session, delivery_id, success=success, error=error)

        with patch.object(broadcast_dal, "mark_delivery_result", fail_once):
            await worker._tick()
            async with self.factory() as session:
                delivery = (await session.execute(select(WebPushDelivery))).scalar_one()
                broadcast_delivery = (
                    await session.execute(
                        select(AdminBroadcastDelivery).where(
                            AdminBroadcastDelivery.delivery_id == 11
                        )
                    )
                ).scalar_one()
            self.assertEqual(delivery.state, DELIVERY_SENT)
            self.assertIsNone(delivery.callback_reported_at)
            self.assertEqual(broadcast_delivery.status, "pending")
            await worker._tick()
            async with self.factory() as session:
                delivery = (await session.execute(select(WebPushDelivery))).scalar_one()
                broadcast_delivery = (
                    await session.execute(
                        select(AdminBroadcastDelivery).where(
                            AdminBroadcastDelivery.delivery_id == 11
                        )
                    )
                ).scalar_one()
            self.assertIsNotNone(delivery.callback_reported_at)
            self.assertEqual(broadcast_delivery.status, "sent")
        self.assertEqual(len(sender.calls), 1)

    async def test_revocation_outbox_reports_broadcast_failure(self) -> None:
        subscription_id = await self.add_subscription()
        now = datetime.now(UTC)
        async with self.factory() as session:
            session.add(
                AdminBroadcast(
                    broadcast_id=8,
                    status="running",
                    target="all",
                    channels=["push"],
                    texts={"en": "Hello"},
                    scheduled_at=now,
                )
            )
            session.add(
                AdminBroadcastDelivery(
                    delivery_id=12,
                    broadcast_id=8,
                    user_id=1,
                    channel="push",
                    destination=subscription_id,
                    status="pending",
                )
            )
            await session.commit()
        await self.store.enqueue_broadcast_push(
            broadcast_id=8,
            broadcast_delivery_id=12,
            user_id=1,
            subscription_id=subscription_id,
            title="Broadcast",
            body="Body",
            url="/news",
        )
        revoked = await self.store.revoke_subscription(user_id=1, endpoint=ENDPOINT)
        self.assertTrue(revoked.found)
        async with self.factory() as session:
            delivery = (await session.execute(select(WebPushDelivery))).scalar_one()
        self.assertEqual(delivery.state, DELIVERY_SKIPPED)
        self.assertIsNone(delivery.callback_reported_at)

        from bot.plugins.builtin.web_push.callbacks import (
            report_pending_broadcast_callbacks,
        )

        confirmed = await report_pending_broadcast_callbacks(self.settings, self.factory)
        self.assertEqual(confirmed, 1)
        async with self.factory() as session:
            delivery = (await session.execute(select(WebPushDelivery))).scalar_one()
            broadcast_delivery = (
                await session.execute(
                    select(AdminBroadcastDelivery).where(AdminBroadcastDelivery.delivery_id == 12)
                )
            ).scalar_one()
        self.assertIsNotNone(delivery.callback_reported_at)
        self.assertEqual(broadcast_delivery.status, "failed")
        self.assertEqual(broadcast_delivery.error, "revoked")

    async def test_worker_revokes_undecryptable_subscription(self) -> None:
        subscription_id = await self.add_subscription()
        await self.store.enqueue_notification(
            user_id=1,
            subscription_id=subscription_id,
            occurrence_id="worker-rotate:1",
            title="Hello",
            body="Body",
            url="/",
            tag=None,
        )
        rotated = make_settings(
            WEB_PUSH_VAPID_PRIVATE_KEY=make_settings().WEB_PUSH_VAPID_PRIVATE_KEY
        )
        sender = FakeSender(PushSendResult(PushDeliveryStatus.SENT))
        worker = WebPushDeliveryWorker(rotated, self.factory, sender=sender)
        await worker._tick()
        async with self.factory() as session:
            delivery = (await session.execute(select(WebPushDelivery))).scalar_one()
            subscription = (
                await session.execute(
                    select(WebPushSubscription).where(WebPushSubscription.id == subscription_id)
                )
            ).scalar_one()
        self.assertEqual(delivery.state, DELIVERY_SKIPPED)
        self.assertEqual(delivery.error_code, "storage_key_rotated")
        self.assertIsNotNone(subscription.revoked_at)
        self.assertEqual(sender.calls, [])

    async def test_disabled_provider_reports_not_configured(self) -> None:
        provider = WebPushChannelProvider(make_settings(web_push_configured=False), self.factory)
        outcome = await provider.deliver(
            settings=make_settings(web_push_configured=False),
            user=SimpleNamespace(user_id=1, push_notifications_enabled=True),
            occurrence=make_occurrence(),
            destination="device",
        )
        self.assertEqual(outcome.reason, "not_configured")


class WebPushPayloadTests(unittest.TestCase):
    def test_url_normalization_rejects_external_share_and_secret_links(self) -> None:
        settings = make_settings()
        self.assertEqual(normalize_push_url("/devices", settings), "/devices")
        self.assertEqual(
            normalize_push_url("https://app.example.test/support?topic=billing#secret", settings),
            "/support?topic=billing",
        )
        for raw in (
            "https://t.me/support",
            "https://evil.example.test/support",
            "//evil.example.test/support",
            "/s/0123456789abcdef0123456789abcdef",
            "/support?access_token=abc",
            "/support?session=abc",
            "/unknown",
            "/support/../s/0123456789abcdef0123456789abcdef",
            "/support/%2e%2e/s/0123456789abcdef0123456789abcdef",
            "/support/%252e%252e/s/0123456789abcdef0123456789abcdef",
            "/support/%2e%2e%2fs%2f0123456789abcdef0123456789abcdef",
            "/support%2f..%2fs%2f0123456789abcdef0123456789abcdef",
            "/support\\..\\s\\0123456789abcdef0123456789abcdef",
            "/support/%5c..%5cs%5c0123456789abcdef0123456789abcdef",
            "%2f%2fs%2f0123456789abcdef0123456789abcdef",
            "%252fs%252f0123456789abcdef0123456789abcdef",
            "/support%00/../s/0123456789abcdef0123456789abcdef",
        ):
            self.assertEqual(normalize_push_url(raw, settings), "/", raw)

    def test_payload_constraints_match_service_worker_contract(self) -> None:
        title, body = sanitize_push_content(
            "T" * 200,
            "B" * 400,
            fallback_title="Minishop",
        )
        self.assertEqual(len(title), 120)
        self.assertEqual(len(body), 200)
        row = SimpleNamespace(title=title, body=body, url="/notifications", tag="a" * 32)
        payload = build_push_payload(row)
        self.assertLessEqual(len(payload["title"]), 120)
        self.assertLessEqual(len(payload["body"]), 200)
        self.assertEqual(payload["url"], "/notifications")


class WebPushSenderValidationTests(unittest.TestCase):
    def test_endpoint_allowlist_rejects_ssrf_shapes(self) -> None:
        from bot.plugins.builtin.web_push.sender import PushEndpointError, validate_endpoint

        for endpoint in (
            "https://fcm.googleapis.com/fcm/send/abc",
            "https://updates.push.services.mozilla.com/wpush/v2/abc",
            "https://web.push.apple.com/abc",
            "https://x.notify.windows.com/abc",
        ):
            self.assertEqual(validate_endpoint(endpoint), endpoint)
        for endpoint in (
            "http://fcm.googleapis.com/fcm/send/abc",
            "https://fcm.googleapis.com:8443/fcm/send/abc",
            "https://user:pass@fcm.googleapis.com/fcm/send/abc",
            "https://fcm.googleapis.com/fcm/send/abc#fragment",
            "https://127.0.0.1/push",
            "https://evil.example.test/push",
            "https://fcm.googleapis.com.evil.example.test/push",
        ):
            with self.assertRaises(PushEndpointError, msg=endpoint):
                validate_endpoint(endpoint)

    def test_response_status_mapping(self) -> None:
        from bot.plugins.builtin.web_push.sender import (
            PushDeliveryStatus,
            _result_for_status,
        )

        self.assertEqual(_result_for_status(201).status, PushDeliveryStatus.SENT)
        self.assertEqual(_result_for_status(404).status, PushDeliveryStatus.GONE)
        self.assertEqual(_result_for_status(410).status, PushDeliveryStatus.GONE)
        self.assertEqual(_result_for_status(400).status, PushDeliveryStatus.TERMINAL)
        self.assertEqual(_result_for_status(401).status, PushDeliveryStatus.TERMINAL)
        self.assertEqual(_result_for_status(403).status, PushDeliveryStatus.TERMINAL)
        self.assertEqual(_result_for_status(413).status, PushDeliveryStatus.TERMINAL)
        self.assertEqual(_result_for_status(429).status, PushDeliveryStatus.RETRY)
        self.assertEqual(_result_for_status(503).status, PushDeliveryStatus.RETRY)
        self.assertEqual(_result_for_status(302).status, PushDeliveryStatus.TERMINAL)
