"""Authenticated Web Push lifecycle and ownership route regressions."""

from __future__ import annotations

import json
from contextlib import ExitStack
from typing import Any
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import select

from bot.plugins.builtin.web_push import routes as routes_module
from bot.plugins.builtin.web_push.store import DELIVERY_SKIPPED
from db.web_push_models import WebPushDelivery, WebPushSubscription
from tests.unit import test_web_push_plugin as push_tests
from tests.unit.test_web_push_plugin import (
    APPLICATION_SERVER_KEYS,
    ENDPOINT,
    make_settings,
)


class WebPushRouteTests(push_tests.WebPushPluginTests):
    def make_app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/api/plugins/web_push/status", routes_module.web_push_status_route)
        app.router.add_post(
            "/api/plugins/web_push/subscription", routes_module.web_push_subscribe_route
        )
        app.router.add_post(
            "/api/plugins/web_push/subscription/sync", routes_module.web_push_sync_route
        )
        app.router.add_delete(
            "/api/plugins/web_push/subscription", routes_module.web_push_unsubscribe_route
        )
        return app

    def route_patches(self, user_id: int, settings: Any | None = None) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(
            patch.object(routes_module, "_require_user_id", lambda request: user_id)
        )
        stack.enter_context(
            patch.object(
                routes_module,
                "get_settings",
                lambda request: settings or self.settings,
            )
        )
        stack.enter_context(
            patch.object(routes_module, "get_session_factory", lambda request: self.factory)
        )
        stack.enter_context(
            patch.object(routes_module, "_rate_limited", lambda *args, **kwargs: _none())
        )
        return stack

    async def test_status_route_never_returns_endpoints_or_keys(self) -> None:
        await self.add_subscription(user_id=1, endpoint=ENDPOINT, label="Phone")
        async with TestClient(TestServer(self.make_app())) as client:
            with self.route_patches(1):
                response = await client.post(
                    "/api/plugins/web_push/status", json={"endpoint": ENDPOINT}
                )
                body = await response.json()
        self.assertEqual(response.status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["current_device_id"], body["devices"][0]["id"])
        self.assertEqual(body["devices"][0]["label"], "Phone")
        serialized = json.dumps(body)
        self.assertNotIn(ENDPOINT, serialized)
        self.assertNotIn(APPLICATION_SERVER_KEYS["p256dh"], serialized)
        self.assertNotIn(APPLICATION_SERVER_KEYS["auth"], serialized)

    async def test_subscribe_validates_endpoint_keys_and_configuration(self) -> None:
        async with TestClient(TestServer(self.make_app())) as client:
            with self.route_patches(1):
                bad_endpoint = await client.post(
                    "/api/plugins/web_push/subscription",
                    json={
                        "subscription": {
                            "endpoint": "https://evil.example.test/push",
                            "keys": APPLICATION_SERVER_KEYS,
                        }
                    },
                )
                self.assertEqual(bad_endpoint.status, 400)
                bad_keys = await client.post(
                    "/api/plugins/web_push/subscription",
                    json={
                        "subscription": {
                            "endpoint": ENDPOINT,
                            "keys": {"p256dh": "AAAA", "auth": "AAAA"},
                        }
                    },
                )
                self.assertEqual(bad_keys.status, 400)
            with self.route_patches(1, make_settings(web_push_configured=False)):
                disabled = await client.post(
                    "/api/plugins/web_push/subscription",
                    json={
                        "subscription": {
                            "endpoint": ENDPOINT,
                            "keys": APPLICATION_SERVER_KEYS,
                        }
                    },
                )
                self.assertEqual(disabled.status, 503)
                self.assertEqual((await disabled.json())["error"], "web_push_not_configured")

    async def test_subscribe_and_unsubscribe_are_ownership_scoped(self) -> None:
        async with TestClient(TestServer(self.make_app())) as client:
            with self.route_patches(1):
                first = await client.post(
                    "/api/plugins/web_push/subscription",
                    json={
                        "subscription": {
                            "endpoint": ENDPOINT,
                            "keys": APPLICATION_SERVER_KEYS,
                        },
                        "label": "Phone",
                    },
                )
                first_body = await first.json()
            self.assertEqual(first.status, 200)
            device_id = first_body["device_id"]
            await self.store.enqueue_notification(
                user_id=1,
                subscription_id=device_id,
                occurrence_id="switch-without-settings:1",
                title="Previous account",
                body="Must not reach account B",
                url="/",
                tag=None,
            )
            with self.route_patches(2):
                second = await client.post(
                    "/api/plugins/web_push/subscription",
                    json={
                        "subscription": {
                            "endpoint": ENDPOINT,
                            "keys": APPLICATION_SERVER_KEYS,
                        },
                        "label": "Other",
                    },
                )
                self.assertEqual(second.status, 200)
                second_body = await second.json()
                self.assertEqual(second_body["device_id"], device_id)
                self.assertEqual(second_body["devices"][0]["label"], "Other")
            async with self.factory() as session:
                delivery = (
                    await session.execute(
                        select(WebPushDelivery).where(
                            WebPushDelivery.occurrence_id == "switch-without-settings:1"
                        )
                    )
                ).scalar_one()
            self.assertEqual(delivery.state, DELIVERY_SKIPPED)
            self.assertEqual(delivery.error_code, "account_reassigned")
            with self.route_patches(1):
                status = await client.post(
                    "/api/plugins/web_push/status", json={"endpoint": ENDPOINT}
                )
                self.assertEqual((await status.json())["devices"], [])
                no_ownership = await client.request(
                    "DELETE",
                    "/api/plugins/web_push/subscription",
                    json={"device_id": device_id},
                )
                self.assertFalse((await no_ownership.json())["revoked"])
            with self.route_patches(2):
                owned = await client.request(
                    "DELETE",
                    "/api/plugins/web_push/subscription",
                    json={"device_id": device_id},
                )
                self.assertTrue((await owned.json())["revoked"])

    async def test_unsubscribe_marks_queued_rows_skipped(self) -> None:
        subscription_id = await self.add_subscription()
        await self.store.enqueue_notification(
            user_id=1,
            subscription_id=subscription_id,
            occurrence_id="route-revoke:1",
            title="Hello",
            body="Body",
            url="/",
            tag=None,
        )
        async with TestClient(TestServer(self.make_app())) as client:
            with self.route_patches(1):
                response = await client.request(
                    "DELETE",
                    "/api/plugins/web_push/subscription",
                    json={"endpoint": ENDPOINT},
                )
                payload = await response.json()
        self.assertTrue(payload["revoked"])
        async with self.factory() as session:
            delivery = (await session.execute(select(WebPushDelivery))).scalar_one()
        self.assertEqual(delivery.state, DELIVERY_SKIPPED)
        self.assertEqual(delivery.error_code, "revoked")

    async def test_passive_reload_does_not_restore_removed_endpoint(self) -> None:
        device_id = await self.add_subscription()
        await self.store.revoke_subscription(user_id=1, device_id=device_id)
        async with TestClient(TestServer(self.make_app())) as client:
            with self.route_patches(2):
                response = await client.post(
                    "/api/plugins/web_push/subscription/sync",
                    json={"subscription": {"endpoint": ENDPOINT, "keys": APPLICATION_SERVER_KEYS}},
                )
                payload = await response.json()
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["current_device_id"], "")
        self.assertEqual(payload["devices"], [])
        stored = await self.store.find_subscription_by_id(device_id)
        assert stored is not None
        self.assertIsNotNone(stored.revoked_at)
        self.assertEqual(stored.user_id, 1)

    async def test_explicit_subscribe_reactivates_revoked_endpoint(self) -> None:
        device_id = await self.add_subscription()
        await self.store.revoke_subscription(user_id=1, device_id=device_id)
        async with TestClient(TestServer(self.make_app())) as client:
            with self.route_patches(1):
                response = await client.post(
                    "/api/plugins/web_push/subscription",
                    json={"subscription": {"endpoint": ENDPOINT, "keys": APPLICATION_SERVER_KEYS}},
                )
                payload = await response.json()
        self.assertEqual(payload["device_id"], device_id)
        self.assertEqual(payload["current_device_id"], device_id)
        stored = await self.store.find_subscription_by_id(device_id)
        assert stored is not None
        self.assertIsNone(stored.revoked_at)

    async def test_passive_sync_rebinds_active_endpoint_and_skips_old_payload(self) -> None:
        device_id = await self.add_subscription()
        await self.store.enqueue_notification(
            user_id=1,
            subscription_id=device_id,
            occurrence_id="passive-switch",
            title="A",
            body="Private",
            url="/",
            tag=None,
        )
        async with TestClient(TestServer(self.make_app())) as client:
            with self.route_patches(2):
                response = await client.post(
                    "/api/plugins/web_push/subscription/sync",
                    json={"subscription": {"endpoint": ENDPOINT, "keys": APPLICATION_SERVER_KEYS}},
                )
                payload = await response.json()
        self.assertEqual(payload["current_device_id"], device_id)
        stored = await self.store.find_subscription_by_id(device_id)
        assert stored is not None
        self.assertEqual(stored.user_id, 2)
        async with self.factory() as session:
            delivery = (await session.execute(select(WebPushDelivery))).scalar_one()
        self.assertEqual(delivery.state, DELIVERY_SKIPPED)
        self.assertEqual(delivery.error_code, "account_reassigned")

    async def test_passive_sync_does_not_enroll_unknown_endpoint(self) -> None:
        async with TestClient(TestServer(self.make_app())) as client:
            with self.route_patches(1):
                response = await client.post(
                    "/api/plugins/web_push/subscription/sync",
                    json={"subscription": {"endpoint": ENDPOINT, "keys": APPLICATION_SERVER_KEYS}},
                )
                payload = await response.json()
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["current_device_id"], "")
        async with self.factory() as session:
            self.assertEqual(
                (await session.execute(select(WebPushSubscription))).scalars().all(), []
            )

    async def test_chunked_oversized_body_is_rejected(self) -> None:
        async def body():
            for _ in range(20):
                yield b"x" * 1024

        async with TestClient(TestServer(self.make_app())) as client:
            with self.route_patches(1):
                response = await client.post(
                    "/api/plugins/web_push/status",
                    data=body(),
                    headers={"Content-Type": "application/json"},
                )
                await response.read()
        self.assertEqual(response.status, 413)

    async def test_status_requires_authentication(self) -> None:
        async with TestClient(TestServer(self.make_app())) as client:
            with patch.object(
                routes_module,
                "_require_user_id",
                side_effect=web.HTTPUnauthorized,
            ):
                response = await client.post("/api/plugins/web_push/status", json={})
        self.assertEqual(response.status, 401)


async def _none() -> None:
    return None
