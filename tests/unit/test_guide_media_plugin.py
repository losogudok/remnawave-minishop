"""Focused tests for the built-in guide media plugin."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.app.web.context import SESSION_FACTORY, SETTINGS
from bot.plugins.builtin.guide_media import (
    GuideMediaPlugin,
    routes,
    service,
    storage,
)
from bot.plugins.builtin.guide_media.schemas import (
    GuideMediaCatalogAppOut,
    GuideMediaCatalogConfigOut,
    GuideMediaCatalogPlatformOut,
)
from bot.plugins.extensions import GuideMediaContext
from bot.services.message_image_service import UploadedMessageImage
from config.settings import Settings
from db.guide_media_models import GuideMediaImage as GuideMediaImageRow
from tests.support.settings_stub import settings_stub


def image_bytes(fmt: str = "PNG", color: tuple[int, int, int] = (32, 96, 160)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (32, 24), color).save(output, format=fmt)
    return output.getvalue()


def animated_webp_bytes() -> bytes:
    output = io.BytesIO()
    frames = [Image.new("RGB", (16, 16), (255, 0, 0)), Image.new("RGB", (16, 16), (0, 255, 0))]
    frames[0].save(
        output,
        format="WEBP",
        save_all=True,
        append_images=frames[1:],
        duration=20,
        loop=0,
    )
    return output.getvalue()


class Harness:
    def __init__(self, engine_url: str = "sqlite+aiosqlite://") -> None:
        self.engine = create_async_engine(engine_url)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.settings = cast(Settings, settings_stub())

    async def start(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: GuideMediaImageRow.__table__.create(
                    sync_connection, checkfirst=True
                )
            )

    async def close(self) -> None:
        await self.engine.dispose()


@pytest.fixture
def media_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "guide-media"
    monkeypatch.setattr(storage, "GUIDE_MEDIA_DIR", target)
    return target


@pytest.fixture
def harness(media_dir: Path) -> Iterator[Harness]:
    instance = Harness()
    asyncio.run(instance.start())
    yield instance
    asyncio.run(instance.close())


@pytest.fixture
def file_harness(tmp_path: Path, media_dir: Path) -> Iterator[Harness]:
    instance = Harness(f"sqlite+aiosqlite:///{tmp_path / 'guide-media.sqlite'}")
    asyncio.run(instance.start())
    yield instance
    asyncio.run(instance.close())


async def _create(
    harness: Harness,
    *,
    config_id: str = "local:file",
    platform: str = "ios",
    client_id: str = "client",
    captions: dict[str, str] | None = None,
    order: int | None = None,
    payload: bytes | None = None,
) -> GuideMediaImageRow:
    async with harness.factory() as session:
        row = await service.create_image(
            session,
            upload=UploadedMessageImage(
                data=payload if payload is not None else image_bytes(),
                filename="shot.png",
                content_type="image/png",
            ),
            config_id=config_id,
            platform=platform,
            client_id=client_id,
            captions=captions if captions is not None else {"ru": "Шаг", "en": "Step"},
            order=order,
        )
        await session.commit()
        return row


def _catalog(*, ambiguous: bool = False, client_id: str = "client") -> list[Any]:
    return [
        GuideMediaCatalogConfigOut(
            config_id="local:file",
            source="file",
            platforms=[
                GuideMediaCatalogPlatformOut(
                    id="ios",
                    label="iOS",
                    apps=[
                        GuideMediaCatalogAppOut(
                            id=client_id, name="Client", explicit_id=True, ambiguous=ambiguous
                        )
                    ],
                )
            ],
        )
    ]


def _loaded_catalog(
    *,
    ambiguous: bool = False,
    client_id: str = "client",
    available: bool = True,
    orphaned_known: bool = True,
    error: str | None = None,
    entries: list[Any] | None = None,
) -> service.AdminCatalog:
    return service.AdminCatalog(
        entries=entries
        if entries is not None
        else _catalog(ambiguous=ambiguous, client_id=client_id),
        available=available,
        error=error,
        orphaned_known=orphaned_known,
    )


def _app(harness: Harness) -> web.Application:
    app = web.Application()
    routes.setup_guide_media_routes(app)
    app[SETTINGS] = harness.settings
    app[SESSION_FACTORY] = harness.factory
    return app


def test_valid_png_upload_persists_metadata_and_captions(harness: Harness) -> None:
    row = asyncio.run(_create(harness, captions={"ru": "Первый шаг", "en": "First step"}))
    assert row.image_id
    media_file = storage.GUIDE_MEDIA_DIR / row.digest[:2] / f"{row.digest}.webp"
    assert media_file.is_file()
    assert hashlib.sha256(media_file.read_bytes()).hexdigest() == row.digest
    assert row.captions == {"ru": "Первый шаг", "en": "First step"}
    assert row.content_type == "image/webp"
    assert row.width == 32
    assert row.height == 24
    assert row.size_bytes == media_file.stat().st_size
    assert int(row.sort_order) == 0


def test_valid_webp_upload_is_accepted(harness: Harness) -> None:
    row = asyncio.run(_create(harness, payload=image_bytes("WEBP")))
    assert row.digest
    assert (storage.GUIDE_MEDIA_DIR / row.digest[:2] / f"{row.digest}.webp").is_file()


def test_non_image_and_markup_payloads_are_rejected(harness: Harness) -> None:
    for payload, expected in (
        (b"<svg xmlns='http://www.w3.org/2000/svg'><rect/></svg>", "invalid_image"),
        (b"<html><body>not an image</body></html>", "invalid_image"),
        (b"plain text bytes", "invalid_image"),
    ):
        with pytest.raises(service.GuideMediaError) as caught:
            asyncio.run(_create(harness, payload=payload))
        assert caught.value.code == expected


def test_animated_image_is_rejected(harness: Harness) -> None:
    with pytest.raises(service.GuideMediaError) as caught:
        asyncio.run(_create(harness, payload=animated_webp_bytes()))
    assert caught.value.code == "animated_image"


def test_too_large_image_is_rejected_before_decoding(harness: Harness) -> None:
    with pytest.raises(service.GuideMediaError) as caught:
        asyncio.run(_create(harness, payload=b"x" * (8 * 1024 * 1024 + 1)))
    assert caught.value.code == "image_too_large"
    assert caught.value.status == 413


def test_gallery_rejects_more_than_eight_images(harness: Harness) -> None:
    async def scenario() -> None:
        for index in range(8):
            await _create(harness, order=index)
        with pytest.raises(service.GuideMediaError) as caught:
            await _create(harness)
        assert caught.value.code == "too_many_screenshots"

    asyncio.run(scenario())


def test_invalid_keys_and_captions_are_rejected(harness: Harness) -> None:
    with pytest.raises(service.GuideMediaError) as caught:
        asyncio.run(_create(harness, client_id="not a valid id!"))
    assert caught.value.code == "invalid_guide_key"

    with pytest.raises(service.GuideMediaError) as caught:
        asyncio.run(_create(harness, captions={"ru": "x" * 301}))
    assert caught.value.code == "invalid_captions"


def test_reorder_requires_the_exact_gallery_set(harness: Harness) -> None:
    async def scenario() -> None:
        first = await _create(harness, order=0)
        second = await _create(harness, order=1)
        third = await _create(harness, order=2)
        async with harness.factory() as session:
            key, rows = await service.reorder_images(
                session,
                config_id="local:file",
                platform="ios",
                client_id="client",
                image_ids=[third.image_id, first.image_id, second.image_id],
            )
            await session.commit()
            assert key == ("local:file", "ios", "client")
            assert [row.image_id for row in rows] == [
                third.image_id,
                first.image_id,
                second.image_id,
            ]
            assert [int(row.sort_order) for row in rows] == [0, 1, 2]

        async with harness.factory() as session:
            with pytest.raises(service.GuideMediaError) as caught:
                await service.reorder_images(
                    session,
                    config_id="local:file",
                    platform="ios",
                    client_id="client",
                    image_ids=[first.image_id, second.image_id],
                )
            assert caught.value.code == "gallery_mismatch"
            with pytest.raises(service.GuideMediaError) as caught:
                await service.reorder_images(
                    session,
                    config_id="local:file",
                    platform="ios",
                    client_id="client",
                    image_ids=[first.image_id, second.image_id, third.image_id, third.image_id],
                )
            assert caught.value.code == "gallery_mismatch"

    asyncio.run(scenario())


def test_update_captions_and_order_returns_unknown_image(harness: Harness) -> None:
    async def scenario() -> None:
        row = await _create(harness)
        async with harness.factory() as session:
            updated = await service.update_image(
                session,
                image_id=row.image_id,
                captions={"en": "Renamed"},
                order=4,
                update_captions=True,
            )
            await session.commit()
            assert updated.captions == {"en": "Renamed"}
            assert int(updated.sort_order) == 4
        async with harness.factory() as session:
            with pytest.raises(service.GuideMediaError) as caught:
                await service.update_image(
                    session,
                    image_id="0" * 32,
                    captions=None,
                    order=None,
                    update_captions=False,
                )
            assert caught.value.code == "unknown_image"

    asyncio.run(scenario())


def test_delete_removes_file_only_when_digest_is_unreferenced(harness: Harness) -> None:
    async def scenario() -> None:
        shared = image_bytes()
        first = await _create(harness, client_id="first", payload=shared)
        second = await _create(harness, client_id="second", payload=shared)
        assert first.digest == second.digest
        path = storage.GUIDE_MEDIA_DIR / first.digest[:2] / f"{first.digest}.webp"
        async with harness.factory() as session:
            _, delete_file = await service.delete_image(session, image_id=first.image_id)
            await session.commit()
        assert delete_file is False
        assert path.is_file()
        async with harness.factory() as session:
            _, delete_file = await service.delete_image(session, image_id=second.image_id)
            await session.commit()
        assert delete_file is True
        await storage.delete_image_file(second.digest)
        assert not path.exists()

    asyncio.run(scenario())


def test_admin_galleries_route_requires_admin(harness: Harness) -> None:
    async def scenario() -> None:
        async with TestClient(TestServer(_app(harness))) as client:
            response = await client.get("/api/admin/guide-media/galleries")
        assert response.status == 401

    asyncio.run(scenario())


def test_upload_route_accepts_multipart_and_returns_image(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        service,
        "load_admin_catalog",
        AsyncMock(
            return_value=service.AdminCatalog(
                entries=_catalog(), available=True, orphaned_known=True
            )
        ),
    )
    monkeypatch.setattr(routes, "_require_admin_user_id", lambda _request: 7)
    monkeypatch.setattr(routes, "_admin_language", AsyncMock(return_value="en"))

    async def scenario() -> None:
        form = FormData()
        form.add_field("config_id", "local:file")
        form.add_field("platform", "ios")
        form.add_field("client_id", "client")
        form.add_field("captions", json.dumps({"ru": "Шаг", "en": "Step"}))
        form.add_field("image", image_bytes(), filename="shot.png", content_type="image/png")
        async with TestClient(TestServer(_app(harness))) as client:
            response = await client.post("/api/admin/guide-media/images", data=form)
            payload = await response.json()
        assert response.status == 200
        assert payload["ok"] is True
        assert payload["image"]["captions"] == {"ru": "Шаг", "en": "Step"}
        assert payload["image"]["src"].startswith("/api/plugins/guide_media/media/")
        assert payload["image"]["src"].endswith(".webp")

    asyncio.run(scenario())


def test_upload_route_rejects_missing_and_ambiguous_images(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes, "_require_admin_user_id", lambda _request: 7)
    monkeypatch.setattr(routes, "_admin_language", AsyncMock(return_value="en"))
    monkeypatch.setattr(
        service,
        "load_admin_catalog",
        AsyncMock(
            return_value=service.AdminCatalog(
                entries=_catalog(ambiguous=True), available=True, orphaned_known=True
            )
        ),
    )

    async def scenario() -> None:
        async with TestClient(TestServer(_app(harness))) as client:
            missing = await client.post(
                "/api/admin/guide-media/images",
                json={
                    "config_id": "local:file",
                    "platform": "ios",
                    "client_id": "client",
                    "captions": {},
                },
            )
            missing_payload = await missing.json()
            form = FormData()
            form.add_field("config_id", "local:file")
            form.add_field("platform", "ios")
            form.add_field("client_id", "client")
            form.add_field("captions", "{}")
            form.add_field("image", image_bytes(), filename="shot.png", content_type="image/png")
            ambiguous_response = await client.post("/api/admin/guide-media/images", data=form)
            ambiguous_payload = await ambiguous_response.json()
        assert missing.status == 400
        assert missing_payload["error"] == "image_missing"
        assert ambiguous_response.status == 400
        assert ambiguous_payload["error"] == "ambiguous_client"

    asyncio.run(scenario())


def test_upload_route_rejects_invalid_guide_key(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes, "_require_admin_user_id", lambda _request: 7)
    monkeypatch.setattr(routes, "_admin_language", AsyncMock(return_value="en"))
    monkeypatch.setattr(
        service,
        "load_admin_catalog",
        AsyncMock(
            return_value=service.AdminCatalog(
                entries=_catalog(), available=True, orphaned_known=True
            )
        ),
    )

    async def scenario() -> None:
        form = FormData()
        form.add_field("config_id", "local:file")
        form.add_field("platform", "ios")
        form.add_field("client_id", "not valid!")
        form.add_field("captions", "{}")
        form.add_field("image", image_bytes(), filename="shot.png", content_type="image/png")
        async with TestClient(TestServer(_app(harness))) as client:
            response = await client.post("/api/admin/guide-media/images", data=form)
            payload = await response.json()
        assert response.status == 400
        assert payload["error"] == "invalid_guide_key"

    asyncio.run(scenario())


def test_admin_galleries_route_flags_orphans_and_deletes_them(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = asyncio.run(_create(harness, client_id="removed"))
    monkeypatch.setattr(routes, "_require_admin_user_id", lambda _request: 7)
    monkeypatch.setattr(routes, "_admin_language", AsyncMock(return_value="en"))
    monkeypatch.setattr(
        service,
        "load_admin_catalog",
        AsyncMock(
            return_value=service.AdminCatalog(
                entries=_catalog(client_id="renamed"), available=True, orphaned_known=True
            )
        ),
    )

    async def scenario() -> None:
        async with TestClient(TestServer(_app(harness))) as client:
            response = await client.get("/api/admin/guide-media/galleries")
            payload = await response.json()
            assert response.status == 200
            assert payload["catalog"][0]["config_id"] == "local:file"
            assert payload["orphaned"] == [
                {"config_id": "local:file", "platform": "ios", "client_id": "removed"}
            ]
            assert payload["galleries"][0]["orphaned"] is True
            assert payload["galleries"][0]["images"][0]["image_id"] == row.image_id
            deleted = await client.delete(f"/api/admin/guide-media/images/{row.image_id}")
            assert deleted.status == 200
            assert (await deleted.json()) == {"ok": True}
        async with harness.factory() as session:
            assert await session.scalar(select(func.count()).select_from(GuideMediaImageRow)) == 0
        assert not any(storage.GUIDE_MEDIA_DIR.rglob("*.webp"))

    asyncio.run(scenario())


def test_public_media_route_serves_headers_etag_and_304(
    harness: Harness,
) -> None:
    row = asyncio.run(_create(harness))

    async def scenario() -> None:
        async with TestClient(TestServer(_app(harness))) as client:
            response = await client.get(f"/api/plugins/guide_media/media/{row.digest}.webp")
            assert response.status == 200
            assert response.headers["Content-Type"] == "image/webp"
            assert response.headers["X-Content-Type-Options"] == "nosniff"
            assert response.headers["Content-Disposition"] == (
                f'inline; filename="guide-{row.digest[:16]}.webp"'
            )
            assert response.headers["Cache-Control"] == "public, max-age=31536000, immutable"
            assert response.headers["ETag"] == f'"{row.digest}"'
            assert int(response.headers["Content-Length"]) == len(await response.read())
            cached = await client.get(
                f"/api/plugins/guide_media/media/{row.digest}.webp",
                headers={"If-None-Match": f'"{row.digest}"'},
            )
            assert cached.status == 304
            unknown = await client.get(f"/api/plugins/guide_media/media/{'0' * 64}.webp")
            assert unknown.status == 404

    asyncio.run(scenario())


def test_public_media_route_does_not_serve_missing_or_corrupt_files(
    harness: Harness,
) -> None:
    row = asyncio.run(_create(harness))
    path = storage.GUIDE_MEDIA_DIR / row.digest[:2] / f"{row.digest}.webp"
    path.write_bytes(b"corrupt")

    async def scenario() -> None:
        async with TestClient(TestServer(_app(harness))) as client:
            corrupt = await client.get(f"/api/plugins/guide_media/media/{row.digest}.webp")
            assert corrupt.status == 404
        path.unlink()
        async with TestClient(TestServer(_app(harness))) as client:
            missing = await client.get(f"/api/plugins/guide_media/media/{row.digest}.webp")
            assert missing.status == 404

    asyncio.run(scenario())


def test_provider_returns_public_sources_and_sorted_captions(harness: Harness) -> None:
    async def scenario() -> None:
        await _create(harness, order=2, captions={"en": "Second"})
        await _create(harness, order=0, captions={"en": "First"})
        provider = service.guide_media_provider(harness.factory)
        galleries = await provider.resolve(
            GuideMediaContext(user_id=None, language="en", is_public=True)
        )
        assert len(galleries) == 1
        gallery = galleries[0]
        assert (gallery.config_id, gallery.platform, gallery.client_id) == (
            "local:file",
            "ios",
            "client",
        )
        assert [image.captions["en"] for image in gallery.images] == ["First", "Second"]
        assert all(
            image.src == image.public_src
            and image.src.startswith("/api/plugins/guide_media/media/")
            for image in gallery.images
        )

    asyncio.run(scenario())


def test_provider_without_session_factory_returns_empty() -> None:
    provider = service.guide_media_provider(None)

    async def scenario() -> None:
        assert (
            await provider.resolve(GuideMediaContext(user_id=None, language="en", is_public=True))
            == ()
        )

    asyncio.run(scenario())


def test_provider_skips_invalid_keys_and_caps_gallery_size(harness: Harness) -> None:
    async def scenario() -> None:
        async with harness.factory() as session:
            for index in range(9):
                session.add(
                    GuideMediaImageRow(
                        image_id=f"{index:032x}",
                        digest=f"{index:064x}",
                        config_id="local:file",
                        platform="ios",
                        client_id="client",
                        captions={"en": f"Shot {index}"},
                        sort_order=index,
                        content_type="image/webp",
                        width=10,
                        height=10,
                        size_bytes=1,
                    )
                )
            session.add(
                GuideMediaImageRow(
                    image_id="f" * 32,
                    digest="f" * 64,
                    config_id="local:file",
                    platform="ios",
                    client_id="not valid!",
                    captions={},
                    sort_order=0,
                    content_type="image/webp",
                    width=10,
                    height=10,
                    size_bytes=1,
                )
            )
            await session.commit()
        provider = service.guide_media_provider(harness.factory)
        galleries = await provider.resolve(
            GuideMediaContext(user_id=None, language="en", is_public=True)
        )
        assert len(galleries) == 1
        assert len(galleries[0].images) == 8
        assert [image.order for image in galleries[0].images] == list(range(8))

    asyncio.run(scenario())


def test_migration_id_is_namespaced_and_ddl_is_idempotent() -> None:
    plugin = GuideMediaPlugin()
    migrations = plugin.migrations()
    assert migrations
    migration = migrations[0]
    assert migration.id == "guide_media.0001_initial"

    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(lambda sync: migration.upgrade(sync))
                await connection.run_sync(lambda sync: migration.upgrade(sync))
                columns = await connection.execute(
                    text("PRAGMA table_info(ext_guide_media_images)")
                )
                names = {str(row[1]) for row in columns}
                assert {
                    "image_id",
                    "digest",
                    "config_id",
                    "platform",
                    "client_id",
                    "captions",
                    "sort_order",
                    "content_type",
                    "width",
                    "height",
                    "size_bytes",
                    "created_at",
                    "updated_at",
                } <= names
                indexes = await connection.execute(
                    text("PRAGMA index_list(ext_guide_media_images)")
                )
                assert "ix_ext_guide_media_gallery" in {str(row[1]) for row in indexes}
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_message_image_errors_map_to_plugin_codes(harness: Harness) -> None:
    async def scenario() -> None:
        async with harness.factory() as session:
            with pytest.raises(service.GuideMediaError) as caught:
                await service.create_image(
                    session,
                    upload=UploadedMessageImage(data=b""),
                    config_id="local:file",
                    platform="ios",
                    client_id="client",
                    captions={},
                    order=None,
                )
        assert caught.value.code == "empty_image"

    asyncio.run(scenario())


def test_commit_failure_cleans_up_unreferenced_file(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        payload = image_bytes(color=(11, 22, 33))
        prepared = await storage.prepare_image(
            UploadedMessageImage(data=payload, filename="shot.png", content_type="image/png")
        )
        path = storage.GUIDE_MEDIA_DIR / prepared.digest[:2] / f"{prepared.digest}.webp"
        async with harness.factory() as session:
            real_commit = session.commit
            calls = 0

            async def flaky_commit() -> None:
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise RuntimeError("commit failed")
                await real_commit()

            monkeypatch.setattr(session, "commit", flaky_commit)
            with pytest.raises(RuntimeError):
                await service.create_image(
                    session,
                    upload=UploadedMessageImage(
                        data=payload, filename="shot.png", content_type="image/png"
                    ),
                    config_id="local:file",
                    platform="ios",
                    client_id="client",
                    captions={},
                    order=None,
                )
        assert not path.exists()
        async with harness.factory() as session:
            assert await session.scalar(select(func.count()).select_from(GuideMediaImageRow)) == 0

    asyncio.run(scenario())


def test_flush_failure_cleans_up_unreferenced_file(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        payload = image_bytes(color=(2, 2, 2))
        prepared = await storage.prepare_image(
            UploadedMessageImage(data=payload, filename="shot.png", content_type="image/png")
        )
        path = storage.GUIDE_MEDIA_DIR / prepared.digest[:2] / f"{prepared.digest}.webp"
        async with harness.factory() as session:

            async def failing_flush() -> None:
                raise RuntimeError("flush failed")

            monkeypatch.setattr(session, "flush", failing_flush)
            with pytest.raises(RuntimeError):
                await service.create_image(
                    session,
                    upload=UploadedMessageImage(
                        data=payload, filename="shot.png", content_type="image/png"
                    ),
                    config_id="local:file",
                    platform="ios",
                    client_id="client",
                    captions={},
                    order=None,
                )
        assert not path.exists()

    asyncio.run(scenario())


def test_default_order_uses_max_and_explicit_order_conflicts(harness: Harness) -> None:
    async def scenario() -> None:
        first = await _create(harness, order=0)
        second = await _create(harness, order=1)
        third = await _create(harness, order=2)
        async with harness.factory() as session:
            await service.delete_image(session, image_id=second.image_id)
        fourth = await _create(harness)
        assert int(fourth.sort_order) == 3
        provider = service.guide_media_provider(harness.factory)
        galleries = await provider.resolve(
            GuideMediaContext(user_id=None, language="en", is_public=True)
        )
        assert [image.order for image in galleries[0].images] == [0, 2, 3]
        assert [image.order for image in galleries[0].images] == sorted(
            image.order for image in galleries[0].images
        )
        with pytest.raises(service.GuideMediaError) as caught:
            await _create(harness, order=2)
        assert caught.value.code == "order_conflict"
        async with harness.factory() as session:
            with pytest.raises(service.GuideMediaError) as caught:
                await service.update_image(
                    session,
                    image_id=fourth.image_id,
                    captions=None,
                    order=0,
                    update_captions=False,
                )
        assert caught.value.code == "order_conflict"
        assert first.image_id != third.image_id

    asyncio.run(scenario())


def test_upload_requires_available_catalog_and_exact_target(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes, "_require_admin_user_id", lambda _request: 7)
    monkeypatch.setattr(routes, "_admin_language", AsyncMock(return_value="en"))

    def form() -> FormData:
        value = FormData()
        value.add_field("config_id", "local:file")
        value.add_field("platform", "ios")
        value.add_field("client_id", "client")
        value.add_field("captions", "{}")
        value.add_field("image", image_bytes(), filename="shot.png", content_type="image/png")
        return value

    async def scenario() -> None:
        monkeypatch.setattr(
            service,
            "load_admin_catalog",
            AsyncMock(
                return_value=_loaded_catalog(
                    available=False, orphaned_known=False, error="panel down"
                )
            ),
        )
        async with TestClient(TestServer(_app(harness))) as client:
            unavailable = await client.post("/api/admin/guide-media/images", data=form())
            unavailable_payload = await unavailable.json()
        assert unavailable.status == 503
        assert unavailable_payload["error"] == "catalog_unavailable"

        monkeypatch.setattr(
            service,
            "load_admin_catalog",
            AsyncMock(return_value=_loaded_catalog(client_id="other")),
        )
        async with TestClient(TestServer(_app(harness))) as client:
            unknown = await client.post("/api/admin/guide-media/images", data=form())
            unknown_payload = await unknown.json()
        assert unknown.status == 400
        assert unknown_payload["error"] == "unknown_guide_target"

    asyncio.run(scenario())


def test_admin_galleries_reports_catalog_failures_without_hiding_stored_galleries(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = asyncio.run(_create(harness))
    monkeypatch.setattr(routes, "_require_admin_user_id", lambda _request: 7)
    monkeypatch.setattr(routes, "_admin_language", AsyncMock(return_value="en"))

    async def get_payload() -> dict[str, Any]:
        async with TestClient(TestServer(_app(harness))) as client:
            response = await client.get("/api/admin/guide-media/galleries")
            payload = await response.json()
        assert response.status == 200
        return cast(dict[str, Any], payload)

    monkeypatch.setattr(
        service,
        "load_admin_catalog",
        AsyncMock(
            return_value=_loaded_catalog(
                entries=[], available=False, orphaned_known=False, error="panel down"
            )
        ),
    )
    unavailable = asyncio.run(get_payload())
    assert unavailable["catalog_available"] is False
    assert unavailable["catalog_error"] == "panel down"
    assert unavailable["orphaned_known"] is False
    assert unavailable["orphaned"] == []
    assert unavailable["galleries"][0]["images"][0]["image_id"] == row.image_id
    assert unavailable["galleries"][0]["orphaned"] is False

    empty = [GuideMediaCatalogConfigOut(config_id="local:file", source="file", platforms=[])]
    monkeypatch.setattr(
        service,
        "load_admin_catalog",
        AsyncMock(
            return_value=_loaded_catalog(
                entries=empty, available=True, orphaned_known=True, error=None
            )
        ),
    )
    empty_payload = asyncio.run(get_payload())
    assert empty_payload["catalog_available"] is True
    assert empty_payload["catalog_error"] is None
    assert empty_payload["orphaned_known"] is True
    assert empty_payload["orphaned"] == [
        {"config_id": "local:file", "platform": "ios", "client_id": "client"}
    ]


def test_plugin_contributes_janitor_worker() -> None:
    ctx = cast(
        Any,
        SimpleNamespace(settings=cast(Settings, settings_stub()), session_factory=None),
    )
    specs = GuideMediaPlugin().worker_tasks(ctx)
    assert [spec.name for spec in specs] == ["GuideMediaJanitor"]


def test_update_route_returns_serialized_image_after_commit(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = asyncio.run(_create(harness, captions={"en": "Before"}))
    monkeypatch.setattr(routes, "_require_admin_user_id", lambda _request: 7)

    async def scenario() -> None:
        async with TestClient(TestServer(_app(harness))) as client:
            response = await client.patch(
                f"/api/admin/guide-media/images/{row.image_id}",
                json={"captions": {"ru": "После", "en": "After"}},
            )
            payload = await response.json()
        assert response.status == 200
        assert payload["ok"] is True
        assert payload["image"]["captions"] == {"ru": "После", "en": "After"}

    asyncio.run(scenario())


def test_reorder_route_returns_serialized_gallery_after_commit(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = asyncio.run(_create(harness, order=0))
    second = asyncio.run(_create(harness, order=1))
    monkeypatch.setattr(routes, "_require_admin_user_id", lambda _request: 7)

    async def scenario() -> None:
        async with TestClient(TestServer(_app(harness))) as client:
            response = await client.post(
                "/api/admin/guide-media/images/reorder",
                json={
                    "config_id": "local:file",
                    "platform": "ios",
                    "client_id": "client",
                    "image_ids": [second.image_id, first.image_id],
                },
            )
            payload = await response.json()
        assert response.status == 200
        assert [item["image_id"] for item in payload["gallery"]["images"]] == [
            second.image_id,
            first.image_id,
        ]

    asyncio.run(scenario())
