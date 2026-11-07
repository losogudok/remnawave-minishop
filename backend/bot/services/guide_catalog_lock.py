"""Synchronize local catalog settings mutations with catalog-dependent commits."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from functools import wraps
from weakref import WeakKeyDictionary

_LOCKS: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = WeakKeyDictionary()


def guide_catalog_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _LOCKS.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[loop] = lock
    return lock


def serialize_catalog_changes[**P, T](
    function: Callable[P, Awaitable[T]],
) -> Callable[P, Awaitable[T]]:
    @wraps(function)
    async def guarded(*args: P.args, **kwargs: P.kwargs) -> T:
        async with guide_catalog_lock():
            return await function(*args, **kwargs)

    return guarded
