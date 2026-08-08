"""Core migrations 0056 onward.

Keep this module append-only.
"""

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from .engine import Migration


def _migration_0056_add_tariff_binding_audit(connection: Connection) -> None:
    """Record how and when a subscription became linked to a tariff."""

    inspector = inspect(connection)
    if "subscriptions" not in set(inspector.get_table_names()):
        return
    columns = {column["name"] for column in inspector.get_columns("subscriptions")}
    additions = {
        "tariff_binding_source": "VARCHAR(32)",
        "tariff_bound_at": "TIMESTAMPTZ",
        "tariff_binding_note": "VARCHAR(255)",
    }
    for column, definition in additions.items():
        if column not in columns:
            connection.execute(text(f"ALTER TABLE subscriptions ADD COLUMN {column} {definition}"))
    connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_subscriptions_tariff_binding_source "
            "ON subscriptions (tariff_binding_source)"
        )
    )


def _migration_0057_add_platega_subscriptions(connection: Connection) -> None:
    """Mirror Platega SBP subscription mandates so renewals stay attributable."""

    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS platega_subscriptions (
                id SERIAL PRIMARY KEY,
                platega_subscription_id VARCHAR NOT NULL UNIQUE,
                user_id BIGINT NOT NULL REFERENCES users(user_id),
                status VARCHAR(32) NOT NULL DEFAULT 'active',
                amount DOUBLE PRECISION NOT NULL,
                currency VARCHAR NOT NULL,
                interval_code INTEGER NOT NULL,
                months INTEGER NOT NULL,
                sale_mode VARCHAR,
                tariff_key VARCHAR,
                next_charge_at TIMESTAMPTZ,
                last_charge_at TIMESTAMPTZ,
                charges_count INTEGER NOT NULL DEFAULT 0,
                cancelled_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    for statement in (
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_platega_subscriptions_platega_subscription_id "
        "ON platega_subscriptions (platega_subscription_id)",
        "CREATE INDEX IF NOT EXISTS ix_platega_subscriptions_user_id "
        "ON platega_subscriptions (user_id)",
        "CREATE INDEX IF NOT EXISTS ix_platega_subscriptions_status "
        "ON platega_subscriptions (status)",
        "CREATE INDEX IF NOT EXISTS ix_platega_subscriptions_tariff_key "
        "ON platega_subscriptions (tariff_key)",
        "CREATE INDEX IF NOT EXISTS ix_platega_subscriptions_user_status "
        "ON platega_subscriptions (user_id, status)",
    ):
        connection.execute(text(statement))


def _migration_0058_add_partner_program(connection: Connection) -> None:
    """Add the independent partner attribution and append-only money ledger."""

    inspector = inspect(connection)
    payment_columns = {column["name"] for column in inspector.get_columns("payments")}
    if "funding_source" not in payment_columns:
        connection.execute(
            text(
                "ALTER TABLE payments ADD COLUMN funding_source VARCHAR(48) "
                "NOT NULL DEFAULT 'external'"
            )
        )
    connection.execute(
        text("CREATE INDEX IF NOT EXISTS ix_payments_funding_source ON payments (funding_source)")
    )

    statements = (
        """
        CREATE TABLE IF NOT EXISTS partner_profiles (
            partner_id BIGSERIAL PRIMARY KEY,
            user_id BIGINT UNIQUE REFERENCES users(user_id) ON DELETE SET NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'paused', 'closed')),
            commission_bps INTEGER NOT NULL DEFAULT 3000
                CHECK (commission_bps >= 0 AND commission_bps <= 10000),
            partner_code VARCHAR(64) NOT NULL UNIQUE,
            display_label_snapshot VARCHAR(255) NOT NULL,
            welcome_message TEXT,
            pause_reason TEXT,
            reapply_allowed_at TIMESTAMPTZ,
            activated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            paused_at TIMESTAMPTZ,
            closed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS partner_applications (
            application_id BIGSERIAL PRIMARY KEY,
            user_id BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
            display_label_snapshot VARCHAR(255) NOT NULL,
            message TEXT NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'approved', 'rejected', 'canceled')),
            submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            decided_at TIMESTAMPTZ,
            decided_by_admin_id BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
            decision_message TEXT,
            approved_commission_bps INTEGER,
            welcome_message TEXT,
            reapply_allowed_at TIMESTAMPTZ
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS partner_clients (
            partner_client_id BIGSERIAL PRIMARY KEY,
            partner_id BIGINT NOT NULL REFERENCES partner_profiles(partner_id) ON DELETE RESTRICT,
            client_user_id BIGINT UNIQUE REFERENCES users(user_id) ON DELETE SET NULL,
            public_client_id VARCHAR(32) NOT NULL UNIQUE,
            public_label_snapshot VARCHAR(255) NOT NULL,
            source VARCHAR(32) NOT NULL CHECK (
                source IN ('partner_telegram_link', 'partner_web_link',
                           'referral_import', 'admin_manual')
            ),
            attributed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            eligible_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            attributed_by_admin_id BIGINT REFERENCES users(user_id) ON DELETE SET NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS partner_commissions (
            commission_id BIGSERIAL PRIMARY KEY,
            partner_id BIGINT NOT NULL REFERENCES partner_profiles(partner_id) ON DELETE RESTRICT,
            partner_client_id BIGINT NOT NULL
                REFERENCES partner_clients(partner_client_id) ON DELETE RESTRICT,
            payment_id INTEGER UNIQUE REFERENCES payments(payment_id) ON DELETE SET NULL,
            gross_amount_minor BIGINT NOT NULL CHECK (gross_amount_minor >= 0),
            commission_amount_minor BIGINT NOT NULL CHECK (commission_amount_minor >= 0),
            currency VARCHAR(16) NOT NULL,
            currency_scale INTEGER NOT NULL,
            commission_bps_snapshot INTEGER NOT NULL,
            sale_mode_snapshot VARCHAR(64),
            provider_snapshot VARCHAR(64),
            status VARCHAR(16) NOT NULL
                CHECK (status IN ('pending', 'available', 'reversed', 'excluded')),
            exclusion_reason VARCHAR(64),
            source_paid_at TIMESTAMPTZ NOT NULL,
            available_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            reversed_at TIMESTAMPTZ
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS partner_ledger_entries (
            entry_id BIGSERIAL PRIMARY KEY,
            partner_id BIGINT NOT NULL REFERENCES partner_profiles(partner_id) ON DELETE RESTRICT,
            currency VARCHAR(16) NOT NULL,
            currency_scale INTEGER NOT NULL,
            amount_minor BIGINT NOT NULL,
            kind VARCHAR(32) NOT NULL CHECK (kind IN (
                'commission_credit', 'manual_adjustment', 'withdrawal_reserve',
                'withdrawal_release', 'subscription_spend', 'subscription_spend_release',
                'commission_reversal'
            )),
            state VARCHAR(16) NOT NULL DEFAULT 'posted'
                CHECK (state IN ('pending', 'posted', 'void')),
            reference_type VARCHAR(32) NOT NULL,
            reference_id VARCHAR(64) NOT NULL,
            idempotency_key VARCHAR(128) NOT NULL UNIQUE,
            actor_admin_id BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
            reason TEXT,
            metadata_json TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            posted_at TIMESTAMPTZ
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS partner_withdrawals (
            withdrawal_id BIGSERIAL PRIMARY KEY,
            partner_id BIGINT NOT NULL REFERENCES partner_profiles(partner_id) ON DELETE RESTRICT,
            method_id_snapshot VARCHAR(64) NOT NULL,
            method_type_snapshot VARCHAR(32) NOT NULL,
            method_snapshot_json TEXT NOT NULL,
            debit_amount_minor BIGINT NOT NULL CHECK (debit_amount_minor > 0),
            debit_currency VARCHAR(16) NOT NULL,
            currency_scale INTEGER NOT NULL,
            settlement_asset VARCHAR(16),
            network VARCHAR(64),
            status VARCHAR(16) NOT NULL DEFAULT 'requested' CHECK (
                status IN ('requested', 'processing', 'paid', 'rejected', 'canceled', 'failed')
            ),
            status_version INTEGER NOT NULL DEFAULT 1,
            status_message TEXT,
            external_reference VARCHAR(255),
            settlement_amount VARCHAR(64),
            requisites_ciphertext BYTEA,
            requisites_key_id VARCHAR(32) NOT NULL,
            masked_requisites VARCHAR(255) NOT NULL,
            client_idempotency_key VARCHAR(128) NOT NULL UNIQUE,
            requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            processing_at TIMESTAMPTZ,
            paid_at TIMESTAMPTZ,
            decided_at TIMESTAMPTZ,
            handled_by_admin_id BIGINT REFERENCES users(user_id) ON DELETE SET NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS partner_audit_events (
            audit_event_id BIGSERIAL PRIMARY KEY,
            partner_id BIGINT REFERENCES partner_profiles(partner_id) ON DELETE RESTRICT,
            application_id BIGINT
                REFERENCES partner_applications(application_id) ON DELETE SET NULL,
            withdrawal_id BIGINT REFERENCES partner_withdrawals(withdrawal_id) ON DELETE SET NULL,
            event_type VARCHAR(64) NOT NULL,
            actor_type VARCHAR(16) NOT NULL,
            actor_user_id BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
            old_values_json TEXT,
            new_values_json TEXT,
            reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )
    for statement in statements:
        connection.execute(text(statement))

    indexes = (
        "CREATE INDEX IF NOT EXISTS ix_partner_profiles_user_id ON partner_profiles (user_id)",
        "CREATE INDEX IF NOT EXISTS ix_partner_profiles_status ON partner_profiles (status)",
        "CREATE INDEX IF NOT EXISTS ix_partner_profiles_status_created "
        "ON partner_profiles (status, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_partner_applications_status_submitted "
        "ON partner_applications (status, submitted_at)",
        "CREATE INDEX IF NOT EXISTS ix_partner_applications_user_submitted "
        "ON partner_applications (user_id, submitted_at)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_partner_applications_pending_user "
        "ON partner_applications (user_id) WHERE status = 'pending' AND user_id IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS ix_partner_clients_partner_attributed "
        "ON partner_clients (partner_id, attributed_at)",
        "CREATE INDEX IF NOT EXISTS ix_partner_commissions_partner_created "
        "ON partner_commissions (partner_id, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_partner_commissions_status_available "
        "ON partner_commissions (status, available_at)",
        "CREATE INDEX IF NOT EXISTS ix_partner_ledger_partner_currency "
        "ON partner_ledger_entries (partner_id, currency, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_partner_ledger_reference "
        "ON partner_ledger_entries (reference_type, reference_id)",
        "CREATE INDEX IF NOT EXISTS ix_partner_withdrawals_status_requested "
        "ON partner_withdrawals (status, requested_at)",
        "CREATE INDEX IF NOT EXISTS ix_partner_withdrawals_partner_requested "
        "ON partner_withdrawals (partner_id, requested_at)",
        "CREATE INDEX IF NOT EXISTS ix_partner_audit_partner_created "
        "ON partner_audit_events (partner_id, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_partner_audit_event_created "
        "ON partner_audit_events (event_type, created_at)",
    )
    for statement in indexes:
        connection.execute(text(statement))


CHAIN_0056_0070: list[Migration] = [
    Migration(
        id="0056_add_tariff_binding_audit",
        description="Record subscription tariff-binding provenance and repair metadata",
        upgrade=_migration_0056_add_tariff_binding_audit,
    ),
    Migration(
        id="0057_add_platega_subscriptions",
        description="Track Platega SBP subscription mandates for provider-managed renewals",
        upgrade=_migration_0057_add_platega_subscriptions,
    ),
    Migration(
        id="0058_add_partner_program",
        description="Add partner profiles, attribution, commission decisions and money ledger",
        upgrade=_migration_0058_add_partner_program,
    ),
]
