from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

from db.migrator import chain_0046_0060
from db.models import Payment


class _RecordingConnection:
    dialect = postgresql.dialect()

    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: object) -> None:
        self.statements.append(str(statement))


def test_payment_model_has_nullable_tariff_change_quote_snapshot() -> None:
    column = Payment.__table__.columns["tariff_change_quote_snapshot"]

    assert isinstance(column.type, Text)
    assert column.nullable


def test_migration_adds_tariff_change_quote_snapshot_once() -> None:
    connection = _RecordingConnection()
    inspector = SimpleNamespace(
        get_table_names=lambda: ["payments"],
        get_columns=lambda _table: [{"name": "payment_id"}],
    )

    with patch.object(chain_0046_0060, "inspect", return_value=inspector):
        chain_0046_0060._migration_0051_add_tariff_change_quote_snapshots(connection)

    assert connection.statements == [
        "ALTER TABLE payments ADD COLUMN tariff_change_quote_snapshot TEXT"
    ]


def test_migration_is_idempotent_when_snapshot_column_exists() -> None:
    connection = _RecordingConnection()
    inspector = SimpleNamespace(
        get_table_names=lambda: ["payments"],
        get_columns=lambda _table: [{"name": "tariff_change_quote_snapshot"}],
    )

    with patch.object(chain_0046_0060, "inspect", return_value=inspector):
        chain_0046_0060._migration_0051_add_tariff_change_quote_snapshots(connection)

    assert connection.statements == []
