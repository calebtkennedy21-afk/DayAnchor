from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from threading import RLock
from uuid import UUID, uuid4

import psycopg
from psycopg.pq import TransactionStatus
import pytest

import notification_outbox as outbox


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.now(timezone.utc)


class FakeDatabase:
    def __init__(self):
        self.rows = {}
        self.next_id = 1
        self.lock = RLock()
        self.queries = []
        self.fail_after = None
        self.missing_schema = False
        self.transaction_commits = 0
        self.transaction_rollbacks = 0

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
            raise AssertionError("Fake transaction must begin from an open idle connection.")
        self.connection.info.transaction_status = TransactionStatus.INTRANS
        self.database.lock.acquire()
        self.snapshot = (deepcopy(self.database.rows), self.database.next_id)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is not None:
            self.database.rows, self.database.next_id = self.snapshot
            self.database.transaction_rollbacks += 1
        else:
            self.database.transaction_commits += 1
        self.connection.info.transaction_status = TransactionStatus.IDLE
        self.database.lock.release()
        return False


class FakeCursor:
    def __init__(self, database, connection):
        self.database = database
        self.connection = connection
        self.result = None
        self.results = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params=()):
        if self.connection.closed:
            raise psycopg.OperationalError("connection is closed")
        if self.connection.info.transaction_status != TransactionStatus.INTRANS:
            raise AssertionError("SQL must run inside the repository-owned transaction.")
        normalized = " ".join(query.split())
        self.database.queries.append((normalized, params))
        if self.database.missing_schema:
            raise psycopg.errors.UndefinedTable("notification_outbox is missing")

        self.result = None
        self.results = []
        self.rowcount = 0
        if normalized.startswith("INSERT INTO notification_outbox"):
            self._insert(params)
        elif normalized.startswith("SELECT * FROM notification_outbox WHERE source_type"):
            self.result = deepcopy(self.database.rows.get(tuple(params)))
        elif normalized.startswith("SELECT id FROM notification_outbox"):
            self._claim_candidates(params[0])
        elif normalized.startswith("UPDATE notification_outbox SET status = 'claimed'"):
            self._claim_row(params)
        elif normalized.startswith("UPDATE notification_outbox SET status = 'sending'"):
            self._mark_sending(params)
        elif normalized.startswith("UPDATE notification_outbox SET status = 'sent'"):
            self._record_success(params)
        elif normalized.startswith("SELECT attempt_count FROM notification_outbox"):
            row = self._find(params[0], "sending", params[1])
            self.result = {"attempt_count": row["attempt_count"]} if row else None
        elif normalized.startswith("UPDATE notification_outbox SET status = CASE") and "LEAST(max_attempts" in normalized:
            self._record_failure(params)
        elif normalized.startswith("UPDATE notification_outbox SET status = 'uncertain'") and "Lease expired after sending started" in normalized:
            self._recover_sending()
        elif normalized.startswith("UPDATE notification_outbox SET status = 'uncertain'"):
            self._record_uncertain(params)
        elif normalized.startswith("UPDATE notification_outbox SET status = CASE"):
            self._recover_claimed()
        elif normalized.startswith("UPDATE notification_outbox SET status = 'dead'"):
            self._expire_pending()
        elif normalized.startswith("SELECT * FROM notification_outbox WHERE id"):
            row = next((r for r in self.database.rows.values() if r["id"] == params[0]), None)
            self.result = deepcopy(row)
        else:
            raise AssertionError(f"Unrecognized SQL in fake: {normalized}")

        if self.database.fail_after and self.database.fail_after in normalized:
            self.database.fail_after = None
            raise psycopg.OperationalError("simulated failure after mutation")

    def _insert(self, params):
        source_type, source_key, destination_type, destination_id, payload, max_attempts, expires_at = params
        identity = (source_type, source_key, destination_type, destination_id)
        if identity in self.database.rows:
            return
        row = {
            "id": self.database.next_id,
            "source_type": source_type,
            "source_key": source_key,
            "destination_type": destination_type,
            "destination_id": destination_id,
            "payload": json.loads(payload),
            "status": "pending",
            "attempt_count": 0,
            "max_attempts": max_attempts,
            "available_at": NOW - timedelta(seconds=1),
            "expires_at": expires_at,
            "claim_owner": None,
            "claim_token": None,
            "lease_expires_at": None,
            "send_started_at": None,
            "telegram_message_id": None,
            "error_classification": None,
            "last_error": None,
            "sent_at": None,
            "uncertain_at": None,
        }
        self.database.next_id += 1
        self.database.rows[identity] = row
        self.result = deepcopy(row)

    def _claim_candidates(self, limit):
        candidates = sorted(self.database.rows.values(), key=lambda row: (row["available_at"], row["id"]))
        self.results = [
            {"id": row["id"]}
            for row in candidates
            if row["status"] in {"pending", "failed"}
            and row["available_at"] <= NOW
            and (row["expires_at"] is None or row["expires_at"] > NOW)
            and row["attempt_count"] < row["max_attempts"]
        ][:limit]

    def _claim_row(self, params):
        owner, token, lease_seconds, notification_id = params
        row = next((r for r in self.database.rows.values() if r["id"] == notification_id), None)
        if not row or row["status"] not in {"pending", "failed"}:
            return
        if row["expires_at"] is not None and row["expires_at"] <= NOW:
            return
        row.update(
            status="claimed",
            claim_owner=owner,
            claim_token=token,
            lease_expires_at=NOW + timedelta(seconds=lease_seconds),
        )
        self.result = deepcopy(row)
        self.rowcount = 1

    def _find(self, notification_id, status, token):
        row = next((r for r in self.database.rows.values() if r["id"] == notification_id), None)
        return row if row and row["status"] == status and row["claim_token"] == token else None

    def _mark_sending(self, params):
        notification_id, token = params
        row = self._find(notification_id, "claimed", token)
        if not row:
            return
        row.update(
            status="sending",
            attempt_count=row["attempt_count"] + 1,
            send_started_at=NOW,
        )
        self.result = deepcopy(row)
        self.rowcount = 1

    def _record_success(self, params):
        message_id, notification_id, token = params
        row = self._find(notification_id, "sending", token)
        if not row:
            return
        row.update(
            status="sent",
            telegram_message_id=message_id,
            sent_at=NOW,
            claim_owner=None,
            claim_token=None,
            lease_expires_at=None,
            last_error=None,
            error_classification=None,
        )
        self.result = deepcopy(row)
        self.rowcount = 1

    def _record_failure(self, params):
        retryable, policy_attempts, retryable_again, policy_attempts_again, delay, classification, error, notification_id, token = params
        row = self._find(notification_id, "sending", token)
        if not row:
            return
        retry = (
            retryable and retryable_again
            and row["attempt_count"] < min(row["max_attempts"], policy_attempts, policy_attempts_again)
            and (row["expires_at"] is None or row["expires_at"] > NOW)
        )
        expired = row["expires_at"] is not None and row["expires_at"] <= NOW
        row.update(
            status="dead" if expired or not retry else "failed",
            available_at=NOW + timedelta(seconds=delay) if retry else row["available_at"],
            error_classification="expired" if expired else classification,
            last_error=error,
            claim_owner=None,
            claim_token=None,
            lease_expires_at=None,
        )
        self.result = deepcopy(row)
        self.rowcount = 1

    def _record_uncertain(self, params):
        error, notification_id, token = params
        row = self._find(notification_id, "sending", token)
        if not row:
            return
        row.update(
            status="uncertain",
            uncertain_at=NOW,
            error_classification="uncertain",
            last_error=error,
            claim_owner=None,
            claim_token=None,
            lease_expires_at=None,
        )
        self.result = deepcopy(row)
        self.rowcount = 1

    def _recover_claimed(self):
        for row in self.database.rows.values():
            if row["status"] == "claimed" and row["lease_expires_at"] <= NOW:
                expired = row["expires_at"] is not None and row["expires_at"] <= NOW
                row.update(
                    status="dead" if expired else "pending",
                    error_classification="expired" if expired else row["error_classification"],
                    last_error="Notification expired before sending." if expired else row["last_error"],
                    claim_owner=None,
                    claim_token=None,
                    lease_expires_at=None,
                )
                self.rowcount += 1

    def _recover_sending(self):
        for row in self.database.rows.values():
            if row["status"] == "sending" and row["lease_expires_at"] <= NOW:
                row.update(
                    status="uncertain",
                    uncertain_at=NOW,
                    error_classification="uncertain",
                    last_error="Lease expired after sending started; delivery is uncertain.",
                    claim_owner=None,
                    claim_token=None,
                    lease_expires_at=None,
                )
                self.rowcount += 1

    def _expire_pending(self):
        for row in self.database.rows.values():
            if (
                row["status"] in {"pending", "failed"}
                and row["expires_at"] is not None
                and row["expires_at"] <= NOW
            ):
                row.update(
                    status="dead",
                    error_classification="expired",
                    last_error="Notification expired before sending.",
                )
                self.rowcount += 1

    def fetchone(self):
        return deepcopy(self.result)

    def fetchall(self):
        return deepcopy(self.results)


def _enqueue(
    database,
    *,
    source_type="task_reminder",
    destination_id="chat-1",
    source_key="task:12:2026-11-22:15",
    expires_at=None,
    max_attempts=8,
):
    return outbox.enqueue_notification(
        database.connect(),
        source_type=source_type,
        source_key=source_key,
        destination_type="telegram",
        destination_id=destination_id,
        payload={"text": "Reminder"},
        expires_at=expires_at,
        max_attempts=max_attempts,
    )


def _send_started(database, row=None):
    row = row or _enqueue(database)
    claim = outbox.claim_notifications(database.connect(), "worker-a")[0]
    return row, claim, outbox.mark_sending(database.connect(), row["id"], claim["claim_token"])


def test_enqueue_is_idempotent_by_source_and_destination_and_does_not_reopen():
    database = FakeDatabase()
    first = _enqueue(database)
    duplicate = _enqueue(database)
    other_destination = _enqueue(database, destination_id="chat-2")
    other_source = _enqueue(database, source_type="family_schedule")

    assert first["id"] == duplicate["id"]
    assert other_destination["id"] != first["id"]
    assert other_source["id"] != first["id"]
    assert len(database.rows) == 3
    identity_insert = next(query for query, _ in database.queries if query.startswith("INSERT"))
    assert "ON CONFLICT (source_type, source_key, destination_type, destination_id) DO NOTHING" in identity_insert

    row = database.rows[("task_reminder", first["source_key"], "telegram", "chat-1")]
    row["status"] = "uncertain"
    assert _enqueue(database)["status"] == "uncertain"
    row["status"] = "sent"
    assert _enqueue(database)["status"] == "sent"
    row["status"] = "dead"
    assert _enqueue(database)["status"] == "dead"


@pytest.mark.parametrize(
    "values",
    [
        {"source_type": "task reminder"},
        {"source_key": ""},
        {"destination_type": "Telegram"},
        {"destination_id": " "},
    ],
)
def test_enqueue_rejects_missing_or_unstable_identity_parts(values):
    identity = {
        "source_type": "task_reminder",
        "source_key": "stable-id",
        "destination_type": "telegram",
        "destination_id": "chat-1",
        "payload": {"text": "Hello"},
    }
    identity.update(values)
    with pytest.raises(ValueError):
        outbox.enqueue_notification(FakeDatabase().connect(), **identity)


def test_pending_claim_is_atomic_and_send_start_increments_attempt_count():
    database = FakeDatabase()
    row = _enqueue(database)
    connection = database.connect()
    claimed = outbox.claim_notifications(connection, "worker-a", lease_seconds=45)

    assert [item["id"] for item in claimed] == [row["id"]]
    assert claimed[0]["status"] == "claimed"
    assert isinstance(claimed[0]["claim_token"], UUID)
    assert claimed[0]["lease_expires_at"] > NOW
    sending = outbox.mark_sending(connection, row["id"], claimed[0]["claim_token"])
    assert sending["status"] == "sending"
    assert sending["attempt_count"] == 1
    assert sending["send_started_at"] is not None
    assert connection.info.transaction_status == TransactionStatus.IDLE
    claim_sql = next(query for query, _ in database.queries if query.startswith("SELECT id"))
    assert "FOR UPDATE SKIP LOCKED" in claim_sql


def test_concurrent_mocked_dispatchers_receive_disjoint_claims():
    from concurrent.futures import ThreadPoolExecutor

    database = FakeDatabase()
    rows = [_enqueue(database, source_key=f"task:{item}") for item in range(6)]

    def claim(worker):
        return outbox.claim_notifications(database.connect(), worker, limit=3)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("worker-a", "worker-b")))

    claimed_ids = [row["id"] for batch in results for row in batch]
    assert len(claimed_ids) == len(set(claimed_ids)) == len(rows)
    assert set(claimed_ids) == {row["id"] for row in rows}


def test_claim_token_fences_stale_workers_from_transition_and_finalization():
    database = FakeDatabase()
    row = _enqueue(database)
    claim = outbox.claim_notifications(database.connect(), "worker-a")[0]
    wrong_token = uuid4()

    assert outbox.mark_sending(database.connect(), row["id"], wrong_token) is None
    assert outbox.mark_sending(database.connect(), row["id"], claim["claim_token"])["status"] == "sending"
    assert outbox.record_success(database.connect(), row["id"], wrong_token, 99) is None
    assert database.rows[("task_reminder", row["source_key"], "telegram", "chat-1")]["status"] == "sending"


def test_lease_recovery_requeues_before_send_and_marks_after_send_uncertain():
    database = FakeDatabase()
    before_send = _enqueue(database, source_key="before-send")
    claim_before = outbox.claim_notifications(database.connect(), "worker-a")[0]
    row_after, claim_after, _ = _send_started(database, _enqueue(database, source_key="after-send"))
    database.rows[("task_reminder", before_send["source_key"], "telegram", "chat-1")]["lease_expires_at"] = NOW - timedelta(seconds=1)
    database.rows[("task_reminder", row_after["source_key"], "telegram", "chat-1")]["lease_expires_at"] = NOW - timedelta(seconds=1)

    recovered = outbox.recover_expired_leases(database.connect())

    assert recovered["claimed_recovered"] == 1
    assert recovered["sending_uncertain"] == 1
    assert database.rows[("task_reminder", before_send["source_key"], "telegram", "chat-1")]["status"] == "pending"
    after_state = database.rows[("task_reminder", row_after["source_key"], "telegram", "chat-1")]
    assert after_state["status"] == "uncertain"
    assert after_state["uncertain_at"] is not None
    assert after_state["claim_token"] is None
    assert claim_before["claim_token"] != claim_after["claim_token"]


def test_success_persists_telegram_id_and_terminal_state():
    database = FakeDatabase()
    row, claim, _ = _send_started(database)

    sent = outbox.record_success(database.connect(), row["id"], claim["claim_token"], 853)

    assert sent["status"] == "sent"
    assert sent["telegram_message_id"] == 853
    assert sent["sent_at"] is not None
    assert sent["claim_token"] is None


def test_transient_failure_retries_with_bounded_backoff_and_permanent_failure_is_terminal():
    database = FakeDatabase()
    row, claim, _ = _send_started(database)
    retried = outbox.record_failure(
        database.connect(),
        row["id"],
        claim["claim_token"],
        classification="transient",
        error="temporary",
        random_fn=lambda low, high: 1.0,
    )
    assert retried["status"] == "failed"
    assert retried["available_at"] == NOW + timedelta(seconds=60)
    assert retried["error_classification"] == "transient"

    row2, claim2, _ = _send_started(database, _enqueue(database, source_key="permanent"))
    failed = outbox.record_failure(
        database.connect(),
        row2["id"],
        claim2["claim_token"],
        classification="permanent",
        error="invalid destination",
    )
    assert failed["status"] == "dead"
    assert failed["error_classification"] == "permanent"


def test_retry_ceiling_and_expiration_prevent_future_claims():
    database = FakeDatabase()
    row, claim, _ = _send_started(database, _enqueue(database, source_key="one-attempt", max_attempts=1))
    terminal = outbox.record_failure(
        database.connect(),
        row["id"],
        claim["claim_token"],
        classification="rate_limited",
        error="try later",
    )
    assert terminal["status"] == "dead"
    assert outbox.claim_notifications(database.connect(), "worker-b") == []

    expired = _enqueue(
        database,
        source_key="expired",
        expires_at=NOW - timedelta(seconds=1),
    )
    assert expired["status"] == "pending"
    assert outbox.claim_notifications(database.connect(), "worker-c") == []
    recovered = outbox.recover_expired_leases(database.connect())
    assert recovered["expired"] == 1
    assert database.rows[("task_reminder", "expired", "telegram", "chat-1")]["status"] == "dead"


def test_uncertain_delivery_is_not_automatically_retried():
    database = FakeDatabase()
    row, claim, _ = _send_started(database)

    uncertain = outbox.record_uncertain(database.connect(), row["id"], claim["claim_token"], "timeout")

    assert uncertain["status"] == "uncertain"
    assert uncertain["error_classification"] == "uncertain"
    assert uncertain["uncertain_at"] is not None
    assert outbox.claim_notifications(database.connect(), "worker-b") == []


def test_retry_policy_jitter_is_independent_and_capped():
    policy = outbox.RetryPolicy(base_delay_seconds=60, max_delay_seconds=1000)
    assert outbox.retry_delay_seconds(1, policy, lambda low, high: low) == 30
    assert outbox.retry_delay_seconds(8, policy, lambda low, high: high) == 1000
    assert outbox.DEFAULT_RETRY_POLICY.max_attempts == 8
    assert outbox.DEFAULT_RETRY_POLICY.max_delay_seconds == 24 * 60 * 60


def test_state_machine_lists_only_valid_transitions():
    assert outbox.transition_allowed("pending", "claimed")
    assert outbox.transition_allowed("claimed", "pending")
    assert outbox.transition_allowed("sending", "uncertain")
    assert outbox.transition_allowed("uncertain", "pending")
    assert not outbox.transition_allowed("uncertain", "claimed")
    assert not outbox.transition_allowed("sent", "pending")
    assert not outbox.transition_allowed("dead", "claimed")
    assert not outbox.transition_allowed("unknown", "pending")


def test_diagnostics_read_and_rollback_propagates_database_failure():
    database = FakeDatabase()
    row = _enqueue(database)
    connection = database.connect()
    assert outbox.get_notification(connection, row["id"])["id"] == row["id"]
    claim = outbox.claim_notifications(database.connect(), "worker-a")[0]
    database.fail_after = "SET status = 'sending'"

    with pytest.raises(psycopg.OperationalError, match="simulated failure"):
        outbox.mark_sending(connection, row["id"], claim["claim_token"])

    assert connection.info.transaction_status == TransactionStatus.IDLE
    assert database.transaction_rollbacks == 1
    persisted = outbox.get_notification(database.connect(), row["id"])
    assert persisted["status"] == "claimed"
    assert persisted["attempt_count"] == 0


def test_idle_connection_is_accepted_and_mutation_commits_before_return():
    database = FakeDatabase()
    connection = database.connect()

    row = outbox.enqueue_notification(
        connection,
        source_type="task_reminder",
        source_key="stable-task-reminder-id",
        destination_type="telegram",
        destination_id="chat-1",
        payload={"text": "Reminder"},
    )

    assert row["status"] == "pending"
    assert connection.info.transaction_status == TransactionStatus.IDLE
    assert database.transaction_commits == 1
    assert outbox.get_notification(database.connect(), row["id"])["id"] == row["id"]


def test_active_transaction_is_rejected_without_sql_or_committing_caller_work():
    database = FakeDatabase()
    connection = database.connect()
    query_count = len(database.queries)

    with connection.transaction():
        with pytest.raises(outbox.OutboxConnectionError, match="idle connection"):
            outbox.enqueue_notification(
                connection,
                source_type="task_reminder",
                source_key="active-transaction",
                destination_type="telegram",
                destination_id="chat-1",
                payload={"text": "Reminder"},
            )
        assert connection.info.transaction_status == TransactionStatus.INTRANS
        assert len(database.queries) == query_count
        assert database.transaction_commits == 0

    assert connection.info.transaction_status == TransactionStatus.IDLE
    assert database.transaction_commits == 1
    assert not database.rows


@pytest.mark.parametrize(
    "connection_state",
    ["active", "failed", "unknown", "closed"],
)
def test_failed_or_closed_connection_is_rejected_before_sql(connection_state):
    database = FakeDatabase()
    connection = database.connect()
    if connection_state == "active":
        connection.info.transaction_status = TransactionStatus.ACTIVE
    elif connection_state == "failed":
        connection.info.transaction_status = TransactionStatus.INERROR
    elif connection_state == "unknown":
        connection.info.transaction_status = TransactionStatus.UNKNOWN
    elif connection_state == "closed":
        connection.closed = True
    query_count = len(database.queries)

    with pytest.raises(outbox.OutboxConnectionError):
        outbox.claim_notifications(connection, "worker-a")

    assert len(database.queries) == query_count
    assert not database.rows
    assert database.transaction_commits == 0


def test_diagnostic_reads_are_scoped_and_repeated_operations_reuse_idle_connection():
    database = FakeDatabase()
    connection = database.connect()

    row = outbox.enqueue_notification(
        connection,
        source_type="task_reminder",
        source_key="repeat-operations",
        destination_type="telegram",
        destination_id="chat-1",
        payload={"text": "Reminder"},
    )
    assert connection.info.transaction_status == TransactionStatus.IDLE
    assert outbox.get_notification(connection, row["id"])["id"] == row["id"]
    assert connection.info.transaction_status == TransactionStatus.IDLE
    claims = outbox.claim_notifications(connection, "worker-a")
    assert connection.info.transaction_status == TransactionStatus.IDLE
    sending = outbox.mark_sending(connection, row["id"], claims[0]["claim_token"])
    assert connection.info.transaction_status == TransactionStatus.IDLE

    saved = outbox.record_success(connection, row["id"], sending["claim_token"], 8)

    assert saved["status"] == "sent"
    assert connection.info.transaction_status == TransactionStatus.IDLE
    assert database.transaction_commits == 5


def test_mark_sending_durable_commit_is_required_before_dispatcher_can_send():
    database = FakeDatabase()
    row = _enqueue(database)
    connection = database.connect()
    claim = outbox.claim_notifications(connection, "worker-a")[0]
    commits_before_sending = database.transaction_commits

    sending = outbox.mark_sending(connection, row["id"], claim["claim_token"])

    assert sending["status"] == "sending"
    assert database.transaction_commits == commits_before_sending + 1
    assert connection.info.transaction_status == TransactionStatus.IDLE
    persisted = outbox.get_notification(database.connect(), row["id"])
    assert persisted["status"] == "sending"
    assert persisted["attempt_count"] == 1
    assert persisted["send_started_at"] is not None


def test_missing_outbox_schema_is_explicit_and_never_falls_back_to_send():
    database = FakeDatabase()
    database.missing_schema = True

    with pytest.raises(psycopg.errors.UndefinedTable):
        outbox.claim_notifications(database.connect(), "worker-a")

    assert all("telegram" not in query.lower() for query, _ in database.queries)


def test_migration_defines_constraints_and_partial_indexes_without_case_access():
    migration = (ROOT / "migrations" / "0001_notification_outbox.sql").read_text()

    assert "CREATE TABLE IF NOT EXISTS notification_outbox" in migration
    assert "UNIQUE (source_type, source_key, destination_type, destination_id)" in migration
    assert "notification_outbox_source_key_check" in migration
    assert "notification_outbox_eligibility_idx" in migration
    assert "notification_outbox_lease_recovery_idx" in migration
    assert "FOR UPDATE SKIP LOCKED" not in migration
    assert "surgical_cases" not in migration

    database = FakeDatabase()
    row = _enqueue(database)
    claim = outbox.claim_notifications(database.connect(), "worker-a")[0]
    sending = outbox.mark_sending(database.connect(), row["id"], claim["claim_token"])
    outbox.record_success(database.connect(), row["id"], sending["claim_token"], 8)
    assert not any("surgical_cases" in query.lower() for query, _ in database.queries)
