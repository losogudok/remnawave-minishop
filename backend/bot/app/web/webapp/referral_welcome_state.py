from config.settings import Settings
from db.models import User

from .auth_common import _referral_welcome_telegram_required_reason


def resolve_referral_welcome_state(
    settings: Settings,
    user: User,
    *,
    ordinary_referral_enabled_for_user: bool,
    partner_client_eligible: bool,
    has_active_subscription: bool,
) -> tuple[int, str | None]:
    configured_days = max(0, int(settings.referral_settings.welcome_bonus_days or 0))
    visible = ordinary_referral_enabled_for_user or partner_client_eligible
    welcome_days = configured_days if visible else 0
    eligible_source = (
        ordinary_referral_enabled_for_user and bool(user.referred_by_id)
    ) or partner_client_eligible
    already_claimed = getattr(user, "referral_welcome_bonus_claimed_at", None) is not None
    if not eligible_source or already_claimed or has_active_subscription or welcome_days <= 0:
        return welcome_days, None
    return welcome_days, _referral_welcome_telegram_required_reason(settings, user)
