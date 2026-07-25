"""The shared message composition contract used by broadcasts, single-user
messages, and plugin-owned sequences."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from aiogram.types import InlineKeyboardMarkup

from bot.services import outbound_messaging
from bot.services.message_composition import (
    MessageButton,
    MessageButtonInput,
    MessageValidationError,
    email_links_for_buttons,
    message_promo_codes,
    normalize_message_channels,
    resolve_message_buttons,
    telegram_markup_for_buttons,
)

MINI_APP_HTTPS = "https://shop.example/app"
MINI_APP_HTTP = "http://shop.example/app"


def _resolve(
    *buttons: MessageButtonInput,
    mini_app_url: str = MINI_APP_HTTPS,
) -> list[MessageButton]:
    return resolve_message_buttons(
        list(buttons),
        mini_app_url=mini_app_url,
        bot_username="shop_bot",
    )


class MessageCompositionTests(unittest.TestCase):
    def test_plain_dataclass_inputs_need_no_http_schema(self) -> None:
        """A plugin composes buttons without importing the admin HTTP models."""

        resolved = _resolve(
            MessageButtonInput(kind="url", label="Plans", url="https://shop.example")
        )

        self.assertEqual(
            resolved,
            [MessageButton(label="Plans", url="https://shop.example", kind="url")],
        )

    def test_promo_webapp_button_opens_inside_the_mini_app(self) -> None:
        resolved = _resolve(
            MessageButtonInput(kind="promo_webapp", label="Apply", promo_code="SAVE10")
        )

        self.assertEqual(
            resolved[0].telegram_web_app_url,
            "https://shop.example/app?startapp=promo_SAVE10",
        )
        markup = telegram_markup_for_buttons(resolved)
        self.assertIsInstance(markup, InlineKeyboardMarkup)
        assert markup is not None
        inline = markup.inline_keyboard[0][0]
        # A web_app button keeps the customer inside Telegram; a url button
        # would hand the promo link to an external browser without the Mini
        # App authorization.
        self.assertIsNotNone(inline.web_app)
        self.assertIsNone(inline.url)

    def test_webapp_kind_opens_an_arbitrary_target_inside_telegram(self) -> None:
        resolved = _resolve(
            MessageButtonInput(kind="webapp", label="Plans", url=f"{MINI_APP_HTTPS}?startapp=plans")
        )

        self.assertEqual(resolved[0].telegram_web_app_url, f"{MINI_APP_HTTPS}?startapp=plans")
        markup = telegram_markup_for_buttons(resolved)
        assert markup is not None
        self.assertIsNotNone(markup.inline_keyboard[0][0].web_app)

    def test_webapp_kind_degrades_to_a_link_without_https(self) -> None:
        resolved = _resolve(MessageButtonInput(kind="webapp", label="Plans", url=MINI_APP_HTTP))

        self.assertIsNone(resolved[0].telegram_web_app_url)
        markup = telegram_markup_for_buttons(resolved)
        assert markup is not None
        self.assertEqual(markup.inline_keyboard[0][0].url, MINI_APP_HTTP)

    def test_promo_webapp_falls_back_to_a_startapp_deeplink_without_https(self) -> None:
        resolved = _resolve(
            MessageButtonInput(kind="promo_webapp", label="Apply", promo_code="SAVE10"),
            mini_app_url=MINI_APP_HTTP,
        )

        # Telegram refuses a plain-http web_app target, so the code still has
        # to reach the Mini App through t.me rather than a browser tab.
        self.assertIsNone(resolved[0].telegram_web_app_url)
        self.assertEqual(resolved[0].url, "https://t.me/shop_bot?startapp=promo_SAVE10")

    def test_promo_bot_button_deep_links_into_the_bot(self) -> None:
        resolved = _resolve(
            MessageButtonInput(kind="promo_bot", label="Apply", promo_code="SAVE10")
        )

        self.assertEqual(resolved[0].url, "https://t.me/shop_bot?start=promo_SAVE10")
        self.assertEqual(message_promo_codes(resolved), ["SAVE10"])
        self.assertEqual(
            email_links_for_buttons(resolved),
            [("Apply", "https://t.me/shop_bot?start=promo_SAVE10")],
        )

    def test_authoring_mistakes_carry_a_stable_code(self) -> None:
        cases = [
            (MessageButtonInput(kind="url", label="Plans"), "button_url_required"),
            (MessageButtonInput(kind="url", label="Plans", url="ftp://x"), "button_url_invalid"),
            (MessageButtonInput(kind="promo_bot", label="Apply"), "button_promo_code_required"),
            (
                MessageButtonInput(kind="promo_bot", label="Apply", promo_code="not valid"),
                "button_promo_code_invalid",
            ),
            (MessageButtonInput(kind="nope", label="Apply"), "button_kind_invalid"),
            (MessageButtonInput(kind="url", label="", url="https://x"), "button_label_required"),
        ]
        for button, code in cases:
            with self.subTest(code=code), self.assertRaises(MessageValidationError) as error:
                _resolve(button)
            self.assertEqual(error.exception.code, code)

    def test_too_many_buttons_are_rejected(self) -> None:
        buttons = [
            MessageButtonInput(kind="url", label=f"Link {index}", url="https://x")
            for index in range(5)
        ]
        with self.assertRaises(MessageValidationError) as error:
            resolve_message_buttons(
                list(buttons),
                mini_app_url=MINI_APP_HTTPS,
                bot_username="shop_bot",
            )
        self.assertEqual(error.exception.code, "too_many_buttons")

    def test_channels_are_deduplicated_and_ordered(self) -> None:
        self.assertEqual(
            normalize_message_channels(["email", "telegram", "email"]),
            ["telegram", "email"],
        )
        with self.assertRaises(MessageValidationError) as empty:
            normalize_message_channels([])
        self.assertEqual(empty.exception.code, "no_channels")
        with self.assertRaises(MessageValidationError) as unknown:
            normalize_message_channels(["sms"])
        self.assertEqual(unknown.exception.code, "invalid_channel")


class _FakeQueue:
    def __init__(self, captured: dict[str, Any]) -> None:
        self._captured = captured

    async def send_message(self, **kwargs: Any) -> None:
        self._captured.update(kwargs)


async def _no_audit(*_args: Any, **_kwargs: Any) -> None:
    return None


class OutboundButtonSeamTests(unittest.IsolatedAsyncioTestCase):
    async def _send(self, **kwargs: Any) -> dict[str, Any]:
        captured: dict[str, Any] = {}
        with (
            patch.object(outbound_messaging, "get_queue_manager", lambda: _FakeQueue(captured)),
            patch.object(outbound_messaging, "log_user_message_delivery", _no_audit),
        ):
            sent = await outbound_messaging.OutboundMessagingService().send_text(
                None,
                user_id=42,
                text="hello",
                **kwargs,
            )
        captured["__sent__"] = sent
        return captured

    async def test_send_attaches_the_inline_keyboard(self) -> None:
        """The public send seam forwards buttons as a real inline keyboard."""

        buttons = _resolve(MessageButtonInput(kind="promo_bot", label="Apply", promo_code="SAVE10"))

        captured = await self._send(buttons=buttons)

        self.assertTrue(captured["__sent__"])
        markup = captured["reply_markup"]
        self.assertIsInstance(markup, InlineKeyboardMarkup)
        self.assertEqual(
            markup.inline_keyboard[0][0].url,
            "https://t.me/shop_bot?start=promo_SAVE10",
        )

    async def test_send_without_buttons_stays_keyboardless(self) -> None:
        captured = await self._send()

        self.assertIsNone(captured["reply_markup"])


if __name__ == "__main__":
    unittest.main()
