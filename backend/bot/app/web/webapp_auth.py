import asyncio
import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl

from bot.app.web.webapp.telegram_oauth_transport import (
    get_telegram_oauth_http_session,
    telegram_oauth_transport_route_key,
)
from config.settings import Settings
from config.telegram_proxy import safe_telegram_network_error_detail

if TYPE_CHECKING:
    from jwt import PyJWK

logger = logging.getLogger(__name__)

# 5 minutes clock skew tolerance for Telegram clients
TELEGRAM_CLOCK_SKEW_SECONDS = 300
TELEGRAM_OAUTH_ISSUER = "https://oauth.telegram.org"
TELEGRAM_OAUTH_JWKS_URL = "https://oauth.telegram.org/.well-known/jwks.json"
TELEGRAM_OAUTH_ALGORITHMS = ["RS256", "ES256", "EdDSA"]
# The signing keys are fetched over the network, on the login callback, while the customer's
# browser waits. Telegram rotates them rarely, so one cached key set serves every login instead
# of one round-trip per login. The fetch uses the same dedicated transport as the token exchange,
# which lets an operator opt both server-side OIDC calls into the Bot API SOCKS5 route.
TELEGRAM_OAUTH_JWKS_LIFESPAN_SECONDS = 600
TELEGRAM_OAUTH_JWKS_TIMEOUT_SECONDS = 10.0
TELEGRAM_OAUTH_VALIDATION_TIMEOUT_SECONDS = 12.0


@dataclass(frozen=True)
class _TelegramJwksCacheEntry:
    payload: dict[str, Any]
    fetched_at: float
    transport_route_key: str


_telegram_jwks_cache: _TelegramJwksCacheEntry | None = None
_telegram_jwks_cache_lock = asyncio.Lock()


async def _fetch_telegram_oauth_jwks(settings: Settings) -> dict[str, Any]:
    session = await get_telegram_oauth_http_session(settings)
    async with session.get(
        TELEGRAM_OAUTH_JWKS_URL,
        timeout=TELEGRAM_OAUTH_JWKS_TIMEOUT_SECONDS,
    ) as response:
        if response.status >= 400:
            raise RuntimeError(f"Telegram OAuth JWKS endpoint returned HTTP {response.status}")
        payload = await response.json(content_type=None)

    if not isinstance(payload, dict) or not isinstance(payload.get("keys"), list):
        raise ValueError("Telegram OAuth JWKS endpoint returned an invalid key set")
    return dict(payload)


async def _get_telegram_oauth_jwks(
    settings: Settings,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    global _telegram_jwks_cache

    route_key = telegram_oauth_transport_route_key(settings)
    now = time.monotonic()
    cached = _telegram_jwks_cache
    if (
        not force_refresh
        and cached is not None
        and cached.transport_route_key == route_key
        and now - cached.fetched_at < TELEGRAM_OAUTH_JWKS_LIFESPAN_SECONDS
    ):
        return cached.payload

    async with _telegram_jwks_cache_lock:
        now = time.monotonic()
        cached = _telegram_jwks_cache
        if (
            not force_refresh
            and cached is not None
            and cached.transport_route_key == route_key
            and now - cached.fetched_at < TELEGRAM_OAUTH_JWKS_LIFESPAN_SECONDS
        ):
            return cached.payload

        payload = await _fetch_telegram_oauth_jwks(settings)
        _telegram_jwks_cache = _TelegramJwksCacheEntry(
            payload=payload,
            fetched_at=time.monotonic(),
            transport_route_key=route_key,
        )
        return payload


def _select_telegram_oauth_signing_key(
    id_token: str,
    jwks_payload: dict[str, Any],
) -> "PyJWK | None":
    import jwt

    key_id = str(jwt.get_unverified_header(id_token).get("kid") or "")
    if not key_id:
        raise ValueError("Telegram OAuth ID token does not contain a key ID")

    key_set = jwt.PyJWKSet.from_dict(jwks_payload)
    for key in key_set.keys:
        if key.key_id == key_id and key.public_key_use in {"sig", None}:
            return key
    return None


async def _load_telegram_oauth_signing_key(
    settings: Settings,
    id_token: str,
) -> "PyJWK":
    jwks_payload = await _get_telegram_oauth_jwks(settings)
    signing_key = await asyncio.to_thread(
        _select_telegram_oauth_signing_key,
        id_token,
        jwks_payload,
    )
    if signing_key is not None:
        return signing_key

    # A missing kid usually means Telegram rotated keys before the cache expired.
    jwks_payload = await _get_telegram_oauth_jwks(settings, force_refresh=True)
    signing_key = await asyncio.to_thread(
        _select_telegram_oauth_signing_key,
        id_token,
        jwks_payload,
    )
    if signing_key is None:
        raise ValueError("Telegram OAuth signing key was not found after refreshing JWKS")
    return signing_key


def _urlsafe_b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _urlsafe_b64decode(raw: str) -> bytes:
    padded = raw + ("=" * (-len(raw) % 4))
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _webapp_session_secret(settings: Settings) -> str:
    try:
        return str(settings.webapp_settings.session_secret)
    except AttributeError:
        return str(settings.WEBAPP_SESSION_SECRET)


def _webapp_session_ttl_seconds(settings: Settings) -> int:
    try:
        return int(settings.webapp_settings.session_ttl_seconds)
    except AttributeError:
        return int(settings.WEBAPP_SESSION_TTL_SECONDS)


def _session_secret(settings: Settings) -> bytes:
    return hmac.new(
        _webapp_session_secret(settings).encode("utf-8"),
        b"remnawave-tg-shop-webapp-session",
        hashlib.sha256,
    ).digest()


def create_webapp_session_token(settings: Settings, user_id: int) -> str:
    now = int(time.time())
    payload = {
        "sub": int(user_id),
        "iat": now,
        "exp": now + max(60, _webapp_session_ttl_seconds(settings)),
    }
    payload_part = _urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(
        _session_secret(settings),
        payload_part.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{payload_part}.{_urlsafe_b64encode(signature)}"


def verify_webapp_session_token(settings: Settings, token: str) -> int | None:
    if not token or "." not in token:
        return None

    try:
        payload_part, signature_part = token.split(".", 1)
        expected_signature = hmac.new(
            _session_secret(settings),
            payload_part.encode("ascii"),
            hashlib.sha256,
        ).digest()
        received_signature = _urlsafe_b64decode(signature_part)
        if not hmac.compare_digest(expected_signature, received_signature):
            return None

        payload = json.loads(_urlsafe_b64decode(payload_part).decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return int(payload["sub"])
    except Exception as exc:
        logger.debug("Failed to verify webapp session token: %s", exc)
        return None


def create_telegram_oauth_nonce(settings: Settings, *, ttl_seconds: int = 600) -> str:
    now = int(time.time())
    payload = {
        "n": secrets.token_urlsafe(24),
        "iat": now,
        "exp": now + max(60, int(ttl_seconds)),
    }
    payload_part = _urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(
        _session_secret(settings),
        f"telegram-oauth-nonce.{payload_part}".encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{payload_part}.{_urlsafe_b64encode(signature)}"


def verify_telegram_oauth_nonce(settings: Settings, nonce: str) -> bool:
    if not nonce or "." not in nonce:
        return False

    try:
        payload_part, signature_part = nonce.split(".", 1)
        expected_signature = hmac.new(
            _session_secret(settings),
            f"telegram-oauth-nonce.{payload_part}".encode("ascii"),
            hashlib.sha256,
        ).digest()
        received_signature = _urlsafe_b64decode(signature_part)
        if not hmac.compare_digest(expected_signature, received_signature):
            return False

        payload = json.loads(_urlsafe_b64decode(payload_part).decode("utf-8"))
        now = int(time.time())
        if int(payload.get("exp", 0)) < now:
            return False
        if int(payload.get("iat", 0)) > now + TELEGRAM_CLOCK_SKEW_SECONDS:
            return False
        return bool(payload.get("n"))
    except Exception as exc:
        logger.debug("Failed to verify Telegram OAuth nonce: %s", exc)
        return False


def create_signed_telegram_oauth_state(
    settings: Settings,
    payload: dict[str, Any],
    *,
    ttl_seconds: int = 600,
) -> str:
    now = int(time.time())
    state_payload = {
        **payload,
        "iat": now,
        "exp": now + max(60, int(ttl_seconds)),
    }
    payload_part = _urlsafe_b64encode(
        json.dumps(state_payload, separators=(",", ":")).encode("utf-8")
    )
    signature = hmac.new(
        _session_secret(settings),
        f"telegram-oauth-state.{payload_part}".encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{payload_part}.{_urlsafe_b64encode(signature)}"


def verify_signed_telegram_oauth_state(
    settings: Settings,
    state: str,
) -> dict[str, Any] | None:
    if not state or "." not in state:
        return None

    try:
        payload_part, signature_part = state.split(".", 1)
        expected_signature = hmac.new(
            _session_secret(settings),
            f"telegram-oauth-state.{payload_part}".encode("ascii"),
            hashlib.sha256,
        ).digest()
        received_signature = _urlsafe_b64decode(signature_part)
        if not hmac.compare_digest(expected_signature, received_signature):
            return None

        payload = json.loads(_urlsafe_b64decode(payload_part).decode("utf-8"))
        now = int(time.time())
        if int(payload.get("exp", 0)) < now:
            return None
        if int(payload.get("iat", 0)) > now + TELEGRAM_CLOCK_SKEW_SECONDS:
            return None
        return payload
    except Exception as exc:
        logger.debug("Failed to verify Telegram OAuth state: %s", exc)
        return None


async def validate_telegram_oauth_id_token(
    id_token: str,
    *,
    settings: Settings,
    client_id: int,
    expected_nonce: str,
    max_age_seconds: int,
) -> dict[str, Any] | None:
    """Validate Telegram OIDC ID token and return a Telegram-like user payload."""

    if not id_token or not client_id or not expected_nonce:
        return None

    try:
        import jwt
    except Exception as exc:
        logger.error(
            "PyJWT is not installed; Telegram OAuth ID token validation is unavailable: %s",
            exc,
        )
        return None

    try:
        signing_key = await asyncio.wait_for(
            _load_telegram_oauth_signing_key(settings, id_token),
            timeout=TELEGRAM_OAUTH_VALIDATION_TIMEOUT_SECONDS,
        )
        claims = await asyncio.to_thread(
            jwt.decode,
            id_token,
            signing_key.key,
            algorithms=TELEGRAM_OAUTH_ALGORITHMS,
            audience=str(client_id),
            issuer=TELEGRAM_OAUTH_ISSUER,
            leeway=TELEGRAM_CLOCK_SKEW_SECONDS,
            options={"require": ["exp", "iat", "iss", "aud"]},
        )

        if not hmac.compare_digest(str(claims.get("nonce") or ""), expected_nonce):
            logger.warning("Telegram OAuth nonce mismatch.")
            return None

        now = int(time.time())
        issued_at = int(claims.get("iat") or 0)
        max_age = max(60, int(max_age_seconds))
        if issued_at > now + TELEGRAM_CLOCK_SKEW_SECONDS or now - issued_at > max_age:
            logger.warning("Telegram OAuth ID token is stale.")
            return None

        telegram_id_raw = claims.get("id")
        if not telegram_id_raw:
            return None
        telegram_id = int(telegram_id_raw)

        full_name = str(claims.get("name") or "").strip()
        first_name = str(claims.get("given_name") or "").strip()
        last_name = str(claims.get("family_name") or "").strip()
        if full_name and not first_name:
            name_parts = full_name.split(None, 1)
            first_name = name_parts[0]
            if len(name_parts) > 1 and not last_name:
                last_name = name_parts[1]

        return {
            "id": telegram_id,
            "username": claims.get("preferred_username") or claims.get("username"),
            "first_name": first_name or full_name or "Telegram",
            "last_name": last_name,
            "photo_url": claims.get("picture"),
            "language_code": claims.get("locale"),
        }
    except Exception as exc:
        logger.warning(
            "Failed to validate Telegram OAuth ID token: %s",
            safe_telegram_network_error_detail(exc),
        )
        return None


def validate_telegram_webapp_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_seconds: int,
) -> dict[str, Any] | None:
    """Validate Telegram Mini App initData and return the trusted user payload."""

    try:
        parsed_data = dict(parse_qsl(init_data or "", keep_blank_values=True))
        received_hash = parsed_data.pop("hash", None)
        if not received_hash:
            return None

        data_check_string = "\n".join(
            f"{key}={value}" for key, value in sorted(parsed_data.items())
        )
        secret_key = hmac.new(
            b"WebAppData",
            bot_token.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(calculated_hash, received_hash):
            logger.warning("Telegram WebApp initData hash mismatch.")
            return None

        auth_date_raw = parsed_data.get("auth_date")
        if auth_date_raw:
            auth_date = int(auth_date_raw)
            now = int(time.time())
            max_age = max(60, int(max_age_seconds))
            if auth_date > now + TELEGRAM_CLOCK_SKEW_SECONDS or now - auth_date > max_age:
                logger.warning("Telegram WebApp initData auth_date is stale.")
                return None

        user_json = parsed_data.get("user")
        if not user_json:
            return None
        user_data = json.loads(user_json)
        if not user_data.get("id"):
            return None
        if parsed_data.get("start_param"):
            user_data["start_param"] = parsed_data.get("start_param")
        return user_data
    except Exception as exc:
        logger.warning("Failed to validate Telegram WebApp initData: %s", exc)
        return None


def validate_telegram_login_widget_data(
    auth_data: Any,
    bot_token: str,
    *,
    max_age_seconds: int,
) -> dict[str, Any] | None:
    """Validate Telegram Login Widget data and return the trusted user payload."""

    try:
        if isinstance(auth_data, str):
            parsed_data = dict(parse_qsl(auth_data or "", keep_blank_values=True))
        elif isinstance(auth_data, dict):
            parsed_data = {
                str(key): str(value) for key, value in auth_data.items() if value is not None
            }
        else:
            return None

        received_hash = str(parsed_data.pop("hash", "") or "")
        if not received_hash:
            return None

        data_check_string = "\n".join(
            f"{key}={value}" for key, value in sorted(parsed_data.items())
        )
        secret_key = hashlib.sha256(bot_token.encode("utf-8")).digest()
        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(calculated_hash, received_hash):
            logger.warning("Telegram Login Widget hash mismatch.")
            return None

        auth_date_raw = parsed_data.get("auth_date")
        if auth_date_raw:
            auth_date = int(auth_date_raw)
            now = int(time.time())
            max_age = max(60, int(max_age_seconds))
            if auth_date > now + TELEGRAM_CLOCK_SKEW_SECONDS or now - auth_date > max_age:
                logger.warning("Telegram Login Widget auth_date is stale.")
                return None

        user_id_raw = parsed_data.get("id")
        if not user_id_raw:
            return None
        int(user_id_raw)

        if not parsed_data.get("first_name"):
            return None

        return parsed_data
    except Exception as exc:
        logger.warning("Failed to validate Telegram Login Widget data: %s", exc)
        return None
