from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import Settings
from db.dal import auto_renew_dal, subscription_dal

from .auto_renew import financial_retry_delay


@dataclass(frozen=True, slots=True)
class AutoRenewCancellationDecision:
    cycle_id: int | None = None
    retry_at: datetime | None = None


def cancellation_can_finalize(
    payment: Any,
    payment_id: int,
    logger: logging.Logger,
) -> bool:
    if str(getattr(payment, "status", "") or "").strip().lower() in {
        "canceled",
        "cancelled",
    }:
        return True
    logger.info("Ignoring stale cancellation for finalized payment %s", payment_id)
    return False


async def handle_auto_renew_cancellation(
    session: AsyncSession,
    *,
    payment: Any,
    user_id: int,
    cancellation_party: str | None,
    cancellation_reason: str | None,
    settings: Settings,
) -> AutoRenewCancellationDecision:
    cycle_id_raw = getattr(payment, "auto_renew_cycle_id", None)
    if cycle_id_raw is None:
        return AutoRenewCancellationDecision()
    cycle_id = int(cycle_id_raw)
    cycle = await auto_renew_dal.get_cycle(session, cycle_id, fresh=True)
    if cycle is None:
        return AutoRenewCancellationDecision(cycle_id=cycle_id)
    if cancellation_reason == "permission_revoked":
        await subscription_dal.invalidate_user_auto_renew_consent(
            session,
            user_id,
            reason="provider_permission_revoked",
            disable=True,
            provider="yookassa",
        )
        await auto_renew_dal.stop_cycle(
            session,
            cycle_id,
            "provider_permission_revoked",
            cancellation_party=cancellation_party,
            cancellation_reason=cancellation_reason,
        )
        return AutoRenewCancellationDecision(cycle_id=cycle_id)

    delay = financial_retry_delay(cancellation_reason)
    max_attempts = min(
        2,
        max(
            1,
            int(settings.AUTO_RENEW_MAX_FINANCIAL_ATTEMPTS),
        ),
    )
    candidate_retry_at = datetime.now(UTC) + delay if delay else None
    renewal_end = cycle.renewal_cycle_end
    renewal_end = (
        renewal_end.replace(tzinfo=UTC)
        if renewal_end.tzinfo is None
        else renewal_end.astimezone(UTC)
    )
    cutoff = renewal_end + timedelta(
        hours=max(
            0,
            int(settings.AUTO_RENEW_RETRY_GRACE_HOURS),
        )
    )
    if (
        bool(settings.AUTO_RENEW_RETRY_ENABLED)
        and cancellation_party != "merchant"
        and candidate_retry_at is not None
        and int(cycle.financial_attempts or 0) < max_attempts
        and candidate_retry_at <= cutoff
    ):
        await auto_renew_dal.schedule_financial_retry(
            session,
            cycle_id=cycle_id,
            next_attempt_at=candidate_retry_at,
            cancellation_party=cancellation_party,
            cancellation_reason=cancellation_reason or "unknown",
        )
        return AutoRenewCancellationDecision(
            cycle_id=cycle_id,
            retry_at=candidate_retry_at,
        )

    await auto_renew_dal.stop_cycle(
        session,
        cycle_id,
        "financial_retry_not_allowed" if delay is None else "financial_retry_unavailable",
        cancellation_party=cancellation_party,
        cancellation_reason=cancellation_reason,
    )
    return AutoRenewCancellationDecision(cycle_id=cycle_id)
