import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from sqlalchemy.ext.asyncio import AsyncSession

from bot.app.web.webapp import common
from db.models import User, UserTelegramAvatar


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("force_refresh", [False, True])
def test_avatar_refresh_without_telegram_bot(cached: bool, force_refresh: bool) -> None:
    request = make_mocked_request("GET", "/api/me?fresh=1", app=web.Application())
    session = AsyncMock(spec=AsyncSession)
    user = User(user_id=42, telegram_id=42)
    avatar = (
        UserTelegramAvatar(
            user_id=42,
            image_bytes=b"cached-avatar",
            content_type="image/jpeg",
            updated_at=datetime.now(UTC) - timedelta(days=365),
        )
        if cached
        else None
    )
    with (
        patch.object(common.user_dal, "get_user_telegram_avatar", AsyncMock(return_value=avatar)),
        patch.object(common, "_fetch_telegram_avatar", AsyncMock()) as fetch,
        patch.object(common.user_dal, "upsert_user_telegram_avatar", AsyncMock()) as upsert,
        patch.object(common, "_AVATAR_RETRY_AFTER", {}),
    ):
        result = asyncio.run(
            common._ensure_cached_telegram_avatar(
                request, session, user, force_refresh=force_refresh
            )
        )
    assert result is avatar
    fetch.assert_not_awaited()
    upsert.assert_not_awaited()
