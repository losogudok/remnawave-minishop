from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy.dialects import postgresql

from db.migrator import chain_0046_0060


class _RecordingConnection:
    dialect = postgresql.dialect()

    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: object) -> None:
        self.statements.append(str(statement))


def _inspector(*, complete: bool = True) -> SimpleNamespace:
    payment_columns = [
        "created_at",
        "sale_mode",
        "status",
        "subscription_duration_months",
        "user_id",
    ]
    subscription_columns = ["end_date", "is_active", "start_date", "user_id"]
    if not complete:
        payment_columns.remove("sale_mode")
    columns = {
        "payments": [{"name": name} for name in payment_columns],
        "subscriptions": [{"name": name} for name in subscription_columns],
    }
    return SimpleNamespace(
        get_table_names=lambda: ["payments", "subscriptions"],
        get_columns=lambda table: columns[table],
    )


def test_migration_repairs_active_starts_from_subscription_payments() -> None:
    connection = _RecordingConnection()

    with patch.object(chain_0046_0060, "inspect", return_value=_inspector()):
        chain_0046_0060._migration_0053_restore_active_subscription_start_dates(connection)

    assert len(connection.statements) == 1
    statement = " ".join(connection.statements[0].split())
    normalized_statement = statement.lower()
    assert "update subscriptions as subscription" in normalized_statement
    assert "subscription.is_active is true" in normalized_statement
    assert "subscription.end_date > now()" in normalized_statement
    assert "evidence.first_paid_at + interval '1 day'" in normalized_statement
    assert "subscription_duration_months" in normalized_statement
    assert "= 'subscription'" in normalized_statement


def test_migration_skips_incomplete_legacy_schema() -> None:
    connection = _RecordingConnection()

    with patch.object(chain_0046_0060, "inspect", return_value=_inspector(complete=False)):
        chain_0046_0060._migration_0053_restore_active_subscription_start_dates(connection)

    assert connection.statements == []
