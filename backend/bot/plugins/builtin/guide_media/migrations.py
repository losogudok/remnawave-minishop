"""Portable plugin migration for guide media galleries."""

from sqlalchemy import text
from sqlalchemy.engine import Connection

from db.migrator import Migration


def _migration_0001_initial(connection: Connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS ext_guide_media_images (
                image_id VARCHAR(32) PRIMARY KEY,
                digest VARCHAR(64) NOT NULL,
                config_id VARCHAR(128) NOT NULL,
                platform VARCHAR(64) NOT NULL,
                client_id VARCHAR(128) NOT NULL,
                captions JSON NOT NULL DEFAULT '{}',
                sort_order INTEGER NOT NULL DEFAULT 0,
                content_type VARCHAR(32) NOT NULL DEFAULT 'image/webp',
                width INTEGER,
                height INTEGER,
                size_bytes INTEGER NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_ext_guide_media_gallery "
            "ON ext_guide_media_images (config_id, platform, client_id, sort_order)"
        )
    )


MIGRATIONS: list[Migration] = [
    Migration(
        id="guide_media.0001_initial",
        description="Create ordered guide media galleries",
        upgrade=_migration_0001_initial,
    )
]
