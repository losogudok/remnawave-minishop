from unittest.mock import MagicMock, patch

from db.migrator import chain_0046_0060
from db.models import Payment


def test_payment_model_has_nullable_entitlement_context_snapshot() -> None:
    column = Payment.__table__.columns["entitlement_context_snapshot"]

    assert column.nullable is True


def test_migration_adds_entitlement_context_snapshot_once() -> None:
    connection = MagicMock()

    with patch.object(
        chain_0046_0060,
        "inspect",
        return_value=MagicMock(
            get_table_names=lambda: ["payments"],
            get_columns=lambda _table: [],
        ),
    ):
        chain_0046_0060._migration_0052_add_entitlement_context_snapshots(connection)

    statement = str(connection.execute.call_args.args[0])
    assert statement == ("ALTER TABLE payments ADD COLUMN entitlement_context_snapshot TEXT")

    connection.reset_mock()
    with patch.object(
        chain_0046_0060,
        "inspect",
        return_value=MagicMock(
            get_table_names=lambda: ["payments"],
            get_columns=lambda _table: [{"name": "entitlement_context_snapshot"}],
        ),
    ):
        chain_0046_0060._migration_0052_add_entitlement_context_snapshots(connection)

    connection.execute.assert_not_called()
