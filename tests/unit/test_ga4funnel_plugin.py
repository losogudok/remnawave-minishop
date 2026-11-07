"""Built-in ``ga4funnel`` plugin: descriptor, routes, capture, delivery, reconcile."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import tempfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import event, inspect, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.app.web.context import SESSION_FACTORY, SETTINGS
from bot.infra import events
from bot.plugins.builtin import BUILTIN_PLUGINS
from bot.plugins.builtin.ga4funnel import Ga4FunnelPlugin
from bot.plugins.builtin.ga4funnel import client as ga4_client
from bot.plugins.builtin.ga4funnel import routes as ga4_routes
from bot.plugins.builtin.ga4funnel import store as ga4_store
from bot.plugins.builtin.ga4funnel import worker as ga4_worker
from bot.plugins.builtin.ga4funnel.capture import capture_payment
from bot.plugins.builtin.ga4funnel.config import (
    build_event_body,
    ga4_configured,
    normalize_measurement_id,
    sanitize_event_params,
)
from bot.plugins.builtin.ga4funnel.migrations import CHAIN_GA4FUNNEL
from bot.plugins.builtin.ga4funnel.reconcile import (
    extract_first_connected_at,
    reconcile,
)
from bot.plugins.builtin.ga4funnel.signals import resolve_signals
from bot.plugins.builtin.ga4funnel.worker import Ga4FunnelWorker
from bot.plugins.extensions import OperationContext, SignalContext
from bot.plugins.spec import PluginContext
from config.settings import Settings
from db.base import Base
from db.ga4_models import Ga4FunnelBinding, Ga4FunnelEvent, Ga4FunnelMilestone
from db.models import AccountAlias, Payment, Subscription, User

REPO_ROOT = Path(__file__).resolve().parents[2]


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "BOT_TOKEN": "x",
        "POSTGRES_USER": "u",
        "POSTGRES_PASSWORD": "p",
        "ADMIN_IDS": "1",
    }
    values.update(overrides)
    return Settings(**values)


def configured_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "GA4_ENABLED": True,
        "GA4_MEASUREMENT_ID": "G-ABCD1234",
        "GA4_API_SECRET": "server-secret",
    }
    values.update(overrides)
    return make_settings(**values)


class DescriptorTests(IsolatedAsyncioTestCase):
    async def test_descriptor_requires_full_configuration(self) -> None:
        variants = (
            make_settings(),
            configured_settings(GA4_ENABLED=False),
            configured_settings(GA4_MEASUREMENT_ID="UA-123"),
            configured_settings(GA4_MEASUREMENT_ID=""),
            configured_settings(GA4_API_SECRET=""),
        )
        for settings in variants:
            with self.subTest(settings=settings.GA4_MEASUREMENT_ID):
                self.assertFalse(ga4_configured(settings))
                self.assertIsNone(await resolve_signals(SignalContext(settings=settings)))

    async def test_descriptor_exposes_public_config_only(self) -> None:
        settings = configured_settings(GA4_DEBUG_MODE=True)
        contribution = await resolve_signals(SignalContext(settings=settings))

        self.assertIsNotNone(contribution)
        assert contribution is not None
        self.assertEqual(contribution.entry, "/api/plugins/ga4funnel/signals.mjs")
        self.assertEqual(contribution.scripts, ())
        self.assertEqual(contribution.connect_hosts, ())
        self.assertEqual(
            contribution.config,
            {"measurement_id": "G-ABCD1234", "debug_mode": True},
        )
        self.assertNotIn("secret", json.dumps(contribution.config).lower())
        self.assertNotIn("server-secret", json.dumps(contribution.config))
        self.assertNotIn("googletagmanager", json.dumps(contribution.__dict__))

    def test_measurement_id_normalization(self) -> None:
        self.assertEqual(normalize_measurement_id(" g-abcdef12 "), "G-ABCDEF12")
        self.assertIsNone(normalize_measurement_id("G-12"))
        self.assertIsNone(normalize_measurement_id("G-ABCDEF12/extra"))


class RouteTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite://")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.settings = configured_settings()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    def app(self) -> web.Application:
        app = web.Application()
        app[SETTINGS] = self.settings
        app[SESSION_FACTORY] = self.factory
        ga4_routes.setup_ga4funnel_routes(app)
        return app

    async def test_config_route_is_no_store_and_hides_secret(self) -> None:
        request = make_mocked_request("GET", ga4_routes.CONFIG_PATH, app=self.app())
        response = await ga4_routes.ga4funnel_config_route(request)
        body = json.loads(response.text or "{}")

        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(
            body,
            {
                "ok": True,
                "enabled": True,
                "measurement_id": "G-ABCD1234",
                "debug_mode": False,
            },
        )
        self.assertNotIn("server-secret", response.text or "")

    async def test_config_route_disabled_has_no_measurement_id(self) -> None:
        self.settings = make_settings()
        request = make_mocked_request("GET", ga4_routes.CONFIG_PATH, app=self.app())
        response = await ga4_routes.ga4funnel_config_route(request)
        body = json.loads(response.text or "{}")

        self.assertFalse(body["enabled"])
        self.assertEqual(body["measurement_id"], "")

    async def test_adapter_route_headers_and_disabled_404(self) -> None:
        request = make_mocked_request("GET", ga4_routes.SIGNALS_PATH, app=self.app())
        response = await ga4_routes.ga4funnel_signals_route(request)

        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, "application/javascript")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("mountOnboardingSignals", response.text or "")

        self.settings = make_settings()
        disabled = make_mocked_request("GET", ga4_routes.SIGNALS_PATH, app=self.app())
        with self.assertRaises(web.HTTPNotFound):
            await ga4_routes.ga4funnel_signals_route(disabled)

    async def test_frame_routes_are_strict_and_have_no_secret(self) -> None:
        request = make_mocked_request("GET", ga4_routes.FRAME_PATH, app=self.app())
        frame = await ga4_routes.ga4funnel_frame_route(request)
        html = frame.text or ""
        csp = frame.headers["Content-Security-Policy"]

        self.assertEqual(frame.status, 200)
        self.assertEqual(frame.content_type, "text/html")
        self.assertEqual(frame.headers["Cache-Control"], "no-store")
        self.assertEqual(frame.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(frame.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(
            csp,
            "default-src 'none'; "
            "script-src 'self' https://www.googletagmanager.com; "
            "connect-src https://www.google-analytics.com "
            "https://analytics.google.com https://region1.google-analytics.com; "
            "img-src https://www.google-analytics.com; "
            "frame-ancestors 'self' https://web.telegram.org https://t.me",
        )
        self.assertNotIn("unsafe-inline", csp)
        self.assertNotIn("server-secret", html)
        self.assertEqual(html.count("<script"), 1)
        self.assertIn(ga4_routes.FRAME_SCRIPT_PATH, html)

        script_request = make_mocked_request("GET", ga4_routes.FRAME_SCRIPT_PATH, app=self.app())
        script = await ga4_routes.ga4funnel_frame_script_route(script_request)
        self.assertEqual(script.status, 200)
        self.assertEqual(script.content_type, "application/javascript")
        self.assertEqual(script.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("ga4_event", script.text or "")
        self.assertNotIn("server-secret", script.text or "")

        self.settings = make_settings()
        with self.assertRaises(web.HTTPNotFound):
            await ga4_routes.ga4funnel_frame_route(
                make_mocked_request("GET", ga4_routes.FRAME_PATH, app=self.app())
            )
        with self.assertRaises(web.HTTPNotFound):
            await ga4_routes.ga4funnel_frame_script_route(
                make_mocked_request("GET", ga4_routes.FRAME_SCRIPT_PATH, app=self.app())
            )

    async def test_bind_requires_auth_and_validates_client_id(self) -> None:
        app = self.app()
        with patch.object(ga4_routes, "_require_user_id", return_value=7):
            async with TestClient(TestServer(app)) as client:
                invalid = await client.post(
                    ga4_routes.BIND_PATH,
                    json={"client_id": "not-a-ga-client"},
                )
                valid = await client.post(
                    ga4_routes.BIND_PATH,
                    json={"client_id": "123456789.987654321"},
                )
                valid_body = await valid.text()

        self.assertEqual(invalid.status, 400)
        self.assertEqual(valid.status, 200)
        self.assertEqual(json.loads(valid_body), {"ok": True})

    async def test_bind_releases_queued_events_without_client_id(self) -> None:
        async with self.factory() as session:
            await ga4_store.record_event(
                session,
                name="sign_up",
                dedupe_key="sign_up:user:7",
                user_id=7,
                params={},
            )
            await session.commit()

        app = self.app()
        with patch.object(ga4_routes, "_require_user_id", return_value=7):
            async with TestClient(TestServer(app)) as client:
                response = await client.post(
                    ga4_routes.BIND_PATH,
                    json={"client_id": "111.222"},
                )
        self.assertEqual(response.status, 200)

        async with self.factory() as session:
            binding = await session.scalar(
                select(Ga4FunnelBinding).where(Ga4FunnelBinding.user_id == 7)
            )
            queued = await session.scalar(
                select(Ga4FunnelEvent).where(Ga4FunnelEvent.dedupe_key == "sign_up:user:7")
            )
        assert binding is not None and queued is not None
        self.assertEqual(binding.client_id, "111.222")
        self.assertEqual(queued.client_id, "111.222")


class DatabaseTestCase(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        events.reset_subscribers()
        self.engine = create_async_engine("sqlite+aiosqlite://")

        @event.listens_for(self.engine.sync_engine, "connect")
        def disable_legacy_transactions(connection: Any, _record: Any) -> None:
            connection.isolation_level = None

        @event.listens_for(self.engine.sync_engine, "begin")
        def begin(connection: Any) -> None:
            connection.exec_driver_sql("BEGIN")

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.settings = configured_settings()
        self.ctx = PluginContext(settings=self.settings, session_factory=self.factory)
        self.plugin = Ga4FunnelPlugin()

    async def asyncTearDown(self) -> None:
        events.reset_subscribers()
        await self.engine.dispose()

    async def add_user(self, user_id: int, **overrides: Any) -> None:
        async with self.factory() as session:
            session.add(User(user_id=user_id, **overrides))
            await session.commit()

    async def add_event(
        self,
        *,
        event_id: str,
        name: str,
        dedupe_key: str,
        user_id: int | None = None,
        client_id: str | None = None,
        occurred_at: datetime | None = None,
        received_at: datetime | None = None,
        params: dict[str, Any] | None = None,
        state: str = "queued",
    ) -> None:
        now = datetime.now(UTC)
        async with self.factory() as session:
            session.add(
                Ga4FunnelEvent(
                    id=event_id,
                    user_id=user_id,
                    client_id=client_id,
                    name=name,
                    dedupe_key=dedupe_key,
                    params_json=params or {},
                    occurred_at=occurred_at,
                    received_at=received_at or now,
                    state=state,
                    attempts=0,
                    max_attempts=8,
                    next_attempt_at=received_at or now,
                )
            )
            await session.commit()

    async def events_by_name(self, name: str) -> list[Ga4FunnelEvent]:
        async with self.factory() as session:
            return list(
                (
                    await session.scalars(select(Ga4FunnelEvent).where(Ga4FunnelEvent.name == name))
                ).all()
            )


class CaptureTests(DatabaseTestCase):
    async def test_disabled_configuration_stores_no_events(self) -> None:
        self.ctx.settings.GA4_ENABLED = False
        self.plugin.setup(self.ctx)
        await events.emit(
            events.USER_REGISTERED,
            {"user_id": 1, "registered_via": "telegram"},
        )
        await events.emit(
            events.TRIAL_ACTIVATED,
            {"user_id": 1, "days": 7},
        )
        await events.emit(
            events.PANEL_WEBHOOK_RECEIVED,
            {"event": "user.first_connected", "panel_user_uuid": "panel-1"},
        )

        async with self.factory() as session:
            count = len((await session.scalars(select(Ga4FunnelEvent))).all())
        self.assertEqual(count, 0)

    async def test_registration_and_trial_are_deduplicated(self) -> None:
        self.plugin.setup(self.ctx)
        await events.emit(
            events.USER_REGISTERED,
            {"user_id": 1, "registered_via": "telegram"},
        )
        await events.emit(
            events.USER_REGISTERED,
            {"user_id": 1, "registered_via": "telegram"},
        )
        await events.emit(
            events.USER_REGISTERED,
            {"user_id": 2, "registered_via": "panel_sync"},
        )
        await events.emit(
            events.TRIAL_ACTIVATED,
            {"user_id": 1, "days": 7, "traffic_gb": 10.5, "end_date": "ignored"},
        )
        await events.emit(
            events.TRIAL_ACTIVATED,
            {"user_id": 1, "days": 7, "traffic_gb": 10.5},
        )

        signups = await self.events_by_name("sign_up")
        trials = await self.events_by_name("trial_started")
        self.assertEqual([row.dedupe_key for row in signups], ["sign_up:user:1"])
        self.assertEqual([row.user_id for row in signups], [1])
        self.assertEqual([row.dedupe_key for row in trials], ["trial_started:user:1"])
        self.assertEqual(trials[0].params_json, {"days": 7, "traffic_gb": 10.5})
        async with self.factory() as session:
            milestones = list(
                (
                    await session.scalars(
                        select(Ga4FunnelMilestone).where(
                            Ga4FunnelMilestone.user_id == 1,
                            Ga4FunnelMilestone.name == "trial_started",
                        )
                    )
                ).all()
            )
        self.assertEqual(len(milestones), 1)

    async def test_payment_capture_uses_real_payment_not_checkout_click(self) -> None:
        await self.add_user(1)
        async with self.factory() as session:
            payment = Payment(
                user_id=1,
                amount=499.0,
                currency="RUB",
                status="succeeded",
                provider="yookassa",
                sale_mode="subscription@standard",
                subscription_duration_months=3,
            )
            session.add(payment)
            await session.commit()
            payment_db_id = int(payment.payment_id)

        self.plugin.setup(self.ctx)
        await events.emit(
            events.PROMO_CODE_APPLIED,
            {"user_id": 1, "promo_code_id": 5, "payment_db_id": payment_db_id},
        )
        self.assertEqual(await self.events_by_name("purchase"), [])

        contributions = self.plugin.extensions(self.ctx)
        job = next(item for item in contributions.jobs if item.id == "capture_payment")
        operation = OperationContext("op-1", "ga4funnel", 1, self.ctx)
        result = await job.run(
            operation,
            {
                "event": "payment.succeeded",
                "event_id": str(payment_db_id),
                "payload": {
                    "user_id": 1,
                    "payment_db_id": payment_db_id,
                    "amount": 499.0,
                    "currency": "RUB",
                    "sale_mode": "subscription@standard",
                },
            },
        )

        purchases = await self.events_by_name("purchase")
        self.assertTrue(result["stored"])
        self.assertEqual(len(purchases), 1)
        self.assertEqual(
            purchases[0].dedupe_key,
            f"purchase:payment:{payment_db_id}",
        )
        self.assertEqual(purchases[0].params_json["transaction_id"], str(payment_db_id))
        self.assertEqual(purchases[0].params_json["months"], 3)
        async with self.factory() as session:
            milestones = list(
                (
                    await session.scalars(
                        select(Ga4FunnelMilestone).where(
                            Ga4FunnelMilestone.user_id == 1,
                            Ga4FunnelMilestone.name == "purchase",
                        )
                    )
                ).all()
            )
        self.assertEqual(len(milestones), 1)

        # Duplicate callback/job replay does not create another purchase.
        await job.run(
            operation,
            {
                "event": "payment.succeeded",
                "event_id": str(payment_db_id),
                "payload": {
                    "user_id": 1,
                    "payment_db_id": payment_db_id,
                    "amount": 499.0,
                    "currency": "RUB",
                    "sale_mode": "subscription@standard",
                },
            },
        )
        self.assertEqual(len(await self.events_by_name("purchase")), 1)

    async def test_delayed_purchase_uses_merged_target_owner(self) -> None:
        await self.add_user(1)
        await self.add_user(2)
        async with self.factory() as session:
            session.add(
                Ga4FunnelBinding(
                    id="t" * 32,
                    user_id=2,
                    client_id="target.client",
                    created_at=datetime.now(UTC),
                    last_seen_at=datetime.now(UTC),
                )
            )
            payment = Payment(
                user_id=2,
                amount=499.0,
                currency="RUB",
                status="succeeded",
                provider="yookassa",
                sale_mode="subscription@standard",
                subscription_duration_months=1,
            )
            session.add(payment)
            await session.commit()
            payment_db_id = int(payment.payment_id)

        # Core moved the durable operation to the target during the merge; the
        # job payload still carries the old source id.
        operation = OperationContext("op-merged", "ga4funnel", 2, self.ctx)
        await capture_payment(
            operation,
            {
                "event": "payment.succeeded",
                "event_id": str(payment_db_id),
                "payload": {
                    "user_id": 1,
                    "payment_db_id": payment_db_id,
                    "amount": 499.0,
                    "currency": "RUB",
                    "sale_mode": "subscription@standard",
                },
            },
        )

        purchases = await self.events_by_name("purchase")
        self.assertEqual(len(purchases), 1)
        self.assertEqual(purchases[0].user_id, 2)
        client_id = await ga4_store.latest_client_id(self.factory, 2)
        self.assertEqual(client_id, "target.client")

    async def test_purchase_owner_falls_back_to_account_alias(self) -> None:
        await self.add_user(1)
        await self.add_user(2)
        async with self.factory() as session:
            session.add(
                AccountAlias(
                    old_user_id=1,
                    new_user_id=2,
                    reason="merge",
                )
            )
            payment = Payment(
                user_id=1,
                amount=499.0,
                currency="RUB",
                status="succeeded",
                provider="yookassa",
                sale_mode="subscription@standard",
                subscription_duration_months=1,
            )
            session.add(payment)
            await session.commit()
            payment_db_id = int(payment.payment_id)

        operation = OperationContext("op-alias", "ga4funnel", None, self.ctx)
        await capture_payment(
            operation,
            {
                "event": "payment.succeeded",
                "event_id": str(payment_db_id),
                "payload": {
                    "user_id": 1,
                    "payment_db_id": payment_db_id,
                    "amount": 499.0,
                    "currency": "RUB",
                    "sale_mode": "subscription@standard",
                },
            },
        )

        purchases = await self.events_by_name("purchase")
        self.assertEqual([row.user_id for row in purchases], [2])

    async def test_first_connected_resolves_local_user_and_pending_event(self) -> None:
        await self.add_user(1, panel_user_uuid="panel-1")
        await self.add_user(2, telegram_id=777)
        self.plugin.setup(self.ctx)

        await events.emit(
            events.PANEL_WEBHOOK_RECEIVED,
            {
                "event": "user.first_connected",
                "panel_user_uuid": "panel-1",
                "occurred_at": "2026-11-01T10:00:00Z",
            },
        )
        await events.emit(
            events.PANEL_WEBHOOK_RECEIVED,
            {
                "event": "user.first_connected",
                "panel_user_uuid": "unknown-panel",
                "telegram_id": 777,
                "occurred_at": "2026-11-01T11:00:00Z",
            },
        )
        await events.emit(
            events.PANEL_WEBHOOK_RECEIVED,
            {
                "event": "user.first_connected",
                "panel_user_uuid": "pending-panel",
                "occurred_at": "2026-11-01T12:00:00Z",
            },
        )

        records = await self.events_by_name("vpn_first_connected")
        keys = {row.dedupe_key for row in records}
        self.assertIn("first_connected:user:1", keys)
        self.assertIn("first_connected:user:2", keys)
        self.assertIn("first_connected:panel:panel_uuid:pending-panel", keys)


class IdentityReconciliationTests(DatabaseTestCase):
    async def test_numeric_panel_uuid_is_not_confused_with_telegram_id(self) -> None:
        await self.add_user(1, panel_user_uuid="123")
        await self.add_user(2, telegram_id=123)
        now = datetime.now(UTC)
        async with self.factory() as session:
            session.add_all(
                [
                    Ga4FunnelEvent(
                        id="x" * 32,
                        user_id=None,
                        name="vpn_first_connected",
                        dedupe_key="first_connected:panel:panel_uuid:123",
                        params_json={},
                        occurred_at=now,
                        received_at=now,
                        state="queued",
                        attempts=0,
                        max_attempts=8,
                        next_attempt_at=now,
                    ),
                    Ga4FunnelEvent(
                        id="y" * 32,
                        user_id=None,
                        name="vpn_first_connected",
                        dedupe_key="first_connected:panel:telegram:123",
                        params_json={},
                        occurred_at=now,
                        received_at=now,
                        state="queued",
                        attempts=0,
                        max_attempts=8,
                        next_attempt_at=now,
                    ),
                ]
            )
            await session.commit()

        result = await reconcile(
            PluginContext(settings=self.settings, session_factory=self.factory)
        )
        self.assertEqual(result["resolved"], 2)
        async with self.factory() as session:
            panel_event = await session.scalar(
                select(Ga4FunnelEvent).where(Ga4FunnelEvent.dedupe_key == "first_connected:user:1")
            )
            telegram_event = await session.scalar(
                select(Ga4FunnelEvent).where(Ga4FunnelEvent.dedupe_key == "first_connected:user:2")
            )
            panel_milestone = await session.scalar(
                select(Ga4FunnelMilestone).where(Ga4FunnelMilestone.user_id == 1)
            )
            telegram_milestone = await session.scalar(
                select(Ga4FunnelMilestone).where(Ga4FunnelMilestone.user_id == 2)
            )
        self.assertIsNotNone(panel_event)
        self.assertIsNotNone(telegram_event)
        self.assertIsNotNone(panel_milestone)
        self.assertIsNotNone(telegram_milestone)


class WorkerTests(DatabaseTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.refresh = patch(
            "bot.services.settings_override_service.refresh_overrides_from_db",
            AsyncMock(return_value=0),
        )
        self.refresh.start()

    async def asyncTearDown(self) -> None:
        self.refresh.stop()
        await super().asyncTearDown()

    async def test_success_marks_sent_and_uses_binding(self) -> None:
        await self.add_event(
            event_id="e" * 32,
            name="trial_started",
            dedupe_key="trial_started:user:1",
            user_id=1,
        )
        async with self.factory() as session:
            session.add(
                Ga4FunnelBinding(
                    id="b" * 32,
                    user_id=1,
                    client_id="111.222",
                    created_at=datetime.now(UTC),
                    last_seen_at=datetime.now(UTC),
                )
            )
            await session.commit()
        seen: list[tuple[str, str]] = []

        async def sender(_session: Any, **kwargs: Any) -> ga4_client.DeliveryOutcome:
            seen.append((str(kwargs["name"]), str(kwargs["client_id"])))
            return ga4_client.DeliveryOutcome(sent=True, retryable=False, status=204)

        worker = Ga4FunnelWorker(self.ctx, sender=sender, http_session=object())
        self.assertEqual(await worker.tick(), 1)

        self.assertEqual(seen, [("trial_started", "111.222")])
        async with self.factory() as session:
            row = await session.get(Ga4FunnelEvent, "e" * 32)
        assert row is not None
        self.assertEqual(row.state, "sent")
        self.assertIsNotNone(row.sent_at)

    async def test_4xx_fails_without_retry_and_5xx_retries(self) -> None:
        await self.add_event(
            event_id="a" * 32,
            name="sign_up",
            dedupe_key="sign_up:user:1",
            user_id=1,
            client_id="1.2",
        )
        await self.add_event(
            event_id="b" * 32,
            name="trial_started",
            dedupe_key="trial_started:user:1",
            user_id=1,
            client_id="1.2",
        )

        async def sender(_session: Any, **kwargs: Any) -> ga4_client.DeliveryOutcome:
            if kwargs["name"] == "sign_up":
                return ga4_client.DeliveryOutcome(
                    sent=False, retryable=False, error_code="http_4xx", status=400
                )
            return ga4_client.DeliveryOutcome(
                sent=False, retryable=True, error_code="http_5xx", status=500
            )

        worker = Ga4FunnelWorker(self.ctx, sender=sender, http_session=object())
        await worker.tick()

        async with self.factory() as session:
            failed = await session.get(Ga4FunnelEvent, "a" * 32)
            retried = await session.get(Ga4FunnelEvent, "b" * 32)
        assert failed is not None and retried is not None
        self.assertEqual(failed.state, "failed")
        self.assertEqual(failed.error_code, "http_4xx")
        self.assertEqual(retried.state, "queued")
        self.assertEqual(retried.error_code, "http_5xx")
        self.assertGreater(retried.next_attempt_at, datetime.now(UTC).replace(tzinfo=None))

    async def test_disabled_worker_leaves_events_queued(self) -> None:
        self.ctx.settings.GA4_ENABLED = False
        await self.add_event(
            event_id="c" * 32,
            name="sign_up",
            dedupe_key="sign_up:user:1",
            user_id=1,
            client_id="1.2",
        )
        sender = AsyncMock()
        worker = Ga4FunnelWorker(self.ctx, sender=sender, http_session=object())

        self.assertEqual(await worker.tick(), 0)
        sender.assert_not_awaited()
        async with self.factory() as session:
            row = await session.get(Ga4FunnelEvent, "c" * 32)
        assert row is not None
        self.assertEqual(row.state, "queued")

    async def test_missing_binding_skips_after_24_hours(self) -> None:
        await self.add_event(
            event_id="d" * 32,
            name="sign_up",
            dedupe_key="sign_up:user:1",
            user_id=1,
            received_at=datetime.now(UTC) - timedelta(hours=25),
        )
        worker = Ga4FunnelWorker(self.ctx, sender=AsyncMock(), http_session=object())
        await worker.tick()

        async with self.factory() as session:
            row = await session.get(Ga4FunnelEvent, "d" * 32)
        assert row is not None
        self.assertEqual(row.state, "skipped")
        self.assertEqual(row.error_code, "binding_missing")

    async def test_lost_lease_never_calls_http(self) -> None:
        await self.add_event(
            event_id="g" * 32,
            name="sign_up",
            dedupe_key="sign_up:user:1",
            user_id=1,
            client_id="1.2",
        )
        now = datetime.now(UTC)
        claimed = await ga4_store.claim_batch(self.factory, now=now, limit=1)
        self.assertEqual(len(claimed), 1)
        async with self.factory() as session:
            await session.execute(
                update(Ga4FunnelEvent)
                .where(Ga4FunnelEvent.id == "g" * 32)
                .values(lease_token="other-worker-token", lease_until=now + timedelta(minutes=1))
            )
            await session.commit()

        sender = AsyncMock()
        worker = Ga4FunnelWorker(self.ctx, sender=sender, http_session=object())
        await worker._deliver(claimed[0], now=now)
        sender.assert_not_awaited()

    async def test_expired_lease_before_send_never_calls_http(self) -> None:
        await self.add_event(
            event_id="i" * 32,
            name="sign_up",
            dedupe_key="sign_up:user:1",
            user_id=1,
            client_id="1.2",
        )
        now = datetime.now(UTC)
        claimed = await ga4_store.claim_batch(self.factory, now=now, limit=1)
        self.assertEqual(len(claimed), 1)
        async with self.factory() as session:
            await session.execute(
                update(Ga4FunnelEvent)
                .where(Ga4FunnelEvent.id == "i" * 32)
                .values(lease_until=now - timedelta(seconds=1))
            )
            await session.commit()

        sender = AsyncMock()
        worker = Ga4FunnelWorker(self.ctx, sender=sender, http_session=object())
        await worker._deliver(claimed[0], now=now)
        sender.assert_not_awaited()

    async def test_reclaim_during_lookup_sends_once_with_new_owner(self) -> None:
        await self.add_event(
            event_id="h" * 32,
            name="sign_up",
            dedupe_key="sign_up:user:1",
            user_id=1,
        )
        async with self.factory() as session:
            session.add(
                Ga4FunnelBinding(
                    id="c" * 32,
                    user_id=1,
                    client_id="1.2",
                    created_at=datetime.now(UTC),
                    last_seen_at=datetime.now(UTC),
                )
            )
            await session.commit()
        now = datetime.now(UTC)
        first = await ga4_store.claim_batch(self.factory, now=now, limit=1)
        self.assertEqual(len(first), 1)
        reclaimed_token = "reclaimed-worker-token"
        second = replace(first[0], lease_token=reclaimed_token, attempts=2)
        sender = AsyncMock(
            return_value=ga4_client.DeliveryOutcome(sent=True, retryable=False, status=204)
        )

        async def reclaim_during_lookup(_factory: Any, user_id: int) -> str | None:
            async with _factory() as session:
                await session.execute(
                    update(Ga4FunnelEvent)
                    .where(Ga4FunnelEvent.id == "h" * 32)
                    .values(
                        state="running",
                        lease_token=reclaimed_token,
                        lease_until=now + timedelta(minutes=1),
                        attempts=2,
                    )
                )
                await session.commit()
            return "1.2"

        stale_worker = Ga4FunnelWorker(self.ctx, sender=sender, http_session=object())
        with patch.object(ga4_store, "latest_client_id", reclaim_during_lookup):
            await stale_worker._deliver(first[0], now=now)
        self.assertEqual(sender.await_count, 0)

        current_worker = Ga4FunnelWorker(self.ctx, sender=sender, http_session=object())
        await current_worker._deliver(second, now=now)
        self.assertEqual(sender.await_count, 1)
        async with self.factory() as session:
            row = await session.get(Ga4FunnelEvent, "h" * 32)
        assert row is not None
        self.assertEqual(row.state, "sent")
        self.assertIsNone(row.lease_token)

    async def test_clock_advance_during_preparation_skips_send(self) -> None:
        await self.add_event(
            event_id="j" * 32,
            name="sign_up",
            dedupe_key="sign_up:user:1",
            user_id=1,
        )
        now = datetime.now(UTC)
        async with self.factory() as session:
            session.add(
                Ga4FunnelBinding(
                    id="d" * 32,
                    user_id=1,
                    client_id="1.2",
                    created_at=now,
                    last_seen_at=now,
                )
            )
            await session.commit()
        claimed = await ga4_store.claim_batch(self.factory, now=now, limit=1)
        self.assertEqual(len(claimed), 1)

        class AdvancingDateTime:
            current = datetime.now(UTC)

            @classmethod
            def now(cls, _tz: Any = None) -> datetime:
                return cls.current

        AdvancingDateTime.current = now

        async def advance_during_lookup(_factory: Any, _user_id: int) -> str | None:
            AdvancingDateTime.current = now + timedelta(minutes=2)
            return "1.2"

        sender = AsyncMock()
        worker = Ga4FunnelWorker(self.ctx, sender=sender, http_session=object())
        with (
            patch.object(ga4_worker, "datetime", AdvancingDateTime),
            patch.object(ga4_store, "latest_client_id", advance_during_lookup),
        ):
            await worker._deliver(claimed[0])
        sender.assert_not_awaited()

    async def test_real_claim_reclaim_has_one_sender_and_new_owner_completes(self) -> None:
        await self.add_event(
            event_id="k" * 32,
            name="sign_up",
            dedupe_key="sign_up:user:1",
            user_id=1,
        )
        first_time = datetime.now(UTC)
        async with self.factory() as session:
            session.add(
                Ga4FunnelBinding(
                    id="e" * 32,
                    user_id=1,
                    client_id="1.2",
                    created_at=first_time,
                    last_seen_at=first_time,
                )
            )
            await session.commit()
        first = await ga4_store.claim_batch(self.factory, now=first_time, limit=1)
        self.assertEqual(len(first), 1)
        reclaim_time = first_time + timedelta(minutes=2)
        second = await ga4_store.claim_batch(self.factory, now=reclaim_time, limit=1)
        self.assertEqual(len(second), 1)
        self.assertNotEqual(first[0].lease_token, second[0].lease_token)
        self.assertEqual(second[0].attempts, 2)
        self.assertFalse(
            await ga4_store.renew_lease(self.factory, event=first[0], now=reclaim_time)
        )

        sender = AsyncMock(
            return_value=ga4_client.DeliveryOutcome(sent=True, retryable=False, status=204)
        )
        stale_worker = Ga4FunnelWorker(self.ctx, sender=sender, http_session=object())
        await stale_worker._deliver(first[0], now=reclaim_time)
        self.assertEqual(sender.await_count, 0)

        current_worker = Ga4FunnelWorker(self.ctx, sender=sender, http_session=object())
        await current_worker._deliver(second[0], now=reclaim_time)
        self.assertEqual(sender.await_count, 1)
        async with self.factory() as session:
            row = await session.get(Ga4FunnelEvent, "k" * 32)
        assert row is not None
        self.assertEqual(row.state, "sent")
        self.assertIsNone(row.lease_token)

    async def test_missing_binding_retries_before_24_hours(self) -> None:
        await self.add_event(
            event_id="f" * 32,
            name="sign_up",
            dedupe_key="sign_up:user:1",
            user_id=1,
        )
        worker = Ga4FunnelWorker(self.ctx, sender=AsyncMock(), http_session=object())
        await worker.tick()

        async with self.factory() as session:
            row = await session.get(Ga4FunnelEvent, "f" * 32)
        assert row is not None
        self.assertEqual(row.state, "queued")
        self.assertEqual(row.attempts, 0)
        self.assertGreater(row.next_attempt_at, datetime.now(UTC).replace(tzinfo=None))


class ClaimConcurrencyTests(IsolatedAsyncioTestCase):
    async def test_concurrent_claimers_each_own_disjoint_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = create_async_engine(
                f"sqlite+aiosqlite:///{Path(directory) / 'claims.sqlite'}",
                connect_args={"timeout": 10},
            )
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            now = datetime.now(UTC)
            async with factory() as session:
                for index in range(20):
                    session.add(
                        Ga4FunnelEvent(
                            id=f"{index:032x}",
                            user_id=1,
                            client_id="1.2",
                            name="sign_up",
                            dedupe_key=f"sign_up:user:{index}",
                            params_json={},
                            received_at=now,
                            state="queued",
                            attempts=0,
                            max_attempts=8,
                            next_attempt_at=now,
                        )
                    )
                await session.commit()

            barrier = asyncio.Barrier(2)

            async def claim() -> list[ga4_store.ClaimedEvent]:
                await barrier.wait()
                claimed: list[ga4_store.ClaimedEvent] = await ga4_store.claim_batch(
                    factory, now=now, limit=20
                )
                return claimed

            first, second = await asyncio.gather(claim(), claim())
            ids = [item.id for item in (*first, *second)]
            self.assertEqual(len(ids), 20)
            self.assertEqual(len(set(ids)), 20)
            await engine.dispose()


class MeasurementProtocolTests(TestCase):
    def test_server_payload_is_allowlisted(self) -> None:
        params = sanitize_event_params(
            "purchase",
            {
                "transaction_id": "42",
                "value": 100.5,
                "currency": "rub",
                "sale_mode": "subscription@standard",
                "months": 3,
                "email": "user@example.com",
                "telegram_id": 777,
                "subscription_url": "https://app.example/s/token",
            },
        )
        self.assertEqual(
            params,
            {
                "transaction_id": "42",
                "value": 100.5,
                "currency": "RUB",
                "sale_mode": "subscription@standard",
                "months": 3,
            },
        )
        self.assertEqual(sanitize_event_params("vpn_first_connected", {"email": "x"}), {})

    def test_timestamp_micros_only_within_72_hours(self) -> None:
        now = datetime(2026, 11, 5, 12, tzinfo=UTC)
        fresh = build_event_body(
            client_id="1.2",
            name="trial_started",
            params={"days": 7},
            occurred_at=now - timedelta(hours=71),
            now=now,
        )
        stale = build_event_body(
            client_id="1.2",
            name="trial_started",
            params={"days": 7},
            occurred_at=now - timedelta(hours=73),
            now=now,
        )
        fresh_params = fresh["events"][0]["params"]
        stale_params = stale["events"][0]["params"]
        assert isinstance(fresh_params, dict) and isinstance(stale_params, dict)
        self.assertIn("timestamp_micros", fresh_params)
        self.assertNotIn("timestamp_micros", stale_params)
        self.assertEqual(fresh_params["engagement_time_msec"], "1")

    def test_send_event_classifies_statuses_without_leaking_secret(self) -> None:
        class FakeResponse:
            def __init__(self, status: int) -> None:
                self.status = status
                self.released = False

            async def read(self, _limit: int = -1) -> bytes:
                return b""

            def release(self) -> None:
                self.released = True

        class FakeSession:
            def __init__(self, response: FakeResponse | Exception) -> None:
                self.response = response
                self.calls: list[dict[str, Any]] = []

            async def post(self, url: str, **kwargs: Any) -> FakeResponse:
                self.calls.append({"url": url, **kwargs})
                if isinstance(self.response, Exception):
                    raise self.response
                return self.response

        settings = configured_settings()

        async def run() -> None:
            for status, expected in (
                (204, (True, False, None)),
                (429, (False, True, "http_429")),
                (500, (False, True, "http_5xx")),
                (400, (False, False, "http_4xx")),
            ):
                session = FakeSession(FakeResponse(status))
                outcome = await ga4_client.send_event(
                    session,
                    settings=settings,
                    client_id="1.2",
                    name="sign_up",
                    params={},
                    occurred_at=None,
                )
                self.assertEqual(
                    (outcome.sent, outcome.retryable, outcome.error_code),
                    expected,
                )
                self.assertEqual(
                    session.calls[0]["url"],
                    "https://www.google-analytics.com/mp/collect",
                )
                self.assertFalse(session.calls[0]["allow_redirects"])

            failing = FakeSession(RuntimeError("url?api_secret=server-secret"))
            network = await ga4_client.send_event(
                failing,
                settings=settings,
                client_id="1.2",
                name="sign_up",
                params={},
                occurred_at=None,
            )
            self.assertFalse(network.sent)
            self.assertTrue(network.retryable)
            self.assertEqual(network.error_code, "network_error")

        asyncio.run(run())


class MergeAndReconcileTests(DatabaseTestCase):
    async def test_account_merge_moves_rows_without_unique_conflicts(self) -> None:
        now = datetime.now(UTC)
        async with self.factory() as session:
            session.add_all(
                [
                    Ga4FunnelBinding(
                        id="b1" * 16,
                        user_id=1,
                        client_id="same.client",
                        created_at=now,
                        last_seen_at=now,
                    ),
                    Ga4FunnelBinding(
                        id="b2" * 16,
                        user_id=1,
                        client_id="other.client",
                        created_at=now,
                        last_seen_at=now,
                    ),
                    Ga4FunnelBinding(
                        id="b3" * 16,
                        user_id=2,
                        client_id="same.client",
                        created_at=now,
                        last_seen_at=now,
                    ),
                    Ga4FunnelEvent(
                        id="e1" * 16,
                        user_id=1,
                        name="sign_up",
                        dedupe_key="sign_up:user:1",
                        params_json={},
                        received_at=now,
                        state="queued",
                        attempts=0,
                        max_attempts=8,
                        next_attempt_at=now,
                    ),
                    Ga4FunnelEvent(
                        id="e2" * 16,
                        user_id=2,
                        name="sign_up",
                        dedupe_key="sign_up:user:2",
                        params_json={},
                        received_at=now,
                        state="queued",
                        attempts=0,
                        max_attempts=8,
                        next_attempt_at=now,
                    ),
                    Ga4FunnelEvent(
                        id="e3" * 16,
                        user_id=1,
                        name="trial_started",
                        dedupe_key="trial_started:user:1",
                        params_json={},
                        received_at=now,
                        state="queued",
                        attempts=0,
                        max_attempts=8,
                        next_attempt_at=now,
                    ),
                    Ga4FunnelMilestone(
                        id="m1" * 16,
                        user_id=1,
                        name="vpn_first_connected",
                        occurred_at=now,
                    ),
                    Ga4FunnelMilestone(
                        id="m2" * 16,
                        user_id=2,
                        name="vpn_first_connected",
                        occurred_at=now,
                    ),
                ]
            )
            await session.commit()

        self.plugin.setup(self.ctx)
        await events.emit(events.ACCOUNT_MERGED, {"source_user_id": 1, "target_user_id": 2})

        async with self.factory() as session:
            bindings = list((await session.scalars(select(Ga4FunnelBinding))).all())
            rows = list((await session.scalars(select(Ga4FunnelEvent))).all())
            milestones = list((await session.scalars(select(Ga4FunnelMilestone))).all())
        self.assertEqual(
            sorted((row.user_id, row.client_id) for row in bindings),
            [(2, "other.client"), (2, "same.client")],
        )
        self.assertEqual(
            sorted(row.dedupe_key for row in rows),
            ["sign_up:user:2", "trial_started:user:2"],
        )
        self.assertEqual(
            [(row.user_id, row.name) for row in milestones], [(2, "vpn_first_connected")]
        )

    async def test_reconcile_resolves_pending_and_confirms_panel_connection(self) -> None:
        await self.add_user(1, panel_user_uuid="panel-1")
        await self.add_user(2, panel_user_uuid="panel-2")
        await self.add_user(3, telegram_id=333)
        now = datetime.now(UTC)
        async with self.factory() as session:
            session.add(
                Subscription(
                    user_id=2,
                    panel_user_uuid="panel-2",
                    is_active=True,
                    start_date=now,
                    end_date=now + timedelta(days=30),
                )
            )
            session.add(
                Ga4FunnelEvent(
                    id="p" * 32,
                    user_id=None,
                    name="vpn_first_connected",
                    dedupe_key="first_connected:panel:telegram:333",
                    params_json={},
                    occurred_at=now,
                    received_at=now,
                    state="queued",
                    attempts=0,
                    max_attempts=8,
                    next_attempt_at=now,
                )
            )
            await session.commit()

        panel_service = SimpleNamespace(
            get_user_by_uuid=AsyncMock(return_value={"firstConnectedAt": "2026-11-02T10:00:00Z"})
        )
        ctx = PluginContext(
            settings=self.settings,
            session_factory=self.factory,
            services={"panel_service": panel_service},
        )
        result = await reconcile(ctx)

        self.assertEqual(result["resolved"], 1)
        self.assertEqual(result["confirmed"], 1)
        async with self.factory() as session:
            resolved = await session.scalar(
                select(Ga4FunnelEvent).where(Ga4FunnelEvent.dedupe_key == "first_connected:user:3")
            )
            confirmed = await session.scalar(
                select(Ga4FunnelMilestone).where(Ga4FunnelMilestone.user_id == 2)
            )
        self.assertIsNotNone(resolved)
        self.assertIsNotNone(confirmed)

    async def test_reconcile_is_clean_without_panel_service(self) -> None:
        ctx = PluginContext(settings=self.settings, session_factory=self.factory)
        result = await reconcile(ctx)
        self.assertEqual(result, {"resolved": 0, "confirmed": 0, "expired": 0})

    def test_extracts_first_connected_at_from_nested_panel_payload(self) -> None:
        value = {"response": {"userTraffic": {"firstConnectedAt": "2026-11-02T10:00:00Z"}}}
        parsed = extract_first_connected_at(value)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.isoformat(), "2026-11-02T10:00:00+00:00")
        self.assertIsNone(extract_first_connected_at({"firstConnectedAt": "not-a-date"}))


class MigrationAndLoaderTests(TestCase):
    def test_builtin_loader_contains_ga4funnel(self) -> None:
        names = [plugin.name for plugin in BUILTIN_PLUGINS]
        self.assertIn("ga4funnel", names)
        plugin = next(item for item in BUILTIN_PLUGINS if item is Ga4FunnelPlugin)
        self.assertEqual(plugin.name, "ga4funnel")

    def test_migration_prefix_and_idempotency(self) -> None:
        self.assertEqual(
            [migration.id for migration in CHAIN_GA4FUNNEL], ["ga4funnel.0001_initial"]
        )
        engine = create_sync_engine("sqlite://")
        try:
            migration = CHAIN_GA4FUNNEL[0]
            with engine.begin() as connection:
                migration.upgrade(connection)
                migration.upgrade(connection)
            tables = set(inspect(engine).get_table_names())
        finally:
            engine.dispose()
        self.assertTrue(
            {
                "ext_ga4funnel_bindings",
                "ext_ga4funnel_events",
                "ext_ga4funnel_milestones",
            }
            <= tables
        )


_NODE = shutil.which("node")
_HARNESS_PATH = REPO_ROOT / "tests" / "support" / "ga4_signals_harness.mjs"
_ADAPTER_PATH = REPO_ROOT / "backend" / "bot" / "plugins" / "builtin" / "ga4funnel" / "signals.mjs"


def _run_adapter_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    assert _NODE, "node is required for the GA4 adapter harness"
    payload = {**scenario, "modulePath": str(_ADAPTER_PATH)}
    proc = subprocess.run(
        [_NODE, str(_HARNESS_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    outcome: dict[str, Any] = json.loads(proc.stdout)
    return outcome


_FRAME_HARNESS_PATH = REPO_ROOT / "tests" / "support" / "ga4_frame_harness.mjs"
_FRAME_PATH = REPO_ROOT / "backend" / "bot" / "plugins" / "builtin" / "ga4funnel" / "frame.mjs"


def _run_frame_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    assert _NODE, "node is required for the GA4 frame harness"
    payload = {**scenario, "modulePath": str(_FRAME_PATH)}
    proc = subprocess.run(
        [_NODE, str(_FRAME_HARNESS_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    outcome: dict[str, Any] = json.loads(proc.stdout)
    return outcome


@pytest.mark.skipif(not _NODE, reason="node is required for the GA4 adapter harness")
class BrowserAdapterTests(TestCase):
    def test_maps_signals_to_isolated_frame_and_binds_once(self) -> None:
        outcome = _run_adapter_scenario(
            {
                "config": {
                    "ok": True,
                    "enabled": True,
                    "measurement_id": "G-ABCD1234",
                    "debug_mode": False,
                },
                "cookie": "_ga=GA1.1.111.222; rw_webapp_csrf=csrf-token",
                "authenticated": False,
                "attribution": {
                    "code": "summer",
                    "utm": {
                        "utm_source": "tg",
                        "utm_medium": "https://evil.example/token",
                    },
                },
                "signals": [
                    {
                        "action": "screen_viewed",
                        "screen": "install",
                        "surface": "app",
                        "ad_code": "summer",
                        "utm_source": "tg",
                    },
                    {
                        "action": "external_opened",
                        "screen": "install",
                        "surface": "app",
                    },
                    {
                        "action": "connect_opened",
                        "screen": "home",
                        "surface": "app",
                    },
                    {
                        "action": "auth_completed",
                        "screen": "home",
                        "surface": "app",
                        "authenticated": True,
                    },
                    {
                        "action": "trial_activated",
                        "screen": "trial",
                        "surface": "app",
                    },
                    {
                        "action": "unknown",
                        "screen": "home",
                        "surface": "app",
                    },
                ],
            }
        )

        self.assertEqual(outcome["appGtag"], "undefined")
        self.assertIsNone(outcome["appDataLayer"])
        frame = outcome["frame"]
        assert frame is not None
        self.assertEqual(frame["src"], "/api/plugins/ga4funnel/frame")
        self.assertIn("allow-scripts", frame["sandbox"])
        self.assertIn("allow-same-origin", frame["sandbox"])
        self.assertEqual(frame["referrerPolicy"], "no-referrer")

        messages = [item["message"] for item in outcome["frameMessages"]]
        init = next(item for item in messages if item["type"] == "ga4_init")
        self.assertEqual(init["measurement_id"], "G-ABCD1234")
        self.assertNotIn("server-secret", json.dumps(messages))
        events = [item for item in messages if item["type"] == "ga4_event"]
        names = [item["name"] for item in events]
        self.assertIn("page_view", names)
        self.assertIn("app_download_click", names)
        self.assertIn("connect_instructions_opened", names)
        self.assertIn("login", names)
        self.assertNotIn("trial_activated", names)
        self.assertNotIn("unknown", names)
        page_view = next(item for item in events if item["name"] == "page_view")
        self.assertEqual(
            page_view["params"]["page_location"],
            "https://app.example.com/__/install",
        )
        self.assertEqual(page_view["params"]["page_title"], "install")
        self.assertEqual(page_view["params"]["utm_source"], "tg")
        self.assertNotIn("utm_medium", page_view["params"])
        for item in events:
            self.assertTrue(
                item["params"]["page_location"].startswith("https://app.example.com/__/")
            )
            self.assertEqual(item["params"]["page_referrer"], "")

        rendered = json.dumps(outcome)
        self.assertNotIn("evil.example", rendered)
        self.assertNotIn("GA1.1", rendered)
        binds = [item for item in outcome["fetches"] if item["url"].endswith("/bind")]
        self.assertEqual(len(binds), 1)
        self.assertEqual(binds[0]["body"], {"client_id": "111.222"})
        self.assertEqual(binds[0]["headers"].get("X-CSRF-Token"), "csrf-token")

    def test_never_leaks_document_url_or_referrer_to_frame(self) -> None:
        outcome = _run_adapter_scenario(
            {
                "config": {
                    "ok": True,
                    "enabled": True,
                    "measurement_id": "G-ABCD1234",
                    "debug_mode": False,
                },
                "cookie": "_ga=GA1.1.111.222",
                "locationHref": "https://app.example.com/s/RAW-SHARE-TOKEN",
                "referrer": "https://app.example.com/s/RAW-REFERRER-TOKEN",
                "signals": [
                    {"action": "screen_viewed", "screen": "install", "surface": "app"},
                    {"action": "external_opened", "screen": "install", "surface": "app"},
                    {"action": "connect_opened", "screen": "home", "surface": "app"},
                ],
            }
        )
        rendered = json.dumps(outcome)
        self.assertNotIn("RAW-SHARE-TOKEN", rendered)
        self.assertNotIn("RAW-REFERRER-TOKEN", rendered)
        self.assertNotIn("/s/", rendered)
        messages = [item["message"] for item in outcome["frameMessages"]]
        for message in messages:
            if message["type"] != "ga4_event":
                continue
            self.assertTrue(
                message["params"]["page_location"].startswith("https://app.example.com/__/")
            )
            self.assertEqual(message["params"]["page_referrer"], "")

    def test_iframe_failure_is_an_isolated_noop(self) -> None:
        outcome = _run_adapter_scenario(
            {
                "config": {
                    "ok": True,
                    "enabled": True,
                    "measurement_id": "G-ABCD1234",
                    "debug_mode": False,
                },
                "cookie": "_ga=GA1.1.111.222",
                "failFrame": True,
                "signals": [
                    {"action": "screen_viewed", "screen": "install", "surface": "app"},
                ],
            }
        )
        self.assertEqual(outcome["appGtag"], "undefined")
        self.assertIsNone(outcome["appDataLayer"])
        self.assertEqual(outcome["frameMessages"], [])

    def test_two_logins_without_logout_signal_bind_twice(self) -> None:
        outcome = _run_adapter_scenario(
            {
                "config": {
                    "ok": True,
                    "enabled": True,
                    "measurement_id": "G-ABCD1234",
                    "debug_mode": False,
                },
                "cookie": "_ga=GA1.1.100.100",
                "authenticated": False,
                "signals": [
                    {
                        "action": "auth_completed",
                        "screen": "home",
                        "surface": "app",
                        "authenticated": True,
                        "cookie": "_ga=GA1.1.111.111",
                    },
                    {
                        "action": "auth_completed",
                        "screen": "home",
                        "surface": "app",
                        "authenticated": True,
                        "cookie": "_ga=GA1.1.222.222",
                    },
                ],
            }
        )
        binds = [item for item in outcome["fetches"] if item["url"].endswith("/bind")]
        self.assertEqual(
            [item["body"]["client_id"] for item in binds],
            ["111.111", "222.222"],
        )

    def test_disabled_config_is_a_noop(self) -> None:
        outcome = _run_adapter_scenario(
            {
                "config": {"ok": True, "enabled": False, "measurement_id": "", "debug_mode": False},
                "cookie": "_ga=GA1.1.111.222",
                "signals": [{"action": "screen_viewed", "screen": "install", "surface": "app"}],
            }
        )
        self.assertEqual(outcome["appGtag"], "undefined")
        self.assertIsNone(outcome["appDataLayer"])
        self.assertIsNone(outcome["frame"])
        self.assertEqual(outcome["frameMessages"], [])
        self.assertEqual([item for item in outcome["fetches"] if item["url"].endswith("/bind")], [])


@pytest.mark.skipif(not _NODE, reason="node is required for the GA4 frame harness")
class FrameClientTests(TestCase):
    def test_queues_gtag_and_forwards_validated_events(self) -> None:
        outcome = _run_frame_scenario(
            {
                "gtagStub": False,
                "messages": [
                    {
                        "origin": "https://evil.example",
                        "data": {"type": "ga4_init", "measurement_id": "G-ABCD1234"},
                    },
                    {
                        "data": {
                            "type": "ga4_init",
                            "measurement_id": "G-ABCD1234",
                            "debug_mode": False,
                        }
                    },
                    {
                        "data": {
                            "type": "ga4_event",
                            "name": "page_view",
                            "params": {
                                "surface": "app",
                                "screen": "install",
                                "page_title": "https://app.example.com/s/RAW-TOKEN",
                                "page_location": "https://app.example.com/s/RAW-TOKEN",
                                "page_referrer": "https://app.example.com/s/REF-TOKEN",
                                "utm_source": "tg",
                                "utm_medium": "https://evil.example/token",
                                "email": "user@example.com",
                            },
                        }
                    },
                    {
                        "data": {
                            "type": "ga4_event",
                            "name": "evil_event",
                            "params": {"email": "user@example.com"},
                        }
                    },
                ],
            }
        )
        self.assertTrue(outcome["gtagInstalled"])
        self.assertTrue(
            any(
                "googletagmanager.com/gtag/js?id=G-ABCD1234" in src
                for src in outcome["appendedScripts"]
            )
        )
        calls = outcome["dataLayer"]
        ready = [
            item for item in outcome["parentMessages"] if item["message"]["type"] == "ga4_ready"
        ]
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0]["targetOrigin"], "https://app.example.com")
        config_call = next(call for call in calls if call[0] == "config")
        self.assertEqual(config_call[1], "G-ABCD1234")
        self.assertFalse(config_call[2]["send_page_view"])
        self.assertEqual(
            config_call[2]["page_location"],
            "https://app.example.com/__/analytics-frame",
        )
        self.assertEqual(config_call[2]["page_referrer"], "")
        events = [call for call in calls if call[0] == "event"]
        self.assertEqual([call[1] for call in events], ["page_view"])
        params = events[0][2]
        self.assertEqual(params["page_location"], "https://app.example.com/__/install")
        self.assertEqual(params["page_referrer"], "")
        self.assertEqual(params["page_title"], "install")
        self.assertEqual(params["utm_source"], "tg")
        self.assertNotIn("utm_medium", params)
        self.assertNotIn("email", params)
        rendered = json.dumps(outcome)
        self.assertNotIn("RAW-TOKEN", rendered)
        self.assertNotIn("REF-TOKEN", rendered)
        self.assertNotIn("user@example.com", rendered)
        self.assertNotIn("evil.example", rendered)

    def test_stubbed_gtag_receives_configured_events(self) -> None:
        outcome = _run_frame_scenario(
            {
                "gtagStub": True,
                "messages": [
                    {
                        "data": {
                            "type": "ga4_init",
                            "measurement_id": "G-ABCD1234",
                            "debug_mode": True,
                        }
                    },
                    {
                        "data": {
                            "type": "ga4_event",
                            "name": "login",
                            "params": {"surface": "app", "screen": "home"},
                        }
                    },
                ],
            }
        )
        calls = outcome["gtagCalls"]
        config_call = next(call for call in calls if call[0] == "config")
        self.assertTrue(config_call[2]["debug_mode"])
        self.assertEqual(
            config_call[2]["page_location"],
            "https://app.example.com/__/analytics-frame",
        )
        login = next(call for call in calls if call[0] == "event" and call[1] == "login")
        self.assertEqual(login[2]["page_location"], "https://app.example.com/__/home")
        self.assertEqual(login[2]["page_referrer"], "")

    def test_invalid_init_and_messages_are_ignored(self) -> None:
        outcome = _run_frame_scenario(
            {
                "messages": [
                    {
                        "data": {
                            "type": "ga4_event",
                            "name": "page_view",
                            "params": {"screen": "install"},
                        }
                    },
                    {
                        "data": {"type": "ga4_init", "measurement_id": "UA-123"},
                    },
                    {
                        "source": "other",
                        "data": {
                            "type": "ga4_init",
                            "measurement_id": "G-ABCD1234",
                        },
                    },
                ]
            }
        )
        self.assertEqual(outcome["appendedScripts"], [])
        self.assertEqual(outcome["gtagCalls"], [])
        self.assertEqual(outcome["parentMessages"], [])


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
