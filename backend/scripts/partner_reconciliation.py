"""Print a read-only partner financial integrity report as JSON."""

from __future__ import annotations

import asyncio
import json

from bot.services.partner_reconciliation_report import build_partner_reconciliation_report
from bot.services.settings_override_service import load_overrides_from_db
from config.settings import Settings
from db.database_setup import init_db_connection


async def main() -> None:
    settings = Settings()
    session_factory = init_db_connection(settings)
    await load_overrides_from_db(settings, session_factory)
    async with session_factory() as session:
        report = await build_partner_reconciliation_report(session)
        await session.rollback()
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
