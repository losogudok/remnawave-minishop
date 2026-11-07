"""Leased GA4 Measurement Protocol delivery worker."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, Protocol

import aiohttp

from bot.plugins.spec import PluginContext

from . import client, store
from .config import ga4_configured

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 5
GA4_RUNTIME_SETTING_KEYS = {
    "GA4_API_SECRET",
    "GA4_DEBUG_MODE",
    "GA4_ENABLED",
    "GA4_MEASUREMENT_ID",
}


class EventSender(Protocol):
    async def __call__(
        self,
        session: Any,
        *,
        settings: Any,
        client_id: str,
        name: str,
        params: dict[str, Any] | None,
        occurred_at: datetime | None,
        now: datetime | None = None,
    ) -> client.DeliveryOutcome: ...


class Ga4FunnelWorker:
    def __init__(
        self,
        ctx: PluginContext,
        *,
        sender: EventSender | None = None,
        http_session: Any | None = None,
    ) -> None:
        self.ctx = ctx
        self.sender: EventSender = sender or client.send_event
        self.http_session = http_session
        self._owns_session = http_session is None

    async def _session(self) -> Any:
        if self.http_session is None:
            self.http_session = aiohttp.ClientSession()
        return self.http_session

    async def aclose(self) -> None:
        if self.http_session is not None and self._owns_session:
            await self.http_session.close()
        self.http_session = None

    async def run(self) -> None:
        try:
            while True:
                try:
                    await self.tick()
                except Exception:
                    logger.warning("GA4 funnel delivery cycle failed")
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
        finally:
            await self.aclose()

    async def tick(self, *, now: datetime | None = None) -> int:
        factory = self.ctx.session_factory
        if factory is None:
            return 0
        from bot.services.settings_override_service import refresh_overrides_from_db

        await refresh_overrides_from_db(
            self.ctx.settings,
            factory,
            keys=GA4_RUNTIME_SETTING_KEYS,
        )
        if not ga4_configured(self.ctx.settings):
            return 0
        delivered = 0
        # Lease one event at a time: a large batch would spend its lease just
        # waiting behind earlier 5-second HTTP calls and invite double sends.
        for _ in range(store.MAX_BATCH_SIZE):
            current = now or datetime.now(UTC)
            claimed = await store.claim_batch(factory, now=current, limit=1)
            if not claimed:
                break
            for event in claimed:
                try:
                    await self._deliver(event)
                except Exception:
                    logger.warning("GA4 funnel could not deliver event %s", event.id)
            delivered += len(claimed)
        return delivered

    async def _deliver(self, event: store.ClaimedEvent, *, now: datetime | None = None) -> None:
        factory = self.ctx.require_session_factory()

        def clock() -> datetime:
            return now or datetime.now(UTC)

        if not await store.lease_is_owned(factory, event=event, now=clock()):
            # Another worker reclaimed the row; never send without a live lease.
            return
        if event.attempts > event.max_attempts:
            await store.mark_failed(factory, event=event, error_code="attempts_exhausted")
            return
        client_id = event.client_id
        if client_id is None and event.user_id is not None:
            client_id = await store.latest_client_id(factory, event.user_id)
        if not client_id:
            await store.defer_without_binding(factory, event=event, now=clock())
            return
        session = await self._session()
        # Everything above may await storage/network setup. Re-check and renew
        # immediately before sending so a stale claim can never double-send.
        if not await store.renew_lease(factory, event=event, now=clock()):
            return
        outcome = await self.sender(
            session,
            settings=self.ctx.settings,
            client_id=client_id,
            name=event.name,
            params=event.params,
            occurred_at=event.occurred_at,
        )
        if outcome.sent:
            await store.mark_sent(factory, event=event, now=clock())
        elif outcome.retryable:
            await store.mark_retry(
                factory,
                event=event,
                error_code=outcome.error_code or "delivery_failed",
                now=clock(),
            )
        else:
            await store.mark_failed(
                factory,
                event=event,
                error_code=outcome.error_code or "delivery_failed",
            )


__all__ = [
    "GA4_RUNTIME_SETTING_KEYS",
    "POLL_INTERVAL_SECONDS",
    "Ga4FunnelWorker",
]
