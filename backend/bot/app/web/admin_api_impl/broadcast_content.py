"""Admin-facing view of the shared message composition contract.

The resolution rules moved to ``bot.services.message_composition`` so a message
addressed to one user, and plugin-owned sequences, compose buttons and channels
exactly like a broadcast does. This module keeps the broadcast-flavoured names
the admin HTTP layer already uses and binds them to the admin settings object.
"""

from __future__ import annotations

from aiohttp import web

from bot.middlewares.i18n import JsonI18n
from bot.services.broadcast_channels import (
    EMAIL_SUBJECT_MAX_LENGTH,
    BroadcastChannelDescriptor,
    descriptor_hard_text_limit,
    get_broadcast_channel_provider,
    normalize_broadcast_channels,
)
from bot.services.message_composition import (
    BUTTON_KIND_PROMO_BOT,
    BUTTON_KIND_PROMO_WEBAPP,
    BUTTON_KIND_URL,
    MAX_BUTTON_LABEL_LENGTH,
    MESSAGE_BUTTON_LABEL_KEYS,
    email_links_for_buttons,
    message_button_label_key,
    resolve_localized_text,
    resolve_message_buttons,
    telegram_markup_for_buttons,
)
from bot.services.message_composition import (
    MAX_MESSAGE_BUTTONS as MAX_BROADCAST_BUTTONS,
)
from bot.services.message_composition import (
    MESSAGE_BUTTON_KINDS as BROADCAST_BUTTON_KINDS,
)
from bot.services.message_composition import (
    MessageButton as BroadcastButton,
)
from bot.services.message_composition import (
    MessageValidationError as BroadcastValidationError,
)
from bot.services.message_composition import (
    message_promo_codes as broadcast_promo_codes,
)
from config.settings import Settings

from .schemas import AdminBroadcastButtonBody

__all__ = [
    "BROADCAST_BUTTON_KINDS",
    "BUTTON_KIND_PROMO_BOT",
    "BUTTON_KIND_PROMO_WEBAPP",
    "BUTTON_KIND_URL",
    "MAX_BROADCAST_BUTTONS",
    "MAX_BUTTON_LABEL_LENGTH",
    "MESSAGE_BUTTON_LABEL_KEYS",
    "BroadcastButton",
    "BroadcastValidationError",
    "broadcast_promo_codes",
    "email_links_for_buttons",
    "message_button_label_key",
    "normalize_broadcast_channels",
    "resolve_broadcast_buttons",
    "resolve_localized_text",
    "selected_channel_descriptors",
    "telegram_markup_for_buttons",
    "validate_broadcast_content_limits",
]


def selected_channel_descriptors(
    settings: Settings,
    channels: list[str],
) -> tuple[list[BroadcastChannelDescriptor], web.Response | None]:
    """Validate selected channels through the registry, never rerouting."""

    from .common import _error

    descriptors: list[BroadcastChannelDescriptor] = []
    for channel in channels:
        provider = get_broadcast_channel_provider(channel)
        if provider is None:
            return [], _error(400, "invalid_channel", channel)
        descriptor = provider.describe(settings)
        if not descriptor.available:
            return [], _error(503, descriptor.reason or "channel_unavailable", channel)
        descriptors.append(descriptor)
    return descriptors, None


def validate_broadcast_content_limits(
    descriptors: list[BroadcastChannelDescriptor],
    *,
    texts: dict[str, str],
    email_subjects: dict[str, str],
) -> web.Response | None:
    """Reject input that no selected channel could deliver up front."""

    from .common import _error

    # Preview-style channels (Web Push) shorten their own payload; only hard
    # limits may reject the campaign. Otherwise a push+email campaign would be
    # rejected for text email can deliver and push would simply truncate.
    text_limits = [
        limit for descriptor in descriptors if (limit := descriptor_hard_text_limit(descriptor)) > 0
    ]
    if text_limits:
        limit = min(text_limits)
        for variant in texts.values():
            if len(variant) > limit:
                return _error(400, "message_too_long", str(limit))
    if any(descriptor.supports_subject for descriptor in descriptors):
        for subject in email_subjects.values():
            if len(subject) > EMAIL_SUBJECT_MAX_LENGTH:
                return _error(400, "email_subject_too_long", str(EMAIL_SUBJECT_MAX_LENGTH))
    return None


def resolve_broadcast_buttons(
    buttons: list[AdminBroadcastButtonBody],
    *,
    settings: Settings,
    bot_username: str | None,
    language: str | None = None,
    i18n: JsonI18n | None = None,
) -> list[BroadcastButton]:
    """Resolve buttons for one recipient language.

    Without an ``i18n`` the prepared captions cannot be looked up, so a button
    must carry an authored one — which is how every caller behaved before
    prepared captions existed.
    """

    return resolve_message_buttons(
        list(buttons),
        mini_app_url=settings.SUBSCRIPTION_MINI_APP_URL,
        bot_username=bot_username,
        language=language,
        translate=(lambda lang, key: i18n.gettext(lang, key)) if i18n is not None else None,
        default_language=settings.DEFAULT_LANGUAGE,
    )
