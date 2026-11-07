"""Encrypted Web Push device subscriptions and durable delivery queue.

Rows are owned by the built-in ``web_push`` plugin. Endpoints and browser
encryption keys are stored only as AES-GCM ciphertext; the plaintext endpoint
is represented by its SHA-256 hash for device identity and deduplication.
"""

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from db.base import Base


class WebPushSubscription(Base):
    __tablename__ = "ext_web_push_subscriptions"
    __table_args__ = (
        UniqueConstraint("endpoint_hash", name="uq_ext_web_push_subscription_endpoint_hash"),
        Index("ix_ext_web_push_subscriptions_user_active", "user_id", "revoked_at"),
    )

    id = Column(String(32), primary_key=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    endpoint_hash = Column(String(64), nullable=False, unique=True)
    endpoint_ciphertext = Column(LargeBinary, nullable=False)
    keys_ciphertext = Column(LargeBinary, nullable=False)
    label = Column(String(120), nullable=False, default="")
    user_agent = Column(String(255), nullable=False, default="")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_seen_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    expiration_time = Column(BigInteger, nullable=True)


class WebPushDelivery(Base):
    __tablename__ = "ext_web_push_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "occurrence_id",
            "subscription_id",
            name="uq_ext_web_push_delivery_occurrence_subscription",
        ),
        UniqueConstraint(
            "broadcast_delivery_id",
            name="uq_ext_web_push_delivery_broadcast_delivery",
        ),
        Index("ix_ext_web_push_deliveries_state_next", "state", "next_attempt_at"),
        Index("ix_ext_web_push_deliveries_subscription_state", "subscription_id", "state"),
        Index("ix_ext_web_push_deliveries_broadcast_state", "broadcast_id", "state"),
    )

    id = Column(String(32), primary_key=True)
    kind = Column(String(16), nullable=False, default="notification")
    user_id = Column(BigInteger, nullable=False, index=True)
    subscription_id = Column(String(32), nullable=False, index=True)
    occurrence_id = Column(String(128), nullable=True, index=True)
    broadcast_id = Column(BigInteger, nullable=True, index=True)
    broadcast_delivery_id = Column(BigInteger, nullable=True, index=True)
    title = Column(String(160), nullable=False, default="")
    body = Column(String(300), nullable=False, default="")
    url = Column(String(512), nullable=False, default="/")
    tag = Column(String(64), nullable=True)
    state = Column(String(16), nullable=False, default="queued", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=6)
    next_attempt_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    lease_until = Column(DateTime(timezone=True), nullable=True)
    lease_token = Column(String(64), nullable=True)
    error_code = Column(String(64), nullable=True)
    # Broadcast callback outbox: terminal rows stay unreported until the Core
    # admin_broadcast_deliveries row is updated successfully.
    callback_reported_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    sent_at = Column(DateTime(timezone=True), nullable=True)
