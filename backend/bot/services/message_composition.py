"""Neutral composition of an outbound message: buttons, channels, promo links.

This is the shared vocabulary for every feature that sends an authored message
to a customer — the admin broadcast, a message addressed to one user, and
plugin-owned sequences. It resolves button descriptors into the links each
delivery channel needs: a Telegram inline keyboard (preferring a ``web_app``
button so the target opens inside the Mini App with its native authorization
instead of an external browser tab) and plain ``(label, url)`` pairs for email.

Nothing here is admin-specific or plugin-specific on purpose: it takes plain
values and returns plain values, so it can be reused across the HTTP layer,
workers, and extensions without importing any of them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

MESSAGE_CHANNELS = ("telegram", "email")
BUTTON_KIND_URL = "url"
BUTTON_KIND_WEBAPP = "webapp"
BUTTON_KIND_PROMO_BOT = "promo_bot"
BUTTON_KIND_PROMO_WEBAPP = "promo_webapp"
BUTTON_KIND_WEBAPP_SECTION = "webapp_section"
MESSAGE_BUTTON_KINDS = (
    BUTTON_KIND_URL,
    BUTTON_KIND_WEBAPP,
    BUTTON_KIND_PROMO_BOT,
    BUTTON_KIND_PROMO_WEBAPP,
    BUTTON_KIND_WEBAPP_SECTION,
)
# Mini App screens an authored button may point at. "plans" opens checkout
# with plan and period selection. The admin panel is deliberately absent:
# these buttons go out to customers.
MINI_APP_SECTIONS = (
    "home",
    "plans",
    "install",
    "trial",
    "invite",
    "devices",
    "support",
    "settings",
)
MAX_MESSAGE_BUTTONS = 4
MAX_BUTTON_LABEL_LENGTH = 64
# Telegram limits /start payloads to 64 chars; the "promo_" prefix leaves 58.
_PROMO_CODE_DEEPLINK_RE = re.compile(r"^[A-Za-z0-9_-]{1,58}$")
_BOT_USERNAME_FALLBACK = "your_bot_username"


class MessageValidationError(ValueError):
    """Raised for author-facing payload problems (mapped to HTTP 400/503)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


class MessageButtonSpec(Protocol):
    """What an authored button looks like before its URL is resolved.

    Declared structurally so HTTP body models and plugin-owned dataclasses both
    satisfy it without importing anything from here.
    """

    kind: str
    label: str
    url: str
    promo_code: str
    section: str


@dataclass(frozen=True)
class MessageButtonInput:
    """Plain implementation of the spec for non-HTTP callers."""

    kind: str
    label: str
    url: str = ""
    promo_code: str = ""
    section: str = ""


@dataclass(frozen=True)
class MessageButton:
    """A resolved button.

    ``url`` is the universal link used for email CTAs and as the Telegram URL
    button fallback. ``telegram_web_app_url`` (when set) makes the Telegram
    button a ``web_app`` one, so the target opens inside the Telegram Mini App
    with its native authorization instead of an external browser tab.
    """

    label: str
    url: str
    kind: str
    promo_code: str = ""
    section: str = ""
    telegram_web_app_url: str | None = None


def normalize_message_channels(raw: list[str] | None) -> list[str]:
    channels = [channel for channel in dict.fromkeys(raw or []) if channel]
    if not channels:
        raise MessageValidationError("no_channels")
    unknown = [channel for channel in channels if channel not in MESSAGE_CHANNELS]
    if unknown:
        raise MessageValidationError("invalid_channel", ", ".join(unknown))
    return [channel for channel in MESSAGE_CHANNELS if channel in channels]


def promo_bot_deeplink(bot_username: str | None, code: str | None) -> str | None:
    """``/start promo_<CODE>`` deep link that applies a code inside the bot."""

    username = str(bot_username or "").strip().lstrip("@")
    normalized_code = str(code or "").strip()
    if not username or username == _BOT_USERNAME_FALLBACK or not normalized_code:
        return None
    return f"https://t.me/{quote(username, safe='')}?start=promo_{quote(normalized_code, safe='')}"


def mini_app_startapp_link(base_url: str | None, payload: str | None) -> str | None:
    """Mini App link carrying a ``startapp`` payload, preserving existing query."""

    raw = str(base_url or "").strip()
    value = str(payload or "").strip()
    if not raw or not value:
        return None
    parts = urlsplit(raw)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["startapp"] = value
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def mini_app_section_link(base_url: str | None, section: str | None) -> str | None:
    """Mini App link that opens one of its screens through ``startapp``."""

    normalized = str(section or "").strip().lower()
    if normalized not in MINI_APP_SECTIONS:
        return None
    return mini_app_startapp_link(base_url, normalized)


def promo_webapp_link(base_url: str | None, code: str | None) -> str | None:
    """Mini App link that prefills a promo code through ``startapp``."""

    normalized_code = str(code or "").strip()
    if not normalized_code:
        return None
    return mini_app_startapp_link(base_url, f"promo_{normalized_code}")


def promo_startapp_deeplink(bot_username: str | None, code: str | None) -> str | None:
    """``t.me`` link that opens the Mini App with a prefilled promo code."""

    username = _clean_bot_username(bot_username)
    normalized_code = str(code or "").strip()
    if not username or not normalized_code:
        return None
    return f"https://t.me/{username}?startapp=promo_{normalized_code}"


def _is_https(url: str) -> bool:
    return url.lower().startswith("https://")


def _clean_bot_username(bot_username: str | None) -> str:
    username = str(bot_username or "").strip().lstrip("@")
    if not username or username == _BOT_USERNAME_FALLBACK:
        return ""
    return username


def _resolve_button(
    button: MessageButtonSpec,
    *,
    mini_app_url: str | None,
    bot_username: str | None,
) -> MessageButton:
    if button.kind in {BUTTON_KIND_URL, BUTTON_KIND_WEBAPP}:
        url = button.url
        if not url:
            raise MessageValidationError("button_url_required", button.label)
        if not url.lower().startswith(("https://", "http://")):
            raise MessageValidationError("button_url_invalid", url)
        # A "webapp" target belongs inside the Mini App. Telegram only accepts
        # https for web_app buttons, so a plain-http target degrades to a
        # normal link rather than being rejected.
        web_app_url = url if button.kind == BUTTON_KIND_WEBAPP and _is_https(url) else None
        return MessageButton(
            label=button.label,
            url=url,
            kind=button.kind,
            telegram_web_app_url=web_app_url,
        )

    if button.kind == BUTTON_KIND_WEBAPP_SECTION:
        return _resolve_section_button(button, mini_app_url=mini_app_url, bot_username=bot_username)

    code = button.promo_code
    if not code:
        raise MessageValidationError("button_promo_code_required", button.label)
    if not _PROMO_CODE_DEEPLINK_RE.fullmatch(code):
        raise MessageValidationError("button_promo_code_invalid", code)

    username = _clean_bot_username(bot_username)
    if button.kind == BUTTON_KIND_PROMO_BOT:
        bot_link = promo_bot_deeplink(username, code)
        if not bot_link:
            raise MessageValidationError("bot_username_unavailable")
        return MessageButton(label=button.label, url=bot_link, kind=button.kind, promo_code=code)

    # promo_webapp: same code-prefill link the Promos section shows for a code.
    webapp_link = promo_webapp_link(mini_app_url, code)
    if webapp_link:
        # Telegram only accepts https targets for web_app buttons; with a
        # plain-http Mini App URL fall back to a t.me startapp deep link so the
        # code still opens inside the Mini App (authorized), not a browser tab.
        if _is_https(webapp_link):
            telegram_web_app_url = webapp_link
        elif username:
            return MessageButton(
                label=button.label,
                url=f"https://t.me/{username}?startapp=promo_{code}",
                kind=button.kind,
                promo_code=code,
            )
        else:
            telegram_web_app_url = None
        return MessageButton(
            label=button.label,
            url=webapp_link,
            kind=button.kind,
            promo_code=code,
            telegram_web_app_url=telegram_web_app_url,
        )
    if username:
        return MessageButton(
            label=button.label,
            url=f"https://t.me/{username}?startapp=promo_{code}",
            kind=button.kind,
            promo_code=code,
        )
    raise MessageValidationError("webapp_url_unavailable")


def _resolve_section_button(
    button: MessageButtonSpec,
    *,
    mini_app_url: str | None,
    bot_username: str | None,
) -> MessageButton:
    """Open a Mini App screen, preferring the in-Telegram ``web_app`` target."""

    section = str(button.section or "").strip().lower()
    if not section:
        raise MessageValidationError("button_section_required", button.label)
    if section not in MINI_APP_SECTIONS:
        raise MessageValidationError("button_section_invalid", section)

    username = _clean_bot_username(bot_username)
    # A t.me startapp link still opens the Mini App authorized, so it is the
    # fallback whenever the configured Mini App URL is unusable as a web_app
    # target (plain http) or missing entirely.
    fallback = f"https://t.me/{username}?startapp={section}" if username else ""
    link = mini_app_section_link(mini_app_url, section)
    if link and _is_https(link):
        return MessageButton(
            label=button.label,
            url=link,
            kind=button.kind,
            section=section,
            telegram_web_app_url=link,
        )
    if fallback:
        return MessageButton(label=button.label, url=fallback, kind=button.kind, section=section)
    if link:
        return MessageButton(label=button.label, url=link, kind=button.kind, section=section)
    raise MessageValidationError("webapp_url_unavailable")


def resolve_message_buttons(
    buttons: list[MessageButtonSpec],
    *,
    mini_app_url: str | None,
    bot_username: str | None,
) -> list[MessageButton]:
    if len(buttons) > MAX_MESSAGE_BUTTONS:
        raise MessageValidationError("too_many_buttons", str(MAX_MESSAGE_BUTTONS))

    resolved: list[MessageButton] = []
    for button in buttons:
        if button.kind not in MESSAGE_BUTTON_KINDS:
            raise MessageValidationError("button_kind_invalid", button.kind)
        if not button.label:
            raise MessageValidationError("button_label_required")
        if len(button.label) > MAX_BUTTON_LABEL_LENGTH:
            raise MessageValidationError("button_label_too_long", button.label)
        resolved.append(
            _resolve_button(button, mini_app_url=mini_app_url, bot_username=bot_username)
        )
    return resolved


def message_promo_codes(buttons: list[MessageButton]) -> list[str]:
    """Unique promo codes referenced by the resolved buttons, in input order."""
    return list(dict.fromkeys(button.promo_code for button in buttons if button.promo_code))


def _telegram_button(button: MessageButton) -> InlineKeyboardButton:
    if button.telegram_web_app_url:
        return InlineKeyboardButton(
            text=button.label,
            web_app=WebAppInfo(url=button.telegram_web_app_url),
        )
    return InlineKeyboardButton(text=button.label, url=button.url)


def telegram_markup_for_buttons(
    buttons: list[MessageButton],
) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[_telegram_button(button)] for button in buttons])


def email_links_for_buttons(buttons: list[MessageButton]) -> list[tuple[str, str]]:
    return [(button.label, button.url) for button in buttons]
