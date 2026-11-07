"""Resolve plugin screenshot galleries without leaking authenticated runtime."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from bot.services.guide_catalog import normalize_client_key
from bot.services.guide_media import normalize_gallery_images

from .contracts import GuideMediaContext
from .registry import get_registry

logger = logging.getLogger(__name__)


def _gallery_images_for_context(
    raw_images: Any,
    *,
    public: bool,
) -> list[dict[str, Any]] | None:
    try:
        return normalize_gallery_images(raw_images, public=public)
    except ValueError:
        return None


async def resolve_guide_media_galleries(
    context: GuideMediaContext,
) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    """Call every guide_media provider; failures close access to that gallery."""

    from bot.plugins.packages import generation_is_current

    if not generation_is_current():
        return {}
    merged: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for owner, entry in get_registry().owners().items():
        if not entry.contributions.guide_media:
            continue
        for provider in entry.contributions.guide_media:
            try:
                async with asyncio.timeout(5):
                    galleries = await provider.resolve(context)
            except Exception:
                logger.warning("Extension guide media %s:%s is unavailable", owner, provider.id)
                continue
            if not isinstance(galleries, (list, tuple)):
                continue
            for gallery in galleries:
                try:
                    key = normalize_client_key(
                        config_id=str(getattr(gallery, "config_id", "") or ""),
                        platform=str(getattr(gallery, "platform", "") or ""),
                        client_id=str(getattr(gallery, "client_id", "") or ""),
                    )
                except ValueError:
                    continue
                raw_images = getattr(gallery, "images", ())
                if isinstance(raw_images, (list, tuple)):
                    converted = []
                    for image in raw_images:
                        src = getattr(image, "src", "")
                        public_src = getattr(image, "public_src", None)
                        chosen = public_src if context.is_public and public_src else src
                        converted.append(
                            {
                                "src": chosen,
                                "captions": getattr(image, "captions", {}),
                                "order": getattr(image, "order", 0),
                            }
                        )
                else:
                    converted = raw_images
                normalized = _gallery_images_for_context(converted, public=context.is_public)
                if normalized is None:
                    continue
                merged[key] = normalized
    return merged
