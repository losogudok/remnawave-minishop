# SQLAlchemy legacy Column declarations expose instance attributes as Column[T]
# to mypy; this DAL intentionally mutates loaded ORM instances.
# mypy: disable-error-code="assignment,arg-type,operator"

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.services.broadcast_channels import get_broadcast_channel_provider
from db.broadcast_models import AdminBroadcast, AdminBroadcastDelivery

ACTIVE_BROADCAST_STATUSES = ("scheduled", "queued", "running")
FINAL_DELIVERY_STATUSES = ("sent", "failed")
CHANNEL_STATS_COUNT_KEYS = ("queued", "sent", "failed", "skipped")


def _channel_stats_entry(*, reason: str = "") -> dict[str, Any]:
    return {
        "users": 0,
        "deliveries": 0,
        "queued": 0,
        "sent": 0,
        "failed": 0,
        "skipped": 0,
        "reason": reason,
    }


def utc_now() -> datetime:
    return datetime.now(UTC)


async def create_broadcast(
    session: AsyncSession,
    *,
    actor_id: int | None,
    target: str,
    user_ids: list[int],
    channels: list[str],
    exclude_blocked_telegram: bool,
    texts: dict[str, str],
    email_subjects: dict[str, str],
    buttons: list[dict[str, Any]],
    scheduled_at: datetime,
    is_visible: bool = True,
    image_id: str | None = None,
) -> AdminBroadcast:
    recipient_ids: set[int] = set()
    total_deliveries = 0
    channel_stats: dict[str, dict[str, Any]] = {}
    for channel in channels:
        provider = get_broadcast_channel_provider(channel)
        if provider is None:
            # The API rejects unknown channels, but a direct caller must not
            # silently fall through to another transport.
            channel_stats[str(channel)] = _channel_stats_entry(reason="provider_absent")
            continue
        recipients = await provider.resolve_recipients(
            session,
            user_ids=user_ids,
            exclude_blocked_telegram=bool(exclude_blocked_telegram),
        )
        channel_user_ids = {int(recipient.user_id) for recipient in recipients}
        recipient_ids.update(channel_user_ids)
        total_deliveries += len(recipients)
        channel_stats[str(channel)] = {
            **_channel_stats_entry(),
            "users": len(channel_user_ids),
            "deliveries": len(recipients),
        }

    status = "scheduled" if scheduled_at > utc_now() else "queued"
    item = AdminBroadcast(
        created_by_admin_id=actor_id,
        status=status,
        is_visible=is_visible,
        target=target,
        channels=list(channels),
        exclude_blocked_telegram=bool(exclude_blocked_telegram),
        texts=dict(texts),
        email_subjects=dict(email_subjects),
        buttons=list(buttons),
        image_id=image_id,
        scheduled_at=scheduled_at,
        recipient_count=len(recipient_ids),
        total_deliveries=total_deliveries,
        channel_stats=channel_stats,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


async def get_broadcast(
    session: AsyncSession,
    broadcast_id: int,
    *,
    include_deleted: bool = False,
    for_update: bool = False,
) -> AdminBroadcast | None:
    stmt = select(AdminBroadcast).where(AdminBroadcast.broadcast_id == broadcast_id)
    if not include_deleted:
        stmt = stmt.where(AdminBroadcast.deleted_at.is_(None))
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_broadcasts(session: AsyncSession, *, limit: int = 60) -> list[AdminBroadcast]:
    stmt = (
        select(AdminBroadcast)
        .where(AdminBroadcast.deleted_at.is_(None), AdminBroadcast.is_visible == True)
        .order_by(AdminBroadcast.created_at.desc(), AdminBroadcast.broadcast_id.desc())
        .limit(max(1, min(int(limit), 100)))
    )
    return list((await session.execute(stmt)).scalars().all())


async def list_failed_deliveries(
    session: AsyncSession,
    broadcast_id: int,
    *,
    limit: int,
    offset: int,
) -> tuple[int, list[AdminBroadcastDelivery]]:
    condition = (
        AdminBroadcastDelivery.broadcast_id == broadcast_id,
        AdminBroadcastDelivery.status == "failed",
    )
    total = int(
        (
            await session.execute(
                select(func.count(AdminBroadcastDelivery.delivery_id)).where(*condition)
            )
        ).scalar_one()
        or 0
    )
    rows = (
        (
            await session.execute(
                select(AdminBroadcastDelivery)
                .where(*condition)
                .order_by(AdminBroadcastDelivery.delivery_id.asc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return total, list(rows)


async def due_broadcast_ids(session: AsyncSession, *, limit: int = 3) -> list[int]:
    stmt = (
        select(AdminBroadcast.broadcast_id)
        .where(
            AdminBroadcast.deleted_at.is_(None),
            AdminBroadcast.status.in_(("scheduled", "queued")),
            AdminBroadcast.scheduled_at <= utc_now(),
        )
        .order_by(AdminBroadcast.scheduled_at.asc(), AdminBroadcast.broadcast_id.asc())
        .limit(max(1, int(limit)))
    )
    return [int(value) for value in (await session.execute(stmt)).scalars().all()]


async def running_broadcast_ids(session: AsyncSession, *, limit: int = 20) -> list[int]:
    stmt = (
        select(AdminBroadcast.broadcast_id)
        .where(
            AdminBroadcast.deleted_at.is_(None),
            AdminBroadcast.status == "running",
        )
        .order_by(AdminBroadcast.updated_at.asc())
        .limit(max(1, int(limit)))
    )
    return [int(value) for value in (await session.execute(stmt)).scalars().all()]


async def begin_broadcast(session: AsyncSession, broadcast_id: int) -> AdminBroadcast | None:
    item = await get_broadcast(session, broadcast_id, for_update=True)
    # Claim a broadcast exactly once. A second worker may have read the same
    # due id before this transaction committed, but it must not enqueue the
    # already-running delivery set again after waiting on the row lock.
    if item is None or item.status not in {"scheduled", "queued"}:
        return None
    if item.scheduled_at > utc_now():
        return None
    item.status = "running"
    item.started_at = item.started_at or utc_now()
    item.finished_at = None
    item.updated_at = utc_now()
    await session.commit()
    await session.refresh(item)
    return item


async def recover_interrupted_broadcasts(session: AsyncSession) -> int:
    """Requeue only deliveries whose in-memory sender vanished with the process."""

    recovered = int(
        (
            await session.execute(
                select(func.count(AdminBroadcastDelivery.delivery_id)).where(
                    AdminBroadcastDelivery.status == "queued"
                )
            )
        ).scalar_one()
        or 0
    )
    await session.execute(
        update(AdminBroadcastDelivery)
        .where(AdminBroadcastDelivery.status == "queued")
        .values(status="pending", queued_at=None)
    )
    active_delivery_broadcasts = (
        select(AdminBroadcastDelivery.broadcast_id)
        .where(AdminBroadcastDelivery.status.in_(("pending", "queued")))
        .distinct()
    )
    await session.execute(
        update(AdminBroadcast)
        .where(
            AdminBroadcast.deleted_at.is_(None),
            or_(
                AdminBroadcast.status == "running",
                and_(
                    AdminBroadcast.status.in_(("queued", "completed", "completed_with_errors")),
                    AdminBroadcast.broadcast_id.in_(active_delivery_broadcasts),
                ),
            ),
        )
        .values(status="queued", finished_at=None, updated_at=utc_now())
    )
    await session.commit()
    return recovered


async def add_deliveries(
    session: AsyncSession,
    broadcast: AdminBroadcast,
    deliveries: Iterable[dict[str, Any]],
) -> list[AdminBroadcastDelivery]:
    existing_stmt = select(
        AdminBroadcastDelivery.user_id,
        AdminBroadcastDelivery.channel,
        AdminBroadcastDelivery.destination_key,
    ).where(AdminBroadcastDelivery.broadcast_id == broadcast.broadcast_id)
    existing_rows = {
        (int(user_id), str(channel), str(destination_key or "default"))
        for user_id, channel, destination_key in (await session.execute(existing_stmt))
    }
    existing_legacy = {
        (user_id, channel)
        for user_id, channel, destination_key in existing_rows
        if destination_key == "legacy"
    }
    for payload in deliveries:
        user_id = int(payload["user_id"])
        channel = str(payload["channel"])
        destination_key = str(payload.get("destination_key") or "default")
        key = (user_id, channel, destination_key)
        # Historical rows carry ``legacy``; their default destination is the
        # same recipient, so a recovered old campaign does not grow a duplicate.
        # Distinct endpoint keys (including ``default``) never collide.
        if key in existing_rows or (
            destination_key == "default" and (user_id, channel) in existing_legacy
        ):
            continue
        existing_rows.add(key)
        session.add(
            AdminBroadcastDelivery(
                broadcast_id=broadcast.broadcast_id,
                user_id=user_id,
                channel=channel,
                destination_key=destination_key,
                destination=str(payload["destination"]),
                language_code=payload.get("language_code"),
                status="pending",
            )
        )
    await session.flush()
    await refresh_broadcast_stats(
        session,
        int(broadcast.broadcast_id),
        commit=False,
        finalize=False,
    )
    await session.commit()
    return await pending_deliveries(session, int(broadcast.broadcast_id))


async def pending_deliveries(
    session: AsyncSession,
    broadcast_id: int,
    *,
    channel: str | None = None,
) -> list[AdminBroadcastDelivery]:
    stmt = select(AdminBroadcastDelivery).where(
        AdminBroadcastDelivery.broadcast_id == broadcast_id,
        AdminBroadcastDelivery.status == "pending",
    )
    if channel is not None:
        stmt = stmt.where(AdminBroadcastDelivery.channel == channel)
    stmt = stmt.order_by(AdminBroadcastDelivery.delivery_id.asc())
    return list((await session.execute(stmt)).scalars().all())


async def mark_delivery_queued(session: AsyncSession, delivery_id: int) -> None:
    await session.execute(
        update(AdminBroadcastDelivery)
        .where(
            AdminBroadcastDelivery.delivery_id == delivery_id,
            AdminBroadcastDelivery.status == "pending",
        )
        .values(
            status="queued",
            attempts=AdminBroadcastDelivery.attempts + 1,
            queued_at=utc_now(),
            error=None,
        )
    )
    await session.commit()


async def mark_delivery_result(
    session: AsyncSession,
    delivery_id: int,
    *,
    success: bool,
    error: str | None = None,
) -> None:
    delivery = (
        await session.execute(
            select(AdminBroadcastDelivery)
            .where(AdminBroadcastDelivery.delivery_id == delivery_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if delivery is None or delivery.status in FINAL_DELIVERY_STATUSES:
        return
    delivery.status = "sent" if success else "failed"
    delivery.error = None if success else str(error or "delivery_failed")[:1000]
    delivery.finished_at = utc_now()
    await session.flush()
    await refresh_broadcast_stats(session, int(delivery.broadcast_id), commit=False)
    await session.commit()


async def refresh_broadcast_stats(
    session: AsyncSession,
    broadcast_id: int,
    *,
    commit: bool = True,
    finalize: bool = True,
) -> AdminBroadcast | None:
    item = await get_broadcast(session, broadcast_id, include_deleted=True, for_update=True)
    if item is None:
        return None
    totals = (
        await session.execute(
            select(
                func.count(AdminBroadcastDelivery.delivery_id),
                func.count(func.distinct(AdminBroadcastDelivery.user_id)),
                func.coalesce(
                    func.sum(case((AdminBroadcastDelivery.status == "sent", 1), else_=0)), 0
                ),
                func.coalesce(
                    func.sum(case((AdminBroadcastDelivery.status == "failed", 1), else_=0)), 0
                ),
            ).where(AdminBroadcastDelivery.broadcast_id == broadcast_id)
        )
    ).one()
    channel_status_rows = (
        await session.execute(
            select(
                AdminBroadcastDelivery.channel,
                AdminBroadcastDelivery.status,
                func.count(AdminBroadcastDelivery.delivery_id),
            )
            .where(AdminBroadcastDelivery.broadcast_id == broadcast_id)
            .group_by(AdminBroadcastDelivery.channel, AdminBroadcastDelivery.status)
        )
    ).all()
    channel_user_rows = (
        await session.execute(
            select(
                AdminBroadcastDelivery.channel,
                func.count(func.distinct(AdminBroadcastDelivery.user_id)),
            )
            .where(AdminBroadcastDelivery.broadcast_id == broadcast_id)
            .group_by(AdminBroadcastDelivery.channel)
        )
    ).all()

    existing_stats = {
        str(channel): dict(stats)
        for channel, stats in dict(item.channel_stats or {}).items()
        if isinstance(stats, dict)
    }
    stats: dict[str, dict[str, Any]] = {}

    def _entry(channel: str) -> dict[str, Any]:
        if channel not in stats:
            reason = str(existing_stats.get(channel, {}).get("reason") or "")
            stats[channel] = _channel_stats_entry(reason=reason)
        return stats[channel]

    for channel in [*list(item.channels or []), *existing_stats]:
        if str(channel):
            _entry(str(channel))

    for channel, status, count in channel_status_rows:
        entry = _entry(str(channel))
        amount = int(count or 0)
        entry["deliveries"] = int(entry["deliveries"]) + amount
        if status in {"pending", "queued"}:
            entry["queued"] = int(entry["queued"]) + amount
        elif status == "sent":
            entry["sent"] = int(entry["sent"]) + amount
        elif status == "failed":
            entry["failed"] = int(entry["failed"]) + amount
        elif status == "skipped":
            entry["skipped"] = int(entry["skipped"]) + amount

    for channel, user_count in channel_user_rows:
        _entry(str(channel))["users"] = int(user_count or 0)

    item.channel_stats = stats
    item.total_deliveries = int(totals[0] or 0)
    item.recipient_count = int(totals[1] or 0)
    item.successful_deliveries = int(totals[2] or 0)
    item.failed_deliveries = int(totals[3] or 0)
    item.telegram_sent = int(stats.get("telegram", {}).get("sent", 0))
    item.telegram_failed = int(stats.get("telegram", {}).get("failed", 0))
    item.email_sent = int(stats.get("email", {}).get("sent", 0))
    item.email_failed = int(stats.get("email", {}).get("failed", 0))

    processed = int(item.successful_deliveries) + int(item.failed_deliveries)
    non_terminal = max(0, int(item.total_deliveries) - processed)
    has_channel_failure = any(str(entry.get("reason") or "") for entry in stats.values())
    if non_terminal > 0 and item.status in {"completed", "completed_with_errors"}:
        # A campaign with any pending/queued row is not finished. This also
        # repairs a campaign finalized by an earlier bug before a slow channel
        # persisted its rows.
        item.status = "running"
        item.finished_at = None
    elif (
        item.status in {"completed", "completed_with_errors"}
        and has_channel_failure
        and item.status != "completed_with_errors"
    ):
        # A channel outcome can be recorded after the last delivery callback
        # finalized the campaign; surface it instead of keeping a false success.
        item.status = "completed_with_errors"
    if finalize and item.status == "running" and non_terminal == 0:
        # A selected channel may have zero rows because its provider vanished
        # or became unavailable. That is a finished campaign with a per-channel
        # outcome, not a silent success.
        item.status = (
            "completed_with_errors"
            if int(item.failed_deliveries) > 0 or has_channel_failure
            else "completed"
        )
        item.finished_at = item.finished_at or utc_now()
    item.updated_at = utc_now()
    if commit:
        await session.commit()
        await session.refresh(item)
    return item


async def record_channel_outcome(
    session: AsyncSession,
    broadcast_id: int,
    channel: str,
    outcome: str,
    *,
    commit: bool = True,
) -> AdminBroadcast | None:
    """Persist a stable per-channel outcome for a channel with no deliveries."""

    item = await get_broadcast(session, broadcast_id, include_deleted=True, for_update=True)
    if item is None:
        return None
    stats = {
        str(name): dict(values)
        for name, values in dict(item.channel_stats or {}).items()
        if isinstance(values, dict)
    }
    entry = _channel_stats_entry(reason=str(outcome or ""))
    existing = stats.get(str(channel))
    if isinstance(existing, dict):
        for key in ("users", "deliveries", *CHANNEL_STATS_COUNT_KEYS):
            value = existing.get(key)
            if isinstance(value, int):
                entry[key] = value
    stats[str(channel)] = entry
    item.channel_stats = stats
    item.updated_at = utc_now()
    if commit:
        await session.commit()
    return item


async def fail_channel_deliveries(
    session: AsyncSession,
    broadcast_id: int,
    channel: str,
    error: str,
    *,
    outcome: str = "provider_error",
    finalize: bool = True,
) -> int:
    """Mark only one channel's unfinished rows as failed and record why."""

    result = await session.execute(
        update(AdminBroadcastDelivery)
        .where(
            AdminBroadcastDelivery.broadcast_id == broadcast_id,
            AdminBroadcastDelivery.channel == channel,
            AdminBroadcastDelivery.status.not_in(FINAL_DELIVERY_STATUSES),
        )
        .values(
            status="failed",
            error=str(error or "delivery_failed")[:1000],
            finished_at=utc_now(),
        )
    )
    await session.flush()
    await record_channel_outcome(session, broadcast_id, channel, outcome, commit=False)
    await refresh_broadcast_stats(
        session,
        broadcast_id,
        commit=False,
        finalize=finalize,
    )
    await session.commit()
    return int(getattr(result, "rowcount", 0) or 0)


async def finish_empty_broadcast(session: AsyncSession, broadcast_id: int) -> None:
    item = await get_broadcast(session, broadcast_id, include_deleted=True, for_update=True)
    if item is None:
        return
    non_terminal = int(
        (
            await session.execute(
                select(func.count(AdminBroadcastDelivery.delivery_id)).where(
                    AdminBroadcastDelivery.broadcast_id == broadcast_id,
                    AdminBroadcastDelivery.status.not_in(FINAL_DELIVERY_STATUSES),
                )
            )
        ).scalar_one()
        or 0
    )
    if non_terminal:
        # Never finish a campaign that still has a live delivery row.
        return
    stats = {
        str(name): dict(values)
        for name, values in dict(item.channel_stats or {}).items()
        if isinstance(values, dict)
    }
    has_channel_failure = any(str(entry.get("reason") or "") for entry in stats.values())
    item.status = "completed_with_errors" if has_channel_failure else "completed"
    item.finished_at = utc_now()
    item.updated_at = utc_now()
    await session.commit()


async def fail_broadcast(session: AsyncSession, broadcast_id: int, error: str) -> None:
    item = await get_broadcast(session, broadcast_id, include_deleted=True, for_update=True)
    if item is None:
        return
    item.status = "failed"
    item.last_error = str(error or "broadcast_failed")[:2000]
    item.finished_at = utc_now()
    item.updated_at = utc_now()
    await session.commit()


async def reschedule_broadcast(
    session: AsyncSession,
    broadcast_id: int,
    scheduled_at: datetime,
) -> AdminBroadcast | None:
    item = await get_broadcast(session, broadcast_id, for_update=True)
    if item is None or item.status not in {"scheduled", "queued"} or item.started_at is not None:
        return None
    item.scheduled_at = scheduled_at
    item.status = "scheduled" if scheduled_at > utc_now() else "queued"
    item.updated_at = utc_now()
    await session.commit()
    await session.refresh(item)
    return item


async def delete_broadcast(session: AsyncSession, broadcast_id: int) -> AdminBroadcast | None:
    item = await get_broadcast(session, broadcast_id, for_update=True)
    if item is None:
        return None
    now = utc_now()
    item.deleted_at = now
    item.updated_at = now
    if item.status in ACTIVE_BROADCAST_STATUSES:
        item.status = "cancelled"
        item.finished_at = now
    await session.commit()
    return item
