"""Public contracts for the built-in GA4 funnel plugin."""

from __future__ import annotations

import json
from pathlib import Path

from bot.app.web.admin_settings_manifest import get_field_by_key, manifest_payload
from bot.plugins.builtin import BUILTIN_PLUGINS
from bot.plugins.builtin.ga4funnel import Ga4FunnelPlugin
from bot.plugins.builtin.ga4funnel.migrations import CHAIN_GA4FUNNEL
from bot.plugins.builtin.ga4funnel.signals import resolve_signals
from bot.plugins.extensions import SignalContext
from bot.plugins.spec import PluginContext
from config.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
OPENAPI_PATH = REPO_ROOT / "docs" / "openapi.json"
LOCALES = REPO_ROOT / "locales"


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        BOT_TOKEN="x",
        POSTGRES_USER="u",
        POSTGRES_PASSWORD="p",
        ADMIN_IDS="1",
        GA4_ENABLED=True,
        GA4_MEASUREMENT_ID="G-ABCD1234",
        GA4_API_SECRET="server-secret",
    )


def test_manifest_exposes_analytics_group_with_secret_flag() -> None:
    fields = {item["key"]: item for item in manifest_payload() if item["section"] == "analytics"}

    assert {
        "GA4_ENABLED",
        "GA4_MEASUREMENT_ID",
        "GA4_API_SECRET",
        "GA4_DEBUG_MODE",
    } == set(fields)
    assert fields["GA4_ENABLED"]["type"] == "bool"
    assert fields["GA4_MEASUREMENT_ID"]["type"] == "string"
    assert fields["GA4_DEBUG_MODE"]["type"] == "bool"
    assert fields["GA4_API_SECRET"]["secret"] is True
    secret_field = get_field_by_key("GA4_API_SECRET")
    assert secret_field is not None
    assert secret_field.allow_empty is True
    assert {item["subsection"] for item in fields.values()} == {"analytics"}
    assert {item["i18n_subsection_key"] for item in fields.values()} == {
        "admin_settings_group_analytics"
    }


def test_analytics_locale_keys_exist_in_both_base_locales() -> None:
    required = {
        "admin_settings_section_analytics",
        "admin_settings_group_analytics",
        "admin_settings_group_analytics_hint",
        "admin_settings_field_ga4_enabled_label",
        "admin_settings_field_ga4_enabled_description",
        "admin_settings_field_ga4_measurement_id_label",
        "admin_settings_field_ga4_measurement_id_description",
        "admin_settings_field_ga4_api_secret_label",
        "admin_settings_field_ga4_api_secret_description",
        "admin_settings_field_ga4_debug_mode_label",
        "admin_settings_field_ga4_debug_mode_description",
    }
    for language in ("ru", "en"):
        messages = json.loads((LOCALES / f"{language}.json").read_text(encoding="utf-8"))
        assert not {key for key in required if not str(messages.get(key, "")).strip()}


def test_openapi_artifact_contains_public_and_authenticated_routes() -> None:
    spec = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    paths = spec["paths"]

    assert "/api/plugins/ga4funnel/config" in paths
    assert "/api/plugins/ga4funnel/signals.mjs" in paths
    assert "/api/plugins/ga4funnel/frame" in paths
    assert "/api/plugins/ga4funnel/frame.mjs" in paths
    frame = paths["/api/plugins/ga4funnel/frame"]["get"]
    assert frame["responses"]["200"]["content"].keys() == {"text/html"}
    frame_script = paths["/api/plugins/ga4funnel/frame.mjs"]["get"]
    assert frame_script["responses"]["200"]["content"].keys() == {"application/javascript"}
    bind = paths["/api/plugins/ga4funnel/bind"]["post"]
    assert bind["security"] == [{"UserSession": []}, {"UserBearer": []}]
    assert paths["/api/plugins/ga4funnel/signals.mjs"]["get"]["responses"]["200"][
        "content"
    ].keys() == {"application/javascript"}


def test_plugin_contributes_signal_jobs_and_durable_payment_subscription() -> None:
    names = [plugin.name for plugin in BUILTIN_PLUGINS]
    assert "ga4funnel" in names
    plugin_cls = next(item for item in BUILTIN_PLUGINS if item is Ga4FunnelPlugin)
    contributions = plugin_cls().extensions(PluginContext(settings=make_settings()))
    assert [provider.id for provider in contributions.signals] == ["web"]
    assert {job.id for job in contributions.jobs} == {"capture_payment", "reconcile"}
    assert [(item.event, item.job) for item in contributions.events] == [
        ("payment.succeeded", "capture_payment")
    ]
    assert [migration.id for migration in CHAIN_GA4FUNNEL] == ["ga4funnel.0001_initial"]


def test_descriptor_never_carries_the_measurement_protocol_secret() -> None:
    import asyncio

    contribution = asyncio.run(resolve_signals(SignalContext(settings=make_settings())))
    assert contribution is not None
    dumped = json.dumps(contribution.config).lower()
    assert "server-secret" not in dumped
    assert "api_secret" not in dumped
    assert "secret" not in dumped
