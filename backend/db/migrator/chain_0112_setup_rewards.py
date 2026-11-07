"""Durable account-level install and Web Push setup rewards."""

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from .engine import Migration


def _migration_0112_setup_rewards(connection: Connection) -> None:
    connection.execute(
        text(
            """CREATE TABLE IF NOT EXISTS setup_rewards (
                id VARCHAR(32) PRIMARY KEY,
                user_id BIGINT NOT NULL,
                reward_type VARCHAR(16) NOT NULL,
                completed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                accepted_days INTEGER NULL,
                subscription_id INTEGER NULL,
                target_end_date TIMESTAMP WITH TIME ZONE NULL,
                confirmed_end_date TIMESTAMP WITH TIME ZONE NULL,
                state VARCHAR(16) NOT NULL DEFAULT 'completed',
                history_json TEXT NULL,
                CONSTRAINT uq_setup_reward_user_type UNIQUE (user_id, reward_type)
            )"""
        )
    )
    if "history_json" not in {
        column["name"] for column in inspect(connection).get_columns("setup_rewards")
    }:
        connection.execute(text("ALTER TABLE setup_rewards ADD COLUMN history_json TEXT NULL"))
    connection.execute(
        text("CREATE INDEX IF NOT EXISTS ix_setup_rewards_user_id ON setup_rewards (user_id)")
    )


CHAIN_0112_SETUP_REWARDS: list[Migration] = [
    Migration(
        id="0112_setup_rewards",
        description="Persist account-level setup completion and reward grants",
        upgrade=_migration_0112_setup_rewards,
    )
]
