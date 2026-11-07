"""GA4 Measurement Protocol delivery (fixed Google endpoint only)."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import aiohttp

from config.settings import Settings

from .config import (
    COLLECT_PATH,
    MEASUREMENT_HOST,
    api_secret,
    build_event_body,
    normalize_measurement_id,
)

REQUEST_TIMEOUT_SECONDS = 5.0
RESPONSE_BODY_LIMIT_BYTES = 4096


class MeasurementResponse(Protocol):
    status: int

    async def read(self, n: int = -1) -> bytes: ...

    def release(self) -> Any: ...


class MeasurementSession(Protocol):
    def post(self, url: str, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class DeliveryOutcome:
    sent: bool
    retryable: bool
    error_code: str | None = None
    status: int | None = None


def _classify(response: MeasurementResponse) -> DeliveryOutcome:
    status = int(response.status)
    if 200 <= status < 300:
        return DeliveryOutcome(sent=True, retryable=False, status=status)
    if status == 429:
        return DeliveryOutcome(sent=False, retryable=True, error_code="http_429", status=status)
    if 500 <= status < 600:
        return DeliveryOutcome(sent=False, retryable=True, error_code="http_5xx", status=status)
    if 300 <= status < 400:
        return DeliveryOutcome(
            sent=False, retryable=False, error_code="redirect_rejected", status=status
        )
    return DeliveryOutcome(sent=False, retryable=False, error_code="http_4xx", status=status)


async def send_event(
    session: MeasurementSession,
    *,
    settings: Settings,
    client_id: str,
    name: str,
    params: dict[str, Any] | None,
    occurred_at: datetime | None,
    now: datetime | None = None,
) -> DeliveryOutcome:
    """Deliver one event; never raises and never logs credentials or the URL."""

    measurement_id = normalize_measurement_id(settings.GA4_MEASUREMENT_ID)
    secret = api_secret(settings)
    if measurement_id is None or secret is None:
        return DeliveryOutcome(sent=False, retryable=False, error_code="not_configured")

    body = build_event_body(
        client_id=client_id,
        name=name,
        params=params,
        occurred_at=occurred_at,
        now=now,
    )
    response: MeasurementResponse | None = None
    try:
        response = await session.post(
            f"{MEASUREMENT_HOST}{COLLECT_PATH}",
            params={"measurement_id": measurement_id, "api_secret": secret},
            json=body,
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
            allow_redirects=False,
        )
        with contextlib.suppress(Exception):
            await response.read(RESPONSE_BODY_LIMIT_BYTES)
        return _classify(response)
    except Exception:
        # No repr: provider/network exceptions can contain the query string.
        return DeliveryOutcome(sent=False, retryable=True, error_code="network_error")
    finally:
        if response is not None:
            with contextlib.suppress(Exception):
                response.release()


__all__ = [
    "REQUEST_TIMEOUT_SECONDS",
    "RESPONSE_BODY_LIMIT_BYTES",
    "DeliveryOutcome",
    "MeasurementResponse",
    "MeasurementSession",
    "send_event",
]
