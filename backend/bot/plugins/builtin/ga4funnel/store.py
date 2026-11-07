"""Short-transaction persistence helpers for the GA4 funnel plugin."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from db.ga4_models import Ga4FunnelBinding, Ga4FunnelEvent, Ga4FunnelMilestone
from db.models import AccountAlias, Payment, Subscription, User

BINDING_RETRY_DELAY = timedelta(minutes=15)
BINDING_MISSING_MAX_AGE = timedelta(hours=24)
LEASE_DURATION = timedelta(seconds=70)
MAX_BACKOFF_SECONDS = 3600
MAX_BATCH_SIZE = 20
MAX_RECONCILE_USERS = 25
MAX_PENDING_EVENTS = 100


def utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True)
class ClaimedEvent:
    id: str
    user_id: int | None
    client_id: str | None
    name: str
    dedupe_key: str
    params: dict[str, Any]
    occurred_at: datetime | None
    received_at: datetime
    attempts: int
    max_attempts: int
    lease_token: str


@dataclass(frozen=True)
class PendingFirstConnected:
    id: str
    dedupe_key: str
    occurred_at: datetime | None
    received_at: datetime


@dataclass(frozen=True)
class FirstConnectedCandidate:
    user_id: int
    panel_user_uuid: str


async def record_event(
    session: AsyncSession,
    *,
    name: str,
    dedupe_key: str,
    user_id: int | None = None,
    client_id: str | None = None,
    params: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
    received_at: datetime | None = None,
    max_attempts: int = 8,
    state: str = "queued",
) -> bool:
    """Insert one deduplicated event in the caller's transaction."""

    existing = await session.scalar(
        select(Ga4FunnelEvent.id).where(Ga4FunnelEvent.dedupe_key == dedupe_key)
    )
    if existing is not None:
        return False
    now = utc(received_at) or datetime.now(UTC)
    row = Ga4FunnelEvent(
        id=uuid.uuid4().hex,
        user_id=user_id,
        client_id=client_id,
        name=name,
        dedupe_key=dedupe_key[:256],
        params_json=dict(params or {}),
        occurred_at=utc(occurred_at),
        received_at=now,
        state=state,
        attempts=0,
        max_attempts=max_attempts,
        next_attempt_at=now,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        return False
    return True


async def record_milestone(
    session: AsyncSession,
    *,
    user_id: int,
    name: str,
    occurred_at: datetime,
) -> bool:
    existing = await session.scalar(
        select(Ga4FunnelMilestone.id).where(
            Ga4FunnelMilestone.user_id == user_id,
            Ga4FunnelMilestone.name == name,
        )
    )
    if existing is not None:
        return False
    row = Ga4FunnelMilestone(
        id=uuid.uuid4().hex,
        user_id=user_id,
        name=name,
        occurred_at=utc(occurred_at) or datetime.now(UTC),
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        return False
    return True


async def find_user_id(
    session: AsyncSession,
    *,
    panel_user_uuid: str | None = None,
    telegram_id: int | str | None = None,
) -> int | None:
    if panel_user_uuid:
        user_id = await session.scalar(
            select(User.user_id).where(User.panel_user_uuid == str(panel_user_uuid))
        )
        if user_id is not None:
            return int(user_id)
    if telegram_id is not None:
        try:
            numeric_telegram_id = int(telegram_id)
        except (TypeError, ValueError):
            return None
        user_id = await session.scalar(
            select(User.user_id).where(User.telegram_id == numeric_telegram_id)
        )
        if user_id is not None:
            return int(user_id)
    return None


async def resolve_account_alias(
    session: AsyncSession,
    user_id: int | None,
) -> int | None:
    """Follow historical account aliases to the current owner."""

    current = user_id
    seen: set[int] = set()
    while current is not None and current not in seen:
        seen.add(current)
        target = await session.scalar(
            select(AccountAlias.new_user_id).where(AccountAlias.old_user_id == current)
        )
        if target is None:
            return current
        current = int(target)
    return current


async def payment_owner(
    session_factory: sessionmaker,
    payment_db_id: int,
) -> int | None:
    async with session_factory() as session:
        user_id = await session.scalar(
            select(Payment.user_id).where(Payment.payment_id == payment_db_id)
        )
    return int(user_id) if user_id is not None else None


async def record_first_connected(
    session: AsyncSession,
    *,
    user_id: int,
    occurred_at: datetime,
) -> None:
    await record_milestone(
        session,
        user_id=user_id,
        name="vpn_first_connected",
        occurred_at=occurred_at,
    )
    await record_event(
        session,
        name="vpn_first_connected",
        dedupe_key=f"first_connected:user:{user_id}",
        user_id=user_id,
        params={},
        occurred_at=occurred_at,
    )


async def resolve_pending_first_connected(
    session: AsyncSession,
    *,
    pending_id: str,
    user_id: int,
    occurred_at: datetime | None,
) -> bool:
    pending = await session.get(Ga4FunnelEvent, pending_id)
    if pending is None:
        return False
    final_key = f"first_connected:user:{user_id}"
    existing = await session.scalar(
        select(Ga4FunnelEvent.id).where(
            Ga4FunnelEvent.dedupe_key == final_key,
            Ga4FunnelEvent.id != pending_id,
        )
    )
    if existing is not None:
        await session.delete(pending)
    else:
        pending.user_id = user_id
        pending.dedupe_key = final_key
        if pending.occurred_at is None and occurred_at is not None:
            pending.occurred_at = utc(occurred_at)
    confirmed_at = utc(pending.occurred_at) or utc(occurred_at) or datetime.now(UTC)
    await record_milestone(
        session,
        user_id=user_id,
        name="vpn_first_connected",
        occurred_at=confirmed_at,
    )
    return True


async def bind_client(
    session: AsyncSession,
    *,
    user_id: int,
    client_id: str,
    now: datetime | None = None,
) -> None:
    current = utc(now) or datetime.now(UTC)
    binding = await session.scalar(
        select(Ga4FunnelBinding).where(
            Ga4FunnelBinding.user_id == user_id,
            Ga4FunnelBinding.client_id == client_id,
        )
    )
    if binding is None:
        session.add(
            Ga4FunnelBinding(
                id=uuid.uuid4().hex,
                user_id=user_id,
                client_id=client_id,
                created_at=current,
                last_seen_at=current,
            )
        )
    else:
        binding.last_seen_at = current
    await session.execute(
        update(Ga4FunnelEvent)
        .where(
            Ga4FunnelEvent.user_id == user_id,
            Ga4FunnelEvent.client_id.is_(None),
            Ga4FunnelEvent.state == "queued",
        )
        .values(
            client_id=client_id,
            next_attempt_at=current,
            lease_token=None,
            lease_until=None,
        )
    )


async def move_account(
    session: AsyncSession,
    *,
    source_user_id: int,
    target_user_id: int,
) -> None:
    """Move plugin rows to a merged account without violating uniques."""

    target_clients = set(
        (
            await session.scalars(
                select(Ga4FunnelBinding.client_id).where(Ga4FunnelBinding.user_id == target_user_id)
            )
        ).all()
    )
    source_bindings = list(
        (
            await session.scalars(
                select(Ga4FunnelBinding).where(Ga4FunnelBinding.user_id == source_user_id)
            )
        ).all()
    )
    for binding in source_bindings:
        if binding.client_id in target_clients:
            await session.delete(binding)
        else:
            binding.user_id = target_user_id
            target_clients.add(binding.client_id)

    target_event_keys = set(
        (
            await session.scalars(
                select(Ga4FunnelEvent.dedupe_key).where(
                    Ga4FunnelEvent.dedupe_key.like(f"%:user:{target_user_id}")
                )
            )
        ).all()
    )
    source_events = list(
        (
            await session.scalars(
                select(Ga4FunnelEvent).where(Ga4FunnelEvent.user_id == source_user_id)
            )
        ).all()
    )
    for event in source_events:
        if event.dedupe_key.endswith(f":user:{source_user_id}"):
            new_key = event.dedupe_key.replace(f":user:{source_user_id}", f":user:{target_user_id}")
            if new_key in target_event_keys:
                await session.delete(event)
                continue
            event.dedupe_key = new_key
            target_event_keys.add(new_key)
        event.user_id = target_user_id

    target_milestones = set(
        (
            await session.scalars(
                select(Ga4FunnelMilestone.name).where(Ga4FunnelMilestone.user_id == target_user_id)
            )
        ).all()
    )
    source_milestones = list(
        (
            await session.scalars(
                select(Ga4FunnelMilestone).where(Ga4FunnelMilestone.user_id == source_user_id)
            )
        ).all()
    )
    for milestone in source_milestones:
        if milestone.name in target_milestones:
            await session.delete(milestone)
        else:
            milestone.user_id = target_user_id
            target_milestones.add(milestone.name)


def _claimed(row: Ga4FunnelEvent, lease_token: str) -> ClaimedEvent:
    return ClaimedEvent(
        id=str(row.id),
        user_id=int(row.user_id) if row.user_id is not None else None,
        client_id=str(row.client_id) if row.client_id else None,
        name=str(row.name),
        dedupe_key=str(row.dedupe_key),
        params=dict(row.params_json or {}),
        occurred_at=utc(row.occurred_at),
        received_at=utc(row.received_at) or datetime.now(UTC),
        attempts=int(row.attempts),
        max_attempts=int(row.max_attempts),
        lease_token=lease_token,
    )


async def claim_batch(
    session_factory: sessionmaker,
    *,
    now: datetime | None = None,
    limit: int = MAX_BATCH_SIZE,
) -> list[ClaimedEvent]:
    """Lease due events; PostgreSQL skips rows locked by another worker."""

    current = utc(now) or datetime.now(UTC)
    due = or_(
        (Ga4FunnelEvent.state == "queued") & (Ga4FunnelEvent.next_attempt_at <= current),
        (Ga4FunnelEvent.state == "running")
        & (Ga4FunnelEvent.lease_until.is_not(None))
        & (Ga4FunnelEvent.lease_until <= current),
    )
    async with session_factory() as session:
        statement = (
            select(Ga4FunnelEvent)
            .where(due)
            .order_by(Ga4FunnelEvent.next_attempt_at, Ga4FunnelEvent.id)
            .limit(max(1, min(limit, MAX_BATCH_SIZE)))
        )
        bind = session.get_bind()
        if getattr(getattr(bind, "dialect", None), "name", "") == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        rows = list((await session.scalars(statement)).all())
        claimed: list[ClaimedEvent] = []
        for row in rows:
            token = uuid.uuid4().hex
            next_attempts = int(row.attempts or 0) + 1
            result = await session.execute(
                update(Ga4FunnelEvent)
                .where(
                    Ga4FunnelEvent.id == row.id,
                    due,
                )
                .values(
                    state="running",
                    lease_token=token,
                    lease_until=current + LEASE_DURATION,
                    attempts=next_attempts,
                )
                .execution_options(synchronize_session=False)
            )
            if int(getattr(result, "rowcount", 0) or 0) != 1:
                continue
            row.state = "running"
            row.lease_token = token
            row.lease_until = current + LEASE_DURATION
            row.attempts = next_attempts
            claimed.append(_claimed(row, token))
        await session.commit()
        return claimed


async def lease_is_owned(
    session_factory: sessionmaker,
    *,
    event: ClaimedEvent,
    now: datetime | None = None,
) -> bool:
    current = utc(now) or datetime.now(UTC)
    async with session_factory() as session:
        owned = await session.scalar(
            select(Ga4FunnelEvent.id).where(
                Ga4FunnelEvent.id == event.id,
                Ga4FunnelEvent.state == "running",
                Ga4FunnelEvent.lease_token == event.lease_token,
                Ga4FunnelEvent.lease_until.is_not(None),
                Ga4FunnelEvent.lease_until > current,
            )
        )
    return owned is not None


async def renew_lease(
    session_factory: sessionmaker,
    *,
    event: ClaimedEvent,
    now: datetime | None = None,
) -> bool:
    """Atomically extend the current owner's lease right before sending."""

    current = utc(now) or datetime.now(UTC)
    async with session_factory() as session:
        result = await session.execute(
            update(Ga4FunnelEvent)
            .where(
                Ga4FunnelEvent.id == event.id,
                Ga4FunnelEvent.state == "running",
                Ga4FunnelEvent.lease_token == event.lease_token,
                Ga4FunnelEvent.lease_until.is_not(None),
                Ga4FunnelEvent.lease_until > current,
            )
            .values(lease_until=current + LEASE_DURATION)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
    return int(getattr(result, "rowcount", 0) or 0) == 1


async def latest_client_id(
    session_factory: sessionmaker,
    user_id: int,
) -> str | None:
    async with session_factory() as session:
        client_id = await session.scalar(
            select(Ga4FunnelBinding.client_id)
            .where(Ga4FunnelBinding.user_id == user_id)
            .order_by(Ga4FunnelBinding.last_seen_at.desc())
            .limit(1)
        )
    return str(client_id) if client_id else None


async def mark_sent(
    session_factory: sessionmaker,
    *,
    event: ClaimedEvent,
    now: datetime | None = None,
) -> None:
    current = utc(now) or datetime.now(UTC)
    async with session_factory() as session:
        await session.execute(
            update(Ga4FunnelEvent)
            .where(
                Ga4FunnelEvent.id == event.id,
                Ga4FunnelEvent.lease_token == event.lease_token,
            )
            .values(
                state="sent",
                sent_at=current,
                error_code=None,
                lease_token=None,
                lease_until=None,
            )
        )
        await session.commit()


async def mark_failed(
    session_factory: sessionmaker,
    *,
    event: ClaimedEvent,
    error_code: str,
) -> None:
    async with session_factory() as session:
        await session.execute(
            update(Ga4FunnelEvent)
            .where(
                Ga4FunnelEvent.id == event.id,
                Ga4FunnelEvent.lease_token == event.lease_token,
            )
            .values(
                state="failed",
                error_code=error_code[:64],
                lease_token=None,
                lease_until=None,
            )
        )
        await session.commit()


async def mark_retry(
    session_factory: sessionmaker,
    *,
    event: ClaimedEvent,
    error_code: str,
    now: datetime | None = None,
) -> None:
    current = utc(now) or datetime.now(UTC)
    if event.attempts >= event.max_attempts:
        await mark_failed(session_factory, event=event, error_code="attempts_exhausted")
        return
    delay = min(MAX_BACKOFF_SECONDS, 2 ** min(event.attempts, 12))
    async with session_factory() as session:
        await session.execute(
            update(Ga4FunnelEvent)
            .where(
                Ga4FunnelEvent.id == event.id,
                Ga4FunnelEvent.lease_token == event.lease_token,
            )
            .values(
                state="queued",
                error_code=error_code[:64],
                next_attempt_at=current + timedelta(seconds=delay),
                lease_token=None,
                lease_until=None,
            )
        )
        await session.commit()


async def defer_without_binding(
    session_factory: sessionmaker,
    *,
    event: ClaimedEvent,
    now: datetime | None = None,
) -> None:
    current = utc(now) or datetime.now(UTC)
    if current - event.received_at >= BINDING_MISSING_MAX_AGE:
        await mark_skipped(
            session_factory,
            event=event,
            error_code="binding_missing",
        )
        return
    async with session_factory() as session:
        await session.execute(
            update(Ga4FunnelEvent)
            .where(
                Ga4FunnelEvent.id == event.id,
                Ga4FunnelEvent.lease_token == event.lease_token,
            )
            .values(
                state="queued",
                error_code=None,
                attempts=max(0, event.attempts - 1),
                next_attempt_at=current + BINDING_RETRY_DELAY,
                lease_token=None,
                lease_until=None,
            )
        )
        await session.commit()


async def mark_skipped(
    session_factory: sessionmaker,
    *,
    event: ClaimedEvent,
    error_code: str,
) -> None:
    async with session_factory() as session:
        await session.execute(
            update(Ga4FunnelEvent)
            .where(
                Ga4FunnelEvent.id == event.id,
                Ga4FunnelEvent.lease_token == event.lease_token,
            )
            .values(
                state="skipped",
                error_code=error_code[:64],
                lease_token=None,
                lease_until=None,
            )
        )
        await session.commit()


async def expire_unbound(
    session_factory: sessionmaker,
    *,
    now: datetime | None = None,
) -> int:
    current = utc(now) or datetime.now(UTC)
    cutoff = current - BINDING_MISSING_MAX_AGE
    async with session_factory() as session:
        result = await session.execute(
            update(Ga4FunnelEvent)
            .where(
                Ga4FunnelEvent.client_id.is_(None),
                Ga4FunnelEvent.state == "queued",
                Ga4FunnelEvent.received_at <= cutoff,
            )
            .values(state="skipped", error_code="binding_missing")
        )
        await session.commit()
    return int(getattr(result, "rowcount", 0) or 0)


async def list_pending_first_connected(
    session_factory: sessionmaker,
    *,
    limit: int = MAX_PENDING_EVENTS,
) -> list[PendingFirstConnected]:
    async with session_factory() as session:
        rows = list(
            (
                await session.scalars(
                    select(Ga4FunnelEvent)
                    .where(
                        Ga4FunnelEvent.user_id.is_(None),
                        Ga4FunnelEvent.name == "vpn_first_connected",
                        Ga4FunnelEvent.state.in_(("queued", "running")),
                    )
                    .order_by(Ga4FunnelEvent.received_at)
                    .limit(max(1, min(limit, MAX_PENDING_EVENTS)))
                )
            ).all()
        )
    return [
        PendingFirstConnected(
            id=str(row.id),
            dedupe_key=str(row.dedupe_key),
            occurred_at=utc(row.occurred_at),
            received_at=utc(row.received_at) or datetime.now(UTC),
        )
        for row in rows
        if str(row.dedupe_key).startswith("first_connected:panel:")
    ]


async def first_connected_candidates(
    session_factory: sessionmaker,
    *,
    limit: int = MAX_RECONCILE_USERS,
) -> list[FirstConnectedCandidate]:
    milestone = (
        select(Ga4FunnelMilestone.id)
        .where(
            Ga4FunnelMilestone.user_id == User.user_id,
            Ga4FunnelMilestone.name == "vpn_first_connected",
        )
        .exists()
    )
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(User.user_id, User.panel_user_uuid)
                .where(
                    User.user_id.in_(select(Subscription.user_id)),
                    User.panel_user_uuid.is_not(None),
                    ~milestone,
                )
                .order_by(User.registration_date.desc(), User.user_id.desc())
                .limit(max(1, min(limit, MAX_RECONCILE_USERS)))
            )
        ).all()
    return [
        FirstConnectedCandidate(user_id=int(user_id), panel_user_uuid=str(panel_user_uuid))
        for user_id, panel_user_uuid in rows
        if panel_user_uuid
    ]


async def payment_months(
    session_factory: sessionmaker,
    payment_db_id: int,
) -> int | None:
    async with session_factory() as session:
        months = await session.scalar(
            select(Payment.subscription_duration_months).where(Payment.payment_id == payment_db_id)
        )
    return int(months) if months is not None else None


async def delete_event(
    session: AsyncSession,
    *,
    dedupe_key: str,
) -> None:
    await session.execute(delete(Ga4FunnelEvent).where(Ga4FunnelEvent.dedupe_key == dedupe_key))


__all__ = [
    "BINDING_MISSING_MAX_AGE",
    "BINDING_RETRY_DELAY",
    "MAX_BATCH_SIZE",
    "MAX_PENDING_EVENTS",
    "MAX_RECONCILE_USERS",
    "ClaimedEvent",
    "FirstConnectedCandidate",
    "PendingFirstConnected",
    "bind_client",
    "claim_batch",
    "defer_without_binding",
    "delete_event",
    "expire_unbound",
    "find_user_id",
    "first_connected_candidates",
    "latest_client_id",
    "lease_is_owned",
    "mark_failed",
    "mark_retry",
    "mark_sent",
    "mark_skipped",
    "move_account",
    "payment_months",
    "payment_owner",
    "record_event",
    "record_first_connected",
    "record_milestone",
    "renew_lease",
    "resolve_account_alias",
    "resolve_pending_first_connected",
    "utc",
]
