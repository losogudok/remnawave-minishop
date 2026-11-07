"""Regression coverage for bounded cleanup and catalog/order projections."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

from bot.plugins.builtin.guide_media import janitor, routes, service, storage
from bot.services.guide_media import normalize_gallery_images
from bot.services.message_image_service import UploadedMessageImage
from tests.unit.test_guide_media_plugin import (
    Harness,
    _app,
    _create,
    _loaded_catalog,
    image_bytes,
)
from tests.unit.test_guide_media_plugin import harness as harness
from tests.unit.test_guide_media_plugin import media_dir as media_dir


def test_default_order_at_contract_limit_remains_valid(harness: Harness) -> None:
    async def scenario() -> None:
        await _create(harness, order=10000)
        await _create(harness, payload=image_bytes(color=(1, 2, 3)))
        async with harness.factory() as session:
            rows = await service.list_images(session)
        assert [int(row.sort_order) for row in rows] == [0, 1]
        galleries = await service.resolve_galleries(harness.factory)
        assert (
            len(
                normalize_gallery_images(
                    [{"src": image.src, "order": image.order} for image in galleries[0].images],
                    public=True,
                )
            )
            == 2
        )

    asyncio.run(scenario())


def test_legacy_ties_have_identical_admin_and_runtime_order(harness: Harness) -> None:
    async def scenario() -> None:
        first = await _create(harness, order=0, payload=image_bytes(color=(1, 1, 1)))
        second = await _create(harness, order=1, payload=image_bytes(color=(2, 2, 2)))
        async with harness.factory() as session:
            rows = await service.list_images(session)
            # Force the DB tie-breaker to be the reverse of the runtime src tie-breaker.
            for row in rows:
                row.sort_order = 0
                row.created_at = first.created_at
                row.image_id = (
                    "0" * 32 if row.digest > min(first.digest, second.digest) else "f" * 32
                )
            await session.commit()
        async with harness.factory() as session:
            rows = await service.list_images(session)
        admin = routes._gallery_out(("local:file", "ios", "client"), rows, orphaned=False)
        gallery = (await service.resolve_galleries(harness.factory))[0]
        runtime = normalize_gallery_images(
            [{"src": image.src, "order": image.order} for image in gallery.images], public=True
        )
        assert [item.src for item in admin.images] == [item["src"] for item in runtime]
        assert (
            [item.order for item in admin.images] == [item["order"] for item in runtime] == [0, 1]
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("failed_id", ["panel:requested", "panel:unavailable"])
def test_failed_requested_source_is_unavailable(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, failed_id: str
) -> None:
    entries = _loaded_catalog().entries
    entries.append(
        service.GuideMediaCatalogConfigOut(
            config_id=failed_id, source="panel", missing=True, error="offline", platforms=[]
        )
    )
    monkeypatch.setattr(
        service,
        "load_admin_catalog",
        AsyncMock(
            return_value=service.AdminCatalog(entries=entries, available=True, error="offline")
        ),
    )

    async def scenario() -> None:
        with pytest.raises(service.GuideMediaError) as caught:
            await service.require_upload_target(
                harness.settings, None, language="en", key=("panel:requested", "ios", "client")
            )
        assert (caught.value.status, caught.value.code) == (503, "catalog_unavailable")
        await service.require_upload_target(
            harness.settings, None, language="en", key=("local:file", "ios", "client")
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("pause_at", ["prepare", "gallery", "snapshot"])
def test_catalog_rename_during_upload_rejects_insert(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, pause_at: str
) -> None:
    from collections.abc import AsyncIterator
    from contextlib import asynccontextmanager

    monkeypatch.setattr(routes, "_require_admin_user_id", lambda _request: 7)
    monkeypatch.setattr(routes, "_admin_language", AsyncMock(return_value="en"))
    catalog = AsyncMock(return_value=_loaded_catalog())
    monkeypatch.setattr(service, "load_admin_catalog", catalog)

    async def scenario() -> None:
        paused, resume = asyncio.Event(), asyncio.Event()
        prepare, lock = storage.prepare_image, service.locks.advisory_xact_lock

        async def paused_prepare(upload: UploadedMessageImage):
            prepared = await prepare(upload)
            if pause_at in ("prepare", "snapshot"):
                paused.set()
                await resume.wait()
            return prepared

        @asynccontextmanager
        async def paused_lock(session, key: str) -> AsyncIterator[None]:
            if pause_at == "gallery" and ":gallery:" in key:
                paused.set()
                await resume.wait()
            async with lock(session, key):
                yield

        monkeypatch.setattr(storage, "prepare_image", paused_prepare)
        monkeypatch.setattr(service.locks, "advisory_xact_lock", paused_lock)
        form = FormData()
        for name, value in [
            ("config_id", "local:file"),
            ("platform", "ios"),
            ("client_id", "client"),
        ]:
            form.add_field(name, value)
        form.add_field("image", image_bytes(), filename="shot.png", content_type="image/png")
        async with TestClient(TestServer(_app(harness))) as client:
            task = asyncio.create_task(client.post("/api/admin/guide-media/images", data=form))
            await asyncio.wait_for(paused.wait(), 5)
            catalog.return_value = _loaded_catalog(client_id="renamed")
            if pause_at == "snapshot":
                catalog.return_value = _loaded_catalog()
                catalog.return_value.entries[0].platforms[0].apps[0].name = "Changed name"
            resume.set()
            response = await task
            assert response.status in (400, 409)
            assert (await response.json())["error"] in ("unknown_guide_target", "catalog_changed")
        async with harness.factory() as session:
            assert await service.list_images(session) == []

    asyncio.run(scenario())


def test_janitor_referenced_oldest_does_not_starve_orphan(harness: Harness) -> None:
    async def scenario() -> None:
        referenced = await _create(harness)
        orphan = await storage.prepare_image(
            UploadedMessageImage(
                data=image_bytes(color=(80, 90, 100)),
                filename="orphan.png",
                content_type="image/png",
            )
        )
        await storage.store_image(orphan)
        os.utime(storage.media_path(referenced.digest), (1, 1))
        os.utime(storage.media_path(orphan.digest), (2, 2))
        cleaner = janitor.GuideMediaJanitor(harness.factory, min_age_seconds=1, batch_size=1)
        removed = sum([await cleaner.run_once() for _ in range(4)])
        assert removed == 1
        assert storage.media_path(referenced.digest).is_file()
        assert not storage.media_path(orphan.digest).exists()

    asyncio.run(scenario())


def test_janitor_removes_only_old_unreferenced_files(harness: Harness) -> None:
    async def scenario() -> None:
        referenced = await _create(harness, payload=image_bytes(color=(7, 7, 7)))
        referenced_path = (
            storage.GUIDE_MEDIA_DIR / referenced.digest[:2] / f"{referenced.digest}.webp"
        )
        orphan_prepared = await storage.prepare_image(
            UploadedMessageImage(
                data=image_bytes(color=(8, 8, 8)), filename="orphan.png", content_type="image/png"
            )
        )
        await storage.store_image(orphan_prepared)
        orphan_path = (
            storage.GUIDE_MEDIA_DIR / orphan_prepared.digest[:2] / f"{orphan_prepared.digest}.webp"
        )
        young_prepared = await storage.prepare_image(
            UploadedMessageImage(
                data=image_bytes(color=(9, 9, 9)), filename="young.png", content_type="image/png"
            )
        )
        await storage.store_image(young_prepared)
        young_path = (
            storage.GUIDE_MEDIA_DIR / young_prepared.digest[:2] / f"{young_prepared.digest}.webp"
        )
        old = time.time() - 7200
        os.utime(referenced_path, (old, old))
        os.utime(orphan_path, (old, old))
        janitor_instance = janitor.GuideMediaJanitor(
            harness.factory, min_age_seconds=3600, batch_size=10
        )
        removed = await janitor_instance.run_once()
        assert removed == 1
        assert referenced_path.is_file()
        assert not orphan_path.exists()
        assert young_path.is_file()

    asyncio.run(scenario())


def test_janitor_batch_size_is_bounded(harness: Harness) -> None:
    async def scenario() -> None:
        paths: list[Path] = []
        for color in ((10, 10, 10), (20, 20, 20)):
            prepared = await storage.prepare_image(
                UploadedMessageImage(
                    data=image_bytes(color=color), filename="orphan.png", content_type="image/png"
                )
            )
            await storage.store_image(prepared)
            path = storage.GUIDE_MEDIA_DIR / prepared.digest[:2] / f"{prepared.digest}.webp"
            old = time.time() - 7200
            os.utime(path, (old, old))
            paths.append(path)
        janitor_instance = janitor.GuideMediaJanitor(
            harness.factory, min_age_seconds=3600, batch_size=1
        )
        assert await janitor_instance.run_once() == 1
        assert sum(path.is_file() for path in paths) == 1
        assert await janitor_instance.run_once() == 1
        assert not any(path.is_file() for path in paths)

    asyncio.run(scenario())


def test_janitor_inspection_budget_counts_referenced_and_young_files(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        # All files share a shard; a directory iterator must resume inside it.
        shard = storage.GUIDE_MEDIA_DIR / "aa"
        shard.mkdir(parents=True)
        for number in range(20):
            (shard / f"aa{number:062x}.webp").write_bytes(b"young")
        real_stat = Path.stat
        inspected: set[Path] = set()

        def observed_stat(path: Path, *, follow_symlinks: bool = True):
            if path.suffix == ".webp" and path.parent == shard:
                inspected.add(path)
            return real_stat(path, follow_symlinks=follow_symlinks)

        monkeypatch.setattr(Path, "stat", observed_stat)
        cleaner = janitor.GuideMediaJanitor(harness.factory, min_age_seconds=3600, batch_size=1)
        try:
            for expected in range(1, 21):
                assert await cleaner.run_once() == 0
                assert len(inspected) == expected
        finally:
            cleaner._scanner.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["detail", "list", "service"])
def test_real_catalog_tracks_failed_panel_source(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from config import subscription_guides_config

    monkeypatch.setattr(
        subscription_guides_config,
        "load_subscription_guides_config",
        lambda _settings: ({"platforms": {}}, "file"),
    )
    harness.settings.SUBSCRIPTION_PAGE_CONFIG_PANEL_ENABLED = True
    panel = SimpleNamespace(
        get_subscription_page_config_list=AsyncMock(return_value=[{"uuid": "requested"}]),
        get_subscription_page_config_by_uuid=AsyncMock(side_effect=RuntimeError("offline")),
    )
    if failure == "list":
        panel.get_subscription_page_config_list.side_effect = RuntimeError("offline")

    async def scenario() -> None:
        loaded = await service.load_admin_catalog(
            harness.settings, None if failure == "service" else panel, language="en"
        )
        assert loaded.available is True
        assert loaded.orphaned_known is False
        with pytest.raises(service.GuideMediaError) as caught:
            await service.require_upload_target(
                harness.settings,
                None if failure == "service" else panel,
                language="en",
                key=("panel:requested", "ios", "client"),
            )
        assert (caught.value.status, caught.value.code) == (503, "catalog_unavailable")

    asyncio.run(scenario())
