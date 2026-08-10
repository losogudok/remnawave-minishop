from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from db.dal import promo_code_dal


def test_picker_query_filters_redeemable_shared_codes_and_escapes_search_wildcards():
    scalar_result = SimpleNamespace(all=list)
    execute = AsyncMock(return_value=SimpleNamespace(scalars=lambda: scalar_result))
    session = cast(
        AsyncSession,
        SimpleNamespace(execute=execute),
    )

    asyncio.run(
        promo_code_dal.get_usable_promo_codes_for_picker(
            session,
            search="SAVE_10%",
            personal=False,
            limit=500,
        )
    )

    statement = execute.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    values = set(compiled.params.values())

    assert "promo_codes.is_active IS true" in sql
    assert "promo_codes.user_id IS NULL" in sql
    assert "coalesce(promo_codes.current_activations" in sql
    assert "promo_codes.valid_until IS NULL" in sql
    assert "%save\\_10\\%%" in values
    assert "save\\_10\\%%" in values
    assert "save_10%" in values
    assert 100 in values
