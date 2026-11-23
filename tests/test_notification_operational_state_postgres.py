"""Disposable PostgreSQL integration tests for operational-state persistence.

Run only against the dedicated local database with
DAYANCHOR_OUTBOX_TEST_DATABASE_URL set. The fixture creates and drops isolated
schemas only after validating the exact loopback database target.
"""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
from threading import Barrier, Event
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.pq import TransactionStatus
import pytest

import notification_operational_state as operational


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "0003_notification_operational_state.sql"
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
    "test_migration_applies_to_empty_schema_and_is_repeatable",
    "test_missing_schema_fails_closed",
}


class QueryTrackingConnection(psycopg.Connection):
    def __init__(self, *args, **kwargs):
        self.query_calls = []
        super().__init__(*args, **kwargs)

    def cursor(self, *args, **kwargs):
        self.query_calls.append("cursor")
        return super().cursor(*args, **kwargs)

    def execute(self, query, *args, **kwargs):
        self.query_calls.append(query)
        return super().execute(query, *args, **kwargs)


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
        raise ValueError("The operational-state test DSN must be a PostgreSQL URL.")
    if parsed_url.query or parsed_url.fragment:
        raise ValueError("The operational-state test DSN must not have query parameters or a fragment.")
    if parsed_url.hostname != "127.0.0.1":
        raise ValueError("The operational-state test DSN host must be exactly 127.0.0.1.")
    if parsed_url.port != int(TEST_PORT) or parsed_url.path != "/outbox_test":
        raise ValueError(
            "The operational-state test DSN must target port 55432 and database outbox_test."
        )
    if parsed_url.username != "outbox_test" or not parsed_url.password:
        raise ValueError(
            "The operational-state test DSN must use outbox_test and a test-only password."
        )

    try:
        parameters = conninfo_to_dict(raw_dsn)
    except psycopg.Error as exc:
        raise ValueError("The operational-state test DSN is invalid.") from exc
    if set(parameters) != ALLOWED_DSN_PARAMETERS:
        raise ValueError("The operational-state test DSN contains missing or disallowed parameters.")
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
        "postgresql://outbox_test:secret@localhost:55432/outbox_test",
        "postgresql://outbox_test:secret@127.0.0.1:5432/outbox_test",
        "postgresql://outbox_test:secret@127.0.0.1:55432/other_db",
        "postgresql://outbox_test:secret@127.0.0.1:55432/outbox_test?service=other",
    ],
)
def test_dsn_validation_rejects_non_disposable_targets(dsn):
    with pytest.raises(ValueError):
        _validated_test_dsn({TEST_DSN_ENV: dsn})


def test_dsn_validation_rejects_application_database_environment():
    dsn = "postgresql://outbox_test:secret@127.0.0.1:55432/outbox_test"
    with pytest.raises(ValueError, match="Application database"):
        _validated_test_dsn({TEST_DSN_ENV: dsn, "DATABASE_URL": ""})


def _connect(test_dsn, schema=None, connection_class=psycopg.Connection):
    connection = connection_class.connect(test_dsn)
    identity = connection.execute(
        "SELECT current_database(), current_user"
    ).fetchone()
    if (
        identity != ("outbox_test", "outbox_test")
        or connection.info.host != "127.0.0.1"
        or str(connection.info.port) != TEST_PORT
        or connection.info.server_version // 10000 != 16
    ):
        connection.close()
        raise RuntimeError(
            "Connection did not reach PostgreSQL 16 on the exact disposable loopback database."
        )
    connection.commit()
    if schema is not None:
        connection.execute(
            sql.SQL("SET search_path TO {}").format(sql.Identifier(schema))
        )
        connection.commit()
    return connection


def _apply_migration(connection):
    statements = [
        statement.strip()
        for statement in MIGRATION.read_text().split(";")
        if statement.strip()
    ]
    with connection.transaction():
        with connection.cursor() as cursor:
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
def operational_db(request, test_dsn):
    schema = f"operational_case_{uuid4().hex}"
    with _connect(test_dsn) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        admin.commit()

    try:
        if request.node.name not in UNMIGRATED_TESTS:
            with _connect(test_dsn, schema) as migration_connection:
                _apply_migration(migration_connection)
                migration_connection.execute(
                    """
                    CREATE TABLE operational_test_actions (
                        update_id BIGINT PRIMARY KEY,
                        effect_count INTEGER NOT NULL CHECK (effect_count > 0)
                    )
                    """
                )
                migration_connection.commit()
        yield test_dsn, schema
    finally:
        with _connect(test_dsn) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
            admin.commit()


def _cursor_values(**overrides):
    values = {
        "bot_id": "123456789",
        "destination_type": "telegram",
        "destination_id": "555",
        "bot_identity_verified": True,
        "destination_verified": True,
        "bot_identity_provenance": "trusted_bot_config",
        "destination_binding_provenance": "operator_verified_binding",
    }
    values.update(overrides)
    return values


def _provision(connection, **overrides):
    return operational.initialize_verified_bot_cursor(
        connection, **_cursor_values(**overrides)
    )


def _record_receipt(connection, **overrides):
    values = {
        "bot_id": "123456789",
        "update_id": 41,
        "destination_type": "telegram",
        "addressed_destination_id": "555",
        "outcome": "applied",
    }
    values.update(overrides)
    return operational.record_update_receipt(connection, **values)


def _assert_action_effect(connection, update_id):
    connection.execute(
        """
        INSERT INTO operational_test_actions (update_id, effect_count)
        VALUES (%s, 1)
        """,
        (update_id,),
    )


def test_migration_applies_to_empty_schema_and_is_repeatable(operational_db):
    test_dsn, schema = operational_db
    with _connect(test_dsn, schema) as connection:
        assert not _has_table(connection, schema, "notification_worker_runs")
        assert not _has_table(connection, schema, "telegram_poll_cursors")
        assert not _has_table(connection, schema, "telegram_update_receipts")

        _apply_migration(connection)
        assert _has_table(connection, schema, "notification_worker_runs")
        assert _has_table(connection, schema, "telegram_poll_cursors")
        assert _has_table(connection, schema, "telegram_update_receipts")
        _apply_migration(connection)


def test_required_constraints_foreign_key_and_indexes_exist(operational_db):
    test_dsn, schema = operational_db
    with _connect(test_dsn, schema) as connection:
        constraints = {
            row[0]
            for row in connection.execute(
                """
                SELECT constraint_row.conname
                FROM pg_catalog.pg_constraint AS constraint_row
                JOIN pg_catalog.pg_class AS table_row ON table_row.oid = constraint_row.conrelid
                JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = table_row.relnamespace
                WHERE namespace.nspname = %s
                """,
                (schema,),
            ).fetchall()
        }
        assert {
            "notification_worker_runs_identity_unique",
            "notification_worker_runs_outcome_check",
            "notification_worker_runs_completion_check",
            "notification_worker_runs_success_error_check",
            "telegram_poll_cursors_bot_id_check",
            "telegram_poll_cursors_destination_type_check",
            "telegram_poll_cursors_next_update_check",
            "telegram_update_receipts_identity_unique",
            "telegram_update_receipts_outcome_check",
            "telegram_update_receipts_destination_type_check",
        } <= constraints

        foreign_keys = connection.execute(
            """
            SELECT count(*)
            FROM pg_catalog.pg_constraint AS constraint_row
            JOIN pg_catalog.pg_class AS table_row ON table_row.oid = constraint_row.conrelid
            JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = table_row.relnamespace
            WHERE namespace.nspname = %s
              AND table_row.relname = 'telegram_update_receipts'
              AND constraint_row.contype = 'f'
              AND constraint_row.confdeltype = 'r'
            """,
            (schema,),
        ).fetchone()[0]
        assert foreign_keys == 1

        indexes = {
            row[0]
            for row in connection.execute(
                """
                SELECT index_class.relname
                FROM pg_catalog.pg_index AS index_info
                JOIN pg_catalog.pg_class AS table_class ON table_class.oid = index_info.indrelid
                JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = table_class.relnamespace
                JOIN pg_catalog.pg_class AS index_class ON index_class.oid = index_info.indexrelid
                WHERE namespace.nspname = %s
                """,
                (schema,),
            ).fetchall()
        }
        assert {
            "notification_worker_runs_latest_idx",
            "telegram_update_receipts_destination_idx",
            "telegram_update_receipts_outcome_idx",
        } <= indexes


def test_database_constraints_reject_invalid_worker_cursor_and_receipt_values(operational_db):
    test_dsn, schema = operational_db
    with _connect(test_dsn, schema) as connection:
        _provision(connection)
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                """
                INSERT INTO notification_worker_runs (worker_id, run_id, completed_at, outcome)
                VALUES ('dispatcher', %s, NOW(), 'unknown')
                """,
                (uuid4(),),
            )
        connection.rollback()

        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                """
                UPDATE telegram_poll_cursors SET next_update_id = -1
                WHERE bot_id = '123456789'
                """
            )
        connection.rollback()

        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                """
                INSERT INTO telegram_poll_cursors (
                    bot_id, destination_type, destination_id,
                    bot_identity_provenance, destination_binding_provenance
                ) VALUES ('123456790', 'telegram', '555', '', 'verified')
                """
            )
        connection.rollback()

        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            connection.execute(
                """
                INSERT INTO telegram_update_receipts (
                    bot_id, update_id, destination_type, outcome
                ) VALUES ('987654321', 1, 'telegram', 'applied')
                """
            )
        connection.rollback()

        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                """
                INSERT INTO telegram_update_receipts (
                    bot_id, update_id, destination_type, outcome
                ) VALUES ('123456789', 1, 'telegram', 'processing')
                """
            )
        connection.rollback()


def test_unverified_identity_or_destination_cannot_be_provisioned(operational_db):
    test_dsn, schema = operational_db
    with _connect(test_dsn, schema) as connection:
        with pytest.raises(ValueError, match="Bot identity"):
            _provision(connection, bot_identity_verified=False)
        with pytest.raises(ValueError, match="Destination binding"):
            _provision(connection, destination_verified=False)
        with pytest.raises(ValueError, match="destination_type='telegram'"):
            _provision(connection, destination_type="discord")
        assert connection.execute("SELECT count(*) FROM telegram_poll_cursors").fetchone()[0] == 0


def test_cursor_is_bot_scoped_and_raw_token_is_never_stored(operational_db):
    test_dsn, schema = operational_db
    raw_token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk"
    with _connect(test_dsn, schema) as connection:
        _provision(connection, bot_identity_provenance="verified_config_not_token")
        second = _provision(
            connection,
            bot_id="987654321",
            destination_id="556",
            bot_identity_provenance="second_verified_config",
        )
        assert second["bot_id"] == "987654321"
        assert connection.execute(
            "SELECT count(*) FROM telegram_poll_cursors WHERE bot_id IN (%s, %s)",
            ("123456789", "987654321"),
        ).fetchone()[0] == 2
        stored = " ".join(
            str(value)
            for row in connection.execute(
                """
                SELECT bot_id, destination_id, bot_identity_provenance,
                       destination_binding_provenance
                FROM telegram_poll_cursors
                """
            ).fetchall()
            for value in row
        )
        assert raw_token not in stored


def test_receipt_identity_is_unique_and_duplicate_outcome_is_idempotent(operational_db):
    test_dsn, schema = operational_db
    with _connect(test_dsn, schema) as connection:
        _provision(connection)
        with connection.transaction():
            first = _record_receipt(connection)
        with connection.transaction():
            duplicate = _record_receipt(connection)

        assert first.inserted is True
        assert duplicate.inserted is False
        assert duplicate.receipt["id"] == first.receipt["id"]
        with pytest.raises(psycopg.errors.UniqueViolation):
            connection.execute(
                """
                INSERT INTO telegram_update_receipts (
                    bot_id, update_id, destination_type, addressed_destination_id, outcome
                ) VALUES ('123456789', 41, 'telegram', '555', 'applied')
                """
            )
        connection.rollback()


def test_invalid_receipt_outcome_is_rejected_by_repository_and_database(operational_db):
    test_dsn, schema = operational_db
    with _connect(test_dsn, schema) as connection:
        _provision(connection)
        with connection.transaction():
            with pytest.raises(ValueError, match="applied, ignored, or rejected"):
                _record_receipt(connection, outcome="processing")
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                """
                INSERT INTO telegram_update_receipts (
                    bot_id, update_id, destination_type, outcome
                ) VALUES ('123456789', 42, 'telegram', 'processing')
                """
            )
        connection.rollback()


def test_repository_transaction_ownership_is_enforced(operational_db):
    test_dsn, schema = operational_db
    connection = _connect(test_dsn, schema, connection_class=QueryTrackingConnection)
    try:
        _provision(connection)
        connection.query_calls.clear()
        with pytest.raises(operational.OperationalStateConnectionError, match="caller-owned"):
            operational.lock_bot_cursor(connection, "123456789")
        with pytest.raises(operational.OperationalStateConnectionError, match="caller-owned"):
            _record_receipt(connection)
        assert connection.query_calls == []

        connection.execute("SELECT 1")
        connection.query_calls.clear()
        with pytest.raises(operational.OperationalStateConnectionError, match="idle connection"):
            operational.start_worker_run(connection, "dispatcher")
        with pytest.raises(operational.OperationalStateConnectionError, match="idle connection"):
            operational.initialize_verified_bot_cursor(connection, **_cursor_values())
        assert connection.query_calls == []
        connection.rollback()
    finally:
        connection.close()


def test_receipt_action_and_cursor_commit_together_without_repository_commit(operational_db):
    test_dsn, schema = operational_db
    writer = _connect(test_dsn, schema)
    observer = _connect(test_dsn, schema)
    try:
        _provision(writer)
        with writer.transaction():
            cursor = operational.lock_bot_cursor(writer, "123456789")
            _assert_action_effect(writer, 51)
            receipt = _record_receipt(writer, update_id=51)
            operational.advance_bot_cursor(
                writer, bot_id=cursor["bot_id"], update_id=receipt.receipt["update_id"]
            )
            assert writer.info.transaction_status == TransactionStatus.INTRANS
            assert observer.execute(
                "SELECT count(*) FROM telegram_update_receipts WHERE update_id = 51"
            ).fetchone()[0] == 0
            observer.rollback()

        assert writer.info.transaction_status == TransactionStatus.IDLE
        assert observer.execute(
            "SELECT count(*) FROM telegram_update_receipts WHERE update_id = 51"
        ).fetchone()[0] == 1
        assert observer.execute(
            "SELECT count(*) FROM operational_test_actions WHERE update_id = 51"
        ).fetchone()[0] == 1
        assert observer.execute(
            "SELECT next_update_id FROM telegram_poll_cursors WHERE bot_id = '123456789'"
        ).fetchone()[0] == 52
        observer.commit()
    finally:
        writer.close()
        observer.close()


def test_rollback_and_action_failure_remove_action_receipt_and_cursor_changes(operational_db):
    test_dsn, schema = operational_db
    with _connect(test_dsn, schema) as connection:
        _provision(connection)
        with pytest.raises(RuntimeError, match="action failed"):
            with connection.transaction():
                cursor = operational.lock_bot_cursor(connection, "123456789")
                _assert_action_effect(connection, 61)
                receipt = _record_receipt(connection, update_id=61)
                operational.advance_bot_cursor(
                    connection, bot_id=cursor["bot_id"], update_id=receipt.receipt["update_id"]
                )
                raise RuntimeError("action failed")

        assert connection.info.transaction_status == TransactionStatus.IDLE
        assert connection.execute(
            "SELECT count(*) FROM telegram_update_receipts WHERE update_id = 61"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM operational_test_actions WHERE update_id = 61"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT next_update_id FROM telegram_poll_cursors WHERE bot_id = '123456789'"
        ).fetchone()[0] == 0
        connection.commit()


def test_receipt_prevents_repeated_database_action_effect(operational_db):
    test_dsn, schema = operational_db
    with _connect(test_dsn, schema) as connection:
        _provision(connection)
        for _ in range(2):
            with connection.transaction():
                operational.lock_bot_cursor(connection, "123456789")
                result = _record_receipt(connection, update_id=71)
                if result.inserted:
                    _assert_action_effect(connection, 71)
                    operational.advance_bot_cursor(
                        connection, bot_id="123456789", update_id=71
                    )
        assert connection.execute(
            "SELECT count(*) FROM operational_test_actions WHERE update_id = 71"
        ).fetchone()[0] == 1
        connection.commit()


def test_two_workers_serialize_on_bot_cursor_and_cursor_never_moves_backward(operational_db):
    test_dsn, schema = operational_db
    setup = _connect(test_dsn, schema)
    _provision(setup)
    setup.close()

    first = _connect(test_dsn, schema)
    first_tx = first.transaction()
    first_tx.__enter__()
    first_cursor = operational.lock_bot_cursor(first, "123456789")
    first_receipt = _record_receipt(first, update_id=90)
    first_advanced = operational.advance_bot_cursor(
        first, bot_id=first_cursor["bot_id"], update_id=first_receipt.receipt["update_id"]
    )
    assert first_advanced["next_update_id"] == 91

    attempting = Event()
    acquired = Event()

    def second_worker():
        with _connect(test_dsn, schema) as second:
            attempting.set()
            with second.transaction():
                locked = operational.lock_bot_cursor(second, "123456789")
                acquired.set()
                assert locked["next_update_id"] == 91
                receipt = _record_receipt(second, update_id=80)
                operational.advance_bot_cursor(
                    second, bot_id="123456789", update_id=receipt.receipt["update_id"]
                )
                return operational.get_update_receipt(
                    second, bot_id="123456789", update_id=80
                )

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(second_worker)
            assert attempting.wait(timeout=5)
            assert not acquired.wait(timeout=0.2)
            first_tx.__exit__(None, None, None)
            second_receipt = future.result(timeout=10)
        assert second_receipt["update_id"] == 80
    finally:
        if first.info.transaction_status == TransactionStatus.INTRANS:
            first_tx.__exit__(RuntimeError, RuntimeError("test cleanup"), None)
        first.close()

    with _connect(test_dsn, schema) as verifier:
        assert verifier.execute(
            "SELECT next_update_id FROM telegram_poll_cursors WHERE bot_id = '123456789'"
        ).fetchone()[0] == 91


def test_concurrent_duplicate_receipt_attempts_produce_one_receipt_and_one_effect(operational_db):
    test_dsn, schema = operational_db
    with _connect(test_dsn, schema) as setup:
        _provision(setup)

    barrier = Barrier(2, timeout=10)

    def record_from_independent_connection():
        with _connect(test_dsn, schema) as connection:
            with connection.transaction():
                barrier.wait()
                result = _record_receipt(connection, update_id=101)
                if result.inserted:
                    _assert_action_effect(connection, 101)
                    operational.advance_bot_cursor(
                        connection, bot_id="123456789", update_id=101
                    )
                return result.inserted

    with ThreadPoolExecutor(max_workers=2) as executor:
        inserted = list(executor.map(lambda _: record_from_independent_connection(), range(2)))

    assert sorted(inserted) == [False, True]
    with _connect(test_dsn, schema) as verifier:
        assert verifier.execute(
            "SELECT count(*) FROM telegram_update_receipts WHERE bot_id = '123456789' AND update_id = 101"
        ).fetchone()[0] == 1
        assert verifier.execute(
            "SELECT count(*) FROM operational_test_actions WHERE update_id = 101"
        ).fetchone()[0] == 1
        verifier.commit()


def test_independent_connection_observes_only_committed_worker_state(operational_db):
    test_dsn, schema = operational_db
    writer = _connect(test_dsn, schema)
    observer = _connect(test_dsn, schema)
    try:
        started = operational.start_worker_run(writer, "dispatcher")
        assert started["outcome"] is None
        assert writer.info.transaction_status == TransactionStatus.IDLE
        assert observer.execute(
            "SELECT count(*) FROM notification_worker_runs"
        ).fetchone()[0] == 1
        observer.commit()
    finally:
        writer.close()
        observer.close()


def test_worker_run_ordering_failure_redaction_and_incomplete_health(operational_db):
    test_dsn, schema = operational_db
    with _connect(test_dsn, schema) as connection:
        older = operational.start_worker_run(connection, "dispatcher")
        assert operational.get_worker_health(connection, "dispatcher")["outcome"] is None
        newer = operational.start_worker_run(connection, "dispatcher")
        operational.complete_worker_run(
            connection,
            "dispatcher",
            newer["run_id"],
            outcome="success",
        )
        token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk"
        operational.complete_worker_run(
            connection,
            "dispatcher",
            older["run_id"],
            outcome="failed",
            error_details=f"request failed bot_token={token}",
        )

        health = operational.get_worker_health(connection, "dispatcher")
        assert health["run_id"] == newer["run_id"]
        assert health["outcome"] == "success"
        failed = connection.execute(
            "SELECT error_details FROM notification_worker_runs WHERE run_id = %s",
            (older["run_id"],),
        ).fetchone()[0]
        assert token not in failed
        assert "[REDACTED" in failed
        connection.commit()
        assert operational.get_worker_health(connection, "missing-worker") is None


def test_successful_worker_run_rejects_error_details_and_invalid_outcome(operational_db):
    test_dsn, schema = operational_db
    with _connect(test_dsn, schema) as connection:
        started = operational.start_worker_run(connection, "scheduler")
        with pytest.raises(ValueError, match="error details"):
            operational.complete_worker_run(
                connection,
                "scheduler",
                started["run_id"],
                outcome="success",
                error_details="failure",
            )
        with pytest.raises(ValueError, match="outcome"):
            operational.complete_worker_run(
                connection, "scheduler", started["run_id"], outcome="unknown"
            )
        assert operational.get_worker_health(connection, "scheduler")["outcome"] is None


def test_missing_schema_fails_closed(operational_db):
    test_dsn, schema = operational_db
    with _connect(test_dsn, schema) as connection:
        with pytest.raises(psycopg.errors.UndefinedTable):
            operational.get_worker_health(connection, "dispatcher")


def test_receipt_recording_never_commits_the_callers_transaction(operational_db):
    test_dsn, schema = operational_db
    connection = _connect(test_dsn, schema)
    observer = _connect(test_dsn, schema)
    try:
        _provision(connection)
        with connection.transaction():
            _record_receipt(connection, update_id=111)
            assert connection.info.transaction_status == TransactionStatus.INTRANS
            assert observer.execute(
                "SELECT count(*) FROM telegram_update_receipts WHERE update_id = 111"
            ).fetchone()[0] == 0
            observer.rollback()
        assert observer.execute(
            "SELECT count(*) FROM telegram_update_receipts WHERE update_id = 111"
        ).fetchone()[0] == 1
        observer.commit()
    finally:
        connection.close()
        observer.close()
