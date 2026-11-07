"""Source-aware catalog of configured guide variants for admin and media keys."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from config.install_targets import legacy_install_app_id

_PLATFORM_ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_APP_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_CONFIG_ID_RE = re.compile(r"[A-Za-z0-9:_-]{1,128}\Z")

LOCAL_ADMIN_JSON_CONFIG_ID = "local:admin_json"
LOCAL_FILE_CONFIG_ID = "local:file"


def is_valid_platform_id(value: str) -> bool:
    return bool(_PLATFORM_ID_RE.fullmatch(value))


def is_valid_client_id(value: str) -> bool:
    return bool(_APP_ID_RE.fullmatch(value))


def is_valid_config_id(value: str) -> bool:
    return bool(_CONFIG_ID_RE.fullmatch(str(value or "").strip()))


def stable_guide_client_id(app: Mapping[str, Any]) -> str:
    explicit = str(app.get("id") or "").strip()
    if explicit:
        return explicit
    return legacy_install_app_id(str(app.get("name") or ""))


def is_explicit_guide_client_id(app: Mapping[str, Any]) -> bool:
    return bool(str(app.get("id") or "").strip())


def normalize_config_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text or not is_valid_config_id(text):
        raise ValueError(f"Invalid guide config identity: {text!r}")
    return text


def normalize_client_key(*, config_id: str, platform: str, client_id: str) -> tuple[str, str, str]:
    normalized_config = normalize_config_id(config_id)
    normalized_platform = str(platform or "").strip()
    normalized_client = str(client_id or "").strip()
    if not is_valid_platform_id(normalized_platform):
        raise ValueError(f"Invalid guide platform: {platform!r}")
    if not is_valid_client_id(normalized_client):
        raise ValueError(f"Invalid guide client identity: {client_id!r}")
    return (normalized_config, normalized_platform, normalized_client)


def _ambiguous_client_ids(apps: list[Mapping[str, Any]]) -> set[str]:
    counts: dict[str, int] = {}
    legacy_seen: set[str] = set()
    for app in apps:
        if not isinstance(app, Mapping):
            continue
        stable = stable_guide_client_id(app)
        counts[stable] = counts.get(stable, 0) + 1
        if not is_explicit_guide_client_id(app):
            legacy_seen.add(stable)
    return {
        stable
        for stable, total in counts.items()
        if total > 1 and (stable in legacy_seen or _has_duplicate_explicit(apps, stable))
    }


def _has_duplicate_explicit(apps: list[Mapping[str, Any]], stable: str) -> bool:
    explicit = 0
    for app in apps:
        if not isinstance(app, Mapping):
            continue
        if stable_guide_client_id(app) == stable and is_explicit_guide_client_id(app):
            explicit += 1
    return explicit > 1


def project_guide_catalog_config(
    config: Mapping[str, Any] | None,
    *,
    config_id: str,
    source: str,
    error: str | None = None,
    missing: bool = False,
) -> dict[str, Any]:
    normalized_config_id = normalize_config_id(config_id)
    normalized_source = str(source or "").strip() or "unknown"
    platforms: list[dict[str, Any]] = []
    if isinstance(config, Mapping):
        raw_platforms = config.get("platforms")
        items: list[tuple[str, Any]] = (
            list(raw_platforms.items()) if isinstance(raw_platforms, Mapping) else []
        )
        for platform_id, raw_platform in items:
            if not isinstance(platform_id, str) or not is_valid_platform_id(platform_id):
                continue
            if not isinstance(raw_platform, Mapping):
                continue
            raw_apps = raw_platform.get("apps")
            app_list = raw_apps if isinstance(raw_apps, list) else []
            ambiguous = _ambiguous_client_ids([app for app in app_list if isinstance(app, Mapping)])
            projected_apps: list[dict[str, Any]] = []
            for app in app_list:
                if not isinstance(app, Mapping):
                    continue
                stable = stable_guide_client_id(app)
                if not is_valid_client_id(stable):
                    continue
                projected_apps.append(
                    {
                        "id": stable,
                        "name": str(app.get("name") or stable),
                        "explicit_id": is_explicit_guide_client_id(app),
                        "ambiguous": stable in ambiguous,
                    }
                )
            display = raw_platform.get("displayName")
            platforms.append(
                {
                    "id": platform_id,
                    "display_name": display if isinstance(display, (str, dict)) else platform_id,
                    "apps": projected_apps,
                }
            )
    return {
        "config_id": normalized_config_id,
        "source": normalized_source,
        "platforms": platforms,
        "error": error,
        "missing": bool(missing),
    }


def project_install_platforms(
    document: Mapping[str, Any] | None,
    *,
    resolve_label: Any | None = None,
    language: str = "en",
) -> list[dict[str, Any]]:
    """Shared projection behind /api/admin/message/targets install_platforms."""

    if not isinstance(document, Mapping):
        return []
    raw_platforms = document.get("platforms")
    if not isinstance(raw_platforms, list):
        return []
    result: list[dict[str, Any]] = []
    for platform in raw_platforms:
        if not isinstance(platform, Mapping):
            continue
        platform_id = str(platform.get("id") or "").strip()
        if not platform_id or not is_valid_platform_id(platform_id):
            continue
        title = platform.get("displayName")
        label: str
        if isinstance(title, dict):
            if resolve_label is not None:
                label = str(resolve_label(title, language=language, default_language="en"))
            else:
                label = (
                    str(title.get(language) or title.get("en") or platform_id).strip()
                    or platform_id
                )
        elif isinstance(title, str) and title.strip():
            label = title.strip()
        else:
            label = platform_id
        apps: list[dict[str, str]] = []
        raw_apps = platform.get("apps")
        if isinstance(raw_apps, list):
            for app in raw_apps:
                if not isinstance(app, Mapping):
                    continue
                stable = stable_guide_client_id(app)
                if not is_valid_client_id(stable):
                    continue
                apps.append({"id": stable, "name": str(app.get("name") or stable)})
        result.append({"id": platform_id, "label": label, "apps": apps})
    return result


def local_config_id_for_source(source: str) -> str:
    normalized = str(source or "").strip()
    if normalized == "admin_json":
        return LOCAL_ADMIN_JSON_CONFIG_ID
    return LOCAL_FILE_CONFIG_ID


def panel_config_id_for_uuid(config_uuid: str) -> str:
    text = str(config_uuid or "").strip()
    if not text:
        raise ValueError("Panel config UUID is empty")
    return f"panel:{text}"


async def build_guide_catalog_entries(
    settings: Any,
    panel_service: Any | None = None,
) -> list[dict[str, Any]]:
    """List every configured guide variant with explicit source identity.

    Local and Panel sources are collected independently so an empty local
    JSON override never implies an empty Panel catalog.
    """

    from config.subscription_guides_config import (
        SubscriptionGuidesConfigError,
        load_subscription_guides_config,
        validate_panel_subscription_guides_config,
    )

    entries: list[dict[str, Any]] = []

    def _local_failure_identity() -> tuple[str, str]:
        admin_json = str(settings.SUBSCRIPTION_PAGE_CONFIG_JSON or "").strip()
        override = bool(settings.SUBSCRIPTION_PAGE_CONFIG_JSON_OVERRIDE_ENABLED)
        if admin_json and override:
            return (LOCAL_ADMIN_JSON_CONFIG_ID, "admin_json")
        return (LOCAL_FILE_CONFIG_ID, "file")

    try:
        local_config, local_source = load_subscription_guides_config(settings)
        entries.append(
            project_guide_catalog_config(
                local_config,
                config_id=local_config_id_for_source(local_source),
                source=local_source,
            )
        )
        local_ok = True
    except SubscriptionGuidesConfigError as exc:
        fail_config_id, fail_source = _local_failure_identity()
        entries.append(
            project_guide_catalog_config(
                None,
                config_id=fail_config_id,
                source=fail_source,
                error=str(exc),
                missing=True,
            )
        )
        local_ok = False
    except Exception as exc:
        fail_config_id, fail_source = _local_failure_identity()
        entries.append(
            project_guide_catalog_config(
                None,
                config_id=fail_config_id,
                source=fail_source,
                error=str(exc),
                missing=True,
            )
        )
        local_ok = False

    panel_enabled = bool(settings.SUBSCRIPTION_PAGE_CONFIG_PANEL_ENABLED)
    if panel_enabled and panel_service is not None:
        list_fn = getattr(panel_service, "get_subscription_page_config_list", None)
        get_by_uuid = getattr(panel_service, "get_subscription_page_config_by_uuid", None)
        if callable(list_fn) and callable(get_by_uuid):
            try:
                payload = await list_fn()
                items = _panel_catalog_items(payload)
                seen: set[str] = set()
                for item in items:
                    uuid = str(item.get("uuid") or "").strip()
                    if not uuid or uuid in seen:
                        continue
                    seen.add(uuid)
                    config_id = panel_config_id_for_uuid(uuid)
                    try:
                        detail = await get_by_uuid(uuid)
                        if detail is None:
                            raise SubscriptionGuidesConfigError(
                                f"Panel subscription page config {uuid} is unavailable"
                            )
                        validated = validate_panel_subscription_guides_config(detail)
                    except SubscriptionGuidesConfigError as exc:
                        entries.append(
                            project_guide_catalog_config(
                                None,
                                config_id=config_id,
                                source="panel",
                                error=str(exc),
                                missing=True,
                            )
                        )
                        continue
                    except Exception as exc:
                        entries.append(
                            project_guide_catalog_config(
                                None,
                                config_id=config_id,
                                source="panel",
                                error=str(exc),
                                missing=True,
                            )
                        )
                        continue
                    entries.append(
                        project_guide_catalog_config(validated, config_id=config_id, source="panel")
                    )
            except SubscriptionGuidesConfigError as exc:
                entries.append(
                    project_guide_catalog_config(
                        None,
                        config_id="panel:unavailable",
                        source="panel",
                        error=str(exc),
                        missing=True,
                    )
                )
            except Exception as exc:
                entries.append(
                    project_guide_catalog_config(
                        None,
                        config_id="panel:unavailable",
                        source="panel",
                        error=str(exc),
                        missing=True,
                    )
                )

    if panel_enabled and not any(entry["source"] == "panel" for entry in entries):
        # Missing service/methods is an unavailable source. An empty successful
        # Panel list, however, is a complete catalog with no matching config.
        list_fn = getattr(panel_service, "get_subscription_page_config_list", None)
        get_by_uuid = getattr(panel_service, "get_subscription_page_config_by_uuid", None)
        if not callable(list_fn) or not callable(get_by_uuid):
            entries.append(
                project_guide_catalog_config(
                    None,
                    config_id="panel:unavailable",
                    source="panel",
                    error="catalog_unavailable",
                    missing=True,
                )
            )

    if not local_ok:
        # Keep the local failure visible without dropping Panel variants.
        pass
    return entries


def _panel_catalog_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, Mapping):
        return []
    for key in ("configs", "items", "subscriptionPageConfigs", "subpageConfigs"):
        items = payload.get(key)
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    for key in ("response", "data", "result"):
        nested = _panel_catalog_items(payload.get(key))
        if nested:
            return nested
    return []
