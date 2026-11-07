"""Panel-backed reconciliation for confirmed first connections."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from bot.plugins.extensions import OperationContext
from bot.plugins.spec import PluginContext

from . import store
from .capture import parse_timestamp
from .config import ga4_configured

logger = logging.getLogger(__name__)

PANEL_LOOKUP_TIMEOUT_SECONDS = 5.0


def extract_first_connected_at(value: object, *, depth: int = 0) -> datetime | None:
    """Find the first panel-confirmed ``firstConnectedAt`` value."""

    if depth > 4:
        return None
    if isinstance(value, dict):
        for key in ("firstConnectedAt", "first_connected_at"):
            if key in value:
                parsed = parse_timestamp(value[key])
                if parsed is not None:
                    return parsed
        for nested in value.values():
            parsed = extract_first_connected_at(nested, depth=depth + 1)
            if parsed is not None:
                return parsed
    elif isinstance(value, (list, tuple)):
        for nested in value:
            parsed = extract_first_connected_at(nested, depth=depth + 1)
            if parsed is not None:
                return parsed
    return None


async def _resolve_pending(ctx: PluginContext) -> int:
    factory = ctx.session_factory
    if factory is None:
        return 0
    resolved = 0
    pending = await store.list_pending_first_connected(factory)
    for item in pending:
        identity = item.dedupe_key.removeprefix("first_connected:panel:")
        kind, _, subject = identity.partition(":")
        panel_user_uuid: str | None = None
        telegram_id: str | None = None
        if kind == "panel_uuid" and subject:
            panel_user_uuid = subject
        elif kind == "telegram" and subject.isdigit():
            telegram_id = subject
        else:
            # Legacy ambiguous keys are ignored instead of guessing an identity.
            continue
        async with factory() as session:
            user_id = await store.find_user_id(
                session,
                panel_user_uuid=panel_user_uuid,
                telegram_id=telegram_id,
            )
            if user_id is None:
                continue
            await store.resolve_pending_first_connected(
                session,
                pending_id=item.id,
                user_id=user_id,
                occurred_at=item.occurred_at,
            )
            await session.commit()
            resolved += 1
    return resolved


async def _confirm_panel_first_connections(ctx: PluginContext) -> int:
    factory = ctx.session_factory
    panel_service = ctx.panel_service
    if factory is None or panel_service is None:
        return 0
    confirmed = 0
    for candidate in await store.first_connected_candidates(factory):
        try:
            async with asyncio.timeout(PANEL_LOOKUP_TIMEOUT_SECONDS):
                panel_user = await panel_service.get_user_by_uuid(candidate.panel_user_uuid)
        except Exception:
            logger.warning("GA4 funnel panel lookup failed for a user candidate")
            continue
        occurred_at = extract_first_connected_at(panel_user)
        if occurred_at is None:
            continue
        async with factory() as session:
            await store.record_first_connected(
                session,
                user_id=candidate.user_id,
                occurred_at=occurred_at,
            )
            await session.commit()
            confirmed += 1
    return confirmed


async def reconcile(ctx: PluginContext) -> dict[str, Any]:
    result: dict[str, Any] = {"resolved": 0, "confirmed": 0, "expired": 0}
    if ctx.session_factory is None or not ga4_configured(ctx.settings):
        return result
    result["resolved"] = await _resolve_pending(ctx)
    result["confirmed"] = await _confirm_panel_first_connections(ctx)
    result["expired"] = await store.expire_unbound(
        ctx.require_session_factory(),
        now=datetime.now(UTC),
    )
    return result


async def run_reconcile(
    operation: OperationContext,
    payload: dict[str, Any],
) -> dict[str, Any]:
    _ = payload
    return await reconcile(operation.runtime)


__all__ = ["extract_first_connected_at", "reconcile", "run_reconcile"]
