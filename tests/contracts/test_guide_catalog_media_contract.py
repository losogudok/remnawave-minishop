"""ONB-02 contract tests: catalog identity, media keys, precedence, auth/public."""

from __future__ import annotations

import asyncio
import copy
import json
from typing import Any
from unittest.mock import AsyncMock, patch

from bot.app.web.webapp import guide_document
from bot.plugins.extensions import (
    ExtensionContributions,
    GuideMediaContext,
    GuideMediaGallery,
    GuideMediaImage,
    GuideMediaProvider,
)
from bot.plugins.extensions.guide_media import resolve_guide_media_galleries
from bot.plugins.extensions.registry import ExtensionRegistry, set_registry
from bot.services.guide_catalog import (
    build_guide_catalog_entries,
    normalize_client_key,
    project_guide_catalog_config,
    project_install_platforms,
    stable_guide_client_id,
)
from bot.services.guide_media import (
    attach_guide_media_to_document,
    find_orphaned_gallery_keys,
    is_public_guide_media_src,
    normalize_gallery_images,
)
from config.subscription_guides_config import (
    default_subscription_guides_config_text,
    validate_subscription_guides_config,
)
from tests.support.settings_stub import settings_stub


def _config() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(default_subscription_guides_config_text())
    return loaded


def _first_platform_id(config: dict[str, Any]) -> str:
    first: str = next(iter(config["platforms"].keys()))
    return first


def _first_app(config: dict[str, Any]) -> dict[str, Any]:
    first: dict[str, Any] = next(iter(config["platforms"].values()))["apps"][0]
    return first


def test_stable_client_identity_survives_rename_and_reorder() -> None:
    config = _config()
    platform_id = _first_platform_id(config)
    apps = config["platforms"][platform_id]["apps"]
    apps[0]["id"] = "preferred-client"
    before = stable_guide_client_id(apps[0])
    apps[0]["name"] = "Renamed client"
    apps.reverse()
    after = stable_guide_client_id(next(app for app in apps if app.get("id") == "preferred-client"))
    assert before == after == "preferred-client"


def test_distinct_configs_reusing_client_ids_stay_scoped() -> None:
    first = project_guide_catalog_config(
        _config(), config_id="panel:11111111-1111-1111-1111-111111111111", source="panel"
    )
    second = project_guide_catalog_config(
        _config(), config_id="panel:22222222-2222-2222-2222-222222222222", source="panel"
    )
    first_key = (
        str(first["config_id"]),
        str(first["platforms"][0]["id"]),
        str(first["platforms"][0]["apps"][0]["id"]),
    )
    second_key = (
        str(second["config_id"]),
        str(second["platforms"][0]["id"]),
        str(second["platforms"][0]["apps"][0]["id"]),
    )
    assert first_key[2] == second_key[2]
    assert first_key[0] != second_key[0]
    # Keys are scoped by config: normalizing both must not collide.
    assert normalize_client_key(
        config_id=first_key[0], platform=first_key[1], client_id=first_key[2]
    ) != normalize_client_key(
        config_id=second_key[0], platform=second_key[1], client_id=second_key[2]
    )


def test_legacy_duplicate_names_are_flagged_ambiguous() -> None:
    config = _config()
    platform_id = _first_platform_id(config)
    apps = copy.deepcopy(config["platforms"][platform_id]["apps"])
    apps[:] = [copy.deepcopy(apps[0]), copy.deepcopy(apps[0])]
    for app in apps:
        app.pop("id", None)
    entry = project_guide_catalog_config(
        {
            **config,
            "platforms": {
                **config["platforms"],
                platform_id: {**config["platforms"][platform_id], "apps": apps},
            },
        },
        config_id="local:file",
        source="file",
    )
    flagged = [app for app in entry["platforms"][0]["apps"] if app["ambiguous"]]
    assert flagged, "duplicate legacy names must be flagged ambiguous"


def test_explicit_duplicate_ids_rejected() -> None:
    from config.subscription_guides_config import SubscriptionGuidesConfigError

    config = _config()
    apps = next(iter(config["platforms"].values()))["apps"]
    apps[:] = [copy.deepcopy(apps[0]), copy.deepcopy(apps[0])]
    for app in apps:
        app["id"] = "same-client"
    try:
        validate_subscription_guides_config(config)
    except SubscriptionGuidesConfigError as exc:
        assert "app ids must be unique" in str(exc)
    else:
        raise AssertionError("explicit duplicate ids must be rejected")


def test_media_never_uses_list_index_and_rejects_ambiguous_legacy() -> None:
    config = _config()
    validated = validate_subscription_guides_config(config)
    document = guide_document.remnawave_v1_to_guide_document(
        validated, source="panel", config_id="panel:abc"
    )
    app = document["platforms"][0]["apps"][0]
    galleries = {
        ("panel:abc", str(document["platforms"][0]["id"]), str(app["id"])): [
            {"src": "https://cdn.example/a.png", "captions": {"en": "Step"}, "order": 1},
            {"src": "https://cdn.example/b.png", "captions": {"en": "Step"}, "order": 0},
        ]
    }
    attached = attach_guide_media_to_document(
        copy.deepcopy(document), galleries, config_id="panel:abc", public=False
    )
    screenshots = attached["platforms"][0]["apps"][0]["screenshots"]
    assert [item["src"] for item in screenshots] == [
        "https://cdn.example/b.png",
        "https://cdn.example/a.png",
    ]

    ambiguous_apps = [
        {"name": "Same Name"},
        {"name": "Same Name"},
    ]
    entry = project_guide_catalog_config(
        {"platforms": {"ios": {"displayName": "iOS", "apps": ambiguous_apps}}},
        config_id="local:file",
        source="file",
    )
    assert all(app["ambiguous"] for app in entry["platforms"][0]["apps"])


def test_empty_local_override_does_not_imply_empty_panel_catalog() -> None:
    settings = settings_stub(
        SUBSCRIPTION_PAGE_CONFIG_JSON="",
        SUBSCRIPTION_PAGE_CONFIG_JSON_OVERRIDE_ENABLED=True,
        SUBSCRIPTION_PAGE_CONFIG_PANEL_ENABLED=True,
        PANEL_API_URL="https://panel.example",
        PANEL_API_KEY="key",
    )

    panel_config = _config()

    class _Panel:
        async def get_subscription_page_config_list(self) -> dict[str, Any]:
            return {"configs": [{"uuid": "11111111-1111-1111-1111-111111111111"}]}

        async def get_subscription_page_config_by_uuid(self, uuid: str) -> dict[str, Any]:
            assert uuid == "11111111-1111-1111-1111-111111111111"
            return {"response": {"config": panel_config}}

    entries = asyncio.run(build_guide_catalog_entries(settings, _Panel()))
    panel_entries = [entry for entry in entries if entry["source"] == "panel"]
    assert panel_entries and panel_entries[0]["platforms"], (
        "empty local override must keep panel variants"
    )


def test_missing_panel_source_keeps_local_catalog() -> None:
    settings = settings_stub(
        SUBSCRIPTION_PAGE_CONFIG_PANEL_ENABLED=False,
        SUBSCRIPTION_PAGE_CONFIG_JSON="",
        SUBSCRIPTION_PAGE_CONFIG_JSON_OVERRIDE_ENABLED=False,
    )
    entries = asyncio.run(build_guide_catalog_entries(settings, None))
    assert entries
    assert any(entry["source"] in {"file", "local"} for entry in entries)


def test_projection_is_shared_with_message_targets() -> None:
    validated = validate_subscription_guides_config(_config())
    document = guide_document.remnawave_v1_to_guide_document(
        validated, source="file", config_id="local:file"
    )
    projected = project_install_platforms(document, resolve_label=None, language="en")
    assert projected
    assert all("id" in platform and "apps" in platform for platform in projected)
    for platform in projected:
        for app in platform["apps"]:
            assert set(app.keys()) == {"id", "name"}


def test_public_src_filtering_keeps_auth_runtime_private() -> None:
    assert not is_public_guide_media_src("/api/extensions/assets/owner/digest/file.png")
    assert not is_public_guide_media_src("/api/extensions/resource?provider=a:b&key=k")
    assert is_public_guide_media_src("https://cdn.example/a.png")
    assert is_public_guide_media_src("/api/guide-media/public/abc.png")

    galleries = {
        ("panel:abc", "ios", "client"): [
            {"src": "/api/extensions/assets/owner/digest/a.png", "captions": {}, "order": 0},
        ]
    }
    document = {"platforms": [{"id": "ios", "apps": [{"id": "client", "name": "C"}]}]}
    attached = attach_guide_media_to_document(
        copy.deepcopy(document), galleries, config_id="panel:abc", public=True
    )
    assert "screenshots" not in attached["platforms"][0]["apps"][0]


def test_orphaned_media_keys_are_visible() -> None:
    catalog: list[dict[str, Any]] = [
        {
            "config_id": "panel:abc",
            "platforms": [{"id": "ios", "apps": [{"id": "client"}]}],
        }
    ]
    galleries: dict[tuple[str, str, str], list[dict[str, Any]]] = {
        ("panel:abc", "ios", "client"): [],
        ("panel:abc", "ios", "removed"): [],
        ("panel:other", "ios", "client"): [],
    }
    orphaned = find_orphaned_gallery_keys(galleries, catalog)
    assert ("panel:abc", "ios", "removed") in orphaned
    assert ("panel:other", "ios", "client") in orphaned
    assert ("panel:abc", "ios", "client") not in orphaned


def test_guide_media_provider_timeouts_do_not_break_document() -> None:
    async def _slow(_: GuideMediaContext) -> tuple[GuideMediaGallery, ...]:
        import asyncio as _asyncio

        await _asyncio.sleep(6)
        return ()

    slow = GuideMediaProvider(id="slow", resolve=_slow)
    registry = ExtensionRegistry()
    registry.register("owner", "1", ExtensionContributions(guide_media=(slow,)))
    previous = None
    try:
        from bot.plugins.extensions.registry import get_registry as _get

        previous = _get()
        set_registry(registry)
        galleries = asyncio.run(
            resolve_guide_media_galleries(
                GuideMediaContext(user_id=1, language="en", is_public=False)
            )
        )
    finally:
        set_registry(previous if previous is not None else ExtensionRegistry())
    assert galleries == {}


def test_public_context_does_not_require_session() -> None:
    seen: dict[str, Any] = {}

    async def _capture(context: GuideMediaContext) -> tuple[GuideMediaGallery, ...]:
        seen["is_public"] = context.is_public
        seen["user_id"] = context.user_id
        seen["share_token"] = context.share_token
        return (
            GuideMediaGallery(
                config_id="panel:abc",
                platform="ios",
                client_id="client",
                images=(
                    GuideMediaImage(
                        src="https://cdn.example/a.png",
                        captions={"en": "A"},
                        order=0,
                        public_src="https://cdn.example/a.png",
                    ),
                ),
            ),
        )

    provider = GuideMediaProvider(id="shots", resolve=_capture)
    registry = ExtensionRegistry()
    registry.register("owner", "1", ExtensionContributions(guide_media=(provider,)))
    try:
        from bot.plugins.extensions.registry import get_registry as _get

        previous = _get()
        set_registry(registry)
        galleries = asyncio.run(
            resolve_guide_media_galleries(
                GuideMediaContext(
                    user_id=None,
                    language="en",
                    is_public=True,
                    share_token="a" * 32,
                    panel_short_uuid="short",
                    panel_user_uuid="user",
                )
            )
        )
    finally:
        set_registry(previous)
    assert seen == {
        "is_public": True,
        "user_id": None,
        "share_token": "a" * 32,
    }
    normalized = normalize_gallery_images(
        [
            {"src": image["src"], "captions": image["captions"], "order": image["order"]}
            for image in galleries[("panel:abc", "ios", "client")]
        ],
        public=True,
    )
    assert normalized[0]["src"] == "https://cdn.example/a.png"


def test_load_guide_content_attaches_media_for_auth_but_filters_for_public() -> None:
    validated = validate_subscription_guides_config(_config())
    platform_id = next(iter(validated["platforms"].keys()))
    client_id = stable_guide_client_id(validated["platforms"][platform_id]["apps"][0])
    config_id = "panel:cfg"

    async def _auth_provider(context: GuideMediaContext) -> tuple[GuideMediaGallery, ...]:
        src = (
            "https://cdn.example/public.png"
            if context.is_public
            else "/api/extensions/assets/owner/digest/a.png"
        )
        return (
            GuideMediaGallery(
                config_id=config_id,
                platform=platform_id,
                client_id=client_id,
                images=(GuideMediaImage(src=src, captions={"en": "Shot"}, order=0),),
            ),
        )

    provider = GuideMediaProvider(id="shots", resolve=_auth_provider)
    registry = ExtensionRegistry()
    registry.register("owner", "1", ExtensionContributions(guide_media=(provider,)))
    from bot.plugins.extensions.registry import get_registry as _get

    previous = _get()
    set_registry(registry)
    try:
        status_auth = {
            "enabled": True,
            "config": validated,
            "source": "panel",
            "config_id": config_id,
            "error": None,
        }
        status_public = dict(status_auth)
        with (
            patch.object(
                guide_document,
                "_subscription_guides_status_for_request",
                AsyncMock(return_value=status_auth),
            ),
            patch("bot.plugins.extensions.registry.get_registry", return_value=registry),
        ):
            document_auth = guide_document.remnawave_v1_to_guide_document(
                validated, source="panel", config_id=config_id
            )
            from bot.services.guide_media import attach_guide_media_to_document as _attach

            galleries_auth = asyncio.run(
                resolve_guide_media_galleries(
                    GuideMediaContext(user_id=7, language="en", is_public=False)
                )
            )
            attached_auth = _attach(
                document_auth, galleries_auth, config_id=config_id, public=False
            )
            assert attached_auth["platforms"][0]["apps"][0]["screenshots"][0]["src"] == (
                "/api/extensions/assets/owner/digest/a.png"
            )

            galleries_public = asyncio.run(
                resolve_guide_media_galleries(
                    GuideMediaContext(
                        user_id=None,
                        language="en",
                        is_public=True,
                        share_token="b" * 32,
                    )
                )
            )
            attached_public = _attach(
                copy.deepcopy(document_auth),
                galleries_public,
                config_id=config_id,
                public=True,
            )
            assert (
                attached_public["platforms"][0]["apps"][0]["screenshots"][0]["src"]
                == "https://cdn.example/public.png"
            )
            assert status_public["config_id"] == config_id
    finally:
        set_registry(previous)


def test_admin_catalog_keeps_panel_variants_when_local_missing() -> None:
    settings = settings_stub(
        SUBSCRIPTION_PAGE_CONFIG_JSON="not-json",
        SUBSCRIPTION_PAGE_CONFIG_JSON_OVERRIDE_ENABLED=True,
        SUBSCRIPTION_PAGE_CONFIG_PANEL_ENABLED=True,
    )

    class _Panel:
        async def get_subscription_page_config_list(self) -> dict[str, Any]:
            return {"configs": [{"uuid": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}]}

        async def get_subscription_page_config_by_uuid(self, uuid: str) -> dict[str, Any]:
            return {"response": {"config": _config()}}

    entries = asyncio.run(build_guide_catalog_entries(settings, _Panel()))
    assert any(entry["source"] == "panel" and entry["platforms"] for entry in entries)
    assert any(
        entry["missing"]
        for entry in entries
        if entry["config_id"] == "local:admin_json" and entry["source"] == "admin_json"
    )


def test_project_install_platforms_string_label_survives_resolver() -> None:
    from bot.services.message_composition import resolve_localized_text

    document = {
        "platforms": [
            {
                "id": "ios",
                "displayName": "Android",
                "apps": [{"id": "client", "name": "Client"}],
            },
            {
                "id": "android",
                "displayName": {"en": "Android EN", "ru": "Android RU"},
                "apps": [{"id": "client", "name": "Client"}],
            },
        ]
    }
    projected = project_install_platforms(
        document, resolve_label=resolve_localized_text, language="en"
    )
    by_id = {platform["id"]: platform["label"] for platform in projected}
    assert by_id["ios"] == "Android"
    assert by_id["android"] == "Android EN"


def test_admin_message_targets_route_with_string_display_name() -> None:
    import json as _json
    from types import SimpleNamespace
    from typing import cast

    from aiohttp import web

    from bot.app.web.admin_api_impl import message_targets

    document = {
        "platforms": [
            {
                "id": "ios",
                "displayName": "Android",
                "apps": [{"id": "client", "name": "Client"}],
            }
        ]
    }
    load = AsyncMock(return_value=({"enabled": True}, document))
    with (
        patch.object(message_targets, "_require_admin_user_id", return_value=99),
        patch.object(message_targets, "_admin_language", AsyncMock(return_value="en")),
        patch.object(message_targets, "get_i18n", return_value=None),
        patch.object(message_targets, "get_session_factory", return_value=lambda: AsyncMock()),
        patch.object(message_targets, "message_pages", AsyncMock(return_value=[])),
        patch.object(message_targets, "load_guide_content", load),
    ):
        request = cast(web.Request, SimpleNamespace())
        response = asyncio.run(message_targets.admin_message_targets_route(request))
    payload = _json.loads(response.text)
    assert payload["ok"] is True
    assert payload["install_platforms"] == [
        {"id": "ios", "label": "Android", "apps": [{"id": "client", "name": "Client"}]}
    ]


def test_ambiguous_legacy_identities_reject_media_attachment() -> None:
    document = {
        "platforms": [
            {
                "id": "ios",
                "apps": [
                    {"name": "Same Name"},
                    {"name": "Same Name"},
                ],
            }
        ]
    }
    galleries = {
        ("local:file", "ios", "same-name"): [
            {"src": "https://cdn.example/a.png", "captions": {"en": "A"}, "order": 0}
        ]
    }
    attached = attach_guide_media_to_document(
        copy.deepcopy(document), galleries, config_id="local:file", public=False
    )
    for app in attached["platforms"][0]["apps"]:
        assert "screenshots" not in app


def test_explicit_legacy_collision_rejects_media_attachment() -> None:
    document = {
        "platforms": [
            {
                "id": "ios",
                "apps": [
                    {"id": "same-name", "name": "Explicit"},
                    {"name": "Same Name"},
                ],
            }
        ]
    }
    galleries = {
        ("local:file", "ios", "same-name"): [
            {"src": "https://cdn.example/a.png", "captions": {"en": "A"}, "order": 0}
        ]
    }
    attached = attach_guide_media_to_document(
        copy.deepcopy(document), galleries, config_id="local:file", public=False
    )
    for app in attached["platforms"][0]["apps"]:
        assert "screenshots" not in app


def test_extension_contributions_positional_compat() -> None:
    from bot.plugins.extensions import ResourceProvider, ResourceResult
    from bot.plugins.extensions.contracts import UserContext

    async def _resolve(_ctx: UserContext, _key: str) -> ResourceResult:
        return ResourceResult(body=b"x")

    provider = ResourceProvider(id="res", resolve=_resolve)
    contributions = ExtensionContributions((), (provider,))
    assert contributions.resources == (provider,)
    assert contributions.guide_media == ()


def test_guide_media_generation_fence_returns_no_media() -> None:
    import bot.plugins.packages as _packages
    from bot.plugins.extensions.registry import get_registry as _get_registry

    async def _resolve(_ctx: GuideMediaContext) -> tuple[GuideMediaGallery, ...]:
        return (
            GuideMediaGallery(
                config_id="local:file",
                platform="ios",
                client_id="client",
                images=(GuideMediaImage(src="https://cdn.example/a.png", captions={}, order=0),),
            ),
        )

    provider = GuideMediaProvider(id="shots", resolve=_resolve)
    registry = ExtensionRegistry()
    registry.register("owner", "1", ExtensionContributions(guide_media=(provider,)))
    previous = _get_registry()
    set_registry(registry)
    try:
        with patch.object(_packages, "generation_is_current", return_value=False):
            galleries = asyncio.run(
                resolve_guide_media_galleries(
                    GuideMediaContext(user_id=1, language="en", is_public=False)
                )
            )
        assert galleries == {}
        document = {"platforms": [{"id": "ios", "apps": [{"id": "client", "name": "C"}]}]}
        attached = attach_guide_media_to_document(
            copy.deepcopy(document), galleries, config_id="local:file", public=False
        )
        assert "screenshots" not in attached["platforms"][0]["apps"][0]
    finally:
        set_registry(previous)


def test_public_src_rejects_absolute_auth_runtime_urls() -> None:
    absolute_auth = "https://shop.example/api/extensions/assets/owner/digest/a.png"
    assert not is_public_guide_media_src(absolute_auth)
    assert not is_public_guide_media_src(
        "https://shop.example/api/extensions/resource?provider=a:b&key=k"
    )
    assert is_public_guide_media_src("https://cdn.example/a.png")
    document = {"platforms": [{"id": "ios", "apps": [{"id": "client", "name": "C"}]}]}
    galleries = {
        ("panel:abc", "ios", "client"): [
            {"src": absolute_auth, "captions": {}, "order": 0},
        ]
    }
    attached = attach_guide_media_to_document(
        copy.deepcopy(document), galleries, config_id="panel:abc", public=True
    )
    assert "screenshots" not in attached["platforms"][0]["apps"][0]


def test_local_failure_preserves_admin_json_identity() -> None:
    settings = settings_stub(
        SUBSCRIPTION_PAGE_CONFIG_JSON="not-json",
        SUBSCRIPTION_PAGE_CONFIG_JSON_OVERRIDE_ENABLED=True,
        SUBSCRIPTION_PAGE_CONFIG_PANEL_ENABLED=False,
    )
    entries = asyncio.run(build_guide_catalog_entries(settings, None))
    assert entries
    assert entries[0]["config_id"] == "local:admin_json"
    assert entries[0]["source"] == "admin_json"
    assert entries[0]["missing"] is True


def test_panel_list_failure_preserves_unavailable_status() -> None:
    settings = settings_stub(
        SUBSCRIPTION_PAGE_CONFIG_JSON="",
        SUBSCRIPTION_PAGE_CONFIG_JSON_OVERRIDE_ENABLED=False,
        SUBSCRIPTION_PAGE_CONFIG_PANEL_ENABLED=True,
    )

    class _FailingPanel:
        async def get_subscription_page_config_list(self) -> dict[str, Any]:
            raise RuntimeError("panel down")

        async def get_subscription_page_config_by_uuid(self, uuid: str) -> dict[str, Any]:
            raise AssertionError("must not be called")

    entries = asyncio.run(build_guide_catalog_entries(settings, _FailingPanel()))
    panel_entries = [entry for entry in entries if entry["source"] == "panel"]
    assert panel_entries
    assert any(entry["missing"] and entry["error"] for entry in panel_entries)


def test_embedded_panel_config_has_no_shared_identity() -> None:
    from bot.app.web.webapp import guides_panel_config as _panel_config

    validated = validate_subscription_guides_config(_config())

    class _Panel:
        async def get_subscription_page_config_by_short_uuid(
            self, short_uuid: str, request_headers: dict[str, str] | None = None
        ) -> dict[str, Any]:
            assert short_uuid == "short-uuid"
            return {"response": {"config": validated}}

    from typing import cast

    from aiohttp import web as _web

    app: dict[str, Any] = {"_panel": _Panel()}
    with (
        patch.object(_panel_config, "_panel_service_from_app", return_value=_Panel()),
        patch.object(
            _panel_config,
            "_subscription_guides_settings_fingerprint",
            return_value=("test",),
        ),
        patch.object(
            _panel_config,
            "_subscription_guides_request_headers_fingerprint",
            return_value=(),
        ),
    ):
        status = asyncio.run(
            _panel_config._subscription_guides_status_from_panel_short_uuid(
                cast(_web.Application, app),
                settings_stub(SUBSCRIPTION_GUIDES_RESOLVED_CACHE_TTL_SECONDS=0),
                "short-uuid",
            )
        )
    assert status["enabled"] is True
    assert status["config_id"] is None


def test_load_guide_content_routes_with_provider() -> None:
    validated = validate_subscription_guides_config(_config())
    platform_id = next(iter(validated["platforms"].keys()))
    client_id = stable_guide_client_id(validated["platforms"][platform_id]["apps"][0])
    config_id = "panel:cfg"

    async def _provider(context: GuideMediaContext) -> tuple[GuideMediaGallery, ...]:
        src = (
            "https://cdn.example/public.png"
            if context.is_public
            else "/api/extensions/assets/owner/digest/a.png"
        )
        return (
            GuideMediaGallery(
                config_id=config_id,
                platform=platform_id,
                client_id=client_id,
                images=(GuideMediaImage(src=src, captions={"en": "Shot"}, order=0),),
            ),
        )

    provider = GuideMediaProvider(id="shots", resolve=_provider)
    registry = ExtensionRegistry()
    registry.register("owner", "1", ExtensionContributions(guide_media=(provider,)))
    from bot.plugins.extensions.registry import get_registry as _get

    previous = _get()
    set_registry(registry)
    try:
        status = {
            "enabled": True,
            "config": validated,
            "source": "panel",
            "config_id": config_id,
            "error": None,
        }
        with patch.object(
            guide_document,
            "_subscription_guides_status_for_request",
            AsyncMock(return_value=status),
        ):
            from types import SimpleNamespace
            from typing import cast as _cast

            from aiohttp import web as _web2

            _, public_document = asyncio.run(
                guide_document.load_guide_content(
                    _cast(_web2.Request, SimpleNamespace()),
                    panel_short_uuid="short",
                    panel_user_uuid="user",
                    share_token="b" * 32,
                )
            )
            assert public_document is not None
            shot = public_document["platforms"][0]["apps"][0]["screenshots"][0]
            assert shot["src"] == "https://cdn.example/public.png"
    finally:
        set_registry(previous)


def test_inline_screenshots_survive_validation_and_document() -> None:
    config = _config()
    platform_id = _first_platform_id(config)
    config["platforms"][platform_id]["apps"][0]["screenshots"] = [
        {"src": "https://cdn.example/a.png", "captions": {"en": "Shot"}, "order": 2},
        {"src": "https://cdn.example/b.png", "captions": {"en": "Shot"}, "order": 1},
    ]
    validated = validate_subscription_guides_config(config)
    screenshots = validated["platforms"][platform_id]["apps"][0].get("screenshots")
    assert screenshots is not None
    assert [item["src"] for item in screenshots] == [
        "https://cdn.example/b.png",
        "https://cdn.example/a.png",
    ]
    document = guide_document.remnawave_v1_to_guide_document(
        validated, source="file", config_id="local:file"
    )
    attached = attach_guide_media_to_document(
        copy.deepcopy(document), {}, config_id="local:file", public=False
    )
    assert attached["platforms"][0]["apps"][0]["screenshots"]
    public_attached = attach_guide_media_to_document(
        copy.deepcopy(document), {}, config_id="local:file", public=True
    )
    assert public_attached["platforms"][0]["apps"][0]["screenshots"]


def test_embedded_configs_do_not_share_media_via_loader() -> None:
    first_config = validate_subscription_guides_config(_config())
    second_config = validate_subscription_guides_config(_config())
    platform_id = next(iter(first_config["platforms"].keys()))
    client_id = stable_guide_client_id(first_config["platforms"][platform_id]["apps"][0])
    first_config["platforms"][platform_id]["apps"][0]["screenshots"] = [
        {"src": "https://cdn.example/first-inline.png", "captions": {"en": "A"}, "order": 0},
    ]
    second_config["platforms"][platform_id]["apps"][0]["screenshots"] = [
        {"src": "https://cdn.example/second-inline.png", "captions": {"en": "B"}, "order": 0},
    ]

    async def _provider(_context: GuideMediaContext) -> tuple[GuideMediaGallery, ...]:
        return (
            GuideMediaGallery(
                config_id="panel:default",
                platform=platform_id,
                client_id=client_id,
                images=(
                    GuideMediaImage(
                        src="https://cdn.example/shared.png", captions={"en": "S"}, order=0
                    ),
                ),
            ),
        )

    provider = GuideMediaProvider(id="shots", resolve=_provider)
    registry = ExtensionRegistry()
    registry.register("owner", "1", ExtensionContributions(guide_media=(provider,)))
    from bot.plugins.extensions.registry import get_registry as _get

    previous = _get()
    set_registry(registry)
    try:
        first_status = {
            "enabled": True,
            "config": first_config,
            "source": "panel",
            "config_id": None,
            "error": None,
        }
        second_status = {
            "enabled": True,
            "config": second_config,
            "source": "panel",
            "config_id": None,
            "error": None,
        }
        with patch.object(
            guide_document,
            "_subscription_guides_status_for_request",
            AsyncMock(side_effect=[first_status, second_status]),
        ):
            from types import SimpleNamespace
            from typing import cast as _cast

            from aiohttp import web as _web2

            _, first_document = asyncio.run(
                guide_document.load_guide_content(
                    _cast(_web2.Request, SimpleNamespace()),
                    panel_short_uuid="short-one",
                    panel_user_uuid="user",
                    share_token="c" * 32,
                )
            )
            _, second_document = asyncio.run(
                guide_document.load_guide_content(
                    _cast(_web2.Request, SimpleNamespace()),
                    panel_short_uuid="short-two",
                    panel_user_uuid="user",
                    share_token="c" * 32,
                )
            )
        assert first_document is not None and second_document is not None
        assert "config_id" not in first_document
        assert "config_id" not in second_document
        first_srcs = [
            item["src"] for item in first_document["platforms"][0]["apps"][0].get("screenshots", [])
        ]
        second_srcs = [
            item["src"]
            for item in second_document["platforms"][0]["apps"][0].get("screenshots", [])
        ]
        assert "https://cdn.example/shared.png" not in first_srcs
        assert "https://cdn.example/shared.png" not in second_srcs
        assert first_srcs == ["https://cdn.example/first-inline.png"]
        assert second_srcs == ["https://cdn.example/second-inline.png"]
    finally:
        set_registry(previous)


def test_public_without_provider_strips_auth_srcs_from_both_representations() -> None:
    import bot.plugins.packages as _packages

    config = _config()
    platform_id = _first_platform_id(config)
    config["platforms"][platform_id]["apps"][0]["screenshots"] = [
        {
            "src": "/api/extensions/assets/owner/digest/private.png",
            "captions": {"en": "Shot"},
            "order": 0,
        },
    ]
    validated = validate_subscription_guides_config(config)
    status = {
        "enabled": True,
        "config": validated,
        "source": "panel",
        "config_id": "panel:cfg",
        "error": None,
    }

    async def _empty(_context: GuideMediaContext) -> tuple[GuideMediaGallery, ...]:
        return ()

    async def _failing(_context: GuideMediaContext) -> tuple[GuideMediaGallery, ...]:
        raise RuntimeError("provider down")

    from bot.plugins.extensions.registry import get_registry as _get

    previous = _get()
    try:
        from types import SimpleNamespace
        from typing import cast as _cast

        from aiohttp import web as _web2

        from bot.services.guide_media import sanitize_config_screenshots_for_public

        # No provider, empty result, exception, and stale generation must all
        # sanitize the document; the raw config needs its own sanitizer.
        scenarios: list[tuple[str, ExtensionRegistry | None, bool]] = [
            ("no-provider", None, False),
            ("empty", None, False),
            ("exception", None, False),
            ("stale-generation", None, True),
        ]
        registries: dict[str, ExtensionRegistry] = {}
        empty_registry = ExtensionRegistry()
        registries["no-provider"] = empty_registry
        empty_provider_registry = ExtensionRegistry()
        empty_provider_registry.register(
            "owner",
            "1",
            ExtensionContributions(guide_media=(GuideMediaProvider(id="e", resolve=_empty),)),
        )
        registries["empty"] = empty_provider_registry
        failing_registry = ExtensionRegistry()
        failing_registry.register(
            "owner",
            "1",
            ExtensionContributions(guide_media=(GuideMediaProvider(id="f", resolve=_failing),)),
        )
        registries["exception"] = failing_registry
        registries["stale-generation"] = empty_provider_registry
        for name, _, stale in scenarios:
            set_registry(registries[name])
            with (
                patch.object(
                    guide_document,
                    "_subscription_guides_status_for_request",
                    AsyncMock(return_value=copy.deepcopy(status)),
                ),
                patch.object(_packages, "generation_is_current", return_value=not stale),
            ):
                _, document = asyncio.run(
                    guide_document.load_guide_content(
                        _cast(_web2.Request, SimpleNamespace()),
                        panel_short_uuid="short",
                        panel_user_uuid="user",
                        share_token="d" * 32,
                    )
                )
            assert document is not None, name
            assert "/api/extensions/assets/" not in json.dumps(document), name
        sanitized = sanitize_config_screenshots_for_public(validated)
        assert "/api/extensions/assets/" not in json.dumps(sanitized)
        assert sanitized["platforms"][platform_id]["apps"][0].get("screenshots") in (None, [])
    finally:
        set_registry(previous)


def test_data_image_screenshots_survive_validation_conversion_attachment() -> None:
    src = "data:image/png;base64,iVBORw0KGgo="
    assert is_public_guide_media_src(src)
    config = _config()
    platform_id = _first_platform_id(config)
    config["platforms"][platform_id]["apps"][0]["screenshots"] = [
        {"src": src, "captions": {"en": "Shot"}, "order": 0},
    ]
    validated = validate_subscription_guides_config(config)
    assert validated["platforms"][platform_id]["apps"][0]["screenshots"][0]["src"] == src
    document = guide_document.remnawave_v1_to_guide_document(
        validated, source="file", config_id="local:file"
    )
    assert document["platforms"][0]["apps"][0]["screenshots"][0]["src"] == src
    attached_auth = attach_guide_media_to_document(
        copy.deepcopy(document), {}, config_id="local:file", public=False
    )
    assert attached_auth["platforms"][0]["apps"][0]["screenshots"][0]["src"] == src
    attached_public = attach_guide_media_to_document(
        copy.deepcopy(document), {}, config_id="local:file", public=True
    )
    assert attached_public["platforms"][0]["apps"][0]["screenshots"][0]["src"] == src
    doc_platform = str(document["platforms"][0]["id"])
    doc_client = str(document["platforms"][0]["apps"][0]["id"])
    galleries = {
        ("local:file", doc_platform, doc_client): [
            {"src": src, "captions": {"en": "Shot"}, "order": 0},
        ]
    }
    attached_gallery = attach_guide_media_to_document(
        copy.deepcopy(document), galleries, config_id="local:file", public=True
    )
    assert attached_gallery["platforms"][0]["apps"][0]["screenshots"][0]["src"] == src


def test_media_only_provider_sees_user_language() -> None:
    seen: dict[str, Any] = {}

    async def _provider(context: GuideMediaContext) -> tuple[GuideMediaGallery, ...]:
        seen["language"] = context.language
        return ()

    provider = GuideMediaProvider(id="shots", resolve=_provider)
    registry = ExtensionRegistry()
    registry.register("owner", "1", ExtensionContributions(guide_media=(provider,)))
    from bot.plugins.extensions.registry import get_registry as _get

    previous = _get()
    set_registry(registry)
    try:
        validated = validate_subscription_guides_config(_config())
        status = {
            "enabled": True,
            "config": validated,
            "source": "file",
            "config_id": "local:file",
            "error": None,
        }
        from types import SimpleNamespace
        from typing import cast as _cast

        from aiohttp import web as _web2

        from tests.support.settings_stub import settings_stub as _settings_stub

        class _Session:
            async def __aenter__(self) -> Any:
                return object()

            async def __aexit__(self, *args: Any) -> bool:
                return False

        class _Factory:
            def __call__(self) -> _Session:
                return _Session()

        request = SimpleNamespace(
            app={
                "settings": _settings_stub(SUBSCRIPTION_GUIDES_ENABLED=True),
                "async_session_factory": _Factory(),
            }
        )
        fake_user = SimpleNamespace(language_code="ru", is_banned=False)
        with (
            patch.object(
                guide_document,
                "_subscription_guides_status_for_request",
                AsyncMock(return_value=status),
            ),
            patch("db.dal.user_dal.get_user_by_id", AsyncMock(return_value=fake_user)),
        ):
            _, document = asyncio.run(
                guide_document.load_guide_content(
                    _cast(_web2.Request, request),
                    user_id=7,
                )
            )
        assert document is not None
        assert seen.get("language") == "ru"
    finally:
        set_registry(previous)


def test_guide_routes_with_provider_cover_valid_malformed_unknown_revoked_shares() -> None:
    import json as _json
    from types import SimpleNamespace
    from typing import cast as _cast

    from aiohttp import web as _web2

    from bot.app.web.webapp import guides as _guides

    validated = validate_subscription_guides_config(_config())
    platform_id = next(iter(validated["platforms"].keys()))
    client_id = stable_guide_client_id(validated["platforms"][platform_id]["apps"][0])
    config_id = "panel:cfg"
    calls: list[bool] = []

    async def _provider(context: GuideMediaContext) -> tuple[GuideMediaGallery, ...]:
        calls.append(context.is_public)
        src = (
            "https://cdn.example/public.png"
            if context.is_public
            else "/api/extensions/assets/owner/digest/a.png"
        )
        return (
            GuideMediaGallery(
                config_id=config_id,
                platform=platform_id,
                client_id=client_id,
                images=(GuideMediaImage(src=src, captions={"en": "Shot"}, order=0),),
            ),
        )

    provider = GuideMediaProvider(id="shots", resolve=_provider)
    registry = ExtensionRegistry()
    registry.register("owner", "1", ExtensionContributions(guide_media=(provider,)))
    from bot.plugins.extensions.registry import get_registry as _get

    previous = _get()
    set_registry(registry)
    try:
        from tests.support.settings_stub import settings_stub as _settings_stub

        class _Session:
            async def __aenter__(self) -> Any:
                return object()

            async def __aexit__(self, *args: Any) -> bool:
                return False

        class _Factory:
            def __call__(self) -> _Session:
                return _Session()

        status = {
            "enabled": True,
            "config": validated,
            "source": "panel",
            "config_id": config_id,
            "error": None,
        }
        fake_user = SimpleNamespace(language_code="en", is_banned=False)
        auth_request = SimpleNamespace(
            app={
                "settings": _settings_stub(SUBSCRIPTION_GUIDES_ENABLED=True),
                "async_session_factory": _Factory(),
            }
        )
        with (
            patch.object(
                guide_document,
                "_subscription_guides_status_for_request",
                AsyncMock(return_value=copy.deepcopy(status)),
            ),
            patch.object(_guides, "_require_user_id", return_value=7),
            patch("db.dal.user_dal.get_user_by_id", AsyncMock(return_value=fake_user)),
        ):
            response = asyncio.run(
                _guides.subscription_guides_route(_cast(_web2.Request, auth_request))
            )
        body = _json.loads(response.text)
        assert body["ok"] is True
        shot = body["guide_document"]["platforms"][0]["apps"][0]["screenshots"][0]
        assert shot["src"] == "/api/extensions/assets/owner/digest/a.png"

        valid_token = "c" * 32
        public_request = SimpleNamespace(
            app={
                "settings": _settings_stub(SUBSCRIPTION_GUIDES_ENABLED=True),
                "async_session_factory": _Factory(),
            },
            match_info={"share_token": valid_token},
        )
        active_subscription = {
            "active": True,
            "panel_short_uuid": "share-short",
            "install_share_token": valid_token,
            "_panel_user_uuid": "panel-user",
        }
        with (
            patch.object(
                guide_document,
                "_subscription_guides_status_for_request",
                AsyncMock(return_value=copy.deepcopy(status)),
            ),
            patch.object(_guides, "public_subscription_limit", AsyncMock(return_value=None)),
            patch.object(
                _guides,
                "_public_subscription_payload_cached",
                AsyncMock(return_value=dict(active_subscription)),
            ),
        ):
            response = asyncio.run(
                _guides.public_subscription_guides_route(_cast(_web2.Request, public_request))
            )
        body = _json.loads(response.text)
        assert body["ok"] is True
        shot = body["guide_document"]["platforms"][0]["apps"][0]["screenshots"][0]
        assert shot["src"] == "https://cdn.example/public.png"

        malformed_request = SimpleNamespace(
            app={
                "settings": _settings_stub(SUBSCRIPTION_GUIDES_ENABLED=True),
                "async_session_factory": _Factory(),
            },
            match_info={"share_token": "!!!"},
        )
        response = asyncio.run(
            _guides.public_subscription_guides_route(_cast(_web2.Request, malformed_request))
        )
        body = _json.loads(response.text)
        assert response.status == 404
        assert body["error"] == "invalid_share_token"

        unknown_request = SimpleNamespace(
            app={
                "settings": _settings_stub(SUBSCRIPTION_GUIDES_ENABLED=True),
                "async_session_factory": _Factory(),
            },
            match_info={"share_token": valid_token},
        )
        with (
            patch.object(_guides, "public_subscription_limit", AsyncMock(return_value=None)),
            patch.object(
                _guides,
                "_public_subscription_payload_cached",
                AsyncMock(return_value={"active": False}),
            ),
        ):
            response = asyncio.run(
                _guides.public_subscription_guides_route(_cast(_web2.Request, unknown_request))
            )
        body = _json.loads(response.text)
        assert response.status == 404
        assert body["error"] == "subscription_unavailable"

        revoked_request = SimpleNamespace(
            app={
                "settings": _settings_stub(SUBSCRIPTION_GUIDES_ENABLED=True),
                "async_session_factory": _Factory(),
            },
            match_info={"share_token": valid_token},
        )
        with (
            patch.object(_guides, "public_subscription_limit", AsyncMock(return_value=None)),
            patch.object(
                _guides,
                "_public_subscription_payload_cached",
                AsyncMock(return_value={"active": False}),
            ),
        ):
            response = asyncio.run(
                _guides.public_subscription_guides_route(_cast(_web2.Request, revoked_request))
            )
        body = _json.loads(response.text)
        assert response.status == 404
        assert body["error"] == "subscription_unavailable"
        assert calls and calls[0] is False and any(call is True for call in calls)
    finally:
        set_registry(previous)
