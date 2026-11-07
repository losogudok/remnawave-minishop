"""Broadcast delivery destinations and per-channel stats (ONB-08).

Delivery rows gain a ``destination_key`` so an endpoint-level channel can
snapshot several destinations for one user without colliding with the old
one-row-per-user/channel uniqueness. Historical rows keep ``legacy``.
Broadcasts gain ``channel_stats`` for per-channel accounting; the legacy
Telegram/email counters remain untouched.
"""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from .engine import Migration

_OLD_UNIQUE_NAME = "uq_admin_broadcast_delivery_user_channel"
_NEW_UNIQUE_NAME = "uq_admin_broadcast_delivery_user_channel_destination"
_NEW_UNIQUE_COLUMNS = ("broadcast_id", "user_id", "channel", "destination_key")


def _migration_0111_broadcast_channel_destinations(connection: Connection) -> None:
    inspector = inspect(connection)
    table_names = set(inspector.get_table_names())
    if "admin_broadcasts" not in table_names or "admin_broadcast_deliveries" not in table_names:
        return

    delivery_columns = {
        column["name"] for column in inspector.get_columns("admin_broadcast_deliveries")
    }
    if "destination_key" not in delivery_columns:
        connection.execute(
            text(
                "ALTER TABLE admin_broadcast_deliveries ADD COLUMN destination_key "
                "VARCHAR(128) NOT NULL DEFAULT 'legacy'"
            )
        )

    broadcast_columns = {column["name"] for column in inspector.get_columns("admin_broadcasts")}
    if "channel_stats" not in broadcast_columns:
        connection.execute(
            text("ALTER TABLE admin_broadcasts ADD COLUMN channel_stats JSON NOT NULL DEFAULT '{}'")
        )

    if connection.dialect.name == "postgresql":
        for constraint in inspector.get_unique_constraints("admin_broadcast_deliveries"):
            name = str(constraint.get("name") or "")
            columns = tuple(constraint.get("column_names") or ())
            if name == _OLD_UNIQUE_NAME or columns == (
                "broadcast_id",
                "user_id",
                "channel",
            ):
                connection.execute(
                    text(
                        f'ALTER TABLE admin_broadcast_deliveries DROP CONSTRAINT IF EXISTS "{name}"'
                    )
                )

    existing_unique_columns = {
        tuple(index.get("column_names") or ())
        for index in inspector.get_indexes("admin_broadcast_deliveries")
        if index.get("unique")
    }
    if _NEW_UNIQUE_COLUMNS not in existing_unique_columns:
        connection.execute(
            text(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {_NEW_UNIQUE_NAME} "
                "ON admin_broadcast_deliveries "
                "(broadcast_id, user_id, channel, destination_key)"
            )
        )


CHAIN_0111_BROADCAST_CHANNEL_DESTINATIONS: list[Migration] = [
    Migration(
        id="0111_broadcast_channel_destinations",
        description="Add broadcast destination keys and per-channel delivery stats",
        upgrade=_migration_0111_broadcast_channel_destinations,
    )
]
