from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit


def _canonical_positive_decimal(value: Any, *, integer: bool, label: str) -> str:
    text = str(value).strip()
    try:
        decimal_value = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} keys must be positive numbers") from exc
    if not decimal_value.is_finite() or decimal_value <= 0:
        raise ValueError(f"{label} keys must be positive numbers")
    if integer and decimal_value != decimal_value.to_integral_value():
        raise ValueError(f"{label} keys must be positive integers")
    if decimal_value == decimal_value.to_integral_value():
        return str(int(decimal_value))
    return format(decimal_value.normalize(), "f").rstrip("0").rstrip(".")


def canonical_tribute_product_unit(value: Any) -> str:
    return _canonical_positive_decimal(value, integer=False, label="Tribute product unit")


def _normalize_tribute_map_keys(
    value: Any,
    *,
    integer: bool,
    label: str,
) -> Any:
    if value is None:
        return {}
    if not isinstance(value, dict):
        return value

    normalized: dict[str, Any] = {}
    for raw_key, item in value.items():
        key = _canonical_positive_decimal(raw_key, integer=integer, label=label)
        if key in normalized:
            raise ValueError(f"duplicate normalized {label} key: {key}")
        normalized[key] = item
    return normalized


def validate_tribute_link(value: str) -> str:
    link = value.strip()
    if not link or any(character.isspace() for character in link):
        raise ValueError("Tribute link must be a valid HTTPS URL")

    parsed = urlsplit(link)
    if parsed.scheme.lower() != "https":
        raise ValueError("Tribute link must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Tribute link must not contain credentials")

    hostname = (parsed.hostname or "").lower()
    allowed_host = hostname in {"t.me", "telegram.me", "tribute.tg"} or hostname.endswith(
        ".tribute.tg"
    )
    if not allowed_host:
        raise ValueError("Tribute link must use an official Tribute or Telegram host")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Tribute link contains an invalid port") from exc
    if port not in {None, 443}:
        raise ValueError("Tribute link must use the default HTTPS port")
    return link
