import asyncio
import base64
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from bot.app.web.admin_api_impl import users_login_script as routes
from bot.app.web.webapp_auth import create_webapp_session_token, verify_webapp_session_token
from config.settings import Settings
from db.models import User


def test_session_ttl_override_does_not_change_normal_sessions() -> None:
    settings = Settings()
    default_ttl = settings.WEBAPP_SESSION_TTL_SECONDS
    token = create_webapp_session_token(settings, 68, ttl_seconds=86400)
    payload = json.loads(base64.urlsafe_b64decode(token.split(".")[0] + "==="))
    assert payload["exp"] - payload["iat"] == 86400
    assert default_ttl == settings.WEBAPP_SESSION_TTL_SECONDS
    assert verify_webapp_session_token(settings, token) == 68
    normal = create_webapp_session_token(settings, 68)
    payload = json.loads(base64.urlsafe_b64decode(normal.split(".")[0] + "==="))
    assert payload["exp"] - payload["iat"] == default_ttl


@pytest.mark.parametrize(
    ("target", "roles", "status"),
    [
        (None, [], 404),
        (User(user_id=68, is_banned=True), [], 403),
        (User(user_id=68, is_banned=False), [True, False], 403),
        (User(user_id=68, is_banned=False), [False], 200),
        (User(user_id=68, is_banned=False), [True, True], 200),
    ],
)
def test_login_script_access_and_audit(
    monkeypatch: pytest.MonkeyPatch, target: User | None, roles: list[bool], status: int
) -> None:
    async def run() -> None:
        settings = Settings(SUBSCRIPTION_MINI_APP_URL="https://app.example.test/")
        session = AsyncMock()
        session.__aenter__.return_value = session
        factory = MagicMock(return_value=session)
        monkeypatch.setattr(routes, "_require_admin_user_id", lambda request: 7)
        monkeypatch.setattr(routes, "get_settings", lambda request: settings)
        monkeypatch.setattr(routes, "get_session_factory", lambda request: factory)
        monkeypatch.setattr(routes.user_dal, "get_user_by_id", AsyncMock(return_value=target))
        monkeypatch.setattr(routes, "has_role", AsyncMock(side_effect=roles))
        audit = AsyncMock()
        monkeypatch.setattr(routes.message_log_dal, "create_message_log", audit)
        request = make_mocked_request(
            "POST", "/api/admin/users/68/login-script", match_info={"user_id": "68"}
        )
        response = await routes.admin_user_login_script_route(request)
        assert response.status == status
        if status != 200:
            audit.assert_not_awaited()
            session.commit.assert_not_awaited()
            return
        payload = json.loads(response.text or "")
        assert payload["ok"] is True
        assert payload["expires_in"] == 86400
        assert response.headers["Cache-Control"] == "no-store"
        assert 'location.origin !== "https://app.example.test"' in payload["script"]
        assert "rw_webapp_session=" in payload["script"]
        assert "rw_webapp_csrf=" in payload["script"]
        assert "Max-Age=86400; Secure; SameSite=None" in payload["script"]
        audit.assert_awaited_once()
        data = audit.call_args.args[1]
        assert data["user_id"] == 7
        assert data["target_user_id"] == 68
        assert "rw_webapp_session" not in data["content"]
        session.commit.assert_awaited_once()

    asyncio.run(run())


def test_unauthenticated_cannot_issue_session(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        from bot.app.web import session

        monkeypatch.setattr(session, "extract_authenticated_user_id", lambda request: None)
        request = make_mocked_request(
            "POST", "/api/admin/users/68/login-script", match_info={"user_id": "68"}
        )
        with pytest.raises(web.HTTPUnauthorized):
            await routes.admin_user_login_script_route(request)

    asyncio.run(run())


def test_expired_override_session_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from bot.app.web import webapp_auth

    settings = Settings()
    monkeypatch.setattr(webapp_auth.time, "time", lambda: 1000)
    token = create_webapp_session_token(settings, 68, ttl_seconds=86400)
    monkeypatch.setattr(webapp_auth.time, "time", lambda: 87401)
    assert verify_webapp_session_token(settings, token) is None


def test_non_admin_cannot_issue_session(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        from bot.app.web import session

        monkeypatch.setattr(session, "extract_authenticated_user_id", lambda request: 7)
        request = make_mocked_request(
            "POST", "/api/admin/users/68/login-script", match_info={"user_id": "68"}
        )
        with pytest.raises(web.HTTPForbidden):
            await routes.admin_user_login_script_route(request)

    asyncio.run(run())


def test_missing_public_https_url_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        monkeypatch.setattr(routes, "_require_admin_user_id", lambda request: 7)
        settings = Settings(SUBSCRIPTION_MINI_APP_URL="http://app.example.test/")
        monkeypatch.setattr(routes, "get_settings", lambda request: settings)
        request = make_mocked_request(
            "POST", "/api/admin/users/68/login-script", match_info={"user_id": "68"}
        )
        response = await routes.admin_user_login_script_route(request)
        assert response.status == 400

    asyncio.run(run())
