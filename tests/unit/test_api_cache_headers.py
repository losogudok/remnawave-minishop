"""API responses must not be reusable from a client or proxy cache.

The Telegram Mini App WebView survives closing the app and answers repeat GETs
from its own cache, so an unmarked ``/api/webapp/me`` kept advertising payment
providers the admin had already disabled.
"""

from __future__ import annotations

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from bot.app.web.cache_headers import API_CACHE_CONTROL, api_no_store_middleware


class ApiCacheHeadersTestCase(AioHTTPTestCase):
    async def get_application(self) -> web.Application:
        app = web.Application(middlewares=[api_no_store_middleware])

        async def api_json(_request: web.Request) -> web.Response:
            return web.json_response({"ok": True})

        async def api_unauthorized(_request: web.Request) -> web.Response:
            raise web.HTTPUnauthorized(text="{}", content_type="application/json")

        async def api_avatar(_request: web.Request) -> web.Response:
            response = web.Response(body=b"binary", content_type="image/jpeg")
            response.headers["Cache-Control"] = "private, max-age=3600"
            return response

        async def api_not_modified(_request: web.Request) -> web.Response:
            return web.Response(status=304, headers={"ETag": "abc"})

        async def asset(_request: web.Request) -> web.Response:
            return web.Response(text="asset")

        app.router.add_get("/api/webapp/me", api_json)
        app.router.add_get("/api/webapp/unauthorized", api_unauthorized)
        app.router.add_get("/api/webapp/avatar", api_avatar)
        app.router.add_get("/api/webapp/avatar-304", api_not_modified)
        app.router.add_get("/subscription_webapp.js", asset)
        return app

    async def test_api_json_is_not_cacheable(self) -> None:
        response = await self.client.get("/api/webapp/me")

        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Cache-Control"], API_CACHE_CONTROL)
        self.assertEqual(response.headers["Pragma"], "no-cache")
        self.assertEqual(response.headers["Expires"], "0")

    async def test_api_error_is_not_cacheable(self) -> None:
        response = await self.client.get("/api/webapp/unauthorized")

        self.assertEqual(response.status, 401)
        self.assertEqual(response.headers["Cache-Control"], API_CACHE_CONTROL)

    async def test_explicit_cache_policy_survives(self) -> None:
        response = await self.client.get("/api/webapp/avatar")

        self.assertEqual(response.headers["Cache-Control"], "private, max-age=3600")
        self.assertNotIn("Pragma", response.headers)

    async def test_revalidated_response_keeps_the_clients_copy(self) -> None:
        response = await self.client.get("/api/webapp/avatar-304")

        self.assertEqual(response.status, 304)
        self.assertNotIn("Cache-Control", response.headers)

    async def test_assets_keep_their_own_policy(self) -> None:
        response = await self.client.get("/subscription_webapp.js")

        self.assertNotIn("Cache-Control", response.headers)
