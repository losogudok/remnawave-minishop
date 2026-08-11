from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from db.dal import partner_reporting_dal


@pytest.mark.parametrize(
    ("sort", "sql_fragment"),
    [
        ("user_asc", "display_label_snapshot"),
        ("status_desc", "partner_profiles.status"),
        ("rate_desc", "commission_bps"),
        ("clients_desc", "clients_count"),
        ("gross_desc", "gross_minor"),
        ("earned_desc", "earned_minor"),
        ("available_desc", "available_minor"),
        ("", "clients_count"),
    ],
)
def test_partner_list_sorting_compiles_for_postgres(sort: str, sql_fragment: str) -> None:
    count_result = MagicMock()
    count_result.scalar_one.return_value = 0
    rows_result = MagicMock()
    rows_result.scalars.return_value.all.return_value = []
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = [count_result, rows_result]

    profiles, total = asyncio.run(
        partner_reporting_dal.list_profiles(
            session,
            status=None,
            search=None,
            currency="RUB",
            sort=sort,
            limit=25,
            offset=0,
        )
    )

    assert profiles == []
    assert total == 0
    assert session.execute.await_count == 2
    list_call = session.execute.await_args_list[1]
    statement = list_call.args[0]
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert "ORDER BY" in compiled
    assert sql_fragment in compiled
