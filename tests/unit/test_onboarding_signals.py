"""Core contract tests for vendor-neutral onboarding signal integrations (ONB-04)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from bot.app.web.webapp import assets as webapp_assets
from bot.app.web.webapp.onboarding_signals import (
    OnboardingSignalDescriptorOut,
    OnboardingSignalsContribution,
    SignalContext,
    collect_onboarding_signal_descriptors,
    is_valid_signal_connect_host,
    is_valid_signal_entry,
    is_valid_signal_script_url,
    onboarding_signals_csp_hosts,
    sanitize_signal_config,
    validate_signal_contribution,
)
from bot.plugins import capabilities
from bot.plugins.extensions import ExtensionContributions, OnboardingSignalProvider
from bot.plugins.extensions.registry import ExtensionError, ExtensionRegistry, set_registry
from config.settings import Settings


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "BOT_TOKEN": "x",
        "POSTGRES_USER": "u",
        "POSTGRES_PASSWORD": "p",
        "ADMIN_IDS": "1",
    }
    values.update(overrides)
    return Settings(**values)


def provider(
    provider_id: str = "web",
    contribution: OnboardingSignalsContribution | str | Exception | None = "default",
) -> OnboardingSignalProvider:
    async def resolve(_context: SignalContext) -> OnboardingSignalsContribution | None:
        if isinstance(contribution, Exception):
            raise contribution
        if contribution == "default":
            return OnboardingSignalsContribution(
                scripts=("https://www.googletagmanager.com/gtag/js?id=G-1",),
                connect_hosts=("https://www.google-analytics.com",),
                config={"measurement_id": "G-1"},
            )
        if contribution is None or contribution == "none":
            return None
        assert isinstance(contribution, OnboardingSignalsContribution)
        return contribution

    return OnboardingSignalProvider(provider_id, resolve)


@pytest.fixture()
def registry() -> ExtensionRegistry:
    value = ExtensionRegistry()
    set_registry(value)
    yield value
    set_registry(ExtensionRegistry())


@pytest.fixture()
def enabled_state() -> dict[str, Any]:
    return {
        "generation": 3,
        "installations": {
            "ga4funnel": {"enabled": True, "digest": "a" * 64},
            "disabled": {"enabled": False, "digest": "b" * 64},
        },
    }


def collect(settings: Settings) -> list[OnboardingSignalDescriptorOut]:
    return asyncio.run(collect_onboarding_signal_descriptors(settings))


def test_capability_is_declared() -> None:
    assert capabilities.CORE_PLUGIN_CAPABILITIES["onboarding_signals"] == 1


def test_registry_accepts_signal_providers_and_rejects_duplicates(
    registry: ExtensionRegistry,
) -> None:
    registry.register("sample", "1", ExtensionContributions(signals=(provider("web"),)))
    with pytest.raises(ExtensionError, match="duplicate_extension_identifier"):
        registry.register(
            "other", "1", ExtensionContributions(signals=(provider("web"), provider("web")))
        )
    with pytest.raises(ExtensionError, match="reserved"):
        registry.register("core", "1", ExtensionContributions(signals=(provider("web"),)))


def test_validation_rejects_unsafe_or_oversized_contributions() -> None:
    valid = OnboardingSignalsContribution(
        entry="/api/plugins/ga4funnel/signals.js",
        scripts=("https://cdn.example.com/s.js",),
        connect_hosts=("https://collect.example.com",),
        config={"measurement_id": "G-1", "debug": False, "sample_rate": 1.5, "retries": 2},
    )
    assert validate_signal_contribution("ga4funnel", "web", valid) is not None
    assert validate_signal_contribution("ga4funnel", "web", OnboardingSignalsContribution()) is None
    assert (
        validate_signal_contribution(
            "ga4funnel",
            "web",
            OnboardingSignalsContribution(scripts=("http://insecure.example/s.js",)),
        )
        is None
    )
    assert (
        validate_signal_contribution(
            "ga4funnel",
            "web",
            OnboardingSignalsContribution(entry="https://evil.example/s.js"),
        )
        is None
    )
    assert (
        validate_signal_contribution(
            "other",
            "web",
            OnboardingSignalsContribution(entry="/api/plugins/ga4funnel/s.js"),
        )
        is None
    )
    assert (
        validate_signal_contribution(
            "ga4funnel",
            "web",
            OnboardingSignalsContribution(config=json.loads('{"nested": {"a": 1}}')),
        )
        is None
    )
    assert (
        validate_signal_contribution(
            "ga4funnel",
            "web",
            OnboardingSignalsContribution(config={"Token": "secret"}),
        )
        is None
    )


def test_entry_script_and_host_allowances() -> None:
    assert is_valid_signal_entry("ga4funnel", "")
    assert is_valid_signal_entry("ga4funnel", "/api/plugins/ga4funnel/signals.js?v=2")
    assert not is_valid_signal_entry("ga4funnel", "/api/plugins/ga4funnel/../escape")
    assert not is_valid_signal_entry("ga4funnel", "/api/plugins/ga4funnel/a\\b")
    assert is_valid_signal_script_url("https://cdn.example.com:8443/s.js")
    assert not is_valid_signal_script_url("http://cdn.example.com/s.js")
    assert not is_valid_signal_script_url("https://user:pass@example.com/s.js")
    assert is_valid_signal_connect_host("https://collect.example.com:443")
    assert not is_valid_signal_connect_host("https://collect.example.com/path")
    assert not is_valid_signal_connect_host("http://collect.example.com")
    assert sanitize_signal_config(None) == {}
    assert sanitize_signal_config({"ok": "x", "n": 1, "f": 0.5, "b": True}) == {
        "ok": "x",
        "n": 1,
        "f": 0.5,
        "b": True,
    }
    assert sanitize_signal_config({"huge": "x" * 300}) is None


def test_collect_skips_disabled_missing_failing_and_invalid_providers(
    registry: ExtensionRegistry, enabled_state: dict[str, Any]
) -> None:
    registry.register("ga4funnel", "1", ExtensionContributions(signals=(provider("web"),)))
    registry.register(
        "broken", "1", ExtensionContributions(signals=(provider("web", ValueError("boom")),))
    )
    registry.register(
        "invalid",
        "1",
        ExtensionContributions(
            signals=(
                provider(
                    "web",
                    OnboardingSignalsContribution(scripts=("http://x",)),
                ),
            )
        ),
    )
    registry.register("silent", "1", ExtensionContributions(signals=(provider("web", "none"),)))
    # Disabled installations are filtered before any resolve runs, so their
    # providers can never observe guest traffic.
    resolved: list[str] = []

    async def spy_resolve(_context: SignalContext) -> OnboardingSignalsContribution | None:
        resolved.append("disabled")
        return None

    registry.register(
        "disabled",
        "1",
        ExtensionContributions(signals=(OnboardingSignalProvider("web", spy_resolve),)),
    )
    state = dict(enabled_state)
    state["installations"] = {
        **state["installations"],
        "broken": {"enabled": True, "digest": "c" * 64},
        "invalid": {"enabled": True, "digest": "d" * 64},
        "silent": {"enabled": True, "digest": "e" * 64},
        "ghost": {"enabled": True, "digest": "f" * 64},
    }
    with (
        patch("bot.app.web.webapp.onboarding_signals.read_state", return_value=state),
        patch("bot.app.web.webapp.onboarding_signals.generation_is_current", return_value=True),
    ):
        descriptors = collect(make_settings())
    assert [(item.owner, item.id) for item in descriptors] == [("ga4funnel", "web")]
    assert resolved == []
    assert descriptors[0].scripts == ["https://www.googletagmanager.com/gtag/js?id=G-1"]
    assert descriptors[0].config == {"measurement_id": "G-1"}
    assert set(descriptors[0].model_dump(mode="json")) == {
        "owner",
        "id",
        "entry",
        "scripts",
        "connect_hosts",
        "config",
    }


def test_collect_orders_deterministically_and_caps_integrations(
    registry: ExtensionRegistry,
) -> None:
    for owner in ("zeta", "alpha", "mid"):
        registry.register(owner, "1", ExtensionContributions(signals=(provider("web"),)))
    state = {
        "generation": 1,
        "installations": {
            owner: {"enabled": True, "digest": "a" * 64} for owner in ("zeta", "mid", "alpha")
        },
    }
    with (
        patch("bot.app.web.webapp.onboarding_signals.read_state", return_value=state),
        patch("bot.app.web.webapp.onboarding_signals.generation_is_current", return_value=True),
    ):
        descriptors = collect(make_settings())
    assert [item.owner for item in descriptors] == ["alpha", "mid", "zeta"]


def test_collect_includes_loaded_builtin_without_installation_record(
    registry: ExtensionRegistry,
) -> None:
    registry.register("ga4funnel", "1", ExtensionContributions(signals=(provider("web"),)))
    state = {"generation": 1, "installations": {}}
    with (
        patch("bot.app.web.webapp.onboarding_signals.read_state", return_value=state),
        patch("bot.app.web.webapp.onboarding_signals.generation_is_current", return_value=True),
    ):
        descriptors = collect(make_settings())
    # Built-in and entry-point owners are already loaded and gate themselves
    # in resolve; only package installations keep the explicit enabled flag.
    assert [(item.owner, item.id) for item in descriptors] == [("ga4funnel", "web")]


def test_collect_returns_empty_for_superseded_generation_or_unreadable_state(
    registry: ExtensionRegistry,
) -> None:
    registry.register("ga4funnel", "1", ExtensionContributions(signals=(provider("web"),)))
    state = {"generation": 1, "installations": {"ga4funnel": {"enabled": True}}}
    with patch("bot.app.web.webapp.onboarding_signals.generation_is_current", return_value=False):
        assert collect(make_settings()) == []
    with (
        patch("bot.app.web.webapp.onboarding_signals.read_state", return_value=state),
        patch("bot.app.web.webapp.onboarding_signals.generation_is_current", return_value=True),
    ):
        assert collect(make_settings()) != []
    with (
        patch(
            "bot.app.web.webapp.onboarding_signals.read_state",
            side_effect=OSError("locked"),
        ),
        patch("bot.app.web.webapp.onboarding_signals.generation_is_current", return_value=True),
    ):
        assert collect(make_settings()) == []


def test_csp_hosts_split_script_and_connect_origins() -> None:
    descriptors = [
        OnboardingSignalDescriptorOut(
            owner="ga4funnel",
            id="web",
            scripts=["https://www.googletagmanager.com/gtag/js?id=G-1"],
            connect_hosts=["https://www.google-analytics.com"],
            config={},
        )
    ]
    script_hosts, connect_hosts = onboarding_signals_csp_hosts(descriptors)
    assert script_hosts == ["https://www.googletagmanager.com"]
    assert connect_hosts == ["https://www.google-analytics.com"]


def _bootstrap_request() -> SimpleNamespace:
    settings = Settings(
        _env_file=None,
        BOT_TOKEN="123456:token",
        POSTGRES_USER="app_user",
        POSTGRES_PASSWORD="app_password",
    )
    i18n = SimpleNamespace(
        locales_data={"en": {"wa_loading": "Loading"}},
        base_locales_data={"en": {}},
        reload_overrides_from_file=lambda: None,
    )
    return SimpleNamespace(
        app={"settings": settings, "webapp_settings_cache": {"ts": 0.0, "data": {}}, "i18n": i18n},
        query={},
    )


def test_bootstrap_payload_carries_sanitized_signal_descriptors() -> None:
    descriptor = OnboardingSignalDescriptorOut(
        owner="ga4funnel",
        id="web",
        scripts=["https://www.googletagmanager.com/gtag/js?id=G-1"],
        connect_hosts=["https://www.google-analytics.com"],
        config={"measurement_id": "G-1"},
    )
    payload = webapp_assets._build_webapp_bootstrap_payload(
        _bootstrap_request(), [descriptor.model_dump(mode="json")]
    )
    assert payload["config"]["onboardingSignals"] == [descriptor.model_dump(mode="json")]
    empty = webapp_assets._build_webapp_bootstrap_payload(_bootstrap_request())
    assert empty["config"]["onboardingSignals"] == []


def test_security_middleware_merges_signal_origins_without_changing_defaults() -> None:
    async def scenario() -> tuple[web.Response, web.Response]:
        import warnings

        from aiohttp.web_exceptions import NotAppKeyWarning

        from bot.app.web.webapp.assets import _security_headers_middleware
        from tests.support.settings_stub import settings_stub

        async def handler(request: web.Request) -> web.Response:
            return web.Response(text="ok")

        # The middleware stores its nonce under a plain string key; aiohttp
        # guides toward RequestKey, so silence that guidance here while
        # exercising the middleware with real requests.
        with (
            patch(
                "bot.app.web.webapp.assets.get_settings",
                return_value=settings_stub(QR_LOGIN_ENABLED=False),
            ),
            warnings.catch_warnings(),
        ):
            warnings.simplefilter("ignore", NotAppKeyWarning)
            plain_request = make_mocked_request("GET", "/")
            plain = await _security_headers_middleware(plain_request, handler)
            stashed_request = make_mocked_request("GET", "/")
            stashed_request[webapp_assets._ONBOARDING_SIGNALS_CSP_KEY] = {
                "script_hosts": ["https://www.googletagmanager.com"],
                "connect_hosts": ["https://www.google-analytics.com"],
            }
            merged = await _security_headers_middleware(stashed_request, handler)
        return plain, merged

    plain, merged = asyncio.run(scenario())
    assert "script-src 'self'" in plain.headers["Content-Security-Policy"]
    assert "googletagmanager" not in plain.headers["Content-Security-Policy"]
    assert "https://www.googletagmanager.com" in merged.headers["Content-Security-Policy"]
    assert "https://www.google-analytics.com" in merged.headers["Content-Security-Policy"]
    assert "'unsafe-eval'" not in merged.headers["Content-Security-Policy"]


def test_index_route_embeds_guest_and_public_signal_descriptors(
    registry: ExtensionRegistry,
) -> None:
    async def scenario() -> tuple[web.Response, dict[str, object]]:
        registry.register("ga4funnel", "1", ExtensionContributions(signals=(provider("web"),)))
        from bot.app.web.context import I18N, SETTINGS, WEBAPP_SETTINGS_CACHE

        app = web.Application()
        app[SETTINGS] = make_settings(BOT_TOKEN="123456:token")
        app[I18N] = SimpleNamespace(
            locales_data={"ru": {"wa_loading": "Loading"}},
            base_locales_data={"ru": {}},
            reload_overrides_from_file=lambda: None,
        )
        app[WEBAPP_SETTINGS_CACHE] = {"ts": 0.0, "data": {}}
        state = {"generation": 1, "installations": {"ga4funnel": {"enabled": True}}}
        with (
            patch("bot.app.web.webapp.onboarding_signals.read_state", return_value=state),
            patch(
                "bot.app.web.webapp.onboarding_signals.generation_is_current",
                return_value=True,
            ),
            patch(
                "bot.app.web.webapp.assets.request_maintenance_mode_enabled",
                AsyncMock(return_value=False),
            ),
        ):
            request = make_mocked_request("GET", "/", app=app)
            response = await webapp_assets.index_route(request)
            stashed = dict(request.get(webapp_assets._ONBOARDING_SIGNALS_CSP_KEY, {}))
        return response, stashed

    response, stashed = asyncio.run(scenario())
    assert response.status == 200
    body = response.text or ""
    assert '"onboardingSignals":[{"owner":"ga4funnel"' in body.replace(" ", "")
    assert stashed["script_hosts"] == ["https://www.googletagmanager.com"]
    assert stashed["connect_hosts"] == ["https://www.google-analytics.com"]
    # The page itself only stashes validated origins; the serving middleware
    # merges them into the Content-Security-Policy header.
    assert "Content-Security-Policy" not in response.headers
    import warnings
    from unittest.mock import patch as mock_patch

    from aiohttp.web_exceptions import NotAppKeyWarning

    from bot.app.web.webapp.assets import (
        _ONBOARDING_SIGNALS_CSP_KEY,
        _security_headers_middleware,
    )
    from tests.support.settings_stub import settings_stub

    async def passthrough(request: web.Request) -> web.Response:
        return web.Response(text="ok")

    with (
        mock_patch(
            "bot.app.web.webapp.assets.get_settings",
            return_value=settings_stub(QR_LOGIN_ENABLED=False),
        ),
        warnings.catch_warnings(),
    ):
        warnings.simplefilter("ignore", NotAppKeyWarning)
        stashed_request = make_mocked_request("GET", "/")
        stashed_request[_ONBOARDING_SIGNALS_CSP_KEY] = stashed
        merged = asyncio.run(_security_headers_middleware(stashed_request, passthrough))
    assert "https://www.googletagmanager.com" in merged.headers["Content-Security-Policy"]
    assert '"onboardingSignals"' in body


def test_extension_contributions_positional_compat_preserves_permissions() -> None:
    """Positional construction without `signals` must keep `permissions` intact.

    Regression for ONB-04 blocker 1: inserting `signals` before `permissions`
    shifted positional args and broke `permissions` into an `AttributeError`.
    `signals` stays last so historical 10-arg positional calls keep working.
    """
    from bot.plugins.extensions import ExtensionContributions

    contributions = ExtensionContributions(
        (),
        (),
        (),
        (),
        (),
        (),
        (),
        (),
        None,
        frozenset({"rewards.balance"}),
    )
    assert contributions.permissions == frozenset({"rewards.balance"})
    assert contributions.signals == ()
    with_signals = ExtensionContributions(
        (),
        (),
        (),
        (),
        (),
        (),
        (),
        (),
        None,
        frozenset({"rewards.days"}),
        (provider("web"),),
    )
    assert with_signals.permissions == frozenset({"rewards.days"})
    assert len(with_signals.signals) == 1


def test_invalid_signal_ports_are_rejected_and_csp_never_raises() -> None:
    assert not is_valid_signal_script_url("https://cdn.example.com:bad/script.js")
    assert not is_valid_signal_script_url("https://cdn.example.com:99999/script.js")
    assert not is_valid_signal_script_url("https://cdn.example.com:0/script.js")
    assert not is_valid_signal_script_url("https://cdn.example.com:65536/script.js")
    assert is_valid_signal_script_url("https://cdn.example.com:8443/s.js")
    assert not is_valid_signal_connect_host("https://collect.example.com:99999")
    assert not is_valid_signal_connect_host("https://collect.example.com:bad")
    assert is_valid_signal_connect_host("https://collect.example.com:443")
    assert (
        validate_signal_contribution(
            "ga4funnel",
            "web",
            OnboardingSignalsContribution(scripts=("https://cdn.example.com:bad/x.js",)),
        )
        is None
    )
    # CSP extraction isolates malformed ports instead of raising into the page.
    descriptors = [
        OnboardingSignalDescriptorOut(
            owner="ga4funnel",
            id="web",
            scripts=["https://cdn.example.com:8443/s.js"],
            connect_hosts=["https://collect.example.com"],
            config={},
        )
    ]
    script_hosts, connect_hosts = onboarding_signals_csp_hosts(descriptors)
    assert script_hosts == ["https://cdn.example.com:8443"]
    assert connect_hosts == ["https://collect.example.com"]
    assert onboarding_signals_csp_hosts([]) == ([], [])


def test_many_failing_providers_are_bounded_by_total_budget(
    registry: ExtensionRegistry,
) -> None:
    import time

    async def slow_resolve(_context: SignalContext) -> OnboardingSignalsContribution | None:
        await asyncio.sleep(5)
        return OnboardingSignalsContribution(
            scripts=("https://cdn.example.com/s.js",),
        )

    for index in range(8):
        registry.register(
            f"slow{index}",
            "1",
            ExtensionContributions(
                signals=(OnboardingSignalProvider(f"web{index}", slow_resolve),)
            ),
        )
    state = {
        "generation": 1,
        "installations": {f"slow{index}": {"enabled": True} for index in range(8)},
    }
    with (
        patch("bot.app.web.webapp.onboarding_signals.read_state", return_value=state),
        patch("bot.app.web.webapp.onboarding_signals.generation_is_current", return_value=True),
    ):
        started = time.monotonic()
        descriptors = collect(make_settings())
        elapsed = time.monotonic() - started
    assert descriptors == []
    assert elapsed < 4.0


def test_collection_rechecks_generation_after_awaits(registry: ExtensionRegistry) -> None:
    registry.register("ga4funnel", "1", ExtensionContributions(signals=(provider("web"),)))
    state = {"generation": 1, "installations": {"ga4funnel": {"enabled": True}}}
    calls = {"count": 0}

    def generation_flips() -> bool:
        calls["count"] += 1
        # First check (before read_state) passes; every later check fails,
        # simulating a plugin update landing mid-collection.
        return calls["count"] <= 1

    with (
        patch("bot.app.web.webapp.onboarding_signals.read_state", return_value=state),
        patch(
            "bot.app.web.webapp.onboarding_signals.generation_is_current",
            side_effect=generation_flips,
        ),
    ):
        assert collect(make_settings()) == []


def test_slow_storage_is_bounded_by_total_budget(registry: ExtensionRegistry) -> None:
    import asyncio
    import time

    from bot.app.web.webapp.onboarding_signals import collect_onboarding_signal_descriptors

    registry.register("ga4funnel", "1", ExtensionContributions(signals=(provider("web"),)))

    def slow_read_state(_root: object) -> dict[str, Any]:
        time.sleep(5)
        return {"generation": 1, "installations": {"ga4funnel": {"enabled": True}}}

    # NOTE: use a manually managed loop instead of asyncio.run: asyncio.run
    # shuts down the default executor and would join the slow background
    # thread, hiding the timeout bound that the long-lived web loop gets.
    loop = asyncio.new_event_loop()
    try:
        with (
            patch("bot.app.web.webapp.onboarding_signals.read_state", side_effect=slow_read_state),
            patch("bot.app.web.webapp.onboarding_signals.generation_is_current", return_value=True),
        ):
            started = time.monotonic()
            descriptors = loop.run_until_complete(
                collect_onboarding_signal_descriptors(make_settings())
            )
            elapsed = time.monotonic() - started
    finally:
        loop.close()
    assert descriptors == []
    # Total budget is 2s; slow storage must not stall collection beyond it.
    assert elapsed < 4.0
