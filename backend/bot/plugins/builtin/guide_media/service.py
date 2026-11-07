"""CRUD, catalog projection and provider resolution for guide media."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from bot.plugins.extensions import (
    GuideMediaContext,
    GuideMediaGallery,
    GuideMediaImage,
    GuideMediaProvider,
)
from bot.services.guide_catalog import (
    build_guide_catalog_entries,
    normalize_client_key,
)
from bot.services.guide_catalog_lock import guide_catalog_lock
from bot.services.guide_media import MAX_SCREENSHOTS_PER_CLIENT
from bot.services.message_composition import resolve_localized_text
from bot.services.message_image_service import MessageImageError, UploadedMessageImage
from config.settings import Settings
from db.guide_media_models import GuideMediaImage as GuideMediaImageRow

from . import locks, storage
from .schemas import (
    GuideMediaCatalogAppOut,
    GuideMediaCatalogConfigOut,
    GuideMediaCatalogPlatformOut,
)

logger = logging.getLogger(__name__)

GuideKey = tuple[str, str, str]


class GuideMediaError(ValueError):
    def __init__(self, code: str, status: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class AdminCatalog:
    entries: list[GuideMediaCatalogConfigOut] = field(default_factory=list)
    available: bool = False
    error: str | None = None
    orphaned_known: bool = False


def normalize_key(*, config_id: str, platform: str, client_id: str) -> GuideKey:
    try:
        return normalize_client_key(config_id=config_id, platform=platform, client_id=client_id)
    except ValueError as exc:
        raise GuideMediaError("invalid_guide_key", 400) from exc


def _captions(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(locale): str(text)
        for locale, text in value.items()
        if isinstance(text, str) and text.strip()
    }


def _order_by() -> tuple[ColumnElement[Any], ...]:
    return (
        GuideMediaImageRow.sort_order,
        GuideMediaImageRow.created_at,
        GuideMediaImageRow.image_id,
    )


async def list_images(session: AsyncSession) -> list[GuideMediaImageRow]:
    rows = await session.scalars(select(GuideMediaImageRow).order_by(*_order_by()))
    return list(rows)


def group_rows(rows: Sequence[GuideMediaImageRow]) -> dict[GuideKey, list[GuideMediaImageRow]]:
    grouped: dict[GuideKey, list[GuideMediaImageRow]] = {}
    for row in rows:
        try:
            key = normalize_client_key(
                config_id=str(row.config_id),
                platform=str(row.platform),
                client_id=str(row.client_id),
            )
        except ValueError:
            continue
        grouped.setdefault(key, []).append(row)
    return grouped


def _key_clause(key: GuideKey) -> tuple[ColumnElement[bool], ...]:
    config_id, platform, client_id = key
    return (
        GuideMediaImageRow.config_id == config_id,
        GuideMediaImageRow.platform == platform,
        GuideMediaImageRow.client_id == client_id,
    )


async def _key_rows(session: AsyncSession, key: GuideKey) -> list[GuideMediaImageRow]:
    rows = await session.scalars(
        select(GuideMediaImageRow).where(*_key_clause(key)).order_by(*_order_by())
    )
    return list(rows)


async def _references_digest(session: AsyncSession, digest: str) -> bool:
    references = await session.scalar(
        select(func.count())
        .select_from(GuideMediaImageRow)
        .where(GuideMediaImageRow.digest == digest)
    )
    return bool(references)


async def cleanup_unreferenced_digest(session: AsyncSession, digest: str) -> bool:
    """Remove a file only while no row references its digest, under a lock."""

    async with locks.advisory_xact_lock(session, locks.digest_lock_key(digest)):
        referenced = await _references_digest(session, digest)
        if referenced:
            await session.commit()
            return False
        try:
            await storage.delete_image_file(digest)
        except OSError:
            await session.rollback()
            raise
        await session.commit()
        return True


async def create_image(
    session: AsyncSession,
    *,
    upload: UploadedMessageImage,
    config_id: str,
    platform: str,
    client_id: str,
    captions: Mapping[str, str] | None,
    order: int | None,
    validate_target: Callable[[], Awaitable[None]] | None = None,
) -> GuideMediaImageRow:
    key = normalize_key(config_id=config_id, platform=platform, client_id=client_id)
    normalized_captions = _normalize_captions(captions)
    normalized_order = _normalize_order(order)
    try:
        prepared = await storage.prepare_image(upload)
    except MessageImageError as exc:
        status = 413 if exc.code == "image_too_large" else 400
        raise GuideMediaError(exc.code, status) from exc

    if validate_target is not None:
        await validate_target()

    stored = False
    try:
        async with (
            locks.advisory_xact_lock(session, locks.gallery_lock_key(*key)),
            locks.advisory_xact_lock(session, locks.digest_lock_key(prepared.digest)),
            guide_catalog_lock(),
        ):
            if validate_target is not None:
                await validate_target()
            stored = True
            await storage.store_image(prepared)
            current = await _key_rows(session, key)
            if len(current) >= MAX_SCREENSHOTS_PER_CLIENT:
                raise GuideMediaError("too_many_screenshots", 409)
            if normalized_order is not None and any(
                int(row.sort_order or 0) == normalized_order for row in current
            ):
                raise GuideMediaError("order_conflict", 409)
            sort_order = (
                normalized_order
                if normalized_order is not None
                else max((int(row.sort_order or 0) for row in current), default=-1) + 1
            )
            if not -10000 <= sort_order <= 10000:
                # The gallery lock protects compaction and append as one change.
                for index, existing in enumerate(current):
                    existing.sort_order = index
                sort_order = len(current)
            _normalize_order(sort_order)
            row = GuideMediaImageRow(
                image_id=uuid4().hex,
                digest=prepared.digest,
                config_id=key[0],
                platform=key[1],
                client_id=key[2],
                captions=normalized_captions,
                sort_order=sort_order,
                content_type=prepared.content_type,
                width=prepared.width,
                height=prepared.height,
                size_bytes=prepared.size_bytes,
            )
            session.add(row)
            await session.flush()
            # Final external catalog snapshot is immediately before commit.
            # Local settings mutations share guide_catalog_lock through commit.
            if validate_target is not None:
                await validate_target()
            await session.commit()
            return row
    except Exception:
        await session.rollback()
        if stored:
            try:
                await cleanup_unreferenced_digest(session, prepared.digest)
            except Exception:
                logger.exception(
                    "Failed to clean up guide media after a failed upload for %s",
                    prepared.digest,
                )
        raise


def _normalize_captions(value: Mapping[str, str] | None) -> dict[str, str]:
    from bot.services.guide_media import normalize_guide_media_captions

    try:
        return normalize_guide_media_captions(value)
    except ValueError as exc:
        raise GuideMediaError("invalid_captions", 400) from exc


def _normalize_order(value: int | None) -> int | None:
    if value is None:
        return None
    if not -10000 <= int(value) <= 10000:
        raise GuideMediaError("invalid_order", 400)
    return int(value)


async def _existing_image(session: AsyncSession, image_id: str) -> GuideMediaImageRow:
    # SELECT with populate_existing bypasses the identity-map copy fetched
    # before waiting for the gallery lock. A concurrent delete is a normal 404.
    row = await session.scalar(
        select(GuideMediaImageRow)
        .where(GuideMediaImageRow.image_id == str(image_id or "").strip().lower())
        .execution_options(populate_existing=True)
    )
    if row is None:
        await session.rollback()
        raise GuideMediaError("unknown_image", 404)
    return row


def effective_orders(rows: Sequence[GuideMediaImageRow]) -> list[int]:
    orders = [int(row.sort_order or 0) for row in rows]
    # Preserve existing positions unless legacy ties need a unique projection.
    return list(range(len(rows))) if len(set(orders)) != len(orders) else orders


async def update_image(
    session: AsyncSession,
    *,
    image_id: str,
    captions: Mapping[str, str] | None,
    order: int | None,
    update_captions: bool,
) -> GuideMediaImageRow:
    row = await session.get(GuideMediaImageRow, str(image_id or "").strip().lower())
    if row is None:
        raise GuideMediaError("unknown_image", 404)
    normalized_order = _normalize_order(order)
    key = (str(row.config_id), str(row.platform), str(row.client_id))
    try:
        async with locks.advisory_xact_lock(session, locks.gallery_lock_key(*key)):
            row = await _existing_image(session, image_id)
            conflict = await session.scalar(
                select(func.count())
                .select_from(GuideMediaImageRow)
                .where(
                    *_key_clause(key),
                    GuideMediaImageRow.sort_order == normalized_order,
                    GuideMediaImageRow.image_id != str(row.image_id),
                )
            )
            if normalized_order is not None and conflict:
                await session.rollback()
                raise GuideMediaError("order_conflict", 409)
            if update_captions:
                row.captions = _normalize_captions(captions)
            if normalized_order is not None:
                row.sort_order = normalized_order
            await session.flush()
            await session.commit()
            return row
    except GuideMediaError:
        raise
    except Exception:
        await session.rollback()
        raise


async def reorder_images(
    session: AsyncSession,
    *,
    config_id: str,
    platform: str,
    client_id: str,
    image_ids: Sequence[str],
) -> tuple[GuideKey, list[GuideMediaImageRow]]:
    key = normalize_key(config_id=config_id, platform=platform, client_id=client_id)
    async with locks.advisory_xact_lock(session, locks.gallery_lock_key(*key)):
        rows = await _key_rows(session, key)
        current = [str(row.image_id) for row in rows]
        requested = [str(image_id).strip().lower() for image_id in image_ids]
        if len(requested) != len(current) or set(requested) != set(current):
            raise GuideMediaError("gallery_mismatch", 409)
        by_id = {str(row.image_id): row for row in rows}
        ordered = [by_id[image_id] for image_id in requested]
        for index, row in enumerate(ordered):
            row.sort_order = index
        try:
            await session.flush()
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        return key, ordered


async def delete_image(
    session: AsyncSession,
    *,
    image_id: str,
) -> tuple[GuideMediaImageRow, bool]:
    row = await session.get(GuideMediaImageRow, str(image_id or "").strip().lower())
    if row is None:
        raise GuideMediaError("unknown_image", 404)
    key = (str(row.config_id), str(row.platform), str(row.client_id))
    async with locks.advisory_xact_lock(session, locks.gallery_lock_key(*key)):
        row = await _existing_image(session, image_id)
        digest = str(row.digest)
        await session.delete(row)
        try:
            await session.flush()
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    try:
        removed = await cleanup_unreferenced_digest(session, digest)
    except Exception:
        logger.exception("Failed to remove unreferenced guide media %s", digest)
        removed = False
    return row, removed


async def find_by_digest(session: AsyncSession, digest: str) -> GuideMediaImageRow | None:
    return cast(
        GuideMediaImageRow | None,
        await session.scalar(
            select(GuideMediaImageRow).where(GuideMediaImageRow.digest == digest).limit(1)
        ),
    )


async def resolve_galleries(
    session_factory: sessionmaker | None,
) -> tuple[GuideMediaGallery, ...]:
    if session_factory is None:
        return ()
    async with session_factory() as session:
        rows = await list_images(session)
    galleries: list[GuideMediaGallery] = []
    grouped = group_rows(rows)
    for key in sorted(grouped):
        images: list[GuideMediaImage] = []
        gallery_rows = grouped[key][:MAX_SCREENSHOTS_PER_CLIENT]
        for row, order in zip(gallery_rows, effective_orders(gallery_rows), strict=True):
            digest = str(row.digest)
            images.append(
                GuideMediaImage(
                    src=storage.media_url(digest),
                    public_src=storage.media_url(digest),
                    captions=_captions(row.captions),
                    order=order,
                )
            )
        galleries.append(
            GuideMediaGallery(
                config_id=key[0],
                platform=key[1],
                client_id=key[2],
                images=tuple(images),
            )
        )
    return tuple(galleries)


def guide_media_provider(session_factory: sessionmaker | None) -> GuideMediaProvider:
    async def _resolve(_context: GuideMediaContext) -> tuple[GuideMediaGallery, ...]:
        return await resolve_galleries(session_factory)

    return GuideMediaProvider(id="guide_media", resolve=_resolve)


def _catalog_label(value: object, *, language: str, fallback: str) -> str:
    if isinstance(value, str):
        return value.strip() or fallback
    if isinstance(value, Mapping):
        localized = {str(key): str(text) for key, text in value.items() if isinstance(text, str)}
        resolved = resolve_localized_text(localized, language=language, default_language="en")
        return resolved.strip() or fallback
    return fallback


def _project_admin_catalog(
    entries: Sequence[Mapping[str, object]],
    language: str,
) -> list[GuideMediaCatalogConfigOut]:
    catalog: list[GuideMediaCatalogConfigOut] = []
    for entry in entries:
        platforms: list[GuideMediaCatalogPlatformOut] = []
        raw_platforms = entry.get("platforms")
        if not isinstance(raw_platforms, (list, tuple)):
            raw_platforms = []
        for platform in raw_platforms:
            if not isinstance(platform, Mapping):
                continue
            platform_id = str(platform.get("id") or "").strip()
            if not platform_id:
                continue
            apps = platform.get("apps")
            projected_apps: list[GuideMediaCatalogAppOut] = []
            if isinstance(apps, (list, tuple)):
                for app in apps:
                    if not isinstance(app, Mapping):
                        continue
                    app_id = str(app.get("id") or "").strip()
                    if not app_id:
                        continue
                    projected_apps.append(
                        GuideMediaCatalogAppOut(
                            id=app_id,
                            name=str(app.get("name") or app_id),
                            explicit_id=bool(app.get("explicit_id")),
                            ambiguous=bool(app.get("ambiguous")),
                        )
                    )
            platforms.append(
                GuideMediaCatalogPlatformOut(
                    id=platform_id,
                    label=_catalog_label(
                        platform.get("display_name"),
                        language=language,
                        fallback=platform_id,
                    ),
                    apps=projected_apps,
                )
            )
        error_value = entry.get("error")
        catalog.append(
            GuideMediaCatalogConfigOut(
                config_id=str(entry.get("config_id") or ""),
                source=str(entry.get("source") or "unknown"),
                platforms=platforms,
                error=str(error_value) if error_value else None,
                missing=bool(entry.get("missing")),
            )
        )
    return catalog


async def load_admin_catalog(
    settings: Settings,
    panel_service: object | None,
    *,
    language: str,
) -> AdminCatalog:
    try:
        raw_entries = await build_guide_catalog_entries(settings, panel_service)
    except Exception as exc:
        logger.warning("Guide media catalog is unavailable", exc_info=True)
        return AdminCatalog(error=str(exc) or "catalog_unavailable")
    entries = _project_admin_catalog(raw_entries, language)
    successful = [entry for entry in entries if not entry.missing and entry.error is None]
    failed = [entry for entry in entries if entry.missing or entry.error]
    available = bool(successful)
    complete = available and not failed
    error: str | None = None
    if failed:
        error = next(
            (str(entry.error) for entry in failed if entry.error),
            "catalog_unavailable",
        )
    if not entries:
        error = "catalog_unavailable"
    return AdminCatalog(
        entries=entries,
        available=available,
        error=error,
        orphaned_known=complete,
    )


async def build_admin_catalog(
    settings: Settings,
    panel_service: object | None,
    *,
    language: str,
) -> list[GuideMediaCatalogConfigOut]:
    """Backward-compatible list projection; use load_admin_catalog for status."""

    loaded = await load_admin_catalog(settings, panel_service, language=language)
    return loaded.entries


async def require_upload_target(
    settings: Settings,
    panel_service: object | None,
    *,
    language: str,
    key: GuideKey,
) -> str:
    catalog = await load_admin_catalog(settings, panel_service, language=language)
    if not catalog.available:
        raise GuideMediaError("catalog_unavailable", 503)
    matches: list[GuideMediaCatalogAppOut] = []
    config_id, platform_id, client_id = key
    for config in catalog.entries:
        requested_source_failed = config.config_id == config_id or (
            config_id.startswith("panel:") and config.config_id == "panel:unavailable"
        )
        if requested_source_failed and (config.missing or config.error):
            raise GuideMediaError("catalog_unavailable", 503)
        if config.config_id != config_id:
            continue
        for platform in config.platforms:
            if platform.id != platform_id:
                continue
            matches.extend(app for app in platform.apps if app.id == client_id)
    if not matches:
        raise GuideMediaError("unknown_guide_target", 400)
    if len(matches) != 1 or matches[0].ambiguous:
        raise GuideMediaError("ambiguous_client", 400)
    # A source-scoped fingerprint defines the external Panel snapshot boundary.
    # Changes after the final successful read belong to the next snapshot.
    import hashlib

    snapshot = next(config for config in catalog.entries if config.config_id == config_id)
    return hashlib.sha256(snapshot.model_dump_json().encode()).hexdigest()


async def is_ambiguous_client(
    settings: Settings,
    panel_service: object | None,
    *,
    language: str,
    key: GuideKey,
) -> bool:
    try:
        await require_upload_target(settings, panel_service, language=language, key=key)
    except GuideMediaError as exc:
        return exc.code == "ambiguous_client"
    return False
