from __future__ import annotations

from typing import Any

from bot.app.web.http_contracts import HttpBodyModel, HttpResponseModel
from bot.services.user_notification_preferences import UserNotificationPreferences


class NotificationPreferencesPatchBody(HttpBodyModel):
    marketing_email: bool | None = None
    marketing_telegram: bool | None = None
    system_email: bool | None = None
    system_telegram: bool | None = None
    push: bool | None = None
    push_notifications_enabled: bool | None = None

    def to_preferences(
        self, current: UserNotificationPreferences | None = None
    ) -> UserNotificationPreferences:
        base = current or UserNotificationPreferences(
            marketing_email=True,
            marketing_telegram=True,
            system_email=True,
            system_telegram=True,
            push_enabled=True,
        )
        requested_push = self.push
        if self.push_notifications_enabled is not None:
            requested_push = self.push_notifications_enabled
        return UserNotificationPreferences(
            marketing_email=self.marketing_email
            if self.marketing_email is not None
            else base.marketing_email,
            marketing_telegram=self.marketing_telegram
            if self.marketing_telegram is not None
            else base.marketing_telegram,
            system_email=self.system_email if self.system_email is not None else base.system_email,
            system_telegram=self.system_telegram
            if self.system_telegram is not None
            else base.system_telegram,
            push_enabled=(requested_push if requested_push is not None else base.push_enabled),
        )


class EmailNotificationPreferencesPatchBody(HttpBodyModel):
    token: str
    marketing_email: bool
    system_email: bool


class NotificationPreferencesOut(HttpResponseModel):
    marketing_email: bool
    marketing_telegram: bool
    system_email: bool
    system_telegram: bool
    push: bool = True
    push_notifications_enabled: bool = True

    @classmethod
    def from_user(cls, user: Any) -> NotificationPreferencesOut:
        preferences = UserNotificationPreferences.from_user(user)
        return cls(
            marketing_email=preferences.marketing_email,
            marketing_telegram=preferences.marketing_telegram,
            system_email=preferences.system_email,
            system_telegram=preferences.system_telegram,
            push=preferences.push_enabled,
            push_notifications_enabled=preferences.push_enabled,
        )
