"""Installable Web App (PWA) infrastructure.

Covers the manifest identity, the single coordinated service worker scope,
the shell wiring, and the deployment contract (nginx proxying, cache policy,
push contract docs, platform limits).
"""

from __future__ import annotations

import asyncio
import io
import json
import shutil
import subprocess
import unittest
from collections.abc import Coroutine
from pathlib import Path
from typing import Any
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from PIL import Image

from bot.app.web.context import SETTINGS
from bot.app.web.openapi import generate_openapi
from bot.app.web.webapp import assets as webapp_assets
from bot.app.web.webapp import assets_branding as webapp_branding
from bot.app.web.webapp import pwa as pwa_module
from bot.app.web.webapp.routes import setup_subscription_webapp_routes
from tests.support.settings_stub import settings_stub

REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATES_DIR = REPO_ROOT / "backend" / "bot" / "app" / "web" / "templates"
TEMPLATE_PATH = _TEMPLATES_DIR / "subscription_webapp.html"
NGINX_CONF_PATH = REPO_ROOT / "deploy" / "docker" / "frontend" / "nginx.conf"
SW_SOURCE_PATH = REPO_ROOT / "backend" / "bot" / "app" / "web" / "webapp" / "pwa_service_worker.js"
WEBAPP_DOC_PATH = REPO_ROOT / "docs" / "features" / "web-app.md"


def _request(path: str, **overrides):
    app = web.Application()
    app[SETTINGS] = settings_stub(**overrides)
    return make_mocked_request("GET", path, app=app)


def _resolve(path: str) -> str | None:
    app = web.Application()
    setup_subscription_webapp_routes(app)
    for resource in app.router.resources():
        for route in resource:
            if route.method not in {"GET", "*"}:
                continue
            info = resource.get_info()
            pattern = info.get("pattern")
            if pattern is not None:
                if pattern.fullmatch(path):
                    return route.handler.__name__
            elif info.get("path") == path:
                return route.handler.__name__
    return None


class PwaSettingsTests(unittest.TestCase):
    def test_pwa_enabled_by_default(self):
        settings = settings_stub()

        self.assertTrue(settings.PWA_ENABLED)
        self.assertTrue(pwa_module.pwa_enabled(settings))

    def test_pwa_disabled_flag(self):
        settings = settings_stub(PWA_ENABLED=False)

        self.assertFalse(pwa_module.pwa_enabled(settings))

    def test_webapp_disabled_implies_pwa_disabled(self):
        settings = settings_stub(WEBAPP_ENABLED=False)

        self.assertFalse(pwa_module.pwa_enabled(settings))


class PwaManifestTests(unittest.TestCase):
    def test_manifest_identity_display_and_launch_url(self):
        response = self._get_manifest()

        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, "application/manifest+json")
        self.assertEqual(response.headers["Cache-Control"], "no-cache")
        payload = json.loads(response.text)
        self.assertEqual(payload["name"], "/minishop")
        self.assertEqual(payload["short_name"], "/minishop")
        self.assertEqual(payload["id"], "/")
        self.assertEqual(payload["start_url"], "/")
        self.assertEqual(payload["scope"], "/")
        self.assertEqual(payload["display"], "standalone")
        self.assertEqual(payload["lang"], "ru")
        self.assertEqual(payload["background_color"], "#03070b")
        self.assertEqual(payload["theme_color"], "#00fe7a")
        icons = payload["icons"]
        by_size = {icon["sizes"]: icon for icon in icons}
        self.assertEqual(by_size["192x192"]["src"], "/icon-192.png")
        self.assertEqual(by_size["512x512"]["src"], "/icon-512.png")
        self.assertEqual(by_size["192x192"]["type"], "image/png")

    def test_manifest_uses_branding_title_and_primary_color(self):
        request = _request(
            "/manifest.webmanifest",
            WEBAPP_TITLE="My Shop",
            WEBAPP_PRIMARY_COLOR="#123456",
        )

        payload = json.loads(self._run(pwa_module.pwa_manifest_route(request)).text)

        self.assertEqual(payload["name"], "My Shop")
        self.assertEqual(payload["short_name"], "My Shop")
        self.assertEqual(payload["theme_color"], "#123456")

    def test_manifest_short_name_truncates_long_titles(self):
        payload = pwa_module.build_pwa_manifest(settings_stub(WEBAPP_TITLE="A" * 40))

        self.assertEqual(payload["name"], "A" * 40)
        self.assertLessEqual(len(payload["short_name"]), 12)

    def test_manifest_carries_no_tokens_or_secrets(self):
        response = self._get_manifest()
        lowered = response.text.lower()

        for secret in ("token", "secret", "bearer", "cookie", "password", "api_key", "apikey"):
            self.assertNotIn(secret, lowered)

    def test_manifest_disabled_returns_404(self):
        for overrides in ({"PWA_ENABLED": False}, {"WEBAPP_ENABLED": False}):
            with self.subTest(overrides=overrides):
                request = _request("/manifest.webmanifest", **overrides)
                with self.assertRaises(web.HTTPNotFound):
                    self._run(pwa_module.pwa_manifest_route(request))

    def _get_manifest(self) -> web.Response:
        return self._run(pwa_module.pwa_manifest_route(_request("/manifest.webmanifest")))

    @staticmethod
    def _run(coro: Coroutine[Any, Any, web.Response]) -> web.Response:
        return asyncio.run(coro)


class PwaServiceWorkerTests(unittest.TestCase):
    def test_worker_headers_scope_mime_and_cache_policy(self):
        with patch.object(webapp_assets, "_resolve_app_version", return_value="9.9.9-test"):
            request = _request("/sw.js")
            response = self._run(pwa_module.pwa_service_worker_route(request))

        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, "application/javascript")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(
            response.headers["Service-Worker-Allowed"], pwa_module.PWA_SERVICE_WORKER_SCOPE
        )
        self.assertNotIn(pwa_module.PWA_CACHE_NAME_PLACEHOLDER, response.text)
        self.assertNotIn(pwa_module.PWA_CACHE_NAME_PREFIX_PLACEHOLDER, response.text)
        self.assertIn("minishop-pwa-v9.9.9-test", response.text)
        self.assertIn("minishop-pwa-v", response.text)

    def test_worker_update_is_coordinated_not_forced(self):
        source = SW_SOURCE_PATH.read_text(encoding="utf-8")

        self.assertIn('addEventListener("message"', source)
        self.assertIn("SKIP_WAITING", source)
        self.assertIn("isCoreOwnedCache", source)
        self.assertIn("startsWith(CACHE_NAME_PREFIX)", source)
        self.assertIn("self.clients.claim()", source)

    def test_worker_activation_purges_only_core_owned_caches(self):
        source = SW_SOURCE_PATH.read_text(encoding="utf-8")

        self.assertIn("key !== CACHE_NAME", source)
        self.assertNotIn("keys.map((key) => (key === CACHE_NAME", source)

    def test_worker_never_caches_private_or_dynamic_responses(self):
        source = SW_SOURCE_PATH.read_text(encoding="utf-8")

        # Navigations (HTML shells, /s/<token> share pages, auth) bypass respondWith.
        self.assertIn('request.mode === "navigate"', source)
        # Only content-addressed immutable assets are cacheable; /api, /auth,
        # /s and the shell never match.
        for pattern in (
            "HASHED_ENTRY_RE",
            "CHUNK_RE",
            "FONT_RE",
            "PROVIDER_LOGO_RE",
            "DIGEST_FAVICON_RE",
            "UPLOADED_LOGO_RE",
        ):
            self.assertIn(pattern, source)
        self.assertIn("isCacheableAsset(url.pathname)", source)
        self.assertIn('response.type === "basic"', source)

    def test_worker_falls_back_to_network_when_storage_fails(self):
        source = SW_SOURCE_PATH.read_text(encoding="utf-8")

        self.assertIn(".catch(() => null)", source)
        self.assertIn(".catch(() => undefined)", source)
        # Storage failure must not reject the asset request: the cache-open
        # miss path serves the network directly.
        self.assertIn("if (!cache) {", source)
        self.assertIn("return fetch(request);", source)

    def test_worker_implements_push_and_click_contract(self):
        source = SW_SOURCE_PATH.read_text(encoding="utf-8")

        self.assertIn('addEventListener("push"', source)
        self.assertIn('addEventListener("notificationclick"', source)
        self.assertIn("showNotification", source)
        self.assertIn("sanitizePushUrl", source)
        self.assertIn("parsed.origin !== self.location.origin", source)
        self.assertIn("self.clients.openWindow", source)

    def test_worker_disabled_serves_retiring_worker(self):
        for overrides in ({"PWA_ENABLED": False}, {"WEBAPP_ENABLED": False}):
            with self.subTest(overrides=overrides):
                request = _request("/sw.js", **overrides)
                response = self._run(pwa_module.pwa_service_worker_route(request))

                self.assertEqual(response.status, 200)
                self.assertEqual(response.content_type, "application/javascript")
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertEqual(
                    response.headers["Service-Worker-Allowed"],
                    pwa_module.PWA_SERVICE_WORKER_SCOPE,
                )
                # The retiring worker unregisters the core registration and
                # clears only the core-owned cache namespace.
                self.assertIn("self.registration.unregister()", response.text)
                self.assertIn(pwa_module.PWA_CACHE_NAME_PREFIX, response.text)
                self.assertNotIn("showNotification", response.text)
                self.assertNotIn("respondWith", response.text)
                self.assertNotIn(pwa_module.PWA_CACHE_NAME_PREFIX_PLACEHOLDER, response.text)

    def test_retirement_worker_source_stays_minimal(self):
        source = (
            REPO_ROOT / "backend" / "bot" / "app" / "web" / "webapp" / "pwa_retirement_worker.js"
        ).read_text(encoding="utf-8")

        self.assertIn("self.skipWaiting()", source)
        self.assertIn("self.registration.unregister()", source)
        self.assertIn("startsWith(CACHE_NAME_PREFIX)", source)
        self.assertNotIn("showNotification", source)
        self.assertNotIn("respondWith", source)
        self.assertNotIn('addEventListener("fetch"', source)
        self.assertNotIn('addEventListener("push"', source)

    @staticmethod
    def _run(coro: Coroutine[Any, Any, web.Response]) -> web.Response:
        return asyncio.run(coro)


class PwaShellTests(unittest.TestCase):
    def test_template_keeps_pwa_placeholders_and_capability_metas(self):
        template = TEMPLATE_PATH.read_text(encoding="utf-8")

        self.assertIn("<!-- WEBAPP_PWA_HEAD -->", template)
        self.assertIn("<!-- WEBAPP_PWA_SCRIPT -->", template)
        self.assertIn('name="mobile-web-app-capable"', template)
        self.assertIn('name="apple-mobile-web-app-capable"', template)

    def test_enabled_shell_injects_manifest_link_and_registration(self):
        template = TEMPLATE_PATH.read_text(encoding="utf-8")

        html = pwa_module.apply_pwa_shell_markup(template, enabled=True, nonce="test-nonce")

        self.assertIn('rel="manifest" href="/manifest.webmanifest"', html)
        self.assertIn('navigator.serviceWorker.register("/sw.js", { scope: "/" })', html)
        self.assertIn('nonce="test-nonce"', html)
        self.assertNotIn("WEBAPP_PWA_HEAD", html)
        self.assertNotIn("WEBAPP_PWA_SCRIPT", html)

    def test_enabled_registration_coordinates_updates(self):
        script = pwa_module.pwa_registration_script("test-nonce")

        # Waiting workers only activate on the page's signal; the page reloads
        # once when the new worker takes control.
        self.assertIn("SKIP_WAITING", script)
        self.assertIn("updatefound", script)
        self.assertIn("controllerchange", script)
        self.assertIn("window.location.reload()", script)
        self.assertIn('navigator.serviceWorker.register("/sw.js", { scope: "/" })', script)

    def test_disabled_shell_stays_usable_without_pwa(self):
        template = TEMPLATE_PATH.read_text(encoding="utf-8")

        html = pwa_module.apply_pwa_shell_markup(template, enabled=False)

        self.assertNotIn("manifest.webmanifest", html)
        self.assertNotIn("serviceWorker", html)
        self.assertNotIn("WEBAPP_PWA_HEAD", html)
        self.assertNotIn("WEBAPP_PWA_SCRIPT", html)
        self.assertIn('src="/subscription_webapp.js"', html)

    def test_registration_survives_static_fallback_preparation(self):
        prepare_script = (
            REPO_ROOT / "frontend" / "scripts" / "prepare_nginx_assets.mjs"
        ).read_text(encoding="utf-8")

        self.assertIn("WEBAPP_PWA_HEAD", prepare_script)
        self.assertIn("WEBAPP_PWA_SCRIPT", prepare_script)
        self.assertIn("/manifest.webmanifest", prepare_script)
        self.assertIn("/sw.js", prepare_script)
        # The static fallback shell must coordinate updates exactly like the
        # backend-served registration script.
        self.assertIn("SKIP_WAITING", prepare_script)
        self.assertIn("controllerchange", prepare_script)
        self.assertIn("window.location.reload()", prepare_script)


class PwaRoutingTests(unittest.TestCase):
    def test_manifest_and_worker_have_dedicated_handlers(self):
        self.assertEqual(_resolve("/manifest.webmanifest"), "pwa_manifest_route")
        self.assertEqual(_resolve("/sw.js"), "pwa_service_worker_route")

    def test_manifest_and_worker_are_not_information_pages(self):
        from bot.app.web.webapp.pages import information_page_route

        self.assertIsNot(_resolve("/manifest.webmanifest"), information_page_route.__name__)
        self.assertIsNot(_resolve("/sw.js"), information_page_route.__name__)

    def test_edge_token_covers_manifest_and_worker(self):
        self.assertTrue(webapp_assets._webapp_edge_token_required("/manifest.webmanifest"))
        self.assertTrue(webapp_assets._webapp_edge_token_required("/sw.js"))

    def test_nginx_proxies_manifest_and_worker_to_backend(self):
        nginx_conf = NGINX_CONF_PATH.read_text(encoding="utf-8")

        for path in ("/manifest.webmanifest", "/sw.js"):
            marker = f"location = {path} {{"
            self.assertIn(marker, nginx_conf)
            start = nginx_conf.index(marker)
            block = nginx_conf[start : nginx_conf.index("\n    }", start)]
            self.assertIn("proxy_pass ${WEBAPP_BACKEND_UPSTREAM};", block)
            self.assertIn("${MINISHOP_EDGE_TOKEN_HEADER_VALUE}", block)

    def test_openapi_keeps_manifest_and_worker_out_of_json_api(self):
        document = generate_openapi()
        paths = document["paths"]

        self.assertNotIn("/manifest.webmanifest", paths)
        self.assertNotIn("/sw.js", paths)


class PwaDocsTests(unittest.TestCase):
    def test_webapp_docs_cover_pwa_operations(self):
        docs = WEBAPP_DOC_PATH.read_text(encoding="utf-8").lower()

        for topic in (
            "pwa",
            "/manifest.webmanifest",
            "/sw.js",
            "scope",
            "push",
            "notificationclick",
            "pwa_enabled",
            "no-store",
            "skip_waiting",
            "controllerchange",
            "minishop-pwa-v",
            "unregister",
            "ios",
            "telegram",
        ):
            self.assertIn(topic, docs)

    def test_webapp_docs_scope_retirement_to_webapp_enabled(self):
        docs = WEBAPP_DOC_PATH.read_text(encoding="utf-8")

        self.assertIn("`PWA_ENABLED=False` при включённом `WEBAPP_ENABLED=True`", docs)
        self.assertIn("после повторного включения WebApp", docs)
        self.assertNotIn("`PWA_ENABLED=False` (или `WEBAPP_ENABLED=False`)", docs)


_NODE = shutil.which("node")
_HARNESS_PATH = REPO_ROOT / "tests" / "support" / "pwa_sw_harness.mjs"
_WORKER_ORIGIN = "https://app.example.com"


def _run_worker_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    assert _NODE, "node is required for the service worker vm harness"
    proc = subprocess.run(
        [_NODE, str(_HARNESS_PATH)],
        input=json.dumps(scenario),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    outcome: dict[str, Any] = json.loads(proc.stdout)
    return outcome


def _worker_scenario(
    sources: dict[str, str],
    steps: list[dict[str, Any]],
    *,
    initial_realm: str = "v1",
    caches: dict[str, dict[str, Any]] | None = None,
    network: dict[str, dict[str, Any]] | None = None,
    clients: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    scenario: dict[str, Any] = {
        "origin": _WORKER_ORIGIN,
        "sources": sources,
        "initialRealm": initial_realm,
        "caches": caches or {},
        "network": network or {},
        "steps": steps,
    }
    if clients is not None:
        scenario["clients"] = clients
    return _run_worker_scenario(scenario)


def _absolute(path: str) -> str:
    return f"{_WORKER_ORIGIN}{path}"


_PAGE_HARNESS_PATH = REPO_ROOT / "tests" / "support" / "pwa_page_harness.mjs"
_PREPARE_NGINX_ASSETS_PATH = REPO_ROOT / "frontend" / "scripts" / "prepare_nginx_assets.mjs"


def _inner_page_js(html_snippet: str) -> str:
    start = html_snippet.index(">", html_snippet.index("<script"))
    end = html_snippet.rindex("</script>")
    return html_snippet[start + 1 : end]


def _backend_page_script() -> str:
    return _inner_page_js(pwa_module.pwa_registration_script("test-nonce"))


def _frontend_page_script() -> str:
    import re

    source = _PREPARE_NGINX_ASSETS_PATH.read_text(encoding="utf-8")
    marker = "const pwaScript = ["
    start = source.index(marker)
    end = source.index('].join("\\n")', start)
    block = source[start + len(marker) : end]
    lines: list[str] = []
    for raw_line in block.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        match = re.fullmatch(r"""(['"])(.*)\1,?""", stripped)
        assert match is not None, f"unexpected pwaScript line: {raw_line!r}"
        lines.append(match.group(2))
    snippet = "\n".join(lines)
    return _inner_page_js(snippet)


def _run_page_scenario(script: str, initial_controller: Any | None = None) -> dict[str, Any]:
    assert _NODE, "node is required for the page registration harness"
    scenario: dict[str, Any] = {"script": script, "initialController": initial_controller}
    proc = subprocess.run(
        [_NODE, str(_PAGE_HARNESS_PATH)],
        input=json.dumps(scenario),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    outcome: dict[str, Any] = json.loads(proc.stdout)
    return outcome


@unittest.skipUnless(_NODE, "node is required for the page registration harness")
class PwaPageRegistrationTests(unittest.TestCase):
    def test_scripts_track_controller_acquisition_dynamically(self):
        for script, variant in (
            (_backend_page_script(), "backend"),
            (_frontend_page_script(), "frontend"),
        ):
            with self.subTest(variant=variant):
                self.assertIn("let pwaHadController", script)
                self.assertIn(
                    "pwaHadController = Boolean(navigator.serviceWorker.controller);",
                    script,
                )

    def test_first_install_primes_flag_and_update_reloads_backend(self):
        outcome = _run_page_scenario(_backend_page_script(), None)

        self.assertFalse(outcome["hadControllerAtLoad"])
        self.assertEqual(outcome["reloadsAfterFirstActivation"], 0)
        self.assertEqual(outcome["skipWaitingMessages"], 1)
        self.assertEqual(outcome["reloads"], 1)

    def test_first_install_primes_flag_and_update_reloads_frontend(self):
        outcome = _run_page_scenario(_frontend_page_script(), None)

        self.assertFalse(outcome["hadControllerAtLoad"])
        self.assertEqual(outcome["reloadsAfterFirstActivation"], 0)
        self.assertEqual(outcome["skipWaitingMessages"], 1)
        self.assertEqual(outcome["reloads"], 1)

    def test_controlled_tab_reloads_on_update(self):
        for script, variant in (
            (_backend_page_script(), "backend"),
            (_frontend_page_script(), "frontend"),
        ):
            with self.subTest(variant=variant):
                outcome = _run_page_scenario(script, {"scriptURL": "/sw.js?v1"})

                self.assertTrue(outcome["hadControllerAtLoad"])
                self.assertEqual(outcome["skipWaitingMessages"], 1)
                self.assertEqual(outcome["reloads"], 1)


@unittest.skipUnless(_NODE, "node is required for the service worker vm harness")
class PwaWorkerBehaviorTests(unittest.TestCase):
    def test_install_does_not_force_takeover(self):
        outcome = _worker_scenario(
            {"v1": pwa_module.render_pwa_service_worker("1.0.0")},
            [{"run": "install", "realm": 0}],
        )

        self.assertEqual(outcome["realms"][0]["skipWaitingCalls"], 0)
        self.assertEqual(outcome["realms"][0]["claimCalls"], 0)

    def test_activate_purges_only_core_owned_caches(self):
        current = pwa_module.pwa_cache_name("9.9.9-test")
        outcome = _worker_scenario(
            {"v1": pwa_module.render_pwa_service_worker("9.9.9-test")},
            [
                {"run": "install", "realm": 0},
                {"run": "message", "realm": 0, "data": {"type": "SKIP_WAITING"}},
                {"run": "activate", "realm": 0},
            ],
            caches={
                current: {_absolute("/x.js"): {"status": 200, "body": "current"}},
                "minishop-pwa-v8.0.0": {_absolute("/old.js"): {"status": 200, "body": "old"}},
                "plugin-scope-cache": {_absolute("/p.js"): {"status": 200, "body": "plugin"}},
            },
        )

        realm = outcome["realms"][0]
        self.assertEqual(realm["skipWaitingCalls"], 1)
        self.assertEqual(realm["claimCalls"], 1)
        self.assertEqual(outcome["deletedCaches"], ["minishop-pwa-v8.0.0"])
        self.assertEqual(set(outcome["caches"]), {current, "plugin-scope-cache"})

    def test_update_keeps_old_tab_served_until_reload(self):
        chunk_v1 = "/subscription_webapp.lazy.abc12345.js"
        chunk_v2 = "/subscription_webapp.lazy.def67890.js"
        outcome = _worker_scenario(
            {
                "v1": pwa_module.render_pwa_service_worker("1.0.0"),
                "v2": pwa_module.render_pwa_service_worker("2.0.0"),
            },
            [
                {"run": "install", "realm": 0},
                {"run": "activate", "realm": 0},
                {"run": "fetch", "realm": 0, "url": chunk_v1},
                {"run": "reload", "source": "v2"},
                {"run": "install", "realm": 1},
                # The open tab is still controlled by the previous worker: its
                # lazy chunk must keep loading while the update is pending.
                {"run": "fetch", "realm": 0, "url": chunk_v1},
                {"run": "message", "realm": 1, "data": {"type": "SKIP_WAITING"}},
                {"run": "activate", "realm": 1},
            ],
            network={
                _absolute(chunk_v1): {"status": 200, "body": "v1-chunk-body"},
                _absolute(chunk_v2): {"status": 200, "body": "v2-chunk-body"},
            },
        )

        old_cache = pwa_module.pwa_cache_name("1.0.0")
        self.assertEqual(outcome["realms"][1]["skipWaitingCalls"], 1)
        self.assertEqual(outcome["realms"][1]["claimCalls"], 1)
        # The pending update neither force-activated nor purged the old cache.
        self.assertIn(old_cache, outcome["deletedCaches"])
        pending_load = outcome["stepResults"][5]
        self.assertTrue(pending_load["responded"])
        self.assertEqual(pending_load["status"], 200)
        self.assertEqual(pending_load["body"], "v1-chunk-body")
        self.assertTrue(pending_load["cacheHit"])
        chunk_fetches = [
            call for call in outcome["fetchCalls"] if call["url"] == _absolute(chunk_v1)
        ]
        self.assertEqual(len(chunk_fetches), 1)

    def test_fetch_bypasses_private_and_dynamic_routes(self):
        chunk = "/subscription_webapp.min.abcdef12.js"
        bypass_urls = [
            "/api/me",
            "/api/subscription-guides/public/ffffffffffffffffffffffffffffffff",
            "/auth/telegram/start",
            "/s/ffffffffffffffffffffffffffffffff",
            "/install",
            "/",
        ]
        steps: list[dict[str, Any]] = [
            {"run": "fetch", "realm": 0, "url": url} for url in bypass_urls
        ]
        steps.append({"run": "fetch", "realm": 0, "url": "/", "mode": "navigate"})
        steps.append({"run": "fetch", "realm": 0, "url": chunk, "method": "POST"})
        steps.append({"run": "fetch", "realm": 0, "url": chunk})
        steps.append({"run": "fetch", "realm": 0, "url": chunk})
        outcome = _worker_scenario(
            {"v1": pwa_module.render_pwa_service_worker("3.0.0")},
            steps,
            network={_absolute(chunk): {"status": 200, "body": "chunk-body"}},
        )

        for result in outcome["stepResults"][: len(bypass_urls) + 2]:
            self.assertFalse(result["responded"], result)
        chunk_results = outcome["stepResults"][len(bypass_urls) + 2 :]
        self.assertEqual(len(chunk_results), 2)
        self.assertFalse(chunk_results[0]["cacheHit"])
        self.assertEqual(chunk_results[0]["body"], "chunk-body")
        self.assertTrue(chunk_results[1]["cacheHit"])

    def test_fetch_falls_back_to_network_when_storage_fails(self):
        chunk = "/subscription_webapp.min.abcdef12.js"
        outcome = _worker_scenario(
            {"v1": pwa_module.render_pwa_service_worker("4.0.0")},
            [
                {"run": "setFail", "open": True, "match": False},
                {"run": "fetch", "realm": 0, "url": chunk},
                {"run": "setFail", "open": False, "match": True},
                {"run": "fetch", "realm": 0, "url": chunk},
                {"run": "setFail", "open": False, "match": False},
                {"run": "fetch", "realm": 0, "url": chunk},
            ],
            network={_absolute(chunk): {"status": 200, "body": "network-body"}},
        )

        for result in (outcome["stepResults"][1], outcome["stepResults"][3]):
            self.assertTrue(result["responded"], result)
            self.assertEqual(result["status"], 200)
            self.assertEqual(result["body"], "network-body")
            self.assertFalse(result["cacheHit"])

    def test_fetch_rejects_only_when_network_is_down(self):
        chunk = "/subscription_webapp.min.abcdef12.js"
        outcome = _worker_scenario(
            {"v1": pwa_module.render_pwa_service_worker("4.1.0")},
            [{"run": "fetch", "realm": 0, "url": chunk}],
            network={_absolute(chunk): {"error": "offline"}},
        )

        result = outcome["stepResults"][0]
        self.assertTrue(result["responded"])
        self.assertIn("rejected", result)

    def test_retirement_worker_unregisters_and_clears_only_core_namespace(self):
        outcome = _worker_scenario(
            {"ret": pwa_module.render_pwa_retirement_worker()},
            [
                {"run": "install", "realm": 0},
                {"run": "activate", "realm": 0},
            ],
            initial_realm="ret",
            caches={
                "minishop-pwa-v9.9.9": {_absolute("/a.js"): {"status": 200, "body": "a"}},
                "minishop-pwa-v8.0.0": {_absolute("/b.js"): {"status": 200, "body": "b"}},
                "plugin-scope-cache": {_absolute("/p.js"): {"status": 200, "body": "p"}},
            },
        )

        realm = outcome["realms"][0]
        self.assertEqual(realm["listeners"], ["install", "activate"])
        self.assertEqual(realm["skipWaitingCalls"], 1)
        self.assertEqual(realm["unregisterCalls"], 1)
        self.assertEqual(realm["claimCalls"], 0)
        self.assertEqual(
            set(outcome["deletedCaches"]), {"minishop-pwa-v9.9.9", "minishop-pwa-v8.0.0"}
        )
        self.assertEqual(set(outcome["caches"]), {"plugin-scope-cache"})

    def test_push_contract_accepts_only_same_origin_urls(self):
        outcome = _worker_scenario(
            {"v1": pwa_module.render_pwa_service_worker("5.0.0")},
            [
                {
                    "run": "push",
                    "realm": 0,
                    "data": {"title": "  Hello  ", "body": "B", "url": "/install"},
                },
                {
                    "run": "push",
                    "realm": 0,
                    "data": {"title": "Hi", "url": "https://evil.example/x"},
                },
                {"run": "push", "realm": 0, "data": {"body": "missing title"}},
                {"run": "push", "realm": 0, "data": None, "dataError": True},
            ],
        )

        notifications = outcome["realms"][0]["notifications"]
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0]["title"], "Hello")
        self.assertEqual(notifications[0]["options"]["icon"], "/icon-192.png")
        self.assertEqual(notifications[0]["options"]["data"], {"url": "/install"})

    def test_notificationclick_focuses_or_opens_same_origin_target(self):
        install_url = _absolute("/install")
        outcome = _worker_scenario(
            {"v1": pwa_module.render_pwa_service_worker("5.1.0")},
            [
                {"run": "notificationclick", "realm": 0, "url": "/install"},
                {"run": "notificationclick", "realm": 0, "url": "/missing"},
            ],
            clients=[{"url": install_url}, {"url": _absolute("/other")}],
        )

        realm = outcome["realms"][0]
        self.assertEqual(realm["focusedClients"], [install_url])
        self.assertEqual(realm["openedWindows"], ["/missing"])


class PwaIconFallbackTests(unittest.TestCase):
    def test_default_branding_serves_sized_pngs(self):
        for path, size in (
            ("/icon-192.png", (192, 192)),
            ("/icon-512.png", (512, 512)),
            ("/apple-touch-icon.png", (180, 180)),
        ):
            with self.subTest(path=path):
                response = self._get_icon(path)

                self.assertEqual(response.status, 200)
                self.assertEqual(response.content_type, "image/png")
                self.assertEqual(response.headers["Cache-Control"], "no-cache")
                with Image.open(io.BytesIO(response.body)) as image:
                    self.assertEqual(image.size, size)

    def test_custom_ico_favicon_falls_back_to_bundled_png(self):
        overrides: dict[str, Any] = {
            "WEBAPP_FAVICON_USE_CUSTOM": True,
            "WEBAPP_FAVICON_URL": "/custom-brand.ico",
        }
        for path, size in (("/icon-192.png", (192, 192)), ("/icon-512.png", (512, 512))):
            with self.subTest(path=path):
                response = self._get_icon(path, **overrides)

                self.assertEqual(response.status, 200)
                self.assertEqual(response.content_type, "image/png")
                with Image.open(io.BytesIO(response.body)) as image:
                    self.assertEqual(image.size, size)

    def test_external_svg_logo_falls_back_to_bundled_png(self):
        response = self._get_icon(
            "/icon-192.png", WEBAPP_LOGO_URL="https://cdn.example/brand/logo.svg"
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, "image/png")
        with Image.open(io.BytesIO(response.body)) as image:
            self.assertEqual(image.size, (192, 192))

    def test_generated_digest_branding_serves_generated_png(self):
        response = self._get_icon(
            "/icon-192.png",
            WEBAPP_FAVICON_USE_CUSTOM=True,
            WEBAPP_FAVICON_URL="/webapp-favicon/19b2a242e5b7bc2d/icon-180.png",
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, "image/png")
        with Image.open(io.BytesIO(response.body)) as image:
            self.assertEqual(image.size, (192, 192))

    def test_manifest_icons_resolve_for_every_supported_branding(self):
        brandings = (
            {},
            {
                "WEBAPP_FAVICON_USE_CUSTOM": True,
                "WEBAPP_FAVICON_URL": "/custom-brand.ico",
            },
            {"WEBAPP_LOGO_URL": "https://cdn.example/brand/logo.svg"},
            {
                "WEBAPP_FAVICON_USE_CUSTOM": True,
                "WEBAPP_FAVICON_URL": "/webapp-favicon/19b2a242e5b7bc2d/icon-180.png",
            },
        )
        for overrides in brandings:
            with self.subTest(overrides=overrides):
                manifest = pwa_module.build_pwa_manifest(settings_stub(**overrides))
                self.assertTrue(manifest["icons"])

                for icon in manifest["icons"]:
                    response = self._get_icon(icon["src"], **overrides)

                    self.assertEqual(response.status, 200, icon["src"])
                    self.assertEqual(response.content_type, "image/png")

    def _get_icon(self, path: str, **overrides: Any) -> web.Response:
        request = _request(path, **overrides)
        response: web.Response = asyncio.run(webapp_branding.webapp_current_favicon_route(request))
        return response
