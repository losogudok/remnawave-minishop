"""Regression tests for admin subscription overrides with autoflush=False sessions.

Production sessions disable SQLAlchemy autoflush (``database_setup.py``).
Admin override handlers mutate the loaded ``Subscription`` ORM row and then
call ``SubscriptionService.sync_*_to_panel(session, user_id)``, which reloads
the active subscription in the same session. If that read uses
``populate_existing=True`` before pending changes are flushed, SQLAlchemy
silently overwrites the in-memory attributes back to the old DB values and
the handler returns 200 OK without persisting anything or updating the panel.
"""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from aiohttp import web
from sqlalchemy import update
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from bot.app.web.admin_api_impl import users_actions
from bot.handlers.admin import user_management_overrides, user_management_state
from bot.services.subscription_service_impl.core import SubscriptionService
from config.tariffs_config import Tariff
from db.dal import subscription_dal
from db.models import Base, Subscription, User
from tests.support.settings_stub import settings_stub


def _make_tariff() -> Tariff:
    return Tariff(
        key="pro",
        name_ru="Pro",
        name_en="Pro",
        billing_model="period",
        monthly_gb=10,
        premium_monthly_gb=5,
        hwid_device_limit=3,
        squad_uuids=["sq-main"],
        premium_squad_uuids=["sq-prem"],
        traffic_limit_strategy="MONTH",
        enabled_periods=[1],
        prices_rub={"1": 300},
    )


def _make_tariffs_config(tariff: Tariff) -> SimpleNamespace:
    return SimpleNamespace(
        tariffs=[tariff],
        enabled_tariffs=[tariff],
        default_tariff=tariff.key,
        require_configured=lambda key: tariff,
        require=lambda key: tariff,
        get=lambda key: tariff if key == tariff.key else None,
    )


class AdminOverrideAutoflushFalseTests(unittest.IsolatedAsyncioTestCase):
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]

    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.addAsyncCleanup(self.engine.dispose)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
            autoflush=False,
        )
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            session.add(User(user_id=1, telegram_id=1001, first_name="Admin"))
            session.add(
                User(
                    user_id=42,
                    telegram_id=4200,
                    panel_user_uuid="panel-42",
                    first_name="Target",
                )
            )
            await session.flush()
            session.add(
                Subscription(
                    subscription_id=10,
                    user_id=42,
                    panel_user_uuid="panel-42",
                    end_date=now + timedelta(days=30),
                    is_active=True,
                    tariff_key="pro",
                    tier_baseline_bytes=10 * 1024**3,
                    traffic_limit_bytes=10 * 1024**3,
                    traffic_used_bytes=0,
                    hwid_device_limit=3,
                    hwid_device_limit_is_override=False,
                    extra_hwid_devices=0,
                    premium_baseline_bytes=5 * 1024**3,
                    premium_used_bytes=6 * 1024**3,
                    premium_is_limited=True,
                    premium_unlimited_override=False,
                    premium_bonus_bytes=0,
                    regular_unlimited_override=False,
                    regular_bonus_bytes=0,
                )
            )
            await session.commit()

        self.tariff = _make_tariff()
        self.settings = settings_stub(
            tariffs_config=_make_tariffs_config(self.tariff),
            USER_HWID_DEVICE_LIMIT=3,
        )
        self.panel_updates: list[tuple[str, dict[str, Any]]] = []
        self.synced_squads: list[list[str]] = []

        async def _fake_panel_update(
            panel_uuid: str, payload: dict[str, Any], **kwargs: Any
        ) -> dict[str, Any]:
            self.panel_updates.append((panel_uuid, dict(payload)))
            return {"uuid": panel_uuid, **payload}

        async def _fake_sync_squads(panel_uuid: str, squads: list[str], **kwargs: Any) -> bool:
            self.synced_squads.append(list(squads))
            return True

        self.panel_service = SimpleNamespace(
            update_user_details_on_panel=AsyncMock(side_effect=_fake_panel_update),
            get_user_by_uuid=AsyncMock(
                return_value={
                    "uuid": "panel-42",
                    "hwidDeviceLimit": 3,
                    "trafficLimitBytes": 10 * 1024**3,
                    "activeInternalSquads": [{"uuid": "sq-main"}],
                }
            ),
        )
        self.subscription_service = SubscriptionService(
            settings=cast(Any, self.settings),
            panel_service=cast(Any, self.panel_service),
        )
        cast(Any, self.subscription_service)._resolve_tariff = lambda key, *a, **kw: self.tariff
        cast(Any, self.subscription_service)._confirmed_panel_entitlement = AsyncMock(
            side_effect=lambda u, resp, payload, **kw: resp
        )
        cast(Any, self.subscription_service)._sync_panel_squads_if_needed = AsyncMock(
            side_effect=_fake_sync_squads
        )
        cast(Any, self.subscription_service).build_effective_panel_squad_fields = AsyncMock(
            return_value={"activeInternalSquads": ["sq-main", "sq-prem"]}
        )

    def _make_request(self, body: dict[str, Any]) -> web.Request:
        req = SimpleNamespace(
            app={
                "settings": self.settings,
                "async_session_factory": self.session_factory,
                "subscription_service": self.subscription_service,
                "panel_service": self.panel_service,
            },
            match_info={"user_id": "42"},
            json=AsyncMock(return_value=body),
        )
        return cast(web.Request, req)

    async def test_web_hwid_device_limit_override_persists_and_pushes_to_panel(self) -> None:
        with (
            patch.object(users_actions, "_require_admin_user_id", return_value=1),
            patch.object(users_actions, "_invalidate_after_admin_user_mutation", AsyncMock()),
        ):
            response = await users_actions.admin_user_hwid_device_limit_route(
                self._make_request({"hwid_device_limit": 7})
            )

        self.assertEqual(response.status, 200)
        payload = json.loads(response.text or "{}")
        self.assertEqual(payload["subscription"]["hwid_device_limit"], 7)
        self.assertEqual(len(self.panel_updates), 1)
        self.assertEqual(self.panel_updates[0][1]["hwidDeviceLimit"], 7)

        async with self.session_factory() as session:
            sub = await subscription_dal.get_active_subscription_by_user_id(session, 42)
            assert sub is not None
            self.assertEqual(sub.hwid_device_limit, 7)
            self.assertTrue(sub.hwid_device_limit_is_override)

    async def test_web_premium_override_persists_and_restores_premium_squads(self) -> None:
        with (
            patch.object(users_actions, "_require_admin_user_id", return_value=1),
            patch.object(users_actions, "_invalidate_after_admin_user_mutation", AsyncMock()),
        ):
            response = await users_actions.admin_user_premium_override_route(
                self._make_request({"unlimited": True, "bonus_gb": 25})
            )

        self.assertEqual(response.status, 200)
        payload = json.loads(response.text or "{}")
        self.assertTrue(payload["subscription"]["premium_unlimited_override"])
        self.assertEqual(payload["subscription"]["premium_bonus_bytes"], 25 * 1024**3)
        self.assertEqual(self.synced_squads, [["sq-main", "sq-prem"]])

        async with self.session_factory() as session:
            sub = await subscription_dal.get_active_subscription_by_user_id(session, 42)
            assert sub is not None
            self.assertTrue(sub.premium_unlimited_override)
            self.assertEqual(sub.premium_bonus_bytes, 25 * 1024**3)
            self.assertFalse(sub.premium_is_limited)

    async def test_web_regular_traffic_override_persists_and_pushes_to_panel(self) -> None:
        with (
            patch.object(users_actions, "_require_admin_user_id", return_value=1),
            patch.object(users_actions, "_invalidate_after_admin_user_mutation", AsyncMock()),
        ):
            response = await users_actions.admin_user_regular_traffic_override_route(
                self._make_request({"unlimited": True, "regular_bonus_gb": 50})
            )

        self.assertEqual(response.status, 200)
        payload = json.loads(response.text or "{}")
        self.assertTrue(payload["subscription"]["regular_unlimited_override"])
        self.assertEqual(payload["subscription"]["regular_bonus_bytes"], 50 * 1024**3)
        self.assertEqual(len(self.panel_updates), 1)
        self.assertEqual(self.panel_updates[0][1]["trafficLimitBytes"], 0)

        async with self.session_factory() as session:
            sub = await subscription_dal.get_active_subscription_by_user_id(session, 42)
            assert sub is not None
            self.assertTrue(sub.regular_unlimited_override)
            self.assertEqual(sub.regular_bonus_bytes, 50 * 1024**3)
            self.assertEqual(sub.traffic_limit_bytes, 0)

    async def test_web_hwid_override_rolls_back_flushed_update_on_panel_failure(self) -> None:
        cast(Any, self.subscription_service)._confirmed_panel_entitlement = AsyncMock(
            return_value=None
        )
        with (
            patch.object(users_actions, "_require_admin_user_id", return_value=1),
            patch.object(users_actions, "_invalidate_after_admin_user_mutation", AsyncMock()),
        ):
            response = await users_actions.admin_user_hwid_device_limit_route(
                self._make_request({"hwid_device_limit": 9})
            )

        self.assertEqual(response.status, 502)
        async with self.session_factory() as session:
            sub = await subscription_dal.get_active_subscription_by_user_id(session, 42)
            assert sub is not None
            self.assertEqual(sub.hwid_device_limit, 3)
            self.assertFalse(sub.hwid_device_limit_is_override)

    async def test_bot_override_callbacks_persist_with_autoflush_false(self) -> None:
        i18n = SimpleNamespace(gettext=lambda lang, key, **kw: key)
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=1001),
            answer=AsyncMock(),
        )
        async with self.session_factory() as session:
            target = await session.get(User, 42)
            assert target is not None
            with patch.object(user_management_overrides, "handle_refresh_user_card", AsyncMock()):
                await user_management_overrides.handle_hwid_limit_apply(
                    cast(Any, callback),
                    target,
                    self.subscription_service,
                    session,
                    cast(Any, self.settings),
                    cast(Any, i18n),
                    "en",
                    hwid_device_limit=8,
                )
                await user_management_overrides.handle_premium_override_apply(
                    cast(Any, callback),
                    target,
                    self.subscription_service,
                    session,
                    cast(Any, self.settings),
                    cast(Any, i18n),
                    "en",
                    unlimited=False,
                    bonus_bytes=12 * 1024**3,
                )

        self.assertEqual(self.panel_updates[-1][1]["hwidDeviceLimit"], 8)
        self.assertEqual(self.synced_squads[-1], ["sq-main", "sq-prem"])

        async with self.session_factory() as session:
            sub = await subscription_dal.get_active_subscription_by_user_id(session, 42)
            assert sub is not None
            self.assertEqual(sub.hwid_device_limit, 8)
            self.assertTrue(sub.hwid_device_limit_is_override)
            self.assertEqual(sub.premium_bonus_bytes, 12 * 1024**3)
            self.assertFalse(sub.premium_is_limited)

    async def test_bot_state_override_handlers_persist_with_autoflush_false(self) -> None:
        i18n = SimpleNamespace(gettext=lambda lang, key, **kw: key)
        state = SimpleNamespace(
            get_data=AsyncMock(return_value={"target_user_id": 42, "card_origin": ""}),
            clear=AsyncMock(),
        )
        message_hwid = SimpleNamespace(
            text="6",
            from_user=SimpleNamespace(id=1001),
            bot=SimpleNamespace(),
            answer=AsyncMock(),
            reply=AsyncMock(),
        )
        message_premium = SimpleNamespace(
            text="15",
            from_user=SimpleNamespace(id=1001),
            bot=SimpleNamespace(),
            answer=AsyncMock(),
            reply=AsyncMock(),
        )
        async with self.session_factory() as session:
            with (
                patch.object(
                    user_management_state, "_resolve_bot_username", AsyncMock(return_value="bot")
                ),
                patch.object(
                    user_management_state, "format_user_card", AsyncMock(return_value="card")
                ),
                patch.object(
                    user_management_state,
                    "get_user_card_keyboard",
                    return_value=SimpleNamespace(as_markup=lambda: None),
                ),
                patch.object(
                    user_management_state, "_send_with_profile_link_fallback", AsyncMock()
                ),
            ):
                await user_management_state.process_hwid_device_limit_handler(
                    cast(Any, message_hwid),
                    cast(Any, state),
                    cast(Any, self.settings),
                    {"current_language": "en", "i18n_instance": i18n},
                    self.subscription_service,
                    session,
                )
                await user_management_state.process_premium_override_bonus_handler(
                    cast(Any, message_premium),
                    cast(Any, state),
                    cast(Any, self.settings),
                    {"current_language": "en", "i18n_instance": i18n},
                    self.subscription_service,
                    session,
                )

        async with self.session_factory() as session:
            sub = await subscription_dal.get_active_subscription_by_user_id(session, 42)
            assert sub is not None
            self.assertEqual(sub.hwid_device_limit, 6)
            self.assertTrue(sub.hwid_device_limit_is_override)
            self.assertEqual(sub.premium_bonus_bytes, 15 * 1024**3)

    async def test_get_active_subscription_fresh_opt_in_reloads_concurrent_update(self) -> None:
        async with self.session_factory() as session:
            loaded = await subscription_dal.get_active_subscription_by_user_id(session, 42)
            assert loaded is not None
            self.assertEqual(loaded.hwid_device_limit, 3)

            async with self.session_factory() as other:
                await other.execute(
                    update(Subscription)
                    .where(Subscription.subscription_id == 10)
                    .values(hwid_device_limit=11)
                )
                await other.commit()

            refreshed = await subscription_dal.get_active_subscription_by_user_id(
                session, 42, fresh=True
            )
            assert refreshed is not None
            self.assertEqual(refreshed.hwid_device_limit, 11)


if __name__ == "__main__":
    unittest.main()
