"""Authoritative server-side funnel capture from Core domain events."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from bot.infra import events
from bot.plugins.extensions import OperationContext
from bot.plugins.spec import PluginContext

from . import store
from .config import PURCHASE, SIGN_UP, TRIAL_STARTED, VPN_FIRST_CONNECTED, ga4_configured

logger = logging.getLogger(__name__)


def parse_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return store.utc(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


async def _store_in_short_transaction(
    ctx: PluginContext,
    callback: Any,
) -> None:
    factory = ctx.session_factory
    if factory is None:
        return
    async with factory() as session:
        await callback(session)
        await session.commit()


async def on_user_registered(ctx: PluginContext, payload: dict[str, Any]) -> None:
    if not ga4_configured(ctx.settings):
        return
    # Panel-sync rows are technical copies, not real user creations.
    if str(payload.get("registered_via") or "") == "panel_sync":
        return
    try:
        user_id = int(payload["user_id"])
    except (KeyError, TypeError, ValueError):
        return

    async def write(session: Any) -> None:
        await store.record_event(
            session,
            name=SIGN_UP,
            dedupe_key=f"sign_up:user:{user_id}",
            user_id=user_id,
            params={},
            occurred_at=datetime.now(UTC),
        )

    await _store_in_short_transaction(ctx, write)


async def on_trial_activated(ctx: PluginContext, payload: dict[str, Any]) -> None:
    if not ga4_configured(ctx.settings):
        return
    try:
        user_id = int(payload["user_id"])
        days = int(payload["days"])
    except (KeyError, TypeError, ValueError):
        return
    params: dict[str, Any] = {"days": days}
    traffic_gb = payload.get("traffic_gb")
    if traffic_gb is not None:
        params["traffic_gb"] = traffic_gb
    occurred_at = datetime.now(UTC)

    async def write(session: Any) -> None:
        await store.record_milestone(
            session,
            user_id=user_id,
            name=TRIAL_STARTED,
            occurred_at=occurred_at,
        )
        await store.record_event(
            session,
            name=TRIAL_STARTED,
            dedupe_key=f"trial_started:user:{user_id}",
            user_id=user_id,
            params=params,
            occurred_at=occurred_at,
        )

    await _store_in_short_transaction(ctx, write)


async def on_panel_webhook(ctx: PluginContext, payload: dict[str, Any]) -> None:
    if not ga4_configured(ctx.settings):
        return
    if str(payload.get("event") or "") != "user.first_connected":
        return
    panel_user_uuid = str(payload.get("panel_user_uuid") or "").strip() or None
    telegram_id = payload.get("telegram_id")
    occurred_at = parse_timestamp(payload.get("occurred_at")) or datetime.now(UTC)
    factory = ctx.session_factory
    if factory is None:
        return
    async with factory() as session:
        user_id = await store.find_user_id(
            session,
            panel_user_uuid=panel_user_uuid,
            telegram_id=telegram_id,
        )
        if user_id is not None:
            await store.record_first_connected(
                session,
                user_id=user_id,
                occurred_at=occurred_at,
            )
        else:
            identity: str | None = None
            if panel_user_uuid is not None:
                identity = f"panel_uuid:{panel_user_uuid}"
            elif isinstance(telegram_id, (int, str)):
                numeric_telegram_id = str(telegram_id).strip()
                if numeric_telegram_id.isdigit():
                    identity = f"telegram:{numeric_telegram_id}"
            if identity is not None:
                await store.record_event(
                    session,
                    name=VPN_FIRST_CONNECTED,
                    dedupe_key=f"first_connected:panel:{identity}",
                    params={},
                    occurred_at=occurred_at,
                )
        await session.commit()


async def on_account_merged(ctx: PluginContext, payload: dict[str, Any]) -> None:
    try:
        source_user_id = int(payload["source_user_id"])
        target_user_id = int(payload["target_user_id"])
    except (KeyError, TypeError, ValueError):
        return
    if source_user_id == target_user_id:
        return

    async def write(session: Any) -> None:
        await store.move_account(
            session,
            source_user_id=source_user_id,
            target_user_id=target_user_id,
        )

    await _store_in_short_transaction(ctx, write)


async def capture_payment(
    operation: OperationContext,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Durable ``payment.succeeded`` job: store one real purchase event."""

    if not ga4_configured(operation.runtime.settings):
        return {"stored": False}
    data = payload.get("payload")
    if not isinstance(data, dict):
        return {"stored": False}
    try:
        payment_db_id = int(data["payment_db_id"])
        amount = float(data["amount"])
    except (KeyError, TypeError, ValueError):
        return {"stored": False}
    payload_user_id: int | None = None
    raw_user_id = data.get("user_id")
    if isinstance(raw_user_id, (int, str)):
        try:
            payload_user_id = int(raw_user_id)
        except (TypeError, ValueError):
            payload_user_id = None
    raw_months = data.get("months")
    months: int | None = None
    if isinstance(raw_months, (int, float, str)) and str(raw_months).strip():
        try:
            months = int(float(raw_months))
        except (TypeError, ValueError):
            months = None
    if months is None:
        months = await store.payment_months(
            operation.runtime.require_session_factory(),
            payment_db_id,
        )
    params = {
        "transaction_id": str(payment_db_id),
        "value": amount,
        "currency": str(data.get("currency") or ""),
        "sale_mode": str(data.get("sale_mode") or ""),
        "months": int(months) if months is not None else 0,
    }
    factory = operation.runtime.session_factory
    if factory is None:
        return {"stored": False}
    payment_user_id = await store.payment_owner(factory, payment_db_id)
    async with factory() as session:
        owner_id: int | None = None
        for candidate in (operation.user_id, payment_user_id, payload_user_id):
            owner_id = await store.resolve_account_alias(session, candidate)
            if owner_id is not None:
                break
        if owner_id is None:
            return {"stored": False}
        occurred_at = datetime.now(UTC)
        await store.record_milestone(
            session,
            user_id=owner_id,
            name=PURCHASE,
            occurred_at=occurred_at,
        )
        stored = await store.record_event(
            session,
            name=PURCHASE,
            dedupe_key=f"purchase:payment:{payment_db_id}",
            user_id=owner_id,
            params=params,
            occurred_at=occurred_at,
        )
        await session.commit()
    return {"stored": stored}


def register_event_handlers(ctx: PluginContext) -> None:
    async def user_registered(event_name: str, payload: dict[str, Any]) -> None:
        try:
            await on_user_registered(ctx, payload)
        except Exception:
            logger.warning("GA4 funnel could not capture %s", event_name)

    async def trial_activated(event_name: str, payload: dict[str, Any]) -> None:
        try:
            await on_trial_activated(ctx, payload)
        except Exception:
            logger.warning("GA4 funnel could not capture %s", event_name)

    async def panel_webhook(event_name: str, payload: dict[str, Any]) -> None:
        try:
            await on_panel_webhook(ctx, payload)
        except Exception:
            logger.warning("GA4 funnel could not capture %s", event_name)

    async def account_merged(event_name: str, payload: dict[str, Any]) -> None:
        try:
            await on_account_merged(ctx, payload)
        except Exception:
            logger.warning("GA4 funnel could not capture %s", event_name)

    events.subscribe(events.USER_REGISTERED, user_registered)
    events.subscribe(events.TRIAL_ACTIVATED, trial_activated)
    events.subscribe(events.PANEL_WEBHOOK_RECEIVED, panel_webhook)
    events.subscribe(events.ACCOUNT_MERGED, account_merged)


__all__ = [
    "capture_payment",
    "on_account_merged",
    "on_panel_webhook",
    "on_trial_activated",
    "on_user_registered",
    "parse_timestamp",
    "register_event_handlers",
]
