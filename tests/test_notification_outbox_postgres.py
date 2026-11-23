"""Disposable PostgreSQL integration tests for the notification outbox.

Run only against the dedicated local database with
DAYANCHOR_OUTBOX_TEST_DATABASE_URL set. The fixture creates and drops schemas
only in that validated database; it never consults application DB settings.
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
from psycopg.pq import TransactionStatus
import pytest

import notification_outbox as outbox


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "0001_notification_outbox.sql"
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
    "test_migration_creates_empty_schema_and_is_idempotent",
    "test_missing_schema_propagates_database_error_and_rolls_back",
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
        raise ValueError(f"Application database environment variables must be unset: {', '.join(blocked)}.")

    raw_dsn = environ.get(TEST_DSN_ENV)
    if not raw_dsn:
        raise ValueError(f"Set {TEST_DSN_ENV} to the dedicated local test database URL.")
    parsed_url = urlsplit(raw_dsn)
    if parsed_url.scheme not in {"postgres", "postgresql"}:
        raise ValueError("The outbox test DSN must be a PostgreSQL URL.")
    if parsed_url.query or parsed_url.fragment:
        raise ValueError("The outbox test DSN must not contain query parameters or a fragment.")
    if parsed_url.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("The outbox test DSN host must be 127.0.0.1 or localhost.")
    if parsed_url.port != int(TEST_PORT) or parsed_url.path != "/outbox_test":
        raise ValueError("The outbox test DSN must target port 55432 and database outbox_test.")
    if parsed_url.username != "outbox_test" or not parsed_url.password:
        raise ValueError("The outbox test DSN must use the outbox_test user and a test-only password.")

    try:
        parameters = conninfo_to_dict(raw_dsn)
    except psycopg.Error as exc:
        raise ValueError("The outbox test DSN is invalid.") from exc

    if set(parameters) != ALLOWED_DSN_PARAMETERS:
        raise ValueError("The outbox test DSN contains missing or disallowed connection parameters.")
    if parameters.get("host") not in {"127.0.0.1", "localhost"}:
        raise ValueError("The outbox test DSN host must be 127.0.0.1 or localhost.")
    if parameters.get("port") != TEST_PORT:
        raise ValueError(f"The outbox test DSN port must be {TEST_PORT}.")
    if parameters.get("dbname") != "outbox_test" or parameters.get("user") != "outbox_test":
        raise ValueError("The outbox test database and user must both be exactly outbox_test.")
    if not parameters.get("password"):
        raise ValueError("The outbox test DSN must provide its test-only password.")
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
        "postgresql://other_user:secret@127.0.0.1:55432/outbox_test",
        "postgresql://outbox_test:secret@127.0.0.1:55432/other_db",
        "postgresql://outbox_test:secret@127.0.0.1:55432/outbox_test?service=other",
        "postgresql://outbox_test:secret@127.0.0.1:55432/outbox_test?servicefile=/tmp/pg_service.conf",
    ],
)
def test_dsn_safety_validation_rejects_non_test_targets(dsn):
    with pytest.raises(ValueError):
        _validated_test_dsn({TEST_DSN_ENV: dsn})


def test_dsn_safety_validation_rejects_application_database_environment():
    dsn = "postgresql://outbox_test:secret@127.0.0.1:55432/outbox_test"

    with pytest.raises(ValueError, match="Application database"):
        _validated_test_dsn({TEST_DSN_ENV: dsn, "DATABASE_URL": ""})


def _connect(test_dsn, schema=None, connection_class=psycopg.Connection):
    connection = connection_class.connect(test_dsn)
    if schema is not None:
        connection.execute(
            sql.SQL("SET search_path TO {}").format(sql.Identifier(schema))
        )
        connection.commit()
    return connection


def _apply_migration(connection):
    statements = [statement.strip() for statement in MIGRATION.read_text().split(";") if statement.strip()]
    with connection.transaction():
        with connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)


def _has_outbox_table(connection, schema):
    row = connection.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_class AS relation
            JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = %s AND relation.relname = 'notification_outbox'
              AND relation.relkind = 'r'
        )
        """,
        (schema,),
    ).fetchone()
    connection.commit()
    return row[0]


def _enqueue(
    connection,
    *,
    source_key="stable-task-reminder-1",
    destination_id="chat-1",
    expires_at=None,
    max_attempts=8,
):
    return outbox.enqueue_notification(
        connection,
        source_type="task_reminder",
        source_key=source_key,
        destination_type="telegram",
        destination_id=destination_id,
        payload={"text": "Test reminder"},
        expires_at=expires_at,
        max_attempts=max_attempts,
    )


@pytest.fixture
def outbox_db(request, test_dsn):
    schema = f"outbox_case_{uuid4().hex}"
    with psycopg.connect(test_dsn) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        admin.commit()

    try:
        if request.node.name not in UNMIGRATED_TESTS:
            with _connect(test_dsn, schema) as migration_connection:
                _apply_migration(migration_connection)
        yield test_dsn, schema
    finally:
        with psycopg.connect(test_dsn) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
            admin.commit()


def test_migration_creates_empty_schema_and_is_idempotent(outbox_db):
    dsn, schema = outbox_db
    with _connect(dsn, schema) as connection:
        assert not _has_outbox_table(connection, schema)
        _apply_migration(connection)
        assert _has_outbox_table(connection, schema)
        _apply_migration(connection)
        assert _has_outbox_table(connection, schema)

        indexes = {
            row[0]
            for row in connection.execute(
                """
                SELECT index_class.relname
                FROM pg_catalog.pg_index AS index_info
                JOIN pg_catalog.pg_class AS table_class ON table_class.oid = index_info.indrelid
                JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = table_class.relnamespace
                JOIN pg_catalog.pg_class AS index_class ON index_class.oid = index_info.indexrelid
                WHERE namespace.nspname = %s AND table_class.relname = 'notification_outbox'
                """,
                (schema,),
            ).fetchall()
        }
        assert {
            "notification_outbox_identity_unique",
            "notification_outbox_claim_token_unique",
            "notification_outbox_eligibility_idx",
            "notification_outbox_lease_recovery_idx",
        } <= indexes
        connection.commit()
        assert connection.info.transaction_status == TransactionStatus.IDLE


def test_database_unique_identity_constraint_and_multiple_destinations(outbox_db):
    dsn, schema = outbox_db
    with _connect(dsn, schema) as connection:
        original = _enqueue(connection)
        other_destination = _enqueue(connection, destination_id="chat-2")
        assert original["id"] != other_destination["id"]

        with pytest.raises(psycopg.errors.UniqueViolation):
            with connection.transaction():
                connection.execute(
                    """
                    INSERT INTO notification_outbox (
                        source_type, source_key, destination_type, destination_id, payload
                    )
                    VALUES (%s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        original["source_type"],
                        original["source_key"],
                        original["destination_type"],
                        original["destination_id"],
                        '{"text":"duplicate"}',
                    ),
                )
        assert connection.info.transaction_status == TransactionStatus.IDLE
        count = connection.execute(
            "SELECT count(*) FROM notification_outbox WHERE source_key = %s",
            (original["source_key"],),
        ).fetchone()[0]
        assert count == 2
        connection.commit()


def test_concurrent_enqueue_same_identity_creates_one_row(outbox_db):
    dsn, schema = outbox_db
    start = Barrier(2)

    def enqueue_from_connection():
        with _connect(dsn, schema) as connection:
            start.wait(timeout=5)
            return _enqueue(connection, source_key="concurrent-shared-key")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: enqueue_from_connection(), range(2)))

    assert results[0]["id"] == results[1]["id"]
    with _connect(dsn, schema) as connection:
        count = connection.execute(
            "SELECT count(*) FROM notification_outbox WHERE source_key = %s",
            ("concurrent-shared-key",),
        ).fetchone()[0]
        connection.commit()
    assert count == 1


def test_concurrent_claims_on_separate_connections_claim_distinct_rows(outbox_db):
    dsn, schema = outbox_db
    with _connect(dsn, schema) as setup:
        _enqueue(setup, source_key="parallel-claim-first")
        _enqueue(setup, source_key="parallel-claim-second")

    start = Barrier(2)

    def claim_from_connection(worker_id):
        with _connect(dsn, schema) as connection:
            start.wait(timeout=5)
            return outbox.claim_notifications(connection, worker_id, limit=1)

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim_from_connection, ("parallel-a", "parallel-b")))

    assert len(claims[0]) == len(claims[1]) == 1
    assert claims[0][0]["id"] != claims[1][0]["id"]


def test_concurrent_claims_use_separate_connections_and_skip_locked_rows(outbox_db):
    dsn, schema = outbox_db
    with _connect(dsn, schema) as setup:
        first = _enqueue(setup, source_key="claim-first")
        second = _enqueue(setup, source_key="claim-second")

    locker = _connect(dsn, schema)
    claimant = _connect(dsn, schema)
    try:
        locker.execute("BEGIN")
        locker.execute(
            """
            SELECT id FROM notification_outbox
            WHERE id = %s FOR UPDATE
            """,
            (first["id"],),
        ).fetchone()

        claim = outbox.claim_notifications(claimant, "skip-locked-worker", limit=1)

        assert [row["id"] for row in claim] == [second["id"]]
        assert locker.info.transaction_status == TransactionStatus.INTRANS
        assert claimant.info.transaction_status == TransactionStatus.IDLE
    finally:
        locker.rollback()
        locker.close()
        claimant.close()


def test_commit_visibility_between_independent_connections(outbox_db):
    dsn, schema = outbox_db
    writer = _connect(dsn, schema)
    observer = _connect(dsn, schema)
    try:
        row = _enqueue(writer, source_key="commit-visible")
        assert writer.info.transaction_status == TransactionStatus.IDLE
        visible = observer.execute(
            "SELECT id, status FROM notification_outbox WHERE source_key = %s",
            ("commit-visible",),
        ).fetchone()
        assert visible == (row["id"], "pending")
        observer.commit()
    finally:
        writer.close()
        observer.close()


def test_active_transaction_rejected_before_repository_sql(outbox_db):
    dsn, schema = outbox_db
    connection = _connect(dsn, schema, connection_class=QueryTrackingConnection)
    try:
        connection.execute("SELECT 1").fetchone()
        connection.query_calls.clear()
        with pytest.raises(outbox.OutboxConnectionError, match="idle connection"):
            _enqueue(connection, source_key="must-not-insert")
        assert connection.query_calls == []
        assert connection.info.transaction_status == TransactionStatus.INTRANS
        connection.rollback()
        assert connection.info.transaction_status == TransactionStatus.IDLE
    finally:
        connection.close()

    with _connect(dsn, schema) as verifier:
        assert verifier.execute(
            "SELECT count(*) FROM notification_outbox WHERE source_key = %s",
            ("must-not-insert",),
        ).fetchone()[0] == 0
        verifier.commit()


def test_diagnostic_read_leaves_connection_idle(outbox_db):
    dsn, schema = outbox_db
    connection = _connect(dsn, schema)
    try:
        row = _enqueue(connection)
        diagnostic = outbox.get_notification(connection, row["id"])
        assert diagnostic["id"] == row["id"]
        assert connection.info.transaction_status == TransactionStatus.IDLE
        assert outbox.claim_notifications(connection, "reused-connection")
        assert connection.info.transaction_status == TransactionStatus.IDLE
    finally:
        connection.close()


def test_database_failure_rolls_back_operation_and_leaves_connection_idle(outbox_db):
    dsn, schema = outbox_db
    setup = _connect(dsn, schema)
    row = _enqueue(setup, source_key="rollback-row")
    claim = outbox.claim_notifications(setup, "rollback-worker")[0]
    sending = outbox.mark_sending(setup, row["id"], claim["claim_token"])
    setup.close()

    blocker = _connect(dsn, schema)
    worker = _connect(dsn, schema)
    try:
        worker.execute("SET lock_timeout = '750ms'")
        worker.commit()
        blocker.execute("BEGIN")
        blocker.execute(
            "SELECT id FROM notification_outbox WHERE id = %s FOR UPDATE",
            (row["id"],),
        ).fetchone()

        with pytest.raises(psycopg.errors.LockNotAvailable):
            outbox.record_success(worker, row["id"], sending["claim_token"], 87)
        assert worker.info.transaction_status == TransactionStatus.IDLE
    finally:
        blocker.rollback()
        blocker.close()
        worker.close()

    with _connect(dsn, schema) as verifier:
        persisted = outbox.get_notification(verifier, row["id"])
        assert persisted["status"] == "sending"
        assert persisted["telegram_message_id"] is None


def test_claim_token_fencing_rejects_stale_worker(outbox_db):
    dsn, schema = outbox_db
    with _connect(dsn, schema) as connection:
        row = _enqueue(connection)
        claim = outbox.claim_notifications(connection, "fence-worker")[0]
        sending = outbox.mark_sending(connection, row["id"], claim["claim_token"])

        assert outbox.record_success(connection, row["id"], uuid4(), 88) is None
        assert outbox.record_uncertain(connection, row["id"], uuid4(), "stale") is None
        assert outbox.get_notification(connection, row["id"])["status"] == "sending"
        assert outbox.record_success(connection, row["id"], sending["claim_token"], 88)["status"] == "sent"


def test_expired_pre_send_claim_recovers_pending_and_can_be_reclaimed(outbox_db):
    dsn, schema = outbox_db
    with _connect(dsn, schema) as connection:
        row = _enqueue(connection, source_key="expired-claim")
        first_claim = outbox.claim_notifications(connection, "expired-worker")[0]
        connection.execute(
            "UPDATE notification_outbox SET lease_expires_at = NOW() - INTERVAL '1 second' WHERE id = %s",
            (row["id"],),
        )
        connection.commit()

        recovered = outbox.recover_expired_leases(connection)
        assert recovered["claimed_recovered"] == 1
        assert outbox.get_notification(connection, row["id"])["status"] == "pending"
        next_claim = outbox.claim_notifications(connection, "replacement-worker")[0]
        assert next_claim["claim_token"] != first_claim["claim_token"]


def test_expired_sending_lease_becomes_uncertain_and_is_not_retried(outbox_db):
    dsn, schema = outbox_db
    with _connect(dsn, schema) as connection:
        row = _enqueue(connection, source_key="expired-sending")
        claim = outbox.claim_notifications(connection, "sending-worker")[0]
        outbox.mark_sending(connection, row["id"], claim["claim_token"])
        connection.execute(
            "UPDATE notification_outbox SET lease_expires_at = NOW() - INTERVAL '1 second' WHERE id = %s",
            (row["id"],),
        )
        connection.commit()

        recovered = outbox.recover_expired_leases(connection)
        assert recovered["sending_uncertain"] == 1
        assert outbox.get_notification(connection, row["id"])["status"] == "uncertain"
        assert outbox.claim_notifications(connection, "no-uncertain-retry") == []


def test_expiration_and_attempt_limits_prevent_claim_or_retry(outbox_db):
    dsn, schema = outbox_db
    with _connect(dsn, schema) as connection:
        expired = _enqueue(
            connection,
            source_key="already-expired",
            expires_at=datetime.now(timezone.utc),
        )
        assert outbox.claim_notifications(connection, "expiry-worker") == []
        recovered = outbox.recover_expired_leases(connection)
        assert recovered["expired"] == 1
        assert outbox.get_notification(connection, expired["id"])["status"] == "dead"

        limited = _enqueue(connection, source_key="one-attempt-only", max_attempts=1)
        claim = outbox.claim_notifications(connection, "limited-worker")[0]
        outbox.mark_sending(connection, limited["id"], claim["claim_token"])
        failed = outbox.record_failure(
            connection,
            limited["id"],
            claim["claim_token"],
            classification="transient",
            error="temporary",
        )
        assert failed["status"] == "dead"
        assert outbox.claim_notifications(connection, "after-limit") == []


def test_missing_schema_propagates_database_error_and_rolls_back(outbox_db):
    dsn, schema = outbox_db
    with _connect(dsn, schema) as connection:
        with pytest.raises(psycopg.errors.UndefinedTable):
            outbox.claim_notifications(connection, "missing-schema")
        assert connection.info.transaction_status == TransactionStatus.IDLE
