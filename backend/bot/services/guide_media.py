"""Core contract for plugin-supplied per-client screenshot galleries."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from config.subscription_guides_config import ALLOWED_LOCALES

from .guide_catalog import (
    is_valid_client_id,
    is_valid_config_id,
    is_valid_platform_id,
    normalize_client_key,
    stable_guide_client_id,
)

MAX_SCREENSHOTS_PER_CLIENT = 8
MAX_CAPTION_CHARS = 300
MAX_SRC_CHARS = 2048
AUTH_RUNTIME_PREFIXES = (
    "/api/extensions/assets/",
    "/api/extensions/resource",
)

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_UNSAFE_SRC_RE = re.compile(r"^(javascript|vbscript):", re.IGNORECASE)


def _guide_media_path(text: str) -> str:
    from urllib.parse import urlsplit

    try:
        return urlsplit(text).path or ""
    except ValueError:
        return text


def is_public_guide_media_src(src: str) -> bool:
    text = str(src or "").strip()
    if not text or len(text) > MAX_SRC_CHARS or _CONTROL_CHARS_RE.search(text):
        return False
    lowered = text.lower()
    if lowered.startswith(("javascript:", "vbscript:")):
        return False
    path = _guide_media_path(text)
    if path.startswith(AUTH_RUNTIME_PREFIXES):
        return False
    if path.startswith("/api/guide-media/public/"):
        return True
    if text.startswith("/") and not text.startswith("//"):
        return True
    if lowered.startswith(("http://", "https://")):
        return True
    return bool(lowered.startswith("data:image/"))


def normalize_guide_media_src(src: Any, *, public: bool) -> str:
    text = str(src or "").strip()
    if not text or len(text) > MAX_SRC_CHARS:
        raise ValueError("Invalid screenshot source")
    if _CONTROL_CHARS_RE.search(text) or _UNSAFE_SRC_RE.match(text.strip()):
        raise ValueError("Unsafe screenshot source")
    lowered = text.lower()
    if lowered.startswith("data:") and not lowered.startswith("data:image/"):
        raise ValueError("Unsafe screenshot source")
    if public and not is_public_guide_media_src(text):
        raise ValueError("Authenticated-only screenshot source in public guide")
    if text.startswith("/") or lowered.startswith(("http://", "https://", "data:image/")):
        return text
    raise ValueError("Unsupported screenshot source")


def normalize_guide_media_captions(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("Invalid screenshot captions")
    result: dict[str, str] = {}
    for locale, text in value.items():
        key = str(locale or "").strip().lower()
        if not key or key not in ALLOWED_LOCALES:
            continue
        caption = str(text or "").strip()
        if not caption:
            continue
        if len(caption) > MAX_CAPTION_CHARS:
            raise ValueError("Screenshot caption is too long")
        result[key] = caption
    return result


def normalize_guide_screenshot(
    raw: Any,
    *,
    public: bool,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("Invalid screenshot")
    order_raw = raw.get("order", 0)
    if isinstance(order_raw, bool):
        raise ValueError("Invalid screenshot order")
    if isinstance(order_raw, int):
        order = order_raw
    elif isinstance(order_raw, float):
        order = int(order_raw)
    elif isinstance(order_raw, str):
        stripped = order_raw.strip()
        if not stripped:
            raise ValueError("Invalid screenshot order")
        try:
            order = int(stripped, 10)
        except ValueError:
            raise ValueError("Invalid screenshot order") from None
    else:
        raise ValueError("Invalid screenshot order")
    if not -10000 <= order <= 10000:
        raise ValueError("Screenshot order out of range")
    src = normalize_guide_media_src(raw.get("src"), public=public)
    captions = normalize_guide_media_captions(raw.get("captions", raw.get("caption")))
    return {"src": src, "captions": captions, "order": order}


def normalize_gallery_images(
    images: Any,
    *,
    public: bool,
) -> list[dict[str, Any]]:
    if not isinstance(images, (list, tuple)):
        raise ValueError("Invalid screenshot gallery")
    if len(images) > MAX_SCREENSHOTS_PER_CLIENT:
        raise ValueError("Too many screenshots for client")
    normalized = [normalize_guide_screenshot(item, public=public) for item in images]
    normalized.sort(key=lambda item: (int(item["order"]), str(item["src"])))
    return normalized


def attach_guide_media_to_document(
    document: dict[str, Any],
    galleries: Mapping[tuple[str, str, str], list[dict[str, Any]]],
    *,
    config_id: str,
    public: bool,
) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ValueError("Invalid guide document")
    if not is_valid_config_id(config_id):
        raise ValueError("Invalid guide config identity")
    platforms = document.get("platforms")
    if not isinstance(platforms, list):
        return document
    for platform in platforms:
        if not isinstance(platform, dict):
            continue
        platform_id = str(platform.get("id") or "").strip()
        if not is_valid_platform_id(platform_id):
            continue
        apps = platform.get("apps")
        if not isinstance(apps, list):
            continue
        counts: dict[str, int] = {}
        for candidate in apps:
            if not isinstance(candidate, dict):
                continue
            candidate_id = stable_guide_client_id(candidate)
            if is_valid_client_id(candidate_id):
                counts[candidate_id] = counts.get(candidate_id, 0) + 1
        for app in apps:
            if not isinstance(app, dict):
                continue
            client_id = stable_guide_client_id(app)
            if not is_valid_client_id(client_id):
                continue
            if counts.get(client_id, 0) > 1:
                app.pop("screenshots", None)
                continue
            try:
                key = normalize_client_key(
                    config_id=config_id, platform=platform_id, client_id=client_id
                )
            except ValueError:
                continue
            images = galleries.get(key)
            if not images:
                existing = app.get("screenshots")
                if isinstance(existing, list) and existing:
                    try:
                        app["screenshots"] = normalize_gallery_images(existing, public=public)
                    except ValueError:
                        app.pop("screenshots", None)
                else:
                    app.pop("screenshots", None)
                continue
            try:
                app["screenshots"] = normalize_gallery_images(images, public=public)
            except ValueError:
                app.pop("screenshots", None)
                continue
    return document


def collect_gallery_keys(
    galleries: Mapping[tuple[str, str, str], Any],
) -> set[tuple[str, str, str]]:
    return set(galleries.keys())


def find_orphaned_gallery_keys(
    galleries: Mapping[tuple[str, str, str], Any],
    catalog_entries: list[Mapping[str, Any]],
) -> list[tuple[str, str, str]]:
    known: set[tuple[str, str, str]] = set()
    for entry in catalog_entries:
        if not isinstance(entry, Mapping):
            continue
        config_id = str(entry.get("config_id") or "").strip()
        for platform in entry.get("platforms") or []:
            if not isinstance(platform, Mapping):
                continue
            platform_id = str(platform.get("id") or "").strip()
            for app in platform.get("apps") or []:
                if not isinstance(app, Mapping):
                    continue
                client_id = str(app.get("id") or "").strip()
                if config_id and platform_id and client_id:
                    known.add((config_id, platform_id, client_id))
    return sorted(key for key in galleries if key not in known)


def sanitize_document_screenshots_for_public(document: dict[str, Any]) -> dict[str, Any]:
    """Drop authenticated-only inline screenshots from a converted document.

    Used for public payloads independently of provider availability so a
    valid public request without galleries, with empty results, with a
    failing provider, or with a stale generation never leaks auth runtime
    refs (auth is still enforced at the asset route).
    """

    platforms = document.get("platforms")
    if not isinstance(platforms, list):
        return document
    for platform in platforms:
        if not isinstance(platform, dict):
            continue
        apps = platform.get("apps")
        if not isinstance(apps, list):
            continue
        for app in apps:
            if not isinstance(app, dict):
                continue
            existing = app.get("screenshots")
            if isinstance(existing, list) and existing:
                try:
                    app["screenshots"] = normalize_gallery_images(existing, public=True)
                except ValueError:
                    app.pop("screenshots", None)
    return document


def sanitize_config_screenshots_for_public(config: Any) -> Any:
    """Return a copy of a raw v1 config with auth-only screenshots removed.

    The raw config representation (platforms as a dict) is a second public
    payload representation alongside the converted document; both must be
    sanitized for public responses.
    """

    if not isinstance(config, dict):
        return config
    import copy as _copy

    sanitized = _copy.deepcopy(config)
    platforms = sanitized.get("platforms")
    if not isinstance(platforms, dict):
        return sanitized
    for raw_platform in platforms.values():
        if not isinstance(raw_platform, dict):
            continue
        apps = raw_platform.get("apps")
        if not isinstance(apps, list):
            continue
        for app in apps:
            if not isinstance(app, dict):
                continue
            existing = app.get("screenshots")
            if isinstance(existing, list) and existing:
                try:
                    app["screenshots"] = normalize_gallery_images(existing, public=True)
                except ValueError:
                    app.pop("screenshots", None)
    return sanitized
