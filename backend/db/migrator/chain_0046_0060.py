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


def _migration_0053_restore_active_subscription_start_dates(connection: Connection) -> None:
    """Restore immutable starts that legacy renewal writes moved forward."""

    inspector = inspect(connection)
    table_names = set(inspector.get_table_names())
    if not {"payments", "subscriptions"}.issubset(table_names):
        return

    payment_columns = {column["name"] for column in inspector.get_columns("payments")}
    subscription_columns = {column["name"] for column in inspector.get_columns("subscriptions")}
    required_payment_columns = {
        "created_at",
        "sale_mode",
        "status",
        "subscription_duration_months",
        "user_id",
    }
    required_subscription_columns = {
        "end_date",
        "is_active",
        "start_date",
        "user_id",
    }
    if not required_payment_columns.issubset(payment_columns) or not (
        required_subscription_columns.issubset(subscription_columns)
    ):
        return

    connection.execute(
        text(
            """
            WITH first_subscription_payment AS (
                SELECT
                    user_id,
                    MIN(created_at) AS first_paid_at
                FROM payments
                WHERE status = 'succeeded'
                  AND created_at IS NOT NULL
                  AND created_at <= NOW()
                  AND COALESCE(subscription_duration_months, 0) > 0
                  AND (
                      sale_mode IS NULL
                      OR LOWER(
                          SPLIT_PART(SPLIT_PART(TRIM(sale_mode), '@', 1), '|', 1)
                      ) = 'subscription'
                  )
                GROUP BY user_id
            )
            UPDATE subscriptions AS subscription
            SET start_date = evidence.first_paid_at
            FROM first_subscription_payment AS evidence
            WHERE subscription.user_id = evidence.user_id
              AND subscription.is_active IS TRUE
              AND subscription.end_date > NOW()
              AND (
                  subscription.start_date IS NULL
                  OR subscription.start_date > evidence.first_paid_at + INTERVAL '1 day'
              )
            """
        )
    )


def _migration_0054_add_payment_checkout_lifecycle(connection: Connection) -> None:
    """Persist authoritative hosted-checkout expiry and reconciliation cadence."""

    inspector = inspect(connection)
    if "payments" not in set(inspector.get_table_names()):
        return
    columns = {column["name"] for column in inspector.get_columns("payments")}
    additions = {
        "checkout_expires_at": "TIMESTAMPTZ",
        "failure_notified_at": "TIMESTAMPTZ",
        "provider_checked_at": "TIMESTAMPTZ",
    }
    for column, definition in additions.items():
        if column not in columns:
            connection.execute(text(f"ALTER TABLE payments ADD COLUMN {column} {definition}"))
    connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_payments_checkout_expires_at "
            "ON payments (checkout_expires_at)"
        )
    )
    connection.execute(
        text(
            "UPDATE payments "
            "SET failure_notified_at = COALESCE(updated_at, created_at, NOW()) "
            "WHERE failure_notified_at IS NULL "
            "AND LOWER(TRIM(status)) IN "
            "('failed', 'canceled', 'cancelled', 'failed_creation')"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_payments_failure_notified_at "
            "ON payments (failure_notified_at)"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_payments_provider_checked_at "
            "ON payments (provider_checked_at)"
        )
    )


def _migration_0055_add_auto_renew_retry_state(connection: Connection) -> None:
    """Persist bounded retry orchestration and customer-consent revisions."""

    inspector = inspect(connection)
    table_names = set(inspector.get_table_names())
    if "subscriptions" in table_names:
        subscription_columns = {column["name"] for column in inspector.get_columns("subscriptions")}
        if "auto_renew_consent_version" not in subscription_columns:
            connection.execute(
                text(
                    "ALTER TABLE subscriptions "
                    "ADD COLUMN auto_renew_consent_version INTEGER NOT NULL DEFAULT 0"
                )
            )

    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS auto_renew_cycles (
                cycle_id SERIAL PRIMARY KEY,
                subscription_id INTEGER NOT NULL REFERENCES subscriptions(subscription_id),
                user_id BIGINT NOT NULL REFERENCES users(user_id),
                provider VARCHAR(32) NOT NULL,
                cycle_anchor DATE NOT NULL,
                renewal_cycle_end TIMESTAMPTZ NOT NULL,
                state VARCHAR(32) NOT NULL DEFAULT 'scheduled',
                base_idempotence_key VARCHAR(64) NOT NULL UNIQUE,
                consent_version INTEGER NOT NULL DEFAULT 0,
                payment_method_id INTEGER
                    REFERENCES user_payment_methods(method_id) ON DELETE SET NULL,
                payment_method_provider_id VARCHAR NOT NULL,
                financial_attempts INTEGER NOT NULL DEFAULT 0,
                transport_replays INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TIMESTAMPTZ,
                lease_expires_at TIMESTAMPTZ,
                current_payment_id INTEGER,
                request_snapshot TEXT NOT NULL,
                last_failure_kind VARCHAR(64),
                last_http_status INTEGER,
                last_provider_code VARCHAR(128),
                cancellation_party VARCHAR(64),
                cancellation_reason VARCHAR(128),
                stopped_reason VARCHAR(128),
                retry_notified_at TIMESTAMPTZ,
                completed_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT uq_auto_renew_cycles_subscription_anchor
                    UNIQUE (subscription_id, cycle_anchor)
            )
            """
        )
    )
    for statement in (
        "CREATE INDEX IF NOT EXISTS ix_auto_renew_cycles_subscription_id "
        "ON auto_renew_cycles (subscription_id)",
        "CREATE INDEX IF NOT EXISTS ix_auto_renew_cycles_user_id ON auto_renew_cycles (user_id)",
        "CREATE INDEX IF NOT EXISTS ix_auto_renew_cycles_provider ON auto_renew_cycles (provider)",
        "CREATE INDEX IF NOT EXISTS ix_auto_renew_cycles_renewal_cycle_end "
        "ON auto_renew_cycles (renewal_cycle_end)",
        "CREATE INDEX IF NOT EXISTS ix_auto_renew_cycles_state ON auto_renew_cycles (state)",
        "CREATE INDEX IF NOT EXISTS ix_auto_renew_cycles_next_attempt_at "
        "ON auto_renew_cycles (next_attempt_at)",
        "CREATE INDEX IF NOT EXISTS ix_auto_renew_cycles_lease_expires_at "
        "ON auto_renew_cycles (lease_expires_at)",
        "CREATE INDEX IF NOT EXISTS ix_auto_renew_cycles_current_payment_id "
        "ON auto_renew_cycles (current_payment_id)",
        "CREATE INDEX IF NOT EXISTS ix_auto_renew_cycles_payment_method_id "
        "ON auto_renew_cycles (payment_method_id)",
        "CREATE INDEX IF NOT EXISTS ix_auto_renew_cycles_cancellation_reason "
        "ON auto_renew_cycles (cancellation_reason)",
        "CREATE INDEX IF NOT EXISTS ix_auto_renew_cycles_stopped_reason "
        "ON auto_renew_cycles (stopped_reason)",
        "CREATE INDEX IF NOT EXISTS ix_auto_renew_cycles_state_next_attempt "
        "ON auto_renew_cycles (state, next_attempt_at)",
        "CREATE INDEX IF NOT EXISTS ix_auto_renew_cycles_user_state "
        "ON auto_renew_cycles (user_id, state)",
    ):
        connection.execute(text(statement))

    if "payments" not in table_names:
        return
    payment_columns = {column["name"] for column in inspector.get_columns("payments")}
    additions = {
        "auto_renew_cycle_id": (
            "INTEGER REFERENCES auto_renew_cycles(cycle_id) ON DELETE SET NULL"
        ),
        "renewal_attempt_number": "INTEGER",
        "renewal_consent_version": "INTEGER",
        "renewal_payment_method_id": "INTEGER",
        "provider_request_snapshot": "TEXT",
        "failure_kind": "VARCHAR(64)",
        "failure_http_status": "INTEGER",
        "failure_provider_code": "VARCHAR(128)",
        "provider_cancellation_party": "VARCHAR(64)",
        "provider_cancellation_reason": "VARCHAR(128)",
    }
    for column, definition in additions.items():
        if column not in payment_columns:
            connection.execute(text(f"ALTER TABLE payments ADD COLUMN {column} {definition}"))
    for statement in (
        "CREATE INDEX IF NOT EXISTS ix_payments_auto_renew_cycle_id "
        "ON payments (auto_renew_cycle_id)",
        "CREATE INDEX IF NOT EXISTS ix_payments_renewal_payment_method_id "
        "ON payments (renewal_payment_method_id)",
        "CREATE INDEX IF NOT EXISTS ix_payments_failure_kind ON payments (failure_kind)",
        "CREATE INDEX IF NOT EXISTS ix_payments_provider_cancellation_reason "
        "ON payments (provider_cancellation_reason)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_payments_auto_renew_cycle_attempt "
        "ON payments (auto_renew_cycle_id, renewal_attempt_number) "
        "WHERE auto_renew_cycle_id IS NOT NULL AND renewal_attempt_number IS NOT NULL",
    ):
        connection.execute(text(statement))


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
    Migration(
        id="0053_restore_active_subscription_start_dates",
        description="Restore immutable active subscription starts after legacy renewals",
        upgrade=_migration_0053_restore_active_subscription_start_dates,
    ),
    Migration(
        id="0054_add_payment_checkout_lifecycle",
        description="Persist hosted-checkout expiry and provider reconciliation cadence",
        upgrade=_migration_0054_add_payment_checkout_lifecycle,
    ),
    Migration(
        id="0055_add_auto_renew_retry_state",
        description="Persist bounded auto-renew retry orchestration and consent revisions",
        upgrade=_migration_0055_add_auto_renew_retry_state,
    ),
]
