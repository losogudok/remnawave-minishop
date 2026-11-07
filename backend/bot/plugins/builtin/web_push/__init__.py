"""Built-in Web Push notifications plugin (ONB-07).

Registers the ``push`` notification channel, authenticated device routes,
encrypted subscription storage and the durable delivery worker. The plugin is
always shipped with Core but disabled until ``WEB_PUSH_ENABLED`` and a matching
VAPID key pair are configured.
"""

from __future__ import annotations

from collections.abc import Coroutine
from typing import TYPE_CHECKING, Any

from aiohttp import web

from bot.plugins.spec import (
    WEB_SCOPE_WEBAPP,
    Plugin,
    PluginContext,
    WorkerTaskSpec,
)

if TYPE_CHECKING:
    from db.migrator import Migration


def _worker_task(ctx: PluginContext) -> Coroutine[Any, Any, None]:
    from .worker import WebPushDeliveryWorker

    return WebPushDeliveryWorker(
        ctx.settings,
        ctx.require_session_factory(),
    ).run()


class WebPushPlugin(Plugin):
    name = "web_push"
    version = "1.0.0"
    plugin_api_min_version = 1
    plugin_api_max_version = 1

    def setup(self, ctx: PluginContext) -> None:
        from bot.services.notification_channels import register_notification_channel_provider

        if ctx.session_factory is None:
            return
        from .provider import WebPushChannelProvider

        register_notification_channel_provider(
            WebPushChannelProvider(ctx.settings, ctx.session_factory)
        )

    def setup_web(self, ctx: PluginContext, app: web.Application, *, scope: str) -> None:
        if scope != WEB_SCOPE_WEBAPP:
            return
        from .routes import (
            register_web_push_contracts,
            web_push_status_route,
            web_push_subscribe_route,
            web_push_sync_route,
            web_push_unsubscribe_route,
        )

        app.router.add_post("/api/plugins/web_push/status", web_push_status_route)
        app.router.add_post("/api/plugins/web_push/subscription", web_push_subscribe_route)
        app.router.add_post("/api/plugins/web_push/subscription/sync", web_push_sync_route)
        app.router.add_delete("/api/plugins/web_push/subscription", web_push_unsubscribe_route)
        register_web_push_contracts()

    def worker_tasks(self, ctx: PluginContext) -> list[WorkerTaskSpec]:
        return [
            WorkerTaskSpec(
                name="WebPushDeliveryWorker",
                factory=_worker_task,
                enabled=lambda settings: bool(settings.WEB_PUSH_ENABLED),
            )
        ]

    def migrations(self) -> list[Migration]:
        from .migrations import WEB_PUSH_MIGRATIONS

        return list(WEB_PUSH_MIGRATIONS)


__all__ = ["WebPushPlugin"]
