from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from types import SimpleNamespace

import psycopg
from psycopg.pq import TransactionStatus
import pytest

import notification_suppression as suppression
import telegram_alerts


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 8, 15, 0, tzinfo=timezone.utc)


class FakeDatabase:
    def __init__(self):
        self.rows = []
        self.next_id = 1
        self.lock = RLock()
        self.missing_schema = False
        self.query_error = None
        self.fail_after_mutation = False
        self.commits = 0
        self.rollbacks = 0

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
        if self.connection.info.transaction_status != TransactionStatus.IDLE:
            raise AssertionError("Repository operation must own an idle connection.")
        self.database.lock.acquire()
        self.snapshot = (deepcopy(self.database.rows), self.database.next_id)
        self.connection.info.transaction_status = TransactionStatus.INTRANS
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            self.database.commits += 1
        else:
            self.database.rows, self.database.next_id = self.snapshot
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
        if self.connection.info.transaction_status != TransactionStatus.INTRANS:
            raise AssertionError("SQL must run inside a repository-owned transaction.")
        normalized = " ".join(query.split())
        if self.database.missing_schema:
            raise psycopg.errors.UndefinedTable("legacy_notification_suppressions is missing")
        if self.database.query_error is not None:
            raise self.database.query_error

        self.result = None
        self.results = []
        if normalized.startswith("INSERT INTO legacy_notification_suppressions"):
            self._insert(params)
        elif normalized.startswith("SELECT * FROM legacy_notification_suppressions WHERE migration_batch_id"):
            self.result = deepcopy(
                next(
                    (
                        row for row in self.database.rows
                        if row["migration_batch_id"] == params[0]
                        and row["legacy_key"] == params[1]
                        and row["destination_type"] == params[2]
                        and row["destination_id"] == params[3]
                    ),
                    None,
                )
            )
        elif normalized.startswith("SELECT id FROM legacy_notification_suppressions WHERE resolution_status ="):
            row = next(
                (
                    row for row in self.database.rows
                    if row["resolution_status"] == "resolved_suppressed"
                    and (
                        row["destination_type"],
                        row["destination_id"],
                        row["source_type"],
                        row["source_key"],
                    ) == tuple(params)
                ),
                None,
            )
            self.result = {"id": row["id"]} if row else None
        elif normalized.startswith("SELECT id FROM legacy_notification_suppressions WHERE resolution_status <>"):
            row = next(
                (
                    row for row in self.database.rows
                    if row["resolution_status"] != "resolved_suppressed"
                    and (
                        (
                            row["destination_type"] == params[0]
                            and row["destination_id"] == params[1]
                        )
                        or row["destination_id"] is None
                    )
                ),
                None,
            )
            self.result = {"id": row["id"]} if row else None
        elif normalized.startswith("SELECT * FROM legacy_notification_suppressions WHERE resolution_status <>"):
            batch, batch_again, limit = params
            self.results = deepcopy(
                [
                    row for row in self.database.rows
                    if row["resolution_status"] != "resolved_suppressed"
                    and (batch is None or row["migration_batch_id"] == batch_again)
                ][:limit]
            )
        elif normalized.startswith("SELECT resolution_status, COUNT(*) AS entry_count"):
            batch, batch_again = params
            counts = {}
            for row in self.database.rows:
                if batch is None or row["migration_batch_id"] == batch_again:
                    status = row["resolution_status"]
                    counts[status] = counts.get(status, 0) + 1
            self.results = [
                {"resolution_status": status, "entry_count": count}
                for status, count in counts.items()
            ]
        else:
            raise AssertionError(f"Unrecognized SQL in fake: {normalized}")

        if self.database.fail_after_mutation and normalized.startswith(
            "INSERT INTO legacy_notification_suppressions"
        ):
            self.database.fail_after_mutation = False
            raise psycopg.OperationalError("simulated failure after mutation")

    def _insert(self, params):
        (
            legacy_key,
            recorded_at,
            destination_type,
            destination_id,
            destination_verified,
            source_type,
            source_key,
            provenance,
            batch_id,
            status,
            resolution_note,
        ) = params
        natural_key = (batch_id, legacy_key, destination_type, destination_id)
        if any(
            (row["migration_batch_id"], row["legacy_key"], row["destination_type"], row["destination_id"])
            == natural_key
            for row in self.database.rows
        ):
            return
        row = {
            "id": self.database.next_id,
            "legacy_key": legacy_key,
            "legacy_recorded_at": recorded_at,
            "destination_type": destination_type,
            "destination_id": destination_id,
            "destination_verified_at": NOW if destination_verified else None,
            "source_type": source_type,
            "source_key": source_key,
            "provenance": provenance,
            "migration_batch_id": batch_id,
            "resolution_status": status,
            "resolution_note": resolution_note,
            "created_at": NOW,
            "updated_at": NOW,
        }
        self.database.rows.append(row)
        self.database.next_id += 1
        self.result = deepcopy(row)

    def fetchone(self):
        return deepcopy(self.result)

    def fetchall(self):
        return deepcopy(self.results)


def _record(database, **overrides):
    values = {
        "legacy_key": "reminder:reminder-1:2026-09-08:primary",
        "legacy_recorded_at": NOW,
        "destination_type": "telegram",
        "destination_id": "chat-1",
        "destination_verified": True,
        "source_type": "quick_reminder",
        "source_key": "reminder-source-1:2026-09-08:primary",
        "provenance": "settings.telegram_alert_history",
        "migration_batch_id": "batch-001",
    }
    values.update(overrides)
    return suppression.record_legacy_suppression(database.connect(), **values)


def _lookup(database, **overrides):
    values = {
        "destination_type": "telegram",
        "destination_id": "chat-1",
        "source_type": "quick_reminder",
        "source_key": "reminder-source-1:2026-09-08:primary",
        "destination_verified": True,
    }
    values.update(overrides)
    return suppression.lookup_suppression(database.connect(), **values)


def test_empty_history_has_no_implicit_suppression():
    database = FakeDatabase()

    assert suppression.get_suppression_import_summary(database.connect()) == {
        "resolved_suppressed": 0,
        "unresolved": 0,
        "ambiguous": 0,
        "destination_unverified": 0,
        "total": 0,
    }
    assert suppression.list_unresolved_suppressions(database.connect()) == []
    assert _lookup(database).decision == "clear"


def test_verified_explicit_mapping_suppresses_exact_destination_and_source():
    database = FakeDatabase()

    entry = _record(database)

    assert entry["legacy_key"] == "reminder:reminder-1:2026-09-08:primary"
    assert entry["legacy_recorded_at"] == NOW
    assert entry["destination_verified_at"] is not None
    assert entry["resolution_status"] == "resolved_suppressed"
    assert "telegram_message_id" not in entry
    assert _lookup(database).decision == "suppressed"


def test_duplicate_history_and_repeat_import_are_idempotent():
    database = FakeDatabase()

    first = _record(database)
    repeated = _record(database)

    assert first["id"] == repeated["id"]
    assert len(database.rows) == 1
    assert suppression.get_suppression_import_summary(database.connect())["total"] == 1


def test_conflicting_repeat_import_rolls_back_instead_of_overwriting():
    database = FakeDatabase()
    _record(database)

    with pytest.raises(suppression.SuppressionImportConflict):
        _record(database, legacy_recorded_at=None)

    assert len(database.rows) == 1
    assert database.rollbacks == 1


def test_unverified_destination_is_retained_and_fails_closed_for_that_destination():
    database = FakeDatabase()
    entry = _record(database, destination_verified=False, source_type=None, source_key=None)

    assert entry["resolution_status"] == "destination_unverified"
    assert entry["destination_verified_at"] is None
    assert _lookup(database).decision == "blocked"
    assert len(suppression.list_unresolved_suppressions(database.connect())) == 1


def test_missing_destination_provenance_blocks_all_destination_lookups():
    database = FakeDatabase()
    entry = _record(
        database,
        destination_id=None,
        destination_verified=False,
        source_type=None,
        source_key=None,
    )

    assert entry["destination_id"] is None
    assert entry["resolution_status"] == "destination_unverified"
    assert _lookup(database, destination_id="other-chat").decision == "blocked"


def test_ambiguous_source_mapping_is_not_suppressed():
    database = FakeDatabase()
    entry = _record(database, ambiguous=True, resolution_note="Multiple persisted candidates.")

    assert entry["resolution_status"] == "ambiguous"
    assert _lookup(database).decision == "blocked"


def test_missing_source_identity_remains_unresolved_and_blocks_destination():
    database = FakeDatabase()
    entry = _record(database, source_type=None, source_key=None)

    assert entry["resolution_status"] == "unresolved"
    assert _lookup(database).decision == "blocked"


def test_suppression_lookup_is_exact_and_destination_isolated():
    database = FakeDatabase()
    _record(database)

    assert _lookup(database, destination_id="chat-2").decision == "clear"
    assert _lookup(database, source_key="different-source").decision == "clear"


def test_lookup_requires_explicit_destination_verification():
    with pytest.raises(ValueError, match="explicit destination verification"):
        _lookup(FakeDatabase(), destination_verified=False)


def test_unverified_history_requires_review_counts_and_lists_records():
    database = FakeDatabase()
    _record(database, source_type=None, source_key=None, destination_verified=False)

    summary = suppression.get_suppression_import_summary(
        database.connect(), migration_batch_id="batch-001"
    )
    unresolved = suppression.list_unresolved_suppressions(
        database.connect(), migration_batch_id="batch-001"
    )

    assert summary["total"] == 1
    assert summary["destination_unverified"] == 1
    assert len(unresolved) == 1


def test_transaction_failure_rolls_back_history_insert():
    database = FakeDatabase()
    database.fail_after_mutation = True

    with pytest.raises(psycopg.OperationalError, match="simulated failure"):
        _record(database)

    assert database.rows == []
    assert database.rollbacks == 1


def test_missing_schema_blocks_lookup_and_never_returns_clear_to_send():
    database = FakeDatabase()
    database.missing_schema = True

    decision = _lookup(database)

    assert decision.decision == "blocked"
    assert decision.error_type == "UndefinedTable"
    with pytest.raises(psycopg.errors.UndefinedTable):
        suppression.get_suppression_import_summary(database.connect())


def test_database_uncertainty_is_an_explicit_blocked_decision():
    database = FakeDatabase()
    database.query_error = psycopg.OperationalError("database temporarily unavailable")

    decision = _lookup(database)

    assert decision.decision == "blocked"
    assert decision.error_type == "OperationalError"


def test_active_transaction_is_rejected():
    connection = FakeDatabase().connect()
    connection.info.transaction_status = TransactionStatus.INTRANS

    with pytest.raises(suppression.SuppressionConnectionError, match="idle connection"):
        suppression.get_suppression_import_summary(connection)


def test_existing_legacy_alert_keys_are_unchanged_and_repository_never_sends(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("Suppression persistence must not send Telegram messages.")

    monkeypatch.setattr(telegram_alerts, "send_telegram_message", fail_if_called)
    now = datetime(2026, 9, 8, 9, 0, tzinfo=timezone.utc)
    alerts = telegram_alerts.collect_due_alerts(
        tasks=[
            {
                "id": "task-1",
                "status": "active",
                "scheduled_date": now.date(),
                "scheduled_time": now.time().replace(tzinfo=None),
                "title": "Task",
            }
        ],
        reminders=[
            {
                "reminder_id": "reminder-1",
                "status": "active",
                "remind_date": now.date(),
                "remind_time": now.time().replace(tzinfo=None),
                "text": "Reminder",
            }
        ],
        morning_checkins={},
        nightly_reflections={},
        history={},
        config={
            "timezone": "UTC",
            "alert_window_minutes": 5,
            "alert_lateness_minutes": 60,
            "offset_minutes": [0],
        },
        family_items=[
            {
                "item_id": "family-1",
                "status": "planned",
                "start_date": now.date(),
                "start_time": now.time().replace(tzinfo=None),
                "title": "Family event",
            }
        ],
        now_value=now,
    )
    keys = {alert["key"] for alert in alerts}
    database = FakeDatabase()
    _record(database)

    assert "task:task-1:2026-09-08:9:00 AM:0" in keys
    assert "event:family-1:2026-09-08:9:00 AM:0" in keys
    assert "reminder:reminder-1:2026-09-08:primary" in keys
    assert len(database.rows) == 1
    assert database.commits == 1


def test_migration_is_additive_rerunnable_and_has_no_message_id_column():
    migration = (ROOT / "migrations" / "0002_legacy_notification_suppressions.sql").read_text()

    assert "CREATE TABLE IF NOT EXISTS legacy_notification_suppressions" in migration
    assert "CREATE UNIQUE INDEX IF NOT EXISTS legacy_notification_suppressions_import_unique" in migration
    assert "telegram_message_id" not in migration
    assert "resolution_status <> 'resolved_suppressed'" in migration
