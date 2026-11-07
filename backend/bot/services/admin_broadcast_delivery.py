"""Durable delivery for immediate and scheduled admin broadcasts.

The service owns the shared work every channel needs: resolving the audience
once, snapshotting recipients as durable delivery rows, personalizing text,
subjects and buttons, preparing images and updating per-channel stats. The
Transport-specific work lives in registered
:mod:`bot.services.broadcast_channels` providers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any

from sqlalchemy.orm import sessionmaker

from bot.middlewares.i18n import JsonI18n
from bot.services.audience_segmentation import AudienceSegmentationService
from bot.services.broadcast_channels import (
    EMAIL_SUBJECT_MAX_LENGTH,
    BroadcastChannelDescriptor,
    BroadcastChannelDispatch,
    BroadcastChannelMessage,
    BroadcastChannelProvider,
    BroadcastDispatchContext,
    descriptor_hard_text_limit,
    get_broadcast_channel_provider,
)
from bot.services.broadcast_personalization import (
    BroadcastUserContext,
    known_shortcodes,
    load_broadcast_contexts,
    render_broadcast_text,
    telegram_html_error,
)
from bot.services.message_composition import (
    MessageButton,
    MessageButtonInput,
    resolve_localized_text,
    resolve_message_buttons,
)
from bot.services.message_image_service import StoredMessageImage, load_message_image
from bot.utils.message_queue import MessageQueueManager
from config.settings import Settings
from db.broadcast_models import AdminBroadcast, AdminBroadcastDelivery
from db.dal import broadcast_dal, user_dal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BroadcastDispatchResult:
    queued: int
    failed: int
    email_queued: int
    channels: list[str]
    channel_statuses: dict[str, dict[str, Any]] = field(default_factory=dict)


class AdminBroadcastDeliveryService:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: sessionmaker,
        i18n: JsonI18n,
        audience_service: AudienceSegmentationService,
        queue_manager: MessageQueueManager | None,
        bot_username: str | None,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.i18n = i18n
        self.audience_service = audience_service
        self.queue_manager = queue_manager
        self.bot_username = bot_username

    async def dispatch(
        self,
        broadcast_id: int,
        *,
        user_ids: list[int] | None = None,
    ) -> BroadcastDispatchResult:
        async with self.session_factory() as session:
            broadcast = await broadcast_dal.begin_broadcast(session, broadcast_id)
        if broadcast is None:
            return BroadcastDispatchResult(0, 0, 0, [], {})

        channels = [str(value) for value in list(broadcast.channels or [])]
        numeric_broadcast_id = int(broadcast.broadcast_id)
        try:
            resolved_user_ids = user_ids
            if resolved_user_ids is None:
                resolved_user_ids = [
                    int(user_id)
                    for user_id in await self.audience_service.resolve_user_ids(
                        str(broadcast.target)
                    )
                ]
            # Image loading is the one shared pre-queue step: a missing image is
            # a hard campaign error before any channel starts.
            stored_image = await self._load_broadcast_image(broadcast)
        except Exception as exc:
            await self._fail_broadcast(broadcast_id, str(exc))
            raise

        queued = 0
        failed = 0
        email_queued = 0
        prepared_any = False
        channel_statuses: dict[str, dict[str, Any]] = {}
        prepared_channels: list[
            tuple[
                str,
                BroadcastChannelDescriptor,
                BroadcastChannelProvider,
                list[AdminBroadcastDelivery],
            ]
        ] = []

        # Phase 1: persist every selected channel's rows before any transport
        # starts. A slow or empty channel must not let an early stats refresh
        # finalize the campaign while another channel's rows are still missing.
        for channel in channels:
            try:
                provider = get_broadcast_channel_provider(channel)
                if provider is None:
                    channel_failed = await self._fail_channel(
                        numeric_broadcast_id,
                        channel,
                        "provider_absent",
                        outcome="provider_absent",
                        finalize=False,
                    )
                    failed += channel_failed
                    channel_statuses[channel] = {
                        "queued": 0,
                        "failed": channel_failed,
                        "outcome": "provider_absent",
                    }
                    continue

                descriptor = provider.describe(self.settings)
                if not descriptor.available:
                    reason = descriptor.reason or "provider_not_configured"
                    channel_failed = await self._fail_channel(
                        numeric_broadcast_id,
                        channel,
                        reason,
                        outcome="provider_not_configured",
                        finalize=False,
                    )
                    failed += channel_failed
                    channel_statuses[channel] = {
                        "queued": 0,
                        "failed": channel_failed,
                        "outcome": "provider_not_configured",
                        "reason": descriptor.reason,
                    }
                    continue

                deliveries = await self._prepare_channel_deliveries(
                    broadcast,
                    resolved_user_ids,
                    channel,
                    provider,
                )
                prepared_any = prepared_any or bool(deliveries)
                if not deliveries:
                    channel_statuses[channel] = {"queued": 0, "failed": 0, "outcome": ""}
                    continue
                prepared_channels.append((channel, descriptor, provider, deliveries))
            except Exception as exc:
                logger.exception("Broadcast channel %s preparation failed", channel)
                channel_failed = await self._fail_channel(
                    numeric_broadcast_id,
                    channel,
                    str(exc) or "provider_error",
                    outcome="provider_error",
                    finalize=False,
                )
                failed += channel_failed
                channel_statuses[channel] = {
                    "queued": 0,
                    "failed": channel_failed,
                    "outcome": "provider_error",
                    "reason": str(exc) or "provider_error",
                }

        # Phase 2: dispatch the persisted rows. Provider failures stay isolated
        # to their channel; a campaign only finalizes after every row is
        # terminal or deliberately left queued for its own callback/recovery.
        for channel, descriptor, provider, deliveries in prepared_channels:
            try:
                result, invalid = await self._dispatch_channel(
                    broadcast,
                    deliveries,
                    descriptor,
                    provider,
                    stored_image,
                )
                queued += 0 if channel == "email" else result.queued
                failed += invalid + result.failed
                if channel == "email":
                    email_queued += result.queued
                channel_statuses[channel] = {
                    "queued": result.queued,
                    "failed": invalid + result.failed,
                    "outcome": result.outcome,
                }
            except Exception as exc:
                logger.exception("Broadcast channel %s failed", channel)
                channel_failed = await self._fail_channel(
                    numeric_broadcast_id,
                    channel,
                    str(exc) or "provider_error",
                    outcome="provider_error",
                    finalize=False,
                )
                failed += channel_failed
                channel_statuses[channel] = {
                    "queued": 0,
                    "failed": channel_failed,
                    "outcome": "provider_error",
                    "reason": str(exc) or "provider_error",
                }

        try:
            async with self.session_factory() as session:
                await broadcast_dal.refresh_broadcast_stats(session, numeric_broadcast_id)
            if not prepared_any:
                async with self.session_factory() as session:
                    await broadcast_dal.finish_empty_broadcast(session, numeric_broadcast_id)
        except Exception as exc:
            await self._fail_broadcast(numeric_broadcast_id, str(exc))
            raise

        return BroadcastDispatchResult(
            queued,
            failed,
            email_queued,
            channels,
            channel_statuses,
        )

    async def _prepare_deliveries(
        self,
        broadcast: AdminBroadcast,
        user_ids: list[int],
        channels: list[str],
    ) -> list[AdminBroadcastDelivery]:
        deliveries: list[AdminBroadcastDelivery] = []
        for channel in channels:
            provider = get_broadcast_channel_provider(channel)
            if provider is None:
                continue
            deliveries.extend(
                await self._prepare_channel_deliveries(
                    broadcast,
                    user_ids,
                    channel,
                    provider,
                )
            )
        return deliveries

    async def _prepare_channel_deliveries(
        self,
        broadcast: AdminBroadcast,
        user_ids: list[int],
        channel: str,
        provider: BroadcastChannelProvider,
    ) -> list[AdminBroadcastDelivery]:
        async with self.session_factory() as session:
            recipients = await provider.resolve_recipients(
                session,
                user_ids=user_ids,
                exclude_blocked_telegram=bool(
                    getattr(broadcast, "exclude_blocked_telegram", False)
                ),
            )
            recipient_user_ids = list(
                dict.fromkeys(int(recipient.user_id) for recipient in recipients)
            )
            languages = (
                await user_dal.get_language_codes_for_broadcast(session, recipient_user_ids)
                if recipient_user_ids
                else {}
            )
            payloads = [
                {
                    "user_id": int(recipient.user_id),
                    "channel": channel,
                    "destination": str(recipient.destination),
                    "destination_key": str(recipient.destination_key or "default"),
                    "language_code": recipient.language_code
                    or languages.get(int(recipient.user_id)),
                }
                for recipient in recipients
            ]
            deliveries = await broadcast_dal.add_deliveries(session, broadcast, payloads)
        return [delivery for delivery in deliveries if str(delivery.channel) == channel]

    async def _queue_deliveries(
        self,
        broadcast: AdminBroadcast,
        deliveries: list[AdminBroadcastDelivery],
        user_ids: list[int],
        channels: list[str],
    ) -> BroadcastDispatchResult:
        del user_ids
        stored_image = await self._load_broadcast_image(broadcast)
        numeric_broadcast_id = int(broadcast.broadcast_id)
        queued = 0
        failed = 0
        email_queued = 0
        channel_statuses: dict[str, dict[str, Any]] = {}
        for channel in channels:
            channel_deliveries = [
                delivery for delivery in deliveries if str(delivery.channel) == channel
            ]
            if not channel_deliveries:
                continue
            provider = get_broadcast_channel_provider(channel)
            if provider is None:
                channel_failed = await self._fail_channel(
                    numeric_broadcast_id,
                    channel,
                    "provider_absent",
                    outcome="provider_absent",
                    finalize=False,
                )
                failed += channel_failed
                channel_statuses[channel] = {
                    "queued": 0,
                    "failed": channel_failed,
                    "outcome": "provider_absent",
                }
                continue
            descriptor = provider.describe(self.settings)
            try:
                result, invalid = await self._dispatch_channel(
                    broadcast,
                    channel_deliveries,
                    descriptor,
                    provider,
                    stored_image,
                )
            except Exception as exc:
                logger.exception("Broadcast channel %s failed", channel)
                channel_failed = await self._fail_channel(
                    numeric_broadcast_id,
                    channel,
                    str(exc) or "provider_error",
                    outcome="provider_error",
                    finalize=False,
                )
                failed += channel_failed
                channel_statuses[channel] = {
                    "queued": 0,
                    "failed": channel_failed,
                    "outcome": "provider_error",
                    "reason": str(exc) or "provider_error",
                }
                continue
            queued += 0 if channel == "email" else result.queued
            failed += invalid + result.failed
            if channel == "email":
                email_queued += result.queued
            channel_statuses[channel] = {
                "queued": result.queued,
                "failed": invalid + result.failed,
                "outcome": result.outcome,
            }

        async with self.session_factory() as session:
            await broadcast_dal.refresh_broadcast_stats(session, numeric_broadcast_id)
        return BroadcastDispatchResult(
            queued,
            failed,
            email_queued,
            channels,
            channel_statuses,
        )

    async def _dispatch_channel(
        self,
        broadcast: AdminBroadcast,
        deliveries: list[AdminBroadcastDelivery],
        descriptor: BroadcastChannelDescriptor,
        provider: BroadcastChannelProvider,
        stored_image: StoredMessageImage | None,
    ) -> tuple[BroadcastChannelDispatch, int]:
        messages, invalid = await self._render_channel_messages(
            broadcast,
            deliveries,
            descriptor,
        )
        context = BroadcastDispatchContext(
            broadcast=broadcast,
            deliveries=deliveries,
            messages=messages,
            settings=self.settings,
            i18n=self.i18n,
            bot_username=self.bot_username,
            session_factory=self.session_factory,
            queue_manager=self.queue_manager,
            audience_service=self.audience_service,
            image=stored_image if descriptor.supports_image else None,
            on_queued=self._mark_queued,
            on_result=self._mark_result,
        )
        result = await provider.dispatch(context)
        return result, invalid

    async def _render_channel_messages(
        self,
        broadcast: AdminBroadcast,
        deliveries: list[AdminBroadcastDelivery],
        descriptor: BroadcastChannelDescriptor,
    ) -> tuple[list[BroadcastChannelMessage], int]:
        texts = {str(key): str(value) for key, value in dict(broadcast.texts or {}).items()}
        subjects = {
            str(key): str(value) for key, value in dict(broadcast.email_subjects or {}).items()
        }
        button_inputs = [
            MessageButtonInput(
                kind=str(item.get("kind") or "url"),
                label=str(item.get("label") or ""),
                url=str(item.get("url") or ""),
                promo_code=str(item.get("promo_code") or ""),
                section=str(item.get("section") or ""),
                platform=str(item.get("platform") or ""),
                app=str(item.get("app") or ""),
                labels={
                    str(key): str(value) for key, value in dict(item.get("labels") or {}).items()
                },
                icon_custom_emoji_id=str(item["icon_custom_emoji_id"])
                if item.get("icon_custom_emoji_id")
                else None,
                icon_emoji=str(item.get("icon_emoji") or ""),
            )
            for item in list(broadcast.buttons or [])
            if isinstance(item, dict)
        ]
        button_shortcodes = set().union(*(known_shortcodes(button.url) for button in button_inputs))
        authored_variants = [
            *texts.values(),
            *subjects.values(),
            *(button.url for button in button_inputs),
        ]
        needed = set().union(*(known_shortcodes(value) for value in authored_variants))
        contexts: dict[int, BroadcastUserContext] = {}
        if needed:
            user_ids = list(dict.fromkeys(int(delivery.user_id) for delivery in deliveries))
            async with self.session_factory() as session:
                contexts = await load_broadcast_contexts(
                    session,
                    self.settings,
                    user_ids,
                    needed,
                    self.audience_service.panel_service,
                )

        button_cache: dict[tuple[str, int | None], list[MessageButton]] = {}

        def language_for(delivery: AdminBroadcastDelivery) -> str:
            context = contexts.get(int(delivery.user_id))
            return str(
                (context.language_code if context else None)
                or delivery.language_code
                or self.settings.DEFAULT_LANGUAGE
            )

        def buttons_for(language: str, user_id: int) -> list[MessageButton]:
            cache_key = (language, user_id if button_shortcodes else None)
            if cache_key not in button_cache:
                context = contexts.get(user_id)
                personalized_inputs = [
                    replace(
                        button,
                        url=render_broadcast_text(
                            button.url,
                            context,
                            lang=language,
                            i18n=self.i18n,
                            settings=self.settings,
                            bot_username=self.bot_username,
                            escape=False,
                        ),
                    )
                    if known_shortcodes(button.url)
                    else button
                    for button in button_inputs
                ]
                button_cache[cache_key] = resolve_message_buttons(
                    personalized_inputs,
                    mini_app_url=self.settings.SUBSCRIPTION_MINI_APP_URL,
                    bot_username=self.bot_username,
                    language=language,
                    translate=lambda lang, key: self.i18n.gettext(lang, key),
                    default_language=self.settings.DEFAULT_LANGUAGE,
                )
            return button_cache[cache_key]

        messages: list[BroadcastChannelMessage] = []
        failed = 0
        for delivery in deliveries:
            language = language_for(delivery)
            template = resolve_localized_text(
                texts,
                language=language,
                default_language=self.settings.DEFAULT_LANGUAGE,
            )
            rendered = (
                render_broadcast_text(
                    template,
                    contexts.get(int(delivery.user_id)),
                    lang=language,
                    i18n=self.i18n,
                    settings=self.settings,
                    bot_username=self.bot_username,
                    escape=True,
                )
                if needed
                else template
            )
            text_limit = descriptor_hard_text_limit(descriptor)
            if text_limit > 0 and len(rendered) > text_limit:
                failed += 1
                await self._mark_result(
                    int(delivery.delivery_id),
                    success=False,
                    error="message_too_long",
                )
                continue
            if descriptor.supports_html:
                html_error = telegram_html_error(rendered)
                if html_error:
                    failed += 1
                    await self._mark_result(
                        int(delivery.delivery_id),
                        success=False,
                        error="invalid_telegram_html",
                    )
                    continue

            subject: str | None = None
            if descriptor.supports_subject:
                subject_template = resolve_localized_text(
                    subjects,
                    language=language,
                    default_language=self.settings.DEFAULT_LANGUAGE,
                )
                rendered_subject = (
                    render_broadcast_text(
                        subject_template,
                        contexts.get(int(delivery.user_id)),
                        lang=language,
                        i18n=self.i18n,
                        settings=self.settings,
                        bot_username=self.bot_username,
                        escape=False,
                    )
                    if needed and subject_template
                    else subject_template
                )
                if len(rendered_subject) > EMAIL_SUBJECT_MAX_LENGTH:
                    failed += 1
                    await self._mark_result(
                        int(delivery.delivery_id),
                        success=False,
                        error="email_subject_too_long",
                    )
                    continue
                subject = rendered_subject or None

            messages.append(
                BroadcastChannelMessage(
                    delivery_id=int(delivery.delivery_id),
                    user_id=int(delivery.user_id),
                    destination=str(delivery.destination),
                    destination_key=str(
                        getattr(delivery, "destination_key", "default") or "default"
                    ),
                    language_code=delivery.language_code,
                    text=rendered,
                    subject=subject,
                    buttons=(
                        buttons_for(language, int(delivery.user_id))
                        if descriptor.supports_buttons
                        else []
                    ),
                )
            )
        return messages, failed

    async def _load_broadcast_image(
        self,
        broadcast: AdminBroadcast,
    ) -> StoredMessageImage | None:
        image_id = getattr(broadcast, "image_id", None)
        if not image_id:
            return None
        async with self.session_factory() as session:
            stored_image = await load_message_image(session, str(image_id))
        if stored_image is None or not stored_image.path.is_file():
            raise RuntimeError("image_missing")
        return stored_image

    async def _mark_queued(self, delivery_id: int) -> None:
        async with self.session_factory() as session:
            await broadcast_dal.mark_delivery_queued(session, delivery_id)

    async def _mark_result(
        self,
        delivery_id: int,
        success: bool,
        error: str | None = None,
    ) -> None:
        async with self.session_factory() as session:
            await broadcast_dal.mark_delivery_result(
                session, delivery_id, success=success, error=error
            )

    async def _fail_channel(
        self,
        broadcast_id: int,
        channel: str,
        error: str,
        *,
        outcome: str = "provider_error",
        finalize: bool = True,
    ) -> int:
        async with self.session_factory() as session:
            return await broadcast_dal.fail_channel_deliveries(
                session,
                broadcast_id,
                channel,
                error,
                outcome=outcome,
                finalize=finalize,
            )

    async def _fail_broadcast(self, broadcast_id: int, error: str) -> None:
        async with self.session_factory() as session:
            await broadcast_dal.fail_broadcast(session, broadcast_id, error)
