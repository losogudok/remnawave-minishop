"""Idempotent schema for the built-in GA4 funnel plugin."""

from __future__ import annotations

from sqlalchemy.engine import Connection

from db.ga4_models import Ga4FunnelBinding, Ga4FunnelEvent, Ga4FunnelMilestone
from db.migrator import Migration


def _upgrade(connection: Connection) -> None:
    Ga4FunnelBinding.__table__.create(connection, checkfirst=True)
    Ga4FunnelEvent.__table__.create(connection, checkfirst=True)
    Ga4FunnelMilestone.__table__.create(connection, checkfirst=True)


CHAIN_GA4FUNNEL = [
    Migration(
        id="ga4funnel.0001_initial",
        description="Persist GA4 bindings, funnel events and confirmed milestones",
        upgrade=_upgrade,
    )
]


__all__ = ["CHAIN_GA4FUNNEL"]
