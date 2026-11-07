"""GA4 analytics settings (built-in ``ga4funnel`` plugin)."""

from __future__ import annotations

import re

from pydantic import Field, SecretStr

_GA4_MEASUREMENT_ID = re.compile(r"G-[A-Z0-9]{4,20}\Z")


def _ga4_api_secret_value(value: object) -> str:
    if isinstance(value, SecretStr):
        return value.get_secret_value().strip()
    return str(value or "").strip()


class AnalyticsSettings:
    GA4_ENABLED: bool = Field(
        default=False,
        description=(
            "Enable the optional GA4 onboarding funnel. Requires a valid "
            "GA4_MEASUREMENT_ID and GA4_API_SECRET; otherwise nothing is "
            "collected or sent."
        ),
    )
    GA4_MEASUREMENT_ID: str = Field(
        default="",
        description="GA4 web-stream measurement ID (G-...). Empty disables analytics.",
    )
    GA4_API_SECRET: SecretStr | None = Field(
        default=None,
        description=(
            "GA4 Measurement Protocol API secret. Server-only; empty disables "
            "authoritative server events."
        ),
    )
    GA4_DEBUG_MODE: bool = Field(
        default=False,
        description="Send GA4 debug_mode to the browser tag and DebugView.",
    )

    @property
    def ga4_configured(self) -> bool:
        """True only when GA4 is enabled and fully configured."""
        measurement_id = str(self.GA4_MEASUREMENT_ID or "").strip()
        return bool(
            self.GA4_ENABLED
            and _GA4_MEASUREMENT_ID.fullmatch(measurement_id)
            and _ga4_api_secret_value(self.GA4_API_SECRET)
        )
