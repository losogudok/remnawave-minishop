"""Customer plugin pages available to every administrator button editor."""

from aiohttp import web
from pydantic import Field

from bot.app.web.context import get_i18n, get_session_factory
from bot.app.web.http_contracts import HttpResponseModel
from bot.app.web.route_contracts import RouteContract, ok_envelope_for, register_contract
from bot.app.web.webapp.guide_document import load_guide_content
from bot.plugins.extensions.presentation import preferences
from bot.plugins.message_catalog import message_pages
from bot.services.message_composition import resolve_localized_text

from .auth import _require_admin_user_id
from .broadcast_shortcodes import _admin_language
from .common import _ok


class AdminMessageTargetOut(HttpResponseModel):
    id: str
    label: str
    owner: str


class AdminInstallAppOut(HttpResponseModel):
    id: str
    name: str


class AdminInstallPlatformOut(HttpResponseModel):
    id: str
    label: str
    apps: list[AdminInstallAppOut] = Field(default_factory=list)


class AdminGuideCatalogAppOut(HttpResponseModel):
    id: str
    name: str
    explicit_id: bool = False
    ambiguous: bool = False


class AdminGuideCatalogPlatformOut(HttpResponseModel):
    id: str
    label: str
    apps: list[AdminGuideCatalogAppOut] = Field(default_factory=list)


class AdminGuideCatalogConfigOut(HttpResponseModel):
    config_id: str
    source: str
    platforms: list[AdminGuideCatalogPlatformOut] = Field(default_factory=list)
    error: str | None = None
    missing: bool = False


class AdminMessageTargetsOut(HttpResponseModel):
    sections: list[AdminMessageTargetOut] = Field(default_factory=list)
    install_platforms: list[AdminInstallPlatformOut] = Field(default_factory=list)
    guide_catalog: list[AdminGuideCatalogConfigOut] = Field(default_factory=list)


register_contract(
    "admin_message_targets_route",
    RouteContract(
        response_schema=ok_envelope_for(AdminMessageTargetsOut),
        models=(
            AdminMessageTargetsOut,
            AdminMessageTargetOut,
            AdminInstallPlatformOut,
            AdminInstallAppOut,
            AdminGuideCatalogConfigOut,
            AdminGuideCatalogPlatformOut,
            AdminGuideCatalogAppOut,
        ),
    ),
)


async def admin_message_targets_route(request: web.Request) -> web.Response:
    from bot.app.web.context import get_settings
    from bot.services.guide_catalog import build_guide_catalog_entries, project_install_platforms

    actor_id = _require_admin_user_id(request)
    language = await _admin_language(request, actor_id)
    i18n = get_i18n(request)
    sections: list[AdminMessageTargetOut] = []
    owner_choices: dict[str, dict[str, tuple[bool, int]]] = {}
    async with get_session_factory(request)() as session:
        for path, label, owner, i18n_key, page_id in await message_pages():
            if owner not in owner_choices:
                owner_choices[owner] = await preferences(session, owner)
            choice = owner_choices[owner].get(f"view:{page_id}")
            if choice is not None and not choice[0]:
                continue
            if i18n_key and i18n is not None:
                translated = i18n.gettext(language, i18n_key)
                if translated != i18n_key:
                    label = translated
            sections.append(AdminMessageTargetOut(id=path, label=label, owner=owner))
    _status, document = await load_guide_content(request, user_id=actor_id)
    projected = project_install_platforms(
        document,
        resolve_label=resolve_localized_text,
        language=language,
    )
    install_platforms = [
        AdminInstallPlatformOut(
            id=platform["id"],
            label=platform["label"],
            apps=[
                AdminInstallAppOut(id=app["id"], name=app["name"])
                for app in platform.get("apps", [])
            ],
        )
        for platform in projected
    ]
    guide_catalog: list[AdminGuideCatalogConfigOut] = []
    try:
        from bot.app.web.webapp.guides_panel_config import _panel_service_from_app

        settings = get_settings(request)
        panel_service = _panel_service_from_app(request.app)
        entries = await build_guide_catalog_entries(settings, panel_service)
    except Exception:
        entries = []
    for entry in entries:
        platforms: list[AdminGuideCatalogPlatformOut] = []
        for platform in entry.get("platforms", []):
            title = platform.get("display_name", platform.get("id"))
            if isinstance(title, dict):
                label = (
                    resolve_localized_text(title, language=language, default_language="en")
                    if isinstance(title, dict)
                    else str(platform.get("id"))
                )
            else:
                label = str(title or platform.get("id"))
            platforms.append(
                AdminGuideCatalogPlatformOut(
                    id=str(platform.get("id")),
                    label=label,
                    apps=[
                        AdminGuideCatalogAppOut(
                            id=str(app.get("id")),
                            name=str(app.get("name") or app.get("id")),
                            explicit_id=bool(app.get("explicit_id")),
                            ambiguous=bool(app.get("ambiguous")),
                        )
                        for app in platform.get("apps", [])
                    ],
                )
            )
        guide_catalog.append(
            AdminGuideCatalogConfigOut(
                config_id=str(entry.get("config_id")),
                source=str(entry.get("source")),
                platforms=platforms,
                error=entry.get("error"),
                missing=bool(entry.get("missing")),
            )
        )
    payload = AdminMessageTargetsOut(
        sections=sections, install_platforms=install_platforms, guide_catalog=guide_catalog
    )
    response = _ok(payload.model_dump(mode="json"))
    response.headers["Cache-Control"] = "private, no-store"
    return response
