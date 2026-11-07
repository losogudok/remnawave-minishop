"""Seeded public instructions work without an authenticated browser session.

This file follows test_remnawave_upgrade so same-database upgrade QA can run
its identity reconciliation before inspecting the migrated public links.
Fresh-stand QA exercises the links directly from the seed.
"""

from __future__ import annotations

import asyncio
import os
import re

import asyncpg
import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("QA_FULLSTACK") != "1", reason="requires the live Docker full-stack QA stand"
)


async def seed_share_token(user_id: int) -> str:
    connection = await asyncpg.connect(
        os.getenv(
            "QA_DB_DSN",
            "postgresql://remnawave_minishop:remnawave_minishop@127.0.0.1:6768/remnawave_minishop",
        )
    )
    try:
        token = await connection.fetchval(
            "SELECT install_share_token FROM subscriptions "
            "WHERE user_id=$1 AND provider='dev_seed' ORDER BY subscription_id LIMIT 1",
            user_id,
        )
        assert isinstance(token, str) and re.fullmatch(r"[a-f0-9]{32}", token)
        return token
    finally:
        await connection.close()


@pytest.mark.parametrize(
    "user_id,expected_status", [(910000001, 200), (910000002, 200), (910000003, 404)]
)
def test_seeded_guides_work_without_a_session(user_id: int, expected_status: int) -> None:
    token = asyncio.run(seed_share_token(user_id))
    base_url = os.getenv("QA_FRONTEND_URL", "http://127.0.0.1:18082")
    with httpx.Client(base_url=base_url, timeout=10, follow_redirects=False) as client:
        page = client.get(f"/s/{token}")
        assert page.status_code == expected_status
        document = client.get(f"/api/subscription-guides/public/{token}")
        assert document.status_code == expected_status
        payload = document.json()
        if expected_status == 200:
            assert payload["ok"] is True and payload["enabled"] is True
            assert payload["guide_document"]["platforms"]
            assert document.headers["Cache-Control"] == "private, no-store"
        else:
            assert payload["error"] == "subscription_unavailable"
