from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from db.migrator import MIGRATIONS, run_database_migrations
from db.models import Base


def applied_migration_ids(connection: Connection) -> set[str]:
    inspector = inspect(connection)
    if "schema_migrations" not in inspector.get_table_names():
        return set()
    return {str(row[0]) for row in connection.execute(text("SELECT id FROM schema_migrations"))}


def create_missing_tables_and_migrate(connection: Connection) -> list[str]:
    before = applied_migration_ids(connection)
    Base.metadata.create_all(connection)
    run_database_migrations(connection)
    after = applied_migration_ids(connection)
    newly_applied = after - before
    return [migration.id for migration in MIGRATIONS if migration.id in newly_applied]


def normalize_serial_sequences(connection: Connection) -> list[str]:
    rows = connection.execute(
        text(
            """
            SELECT
                table_schema,
                table_name,
                column_name,
                pg_get_serial_sequence(
                    format('%I.%I', table_schema, table_name),
                    column_name
                ) AS sequence_name
            FROM information_schema.columns
            WHERE table_schema = ANY(current_schemas(FALSE))
              AND column_default LIKE 'nextval(%'
            ORDER BY table_schema, table_name, ordinal_position
            """
        )
    ).mappings()
    preparer = connection.dialect.identifier_preparer
    normalized: list[str] = []
    for row in rows:
        sequence_name = str(row["sequence_name"] or "").strip()
        if not sequence_name:
            continue
        schema_name = str(row["table_schema"])
        table_name = str(row["table_name"])
        column_name = str(row["column_name"])
        qualified_table = f"{preparer.quote_schema(schema_name)}.{preparer.quote(table_name)}"
        quoted_column = preparer.quote(column_name)
        max_value = connection.scalar(text(f"SELECT MAX({quoted_column}) FROM {qualified_table}"))
        connection.execute(
            text("SELECT setval(to_regclass(:sequence_name), :target_value, :is_called)"),
            {
                "sequence_name": sequence_name,
                "target_value": int(max_value) if max_value is not None else 1,
                "is_called": max_value is not None,
            },
        )
        normalized.append(f"{schema_name}.{table_name}.{column_name}")
    return normalized
