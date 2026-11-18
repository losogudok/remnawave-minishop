"""Provider-bound, one-use challenges; Redis failure never permits a local fallback."""

import asyncio
import re
import secrets
from typing import Protocol, cast

from bot.infra.redis import get_redis, redis_key
from config.settings import Settings

from .native_auth_contracts import NativeProvider

TTL_SECONDS = 300
_VALUE = re.compile(r"[A-Za-z0-9_-]{43}")
_CONSUME = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


class ChallengeRedis(Protocol):
    async def set(self, name: str, value: str, *, ex: int, nx: bool) -> object: ...
    async def get(self, name: str) -> object: ...
    async def eval(self, script: str, numkeys: int, *args: str) -> object: ...


def challenge_key(settings: Settings, provider: NativeProvider, request_id: str) -> str:
    if not _VALUE.fullmatch(request_id):
        raise ValueError("invalid_request_id")
    return redis_key(settings, "auth", "native", provider, request_id)


async def _client(settings: Settings) -> ChallengeRedis:
    client = await get_redis(settings)
    if client is None:
        raise RuntimeError("native_auth_unavailable")
    return cast(ChallengeRedis, client)


async def issue(settings: Settings, provider: NativeProvider) -> tuple[str, str]:
    async with asyncio.timeout(2):
        client = await _client(settings)
        request_id, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        if not await client.set(
            challenge_key(settings, provider, request_id), nonce, ex=TTL_SECONDS, nx=True
        ):
            raise RuntimeError("native_auth_unavailable")
        return request_id, nonce


async def peek(settings: Settings, provider: NativeProvider, request_id: str) -> str | None:
    async with asyncio.timeout(2):
        client = await _client(settings)
        nonce = await client.get(challenge_key(settings, provider, request_id))
        return nonce if isinstance(nonce, str) and _VALUE.fullmatch(nonce) else None


async def consume(
    settings: Settings, provider: NativeProvider, request_id: str, nonce: str
) -> bool:
    async with asyncio.timeout(2):
        client = await _client(settings)
        # Compare-and-delete also checks expiry at consumption, after JWT validation.
        return bool(
            await client.eval(_CONSUME, 1, challenge_key(settings, provider, request_id), nonce)
        )
