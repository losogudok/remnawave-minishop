"""Public GA4 browser descriptor for the onboarding funnel."""

from __future__ import annotations

from bot.plugins.extensions import OnboardingSignalsContribution, SignalContext

from .config import ga4_configured, normalize_measurement_id

ADAPTER_ENTRY = "/api/plugins/ga4funnel/signals.mjs"


async def resolve_signals(context: SignalContext) -> OnboardingSignalsContribution | None:
    """Contribute the frame adapter only when GA4 is fully configured.

    The app page loads no Google scripts: the adapter embeds the tag in a
    same-origin frame. The API secret never enters this public contract; only
    the measurement ID and public debug flag are exposed to the browser.
    """

    settings = context.settings
    if not ga4_configured(settings):
        return None
    measurement_id = normalize_measurement_id(settings.GA4_MEASUREMENT_ID)
    if measurement_id is None:
        return None
    # The Google tag lives in a same-origin isolated frame. The app page loads
    # no Google script and needs no Google CSP origins.
    return OnboardingSignalsContribution(
        entry=ADAPTER_ENTRY,
        scripts=(),
        connect_hosts=(),
        config={
            "measurement_id": measurement_id,
            "debug_mode": bool(settings.GA4_DEBUG_MODE),
        },
    )


__all__ = ["ADAPTER_ENTRY", "resolve_signals"]
