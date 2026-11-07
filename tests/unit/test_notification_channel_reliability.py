"""Round-2 reliability tests for the ONB-06 notification channel contract.

Covers the five remaining review findings:

1. An observed in-progress Redis lock is retryable, never cached as a 24h
   completion, and only completions reconcile back to Redis (with TTL).
2. A torrent partial failure commits no per-destination audit rows, so the
   fingerprint-wide suppression cannot swallow the retry of failed devices.
3. A repeated ``succeeded`` payment callback still retries the customer push
   via fanout dedup.
4. Warning pushes retry on later ticks despite the already recorded DB warning
   marker, tracked separately per destination.
5. Traffic-reset notices reach push-only users with inventory coverage.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time as _time
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.orm import sessionmaker

from bot.payment_providers.shared import success as success_module
from bot.payment_providers.shared.success import PaymentSuccessRequest
from bot.services import notification_channels as channels
from bot.services import tariff_worker_core as core
from bot.services import tariff_worker_shared as shared
from bot.services import torrent_blocker_notifications as torrent_module
from bot.services.notification_channels import (
    ChannelOutcomeStatus,
    NotificationChannelOutcome,
    NotificationDeliveryClaim,
    NotificationOccurrence,
    fanout_push_occurrence,
    try_claim_notification_delivery,
)
from bot.services.tariff_worker_core import TariffWorkerCoreMixin
from bot.services.tariff_worker_regular_warnings import TariffWorkerRegularWarningMixin
from bot.services.user_notification_policy import UserNotificationCategory
from db.dal import tariff_dal
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


def run(coro):
    return asyncio.run(coro)


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
        "occurrence_id": "reliability:1",
        "category": UserNotificationCategory.PAYMENTS,
        "user_id": 42,
        "push_title": "Title",
        "push_body": "Body",
        "push_url": "https://app.example.test/",
    }
    values.update(overrides)
    return NotificationOccurrence(**values)


class _StatefulFakeRedis:
    """Redis double honoring NX locks, values, and TTL expiry."""

    def __init__(self) -> None:
        self.store: dict[str, tuple[str, float]] = {}

    async def set(self, key, value, nx=False, ex=None):
        now = _time.monotonic()
        existing = self.store.get(key)
        if existing is not None and existing[1] > now:
            if nx:
                return False
        else:
            self.store.pop(key, None)
        self.store[key] = (value, now + float(ex or 0))
        return True

    async def get(self, key):
        entry = self.store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at <= _time.monotonic():
            self.store.pop(key, None)
            return None
        return value

    async def ttl(self, key):
        entry = self.store.get(key)
        if entry is None:
            return -2
        _, expires_at = entry
        remaining = expires_at - _time.monotonic()
        if remaining < 0:
            self.store.pop(key, None)
            return -2
        return math.ceil(remaining)

    async def delete(self, key):
        self.store.pop(key, None)
        return 1


class _LockOnlyFakeRedis:
    """Old-style double with set/delete only (no value/TTL introspection)."""

    def __init__(self) -> None:
        self.store: dict[str, tuple[str, float]] = {}

    async def set(self, key, value, nx=False, ex=None):
        now = _time.monotonic()
        existing = self.store.get(key)
        if existing is not None and existing[1] > now:
            if nx:
                return False
        else:
            self.store.pop(key, None)
        self.store[key] = (value, now + float(ex or 0))
        return True

    async def delete(self, key):
        self.store.pop(key, None)
        return 1


def _redis_getter(fake):
    async def _get(settings):
        del settings
        return fake

    return _get


# ---------------------------------------------------------------------------
# 1. In-progress lock vs completion
# ---------------------------------------------------------------------------


def test_lock_contention_stays_retryable_not_completed(isolated_channels, monkeypatch):
    fake = _StatefulFakeRedis()
    monkeypatch.setattr(channels, "get_redis", _redis_getter(fake))
    settings = _settings(REDIS_URL="redis://fake.test")

    assert (
        run(try_claim_notification_delivery(settings, "occ:lock", "push", "d1", ttl_seconds=60))
        == NotificationDeliveryClaim.ACQUIRED
    )
    # B observes A's lock: retryable in-progress, not a duplicate.
    assert (
        run(try_claim_notification_delivery(settings, "occ:lock", "push", "d1", ttl_seconds=60))
        == NotificationDeliveryClaim.IN_PROGRESS
    )
    # Nothing was promoted to a completion: Redis still holds the lock value
    # and the local mirror is in-progress, not done.
    key = channels._notification_delivery_cache_key(settings, "occ:lock", "push", "d1")
    assert fake.store[key][0] == channels._NOTIFICATION_DELIVERY_IN_PROGRESS_VALUE
    local = channels._notification_delivery_cache.get(key)
    assert local is not None
    assert local[0] == channels._NOTIFICATION_DELIVERY_IN_PROGRESS_VALUE
    # A fails and releases the lock; B's replay acquires it.
    run(channels.release_notification_delivery(settings, "occ:lock", "push", "d1"))
    assert key not in fake.store
    assert (
        run(try_claim_notification_delivery(settings, "occ:lock", "push", "d1", ttl_seconds=60))
        == NotificationDeliveryClaim.ACQUIRED
    )


def test_completed_claim_reconciles_done_with_remaining_ttl(isolated_channels, monkeypatch):
    fake = _StatefulFakeRedis()
    monkeypatch.setattr(channels, "get_redis", _redis_getter(fake))
    settings = _settings(REDIS_URL="redis://fake.test")

    assert (
        run(try_claim_notification_delivery(settings, "occ:done", "push", "d1", ttl_seconds=60))
        == NotificationDeliveryClaim.ACQUIRED
    )
    run(channels.complete_notification_delivery(settings, "occ:done", "push", "d1", ttl_seconds=60))

    # A second process with an empty local cache observes the completion.
    channels._notification_delivery_cache.clear()
    assert (
        run(try_claim_notification_delivery(settings, "occ:done", "push", "d1", ttl_seconds=60))
        == NotificationDeliveryClaim.COMPLETED
    )
    key = channels._notification_delivery_cache_key(settings, "occ:done", "push", "d1")
    assert fake.store[key][0] == channels._NOTIFICATION_DELIVERY_DONE_VALUE
    local = channels._notification_delivery_cache.get(key)
    assert local is not None
    assert local[0] == channels._NOTIFICATION_DELIVERY_DONE_VALUE
    # The local completion propagates the remaining Redis TTL, not a fresh 24h.
    assert local[1] - _time.monotonic() <= 60


def test_unreadable_remote_state_is_retryable_in_progress(isolated_channels, monkeypatch):
    fake = _LockOnlyFakeRedis()
    monkeypatch.setattr(channels, "get_redis", _redis_getter(fake))
    settings = _settings(REDIS_URL="redis://fake.test")

    assert run(channels.claim_notification_delivery(settings, "occ:old", "push", "d1"))
    # Contention against an unreadable remote must not become a completion.
    assert (
        run(try_claim_notification_delivery(settings, "occ:old", "push", "d1"))
        == NotificationDeliveryClaim.IN_PROGRESS
    )
    key = channels._notification_delivery_cache_key(settings, "occ:old", "push", "d1")
    local = channels._notification_delivery_cache.get(key)
    assert local is not None
    assert local[0] == channels._NOTIFICATION_DELIVERY_IN_PROGRESS_VALUE


def test_fanout_reports_in_progress_separately_from_duplicate(isolated_channels, monkeypatch):
    provider = FakePushProvider()
    channels.register_notification_channel_provider(provider)
    fake = _StatefulFakeRedis()
    monkeypatch.setattr(channels, "get_redis", _redis_getter(fake))
    settings = _settings(REDIS_URL="redis://fake.test")
    occurrence = _occurrence(occurrence_id="reliability:fanout")

    assert run(
        channels.claim_notification_delivery(settings, occurrence.occurrence_id, "push", "device-1")
    )
    contended = run(fanout_push_occurrence(settings=settings, user=_user(), occurrence=occurrence))
    assert [(outcome.status, outcome.reason) for outcome in contended] == [
        (ChannelOutcomeStatus.SKIPPED, "in_progress")
    ]
    assert provider.calls == []

    run(
        channels.release_notification_delivery(
            settings, occurrence.occurrence_id, "push", "device-1"
        )
    )
    delivered = run(fanout_push_occurrence(settings=settings, user=_user(), occurrence=occurrence))
    assert [outcome.status for outcome in delivered] == [ChannelOutcomeStatus.SENT]
    replay = run(fanout_push_occurrence(settings=settings, user=_user(), occurrence=occurrence))
    assert [(outcome.status, outcome.reason) for outcome in replay] == [
        (ChannelOutcomeStatus.SKIPPED, "duplicate")
    ]
    assert len(provider.calls) == 1


# ---------------------------------------------------------------------------
# 2. Torrent partial failure stays retryable per destination
# ---------------------------------------------------------------------------


def test_torrent_partial_push_failure_retries_failed_device(isolated_channels, monkeypatch):

    provider = FakePushProvider(destinations=("d1", "d2"), failures=("d2",))
    channels.register_notification_channel_provider(provider)
    settings = _settings(
        TORRENT_BLOCKER_NOTIFICATIONS_ENABLED=True,
        TORRENT_BLOCKER_TELEGRAM_NOTIFICATIONS_ENABLED=False,
        TORRENT_BLOCKER_EMAIL_NOTIFICATIONS_ENABLED=False,
        PANEL_WEBHOOK_SECRET="secret",
        TORRENT_BLOCKER_NOTIFICATION_COOLDOWN_SECONDS=0,
        USER_NOTIFICATION_PUSH_ENABLED=True,
    )
    service = torrent_module.TorrentBlockerNotificationService(settings, None, _TorrentI18n(), None)
    user = _user(user_id=123, telegram_id=None, email="")

    async def fake_resolve(session, payload):
        return user

    async def fake_lock(session, user_id):
        return user

    async def fake_has_content(session, **kwargs):
        return False

    async def fake_has_recent(session, **kwargs):
        return False

    logged: list[dict[str, Any]] = []

    async def fake_log(session, **kwargs):
        logged.append(kwargs)

    class DummySession:
        def __init__(self):
            self.commit = AsyncMock()
            self.rollback = AsyncMock()

        async def __call__(self, *args, **kwargs):
            raise AssertionError("unused")

    session = DummySession()
    factory_calls = {"count": 0}

    class DummyFactory:
        def __call__(self):
            factory_calls["count"] += 1

            class _Ctx:
                async def __aenter__(self):
                    return session

                async def __aexit__(self, *args):
                    return False

            return _Ctx()

    service.async_session_factory = cast(sessionmaker, DummyFactory())

    monkeypatch.setattr(service, "_resolve_user", fake_resolve)
    monkeypatch.setattr(torrent_module.user_dal, "lock_user_by_id", fake_lock)
    monkeypatch.setattr(
        torrent_module.message_log_dal, "has_target_event_content", fake_has_content
    )
    monkeypatch.setattr(torrent_module.message_log_dal, "has_recent_target_event", fake_has_recent)
    monkeypatch.setattr(torrent_module, "log_user_message_delivery", fake_log)

    context = {"blocked": True, "marker": "partial-push-probe"}
    with pytest.raises(torrent_module.TorrentBlockerDeliveryError) as exc_info:
        run(service.handle({"uuid": "panel-user-1"}, context))
    assert exc_info.value.channels == ("push",)
    assert [call["destination"] for call in provider.calls] == ["d1", "d2"]
    # Partial success commits no per-destination audit: the fingerprint-wide
    # suppression must not treat d1's delivery as completion for d2.
    assert logged == []
    session.rollback.assert_awaited()
    session.commit.assert_not_awaited()

    provider.failures = ()
    retry_delivery = run(service.handle({"uuid": "panel-user-1"}, context))
    assert retry_delivery.push_sent is True
    destinations = [call["destination"] for call in provider.calls]
    assert destinations.count("d1") == 1
    assert destinations.count("d2") == 2
    assert [entry["recipient"] for entry in logged] == ["d2"]


def test_torrent_partial_in_progress_stays_retryable(isolated_channels, monkeypatch):

    provider = FakePushProvider(destinations=("d1", "d2"))
    channels.register_notification_channel_provider(provider)
    settings = _settings(
        TORRENT_BLOCKER_NOTIFICATIONS_ENABLED=True,
        TORRENT_BLOCKER_TELEGRAM_NOTIFICATIONS_ENABLED=False,
        TORRENT_BLOCKER_EMAIL_NOTIFICATIONS_ENABLED=False,
        PANEL_WEBHOOK_SECRET="secret",
        TORRENT_BLOCKER_NOTIFICATION_COOLDOWN_SECONDS=0,
        USER_NOTIFICATION_PUSH_ENABLED=True,
    )
    service = torrent_module.TorrentBlockerNotificationService(settings, None, _TorrentI18n(), None)
    user = _user(user_id=123, telegram_id=None, email="")

    async def fake_resolve(session, payload):
        return user

    async def fake_lock(session, user_id):
        return user

    async def fake_has_content(session, **kwargs):
        return False

    async def fake_has_recent(session, **kwargs):
        return False

    logged: list[dict[str, Any]] = []

    async def fake_log(session, **kwargs):
        logged.append(kwargs)

    class DummySession:
        def __init__(self):
            self.commit = AsyncMock()
            self.rollback = AsyncMock()

        async def __call__(self, *args, **kwargs):
            raise AssertionError("unused")

    session = DummySession()

    class DummyFactory:
        def __call__(self):
            class _Ctx:
                async def __aenter__(self):
                    return session

                async def __aexit__(self, *args):
                    return False

            return _Ctx()

    service.async_session_factory = cast(sessionmaker, DummyFactory())

    monkeypatch.setattr(service, "_resolve_user", fake_resolve)
    monkeypatch.setattr(torrent_module.user_dal, "lock_user_by_id", fake_lock)
    monkeypatch.setattr(
        torrent_module.message_log_dal, "has_target_event_content", fake_has_content
    )
    monkeypatch.setattr(torrent_module.message_log_dal, "has_recent_target_event", fake_has_recent)
    monkeypatch.setattr(torrent_module, "log_user_message_delivery", fake_log)

    context = {"blocked": True, "marker": "partial-in-progress-probe"}
    fingerprint = torrent_module.TorrentBlockerReport.from_context(
        context, fingerprint_secret="secret"
    ).fingerprint
    occurrence_id = channels.occurrence_id_for("torrent-blocked", fingerprint)

    # Another backend/worker holds d2's delivery lock while this tick runs.
    assert run(channels.claim_notification_delivery(settings, occurrence_id, "push", "d2"))

    with pytest.raises(torrent_module.TorrentBlockerDeliveryError) as exc_info:
        run(service.handle({"uuid": "panel-user-1"}, context))
    assert exc_info.value.channels == ("push",)
    # d2 was skipped as in-progress, never attempted on the provider.
    assert [call["destination"] for call in provider.calls] == ["d1"]
    # The in-progress destination keeps the whole fingerprint retryable:
    # committing d1's audit row would let the fingerprint-wide suppression
    # swallow d2's retry.
    assert logged == []
    session.rollback.assert_awaited()
    session.commit.assert_not_awaited()

    run(channels.release_notification_delivery(settings, occurrence_id, "push", "d2"))
    retry_delivery = run(service.handle({"uuid": "panel-user-1"}, context))
    assert retry_delivery.push_sent is True
    # d1 stays suppressed by per-destination fanout dedup; only d2 is delivered.
    assert [call["destination"] for call in provider.calls] == ["d1", "d2"]
    assert [entry["recipient"] for entry in logged] == ["d2"]


class _TorrentI18n:
    def gettext(self, language, key, **kwargs):
        templates = {
            "torrent_blocker_notification": "Blocked until {unblock_at}",
            "torrent_blocker_notification_without_time": "Blocked temporarily",
            "torrent_blocker_email_subject": "Torrents blocked",
        }
        template = templates.get(key, key)
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError):
            return template


# ---------------------------------------------------------------------------
# 3. Success push retries on a repeated succeeded callback
# ---------------------------------------------------------------------------


def _success_settings(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "DEFAULT_LANGUAGE": "en",
        "SUBSCRIPTION_MINI_APP_URL": "https://app.example.test/",
    }
    values.update(overrides)
    return settings_stub(**values)


class _PaymentI18n:
    def gettext(self, language, key, **kwargs):
        return {
            "email_payment_success_subject": "Payment received",
        }.get(key, key)


def _finalize_request(*, payment, session=None, settings=None, skip_user_notification=False):

    return PaymentSuccessRequest(
        bot=None,
        settings=settings or _success_settings(),
        i18n=_PaymentI18n(),
        session=session or SimpleNamespace(),
        subscription_service=None,
        referral_service=None,
        payment=payment,
        user_id=int(getattr(payment, "user_id", 0) or 0),
        amount=100.0,
        currency="RUB",
        sale_mode=str(getattr(payment, "sale_mode", "") or "subscription"),
        months=1,
        traffic_amount=None,
        provider_subscription="yookassa",
        provider_notification="yookassa",
        skip_user_notification=skip_user_notification,
    )


def test_repeated_succeeded_callback_retries_failed_push(isolated_channels, monkeypatch):

    provider = FakePushProvider(destinations=("device-1",), failures=("device-1",))
    channels.register_notification_channel_provider(provider)
    payment = SimpleNamespace(payment_id=501, status="succeeded", user_id=42, sale_mode="")

    async def fake_get_payment(session, payment_id):
        return payment

    async def fake_get_user(session, user_id):
        return _user(user_id=42)

    monkeypatch.setattr(
        success_module.payment_dal, "get_payment_by_db_id_for_update", fake_get_payment
    )
    monkeypatch.setattr(success_module.user_dal, "get_user_by_id", fake_get_user)

    settings = _success_settings()
    req = _finalize_request(payment=payment, settings=settings)
    assert run(success_module.finalize_successful_payment(req)) is None
    assert len(provider.calls) == 1

    provider.failures = ()
    assert run(success_module.finalize_successful_payment(req)) is None
    assert [call["destination"] for call in provider.calls] == ["device-1", "device-1"]


def test_repeated_succeeded_callback_skips_delivered_push(isolated_channels, monkeypatch):

    provider = FakePushProvider()
    channels.register_notification_channel_provider(provider)
    payment = SimpleNamespace(payment_id=502, status="succeeded", user_id=42, sale_mode="")

    async def fake_get_payment(session, payment_id):
        return payment

    async def fake_get_user(session, user_id):
        return _user(user_id=42)

    monkeypatch.setattr(
        success_module.payment_dal, "get_payment_by_db_id_for_update", fake_get_payment
    )
    monkeypatch.setattr(success_module.user_dal, "get_user_by_id", fake_get_user)

    req = _finalize_request(payment=payment)
    assert run(success_module.finalize_successful_payment(req)) is None
    assert len(provider.calls) == 1
    assert run(success_module.finalize_successful_payment(req)) is None
    assert len(provider.calls) == 1


def test_repeated_succeeded_callback_respects_skip_flag(isolated_channels, monkeypatch):

    provider = FakePushProvider()
    channels.register_notification_channel_provider(provider)
    payment = SimpleNamespace(payment_id=503, status="succeeded", user_id=42, sale_mode="")

    async def fake_get_payment(session, payment_id):
        return payment

    monkeypatch.setattr(
        success_module.payment_dal, "get_payment_by_db_id_for_update", fake_get_payment
    )
    get_user = AsyncMock(return_value=_user(user_id=42))
    monkeypatch.setattr(success_module.user_dal, "get_user_by_id", get_user)

    req = _finalize_request(payment=payment, skip_user_notification=True)
    assert run(success_module.finalize_successful_payment(req)) is None
    assert provider.calls == []
    get_user.assert_not_awaited()


# ---------------------------------------------------------------------------
# 4. Warning pushes retry despite the recorded marker
# ---------------------------------------------------------------------------


class _WarningI18n:
    def gettext(self, language, key, **kwargs):
        return key


class _RegularWarningHarness(TariffWorkerRegularWarningMixin):
    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.i18n = _WarningI18n()
        self.bot = None

    async def _user_lang(self, session: Any, user_id: int) -> str:
        return "en"

    def _usage_placeholders(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {}

    def _traffic_next_reset_note(self, *args: Any, **kwargs: Any) -> str:
        return ""

    def _period_tariff_traffic_strategy(self, *args: Any, **kwargs: Any) -> str:
        return "MONTH"

    def _traffic_topup_markup(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def _send_traffic_warning_email(self, session: Any, **kwargs: Any) -> None:
        return None

    async def maybe_warn(self, *args: Any, **kwargs: Any) -> None:
        await self._maybe_warn_or_throttle(*args, **kwargs)


def test_regular_warning_push_retries_despite_marker(isolated_channels, monkeypatch):

    provider = FakePushProvider(destinations=("device-1",), failures=("device-1",))
    channels.register_notification_channel_provider(provider)
    settings = _settings(tariff_traffic_warning_levels=[90])

    async def fake_get_user(session, user_id):
        return _user(user_id=123)

    async def fake_get_warning(session, **kwargs):
        return SimpleNamespace(warning_id=9)

    async def fail_on_create(session, **kwargs):
        raise AssertionError("marker already exists: must retry, not recreate")

    async def fake_has_content(session, **kwargs):
        return False

    async def fake_log(session, **kwargs):
        return None

    monkeypatch.setattr(shared.user_dal, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(tariff_dal, "get_warning", fake_get_warning)
    monkeypatch.setattr(tariff_dal, "create_warning", fail_on_create)
    monkeypatch.setattr(shared.message_log_dal, "has_target_event_content", fake_has_content)

    harness = _RegularWarningHarness(settings)
    tariff = SimpleNamespace(
        billing_model="traffic",
        monthly_bytes=100,
        name=lambda lang, fallback="ru": "Standard",
    )
    sub = SimpleNamespace(
        subscription_id=42,
        user_id=123,
        traffic_used_bytes=95,
        traffic_limit_bytes=100,
        regular_unlimited_override=False,
        is_throttled=False,
    )
    run(
        harness.maybe_warn(
            object(),
            sub,
            tariff,
            95,
            100,
            warning_period_start=None,
            next_reset_at=None,
            traffic_strategy="MONTH",
        )
    )
    assert provider.calls == [
        {
            "occurrence_id": "traffic-warning:42:traffic_warning_regular_almost:90:regular",
            "destination": "device-1",
        }
    ]

    provider.failures = ()
    run(
        harness.maybe_warn(
            object(),
            sub,
            tariff,
            95,
            100,
            warning_period_start=None,
            next_reset_at=None,
            traffic_strategy="MONTH",
        )
    )
    assert len(provider.calls) == 2


def test_regular_warning_push_skipped_when_already_delivered(isolated_channels, monkeypatch):

    provider = FakePushProvider()
    channels.register_notification_channel_provider(provider)
    settings = _settings(tariff_traffic_warning_levels=[90])

    async def fake_get_user(session, user_id):
        return _user(user_id=123)

    async def fake_get_warning(session, **kwargs):
        return SimpleNamespace(warning_id=9)

    async def fake_has_content(session, **kwargs):
        return True

    monkeypatch.setattr(shared.user_dal, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(tariff_dal, "get_warning", fake_get_warning)
    monkeypatch.setattr(shared.message_log_dal, "has_target_event_content", fake_has_content)

    harness = _RegularWarningHarness(settings)
    tariff = SimpleNamespace(
        billing_model="traffic",
        monthly_bytes=100,
        name=lambda lang, fallback="ru": "Standard",
    )
    sub = SimpleNamespace(
        subscription_id=42,
        user_id=123,
        traffic_used_bytes=95,
        traffic_limit_bytes=100,
        regular_unlimited_override=False,
        is_throttled=False,
    )
    run(
        harness.maybe_warn(
            object(),
            sub,
            tariff,
            95,
            100,
            warning_period_start=None,
            next_reset_at=None,
            traffic_strategy="MONTH",
        )
    )
    assert provider.calls == []


def test_warning_partial_push_only_audits_delivered_and_reports_failure(
    isolated_channels, monkeypatch
):

    provider = FakePushProvider(destinations=("d1", "d2"), failures=("d2",))
    channels.register_notification_channel_provider(provider)
    settings = _settings()
    logged: list[dict[str, Any]] = []

    async def fake_log(session, **kwargs):
        logged.append(kwargs)

    delivered = run(
        shared.deliver_warning_push_only(
            object(),
            settings=settings,
            user=_user(user_id=123),
            user_id=123,
            subscription_id=42,
            text="Traffic 90% used",
            audit_content="kind=regular warning_key=w level=90",
            audit_logger=fake_log,
            warning_key="w",
            level=90,
            kind="regular",
            push_title="Warning",
            logger=logging.getLogger("test"),
        )
    )
    assert delivered is False
    assert [entry["recipient"] for entry in logged] == ["d1"]
    assert "occurrence_id=traffic-warning:42:w:90:regular destination=d1" in logged[0]["content"]


def test_warning_push_occurrence_scoped_to_period(isolated_channels):

    base = shared.warning_push_occurrence_id(42, "w", 90, "regular")
    assert base == "traffic-warning:42:w:90:regular"
    scoped_a = shared.warning_push_occurrence_id(
        42, "w", 90, "regular", "2026-01-01T00:00:00+00:00"
    )
    scoped_b = shared.warning_push_occurrence_id(
        42, "w", 90, "regular", "2026-02-01T00:00:00+00:00"
    )
    assert scoped_a == f"{base}:2026-01-01T00:00:00+00:00"
    assert scoped_b != base
    assert scoped_a != scoped_b

    assert shared.warning_period_identity(None) is None
    naive = datetime(2026, 1, 1)  # noqa: DTZ001 - naive input is the normalization case
    aware = datetime(2026, 1, 1, tzinfo=UTC)
    assert shared.warning_period_identity(naive) == shared.warning_period_identity(aware)
    assert shared.warning_period_identity(aware) == "2026-01-01T00:00:00+00:00"

    reset_base = core.TariffWorkerCoreMixin._reset_push_occurrence_id(77, "traffic_reset_regular")
    assert reset_base == "traffic-reset:77:traffic_reset_regular"
    assert (
        core.TariffWorkerCoreMixin._reset_push_occurrence_id(
            77, "traffic_reset_regular", "2026-01-01T00:00:00+00:00"
        )
        == f"{reset_base}:2026-01-01T00:00:00+00:00"
    )

    # A new quota generation redelivers even though the previous period
    # already completed the same warning level.
    period_provider = FakePushProvider(destinations=("d1",))
    channels.register_notification_channel_provider(period_provider)
    period_settings = _settings()
    period_logged: list[dict[str, Any]] = []

    async def period_log(session, **kwargs):
        period_logged.append(kwargs)

    assert (
        run(
            shared.deliver_warning_push_only(
                object(),
                settings=period_settings,
                user=_user(user_id=123),
                user_id=123,
                subscription_id=42,
                text="Traffic 90% used",
                audit_content="kind=regular warning_key=w level=90",
                audit_logger=period_log,
                warning_key="w",
                level=90,
                kind="regular",
                push_title="Warning",
                period="2026-01-01T00:00:00+00:00",
                logger=logging.getLogger("test"),
            )
        )
        is True
    )
    assert (
        run(
            shared.deliver_warning_push_only(
                object(),
                settings=period_settings,
                user=_user(user_id=123),
                user_id=123,
                subscription_id=42,
                text="Traffic 90% used",
                audit_content="kind=regular warning_key=w level=90",
                audit_logger=period_log,
                warning_key="w",
                level=90,
                kind="regular",
                push_title="Warning",
                period="2026-02-01T00:00:00+00:00",
                logger=logging.getLogger("test"),
            )
        )
        is True
    )
    assert [call["destination"] for call in period_provider.calls] == ["d1", "d1"]
    assert [entry["recipient"] for entry in period_logged] == ["d1", "d1"]


def test_warning_retry_only_pending_destinations_after_ttl_expiry(isolated_channels, monkeypatch):

    provider = FakePushProvider(destinations=("d1", "d2"), failures=("d2",))
    channels.register_notification_channel_provider(provider)
    settings = _settings()
    logged: list[dict[str, Any]] = []

    async def fake_log(session, **kwargs):
        logged.append(kwargs)

    delivered = run(
        shared.deliver_warning_push_only(
            object(),
            settings=settings,
            user=_user(user_id=123),
            user_id=123,
            subscription_id=42,
            text="Traffic 90% used",
            audit_content="kind=regular warning_key=w level=90",
            audit_logger=fake_log,
            warning_key="w",
            level=90,
            kind="regular",
            push_title="Warning",
            logger=logging.getLogger("test"),
        )
    )
    assert delivered is False
    assert [entry["recipient"] for entry in logged] == ["d1"]

    # The dedup TTL expired: per-destination claims are gone while the DB
    # audit rows survive. Without pending-only fan-out the retry would
    # resend the already audited d1 alongside d2.
    channels._notification_delivery_cache.clear()
    provider.failures = ()

    async def fake_get_user(session, user_id):
        return _user(user_id=123)

    async def fake_has_content(session, **kwargs):
        fragment = str(kwargs.get("content_fragment", ""))
        return kwargs.get("event_type") == "push_traffic_warning_sent" and any(
            fragment in entry["content"] for entry in logged
        )

    monkeypatch.setattr(shared.user_dal, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(shared.message_log_dal, "has_target_event_content", fake_has_content)

    run(
        shared.retry_warning_push_if_pending(
            object(),
            settings=settings,
            user_id=123,
            subscription_id=42,
            text="Traffic 90% used",
            audit_content="kind=regular warning_key=w level=90",
            audit_logger=fake_log,
            warning_key="w",
            level=90,
            kind="regular",
            push_title="Warning",
            logger=logging.getLogger("test"),
        )
    )
    assert [call["destination"] for call in provider.calls] == ["d1", "d2", "d2"]
    assert [entry["recipient"] for entry in logged] == ["d1", "d2"]


# ---------------------------------------------------------------------------
# 5. Traffic-reset notices cover push
# ---------------------------------------------------------------------------


def _reset_worker(settings, bot=None):

    return TariffWorkerCoreMixin(
        settings,
        session_factory=None,
        panel_service=None,
        subscription_service=None,
        bot=bot,
        i18n=_WarningI18n(),
    )


def test_reset_notice_channels_include_push_only(isolated_channels):
    settings = _settings(SUBSCRIPTION_MINI_APP_URL="https://app.example.test/")
    worker = _reset_worker(settings, bot=None)
    assert worker._traffic_notice_channels_available() is False

    channels.register_notification_channel_provider(FakePushProvider())
    assert worker._traffic_notice_channels_available() is True


def test_reset_notice_fans_out_push(isolated_channels, monkeypatch):

    provider = FakePushProvider()
    channels.register_notification_channel_provider(provider)
    settings = _settings(SUBSCRIPTION_MINI_APP_URL="https://app.example.test/")
    worker = _reset_worker(settings, bot=None)
    logged: list[dict[str, Any]] = []

    async def fake_log(session, **kwargs):
        logged.append(kwargs)

    async def fake_get_user(session, user_id):
        return _user(user_id=7)

    async def fail_email(**kwargs):
        raise AssertionError("email disabled: must not be attempted")

    monkeypatch.setattr(core.user_dal, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(core, "send_user_notification_email", fail_email)
    monkeypatch.setattr(core, "log_user_message_delivery", fake_log)

    sub = SimpleNamespace(subscription_id=77, user_id=7)
    run(
        worker._send_traffic_reset_notice(
            object(),
            sub=sub,
            subject_key="email_traffic_reset_regular_subject",
            message_text="<b>Quota reset</b>",
            kind="regular",
            warning_key="traffic_reset_regular",
            audit_content="kind=regular warning_key=traffic_reset_regular",
        )
    )
    assert provider.calls == [
        {
            "occurrence_id": "traffic-reset:77:traffic_reset_regular",
            "destination": "device-1",
        }
    ]
    assert [entry["event_type"] for entry in logged] == ["push_traffic_reset_notice_sent"]
    assert (
        "occurrence_id=traffic-reset:77:traffic_reset_regular destination=device-1"
        in logged[0]["content"]
    )
