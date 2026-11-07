"""Broadcast callback outbox reporting for Web Push deliveries.

The plugin's queue row is authoritative. A Core broadcast delivery is updated
only after the terminal push row is persisted, and the outbox marker is set
only after that update succeeds. Repeated reports are idempotent in the Core
DAL, so a crash or a failed callback is retried without resending the push.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import sessionmaker

from .store import DELIVERY_SENT, WebPushStore

logger = logging.getLogger(__name__)


async def report_pending_broadcast_callbacks(
    settings: Any,
    session_factory: sessionmaker | None = None,
    *,
    limit: int = 32,
) -> int:
    """Report unreported terminal push rows; return the confirmed count."""

    from db.dal import broadcast_dal

    if session_factory is None:
        # Revocation/account-switch callbacks may not own a request-scoped
        # factory; use the one the plugin configured during setup.
        from .broadcast import get_broadcast_session_factory

        session_factory = get_broadcast_session_factory()
        if session_factory is None:
            return 0

    store = WebPushStore(settings, session_factory)
    candidates = await store.list_unreported_broadcast_callbacks(limit=limit)
    confirmed = 0
    for candidate in candidates:
        success = candidate.state == DELIVERY_SENT
        try:
            async with session_factory() as session:
                await broadcast_dal.mark_delivery_result(
                    session,
                    candidate.broadcast_delivery_id,
                    success=success,
                    error=None if success else (candidate.error_code or "delivery_failed"),
                )
        except Exception:
            logger.exception(
                "Failed to report Web Push callback for broadcast delivery %s.",
                candidate.broadcast_delivery_id,
            )
            continue
        await store.mark_callback_reported(candidate.id)
        confirmed += 1
    return confirmed


__all__ = ["report_pending_broadcast_callbacks"]
