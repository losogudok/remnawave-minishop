"""Versioned install document and Remnawave v1 ingress adapter."""

import copy
import hashlib
import json
import re
from typing import Any

from aiohttp import web

from config.install_targets import legacy_install_app_id

from .guides_panel_config import _subscription_guides_status_for_request

_PLATFORM_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_EXACT_RESOURCE = re.compile(
    r"\{\{\s*(SUBSCRIPTION_LINK|HAPP_CRYPT3_LINK|HAPP_CRYPT4_LINK|INCY_CRYPT1_LINK)\s*\}\}\Z"
)
_RESOURCE_REPRESENTATIONS = {
    "SUBSCRIPTION_LINK": "http",
    "HAPP_CRYPT3_LINK": "happ-crypt3",
    "HAPP_CRYPT4_LINK": "happ",
    "INCY_CRYPT1_LINK": "incy-crypt1",
}


def remnawave_v1_to_guide_document(
    config: dict[str, Any], *, source: str | None = None, config_id: str | None = None
) -> dict[str, Any]:
    if config.get("version") != "1":
        raise ValueError("Unsupported Remnawave guide version")
    platforms = config.get("platforms")
    if not isinstance(platforms, dict):
        raise ValueError("Invalid Remnawave platforms")
    document = copy.deepcopy({key: value for key, value in config.items() if key != "platforms"})
    document.pop("version", None)
    converted: list[dict[str, Any]] = []
    for platform_id, raw_platform in platforms.items():
        if not isinstance(platform_id, str) or not _PLATFORM_ID.fullmatch(platform_id):
            raise ValueError("Invalid platform ID")
        if not isinstance(raw_platform, dict):
            raise ValueError("Invalid platform")
        platform = {"id": platform_id, **copy.deepcopy(raw_platform)}
        for app in platform.get("apps", []):
            app["id"] = str(app.get("id") or "").strip() or legacy_install_app_id(
                str(app.get("name") or "")
            )
            screenshots = app.get("screenshots")
            if isinstance(screenshots, list) and screenshots:
                try:
                    from bot.services.guide_media import normalize_gallery_images

                    app["screenshots"] = normalize_gallery_images(screenshots, public=False)
                except ValueError:
                    app.pop("screenshots", None)
            for block in app.get("blocks", []):
                for button in block.get("buttons", []):
                    link = str(button.get("link") or "")
                    resource = _EXACT_RESOURCE.fullmatch(link)
                    target: dict[str, str]
                    if resource:
                        target = {
                            "kind": "resource",
                            "resourceId": "primary-subscription",
                            "representation": _RESOURCE_REPRESENTATIONS[resource.group(1)],
                        }
                    else:
                        target = {"kind": "literal", "value": link}
                    button["action"] = {
                        "kind": "copy" if button.get("type") == "copyButton" else "open",
                        "target": target,
                    }
        converted.append(platform)
    document["schemaVersion"] = 1
    document["source"] = source
    if config_id is not None:
        document["config_id"] = config_id
    document["revision"] = hashlib.sha256(
        json.dumps(config, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    document["platforms"] = converted
    document["resources"] = [{"id": "primary-subscription", "kind": "subscription"}]
    return document


async def _attach_guide_media(
    document: dict[str, Any],
    *,
    config_id: str | None,
    user_id: int | None,
    language: str,
    share_token: str | None,
    panel_short_uuid: str | None,
    panel_user_uuid: str | None,
) -> dict[str, Any]:
    from bot.plugins.extensions.guide_media import resolve_guide_media_galleries
    from bot.plugins.extensions.registry import get_registry
    from bot.services.guide_media import (
        attach_guide_media_to_document,
        sanitize_document_screenshots_for_public,
    )

    is_public = user_id is None

    def _public_safe(doc: dict[str, Any]) -> dict[str, Any]:
        if not is_public:
            return doc
        try:
            return sanitize_document_screenshots_for_public(doc)
        except Exception:
            return doc

    if not any(entry.contributions.guide_media for entry in get_registry().owners().values()):
        return _public_safe(document)
    if not config_id:
        return _public_safe(document)
    from bot.plugins.extensions import GuideMediaContext

    context = GuideMediaContext(
        user_id=user_id,
        language=language or "en",
        is_public=is_public,
        share_token=share_token,
        panel_short_uuid=panel_short_uuid,
        panel_user_uuid=panel_user_uuid,
    )
    try:
        galleries = await resolve_guide_media_galleries(context)
    except Exception:
        return _public_safe(document)
    if not galleries:
        return _public_safe(document)
    try:
        return attach_guide_media_to_document(
            document, galleries, config_id=config_id, public=is_public
        )
    except ValueError:
        return _public_safe(document)


async def load_guide_content(
    request: web.Request,
    *,
    user_id: int | None = None,
    panel_short_uuid: str | None = None,
    panel_user_uuid: str | None = None,
    share_token: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Keep the established panel/JSON/file/default priority at the source boundary."""
    status = await _subscription_guides_status_for_request(
        request,
        user_id=user_id,
        panel_short_uuid=panel_short_uuid,
        panel_user_uuid=panel_user_uuid,
    )
    config = status.get("config")
    language = "en"
    if user_id is not None:
        from bot.app.web.context import get_session_factory, get_settings
        from bot.plugins.extensions import UserContext
        from bot.plugins.extensions.guides import merge_guides
        from bot.plugins.extensions.registry import get_registry
        from db.dal import user_dal

        # Resolve the user language for guide fragments and media galleries
        # alike, so media-only providers still see the user locale. The locale
        # lookup is skipped only when no plugin contributes either surface.
        owners = get_registry().owners().values()
        needs_locale = any(
            entry.contributions.guides or entry.contributions.guide_media for entry in owners
        )
        if needs_locale:
            async with get_session_factory(request)() as session:
                user = await user_dal.get_user_by_id(session, user_id)
                if user is not None and not getattr(user, "is_banned", False):
                    language = str(getattr(user, "language_code", None) or "en")
                    if get_settings(request).SUBSCRIPTION_GUIDES_ENABLED and any(
                        entry.contributions.guides for entry in get_registry().owners().values()
                    ):
                        merged = await merge_guides(
                            UserContext(session, user_id, language),
                            config if isinstance(config, dict) and status.get("enabled") else None,
                        )
                        if merged is not None:
                            config = merged
                            status = {**status, "enabled": True, "config": merged}
    if not status.get("enabled") or not isinstance(config, dict):
        return status, None
    raw_config_id = status.get("config_id")
    source_name = str(status.get("source") or "").strip()
    if raw_config_id is None and source_name == "panel":
        # Embedded Panel configs have no canonical panel:<uuid> identity
        # shared with the catalog. Preserve None so provider galleries never
        # attach across distinct embedded configs via a shared fallback.
        canonical_config_id: str | None = None
    elif not isinstance(raw_config_id, str) or not raw_config_id.strip():
        if source_name == "admin_json":
            canonical_config_id = "local:admin_json"
        elif source_name in {"file", ""}:
            canonical_config_id = "local:file"
        elif source_name == "panel":
            canonical_config_id = None
        else:
            canonical_config_id = f"{source_name}:default" if source_name else "local:file"
    else:
        canonical_config_id = raw_config_id.strip()
    document = remnawave_v1_to_guide_document(
        config,
        source=str(status.get("source") or ""),
        config_id=canonical_config_id,
    )
    document = await _attach_guide_media(
        document,
        config_id=canonical_config_id,
        user_id=user_id,
        language=language,
        share_token=share_token,
        panel_short_uuid=panel_short_uuid,
        panel_user_uuid=panel_user_uuid,
    )
    return status, document
