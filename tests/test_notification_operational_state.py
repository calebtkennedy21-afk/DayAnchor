from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier, RLock
from types import SimpleNamespace
from uuid import UUID, uuid4

import psycopg
from psycopg.pq import TransactionStatus
import pytest

import notification_operational_state as operational


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 22, 23, 0, tzinfo=timezone.utc)


class FakeDatabase:
    def __init__(self):
        self.worker_runs = []
        self.cursors = {}
        self.receipts = {}
        self.next_worker_run_id = 1
        self.next_receipt_id = 1
        self.sequence = 0
        self.action_count = 0
        self.lock = RLock()
        self.missing_schema = False
        self.fail_query = None
        self.commits = 0
        self.rollbacks = 0
        self.telegram_calls = 0

    def connect(self):
        return FakeConnection(self)


class FakeConnection:
    def __init__(self, database):
        self.database = database
        self.closed = False
        self.info = SimpleNamespace(transaction_status=TransactionStatus.IDLE)

    def transaction(self):
        return FakeTransaction(self.database, self)

    def cursor(self, row_factory=None):
        return FakeCursor(self.database, self)


class FakeTransaction:
    def __init__(self, database, connection):
        self.database = database
        self.connection = connection

    def __enter__(self):
        if self.connection.closed or self.connection.info.transaction_status != TransactionStatus.IDLE:
            raise AssertionError("Owned transaction requires an open idle connection.")
        self.database.lock.acquire()
        self.snapshot = (
            deepcopy(self.database.worker_runs),
            deepcopy(self.database.cursors),
            deepcopy(self.database.receipts),
            self.database.next_worker_run_id,
            self.database.next_receipt_id,
            self.database.sequence,
            self.database.action_count,
        )
        self.connection.info.transaction_status = TransactionStatus.INTRANS
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            self.database.commits += 1
        else:
            (
                self.database.worker_runs,
                self.database.cursors,
                self.database.receipts,
                self.database.next_worker_run_id,
                self.database.next_receipt_id,
                self.database.sequence,
                self.database.action_count,
            ) = self.snapshot
            self.database.rollbacks += 1
        self.connection.info.transaction_status = TransactionStatus.IDLE
        self.database.lock.release()
        return False


class FakeCursor:
    def __init__(self, database, connection):
        self.database = database
        self.connection = connection
        self.result = None
        self.results = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params=()):
        if self.connection.closed:
            raise psycopg.OperationalError("connection closed")
        if self.connection.info.transaction_status not in (
            TransactionStatus.INTRANS,
            TransactionStatus.INERROR,
        ):
            raise AssertionError("Repository SQL must run in a transaction.")
        normalized = " ".join(query.split())
        self.result = None
        self.results = []

        if self.database.missing_schema:
            raise psycopg.errors.UndefinedTable("operational-state schema missing")
        if self.database.fail_query and self.database.fail_query in normalized:
            raise psycopg.OperationalError("simulated database failure")

        if normalized.startswith("INSERT INTO notification_worker_runs"):
            self._insert_worker_run(params)
        elif normalized.startswith("UPDATE notification_worker_runs"):
            self._complete_worker_run(params)
        elif normalized.startswith("SELECT * FROM notification_worker_runs"):
            self._select_worker_health(params[0])
        elif normalized.startswith("INSERT INTO telegram_poll_cursors"):
            self._insert_cursor(params)
        elif normalized.startswith("SELECT * FROM telegram_poll_cursors"):
            self._select_cursor(params[0])
        elif normalized.startswith("UPDATE telegram_poll_cursors"):
            self._advance_cursor(params)
        elif normalized.startswith("SELECT id FROM telegram_update_receipts"):
            row = self.database.receipts.get(tuple(params))
            self.result = {"id": row["id"]} if row else None
        elif normalized.startswith("INSERT INTO telegram_update_receipts"):
            self._insert_receipt(params)
        elif normalized.startswith("SELECT * FROM telegram_update_receipts"):
            self._select_receipt(params)
        else:
            raise AssertionError(f"Unrecognized operational-state SQL: {normalized}")

    def _insert_worker_run(self, params):
        worker_id, run_id = params
        if any(row["worker_id"] == worker_id and row["run_id"] == run_id for row in self.database.worker_runs):
            raise psycopg.errors.UniqueViolation("duplicate worker run")
        self.database.sequence += 1
        row = {
            "id": self.database.next_worker_run_id,
            "worker_id": worker_id,
            "run_id": run_id,
            "started_at": NOW + timedelta(microseconds=self.database.sequence),
            "completed_at": None,
            "outcome": None,
            "error_details": None,
        }
        self.database.next_worker_run_id += 1
        self.database.worker_runs.append(row)
        self.result = deepcopy(row)

    def _complete_worker_run(self, params):
        outcome, details, worker_id, run_id = params
        row = next(
            (
                item for item in self.database.worker_runs
                if item["worker_id"] == worker_id
                and item["run_id"] == run_id
                and item["completed_at"] is None
            ),
            None,
        )
        if row is not None:
            row.update(
                completed_at=NOW + timedelta(microseconds=self.database.sequence),
                outcome=outcome,
                error_details=details,
            )
            self.result = deepcopy(row)

    def _select_worker_health(self, worker_id):
        rows = [row for row in self.database.worker_runs if row["worker_id"] == worker_id]
        if rows:
            self.result = deepcopy(max(rows, key=lambda row: (row["started_at"], row["id"])))

    def _insert_cursor(self, params):
        bot_id, destination_type, destination_id, bot_provenance, binding_provenance, next_update_id = params
        if bot_id in self.database.cursors:
            return
        row = {
            "bot_id": bot_id,
            "destination_type": destination_type,
            "destination_id": destination_id,
            "bot_identity_provenance": bot_provenance,
            "destination_binding_provenance": binding_provenance,
            "bot_identity_verified_at": NOW,
            "destination_binding_verified_at": NOW,
            "next_update_id": next_update_id,
            "created_at": NOW,
            "updated_at": NOW,
        }
        self.database.cursors[bot_id] = row
        self.result = deepcopy(row)

    def _select_cursor(self, bot_id):
        row = self.database.cursors.get(bot_id)
        if row:
            self.result = deepcopy(row)

    def _advance_cursor(self, params):
        next_update_id, bot_id = params
        row = self.database.cursors.get(bot_id)
        if row:
            row["next_update_id"] = max(row["next_update_id"], next_update_id)
            row["updated_at"] = NOW
            self.result = deepcopy(row)

    def _insert_receipt(self, params):
        bot_id, update_id, destination_type, destination_id, outcome, detail = params
        identity = (bot_id, update_id)
        if identity in self.database.receipts:
            return
        row = {
            "id": self.database.next_receipt_id,
            "bot_id": bot_id,
            "update_id": update_id,
            "destination_type": destination_type,
            "addressed_destination_id": destination_id,
            "outcome": outcome,
            "outcome_detail": detail,
            "processed_at": NOW,
            "created_at": NOW,
        }
        self.database.next_receipt_id += 1
        self.database.receipts[identity] = row
        self.result = deepcopy(row)

    def _select_receipt(self, params):
        row = self.database.receipts.get(tuple(params))
        if row:
            self.result = deepcopy(row)

    def fetchone(self):
        return deepcopy(self.result)

    def fetchall(self):
        return deepcopy(self.results)


def _initialize(database, **overrides):
    values = {
        "bot_id": "123456789",
        "destination_type": "telegram",
        "destination_id": "555",
        "bot_identity_verified": True,
        "destination_verified": True,
        "bot_identity_provenance": "telegram_getme_verified",
        "destination_binding_provenance": "owner_configured_chat",
    }
    values.update(overrides)
    return operational.initialize_verified_bot_cursor(database.connect(), **values)


def _receipt(connection, **overrides):
    values = {
        "bot_id": "123456789",
        "update_id": 41,
        "destination_type": "telegram",
        "addressed_destination_id": "555",
        "outcome": "applied",
    }
    values.update(overrides)
    return operational.record_update_receipt(connection, **values)


def test_worker_run_creation_and_successful_or_failed_completion():
    database = FakeDatabase()
    connection = database.connect()

    started = operational.start_worker_run(connection, "alert_scheduler")
    finished = operational.complete_worker_run(
        connection, "alert_scheduler", started["run_id"], outcome="success"
    )
    failed_started = operational.start_worker_run(connection, "dispatcher")
    failed = operational.complete_worker_run(
        connection,
        "dispatcher",
        failed_started["run_id"],
        outcome="failed",
        error_details="temporary failure",
    )

    assert isinstance(started["run_id"], UUID)
    assert started["completed_at"] is None
    assert finished["outcome"] == "success"
    assert failed["outcome"] == "failed"
    assert failed["error_details"] == "temporary failure"


def test_older_worker_completion_does_not_replace_latest_run_health():
    database = FakeDatabase()
    connection = database.connect()
    older = operational.start_worker_run(connection, "dispatcher")
    newer = operational.start_worker_run(connection, "dispatcher")

    operational.complete_worker_run(
        connection, "dispatcher", newer["run_id"], outcome="success"
    )
    operational.complete_worker_run(
        connection,
        "dispatcher",
        older["run_id"],
        outcome="failed",
        error_details="late failure",
    )

    health = operational.get_worker_health(connection, "dispatcher")
    assert health["run_id"] == newer["run_id"]
    assert health["outcome"] == "success"


def test_worker_error_details_redact_tokens_credentials_and_bound_length():
    database = FakeDatabase()
    connection = database.connect()
    started = operational.start_worker_run(connection, "dispatcher")
    raw_token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk"
    details = (
        f"failed https://worker:password@example.invalid/path "
        f"postgresql://worker:database-secret@example.invalid/app "
        f"bot_token={raw_token} password=hunter2"
    )

    finished = operational.complete_worker_run(
        connection, "dispatcher", started["run_id"], outcome="failed", error_details=details
    )

    assert raw_token not in finished["error_details"]
    assert "worker:password@" not in finished["error_details"]
    assert "worker:database-secret@" not in finished["error_details"]
    assert "hunter2" not in finished["error_details"]
    assert len(finished["error_details"]) <= operational.MAX_ERROR_DETAILS_LENGTH


def test_worker_completion_cannot_be_recorded_twice_or_for_unknown_run():
    database = FakeDatabase()
    connection = database.connect()
    started = operational.start_worker_run(connection, "dispatcher")
    operational.complete_worker_run(connection, "dispatcher", started["run_id"], outcome="success")

    with pytest.raises(operational.OperationalStateConflict):
        operational.complete_worker_run(
            connection, "dispatcher", started["run_id"], outcome="failed", error_details="late"
        )
    with pytest.raises(operational.OperationalStateConflict):
        operational.complete_worker_run(
            connection, "dispatcher", uuid4(), outcome="failed", error_details="unknown"
        )
    with pytest.raises(ValueError, match="valid UUID"):
        operational.start_worker_run(connection, "dispatcher", run_id="")


def test_bot_cursor_initialization_requires_verified_bot_and_destination():
    database = FakeDatabase()

    with pytest.raises(ValueError, match="Bot identity"):
        _initialize(database, bot_identity_verified=False)
    with pytest.raises(ValueError, match="Destination binding"):
        _initialize(database, destination_verified=False)

    cursor = _initialize(database)
    assert cursor["bot_id"] == "123456789"
    assert cursor["destination_id"] == "555"
    assert cursor["next_update_id"] == 0
    assert cursor["bot_identity_verified_at"] is not None
    assert cursor["destination_binding_verified_at"] is not None


@pytest.mark.parametrize("bot_id", ["", "0", "0123", "123:token", "abc", 1.5, True])
def test_bot_identity_is_never_inferred_or_accepted_from_untrusted_values(bot_id):
    with pytest.raises(ValueError, match="trusted numeric Telegram bot identity"):
        _initialize(FakeDatabase(), bot_id=bot_id)


def test_cursor_reinitialization_is_idempotent_but_binding_changes_conflict():
    database = FakeDatabase()

    first = _initialize(database)
    again = _initialize(database)

    assert first["bot_id"] == again["bot_id"]
    assert len(database.cursors) == 1
    with pytest.raises(operational.OperationalStateConflict, match="different verified"):
        _initialize(database, destination_id="other-chat")


def test_destination_binding_type_is_explicitly_telegram():
    with pytest.raises(ValueError, match="destination_type='telegram'"):
        _initialize(FakeDatabase(), destination_type="discord")


def test_cursor_and_receipt_operations_have_explicit_transaction_ownership():
    database = FakeDatabase()
    connection = database.connect()
    _initialize(database)

    with pytest.raises(operational.OperationalStateConnectionError, match="caller-owned"):
        operational.lock_bot_cursor(connection, "123456789")
    with pytest.raises(operational.OperationalStateConnectionError, match="caller-owned"):
        _receipt(connection)

    with connection.transaction():
        cursor = operational.lock_bot_cursor(connection, "123456789")
        receipt = _receipt(connection)
        advanced = operational.advance_bot_cursor(
            connection, bot_id=cursor["bot_id"], update_id=receipt.receipt["update_id"]
        )
        assert advanced["next_update_id"] == 42

    assert database.commits == 2  # initialization plus the caller transaction
    assert connection.info.transaction_status == TransactionStatus.IDLE


def test_receipt_identity_is_unique_and_duplicate_outcome_is_idempotent():
    database = FakeDatabase()
    _initialize(database)
    connection = database.connect()

    with connection.transaction():
        first = _receipt(connection)
    with connection.transaction():
        duplicate = _receipt(connection)

    assert first.inserted is True
    assert duplicate.inserted is False
    assert first.receipt["id"] == duplicate.receipt["id"]
    assert len(database.receipts) == 1


def test_duplicate_update_with_different_result_is_rejected():
    database = FakeDatabase()
    _initialize(database)
    connection = database.connect()
    with connection.transaction():
        _receipt(connection)

    with connection.transaction():
        with pytest.raises(operational.OperationalStateConflict):
            _receipt(connection, outcome="rejected")


def test_receipt_outcomes_distinguish_applied_ignored_and_rejected():
    database = FakeDatabase()
    _initialize(database)
    connection = database.connect()

    for update_id, outcome in ((1, "applied"), (2, "ignored"), (3, "rejected")):
        with connection.transaction():
            saved = _receipt(connection, update_id=update_id, outcome=outcome)
        assert saved.receipt["outcome"] == outcome

    with pytest.raises(ValueError, match="applied, ignored, or rejected"):
        with connection.transaction():
            _receipt(connection, update_id=4, outcome="processing")


def test_cursor_advancement_is_monotonic_and_never_uses_a_chat_scoped_key():
    database = FakeDatabase()
    _initialize(database)
    connection = database.connect()

    with connection.transaction():
        operational.lock_bot_cursor(connection, "123456789")
        _receipt(connection, update_id=100)
        first = operational.advance_bot_cursor(connection, bot_id="123456789", update_id=100)
        _receipt(connection, update_id=40)
        older = operational.advance_bot_cursor(connection, bot_id="123456789", update_id=40)

    assert first["next_update_id"] == 101
    assert older["next_update_id"] == 101
    assert list(database.cursors) == ["123456789"]


def test_concurrent_cursor_advancers_serialize_and_cannot_move_cursor_backward():
    database = FakeDatabase()
    _initialize(database)
    connection = database.connect()
    with connection.transaction():
        _receipt(connection, update_id=50)
        _receipt(connection, update_id=100)

    barrier = Barrier(2, timeout=5)

    def advance(update_id):
        connection = database.connect()
        barrier.wait()
        with connection.transaction():
            operational.lock_bot_cursor(connection, "123456789")
            return operational.advance_bot_cursor(
                connection, bot_id="123456789", update_id=update_id
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(advance, (100, 50)))

    assert all(result["next_update_id"] >= 51 for result in results)
    assert database.cursors["123456789"]["next_update_id"] == 101


def test_receipt_action_and_cursor_rollback_as_one_caller_transaction():
    database = FakeDatabase()
    _initialize(database, next_update_id=40)
    connection = database.connect()

    with pytest.raises(RuntimeError, match="action failure"):
        with connection.transaction():
            operational.lock_bot_cursor(connection, "123456789")
            database.action_count += 1
            _receipt(connection, update_id=41)
            operational.advance_bot_cursor(connection, bot_id="123456789", update_id=41)
            raise RuntimeError("action failure")

    assert database.action_count == 0
    assert database.receipts == {}
    assert database.cursors["123456789"]["next_update_id"] == 40
    assert database.rollbacks == 1


def test_cursor_cannot_advance_for_a_fetched_update_without_receipt():
    database = FakeDatabase()
    _initialize(database)
    connection = database.connect()

    with connection.transaction():
        operational.lock_bot_cursor(connection, "123456789")
        with pytest.raises(operational.OperationalStateConflict, match="durable receipt"):
            operational.advance_bot_cursor(
                connection, bot_id="123456789", update_id=41
            )

    assert database.cursors["123456789"]["next_update_id"] == 0


def test_missing_schema_and_database_errors_never_report_success():
    database = FakeDatabase()
    connection = database.connect()
    database.missing_schema = True

    with pytest.raises(psycopg.errors.UndefinedTable):
        operational.start_worker_run(connection, "dispatcher")
    with pytest.raises(psycopg.errors.UndefinedTable):
        operational.get_bot_cursor(connection, "123456789")

    database.missing_schema = False
    database.fail_query = "SELECT * FROM notification_worker_runs"
    with pytest.raises(psycopg.OperationalError, match="simulated database failure"):
        operational.get_worker_health(connection, "dispatcher")


def test_required_state_missing_and_database_failure_propagate():
    database = FakeDatabase()
    connection = database.connect()

    with connection.transaction():
        with pytest.raises(operational.OperationalStateNotFound):
            operational.lock_bot_cursor(connection, "123456789")
    with connection.transaction():
        with pytest.raises(operational.OperationalStateNotFound):
            operational.advance_bot_cursor(connection, bot_id="123456789", update_id=1)


def test_migration_is_additive_repeatable_and_enforces_required_contracts():
    migration = (ROOT / "migrations" / "0003_notification_operational_state.sql").read_text()

    assert migration.count("CREATE TABLE IF NOT EXISTS") == 3
    assert migration.count("CREATE INDEX IF NOT EXISTS") == 3
    assert "UNIQUE (worker_id, run_id)" in migration
    assert "UNIQUE (bot_id, update_id)" in migration
    assert "REFERENCES telegram_poll_cursors (bot_id)" in migration
    assert "CHECK (outcome IN ('applied', 'ignored', 'rejected'))" in migration
    assert "CHECK (destination_type = 'telegram')" in migration
    assert "CHECK (next_update_id >= 0)" in migration
    assert "bot_token" not in migration.lower()


def test_repository_has_no_telegram_transport_or_active_inbox_calls(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("Operational-state persistence must not call Telegram.")

    monkeypatch.setattr(operational, "send_telegram_message", fail_if_called, raising=False)
    database = FakeDatabase()
    _initialize(database)
    connection = database.connect()
    with connection.transaction():
        operational.lock_bot_cursor(connection, "123456789")
        _receipt(connection, outcome="ignored")
        operational.advance_bot_cursor(connection, bot_id="123456789", update_id=41)

    assert database.telegram_calls == 0
