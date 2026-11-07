"""Broadcast delivery behavior across the pluggable channel contract.

The fake providers below deliberately act like transports (resolve rows,
queue and report results) so the tests exercise the real delivery service and
DAL instead of mocking the seam under test.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.middlewares.i18n import JsonI18n
from bot.services import broadcast_channels as channels
from bot.services.admin_broadcast_delivery import AdminBroadcastDeliveryService
from bot.services.broadcast_channels import (
    BroadcastChannelDescriptor,
    BroadcastChannelDispatch,
    BroadcastChannelMessage,
    BroadcastDispatchContext,
    BroadcastRecipient,
)
from db.base import Base
from db.broadcast_models import AdminBroadcast, AdminBroadcastDelivery
from db.dal import broadcast_dal
from db.models import User
from tests.support.settings_stub import settings_stub

REPO_ROOT = Path(__file__).resolve().parents[2]


class _FakeProvider:
    def __init__(
        self,
        name: str,
        recipients: list[BroadcastRecipient],
        *,
        available: bool = True,
        fail: bool = False,
        supports_subject: bool = False,
        destination_level: str = "user",
        defer_results: bool = False,
    ) -> None:
        self.name = name
        self.recipients = recipients
        self.available = available
        self.fail = fail
        self.supports_subject = supports_subject
        self.destination_level = destination_level
        self.defer_results = defer_results
        self.dispatched = 0
        self.messages: list[BroadcastChannelMessage] = []

    def describe(self, settings: Any) -> BroadcastChannelDescriptor:
        del settings
        return BroadcastChannelDescriptor(
            name=self.name,
            label_key=f"broadcast_channel_{self.name}",
            fallback_label=self.name.title(),
            available=self.available,
            reason="" if self.available else "provider_off",
            supports_subject=self.supports_subject,
            supports_buttons=True,
            supports_image=True,
            max_text_length=4096,
            destination_level=cast(Any, self.destination_level),
        )

    async def resolve_recipients(
        self,
        session: AsyncSession,
        *,
        user_ids: list[int],
        exclude_blocked_telegram: bool,
    ) -> list[BroadcastRecipient]:
        del session, user_ids, exclude_blocked_telegram
        return list(self.recipients)

    async def dispatch(self, context: BroadcastDispatchContext) -> BroadcastChannelDispatch:
        self.dispatched += 1
        if self.fail:
            raise RuntimeError("provider_down")
        self.messages.extend(context.messages)
        for message in context.messages:
            await context.on_queued(message.delivery_id)
            if not self.defer_results:
                await context.on_result(message.delivery_id, True, None)
        return BroadcastChannelDispatch(queued=len(context.messages), failed=0)


def _recipient(
    user_id: int,
    destination: str,
    *,
    destination_key: str = "default",
    language_code: str | None = "en",
) -> BroadcastRecipient:
    return BroadcastRecipient(
        user_id=user_id,
        destination=destination,
        destination_key=destination_key,
        language_code=language_code,
    )


class BroadcastChannelDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        channels.registered_broadcast_channel_names()
        self._saved_providers = dict(channels._CHANNEL_PROVIDERS)
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.addAsyncCleanup(self.engine.dispose)
        async with self.engine.begin() as connection:
            await connection.run_sync(
                Base.metadata.create_all,
                tables=[
                    User.__table__,
                    AdminBroadcast.__table__,
                    AdminBroadcastDelivery.__table__,
                ],
            )
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.settings = settings_stub(
            DEFAULT_LANGUAGE="en",
            SUBSCRIPTION_MINI_APP_URL="https://app.example.test/",
        )
        self.i18n = JsonI18n(str(REPO_ROOT / "locales"), default="en")
        self.audience = SimpleNamespace(
            panel_service=None,
            resolve_user_ids=AsyncMock(return_value=[1, 2]),
        )

    async def asyncTearDown(self) -> None:
        channels._CHANNEL_PROVIDERS.clear()
        channels._CHANNEL_PROVIDERS.update(self._saved_providers)

    def _provider(self, provider: _FakeProvider) -> _FakeProvider:
        channels.register_broadcast_channel_provider(provider)
        return provider

    def _service(self, *, queue_manager: Any = None) -> AdminBroadcastDeliveryService:
        return AdminBroadcastDeliveryService(
            settings=self.settings,
            session_factory=self.factory,
            i18n=self.i18n,
            audience_service=cast(Any, self.audience),
            queue_manager=cast(Any, queue_manager if queue_manager is not None else object()),
            bot_username="demo_bot",
        )

    async def _create(
        self,
        channel_names: list[str],
        *,
        texts: dict[str, str] | None = None,
        buttons: list[dict[str, Any]] | None = None,
    ) -> AdminBroadcast:
        async with self.factory() as session:
            return await broadcast_dal.create_broadcast(
                session,
                actor_id=9,
                target="all",
                user_ids=[1, 2],
                channels=channel_names,
                exclude_blocked_telegram=False,
                texts=texts or {"en": "Hello"},
                email_subjects={"en": "News"},
                buttons=buttons or [],
                scheduled_at=datetime.now(UTC) + timedelta(hours=1),
            )

    async def _reload(self, broadcast_id: int) -> AdminBroadcast:
        async with self.factory() as session:
            item = await broadcast_dal.get_broadcast(session, broadcast_id)
        assert item is not None
        return item

    async def _dispatch(self, broadcast_id: int) -> Any:
        # SQLite returns naive datetimes, so the real claim path cannot compare
        # them with UTC. Mark the campaign running and hand the same row to the
        # service; production uses the real ``begin_broadcast`` claim.
        async with self.factory() as session:
            item = await broadcast_dal.get_broadcast(session, broadcast_id)
            assert item is not None
            item.status = "running"
            item.started_at = item.started_at or datetime.now(UTC)
            await session.commit()
            await session.refresh(item)
        with patch.object(
            broadcast_dal,
            "begin_broadcast",
            AsyncMock(return_value=item),
        ):
            return await self._service().dispatch(broadcast_id)

    async def _statuses(self, broadcast_id: int) -> list[tuple[str, int, str, str]]:
        async with self.factory() as session:
            rows = (
                await session.execute(
                    select(
                        AdminBroadcastDelivery.channel,
                        AdminBroadcastDelivery.user_id,
                        AdminBroadcastDelivery.status,
                        AdminBroadcastDelivery.destination_key,
                    )
                    .where(AdminBroadcastDelivery.broadcast_id == broadcast_id)
                    .order_by(AdminBroadcastDelivery.delivery_id)
                )
            ).all()
        return [
            (str(channel), int(user), str(status), str(key)) for channel, user, status, key in rows
        ]

    async def test_mixed_campaign_reports_each_channel_independently(self) -> None:
        telegram = self._provider(
            _FakeProvider(
                "telegram",
                [_recipient(1, "101"), _recipient(2, "202")],
            )
        )
        email = self._provider(
            _FakeProvider(
                "email",
                [_recipient(1, "one@example.test"), _recipient(2, "two@example.test")],
                supports_subject=True,
            )
        )
        item = await self._create(["telegram", "email"])
        assert item.recipient_count == 2
        assert item.total_deliveries == 4
        assert item.channel_stats["telegram"] == {
            "users": 2,
            "deliveries": 2,
            "queued": 0,
            "sent": 0,
            "failed": 0,
            "skipped": 0,
            "reason": "",
        }

        result = await self._dispatch(int(item.broadcast_id))

        self.assertEqual(result.queued, 2)
        self.assertEqual(result.email_queued, 2)
        self.assertEqual(result.failed, 0)
        self.assertEqual(telegram.dispatched, 1)
        self.assertEqual(email.dispatched, 1)
        reloaded = await self._reload(int(item.broadcast_id))
        self.assertEqual(reloaded.status, "completed")
        self.assertEqual(reloaded.telegram_sent, 2)
        self.assertEqual(reloaded.email_sent, 2)
        self.assertEqual(reloaded.channel_stats["telegram"]["sent"], 2)
        self.assertEqual(reloaded.channel_stats["email"]["sent"], 2)

    async def test_provider_failure_does_not_stop_other_channels(self) -> None:
        telegram = self._provider(
            _FakeProvider(
                "telegram",
                [_recipient(1, "101"), _recipient(2, "202")],
                fail=True,
            )
        )
        email = self._provider(
            _FakeProvider(
                "email",
                [_recipient(1, "one@example.test"), _recipient(2, "two@example.test")],
                supports_subject=True,
            )
        )
        item = await self._create(["telegram", "email"])

        result = await self._dispatch(int(item.broadcast_id))

        self.assertEqual(telegram.dispatched, 1)
        self.assertEqual(email.dispatched, 1)
        self.assertEqual(result.email_queued, 2)
        reloaded = await self._reload(int(item.broadcast_id))
        self.assertEqual(reloaded.status, "completed_with_errors")
        self.assertEqual(reloaded.telegram_failed, 2)
        self.assertEqual(reloaded.email_sent, 2)
        self.assertEqual(reloaded.channel_stats["telegram"]["reason"], "provider_error")
        self.assertEqual(reloaded.channel_stats["email"]["sent"], 2)

    async def test_endpoint_level_channel_counts_users_and_devices_separately(self) -> None:
        push = self._provider(
            _FakeProvider(
                "push",
                [
                    _recipient(1, "device-a", destination_key="device-a"),
                    _recipient(1, "device-default", destination_key="default"),
                    _recipient(1, "device-b", destination_key="device-b"),
                ],
                destination_level="endpoint",
            )
        )
        item = await self._create(["push"])
        self.assertEqual(item.recipient_count, 1)
        self.assertEqual(item.total_deliveries, 3)
        self.assertEqual(item.channel_stats["push"]["users"], 1)
        self.assertEqual(item.channel_stats["push"]["deliveries"], 3)

        result = await self._dispatch(int(item.broadcast_id))

        self.assertEqual(push.dispatched, 1)
        self.assertEqual(result.queued, 3)
        reloaded = await self._reload(int(item.broadcast_id))
        self.assertEqual(reloaded.status, "completed")
        self.assertEqual(reloaded.channel_stats["push"]["users"], 1)
        self.assertEqual(reloaded.channel_stats["push"]["deliveries"], 3)
        self.assertEqual(reloaded.channel_stats["push"]["sent"], 3)
        self.assertEqual(
            await self._statuses(int(item.broadcast_id)),
            [
                ("push", 1, "sent", "device-a"),
                ("push", 1, "sent", "default"),
                ("push", 1, "sent", "device-b"),
            ],
        )

    async def test_zero_recipient_channel_finishes_without_hanging(self) -> None:
        self._provider(_FakeProvider("telegram", [_recipient(1, "101")]))
        self._provider(_FakeProvider("email", [], supports_subject=True))
        item = await self._create(["telegram", "email"])

        result = await self._dispatch(int(item.broadcast_id))

        self.assertEqual(result.queued, 1)
        self.assertEqual(result.email_queued, 0)
        reloaded = await self._reload(int(item.broadcast_id))
        self.assertEqual(reloaded.status, "completed")
        self.assertEqual(reloaded.channel_stats["email"]["deliveries"], 0)
        self.assertEqual(reloaded.channel_stats["telegram"]["sent"], 1)

    async def test_missing_and_unavailable_channels_finish_with_an_outcome(self) -> None:
        self._provider(_FakeProvider("telegram", [_recipient(1, "101")]))
        self._provider(_FakeProvider("locked", [], available=False))
        saved = dict(channels._CHANNEL_PROVIDERS)
        item = await self._create(["telegram", "locked"])
        channels._CHANNEL_PROVIDERS.pop("locked", None)
        try:
            result = await self._dispatch(int(item.broadcast_id))
        finally:
            channels._CHANNEL_PROVIDERS.clear()
            channels._CHANNEL_PROVIDERS.update(saved)

        self.assertEqual(result.channel_statuses["locked"]["outcome"], "provider_absent")
        self.assertEqual(
            result.channel_statuses["telegram"]["outcome"],
            "",
        )
        reloaded = await self._reload(int(item.broadcast_id))
        self.assertEqual(reloaded.status, "completed_with_errors")
        self.assertEqual(reloaded.channel_stats["locked"]["reason"], "provider_absent")
        self.assertEqual(reloaded.channel_stats["telegram"]["sent"], 1)

    async def test_unavailable_provider_never_resolves_or_queues(self) -> None:
        telegram = self._provider(_FakeProvider("telegram", [_recipient(1, "101")]))
        locked = self._provider(_FakeProvider("locked", [], available=False))
        item = await self._create(["telegram", "locked"])

        result = await self._dispatch(int(item.broadcast_id))

        self.assertEqual(telegram.dispatched, 1)
        self.assertEqual(locked.dispatched, 0)
        self.assertEqual(result.channel_statuses["locked"]["outcome"], "provider_not_configured")
        reloaded = await self._reload(int(item.broadcast_id))
        self.assertEqual(reloaded.status, "completed_with_errors")
        self.assertEqual(reloaded.channel_stats["locked"]["reason"], "provider_not_configured")

    async def test_restart_recovery_requeues_only_interrupted_rows(self) -> None:
        telegram = self._provider(
            _FakeProvider("telegram", [_recipient(1, "101"), _recipient(2, "202")])
        )
        item = await self._create(["telegram"])
        async with self.factory() as session:
            await broadcast_dal.add_deliveries(
                session,
                item,
                [
                    {
                        "user_id": 1,
                        "channel": "telegram",
                        "destination": "101",
                        "destination_key": "default",
                    },
                    {
                        "user_id": 2,
                        "channel": "telegram",
                        "destination": "202",
                        "destination_key": "default",
                    },
                ],
            )
            await session.execute(
                update(AdminBroadcastDelivery)
                .where(AdminBroadcastDelivery.broadcast_id == item.broadcast_id)
                .values(status="queued")
            )
            await session.execute(
                update(AdminBroadcast)
                .where(AdminBroadcast.broadcast_id == item.broadcast_id)
                .values(status="running")
            )
            await session.commit()
        async with self.factory() as session:
            recovered = await broadcast_dal.recover_interrupted_broadcasts(session)
        self.assertEqual(recovered, 2)

        result = await self._dispatch(int(item.broadcast_id))

        self.assertEqual(result.queued, 2)
        self.assertEqual(telegram.dispatched, 1)
        reloaded = await self._reload(int(item.broadcast_id))
        self.assertEqual(reloaded.status, "completed")
        self.assertEqual(reloaded.channel_stats["telegram"]["sent"], 2)

    async def test_empty_channel_does_not_finalize_before_queued_email_recovers(self) -> None:
        self._provider(_FakeProvider("telegram", []))
        email = self._provider(
            _FakeProvider(
                "email",
                [_recipient(1, "one@example.test")],
                supports_subject=True,
                defer_results=True,
            )
        )
        item = await self._create(["telegram", "email"])

        result = await self._dispatch(int(item.broadcast_id))

        self.assertEqual(result.queued, 0)
        self.assertEqual(result.email_queued, 1)
        first = await self._reload(int(item.broadcast_id))
        self.assertEqual(first.status, "running")
        self.assertEqual(first.successful_deliveries, 0)
        self.assertEqual(first.failed_deliveries, 0)
        self.assertEqual(
            await self._statuses(int(item.broadcast_id)),
            [("email", 1, "queued", "default")],
        )

        # The queue callback vanished with the process. Simulate the legacy bug
        # where this campaign was already marked completed while a row lived,
        # then verify recovery still finds it and redelivers.
        old_finished_at = datetime(2026, 1, 1, tzinfo=UTC)
        async with self.factory() as session:
            await session.execute(
                update(AdminBroadcast)
                .where(AdminBroadcast.broadcast_id == item.broadcast_id)
                .values(status="completed", finished_at=old_finished_at)
            )
            await session.commit()
            recovered = await broadcast_dal.recover_interrupted_broadcasts(session)
        self.assertEqual(recovered, 1)
        restarted = await self._reload(int(item.broadcast_id))
        self.assertEqual(restarted.status, "queued")
        self.assertIsNone(restarted.finished_at)

        email.defer_results = False
        result = await self._dispatch(int(item.broadcast_id))

        self.assertEqual(result.email_queued, 1)
        final = await self._reload(int(item.broadcast_id))
        self.assertEqual(final.status, "completed")
        self.assertEqual(final.successful_deliveries, 1)
        self.assertEqual(final.email_sent, 1)
        self.assertIsNotNone(final.finished_at)
        assert final.finished_at is not None
        final_finished_at = final.finished_at
        if final_finished_at.tzinfo is None:
            final_finished_at = final_finished_at.replace(tzinfo=UTC)
        self.assertGreater(final_finished_at, old_finished_at)
        self.assertEqual(
            await self._statuses(int(item.broadcast_id)),
            [("email", 1, "sent", "default")],
        )

    async def test_queued_channel_failure_after_empty_channel_finalizes_correctly(self) -> None:
        self._provider(_FakeProvider("telegram", []))
        self._provider(
            _FakeProvider(
                "email",
                [_recipient(1, "one@example.test")],
                supports_subject=True,
                defer_results=True,
            )
        )
        item = await self._create(["telegram", "email"])

        await self._dispatch(int(item.broadcast_id))
        async with self.factory() as session:
            delivery_id = (
                await session.execute(
                    select(AdminBroadcastDelivery.delivery_id).where(
                        AdminBroadcastDelivery.broadcast_id == item.broadcast_id
                    )
                )
            ).scalar_one()
        await self._service()._mark_result(delivery_id, success=False, error="smtp_down")

        reloaded = await self._reload(int(item.broadcast_id))
        self.assertEqual(reloaded.status, "completed_with_errors")
        self.assertEqual(reloaded.failed_deliveries, 1)
        self.assertEqual(reloaded.email_failed, 1)
        self.assertEqual(reloaded.channel_stats["email"]["failed"], 1)

    async def test_recovered_missing_provider_rows_are_terminalized(self) -> None:
        self._provider(_FakeProvider("push", [_recipient(1, "device-a")]))
        item = await self._create(["push"])
        async with self.factory() as session:
            await broadcast_dal.add_deliveries(
                session,
                item,
                [
                    {
                        "user_id": 1,
                        "channel": "push",
                        "destination": "device-a",
                        "destination_key": "device-a",
                    }
                ],
            )
            await session.execute(
                update(AdminBroadcastDelivery)
                .where(AdminBroadcastDelivery.broadcast_id == item.broadcast_id)
                .values(status="queued")
            )
            await session.execute(
                update(AdminBroadcast)
                .where(AdminBroadcast.broadcast_id == item.broadcast_id)
                .values(status="running")
            )
            await session.commit()
        async with self.factory() as session:
            recovered = await broadcast_dal.recover_interrupted_broadcasts(session)
        self.assertEqual(recovered, 1)

        saved = dict(channels._CHANNEL_PROVIDERS)
        channels._CHANNEL_PROVIDERS.pop("push", None)
        try:
            result = await self._dispatch(int(item.broadcast_id))
        finally:
            channels._CHANNEL_PROVIDERS.clear()
            channels._CHANNEL_PROVIDERS.update(saved)

        self.assertEqual(result.channel_statuses["push"]["outcome"], "provider_absent")
        self.assertEqual(result.channel_statuses["push"]["failed"], 1)
        reloaded = await self._reload(int(item.broadcast_id))
        self.assertEqual(reloaded.status, "completed_with_errors")
        self.assertEqual(reloaded.channel_stats["push"]["failed"], 1)
        self.assertEqual(reloaded.channel_stats["push"]["reason"], "provider_absent")
        self.assertEqual(
            await self._statuses(int(item.broadcast_id)),
            [("push", 1, "failed", "device-a")],
        )

    async def test_telegram_language_is_persisted_without_shortcodes(self) -> None:
        async with self.factory() as session:
            session.add(User(user_id=1, language_code="ru"))
            await session.commit()
        provider = self._provider(
            _FakeProvider(
                "telegram",
                [_recipient(1, "101", language_code=None)],
            )
        )
        item = await self._create(
            ["telegram"],
            texts={"ru": "Привет", "en": "Hello"},
            buttons=[
                {
                    "kind": "url",
                    "label": "",
                    "url": "https://example.test",
                    "labels": {"ru": "Открыть", "en": "Open"},
                }
            ],
        )

        await self._dispatch(int(item.broadcast_id))

        self.assertEqual(len(provider.messages), 1)
        self.assertEqual(provider.messages[0].text, "Привет")
        self.assertEqual(provider.messages[0].language_code, "ru")
        self.assertEqual(provider.messages[0].buttons[0].label, "Открыть")
        async with self.factory() as session:
            persisted_language = (
                await session.execute(
                    select(AdminBroadcastDelivery.language_code).where(
                        AdminBroadcastDelivery.broadcast_id == item.broadcast_id
                    )
                )
            ).scalar_one()
        self.assertEqual(persisted_language, "ru")

    async def test_historical_legacy_rows_keep_old_counters_and_stats(self) -> None:
        self._provider(_FakeProvider("telegram", []))
        self._provider(_FakeProvider("email", [], supports_subject=True))
        item = await self._create(["telegram", "email"])
        async with self.factory() as session:
            session.add_all(
                [
                    AdminBroadcastDelivery(
                        broadcast_id=item.broadcast_id,
                        user_id=10,
                        channel="telegram",
                        destination_key="legacy",
                        destination="110",
                        status="sent",
                    ),
                    AdminBroadcastDelivery(
                        broadcast_id=item.broadcast_id,
                        user_id=11,
                        channel="email",
                        destination_key="legacy",
                        destination="eleven@example.test",
                        status="failed",
                        error="smtp_down",
                    ),
                ]
            )
            await session.commit()
            await broadcast_dal.refresh_broadcast_stats(session, int(item.broadcast_id))

        reloaded = await self._reload(int(item.broadcast_id))
        self.assertEqual(reloaded.recipient_count, 2)
        self.assertEqual(reloaded.total_deliveries, 2)
        self.assertEqual(reloaded.telegram_sent, 1)
        self.assertEqual(reloaded.email_failed, 1)
        self.assertEqual(reloaded.channel_stats["telegram"]["sent"], 1)
        self.assertEqual(reloaded.channel_stats["email"]["failed"], 1)
        self.assertEqual(
            await self._statuses(int(item.broadcast_id)),
            [
                ("telegram", 10, "sent", "legacy"),
                ("email", 11, "failed", "legacy"),
            ],
        )

        async with self.factory() as session:
            pending = await broadcast_dal.add_deliveries(
                session,
                item,
                [
                    {
                        "user_id": 10,
                        "channel": "telegram",
                        "destination": "110",
                        "destination_key": "default",
                    }
                ],
            )
            total_rows = (
                (
                    await session.execute(
                        select(AdminBroadcastDelivery).where(
                            AdminBroadcastDelivery.broadcast_id == item.broadcast_id
                        )
                    )
                )
                .scalars()
                .all()
            )
        self.assertEqual(pending, [])
        self.assertEqual(len(total_rows), 2)
