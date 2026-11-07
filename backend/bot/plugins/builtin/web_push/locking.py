"""Serialize subscription authorization and ownership changes through HTTP send.

Lock order is canonical: enrollment takes the endpoint-hash lock first and, if
the row already exists, then the subscription-id lock. Worker sends, reassigns
and revocations take only the subscription-id lock and never wait for an
endpoint lock afterwards. This prevents a lock-order cycle.

PostgreSQL uses transaction-scoped advisory locks so backend and worker
processes agree. The SQLite fallback uses asyncio locks and therefore only
coordinates tasks inside one process; SQLite is not supported for a deployment
with separate backend and worker processes.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from weakref import WeakValueDictionary

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


def _lock_name(key: str) -> str:
    return f"web_push_subscription:{key}"


class SubscriptionGuard:
    """A session that can add canonical subscription locks in fixed order."""

    def __init__(self, session: AsyncSession, *, postgres: bool) -> None:
        self.session = session
        self._postgres = postgres
        self._process_locks: list[asyncio.Lock] = []
        self._keys: set[str] = set()

    async def acquire(self, key: str) -> None:
        name = _lock_name(str(key))
        if name in self._keys:
            return
        self._keys.add(name)
        if self._postgres:
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key), 0)"),
                {"key": name},
            )
            return
        lock = _locks.setdefault(name, asyncio.Lock())
        await lock.acquire()
        self._process_locks.append(lock)

    async def release(self) -> None:
        while self._process_locks:
            self._process_locks.pop().release()
        self._keys.clear()


@asynccontextmanager
async def subscription_transaction(
    factory: sessionmaker, first_key: str
) -> AsyncIterator[SubscriptionGuard]:
    async with factory() as session:
        postgres = session.get_bind().dialect.name == "postgresql"
        guard = SubscriptionGuard(session, postgres=postgres)
        if postgres:
            async with session.begin():
                await guard.acquire(first_key)
                yield guard
            return
        await guard.acquire(first_key)
        try:
            async with session.begin():
                yield guard
        finally:
            await guard.release()
