from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, case, desc, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Payment, User
from db.partner_models import (
    PartnerApplication,
    PartnerClient,
    PartnerCommission,
    PartnerLedgerEntry,
    PartnerProfile,
    PartnerWithdrawal,
)


async def attention_counts(session: AsyncSession) -> dict[str, int]:
    applications = int(
        (
            await session.execute(
                select(func.count())
                .select_from(PartnerApplication)
                .where(PartnerApplication.status == "pending")
            )
        ).scalar_one()
        or 0
    )
    withdrawals = int(
        (
            await session.execute(
                select(func.count())
                .select_from(PartnerWithdrawal)
                .where(PartnerWithdrawal.status == "requested")
            )
        ).scalar_one()
        or 0
    )
    return {
        "pending_applications": applications,
        "requested_withdrawals": withdrawals,
        "total": applications + withdrawals,
    }


async def list_profiles(
    session: AsyncSession,
    *,
    status: str | None,
    search: str | None,
    limit: int,
    offset: int,
) -> tuple[list[PartnerProfile], int]:
    conditions: list[Any] = []
    if status and status != "all":
        conditions.append(PartnerProfile.status == status)
    normalized_search = str(search or "").strip()
    if normalized_search:
        pattern = f"%{normalized_search}%"
        variants: list[Any] = [
            PartnerProfile.display_label_snapshot.ilike(pattern),
            PartnerProfile.partner_code.ilike(pattern),
        ]
        if normalized_search.isdigit():
            variants.extend(
                (
                    PartnerProfile.partner_id == int(normalized_search),
                    PartnerProfile.user_id == int(normalized_search),
                )
            )
        conditions.append(or_(*variants))
    where = and_(*conditions) if conditions else true()
    total = int(
        (
            await session.execute(select(func.count()).select_from(PartnerProfile).where(where))
        ).scalar_one()
        or 0
    )
    result = await session.execute(
        select(PartnerProfile)
        .where(where)
        .order_by(desc(PartnerProfile.created_at), desc(PartnerProfile.partner_id))
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all()), total


async def referral_import_candidates(
    session: AsyncSession,
    *,
    partner_user_id: int,
) -> list[tuple[User, PartnerClient | None, int]]:
    payment_count = (
        select(func.count(Payment.payment_id))
        .where(Payment.user_id == User.user_id, Payment.status == "succeeded")
        .correlate(User)
        .scalar_subquery()
    )
    rows = (
        await session.execute(
            select(User, PartnerClient, payment_count)
            .outerjoin(PartnerClient, PartnerClient.client_user_id == User.user_id)
            .where(User.referred_by_id == partner_user_id)
            .order_by(User.user_id)
        )
    ).all()
    return [(row[0], row[1], int(row[2] or 0)) for row in rows]


async def all_referral_import_candidates(
    session: AsyncSession,
) -> list[tuple[PartnerProfile, User, PartnerClient | None, int]]:
    payment_count = (
        select(func.count(Payment.payment_id))
        .where(Payment.user_id == User.user_id, Payment.status == "succeeded")
        .correlate(User)
        .scalar_subquery()
    )
    rows = (
        await session.execute(
            select(PartnerProfile, User, PartnerClient, payment_count)
            .select_from(PartnerProfile)
            .join(User, User.referred_by_id == PartnerProfile.user_id)
            .outerjoin(PartnerClient, PartnerClient.client_user_id == User.user_id)
            .where(PartnerProfile.user_id.is_not(None))
            .order_by(PartnerProfile.partner_id, User.user_id)
        )
    ).all()
    return [(row[0], row[1], row[2], int(row[3] or 0)) for row in rows]


async def overview_metrics(session: AsyncSession, *, currency: str) -> dict[str, int]:
    active = int(
        (
            await session.execute(
                select(func.count())
                .select_from(PartnerProfile)
                .where(PartnerProfile.status == "active")
            )
        ).scalar_one()
        or 0
    )
    paused = int(
        (
            await session.execute(
                select(func.count())
                .select_from(PartnerProfile)
                .where(PartnerProfile.status == "paused")
            )
        ).scalar_one()
        or 0
    )
    clients = int(
        (await session.execute(select(func.count()).select_from(PartnerClient))).scalar_one() or 0
    )
    commission_row = (
        await session.execute(
            select(
                func.coalesce(func.sum(PartnerCommission.gross_amount_minor), 0),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                PartnerCommission.status == "reversed",
                                -PartnerCommission.commission_amount_minor,
                            ),
                            else_=PartnerCommission.commission_amount_minor,
                        )
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                PartnerCommission.status == "pending",
                                PartnerCommission.commission_amount_minor,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
            ).where(
                func.upper(PartnerCommission.currency) == currency.upper(),
                PartnerCommission.status != "excluded",
            )
        )
    ).one()
    ledger_available = int(
        (
            await session.execute(
                select(func.coalesce(func.sum(PartnerLedgerEntry.amount_minor), 0)).where(
                    func.upper(PartnerLedgerEntry.currency) == currency.upper(),
                    PartnerLedgerEntry.state == "posted",
                )
            )
        ).scalar_one()
        or 0
    )
    withdrawals = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (
                                PartnerWithdrawal.status == "paid",
                                PartnerWithdrawal.debit_amount_minor,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                PartnerWithdrawal.status.in_(("requested", "processing")),
                                PartnerWithdrawal.debit_amount_minor,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
            ).where(func.upper(PartnerWithdrawal.debit_currency) == currency.upper())
        )
    ).one()
    return {
        "active_partners": active,
        "paused_partners": paused,
        "clients": clients,
        "gross_minor": int(commission_row[0] or 0),
        "commissions_minor": int(commission_row[1] or 0),
        "pending_minor": int(commission_row[2] or 0),
        "available_minor": ledger_available,
        "paid_minor": int(withdrawals[0] or 0),
        "requested_minor": int(withdrawals[1] or 0),
    }


async def overview_series(
    session: AsyncSession,
    *,
    currency: str,
    since: datetime,
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(
                func.date(PartnerCommission.created_at).label("day"),
                func.coalesce(func.sum(PartnerCommission.gross_amount_minor), 0).label("gross"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                PartnerCommission.status == "reversed",
                                -PartnerCommission.commission_amount_minor,
                            ),
                            else_=PartnerCommission.commission_amount_minor,
                        )
                    ),
                    0,
                ).label("commission"),
            )
            .where(
                func.upper(PartnerCommission.currency) == currency.upper(),
                PartnerCommission.created_at >= since,
                PartnerCommission.status != "excluded",
            )
            .group_by(func.date(PartnerCommission.created_at))
            .order_by(func.date(PartnerCommission.created_at))
        )
    ).all()
    payout_rows = (
        await session.execute(
            select(
                func.date(PartnerWithdrawal.paid_at).label("day"),
                func.coalesce(func.sum(PartnerWithdrawal.debit_amount_minor), 0).label("paid"),
            )
            .where(
                func.upper(PartnerWithdrawal.debit_currency) == currency.upper(),
                PartnerWithdrawal.status == "paid",
                PartnerWithdrawal.paid_at >= since,
            )
            .group_by(func.date(PartnerWithdrawal.paid_at))
            .order_by(func.date(PartnerWithdrawal.paid_at))
        )
    ).all()
    points: dict[str, dict[str, Any]] = {}
    for row in rows:
        day = row.day.isoformat() if hasattr(row.day, "isoformat") else str(row.day)
        points[day] = {
            "date": day,
            "gross_minor": int(row.gross or 0),
            "commission_minor": int(row.commission or 0),
            "paid_minor": 0,
        }
    for row in payout_rows:
        day = row.day.isoformat() if hasattr(row.day, "isoformat") else str(row.day)
        points.setdefault(
            day,
            {"date": day, "gross_minor": 0, "commission_minor": 0, "paid_minor": 0},
        )["paid_minor"] = int(row.paid or 0)
    return [points[day] for day in sorted(points)]


__all__ = [
    "all_referral_import_candidates",
    "attention_counts",
    "list_profiles",
    "overview_metrics",
    "overview_series",
    "referral_import_candidates",
]
