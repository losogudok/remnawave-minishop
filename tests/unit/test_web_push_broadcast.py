"""ONB-09: Web Push as an endpoint-level broadcast delivery channel."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.app.web.admin_api_impl.broadcast_content import (
    selected_channel_descriptors,
    validate_broadcast_content_limits,
)
from bot.middlewares.i18n import JsonI18n
from bot.plugins.builtin.web_push.broadcast import (
    PUSH_TEXT_MAX_LENGTH,
    WebPushBroadcastChannelProvider,
    plaintext_for_push_broadcast,
    push_click_url,
    split_broadcast_push_text,
)
from bot.plugins.builtin.web_push.callbacks import report_pending_broadcast_callbacks
from bot.plugins.builtin.web_push.sender import PushDeliveryStatus, PushSendResult
from bot.plugins.builtin.web_push.store import (
    DELIVERY_FAILED,
    DELIVERY_QUEUED,
    DELIVERY_SENT,
    DELIVERY_SKIPPED,
    WebPushStore,
    utc_milliseconds,
)
from bot.plugins.builtin.web_push.worker import WebPushDeliveryWorker
from bot.services import broadcast_channels as channels
from bot.services.admin_broadcast_delivery import AdminBroadcastDeliveryService
from bot.services.broadcast_channels import (
    BroadcastChannelDescriptor,
    BroadcastChannelDispatch,
    BroadcastChannelMessage,
    BroadcastDispatchContext,
    BroadcastRecipient,
)
from bot.services.message_composition import MessageButton
from db.base import Base
from db.broadcast_models import AdminBroadcast, AdminBroadcastDelivery
from db.dal import broadcast_dal
from db.models import User
from db.web_push_models import WebPushDelivery, WebPushSubscription
from tests.support.settings_stub import settings_stub
from tests.unit.test_web_push_plugin import APPLICATION_SERVER_KEYS, ENDPOINT, make_settings

REPO_ROOT = Path(__file__).resolve().parents[2]
SECOND_ENDPOINT = "https://updates.push.services.mozilla.com/wpush/v2/device-token-2"
THIRD_ENDPOINT = "https://web.push.apple.com/wpush/v2/device-token-3"


class _SequenceSender:
    def __init__(self, results: dict[str, PushSendResult]) -> None:
        self.results = results
        self.default_result = PushSendResult(PushDeliveryStatus.SENT)
        self.calls: list[str] = []

    async def send(
        self,
        *,
        endpoint: str,
        subscription_keys: dict[str, str],
        payload: bytes,
        now: int | None = None,
    ) -> PushSendResult:
        del subscription_keys, payload, now
        self.calls.append(endpoint)
        return self.results.get(endpoint, self.default_result)

    async def close(self) -> None:
        return None


class WebPushBroadcastProviderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.addAsyncCleanup(self.engine.dispose)
        async with self.engine.begin() as connection:
            await connection.run_sync(
                Base.metadata.create_all,
                tables=[
                    User.__table__,
                    AdminBroadcast.__table__,
                    AdminBroadcastDelivery.__table__,
                    WebPushSubscription.__table__,
                    WebPushDelivery.__table__,
                ],
            )
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.settings = make_settings()
        self.store = WebPushStore(self.settings, self.factory)
        self.provider = WebPushBroadcastChannelProvider(self.settings, self.factory)
        channels.registered_broadcast_channel_names()
        self._saved_providers = dict(channels._CHANNEL_PROVIDERS)
        channels.register_broadcast_channel_provider(self.provider)

    async def asyncTearDown(self) -> None:
        channels._CHANNEL_PROVIDERS.clear()
        channels._CHANNEL_PROVIDERS.update(self._saved_providers)

    async def _add_user(self, user_id: int, **values: Any) -> None:
        async with self.factory() as session:
            session.add(User(user_id=user_id, **values))
            await session.commit()

    async def _add_subscription(
        self,
        user_id: int,
        endpoint: str,
        *,
        expiration_time: int | None = None,
    ) -> str:
        record, _outcome, _skipped = await self.store.upsert_subscription(
            user_id=user_id,
            endpoint=endpoint,
            subscription_keys=APPLICATION_SERVER_KEYS,
            label=endpoint.rsplit("/", 1)[-1],
            expiration_time=expiration_time,
        )
        return str(record.id)

    def _message(
        self,
        *,
        delivery_id: int = 1,
        user_id: int = 1,
        destination: str = "device-a",
        text: str = "<b>Launch</b>\n\nBody <i>details</i>",
        buttons: list[MessageButton] | None = None,
    ) -> BroadcastChannelMessage:
        return BroadcastChannelMessage(
            delivery_id=delivery_id,
            user_id=user_id,
            destination=destination,
            destination_key=destination,
            language_code="en",
            text=text,
            buttons=buttons or [],
        )

    async def _dispatch(
        self,
        messages: list[BroadcastChannelMessage],
        *,
        queued_callback: Any = None,
        result_callback: Any = None,
    ) -> tuple[Any, list[int]]:
        queued: list[int] = []
        results: list[tuple[int, bool, str | None]] = []

        async def on_queued(delivery_id: int) -> None:
            queued.append(delivery_id)

        async def on_result(delivery_id: int, success: bool, error: str | None) -> None:
            results.append((delivery_id, success, error))

        context = BroadcastDispatchContext(
            broadcast=cast(Any, SimpleNamespace(broadcast_id=5)),
            deliveries=cast(Any, []),
            messages=messages,
            settings=self.settings,
            i18n=cast(Any, object()),
            bot_username=None,
            session_factory=self.factory,
            queue_manager=None,
            audience_service=cast(Any, object()),
            image=None,
            on_queued=queued_callback or on_queued,
            on_result=result_callback or on_result,
        )
        result = await self.provider.dispatch(context)
        return result, queued

    async def test_descriptor_availability_reasons_and_capabilities(self) -> None:
        descriptor = self.provider.describe(self.settings)
        self.assertEqual(descriptor.name, "push")
        self.assertEqual(descriptor.label_key, "broadcast_channel_push")
        self.assertEqual(descriptor.fallback_label, "Web Push")
        self.assertEqual(descriptor.order, 30)
        self.assertTrue(descriptor.available)
        self.assertFalse(descriptor.supports_html)
        self.assertFalse(descriptor.supports_subject)
        self.assertFalse(descriptor.supports_image)
        self.assertTrue(descriptor.supports_buttons)
        self.assertEqual(descriptor.max_text_length, PUSH_TEXT_MAX_LENGTH)
        self.assertTrue(descriptor.supports_push_preview)
        self.assertEqual(descriptor.destination_level, "endpoint")

        disabled = self.provider.describe(
            make_settings(WEB_PUSH_ENABLED=False, web_push_configured=False)
        )
        self.assertFalse(disabled.available)
        self.assertEqual(disabled.reason, "push_disabled")

        not_configured = self.provider.describe(
            make_settings(WEB_PUSH_ENABLED=True, web_push_configured=False)
        )
        self.assertFalse(not_configured.available)
        self.assertEqual(not_configured.reason, "push_not_configured")

        kill_switch = self.provider.describe(
            make_settings(web_push_configured=True, USER_NOTIFICATION_PUSH_ENABLED=False)
        )
        self.assertFalse(kill_switch.available)
        self.assertEqual(kill_switch.reason, "user_notification_push_disabled")

    async def test_recipients_are_one_row_per_active_device_and_push_preference(self) -> None:
        await self._add_user(1, language_code="ru")
        await self._add_user(2, language_code="en")
        await self._add_user(3, language_code="ru", is_banned=True)
        await self._add_user(4, language_code="ru", push_notifications_enabled=False)
        await self._add_user(5, language_code="en")
        first = await self._add_subscription(1, ENDPOINT)
        second = await self._add_subscription(1, SECOND_ENDPOINT)
        expired = await self._add_subscription(
            2,
            THIRD_ENDPOINT,
            expiration_time=utc_milliseconds(datetime.now(UTC) - timedelta(minutes=1)),
        )
        banned = await self._add_subscription(3, ENDPOINT.replace("1", "3"))
        opted_out = await self._add_subscription(4, ENDPOINT.replace("1", "4"))
        no_telegram = await self._add_subscription(5, SECOND_ENDPOINT.replace("2", "5"))

        async with self.factory() as session:
            recipients = await self.provider.resolve_recipients(
                session,
                user_ids=[5, 1, 2, 3, 4, 99],
                exclude_blocked_telegram=True,
            )

        self.assertEqual(
            recipients,
            [
                BroadcastRecipient(
                    user_id=1,
                    destination=min(first, second),
                    destination_key=min(first, second),
                    language_code="ru",
                ),
                BroadcastRecipient(
                    user_id=1,
                    destination=max(first, second),
                    destination_key=max(first, second),
                    language_code="ru",
                ),
                BroadcastRecipient(
                    user_id=5,
                    destination=no_telegram,
                    destination_key=no_telegram,
                    language_code="en",
                ),
            ],
        )
        self.assertNotIn(expired, [recipient.destination for recipient in recipients])
        self.assertNotIn(banned, [recipient.destination for recipient in recipients])
        self.assertNotIn(opted_out, [recipient.destination for recipient in recipients])
        for recipient in recipients:
            self.assertNotIn("https://", recipient.destination)

    async def test_dispatch_splits_text_selects_url_and_is_idempotent(self) -> None:
        messages = [
            self._message(
                buttons=[
                    MessageButton(
                        label="Promo", url="https://t.me/bot?start=promo_X", kind="promo"
                    ),
                    MessageButton(
                        label="Support",
                        url="https://app.example.test/support?topic=billing#secret",
                        kind="url",
                    ),
                    MessageButton(
                        label="Other",
                        url="https://app.example.test/devices",
                        kind="url",
                    ),
                ]
            )
        ]

        result, queued = await self._dispatch(messages)
        self.assertEqual((result.queued, result.failed), (1, 0))
        self.assertEqual(queued, [1])

        async with self.factory() as session:
            rows = list((await session.execute(select(WebPushDelivery))).scalars())
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.kind, "broadcast")
        self.assertEqual(row.broadcast_id, 5)
        self.assertEqual(row.broadcast_delivery_id, 1)
        self.assertEqual(row.title, "Launch")
        self.assertEqual(row.body, "Body details")
        self.assertEqual(row.url, "/support?topic=billing")
        self.assertEqual(row.state, DELIVERY_QUEUED)

        duplicate, duplicate_queued = await self._dispatch(messages)
        self.assertEqual((duplicate.queued, duplicate.failed), (1, 0))
        self.assertEqual(duplicate_queued, [1])
        async with self.factory() as session:
            rows = list((await session.execute(select(WebPushDelivery))).scalars())
        self.assertEqual(len(rows), 1)

    async def test_enqueue_failure_status_callback_error_does_not_abort_other_devices(
        self,
    ) -> None:
        messages = [
            self._message(delivery_id=1, destination="device-a"),
            self._message(delivery_id=2, destination="device-b"),
            self._message(delivery_id=3, destination="device-c"),
        ]
        original_enqueue = WebPushStore.enqueue_broadcast_push

        async def flaky_enqueue(store: WebPushStore, **kwargs: Any) -> bool:
            if int(kwargs["broadcast_delivery_id"]) == 1:
                raise RuntimeError("queue write failed")
            return bool(await original_enqueue(store, **kwargs))

        async def failing_result(delivery_id: int, success: bool, error: str | None) -> None:
            del success, error
            if delivery_id == 1:
                raise RuntimeError("core status write failed")

        with patch.object(WebPushStore, "enqueue_broadcast_push", flaky_enqueue):
            result, queued = await self._dispatch(
                messages,
                result_callback=failing_result,
            )

        self.assertEqual((result.queued, result.failed), (2, 1))
        self.assertEqual(queued, [2, 3])
        self.assertFalse(
            await self.store.record_broadcast_failure(
                broadcast_id=5,
                broadcast_delivery_id=1,
                user_id=1,
            )
        )
        async with self.factory() as session:
            rows = list((await session.execute(select(WebPushDelivery))).scalars())
        rows_by_delivery = {int(row.broadcast_delivery_id or 0): row for row in rows}
        self.assertEqual(set(rows_by_delivery), {1, 2, 3})
        failure = rows_by_delivery[1]
        self.assertEqual(failure.state, DELIVERY_FAILED)
        self.assertEqual(failure.error_code, "enqueue_failed")
        self.assertEqual(failure.subscription_id, "")
        self.assertEqual(
            {rows_by_delivery[delivery_id].state for delivery_id in (2, 3)},
            {DELIVERY_QUEUED},
        )

    async def test_dispatch_truncates_body_falls_back_for_long_first_line(self) -> None:
        body = "x" * 300
        result, _queued = await self._dispatch([self._message(text=f"Title\n{body}")])
        self.assertEqual(result.queued, 1)
        async with self.factory() as session:
            row = (await session.execute(select(WebPushDelivery))).scalar_one()
        self.assertEqual(row.title, "Title")
        self.assertLessEqual(len(row.body), 200)
        self.assertTrue(row.body.endswith("…"))

        long_title = "y" * 130
        result, _queued = await self._dispatch(
            [self._message(delivery_id=2, text=f"{long_title}\nShort body")]
        )
        self.assertEqual(result.queued, 1)
        async with self.factory() as session:
            row = (
                await session.execute(
                    select(WebPushDelivery).where(WebPushDelivery.broadcast_delivery_id == 2)
                )
            ).scalar_one()
        self.assertEqual(row.title, "Minishop")
        self.assertIn("Short body", row.body)

    async def test_dispatch_uses_configured_webapp_title_as_fallback(self) -> None:
        self.settings.WEBAPP_TITLE = "My Shop"
        result, _queued = await self._dispatch([self._message(text=f"{'z' * 130}\nShort body")])
        self.assertEqual(result.queued, 1)
        async with self.factory() as session:
            row = (await session.execute(select(WebPushDelivery))).scalar_one()
        self.assertEqual(row.title, "My Shop")
        self.assertEqual(row.body, f"{'z' * 130} Short body")

    async def test_external_and_telegram_only_buttons_never_become_click_targets(self) -> None:
        external = self._message(
            buttons=[MessageButton(label="External", url="https://evil.example/x", kind="url")]
        )
        self.assertEqual(push_click_url(external, self.settings), "/")
        promo = self._message(
            buttons=[MessageButton(label="Promo", url="https://t.me/bot", kind="promo_bot")]
        )
        self.assertEqual(push_click_url(promo, self.settings), "/")
        share = self._message(
            buttons=[
                MessageButton(
                    label="Share",
                    url="https://app.example.test/s/sharetoken",
                    kind="url",
                )
            ]
        )
        self.assertEqual(push_click_url(share, self.settings), "/")

    async def test_plaintext_stripping_keeps_paragraph_breaks(self) -> None:
        self.assertEqual(
            plaintext_for_push_broadcast("<b>Hello</b><br><i>World</i> &amp; friends"),
            "Hello\nWorld & friends",
        )
        self.assertEqual(
            split_broadcast_push_text("<p>Title</p><p>Body</p>", "Minishop"),
            ("Title", "Body"),
        )
        self.assertEqual(
            split_broadcast_push_text("", "Minishop"),
            ("Minishop", ""),
        )
        self.assertEqual(
            split_broadcast_push_text("\nBody only", "Minishop"),
            ("Minishop", "Body only"),
        )

    async def test_api_rejects_unavailable_removed_push_and_allows_mixed_limits(self) -> None:
        _, error = selected_channel_descriptors(self.settings, ["push"])
        self.assertIsNone(error)

        disabled_settings = settings_stub(
            WEB_PUSH_ENABLED=False,
            web_push_configured=False,
            USER_NOTIFICATION_PUSH_ENABLED=True,
        )
        _, disabled = selected_channel_descriptors(disabled_settings, ["push"])
        self.assertIsNotNone(disabled)
        assert disabled is not None and disabled.text is not None
        self.assertEqual(disabled.status, 503)
        self.assertIn("push_disabled", disabled.text)

        not_configured_settings = settings_stub(
            WEB_PUSH_ENABLED=True,
            web_push_configured=False,
        )
        _, not_configured = selected_channel_descriptors(not_configured_settings, ["push"])
        assert not_configured is not None and not_configured.text is not None
        self.assertEqual(not_configured.status, 503)
        self.assertIn("push_not_configured", not_configured.text)

        kill_settings = settings_stub(
            WEB_PUSH_ENABLED=True,
            web_push_configured=True,
            USER_NOTIFICATION_PUSH_ENABLED=False,
        )
        _, kill_switch = selected_channel_descriptors(kill_settings, ["push"])
        assert kill_switch is not None and kill_switch.text is not None
        self.assertEqual(kill_switch.status, 503)
        self.assertIn("user_notification_push_disabled", kill_switch.text)

        channels.unregister_broadcast_channel_provider("push")
        _, removed = selected_channel_descriptors(self.settings, ["push"])
        assert removed is not None and removed.text is not None
        self.assertEqual(removed.status, 400)
        self.assertIn("invalid_channel", removed.text)
        channels.register_broadcast_channel_provider(self.provider)

        telegram = BroadcastChannelDescriptor(
            name="telegram",
            label_key="broadcast_channel_telegram",
            fallback_label="Telegram",
            max_text_length=4096,
        )
        email = BroadcastChannelDescriptor(
            name="email",
            label_key="broadcast_channel_email",
            fallback_label="Email",
            max_text_length=0,
        )
        push = self.provider.describe(self.settings)
        self.assertIsNone(
            validate_broadcast_content_limits(
                [telegram, push],
                texts={"en": "x" * 500},
                email_subjects={},
            )
        )
        self.assertIsNone(
            validate_broadcast_content_limits(
                [email, push],
                texts={"en": "x" * 10_000},
                email_subjects={},
            )
        )
        self.assertIsNone(
            validate_broadcast_content_limits(
                [push],
                texts={"en": "x" * 500},
                email_subjects={},
            )
        )
        telegram_only = validate_broadcast_content_limits(
            [telegram],
            texts={"en": "x" * 5000},
            email_subjects={},
        )
        assert telegram_only is not None
        self.assertEqual(telegram_only.status, 400)
        self.assertIn("message_too_long", telegram_only.text or "")


class _SuccessfulEmailProvider:
    """Minimal user-level channel so the mixed-campaign seam stays real."""

    name = "email"

    def __init__(self, recipients: list[BroadcastRecipient]) -> None:
        self.recipients = recipients

    def describe(self, settings: Any) -> BroadcastChannelDescriptor:
        del settings
        return BroadcastChannelDescriptor(
            name=self.name,
            label_key="broadcast_channel_email",
            fallback_label="Email",
            order=20,
            supports_subject=True,
            max_text_length=0,
        )

    async def resolve_recipients(
        self,
        session: Any,
        *,
        user_ids: list[int],
        exclude_blocked_telegram: bool,
    ) -> list[BroadcastRecipient]:
        del session, user_ids, exclude_blocked_telegram
        return list(self.recipients)

    async def dispatch(self, context: BroadcastDispatchContext) -> BroadcastChannelDispatch:
        for message in context.messages:
            await context.on_queued(message.delivery_id)
            await context.on_result(message.delivery_id, True, None)
        return BroadcastChannelDispatch(queued=len(context.messages), failed=0)


class WebPushBroadcastIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.addAsyncCleanup(self.engine.dispose)
        async with self.engine.begin() as connection:
            await connection.run_sync(
                Base.metadata.create_all,
                tables=[
                    User.__table__,
                    AdminBroadcast.__table__,
                    AdminBroadcastDelivery.__table__,
                    WebPushSubscription.__table__,
                    WebPushDelivery.__table__,
                ],
            )
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.settings = make_settings(DEFAULT_LANGUAGE="en")
        self.store = WebPushStore(self.settings, self.factory)
        self.i18n = JsonI18n(str(REPO_ROOT / "locales"), default="en")
        self.provider = WebPushBroadcastChannelProvider(self.settings, self.factory)
        channels.registered_broadcast_channel_names()
        self._saved_providers = dict(channels._CHANNEL_PROVIDERS)
        channels.register_broadcast_channel_provider(self.provider)
        async with self.factory() as session:
            session.add_all(
                [
                    User(user_id=1, language_code="en"),
                    User(user_id=2, language_code="ru", telegram_id=222),
                    User(user_id=3, language_code="en"),
                ]
            )
            await session.commit()
        self.subscriptions: dict[int, list[str]] = {}
        self.subscriptions[1] = [
            (
                await self.store.upsert_subscription(
                    user_id=1,
                    endpoint=ENDPOINT,
                    subscription_keys=APPLICATION_SERVER_KEYS,
                )
            )[0].id,
            (
                await self.store.upsert_subscription(
                    user_id=1,
                    endpoint=SECOND_ENDPOINT,
                    subscription_keys=APPLICATION_SERVER_KEYS,
                )
            )[0].id,
        ]
        self.subscriptions[2] = [
            (
                await self.store.upsert_subscription(
                    user_id=2,
                    endpoint=THIRD_ENDPOINT,
                    subscription_keys=APPLICATION_SERVER_KEYS,
                )
            )[0].id
        ]
        self.audience = SimpleNamespace(resolve_user_ids=AsyncMock(return_value=[1, 2]))

    async def asyncTearDown(self) -> None:
        channels._CHANNEL_PROVIDERS.clear()
        channels._CHANNEL_PROVIDERS.update(self._saved_providers)

    def _service(self) -> AdminBroadcastDeliveryService:
        return AdminBroadcastDeliveryService(
            settings=self.settings,
            session_factory=self.factory,
            i18n=self.i18n,
            audience_service=cast(Any, self.audience),
            queue_manager=None,
            bot_username="demo_bot",
        )

    async def _create(self, channels_selected: list[str] | None = None) -> AdminBroadcast:
        async with self.factory() as session:
            return await broadcast_dal.create_broadcast(
                session,
                actor_id=9,
                target="all",
                user_ids=[1, 2, 3],
                channels=channels_selected or ["push"],
                exclude_blocked_telegram=False,
                texts={"en": "Launch\nBody details", "ru": "Запуск\nДетали"},
                email_subjects={"en": "News"},
                buttons=[],
                scheduled_at=datetime.now(UTC) + timedelta(hours=1),
            )

    async def _dispatch(self, broadcast_id: int) -> Any:
        async with self.factory() as session:
            item = await broadcast_dal.get_broadcast(session, broadcast_id)
            assert item is not None
            item.status = "running"
            item.started_at = item.started_at or datetime.now(UTC)
            await session.commit()
            await session.refresh(item)
        with patch.object(broadcast_dal, "begin_broadcast", AsyncMock(return_value=item)):
            return await self._service().dispatch(broadcast_id, user_ids=[1, 2, 3])

    async def _reload(self, broadcast_id: int) -> AdminBroadcast:
        async with self.factory() as session:
            item = await broadcast_dal.get_broadcast(session, broadcast_id)
        assert item is not None
        return item

    async def _worker(self, sender: _SequenceSender) -> WebPushDeliveryWorker:
        return WebPushDeliveryWorker(self.settings, self.factory, sender=sender)

    async def test_push_only_campaign_delivers_every_device_and_updates_stats(self) -> None:
        item = await self._create()
        self.assertEqual(item.channels, ["push"])
        self.assertEqual(item.recipient_count, 2)
        self.assertEqual(item.total_deliveries, 3)
        self.assertEqual(item.channel_stats["push"]["users"], 2)
        self.assertEqual(item.channel_stats["push"]["deliveries"], 3)

        result = await self._dispatch(int(item.broadcast_id))
        self.assertEqual((result.queued, result.failed, result.email_queued), (3, 0, 0))
        async with self.factory() as session:
            rows = (
                await session.execute(
                    select(AdminBroadcastDelivery)
                    .where(AdminBroadcastDelivery.broadcast_id == item.broadcast_id)
                    .order_by(AdminBroadcastDelivery.delivery_id)
                )
            ).scalars()
            deliveries = list(rows)
        self.assertEqual({row.status for row in deliveries}, {"queued"})
        self.assertEqual(
            sorted(row.destination_key for row in deliveries),
            sorted([*self.subscriptions[1], *self.subscriptions[2]]),
        )

        sender = _SequenceSender({})
        worker = await self._worker(sender)
        await worker._tick()
        await worker._tick()

        reloaded = await self._reload(int(item.broadcast_id))
        self.assertEqual(reloaded.status, "completed")
        self.assertEqual(reloaded.successful_deliveries, 3)
        self.assertEqual(reloaded.failed_deliveries, 0)
        self.assertEqual(reloaded.channel_stats["push"]["users"], 2)
        self.assertEqual(reloaded.channel_stats["push"]["deliveries"], 3)
        self.assertEqual(reloaded.channel_stats["push"]["sent"], 3)
        self.assertEqual(reloaded.channel_stats["push"]["queued"], 0)
        async with self.factory() as session:
            push_rows = list((await session.execute(select(WebPushDelivery))).scalars())
        self.assertEqual({row.state for row in push_rows}, {DELIVERY_SENT})
        self.assertEqual(len(sender.calls), 3)

    async def test_on_queued_failure_keeps_every_device_and_reconciles_results(self) -> None:
        item = await self._create()
        service = self._service()
        original_mark_queued = service._mark_queued
        calls = {"count": 0}

        async def flaky_mark_queued(delivery_id: int) -> None:
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("core queued status unavailable")
            await original_mark_queued(delivery_id)

        async with self.factory() as session:
            broadcast = await broadcast_dal.get_broadcast(session, int(item.broadcast_id))
            assert broadcast is not None
            broadcast.status = "running"
            broadcast.started_at = broadcast.started_at or datetime.now(UTC)
            await session.commit()
            await session.refresh(broadcast)

        with (
            patch.object(broadcast_dal, "begin_broadcast", AsyncMock(return_value=broadcast)),
            patch.object(service, "_mark_queued", side_effect=flaky_mark_queued),
        ):
            result = await service.dispatch(int(item.broadcast_id), user_ids=[1, 2, 3])

        self.assertEqual((result.queued, result.failed), (3, 0))
        async with self.factory() as session:
            push_rows = list((await session.execute(select(WebPushDelivery))).scalars())
            core_rows = list(
                (
                    await session.execute(
                        select(AdminBroadcastDelivery).where(
                            AdminBroadcastDelivery.broadcast_id == item.broadcast_id
                        )
                    )
                ).scalars()
            )
        self.assertEqual(len(push_rows), 3)
        self.assertEqual(
            sorted(row.status for row in core_rows),
            ["pending", "queued", "queued"],
        )

        worker = await self._worker(_SequenceSender({}))
        await worker._tick()
        await worker._tick()

        reloaded = await self._reload(int(item.broadcast_id))
        self.assertEqual(reloaded.status, "completed")
        self.assertEqual(reloaded.successful_deliveries, 3)
        self.assertEqual(reloaded.failed_deliveries, 0)
        async with self.factory() as session:
            core_rows = list(
                (
                    await session.execute(
                        select(AdminBroadcastDelivery).where(
                            AdminBroadcastDelivery.broadcast_id == item.broadcast_id
                        )
                    )
                ).scalars()
            )
        self.assertEqual({row.status for row in core_rows}, {"sent"})

    async def test_enqueue_and_immediate_result_failure_finishes_via_outbox(self) -> None:
        item = await self._create()
        service = self._service()
        original_enqueue = WebPushStore.enqueue_broadcast_push
        original_mark_result = service._mark_result
        enqueue_calls = {"count": 0}

        async def flaky_enqueue(store: WebPushStore, **kwargs: Any) -> bool:
            enqueue_calls["count"] += 1
            if enqueue_calls["count"] == 1:
                raise RuntimeError("queue write failed")
            return bool(await original_enqueue(store, **kwargs))

        async def flaky_mark_result(
            delivery_id: int,
            success: bool,
            error: str | None = None,
        ) -> None:
            if error == "enqueue_failed":
                raise RuntimeError("core status write failed")
            await original_mark_result(delivery_id, success, error)

        async with self.factory() as session:
            broadcast = await broadcast_dal.get_broadcast(session, int(item.broadcast_id))
            assert broadcast is not None
            broadcast.status = "running"
            broadcast.started_at = broadcast.started_at or datetime.now(UTC)
            await session.commit()
            await session.refresh(broadcast)

        with (
            patch.object(WebPushStore, "enqueue_broadcast_push", flaky_enqueue),
            patch.object(service, "_mark_result", side_effect=flaky_mark_result),
            patch.object(broadcast_dal, "begin_broadcast", AsyncMock(return_value=broadcast)),
        ):
            result = await service.dispatch(int(item.broadcast_id), user_ids=[1, 2, 3])

        self.assertEqual((result.queued, result.failed), (2, 1))
        async with self.factory() as session:
            push_rows = list((await session.execute(select(WebPushDelivery))).scalars())
            core_rows = list(
                (
                    await session.execute(
                        select(AdminBroadcastDelivery).where(
                            AdminBroadcastDelivery.broadcast_id == item.broadcast_id
                        )
                    )
                ).scalars()
            )
        self.assertEqual(len(push_rows), 3)
        failure_rows = [row for row in push_rows if row.state == DELIVERY_FAILED]
        self.assertEqual(len(failure_rows), 1)
        self.assertEqual(failure_rows[0].error_code, "enqueue_failed")
        self.assertEqual(failure_rows[0].subscription_id, "")
        self.assertEqual(
            sorted(row.status for row in core_rows),
            ["pending", "queued", "queued"],
        )

        confirmed = await report_pending_broadcast_callbacks(self.settings, self.factory)
        self.assertEqual(confirmed, 1)
        async with self.factory() as session:
            core_rows = list(
                (
                    await session.execute(
                        select(AdminBroadcastDelivery).where(
                            AdminBroadcastDelivery.broadcast_id == item.broadcast_id
                        )
                    )
                ).scalars()
            )
            reported_failure = (
                await session.execute(
                    select(WebPushDelivery).where(WebPushDelivery.state == DELIVERY_FAILED)
                )
            ).scalar_one()
        self.assertEqual(
            sorted(row.status for row in core_rows),
            ["failed", "queued", "queued"],
        )
        self.assertIsNotNone(reported_failure.callback_reported_at)

        sender = _SequenceSender({})
        worker = await self._worker(sender)
        await worker._tick()
        await worker._tick()

        self.assertEqual(len(sender.calls), 2)
        reloaded = await self._reload(int(item.broadcast_id))
        self.assertEqual(reloaded.status, "completed_with_errors")
        self.assertEqual(reloaded.successful_deliveries, 2)
        self.assertEqual(reloaded.failed_deliveries, 1)
        self.assertEqual(reloaded.channel_stats["push"]["sent"], 2)
        self.assertEqual(reloaded.channel_stats["push"]["failed"], 1)
        async with self.factory() as session:
            core_rows = list(
                (
                    await session.execute(
                        select(AdminBroadcastDelivery).where(
                            AdminBroadcastDelivery.broadcast_id == item.broadcast_id
                        )
                    )
                ).scalars()
            )
        self.assertEqual(
            sorted(row.status for row in core_rows),
            ["failed", "sent", "sent"],
        )

    async def test_mixed_push_and_email_campaigns_keep_independent_channel_rows(self) -> None:
        email = _SuccessfulEmailProvider(
            [
                BroadcastRecipient(user_id=1, destination="one@example.test", language_code="en"),
                BroadcastRecipient(user_id=2, destination="two@example.test", language_code="ru"),
            ]
        )
        channels.register_broadcast_channel_provider(email)
        item = await self._create(["push", "email"])
        self.assertEqual(item.recipient_count, 2)
        self.assertEqual(item.total_deliveries, 5)
        self.assertEqual(item.channel_stats["push"]["deliveries"], 3)
        self.assertEqual(item.channel_stats["email"]["deliveries"], 2)

        result = await self._dispatch(int(item.broadcast_id))
        self.assertEqual((result.queued, result.email_queued, result.failed), (3, 2, 0))
        async with self.factory() as session:
            rows = list(
                (
                    await session.execute(
                        select(AdminBroadcastDelivery)
                        .where(AdminBroadcastDelivery.broadcast_id == item.broadcast_id)
                        .order_by(AdminBroadcastDelivery.delivery_id)
                    )
                ).scalars()
            )
        self.assertEqual(
            [(row.channel, row.status) for row in rows],
            [
                ("push", "queued"),
                ("push", "queued"),
                ("push", "queued"),
                ("email", "sent"),
                ("email", "sent"),
            ],
        )

        worker = await self._worker(_SequenceSender({}))
        await worker._tick()
        await worker._tick()
        reloaded = await self._reload(int(item.broadcast_id))
        self.assertEqual(reloaded.status, "completed")
        self.assertEqual(reloaded.channel_stats["push"]["sent"], 3)
        self.assertEqual(reloaded.channel_stats["email"]["sent"], 2)
        self.assertEqual(reloaded.successful_deliveries, 5)

    async def test_partial_endpoint_failure_and_gone_subscription(self) -> None:
        item = await self._create()
        await self._dispatch(int(item.broadcast_id))
        gone_endpoint = ENDPOINT
        sender = _SequenceSender({gone_endpoint: PushSendResult(PushDeliveryStatus.GONE)})
        worker = await self._worker(sender)
        await worker._tick()
        await worker._tick()

        reloaded = await self._reload(int(item.broadcast_id))
        self.assertEqual(reloaded.status, "completed_with_errors")
        self.assertEqual(reloaded.successful_deliveries, 2)
        self.assertEqual(reloaded.failed_deliveries, 1)
        self.assertEqual(reloaded.channel_stats["push"]["sent"], 2)
        self.assertEqual(reloaded.channel_stats["push"]["failed"], 1)
        async with self.factory() as session:
            failed = (
                await session.execute(
                    select(AdminBroadcastDelivery).where(
                        AdminBroadcastDelivery.broadcast_id == item.broadcast_id,
                        AdminBroadcastDelivery.status == "failed",
                    )
                )
            ).scalar_one()
            gone = (
                await session.execute(
                    select(WebPushSubscription).where(WebPushSubscription.user_id == 1)
                )
            ).scalars()
        self.assertEqual(failed.error, "endpoint_gone")
        self.assertIsNotNone(failed.error)
        self.assertTrue(any(row.revoked_at is not None for row in gone))

    async def test_opt_out_between_creation_and_dispatch_is_rechecked_by_worker(self) -> None:
        item = await self._create()
        self.assertEqual(item.recipient_count, 2)
        self.assertEqual(item.total_deliveries, 3)
        async with self.factory() as session:
            await session.execute(
                update(User).where(User.user_id == 1).values(push_notifications_enabled=False)
            )
            await session.commit()

        result = await self._dispatch(int(item.broadcast_id))
        self.assertEqual((result.queued, result.failed), (1, 0))
        async with self.factory() as session:
            rows = list((await session.execute(select(WebPushDelivery))).scalars())
        self.assertEqual(len(rows), 1)
        self.assertEqual(int(rows[0].user_id), 2)

        worker = await self._worker(_SequenceSender({}))
        await worker._tick()
        await worker._tick()

        reloaded = await self._reload(int(item.broadcast_id))
        self.assertEqual(reloaded.status, "completed")
        self.assertEqual(reloaded.recipient_count, 1)
        self.assertEqual(reloaded.total_deliveries, 1)
        self.assertEqual(reloaded.channel_stats["push"]["users"], 1)
        self.assertEqual(reloaded.channel_stats["push"]["deliveries"], 1)
        self.assertEqual(reloaded.channel_stats["push"]["sent"], 1)
        self.assertEqual(reloaded.channel_stats["push"]["failed"], 0)

    async def test_retry_exhaustion_fails_only_that_device_result(self) -> None:
        item = await self._create()
        await self._dispatch(int(item.broadcast_id))
        async with self.factory() as session:
            await session.execute(update(WebPushDelivery).values(max_attempts=1))
            await session.commit()

        retry_sender = _SequenceSender({})
        retry_sender.default_result = PushSendResult(PushDeliveryStatus.RETRY, "upstream_5xx")
        worker = await self._worker(retry_sender)
        await worker._tick()
        await worker._tick()

        reloaded = await self._reload(int(item.broadcast_id))
        self.assertEqual(reloaded.status, "completed_with_errors")
        self.assertEqual(reloaded.channel_stats["push"]["sent"], 0)
        self.assertEqual(reloaded.channel_stats["push"]["failed"], 3)
        async with self.factory() as session:
            push_rows = list((await session.execute(select(WebPushDelivery))).scalars())
            core_rows = list(
                (
                    await session.execute(
                        select(AdminBroadcastDelivery).where(
                            AdminBroadcastDelivery.broadcast_id == item.broadcast_id
                        )
                    )
                ).scalars()
            )
        self.assertEqual({row.state for row in push_rows}, {DELIVERY_FAILED})
        self.assertEqual({row.error_code for row in push_rows}, {"upstream_5xx"})
        self.assertEqual({row.status for row in core_rows}, {"failed"})

    async def test_opt_out_after_scheduling_skips_push_without_affecting_plan(self) -> None:
        item = await self._create()
        await self._dispatch(int(item.broadcast_id))
        async with self.factory() as session:
            await session.execute(
                update(User).where(User.user_id == 1).values(push_notifications_enabled=False)
            )
            await session.commit()
        worker = await self._worker(_SequenceSender({}))
        await worker._tick()
        await worker._tick()

        reloaded = await self._reload(int(item.broadcast_id))
        self.assertEqual(reloaded.status, "completed_with_errors")
        self.assertEqual(reloaded.channel_stats["push"]["sent"], 1)
        self.assertEqual(reloaded.channel_stats["push"]["failed"], 2)
        async with self.factory() as session:
            rows = list(
                (
                    await session.execute(
                        select(WebPushDelivery).where(WebPushDelivery.user_id == 1)
                    )
                ).scalars()
            )
        self.assertEqual({row.state for row in rows}, {DELIVERY_SKIPPED})
        self.assertEqual({row.error_code for row in rows}, {"preference_disabled"})

    async def test_transient_failure_retries_without_failing_the_core_row(self) -> None:
        item = await self._create(["push"])
        await self._dispatch(int(item.broadcast_id))
        retry_sender = _SequenceSender({})
        retry_sender.default_result = PushSendResult(PushDeliveryStatus.RETRY, "upstream_5xx")
        worker = await self._worker(retry_sender)
        await worker._tick()

        reloaded = await self._reload(int(item.broadcast_id))
        self.assertEqual(reloaded.status, "running")
        self.assertEqual(reloaded.channel_stats["push"]["queued"], 3)
        self.assertEqual(reloaded.channel_stats["push"]["failed"], 0)
        async with self.factory() as session:
            rows = list((await session.execute(select(WebPushDelivery))).scalars())
            self.assertEqual({row.state for row in rows}, {DELIVERY_QUEUED})
            self.assertEqual({row.attempts for row in rows}, {1})
            await session.execute(
                update(WebPushDelivery).values(
                    next_attempt_at=datetime.now(UTC) - timedelta(seconds=1)
                )
            )
            await session.commit()

        success = _SequenceSender({})
        worker = await self._worker(success)
        await worker._tick()
        await worker._tick()
        reloaded = await self._reload(int(item.broadcast_id))
        self.assertEqual(reloaded.status, "completed")
        self.assertEqual(reloaded.channel_stats["push"]["sent"], 3)


class WebPushBroadcastPluginSetupTests(unittest.IsolatedAsyncioTestCase):
    async def test_setup_registers_descriptor_even_when_push_is_disabled(self) -> None:
        from bot.plugins.builtin.web_push import WebPushPlugin
        from bot.plugins.builtin.web_push.broadcast import (
            configure_broadcast_session_factory,
            get_broadcast_session_factory,
        )
        from bot.plugins.spec import PluginContext
        from bot.services import notification_channels

        channels.registered_broadcast_channel_names()
        saved = dict(channels._CHANNEL_PROVIDERS)
        saved_notification = dict(notification_channels._CHANNEL_PROVIDERS)
        factory = cast(Any, object())
        try:
            WebPushPlugin().setup(
                PluginContext(
                    settings=make_settings(
                        WEB_PUSH_ENABLED=False,
                        web_push_configured=False,
                    ),
                    session_factory=cast(Any, factory),
                )
            )
            provider = channels.get_broadcast_channel_provider("push")
            assert provider is not None
            descriptor = provider.describe(
                make_settings(WEB_PUSH_ENABLED=False, web_push_configured=False)
            )
            self.assertEqual(descriptor.reason, "push_disabled")
            self.assertIs(get_broadcast_session_factory(), factory)
        finally:
            configure_broadcast_session_factory(cast(Any, None))
            channels._CHANNEL_PROVIDERS.clear()
            channels._CHANNEL_PROVIDERS.update(saved)
            notification_channels._CHANNEL_PROVIDERS.clear()
            notification_channels._CHANNEL_PROVIDERS.update(saved_notification)
