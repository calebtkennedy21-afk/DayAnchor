"""Disposable PostgreSQL integration tests for Telegram authorization.

Run only against the dedicated local database with
DAYANCHOR_OUTBOX_TEST_DATABASE_URL set. Each test uses a unique schema and
verifies the exact PostgreSQL 16 loopback target before running migrations.
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
import os
from pathlib import Path
from threading import Barrier, Event
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
import pytest

import telegram_authorization as authorization
import telegram_identity


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = tuple(
    ROOT / "migrations" / name
    for name in (
        "0001_notification_outbox.sql",
        "0002_legacy_notification_suppressions.sql",
        "0003_notification_operational_state.sql",
        "0004_telegram_authorization.sql",
    )
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
UNMIGRATED_TESTS = {"test_migrations_apply_in_order_and_0004_is_repeatable"}
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
        raise ValueError("The authorization test DSN must be a PostgreSQL URL.")
    if parsed_url.query or parsed_url.fragment:
        raise ValueError("The authorization test DSN must not contain query parameters or a fragment.")
    if parsed_url.hostname != "127.0.0.1":
        raise ValueError("The authorization test DSN host must be exactly 127.0.0.1.")
    if parsed_url.port != int(TEST_PORT) or parsed_url.path != "/outbox_test":
        raise ValueError(
            "The authorization test DSN must target port 55432 and database outbox_test."
        )
    if parsed_url.username != "outbox_test" or not parsed_url.password:
        raise ValueError(
            "The authorization test DSN must use outbox_test and a test-only password."
        )

    try:
        parameters = conninfo_to_dict(raw_dsn)
    except psycopg.Error as exc:
        raise ValueError("The authorization test DSN is invalid.") from exc
    if set(parameters) != ALLOWED_DSN_PARAMETERS:
        raise ValueError("The authorization test DSN contains missing or disallowed parameters.")
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
        "******remote.example:55432/outbox_test",
        "******localhost:55432/outbox_test",
        "******127.0.0.1:5432/outbox_test",
        "******127.0.0.1:55432/other_db",
        "******127.0.0.1:55432/outbox_test?service=other",
    ],
)
def test_dsn_validation_rejects_non_disposable_targets(dsn):
    with pytest.raises(ValueError):
        _validated_test_dsn({TEST_DSN_ENV: dsn})


def _assert_test_database(connection):
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


def _connect(test_dsn, schema=None):
    connection = psycopg.connect(test_dsn)
    _assert_test_database(connection)
    if schema is not None:
        connection.execute(
            sql.SQL("SET search_path TO {}").format(sql.Identifier(schema))
        )
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
def authorization_db(request, test_dsn):
    schema = f"telegram_auth_case_{uuid4().hex}"
    with _connect(test_dsn) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        admin.commit()

    try:
        if request.node.name not in UNMIGRATED_TESTS:
            with _connect(test_dsn, schema) as migration_connection:
                _apply_migrations(migration_connection)
                migration_connection.execute(
                    """
                    CREATE TABLE authorization_test_actions (
                        action_id UUID PRIMARY KEY,
                        effect TEXT NOT NULL
                    )
                    """
                )
                migration_connection.commit()
        yield test_dsn, schema
    finally:
        with _connect(test_dsn) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
            admin.commit()


class TestOwnerAuthority(authorization.OwnerAuthorizationAuthority):
    def authorize_telegram_change(self, action, resource):
        return authorization.OwnerAuthorizationGrant(
            actor_identity="owner:postgres-test",
            provenance="authenticated_owner_ui",
        )


def _identity(bot_id=123456):
    return telegram_identity.verify_bot_identity(
        "test-only-unpersisted-token",
        lambda _token: {"ok": True, "result": {"id": bot_id, "is_bot": True}},
        now=NOW,
    )


def _approve_destination(connection, *, bot_id=123456, destination_id="555"):
    return authorization.approve_destination_binding(
        connection,
        identity=_identity(bot_id),
        destination_id=destination_id,
        authority=TestOwnerAuthority(),
        now=NOW,
    )


def _approve_sender(connection, binding_id, sender_id=777):
    return authorization.approve_sender(
        connection,
        binding_id=binding_id,
        sender_user_id=sender_id,
        authority=TestOwnerAuthority(),
    )


def _decision(connection, *, bot_id=123456, destination_id="555", sender_id=777):
    with connection.transaction():
        return authorization.authorize_destination_sender(
            connection,
            bot_id=bot_id,
            destination_id=destination_id,
            sender_user_id=sender_id,
        )


def _seed_authorization(test_dsn, schema, *, bot_id=123456, destination_id="555", sender_id=777):
    with _connect(test_dsn, schema) as connection:
        binding = _approve_destination(
            connection, bot_id=bot_id, destination_id=destination_id
        )
        sender = _approve_sender(connection, binding["binding_id"], sender_id)
        return binding, sender


def test_migrations_apply_in_order_and_0004_is_repeatable(authorization_db):
    test_dsn, schema = authorization_db
    with _connect(test_dsn, schema) as connection:
        assert not _has_table(connection, schema, "telegram_destination_bindings")
        assert not _has_table(connection, schema, "telegram_authorized_senders")
        _apply_migrations(connection)
        assert _has_table(connection, schema, "notification_outbox")
        assert _has_table(connection, schema, "legacy_notification_suppressions")
        assert _has_table(connection, schema, "telegram_poll_cursors")
        assert _has_table(connection, schema, "telegram_destination_bindings")
        assert _has_table(connection, schema, "telegram_authorized_senders")
        authorization_migration = MIGRATIONS[-1]
        with connection.transaction():
            for statement in authorization_migration.read_text().split(";"):
                if statement.strip():
                    connection.execute(statement)
        assert _has_table(connection, schema, "telegram_destination_bindings")
        assert _has_table(connection, schema, "telegram_authorized_senders")


def test_schema_constraints_foreign_key_and_indexes_exist(authorization_db):
    test_dsn, schema = authorization_db
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
            "telegram_destination_bindings_bot_id_check",
            "telegram_destination_bindings_destination_type_check",
            "telegram_destination_bindings_destination_id_check",
            "telegram_destination_bindings_status_check",
            "telegram_destination_bindings_approval_provenance_check",
            "telegram_destination_bindings_revocation_check",
            "telegram_destination_bindings_approval_actor_unique",
            "telegram_authorized_senders_binding_fk",
            "telegram_authorized_senders_sender_id_check",
            "telegram_authorized_senders_status_check",
            "telegram_authorized_senders_approval_provenance_check",
            "telegram_authorized_senders_revocation_check",
        } <= constraints

        indexes = {
            row[0]: row[1]
            for row in connection.execute(
                """
                SELECT index_class.relname, index_info.indisunique
                FROM pg_catalog.pg_index AS index_info
                JOIN pg_catalog.pg_class AS table_class ON table_class.oid = index_info.indrelid
                JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = table_class.relnamespace
                JOIN pg_catalog.pg_class AS index_class ON index_class.oid = index_info.indexrelid
                WHERE namespace.nspname = %s
                """,
                (schema,),
            ).fetchall()
        }
        assert indexes["telegram_destination_bindings_one_active_bot_idx"]
        assert not indexes["telegram_destination_bindings_history_idx"]
        assert indexes["telegram_authorized_senders_one_active_idx"]
        assert not indexes["telegram_authorized_senders_lookup_idx"]
        assert not indexes["telegram_authorized_senders_history_idx"]

        foreign_keys = connection.execute(
            """
            SELECT count(*)
            FROM pg_catalog.pg_constraint AS constraint_row
            JOIN pg_catalog.pg_class AS table_row ON table_row.oid = constraint_row.conrelid
            JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = table_row.relnamespace
            WHERE namespace.nspname = %s AND table_row.relname = 'telegram_authorized_senders'
              AND constraint_row.contype = 'f' AND constraint_row.confdeltype = 'r'
            """,
            (schema,),
        ).fetchone()[0]
        assert foreign_keys == 1


def test_database_constraints_reject_malformed_and_orphan_approvals(authorization_db):
    test_dsn, schema = authorization_db
    with _connect(test_dsn, schema) as connection:
        invalid_bindings = [
            {"bot_id": "0"},
            {"destination_type": "email"},
            {"destination_id": "not-numeric"},
            {"approval_status": "pending"},
            {"bot_identity_provenance": "caller-asserted"},
            {"approver_actor_id": ""},
            {"approval_provenance": "untrusted"},
        ]
        defaults = {
            "bot_id": "123456",
            "destination_type": "telegram",
            "destination_id": "555",
            "approval_status": "approved",
            "bot_identity_provenance": "telegram_get_me",
            "approver_actor_id": "owner:test",
            "approval_provenance": "authenticated_owner_ui",
        }
        for invalid in invalid_bindings:
            values = defaults | invalid
            with pytest.raises(psycopg.errors.CheckViolation):
                with connection.transaction():
                    connection.execute(
                        f"""
                        INSERT INTO telegram_destination_bindings (
                            binding_id, bot_id, destination_type, destination_id, approval_status,
                            bot_identity_verified_at, bot_identity_provenance,
                            approver_actor_id, approval_provenance
                        )
                        VALUES (%s, %s, %s, %s, %s, NOW(), %s, %s, %s)
                        """,
                        (
                            uuid4(),
                            values["bot_id"],
                            values["destination_type"],
                            values["destination_id"],
                            values["approval_status"],
                            values["bot_identity_provenance"],
                            values["approver_actor_id"],
                            values["approval_provenance"],
                        ),
                    )

        with pytest.raises(psycopg.errors.CheckViolation):
            with connection.transaction():
                connection.execute(
                    """
                    INSERT INTO telegram_destination_bindings (
                        binding_id, bot_id, destination_type, destination_id,
                        approval_status, bot_identity_verified_at, bot_identity_provenance,
                        approver_actor_id, approval_provenance, revoked_at,
                        revoked_by_actor_id, revocation_provenance
                    ) VALUES (
                        %s, '123456', 'telegram', '555', 'revoked', NOW(),
                        'telegram_get_me', 'owner:test', 'authenticated_owner_ui',
                        NULL, NULL, NULL
                    )
                    """,
                    (uuid4(),),
                )

        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with connection.transaction():
                connection.execute(
                    """
                    INSERT INTO telegram_authorized_senders (
                        sender_approval_id, binding_id, bot_id, sender_user_id,
                        approver_actor_id, approval_provenance
                    ) VALUES (%s, %s, '123456', '777', 'owner:test', 'authenticated_owner_ui')
                    """,
                    (uuid4(), uuid4()),
                )

        valid = _approve_destination(connection)
        with pytest.raises(psycopg.errors.CheckViolation):
            with connection.transaction():
                connection.execute(
                    """
                    INSERT INTO telegram_authorized_senders (
                        sender_approval_id, binding_id, bot_id, sender_user_id,
                        approver_actor_id, approval_provenance
                    ) VALUES (%s, %s, '123456', '0', 'owner:test', 'authenticated_owner_ui')
                    """,
                    (uuid4(), valid["binding_id"]),
                )


def test_approval_idempotency_conflicts_revocation_and_history(authorization_db):
    test_dsn, schema = authorization_db
    with _connect(test_dsn, schema) as connection:
        binding = _approve_destination(connection)
        duplicate_binding = _approve_destination(connection)
        assert duplicate_binding["binding_id"] == binding["binding_id"]
        with pytest.raises(authorization.AuthorizationConflict):
            _approve_destination(connection, destination_id="556")

        sender = _approve_sender(connection, binding["binding_id"])
        duplicate_sender = _approve_sender(connection, binding["binding_id"])
        assert duplicate_sender["sender_approval_id"] == sender["sender_approval_id"]
        assert _decision(connection).allowed

        revoked_sender = authorization.revoke_sender(
            connection,
            binding_id=binding["binding_id"],
            sender_user_id=777,
            authority=TestOwnerAuthority(),
        )
        assert revoked_sender["approval_status"] == "revoked"
        assert not _decision(connection).allowed
        sender_history = authorization.get_approval_history(
            connection, binding_id=binding["binding_id"]
        )["senders"]
        assert [row["approval_status"] for row in sender_history] == ["revoked"]

        _approve_sender(connection, binding["binding_id"])
        authorization.revoke_destination_binding(
            connection,
            binding_id=binding["binding_id"],
            authority=TestOwnerAuthority(),
        )
        assert not _decision(connection).allowed
        history = authorization.get_approval_history(connection, bot_id=123456)
        assert len(history["destinations"]) == 1
        assert history["destinations"][0]["approval_status"] == "revoked"
        assert [row["approval_status"] for row in history["senders"]] == [
            "revoked",
            "revoked",
        ]

        replacement = _approve_destination(connection, destination_id="-100555")
        assert replacement["binding_id"] != binding["binding_id"]
        assert not authorization.read_authorized_senders(
            connection, binding_id=replacement["binding_id"]
        )
        history = authorization.get_approval_history(connection, bot_id=123456)
        assert [row["approval_status"] for row in history["destinations"]] == [
            "revoked",
            "approved",
        ]


def test_bot_replacement_does_not_inherit_bindings_or_senders(authorization_db):
    test_dsn, schema = authorization_db
    with _connect(test_dsn, schema) as connection:
        original = _approve_destination(connection, bot_id=123456)
        _approve_sender(connection, original["binding_id"])
        replacement = _approve_destination(connection, bot_id=654321)
        assert replacement["binding_id"] != original["binding_id"]
        assert authorization.read_authorized_senders(
            connection, binding_id=replacement["binding_id"]
        ) == []


def test_transaction_ownership_visibility_rollback_and_fail_closed(authorization_db):
    test_dsn, schema = authorization_db
    connection = _connect(test_dsn, schema)
    try:
        with connection.transaction():
            with pytest.raises(authorization.AuthorizationConnectionError):
                _approve_destination(connection)
        with pytest.raises(authorization.AuthorizationConnectionError):
            authorization.authorize_destination_sender(
                connection, bot_id=123456, destination_id="555", sender_user_id=777
            )
    finally:
        connection.close()

    with _connect(test_dsn, schema) as writer, _connect(test_dsn, schema) as reader:
        binding_id = uuid4()
        sender_id = uuid4()
        writer.execute("BEGIN")
        writer.execute(
            """
            INSERT INTO telegram_destination_bindings (
                binding_id, bot_id, destination_type, destination_id,
                bot_identity_verified_at, bot_identity_provenance,
                approver_actor_id, approval_provenance
            ) VALUES (%s, '123456', 'telegram', '555', NOW(), 'telegram_get_me',
                      'owner:test', 'authenticated_owner_ui')
            """,
            (binding_id,),
        )
        writer.execute(
            """
            INSERT INTO telegram_authorized_senders (
                sender_approval_id, binding_id, bot_id, sender_user_id,
                approver_actor_id, approval_provenance
            ) VALUES (%s, %s, '123456', '777', 'owner:test', 'authenticated_owner_ui')
            """,
            (sender_id, binding_id),
        )
        assert not _decision(reader).allowed
        writer.commit()
        assert _decision(reader).allowed

        action_id = uuid4()
        with pytest.raises(RuntimeError, match="simulated action failure"):
            with writer.transaction():
                assert authorization.authorize_destination_sender(
                    writer, bot_id=123456, destination_id="555", sender_user_id=777
                ).allowed
                writer.execute(
                    "INSERT INTO authorization_test_actions (action_id, effect) VALUES (%s, %s)",
                    (action_id, "must rollback"),
                )
                raise RuntimeError("simulated action failure")
        assert reader.execute(
            "SELECT count(*) FROM authorization_test_actions WHERE action_id = %s",
            (action_id,),
        ).fetchone()[0] == 0
        reader.commit()
        assert _decision(reader).allowed

    with _connect(test_dsn, schema) as connection:
        connection.execute("DROP TABLE telegram_authorized_senders")
        connection.commit()
        with pytest.raises(authorization.AuthorizationStorageError):
            _decision(connection)


@pytest.mark.parametrize("revoke_kind", ["sender", "destination"])
def test_authorization_action_transaction_serializes_with_revocation(
    authorization_db, revoke_kind
):
    test_dsn, schema = authorization_db
    binding, _sender = _seed_authorization(test_dsn, schema)
    action_connection = _connect(test_dsn, schema)
    revocation_started = Event()
    revocation_authority = TestOwnerAuthority()
    executor = ThreadPoolExecutor(max_workers=1)
    revocation = None

    def revoke():
        revocation_started.set()
        with _connect(test_dsn, schema) as connection:
            if revoke_kind == "sender":
                return authorization.revoke_sender(
                    connection,
                    binding_id=binding["binding_id"],
                    sender_user_id=777,
                    authority=revocation_authority,
                )
            return authorization.revoke_destination_binding(
                connection,
                binding_id=binding["binding_id"],
                authority=revocation_authority,
            )

    try:
        with action_connection.transaction():
            decision = authorization.authorize_destination_sender(
                action_connection,
                bot_id=123456,
                destination_id="555",
                sender_user_id=777,
            )
            assert decision.allowed
            action_id = uuid4()
            action_connection.execute(
                "INSERT INTO authorization_test_actions (action_id, effect) VALUES (%s, %s)",
                (action_id, "authorized action"),
            )
            revocation = executor.submit(revoke)
            assert revocation_started.wait(timeout=2)
            with pytest.raises(FutureTimeoutError):
                revocation.result(timeout=0.15)
        assert revocation is not None
        result = revocation.result(timeout=3)
        assert result["approval_status"] == "revoked"
    finally:
        executor.shutdown(wait=True)
        action_connection.close()

    with _connect(test_dsn, schema) as verify:
        assert verify.execute(
            "SELECT count(*) FROM authorization_test_actions WHERE action_id = %s",
            (action_id,),
        ).fetchone()[0] == 1
        verify.commit()
        assert not _decision(verify).allowed


def test_simulated_action_failure_rolls_back_before_revocation(authorization_db):
    test_dsn, schema = authorization_db
    binding, _sender = _seed_authorization(test_dsn, schema)
    action_connection = _connect(test_dsn, schema)
    revocation_started = Event()
    action_id = uuid4()

    def revoke():
        revocation_started.set()
        with _connect(test_dsn, schema) as connection:
            return authorization.revoke_destination_binding(
                connection,
                binding_id=binding["binding_id"],
                authority=TestOwnerAuthority(),
            )

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            revocation = None
            with pytest.raises(RuntimeError, match="simulated action failure"):
                with action_connection.transaction():
                    assert authorization.authorize_destination_sender(
                        action_connection,
                        bot_id=123456,
                        destination_id="555",
                        sender_user_id=777,
                    ).allowed
                    action_connection.execute(
                        "INSERT INTO authorization_test_actions (action_id, effect) VALUES (%s, %s)",
                        (action_id, "must rollback"),
                    )
                    revocation = executor.submit(revoke)
                    assert revocation_started.wait(timeout=2)
                    with pytest.raises(FutureTimeoutError):
                        revocation.result(timeout=0.15)
                    raise RuntimeError("simulated action failure")
            assert revocation.result(timeout=3)["approval_status"] == "revoked"
    finally:
        action_connection.close()

    with _connect(test_dsn, schema) as verify:
        assert verify.execute(
            "SELECT count(*) FROM authorization_test_actions WHERE action_id = %s",
            (action_id,),
        ).fetchone()[0] == 0
        verify.commit()
        assert not _decision(verify).allowed


def test_revocation_committed_first_denies_later_authorization(authorization_db):
    test_dsn, schema = authorization_db
    binding, _sender = _seed_authorization(test_dsn, schema)
    with _connect(test_dsn, schema) as connection:
        authorization.revoke_sender(
            connection,
            binding_id=binding["binding_id"],
            sender_user_id=777,
            authority=TestOwnerAuthority(),
        )
    with _connect(test_dsn, schema) as connection:
        assert not _decision(connection).allowed


def test_concurrent_duplicate_destination_approvals_have_one_active_record(authorization_db):
    test_dsn, schema = authorization_db
    start = Barrier(2)

    def approve():
        with _connect(test_dsn, schema) as connection:
            start.wait(timeout=3)
            return _approve_destination(connection)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: approve(), range(2), timeout=10))
    assert results[0]["binding_id"] == results[1]["binding_id"]
    with _connect(test_dsn, schema) as connection:
        assert connection.execute(
            """
            SELECT count(*) FROM telegram_destination_bindings
            WHERE bot_id = '123456' AND approval_status = 'approved'
            """
        ).fetchone()[0] == 1
        connection.commit()


def test_destination_revocation_and_sender_approval_preserve_invariant(authorization_db):
    test_dsn, schema = authorization_db
    binding, _sender = _seed_authorization(test_dsn, schema)
    start = Barrier(2)

    def approve_sender():
        with _connect(test_dsn, schema) as connection:
            start.wait(timeout=3)
            try:
                return _approve_sender(connection, binding["binding_id"], 888)
            except authorization.AuthorizationNotFound:
                return None

    def revoke_destination():
        with _connect(test_dsn, schema) as connection:
            start.wait(timeout=3)
            return authorization.revoke_destination_binding(
                connection,
                binding_id=binding["binding_id"],
                authority=TestOwnerAuthority(),
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        approve_result = executor.submit(approve_sender)
        revoke_result = executor.submit(revoke_destination)
        sender = approve_result.result(timeout=10)
        assert revoke_result.result(timeout=10)["approval_status"] == "revoked"

    with _connect(test_dsn, schema) as connection:
        assert connection.execute(
            """
            SELECT count(*)
            FROM telegram_authorized_senders AS sender
            JOIN telegram_destination_bindings AS binding USING (binding_id)
            WHERE binding.binding_id = %s
              AND binding.approval_status = 'revoked'
              AND sender.approval_status = 'approved'
            """,
            (binding["binding_id"],),
        ).fetchone()[0] == 0
        connection.commit()
        if sender is not None:
            assert sender["approval_status"] == "approved"
