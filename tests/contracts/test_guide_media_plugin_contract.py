"""Contract checks for guide media routes, provider mapping and sanitization."""

from __future__ import annotations

import asyncio
from typing import cast

import pytest
from aiohttp import web
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.app.web.route_contracts import BINARY_RESPONSE_SCHEMA, get_contracts
from bot.plugins.builtin.guide_media import GuideMediaPlugin, routes, service
from bot.plugins.extensions import ExtensionContributions, GuideMediaContext
from bot.plugins.extensions.guide_media import resolve_guide_media_galleries
from bot.plugins.extensions.registry import (
    ExtensionRegistry,
    get_registry,
    set_registry,
)
from bot.plugins.spec import PluginContext
from config.settings import Settings
from db.guide_media_models import GuideMediaImage as GuideMediaImageRow
from tests.support.settings_stub import settings_stub


def _route_names() -> set[str]:
    routes.setup_guide_media_routes(web.Application())
    return {
        "guide_media_public_media_route",
        "admin_guide_media_galleries_route",
        "admin_guide_media_upload_route",
        "admin_guide_media_update_route",
        "admin_guide_media_reorder_route",
        "admin_guide_media_delete_route",
    }


def test_every_guide_media_route_registers_a_contract() -> None:
    contracts = get_contracts()
    assert _route_names() <= set(contracts)


def test_public_media_contract_is_binary_and_unauthenticated() -> None:
    routes.setup_guide_media_routes(web.Application())
    contract = get_contracts()["guide_media_public_media_route"]
    assert contract.response_content_type == "image/webp"
    assert contract.response_schema == BINARY_RESPONSE_SCHEMA
    assert contract.security == []


def test_admin_contracts_use_the_ok_envelope() -> None:
    contracts = get_contracts()
    for name in (
        "admin_guide_media_galleries_route",
        "admin_guide_media_upload_route",
        "admin_guide_media_update_route",
        "admin_guide_media_reorder_route",
        "admin_guide_media_delete_route",
    ):
        contract = contracts[name]
        assert contract.response_content_type == "application/json"
        assert contract.response_schema is not None


def test_upload_contract_requires_multipart() -> None:
    routes.setup_guide_media_routes(web.Application())
    contract = get_contracts()["admin_guide_media_upload_route"]
    assert contract.request_content is not None
    assert set(contract.request_content) == {"multipart/form-data"}


def test_routes_are_mounted_on_the_expected_paths() -> None:
    app = web.Application()
    routes.setup_guide_media_routes(app)
    paths = {resource.canonical for resource in app.router.resources()}
    assert paths == {
        "/api/plugins/guide_media/media/{digest}.webp",
        "/api/admin/guide-media/galleries",
        "/api/admin/guide-media/images",
        "/api/admin/guide-media/images/reorder",
        "/api/admin/guide-media/images/{image_id}",
    }


def test_plugin_contributes_a_public_gallery_provider() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(
                    lambda sync_connection: GuideMediaImageRow.__table__.create(
                        sync_connection, checkfirst=True
                    )
                )
            async with factory() as session:
                for order, (locale, caption) in enumerate((("en", "First"), ("ru", "Первый"))):
                    session.add(
                        GuideMediaImageRow(
                            image_id=f"{order:032x}",
                            digest=f"{order + 1:064x}",
                            config_id="local:file",
                            platform="ios",
                            client_id="client",
                            captions={locale: caption, "xx": "ignored"},
                            sort_order=order,
                            content_type="image/webp",
                            width=10,
                            height=10,
                            size_bytes=1,
                        )
                    )
                broken = GuideMediaImageRow(
                    image_id="f" * 32,
                    digest="f" * 64,
                    config_id="local:file",
                    platform="ios",
                    client_id="not valid!",
                    captions={"en": "Broken"},
                    sort_order=0,
                    content_type="image/webp",
                    width=10,
                    height=10,
                    size_bytes=1,
                )
                session.add(broken)
                await session.commit()
            ctx = PluginContext(settings=cast(Settings, settings_stub()), session_factory=factory)
            plugin = GuideMediaPlugin()
            contributions = plugin.extensions(ctx)
            assert isinstance(contributions, ExtensionContributions)
            provider = contributions.guide_media[0]
            assert provider.id == "guide_media"
            galleries = await provider.resolve(
                GuideMediaContext(user_id=None, language="en", is_public=True)
            )
            assert len(galleries) == 1
            assert galleries[0].config_id == "local:file"
            assert [image.order for image in galleries[0].images] == [0, 1]
            assert all(
                image.src == image.public_src
                and image.src.startswith("/api/plugins/guide_media/media/")
                for image in galleries[0].images
            )

            registry = ExtensionRegistry()
            registry.register(plugin.name, plugin.version, contributions)
            previous = get_registry()
            set_registry(registry)
            try:
                merged = await resolve_guide_media_galleries(
                    GuideMediaContext(user_id=None, language="en", is_public=True)
                )
            finally:
                set_registry(previous)
            key = ("local:file", "ios", "client")
            assert key in merged
            assert [item["captions"] for item in merged[key]] == [
                {"en": "First"},
                {"ru": "Первый"},
            ]
            assert all(
                item["src"].startswith("/api/plugins/guide_media/media/") for item in merged[key]
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_provider_skips_invalid_keys_and_caps_at_eight() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(
                    lambda sync_connection: GuideMediaImageRow.__table__.create(
                        sync_connection, checkfirst=True
                    )
                )
            async with factory() as session:
                for order in range(9):
                    session.add(
                        GuideMediaImageRow(
                            image_id=f"{order:032x}",
                            digest=f"{order:064x}",
                            config_id="local:file",
                            platform="ios",
                            client_id="client",
                            captions={"en": str(order)},
                            sort_order=order,
                            content_type="image/webp",
                            width=1,
                            height=1,
                            size_bytes=1,
                        )
                    )
                await session.commit()
            galleries = await service.resolve_galleries(factory)
            assert len(galleries) == 1
            assert len(galleries[0].images) == 8
            assert [image.order for image in galleries[0].images] == list(range(8))
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_admin_catalog_and_ambiguity_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = [
        service.GuideMediaCatalogConfigOut(
            config_id="local:file",
            source="file",
            platforms=[
                service.GuideMediaCatalogPlatformOut(
                    id="ios",
                    label="iOS",
                    apps=[
                        service.GuideMediaCatalogAppOut(
                            id="same-name",
                            name="Same name",
                            explicit_id=False,
                            ambiguous=True,
                        )
                    ],
                )
            ],
        )
    ]

    async def fake_catalog(*_args: object, **_kwargs: object) -> service.AdminCatalog:
        return service.AdminCatalog(entries=catalog, available=True, orphaned_known=True)

    monkeypatch.setattr(service, "load_admin_catalog", fake_catalog)

    async def scenario() -> None:
        with pytest.raises(service.GuideMediaError) as ambiguous:
            await service.require_upload_target(
                cast(Settings, settings_stub()),
                None,
                language="en",
                key=("local:file", "ios", "same-name"),
            )
        assert ambiguous.value.code == "ambiguous_client"
        with pytest.raises(service.GuideMediaError) as missing:
            await service.require_upload_target(
                cast(Settings, settings_stub()),
                None,
                language="en",
                key=("local:file", "ios", "another"),
            )
        assert missing.value.code == "unknown_guide_target"

    asyncio.run(scenario())


def test_gallery_key_validation_rejects_malformed_identity() -> None:
    with pytest.raises(service.GuideMediaError) as caught:
        service.normalize_key(config_id="", platform="ios", client_id="client")
    assert caught.value.code == "invalid_guide_key"


def test_guide_media_capability_is_declared() -> None:
    from bot.plugins import capabilities

    assert capabilities.CORE_PLUGIN_CAPABILITIES["guide_media"] == 1
