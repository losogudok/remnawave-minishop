"""Core contract for extensible user notification channels.

This module is the Core side of the ONB-06 notification-channel contract. It
introduces the shared vocabulary every channel (Telegram, email, Web Push, and
future transports) speaks, without reimplementing the eligibility, category,
ban, availability, or preference policy that already lives in
:mod:`bot.services.user_notification_policy`:

- :class:`NotificationChannel` — the closed set of known channel names.
  Unknown names never fall through to another channel (in particular, never
  to email); they resolve to ``None`` and are reported as skipped.
- :class:`NotificationOccurrence` — one business occurrence rendered with
  channel-appropriate content and actions. The same occurrence fans out to
  every enabled, eligible channel; push eligibility never depends on Telegram
  delivery success or Telegram ownership.
- :class:`NotificationChannelProvider` — the transport interface a plugin
  (for example the ONB-07 Web Push plugin) registers. Core keeps working
  with no provider registered: dispatch reports ``provider_absent`` and the
  Telegram/email behavior is unchanged.
- :class:`NotificationChannelOutcome` — per-channel, per-destination outcome
  reporting (``sent`` / ``failed`` / ``skipped`` with a stable reason).
- Idempotent occurrence identity plus reliable enqueue boundaries across
  backend and worker: delivery takes a short-lived in-progress lock on shared
  Redis state (completed with a long-lived marker on success), with an
  in-process fallback that is written through and reconciled on Redis
  recovery, so independent channel or device deliveries never suppress each
  other, a failed destination releases its lock for retry, and a crash
  between lock and completion never leaves a permanent duplicate marker.

Inventory of customer service/support user notices versus admin/log traffic
(acceptance: distinguish the two; include direct-send paths instead of
assuming every Telegram message originates in one domain-event stream):

================= ============================================================
User notice       Producer (push wired in this change)
================= ============================================================
payments          ``CoreEventReactions.on_payment_canceled``
                  occurrence ``payment-failed:{payment_db_id}``;
                  ``send_success_message_to_user`` (direct success path,
                  secrets redacted from push content/URL)
                  occurrence ``payment-succeeded:{payment_db_id}``
subscription /    ``SubscriptionLifecycleNotificationService.send_stage``
reminder / expiry (reminder worker direct path)
                  occurrence ``subscription-notice:{subscription_id}:{stage}:{period}``
traffic           ``SubscriptionNotificationWorker`` trial-traffic direct path
                  occurrence ``trial-traffic-depleted:{subscription_id}``;
                  ``deliver_traffic_warning`` (regular/premium traffic
                  warnings) occurrence
                  ``traffic-warning:{subscription_id}:{warning_key}:{level}:{kind}``
traffic reset     ``_send_traffic_reset_notice`` (regular/premium
                  quota-reset notices) occurrence
                  ``traffic-reset:{subscription_id}:{warning_key}``
device activity / ``HwidDeviceNotificationService.handle_added``
limits            occurrences ``device-connected:{fingerprint}`` and
                  ``device-limit:{fingerprint}``
torrent limits    ``TorrentBlockerNotificationService.handle``
                  occurrence ``torrent-blocked:{fingerprint}``
referral updates  ``CoreEventReactions.on_referral_bonus_granted``
                  occurrence ``referral-bonus:{inviter}:{referee}:{reason}:…``
support replies   ``NotificationSupportMixin.notify_support_admin_reply`` and
                  ``notify_support_ticket_closed`` (support direct paths)
                  occurrences ``support-admin-reply:{message_id}`` and
                  ``support-ticket-closed:{ticket_id}:{transition}``
================= ============================================================

Admin/log traffic (log channel, admin inboxes, partner notices) is explicitly
*not* mirrored: occurrences flagged ``admin_only=True`` are skipped for push,
as are occurrences carrying authentication secrets
(``sensitive=True``), whose lock-screen previews must never leak credentials
or subscription tokens.

Preference model: one Web Push preference (``push_notifications_enabled``)
covers service/support updates and news/promotions; enabling notifications
enables both. Existing Telegram/email category preferences are preserved.
"""

from __future__ import annotations

import html
import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from bot.infra.redis import get_redis, redis_key
from bot.services.user_notification_policy import (
    UserNotificationCategory,
    push_notifications_allowed,
    push_notifications_selected,
    user_notifications_blocked,
)

logger = logging.getLogger(__name__)


class NotificationChannel(StrEnum):
    """Known user notification channels."""

    TELEGRAM = "telegram"
    EMAIL = "email"
    PUSH = "push"


class ChannelOutcomeStatus(StrEnum):
    """Delivery outcome for one channel/destination pair."""

    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class NotificationChannelOutcome:
    """Per-channel, per-destination outcome of one fan-out."""

    channel: str
    destination: str
    status: ChannelOutcomeStatus
    reason: str

    @property
    def delivered(self) -> bool:
        return self.status == ChannelOutcomeStatus.SENT


@dataclass(frozen=True)
class NotificationOccurrence:
    """One business occurrence fanned out with channel-appropriate content.

    ``occurrence_id`` is the stable idempotency identity shared by backend
    and worker (for example
    ``"subscription-notice:42:before_3d:1780272000"``, where the trailing part
    is the period identity so a renewed period redelivers anew). Push
    content is plain text plus an optional same-origin action URL; Telegram
    keeps its HTML message and keyboards, email keeps subject plus HTML body.
    """

    occurrence_id: str
    category: UserNotificationCategory
    user_id: int
    push_title: str = ""
    push_body: str = ""
    push_url: str | None = None
    sensitive: bool = False
    admin_only: bool = False
    dedupe_ttl_seconds: int = 24 * 60 * 60

    def __post_init__(self) -> None:
        if not str(self.occurrence_id or "").strip():
            raise ValueError("NotificationOccurrence requires a stable occurrence_id")


class NotificationChannelProvider(Protocol):
    """Transport interface registered for one notification channel.

    Implemented by Core for built-in transports and by plugins (for example
    the Web Push plugin) for additional ones. ``destinations_for_user`` must
    stay cheap: it runs during fan-out for every occurrence. Authorization,
    endpoint ownership rechecks, and retry/backoff live in the provider.
    """

    @property
    def name(self) -> str:
        """Channel this provider delivers, for example ``"push"``."""
        ...

    def is_configured(self, settings: Any) -> bool:
        """Whether the provider has the credentials/config to deliver."""
        ...

    def destinations_for_user(self, settings: Any, user: Any) -> tuple[str, ...]:
        """Delivery destinations for a user (for push: subscribed devices)."""
        ...

    async def deliver(
        self,
        *,
        settings: Any,
        user: Any,
        occurrence: NotificationOccurrence,
        destination: str,
    ) -> NotificationChannelOutcome:
        """Deliver one occurrence to one destination and report the outcome."""
        ...


_CHANNEL_PROVIDERS: dict[str, NotificationChannelProvider] = {}


def parse_notification_channel(value: Any) -> NotificationChannel | None:
    """Resolve a channel name; unknown names resolve to ``None``.

    Unknown channels never fall through to another channel (in particular,
    never to email): callers must treat ``None`` as "no such channel".
    """

    if isinstance(value, NotificationChannel):
        return value
    try:
        return NotificationChannel(str(value or "").strip().lower())
    except ValueError:
        return None


def register_notification_channel_provider(provider: NotificationChannelProvider) -> None:
    """Register (or replace) the provider for ``provider.name``."""

    channel = parse_notification_channel(provider.name)
    if channel is None:
        raise ValueError(f"Cannot register provider for unknown channel {provider.name!r}")
    _CHANNEL_PROVIDERS[channel.value] = provider


def unregister_notification_channel_provider(name: Any) -> bool:
    """Remove a registered provider; returns whether one was registered."""

    channel = parse_notification_channel(name)
    if channel is None:
        return False
    return _CHANNEL_PROVIDERS.pop(channel.value, None) is not None


def get_notification_channel_provider(name: Any) -> NotificationChannelProvider | None:
    """Return the provider for a channel, or ``None`` when absent/unknown."""

    channel = parse_notification_channel(name)
    if channel is None:
        return None
    return _CHANNEL_PROVIDERS.get(channel.value)


def registered_notification_channel_names() -> tuple[str, ...]:
    return tuple(sorted(_CHANNEL_PROVIDERS))


async def resolve_channel_destinations(
    provider: NotificationChannelProvider,
    settings: Any,
    user: Any,
) -> tuple[str, ...]:
    """Resolve a provider's destinations asynchronously when it supports it.

    The base protocol keeps ``destinations_for_user`` synchronous for
    compatibility. Providers whose destinations live in the database (Web Push
    subscriptions) implement an additive ``resolve_destinations`` coroutine;
    callers must use this helper so every fan-out path resolves the same set.
    """

    resolver = getattr(provider, "resolve_destinations", None)
    if callable(resolver):
        resolved = await resolver(settings, user)
        return tuple(resolved or ())
    return tuple(provider.destinations_for_user(settings, user) or ())


def push_provider_available(settings: Any) -> bool:
    """Whether a push provider is registered and configured to deliver."""

    provider = get_notification_channel_provider(NotificationChannel.PUSH)
    if provider is None:
        return False
    try:
        return bool(provider.is_configured(settings))
    except Exception:
        logger.exception("Push provider availability check failed.")
        return False


_TAG_RE = re.compile(r"<[^>]*>")
_WHITESPACE_RE = re.compile(r"\s+")


def plaintext_for_push(value: Any, *, limit: int = 240) -> str:
    """Render channel-appropriate plain text for push from an HTML message."""

    text = _WHITESPACE_RE.sub(" ", html.unescape(_TAG_RE.sub("", str(value or "")))).strip()
    if limit > 0 and len(text) > limit:
        text = text[: max(0, limit - 1)].rstrip() + "…"
    return text


def occurrence_id_for(*parts: Any) -> str:
    """Build a stable occurrence identity from id parts (skips empties)."""

    cleaned = [str(part).strip() for part in parts if part is not None and str(part).strip()]
    if not cleaned:
        raise ValueError("occurrence_id_for requires at least one non-empty part")
    return ":".join(cleaned)


_NOTIFICATION_DELIVERY_TTL_SECONDS = 24 * 60 * 60
_NOTIFICATION_IN_PROGRESS_TTL_SECONDS = 5 * 60
_NOTIFICATION_DELIVERY_DONE_VALUE = "done"
_NOTIFICATION_DELIVERY_IN_PROGRESS_VALUE = "in-progress"
_NOTIFICATION_DELIVERY_CACHE_MAX = 4096


class NotificationDeliveryClaim(StrEnum):
    """Result of attempting one occurrence/channel/destination delivery.

    ``ACQUIRED`` means this caller holds the delivery and must proceed;
    ``IN_PROGRESS`` means another backend/worker holds the short-lived lock
    (retryable: this attempt skips but nothing is permanently suppressed);
    ``COMPLETED`` means the delivery already finished (duplicate).
    """

    ACQUIRED = "acquired"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


_notification_delivery_cache: OrderedDict[str, tuple[str, float]] = OrderedDict()


def _notification_delivery_cache_key(
    settings: Any,
    occurrence_id: str,
    channel: str,
    destination: str,
) -> str:
    return redis_key(settings, "notification-delivery", occurrence_id, channel, destination)


def _claim_local_notification_delivery(cache_key: str, ttl_seconds: int) -> bool:
    now = time.monotonic()
    for key, (_state, expires_at) in list(_notification_delivery_cache.items()):
        if expires_at <= now:
            _notification_delivery_cache.pop(key, None)
    if cache_key in _notification_delivery_cache:
        _notification_delivery_cache.move_to_end(cache_key)
        return False
    _notification_delivery_cache[cache_key] = (
        _NOTIFICATION_DELIVERY_IN_PROGRESS_VALUE,
        now + max(1, int(ttl_seconds)),
    )
    while len(_notification_delivery_cache) > _NOTIFICATION_DELIVERY_CACHE_MAX:
        _notification_delivery_cache.popitem(last=False)
    return True


def _peek_local_notification_delivery(cache_key: str) -> str | None:
    now = time.monotonic()
    entry = _notification_delivery_cache.get(cache_key)
    if entry is None:
        return None
    state, expires_at = entry
    if expires_at <= now:
        _notification_delivery_cache.pop(cache_key, None)
        return None
    _notification_delivery_cache.move_to_end(cache_key)
    return state


def _complete_local_notification_delivery(cache_key: str, ttl_seconds: int) -> None:
    now = time.monotonic()
    _notification_delivery_cache[cache_key] = (
        _NOTIFICATION_DELIVERY_DONE_VALUE,
        now + max(1, int(ttl_seconds)),
    )
    _notification_delivery_cache.move_to_end(cache_key)
    while len(_notification_delivery_cache) > _NOTIFICATION_DELIVERY_CACHE_MAX:
        _notification_delivery_cache.popitem(last=False)


def _refresh_local_notification_delivery(cache_key: str, ttl_seconds: int) -> None:
    """Mirror a remotely observed in-progress lock without completing it."""

    now = time.monotonic()
    _notification_delivery_cache[cache_key] = (
        _NOTIFICATION_DELIVERY_IN_PROGRESS_VALUE,
        now + max(1, int(ttl_seconds)),
    )
    _notification_delivery_cache.move_to_end(cache_key)
    while len(_notification_delivery_cache) > _NOTIFICATION_DELIVERY_CACHE_MAX:
        _notification_delivery_cache.popitem(last=False)


async def _read_remote_delivery_state(
    redis: Any,
    cache_key: str,
) -> tuple[str | None, int | None]:
    """Return the shared ``(value, remaining_ttl_seconds)`` for a delivery key.

    Unknown/unreadable remotes report ``(None, None)`` so the caller treats
    the contention as a retryable in-progress lock, never as completion.
    """

    value: str | None = None
    remaining: int | None = None
    getter = getattr(redis, "get", None)
    if callable(getter):
        try:
            raw = await getter(cache_key)
        except Exception:
            logger.warning("Notification delivery dedupe read failed for %s.", cache_key)
            return None, None
        if raw is not None:
            value = raw.decode() if isinstance(raw, bytes) else str(raw)
    ttl_fn = getattr(redis, "ttl", None)
    if callable(ttl_fn):
        try:
            ttl_raw = await ttl_fn(cache_key)
        except Exception:
            logger.warning("Notification delivery dedupe TTL read failed for %s.", cache_key)
        else:
            try:
                ttl_seconds = int(ttl_raw)
            except (TypeError, ValueError):
                ttl_seconds = None
            if ttl_seconds is not None and ttl_seconds >= 0:
                remaining = ttl_seconds
    return value, remaining


async def try_claim_notification_delivery(
    settings: Any,
    occurrence_id: str,
    channel: str,
    destination: str,
    *,
    ttl_seconds: int = _NOTIFICATION_DELIVERY_TTL_SECONDS,
) -> NotificationDeliveryClaim:
    """Claim one occurrence/channel/destination delivery (backend+worker safe).

    Reliable enqueue boundary: the claim is a short-lived in-progress lock,
    not the completion marker. ``complete_notification_delivery`` records the
    long-lived completion after the provider reports success; failures release
    the lock for retry. A crash between the lock and completion therefore never
    leaves a permanent 24h duplicate marker: the lock expires and a replay
    redelivers (at-least-once), while a completed delivery stays suppressed.

    An observed in-progress lock is mirrored locally with its remaining TTL
    and reported as ``IN_PROGRESS`` (retryable), never cached as a 24h
    completion. Only an observed ``done`` value completes the local cache and
    is reconciled back to Redis, propagating the remaining TTL.

    The lock lives on shared Redis state so backend and worker cannot double
    deliver; an in-process fallback keeps unit paths and Redis outages safe.
    Both layers are kept in sync (write-through): a Redis completion also
    marks the local cache, and a local completion observed during an outage is
    reconciled back to Redis on recovery, so an outage transition cannot claim
    the same delivery twice.
    """

    ttl = max(1, int(ttl_seconds))
    lock_ttl = max(1, min(ttl, _NOTIFICATION_IN_PROGRESS_TTL_SECONDS))
    cache_key = _notification_delivery_cache_key(settings, occurrence_id, channel, destination)
    local_state = _peek_local_notification_delivery(cache_key)
    if local_state == _NOTIFICATION_DELIVERY_DONE_VALUE:
        # Local completion (written through from an earlier Redis claim, or
        # claimed locally during an outage) wins: reconcile it to Redis below
        # and suppress without touching the provider.
        try:
            redis = await get_redis(settings)
        except Exception:
            redis = None
        if redis is not None:
            try:
                await redis.set(
                    cache_key,
                    _NOTIFICATION_DELIVERY_DONE_VALUE,
                    nx=True,
                    ex=ttl,
                )
            except Exception:
                logger.warning(
                    "Notification delivery dedupe reconciliation failed for %s.", cache_key
                )
        logger.info(
            "Skipping duplicate %s delivery for occurrence %s destination %s.",
            channel,
            occurrence_id,
            destination,
        )
        return NotificationDeliveryClaim.COMPLETED
    if local_state == _NOTIFICATION_DELIVERY_IN_PROGRESS_VALUE:
        # Concurrent protection for an in-progress delivery: retryable, not a
        # duplicate. Never reconciled as done to Redis.
        logger.info(
            "Skipping in-progress %s delivery for occurrence %s destination %s.",
            channel,
            occurrence_id,
            destination,
        )
        return NotificationDeliveryClaim.IN_PROGRESS
    try:
        redis = await get_redis(settings)
    except Exception as exc:
        logger.warning("Notification delivery dedupe fell back to process memory: %s", exc)
        redis = None
    if redis is not None:
        try:
            claimed = await redis.set(
                cache_key,
                _NOTIFICATION_DELIVERY_IN_PROGRESS_VALUE,
                nx=True,
                ex=lock_ttl,
            )
        except Exception as exc:
            logger.warning("Notification delivery Redis dedupe failed, using memory: %s", exc)
        else:
            if claimed:
                _claim_local_notification_delivery(cache_key, lock_ttl)
                return NotificationDeliveryClaim.ACQUIRED
            remote_value, remote_ttl = await _read_remote_delivery_state(redis, cache_key)
            if remote_value == _NOTIFICATION_DELIVERY_DONE_VALUE:
                # Another backend/worker completed: mirror completion with
                # the remaining TTL so a later outage fallback still suppresses.
                completed_ttl = max(1, min(ttl, remote_ttl)) if remote_ttl is not None else ttl
                _complete_local_notification_delivery(cache_key, completed_ttl)
                logger.info(
                    "Skipping duplicate %s delivery for occurrence %s destination %s.",
                    channel,
                    occurrence_id,
                    destination,
                )
                return NotificationDeliveryClaim.COMPLETED
            # Another backend/worker holds the lock (or the remote state is
            # unreadable): mirror it as in-progress with its remaining TTL so
            # the replay stays retryable instead of becoming a 24h duplicate.
            in_progress_ttl = (
                max(1, min(lock_ttl, remote_ttl)) if remote_ttl is not None else lock_ttl
            )
            _refresh_local_notification_delivery(cache_key, in_progress_ttl)
            logger.info(
                "Skipping in-progress %s delivery for occurrence %s destination %s.",
                channel,
                occurrence_id,
                destination,
            )
            return NotificationDeliveryClaim.IN_PROGRESS
    claimed_local = _claim_local_notification_delivery(cache_key, lock_ttl)
    if not claimed_local:
        logger.info(
            "Skipping duplicate in-process %s delivery for occurrence %s destination %s.",
            channel,
            occurrence_id,
            destination,
        )
        return NotificationDeliveryClaim.IN_PROGRESS
    return NotificationDeliveryClaim.ACQUIRED


async def claim_notification_delivery(
    settings: Any,
    occurrence_id: str,
    channel: str,
    destination: str,
    *,
    ttl_seconds: int = _NOTIFICATION_DELIVERY_TTL_SECONDS,
) -> bool:
    """Claim one occurrence/channel/destination delivery (backend+worker safe).

    Returns ``True`` for the first claimant, ``False`` for duplicates and for
    in-progress deliveries held by another backend/worker. Prefer
    :func:`try_claim_notification_delivery` when the caller must distinguish a
    retryable in-progress lock from a completed duplicate.
    """

    claim = await try_claim_notification_delivery(
        settings,
        occurrence_id,
        channel,
        destination,
        ttl_seconds=ttl_seconds,
    )
    return claim == NotificationDeliveryClaim.ACQUIRED


async def complete_notification_delivery(
    settings: Any,
    occurrence_id: str,
    channel: str,
    destination: str,
    *,
    ttl_seconds: int = _NOTIFICATION_DELIVERY_TTL_SECONDS,
) -> None:
    """Mark one claimed delivery complete (long-lived duplicate suppression)."""

    ttl = max(1, int(ttl_seconds))
    cache_key = _notification_delivery_cache_key(settings, occurrence_id, channel, destination)
    try:
        redis = await get_redis(settings)
    except Exception:
        redis = None
    if redis is not None:
        try:
            await redis.set(
                cache_key,
                _NOTIFICATION_DELIVERY_DONE_VALUE,
                ex=ttl,
            )
        except Exception:
            logger.exception(
                "Failed to record %s delivery completion for occurrence %s.",
                channel,
                occurrence_id,
            )
    _complete_local_notification_delivery(cache_key, ttl)


async def release_notification_delivery(
    settings: Any,
    occurrence_id: str,
    channel: str,
    destination: str,
) -> None:
    """Release a claim so a failed destination delivery can be retried."""

    cache_key = _notification_delivery_cache_key(settings, occurrence_id, channel, destination)
    try:
        redis = await get_redis(settings)
    except Exception:
        redis = None
    if redis is not None:
        try:
            await redis.delete(cache_key)
        except Exception:
            logger.exception(
                "Failed to release %s delivery claim for occurrence %s.",
                channel,
                occurrence_id,
            )
    _notification_delivery_cache.pop(cache_key, None)


def _skipped(channel: str, destination: str, reason: str) -> NotificationChannelOutcome:
    return NotificationChannelOutcome(
        channel=channel,
        destination=destination,
        status=ChannelOutcomeStatus.SKIPPED,
        reason=reason,
    )


async def fanout_push_occurrence(
    *,
    settings: Any,
    user: Any,
    occurrence: NotificationOccurrence,
    provider: NotificationChannelProvider | None = None,
) -> list[NotificationChannelOutcome]:
    """Fan out one occurrence to every subscribed push destination.

    Never raises: per-destination failures are reported as ``failed``
    outcomes and never suppress other destinations. Push eligibility is
    independent of Telegram delivery success and Telegram ownership.
    """

    channel = NotificationChannel.PUSH.value
    try:
        expected_user_id = int(getattr(user, "user_id", 0) or 0)
    except (TypeError, ValueError):
        return [_skipped(channel, "", "unknown_user")]
    if int(occurrence.user_id or 0) != expected_user_id or expected_user_id <= 0:
        return [_skipped(channel, "", "user_mismatch")]

    if user_notifications_blocked(user):
        return [_skipped(channel, "", "banned")]
    if occurrence.sensitive:
        return [_skipped(channel, "", "sensitive_content")]
    if occurrence.admin_only:
        return [_skipped(channel, "", "admin_only")]
    if not push_notifications_selected(settings, occurrence.category):
        return [_skipped(channel, "", "channel_disabled")]
    if not push_notifications_allowed(user):
        return [_skipped(channel, "", "user_opted_out")]

    active_provider = provider
    if active_provider is None:
        active_provider = get_notification_channel_provider(NotificationChannel.PUSH)
    if active_provider is None:
        return [_skipped(channel, "", "provider_absent")]
    try:
        configured = bool(active_provider.is_configured(settings))
    except Exception:
        logger.exception("Push provider configuration check failed.")
        return [_skipped(channel, "", "provider_error")]
    if not configured:
        return [_skipped(channel, "", "provider_not_configured")]

    try:
        destinations = await resolve_channel_destinations(active_provider, settings, user)
    except Exception:
        logger.exception("Push provider destination lookup failed.")
        return [_skipped(channel, "", "destinations_error")]
    destinations = tuple(
        str(destination).strip() for destination in destinations if str(destination or "").strip()
    )
    if not destinations:
        return [_skipped(channel, "", "no_destinations")]

    outcomes: list[NotificationChannelOutcome] = []
    for destination in destinations:
        claim = await try_claim_notification_delivery(
            settings,
            occurrence.occurrence_id,
            channel,
            destination,
            ttl_seconds=occurrence.dedupe_ttl_seconds,
        )
        if claim != NotificationDeliveryClaim.ACQUIRED:
            reason = "duplicate" if claim == NotificationDeliveryClaim.COMPLETED else "in_progress"
            outcomes.append(_skipped(channel, destination, reason))
            continue
        try:
            outcome = await active_provider.deliver(
                settings=settings,
                user=user,
                occurrence=occurrence,
                destination=destination,
            )
        except Exception as exc:
            logger.exception(
                "Push delivery failed for occurrence %s destination %s.",
                occurrence.occurrence_id,
                destination,
            )
            await release_notification_delivery(
                settings, occurrence.occurrence_id, channel, destination
            )
            outcomes.append(
                NotificationChannelOutcome(
                    channel=channel,
                    destination=destination,
                    status=ChannelOutcomeStatus.FAILED,
                    reason=f"delivery_error:{type(exc).__name__}",
                )
            )
            continue
        if outcome.status == ChannelOutcomeStatus.FAILED:
            await release_notification_delivery(
                settings, occurrence.occurrence_id, channel, destination
            )
        else:
            await complete_notification_delivery(
                settings,
                occurrence.occurrence_id,
                channel,
                destination,
                ttl_seconds=occurrence.dedupe_ttl_seconds,
            )
        outcomes.append(outcome)
    return outcomes


@dataclass(frozen=True)
class NotificationIntent:
    """Declarative fan-out intent for one occurrence across channels.

    Documents which channels an occurrence targets; unknown channels are kept
    as-is so dispatch can report them instead of falling through to email.
    """

    occurrence: NotificationOccurrence
    channels: tuple[str, ...] = field(
        default=(
            NotificationChannel.TELEGRAM.value,
            NotificationChannel.EMAIL.value,
            NotificationChannel.PUSH.value,
        )
    )

    def known_channels(self) -> tuple[NotificationChannel, ...]:
        return tuple(
            channel
            for channel in (parse_notification_channel(name) for name in self.channels)
            if channel is not None
        )

    def unknown_channels(self) -> tuple[str, ...]:
        return tuple(
            str(name) for name in self.channels if parse_notification_channel(name) is None
        )
