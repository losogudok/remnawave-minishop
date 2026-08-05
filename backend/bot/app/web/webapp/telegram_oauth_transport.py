from __future__ import annotations

import asyncio
import hashlib
import logging

from aiohttp import ClientSession, ClientTimeout
from aiohttp_socks import ProxyConnector

from config.settings import Settings
from config.telegram_proxy import safe_telegram_proxy_endpoint

logger = logging.getLogger(__name__)

TELEGRAM_OAUTH_HTTP_TIMEOUT_SECONDS = 30.0

_TELEGRAM_OAUTH_HTTP_SESSION: ClientSession | None = None
_TELEGRAM_OAUTH_HTTP_SESSION_LOCK = asyncio.Lock()
_TELEGRAM_OAUTH_HTTP_SESSION_ROUTE_KEY: str | None = None


def telegram_oauth_transport_route_key(settings: Settings) -> str:
    """Return a credential-free identity for the selected OAuth network route."""
    if not settings.TELEGRAM_OAUTH_USE_BOT_PROXY:
        return "direct"

    proxy_url = settings.TELEGRAM_BOT_PROXY_URL
    if proxy_url is None:
        raise RuntimeError("Telegram OAuth proxy is enabled without TELEGRAM_BOT_PROXY_URL")

    digest = hashlib.sha256(proxy_url.get_secret_value().encode("utf-8")).hexdigest()
    return f"socks5:{digest}"


async def get_telegram_oauth_http_session(settings: Settings) -> ClientSession:
    """Get the dedicated client used only for Telegram OAuth token and JWKS calls."""
    global _TELEGRAM_OAUTH_HTTP_SESSION
    global _TELEGRAM_OAUTH_HTTP_SESSION_ROUTE_KEY

    route_key = telegram_oauth_transport_route_key(settings)
    async with _TELEGRAM_OAUTH_HTTP_SESSION_LOCK:
        current_session = _TELEGRAM_OAUTH_HTTP_SESSION
        if (
            current_session is not None
            and not current_session.closed
            and route_key == _TELEGRAM_OAUTH_HTTP_SESSION_ROUTE_KEY
        ):
            return current_session

        if current_session is not None and not current_session.closed:
            await current_session.close()

        connector = None
        proxy_url = settings.TELEGRAM_BOT_PROXY_URL
        if settings.TELEGRAM_OAUTH_USE_BOT_PROXY:
            if proxy_url is None:
                raise RuntimeError("Telegram OAuth proxy is enabled without TELEGRAM_BOT_PROXY_URL")
            connector = ProxyConnector.from_url(
                proxy_url.get_secret_value(),
                rdns=True,
            )
            logger.info(
                "Telegram OAuth SOCKS5 proxy enabled for token and JWKS requests: %s",
                safe_telegram_proxy_endpoint(proxy_url),
            )

        _TELEGRAM_OAUTH_HTTP_SESSION = ClientSession(
            connector=connector,
            timeout=ClientTimeout(total=TELEGRAM_OAUTH_HTTP_TIMEOUT_SECONDS),
            headers={
                "User-Agent": "remnawave-minishop/telegram-oauth",
                "Accept": "application/json",
            },
        )
        _TELEGRAM_OAUTH_HTTP_SESSION_ROUTE_KEY = route_key
        return _TELEGRAM_OAUTH_HTTP_SESSION


async def close_telegram_oauth_http_session() -> None:
    global _TELEGRAM_OAUTH_HTTP_SESSION
    global _TELEGRAM_OAUTH_HTTP_SESSION_ROUTE_KEY

    async with _TELEGRAM_OAUTH_HTTP_SESSION_LOCK:
        if _TELEGRAM_OAUTH_HTTP_SESSION is not None and not _TELEGRAM_OAUTH_HTTP_SESSION.closed:
            await _TELEGRAM_OAUTH_HTTP_SESSION.close()
        _TELEGRAM_OAUTH_HTTP_SESSION = None
        _TELEGRAM_OAUTH_HTTP_SESSION_ROUTE_KEY = None
