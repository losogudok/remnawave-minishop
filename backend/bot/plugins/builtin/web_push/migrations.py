"""Append-only migrations for the built-in Web Push plugin."""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from db.migrator import Migration


def _upgrade_0001(connection: Connection) -> None:
    from db.web_push_models import WebPushDelivery, WebPushSubscription

    WebPushSubscription.__table__.create(connection, checkfirst=True)
    WebPushDelivery.__table__.create(connection, checkfirst=True)


def _upgrade_0002(connection: Connection) -> None:
    """Add the broadcast callback outbox marker to an existing queue table."""

    inspector = inspect(connection)
    if "ext_web_push_deliveries" not in set(inspector.get_table_names()):
        return
    columns = {column["name"] for column in inspector.get_columns("ext_web_push_deliveries")}
    if "callback_reported_at" in columns:
        return
    connection.execute(
        text("ALTER TABLE ext_web_push_deliveries ADD COLUMN callback_reported_at TIMESTAMP NULL")
    )


WEB_PUSH_MIGRATIONS: list[Migration] = [
    Migration(
        id="web_push.0001_initial",
        description="Encrypted Web Push subscriptions and durable delivery queue",
        upgrade=_upgrade_0001,
    ),
    Migration(
        id="web_push.0002_callback_report",
        description="Track broadcast callback outbox delivery",
        upgrade=_upgrade_0002,
    ),
]

__all__ = ["WEB_PUSH_MIGRATIONS"]
