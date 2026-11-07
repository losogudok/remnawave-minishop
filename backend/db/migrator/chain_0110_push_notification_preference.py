"""Single per-user Web Push notification preference (ONB-06)."""

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .engine import Migration


def _migration_0110_add_push_notification_preference(connection: Connection) -> None:
    connection.execute(
        text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS push_notifications_enabled "
            "BOOLEAN NOT NULL DEFAULT TRUE"
        )
    )


CHAIN_0110_PUSH_NOTIFICATION_PREFERENCE: list[Migration] = [
    Migration(
        id="0110_push_notification_preference",
        description="Add the single per-user Web Push notification preference",
        upgrade=_migration_0110_add_push_notification_preference,
    )
]
