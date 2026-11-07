"""Encrypted subscription storage and durable Web Push delivery queue."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from config.web_push_keys import WebPushKeyError, b64url_decode
from db.web_push_models import WebPushDelivery, WebPushSubscription

from .locking import subscription_transaction
from .payload import (
    normalize_push_url,
    push_tag_for,
    sanitize_push_content,
)

logger = logging.getLogger(__name__)

ENDPOINT_AAD = b"web_push_endpoint_v1"
KEYS_AAD = b"web_push_keys_v1"
STORAGE_KEY_INFO = b"minishop web push storage v1"
NONCE_BYTES = 12
DEFAULT_MAX_ATTEMPTS = 6
LEASE_SECONDS = 60
CLAIM_BATCH_SIZE = 32

DELIVERY_QUEUED = "queued"
DELIVERY_RUNNING = "running"
DELIVERY_SENT = "sent"
DELIVERY_FAILED = "failed"
DELIVERY_SKIPPED = "skipped"

SUBSCRIBE_NEW = "created"
SUBSCRIBE_UPDATED = "updated"
SUBSCRIBE_REASSIGNED = "reassigned"


class WebPushStorageError(RuntimeError):
    """Storage encryption is unavailable or a stored value cannot be read."""


class WebPushSubscriptionMissing(LookupError):
    """Passive ownership sync never enrolls an unknown endpoint."""


@dataclass(frozen=True)
class SubscriptionRecord:
    id: str
    user_id: int
    endpoint_hash: str
    label: str
    created_at: datetime
    last_seen_at: datetime
    revoked_at: datetime | None


@dataclass(frozen=True)
class ClaimedDelivery:
    id: str
    kind: str
    user_id: int
    subscription_id: str
    occurrence_id: str | None
    broadcast_id: int | None
    broadcast_delivery_id: int | None
    title: str
    body: str
    url: str
    tag: str | None
    attempts: int
    max_attempts: int
    lease_token: str


@dataclass(frozen=True)
class CallbackCandidate:
    id: str
    broadcast_delivery_id: int
    state: str
    error_code: str


@dataclass(frozen=True)
class RevocationResult:
    found: bool
    device_id: str = ""
    broadcast_delivery_ids: tuple[int, ...] = ()


def _secret_value(value: Any) -> str:
    getter = getattr(value, "get_secret_value", None)
    if callable(getter):
        return str(getter())
    return str(value or "")


def endpoint_hash(endpoint: str) -> str:
    return hashlib.sha256(str(endpoint or "").encode("utf-8")).hexdigest()


def _storage_key(settings: Any) -> bytes:
    private_key = _secret_value(settings.WEB_PUSH_VAPID_PRIVATE_KEY)
    if not private_key:
        raise WebPushStorageError("Web Push storage key is unavailable")
    try:
        raw_private = b64url_decode(private_key)
    except WebPushKeyError as exc:
        raise WebPushStorageError("Web Push storage key is invalid") from exc
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=STORAGE_KEY_INFO,
    ).derive(raw_private)


def encrypt_value(settings: Any, plaintext: bytes, *, aad: bytes) -> bytes:
    nonce = secrets.token_bytes(NONCE_BYTES)
    ciphertext = AESGCM(_storage_key(settings)).encrypt(nonce, bytes(plaintext), aad)
    return nonce + ciphertext


def decrypt_value(settings: Any, blob: bytes, *, aad: bytes) -> bytes:
    raw = bytes(blob or b"")
    if len(raw) <= NONCE_BYTES:
        raise WebPushStorageError("Web Push stored value is truncated")
    try:
        return AESGCM(_storage_key(settings)).decrypt(raw[:NONCE_BYTES], raw[NONCE_BYTES:], aad)
    except (InvalidTag, ValueError) as exc:
        raise WebPushStorageError("Web Push stored value cannot be decrypted") from exc


def encode_subscription_keys(subscription_keys: dict[str, Any]) -> bytes:
    return json.dumps(
        {
            "p256dh": str(subscription_keys.get("p256dh") or ""),
            "auth": str(subscription_keys.get("auth") or ""),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def decode_subscription_keys(plaintext: bytes) -> dict[str, str]:
    try:
        decoded = json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WebPushStorageError("Web Push subscription keys are invalid") from exc
    if not isinstance(decoded, dict):
        raise WebPushStorageError("Web Push subscription keys are invalid")
    return {
        "p256dh": str(decoded.get("p256dh") or ""),
        "auth": str(decoded.get("auth") or ""),
    }


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def utc_milliseconds(value: datetime) -> int:
    """Browser ``expirationTime`` is a millisecond epoch timestamp."""

    return int(_as_utc(value).timestamp() * 1000)


class WebPushStore:
    """Owns all persisted Web Push state for one process configuration."""

    def __init__(self, settings: Any, session_factory: sessionmaker) -> None:
        self.settings = settings
        self.session_factory = session_factory

    def encrypt_endpoint(self, endpoint: str) -> bytes:
        return encrypt_value(self.settings, endpoint.encode("utf-8"), aad=ENDPOINT_AAD)

    def decrypt_endpoint(self, blob: bytes) -> str:
        return decrypt_value(self.settings, blob, aad=ENDPOINT_AAD).decode("utf-8")

    def encrypt_keys(self, keys: dict[str, Any]) -> bytes:
        return encrypt_value(self.settings, encode_subscription_keys(keys), aad=KEYS_AAD)

    def decrypt_keys(self, blob: bytes) -> dict[str, str]:
        return decode_subscription_keys(decrypt_value(self.settings, blob, aad=KEYS_AAD))

    async def _skip_subscription_deliveries(
        self,
        session: Any,
        subscription_id: str,
        reason: str,
        *,
        now: datetime,
        exclude_delivery_id: str = "",
    ) -> tuple[int, ...]:
        conditions = [
            WebPushDelivery.subscription_id == subscription_id,
            WebPushDelivery.state.in_((DELIVERY_QUEUED, DELIVERY_RUNNING)),
        ]
        if exclude_delivery_id:
            conditions.append(WebPushDelivery.id != exclude_delivery_id)
        broadcast_ids = tuple(
            int(value)
            for value in (
                await session.execute(
                    select(WebPushDelivery.broadcast_delivery_id).where(
                        *conditions,
                        WebPushDelivery.kind == "broadcast",
                        WebPushDelivery.broadcast_delivery_id.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
            if value is not None
        )
        await session.execute(
            update(WebPushDelivery)
            .where(*conditions)
            .values(
                state=DELIVERY_SKIPPED,
                error_code=reason[:64],
                lease_token=None,
                lease_until=None,
                updated_at=now,
            )
        )
        return broadcast_ids

    async def upsert_subscription(
        self,
        *,
        user_id: int,
        endpoint: str,
        subscription_keys: dict[str, Any],
        label: str = "",
        user_agent: str = "",
        expiration_time: int | None = None,
        reactivate: bool = True,
        now: datetime | None = None,
    ) -> tuple[SubscriptionRecord, str, tuple[int, ...]]:
        moment = now or datetime.now(UTC)
        endpoint_digest = endpoint_hash(endpoint)
        label_value = str(label or "").strip()[:120]
        agent_value = str(user_agent or "").strip()[:255]
        # Canonical lock order: endpoint hash first, then subscription id.
        # Worker sends/reassigns/revocations take only the id lock and never
        # wait for an endpoint lock, so the two orders cannot form a cycle.
        endpoint_lock = f"endpoint:{endpoint_digest}"
        async with subscription_transaction(self.session_factory, endpoint_lock) as guard:
            session = guard.session
            row_id = await session.scalar(
                select(WebPushSubscription.id).where(
                    WebPushSubscription.endpoint_hash == endpoint_digest
                )
            )
            existing: WebPushSubscription | None = None
            if row_id is not None:
                await guard.acquire(str(row_id))
                # Re-read after both locks: another enrollment may have
                # created or changed the row while this request waited.
                existing = (
                    await session.execute(
                        select(WebPushSubscription)
                        .where(WebPushSubscription.id == str(row_id))
                        .with_for_update()
                    )
                ).scalar_one_or_none()
            skipped_broadcasts: tuple[int, ...] = ()
            if existing is None:
                if not reactivate:
                    raise WebPushSubscriptionMissing
                record = WebPushSubscription(
                    id=secrets.token_hex(16),
                    user_id=int(user_id),
                    endpoint_hash=endpoint_digest,
                    endpoint_ciphertext=self.encrypt_endpoint(endpoint),
                    keys_ciphertext=self.encrypt_keys(subscription_keys),
                    label=label_value,
                    user_agent=agent_value,
                    created_at=moment,
                    last_seen_at=moment,
                    expiration_time=expiration_time,
                )
                session.add(record)
                outcome = SUBSCRIBE_NEW
            else:
                if not reactivate and existing.revoked_at is not None:
                    # A reload/account transition must never undo removal.
                    return self._subscription_record(existing), "revoked", ()
                if int(existing.user_id) != int(user_id):
                    skipped_broadcasts = await self._skip_subscription_deliveries(
                        session, str(existing.id), "account_reassigned", now=moment
                    )
                    outcome = SUBSCRIBE_REASSIGNED
                else:
                    outcome = SUBSCRIBE_UPDATED
                existing.user_id = int(user_id)
                existing.endpoint_ciphertext = self.encrypt_endpoint(endpoint)
                existing.keys_ciphertext = self.encrypt_keys(subscription_keys)
                if label_value:
                    existing.label = label_value
                if agent_value:
                    existing.user_agent = agent_value
                if reactivate:
                    existing.expiration_time = expiration_time
                    existing.revoked_at = None
                existing.last_seen_at = moment
                record = existing
            await session.flush()
            stored = self._subscription_record(record)
        return stored, outcome, skipped_broadcasts

    @staticmethod
    def _subscription_record(record: WebPushSubscription) -> SubscriptionRecord:
        return SubscriptionRecord(
            id=str(record.id),
            user_id=int(record.user_id),
            endpoint_hash=str(record.endpoint_hash),
            label=str(record.label or ""),
            created_at=_as_utc(record.created_at),
            last_seen_at=_as_utc(record.last_seen_at),
            revoked_at=_as_utc(record.revoked_at) if record.revoked_at else None,
        )

    async def list_active_subscriptions(self, user_id: int) -> tuple[WebPushSubscription, ...]:
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(WebPushSubscription)
                    .where(
                        WebPushSubscription.user_id == int(user_id),
                        WebPushSubscription.revoked_at.is_(None),
                        or_(
                            WebPushSubscription.expiration_time.is_(None),
                            WebPushSubscription.expiration_time > utc_milliseconds(now),
                        ),
                    )
                    .order_by(WebPushSubscription.created_at.asc())
                )
            ).scalars()
            return tuple(rows.all())

    async def list_subscriptions(self, user_id: int) -> tuple[SubscriptionRecord, ...]:
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(WebPushSubscription)
                    .where(
                        WebPushSubscription.user_id == int(user_id),
                        WebPushSubscription.revoked_at.is_(None),
                        or_(
                            WebPushSubscription.expiration_time.is_(None),
                            WebPushSubscription.expiration_time > utc_milliseconds(now),
                        ),
                    )
                    .order_by(WebPushSubscription.created_at.asc())
                )
            ).scalars()
            return tuple(
                SubscriptionRecord(
                    id=str(row.id),
                    user_id=int(row.user_id),
                    endpoint_hash=str(row.endpoint_hash),
                    label=str(row.label or ""),
                    created_at=_as_utc(row.created_at),
                    last_seen_at=_as_utc(row.last_seen_at),
                    revoked_at=_as_utc(row.revoked_at) if row.revoked_at else None,
                )
                for row in rows.all()
            )

    async def find_subscription_by_id(self, device_id: str) -> WebPushSubscription | None:
        async with self.session_factory() as session:
            return (
                await session.execute(
                    select(WebPushSubscription).where(
                        WebPushSubscription.id == str(device_id or "").strip()
                    )
                )
            ).scalar_one_or_none()

    async def revoke_subscription(
        self,
        *,
        user_id: int,
        endpoint: str = "",
        device_id: str = "",
        now: datetime | None = None,
    ) -> RevocationResult:
        moment = now or datetime.now(UTC)
        async with self.session_factory() as session:
            stmt = select(WebPushSubscription).where(WebPushSubscription.user_id == int(user_id))
            if device_id:
                stmt = stmt.where(WebPushSubscription.id == str(device_id).strip())
            elif endpoint:
                stmt = stmt.where(WebPushSubscription.endpoint_hash == endpoint_hash(endpoint))
            else:
                return RevocationResult(False)
            subscription_id = await session.scalar(stmt.with_only_columns(WebPushSubscription.id))
        if subscription_id is None:
            return RevocationResult(False)
        async with subscription_transaction(self.session_factory, str(subscription_id)) as guard:
            session = guard.session
            record = await session.get(WebPushSubscription, subscription_id)
            if record is None or int(record.user_id) != int(user_id):
                return RevocationResult(False)
            record.revoked_at = moment
            skipped = await self._skip_subscription_deliveries(
                session, str(record.id), "revoked", now=moment
            )
            return RevocationResult(
                True,
                device_id=str(record.id),
                broadcast_delivery_ids=skipped,
            )

    async def revoke_subscription_id(
        self,
        subscription_id: str,
        *,
        reason: str,
        now: datetime | None = None,
        exclude_delivery_id: str = "",
        session: AsyncSession | None = None,
    ) -> tuple[bool, tuple[int, ...]]:
        moment = now or datetime.now(UTC)
        if session is None:
            async with subscription_transaction(self.session_factory, subscription_id) as guard:
                return await self.revoke_subscription_id(
                    subscription_id,
                    reason=reason,
                    now=moment,
                    exclude_delivery_id=exclude_delivery_id,
                    session=guard.session,
                )
        else:
            record = (
                await session.execute(
                    select(WebPushSubscription)
                    .where(WebPushSubscription.id == str(subscription_id))
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if record is None:
                return False, ()
            record.revoked_at = moment
            skipped = await self._skip_subscription_deliveries(
                session,
                str(record.id),
                reason,
                now=moment,
                exclude_delivery_id=exclude_delivery_id,
            )
            return True, skipped

    async def enqueue_notification(
        self,
        *,
        user_id: int,
        subscription_id: str,
        occurrence_id: str,
        title: str,
        body: str,
        url: str,
        tag: str | None,
        now: datetime | None = None,
    ) -> bool:
        moment = now or datetime.now(UTC)
        async with self.session_factory() as session:
            existing = (
                await session.execute(
                    select(WebPushDelivery.id).where(
                        WebPushDelivery.occurrence_id == str(occurrence_id),
                        WebPushDelivery.subscription_id == str(subscription_id),
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return False
            session.add(
                WebPushDelivery(
                    id=secrets.token_hex(16),
                    kind="notification",
                    user_id=int(user_id),
                    subscription_id=str(subscription_id),
                    occurrence_id=str(occurrence_id),
                    title=str(title)[:160],
                    body=str(body)[:300],
                    url=str(url)[:512],
                    tag=(str(tag)[:64] if tag else None),
                    state=DELIVERY_QUEUED,
                    attempts=0,
                    max_attempts=DEFAULT_MAX_ATTEMPTS,
                    next_attempt_at=moment,
                    created_at=moment,
                    updated_at=moment,
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return False
        return True

    async def enqueue_broadcast_push(
        self,
        *,
        broadcast_id: int,
        broadcast_delivery_id: int,
        user_id: int,
        subscription_id: str,
        title: str,
        body: str,
        url: str,
        now: datetime | None = None,
    ) -> bool:
        """Idempotently queue one broadcast delivery (used by ONB-09)."""

        moment = now or datetime.now(UTC)
        safe_title, safe_body = sanitize_push_content(
            title,
            body,
            fallback_title=str(getattr(self.settings, "WEBAPP_TITLE", "") or ""),
        )
        safe_url = normalize_push_url(url, self.settings)
        async with self.session_factory() as session:
            existing = (
                await session.execute(
                    select(WebPushDelivery.id).where(
                        WebPushDelivery.broadcast_delivery_id == int(broadcast_delivery_id)
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return False
            session.add(
                WebPushDelivery(
                    id=secrets.token_hex(16),
                    kind="broadcast",
                    user_id=int(user_id),
                    subscription_id=str(subscription_id),
                    broadcast_id=int(broadcast_id),
                    broadcast_delivery_id=int(broadcast_delivery_id),
                    title=safe_title,
                    body=safe_body,
                    url=safe_url,
                    tag=push_tag_for(f"broadcast:{broadcast_id}"),
                    state=DELIVERY_QUEUED,
                    attempts=0,
                    max_attempts=DEFAULT_MAX_ATTEMPTS,
                    next_attempt_at=moment,
                    created_at=moment,
                    updated_at=moment,
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return False
        return True

    async def record_broadcast_failure(
        self,
        *,
        broadcast_id: int,
        broadcast_delivery_id: int,
        user_id: int,
        error_code: str = "enqueue_failed",
        now: datetime | None = None,
    ) -> bool:
        """Persist a terminal enqueue failure for the Core callback outbox.

        No endpoint exists for this delivery, so the worker must never claim
        it. The row is terminal immediately and the unique Core delivery id
        makes repeated reports idempotent. ``subscription_id`` deliberately
        uses an empty sentinel: the callback flusher only needs the delivery
        id, state and error code.
        """

        moment = now or datetime.now(UTC)
        async with self.session_factory() as session:
            existing = (
                await session.execute(
                    select(WebPushDelivery.id).where(
                        WebPushDelivery.broadcast_delivery_id == int(broadcast_delivery_id)
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return False
            session.add(
                WebPushDelivery(
                    id=secrets.token_hex(16),
                    kind="broadcast",
                    user_id=int(user_id),
                    subscription_id="",
                    broadcast_id=int(broadcast_id),
                    broadcast_delivery_id=int(broadcast_delivery_id),
                    title="",
                    body="",
                    url="/",
                    tag=None,
                    state=DELIVERY_FAILED,
                    attempts=0,
                    max_attempts=0,
                    next_attempt_at=moment,
                    error_code=str(error_code)[:64],
                    created_at=moment,
                    updated_at=moment,
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return False
        return True

    async def claim_due_deliveries(
        self,
        *,
        now: datetime | None = None,
        batch_size: int = CLAIM_BATCH_SIZE,
    ) -> tuple[ClaimedDelivery, ...]:
        moment = now or datetime.now(UTC)
        lease_until = moment + timedelta(seconds=LEASE_SECONDS)
        async with self.session_factory() as session, session.begin():
            stmt = (
                select(WebPushDelivery)
                .where(
                    or_(
                        and_(
                            WebPushDelivery.state == DELIVERY_QUEUED,
                            WebPushDelivery.next_attempt_at <= moment,
                        ),
                        and_(
                            WebPushDelivery.state == DELIVERY_RUNNING,
                            or_(
                                WebPushDelivery.lease_until.is_(None),
                                WebPushDelivery.lease_until <= moment,
                            ),
                        ),
                    )
                )
                .order_by(
                    WebPushDelivery.next_attempt_at.asc(),
                    WebPushDelivery.created_at.asc(),
                )
                .limit(max(1, int(batch_size)))
            )
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                stmt = stmt.with_for_update(skip_locked=True)
            rows = list((await session.execute(stmt)).scalars().all())
            claimed: list[ClaimedDelivery] = []
            for row in rows:
                token = secrets.token_hex(16)
                row.state = DELIVERY_RUNNING
                row.attempts = int(row.attempts or 0) + 1
                row.lease_token = token
                row.lease_until = lease_until
                row.updated_at = moment
                claimed.append(
                    ClaimedDelivery(
                        id=str(row.id),
                        kind=str(row.kind),
                        user_id=int(row.user_id),
                        subscription_id=str(row.subscription_id),
                        occurrence_id=str(row.occurrence_id) if row.occurrence_id else None,
                        broadcast_id=int(row.broadcast_id) if row.broadcast_id else None,
                        broadcast_delivery_id=(
                            int(row.broadcast_delivery_id) if row.broadcast_delivery_id else None
                        ),
                        title=str(row.title or ""),
                        body=str(row.body or ""),
                        url=str(row.url or "/"),
                        tag=str(row.tag) if row.tag else None,
                        attempts=int(row.attempts),
                        max_attempts=int(row.max_attempts or DEFAULT_MAX_ATTEMPTS),
                        lease_token=token,
                    )
                )
        return tuple(claimed)

    async def list_unreported_broadcast_callbacks(
        self,
        *,
        limit: int = CLAIM_BATCH_SIZE,
    ) -> tuple[CallbackCandidate, ...]:
        """Return terminal broadcast rows whose Core callback is not confirmed."""

        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        WebPushDelivery.id,
                        WebPushDelivery.broadcast_delivery_id,
                        WebPushDelivery.state,
                        WebPushDelivery.error_code,
                    )
                    .where(
                        WebPushDelivery.kind == "broadcast",
                        WebPushDelivery.broadcast_delivery_id.is_not(None),
                        WebPushDelivery.callback_reported_at.is_(None),
                        WebPushDelivery.state.in_(
                            (DELIVERY_SENT, DELIVERY_FAILED, DELIVERY_SKIPPED)
                        ),
                    )
                    .order_by(WebPushDelivery.updated_at.asc())
                    .limit(max(1, int(limit)))
                )
            ).all()
        return tuple(
            CallbackCandidate(
                id=str(row.id),
                broadcast_delivery_id=int(row.broadcast_delivery_id),
                state=str(row.state),
                error_code=str(row.error_code or ""),
            )
            for row in rows
            if row.broadcast_delivery_id is not None
        )

    async def mark_callback_reported(
        self,
        delivery_id: str,
        *,
        now: datetime | None = None,
    ) -> None:
        moment = now or datetime.now(UTC)
        async with self.session_factory() as session, session.begin():
            await session.execute(
                update(WebPushDelivery)
                .where(WebPushDelivery.id == str(delivery_id))
                .values(callback_reported_at=moment, updated_at=moment)
            )

    async def mark_sent(
        self,
        delivery_id: str,
        lease_token: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        moment = now or datetime.now(UTC)
        async with self.session_factory() as session, session.begin():
            result = await session.execute(
                update(WebPushDelivery)
                .where(
                    WebPushDelivery.id == str(delivery_id),
                    WebPushDelivery.lease_token == lease_token,
                )
                .values(
                    state=DELIVERY_SENT,
                    sent_at=moment,
                    error_code=None,
                    lease_token=None,
                    lease_until=None,
                    updated_at=moment,
                )
            )
            return bool(result.rowcount)

    async def mark_failed(
        self,
        delivery_id: str,
        lease_token: str,
        reason: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        moment = now or datetime.now(UTC)
        async with self.session_factory() as session, session.begin():
            result = await session.execute(
                update(WebPushDelivery)
                .where(
                    WebPushDelivery.id == str(delivery_id),
                    WebPushDelivery.lease_token == lease_token,
                )
                .values(
                    state=DELIVERY_FAILED,
                    error_code=str(reason)[:64],
                    lease_token=None,
                    lease_until=None,
                    updated_at=moment,
                )
            )
            return bool(result.rowcount)

    async def mark_skipped(
        self,
        delivery_id: str,
        lease_token: str,
        reason: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        moment = now or datetime.now(UTC)
        async with self.session_factory() as session, session.begin():
            result = await session.execute(
                update(WebPushDelivery)
                .where(
                    WebPushDelivery.id == str(delivery_id),
                    WebPushDelivery.lease_token == lease_token,
                )
                .values(
                    state=DELIVERY_SKIPPED,
                    error_code=str(reason)[:64],
                    lease_token=None,
                    lease_until=None,
                    updated_at=moment,
                )
            )
            return bool(result.rowcount)

    async def reschedule(
        self,
        delivery_id: str,
        lease_token: str,
        *,
        attempts: int,
        now: datetime | None = None,
    ) -> bool:
        moment = now or datetime.now(UTC)
        delay_seconds = min(2 ** max(1, int(attempts)), 60 * 60)
        async with self.session_factory() as session, session.begin():
            result = await session.execute(
                update(WebPushDelivery)
                .where(
                    WebPushDelivery.id == str(delivery_id),
                    WebPushDelivery.lease_token == lease_token,
                )
                .values(
                    state=DELIVERY_QUEUED,
                    next_attempt_at=moment + timedelta(seconds=delay_seconds),
                    lease_token=None,
                    lease_until=None,
                    updated_at=moment,
                )
            )
            return bool(result.rowcount)

    async def touch_subscription(
        self,
        subscription_id: str,
        *,
        now: datetime | None = None,
    ) -> None:
        moment = now or datetime.now(UTC)
        async with self.session_factory() as session, session.begin():
            await session.execute(
                update(WebPushSubscription)
                .where(WebPushSubscription.id == str(subscription_id))
                .values(last_seen_at=moment)
            )


async def enqueue_broadcast_push(
    *,
    settings: Any,
    session_factory: sessionmaker,
    broadcast_id: int,
    broadcast_delivery_id: int,
    user_id: int,
    subscription_id: str,
    title: str,
    body: str,
    url: str,
) -> bool:
    """Queue one broadcast push for the future ONB-09 broadcast provider.

    The unique ``broadcast_delivery_id`` makes repeated provider calls safe.
    """

    return await WebPushStore(settings, session_factory).enqueue_broadcast_push(
        broadcast_id=broadcast_id,
        broadcast_delivery_id=broadcast_delivery_id,
        user_id=user_id,
        subscription_id=subscription_id,
        title=title,
        body=body,
        url=url,
    )


__all__ = [
    "CLAIM_BATCH_SIZE",
    "DEFAULT_MAX_ATTEMPTS",
    "DELIVERY_FAILED",
    "DELIVERY_QUEUED",
    "DELIVERY_RUNNING",
    "DELIVERY_SENT",
    "DELIVERY_SKIPPED",
    "ENDPOINT_AAD",
    "KEYS_AAD",
    "LEASE_SECONDS",
    "SUBSCRIBE_NEW",
    "SUBSCRIBE_REASSIGNED",
    "SUBSCRIBE_UPDATED",
    "CallbackCandidate",
    "ClaimedDelivery",
    "RevocationResult",
    "SubscriptionRecord",
    "WebPushStorageError",
    "WebPushStore",
    "WebPushSubscriptionMissing",
    "decode_subscription_keys",
    "decrypt_value",
    "encode_subscription_keys",
    "encrypt_value",
    "endpoint_hash",
    "enqueue_broadcast_push",
    "utc_milliseconds",
]
