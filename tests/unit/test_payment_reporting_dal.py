from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from db.dal import payment_reporting_dal


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz: object = None) -> _FrozenDateTime:
        del tz
        return cls(2025, 1, 2, 12, tzinfo=UTC)


def test_all_time_daily_revenue_starts_at_first_matching_payment(monkeypatch) -> None:
    monkeypatch.setattr(payment_reporting_dal, "datetime", _FrozenDateTime)
    result = MagicMock()
    result.all.return_value = [
        (date(2024, 12, 31), 5),
        (date(2025, 1, 2), 7),
    ]
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = result

    series = asyncio.run(payment_reporting_dal._daily_revenue_series_utc(session, days=None))

    assert series == [
        {"date": "2024-12-31", "amount": 5.0},
        {"date": "2025-01-01", "amount": 0.0},
        {"date": "2025-01-02", "amount": 7.0},
    ]
    statement = session.execute.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "payments.created_at >=" not in sql


def test_all_time_daily_revenue_without_payments_returns_today(monkeypatch) -> None:
    monkeypatch.setattr(payment_reporting_dal, "datetime", _FrozenDateTime)
    result = MagicMock()
    result.all.return_value = []
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = result

    series = asyncio.run(payment_reporting_dal._daily_revenue_series_utc(session, days=None))

    assert series == [{"date": "2025-01-02", "amount": 0.0}]
