from __future__ import annotations

import re
from urllib.parse import urlsplit

from pydantic import SecretStr

_ENCODED_CREDENTIAL_PART_RE = re.compile(r"(?:[A-Za-z0-9._~-]|%[0-9A-Fa-f]{2})+")
_SOCKS5_CREDENTIALS_RE = re.compile(r"(?i)\bsocks5://[^/@\s]+@")


def validate_telegram_bot_proxy_url(value: SecretStr | None) -> SecretStr | None:
    """Validate and normalize the optional Telegram Bot API SOCKS5 endpoint."""
    if value is None:
        return None

    raw_url = value.get_secret_value().strip()
    if not raw_url:
        return None
    if any(character.isspace() for character in raw_url):
        raise ValueError("Telegram Bot proxy URL must not contain whitespace")
    if not raw_url.startswith("socks5://"):
        raise ValueError("Telegram Bot proxy URL must use the socks5:// scheme")
    if "?" in raw_url or "#" in raw_url:
        raise ValueError("Telegram Bot proxy URL must not include a path, query, or fragment")

    try:
        parsed = urlsplit(raw_url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Telegram Bot proxy URL has an invalid host or port") from exc

    if not hostname:
        raise ValueError("Telegram Bot proxy URL must include a hostname")
    if port is None:
        raise ValueError("Telegram Bot proxy URL must include an explicit port")
    if not 1 <= port <= 65535:
        raise ValueError("Telegram Bot proxy port must be between 1 and 65535")
    if parsed.path or parsed.query or parsed.fragment:
        raise ValueError("Telegram Bot proxy URL must not include a path, query, or fragment")
    if "%" in hostname or "@" in hostname:
        raise ValueError("Telegram Bot proxy URL has an invalid hostname")

    username = parsed.username
    password = parsed.password
    if (username is None) != (password is None):
        raise ValueError("Telegram Bot proxy username and password must be provided together")
    if username is not None and password is not None:
        if not username or not password:
            raise ValueError("Telegram Bot proxy username and password must not be empty")
        if not _ENCODED_CREDENTIAL_PART_RE.fullmatch(username) or not (
            _ENCODED_CREDENTIAL_PART_RE.fullmatch(password)
        ):
            raise ValueError(
                "Telegram Bot proxy credentials contain characters that must be percent-encoded"
            )

    return SecretStr(raw_url)


def redact_telegram_proxy_credentials(value: str) -> str:
    """Remove SOCKS5 credentials from arbitrary diagnostic text."""
    return _SOCKS5_CREDENTIALS_RE.sub("socks5://***:***@", value)


def safe_telegram_network_error_detail(exc: BaseException) -> str:
    """Render a Telegram transport error without leaking SOCKS5 credentials."""
    root_cause = exc.__cause__ or exc.__context__
    detail = str(exc)
    if root_cause:
        root_detail = f"{type(root_cause).__name__}: {root_cause}"
        if root_detail not in detail:
            detail = f"{detail} ({root_detail})"
    return redact_telegram_proxy_credentials(detail)


def safe_telegram_proxy_endpoint(value: SecretStr) -> str:
    """Return a log-safe endpoint while preserving useful routing details."""
    return redact_telegram_proxy_credentials(value.get_secret_value())
