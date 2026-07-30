"""Admin-facing view of the shared message composition contract.

The resolution rules moved to ``bot.services.message_composition`` so a message
addressed to one user, and plugin-owned sequences, compose buttons and channels
exactly like a broadcast does. This module keeps the broadcast-flavoured names
the admin HTTP layer already uses and binds them to the admin settings object.
"""

from __future__ import annotations

from bot.middlewares.i18n import JsonI18n
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
    MESSAGE_CHANNELS as BROADCAST_CHANNELS,
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
from bot.services.message_composition import (
    normalize_message_channels as normalize_broadcast_channels,
)
from config.settings import Settings

from .schemas import AdminBroadcastButtonBody

__all__ = [
    "BROADCAST_BUTTON_KINDS",
    "BROADCAST_CHANNELS",
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
    "telegram_markup_for_buttons",
]


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
