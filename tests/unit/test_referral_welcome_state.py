from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

from bot.app.web.webapp.referral_welcome_state import resolve_referral_welcome_state
from config.settings import Settings
from db.models import User
from tests.support.settings_stub import settings_stub


def _user(**overrides: object) -> User:
    values: dict[str, object] = {
        "referred_by_id": None,
        "telegram_id": None,
        "email": "client@example.com",
        "referral_welcome_bonus_claimed_at": None,
    }
    values.update(overrides)
    return cast(User, SimpleNamespace(**values))


def _settings() -> Settings:
    return cast(
        Settings,
        settings_stub(
            REFERRAL_PROGRAM_ENABLED=False,
            REFERRAL_WELCOME_BONUS_DAYS=3,
            REFERRAL_WELCOME_BONUS_WITHOUT_TELEGRAM_ENABLED=False,
        ),
    )


def test_partner_client_sees_pending_welcome_bonus_when_referrals_are_disabled() -> None:
    result = resolve_referral_welcome_state(
        _settings(),
        _user(),
        ordinary_referral_enabled_for_user=False,
        partner_client_eligible=True,
        has_active_subscription=False,
    )

    assert result == (3, "telegram_required")


def test_disabled_referrals_hide_stale_ordinary_welcome_bonus() -> None:
    result = resolve_referral_welcome_state(
        _settings(),
        _user(referred_by_id=7),
        ordinary_referral_enabled_for_user=False,
        partner_client_eligible=False,
        has_active_subscription=False,
    )

    assert result == (0, None)


def test_claimed_partner_welcome_bonus_is_not_offered_again() -> None:
    result = resolve_referral_welcome_state(
        _settings(),
        _user(referral_welcome_bonus_claimed_at=datetime(2026, 1, 1, tzinfo=UTC)),
        ordinary_referral_enabled_for_user=False,
        partner_client_eligible=True,
        has_active_subscription=False,
    )

    assert result == (3, None)
