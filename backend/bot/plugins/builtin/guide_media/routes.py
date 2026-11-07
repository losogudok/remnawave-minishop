"""HTTP routes for the built-in guide media plugin."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from aiohttp import web

from bot.app.web.admin_api_impl.auth import _require_admin_user_id
from bot.app.web.admin_api_impl.broadcast_shortcodes import _admin_language
from bot.app.web.admin_api_impl.common import _error, _ok
from bot.app.web.context import get_session_factory, get_settings
from bot.app.web.request_parsing import (
    parse_body_or_400,
    parse_body_with_optional_image_or_400,
)
from bot.app.web.route_contracts import (
    BINARY_RESPONSE_SCHEMA,
    RouteContract,
    ok_envelope_for,
    register_contract,
    schema_ref,
)
from bot.services.guide_media import find_orphaned_gallery_keys
from bot.services.message_image_service import MessageImageError
from db.guide_media_models import GuideMediaImage

from . import service, storage
from .schemas import (
    AdminGuideMediaGalleriesOut,
    AdminGuideMediaReorderBody,
    AdminGuideMediaUpdateBody,
    AdminGuideMediaUploadBody,
    GuideMediaCatalogAppOut,
    GuideMediaCatalogConfigOut,
    GuideMediaCatalogPlatformOut,
    GuideMediaGalleryKeyOut,
    GuideMediaGalleryOut,
    GuideMediaImageOut,
)

_MEDIA_CACHE_CONTROL = "public, max-age=31536000, immutable"
_UPLOAD_REQUEST_CONTENT: dict[str, dict[str, Any]] = {
    "multipart/form-data": {
        "allOf": [
            schema_ref(AdminGuideMediaUploadBody),
            {
                "type": "object",
                "properties": {"image": BINARY_RESPONSE_SCHEMA},
            },
        ]
    }
}

register_contract(
    "guide_media_public_media_route",
    RouteContract(
        response_content_type="image/webp",
        response_schema=BINARY_RESPONSE_SCHEMA,
        security=[],
    ),
)
register_contract(
    "admin_guide_media_galleries_route",
    RouteContract(
        response_schema=ok_envelope_for(AdminGuideMediaGalleriesOut),
        models=(
            AdminGuideMediaGalleriesOut,
            GuideMediaCatalogConfigOut,
            GuideMediaCatalogPlatformOut,
            GuideMediaCatalogAppOut,
            GuideMediaGalleryOut,
            GuideMediaGalleryKeyOut,
            GuideMediaImageOut,
        ),
    ),
)
register_contract(
    "admin_guide_media_upload_route",
    RouteContract(
        request_content=_UPLOAD_REQUEST_CONTENT,
        response_schema=ok_envelope_for(GuideMediaImageOut, key="image"),
        models=(AdminGuideMediaUploadBody, GuideMediaImageOut),
    ),
)
register_contract(
    "admin_guide_media_update_route",
    RouteContract(
        request_model=AdminGuideMediaUpdateBody,
        response_schema=ok_envelope_for(GuideMediaImageOut, key="image"),
        models=(AdminGuideMediaUpdateBody, GuideMediaImageOut),
    ),
)
register_contract(
    "admin_guide_media_reorder_route",
    RouteContract(
        request_model=AdminGuideMediaReorderBody,
        response_schema=ok_envelope_for(GuideMediaGalleryOut, key="gallery"),
        models=(AdminGuideMediaReorderBody, GuideMediaGalleryOut, GuideMediaImageOut),
    ),
)
register_contract(
    "admin_guide_media_delete_route",
    RouteContract(response_schema=ok_envelope_for()),
)


def _image_out(row: GuideMediaImage) -> GuideMediaImageOut:
    digest = str(row.digest)
    return GuideMediaImageOut(
        image_id=str(row.image_id),
        digest=digest,
        config_id=str(row.config_id),
        platform=str(row.platform),
        client_id=str(row.client_id),
        captions=_captions(row.captions),
        order=int(row.sort_order or 0),
        content_type=str(row.content_type or "image/webp"),
        width=int(row.width) if row.width is not None else None,
        height=int(row.height) if row.height is not None else None,
        size_bytes=int(row.size_bytes or 0),
        created_at=row.created_at,
        updated_at=row.updated_at,
        src=storage.media_url(digest),
    )


def _captions(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(locale): str(text) for locale, text in value.items()}


def _gallery_out(
    key: service.GuideKey,
    rows: Iterable[GuideMediaImage],
    *,
    orphaned: bool,
) -> GuideMediaGalleryOut:
    gallery_rows = list(rows)
    images = [
        _image_out(row).model_copy(update={"order": order})
        for row, order in zip(gallery_rows, service.effective_orders(gallery_rows), strict=True)
    ]
    return GuideMediaGalleryOut(
        config_id=key[0],
        platform=key[1],
        client_id=key[2],
        orphaned=orphaned,
        images=images,
    )


def _validate_key(body: object) -> service.GuideKey:
    return service.normalize_key(
        config_id=str(getattr(body, "config_id", "")),
        platform=str(getattr(body, "platform", "")),
        client_id=str(getattr(body, "client_id", "")),
    )


def _panel_service(request: web.Request) -> object | None:
    from bot.app.web.webapp.guides_panel_config import _panel_service_from_app

    return _panel_service_from_app(request.app)


async def admin_guide_media_galleries_route(request: web.Request) -> web.Response:
    actor_id = _require_admin_user_id(request)
    language = await _admin_language(request, actor_id)
    settings = get_settings(request)
    panel_service = _panel_service(request)
    loaded = await service.load_admin_catalog(settings, panel_service, language=language)
    catalog_entries = [item.model_dump(mode="json") for item in loaded.entries]
    async with get_session_factory(request)() as session:
        rows = await service.list_images(session)
    grouped = service.group_rows(rows)
    orphaned_keys = (
        find_orphaned_gallery_keys({key: [] for key in grouped}, catalog_entries)
        if loaded.orphaned_known
        else []
    )
    orphaned_set = set(orphaned_keys)
    galleries = [
        _gallery_out(key, grouped[key], orphaned=key in orphaned_set) for key in sorted(grouped)
    ]
    orphaned = [
        GuideMediaGalleryKeyOut(config_id=key[0], platform=key[1], client_id=key[2])
        for key in orphaned_keys
    ]
    payload = AdminGuideMediaGalleriesOut(
        catalog=loaded.entries,
        catalog_available=loaded.available,
        catalog_error=loaded.error,
        orphaned_known=loaded.orphaned_known,
        galleries=galleries,
        orphaned=orphaned,
    )
    response = _ok(payload.model_dump(mode="json"))
    response.headers["Cache-Control"] = "private, no-store"
    return response


async def admin_guide_media_upload_route(request: web.Request) -> web.Response:
    actor_id = _require_admin_user_id(request)
    body, image = await parse_body_with_optional_image_or_400(request, AdminGuideMediaUploadBody)
    if image is None:
        return _error(400, "image_missing")
    try:
        key = _validate_key(body)
    except service.GuideMediaError as exc:
        return _error(exc.status, exc.code)
    settings = get_settings(request)
    language = await _admin_language(request, actor_id)
    panel_service = _panel_service(request)
    try:
        snapshot = await service.require_upload_target(
            settings, panel_service, language=language, key=key
        )
    except service.GuideMediaError as exc:
        return _error(exc.status, exc.code)

    async def validate_target() -> None:
        current = await service.require_upload_target(
            settings, panel_service, language=language, key=key
        )
        if current != snapshot:
            raise service.GuideMediaError("catalog_changed", 409)

    async with get_session_factory(request)() as session:
        try:
            row = await service.create_image(
                session,
                upload=image,
                config_id=key[0],
                platform=key[1],
                client_id=key[2],
                captions=body.captions,
                order=body.order,
                validate_target=validate_target,
            )
            await session.refresh(row)
        except service.GuideMediaError as exc:
            await session.rollback()
            return _error(exc.status, exc.code)
        except MessageImageError as exc:
            await session.rollback()
            return _error(400, exc.code)
        return _ok({"image": _image_out(row).model_dump(mode="json")})


async def admin_guide_media_update_route(request: web.Request) -> web.Response:
    _require_admin_user_id(request)
    body = await parse_body_or_400(request, AdminGuideMediaUpdateBody)
    image_id = str(request.match_info.get("image_id") or "")
    update_captions = "captions" in body.model_fields_set
    async with get_session_factory(request)() as session:
        try:
            row = await service.update_image(
                session,
                image_id=image_id,
                captions=body.captions,
                order=body.order,
                update_captions=update_captions,
            )
            await session.refresh(row)
        except service.GuideMediaError as exc:
            await session.rollback()
            return _error(exc.status, exc.code)
        return _ok({"image": _image_out(row).model_dump(mode="json")})


async def admin_guide_media_reorder_route(request: web.Request) -> web.Response:
    _require_admin_user_id(request)
    body = await parse_body_or_400(request, AdminGuideMediaReorderBody)
    try:
        key = _validate_key(body)
    except service.GuideMediaError as exc:
        return _error(exc.status, exc.code)
    async with get_session_factory(request)() as session:
        try:
            key, rows = await service.reorder_images(
                session,
                config_id=key[0],
                platform=key[1],
                client_id=key[2],
                image_ids=body.image_ids,
            )
            for row in rows:
                await session.refresh(row)
        except service.GuideMediaError as exc:
            await session.rollback()
            return _error(exc.status, exc.code)
        return _ok({"gallery": _gallery_out(key, rows, orphaned=False).model_dump(mode="json")})


async def admin_guide_media_delete_route(request: web.Request) -> web.Response:
    _require_admin_user_id(request)
    image_id = str(request.match_info.get("image_id") or "")
    async with get_session_factory(request)() as session:
        try:
            await service.delete_image(session, image_id=image_id)
        except service.GuideMediaError as exc:
            await session.rollback()
            return _error(exc.status, exc.code)
    return _ok({})


def _etag_matches(header: str, etag: str) -> bool:
    for token in header.split(","):
        candidate = token.strip()
        if candidate == "*" or candidate == etag or candidate == f"W/{etag}":
            return True
    return False


async def guide_media_public_media_route(request: web.Request) -> web.Response:
    digest = str(request.match_info.get("digest") or "").strip().lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        return _error(404, "not_found")
    async with get_session_factory(request)() as session:
        row = await service.find_by_digest(session, digest)
    if row is None:
        return _error(404, "not_found")
    try:
        body = await storage.read_image(digest, size_bytes=int(row.size_bytes or 0))
    except MessageImageError:
        return _error(404, "not_found")
    etag = f'"{digest}"'
    headers = {
        "Content-Type": storage.GUIDE_MEDIA_CONTENT_TYPE,
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": f'inline; filename="guide-{digest[:16]}.webp"',
        "Cache-Control": _MEDIA_CACHE_CONTROL,
        "ETag": etag,
        "Content-Length": str(len(body)),
    }
    if _etag_matches(request.headers.get("If-None-Match", ""), etag):
        return web.Response(status=304, headers=headers)
    return web.Response(body=body, headers=headers)


def setup_guide_media_routes(app: web.Application) -> None:
    app.router.add_get(
        "/api/plugins/guide_media/media/{digest:[0-9a-f]{64}}.webp",
        guide_media_public_media_route,
    )
    app.router.add_get("/api/admin/guide-media/galleries", admin_guide_media_galleries_route)
    app.router.add_post("/api/admin/guide-media/images", admin_guide_media_upload_route)
    app.router.add_post("/api/admin/guide-media/images/reorder", admin_guide_media_reorder_route)
    app.router.add_patch("/api/admin/guide-media/images/{image_id}", admin_guide_media_update_route)
    app.router.add_delete(
        "/api/admin/guide-media/images/{image_id}", admin_guide_media_delete_route
    )
