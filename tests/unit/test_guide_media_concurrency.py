"""Barrier-controlled SQLite fallback tests; no live PostgreSQL/multiprocess proof."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from bot.plugins.builtin.guide_media import locks, service, storage
from tests.unit.test_guide_media_plugin import Harness, _create, image_bytes
from tests.unit.test_guide_media_plugin import file_harness as file_harness
from tests.unit.test_guide_media_plugin import media_dir as media_dir


def test_concurrent_uploads_cannot_bypass_gallery_limit(
    file_harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        for index in range(7):
            await _create(
                file_harness, order=index, payload=image_bytes(color=(index * 30, 50, 60))
            )
        read, release, contender = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original_rows, original_lock = service._key_rows, locks.advisory_xact_lock
        active = False
        observed_sizes: list[int] = []

        @asynccontextmanager
        async def observed_lock(session: AsyncSession, key: str) -> AsyncIterator[None]:
            nonlocal active
            if ":gallery:" not in key:
                async with original_lock(session, key):
                    yield
                return
            if read.is_set():
                contender.set()
            async with original_lock(session, key):
                assert not active, "gallery critical sections overlap"
                active = True
                try:
                    yield
                finally:
                    active = False

        async def paused_rows(session: AsyncSession, key: service.GuideKey):
            rows = await original_rows(session, key)
            observed_sizes.append(len(rows))
            if not read.is_set():
                read.set()
                await release.wait()
            return rows

        monkeypatch.setattr(locks, "advisory_xact_lock", observed_lock)
        monkeypatch.setattr(service, "_key_rows", paused_rows)
        first = asyncio.create_task(_create(file_harness, payload=image_bytes(color=(100, 1, 1))))
        await asyncio.wait_for(read.wait(), 5)
        second = asyncio.create_task(_create(file_harness, payload=image_bytes(color=(200, 2, 2))))
        await asyncio.wait_for(contender.wait(), 5)
        release.set()
        results = await asyncio.gather(first, second, return_exceptions=True)
        errors = [item for item in results if isinstance(item, Exception)]
        assert len(errors) == 1
        assert isinstance(errors[0], service.GuideMediaError)
        assert errors[0].code == "too_many_screenshots"
        assert observed_sizes == [7, 8]
        async with file_harness.factory() as session:
            rows = await service.list_images(session)
        assert len(rows) == len({row.digest for row in rows}) == 8

    asyncio.run(scenario())


def test_delete_race_keeps_file_referenced_by_concurrent_upload(
    file_harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        payload = image_bytes(color=(44, 55, 66))
        row = await _create(file_harness, client_id="source", payload=payload)
        deleting, upload_waiting, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        real_delete, real_lock = storage.delete_image_file, locks.advisory_xact_lock

        async def paused_delete(digest: str) -> None:
            deleting.set()
            await release.wait()
            await real_delete(digest)

        @asynccontextmanager
        async def observed_lock(session: AsyncSession, key: str) -> AsyncIterator[None]:
            if deleting.is_set() and key == locks.digest_lock_key(row.digest):
                upload_waiting.set()
            async with real_lock(session, key):
                yield

        monkeypatch.setattr(storage, "delete_image_file", paused_delete)
        monkeypatch.setattr(locks, "advisory_xact_lock", observed_lock)
        async with file_harness.factory() as session:
            delete = asyncio.create_task(service.delete_image(session, image_id=row.image_id))
            await asyncio.wait_for(deleting.wait(), 5)
            upload = asyncio.create_task(_create(file_harness, client_id="target", payload=payload))
            await asyncio.wait_for(upload_waiting.wait(), 5)
            release.set()
            await delete
            created = await upload
        await storage.read_image(created.digest, size_bytes=created.size_bytes)
        async with file_harness.factory() as session:
            rows = await service.list_images(session)
        assert [(item.client_id, item.digest) for item in rows] == [("target", row.digest)]

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["update", "captions", "delete"])
def test_concurrent_deletion_returns_unknown_image(
    file_harness: Harness, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    async def scenario() -> None:
        row = await _create(file_harness)
        fetched, resume = asyncio.Event(), asyncio.Event()
        real_lock = locks.advisory_xact_lock
        async with file_harness.factory() as losing_session:

            @asynccontextmanager
            async def paused_lock(session: AsyncSession, key: str) -> AsyncIterator[None]:
                if session is losing_session and ":gallery:" in key:
                    fetched.set()
                    await resume.wait()
                async with real_lock(session, key):
                    yield

            monkeypatch.setattr(locks, "advisory_xact_lock", paused_lock)
            if operation == "delete":
                pending = service.delete_image(losing_session, image_id=row.image_id)
            else:
                pending = service.update_image(
                    losing_session,
                    image_id=row.image_id,
                    captions={"en": "Changed"},
                    order=1 if operation == "update" else None,
                    update_captions=True,
                )
            loser = asyncio.create_task(pending)
            await asyncio.wait_for(fetched.wait(), 5)
            async with file_harness.factory() as winning_session:
                await service.delete_image(winning_session, image_id=row.image_id)
            resume.set()
            with pytest.raises(service.GuideMediaError) as caught:
                await loser
            assert (caught.value.status, caught.value.code) == (404, "unknown_image")

    asyncio.run(scenario())


def test_postgres_advisory_lock_uses_transaction_scope() -> None:
    session = SimpleNamespace(
        bind=SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
        execute=AsyncMock(),
    )

    async def scenario() -> None:
        async with locks.advisory_xact_lock(cast(Any, session), "guide_media:test"):
            pass

    asyncio.run(scenario())
    session.execute.assert_awaited_once()
    statement = session.execute.await_args.args[0]
    assert "pg_advisory_xact_lock" in str(statement)
    assert "hashtext" in str(statement)


@pytest.mark.parametrize("operation", ["update", "refresh", "load"])
def test_local_settings_changes_wait_for_upload_commit(
    file_harness: Harness, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    from bot.services import guide_catalog_lock, settings_override_service

    async def scenario() -> None:
        committing, attempted, release, committed = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )
        original_lock = guide_catalog_lock.guide_catalog_lock

        @asynccontextmanager
        async def observed_catalog_lock() -> AsyncIterator[None]:
            attempted.set()
            async with original_lock():
                assert committed.is_set(), "settings entered before upload commit"
                yield

        # The plugin has its imported lock; settings decorators resolve this
        # observer at invocation and must wait on that very same underlying lock.
        monkeypatch.setattr(guide_catalog_lock, "guide_catalog_lock", observed_catalog_lock)
        monkeypatch.setattr(
            settings_override_service.app_settings_dal,
            "get_all_overrides",
            AsyncMock(return_value={}),
        )
        monkeypatch.setattr(
            settings_override_service.app_settings_dal, "upsert_override", AsyncMock()
        )
        async with file_harness.factory() as session:
            real_commit = session.commit

            async def paused_commit() -> None:
                committing.set()
                await release.wait()
                await real_commit()
                committed.set()

            monkeypatch.setattr(session, "commit", paused_commit)
            upload = asyncio.create_task(
                service.create_image(
                    session,
                    upload=service.UploadedMessageImage(data=image_bytes()),
                    config_id="local:file",
                    platform="ios",
                    client_id="client",
                    captions={},
                    order=None,
                )
            )
            await asyncio.wait_for(committing.wait(), 5)
            if operation == "update":
                change = settings_override_service.update_overrides(
                    file_harness.settings,
                    file_harness.factory,
                    updates={"SUBSCRIPTION_PAGE_CONFIG_PANEL_ENABLED": False},
                )
            elif operation == "refresh":
                change = settings_override_service.refresh_overrides_from_db(
                    file_harness.settings, file_harness.factory
                )
            else:
                change = settings_override_service.load_overrides_from_db(
                    file_harness.settings, file_harness.factory
                )
            settings_task = asyncio.create_task(change)
            await asyncio.wait_for(attempted.wait(), 5)
            assert not settings_task.done()
            release.set()
            await upload
            await settings_task

    asyncio.run(scenario())
