"""Built-in plugin owning screenshot galleries for subscription guides."""

from __future__ import annotations

from aiohttp import web

from bot.plugins.extensions import ExtensionContributions
from bot.plugins.spec import WEB_SCOPE_WEBAPP, Plugin, PluginContext, WorkerTaskSpec
from db.migrator import Migration

from .migrations import MIGRATIONS
from .service import guide_media_provider


class GuideMediaPlugin(Plugin):
    name = "guide_media"
    version = "1.0.0"
    plugin_api_min_version = 1
    plugin_api_max_version = 1

    def extensions(self, ctx: PluginContext) -> ExtensionContributions:
        return ExtensionContributions(guide_media=(guide_media_provider(ctx.session_factory),))

    def setup_web(self, ctx: PluginContext, app: web.Application, *, scope: str) -> None:
        if scope != WEB_SCOPE_WEBAPP:
            return
        from .routes import setup_guide_media_routes

        setup_guide_media_routes(app)

    def worker_tasks(self, ctx: PluginContext) -> list[WorkerTaskSpec]:
        from .janitor import GuideMediaJanitor

        return [
            WorkerTaskSpec(
                name="GuideMediaJanitor",
                factory=lambda context: GuideMediaJanitor(context.session_factory).run(),
            )
        ]

    def migrations(self) -> list[Migration]:
        return list(MIGRATIONS)
