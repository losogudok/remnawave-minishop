"""Shared channel policy for automatic user notifications."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from bot.services.telegram_notifications import (
    TELEGRAM_NOTIFICATIONS_BLOCKED,
    TELEGRAM_NOTIFICATIONS_NEEDS_START,
    normalize_telegram_notification_status,
)
from bot.services.user_notification_preferences import UserNotificationPreferences


class UserNotificationCategory(StrEnum):
    MARKETING = "marketing"
    PAYMENTS = "payments"
    SUBSCRIPTIONS = "subscriptions"
    TRAFFIC = "traffic"
    DEVICES = "devices"
    DEVICE_ACTIVITY = "device_activity"
    DEVICE_LIMIT = "device_limit"
    LIMITS = "limits"
    SUPPORT = "support"
    REFERRALS = "referrals"


@dataclass(frozen=True)
class UserNotificationDeliveryPlan:
    telegram: bool = False
    email: bool = False
    push: bool = False

    @property
    def any_enabled(self) -> bool:
        return self.telegram or self.email or self.push


PUSH_NOTIFICATIONS_ENABLED_SETTING = "USER_NOTIFICATION_PUSH_ENABLED"


_CATEGORY_SETTING_KEYS: dict[UserNotificationCategory, tuple[str, str]] = {
    UserNotificationCategory.MARKETING: (
        "USER_NOTIFICATION_MARKETING_TELEGRAM_ENABLED",
        "USER_NOTIFICATION_MARKETING_EMAIL_ENABLED",
    ),
    UserNotificationCategory.PAYMENTS: (
        "USER_NOTIFICATION_PAYMENTS_TELEGRAM_ENABLED",
        "USER_NOTIFICATION_PAYMENTS_EMAIL_ENABLED",
    ),
    UserNotificationCategory.SUBSCRIPTIONS: (
        "SUBSCRIPTION_NOTIFICATIONS_ENABLED",
        "SUBSCRIPTION_EMAIL_NOTIFICATIONS_ENABLED",
    ),
    UserNotificationCategory.TRAFFIC: (
        "USER_NOTIFICATION_TRAFFIC_TELEGRAM_ENABLED",
        "USER_NOTIFICATION_TRAFFIC_EMAIL_ENABLED",
    ),
    UserNotificationCategory.DEVICES: (
        "USER_NOTIFICATION_DEVICES_TELEGRAM_ENABLED",
        "USER_NOTIFICATION_DEVICES_EMAIL_ENABLED",
    ),
    UserNotificationCategory.DEVICE_ACTIVITY: (
        "USER_NOTIFICATION_DEVICE_ACTIVITY_TELEGRAM_ENABLED",
        "USER_NOTIFICATION_DEVICE_ACTIVITY_EMAIL_ENABLED",
    ),
    UserNotificationCategory.DEVICE_LIMIT: (
        "USER_NOTIFICATION_DEVICE_LIMIT_TELEGRAM_ENABLED",
        "USER_NOTIFICATION_DEVICE_LIMIT_EMAIL_ENABLED",
    ),
    UserNotificationCategory.LIMITS: (
        "TORRENT_BLOCKER_TELEGRAM_NOTIFICATIONS_ENABLED",
        "TORRENT_BLOCKER_EMAIL_NOTIFICATIONS_ENABLED",
    ),
    UserNotificationCategory.SUPPORT: (
        "USER_NOTIFICATION_SUPPORT_TELEGRAM_ENABLED",
        "USER_NOTIFICATION_SUPPORT_EMAIL_ENABLED",
    ),
    UserNotificationCategory.REFERRALS: (
        "USER_NOTIFICATION_REFERRALS_TELEGRAM_ENABLED",
        "USER_NOTIFICATION_REFERRALS_EMAIL_ENABLED",
    ),
}


def user_notification_channel_selected(
    settings: Any,
    category: UserNotificationCategory,
    channel: str,
) -> bool:
    if channel == "push":
        return push_notifications_selected(settings, category)
    if channel != "telegram" and channel != "email":
        # Unknown channels never fall through to another channel (in
        # particular, never to email): they are simply not selected.
        return False
    telegram_key, email_key = _CATEGORY_SETTING_KEYS[category]
    key = telegram_key if channel == "telegram" else email_key
    return bool(getattr(settings, key, True))


def push_notifications_selected(settings: Any, category: UserNotificationCategory) -> bool:
    """Whether automatic push mirroring is selected for a category.

    Push has a single Core kill-switch shared by every category (service,
    support, and news/promotions) instead of per-category channel toggles.
    """

    del category
    return bool(getattr(settings, PUSH_NOTIFICATIONS_ENABLED_SETTING, True))


def push_notifications_allowed(user: Any) -> bool:
    """Return the single user Web Push opt-in, independent of other channels.

    One preference covers service/support updates and news/promotions, and it
    never depends on Telegram delivery state or Telegram ownership.
    """

    return bool(getattr(user, "push_notifications_enabled", True))


def user_notification_channel_allowed(
    user: Any,
    category: UserNotificationCategory,
    channel: str,
) -> bool:
    """Return the user's opt-in independently from channel availability."""

    preferences = UserNotificationPreferences.from_user(user)
    if channel == "push":
        return push_notifications_allowed(user)
    if channel != "telegram" and channel != "email":
        # Unknown channels never fall through to another channel (in
        # particular, never to email): they are simply not allowed.
        return False
    if category == UserNotificationCategory.SUPPORT:
        return True
    if category == UserNotificationCategory.MARKETING:
        return (
            preferences.marketing_telegram if channel == "telegram" else preferences.marketing_email
        )
    return preferences.system_telegram if channel == "telegram" else preferences.system_email


def telegram_recipient(user: Any, fallback_user_id: Any = None) -> int | None:
    """Return a reachable linked Telegram chat id, if one is known."""

    chat_id = 0
    for candidate in (getattr(user, "telegram_id", None),):
        if not isinstance(candidate, (int, float, str)):
            continue
        try:
            chat_id = int(candidate or 0)
        except (TypeError, ValueError):
            continue
        if chat_id > 0:
            break
    if chat_id <= 0:
        return None
    status = normalize_telegram_notification_status(
        getattr(user, "telegram_notifications_status", None)
    )
    if status in {TELEGRAM_NOTIFICATIONS_NEEDS_START, TELEGRAM_NOTIFICATIONS_BLOCKED}:
        return None
    return chat_id


def smtp_delivery_available(settings: Any) -> bool:
    """Use the SMTP capability independently of email login settings."""
    return bool(settings.smtp_delivery_configured)


def email_recipient(settings: Any, user: Any) -> str:
    """Return a linked email only when the application can deliver email."""

    if not smtp_delivery_available(settings):
        return ""
    return (
        str(getattr(user, "notification_email", None) or getattr(user, "email", "") or "")
        .strip()
        .lower()
    )


def user_notifications_blocked(user: Any) -> bool:
    """Do not deliver automatic notifications to banned accounts."""

    return bool(getattr(user, "is_banned", False))


def user_notification_delivery_plan(
    settings: Any,
    category: UserNotificationCategory,
    user: Any,
    *,
    telegram_available: bool | None = None,
    email_available: bool | None = None,
    push_available: bool | None = None,
) -> UserNotificationDeliveryPlan:
    """Resolve enabled channels, including the optional single-channel fallback.

    Fallback is intentionally based on recipient availability, not a transient
    send failure. When both preferences are disabled, no fallback is possible.
    Push is resolved independently of the Telegram/email fallback: another
    channel succeeding or failing never enables or suppresses push, and push
    availability never depends on Telegram delivery state or ownership.
    """

    if user_notifications_blocked(user):
        return UserNotificationDeliveryPlan()

    telegram_selected = user_notification_channel_selected(settings, category, "telegram")
    email_selected = user_notification_channel_selected(settings, category, "email")
    telegram_allowed = user_notification_channel_allowed(user, category, "telegram")
    email_allowed = user_notification_channel_allowed(user, category, "email")

    effective_telegram_selected = telegram_selected and telegram_allowed
    effective_email_selected = email_selected and email_allowed
    if not effective_telegram_selected and not effective_email_selected:
        return UserNotificationDeliveryPlan(
            push=_push_plan_enabled(settings, category, user, push_available)
        )

    if telegram_available is None:
        telegram_available = telegram_recipient(user) is not None
    telegram_available = bool(telegram_available and getattr(settings, "TELEGRAM_ENABLED", True))
    if email_available is None:
        email_available = bool(email_recipient(settings, user))

    send_telegram = effective_telegram_selected and telegram_available
    send_email = effective_email_selected and email_available
    send_push = _push_plan_enabled(settings, category, user, push_available)
    if send_telegram or send_email:
        return UserNotificationDeliveryPlan(
            telegram=send_telegram,
            email=send_email,
            push=send_push,
        )

    fallback_enabled = bool(settings.USER_NOTIFICATION_SINGLE_CHANNEL_FALLBACK_ENABLED)
    if not fallback_enabled or telegram_selected == email_selected:
        return UserNotificationDeliveryPlan(push=send_push)

    return UserNotificationDeliveryPlan(
        telegram=bool(telegram_allowed and telegram_available),
        email=bool(email_allowed and email_available),
        push=send_push,
    )


def _push_plan_enabled(
    settings: Any,
    category: UserNotificationCategory,
    user: Any,
    push_available: bool | None,
) -> bool:
    if not push_notifications_selected(settings, category):
        return False
    if not push_notifications_allowed(user):
        return False
    if push_available is None:
        # Local import: notification_channels owns the provider registry and
        # already depends on this policy module.
        from bot.services.notification_channels import push_provider_available

        return push_provider_available(settings)
    return bool(push_available)
