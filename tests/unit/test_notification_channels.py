"""Unit tests for the ONB-06 notification channel contract.

Covers channel registration, availability, preferences, outcome reporting,
absent-provider behavior, unknown channels (which must never fall through to
email), idempotent occurrence identity with reliable enqueue boundaries, and
the Core push eligibility policy.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from bot.services import notification_channels as channels
from bot.services.notification_channels import (
    ChannelOutcomeStatus,
    NotificationChannel,
    NotificationChannelOutcome,
    NotificationIntent,
    NotificationOccurrence,
    fanout_push_occurrence,
    get_notification_channel_provider,
    occurrence_id_for,
    parse_notification_channel,
    plaintext_for_push,
    push_provider_available,
    register_notification_channel_provider,
    resolve_channel_destinations,
    unregister_notification_channel_provider,
)
from bot.services.user_notification_policy import (
    UserNotificationCategory,
    user_notification_channel_allowed,
    user_notification_channel_selected,
    user_notification_delivery_plan,
)
from tests.support.settings_stub import settings_stub


class FakePushProvider:
    name = "push"

    def __init__(
        self,
        *,
        configured: bool = True,
        destinations: tuple[str, ...] = ("device-1",),
        failures: tuple[str, ...] = (),
    ) -> None:
        self.configured = configured
        self.destinations = destinations
        self.failures = failures
        self.calls: list[dict[str, Any]] = []

    def is_configured(self, settings: Any) -> bool:
        del settings
        return self.configured

    def destinations_for_user(self, settings: Any, user: Any) -> tuple[str, ...]:
        del settings, user
        return self.destinations

    async def deliver(
        self,
        *,
        settings: Any,
        user: Any,
        occurrence: NotificationOccurrence,
        destination: str,
    ) -> NotificationChannelOutcome:
        del settings, user
        self.calls.append({"occurrence_id": occurrence.occurrence_id, "destination": destination})
        if destination in self.failures:
            raise RuntimeError(f"delivery boom for {destination}")
        return NotificationChannelOutcome(
            channel="push",
            destination=destination,
            status=ChannelOutcomeStatus.SENT,
            reason="sent",
        )


@pytest.fixture
def isolated_channels():
    saved_providers = dict(channels._CHANNEL_PROVIDERS)
    channels._CHANNEL_PROVIDERS.clear()
    channels._notification_delivery_cache.clear()
    yield channels
    channels._CHANNEL_PROVIDERS.clear()
    channels._CHANNEL_PROVIDERS.update(saved_providers)
    channels._notification_delivery_cache.clear()


def _settings(**overrides: Any) -> Any:
    values: dict[str, Any] = {"USER_NOTIFICATION_PUSH_ENABLED": True}
    values.update(overrides)
    return settings_stub(**values)


def _user(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "user_id": 42,
        "telegram_id": None,
        "email": "",
        "language_code": "en",
        "telegram_notifications_status": None,
        "push_notifications_enabled": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _occurrence(**overrides: Any) -> NotificationOccurrence:
    values: dict[str, Any] = {
        "occurrence_id": "test-occurrence:1",
        "category": UserNotificationCategory.PAYMENTS,
        "user_id": 42,
        "push_title": "Title",
        "push_body": "Body",
        "push_url": "https://app.example.test/",
    }
    values.update(overrides)
    return NotificationOccurrence(**values)


def run(coro):
    return asyncio.run(coro)


def test_known_channel_set_is_closed():
    assert {channel.value for channel in NotificationChannel} == {"telegram", "email", "push"}


def test_unknown_channel_never_resolves(isolated_channels):
    for unknown in ("sms", "webhook", "", "emails", None, 42):
        assert parse_notification_channel(unknown) is None
        assert get_notification_channel_provider(unknown) is None
    # Known names normalize case and surrounding whitespace.
    assert parse_notification_channel("EMAIL ") == NotificationChannel.EMAIL


def test_unknown_channel_never_selected_allowed_or_planned(isolated_channels):
    settings = _settings()
    user = _user()
    for unknown in ("sms", "webhook", ""):
        selected = user_notification_channel_selected(
            settings, UserNotificationCategory.PAYMENTS, unknown
        )
        allowed = user_notification_channel_allowed(
            user, UserNotificationCategory.PAYMENTS, unknown
        )
        assert selected is False
        assert allowed is False
    # And unknown channels never enable email as a side effect.
    plan = user_notification_delivery_plan(settings, UserNotificationCategory.PAYMENTS, user)
    assert plan.email is False


def test_provider_registration_round_trip(isolated_channels):
    provider = FakePushProvider()
    assert get_notification_channel_provider("push") is None
    register_notification_channel_provider(provider)
    assert get_notification_channel_provider("push") is provider
    assert get_notification_channel_provider(NotificationChannel.PUSH) is provider
    assert unregister_notification_channel_provider("push") is True
    assert get_notification_channel_provider("push") is None
    assert unregister_notification_channel_provider("push") is False


def test_registering_unknown_channel_name_raises(isolated_channels):
    with pytest.raises(ValueError):
        register_notification_channel_provider(SimpleNamespace(name="sms"))


def test_push_provider_availability(isolated_channels):
    settings = _settings()
    assert push_provider_available(settings) is False
    register_notification_channel_provider(FakePushProvider(configured=False))
    assert push_provider_available(settings) is False
    register_notification_channel_provider(FakePushProvider(configured=True))
    assert push_provider_available(settings) is True


def test_occurrence_requires_stable_identity():
    with pytest.raises(ValueError):
        NotificationOccurrence(
            occurrence_id="  ",
            category=UserNotificationCategory.PAYMENTS,
            user_id=42,
        )


def test_occurrence_id_builder_skips_empties():
    assert occurrence_id_for("referral-bonus", 7, None, "", "payment", 0) == (
        "referral-bonus:7:payment:0"
    )
    with pytest.raises(ValueError):
        occurrence_id_for(None, "")


def test_plaintext_for_push_strips_markup():
    assert plaintext_for_push("<b>Hi</b>  user &amp; friends") == "Hi user & friends"
    assert plaintext_for_push(None) == ""
    long_text = "x" * 300
    assert plaintext_for_push(long_text, limit=240).endswith("…")
    assert len(plaintext_for_push(long_text, limit=240)) == 240


def test_intent_splits_known_and_unknown_channels():
    intent = NotificationIntent(
        occurrence=_occurrence(),
        channels=("telegram", "push", "sms"),
    )
    assert intent.known_channels() == (NotificationChannel.TELEGRAM, NotificationChannel.PUSH)
    assert intent.unknown_channels() == ("sms",)


def test_banned_user_skipped_without_provider_call(isolated_channels):
    provider = FakePushProvider()
    register_notification_channel_provider(provider)
    outcomes = run(
        fanout_push_occurrence(
            settings=_settings(), user=_user(is_banned=True), occurrence=_occurrence()
        )
    )
    assert [(outcome.status, outcome.reason) for outcome in outcomes] == [
        (ChannelOutcomeStatus.SKIPPED, "banned")
    ]
    assert provider.calls == []


@pytest.mark.parametrize("flag", [{"sensitive": True}, {"admin_only": True}])
def test_sensitive_and_admin_only_excluded_from_push(isolated_channels, flag):
    provider = FakePushProvider()
    register_notification_channel_provider(provider)
    outcomes = run(
        fanout_push_occurrence(settings=_settings(), user=_user(), occurrence=_occurrence(**flag))
    )
    assert len(outcomes) == 1
    assert outcomes[0].status == ChannelOutcomeStatus.SKIPPED
    assert provider.calls == []


def test_absent_provider_reported_as_skip(isolated_channels):
    outcomes = run(
        fanout_push_occurrence(settings=_settings(), user=_user(), occurrence=_occurrence())
    )
    assert [(outcome.status, outcome.reason) for outcome in outcomes] == [
        (ChannelOutcomeStatus.SKIPPED, "provider_absent")
    ]


def test_unconfigured_provider_reported_as_skip(isolated_channels):
    register_notification_channel_provider(FakePushProvider(configured=False))
    outcomes = run(
        fanout_push_occurrence(settings=_settings(), user=_user(), occurrence=_occurrence())
    )
    assert [(outcome.status, outcome.reason) for outcome in outcomes] == [
        (ChannelOutcomeStatus.SKIPPED, "provider_not_configured")
    ]


def test_no_destinations_reported_as_skip(isolated_channels):
    register_notification_channel_provider(FakePushProvider(destinations=()))
    outcomes = run(
        fanout_push_occurrence(settings=_settings(), user=_user(), occurrence=_occurrence())
    )
    assert [(outcome.status, outcome.reason) for outcome in outcomes] == [
        (ChannelOutcomeStatus.SKIPPED, "no_destinations")
    ]


def test_user_opt_out_and_channel_disabled(isolated_channels):
    register_notification_channel_provider(FakePushProvider())
    outcomes = run(
        fanout_push_occurrence(
            settings=_settings(),
            user=_user(push_notifications_enabled=False),
            occurrence=_occurrence(),
        )
    )
    assert [(outcome.status, outcome.reason) for outcome in outcomes] == [
        (ChannelOutcomeStatus.SKIPPED, "user_opted_out")
    ]
    outcomes = run(
        fanout_push_occurrence(
            settings=_settings(USER_NOTIFICATION_PUSH_ENABLED=False),
            user=_user(),
            occurrence=_occurrence(),
        )
    )
    assert [(outcome.status, outcome.reason) for outcome in outcomes] == [
        (ChannelOutcomeStatus.SKIPPED, "channel_disabled")
    ]


def test_user_mismatch_guard(isolated_channels):
    register_notification_channel_provider(FakePushProvider())
    outcomes = run(
        fanout_push_occurrence(
            settings=_settings(), user=_user(user_id=99), occurrence=_occurrence(user_id=42)
        )
    )
    assert [(outcome.status, outcome.reason) for outcome in outcomes] == [
        (ChannelOutcomeStatus.SKIPPED, "user_mismatch")
    ]


def test_fanout_reaches_every_subscribed_device(isolated_channels):
    provider = FakePushProvider(destinations=("device-1", "device-2"))
    register_notification_channel_provider(provider)
    outcomes = run(
        fanout_push_occurrence(settings=_settings(), user=_user(), occurrence=_occurrence())
    )
    assert [(outcome.status, outcome.destination) for outcome in outcomes] == [
        (ChannelOutcomeStatus.SENT, "device-1"),
        (ChannelOutcomeStatus.SENT, "device-2"),
    ]
    assert [call["destination"] for call in provider.calls] == ["device-1", "device-2"]


def test_independent_destination_failure_does_not_suppress_others(isolated_channels):
    provider = FakePushProvider(destinations=("device-1", "device-2"), failures=("device-1",))
    register_notification_channel_provider(provider)
    outcomes = run(
        fanout_push_occurrence(settings=_settings(), user=_user(), occurrence=_occurrence())
    )
    by_destination = {outcome.destination: outcome for outcome in outcomes}
    assert by_destination["device-1"].status == ChannelOutcomeStatus.FAILED
    assert by_destination["device-1"].reason.startswith("delivery_error:")
    assert by_destination["device-2"].status == ChannelOutcomeStatus.SENT


def test_failed_claim_released_for_retry(isolated_channels):
    provider = FakePushProvider(destinations=("device-1",), failures=("device-1",))
    register_notification_channel_provider(provider)
    first = run(
        fanout_push_occurrence(settings=_settings(), user=_user(), occurrence=_occurrence())
    )
    assert first[0].status == ChannelOutcomeStatus.FAILED
    provider.failures = ()
    second = run(
        fanout_push_occurrence(settings=_settings(), user=_user(), occurrence=_occurrence())
    )
    assert second[0].status == ChannelOutcomeStatus.SENT
    assert len(provider.calls) == 2


def test_duplicates_suppressed_per_destination(isolated_channels):
    provider = FakePushProvider(destinations=("device-1", "device-2"))
    register_notification_channel_provider(provider)
    first = run(
        fanout_push_occurrence(settings=_settings(), user=_user(), occurrence=_occurrence())
    )
    assert all(outcome.status == ChannelOutcomeStatus.SENT for outcome in first)
    second = run(
        fanout_push_occurrence(settings=_settings(), user=_user(), occurrence=_occurrence())
    )
    assert [(outcome.status, outcome.reason) for outcome in second] == [
        (ChannelOutcomeStatus.SKIPPED, "duplicate"),
        (ChannelOutcomeStatus.SKIPPED, "duplicate"),
    ]
    assert len(provider.calls) == 2
    # A different occurrence is a different business event and still delivers.
    third = run(
        fanout_push_occurrence(
            settings=_settings(),
            user=_user(),
            occurrence=_occurrence(occurrence_id="test-occurrence:2"),
        )
    )
    assert all(outcome.status == ChannelOutcomeStatus.SENT for outcome in third)


def test_push_eligibility_independent_of_telegram(isolated_channels):
    provider = FakePushProvider()
    register_notification_channel_provider(provider)
    user = _user(
        telegram_id=None,
        email="",
        telegram_notifications_status="blocked",
        system_notifications_telegram_enabled=False,
        system_notifications_email_enabled=False,
    )
    outcomes = run(
        fanout_push_occurrence(settings=_settings(), user=user, occurrence=_occurrence())
    )
    assert [(outcome.status, outcome.reason) for outcome in outcomes] == [
        (ChannelOutcomeStatus.SENT, "sent")
    ]


def test_plan_push_defaults_and_single_preference(isolated_channels):
    user = _user()
    settings = _settings()
    # No provider registered: push stays out of the plan, Telegram/email intact.
    plan = user_notification_delivery_plan(settings, UserNotificationCategory.PAYMENTS, user)
    assert (plan.telegram, plan.email, plan.push) == (False, False, False)

    register_notification_channel_provider(FakePushProvider())
    plan = user_notification_delivery_plan(settings, UserNotificationCategory.PAYMENTS, user)
    assert plan.push is True

    # One push preference covers marketing and support too, independent of
    # the Telegram/email category preferences.
    telegram_opt_out = _user(
        marketing_notifications_telegram_enabled=False,
        marketing_notifications_email_enabled=False,
        system_notifications_telegram_enabled=False,
        system_notifications_email_enabled=False,
    )
    for category in (
        UserNotificationCategory.MARKETING,
        UserNotificationCategory.SUPPORT,
        UserNotificationCategory.REFERRALS,
    ):
        plan = user_notification_delivery_plan(settings, category, telegram_opt_out)
        assert plan.push is True, category

    opted_out = _user(push_notifications_enabled=False)
    for category in (
        UserNotificationCategory.MARKETING,
        UserNotificationCategory.SUPPORT,
        UserNotificationCategory.PAYMENTS,
    ):
        plan = user_notification_delivery_plan(settings, category, opted_out)
        assert plan.push is False, category


def test_plan_push_does_not_join_single_channel_fallback(isolated_channels):
    register_notification_channel_provider(FakePushProvider())
    settings = _settings(
        USER_NOTIFICATION_PAYMENTS_TELEGRAM_ENABLED=True,
        USER_NOTIFICATION_PAYMENTS_EMAIL_ENABLED=False,
        USER_NOTIFICATION_SINGLE_CHANNEL_FALLBACK_ENABLED=True,
    )
    # Telegram unavailable (no chat id): fallback must not pull push along,
    # and push stays independently enabled.
    plan = user_notification_delivery_plan(
        settings, UserNotificationCategory.PAYMENTS, _user(telegram_id=None)
    )
    assert (plan.telegram, plan.email, plan.push) == (False, False, True)


def test_plan_push_for_banned_user_stays_empty(isolated_channels):
    register_notification_channel_provider(FakePushProvider())
    plan = user_notification_delivery_plan(
        _settings(), UserNotificationCategory.SUPPORT, _user(is_banned=True)
    )
    assert not plan.any_enabled


class _FakeRedis:
    """Minimal async Redis double for dedup transition tests."""

    def __init__(self, *, fail: bool = False) -> None:
        self.store: dict[str, tuple[str, float]] = {}
        self.fail = fail

    async def set(self, key, value, nx=False, ex=None):
        import time as _time

        if self.fail:
            raise RuntimeError("redis down")
        now = _time.monotonic()
        existing = self.store.get(key)
        if existing is not None and existing[1] > now:
            if nx:
                return False
        elif existing is not None:
            self.store.pop(key, None)
        self.store[key] = (value, now + float(ex or 0))
        return True

    async def delete(self, key):
        if self.fail:
            raise RuntimeError("redis down")
        self.store.pop(key, None)
        return 1


def test_crash_between_lock_and_completion_does_not_permanently_suppress(
    isolated_channels, monkeypatch
):
    import time as _time

    from bot.services import notification_channels as ch

    fake = _FakeRedis()
    monkeypatch.setattr(ch, "get_redis", _FakeRedis_getter(fake))

    settings = _settings()
    # Simulate a crash: claim the in-progress lock but never complete.
    assert run(ch.claim_notification_delivery(settings, "crash:1", "push", "d1", ttl_seconds=60))
    # Immediate replay sees the in-progress lock (concurrent protection).
    assert not run(
        ch.claim_notification_delivery(settings, "crash:1", "push", "d1", ttl_seconds=60)
    )
    # But the lock is short-lived, not a permanent 24h marker: after expiry a
    # replay redelivers instead of reporting a permanent duplicate.
    key = ch._notification_delivery_cache_key(settings, "crash:1", "push", "d1")
    ch._notification_delivery_cache[key] = (
        ch._NOTIFICATION_DELIVERY_IN_PROGRESS_VALUE,
        _time.monotonic() - 1,
    )
    fake.store.pop(key, None)
    assert run(ch.claim_notification_delivery(settings, "crash:1", "push", "d1", ttl_seconds=60))
    run(ch.complete_notification_delivery(settings, "crash:1", "push", "d1", ttl_seconds=60))
    # A completed delivery stays suppressed.
    assert not run(
        ch.claim_notification_delivery(settings, "crash:1", "push", "d1", ttl_seconds=60)
    )


def _FakeRedis_getter(fake):
    async def _get(settings):
        del settings
        return fake

    return _get


def test_redis_outage_transition_does_not_double_deliver(isolated_channels, monkeypatch):
    from bot.services import notification_channels as ch

    provider = FakePushProvider()
    register_notification_channel_provider(provider)
    settings = _settings()
    occurrence = _occurrence(occurrence_id="outage:1")

    fake = _FakeRedis()
    monkeypatch.setattr(ch, "get_redis", _FakeRedis_getter(fake))
    first = run(fanout_push_occurrence(settings=settings, user=_user(), occurrence=occurrence))
    assert all(outcome.status == ChannelOutcomeStatus.SENT for outcome in first)

    # Redis outage: the local write-through still suppresses the replay.
    async def _no_redis(settings):
        del settings
        return None

    monkeypatch.setattr(ch, "get_redis", _no_redis)
    second = run(fanout_push_occurrence(settings=settings, user=_user(), occurrence=occurrence))
    assert [(outcome.status, outcome.reason) for outcome in second] == [
        (ChannelOutcomeStatus.SKIPPED, "duplicate")
    ]
    assert len(provider.calls) == 1


def test_local_claim_reconciled_to_redis_on_recovery(isolated_channels, monkeypatch):
    from bot.services import notification_channels as ch

    provider = FakePushProvider()
    register_notification_channel_provider(provider)
    settings = _settings()
    occurrence = _occurrence(occurrence_id="outage:2")

    async def _no_redis(settings):
        del settings
        return None

    monkeypatch.setattr(ch, "get_redis", _no_redis)
    first = run(fanout_push_occurrence(settings=settings, user=_user(), occurrence=occurrence))
    assert all(outcome.status == ChannelOutcomeStatus.SENT for outcome in first)

    # Redis recovers empty: the local completion still suppresses and is
    # reconciled back to Redis so other processes agree.
    fake = _FakeRedis()
    monkeypatch.setattr(ch, "get_redis", _FakeRedis_getter(fake))
    second = run(fanout_push_occurrence(settings=settings, user=_user(), occurrence=occurrence))
    assert [(outcome.status, outcome.reason) for outcome in second] == [
        (ChannelOutcomeStatus.SKIPPED, "duplicate")
    ]
    assert len(provider.calls) == 1
    key = ch._notification_delivery_cache_key(settings, "outage:2", "push", "device-1")
    assert key in fake.store


class FakeResolvingPushProvider(FakePushProvider):
    """Provider with DB-backed async destinations and a sync compatibility shim."""

    def __init__(self, *, async_destinations: tuple[str, ...]) -> None:
        super().__init__(destinations=("sync-device",))
        self.async_destinations = async_destinations

    async def resolve_destinations(self, settings: Any, user: Any) -> tuple[str, ...]:
        del settings, user
        return self.async_destinations


def test_resolve_channel_destinations_prefers_async_resolver():
    provider = FakeResolvingPushProvider(async_destinations=("device-a", "device-b"))
    destinations = run(resolve_channel_destinations(provider, _settings(), _user()))
    assert destinations == ("device-a", "device-b")


def test_resolve_channel_destinations_keeps_sync_fallback():
    provider = FakePushProvider(destinations=("sync-device",))
    destinations = run(resolve_channel_destinations(provider, _settings(), _user()))
    assert destinations == ("sync-device",)


def test_fanout_uses_async_destinations_not_sync_shim(isolated_channels):
    provider = FakeResolvingPushProvider(async_destinations=("device-a", "device-b"))
    register_notification_channel_provider(provider)
    outcomes = run(
        fanout_push_occurrence(
            settings=_settings(),
            user=_user(),
            occurrence=_occurrence(occurrence_id="async:1"),
        )
    )
    assert [outcome.destination for outcome in outcomes] == ["device-a", "device-b"]
    assert [call["destination"] for call in provider.calls] == ["device-a", "device-b"]
