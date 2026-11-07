"""Built-in optional GA4 onboarding funnel plugin.

The browser adapter observes sanitized Core signals; authoritative funnel
events come from Core domain events and the ``payment.succeeded`` durable
outbox. Every outbound Measurement Protocol parameter is allowlisted so URLs,
tokens, emails and Telegram/user identifiers never leave the server.
"""

from __future__ import annotations

from collections.abc import Coroutine
from typing import Any

from aiohttp import web

from bot.plugins.extensions import (
    DurableSubscription,
    ExtensionContributions,
    JobHandler,
    OnboardingSignalProvider,
)
from bot.plugins.spec import (
    WEB_SCOPE_WEBAPP,
    Plugin,
    PluginContext,
    WorkerTaskSpec,
)
from db.migrator import Migration

from .capture import capture_payment, register_event_handlers
from .config import PLUGIN_NAME, PLUGIN_VERSION
from .migrations import CHAIN_GA4FUNNEL
from .reconcile import run_reconcile
from .routes import setup_ga4funnel_routes
from .signals import resolve_signals


def _worker_coroutine(ctx: PluginContext) -> Coroutine[Any, Any, None]:
    from .worker import Ga4FunnelWorker

    async def _run() -> None:
        await Ga4FunnelWorker(ctx).run()

    return _run()


class Ga4FunnelPlugin(Plugin):
    name = PLUGIN_NAME
    version = PLUGIN_VERSION
    plugin_api_min_version = 1
    plugin_api_max_version = 1

    def __init__(self) -> None:
        self._subscribed = False

    def setup(self, ctx: PluginContext) -> None:
        if self._subscribed:
            return
        register_event_handlers(ctx)
        self._subscribed = True

    def extensions(self, ctx: PluginContext) -> ExtensionContributions:
        _ = ctx
        return ExtensionContributions(
            signals=(OnboardingSignalProvider("web", resolve_signals),),
            jobs=(
                JobHandler(
                    "capture_payment",
                    capture_payment,
                    timeout_seconds=30,
                    max_attempts=8,
                ),
                JobHandler(
                    "reconcile",
                    run_reconcile,
                    timeout_seconds=120,
                    max_attempts=3,
                    interval_seconds=900,
                ),
            ),
            events=(DurableSubscription("payment.succeeded", "capture_payment"),),
        )

    def setup_web(self, ctx: PluginContext, app: web.Application, *, scope: str) -> None:
        _ = ctx
        if scope != WEB_SCOPE_WEBAPP:
            return
        setup_ga4funnel_routes(app)

    def worker_tasks(self, ctx: PluginContext) -> list[WorkerTaskSpec]:
        _ = ctx
        return [WorkerTaskSpec(name="Ga4FunnelWorker", factory=_worker_coroutine)]

    def migrations(self) -> list[Migration]:
        return list(CHAIN_GA4FUNNEL)


__all__ = ["Ga4FunnelPlugin"]
