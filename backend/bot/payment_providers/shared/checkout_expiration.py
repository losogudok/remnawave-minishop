from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

_ABSOLUTE_EXPIRATION_KEYS = frozenset(
    {
        "checkout_expires_at",
        "expiration_date",
        "expirationdate",
        "expiration_datetime",
        "expirationdatetime",
        "expired_at",
        "expiredat",
        "expires_at",
        "expiresat",
    }
)


def _parse_expiration(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        try:
            return datetime.fromtimestamp(timestamp, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.replace(".", "", 1).isdigit():
        try:
            return _parse_expiration(float(text))
        except ValueError:
            return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _find_expiration(value: Any) -> datetime | None:
    if isinstance(value, Mapping):
        for key, candidate in value.items():
            normalized_key = str(key).strip().lower().replace("-", "_")
            if normalized_key in _ABSOLUTE_EXPIRATION_KEYS:
                parsed = _parse_expiration(candidate)
                if parsed is not None:
                    return parsed
        for candidate in value.values():
            parsed = _find_expiration(candidate)
            if parsed is not None:
                return parsed
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for candidate in value:
            parsed = _find_expiration(candidate)
            if parsed is not None:
                return parsed
    return None


def resolve_checkout_expiration(
    provider_response: Any,
    *,
    fallback_ttl_seconds: int | None = None,
    now: datetime | None = None,
) -> datetime | None:
    """Resolve a provider-issued absolute expiry or the exact TTL sent on creation."""

    explicit = _find_expiration(provider_response)
    if explicit is not None:
        return explicit
    if fallback_ttl_seconds is None:
        return None
    ttl = int(fallback_ttl_seconds)
    if ttl <= 0:
        return None
    started_at = now or datetime.now(UTC)
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    return started_at.astimezone(UTC) + timedelta(seconds=ttl)
