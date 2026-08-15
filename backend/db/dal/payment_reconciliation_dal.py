from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import joinedload

from db.models import Payment


async def mark_provider_payment_checked(
    session: AsyncSession,
    payment_db_id: int,
    *,
    checkout_expires_at: datetime | None = None,
) -> Payment | None:
    """Rotate a remotely inspected pending payment without changing its status."""

    result = await session.execute(
        select(Payment)
        .where(Payment.payment_id == payment_db_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    payment = result.scalar_one_or_none()
    if payment is None:
        return None
    payment.provider_checked_at = func.now()
    if checkout_expires_at is not None:
        payment.checkout_expires_at = checkout_expires_at
    await session.flush()
    await session.refresh(payment)
    return payment


async def list_candidates(
    session: AsyncSession,
    *,
    providers: tuple[str, ...],
    limit: int = 100,
    retry_after_seconds: int = 60,
) -> list[Payment]:
    """Return pending hosted checkouts in least-recently-inspected order."""

    if not providers:
        return []
    cutoff = datetime.now(UTC) - timedelta(seconds=max(1, int(retry_after_seconds)))
    normalized_status = func.lower(func.trim(Payment.status))
    stmt = (
        select(Payment)
        .where(
            func.lower(Payment.provider).in_(tuple(provider.lower() for provider in providers)),
            or_(
                Payment.provider_payment_id.isnot(None),
                Payment.yookassa_payment_id.isnot(None),
                and_(
                    func.lower(Payment.provider).in_(("cloudpayments", "overpay")),
                    Payment.is_auto_renew.is_(True),
                ),
            ),
            or_(
                normalized_status == "pending",
                normalized_status.like("pending_%"),
                normalized_status.in_(
                    (
                        "active",
                        "created",
                        "new",
                        "open",
                        "process",
                        "processing",
                        "creation_unknown",
                        "activation_failed",
                        "succeeded_pending_finalization",
                        "underpaid",
                        "waiting_for_capture",
                    )
                ),
            ),
            or_(
                Payment.provider_checked_at.is_(None),
                Payment.provider_checked_at <= cutoff,
            ),
        )
        .options(joinedload(Payment.user), joinedload(Payment.promo_code_used))
        .order_by(
            Payment.provider_checked_at.asc().nullsfirst(),
            Payment.created_at.asc(),
            Payment.payment_id.asc(),
        )
        .limit(max(1, int(limit)))
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def list_unsent_failure_notifications(
    session: AsyncSession,
    *,
    limit: int = 100,
    older_than_seconds: int = 0,
) -> list[Payment]:
    """Return new terminal failures whose user notification still needs delivery."""

    normalized_status = func.lower(func.trim(Payment.status))
    conditions = [
        normalized_status.in_(("failed", "canceled", "cancelled", "failed_creation")),
        Payment.failure_notified_at.is_(None),
    ]
    if older_than_seconds > 0:
        cutoff = datetime.now(UTC) - timedelta(seconds=int(older_than_seconds))
        conditions.append(func.coalesce(Payment.updated_at, Payment.created_at) <= cutoff)
    stmt = (
        select(Payment)
        .where(*conditions)
        .options(joinedload(Payment.user), joinedload(Payment.promo_code_used))
        .order_by(Payment.updated_at.asc().nullsfirst(), Payment.payment_id.asc())
        .limit(max(1, int(limit)))
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def mark_user_failures_superseded_by_success(
    session: AsyncSession,
    *,
    user_id: int,
    succeeded_at: datetime,
) -> int:
    """Silence unresolved failures that predate a successful retry."""

    normalized_status = func.lower(func.trim(Payment.status))
    result = await session.execute(
        update(Payment)
        .where(
            Payment.user_id == user_id,
            normalized_status.in_(("failed", "canceled", "cancelled", "failed_creation")),
            Payment.failure_notified_at.is_(None),
            func.coalesce(Payment.updated_at, Payment.created_at) <= succeeded_at,
        )
        .values(failure_notified_at=func.now())
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def mark_failure_notification_sent(
    session: AsyncSession,
    payment_db_id: int,
) -> bool:
    """Persist successful failure-event delivery exactly once."""

    stmt = (
        update(Payment)
        .where(
            Payment.payment_id == payment_db_id,
            Payment.failure_notified_at.is_(None),
        )
        .values(failure_notified_at=func.now())
    )
    result = await session.execute(stmt)
    return bool(getattr(result, "rowcount", 0))
