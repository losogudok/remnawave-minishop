"""Issue an audited, time-limited cabinet session for admin troubleshooting."""

import json
import secrets
from urllib.parse import urlsplit

from aiohttp import web

from bot.app.web.context import get_session_factory, get_settings
from bot.app.web.http_contracts import HttpResponseModel
from bot.app.web.route_contracts import RouteContract, ok_envelope_for, register_contract
from bot.app.web.session import WEBAPP_SESSION_COOKIE_NAME
from bot.app.web.webapp.constants import WEBAPP_CSRF_COOKIE_NAME
from bot.app.web.webapp_auth import create_webapp_session_token
from bot.services.account_roles import ROLE_OWNER, has_role
from db.dal import message_log_dal, user_dal

from .auth import _require_admin_user_id
from .common import _error, _ok

LOGIN_SESSION_TTL_SECONDS = 24 * 60 * 60


class AdminUserLoginScriptOut(HttpResponseModel):
    script: str
    expires_in: int


register_contract(
    "admin_user_login_script_route",
    RouteContract(
        response_schema=ok_envelope_for(AdminUserLoginScriptOut),
        models=(AdminUserLoginScriptOut,),
    ),
)


def build_login_script(origin: str, token: str, csrf: str) -> str:
    """JSON-quote all values; run only on the intended origin in a fresh profile."""
    cookies = [
        f"{name}={value}; Path=/; Max-Age={LOGIN_SESSION_TTL_SECONDS}; Secure; SameSite=None"
        for name, value in (
            (WEBAPP_SESSION_COOKIE_NAME, token),
            (WEBAPP_CSRF_COOKIE_NAME, csrf),
        )
    ]
    return (
        "(() => {\n"
        f"  if (location.origin !== {json.dumps(origin)}) throw new Error();\n"
        + "".join(f"  document.cookie = {json.dumps(cookie)};\n" for cookie in cookies)
        + '  location.assign("/");\n})();'
    )


async def admin_user_login_script_route(request: web.Request) -> web.Response:
    actor_id = _require_admin_user_id(request)
    target_id = int(request.match_info["user_id"])
    settings = get_settings(request)
    public_url = urlsplit(settings.SUBSCRIPTION_MINI_APP_URL or "")
    if public_url.scheme != "https" or not public_url.hostname:
        return _error(400, "login_script_url_unavailable")
    origin = f"{public_url.scheme}://{public_url.netloc}"

    async with get_session_factory(request)() as session:
        target = await user_dal.get_user_by_id(session, target_id)
        if target is None:
            return _error(404, "not_found")
        if target.is_banned:
            return _error(403, "banned")
        if await has_role(session, target_id, ROLE_OWNER) and not await has_role(
            session, actor_id, ROLE_OWNER
        ):
            return _error(403, "forbidden")
        token = create_webapp_session_token(
            settings, target_id, ttl_seconds=LOGIN_SESSION_TTL_SECONDS
        )
        script = build_login_script(origin, token, secrets.token_hex(32))
        await message_log_dal.create_message_log(
            session,
            {
                "user_id": actor_id,
                "target_user_id": target_id,
                "event_type": "admin_user_login_script",
                "content": f"Issued cabinet session; ttl_seconds={LOGIN_SESSION_TTL_SECONDS}",
                "is_admin_event": True,
            },
        )
        await session.commit()

    response = _ok(
        AdminUserLoginScriptOut(script=script, expires_in=LOGIN_SESSION_TTL_SECONDS).model_dump(
            mode="json"
        )
    )
    response.headers["Cache-Control"] = "no-store"
    return response
