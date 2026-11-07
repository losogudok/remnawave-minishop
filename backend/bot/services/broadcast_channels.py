"""Core contract for extensible admin broadcast delivery channels.

Telegram and email used to be the only transports, and every part of a
broadcast — recipient resolution, rendering, validation and queueing — knew
their names. This module introduces the shared vocabulary a channel speaks so
a plugin (for example a later Web Push integration) can register another
transport without changing Core:

- :class:`BroadcastChannelDescriptor` — what the composer needs to know about
  a channel: its localized label, availability, capabilities and limits.
- :class:`BroadcastRecipient` — one resolved destination row, including the
  destination key that distinguishes several endpoints of one user.
- :class:`BroadcastChannelMessage` — rendered content for one persisted
  delivery row (text, optional subject, resolved buttons).
- :class:`BroadcastChannelProvider` — the transport interface.
- :class:`BroadcastDispatchContext` / :class:`BroadcastChannelDispatch` — the
  inputs and per-channel result of one dispatch.

Unknown channel names never fall through to another channel (in particular,
never to email): :func:`parse_broadcast_channel` returns ``None`` and the
delivery service records a ``provider_absent`` outcome for that channel while
the other selected channels keep working.

MESSAGE_CHANNELS in :mod:`bot.services.message_composition` stays the closed
set for direct one-user messages; broadcasts use this registry.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

from bot.services.message_composition import MessageButton, MessageValidationError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import sessionmaker

    from bot.middlewares.i18n import JsonI18n
    from bot.services.audience_segmentation import AudienceSegmentationService
    from bot.services.message_image_service import StoredMessageImage
    from bot.utils.message_queue import MessageQueueManager
    from config.settings import Settings
    from db.broadcast_models import AdminBroadcast, AdminBroadcastDelivery

TELEGRAM_MESSAGE_MAX_LENGTH = 4096
# RFC 5322 recommends keeping header lines below 78 characters, but subject
# headers may be folded; 255 is a practical author-facing bound that still
# leaves room for every real subject while keeping a typo from becoming an
# opaque SMTP failure.
EMAIL_SUBJECT_MAX_LENGTH = 255


@dataclass(frozen=True)
class BroadcastChannelDescriptor:
    """Composer-facing description of one broadcast channel."""

    name: str
    label_key: str
    fallback_label: str
    order: int = 100
    available: bool = True
    reason: str = ""
    supports_html: bool = True
    supports_subject: bool = False
    supports_image: bool = True
    supports_buttons: bool = True
    max_text_length: int = 4096
    # Preview-style channels (Web Push) advertise a compact composer budget
    # here but shorten rendered text instead of rejecting the campaign.
    supports_push_preview: bool = False
    destination_level: Literal["user", "endpoint"] = "user"


@dataclass(frozen=True)
class BroadcastRecipient:
    """One resolved destination for a broadcast."""

    user_id: int
    destination: str
    destination_key: str = "default"
    language_code: str | None = None


@dataclass(frozen=True)
class BroadcastChannelMessage:
    """Rendered content for one persisted broadcast delivery row."""

    delivery_id: int
    user_id: int
    destination: str
    destination_key: str
    language_code: str | None
    text: str
    subject: str | None = None
    buttons: list[MessageButton] = field(default_factory=list)


@dataclass(frozen=True)
class BroadcastChannelDispatch:
    """Result of one channel provider dispatch."""

    queued: int
    failed: int
    outcome: str = ""


@dataclass(frozen=True)
class BroadcastDispatchContext:
    """Everything a provider needs to deliver its own slice of a broadcast."""

    broadcast: AdminBroadcast
    deliveries: list[AdminBroadcastDelivery]
    messages: list[BroadcastChannelMessage]
    settings: Settings
    i18n: JsonI18n
    bot_username: str | None
    session_factory: sessionmaker
    queue_manager: MessageQueueManager | None
    audience_service: AudienceSegmentationService
    image: StoredMessageImage | None
    on_queued: Callable[[int], Awaitable[None]]
    on_result: Callable[[int, bool, str | None], Awaitable[None]]


class BroadcastChannelProvider(Protocol):
    """Transport interface registered for one broadcast channel."""

    name: str

    def describe(self, settings: Settings) -> BroadcastChannelDescriptor:
        """Describe the channel and whether it can deliver right now."""
        ...

    async def resolve_recipients(
        self,
        session: AsyncSession,
        *,
        user_ids: list[int],
        exclude_blocked_telegram: bool,
    ) -> list[BroadcastRecipient]:
        """Resolve delivery destinations for the selected audience."""
        ...

    async def dispatch(
        self,
        context: BroadcastDispatchContext,
    ) -> BroadcastChannelDispatch:
        """Deliver the rendered messages and report queued/failed counts."""
        ...


_CHANNEL_PROVIDERS: dict[str, BroadcastChannelProvider] = {}
_BUILTINS_LOADED = False


def descriptor_hard_text_limit(descriptor: BroadcastChannelDescriptor) -> int:
    """Text length a channel rejects up front instead of shortening.

    Web Push is a preview-style channel: its descriptor advertises the compact
    title+body budget so the composer can explain it, while the provider
    truncates each rendered message. A mixed campaign must therefore not be
    rejected merely because its Telegram text exceeds that budget.
    """

    return 0 if descriptor.supports_push_preview else descriptor.max_text_length


def _normalize_channel_name(value: Any) -> str:
    return str(value or "").strip().lower()


def _ensure_builtin_providers() -> None:
    """Register the Core Telegram and email providers exactly once."""

    global _BUILTINS_LOADED
    if _BUILTINS_LOADED:
        return
    _BUILTINS_LOADED = True
    from bot.services.broadcast_channels_builtin import (
        builtin_broadcast_channel_providers,
    )

    for provider in builtin_broadcast_channel_providers():
        _CHANNEL_PROVIDERS.setdefault(_normalize_channel_name(provider.name), provider)


def register_broadcast_channel_provider(provider: BroadcastChannelProvider) -> None:
    """Register (or replace) the provider for ``provider.name``."""

    _ensure_builtin_providers()
    name = _normalize_channel_name(provider.name)
    if not name:
        raise ValueError("Cannot register a broadcast channel provider without a name")
    _CHANNEL_PROVIDERS[name] = provider


def unregister_broadcast_channel_provider(name: Any) -> bool:
    """Remove a registered provider; returns whether one was registered."""

    _ensure_builtin_providers()
    normalized = _normalize_channel_name(name)
    if not normalized:
        return False
    return _CHANNEL_PROVIDERS.pop(normalized, None) is not None


def get_broadcast_channel_provider(name: Any) -> BroadcastChannelProvider | None:
    """Return the provider for a channel, or ``None`` when absent/unknown."""

    _ensure_builtin_providers()
    normalized = _normalize_channel_name(name)
    if not normalized:
        return None
    return _CHANNEL_PROVIDERS.get(normalized)


def parse_broadcast_channel(value: Any) -> str | None:
    """Resolve a channel name; unknown names resolve to ``None``.

    Unknown channels never fall through to another channel (in particular,
    never to email): callers must treat ``None`` as "no such channel".
    """

    normalized = _normalize_channel_name(value)
    if not normalized or get_broadcast_channel_provider(normalized) is None:
        return None
    return normalized


def registered_broadcast_channel_names() -> tuple[str, ...]:
    """Registered channel names in stable alphabetical order."""

    _ensure_builtin_providers()
    return tuple(sorted(_CHANNEL_PROVIDERS))


def _registered_names_in_registration_order() -> tuple[str, ...]:
    _ensure_builtin_providers()
    return tuple(_CHANNEL_PROVIDERS)


def broadcast_channel_descriptors(settings: Settings) -> list[BroadcastChannelDescriptor]:
    """Describe every registered channel, ordered for the composer."""

    _ensure_builtin_providers()
    descriptors = [provider.describe(settings) for provider in _CHANNEL_PROVIDERS.values()]
    return sorted(descriptors, key=lambda item: (item.order, item.name))


def normalize_broadcast_channels(raw: list[str] | None) -> list[str]:
    """Validate and order selected channels through the registry.

    Unknown names are rejected with the stable ``invalid_channel`` code rather
    than silently dropped or rerouted. The returned order follows provider
    registration (Telegram, email, then plugin channels), so existing clients
    keep seeing ``["telegram", "email"]``.
    """

    channels = [
        _normalize_channel_name(channel)
        for channel in dict.fromkeys(raw or [])
        if _normalize_channel_name(channel)
    ]
    if not channels:
        raise MessageValidationError("no_channels")
    unknown = [channel for channel in channels if get_broadcast_channel_provider(channel) is None]
    if unknown:
        raise MessageValidationError("invalid_channel", ", ".join(unknown))
    return [name for name in _registered_names_in_registration_order() if name in channels]
