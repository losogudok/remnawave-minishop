from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import Date, and_, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Payment, User


async def _daily_revenue_series_utc(session: AsyncSession, days: int = 14) -> list[dict[str, Any]]:
    """Succeeded external-payment totals per UTC calendar day."""

    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    range_start = today_start - timedelta(days=days - 1)
    day_col = cast(func.date_trunc("day", Payment.created_at), Date).label("d")
    result = await session.execute(
        select(day_col, func.coalesce(func.sum(Payment.amount), 0.0))
        .where(
            and_(
                Payment.status == "succeeded",
                Payment.funding_source == "external",
                Payment.created_at >= range_start,
            )
        )
        .group_by(day_col)
        .order_by(day_col)
    )
    by_day: dict[date, float] = {}
    for row in result.all():
        day = row[0]
        if isinstance(day, datetime):
            day = day.date()
        by_day[day] = float(row[1] or 0)
    return [
        {
            "date": (range_start + timedelta(days=index)).date().isoformat(),
            "amount": float(by_day.get((range_start + timedelta(days=index)).date(), 0.0) or 0.0),
        }
        for index in range(days)
    ]


async def get_financial_statistics(session: AsyncSession) -> dict[str, Any]:
    """Get cash-revenue statistics, excluding internal balance payments."""

    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=7)
    month_start = today_start - timedelta(days=30)
    revenue_row = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(case((Payment.created_at >= today_start, Payment.amount), else_=0)),
                    0,
                ),
                func.coalesce(
                    func.sum(case((Payment.created_at >= week_start, Payment.amount), else_=0)),
                    0,
                ),
                func.coalesce(
                    func.sum(case((Payment.created_at >= month_start, Payment.amount), else_=0)),
                    0,
                ),
                func.coalesce(func.sum(Payment.amount), 0),
                func.coalesce(func.sum(case((Payment.created_at >= today_start, 1), else_=0)), 0),
            ).where(Payment.status == "succeeded", Payment.funding_source == "external")
        )
    ).one()
    return {
        "today_revenue": float(revenue_row[0] or 0),
        "week_revenue": float(revenue_row[1] or 0),
        "month_revenue": float(revenue_row[2] or 0),
        "all_time_revenue": float(revenue_row[3] or 0),
        "today_payments_count": int(revenue_row[4] or 0),
        "daily_series": await _daily_revenue_series_utc(session, days=730),
    }


async def get_user_total_paid(session: AsyncSession, user_id: int) -> float:
    result = await session.execute(
        select(func.sum(Payment.amount)).where(
            Payment.user_id == user_id,
            Payment.status == "succeeded",
            Payment.funding_source == "external",
        )
    )
    return float(result.scalar() or 0)


async def get_referral_revenue(session: AsyncSession, referrer_id: int) -> float:
    result = await session.execute(
        select(func.sum(Payment.amount))
        .join(User, Payment.user_id == User.user_id)
        .where(
            User.referred_by_id == referrer_id,
            Payment.status == "succeeded",
            Payment.funding_source == "external",
        )
    )
    return float(result.scalar() or 0)


__all__ = ["get_financial_statistics", "get_referral_revenue", "get_user_total_paid"]
