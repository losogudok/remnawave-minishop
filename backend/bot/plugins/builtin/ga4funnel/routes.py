"""Public GA4 funnel routes served by the built-in plugin."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from aiohttp import web
from pydantic import Field

from bot.app.web.context import get_session_factory, get_settings
from bot.app.web.http_contracts import HttpBodyModel, HttpResponseModel
from bot.app.web.request_parsing import parse_body_or_400
from bot.app.web.route_contracts import (
    GENERIC_OK_RESPONSE,
    USER_SECURITY,
    RouteContract,
    ok_envelope_with,
    register_contract,
)
from bot.app.web.webapp.common import _require_user_id

from . import store
from .config import (
    CLIENT_ID_PATTERN,
    CONNECT_HOSTS,
    SCRIPT_ORIGINS,
    ga4_configured,
    normalize_measurement_id,
)

CONFIG_PATH = "/api/plugins/ga4funnel/config"
SIGNALS_PATH = "/api/plugins/ga4funnel/signals.mjs"
BIND_PATH = "/api/plugins/ga4funnel/bind"
FRAME_PATH = "/api/plugins/ga4funnel/frame"
FRAME_SCRIPT_PATH = "/api/plugins/ga4funnel/frame.mjs"
BIND_BODY_MAX_BYTES = 4096
FRAME_CSP = (
    "default-src 'none'; "
    f"script-src 'self' {' '.join(SCRIPT_ORIGINS)}; "
    f"connect-src {' '.join(CONNECT_HOSTS)}; "
    "img-src https://www.google-analytics.com; "
    "frame-ancestors 'self' https://web.telegram.org https://t.me"
)
_FRAME_HTML = (
    '<!doctype html><html><head><meta charset="utf-8">'
    '<meta name="referrer" content="no-referrer">'
    "<title>Analytics</title></head><body>"
    f'<script type="module" src="{FRAME_SCRIPT_PATH}"></script>'
    "</body></html>"
)


class Ga4ConfigOut(HttpResponseModel):
    enabled: bool
    measurement_id: str
    debug_mode: bool


class Ga4BindBody(HttpBodyModel):
    client_id: str = Field(min_length=3, max_length=41, pattern=CLIENT_ID_PATTERN.pattern)


@lru_cache(maxsize=1)
def _adapter_source() -> str:
    return Path(__file__).with_name("signals.mjs").read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def _frame_source() -> str:
    return Path(__file__).with_name("frame.mjs").read_text(encoding="utf-8")


async def ga4funnel_config_route(request: web.Request) -> web.Response:
    settings = get_settings(request)
    configured = ga4_configured(settings)
    measurement_id = normalize_measurement_id(settings.GA4_MEASUREMENT_ID) if configured else None
    body = Ga4ConfigOut(
        enabled=configured,
        measurement_id=measurement_id or "",
        debug_mode=bool(settings.GA4_DEBUG_MODE),
    )
    response = web.json_response({"ok": True, **body.model_dump(mode="json")})
    response.headers["Cache-Control"] = "no-store"
    return response


async def ga4funnel_signals_route(request: web.Request) -> web.Response:
    settings = get_settings(request)
    if not ga4_configured(settings):
        raise web.HTTPNotFound(text="not_found")
    response = web.Response(text=_adapter_source(), content_type="application/javascript")
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


async def ga4funnel_frame_route(request: web.Request) -> web.Response:
    settings = get_settings(request)
    if not ga4_configured(settings):
        raise web.HTTPNotFound(text="not_found")
    response = web.Response(text=_FRAME_HTML, content_type="text/html")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = FRAME_CSP
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


async def ga4funnel_frame_script_route(request: web.Request) -> web.Response:
    settings = get_settings(request)
    if not ga4_configured(settings):
        raise web.HTTPNotFound(text="not_found")
    response = web.Response(text=_frame_source(), content_type="application/javascript")
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


async def ga4funnel_bind_route(request: web.Request) -> web.Response:
    user_id = _require_user_id(request)
    body = await parse_body_or_400(
        request.clone(client_max_size=BIND_BODY_MAX_BYTES),
        Ga4BindBody,
    )
    factory = get_session_factory(request)
    async with factory() as session:
        await store.bind_client(
            session,
            user_id=user_id,
            client_id=body.client_id,
        )
        await session.commit()
    return web.json_response({"ok": True})


def setup_ga4funnel_routes(app: web.Application) -> None:
    app.router.add_get(CONFIG_PATH, ga4funnel_config_route)
    app.router.add_get(SIGNALS_PATH, ga4funnel_signals_route)
    app.router.add_get(FRAME_PATH, ga4funnel_frame_route)
    app.router.add_get(FRAME_SCRIPT_PATH, ga4funnel_frame_script_route)
    app.router.add_post(BIND_PATH, ga4funnel_bind_route)


register_contract(
    "ga4funnel_config_route",
    RouteContract(
        response_schema=ok_envelope_with(
            {
                "enabled": {"type": "boolean"},
                "measurement_id": {"type": "string"},
                "debug_mode": {"type": "boolean"},
            }
        ),
        models=(Ga4ConfigOut,),
        security=[],
    ),
)
register_contract(
    "ga4funnel_signals_route",
    RouteContract(
        response_schema={"type": "string"},
        response_content_type="application/javascript",
        security=[],
    ),
)
register_contract(
    "ga4funnel_frame_route",
    RouteContract(
        response_schema={"type": "string"},
        response_content_type="text/html",
        security=[],
    ),
)
register_contract(
    "ga4funnel_frame_script_route",
    RouteContract(
        response_schema={"type": "string"},
        response_content_type="application/javascript",
        security=[],
    ),
)
register_contract(
    "ga4funnel_bind_route",
    RouteContract(
        request_model=Ga4BindBody,
        response_schema=GENERIC_OK_RESPONSE,
        models=(Ga4BindBody,),
        security=USER_SECURITY,
    ),
)


__all__ = [
    "BIND_PATH",
    "CONFIG_PATH",
    "FRAME_CSP",
    "FRAME_PATH",
    "FRAME_SCRIPT_PATH",
    "SIGNALS_PATH",
    "ga4funnel_bind_route",
    "ga4funnel_config_route",
    "ga4funnel_frame_route",
    "ga4funnel_frame_script_route",
    "ga4funnel_signals_route",
    "setup_ga4funnel_routes",
]
