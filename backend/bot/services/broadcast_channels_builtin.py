"""Core Telegram and email broadcast channel providers.

These are the built-in implementations of :mod:`bot.services.broadcast_channels`.
Rendering, personalization and recipient snapshots stay in the delivery
service; this module owns the transport-specific parts that used to be
hard-coded there: Telegram queueing and notification-state callbacks, Telegram
message length validation, email subject handling and email scheduling.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from aiogram.types import BufferedInputFile

from bot.services import broadcast_email_service
from bot.services.broadcast_channels import (
    EMAIL_SUBJECT_MAX_LENGTH,
    TELEGRAM_MESSAGE_MAX_LENGTH,
    BroadcastChannelDescriptor,
    BroadcastChannelDispatch,
    BroadcastChannelMessage,
    BroadcastChannelProvider,
    BroadcastDispatchContext,
    BroadcastRecipient,
)
from bot.services.broadcast_email_service import BroadcastEmailRecipient
from bot.services.message_composition import (
    email_links_for_buttons,
    telegram_markup_for_buttons,
)
from bot.services.message_image_telegram import prepare_telegram_photo
from bot.services.telegram_notifications import (
    mark_telegram_notifications_status,
    record_telegram_notification_failure,
)
from bot.utils.message_queue import MessageQueueManager, delivery_attempted_at, get_queue_manager
from db.dal import user_dal

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from config.settings import Settings

logger = logging.getLogger(__name__)


def _telegram_queue_available() -> bool:
    """Whether the process currently has a running Telegram message queue."""

    return get_queue_manager() is not None


class TelegramBroadcastChannelProvider(BroadcastChannelProvider):
    """Deliver broadcasts through the rate-limited Telegram queue."""

    name = "telegram"

    def describe(self, settings: Settings) -> BroadcastChannelDescriptor:
        enabled = bool(getattr(settings, "TELEGRAM_ENABLED", True))
        available = enabled and _telegram_queue_available()
        reason = "" if available else ("telegram_disabled" if not enabled else "queue_unavailable")
        return BroadcastChannelDescriptor(
            name=self.name,
            label_key="broadcast_channel_telegram",
            fallback_label="Telegram",
            order=10,
            available=available,
            reason=reason,
            supports_html=True,
            supports_subject=False,
            supports_image=True,
            supports_buttons=True,
            max_text_length=TELEGRAM_MESSAGE_MAX_LENGTH,
            supports_push_preview=False,
            destination_level="user",
        )

    async def resolve_recipients(
        self,
        session: AsyncSession,
        *,
        user_ids: list[int],
        exclude_blocked_telegram: bool,
    ) -> list[BroadcastRecipient]:
        recipients = await user_dal.get_telegram_recipients_for_broadcast(
            session,
            user_ids,
            exclude_blocked=exclude_blocked_telegram,
        )
        return [
            BroadcastRecipient(
                user_id=int(user_id),
                destination=str(chat_id),
                destination_key="default",
            )
            for user_id, chat_id in recipients
        ]

    async def dispatch(self, context: BroadcastDispatchContext) -> BroadcastChannelDispatch:
        if context.queue_manager is None:
            raise RuntimeError("queue_unavailable")
        photo = await prepare_telegram_photo(context.image) if context.image is not None else None
        queued = 0
        failed = 0
        for message in context.messages:
            if len(message.text) > TELEGRAM_MESSAGE_MAX_LENGTH:
                failed += 1
                await context.on_result(message.delivery_id, False, "message_too_long")
                continue
            await self._queue_telegram(context, message, image=photo)
            queued += 1
        return BroadcastChannelDispatch(queued=queued, failed=failed)

    async def _queue_telegram(
        self,
        context: BroadcastDispatchContext,
        message: BroadcastChannelMessage,
        *,
        image: BufferedInputFile | None = None,
    ) -> None:
        queue_manager: MessageQueueManager | None = context.queue_manager
        if queue_manager is None:
            raise RuntimeError("queue_unavailable")
        delivery_id = message.delivery_id

        remaining = 1
        failure: str | None = None
        telegram_status_recorded = False
        queued_at = datetime.now(UTC)

        async def finish_part(error: Exception | None = None) -> None:
            nonlocal remaining, failure
            if error is not None and failure is None:
                failure = str(error)
            remaining -= 1
            if remaining == 0:
                await context.on_result(delivery_id, failure is None, failure)

        async def on_success(_result: Any) -> None:
            try:
                async with context.session_factory() as session:
                    await mark_telegram_notifications_status(
                        session,
                        message.user_id,
                        "enabled",
                        telegram_id=int(message.destination),
                        checked_at=delivery_attempted_at.get() or queued_at,
                    )
                    await session.commit()
            except Exception:
                logger.exception("metric telegram_delivery_state_write_errors_total=1")
            await finish_part()

        async def on_failure(exc: Exception) -> None:
            nonlocal telegram_status_recorded
            if not telegram_status_recorded:
                telegram_status = await record_telegram_notification_failure(
                    context.session_factory,
                    message.user_id,
                    exc,
                    attempted_at=delivery_attempted_at.get() or queued_at,
                    telegram_id=int(message.destination),
                )
                telegram_status_recorded = telegram_status is not None
            await finish_part(exc)

        chat_id = int(message.destination)
        markup = telegram_markup_for_buttons(message.buttons)
        if image is not None:
            photo_kwargs: dict[str, Any] = {
                "photo": image,
                "reply_markup": markup,
                "callback": on_success,
                "error_callback": on_failure,
            }
            if message.text:
                photo_kwargs["caption"] = message.text
                photo_kwargs["parse_mode"] = "HTML"
            await queue_manager.send_photo(chat_id, **photo_kwargs)
        elif message.text:
            await queue_manager.send_message(
                chat_id,
                text=message.text,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=markup,
                callback=on_success,
                error_callback=on_failure,
            )
        await context.on_queued(delivery_id)


class EmailBroadcastChannelProvider(BroadcastChannelProvider):
    """Deliver broadcasts through the existing background SMTP scheduler."""

    name = "email"

    def describe(self, settings: Settings) -> BroadcastChannelDescriptor:
        available = bool(settings.smtp_delivery_configured)
        return BroadcastChannelDescriptor(
            name=self.name,
            label_key="broadcast_channel_email",
            fallback_label="Email",
            order=20,
            available=available,
            reason="" if available else "email_not_configured",
            supports_html=True,
            supports_subject=True,
            supports_image=True,
            supports_buttons=True,
            # Email bodies are rendered from Telegram HTML and delivered by
            # SMTP; the Telegram 4096-character limit does not apply.
            max_text_length=0,
            supports_push_preview=False,
            destination_level="user",
        )

    async def resolve_recipients(
        self,
        session: AsyncSession,
        *,
        user_ids: list[int],
        exclude_blocked_telegram: bool,
    ) -> list[BroadcastRecipient]:
        del exclude_blocked_telegram
        recipients = await user_dal.get_email_recipients_for_broadcast(session, user_ids)
        return [
            BroadcastRecipient(
                user_id=int(user_id),
                destination=str(email),
                destination_key="default",
                language_code=language,
            )
            for user_id, email, language in recipients
        ]

    async def dispatch(self, context: BroadcastDispatchContext) -> BroadcastChannelDispatch:
        recipients: list[BroadcastEmailRecipient] = []
        failed = 0
        for message in context.messages:
            subject = message.subject or None
            if subject and len(subject) > EMAIL_SUBJECT_MAX_LENGTH:
                failed += 1
                await context.on_result(message.delivery_id, False, "email_subject_too_long")
                continue
            recipients.append(
                BroadcastEmailRecipient(
                    user_id=message.user_id,
                    email=message.destination,
                    language_code=message.language_code,
                    message_text=message.text,
                    subject=subject,
                    buttons=email_links_for_buttons(message.buttons),
                    delivery_id=message.delivery_id,
                )
            )

        for recipient in recipients:
            if recipient.delivery_id is not None:
                await context.on_queued(recipient.delivery_id)

        image = await context.image.email_inline() if context.image is not None else None

        async def on_result(
            recipient: BroadcastEmailRecipient,
            success: bool,
            error: str | None,
        ) -> None:
            if recipient.delivery_id is not None:
                await context.on_result(recipient.delivery_id, success, error)

        broadcast = context.broadcast
        queued = broadcast_email_service.schedule_broadcast_emails(
            settings=context.settings,
            i18n=context.i18n,
            recipients=recipients,
            subject="",
            message_text="",
            session_factory=context.session_factory,
            actor_id=int(broadcast.created_by_admin_id) if broadcast.created_by_admin_id else None,
            target=str(broadcast.target),
            on_result=on_result,
            image=image,
        )
        return BroadcastChannelDispatch(queued=queued, failed=failed)


_TELEGRAM_PROVIDER = TelegramBroadcastChannelProvider()
_EMAIL_PROVIDER = EmailBroadcastChannelProvider()


def builtin_broadcast_channel_providers() -> tuple[BroadcastChannelProvider, ...]:
    """Providers Core registers lazily when the broadcast registry is used."""

    return (_TELEGRAM_PROVIDER, _EMAIL_PROVIDER)
