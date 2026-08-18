from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

from sqlalchemy.engine import Connection

from db.migrator.chain_0056_0070 import _migration_0063_reconcile_tgshop_promo_codes


class _Inspector:
    def __init__(self, *, has_table: bool = True, bonus_days_nullable: bool = True) -> None:
        self.has_table = has_table
        self.bonus_days_nullable = bonus_days_nullable

    def get_table_names(self) -> list[str]:
        return ["promo_codes"] if self.has_table else []

    def get_columns(self, table_name: str) -> list[dict[str, object]]:
        assert table_name == "promo_codes"
        return [
            {"name": "bonus_days", "nullable": self.bonus_days_nullable},
            {"name": "discount_percentage", "nullable": True},
            {"name": "discount_percent", "nullable": True},
        ]


class _RecordingConnection:
    def __init__(self, dialect: str = "postgresql") -> None:
        self.dialect = SimpleNamespace(name=dialect)
        self.statements: list[str] = []

    def execute(self, clause: object) -> None:
        self.statements.append(" ".join(str(clause).split()))


def test_tgshop_promo_migration_normalizes_rewards_and_constraint() -> None:
    connection = _RecordingConnection()

    with patch(
        "db.migrator.chain_0056_0070.inspect",
        return_value=_Inspector(),
    ):
        _migration_0063_reconcile_tgshop_promo_codes(cast(Connection, connection))

    assert connection.statements == [
        "UPDATE promo_codes SET bonus_days = 0 WHERE bonus_days IS NULL",
        (
            "UPDATE promo_codes SET discount_percent = discount_percentage "
            "WHERE discount_percent IS NULL AND discount_percentage BETWEEN 1 AND 100"
        ),
        "ALTER TABLE promo_codes ALTER COLUMN bonus_days SET NOT NULL",
    ]


def test_tgshop_promo_migration_skips_absent_table() -> None:
    connection = _RecordingConnection()

    with patch(
        "db.migrator.chain_0056_0070.inspect",
        return_value=_Inspector(has_table=False),
    ):
        _migration_0063_reconcile_tgshop_promo_codes(cast(Connection, connection))

    assert connection.statements == []
