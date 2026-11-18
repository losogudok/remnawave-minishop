"""Native Google and Telegram ID-token exchanges, independent of browser sessions."""

from aiohttp import web

from bot.app.web.context import get_settings
from bot.app.web.request_parsing import parse_body
from bot.app.web.webapp_auth import validate_telegram_oauth_id_token
from config.settings import Settings

from . import auth_oauth, external_oauth, native_auth_challenges
from .external_oauth_providers import google_profile, provider_for_settings
from .native_auth_contracts import (
    NativeChallengeBody,
    NativeChallengeOut,
    NativeProvider,
    NativeTokenBody,
)
from .payloads import WebAppTelegramAuthPayload
from .rate_limits import check_request_limits, client_ip
from .response_helpers import json_response


def _no_store(response: web.Response) -> web.Response:
    response.headers.update({"Cache-Control": "no-store", "Pragma": "no-cache"})
    return response


def _error(code: str, status: int) -> web.Response:
    return _no_store(json_response({"ok": False, "error": code}, status=status))


def _telegram_client_id(settings: Settings) -> int:
    return int(settings.TELEGRAM_NATIVE_OAUTH_CLIENT_ID or settings.TELEGRAM_OAUTH_CLIENT_ID or 0)


def _configuration_error(settings: Settings, provider: NativeProvider) -> web.Response | None:
    if provider == "google":
        if not settings.GOOGLE_OIDC_ENABLED:
            return _error("google_login_disabled", 404)
        if provider_for_settings(settings, "google") is None:
            return _error("google_not_configured", 503)
    else:
        if not settings.TELEGRAM_ENABLED or not settings.TELEGRAM_LOGIN_ENABLED:
            return _error("telegram_login_disabled", 404)
        # Native audiences must be explicitly registered, never inferred from BOT_TOKEN.
        if _telegram_client_id(settings) <= 0:
            return _error("telegram_not_configured", 503)
    return None


async def _preflight(
    request: web.Request, provider: NativeProvider, *, exchange: bool
) -> web.Response | None:
    # These routes never inherit a browser identity or an old app account.
    if any(name in request.headers for name in ("Origin", "Cookie", "Authorization")):
        return _error("native_request_required", 403)
    if request.content_type != "application/json":
        return _error("invalid_request", 400)
    blocked = _configuration_error(get_settings(request), provider)
    if blocked is not None:
        return blocked
    limited = await check_request_limits(
        request,
        [
            (
                f"native:{provider}:{'token' if exchange else 'challenge'}:{client_ip(request)}",
                20 if exchange else 10,
            )
        ],
        window_seconds=60,
    )
    return _no_store(limited) if limited is not None else None


async def _challenge(request: web.Request, provider: NativeProvider) -> web.Response:
    blocked = await _preflight(request, provider, exchange=False)
    if blocked is not None:
        return blocked
    body, error = await parse_body(request.clone(client_max_size=16384), NativeChallengeBody)
    if error is not None:
        return _no_store(error)
    assert body is not None
    try:
        request_id, nonce = await native_auth_challenges.issue(get_settings(request), provider)
    except Exception:
        return _error("native_auth_unavailable", 503)
    return _no_store(
        json_response(
            NativeChallengeOut(
                ok=True,
                request_id=request_id,
                nonce=nonce,
                expires_in=native_auth_challenges.TTL_SECONDS,
            ).model_dump(mode="json")
        )
    )


async def _exchange(request: web.Request, provider: NativeProvider) -> web.Response:
    blocked = await _preflight(request, provider, exchange=True)
    if blocked is not None:
        return blocked
    body, error = await parse_body(request.clone(client_max_size=16384), NativeTokenBody)
    if error is not None:
        return _no_store(error)
    assert body is not None
    settings = get_settings(request)
    try:
        nonce = await native_auth_challenges.peek(settings, provider, body.request_id)
    except Exception:
        return _error("native_auth_unavailable", 503)
    if nonce is None:
        return _error("challenge_expired", 410)

    google = provider_for_settings(settings, "google") if provider == "google" else None
    if provider == "google":
        if google is None:
            return _error("google_not_configured", 503)
        profile = await google_profile({"id_token": body.id_token}, google, nonce)
    else:
        profile = await validate_telegram_oauth_id_token(
            body.id_token,
            settings=settings,
            client_id=_telegram_client_id(settings),
            expected_nonce=nonce,
            max_age_seconds=native_auth_challenges.TTL_SECONDS,
            algorithms=("RS256",),
            leeway_seconds=0,
        )
    if profile is None:
        return _error(f"invalid_{provider}_credential", 401)
    try:
        accepted = await native_auth_challenges.consume(settings, provider, body.request_id, nonce)
    except Exception:
        return _error("native_auth_unavailable", 503)
    if not accepted:
        return _error("challenge_expired", 410)

    # Only the atomic winner may mutate accounts or create a session. Failures
    # after this boundary require a fresh challenge and provider credential.
    if provider == "google":
        assert google is not None
        response = await external_oauth.complete_native_google_login(request, google, profile)
    else:
        response = await auth_oauth.complete_telegram_login(
            request, profile, auth_payload=WebAppTelegramAuthPayload(), native=True
        )
    return _no_store(response)


async def native_google_challenge_route(request: web.Request) -> web.Response:
    return await _challenge(request, "google")


async def native_google_token_route(request: web.Request) -> web.Response:
    return await _exchange(request, "google")


async def native_telegram_challenge_route(request: web.Request) -> web.Response:
    return await _challenge(request, "telegram")


async def native_telegram_token_route(request: web.Request) -> web.Response:
    return await _exchange(request, "telegram")
