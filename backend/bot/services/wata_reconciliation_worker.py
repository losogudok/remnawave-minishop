from __future__ import annotations

import asyncio
import contextlib
import logging
import time

from sqlalchemy.orm import sessionmaker

from bot.infra.redis import redis_lock
from bot.payment_providers.shared.common import detached_payment_snapshot
from bot.payment_providers.wata.service import WataService
from config.settings import Settings
from db.dal import payment_dal, wata_reconciliation_dal

logger = logging.getLogger(__name__)

WATA_RECONCILIATION_LOCK = "wata-payment-reconciliation"
DEFAULT_WATA_RECONCILIATION_TICK_SECONDS = 60
DEFAULT_WATA_RECONCILIATION_GRACE_SECONDS = 30
DEFAULT_WATA_RECONCILIATION_BATCH_SIZE = 100


class WataReconciliationWorker:
    """Finalize Wata links whose configured hosted-checkout lifetime elapsed."""

    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker,
        wata_service: WataService,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.wata_service = wata_service
        self._stopped = asyncio.Event()

    async def run(self) -> None:
        if not self.wata_service.configured:
            logger.info("Wata reconciliation worker disabled: provider is not configured")
            return

        while not self._stopped.is_set():
            try:
                async with redis_lock(
                    self.settings,
                    WATA_RECONCILIATION_LOCK,
                    ttl_seconds=max(60, self._tick_seconds()),
                ) as acquired:
                    if not acquired:
                        logger.info("Wata reconciliation tick skipped: Redis lock is held")
                    else:
                        started = time.monotonic()
                        await self.tick()
                        logger.info(
                            "metric worker_tick_duration_seconds=%.3f worker=wata_reconciliation",
                            time.monotonic() - started,
                        )
            except Exception:
                logger.exception("Wata reconciliation worker tick failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stopped.wait(), timeout=self._tick_seconds())

    def stop(self) -> None:
        self._stopped.set()

    def _tick_seconds(self) -> int:
        return DEFAULT_WATA_RECONCILIATION_TICK_SECONDS

    def _grace_seconds(self) -> int:
        return DEFAULT_WATA_RECONCILIATION_GRACE_SECONDS

    def _batch_size(self) -> int:
        return DEFAULT_WATA_RECONCILIATION_BATCH_SIZE

    async def tick(self) -> None:
        async with self.session_factory() as session:
            payments = await wata_reconciliation_dal.list_candidates(
                session,
                limit=self._batch_size(),
                grace_seconds=self._grace_seconds(),
            )
            candidates = [detached_payment_snapshot(payment) for payment in payments]

        for candidate in candidates:
            if not self.wata_service.payment_link_expired_locally(
                candidate,
                grace_seconds=self._grace_seconds(),
            ):
                continue
            try:
                await self._reconcile_candidate(candidate.payment_id)
            except Exception:
                logger.exception("Failed to reconcile Wata payment %s", candidate.payment_id)

    async def _reconcile_candidate(self, payment_id: int) -> None:
        async with self.session_factory() as session:
            payment = await payment_dal.get_payment_by_db_id(
                session,
                payment_id,
                fresh=True,
            )
            if payment is None:
                return
            payment_snapshot = detached_payment_snapshot(payment)
            if not self.wata_service.payment_link_expired_locally(
                payment_snapshot,
                grace_seconds=self._grace_seconds(),
            ):
                return
            await session.rollback()
            await self.wata_service.refresh_payment_status(session, payment_snapshot)
