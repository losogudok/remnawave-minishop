"""Process-wide and database transaction locks for guide media mutations."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_PROCESS_LOCKS: dict[tuple[int, str], asyncio.Lock] = {}


def _process_lock(lock_key: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    key = (id(loop), lock_key)
    lock = _PROCESS_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _PROCESS_LOCKS[key] = lock
    return lock


@asynccontextmanager
async def advisory_xact_lock(session: AsyncSession, lock_key: str) -> AsyncIterator[None]:
    """Serialize a critical section across workers or in-process sessions.

    PostgreSQL uses a transaction-scoped advisory lock, so the caller must keep
    the transaction open for the whole critical section. SQLite and other
    backends fall back to a process-local asyncio lock.
    """

    bind = session.bind
    dialect = str(getattr(getattr(bind, "dialect", None), "name", ""))
    if dialect == "postgresql":
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": lock_key},
        )
        yield
        return
    async with _process_lock(lock_key):
        yield


def gallery_lock_key(config_id: str, platform: str, client_id: str) -> str:
    return f"guide_media:gallery:{config_id}:{platform}:{client_id}"


def digest_lock_key(digest: str) -> str:
    return f"guide_media:digest:{digest}"
