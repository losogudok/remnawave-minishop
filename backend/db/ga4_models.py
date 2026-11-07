"""Persistence for the optional GA4 onboarding funnel plugin."""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
)

from db.base import Base


class Ga4FunnelBinding(Base):
    """Opaque GA4 browser client joined to an internal account."""

    __tablename__ = "ext_ga4funnel_bindings"
    __table_args__ = (
        UniqueConstraint("user_id", "client_id", name="uq_ga4funnel_binding_user_client"),
        Index("ix_ga4funnel_bindings_client_id", "client_id"),
    )

    id = Column(String(32), primary_key=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    client_id = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), nullable=False)


class Ga4FunnelEvent(Base):
    """Authoritative funnel event awaiting Measurement Protocol delivery."""

    __tablename__ = "ext_ga4funnel_events"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_ga4funnel_event_dedupe"),
        Index("ix_ga4funnel_events_user_id", "user_id"),
        Index("ix_ga4funnel_events_client_id", "client_id"),
        Index("ix_ga4funnel_events_due", "state", "next_attempt_at"),
    )

    id = Column(String(32), primary_key=True)
    user_id = Column(BigInteger, nullable=True)
    client_id = Column(String(64), nullable=True)
    name = Column(String(64), nullable=False)
    dedupe_key = Column(String(256), nullable=False)
    params_json = Column(JSON, nullable=False)
    occurred_at = Column(DateTime(timezone=True), nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=False)
    state = Column(String(16), nullable=False, default="queued", server_default="queued")
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    max_attempts = Column(Integer, nullable=False, default=8, server_default="8")
    next_attempt_at = Column(DateTime(timezone=True), nullable=False)
    lease_until = Column(DateTime(timezone=True), nullable=True)
    lease_token = Column(String(32), nullable=True)
    error_code = Column(String(64), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)


class Ga4FunnelMilestone(Base):
    """Confirmed outcome that reconciliation must never duplicate."""

    __tablename__ = "ext_ga4funnel_milestones"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_ga4funnel_milestone_user_name"),)

    id = Column(String(32), primary_key=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    name = Column(String(64), nullable=False)
    occurred_at = Column(DateTime(timezone=True), nullable=False)


__all__ = ["Ga4FunnelBinding", "Ga4FunnelEvent", "Ga4FunnelMilestone"]
