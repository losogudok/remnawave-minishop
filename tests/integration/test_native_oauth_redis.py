"""One-use native challenges and rate windows against disposable, real Redis."""

import asyncio
import os
import uuid
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from redis.asyncio import Redis

from bot.app.web.webapp import native_auth_challenges as challenges
from config.settings import Settings
from tests.support.settings_stub import settings_stub

REDIS_URL = os.environ.get("CORE_PERFORMANCE_TEST_REDIS_URL", "")
pytestmark = pytest.mark.skipif(not REDIS_URL, reason="Disposable Redis URL is required")


def test_native_challenge_consumption_is_atomic_provider_bound_and_checks_expiry() -> None:
    async def run() -> None:
        settings = cast(Settings, settings_stub(REDIS_KEY_PREFIX="native-test-" + uuid.uuid4().hex))
        first = Redis.from_url(REDIS_URL, decode_responses=True)
        second = Redis.from_url(REDIS_URL, decode_responses=True)
        keys: list[str] = []
        try:
            with patch.object(challenges, "get_redis", AsyncMock(return_value=first)):
                request_id, nonce = await challenges.issue(settings, "google")
                key = challenges.challenge_key(settings, "google", request_id)
                keys.append(key)
                assert 0 < await first.ttl(key) <= 300
                assert await challenges.peek(settings, "telegram", request_id) is None
                assert not await challenges.consume(settings, "google", request_id, "wrong")
                # Independent connections represent two backend replicas.
                results = await asyncio.gather(
                    first.eval(challenges._CONSUME, 1, key, nonce),
                    second.eval(challenges._CONSUME, 1, key, nonce),
                )
                assert sorted(results) == [0, 1]
                assert not await challenges.consume(settings, "google", request_id, nonce)
                request_id, nonce = await challenges.issue(settings, "telegram")
                key = challenges.challenge_key(settings, "telegram", request_id)
                keys.append(key)
                assert await challenges.peek(settings, "telegram", request_id) == nonce
                await first.pexpire(key, 1)
                await asyncio.sleep(0.02)
                assert not await challenges.consume(settings, "telegram", request_id, nonce)
                assert await challenges.peek(settings, "telegram", request_id) is None
        finally:
            try:
                if keys:
                    await first.delete(*keys)
            finally:
                await first.aclose()
                await second.aclose()

    asyncio.run(run())
