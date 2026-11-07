"""GA4 funnel settings, allowlists and Measurement Protocol payloads."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from pydantic import JsonValue, SecretStr

from config.settings import Settings

PLUGIN_NAME = "ga4funnel"
PLUGIN_VERSION = "1.0.0"

MEASUREMENT_ID_PATTERN = re.compile(r"G-[A-Z0-9]{4,20}$")
CLIENT_ID_PATTERN = re.compile(r"^[0-9]{1,20}\.[0-9]{1,20}$")
COOKIE_CLIENT_ID_PATTERN = re.compile(r"GA1\.[0-9]+\.[0-9]{1,20}\.[0-9]{1,20}\Z")

MEASUREMENT_HOST = "https://www.google-analytics.com"
COLLECT_PATH = "/mp/collect"

SIGN_UP = "sign_up"
TRIAL_STARTED = "trial_started"
PURCHASE = "purchase"
VPN_FIRST_CONNECTED = "vpn_first_connected"

ALLOWED_EVENT_NAMES = frozenset({SIGN_UP, TRIAL_STARTED, PURCHASE, VPN_FIRST_CONNECTED})
ALLOWED_EVENT_PARAMS: dict[str, frozenset[str]] = {
    SIGN_UP: frozenset(),
    TRIAL_STARTED: frozenset({"days", "traffic_gb"}),
    PURCHASE: frozenset({"transaction_id", "value", "currency", "sale_mode", "months"}),
    VPN_FIRST_CONNECTED: frozenset(),
}

#: Browser descriptor origins (fixed Google endpoints only).
SCRIPT_ORIGINS = ("https://www.googletagmanager.com",)
CONNECT_HOSTS = (
    "https://www.google-analytics.com",
    "https://analytics.google.com",
    "https://region1.google-analytics.com",
)

#: Occurrence timestamps older than this are not sent as ``timestamp_micros``.
MAX_EVENT_AGE_SECONDS = 72 * 60 * 60


def normalize_measurement_id(value: object) -> str | None:
    text = str(value or "").strip().upper()
    return text if MEASUREMENT_ID_PATTERN.fullmatch(text) else None


def api_secret(settings: Settings) -> str | None:
    value = settings.GA4_API_SECRET
    if isinstance(value, SecretStr):
        text = value.get_secret_value().strip()
    else:
        text = str(value or "").strip()
    return text or None


def ga4_configured(settings: Settings) -> bool:
    return bool(
        settings.GA4_ENABLED
        and normalize_measurement_id(settings.GA4_MEASUREMENT_ID)
        and api_secret(settings)
    )


def _safe_text(value: object, limit: int = 64) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit]


def _safe_float(
    value: object,
    *,
    minimum: float = 0.0,
    maximum: float = 1_000_000.0,
) -> float | None:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return min(max(parsed, minimum), maximum)


def _safe_int(value: object, *, minimum: int = 0, maximum: int = 100_000) -> int | None:
    try:
        parsed = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    return min(max(parsed, minimum), maximum)


def sanitize_event_params(name: str, params: dict[str, Any] | None) -> dict[str, JsonValue]:
    """Return only the allowlisted, bounded parameters for a server event."""

    allowed = ALLOWED_EVENT_PARAMS.get(name)
    if allowed is None or not params:
        return {}
    cleaned: dict[str, JsonValue] = {}
    for key in allowed:
        if key not in params:
            continue
        value = params[key]
        if key == "days":
            parsed = _safe_int(value, maximum=3650)
            if parsed is not None:
                cleaned[key] = parsed
        elif key == "traffic_gb":
            parsed_float = _safe_float(value, maximum=1_000_000.0)
            if parsed_float is not None:
                cleaned[key] = parsed_float
        elif key == "value":
            parsed_value = _safe_float(value)
            if parsed_value is not None:
                cleaned[key] = parsed_value
        elif key == "months":
            parsed_months = _safe_int(value, maximum=1200)
            if parsed_months is not None:
                cleaned[key] = parsed_months
        elif key == "transaction_id":
            text = _safe_text(value, 64)
            if text is not None:
                cleaned[key] = text
        elif key == "currency":
            text = _safe_text(value, 8)
            if text is not None:
                cleaned[key] = text.upper()
        elif key == "sale_mode":
            text = _safe_text(value, 64)
            if text is not None:
                cleaned[key] = text
    return cleaned


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def build_event_body(
    *,
    client_id: str,
    name: str,
    params: dict[str, Any] | None,
    occurred_at: datetime | None,
    now: datetime | None = None,
) -> dict[str, JsonValue]:
    """Build one Measurement Protocol body without leaking non-allowlisted data."""

    current = as_utc(now) or datetime.now(UTC)
    event_params: dict[str, JsonValue] = sanitize_event_params(name, params)
    event_params["engagement_time_msec"] = "1"
    occurred = as_utc(occurred_at)
    if occurred is not None:
        age_seconds = (current - occurred).total_seconds()
        if 0 <= age_seconds <= MAX_EVENT_AGE_SECONDS:
            event_params["timestamp_micros"] = int(occurred.timestamp() * 1_000_000)
    return {
        "client_id": client_id,
        "events": [{"name": name, "params": event_params}],
    }


__all__ = [
    "ALLOWED_EVENT_NAMES",
    "ALLOWED_EVENT_PARAMS",
    "CLIENT_ID_PATTERN",
    "COLLECT_PATH",
    "CONNECT_HOSTS",
    "COOKIE_CLIENT_ID_PATTERN",
    "MAX_EVENT_AGE_SECONDS",
    "MEASUREMENT_HOST",
    "MEASUREMENT_ID_PATTERN",
    "PLUGIN_NAME",
    "PLUGIN_VERSION",
    "PURCHASE",
    "SCRIPT_ORIGINS",
    "SIGN_UP",
    "TRIAL_STARTED",
    "VPN_FIRST_CONNECTED",
    "api_secret",
    "as_utc",
    "build_event_body",
    "ga4_configured",
    "normalize_measurement_id",
    "sanitize_event_params",
]
