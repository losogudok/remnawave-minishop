from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from db.models import Payment

_WATA_RECONCILABLE_STATUSES = (
    "pending_wata",
    "pending",
    "created",
    "succeeded_pending_finalization",
)


async def list_candidates(
    session: AsyncSession,
    *,
    limit: int = 100,
    grace_seconds: int = 30,
) -> list[Payment]:
    """Return old pending Wata links that may need terminal reconciliation."""

    cutoff = datetime.now(UTC) - timedelta(seconds=max(0, int(grace_seconds)))
    normalized_provider = func.lower(func.trim(Payment.provider))
    normalized_status = func.lower(func.trim(Payment.status))
    stmt = (
        select(Payment)
        .where(
            normalized_provider.in_(("wata", "wata_crypto")),
            normalized_status.in_(_WATA_RECONCILABLE_STATUSES),
            Payment.provider_payment_id.isnot(None),
            Payment.provider_payment_url.isnot(None),
            func.length(func.trim(Payment.provider_payment_url)) > 0,
            Payment.created_at <= cutoff,
        )
        .order_by(Payment.created_at.asc(), Payment.payment_id.asc())
        .limit(max(1, int(limit)))
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
