"""Built-in plugins bundled with the application.

These exercise the same extension API as externally installed plugins and
serve as reference implementations. They are always active regardless of
``PLUGINS_ENABLED``, which only gates entry-point discovery.
"""

from .ga4funnel import Ga4FunnelPlugin
from .guide_media import GuideMediaPlugin
from .lknpd import LknpdPlugin
from .telemetry import TelemetryPlugin

#: Plugin classes instantiated by the loader, in activation order.
BUILTIN_PLUGINS = (
    TelemetryPlugin,
    LknpdPlugin,
    Ga4FunnelPlugin,
    GuideMediaPlugin,
)

__all__ = [
    "BUILTIN_PLUGINS",
    "Ga4FunnelPlugin",
    "GuideMediaPlugin",
    "LknpdPlugin",
    "TelemetryPlugin",
]
