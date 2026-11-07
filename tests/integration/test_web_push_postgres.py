"""Run delivery/ownership races across real PostgreSQL sessions."""

import os
import unittest
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.plugins.builtin.web_push.store import WebPushStore
from db.base import Base
from db.models import User
from tests.unit import test_web_push_plugin as push_tests
from tests.unit.test_web_push_plugin import make_settings

DATABASE_URL = os.environ.get("CORE_PERFORMANCE_TEST_DATABASE_URL", "")


@unittest.skipUnless(DATABASE_URL, "Disposable PostgreSQL URL is required")
class WebPushPostgresTests(push_tests.WebPushPluginTests):
    async def asyncSetUp(self) -> None:
        self.schema = "web_push_test_" + uuid.uuid4().hex
        self.admin_engine = create_async_engine(DATABASE_URL)
        async with self.admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.engine = create_async_engine(
            DATABASE_URL, connect_args={"server_settings": {"search_path": self.schema}}
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.settings = make_settings()
        self.store = WebPushStore(self.settings, self.factory)
        async with self.factory() as session:
            session.add_all([User(user_id=1), User(user_id=2)])
            await session.commit()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        async with self.admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        await self.admin_engine.dispose()
