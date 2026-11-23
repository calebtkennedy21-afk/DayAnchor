"""PostgreSQL operations and state rules for the inactive notification outbox.

Each repository operation owns a short transaction on an idle connection.
Callers must not wrap these operations in an outer transaction.
"""

from contextlib import contextmanager
import json
import math
import random
import re
from dataclasses import dataclass
from typing import Callable
from uuid import UUID, uuid4

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row


STATUSES = frozenset({"pending", "claimed", "sending", "sent", "failed", "uncertain", "dead"})
ALLOWED_TRANSITIONS = {
    "pending": frozenset({"claimed", "dead"}),
    "claimed": frozenset({"sending", "pending", "dead"}),
    "sending": frozenset({"sent", "failed", "uncertain", "dead"}),
    "failed": frozenset({"claimed", "dead"}),
    "uncertain": frozenset({"sent", "dead", "pending"}),
    "sent": frozenset(),
    "dead": frozenset(),
}
FAILURE_CLASSIFICATIONS = frozenset({"transient", "rate_limited", "permanent", "unknown"})
MAX_ATTEMPTS = 8
MAX_BACKOFF_SECONDS = 24 * 60 * 60
MAX_ERROR_LENGTH = 1000
IDENTIFIER_MAX_LENGTH = 255


class OutboxConnectionError(RuntimeError):
    """Raised when an outbox operation cannot safely own its transaction."""


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = MAX_ATTEMPTS
    base_delay_seconds: float = 60
    max_delay_seconds: float = MAX_BACKOFF_SECONDS

    def __post_init__(self):
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= MAX_ATTEMPTS:
            raise ValueError(f"max_attempts must be between 1 and {MAX_ATTEMPTS}.")
        if self.base_delay_seconds <= 0:
            raise ValueError("base_delay_seconds must be positive.")
        if not self.base_delay_seconds <= self.max_delay_seconds <= MAX_BACKOFF_SECONDS:
            raise ValueError(f"max_delay_seconds must be between base delay and {MAX_BACKOFF_SECONDS}.")


DEFAULT_RETRY_POLICY = RetryPolicy()


def transition_allowed(current_status, next_status):
    """Return whether a state transition is part of the outbox contract."""
    return current_status in STATUSES and next_status in ALLOWED_TRANSITIONS.get(current_status, ())


def retry_delay_seconds(attempt_count, policy=DEFAULT_RETRY_POLICY, random_fn=random.uniform):
    """Return bounded exponential backoff with multiplicative jitter."""
    if type(attempt_count) is not int or attempt_count < 1:
        raise ValueError("attempt_count must be a positive integer.")
    exponent_limit = max(
        0,
        math.ceil(math.log2(policy.max_delay_seconds / policy.base_delay_seconds)),
    )
    exponent = min(attempt_count - 1, exponent_limit)
    delay = min(policy.max_delay_seconds, policy.base_delay_seconds * (2**exponent))
    return min(policy.max_delay_seconds, max(0.0, random_fn(0.5, 1.5) * delay))


def _stable_identifier(value, name, *, pattern=None):
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a stable string identifier.")
    normalized = value.strip()
    if not normalized or len(normalized) > IDENTIFIER_MAX_LENGTH or "\x00" in normalized:
        raise ValueError(f"{name} must be a non-empty identifier of at most {IDENTIFIER_MAX_LENGTH} characters.")
    if pattern and not pattern.fullmatch(normalized):
        raise ValueError(f"{name} has an invalid format.")
    return normalized


def _identity(source_type, source_key, destination_type, destination_id):
    return (
        _stable_identifier(source_type, "source_type", pattern=_SOURCE_TYPE_PATTERN),
        _stable_identifier(source_key, "source_key"),
        _stable_identifier(destination_type, "destination_type", pattern=_DESTINATION_TYPE_PATTERN),
        _stable_identifier(destination_id, "destination_id"),
    )


def _claim_token(value):
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("claim_token must be a valid UUID.") from exc


def _bounded_error(value):
    return str(value or "")[:MAX_ERROR_LENGTH]


def _fetchone_dict(cursor):
    row = cursor.fetchone()
    return dict(row) if row is not None else None


def _require_idle_connection(connection):
    if connection is None or connection.closed:
        raise OutboxConnectionError("Outbox operations require an open PostgreSQL connection.")
    try:
        status = connection.info.transaction_status
    except psycopg.Error as exc:
        raise OutboxConnectionError("Could not verify PostgreSQL connection transaction status.") from exc
    if status != TransactionStatus.IDLE:
        raise OutboxConnectionError(
            "Outbox operations require an idle connection; caller-owned or failed transactions are not allowed."
        )


@contextmanager
def _operation_transaction(connection):
    _require_idle_connection(connection)
    with connection.transaction():
        yield


def enqueue_notification(
    connection,
    *,
    source_type,
    source_key,
    destination_type,
    destination_id,
    payload,
    expires_at=None,
    max_attempts=MAX_ATTEMPTS,
):
    """Insert once; source_key must be a persisted ID, never display text or list position."""
    identity = _identity(source_type, source_key, destination_type, destination_id)
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object.")
    if type(max_attempts) is not int or not 1 <= max_attempts <= MAX_ATTEMPTS:
        raise ValueError(f"max_attempts must be between 1 and {MAX_ATTEMPTS}.")
    try:
        serialized_payload = json.dumps(payload, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("payload must contain valid JSON values.") from exc

    with _operation_transaction(connection):
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                INSERT INTO notification_outbox (
                    source_type, source_key, destination_type, destination_id,
                    payload, status, max_attempts, available_at, expires_at
                )
                VALUES (%s, %s, %s, %s, %s::jsonb, 'pending', %s, NOW(), %s)
                ON CONFLICT (source_type, source_key, destination_type, destination_id) DO NOTHING
                RETURNING *
                """,
                (*identity, serialized_payload, max_attempts, expires_at),
            )
            row = _fetchone_dict(cursor)
            if row is not None:
                return row
            cursor.execute(
                """
                SELECT * FROM notification_outbox
                WHERE source_type = %s AND source_key = %s
                  AND destination_type = %s AND destination_id = %s
                """,
                identity,
            )
            row = _fetchone_dict(cursor)
            if row is None:
                raise RuntimeError("Notification identity conflict did not resolve to an existing row.")
            return row


def claim_notifications(connection, owner, limit=20, lease_seconds=120):
    """Atomically claim a bounded batch; returned claims have unique fencing tokens.

    The operation commits before returning. Callers must use an idle connection
    and must not wrap it in an outer transaction.
    """
    owner = _stable_identifier(owner, "owner")
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer.")
    if type(lease_seconds) is not int or lease_seconds < 1:
        raise ValueError("lease_seconds must be a positive integer.")

    claimed = []
    with _operation_transaction(connection):
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT id
                FROM notification_outbox
                WHERE status IN ('pending', 'failed')
                  AND available_at <= NOW()
                  AND (expires_at IS NULL OR expires_at > NOW())
                  AND attempt_count < max_attempts
                ORDER BY available_at, id
                LIMIT %s
                FOR UPDATE SKIP LOCKED
                """,
                (limit,),
            )
            candidates = cursor.fetchall()
            for candidate in candidates:
                token = uuid4()
                cursor.execute(
                    """
                    UPDATE notification_outbox
                    SET status = 'claimed', claim_owner = %s, claim_token = %s,
                        lease_expires_at = NOW() + %s * INTERVAL '1 second',
                        updated_at = NOW()
                    WHERE id = %s AND status IN ('pending', 'failed')
                      AND available_at <= NOW()
                      AND (expires_at IS NULL OR expires_at > NOW())
                      AND attempt_count < max_attempts
                    RETURNING *
                    """,
                    (owner, token, lease_seconds, candidate["id"]),
                )
                row = _fetchone_dict(cursor)
                if row is not None:
                    claimed.append(row)
    return claimed


def mark_sending(connection, notification_id, claim_token):
    """Commit send-start before returning; a dispatcher must send only on success.

    The connection must be idle so releasing this transaction cannot be a
    nested savepoint inside caller-owned work.
    """
    token = _claim_token(claim_token)
    with _operation_transaction(connection):
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                UPDATE notification_outbox
                SET status = 'sending', attempt_count = attempt_count + 1,
                    send_started_at = NOW(), updated_at = NOW()
                WHERE id = %s AND status = 'claimed' AND claim_token = %s
                  AND lease_expires_at > NOW()
                  AND (expires_at IS NULL OR expires_at > NOW())
                  AND attempt_count < max_attempts
                RETURNING *
                """,
                (notification_id, token),
            )
            return _fetchone_dict(cursor)


def record_success(connection, notification_id, claim_token, telegram_message_id):
    """Persist a confirmed Telegram success; stale claim tokens cannot finalize."""
    token = _claim_token(claim_token)
    if type(telegram_message_id) is not int or telegram_message_id < 1:
        raise ValueError("telegram_message_id must be a positive integer.")
    with _operation_transaction(connection):
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                UPDATE notification_outbox
                SET status = 'sent', telegram_message_id = %s, sent_at = NOW(),
                    claim_owner = NULL, claim_token = NULL, lease_expires_at = NULL,
                    last_error = NULL, error_classification = NULL, updated_at = NOW()
                WHERE id = %s AND status = 'sending' AND claim_token = %s
                RETURNING *
                """,
                (telegram_message_id, notification_id, token),
            )
            return _fetchone_dict(cursor)


def record_failure(
    connection,
    notification_id,
    claim_token,
    *,
    classification,
    error,
    policy=DEFAULT_RETRY_POLICY,
    random_fn=random.uniform,
):
    """Record a definite failure, retrying only classified transient failures."""
    if classification not in FAILURE_CLASSIFICATIONS:
        raise ValueError(f"classification must be one of {sorted(FAILURE_CLASSIFICATIONS)}.")
    token = _claim_token(claim_token)
    retryable = classification in {"transient", "rate_limited"}
    with _operation_transaction(connection):
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT attempt_count FROM notification_outbox
                WHERE id = %s AND status = 'sending' AND claim_token = %s
                FOR UPDATE
                """,
                (notification_id, token),
            )
            current = _fetchone_dict(cursor)
            if current is None:
                return None
            delay = retry_delay_seconds(current["attempt_count"], policy, random_fn) if retryable else 0
            cursor.execute(
                """
                UPDATE notification_outbox
                SET status = CASE
                        WHEN expires_at IS NOT NULL AND expires_at <= NOW() THEN 'dead'
                        WHEN %s AND attempt_count < LEAST(max_attempts, %s) THEN 'failed'
                        ELSE 'dead'
                    END,
                    available_at = CASE
                        WHEN %s AND attempt_count < LEAST(max_attempts, %s)
                          AND (expires_at IS NULL OR expires_at > NOW())
                        THEN NOW() + %s * INTERVAL '1 second'
                        ELSE available_at
                    END,
                    error_classification = CASE
                        WHEN expires_at IS NOT NULL AND expires_at <= NOW() THEN 'expired'
                        ELSE %s
                    END,
                    last_error = %s, claim_owner = NULL, claim_token = NULL,
                    lease_expires_at = NULL, updated_at = NOW()
                WHERE id = %s AND status = 'sending' AND claim_token = %s
                RETURNING *
                """,
                (
                    retryable,
                    policy.max_attempts,
                    retryable,
                    policy.max_attempts,
                    delay,
                    classification,
                    _bounded_error(error),
                    notification_id,
                    token,
                ),
            )
            return _fetchone_dict(cursor)


def record_uncertain(connection, notification_id, claim_token, error):
    """Persist an ambiguous send result without making it eligible for retry."""
    token = _claim_token(claim_token)
    with _operation_transaction(connection):
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                UPDATE notification_outbox
                SET status = 'uncertain', uncertain_at = NOW(),
                    error_classification = 'uncertain', last_error = %s,
                    claim_owner = NULL, claim_token = NULL, lease_expires_at = NULL,
                    updated_at = NOW()
                WHERE id = %s AND status = 'sending' AND claim_token = %s
                RETURNING *
                """,
                (_bounded_error(error), notification_id, token),
            )
            return _fetchone_dict(cursor)


def recover_expired_leases(connection):
    """Recover pre-send claims safely and quarantine claims whose send had started."""
    with _operation_transaction(connection):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE notification_outbox
                SET status = CASE
                        WHEN expires_at IS NOT NULL AND expires_at <= NOW() THEN 'dead'
                        ELSE 'pending'
                    END,
                    error_classification = CASE
                        WHEN expires_at IS NOT NULL AND expires_at <= NOW() THEN 'expired'
                        ELSE error_classification
                    END,
                    last_error = CASE
                        WHEN expires_at IS NOT NULL AND expires_at <= NOW()
                        THEN 'Notification expired before sending.'
                        ELSE last_error
                    END,
                    claim_owner = NULL, claim_token = NULL, lease_expires_at = NULL,
                    updated_at = NOW()
                WHERE status = 'claimed' AND lease_expires_at <= NOW()
                """
            )
            claimed_recovered = cursor.rowcount
            cursor.execute(
                """
                UPDATE notification_outbox
                SET status = 'uncertain', uncertain_at = NOW(),
                    error_classification = 'uncertain',
                    last_error = 'Lease expired after sending started; delivery is uncertain.',
                    claim_owner = NULL, claim_token = NULL, lease_expires_at = NULL,
                    updated_at = NOW()
                WHERE status = 'sending' AND lease_expires_at <= NOW()
                """
            )
            sending_recovered = cursor.rowcount
            cursor.execute(
                """
                UPDATE notification_outbox
                SET status = 'dead', error_classification = 'expired',
                    last_error = 'Notification expired before sending.',
                    updated_at = NOW()
                WHERE status IN ('pending', 'failed')
                  AND expires_at IS NOT NULL AND expires_at <= NOW()
                """
            )
            expired = cursor.rowcount
    return {
        "claimed_recovered": claimed_recovered,
        "sending_uncertain": sending_recovered,
        "expired": expired,
    }


def get_notification(connection, notification_id):
    """Read one row in an owned transaction without leaving it open.

    Like mutations, diagnostic reads require an idle connection and must not be
    called inside a caller-owned transaction.
    """
    with _operation_transaction(connection):
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SELECT * FROM notification_outbox WHERE id = %s", (notification_id,))
            return _fetchone_dict(cursor)


_SOURCE_TYPE_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_DESTINATION_TYPE_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
