"""Wiring tests: direct-send paths fan out through the channel contract.

Covers reminders (``SubscriptionLifecycleNotificationService.send_stage``),
trial-traffic warnings (``SubscriptionNotificationWorker``), support replies,
and the payment/referral event reactions: one business occurrence reaches
every enabled eligible channel, duplicates are suppressed, an unavailable
provider changes nothing, and one channel failing never suppresses another.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from bot.services import event_reactions
from bot.services import notification_channels as channels
from bot.services import subscription_lifecycle_notifications as lifecycle
from bot.services import subscription_notification_worker as worker_module
from bot.services.event_reactions import CoreEventReactions
from bot.services.notification_channels import (
    ChannelOutcomeStatus,
    NotificationChannelOutcome,
    NotificationOccurrence,
)
from bot.services.notification_service import NotificationService
from bot.services.subscription_lifecycle_notifications import (
    SubscriptionLifecycleNotificationService,
    SubscriptionNotificationStage,
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


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Shared fakes


class FakeI18n:
    def gettext(self, lang_code, key, **kwargs):
        templates = {
            "subscription_72h_notification": "Hi {user_name}, expires on {end_date}",
            "user_name_fallback": "dear user",
            "email_subscription_lifecycle_subject_before_days": "{days} days left",
            "email_subscription_lifecycle_subject_before_hours": "{hours} hours left",
            "email_subscription_lifecycle_subject_expired": "Expired",
            "email_subscription_lifecycle_subject_expired_after": "Expired yesterday",
            "email_subscription_lifecycle_subject_autorenew": "Auto-renewal tomorrow",
            "email_subscription_lifecycle_subject_expired_after_hours": "Expired {hours}h ago",
            "email_subscription_lifecycle_cta": "Open dashboard",
            "email_subscription_lifecycle_intro_direct": "Direct email notice",
            "email_subscription_lifecycle_intro_mirrored": "Mirrored Telegram notice",
            "email_subscription_lifecycle_row_end_date": "Active until",
            "email_footer_auto": "Sent by {brand}",
            "email_trial_traffic_depleted_subject": "Trial traffic used up",
            "payment_failed": "Payment failed: retry at {retry_at}",
            "email_payment_failed_subject": "Payment failed",
            "email_referral_bonus_subject": "Referral bonus credited",
            "referral_bonus_inviter_notification_new_sub": (
                "Bonus {days}d from {referee_name}, ends {new_end_date}"
            ),
            "email_support_admin_reply_user_subject": "New reply for ticket #{ticket_id}",
            "email_support_ticket_closed_user_subject": "Ticket #{ticket_id} was closed",
            "wa_support_open_ticket": "Open ticket",
        }
        template = templates.get(key, key)
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError):
            return template


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append({"chat_id": chat_id, "text": text, **kwargs})


class FakeEmailService:
    def __init__(self):
        self.messages = []

    async def send_rendered_email(self, *, email, content):
        self.messages.append({"email": email, "content": content})


def _settings(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "DEFAULT_LANGUAGE": "en",
        "SUBSCRIPTION_NOTIFICATIONS_ENABLED": True,
        "SUBSCRIPTION_EMAIL_NOTIFICATIONS_ENABLED": True,
        "USER_NOTIFICATION_SINGLE_CHANNEL_FALLBACK_ENABLED": True,
        "SUBSCRIPTION_MINI_APP_URL": "https://app.example.test/",
        "WEBAPP_PRIMARY_COLOR": "#00fe7a",
        "WEBAPP_TITLE": "Minishop",
        "WEBAPP_LOGO_URL": "",
        "email_auth_configured": True,
        "smtp_delivery_configured": True,
    }
    values.update(overrides)
    return settings_stub(**values)


def _reminder_user(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "user_id": 123,
        "telegram_id": 555,
        "email": "user@example.test",
        "language_code": "en",
        "first_name": "Ada",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _subscription(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "subscription_id": 42,
        "user_id": 123,
        "end_date": datetime(2026, 6, 1, tzinfo=UTC),
        "tariff_key": "standard",
        "provider": "yookassa",
        "status_from_panel": "ACTIVE",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _reminder_service(bot=None, email_service=None, settings=None):
    return SubscriptionLifecycleNotificationService(
        settings or _settings(),
        bot if bot is not None else FakeBot(),
        FakeI18n(),
        email_service=email_service,
    )


def _history_mocks(monkeypatch, recorded: list, status_changes: list | None = None):
    async def fake_has(session, subscription_id, notification_key):
        return notification_key in recorded

    async def fake_record(session, subscription_id, notification_key, *, sent_at=None):
        recorded.append(notification_key)

    async def fake_mark_status(session, user_id, status, *, telegram_id=None, checked_at=None):
        if status_changes is not None:
            status_changes.append(user_id)

    async def fake_log(*_args, **_kwargs):
        return None

    monkeypatch.setattr(lifecycle.subscription_dal, "has_subscription_notification", fake_has)
    monkeypatch.setattr(lifecycle.subscription_dal, "record_subscription_notification", fake_record)
    monkeypatch.setattr(lifecycle, "mark_telegram_notifications_status", fake_mark_status)
    monkeypatch.setattr(lifecycle, "log_user_message_delivery", fake_log)


def _send_reminder(monkeypatch, recorded, provider=None, **service_kwargs):
    _history_mocks(monkeypatch, recorded)
    if provider is not None:
        channels.register_notification_channel_provider(provider)
    service_kwargs.setdefault("email_service", FakeEmailService())
    service = _reminder_service(**service_kwargs)
    return run(
        service.send_stage(
            object(),
            _subscription(),
            SubscriptionNotificationStage(
                key="before_3d",
                message_key="subscription_72h_notification",
                days_left=3,
            ),
            user=_reminder_user(),
            telegram_markup="markup",
        )
    )


# ---------------------------------------------------------------------------
# Reminders (direct path through send_stage)


def test_reminder_mirrors_push_with_channel_history(monkeypatch, isolated_channels):
    recorded: list = []
    provider = FakePushProvider()
    delivery = _send_reminder(monkeypatch, recorded, provider=provider)

    assert delivery.telegram_sent is True
    assert delivery.email_sent is True
    assert delivery.push_sent is True
    assert recorded == ["before_3d:telegram", "before_3d:email", "before_3d:push"]
    assert provider.calls == [
        {"occurrence_id": "subscription-notice:42:before_3d:1780272000", "destination": "device-1"}
    ]


def test_reminder_push_not_resent_after_history_recorded(monkeypatch, isolated_channels):
    recorded: list = []
    provider = FakePushProvider()
    first = _send_reminder(monkeypatch, recorded, provider=provider)
    second = _send_reminder(monkeypatch, recorded, provider=provider)

    assert first.push_sent is True
    assert second.push_sent is False
    assert recorded.count("before_3d:push") == 1
    assert len(provider.calls) == 1


def test_reminder_without_provider_keeps_telegram_email_only(monkeypatch, isolated_channels):
    recorded: list = []
    delivery = _send_reminder(monkeypatch, recorded)

    assert (delivery.telegram_sent, delivery.email_sent, delivery.push_sent) == (True, True, False)
    assert recorded == ["before_3d:telegram", "before_3d:email"]


def test_reminder_push_failure_does_not_suppress_telegram_email(monkeypatch, isolated_channels):
    recorded: list = []
    provider = FakePushProvider(failures=("device-1",))
    delivery = _send_reminder(monkeypatch, recorded, provider=provider)

    assert (delivery.telegram_sent, delivery.email_sent, delivery.push_sent) == (True, True, False)
    assert recorded == ["before_3d:telegram", "before_3d:email"]


def test_reminder_push_delivers_without_telegram_email(monkeypatch, isolated_channels):
    recorded: list = []
    provider = FakePushProvider()
    settings = _settings(
        SUBSCRIPTION_NOTIFICATIONS_ENABLED=False,
        SUBSCRIPTION_EMAIL_NOTIFICATIONS_ENABLED=False,
    )
    delivery = _send_reminder(monkeypatch, recorded, provider=provider, settings=settings)

    assert (delivery.telegram_sent, delivery.email_sent, delivery.push_sent) == (
        False,
        False,
        True,
    )
    assert recorded == ["before_3d:push"]
    assert delivery.any_sent is True


# ---------------------------------------------------------------------------
# Trial traffic (worker direct path)


def _worker(settings=None):
    return worker_module.SubscriptionNotificationWorker(
        settings or _settings(),
        session_factory=None,
        bot=None,
        i18n=FakeI18n(),
        panel_service=None,
        subscription_service=None,
    )


def test_trial_traffic_push_delivery_and_audit(monkeypatch, isolated_channels):
    provider = FakePushProvider()
    channels.register_notification_channel_provider(provider)
    logged = []

    async def fake_log(session, **kwargs):
        logged.append(kwargs)

    monkeypatch.setattr(worker_module, "log_user_message_delivery", fake_log)
    service = _worker()
    sub = _subscription()
    user = _reminder_user()

    sent = run(
        service._send_trial_traffic_push(
            object(),
            sub,
            user=user,
            lang="en",
            message_text="Trial used up",
            used=100,
            limit=100,
            sent_at=datetime.now(UTC),
        )
    )

    assert sent is True
    assert provider.calls == [
        {"occurrence_id": "trial-traffic-depleted:42", "destination": "device-1"}
    ]
    assert logged and logged[0]["channel"] == "push"
    assert logged[0]["recipient"] == "device-1"


def test_trial_traffic_push_absent_provider_sends_nothing(monkeypatch, isolated_channels):
    logged = []

    async def fake_log(session, **kwargs):
        logged.append(kwargs)

    monkeypatch.setattr(worker_module, "log_user_message_delivery", fake_log)
    sent = run(
        _worker()._send_trial_traffic_push(
            object(),
            _subscription(),
            user=_reminder_user(),
            lang="en",
            message_text="Trial used up",
            used=100,
            limit=100,
            sent_at=datetime.now(UTC),
        )
    )

    assert sent is False
    assert logged == []


# ---------------------------------------------------------------------------
# Support replies (direct path)


def _support_service(bot=None, email_service=None, settings=None):
    return NotificationService(
        bot if bot is not None else FakeBot(),
        settings or _settings(),
        FakeI18n(),
        session_factory=None,
        email_auth_service=email_service if email_service is not None else FakeEmailService(),
        bot_username="testbot",
    )


def _ticket(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "ticket_id": 5,
        "subject": "Login issue",
        "priority": "normal",
        "category": "other",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _support_message(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "message_id": 11,
        "body": "We fixed it",
        "body_format": "text",
        "buttons": None,
        "image_id": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _support_user(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "user_id": 42,
        "telegram_id": 555,
        "email": "user@example.test",
        "language_code": "en",
        "first_name": "Ada",
        "last_name": None,
        "username": None,
        "minishop_id": "M1",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_support_admin_reply_mirrors_push_alongside_telegram_email(isolated_channels):
    provider = FakePushProvider()
    channels.register_notification_channel_provider(provider)
    bot = FakeBot()
    email_service = FakeEmailService()
    service = _support_service(bot=bot, email_service=email_service)

    run(service.notify_support_admin_reply(_ticket(), _support_message(), _support_user()))

    assert len(bot.messages) == 1
    assert len(email_service.messages) == 1
    assert provider.calls == [
        {"occurrence_id": "support-admin-reply:11", "destination": "device-1"}
    ]


def test_support_admin_reply_push_duplicate_suppressed(isolated_channels):
    provider = FakePushProvider()
    channels.register_notification_channel_provider(provider)
    service = _support_service()
    ticket, message, user = _ticket(), _support_message(), _support_user()

    run(service.notify_support_admin_reply(ticket, message, user))
    run(service.notify_support_admin_reply(ticket, message, user))

    assert len(provider.calls) == 1


def test_support_admin_reply_push_failure_keeps_telegram_email(isolated_channels):
    channels.register_notification_channel_provider(FakePushProvider(failures=("device-1",)))
    bot = FakeBot()
    email_service = FakeEmailService()
    service = _support_service(bot=bot, email_service=email_service)

    run(service.notify_support_admin_reply(_ticket(), _support_message(), _support_user()))

    assert len(bot.messages) == 1
    assert len(email_service.messages) == 1


def test_support_ticket_closed_mirrors_push(isolated_channels):
    provider = FakePushProvider()
    channels.register_notification_channel_provider(provider)
    service = _support_service()

    run(service.notify_support_ticket_closed(_ticket(), _support_user(), None))

    assert provider.calls == [
        {"occurrence_id": "support-ticket-closed:5", "destination": "device-1"}
    ]


# ---------------------------------------------------------------------------
# Event reactions (payment failure, referral bonus)


def _reaction_context(bot=None):
    from bot.plugins import PluginContext

    class _SessionFactory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return SimpleNamespace()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    return PluginContext(
        settings=_settings(),
        session_factory=_SessionFactory(),
        bot=bot or FakeBot(),
        i18n=FakeI18n(),
        services={},
    )


def _payment_user(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "user_id": 42,
        "telegram_id": 111,
        "email": "user@example.test",
        "language_code": "en",
        "telegram_notifications_status": "enabled",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_payment_canceled_mirrors_push_independently(monkeypatch, isolated_channels):
    provider = FakePushProvider()
    channels.register_notification_channel_provider(provider)
    bot = FakeBot()
    ctx = _reaction_context(bot=bot)
    reactions = CoreEventReactions(ctx)
    send_email = AsyncMock(return_value=True)
    monkeypatch.setattr(event_reactions, "send_user_notification_email", send_email)
    monkeypatch.setattr(event_reactions, "invalidate_webapp_user_caches", AsyncMock())
    monkeypatch.setattr(
        event_reactions.user_dal, "get_user_by_id", AsyncMock(return_value=_payment_user())
    )
    monkeypatch.setattr(
        event_reactions.payment_dal,
        "get_payment_by_db_id",
        AsyncMock(return_value=None),
    )

    async def run_twice():
        payload = {"user_id": 42, "payment_db_id": 777}
        await reactions.on_payment_canceled("payment.canceled", dict(payload))
        await reactions.on_payment_canceled("payment.canceled", dict(payload))

    run(run_twice())

    assert len(bot.messages) == 1
    assert send_email.await_count == 1
    assert provider.calls == [{"occurrence_id": "payment-failed:777", "destination": "device-1"}]


def test_payment_canceled_push_survives_telegram_failure(monkeypatch, isolated_channels):
    provider = FakePushProvider()
    channels.register_notification_channel_provider(provider)

    class FailingBot(FakeBot):
        async def send_message(self, chat_id, text, **kwargs):
            raise RuntimeError("telegram down")

    ctx = _reaction_context(bot=FailingBot())
    reactions = CoreEventReactions(ctx)
    monkeypatch.setattr(
        event_reactions, "send_user_notification_email", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(event_reactions, "invalidate_webapp_user_caches", AsyncMock())
    monkeypatch.setattr(
        event_reactions.user_dal, "get_user_by_id", AsyncMock(return_value=_payment_user())
    )
    monkeypatch.setattr(
        event_reactions.payment_dal,
        "get_payment_by_db_id",
        AsyncMock(return_value=None),
    )

    run(reactions.on_payment_canceled("payment.canceled", {"user_id": 42, "payment_db_id": 778}))

    assert provider.calls == [{"occurrence_id": "payment-failed:778", "destination": "device-1"}]


def test_referral_bonus_mirrors_push(monkeypatch, isolated_channels):
    provider = FakePushProvider()
    channels.register_notification_channel_provider(provider)
    bot = FakeBot()
    ctx = _reaction_context(bot=bot)
    reactions = CoreEventReactions(ctx)
    send_email = AsyncMock(return_value=True)
    monkeypatch.setattr(event_reactions, "send_user_notification_email", send_email)
    inviter = _payment_user(user_id=7)
    monkeypatch.setattr(event_reactions.user_dal, "get_user_by_id", AsyncMock(return_value=inviter))

    run(
        reactions.on_referral_bonus_granted(
            "referral.bonus_granted",
            {
                "inviter_bonus_applied": True,
                "inviter_user_id": 7,
                "inviter_bonus_days": 3,
                "inviter_bonus_kind": "new_sub",
                "referee_user_id": 9,
                "reason": "payment",
                "payment_db_id": 55,
            },
        )
    )

    assert len(bot.messages) == 1
    assert send_email.await_count == 1
    assert provider.calls == [
        {
            "occurrence_id": "referral-bonus:7:9:payment:new_sub:55",
            "destination": "device-1",
        }
    ]


# ---------------------------------------------------------------------------
# Blocker 2: previously unwired direct-send paths
# ---------------------------------------------------------------------------


def test_success_payment_mirrors_push_without_secrets(isolated_channels):
    from bot.payment_providers.shared import success as success_module

    captured: list = []

    class CapturingPushProvider(FakePushProvider):
        async def deliver(self, *, settings, user, occurrence, destination):
            captured.append(occurrence)
            return await super().deliver(
                settings=settings, user=user, occurrence=occurrence, destination=destination
            )

    provider = CapturingPushProvider()
    channels.register_notification_channel_provider(provider)
    bot = FakeBot()
    settings = _settings()
    user = _reminder_user(push_notifications_enabled=True)

    secret_text = (
        "Payment successful! Your gift: https://app.example.test/?gift=SECRET-TOKEN-123 "
        "open it now."
    )
    run(
        success_module.send_success_message_to_user(
            bot=bot,
            user_id=123,
            text=secret_text,
            language="en",
            i18n=FakeI18n(),
            settings=settings,
            config_link_display="https://panel.example.test/secret-config-token",
            connect_button_url="https://panel.example.test/secret-connect",
            include_keyboard=False,
            user=user,
            sale_mode="subscription",
            payment_db_id=999,
        )
    )

    assert len(bot.messages) == 1
    assert provider.calls == [{"occurrence_id": "payment-succeeded:999", "destination": "device-1"}]
    assert captured and "SECRET-TOKEN-123" not in captured[0].push_body
    assert "http" not in captured[0].push_body
    assert captured[0].push_url == "https://app.example.test/"
    assert "SECRET" not in (captured[0].push_url or "")


def test_traffic_warning_mirrors_push(isolated_channels):
    from bot.services.tariff_worker_shared import deliver_traffic_warning

    async def fake_audit(session, **kwargs):
        return None

    async def fake_email(session, **kwargs):
        return None

    provider = FakePushProvider()
    channels.register_notification_channel_provider(provider)
    bot = FakeBot()
    settings = _settings()

    import bot.services.tariff_worker_shared as shared

    user = _reminder_user()
    original_get_user = shared.user_dal.get_user_by_id

    async def fake_get_user(session, user_id):
        return user

    shared.user_dal.get_user_by_id = fake_get_user  # type: ignore[assignment]
    try:
        run(
            deliver_traffic_warning(
                object(),
                settings=settings,
                bot=bot,
                user_id=123,
                text="Traffic 90% used",
                markup=None,
                audit_content="kind=regular warning_key=test",
                audit_logger=fake_audit,
                email_sender=fake_email,
                subject_key="email_trial_traffic_depleted_subject",
                kind="regular",
                warning_key="traffic_warning_regular_almost",
                logger=__import__("logging").getLogger("test"),
                telegram_failure_message="fail %s",
                subscription_id=42,
                level=90,
                push_title="Traffic warning",
            )
        )
    finally:
        shared.user_dal.get_user_by_id = original_get_user  # type: ignore[assignment]

    assert len(bot.messages) == 1
    assert provider.calls == [
        {
            "occurrence_id": "traffic-warning:42:traffic_warning_regular_almost:90:regular",
            "destination": "device-1",
        }
    ]


def test_hwid_device_mirrors_push(monkeypatch, isolated_channels):
    from bot.services import hwid_device_notifications as hwid_module

    provider = FakePushProvider(destinations=("device-1",))
    channels.register_notification_channel_provider(provider)

    settings = _settings(
        MY_DEVICES_SECTION_ENABLED=True,
        USER_NOTIFICATION_DEVICE_ACTIVITY_TELEGRAM_ENABLED=True,
        USER_NOTIFICATION_DEVICE_ACTIVITY_EMAIL_ENABLED=True,
    )
    service = hwid_module.HwidDeviceNotificationService(
        settings, FakeBot(), FakeI18n(), panel_service=None
    )
    user = SimpleNamespace(
        user_id=123,
        telegram_id=555,
        email="user@example.test",
        language_code="en",
        first_name="Ada",
    )
    subscription = SimpleNamespace(
        subscription_id=42,
        user_id=123,
        panel_user_uuid="panel-uuid",
        hwid_device_limit=5,
        extra_hwid_devices=0,
        is_active=True,
        end_date=datetime(2026, 6, 1, tzinfo=UTC),
        provider="yookassa",
        status_from_panel="ACTIVE",
        tariff_key="standard",
    )

    async def fake_count(reference):
        return 1

    async def fake_emit(*args, **kwargs):
        return None

    seen: list = []

    async def fake_has(session, subscription_id, key):
        return False

    async def fake_record(session, subscription_id, key, *, sent_at=None):
        seen.append(key)

    async def fake_email(**kwargs):
        return True

    async def fake_log(*args, **kwargs):
        return None

    monkeypatch.setattr(service, "_current_device_count", fake_count)
    monkeypatch.setattr(service, "_emit_domain_events_once", fake_emit)
    monkeypatch.setattr(hwid_module.subscription_dal, "has_subscription_notification", fake_has)
    monkeypatch.setattr(
        hwid_module.subscription_dal, "record_subscription_notification", fake_record
    )
    monkeypatch.setattr(hwid_module, "send_user_notification_email", fake_email)
    monkeypatch.setattr(hwid_module, "log_user_message_delivery", fake_log)

    async def fake_mark_status(session, user_id, status, *, telegram_id=None, checked_at=None):
        return None

    monkeypatch.setattr(hwid_module, "mark_telegram_notifications_status", fake_mark_status)

    result = run(
        service.handle_added(
            object(),
            user=user,
            subscription=subscription,
            user_payload={"uuid": "panel-uuid"},
            context={"fingerprint": "ABC123", "device_model": "Phone"},
        )
    )

    assert "push" not in result.retry_channels
    assert any(call["occurrence_id"] == "device-connected:abc123" for call in provider.calls)
    assert "device_connected:abc123:push" in seen


def test_torrent_blocker_mirrors_push(monkeypatch, isolated_channels):
    from bot.services import torrent_blocker_notifications as torrent_module

    provider = FakePushProvider(destinations=("device-1",))
    channels.register_notification_channel_provider(provider)
    settings = _settings(
        TORRENT_BLOCKER_NOTIFICATIONS_ENABLED=True,
        TORRENT_BLOCKER_TELEGRAM_NOTIFICATIONS_ENABLED=False,
        TORRENT_BLOCKER_EMAIL_NOTIFICATIONS_ENABLED=False,
        PANEL_WEBHOOK_SECRET="secret",
        TORRENT_BLOCKER_NOTIFICATION_COOLDOWN_SECONDS=0,
    )
    service = torrent_module.TorrentBlockerNotificationService(settings, None, FakeI18n(), None)
    user = SimpleNamespace(
        user_id=123,
        telegram_id=None,
        email="",
        language_code="en",
        telegram_notifications_status=None,
    )

    async def fake_resolve(session, payload):
        return user

    async def fake_suppress(*args, **kwargs):
        return None

    async def fake_log(*args, **kwargs):
        return None

    async def fake_lock(session, user_id):
        return user

    class DummySession:
        async def commit(self):
            return None

        async def rollback(self):
            return None

    monkeypatch.setattr(service, "_resolve_user", fake_resolve)
    monkeypatch.setattr(service, "_channel_suppression_reason", fake_suppress)
    monkeypatch.setattr(torrent_module, "log_user_message_delivery", fake_log)
    monkeypatch.setattr(torrent_module.user_dal, "lock_user_by_id", fake_lock)

    context = {"blocked": True, "marker": "push-probe-1"}
    report = torrent_module.TorrentBlockerReport.from_context(context, fingerprint_secret="secret")
    sent, error = run(
        service._deliver_push(
            DummySession(),  # type: ignore[arg-type]
            user_id=123,
            enabled=True,
            fingerprint=report.fingerprint,
            report=report,
            message_text="Torrent blocked",
            sent_at=datetime.now(UTC),
        )
    )

    assert error is None
    assert sent is True
    assert provider.calls == [
        {"occurrence_id": f"torrent-blocked:{report.fingerprint}", "destination": "device-1"}
    ]


# ---------------------------------------------------------------------------
# Blocker 3: push-only reminders must still send
# ---------------------------------------------------------------------------


def test_expiry_tick_push_only_calls_send_stage(monkeypatch, isolated_channels):
    provider = FakePushProvider()
    channels.register_notification_channel_provider(provider)
    settings = _settings(
        SUBSCRIPTION_NOTIFICATIONS_ENABLED=False,
        SUBSCRIPTION_EMAIL_NOTIFICATIONS_ENABLED=False,
        USER_NOTIFICATION_PUSH_ENABLED=True,
        SUBSCRIPTION_NOTIFY_DAYS_BEFORE=3,
    )
    worker = worker_module.SubscriptionNotificationWorker(
        settings,
        session_factory=None,
        bot=FakeBot(),
        i18n=FakeI18n(),
        panel_service=None,
        subscription_service=None,
    )
    from datetime import timedelta as _timedelta

    sub = _subscription(end_date=datetime.now(UTC) + _timedelta(days=2, hours=1))
    calls: list = []

    class FakeResult:
        def scalars(self):
            class All:
                def all(self):
                    return [sub]

            return All()

    class FakeSession:
        async def execute(self, *args, **kwargs):
            return FakeResult()

    async def fake_latest(session, user_ids, *, now=None):
        return {}

    async def fake_emit(session, subscription, stage, sent_at):
        return None

    async def fake_send_stage(session, subscription, stage, *, sent_at=None):
        calls.append(stage.key)
        return SimpleNamespace(telegram_sent=False, email_sent=False, push_sent=True)

    monkeypatch.setattr(worker_module.subscription_dal, "get_latest_active_end_dates", fake_latest)
    monkeypatch.setattr(worker, "_emit_expiry_event_once", fake_emit)
    monkeypatch.setattr(worker.lifecycle_notifications, "send_stage", fake_send_stage)

    run(worker.expiry_tick(FakeSession()))  # type: ignore[arg-type]

    assert calls, "push-only reminder must still call send_stage"


# ---------------------------------------------------------------------------
# Blocker 4: channel isolation (support)
# ---------------------------------------------------------------------------


def test_support_admin_reply_push_survives_telegram_exception(isolated_channels):
    provider = FakePushProvider()
    channels.register_notification_channel_provider(provider)

    class FailingBot(FakeBot):
        async def send_message(self, chat_id, text, **kwargs):
            raise RuntimeError("telegram down")

    email_service = FakeEmailService()
    service = _support_service(bot=FailingBot(), email_service=email_service)

    run(service.notify_support_admin_reply(_ticket(), _support_message(), _support_user()))

    assert len(email_service.messages) == 1
    assert provider.calls == [
        {"occurrence_id": "support-admin-reply:11", "destination": "device-1"}
    ]


def test_support_admin_reply_push_survives_email_exception(isolated_channels):
    provider = FakePushProvider()
    channels.register_notification_channel_provider(provider)

    class FailingEmail(FakeEmailService):
        async def send_rendered_email(self, *, email, content):
            raise RuntimeError("smtp down")

    bot = FakeBot()
    service = _support_service(bot=bot, email_service=FailingEmail())

    run(service.notify_support_admin_reply(_ticket(), _support_message(), _support_user()))

    assert len(bot.messages) == 1
    assert provider.calls == [
        {"occurrence_id": "support-admin-reply:11", "destination": "device-1"}
    ]


# ---------------------------------------------------------------------------
# Blocker 5: partial device success must stay retryable
# ---------------------------------------------------------------------------


def test_reminder_partial_device_success_retries_failed(monkeypatch, isolated_channels):
    recorded: list = []
    provider = FakePushProvider(destinations=("device-1", "device-2"), failures=("device-2",))
    first = _send_reminder(monkeypatch, recorded, provider=provider)

    assert first.push_sent is True
    # The coarse stage marker must NOT be recorded while a device failed.
    assert "before_3d:push" not in recorded

    provider.failures = ()
    second = _send_reminder(monkeypatch, recorded, provider=provider)

    assert second.push_sent is True
    assert recorded.count("before_3d:push") == 1
    destinations = [call["destination"] for call in provider.calls]
    assert destinations.count("device-1") == 1
    assert destinations.count("device-2") == 2


def test_trial_traffic_partial_device_success_retries_failed(monkeypatch, isolated_channels):
    provider = FakePushProvider(destinations=("device-1", "device-2"), failures=("device-2",))
    channels.register_notification_channel_provider(provider)
    logged = []

    async def fake_log(session, **kwargs):
        logged.append(kwargs)

    monkeypatch.setattr(worker_module, "log_user_message_delivery", fake_log)
    service = _worker()

    first = run(
        service._send_trial_traffic_push(
            object(),
            _subscription(),
            user=_reminder_user(),
            lang="en",
            message_text="Trial used up",
            used=100,
            limit=100,
            sent_at=datetime.now(UTC),
        )
    )
    # Partial delivery is not complete: the caller must not record the marker.
    assert first is False

    provider.failures = ()
    second = run(
        service._send_trial_traffic_push(
            object(),
            _subscription(),
            user=_reminder_user(),
            lang="en",
            message_text="Trial used up",
            used=100,
            limit=100,
            sent_at=datetime.now(UTC),
        )
    )
    assert second is True


# ---------------------------------------------------------------------------
# Blocker 6: payment push retries despite outer Telegram/email success
# ---------------------------------------------------------------------------


def test_payment_canceled_push_retries_after_outer_success(monkeypatch, isolated_channels):
    provider = FakePushProvider(destinations=("device-1",), failures=("device-1",))
    channels.register_notification_channel_provider(provider)
    bot = FakeBot()
    ctx = _reaction_context(bot=bot)
    reactions = CoreEventReactions(ctx)
    monkeypatch.setattr(
        event_reactions, "send_user_notification_email", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(event_reactions, "invalidate_webapp_user_caches", AsyncMock())
    monkeypatch.setattr(
        event_reactions.user_dal, "get_user_by_id", AsyncMock(return_value=_payment_user())
    )
    monkeypatch.setattr(
        event_reactions.payment_dal, "get_payment_by_db_id", AsyncMock(return_value=None)
    )

    run(reactions.on_payment_canceled("payment.canceled", {"user_id": 42, "payment_db_id": 779}))
    assert len(bot.messages) == 1
    assert len(provider.calls) == 1

    provider.failures = ()
    run(reactions.on_payment_canceled("payment.canceled", {"user_id": 42, "payment_db_id": 779}))

    # Telegram/email stay suppressed (outer claim) while push retries.
    assert len(bot.messages) == 1
    assert len(provider.calls) == 2


# ---------------------------------------------------------------------------
# Blocker 9: period/transition identity delivers anew
# ---------------------------------------------------------------------------


def test_subscription_period_change_delivers_push_anew(monkeypatch, isolated_channels):
    from bot.services.subscription_lifecycle_notifications import (
        SubscriptionLifecycleNotificationService,
    )

    recorded: list = []
    provider = FakePushProvider()
    channels.register_notification_channel_provider(provider)
    _history_mocks(monkeypatch, recorded)
    email_service = FakeEmailService()
    settings = _settings(
        SUBSCRIPTION_NOTIFICATIONS_ENABLED=False,
        SUBSCRIPTION_EMAIL_NOTIFICATIONS_ENABLED=False,
    )
    service = SubscriptionLifecycleNotificationService(
        settings, FakeBot(), FakeI18n(), email_service=email_service
    )

    async def send_for(end_date):
        return await service.send_stage(
            object(),
            _subscription(end_date=end_date),
            SubscriptionNotificationStage(
                key="before_3d",
                message_key="subscription_72h_notification",
                days_left=3,
            ),
            user=_reminder_user(),
            telegram_markup="markup",
        )

    first = run(send_for(datetime(2026, 6, 1, tzinfo=UTC)))
    assert first.push_sent is True
    channels._notification_delivery_cache.clear()
    recorded.clear()

    second = run(send_for(datetime(2026, 7, 1, tzinfo=UTC)))
    assert second.push_sent is True
    assert provider.calls[0]["occurrence_id"] != provider.calls[1]["occurrence_id"]


def test_support_ticket_reopen_delivers_closed_push_anew(isolated_channels):
    provider = FakePushProvider()
    channels.register_notification_channel_provider(provider)
    service = _support_service()

    run(service.notify_support_ticket_closed(_ticket(change_cursor=10), _support_user(), None))
    run(service.notify_support_ticket_closed(_ticket(change_cursor=10), _support_user(), None))
    assert len(provider.calls) == 1

    run(service.notify_support_ticket_closed(_ticket(change_cursor=11), _support_user(), None))
    assert len(provider.calls) == 2
