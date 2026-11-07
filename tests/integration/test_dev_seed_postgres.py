"""Run the actual dev seed against disposable schemas and verified Panel identities."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from db import auth_models as auth_models
from db.models import Base

DATABASE_URL = os.environ.get("CORE_PERFORMANCE_TEST_DATABASE_URL", "")
DEV_DIR = Path(__file__).resolve().parents[2] / "deploy" / "dev"
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="Disposable PostgreSQL URL is required")
SEED_USERS = ("runes_admin", "runes_active", "runes_expired")


@asynccontextmanager
async def seed_database() -> AsyncIterator[asyncpg.Connection]:
    schema = "dev_seed_" + uuid.uuid4().hex
    engine = create_async_engine(DATABASE_URL)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            await connection.run_sync(Base.metadata.create_all)
        database = await asyncpg.connect(
            DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://"),
            server_settings={"search_path": schema},
        )
        try:
            yield database
        finally:
            await database.close()
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()


async def apply_seed(database: asyncpg.Connection, identities: list[dict[str, str]]) -> None:
    # psql quotes the JSON supplied by the wrapper; exercise the same SQL on
    # PostgreSQL without requiring a psql executable on the pytest host.
    payload = "'" + json.dumps(identities).replace("'", "''") + "'"
    sql = (DEV_DIR / "seed-minishop.sql").read_text().replace(":'panel_seed_users'", payload)
    async with database.transaction():
        await database.execute(sql)


@pytest.mark.parametrize("numeric", [True, False])
def test_seed_links_real_panel_ids_and_keeps_working_shares_on_reapply(numeric: bool) -> None:
    async def run() -> None:
        identities = [
            {
                "username": username,
                "panel_user_uuid": str(700 + index)
                if numeric
                else str(uuid.uuid5(uuid.NAMESPACE_DNS, username)),
                "panel_short_uuid": f"seed-{index}",
            }
            for index, username in enumerate(SEED_USERS, 1)
        ]
        async with seed_database() as database:
            await apply_seed(database, identities)
            expected = {row["username"]: row for row in identities}
            query = """
                SELECT u.username, u.panel_user_uuid, s.panel_user_uuid AS subscription_ref,
                       s.install_share_panel_short_uuid, s.install_share_token,
                       s.is_active, s.subscription_id
                FROM users u JOIN subscriptions s USING (user_id) ORDER BY u.username
            """
            first = await database.fetch(query)
            assert len(first) == 3
            for row in first:
                identity = expected[row["username"]]
                assert row["panel_user_uuid"] == identity["panel_user_uuid"]
                assert row["subscription_ref"] == identity["panel_user_uuid"]
                assert row["install_share_panel_short_uuid"] == identity["panel_short_uuid"]
                token = row["install_share_token"]
                assert len(token) == 32 and all(c in "0123456789abcdef" for c in token)
                assert row["is_active"] == (row["username"] != "runes_expired")

            # A valid share issued by the application must survive reseeding.
            token = uuid.uuid4().hex
            await database.execute(
                "UPDATE subscriptions SET install_share_token=$1 WHERE user_id=910000001",
                token,
            )
            await apply_seed(database, identities)
            second = await database.fetch(query)
            admin = next(row for row in second if row["username"] == "runes_admin")
            assert admin["install_share_token"] == token
            assert [row["subscription_id"] for row in second] == [
                row["subscription_id"] for row in first
            ]
            assert await database.fetchval("SELECT count(*) FROM payments") == 3

            # A changed upstream subscription must retire the former share.
            identities[0]["panel_short_uuid"] = "reissued-admin"
            await apply_seed(database, identities)
            third = await database.fetch(query)
            admin = next(row for row in third if row["username"] == "runes_admin")
            assert admin["install_share_token"] != token
            assert admin["install_share_panel_short_uuid"] == "reissued-admin"

    asyncio.run(run())


def test_seed_rejects_incomplete_panel_mapping_before_writing_accounts() -> None:
    async def run() -> None:
        async with seed_database() as database:
            with pytest.raises(asyncpg.PostgresError, match="seeded Panel users"):
                await apply_seed(database, [])
            assert await database.fetchval("SELECT count(*) FROM users") == 0
            assert await database.fetchval("SELECT count(*) FROM subscriptions") == 0

    asyncio.run(run())


@pytest.mark.parametrize("numeric", [True, False])
def test_export_reads_the_actual_public_identity_for_each_panel_generation(numeric: bool) -> None:
    async def run() -> None:
        async with seed_database() as database:
            columns = "id bigint" if numeric else "uuid uuid, t_id bigint"
            await database.execute(
                f"CREATE TEMP TABLE users ({columns}, username text, short_uuid text)"
            )
            reference = "731" if numeric else str(uuid.uuid4())
            await database.execute(
                "INSERT INTO users ("
                + ("id" if numeric else "uuid")
                + ", username, short_uuid) VALUES ($1, 'runes_admin', 'devadmin01')",
                731 if numeric else uuid.UUID(reference),
            )
            exported = json.loads(
                await database.fetchval((DEV_DIR / "seed-remnawave-identities.sql").read_text())
            )
            assert exported == [
                {
                    "username": "runes_admin",
                    "panel_user_uuid": reference,
                    "panel_short_uuid": "devadmin01",
                }
            ]

    asyncio.run(run())
