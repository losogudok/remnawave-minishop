"""Bounded background cleanup for unreferenced guide media files."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import sessionmaker

from . import service, storage

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 3600
DEFAULT_MIN_AGE_SECONDS = 3600
DEFAULT_BATCH_SIZE = 100


class GuideMediaJanitor:
    def __init__(
        self,
        session_factory: sessionmaker | None,
        *,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        min_age_seconds: int = DEFAULT_MIN_AGE_SECONDS,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self.session_factory = session_factory
        self.interval_seconds = max(1, int(interval_seconds))
        self.min_age_seconds = max(0, int(min_age_seconds))
        self.batch_size = max(1, int(batch_size))
        self._scanner = storage.StaleDigestScanner()
        self._run_lock = asyncio.Lock()

    async def run(self) -> None:
        try:
            while True:
                try:
                    await self.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Guide media janitor failed; retrying next cycle")
                await asyncio.sleep(self.interval_seconds)
        finally:
            await asyncio.to_thread(self._scanner.close)

    async def run_once(self) -> int:
        async with self._run_lock:
            return await self._run_once()

    async def _run_once(self) -> int:
        if self.session_factory is None:
            return 0
        digests = await self._scanner.stale_digests(
            older_than_seconds=self.min_age_seconds,
            limit=self.batch_size,
        )
        removed = 0
        for digest in digests:
            async with self.session_factory() as session:
                try:
                    if await service.cleanup_unreferenced_digest(session, digest):
                        removed += 1
                except Exception:
                    logger.exception("Guide media janitor failed to clean %s", digest)
        return removed
