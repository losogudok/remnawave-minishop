from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest

from bot.services.panel_api_service import PanelApiService
from tests.support.settings_stub import settings_stub

REPO_ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    os.getenv("QA_FULLSTACK") != "1",
    reason="requires the live Docker full-stack QA stand",
)


def _read_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key] = value
    return result


def _panel_service() -> PanelApiService:
    env = _read_env(REPO_ROOT / ".env.remnawave-dev")
    api_key = os.getenv("QA_REMNAWAVE_API_TOKEN") or env["REMNAWAVE_DEV_API_TOKEN"]
    api_url = os.getenv("QA_REMNAWAVE_API_URL", "http://127.0.0.1:3000/api")
    return PanelApiService(
        settings_stub(
            PANEL_API_URL=api_url,
            PANEL_API_KEY=api_key,
            PANEL_WRITE_MODE="live",
            PANEL_ALL_USERS_CACHE_TTL_SECONDS=0,
            PANEL_USER_CACHE_TTL_SECONDS=0,
            PANEL_DEVICES_CACHE_TTL_SECONDS=0,
            REDIS_URL=None,
        )
    )


async def _exercise_panel_contract() -> None:
    expected_version = os.environ["QA_REMNAWAVE_PRESET"]
    service = _panel_service()
    username = f"qa_{uuid.uuid4().hex[:16]}"
    user_ref: str | None = None
    try:
        compatibility = await service.get_panel_api_compatibility(force_refresh=True)
        assert compatibility.version is not None
        assert compatibility.version.lstrip("v").startswith(expected_version)
        assert compatibility.support_status in {"current", "maintenance"}

        users = await service.get_all_panel_users(log_responses=False)
        assert users is not None
        assert {str(user.get("username")) for user in users} >= {
            "runes_admin",
            "runes_active",
            "runes_expired",
        }
        assert all(str(user.get("uuid") or "").strip() for user in users)

        squads = await service.get_internal_squads()
        assert squads
        squad_uuid = str(squads[0]["uuid"])

        created = await service.create_panel_user(
            username_on_panel=username,
            email=f"{username}@example.test",
            telegram_id=990000000 + int(uuid.uuid4().hex[:5], 16),
            default_expire_days=2,
            default_traffic_limit_bytes=1024,
            default_traffic_limit_strategy="NO_RESET",
        )
        assert created and not created.get("error"), created
        panel_user = created.get("response")
        assert isinstance(panel_user, dict)
        user_ref = str(panel_user.get("uuid") or "")
        assert user_ref

        found = await service.get_users_by_filter(username=username)
        assert found and found[0]["uuid"] == user_ref

        updated = await service.update_user_details_on_panel(
            user_ref,
            {"description": "core compatibility live smoke"},
        )
        assert updated and updated.get("description") == "core compatibility live smoke"

        assert await service.add_users_to_internal_squad(squad_uuid, [user_ref])
        with_squad = await service.get_user_by_uuid(user_ref, use_cache=False)
        assert with_squad is not None
        active_squads = with_squad.get("activeInternalSquads") or []
        assert squad_uuid in {
            str(item.get("uuid") if isinstance(item, dict) else item) for item in active_squads
        }

        assert await service.remove_users_from_internal_squad(squad_uuid, [user_ref])
    finally:
        if user_ref:
            assert await service.delete_user_from_panel(user_ref)
        await service.close_session()


def test_live_panel_read_write_contract() -> None:
    asyncio.run(_exercise_panel_contract())
