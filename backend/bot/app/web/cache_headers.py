"""Keep API responses out of client and proxy caches.

Static assets and the Web App shell already declare their caching policy, but
JSON API responses declared nothing at all. A bare 200 with no validators is
fair game for heuristic caching, and the Telegram Mini App is the worst place
for that: the in-app WebView and its URL cache outlive a single opening, so a
stale ``/api/webapp/me`` kept showing payment buttons for providers the admin
had already switched off — with no way for the user to force a reload.

Responses that set their own ``Cache-Control`` (avatars, downloads) keep it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from aiohttp import web

API_CACHE_CONTROL = "no-store, no-cache, must-revalidate, max-age=0"
_API_PATH_PREFIX = "/api/"


def is_api_path(path: str) -> bool:
    return path.startswith(_API_PATH_PREFIX)


def mark_no_store(response: web.StreamResponse) -> None:
    if "Cache-Control" in response.headers:
        return
    if response.status == 304:
        # The client revalidated a copy it is allowed to keep (avatars); saying
        # "no-store" here would throw that copy away on every check.
        return
    response.headers["Cache-Control"] = API_CACHE_CONTROL
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"


@web.middleware
async def api_no_store_middleware(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    if not is_api_path(request.path):
        return await handler(request)
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        # Error responses are the ones a stale cache hurts most: a cached 401
        # or 409 would outlive the condition that produced it.
        mark_no_store(exc)
        raise
    mark_no_store(response)
    return response
