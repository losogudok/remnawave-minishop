"""Contract pins for the ONB-08 broadcast delivery-channel contract.

These tests guard the additive seam a later Web Push provider builds on: the
provider protocol, the Core-registered channel names, unknown-channel
rejection (never a fallback to email), provider absence/not-configured
outcomes, destination-key uniqueness and the legacy response shape.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import ANY, AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.app.web.admin_api_impl.broadcast import _broadcast_out
from bot.app.web.admin_api_impl.broadcast_content import selected_channel_descriptors
from bot.app.web.admin_api_impl.response_schemas import AdminBroadcastOut
from bot.services import broadcast_channels as channels
from bot.services import broadcast_channels_builtin as builtin
from bot.services.admin_broadcast_delivery import (
    AdminBroadcastDeliveryService,
    BroadcastDispatchResult,
)
from bot.services.broadcast_channels import (
    BroadcastChannelDescriptor,
    BroadcastChannelDispatch,
    BroadcastChannelMessage,
    BroadcastChannelProvider,
    BroadcastDispatchContext,
    BroadcastRecipient,
    normalize_broadcast_channels,
)
from bot.services.message_composition import MessageValidationError
from db.base import Base
from db.broadcast_models import AdminBroadcast, AdminBroadcastDelivery
from db.dal import broadcast_dal
from tests.support.settings_stub import settings_stub


def test_provider_protocol_surface() -> None:
    assert issubclass(BroadcastChannelProvider, object)
    assert set(BroadcastChannelProvider.__protocol_attrs__) == {
        "name",
        "describe",
        "resolve_recipients",
        "dispatch",
    }


def test_builtin_channels_register_lazily_and_keep_existing_order() -> None:
    assert channels.registered_broadcast_channel_names() == ("email", "telegram")
    descriptors = channels.broadcast_channel_descriptors(settings_stub())
    assert [descriptor.name for descriptor in descriptors] == ["telegram", "email"]
    assert [descriptor.label_key for descriptor in descriptors] == [
        "broadcast_channel_telegram",
        "broadcast_channel_email",
    ]


def test_unknown_channels_resolve_to_none_and_never_fall_through_to_email() -> None:
    for unknown in ("sms", "webhook", "", None):
        assert channels.parse_broadcast_channel(unknown) is None
        assert channels.get_broadcast_channel_provider(unknown) is None
    with pytest.raises(MessageValidationError) as error:
        normalize_broadcast_channels(["telegram", "sms"])
    assert error.value.code == "invalid_channel"
    assert error.value.detail == "sms"


def test_normalization_uses_provider_order_and_deduplicates() -> None:
    assert normalize_broadcast_channels(["email", "telegram", "email"]) == [
        "telegram",
        "email",
    ]
    with pytest.raises(MessageValidationError) as error:
        normalize_broadcast_channels([])
    assert error.value.code == "no_channels"


class _AbsentSession:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_args: object) -> bool:
        return False


class _AbsentSessionFactory:
    def __call__(self) -> _AbsentSession:
        return _AbsentSession()


class _UnavailableProvider:
    name = "unavailable-test"

    def describe(self, settings: Any) -> BroadcastChannelDescriptor:
        del settings
        return BroadcastChannelDescriptor(
            name=self.name,
            label_key="broadcast_channel_unavailable_test",
            fallback_label="Unavailable",
            available=False,
            reason="provider_off",
        )

    async def resolve_recipients(
        self,
        session: Any,
        *,
        user_ids: list[int],
        exclude_blocked_telegram: bool,
    ) -> list[BroadcastRecipient]:
        del session, user_ids, exclude_blocked_telegram
        raise AssertionError("unavailable provider must not resolve recipients")

    async def dispatch(self, context: BroadcastDispatchContext) -> BroadcastChannelDispatch:
        del context
        raise AssertionError("unavailable provider must not dispatch")


def _broadcast(*channel_names: str) -> AdminBroadcast:
    return cast(
        AdminBroadcast,
        SimpleNamespace(
            broadcast_id=5,
            created_by_admin_id=None,
            target="all",
            channels=list(channel_names),
            exclude_blocked_telegram=False,
            texts={"en": "Hello"},
            email_subjects={},
            buttons=[],
            image_id=None,
        ),
    )


class _Audience:
    async def resolve_user_ids(self, target: str) -> list[int]:
        del target
        return [1]


def _service() -> AdminBroadcastDeliveryService:
    return AdminBroadcastDeliveryService(
        settings=settings_stub(),
        session_factory=cast(Any, _AbsentSessionFactory()),
        i18n=cast(Any, object()),
        audience_service=cast(Any, _Audience()),
        queue_manager=None,
        bot_username=None,
    )


def _dispatch_with_provider(
    provider: BroadcastChannelProvider,
    channel: str,
) -> BroadcastDispatchResult:
    saved = dict(channels._CHANNEL_PROVIDERS)
    channels._CHANNEL_PROVIDERS[channel] = provider
    try:
        with (
            patch.object(
                broadcast_dal, "begin_broadcast", AsyncMock(return_value=_broadcast(channel))
            ),
            patch.object(
                broadcast_dal,
                "fail_channel_deliveries",
                AsyncMock(return_value=0),
            ),
            patch.object(broadcast_dal, "refresh_broadcast_stats", AsyncMock()),
            patch.object(broadcast_dal, "finish_empty_broadcast", AsyncMock()),
        ):
            return asyncio.run(_service().dispatch(5))
    finally:
        channels._CHANNEL_PROVIDERS.clear()
        channels._CHANNEL_PROVIDERS.update(saved)


def test_missing_provider_records_outcome_without_failing_the_broadcast() -> None:
    saved = dict(channels._CHANNEL_PROVIDERS)
    channels._CHANNEL_PROVIDERS.pop("telegram", None)
    try:
        with (
            patch.object(
                broadcast_dal, "begin_broadcast", AsyncMock(return_value=_broadcast("telegram"))
            ),
            patch.object(
                broadcast_dal,
                "fail_channel_deliveries",
                AsyncMock(return_value=0),
            ) as fail_channel,
            patch.object(broadcast_dal, "refresh_broadcast_stats", AsyncMock()),
            patch.object(broadcast_dal, "finish_empty_broadcast", AsyncMock()),
        ):
            result = asyncio.run(_service().dispatch(5))
    finally:
        channels._CHANNEL_PROVIDERS.clear()
        channels._CHANNEL_PROVIDERS.update(saved)

    fail_channel.assert_awaited_once_with(
        ANY,
        5,
        "telegram",
        "provider_absent",
        outcome="provider_absent",
        finalize=False,
    )
    assert result.channel_statuses["telegram"]["outcome"] == "provider_absent"
    assert result.queued == 0


def test_registered_but_unavailable_provider_reports_not_configured() -> None:
    provider = _UnavailableProvider()
    result = _dispatch_with_provider(provider, provider.name)
    assert result.channel_statuses[provider.name]["outcome"] == "provider_not_configured"
    assert result.channel_statuses[provider.name]["reason"] == "provider_off"


def test_builtin_provider_descriptors_expose_capabilities_and_stable_reasons() -> None:
    settings = settings_stub()
    with patch.object(builtin, "get_queue_manager", return_value=None):
        telegram = builtin.TelegramBroadcastChannelProvider().describe(settings)
    email = builtin.EmailBroadcastChannelProvider().describe(settings)
    assert telegram.available is False
    assert telegram.reason == "queue_unavailable"
    assert telegram.supports_subject is False
    assert telegram.max_text_length == 4096
    assert email.available is False
    assert email.reason == "email_not_configured"
    assert email.supports_subject is True


def test_destination_keys_allow_multiple_rows_per_user_and_channel() -> None:
    async def run() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(
                    Base.metadata.create_all,
                    tables=[AdminBroadcast.__table__, AdminBroadcastDelivery.__table__],
                )
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                broadcast = AdminBroadcast(
                    status="queued",
                    target="all",
                    channels=["push"],
                    texts={"en": "Hello"},
                    email_subjects={},
                    buttons=[],
                    scheduled_at=datetime.now(UTC),
                    recipient_count=1,
                    total_deliveries=2,
                    channel_stats={},
                )
                session.add(broadcast)
                await session.commit()
                await session.refresh(broadcast)
                deliveries = await broadcast_dal.add_deliveries(
                    session,
                    broadcast,
                    [
                        {
                            "user_id": 1,
                            "channel": "push",
                            "destination": "device-a",
                            "destination_key": "device-a",
                        },
                        {
                            "user_id": 1,
                            "channel": "push",
                            "destination": "device-default",
                            "destination_key": "default",
                        },
                        {
                            "user_id": 1,
                            "channel": "push",
                            "destination": "device-b",
                            "destination_key": "device-b",
                        },
                        {
                            "user_id": 1,
                            "channel": "push",
                            "destination": "device-a",
                            "destination_key": "device-a",
                        },
                    ],
                )
                assert len(deliveries) == 3
                assert {item.destination_key for item in deliveries} == {
                    "device-a",
                    "default",
                    "device-b",
                }
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_legacy_response_keeps_old_counters_and_adds_channel_stats() -> None:
    now = datetime.now(UTC)
    item = SimpleNamespace(
        broadcast_id=7,
        status="completed",
        target="all",
        channels=["telegram", "email"],
        exclude_blocked_telegram=False,
        texts={"en": "Hello"},
        email_subjects={},
        buttons=[],
        image_id=None,
        scheduled_at=now,
        created_at=now,
        started_at=now,
        finished_at=now,
        updated_at=now,
        recipient_count=2,
        total_deliveries=3,
        successful_deliveries=2,
        failed_deliveries=1,
        telegram_sent=1,
        telegram_failed=0,
        email_sent=1,
        email_failed=1,
        channel_stats={
            "telegram": {"users": 1, "deliveries": 1, "sent": 1},
            "email": {"users": 1, "deliveries": 2, "sent": 1, "failed": 1},
        },
        last_error=None,
    )
    payload = _broadcast_out(cast(AdminBroadcast, item))
    assert isinstance(payload, AdminBroadcastOut)
    assert payload.telegram_sent == 1
    assert payload.email_failed == 1
    assert payload.channel_stats["email"]["failed"] == 1


def test_api_rejects_unknown_and_unavailable_channels_without_rerouting() -> None:
    _, unknown_error = selected_channel_descriptors(settings_stub(), ["sms"])
    assert unknown_error is not None
    assert unknown_error.status == 400
    assert unknown_error.text is not None and '"invalid_channel"' in unknown_error.text

    _, unavailable_error = selected_channel_descriptors(settings_stub(), ["email"])
    assert unavailable_error is not None
    assert unavailable_error.status == 503
    assert unavailable_error.text is not None and '"email_not_configured"' in unavailable_error.text


def test_recipient_and_message_contract_shapes() -> None:
    recipient = BroadcastRecipient(
        user_id=1,
        destination="device-a",
        destination_key="device-a",
        language_code="en",
    )
    message = BroadcastChannelMessage(
        delivery_id=3,
        user_id=1,
        destination="device-a",
        destination_key="device-a",
        language_code="en",
        text="Hello",
        subject="News",
        buttons=[],
    )
    assert recipient.destination_key == "device-a"
    assert message.subject == "News"
