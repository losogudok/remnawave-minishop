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


def _migration_0049_add_support_message_rich_body(connection: Connection) -> None:
    """Let a ticket message carry markup and buttons.

    Existing rows stay ``text``: they were written in a plain-text composer and
    re-reading them as markup would turn characters their author typed into
    formatting.
    """

    inspector = inspect(connection)
    if "support_ticket_messages" not in set(inspector.get_table_names()):
        return
    columns = {column["name"] for column in inspector.get_columns("support_ticket_messages")}
    additions = {
        "body_format": "VARCHAR(8) NOT NULL DEFAULT 'text'",
        "buttons": "TEXT",
    }
    for column, definition in additions.items():
        if column not in columns:
            connection.execute(
                text(f"ALTER TABLE support_ticket_messages ADD COLUMN {column} {definition}")
            )


def _migration_0050_add_tribute_webhook_state(connection: Connection) -> None:
    """Persist Tribute Shop, subscription, and Digital Product webhook state."""

    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS tribute_entitlements (
                entitlement_id SERIAL PRIMARY KEY,
                tribute_subscription_id BIGINT NOT NULL,
                tribute_period_id BIGINT NOT NULL,
                trb_user_id VARCHAR(128) NOT NULL,
                telegram_user_id BIGINT NOT NULL,
                user_id BIGINT REFERENCES users(user_id),
                tariff_key VARCHAR,
                duration_months INTEGER,
                subscription_type VARCHAR(16),
                status VARCHAR(32) NOT NULL DEFAULT 'active',
                active_until TIMESTAMPTZ NOT NULL,
                last_event_name VARCHAR(64) NOT NULL,
                last_event_created_at TIMESTAMPTZ NOT NULL,
                last_event_fingerprint VARCHAR(64) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT uq_tribute_entitlements_subscription_user
                    UNIQUE (tribute_subscription_id, trb_user_id)
            )
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS tribute_webhook_events (
                event_id SERIAL PRIMARY KEY,
                fingerprint VARCHAR(64) NOT NULL UNIQUE,
                event_name VARCHAR(64) NOT NULL,
                tribute_subscription_id BIGINT NOT NULL,
                tribute_period_id BIGINT NOT NULL,
                trb_user_id VARCHAR(128) NOT NULL,
                telegram_user_id BIGINT NOT NULL,
                event_created_at TIMESTAMPTZ NOT NULL,
                event_sent_at TIMESTAMPTZ NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                price BIGINT NOT NULL,
                amount BIGINT NOT NULL,
                currency VARCHAR(16) NOT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'processing',
                status_reason VARCHAR(128),
                payment_id INTEGER REFERENCES payments(payment_id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                processed_at TIMESTAMPTZ
            )
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS tribute_product_purchases (
                purchase_row_id SERIAL PRIMARY KEY,
                tribute_purchase_id BIGINT NOT NULL UNIQUE,
                tribute_transaction_id BIGINT NOT NULL,
                tribute_product_id BIGINT NOT NULL,
                trb_user_id VARCHAR(128),
                telegram_user_id BIGINT,
                user_id BIGINT REFERENCES users(user_id),
                tariff_key VARCHAR,
                sale_mode VARCHAR(64),
                units DOUBLE PRECISION,
                amount BIGINT NOT NULL,
                currency VARCHAR(16) NOT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'processing',
                status_reason VARCHAR(128),
                payment_id INTEGER REFERENCES payments(payment_id) ON DELETE SET NULL,
                purchase_created_at TIMESTAMPTZ,
                fulfilled_at TIMESTAMPTZ,
                refunded_at TIMESTAMPTZ,
                refund_reason VARCHAR(512),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS tribute_shop_webhook_events (
                event_id SERIAL PRIMARY KEY,
                fingerprint VARCHAR(64) NOT NULL UNIQUE,
                event_name VARCHAR(64) NOT NULL,
                order_uuid VARCHAR(36) NOT NULL,
                event_created_at TIMESTAMPTZ NOT NULL,
                event_sent_at TIMESTAMPTZ NOT NULL,
                amount BIGINT,
                currency VARCHAR(16),
                transaction_id BIGINT,
                status VARCHAR(32) NOT NULL DEFAULT 'processing',
                status_reason VARCHAR(128),
                payment_id INTEGER REFERENCES payments(payment_id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                processed_at TIMESTAMPTZ
            )
            """
        )
    )
    for statement in (
        "CREATE INDEX IF NOT EXISTS ix_tribute_entitlements_subscription_id "
        "ON tribute_entitlements (tribute_subscription_id)",
        "CREATE INDEX IF NOT EXISTS ix_tribute_entitlements_telegram_user_id "
        "ON tribute_entitlements (telegram_user_id)",
        "CREATE INDEX IF NOT EXISTS ix_tribute_entitlements_user_id "
        "ON tribute_entitlements (user_id)",
        "CREATE INDEX IF NOT EXISTS ix_tribute_entitlements_tariff_key "
        "ON tribute_entitlements (tariff_key)",
        "CREATE INDEX IF NOT EXISTS ix_tribute_entitlements_status "
        "ON tribute_entitlements (status)",
        "CREATE INDEX IF NOT EXISTS ix_tribute_entitlements_active_until "
        "ON tribute_entitlements (active_until)",
        "CREATE INDEX IF NOT EXISTS ix_tribute_entitlements_last_event_created_at "
        "ON tribute_entitlements (last_event_created_at)",
        "CREATE INDEX IF NOT EXISTS ix_tribute_entitlements_telegram_subscription "
        "ON tribute_entitlements (telegram_user_id, tribute_subscription_id)",
        "CREATE INDEX IF NOT EXISTS ix_tribute_webhook_events_fingerprint "
        "ON tribute_webhook_events (fingerprint)",
        "CREATE INDEX IF NOT EXISTS ix_tribute_webhook_events_event_name "
        "ON tribute_webhook_events (event_name)",
        "CREATE INDEX IF NOT EXISTS ix_tribute_webhook_events_subscription_id "
        "ON tribute_webhook_events (tribute_subscription_id)",
        "CREATE INDEX IF NOT EXISTS ix_tribute_webhook_events_telegram_user_id "
        "ON tribute_webhook_events (telegram_user_id)",
        "CREATE INDEX IF NOT EXISTS ix_tribute_webhook_events_event_created_at "
        "ON tribute_webhook_events (event_created_at)",
        "CREATE INDEX IF NOT EXISTS ix_tribute_webhook_events_status "
        "ON tribute_webhook_events (status)",
        "CREATE INDEX IF NOT EXISTS ix_tribute_webhook_events_payment_id "
        "ON tribute_webhook_events (payment_id)",
        "CREATE INDEX IF NOT EXISTS ix_tribute_product_purchases_purchase_id "
        "ON tribute_product_purchases (tribute_purchase_id)",
        "CREATE INDEX IF NOT EXISTS ix_tribute_product_purchases_product_id "
        "ON tribute_product_purchases (tribute_product_id)",
        "CREATE INDEX IF NOT EXISTS ix_tribute_product_purchases_telegram_user_id "
        "ON tribute_product_purchases (telegram_user_id)",
        "CREATE INDEX IF NOT EXISTS ix_tribute_product_purchases_user_id "
        "ON tribute_product_purchases (user_id)",
        "CREATE INDEX IF NOT EXISTS ix_tribute_product_purchases_tariff_key "
        "ON tribute_product_purchases (tariff_key)",
        "CREATE INDEX IF NOT EXISTS ix_tribute_product_purchases_status "
        "ON tribute_product_purchases (status)",
        "CREATE INDEX IF NOT EXISTS ix_tribute_product_purchases_payment_id "
        "ON tribute_product_purchases (payment_id)",
        "CREATE INDEX IF NOT EXISTS ix_tribute_shop_webhook_events_fingerprint "
        "ON tribute_shop_webhook_events (fingerprint)",
        "CREATE INDEX IF NOT EXISTS ix_tribute_shop_webhook_events_event_name "
        "ON tribute_shop_webhook_events (event_name)",
        "CREATE INDEX IF NOT EXISTS ix_tribute_shop_webhook_events_order_uuid "
        "ON tribute_shop_webhook_events (order_uuid)",
        "CREATE INDEX IF NOT EXISTS ix_tribute_shop_webhook_events_event_created_at "
        "ON tribute_shop_webhook_events (event_created_at)",
        "CREATE INDEX IF NOT EXISTS ix_tribute_shop_webhook_events_transaction_id "
        "ON tribute_shop_webhook_events (transaction_id)",
        "CREATE INDEX IF NOT EXISTS ix_tribute_shop_webhook_events_status "
        "ON tribute_shop_webhook_events (status)",
        "CREATE INDEX IF NOT EXISTS ix_tribute_shop_webhook_events_payment_id "
        "ON tribute_shop_webhook_events (payment_id)",
    ):
        connection.execute(text(statement))


def _migration_0051_add_tariff_change_quote_snapshots(connection: Connection) -> None:
    """Freeze paid tariff-change quotes until their provider payment is fulfilled."""

    inspector = inspect(connection)
    if "payments" not in set(inspector.get_table_names()):
        return
    columns = {column["name"] for column in inspector.get_columns("payments")}
    if "tariff_change_quote_snapshot" not in columns:
        connection.execute(
            text("ALTER TABLE payments ADD COLUMN tariff_change_quote_snapshot TEXT")
        )


def _migration_0052_add_entitlement_context_snapshots(connection: Connection) -> None:
    """Bind one-time entitlement orders to the subscription they were quoted for."""

    inspector = inspect(connection)
    if "payments" not in set(inspector.get_table_names()):
        return
    columns = {column["name"] for column in inspector.get_columns("payments")}
    if "entitlement_context_snapshot" not in columns:
        connection.execute(
            text("ALTER TABLE payments ADD COLUMN entitlement_context_snapshot TEXT")
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
    Migration(
        id="0049_add_support_message_rich_body",
        description="Store the markup format and attached buttons of a ticket message",
        upgrade=_migration_0049_add_support_message_rich_body,
    ),
    Migration(
        id="0050_add_tribute_webhook_state",
        description="Persist Tribute Shop, subscription, and Digital Product webhook state",
        upgrade=_migration_0050_add_tribute_webhook_state,
    ),
    Migration(
        id="0051_add_tariff_change_quote_snapshots",
        description="Persist immutable paid tariff-change checkout quotes",
        upgrade=_migration_0051_add_tariff_change_quote_snapshots,
    ),
    Migration(
        id="0052_add_entitlement_context_snapshots",
        description="Bind one-time entitlement orders to their quoted subscription",
        upgrade=_migration_0052_add_entitlement_context_snapshots,
    ),
]
