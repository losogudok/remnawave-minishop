import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from aiogram import Bot
from sqlalchemy.orm import sessionmaker

from bot.middlewares.i18n import JsonI18n
from bot.services import admin_broadcast_delivery as delivery_module
from bot.services.admin_broadcast_delivery import AdminBroadcastDeliveryService
from bot.services.broadcast_personalization import BroadcastUserContext
from bot.utils.message_queue import QueuedMessage, TelegramMessageQueue
from db.broadcast_models import AdminBroadcast, AdminBroadcastDelivery
from tests.support.settings_stub import settings_stub

REPO_ROOT = Path(__file__).resolve().parents[2]


class _SessionContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False


class _SessionFactory:
    def __call__(self) -> _SessionContext:
        return _SessionContext()


class _Queue:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send_message(self, chat_id: int, **kwargs: Any) -> None:
        self.messages.append({"chat_id": chat_id, **kwargs})


def _service(queue: _Queue | None = None) -> AdminBroadcastDeliveryService:
    settings = settings_stub(
        SUBSCRIPTION_MINI_APP_URL="https://app.example.test/",
        DEFAULT_LANGUAGE="ru",
    )
    return AdminBroadcastDeliveryService(
        settings=settings,
        session_factory=cast(sessionmaker, _SessionFactory()),
        i18n=JsonI18n(str(REPO_ROOT / "locales"), default="ru"),
        audience_service=cast(Any, SimpleNamespace(panel_service=None)),
        queue_manager=cast(Any, queue),
        bot_username="demo_bot",
    )


def _broadcast(**overrides: Any) -> AdminBroadcast:
    values: dict[str, Any] = {
        "broadcast_id": 1,
        "created_by_admin_id": 99,
        "target": "all",
        "channels": ["telegram", "email"],
        "texts": {"ru": "Привет {first_name}", "en": "Hello {first_name}"},
        "email_subjects": {"ru": "Новости", "en": "News"},
        "buttons": [],
    }
    values.update(overrides)
    return cast(AdminBroadcast, SimpleNamespace(**values))


def _delivery(**overrides: Any) -> AdminBroadcastDelivery:
    values: dict[str, Any] = {
        "delivery_id": 1,
        "broadcast_id": 1,
        "user_id": 1,
        "channel": "telegram",
        "destination": "111",
        "language_code": "en",
    }
    values.update(overrides)
    return cast(AdminBroadcastDelivery, SimpleNamespace(**values))


class AdminBroadcastDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_recipient_destinations_are_snapshotted_per_channel(self) -> None:
        captured: list[dict[str, Any]] = []

        async def add_deliveries(
            _session: object,
            _broadcast: AdminBroadcast,
            deliveries: list[dict[str, Any]],
        ) -> list[AdminBroadcastDelivery]:
            captured.extend(deliveries)
            return []

        with (
            patch.object(
                delivery_module.user_dal,
                "get_language_codes_for_broadcast",
                AsyncMock(return_value={-555: "ru"}),
            ),
            patch.object(
                delivery_module.user_dal,
                "get_telegram_recipients_for_broadcast",
                AsyncMock(return_value=[(-555, 123456789)]),
            ),
            patch.object(
                delivery_module.user_dal,
                "get_email_recipients_for_broadcast",
                AsyncMock(return_value=[(-555, "linked@example.com", "ru")]),
            ),
            patch.object(
                delivery_module.broadcast_dal,
                "add_deliveries",
                side_effect=add_deliveries,
            ),
        ):
            await _service()._prepare_deliveries(
                _broadcast(),
                [-555],
                ["telegram", "email"],
            )

        self.assertEqual(
            [(item["channel"], item["destination"]) for item in captured],
            [("telegram", "123456789"), ("email", "linked@example.com")],
        )

    async def test_personalization_is_rendered_for_telegram_and_email(self) -> None:
        queue = _Queue()
        service = _service(queue)
        contexts = {
            1: BroadcastUserContext(user_id=1, first_name="Ann", language_code="en"),
            2: BroadcastUserContext(user_id=2, first_name="Борис", language_code="ru"),
        }
        scheduled: list[Any] = []

        def schedule(**kwargs: Any) -> int:
            scheduled.extend(kwargs["recipients"])
            return len(kwargs["recipients"])

        deliveries = [
            _delivery(delivery_id=1, user_id=1, destination="111", language_code="en"),
            _delivery(delivery_id=2, user_id=2, destination="222", language_code="ru"),
            _delivery(
                delivery_id=3,
                user_id=1,
                channel="email",
                destination="ann@example.test",
                language_code="en",
            ),
            _delivery(
                delivery_id=4,
                user_id=2,
                channel="email",
                destination="boris@example.test",
                language_code="ru",
            ),
        ]

        with (
            patch.object(
                delivery_module, "load_broadcast_contexts", AsyncMock(return_value=contexts)
            ),
            patch.object(delivery_module, "schedule_broadcast_emails", side_effect=schedule),
            patch.object(delivery_module.broadcast_dal, "mark_delivery_queued", AsyncMock()),
            patch.object(delivery_module.broadcast_dal, "refresh_broadcast_stats", AsyncMock()),
        ):
            result = await service._queue_deliveries(
                _broadcast(),
                deliveries,
                [1, 2],
                ["telegram", "email"],
            )

        self.assertEqual([item["text"] for item in queue.messages], ["Hello Ann", "Привет Борис"])
        self.assertEqual([item.message_text for item in scheduled], ["Hello Ann", "Привет Борис"])
        self.assertEqual(result.queued, 2)
        self.assertEqual(result.email_queued, 2)


class MessageQueueDeliveryCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_failure_callback_receives_terminal_send_error(self) -> None:
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("offline")))
        queue = TelegramMessageQueue(cast(Bot, bot), messages_per_second=1000)
        failures: list[str] = []

        async def on_failure(exc: Exception) -> None:
            failures.append(str(exc))

        await queue.add_message(
            QueuedMessage(
                chat_id=42,
                method_name="send_message",
                kwargs={"text": "Hello"},
                error_callback=on_failure,
            )
        )
        if queue._processing_task is not None:
            await queue._processing_task

        self.assertEqual(failures, ["offline"])
        self.assertEqual(queue.total_failed, 1)
