"""Contract pins for the ONB-06 notification channel contract.

These tests guard the seams the Web Push plugin (ONB-07) and the broadcast
channel contract (ONB-08) build on: the closed channel set, the provider
interface, unknown-channel behavior, the single push preference, and the
idempotent occurrence identity. Behavior details live in
``tests/unit/test_notification_channels.py``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from bot.services import notification_channels as channels
from bot.services.notification_channels import (
    ChannelOutcomeStatus,
    NotificationChannel,
    NotificationChannelOutcome,
    NotificationChannelProvider,
    NotificationOccurrence,
    fanout_push_occurrence,
)
from bot.services.user_notification_policy import (
    UserNotificationCategory,
    user_notification_channel_allowed,
    user_notification_channel_selected,
)
from db.migrator import MIGRATIONS
from db.user_notification_preference_columns import UserNotificationPreferenceColumns
from tests.support.settings_stub import settings_stub


def test_channel_set_is_closed():
    assert {channel.value for channel in NotificationChannel} == {"telegram", "email", "push"}


def test_provider_protocol_surface():
    assert issubclass(NotificationChannelProvider, object)
    assert set(NotificationChannelProvider.__protocol_attrs__) == {
        "name",
        "is_configured",
        "destinations_for_user",
        "deliver",
    }


def test_unknown_channels_never_resolve_or_enable_email():
    settings = settings_stub()
    user = SimpleNamespace()

    for unknown in ("sms", "webhook", "", None):
        assert channels.parse_notification_channel(unknown) is None
        assert channels.get_notification_channel_provider(unknown) is None
        assert (
            user_notification_channel_selected(
                settings, UserNotificationCategory.PAYMENTS, str(unknown or "")
            )
            is False
        )
        assert (
            user_notification_channel_allowed(
                user, UserNotificationCategory.PAYMENTS, str(unknown or "")
            )
            is False
        )
        assert (
            user_notification_channel_selected(
                settings, UserNotificationCategory.MARKETING, str(unknown or "")
            )
            is False
        )


def test_push_preference_model_and_migration_present():
    assert hasattr(UserNotificationPreferenceColumns, "push_notifications_enabled")
    assert MIGRATIONS[-1].id == "0110_push_notification_preference"


class _ConfiguredPushProvider:
    name = "push"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def is_configured(self, settings: Any) -> bool:
        del settings
        return True

    def destinations_for_user(self, settings: Any, user: Any) -> tuple[str, ...]:
        del settings, user
        return ("device-9",)

    async def deliver(
        self,
        *,
        settings: Any,
        user: Any,
        occurrence: NotificationOccurrence,
        destination: str,
    ) -> NotificationChannelOutcome:
        del settings, user, occurrence
        self.calls.append(destination)
        return NotificationChannelOutcome(
            channel="push",
            destination=destination,
            status=ChannelOutcomeStatus.SENT,
            reason="sent",
        )


def test_sensitive_and_admin_only_occurrences_never_reach_provider():
    saved = dict(channels._CHANNEL_PROVIDERS)
    channels._CHANNEL_PROVIDERS.clear()
    channels._notification_delivery_cache.clear()
    try:
        provider = _ConfiguredPushProvider()
        channels.register_notification_channel_provider(provider)
        settings = settings_stub(USER_NOTIFICATION_PUSH_ENABLED=True)
        user = SimpleNamespace(user_id=7, push_notifications_enabled=True)

        for flags in ({"sensitive": True}, {"admin_only": True}):
            occurrence = NotificationOccurrence(
                occurrence_id="contract-probe:1",
                category=UserNotificationCategory.PAYMENTS,
                user_id=7,
                **flags,
            )
            outcomes = asyncio.run(
                fanout_push_occurrence(settings=settings, user=user, occurrence=occurrence)
            )
            assert len(outcomes) == 1
            assert outcomes[0].status == ChannelOutcomeStatus.SKIPPED
        assert provider.calls == []
    finally:
        channels._CHANNEL_PROVIDERS.clear()
        channels._CHANNEL_PROVIDERS.update(saved)
        channels._notification_delivery_cache.clear()
