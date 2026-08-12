from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, and_, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from db.models import Payment
from db.partner_models import PartnerLedgerEntry


async def list_stale_partner_balance_payments(
    session: AsyncSession,
    *,
    older_than: datetime,
    limit: int,
) -> list[Payment]:
    last_activity = func.coalesce(Payment.updated_at, Payment.created_at)
    spend = aliased(PartnerLedgerEntry, name="partner_spend")
    release = aliased(PartnerLedgerEntry, name="partner_spend_release")
    result = await session.execute(
        select(Payment)
        .join(
            spend,
            and_(
                spend.reference_type == "payment",
                spend.reference_id == cast(Payment.payment_id, String),
                spend.kind == "subscription_spend",
            ),
        )
        .outerjoin(
            release,
            and_(
                release.reference_type == "payment",
                release.reference_id == cast(Payment.payment_id, String),
                release.kind == "subscription_spend_release",
            ),
        )
        .where(
            Payment.provider == "partner_balance",
            Payment.status == "succeeded_pending_finalization",
            last_activity < older_than,
            release.entry_id.is_(None),
        )
        .order_by(last_activity, Payment.payment_id)
        .limit(limit)
        .with_for_update(skip_locked=True, of=Payment)
    )
    return list(result.scalars().all())


async def list_terminal_partner_checkout_payments(
    session: AsyncSession,
    *,
    statuses: set[str] | frozenset[str],
    limit: int,
) -> list[Payment]:
    spend = aliased(PartnerLedgerEntry, name="partner_checkout_spend")
    release = aliased(PartnerLedgerEntry, name="partner_checkout_spend_release")
    normalized_status = func.lower(func.trim(Payment.status))
    result = await session.execute(
        select(Payment)
        .join(
            spend,
            and_(
                spend.reference_type == "payment",
                spend.reference_id == cast(Payment.payment_id, String),
                spend.kind == "checkout_spend",
            ),
        )
        .outerjoin(
            release,
            and_(
                release.reference_type == "payment",
                release.reference_id == cast(Payment.payment_id, String),
                release.kind == "checkout_spend_release",
                release.state == "posted",
            ),
        )
        .where(
            normalized_status.in_(tuple(sorted(statuses))),
            release.entry_id.is_(None),
        )
        .order_by(Payment.payment_id)
        .limit(limit)
        .with_for_update(skip_locked=True, of=Payment)
    )
    return list(result.scalars().all())


async def list_stale_partner_checkout_payments(
    session: AsyncSession,
    *,
    older_than: datetime,
    limit: int,
) -> list[Payment]:
    last_activity = func.coalesce(Payment.updated_at, Payment.created_at)
    spend = aliased(PartnerLedgerEntry, name="stale_partner_checkout_spend")
    release = aliased(PartnerLedgerEntry, name="stale_partner_checkout_spend_release")
    result = await session.execute(
        select(Payment)
        .join(
            spend,
            and_(
                spend.reference_type == "payment",
                spend.reference_id == cast(Payment.payment_id, String),
                spend.kind == "checkout_spend",
            ),
        )
        .outerjoin(
            release,
            and_(
                release.reference_type == "payment",
                release.reference_id == cast(Payment.payment_id, String),
                release.kind == "checkout_spend_release",
                release.state == "posted",
            ),
        )
        .where(
            Payment.status == "succeeded_pending_finalization",
            last_activity < older_than,
            release.entry_id.is_(None),
        )
        .order_by(last_activity, Payment.payment_id)
        .limit(limit)
        .with_for_update(skip_locked=True, of=Payment)
    )
    return list(result.scalars().all())
