"""Authenticated Web Push lifecycle and ownership route regressions."""

from __future__ import annotations

import asyncio
import json
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import select

from bot.plugins.builtin.web_push import routes as routes_module
from bot.plugins.builtin.web_push.store import DELIVERY_SKIPPED
from db.dal.user_merge_setup_rewards import merge_setup_rewards
from db.models import Subscription
from db.setup_reward_models import SetupReward
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
        app.router.add_post(
            "/api/setup-rewards/install-evidence",
            routes_module.setup_reward_install_evidence_route,
        )
        app.router.add_get("/api/setup-rewards/status", routes_module.setup_rewards_status_route)
        app.router.add_post("/api/setup-rewards/claim", routes_module.setup_reward_claim_route)
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

    async def test_zero_reward_preserves_completion_and_positive_claim_is_frozen(self) -> None:
        async with TestClient(TestServer(self.make_app())) as client:
            with self.route_patches(1):
                evidence = await client.post(
                    "/api/setup-rewards/install-evidence", json={"standalone": True}
                )
                zero = await client.post("/api/setup-rewards/claim", json={"rewardType": "install"})
                zero_payload = await zero.json()
                evidence_again = await client.post(
                    "/api/setup-rewards/install-evidence", json={"standalone": True}
                )
                self.settings.SETUP_INSTALL_REWARD_DAYS = 9
                async with self.factory() as session:
                    session.add(
                        Subscription(
                            user_id=1,
                            panel_user_uuid="panel-user-1",
                            end_date=datetime.now(UTC) + timedelta(days=3),
                            is_active=True,
                            status_from_panel="ACTIVE_BONUS",
                            provider="trial",
                        )
                    )
                    await session.commit()
                expected_end = datetime.now(UTC) + timedelta(days=12)
                service = SimpleNamespace(
                    extend_subscription_reward_expiration=AsyncMock(return_value=expected_end)
                )
                with patch.object(
                    routes_module, "get_subscription_service", lambda request: service
                ):
                    granted = await client.post(
                        "/api/setup-rewards/claim", json={"rewardType": "install"}
                    )
                    granted_payload = await granted.json()
                    self.settings.SETUP_INSTALL_REWARD_DAYS = 30
                    again = await client.post(
                        "/api/setup-rewards/claim", json={"rewardType": "install"}
                    )
                    again_payload = await again.json()
        self.assertEqual(evidence.status, 200)
        self.assertEqual(evidence_again.status, 200)
        self.assertEqual(zero_payload["state"], "completed")
        self.assertEqual(zero_payload["days"], 0)
        self.assertEqual(granted_payload["state"], "confirmed")
        self.assertEqual(granted_payload["days"], 9)
        self.assertEqual(again_payload["days"], 9)
        service.extend_subscription_reward_expiration.assert_awaited_once()
        async with self.factory() as session:
            reward = await session.scalar(select(SetupReward))
        assert reward is not None
        self.assertEqual(reward.accepted_days, 9)
        self.assertEqual(reward.state, "confirmed")

    async def test_reward_status_reports_current_offers_and_account_history(self) -> None:
        self.settings.SETUP_INSTALL_REWARD_DAYS = 4
        self.settings.SETUP_PUSH_REWARD_DAYS = 6
        async with self.factory() as session:
            session.add(
                SetupReward(
                    id="status-install",
                    user_id=1,
                    reward_type="install",
                    accepted_days=3,
                    state="confirmed",
                    confirmed_end_date=datetime.now(UTC) + timedelta(days=30),
                )
            )
            await session.commit()
        async with TestClient(TestServer(self.make_app())) as client:
            with self.route_patches(1):
                response = await client.get("/api/setup-rewards/status")
                payload = await response.json()

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["install_days"], 4)
        self.assertEqual(payload["push_days"], 6)
        self.assertTrue(payload["install_completed"])
        self.assertFalse(payload["push_completed"])
        self.assertEqual(payload["install_state"], "confirmed")
        self.assertEqual(payload["install_reward_days"], 3)
        self.assertIsNotNone(payload["install_end_date"])

    async def test_inactive_account_keeps_setup_completion_without_accepting_reward(self) -> None:
        self.settings.SETUP_INSTALL_REWARD_DAYS = 5
        async with TestClient(TestServer(self.make_app())) as client:
            with self.route_patches(1):
                await client.post("/api/setup-rewards/install-evidence", json={"standalone": True})
                response = await client.post(
                    "/api/setup-rewards/claim", json={"rewardType": "install"}
                )
                payload = await response.json()
        self.assertEqual(response.status, 409)
        self.assertEqual(payload["error"], "active_subscription_required")
        async with self.factory() as session:
            reward = await session.scalar(select(SetupReward))
        assert reward is not None
        self.assertIsNone(reward.accepted_days)
        self.assertEqual(reward.state, "completed")

    async def test_ambiguous_extension_retries_same_target_without_adding_days_twice(self) -> None:
        self.settings.SETUP_INSTALL_REWARD_DAYS = 5
        async with self.factory() as session:
            session.add(
                Subscription(
                    user_id=1,
                    panel_user_uuid="panel-user-1",
                    end_date=datetime.now(UTC) + timedelta(days=3),
                    is_active=True,
                    status_from_panel="ACTIVE",
                    provider="stripe",
                    tariff_key="paid",
                    traffic_limit_bytes=1234,
                    hwid_device_limit=2,
                )
            )
            await session.commit()
        expected_end = datetime.now(UTC) + timedelta(days=8)
        service = SimpleNamespace(
            extend_subscription_reward_expiration=AsyncMock(side_effect=[None, expected_end])
        )
        async with TestClient(TestServer(self.make_app())) as client:
            with self.route_patches(1):
                await client.post("/api/setup-rewards/install-evidence", json={"standalone": True})
                with patch.object(
                    routes_module, "get_subscription_service", lambda request: service
                ):
                    first = await client.post(
                        "/api/setup-rewards/claim", json={"rewardType": "install"}
                    )
                    first_payload = await first.json()
                    self.settings.SETUP_INSTALL_REWARD_DAYS = 20
                    second = await client.post(
                        "/api/setup-rewards/claim", json={"rewardType": "install"}
                    )
                    second_payload = await second.json()
        self.assertEqual(first_payload["state"], "pending")
        self.assertEqual(second_payload["state"], "confirmed")
        self.assertEqual(second_payload["days"], 5)
        calls = service.extend_subscription_reward_expiration.await_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].args[3], calls[1].args[3])
        for call in calls:
            self.assertEqual(call.args[1], 1)
            self.assertEqual(call.args[2], 1)

    async def test_concurrent_claims_apply_one_absolute_extension(self) -> None:
        self.settings.SETUP_INSTALL_REWARD_DAYS = 5
        initial_end = datetime.now(UTC) + timedelta(days=3)
        async with self.factory() as session:
            session.add(
                Subscription(
                    user_id=1,
                    panel_user_uuid="panel-user-1",
                    end_date=initial_end,
                    is_active=True,
                    status_from_panel="ACTIVE",
                    provider="stripe",
                    tariff_key="paid",
                )
            )
            session.add(
                SetupReward(
                    id="concurrent-install",
                    user_id=1,
                    reward_type="install",
                    state="completed",
                )
            )
            await session.commit()

        both_claims_ready = asyncio.Event()
        grant_lock = asyncio.Lock()
        current_end = initial_end
        extension_count = 0
        targets: list[datetime] = []

        async def apply_once(
            session: Any, user_id: int, subscription_id: int, target: datetime
        ) -> datetime:
            nonlocal current_end, extension_count
            del session, user_id, subscription_id
            normalized_target = target.replace(tzinfo=UTC) if target.tzinfo is None else target
            targets.append(normalized_target)
            if len(targets) == 2:
                both_claims_ready.set()
            await both_claims_ready.wait()
            async with grant_lock:
                if normalized_target > current_end:
                    current_end = normalized_target
                    extension_count += 1
                return current_end

        service = SimpleNamespace(extend_subscription_reward_expiration=apply_once)
        async with TestClient(TestServer(self.make_app())) as client:
            with (
                self.route_patches(1),
                patch.object(routes_module, "get_subscription_service", lambda request: service),
            ):
                responses = await asyncio.gather(
                    client.post("/api/setup-rewards/claim", json={"rewardType": "install"}),
                    client.post("/api/setup-rewards/claim", json={"rewardType": "install"}),
                )
                payloads = [await response.json() for response in responses]

        self.assertEqual([response.status for response in responses], [200, 200])
        self.assertEqual({payload["state"] for payload in payloads}, {"confirmed"})
        self.assertEqual({payload["days"] for payload in payloads}, {5})
        self.assertEqual(len(targets), 2)
        self.assertEqual(targets[0], targets[1])
        self.assertEqual(extension_count, 1)

    async def test_push_claim_requires_current_server_device_and_account_preference(self) -> None:
        self.settings.SETUP_PUSH_REWARD_DAYS = 5
        async with TestClient(TestServer(self.make_app())) as client:
            with (
                self.route_patches(1),
                patch.object(routes_module, "push_notifications_allowed", lambda user: True),
            ):
                no_endpoint = await client.post(
                    "/api/setup-rewards/claim", json={"rewardType": "push"}
                )
                no_endpoint_payload = await no_endpoint.json()
                endpoint_only = await client.post(
                    "/api/setup-rewards/claim",
                    json={"rewardType": "push", "endpoint": ENDPOINT},
                )
                endpoint_only_payload = await endpoint_only.json()
            await self.add_subscription(user_id=1, endpoint=ENDPOINT)
            with (
                self.route_patches(1),
                patch.object(routes_module, "push_notifications_allowed", lambda user: False),
            ):
                preference_disabled = await client.post(
                    "/api/setup-rewards/claim",
                    json={"rewardType": "push", "endpoint": ENDPOINT},
                )
                preference_disabled_payload = await preference_disabled.json()

        self.assertEqual(no_endpoint.status, 409)
        self.assertEqual(no_endpoint_payload["error"], "push_device_not_confirmed")
        self.assertEqual(endpoint_only.status, 409)
        self.assertEqual(endpoint_only_payload["error"], "push_device_not_confirmed")
        self.assertEqual(preference_disabled.status, 409)
        self.assertEqual(preference_disabled_payload["error"], "push_preference_disabled")

    async def test_pending_push_reward_retries_from_account_history_without_current_device(
        self,
    ) -> None:
        self.settings.SETUP_PUSH_REWARD_DAYS = 5
        initial_end = datetime.now(UTC) + timedelta(days=3)
        target_end = initial_end + timedelta(days=5)
        async with self.factory() as session:
            subscription = Subscription(
                user_id=1,
                panel_user_uuid="panel-user-1",
                end_date=initial_end,
                is_active=True,
                status_from_panel="ACTIVE",
                provider="stripe",
                tariff_key="paid",
            )
            session.add(subscription)
            await session.flush()
            session.add(
                SetupReward(
                    id="pending-push",
                    user_id=1,
                    reward_type="push",
                    accepted_days=5,
                    subscription_id=subscription.subscription_id,
                    target_end_date=target_end,
                    state="pending",
                )
            )
            await session.commit()
        confirmed_end = target_end
        service = SimpleNamespace(
            extend_subscription_reward_expiration=AsyncMock(return_value=confirmed_end)
        )
        async with TestClient(TestServer(self.make_app())) as client:
            with (
                self.route_patches(1),
                patch.object(routes_module, "push_notifications_allowed", lambda user: False),
                patch.object(routes_module, "get_subscription_service", lambda request: service),
            ):
                response = await client.post(
                    "/api/setup-rewards/claim", json={"rewardType": "push"}
                )
                payload = await response.json()

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["state"], "confirmed")
        self.assertEqual(payload["days"], 5)
        service.extend_subscription_reward_expiration.assert_awaited_once()

    async def test_independent_pending_rewards_stack_on_the_same_subscription(self) -> None:
        self.settings.SETUP_PUSH_REWARD_DAYS = 7
        initial_end = datetime.now(UTC) + timedelta(days=3)
        install_target = initial_end + timedelta(days=5)
        async with self.factory() as session:
            subscription = Subscription(
                user_id=1,
                panel_user_uuid="panel-user-1",
                end_date=initial_end,
                is_active=True,
                status_from_panel="ACTIVE",
                provider="stripe",
                tariff_key="paid",
            )
            session.add(subscription)
            await session.flush()
            session.add_all(
                [
                    SetupReward(
                        id="pending-install",
                        user_id=1,
                        reward_type="install",
                        accepted_days=5,
                        subscription_id=subscription.subscription_id,
                        target_end_date=install_target,
                        state="pending",
                    ),
                    SetupReward(
                        id="completed-push",
                        user_id=1,
                        reward_type="push",
                        state="completed",
                    ),
                ]
            )
            await session.commit()
        await self.add_subscription(user_id=1, endpoint=ENDPOINT)

        expected_end = install_target + timedelta(days=7)
        service = SimpleNamespace(
            extend_subscription_reward_expiration=AsyncMock(return_value=expected_end)
        )
        async with TestClient(TestServer(self.make_app())) as client:
            with (
                self.route_patches(1),
                patch.object(routes_module, "push_notifications_allowed", lambda user: True),
                patch.object(routes_module, "get_subscription_service", lambda request: service),
            ):
                response = await client.post(
                    "/api/setup-rewards/claim",
                    json={"rewardType": "push", "endpoint": ENDPOINT},
                )
                payload = await response.json()

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["state"], "confirmed")
        self.assertEqual(payload["days"], 7)
        actual_target = service.extend_subscription_reward_expiration.await_args.args[3]
        if actual_target.tzinfo is None:
            actual_target = actual_target.replace(tzinfo=UTC)
        self.assertEqual(actual_target, expected_end)

    async def test_account_merge_preserves_completed_reward_claim_history(self) -> None:
        accepted_at = datetime.now(UTC)
        target = SetupReward(
            id="target-install",
            user_id=1,
            reward_type="install",
            state="completed",
        )
        source = SetupReward(
            id="source-install",
            user_id=2,
            reward_type="install",
            completed_at=accepted_at,
            accepted_days=7,
            subscription_id=22,
            target_end_date=accepted_at + timedelta(days=7),
            confirmed_end_date=accepted_at + timedelta(days=7),
            state="confirmed",
        )
        async with self.factory() as session:
            session.add_all([target, source])
            await session.commit()
            await merge_setup_rewards(session, source_user_id=2, target_user_id=1)
            await session.commit()
        async with self.factory() as session:
            rows = list((await session.execute(select(SetupReward))).scalars().all())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].user_id, 1)
        self.assertEqual(rows[0].accepted_days, 7)
        self.assertEqual(rows[0].state, "confirmed")
        history = json.loads(rows[0].history_json)
        self.assertEqual({entry["user_id"] for entry in history}, {1, 2})

    async def test_account_merge_rebinds_pending_claim_when_target_has_completion(self) -> None:
        accepted_at = datetime.now(UTC)
        target = SetupReward(
            id="target-install",
            user_id=1,
            reward_type="install",
            state="completed",
        )
        source = SetupReward(
            id="source-install",
            user_id=2,
            reward_type="install",
            completed_at=accepted_at,
            accepted_days=7,
            subscription_id=22,
            target_end_date=accepted_at + timedelta(days=7),
            state="pending",
        )
        async with self.factory() as session:
            session.add_all([target, source])
            await session.commit()
            await merge_setup_rewards(
                session,
                source_user_id=2,
                target_user_id=1,
                source_subscription_id=22,
                surviving_subscription_id=11,
            )
            await session.commit()
        async with self.factory() as session:
            row = await session.scalar(select(SetupReward))
        assert row is not None
        self.assertEqual(row.accepted_days, 7)
        self.assertEqual(row.state, "pending")
        self.assertEqual(row.subscription_id, 11)
        target_end = row.target_end_date
        if target_end.tzinfo is None:
            target_end = target_end.replace(tzinfo=UTC)
        self.assertEqual(target_end, accepted_at + timedelta(days=7))
        history = json.loads(row.history_json)
        source_entry = next(entry for entry in history if entry["user_id"] == 2)
        self.assertEqual(source_entry["subscription_id"], 22)


async def _none() -> None:
    return None
