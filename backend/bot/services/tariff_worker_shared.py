"""Shared helpers for tariff traffic workers."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from inspect import iscoroutine
from types import SimpleNamespace
from typing import Any, Protocol

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from bot.services.notification_channels import (
    ChannelOutcomeStatus,
    NotificationChannelOutcome,
    NotificationChannelProvider,
    NotificationOccurrence,
    fanout_push_occurrence,
    get_notification_channel_provider,
    occurrence_id_for,
    plaintext_for_push,
    resolve_channel_destinations,
)
from bot.services.user_notification_policy import (
    UserNotificationCategory,
    telegram_recipient,
    user_notification_channel_allowed,
    user_notification_channel_selected,
    user_notification_delivery_plan,
)
from config.settings import Settings
from db.dal import message_log_dal, tariff_dal, user_dal

logger = logging.getLogger(__name__)

PREMIUM_WARNING_LEVEL_OFFSET = 1000
# Single warning per premium billing period when usage reached or exceeded the quota.
PREMIUM_WARNING_DEPLETED_LEVEL = PREMIUM_WARNING_LEVEL_OFFSET + 100

# Process active subscriptions in chunks and prefetch panel data concurrently
# to avoid an N+1 serial chain to the Remnawave panel each tick.
TARIFF_WORKER_BATCH_SIZE = 50
TARIFF_WORKER_PANEL_CONCURRENCY = 10
TARIFF_WORKER_BULK_PANEL_FETCH_THRESHOLD = 50
TARIFF_WORKER_SQUAD_CONFIRMATION_CACHE_TTL_SECONDS = 900
TARIFF_WORKER_DB_RETRY_ATTEMPTS = 3
TARIFF_WORKER_DB_RETRY_BASE_SLEEP_SECONDS = 0.5
POSTGRES_RETRYABLE_SQLSTATES = {"40001", "40P01"}
POSTGRES_RETRYABLE_ERROR_NAMES = {"DeadlockDetectedError", "SerializationError"}


PANEL_LIMIT_DRIFT_CACHE_PARTS = ("panel-limit-drift", "subscriptions")
PANEL_LIMIT_DRIFT_CACHE_TTL_SECONDS = 24 * 60 * 60


@dataclass
class PanelLimitPatchState:
    """Bookkeeping for panel limit writes that keep coming back."""

    signature: str
    attempts: int
    blocked_until: float


async def record_panel_limit_drift(
    settings: Settings,
    *,
    panel_uuid: str,
    subscription_id: int,
    desired: str,
    observed: str,
) -> None:
    """Remember a panel user whose limits are rewritten by somebody else.

    The admin health panel reads this: without it the operator only learns about
    a second writer from a user complaint.
    """
    if not settings.REDIS_URL:
        return
    from bot.infra.redis import cache_get_json, cache_set_json, redis_key

    key = redis_key(settings, *PANEL_LIMIT_DRIFT_CACHE_PARTS)
    stored = await cache_get_json(settings, key)
    record: dict[str, Any] = stored if isinstance(stored, dict) else {}
    record[panel_uuid] = {
        "subscription_id": subscription_id,
        "desired": desired,
        "observed": observed,
        "last_seen_at": datetime.now(UTC).isoformat(),
    }
    await cache_set_json(settings, key, record, PANEL_LIMIT_DRIFT_CACHE_TTL_SECONDS)


def canonical_subscriptions_per_panel_user(
    subscriptions: list[Any],
    *,
    logger: logging.Logger,
) -> list[Any]:
    """Keep one active subscription per panel user.

    Activation deactivates the other active subscriptions of a panel user, so a
    second active row is data drift. Left alone, each row pushes its own limits
    and squads to the same panel user on every tick: the values flip back and
    forth and Remnawave emits a ``user.modified`` webhook for each write.
    """
    by_panel_user: dict[str, Any] = {}
    duplicates: dict[str, list[Any]] = {}
    unbound: list[Any] = []
    for sub in subscriptions:
        panel_uuid = str(getattr(sub, "panel_user_uuid", "") or "")
        if not panel_uuid:
            unbound.append(sub)
            continue
        current = by_panel_user.get(panel_uuid)
        if current is None:
            by_panel_user[panel_uuid] = sub
            continue
        keep_new = _subscription_rank(sub) > _subscription_rank(current)
        winner, loser = (sub, current) if keep_new else (current, sub)
        by_panel_user[panel_uuid] = winner
        duplicates.setdefault(panel_uuid, []).append(loser)

    for panel_uuid, skipped in duplicates.items():
        logger.warning(
            "TariffTrafficWorker: panel user %s has %s active subscriptions; syncing only %s and "
            "skipping %s. Deactivate the stale rows: competing rows rewrite each other on the "
            "panel every tick.",
            panel_uuid,
            len(skipped) + 1,
            getattr(by_panel_user[panel_uuid], "subscription_id", None),
            ", ".join(str(getattr(sub, "subscription_id", None)) for sub in skipped),
        )
    return [*by_panel_user.values(), *unbound]


def _subscription_rank(sub: Any) -> tuple[float, int]:
    end_date = getattr(sub, "end_date", None)
    timestamp = end_date.timestamp() if isinstance(end_date, datetime) else 0.0
    return timestamp, int(getattr(sub, "subscription_id", 0) or 0)


def fmt_bytes(value: int) -> str:
    size = float(max(0, int(value or 0)))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"


async def resolve_flexible_limit_baseline(
    session: AsyncSession,
    *,
    subscription_id: int,
    kind: str,
    at: datetime,
    active_baseline: int | None,
    stored_baseline: int | None,
    default_baseline: int,
    preserve_without_history: bool,
) -> int:
    """Resolve a flexible quota without applying a future window early.

    A scheduled renewal row is history in the broad sense, but it is not an
    expired entitlement. Until its ``valid_from`` arrives, the current stored
    baseline must remain authoritative. Once any flexible window has started
    and no row is active anymore, the quota returns to the tariff default.
    """
    if active_baseline is not None:
        return max(0, int(active_baseline))

    history_start = await tariff_dal.get_flexible_traffic_limit_history_start(
        session,
        subscription_id=subscription_id,
        kind=kind,
    )
    if history_start is None:
        resolved = stored_baseline if preserve_without_history else default_baseline
    else:
        normalized_start = (
            history_start.replace(tzinfo=UTC)
            if history_start.tzinfo is None
            else history_start.astimezone(UTC)
        )
        normalized_at = at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)
        resolved = stored_baseline if normalized_start > normalized_at else default_baseline
    return max(0, int(resolved or default_baseline or 0))


class MessageDeliveryLogger(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        *,
        target_user_id: int,
        event_type: str,
        channel: str,
        recipient: str,
        content: str,
    ) -> None: ...


class TrafficWarningEmailSender(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        subject_key: str,
        message_text: str,
        kind: str,
        warning_key: str,
        audit_content: str,
    ) -> None: ...


def warning_period_identity(period_start_at: datetime | None) -> str | None:
    """Quota-generation identity scoping a warning push occurrence to one period.

    Warning markers are recorded per billing period, but the push occurrence
    used to share no period component: a delivery audit row from period A
    matched the same warning in period B, so B's first failed push looked
    already delivered on retry and was never resent. Scoping the occurrence
    (and therefore the per-destination audit identity) to the period keeps
    each quota generation independently retryable (same boundary as the
    subscription lifecycle period fix).
    """

    if period_start_at is None:
        return None
    if period_start_at.tzinfo is None:
        normalized = period_start_at.replace(tzinfo=UTC)
    else:
        normalized = period_start_at.astimezone(UTC)
    return normalized.isoformat()


def warning_push_occurrence_id(
    subscription_id_or_user_id: int | None,
    warning_key: str,
    level: int | None,
    kind: str,
    period: str | None = None,
) -> str:
    """Stable push occurrence identity for one traffic warning level."""

    return occurrence_id_for(
        "traffic-warning",
        subscription_id_or_user_id,
        warning_key,
        level,
        kind,
        period,
    )


def warning_push_audit_content(audit_content: str, occurrence_id: str, destination: str) -> str:
    """Per-destination push audit content scoped to one warning occurrence.

    The contiguous ``occurrence_id=… destination=…`` marker lets a later tick
    tell delivered destinations apart from failed ones with a single audit
    lookup, so a partial device failure stays retryable per destination.
    """

    return f"{audit_content} occurrence_id={occurrence_id} destination={destination}"


async def warning_push_destination_delivered(
    session: AsyncSession,
    *,
    user_id: int,
    event_type: str,
    occurrence_id: str,
    destination: str,
) -> bool:
    """Whether one warning push destination already has a delivery audit row."""

    try:
        return await message_log_dal.has_target_event_content(
            session,
            target_user_id=user_id,
            event_type=event_type,
            content_fragment=warning_push_audit_content("", occurrence_id, destination).strip(),
        )
    except Exception:
        logger.exception(
            "Failed to check warning push delivery audit for user %s occurrence %s.",
            user_id,
            occurrence_id,
        )
        # Fail closed: a DB blip must not duplicate an already delivered push;
        # the next tick rechecks.
        return True


class _PendingOnlyPushProvider:
    """Restrict one warning push fan-out to destinations still pending delivery.

    The retry path resolves the pending set from per-destination audit rows;
    without this restriction a retry after the dedup TTL expiry would resend
    already audited destinations instead of only the pending ones.
    """

    def __init__(self, inner: NotificationChannelProvider, allowed: tuple[str, ...]) -> None:
        self._inner = inner
        self._allowed = frozenset(allowed)

    @property
    def name(self) -> str:
        return str(self._inner.name)

    def is_configured(self, settings: Any) -> bool:
        return bool(self._inner.is_configured(settings))

    def destinations_for_user(self, settings: Any, user: Any) -> tuple[str, ...]:
        destinations = tuple(self._inner.destinations_for_user(settings, user) or ())
        return tuple(destination for destination in destinations if destination in self._allowed)

    async def resolve_destinations(self, settings: Any, user: Any) -> tuple[str, ...]:
        destinations = await resolve_channel_destinations(self._inner, settings, user)
        return tuple(destination for destination in destinations if destination in self._allowed)

    async def deliver(
        self,
        *,
        settings: Any,
        user: Any,
        occurrence: NotificationOccurrence,
        destination: str,
    ) -> NotificationChannelOutcome:
        return await self._inner.deliver(
            settings=settings,
            user=user,
            occurrence=occurrence,
            destination=destination,
        )


async def deliver_warning_push_only(
    session: AsyncSession,
    *,
    settings: Settings,
    user: Any,
    user_id: int,
    subscription_id: int | None,
    text: str,
    audit_content: str,
    audit_logger: MessageDeliveryLogger,
    warning_key: str,
    level: int | None,
    kind: str,
    push_title: str | None,
    event_type: str = "push_traffic_warning_sent",
    occurrence_id: str | None = None,
    period: str | None = None,
    only_destinations: tuple[str, ...] | None = None,
    logger: logging.Logger,
) -> bool:
    """Fan out a traffic warning push without touching Telegram/email.

    Returns ``True`` only when every provider destination confirmed delivery;
    any failure stays retryable via the released fanout claims. Delivered
    destinations are audited per destination so a later tick can retry exactly
    the pending ones despite the already recorded warning marker.
    """

    resolved_occurrence_id = occurrence_id or warning_push_occurrence_id(
        subscription_id if subscription_id is not None else user_id,
        warning_key,
        level,
        kind,
        period,
    )
    occurrence = NotificationOccurrence(
        occurrence_id=resolved_occurrence_id,
        category=UserNotificationCategory.TRAFFIC,
        user_id=int(getattr(user, "user_id", 0) or user_id),
        push_title=push_title or plaintext_for_push(text)[:120],
        push_body=plaintext_for_push(text),
        push_url=str(settings.SUBSCRIPTION_MINI_APP_URL or "").strip() or None,
    )
    try:
        provider: NotificationChannelProvider | None = None
        if only_destinations is not None:
            inner_provider = get_notification_channel_provider("push")
            if inner_provider is not None:
                provider = _PendingOnlyPushProvider(inner_provider, tuple(only_destinations))
        outcomes = await fanout_push_occurrence(
            settings=settings,
            user=user,
            occurrence=occurrence,
            provider=provider,
        )
    except Exception:
        logger.exception("Failed to fan out traffic warning push for user %s", user_id)
        return False
    delivered = [outcome.destination for outcome in outcomes if outcome.delivered]
    for destination in delivered:
        try:
            await audit_logger(
                session,
                target_user_id=user_id,
                event_type=event_type,
                channel="push",
                recipient=destination,
                content=warning_push_audit_content(
                    audit_content, occurrence.occurrence_id, destination
                ),
            )
        except Exception:
            logger.exception("Failed to audit traffic warning push for user %s", user_id)
    return bool(delivered) and not any(
        outcome.status == ChannelOutcomeStatus.FAILED for outcome in outcomes
    )


async def retry_warning_push_if_pending(
    session: AsyncSession,
    *,
    settings: Settings,
    user_id: int,
    subscription_id: int | None,
    text: str,
    audit_content: str,
    audit_logger: MessageDeliveryLogger,
    warning_key: str,
    level: int | None,
    kind: str,
    push_title: str | None,
    event_type: str = "push_traffic_warning_sent",
    occurrence_id: str | None = None,
    period: str | None = None,
    logger: logging.Logger,
) -> None:
    """Retry a warning push whose DB warning marker already exists.

    Warning markers are recorded before delivery, so a later tick skips the
    warning even when the push never delivered. Pending delivery is tracked
    separately via per-destination audit rows: destinations without an audit
    row are retried, delivered ones stay suppressed by fanout dedup.
    """

    try:
        user = await user_dal.get_user_by_id(session, user_id)
    except Exception:
        logger.exception("Failed to load user %s for traffic warning push retry", user_id)
        return
    if iscoroutine(user):
        user.close()
        return
    if user is None:
        return
    plan = user_notification_delivery_plan(
        settings,
        UserNotificationCategory.TRAFFIC,
        user,
        telegram_available=False,
    )
    if not plan.push:
        return
    resolved_occurrence_id = occurrence_id or warning_push_occurrence_id(
        subscription_id if subscription_id is not None else user_id,
        warning_key,
        level,
        kind,
        period,
    )
    provider = get_notification_channel_provider("push")
    pending: list[str] | None = None
    if provider is not None:
        try:
            destinations = await resolve_channel_destinations(provider, settings, user)
        except Exception:
            logger.exception(
                "Failed to resolve push destinations for warning retry for user %s", user_id
            )
            return
        destinations = tuple(
            str(destination).strip()
            for destination in destinations
            if str(destination or "").strip()
        )
        if not destinations:
            return
        pending = [
            destination
            for destination in destinations
            if not await warning_push_destination_delivered(
                session,
                user_id=user_id,
                event_type=event_type,
                occurrence_id=resolved_occurrence_id,
                destination=destination,
            )
        ]
        if not pending:
            return
    await deliver_warning_push_only(
        session,
        settings=settings,
        user=user,
        user_id=user_id,
        subscription_id=subscription_id,
        text=text,
        audit_content=audit_content,
        audit_logger=audit_logger,
        warning_key=warning_key,
        level=level,
        kind=kind,
        push_title=push_title,
        event_type=event_type,
        occurrence_id=resolved_occurrence_id,
        only_destinations=tuple(pending) if pending is not None else None,
        logger=logger,
    )


async def deliver_traffic_warning(
    session: AsyncSession,
    *,
    settings: Settings,
    bot: Bot | None,
    user_id: int,
    text: str,
    markup: InlineKeyboardMarkup | None,
    audit_content: str,
    audit_logger: MessageDeliveryLogger,
    email_sender: TrafficWarningEmailSender,
    subject_key: str,
    kind: str,
    warning_key: str,
    logger: logging.Logger,
    telegram_failure_message: str,
    subscription_id: int | None = None,
    level: int | None = None,
    push_title: str | None = None,
    period: str | None = None,
) -> bool | None:
    """Deliver one traffic warning across Telegram, email, and push.

    Returns the push outcome: ``True`` when every push destination confirmed,
    ``False`` when the push was attempted but not fully delivered (failed
    destinations released their fanout claims and stay retryable on the next
    tick via :func:`retry_warning_push_if_pending` despite the recorded
    warning marker), ``None`` when no push was attempted.
    """
    try:
        user = await user_dal.get_user_by_id(session, user_id)
    except Exception:
        logger.exception("Failed to load user %s for traffic notification policy", user_id)
        return None
    if iscoroutine(user):
        user.close()
        user = None
    if user is None:
        user = SimpleNamespace(
            user_id=user_id,
            telegram_id=None,
            email=None,
            telegram_notifications_status="unknown",
        )
    chat_id = telegram_recipient(user, user_id)
    plan = user_notification_delivery_plan(
        settings,
        UserNotificationCategory.TRAFFIC,
        user,
        telegram_available=bot is not None and chat_id is not None,
    )
    if plan.telegram and bot and chat_id is not None:
        try:
            await bot.send_message(
                chat_id,
                text,
                reply_markup=markup,
                parse_mode="HTML",
            )
            await audit_logger(
                session,
                target_user_id=user_id,
                event_type="telegram_traffic_warning_sent",
                channel="telegram",
                recipient=str(chat_id),
                content=audit_content,
            )
        except Exception:
            logger.exception(telegram_failure_message, user_id)

    if plan.email or (
        user_notification_channel_selected(
            settings,
            UserNotificationCategory.TRAFFIC,
            "email",
        )
        and user_notification_channel_allowed(
            user,
            UserNotificationCategory.TRAFFIC,
            "email",
        )
    ):
        try:
            await email_sender(
                session,
                user_id=user_id,
                subject_key=subject_key,
                message_text=text,
                kind=kind,
                warning_key=warning_key,
                audit_content=audit_content,
            )
        except Exception:
            logger.exception("Failed to send traffic warning email to user %s", user_id)

    if plan.push:
        return await deliver_warning_push_only(
            session,
            settings=settings,
            user=user,
            user_id=user_id,
            subscription_id=subscription_id,
            text=text,
            audit_content=audit_content,
            audit_logger=audit_logger,
            warning_key=warning_key,
            level=level,
            kind=kind,
            push_title=push_title,
            period=period,
            logger=logger,
        )
    return None
