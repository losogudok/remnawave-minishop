"""Installable Web App (PWA) infrastructure.

Serves the Web App manifest and the single coordinated service worker for the
``/`` scope, and injects the install wiring (manifest link + worker
registration) into the Mini App shell. While PWA is disabled, ``/sw.js`` serves
a retiring worker that unregisters already-installed clients and clears only
the core-owned caches. The browser and Telegram Mini App keep working with PWA
disabled or without push: registration is feature-detected and best-effort,
and nothing in the shell depends on the worker.

Ownership: core owns ``/sw.js`` over scope ``/``. Plugins must not register
competing workers over ``/``; narrowing, disabling, and removal are documented
in ``docs/features/web-app.md`` (PWA section).
"""

from __future__ import annotations

import json
import re
from typing import Any

from aiohttp import web

from bot.app.web.context import get_settings
from config.settings import Settings

from .asset_paths import PWA_RETIREMENT_WORKER_SOURCE_PATH, PWA_SERVICE_WORKER_SOURCE_PATH
from .assets_static import _read_template_text_cached
from .common import _normalize_language
from .constants import WEBAPP_PWA_HEAD_PLACEHOLDER, WEBAPP_PWA_SCRIPT_PLACEHOLDER

PWA_MANIFEST_CACHE_CONTROL = "no-cache"
PWA_SERVICE_WORKER_CACHE_CONTROL = "no-store"
PWA_SERVICE_WORKER_SCOPE = "/"
PWA_START_URL = "/"
PWA_DISPLAY = "standalone"
PWA_BACKGROUND_COLOR = "#03070b"
PWA_FALLBACK_NAME = "Subscription"
PWA_FALLBACK_PRIMARY_COLOR = "#00fe7a"
PWA_SHORT_NAME_MAX_LENGTH = 12
PWA_CACHE_NAME_PREFIX = "minishop-pwa-v"
PWA_CACHE_NAME_PLACEHOLDER = "__PWA_CACHE_NAME__"
PWA_CACHE_NAME_PREFIX_PLACEHOLDER = "__PWA_CACHE_NAME_PREFIX__"
_PWA_CACHE_NAME_SAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9._-]+")


def pwa_enabled(settings: Settings) -> bool:
    return bool(settings.WEBAPP_ENABLED and settings.PWA_ENABLED)


def _sanitize_cache_name_version(raw_version: str) -> str:
    cleaned = _PWA_CACHE_NAME_SAFE_CHARS_RE.sub("-", str(raw_version or "").strip())
    return cleaned.strip("-") or "unknown"


def pwa_cache_name(app_version: str) -> str:
    return f"{PWA_CACHE_NAME_PREFIX}{_sanitize_cache_name_version(app_version)}"


def _pwa_display_name(settings: Settings) -> str:
    return str(settings.WEBAPP_TITLE or "").strip() or PWA_FALLBACK_NAME


def _pwa_short_name(display_name: str) -> str:
    if len(display_name) <= PWA_SHORT_NAME_MAX_LENGTH:
        return display_name
    return display_name[:PWA_SHORT_NAME_MAX_LENGTH].rstrip() or display_name[:1]


def _pwa_theme_color(settings: Settings) -> str:
    return str(settings.WEBAPP_PRIMARY_COLOR or "").strip() or PWA_FALLBACK_PRIMARY_COLOR


def build_pwa_manifest(settings: Settings) -> dict[str, Any]:
    """Build the install identity. Public branding only: no tokens or secrets."""

    display_name = _pwa_display_name(settings)
    theme_color = _pwa_theme_color(settings)
    return {
        "name": display_name,
        "short_name": _pwa_short_name(display_name),
        "id": PWA_START_URL,
        "start_url": PWA_START_URL,
        "scope": PWA_SERVICE_WORKER_SCOPE,
        "display": PWA_DISPLAY,
        "lang": _normalize_language(settings.DEFAULT_LANGUAGE),
        "background_color": PWA_BACKGROUND_COLOR,
        "theme_color": theme_color,
        "icons": [
            {
                "src": "/icon-192.png",
                "sizes": "192x192",
                "type": "image/png",
            },
            {
                "src": "/icon-512.png",
                "sizes": "512x512",
                "type": "image/png",
            },
            {
                "src": "/apple-touch-icon.png",
                "sizes": "180x180",
                "type": "image/png",
            },
        ],
    }


def render_pwa_service_worker(app_version: str) -> str:
    source = _read_template_text_cached(PWA_SERVICE_WORKER_SOURCE_PATH)
    return source.replace(PWA_CACHE_NAME_PLACEHOLDER, pwa_cache_name(app_version)).replace(
        PWA_CACHE_NAME_PREFIX_PLACEHOLDER, PWA_CACHE_NAME_PREFIX
    )


def render_pwa_retirement_worker() -> str:
    """Retirement worker served at /sw.js while PWA is disabled.

    It unregisters the core-owned registration and deletes only the
    core-owned caches, so already-installed clients stop intercepting fetches
    and push without touching narrower-scope plugin caches.
    """

    source = _read_template_text_cached(PWA_RETIREMENT_WORKER_SOURCE_PATH)
    return source.replace(PWA_CACHE_NAME_PREFIX_PLACEHOLDER, PWA_CACHE_NAME_PREFIX)


def pwa_head_markup() -> str:
    return '<link rel="manifest" href="/manifest.webmanifest" id="app-manifest" />'


def pwa_registration_script(nonce: str) -> str:
    # Coordinated updates: a new worker installs into "waiting" and never
    # force-activates. The page opts in via SKIP_WAITING and reloads once when
    # the new worker takes control, so an open tab is never stranded between
    # asset generations. The first activation only primes the controller flag
    # (the shell is already current), so a later update still reloads.
    return (
        '<script type="module" nonce="'
        + str(nonce or "")
        + '">\nif ("serviceWorker" in navigator) {\n'
        "  let pwaHadController = Boolean(navigator.serviceWorker.controller);\n"
        "  let pwaReactivated = false;\n"
        "  const pwaActivateWaiting = (registration) => {\n"
        "    const waiting = registration && registration.waiting;\n"
        "    if (waiting) {\n"
        '      try { waiting.postMessage({ type: "SKIP_WAITING" }); } catch (err) {}\n'
        "    }\n"
        "  };\n"
        '  navigator.serviceWorker.addEventListener("controllerchange", () => {\n'
        "    if (pwaHadController && !pwaReactivated) {\n"
        "      pwaReactivated = true;\n"
        "      window.location.reload();\n"
        "    }\n"
        "    pwaHadController = Boolean(navigator.serviceWorker.controller);\n"
        "  });\n"
        '  window.addEventListener("load", () => {\n'
        '    navigator.serviceWorker.register("/sw.js", { scope: "/" })\n'
        "      .then((registration) => {\n"
        "        pwaActivateWaiting(registration);\n"
        '        registration.addEventListener("updatefound", () => {\n'
        "          const installing = registration.installing;\n"
        "          if (installing) {\n"
        '            installing.addEventListener("statechange", () => {\n'
        '              if (installing.state === "installed"\n'
        "                && navigator.serviceWorker.controller) {\n"
        "                pwaActivateWaiting(registration);\n"
        "              }\n"
        "            });\n"
        "          }\n"
        "        });\n"
        "      })\n"
        "      .catch(() => {});\n"
        "  });\n"
        "}\n</script>"
    )


def apply_pwa_shell_markup(html: str, *, enabled: bool, nonce: str = "") -> str:
    """Inject (or strip) the PWA head link and worker registration script."""

    head_markup = pwa_head_markup() if enabled else ""
    script_markup = pwa_registration_script(nonce) if enabled else ""
    html = html.replace(WEBAPP_PWA_HEAD_PLACEHOLDER, head_markup, 1)
    return html.replace(WEBAPP_PWA_SCRIPT_PLACEHOLDER, script_markup, 1)


def _pwa_not_found() -> web.HTTPNotFound:
    return web.HTTPNotFound(text="pwa_disabled")


async def pwa_manifest_route(request: web.Request) -> web.Response:
    settings: Settings = get_settings(request)
    if not pwa_enabled(settings):
        raise _pwa_not_found()

    body = json.dumps(
        build_pwa_manifest(settings),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    response = web.Response(text=body, content_type="application/manifest+json")
    response.headers["Cache-Control"] = PWA_MANIFEST_CACHE_CONTROL
    return response


async def pwa_service_worker_route(request: web.Request) -> web.Response:
    settings: Settings = get_settings(request)
    if not pwa_enabled(settings):
        # Retire instead of 404: a 404 would leave an already-installed worker
        # intercepting fetches and push forever. Browsers revalidate /sw.js on
        # navigation (no-store), so serving the retiring worker here propagates
        # the removal to installed clients without user action.
        body = render_pwa_retirement_worker()
    else:
        from .assets import _resolve_app_version

        body = render_pwa_service_worker(_resolve_app_version())

    response = web.Response(text=body, content_type="application/javascript")
    response.headers["Cache-Control"] = PWA_SERVICE_WORKER_CACHE_CONTROL
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["Service-Worker-Allowed"] = PWA_SERVICE_WORKER_SCOPE
    return response


__all__ = [
    "PWA_BACKGROUND_COLOR",
    "PWA_CACHE_NAME_PLACEHOLDER",
    "PWA_CACHE_NAME_PREFIX",
    "PWA_CACHE_NAME_PREFIX_PLACEHOLDER",
    "PWA_DISPLAY",
    "PWA_FALLBACK_NAME",
    "PWA_FALLBACK_PRIMARY_COLOR",
    "PWA_MANIFEST_CACHE_CONTROL",
    "PWA_SERVICE_WORKER_CACHE_CONTROL",
    "PWA_SERVICE_WORKER_SCOPE",
    "PWA_SHORT_NAME_MAX_LENGTH",
    "PWA_START_URL",
    "apply_pwa_shell_markup",
    "build_pwa_manifest",
    "pwa_cache_name",
    "pwa_enabled",
    "pwa_head_markup",
    "pwa_manifest_route",
    "pwa_registration_script",
    "pwa_service_worker_route",
    "render_pwa_retirement_worker",
    "render_pwa_service_worker",
]
