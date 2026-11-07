from types import SimpleNamespace

from bot.services.email_templates_common import EmailContent
from bot.services.user_notification_preferences import (
    UserNotificationPreferences,
    add_user_email_preferences_footer,
    create_email_preferences_token,
    token_matches_user_email,
    verify_email_preferences_token,
)


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        WEBAPP_SESSION_SECRET="stable-test-secret",
        SUBSCRIPTION_MINI_APP_URL="https://app.example.test",
        DEFAULT_LANGUAGE="en",
    )


def _user(**overrides: object) -> SimpleNamespace:
    values = {
        "user_id": 42,
        "email": "user@example.test",
        "notification_email": "alerts@example.test",
        "language_code": "en",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_preferences_default_to_enabled_for_legacy_user_objects() -> None:
    assert UserNotificationPreferences.from_user(_user()) == UserNotificationPreferences()


def test_signed_token_is_bound_to_current_notification_email() -> None:
    settings = _settings()
    user = _user()
    token = create_email_preferences_token(settings, user)
    verified = verify_email_preferences_token(settings, token)

    assert verified is not None
    assert verified[0] == 42
    assert token_matches_user_email(user, verified[1])

    user.notification_email = "new@example.test"
    assert not token_matches_user_email(user, verified[1])


def test_invalid_signature_is_rejected() -> None:
    token = create_email_preferences_token(_settings(), _user())

    assert verify_email_preferences_token(_settings(), f"{token}x") is None


def test_footer_contains_public_preferences_link() -> None:
    content = add_user_email_preferences_footer(
        EmailContent(subject="Subject", text="Body", html="<html><body>Body</body></html>"),
        settings=_settings(),
        i18n=None,
        user=_user(),
    )

    assert "Manage email notifications" in content.text
    assert "https://app.example.test/unsubscribe?token=" in content.text
    assert "/unsubscribe?token=" in content.html


def test_footer_is_unchanged_when_user_preferences_are_disabled() -> None:
    original = EmailContent(subject="Subject", text="Body", html="<html><body>Body</body></html>")
    settings = _settings()
    settings.USER_NOTIFICATION_PREFERENCES_ENABLED = False

    content = add_user_email_preferences_footer(
        original,
        settings=settings,
        i18n=None,
        user=_user(),
    )

    assert content is original


def test_single_push_preference_round_trip() -> None:
    from bot.services.user_notification_preferences import apply_user_notification_preferences

    user = _user(push_notifications_enabled=False)
    preferences = UserNotificationPreferences.from_user(user)

    assert preferences.push_enabled is False
    # Telegram/email preferences are preserved alongside the push toggle.
    assert preferences.as_dict() == {
        "marketing_email": True,
        "marketing_telegram": True,
        "system_email": True,
        "system_telegram": True,
        "push": False,
    }

    apply_user_notification_preferences(user, UserNotificationPreferences())
    assert user.push_notifications_enabled is True
    assert UserNotificationPreferences.from_user(user).push_enabled is True


def test_patch_body_omitted_fields_preserve_push() -> None:
    from bot.app.web.webapp.notification_preference_schemas import NotificationPreferencesPatchBody

    current = UserNotificationPreferences(
        marketing_email=False,
        marketing_telegram=False,
        system_email=False,
        system_telegram=False,
        push_enabled=False,
    )
    # Empty PATCH preserves every existing value, including push=False.
    merged = NotificationPreferencesPatchBody().to_preferences(current)
    assert merged == current

    # Explicit toggle flips only the targeted field.
    toggled = NotificationPreferencesPatchBody(push=True).to_preferences(current)
    assert toggled.push_enabled is True
    assert toggled.marketing_email is False
    assert toggled.system_telegram is False


def test_patch_body_explicit_push_toggle() -> None:
    from bot.app.web.webapp.notification_preference_schemas import NotificationPreferencesPatchBody

    current = UserNotificationPreferences()
    assert current.push_enabled is True
    disabled = NotificationPreferencesPatchBody(push=False).to_preferences(current)
    assert disabled.push_enabled is False
    assert disabled.marketing_email is True


def test_email_unsubscribe_flow_preserves_push() -> None:
    from bot.services.user_notification_preferences import apply_user_notification_preferences

    user = _user(push_notifications_enabled=False)
    current = UserNotificationPreferences.from_user(user)
    assert current.push_enabled is False
    # Mirrors email_notification_preferences_update_route: only email flags
    # change; telegram and push stay exactly as stored.
    apply_user_notification_preferences(
        user,
        UserNotificationPreferences(
            marketing_email=False,
            marketing_telegram=current.marketing_telegram,
            system_email=False,
            system_telegram=current.system_telegram,
            push_enabled=current.push_enabled,
        ),
    )
    assert user.push_notifications_enabled is False


def test_preferences_out_response_exposes_push() -> None:
    from bot.app.web.webapp.notification_preference_schemas import NotificationPreferencesOut

    out = NotificationPreferencesOut.from_user(_user(push_notifications_enabled=False))
    assert out.model_dump(mode="json")["push"] is False
    out_enabled = NotificationPreferencesOut.from_user(_user(push_notifications_enabled=True))
    assert out_enabled.model_dump(mode="json")["push"] is True
