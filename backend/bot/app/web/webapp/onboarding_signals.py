"""Vendor-neutral onboarding signal integrations for the Mini App shell.

Core contract (ONB-04) letting deployment-enabled integrations initialize on
guest, public and authenticated surfaces and observe allowlisted navigation
and actions. A plugin contributes :class:`OnboardingSignalProvider` entries
through ``Plugin.extensions()``; Core collects their public descriptors into
the webapp bootstrap payload, merges their validated origins into the page
Content-Security-Policy, and drives them through a sanitized browser
lifecycle. The plugin itself owns settings, persistence and delivery (ONB-05).

Lifecycle semantics (enforced by the browser host):

- Collection starts on the first visit without a visitor consent prompt once
  an administrator has enabled the integration (plugin installed and
  enabled, provider resolves to a contribution). Disabling or removing the
  integration contributes no descriptor, no asset and no CSP origin, and Core
  issues no third-party request for it.
- The browser host coordinates with the existing advertising capture: vendor
  scripts are injected only after ``captureAdvertising`` settles, so
  acquisition metadata is attributed before its URL cleanup, while existing
  attribution behavior is preserved byte-for-byte.
- Signals carry safe screen/action names (``login``, ``public_install``,
  ``home``, ``install``, ``trial``, ...) and explicit flat fields. Raw URLs,
  subscription/share tokens, email addresses, Telegram identifiers and
  arbitrary payloads are never accepted.
- Repeated mount/navigation of the same screen does not duplicate signals;
  each ``(screen, surface)`` view is reported once per page load.
- Provider failures (resolve raising, invalid contribution, script load
  error) are isolated: the integration is skipped and the page, login,
  checkout, instructions and navigation keep working.
- Browser-to-account binding reuses the existing advertising visit claim on
  authentication (the opaque visit cookie is joined to the account by
  ``advertising_identity_middleware``); the browser host additionally
  notifies integrations of the guest-to-auth transition through an explicit
  ``setAuthenticated`` callback so tag-generated browser context can be
  joined without exposing Core DOM or intercepting global fetch/history.

Trust: plugin code is trusted publisher-signed code (see
``bot.plugins.packages``). Declared third-party script URLs and connect
origins are validated here (https-only, bounded counts/sizes) and merged
into the page CSP only while the integration is enabled.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from urllib.parse import urlsplit

from pydantic import Field, JsonValue

from bot.app.web.http_contracts import HttpResponseModel
from bot.plugins.extensions.contracts import (
    OnboardingSignalsContribution,
    SignalContext,
)
from bot.plugins.extensions.registry import get_registry
from bot.plugins.packages import generation_is_current, package_root, read_state
from config.settings import Settings

logger = logging.getLogger(__name__)

#: Capability name plugins request in ``core_compatibility``.
ONBOARDING_SIGNALS_CAPABILITY = "onboarding_signals"

#: Upper bounds keeping one page load predictable with several integrations.
MAX_SIGNAL_INTEGRATIONS = 4
MAX_SIGNAL_SCRIPTS = 4
MAX_SIGNAL_CONNECT_HOSTS = 8
MAX_SIGNAL_ENTRY_LENGTH = 256
MAX_SIGNAL_SCRIPT_LENGTH = 256
MAX_SIGNAL_CONFIG_KEYS = 16
MAX_SIGNAL_CONFIG_STRING_LENGTH = 256
#: Per-provider resolve budget (seconds).
MAX_SIGNAL_PROVIDER_SECONDS = 5.0
#: Total collection budget (seconds) bounding the critical page path even when
#: many providers hang or fail. Must stay well under HTTP client timeouts.
MAX_SIGNAL_COLLECTION_SECONDS = 2.0

_SIGNAL_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_SIGNAL_HOST_PATTERN = rf"(?:{_SIGNAL_LABEL}\.)*{_SIGNAL_LABEL}"
_SIGNAL_HOST = re.compile(f"^{_SIGNAL_HOST_PATTERN}$")
_SIGNAL_ORIGIN = re.compile(f"^https://{_SIGNAL_HOST_PATTERN}(?::[0-9]{{1,5}})?$")
_SIGNAL_CONFIG_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


class OnboardingSignalDescriptorOut(HttpResponseModel):
    """Public browser descriptor for one enabled signal integration."""

    owner: str
    id: str
    entry: str = ""
    scripts: list[str] = Field(default_factory=list)
    connect_hosts: list[str] = Field(default_factory=list)
    config: dict[str, JsonValue] = Field(default_factory=dict)


def _valid_signal_host(host: str) -> bool:
    return bool(host) and len(host) <= 253 and bool(_SIGNAL_HOST.fullmatch(host.lower()))


def _valid_signal_port(port: int | None) -> bool:
    if port is None:
        return True
    return 1 <= port <= 65535


def is_valid_signal_entry(owner: str, entry: str) -> bool:
    """Same-origin plugin modules only; the plugin serves them publicly."""
    if not entry:
        return True
    if len(entry) > MAX_SIGNAL_ENTRY_LENGTH or "\\" in entry:
        return False
    if not entry.startswith(f"/api/plugins/{owner}/"):
        return False
    parts = entry.split("?", 1)[0].split("#", 1)[0].split("/")
    return all(part not in {"", ".", ".."} for part in parts[1:])


def is_valid_signal_script_url(url: str) -> bool:
    if not url or len(url) > MAX_SIGNAL_SCRIPT_LENGTH:
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme != "https" or parts.username or parts.password:
        return False
    if not _valid_signal_host(parts.hostname or ""):
        return False
    try:
        port = parts.port
    except ValueError:
        return False
    return _valid_signal_port(port)


def is_valid_signal_connect_host(origin: str) -> bool:
    if not origin or len(origin) > 253 or not _SIGNAL_ORIGIN.fullmatch(origin.lower()):
        return False
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    if parts.scheme != "https" or parts.username or parts.password:
        return False
    if not _valid_signal_host(parts.hostname or ""):
        return False
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        return False
    try:
        port = parts.port
    except ValueError:
        return False
    return _valid_signal_port(port)


def sanitize_signal_config(value: Any) -> dict[str, JsonValue] | None:
    """Keep only flat public config values; reject nesting and oversize."""
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > MAX_SIGNAL_CONFIG_KEYS:
        return None
    cleaned: dict[str, JsonValue] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not _SIGNAL_CONFIG_KEY.fullmatch(key):
            return None
        if isinstance(item, bool):
            cleaned[key] = item
        elif isinstance(item, str):
            if len(item) > MAX_SIGNAL_CONFIG_STRING_LENGTH:
                return None
            cleaned[key] = item
        elif isinstance(item, int) and not isinstance(item, bool):
            if abs(item) > 9_999_999_999_999_999:
                return None
            cleaned[key] = item
        elif isinstance(item, float):
            cleaned[key] = item
        else:
            return None
    return cleaned


def validate_signal_contribution(
    owner: str, provider_id: str, contribution: OnboardingSignalsContribution
) -> OnboardingSignalDescriptorOut | None:
    try:
        scripts = list(contribution.scripts or ())
        connect_hosts = list(contribution.connect_hosts or ())
        if (
            len(scripts) > MAX_SIGNAL_SCRIPTS
            or len(connect_hosts) > MAX_SIGNAL_CONNECT_HOSTS
            or not is_valid_signal_entry(owner, contribution.entry or "")
            or any(not is_valid_signal_script_url(item) for item in scripts)
            or any(not is_valid_signal_connect_host(item) for item in connect_hosts)
        ):
            return None
        config = sanitize_signal_config(contribution.config)
        if config is None:
            return None
        if not contribution.entry and not scripts and not connect_hosts:
            return None
        return OnboardingSignalDescriptorOut(
            owner=owner,
            id=provider_id,
            entry=contribution.entry or "",
            scripts=scripts,
            connect_hosts=connect_hosts,
            config=config,
        )
    except Exception:
        logger.warning("Onboarding signal %s:%s contribution failed validation", owner, provider_id)
        return None


async def collect_onboarding_signal_descriptors(
    settings: Settings,
) -> list[OnboardingSignalDescriptorOut]:
    """Collect validated public descriptors for enabled integrations.

    Never raises: provider failures, a superseded plugin generation and
    invalid contributions only skip that provider. Ordering is deterministic
    (owner, provider id) so attribution sequencing stays stable. The whole
    collection -- including the initial plugin-state read and every
    generation fence -- is bounded by ``MAX_SIGNAL_COLLECTION_SECONDS`` so
    slow storage cannot delay the page beyond budget; the generation is
    rechecked after every await before any result is published.
    """
    try:
        async with asyncio.timeout(MAX_SIGNAL_COLLECTION_SECONDS):
            # Blocking disk reads run in a worker thread so the timeout can
            # bound slow storage instead of stalling the event loop.
            if not await asyncio.to_thread(generation_is_current):
                return []
            try:
                state = await asyncio.to_thread(read_state, package_root())
            except Exception:
                logger.warning("Onboarding signal collection cannot read plugin state")
                return []
            if not await asyncio.to_thread(generation_is_current):
                return []
            installations = state.get("installations", {})
            if not isinstance(installations, dict):
                return []
            owners = get_registry().owners()
            context = SignalContext(settings=settings)
            descriptors: list[OnboardingSignalDescriptorOut] = []
            # Built-in and entry-point plugins are already loaded and gate
            # themselves in ``resolve``; only package installations carry the
            # explicit ``enabled`` flag. A package owner without a record is
            # not active, while an installed owner that is not loaded anymore
            # is skipped.
            for owner in sorted(set(owners) | set(installations)):
                if not await asyncio.to_thread(generation_is_current):
                    return []
                entry = owners.get(owner)
                if entry is None:
                    continue
                if owner in installations:
                    installation = installations[owner]
                    if not isinstance(installation, dict) or not installation.get("enabled"):
                        continue
                for provider in entry.contributions.signals:
                    if not await asyncio.to_thread(generation_is_current):
                        return []
                    try:
                        async with asyncio.timeout(MAX_SIGNAL_PROVIDER_SECONDS):
                            contribution = await provider.resolve(context)
                    except Exception:
                        logger.warning("Onboarding signal %s:%s is unavailable", owner, provider.id)
                        continue
                    if not await asyncio.to_thread(generation_is_current):
                        return []
                    if contribution is None:
                        continue
                    if not isinstance(contribution, OnboardingSignalsContribution):
                        logger.warning(
                            "Onboarding signal %s:%s returned an invalid contribution",
                            owner,
                            provider.id,
                        )
                        continue
                    try:
                        validated = validate_signal_contribution(owner, provider.id, contribution)
                    except Exception:
                        logger.warning(
                            "Onboarding signal %s:%s contribution failed validation",
                            owner,
                            provider.id,
                        )
                        continue
                    if validated is None:
                        logger.warning(
                            "Onboarding signal %s:%s contribution failed validation",
                            owner,
                            provider.id,
                        )
                        continue
                    descriptors.append(validated)
                    if len(descriptors) >= MAX_SIGNAL_INTEGRATIONS:
                        if not await asyncio.to_thread(generation_is_current):
                            return []
                        return descriptors
            if not await asyncio.to_thread(generation_is_current):
                return []
            return descriptors
    except TimeoutError:
        logger.warning("Onboarding signal collection exceeded total budget")
        return []


def onboarding_signals_csp_hosts(
    descriptors: list[OnboardingSignalDescriptorOut],
) -> tuple[list[str], list[str]]:
    """Split validated origins into script-src and connect-src additions.

    Never raises: every contribution/CSP extraction is isolated so a single
    malformed descriptor cannot break the page.
    """
    script_hosts: list[str] = []
    connect_hosts: list[str] = []
    try:
        items = list(descriptors or ())
    except Exception:
        return [], []
    for descriptor in items:
        try:
            scripts = list(descriptor.scripts or ())
        except Exception:
            continue
        for url in scripts:
            try:
                if not isinstance(url, str) or not url:
                    continue
                try:
                    parts = urlsplit(url)
                except ValueError:
                    continue
                if parts.scheme != "https":
                    continue
                hostname = (parts.hostname or "").lower()
                if not _valid_signal_host(hostname):
                    continue
                try:
                    port = parts.port
                except ValueError:
                    continue
                if not _valid_signal_port(port):
                    continue
                origin = f"https://{hostname}" + (f":{port}" if port else "")
                if origin not in script_hosts:
                    script_hosts.append(origin)
            except Exception:
                continue
        try:
            origins = list(descriptor.connect_hosts or ())
        except Exception:
            continue
        for origin in origins:
            try:
                if not isinstance(origin, str) or not is_valid_signal_connect_host(origin):
                    continue
                normalized = origin.lower()
                if normalized not in connect_hosts:
                    connect_hosts.append(normalized)
            except Exception:
                continue
    return script_hosts, connect_hosts


__all__ = [
    "MAX_SIGNAL_COLLECTION_SECONDS",
    "MAX_SIGNAL_CONFIG_KEYS",
    "MAX_SIGNAL_CONFIG_STRING_LENGTH",
    "MAX_SIGNAL_CONNECT_HOSTS",
    "MAX_SIGNAL_ENTRY_LENGTH",
    "MAX_SIGNAL_INTEGRATIONS",
    "MAX_SIGNAL_PROVIDER_SECONDS",
    "MAX_SIGNAL_SCRIPTS",
    "MAX_SIGNAL_SCRIPT_LENGTH",
    "ONBOARDING_SIGNALS_CAPABILITY",
    "OnboardingSignalDescriptorOut",
    "collect_onboarding_signal_descriptors",
    "is_valid_signal_connect_host",
    "is_valid_signal_entry",
    "is_valid_signal_script_url",
    "onboarding_signals_csp_hosts",
    "sanitize_signal_config",
    "validate_signal_contribution",
]
