"""Allowlisted Web Push HTTP sender.

The sender treats the endpoint as untrusted input even though it was validated
at subscription time: every delivery re-validates the host, never follows
redirects, caps the response body and logs only stable result codes. Endpoint
URLs, VAPID JWTs, browser keys and payloads are never logged.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from config.web_push_keys import WebPushKeyError

from .crypto import (
    build_vapid_jwt,
    encrypt_push_payload,
    load_vapid_private_key,
    load_vapid_public_key,
)

logger = logging.getLogger(__name__)

PUSH_TIMEOUT_SECONDS = 10.0
PUSH_RESPONSE_BODY_LIMIT = 4096
_HTTPS_PORT = 443
_EXACT_HOSTS = frozenset(
    {
        "fcm.googleapis.com",
        "push.microsoft.com",
        "push.services.mozilla.com",
        "updates.push.services.mozilla.com",
        "web.push.apple.com",
    }
)
_SUFFIX_HOSTS = (
    ".notify.windows.com",
    ".push.apple.com",
)


class PushDeliveryStatus(StrEnum):
    SENT = "sent"
    GONE = "gone"
    RETRY = "retry"
    TERMINAL = "terminal"


@dataclass(frozen=True)
class PushSendResult:
    status: PushDeliveryStatus
    error_code: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status == PushDeliveryStatus.SENT


class PushEndpointError(ValueError):
    """Raised when an endpoint is not an allowlisted HTTPS push service."""


def validate_endpoint(value: str) -> str:
    """Validate and normalize a push endpoint or raise ``PushEndpointError``."""

    raw = str(value or "").strip()
    if not raw or any(char.isspace() for char in raw):
        raise PushEndpointError("endpoint_invalid")
    try:
        parsed = urlsplit(raw)
    except ValueError as exc:
        raise PushEndpointError("endpoint_invalid") from exc
    if parsed.scheme.lower() != "https":
        raise PushEndpointError("endpoint_not_https")
    if parsed.username is not None or parsed.password is not None:
        raise PushEndpointError("endpoint_userinfo")
    if parsed.fragment:
        raise PushEndpointError("endpoint_fragment")
    try:
        port = parsed.port
    except ValueError as exc:
        raise PushEndpointError("endpoint_port") from exc
    if port not in (None, _HTTPS_PORT):
        raise PushEndpointError("endpoint_port")
    host = str(parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise PushEndpointError("endpoint_host")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise PushEndpointError("endpoint_ip_literal")
    if host not in _EXACT_HOSTS and not any(
        host.endswith(suffix) and host != suffix[1:] for suffix in _SUFFIX_HOSTS
    ):
        raise PushEndpointError("endpoint_host_not_allowed")
    return urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))


def endpoint_origin(endpoint: str) -> str:
    parsed = urlsplit(validate_endpoint(endpoint))
    return f"{parsed.scheme}://{parsed.netloc}".lower()


class WebPushSender:
    def __init__(
        self,
        settings: Any,
        *,
        http_session: aiohttp.ClientSession | None = None,
        timeout_seconds: float = PUSH_TIMEOUT_SECONDS,
    ) -> None:
        self.settings = settings
        self._http_session = http_session
        self._owns_session = http_session is None
        self._timeout_seconds = timeout_seconds

    def _session(self) -> aiohttp.ClientSession:
        if self._http_session is None:
            self._http_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._timeout_seconds),
                trust_env=False,
            )
        return self._http_session

    async def close(self) -> None:
        if self._http_session is not None and self._owns_session:
            await self._http_session.close()
        self._http_session = None

    async def send(
        self,
        *,
        endpoint: str,
        subscription_keys: dict[str, str],
        payload: bytes,
        now: int | None = None,
    ) -> PushSendResult:
        try:
            normalized_endpoint = validate_endpoint(endpoint)
            private = load_vapid_private_key(
                _secret_value(self.settings.WEB_PUSH_VAPID_PRIVATE_KEY)
            )
            public = load_vapid_public_key(str(self.settings.WEB_PUSH_VAPID_PUBLIC_KEY or ""))
            jwt = build_vapid_jwt(
                private,
                public,
                str(self.settings.WEB_PUSH_VAPID_SUBJECT or ""),
                endpoint_origin(normalized_endpoint),
                now=now,
            )
            body = encrypt_push_payload(subscription_keys, payload)
        except (PushEndpointError, WebPushKeyError, ValueError):
            return PushSendResult(PushDeliveryStatus.TERMINAL, "configuration_invalid")

        ttl = int(self.settings.WEB_PUSH_DELIVERY_TTL_SECONDS or 0)
        public_key = str(self.settings.WEB_PUSH_VAPID_PUBLIC_KEY or "")
        headers = {
            "Authorization": f"vapid t={jwt}, k={public_key}",
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": str(max(0, min(86400, ttl))),
            "Urgency": "normal",
        }
        try:
            async with self._session().post(
                normalized_endpoint,
                data=body,
                headers=headers,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=self._timeout_seconds),
            ) as response:
                # Read at most the cap, even when the push service lies about
                # (or omits) Content-Length.
                await response.content.read(PUSH_RESPONSE_BODY_LIMIT)
                return _result_for_status(response.status)
        except asyncio.CancelledError:
            raise
        except (TimeoutError, aiohttp.ClientError, OSError):
            return PushSendResult(PushDeliveryStatus.RETRY, "network_error")


def _result_for_status(status: int) -> PushSendResult:
    if 200 <= status < 300:
        return PushSendResult(PushDeliveryStatus.SENT)
    if status in {404, 410}:
        return PushSendResult(PushDeliveryStatus.GONE, "endpoint_gone")
    if status in {400, 401, 403, 413}:
        return PushSendResult(PushDeliveryStatus.TERMINAL, f"http_{status}")
    if status == 429 or 500 <= status < 600:
        return PushSendResult(PushDeliveryStatus.RETRY, f"http_{status}")
    return PushSendResult(PushDeliveryStatus.TERMINAL, f"http_{status}")


def _secret_value(value: Any) -> str:
    getter = getattr(value, "get_secret_value", None)
    if callable(getter):
        return str(getter())
    return str(value or "")


__all__ = [
    "PUSH_RESPONSE_BODY_LIMIT",
    "PUSH_TIMEOUT_SECONDS",
    "PushDeliveryStatus",
    "PushEndpointError",
    "PushSendResult",
    "WebPushSender",
    "endpoint_origin",
    "validate_endpoint",
]
