"""Disposable PostgreSQL integration tests for legacy suppression persistence.

Run only against the dedicated local database with
DAYANCHOR_OUTBOX_TEST_DATABASE_URL set. Tests create and drop isolated schemas
only after validating the exact loopback database target.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
from pathlib import Path
from threading import Barrier
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
import pytest

import notification_suppression as suppression


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = (
    ROOT / "migrations" / "0001_notification_outbox.sql",
    ROOT / "migrations" / "0002_legacy_notification_suppressions.sql",
)
TEST_DSN_ENV = "DAYANCHOR_OUTBOX_TEST_DATABASE_URL"
TEST_PORT = "55432"
ALLOWED_DSN_PARAMETERS = {"host", "port", "dbname", "user", "password"}
BLOCKED_ENV_NAMES = {
    "DATABASE_URL",
    "DATABASE_PUBLIC_URL",
    "DATABASE_PRIVATE_URL",
    "POSTGRES_URL",
    "POSTGRESQL_URL",
}
UNMIGRATED_TESTS = {
    "test_migrations_apply_to_empty_schema_and_are_repeatable",
    "test_missing_schema_fails_closed",
}
NOW = datetime(2026, 11, 22, 23, 0, tzinfo=timezone.utc)


def _validated_test_dsn(environ):
    blocked = sorted(
        name
        for name in environ
        if name.startswith(("PG", "RAILWAY_")) or name in BLOCKED_ENV_NAMES
    )
    if blocked:
        raise ValueError(
            "Application database environment variables must be unset: "
            + ", ".join(blocked)
            + "."
        )

    raw_dsn = environ.get(TEST_DSN_ENV)
    if not raw_dsn:
        raise ValueError(f"Set {TEST_DSN_ENV} to the dedicated local test database URL.")
    parsed_url = urlsplit(raw_dsn)
    if parsed_url.scheme not in {"postgres", "postgresql"}:
        raise ValueError("The suppression test DSN must be a PostgreSQL URL.")
    if parsed_url.query or parsed_url.fragment:
        raise ValueError("The suppression test DSN must not contain query parameters or a fragment.")
    if parsed_url.hostname != "127.0.0.1":
        raise ValueError("The suppression test DSN host must be exactly 127.0.0.1.")
    if parsed_url.port != int(TEST_PORT) or parsed_url.path != "/outbox_test":
        raise ValueError("The suppression test DSN must target port 55432 and database outbox_test.")
    if parsed_url.username != "outbox_test" or not parsed_url.password:
        raise ValueError("The suppression test DSN must use outbox_test and a test-only password.")

    try:
        parameters = conninfo_to_dict(raw_dsn)
    except psycopg.Error as exc:
        raise ValueError("The suppression test DSN is invalid.") from exc

    if set(parameters) != ALLOWED_DSN_PARAMETERS:
        raise ValueError("The suppression test DSN contains missing or disallowed parameters.")
    if (
        parameters.get("host") != "127.0.0.1"
        or parameters.get("port") != TEST_PORT
        or parameters.get("dbname") != "outbox_test"
        or parameters.get("user") != "outbox_test"
        or not parameters.get("password")
    ):
        raise ValueError("The parsed DSN does not match the disposable test database.")
    return raw_dsn


@pytest.fixture(scope="session")
def test_dsn():
    try:
        return _validated_test_dsn(os.environ)
    except ValueError as exc:
        raise pytest.UsageError(str(exc)) from exc


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://outbox_test:secret@remote.example:55432/outbox_test",
        "postgresql://outbox_test:secret@/outbox_test",
        "postgresql://outbox_test:secret@localhost:55432/outbox_test",
        "postgresql://other:secret@127.0.0.1:55432/outbox_test",
        "postgresql://outbox_test:secret@127.0.0.1:55432/other_db",
        "postgresql://outbox_test:secret@127.0.0.1:55432/outbox_test?service=other",
        "postgresql://outbox_test:secret@127.0.0.1:55432/outbox_test?servicefile=/tmp/pg_service.conf",
    ],
)
def test_dsn_validation_rejects_non_disposable_targets(dsn):
    with pytest.raises(ValueError):
        _validated_test_dsn({TEST_DSN_ENV: dsn})


def _assert_test_database(connection):
    database, user = connection.execute(
        "SELECT current_database(), current_user"
    ).fetchone()
    if (database, user, connection.info.host, str(connection.info.port)) != (
        "outbox_test",
        "outbox_test",
        "127.0.0.1",
        TEST_PORT,
    ):
        connection.close()
        raise RuntimeError("Connection did not reach the exact disposable loopback test database.")
    connection.commit()


def _connect(test_dsn, schema=None):
    connection = psycopg.connect(test_dsn)
    _assert_test_database(connection)
    if schema is not None:
        connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        connection.commit()
    return connection


def _apply_migrations(connection):
    with connection.transaction():
        with connection.cursor() as cursor:
            for migration in MIGRATIONS:
                statements = [
                    statement.strip()
                    for statement in migration.read_text().split(";")
                    if statement.strip()
                ]
                for statement in statements:
                    cursor.execute(statement)


def _has_table(connection, schema, table):
    return connection.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_class AS relation
            JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = %s AND relation.relname = %s AND relation.relkind = 'r'
        )
        """,
        (schema, table),
    ).fetchone()[0]


@pytest.fixture
def suppression_db(request, test_dsn):
    schema = f"suppression_case_{uuid4().hex}"
    with psycopg.connect(test_dsn) as admin:
        _assert_test_database(admin)
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        admin.commit()

    try:
        if request.node.name not in UNMIGRATED_TESTS:
            with _connect(test_dsn, schema) as migration_connection:
                _apply_migrations(migration_connection)
        yield test_dsn, schema
    finally:
        with psycopg.connect(test_dsn) as admin:
            _assert_test_database(admin)
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
            admin.commit()


def _record(test_dsn, schema, **overrides):
    values = {
        "legacy_key": "reminder:reminder-1:2026-11-22:primary",
        "legacy_recorded_at": NOW,
        "destination_type": "telegram",
        "destination_id": "chat-1",
        "destination_verified": True,
        "source_type": "quick_reminder",
        "source_key": "reminder-source-1:2026-11-22:primary",
        "provenance": "settings.telegram_alert_history",
        "migration_batch_id": "batch-001",
    }
    values.update(overrides)
    with _connect(test_dsn, schema) as connection:
        return suppression.record_legacy_suppression(connection, **values)


def _lookup(test_dsn, schema, **overrides):
    values = {
        "destination_type": "telegram",
        "destination_id": "chat-1",
        "source_type": "quick_reminder",
        "source_key": "reminder-source-1:2026-11-22:primary",
        "destination_verified": True,
    }
    values.update(overrides)
    with _connect(test_dsn, schema) as connection:
        return suppression.lookup_suppression(connection, **values)


def test_migrations_apply_to_empty_schema_and_are_repeatable(suppression_db):
    test_dsn, schema = suppression_db
    with _connect(test_dsn, schema) as connection:
        assert not _has_table(connection, schema, "notification_outbox")
        assert not _has_table(connection, schema, "legacy_notification_suppressions")

        _apply_migrations(connection)
        assert _has_table(connection, schema, "notification_outbox")
        assert _has_table(connection, schema, "legacy_notification_suppressions")

        _apply_migrations(connection)
        assert _has_table(connection, schema, "notification_outbox")
        assert _has_table(connection, schema, "legacy_notification_suppressions")


def test_migration_creates_actual_constraints_and_indexes(suppression_db):
    test_dsn, schema = suppression_db
    with _connect(test_dsn, schema) as connection:
        constraints = {
            row[0]
            for row in connection.execute(
                """
                SELECT constraint_row.conname
                FROM pg_catalog.pg_constraint AS constraint_row
                JOIN pg_catalog.pg_class AS table_row ON table_row.oid = constraint_row.conrelid
                JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = table_row.relnamespace
                WHERE namespace.nspname = %s AND table_row.relname = 'legacy_notification_suppressions'
                """,
                (schema,),
            ).fetchall()
        }
        indexes = {
            row[0]
            for row in connection.execute(
                """
                SELECT index_row.relname
                FROM pg_catalog.pg_index AS index_info
                JOIN pg_catalog.pg_class AS table_row ON table_row.oid = index_info.indrelid
                JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = table_row.relnamespace
                JOIN pg_catalog.pg_class AS index_row ON index_row.oid = index_info.indexrelid
                WHERE namespace.nspname = %s AND table_row.relname = 'legacy_notification_suppressions'
                """,
                (schema,),
            ).fetchall()
        }

        assert {
            "legacy_notification_suppressions_key_check",
            "legacy_notification_suppressions_source_pair_check",
            "legacy_notification_suppressions_status_check",
            "legacy_notification_suppressions_verification_check",
            "legacy_notification_suppressions_resolved_check",
        } <= constraints
        assert {
            "legacy_notification_suppressions_import_unique",
            "legacy_notification_suppressions_lookup_idx",
            "legacy_notification_suppressions_unresolved_idx",
        } <= indexes

        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                """
                INSERT INTO legacy_notification_suppressions (
                    legacy_key, destination_type, provenance, migration_batch_id, resolution_status
                ) VALUES ('invalid:row', 'telegram', 'test', 'batch', 'sent')
                """
            )
        connection.rollback()


def test_exact_destination_match_suppresses_and_other_destination_is_clear(suppression_db):
    test_dsn, schema = suppression_db
    entry = _record(test_dsn, schema)

    assert entry["resolution_status"] == "resolved_suppressed"
    assert _lookup(test_dsn, schema).decision == "suppressed"
    assert _lookup(test_dsn, schema, destination_id="chat-2").decision == "clear"


def test_unknown_destination_history_blocks_clearance_for_all_destinations(suppression_db):
    test_dsn, schema = suppression_db
    entry = _record(
        test_dsn,
        schema,
        destination_id=None,
        destination_verified=False,
        source_type=None,
        source_key=None,
    )

    assert entry["resolution_status"] == "destination_unverified"
    assert _lookup(test_dsn, schema, destination_id="chat-2").decision == "blocked"


def test_unresolved_mapped_history_blocks_clearance(suppression_db):
    test_dsn, schema = suppression_db
    entry = _record(
        test_dsn,
        schema,
        ambiguous=True,
        resolution_note="Multiple persisted source candidates.",
    )

    assert entry["source_type"] == "quick_reminder"
    assert entry["source_key"] == "reminder-source-1:2026-11-22:primary"
    assert entry["resolution_status"] == "ambiguous"
    assert _lookup(test_dsn, schema).decision == "blocked"


def test_explicit_verified_resolution_suppresses_exact_identity(suppression_db):
    test_dsn, schema = suppression_db
    entry = _record(test_dsn, schema, destination_verified=True)

    assert entry["destination_verified_at"] is not None
    assert entry["resolution_status"] == "resolved_suppressed"
    assert _lookup(test_dsn, schema).decision == "suppressed"


def test_unverified_destination_cannot_be_recorded_as_resolved(suppression_db):
    test_dsn, schema = suppression_db
    entry = _record(test_dsn, schema, destination_verified=False)

    assert entry["destination_verified_at"] is None
    assert entry["resolution_status"] == "destination_unverified"
    assert _lookup(test_dsn, schema).decision == "blocked"


def test_repeat_import_is_idempotent(suppression_db):
    test_dsn, schema = suppression_db

    first = _record(test_dsn, schema)
    repeated = _record(test_dsn, schema)

    assert first["id"] == repeated["id"]
    with _connect(test_dsn, schema) as connection:
        count = connection.execute(
            "SELECT count(*) FROM legacy_notification_suppressions"
        ).fetchone()[0]
    assert count == 1


def test_conflicting_duplicate_import_is_rejected_without_overwrite(suppression_db):
    test_dsn, schema = suppression_db
    first = _record(test_dsn, schema)

    with pytest.raises(suppression.SuppressionImportConflict):
        _record(test_dsn, schema, legacy_recorded_at=None)

    with _connect(test_dsn, schema) as connection:
        saved = connection.execute(
            "SELECT legacy_recorded_at FROM legacy_notification_suppressions WHERE id = %s",
            (first["id"],),
        ).fetchone()[0]
    assert saved == NOW


def test_concurrent_imports_of_identical_identity_return_one_row(suppression_db):
    test_dsn, schema = suppression_db
    barrier = Barrier(2, timeout=10)

    def import_same_row():
        barrier.wait()
        return _record(test_dsn, schema)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(import_same_row)
        second_future = executor.submit(import_same_row)
        first = first_future.result(timeout=20)
        second = second_future.result(timeout=20)

    assert first["id"] == second["id"]
    with _connect(test_dsn, schema) as connection:
        count = connection.execute(
            "SELECT count(*) FROM legacy_notification_suppressions"
        ).fetchone()[0]
    assert count == 1


def test_committed_import_is_visible_to_independent_connection(suppression_db):
    test_dsn, schema = suppression_db

    entry = _record(test_dsn, schema)
    decision = _lookup(test_dsn, schema)

    assert entry["id"] is not None
    assert decision.decision == "suppressed"
    assert decision.entry_id == entry["id"]


def test_sql_failure_rolls_back_insert(suppression_db):
    test_dsn, schema = suppression_db
    with _connect(test_dsn, schema) as connection:
        connection.execute(
            """
            CREATE FUNCTION fail_suppression_insert() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'intentional isolated integration-test failure';
            END
            $$
            """
        )
        connection.execute(
            """
            CREATE TRIGGER fail_suppression_insert
            BEFORE INSERT ON legacy_notification_suppressions
            FOR EACH ROW EXECUTE FUNCTION fail_suppression_insert()
            """
        )
        connection.commit()

    with pytest.raises(psycopg.Error, match="intentional isolated integration-test failure"):
        _record(test_dsn, schema)

    with _connect(test_dsn, schema) as connection:
        count = connection.execute(
            "SELECT count(*) FROM legacy_notification_suppressions"
        ).fetchone()[0]
    assert count == 0


def test_caller_owned_transaction_is_rejected(suppression_db):
    test_dsn, schema = suppression_db
    connection = _connect(test_dsn, schema)
    try:
        connection.execute("SELECT 1")
        with pytest.raises(suppression.SuppressionConnectionError, match="idle connection"):
            suppression.get_suppression_import_summary(connection)
    finally:
        connection.rollback()
        connection.close()


def test_missing_schema_fails_closed(suppression_db):
    test_dsn, schema = suppression_db
    with _connect(test_dsn, schema) as connection:
        decision = suppression.lookup_suppression(
            connection,
            destination_type="telegram",
            destination_id="chat-1",
            source_type="quick_reminder",
            source_key="reminder-source-1:2026-11-22:primary",
            destination_verified=True,
        )

    assert decision.decision == "blocked"
    assert decision.error_type == "UndefinedTable"


def test_original_legacy_key_and_timestamp_are_preserved(suppression_db):
    test_dsn, schema = suppression_db
    key = "event:family_0_2026-11-22_soccer:2026-11-22:9:00 AM:0"

    entry = _record(
        test_dsn,
        schema,
        legacy_key=key,
        legacy_recorded_at=NOW,
        source_type=None,
        source_key=None,
    )

    assert entry["legacy_key"] == key
    assert entry["legacy_recorded_at"] == NOW
    assert entry["resolution_status"] == "unresolved"


def test_suppression_migration_and_rows_do_not_fabricate_telegram_message_ids(suppression_db):
    test_dsn, schema = suppression_db
    entry = _record(test_dsn, schema)

    with _connect(test_dsn, schema) as connection:
        columns = {
            row[0]
            for row in connection.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = %s AND table_name = 'legacy_notification_suppressions'
                """,
                (schema,),
            ).fetchall()
        }

    assert "telegram_message_id" not in columns
    assert "telegram_message_id" not in entry
