"""Core migrations 0046 onward.

Keep this module append-only. It deliberately starts a fresh range instead of
rewriting the historical 0022-0045 chain.
"""

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from .engine import Migration


def _migration_0046_add_recurring_payment_attribution(connection: Connection) -> None:
    """Persist provider-neutral attribution for automatic renewal attempts."""

    inspector = inspect(connection)
    columns = {column["name"] for column in inspector.get_columns("payments")}
    additions = {
        "is_auto_renew": "BOOLEAN NOT NULL DEFAULT FALSE",
        "renewal_subscription_id": "INTEGER",
        "renewal_cycle_end": "TIMESTAMPTZ",
    }
    for column, definition in additions.items():
        if column not in columns:
            connection.execute(text(f"ALTER TABLE payments ADD COLUMN {column} {definition}"))
    connection.execute(
        text("CREATE INDEX IF NOT EXISTS ix_payments_is_auto_renew ON payments (is_auto_renew)")
    )
    connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_payments_renewal_subscription_id "
            "ON payments (renewal_subscription_id)"
        )
    )


def _migration_0047_add_hwid_traffic_bonus_snapshots(connection: Connection) -> None:
    """Freeze package traffic bonuses in payments and active HWID purchases."""

    inspector = inspect(connection)
    table_names = set(inspector.get_table_names())
    if "payments" in table_names:
        payment_columns = {column["name"] for column in inspector.get_columns("payments")}
        if "hwid_traffic_bonus_bytes" not in payment_columns:
            connection.execute(
                text("ALTER TABLE payments ADD COLUMN hwid_traffic_bonus_bytes BIGINT")
            )
    if "hwid_device_purchases" in table_names:
        purchase_columns = {
            column["name"] for column in inspector.get_columns("hwid_device_purchases")
        }
        if "traffic_bonus_bytes" not in purchase_columns:
            connection.execute(
                text("ALTER TABLE hwid_device_purchases ADD COLUMN traffic_bonus_bytes BIGINT")
            )


def _migration_0048_add_promo_code_owner(connection: Connection) -> None:
    """Record the customer a promo code was minted for, when it has one.

    Nullable by design: an ordinary code belongs to no one, and a single
    allowed activation says nothing about ownership. Only this column marks a
    code as personal.
    """

    inspector = inspect(connection)
    columns = {column["name"] for column in inspector.get_columns("promo_codes")}
    if "user_id" not in columns:
        connection.execute(text("ALTER TABLE promo_codes ADD COLUMN user_id BIGINT"))
    connection.execute(
        text("CREATE INDEX IF NOT EXISTS ix_promo_codes_user_id ON promo_codes (user_id)")
    )


CHAIN_0046_0060: list[Migration] = [
    Migration(
        id="0046_add_recurring_payment_attribution",
        description="Persist auto-renew attribution and renewal cycle references",
        upgrade=_migration_0046_add_recurring_payment_attribution,
    ),
    Migration(
        id="0047_add_hwid_traffic_bonus_snapshots",
        description="Persist traffic bonus snapshots for HWID device purchases",
        upgrade=_migration_0047_add_hwid_traffic_bonus_snapshots,
    ),
    Migration(
        id="0048_add_promo_code_owner",
        description="Record the customer a personal promo code was issued for",
        upgrade=_migration_0048_add_promo_code_owner,
    ),
]
