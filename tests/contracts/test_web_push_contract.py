"""Contract pins for the built-in web_push plugin (ONB-07)."""

from __future__ import annotations

from types import SimpleNamespace

from aiohttp import web
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import SecretStr
from sqlalchemy import create_engine, inspect

from bot.app.web.admin_settings_manifest import get_field_by_key
from bot.app.web.route_contracts import USER_SECURITY, get_contracts
from bot.app.web.webapp.notification_preference_schemas import (
    NotificationPreferencesOut,
    NotificationPreferencesPatchBody,
)
from bot.plugins.builtin import BUILTIN_PLUGINS, WebPushPlugin
from bot.plugins.builtin.web_push.crypto import b64url_encode, public_key_bytes
from bot.plugins.spec import PluginContext
from bot.services.user_notification_preferences import UserNotificationPreferences
from config.web_push_keys import vapid_keys_valid, vapid_subject_valid


def _keys() -> tuple[str, str]:
    private = ec.generate_private_key(ec.SECP256R1())
    return (
        b64url_encode(public_key_bytes(private)),
        b64url_encode(private.private_numbers().private_value.to_bytes(32, "big")),
    )


def test_builtin_loader_lists_web_push():
    names = [plugin.name for plugin in BUILTIN_PLUGINS]
    assert "web_push" in names
    assert names.count("web_push") == 1
    assert issubclass(WebPushPlugin, object)


def test_plugin_migration_prefix_and_idempotency():
    plugin = WebPushPlugin()
    migrations = plugin.migrations()
    assert [migration.id for migration in migrations] == [
        "web_push.0001_initial",
        "web_push.0002_callback_report",
    ]
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        for migration in migrations:
            migration.upgrade(connection)
            migration.upgrade(connection)
        tables = set(inspect(connection).get_table_names())
        delivery_columns = {
            column["name"] for column in inspect(connection).get_columns("ext_web_push_deliveries")
        }
    assert "ext_web_push_subscriptions" in tables
    assert "ext_web_push_deliveries" in tables
    assert "callback_reported_at" in delivery_columns


def test_plugin_web_routes_and_contracts_registered():
    plugin = WebPushPlugin()
    app = web.Application()
    plugin.setup_web(PluginContext(settings=SimpleNamespace()), app, scope="webapp")
    routes = {
        (route.method, route.resource.canonical)
        for route in app.router.routes()
        if route.resource is not None
    }
    assert ("POST", "/api/plugins/web_push/status") in routes
    assert ("POST", "/api/plugins/web_push/subscription") in routes
    assert ("POST", "/api/plugins/web_push/subscription/sync") in routes
    assert ("DELETE", "/api/plugins/web_push/subscription") in routes
    contracts = get_contracts()
    for handler in (
        "web_push_status_route",
        "web_push_subscribe_route",
        "web_push_sync_route",
        "web_push_unsubscribe_route",
    ):
        assert handler in contracts
        assert contracts[handler].security == USER_SECURITY


def test_push_preference_contract_accepts_new_and_legacy_field():
    current = UserNotificationPreferences(
        marketing_email=False,
        marketing_telegram=True,
        system_email=False,
        system_telegram=True,
        push_enabled=True,
    )
    disabled = NotificationPreferencesPatchBody(push_notifications_enabled=False).to_preferences(
        current
    )
    assert disabled.push_enabled is False
    assert disabled.marketing_email is False
    assert disabled.system_email is False
    legacy = NotificationPreferencesPatchBody(push=True).to_preferences(current)
    assert legacy.push_enabled is True
    out = NotificationPreferencesOut.from_user(
        SimpleNamespace(
            marketing_notifications_email_enabled=True,
            marketing_notifications_telegram_enabled=True,
            system_notifications_email_enabled=True,
            system_notifications_telegram_enabled=True,
            push_notifications_enabled=False,
        )
    )
    assert out.push is False
    assert out.push_notifications_enabled is False


def test_admin_manifest_exposes_web_push_without_exposing_secret_value():
    enabled = get_field_by_key("WEB_PUSH_ENABLED")
    private = get_field_by_key("WEB_PUSH_VAPID_PRIVATE_KEY")
    public = get_field_by_key("WEB_PUSH_VAPID_PUBLIC_KEY")
    ttl = get_field_by_key("WEB_PUSH_DELIVERY_TTL_SECONDS")
    assert enabled is not None and enabled.subsection == "web_push"
    assert public is not None and public.subsection == "web_push"
    assert private is not None
    assert private.secret is True
    assert private.allow_empty is True
    assert ttl is not None and ttl.min == 0 and ttl.max == 86400


def test_vapid_key_and_subject_validation():
    public, private = _keys()
    assert vapid_keys_valid(public, private) is True
    other_public, _other_private = _keys()
    assert vapid_keys_valid(other_public, private) is False
    assert vapid_subject_valid("mailto:ops@example.com") is True
    assert vapid_subject_valid("https://example.com/contact") is True
    assert vapid_subject_valid("ops@example.com") is False
    assert vapid_subject_valid("http://example.com") is False


def test_settings_property_requires_matching_pair():
    from config.settings import Settings

    public, private = _keys()
    settings = Settings.model_construct(
        WEB_PUSH_ENABLED=True,
        WEB_PUSH_VAPID_PUBLIC_KEY=public,
        WEB_PUSH_VAPID_PRIVATE_KEY=SecretStr(private),
        WEB_PUSH_VAPID_SUBJECT="mailto:ops@example.com",
    )
    assert settings.web_push_configured is True
    # Admin overrides store secret values as plain strings on the live settings
    # object; validation must keep working after an override is applied.
    settings.WEB_PUSH_VAPID_PRIVATE_KEY = private
    assert settings.web_push_configured is True
    settings.WEB_PUSH_VAPID_SUBJECT = "ops@example.com"
    assert settings.web_push_configured is False
    settings.WEB_PUSH_VAPID_SUBJECT = "mailto:ops@example.com"
    settings.WEB_PUSH_ENABLED = False
    assert settings.web_push_configured is False
