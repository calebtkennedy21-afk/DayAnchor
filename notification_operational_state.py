"""Persistence primitives for worker health and Telegram polling state.

Worker operations own short transactions on idle connections. Receipt and
cursor operations participate in a caller-owned transaction so an inbox
processor can commit the action, receipt, and cursor advancement together.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import re
from uuid import UUID, uuid4

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row


MAX_WORKER_ID_LENGTH = 128
MAX_PROVENANCE_LENGTH = 128
MAX_DESTINATION_ID_LENGTH = 255
MAX_ERROR_DETAILS_LENGTH = 1000
MAX_RECEIPT_DETAIL_LENGTH = 500
RECEIPT_OUTCOMES = frozenset({"applied", "ignored", "rejected"})
WORKER_OUTCOMES = frozenset({"success", "failed"})
_BOT_ID_PATTERN = re.compile(r"[1-9][0-9]{0,19}\Z")
_IDENTIFIER_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_TELEGRAM_TOKEN_PATTERN = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")
_URL_CREDENTIAL_PATTERN = re.compile(
    r"(?i)((?:https?|postgres(?:ql)?)://)[^/@\s:]+:[^/@\s]+@"
)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(bot[_-]?token|access[_-]?token|password|passwd|token|secret|api[_-]?key)"
    r"\s*([=:])\s*([^\s,;]+)"
)


class OperationalStateConnectionError(RuntimeError):
    """Raised when a repository operation has unsafe connection ownership."""


class OperationalStateConflict(RuntimeError):
    """Raised when persisted operational identities or state conflict."""


class OperationalStateNotFound(RuntimeError):
    """Raised when required operational state has not been provisioned."""


@dataclass(frozen=True)
class UpdateReceiptResult:
    receipt: dict
    inserted: bool


def sanitize_error_details(value):
    """Redact common token, URL credential, and secret-assignment forms."""
    text = str(value or "")
    text = _TELEGRAM_TOKEN_PATTERN.sub("[REDACTED_TELEGRAM_TOKEN]", text)
    text = _URL_CREDENTIAL_PATTERN.sub(r"\1[REDACTED]@", text)
    text = _SECRET_ASSIGNMENT_PATTERN.sub(r"\1\2[REDACTED]", text)
    return text[:MAX_ERROR_DETAILS_LENGTH]


def _text(value, name, maximum, *, pattern=None):
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string.")
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > maximum
        or "\x00" in normalized
        or any(ord(char) < 32 for char in normalized)
    ):
        raise ValueError(f"{name} must be non-empty printable text of at most {maximum} characters.")
    if pattern is not None and not pattern.fullmatch(normalized):
        raise ValueError(f"{name} has an invalid format.")
    return normalized


def _bot_id(value):
    if isinstance(value, bool):
        raise ValueError("bot_id must be a trusted numeric Telegram bot identity.")
    normalized = str(value).strip() if isinstance(value, (str, int)) else ""
    if not _BOT_ID_PATTERN.fullmatch(normalized):
        raise ValueError("bot_id must be a trusted numeric Telegram bot identity.")
    return normalized


def _update_id(value):
    if type(value) is not int or value < 0:
        raise ValueError("update_id must be a non-negative integer.")
    return value


def _uuid(value, name):
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{name} must be a valid UUID.") from exc


def _optional_text(value, name, maximum):
    if value is None:
        return None
    return _text(value, name, maximum)


def _require_open(connection):
    if connection is None or connection.closed:
        raise OperationalStateConnectionError(
            "Operational-state operations require an open PostgreSQL connection."
        )
    try:
        return connection.info.transaction_status
    except psycopg.Error as exc:
        raise OperationalStateConnectionError(
            "Could not verify PostgreSQL connection transaction status."
        ) from exc


def _require_idle(connection):
    if _require_open(connection) != TransactionStatus.IDLE:
        raise OperationalStateConnectionError(
            "This operation owns its transaction and requires an idle connection."
        )


def _require_active(connection):
    if _require_open(connection) != TransactionStatus.INTRANS:
        raise OperationalStateConnectionError(
            "This operation participates in a caller-owned transaction."
        )


@contextmanager
def _owned_transaction(connection):
    _require_idle(connection)
    with connection.transaction():
        yield


def _fetchone_dict(cursor):
    row = cursor.fetchone()
    return dict(row) if row is not None else None


def start_worker_run(connection, worker_id, run_id=None):
    """Create a durable run record; the operation owns its transaction."""
    worker = _text(worker_id, "worker_id", MAX_WORKER_ID_LENGTH)
    run_uuid = _uuid(run_id if run_id is not None else uuid4(), "run_id")
    with _owned_transaction(connection):
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                INSERT INTO notification_worker_runs (worker_id, run_id)
                VALUES (%s, %s)
                RETURNING *
                """,
                (worker, run_uuid),
            )
            row = _fetchone_dict(cursor)
            if row is None:
                raise RuntimeError("Worker-run insert did not return its row.")
            return row


def complete_worker_run(connection, worker_id, run_id, *, outcome, error_details=None):
    """Complete one run once; old runs remain historical and cannot replace latest."""
    worker = _text(worker_id, "worker_id", MAX_WORKER_ID_LENGTH)
    run_uuid = _uuid(run_id, "run_id")
    if outcome not in WORKER_OUTCOMES:
        raise ValueError("outcome must be 'success' or 'failed'.")
    if outcome == "success" and error_details:
        raise ValueError("Successful worker runs cannot contain error details.")
    details = sanitize_error_details(error_details) if error_details else None
    details = details[:MAX_ERROR_DETAILS_LENGTH] if details is not None else None
    with _owned_transaction(connection):
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                UPDATE notification_worker_runs
                SET completed_at = NOW(), outcome = %s, error_details = %s
                WHERE worker_id = %s AND run_id = %s AND completed_at IS NULL
                RETURNING *
                """,
                (outcome, details, worker, run_uuid),
            )
            row = _fetchone_dict(cursor)
            if row is None:
                raise OperationalStateConflict(
                    "Worker run is missing or has already been completed."
                )
            return row


def get_worker_health(connection, worker_id):
    """Read the latest started run; completion order cannot supersede newer runs."""
    worker = _text(worker_id, "worker_id", MAX_WORKER_ID_LENGTH)
    with _owned_transaction(connection):
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT *
                FROM notification_worker_runs
                WHERE worker_id = %s
                ORDER BY started_at DESC, id DESC
                LIMIT 1
                """,
                (worker,),
            )
            return _fetchone_dict(cursor)


def initialize_verified_bot_cursor(
    connection,
    *,
    bot_id,
    destination_type,
    destination_id,
    bot_identity_verified,
    destination_verified,
    bot_identity_provenance,
    destination_binding_provenance,
    next_update_id=0,
):
    """Provision a bot-scoped cursor only from explicit trusted identities."""
    normalized_bot_id = _bot_id(bot_id)
    if bot_identity_verified is not True:
        raise ValueError("Bot identity must be explicitly verified before cursor provisioning.")
    if destination_verified is not True:
        raise ValueError("Destination binding must be explicitly verified before cursor provisioning.")
    destination_kind = _text(
        destination_type, "destination_type", 64, pattern=_IDENTIFIER_PATTERN
    )
    if destination_kind != "telegram":
        raise ValueError("Telegram polling cursors require destination_type='telegram'.")
    destination = _text(destination_id, "destination_id", MAX_DESTINATION_ID_LENGTH)
    bot_provenance = _text(
        bot_identity_provenance, "bot_identity_provenance", MAX_PROVENANCE_LENGTH
    )
    binding_provenance = _text(
        destination_binding_provenance,
        "destination_binding_provenance",
        MAX_PROVENANCE_LENGTH,
    )
    if type(next_update_id) is not int or next_update_id < 0:
        raise ValueError("next_update_id must be a non-negative integer.")

    with _owned_transaction(connection):
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                INSERT INTO telegram_poll_cursors (
                    bot_id, destination_type, destination_id,
                    bot_identity_provenance, destination_binding_provenance,
                    next_update_id
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (bot_id) DO NOTHING
                RETURNING *
                """,
                (
                    normalized_bot_id,
                    destination_kind,
                    destination,
                    bot_provenance,
                    binding_provenance,
                    next_update_id,
                ),
            )
            row = _fetchone_dict(cursor)
            if row is not None:
                return row
            cursor.execute(
                """
                SELECT *
                FROM telegram_poll_cursors
                WHERE bot_id = %s
                """,
                (normalized_bot_id,),
            )
            existing = _fetchone_dict(cursor)
            if existing is None:
                raise OperationalStateConflict(
                    "Bot cursor provisioning conflicted without an existing row."
                )
            expected_binding = (
                destination_kind,
                destination,
                bot_provenance,
                binding_provenance,
            )
            actual_binding = (
                existing["destination_type"],
                existing["destination_id"],
                existing["bot_identity_provenance"],
                existing["destination_binding_provenance"],
            )
            if actual_binding != expected_binding:
                raise OperationalStateConflict(
                    "Bot cursor already has a different verified identity or destination binding."
                )
            return existing


def get_bot_cursor(connection, bot_id):
    """Read a provisioned cursor using a repository-owned transaction."""
    normalized_bot_id = _bot_id(bot_id)
    with _owned_transaction(connection):
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT * FROM telegram_poll_cursors WHERE bot_id = %s",
                (normalized_bot_id,),
            )
            return _fetchone_dict(cursor)


def lock_bot_cursor(connection, bot_id):
    """Lock a provisioned bot cursor inside the caller-owned update transaction."""
    _require_active(connection)
    normalized_bot_id = _bot_id(bot_id)
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT *
            FROM telegram_poll_cursors
            WHERE bot_id = %s
            FOR UPDATE
            """,
            (normalized_bot_id,),
        )
        row = _fetchone_dict(cursor)
    if row is None:
        raise OperationalStateNotFound("Verified Telegram bot cursor has not been provisioned.")
    return row


def get_update_receipt(connection, *, bot_id, update_id):
    """Look up one receipt inside the caller-owned update transaction."""
    _require_active(connection)
    normalized_bot_id = _bot_id(bot_id)
    normalized_update_id = _update_id(update_id)
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT *
            FROM telegram_update_receipts
            WHERE bot_id = %s AND update_id = %s
            """,
            (normalized_bot_id, normalized_update_id),
        )
        return _fetchone_dict(cursor)


def record_update_receipt(
    connection,
    *,
    bot_id,
    update_id,
    destination_type,
    addressed_destination_id,
    outcome,
    outcome_detail=None,
):
    """Record a finalized action outcome within the caller's action transaction."""
    _require_active(connection)
    normalized_bot_id = _bot_id(bot_id)
    normalized_update_id = _update_id(update_id)
    destination_kind = _text(
        destination_type, "destination_type", 64, pattern=_IDENTIFIER_PATTERN
    )
    if destination_kind != "telegram":
        raise ValueError("Telegram update receipts require destination_type='telegram'.")
    destination = _optional_text(
        addressed_destination_id,
        "addressed_destination_id",
        MAX_DESTINATION_ID_LENGTH,
    )
    if outcome not in RECEIPT_OUTCOMES:
        raise ValueError("outcome must be applied, ignored, or rejected.")
    detail = _optional_text(outcome_detail, "outcome_detail", MAX_RECEIPT_DETAIL_LENGTH)
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            INSERT INTO telegram_update_receipts (
                bot_id, update_id, destination_type, addressed_destination_id,
                outcome, outcome_detail
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (bot_id, update_id) DO NOTHING
            RETURNING *
            """,
            (
                normalized_bot_id,
                normalized_update_id,
                destination_kind,
                destination,
                outcome,
                detail,
            ),
        )
        row = _fetchone_dict(cursor)
        if row is not None:
            return UpdateReceiptResult(row, True)
        cursor.execute(
            """
            SELECT *
            FROM telegram_update_receipts
            WHERE bot_id = %s AND update_id = %s
            """,
            (normalized_bot_id, normalized_update_id),
        )
        existing = _fetchone_dict(cursor)
        if existing is None:
            raise OperationalStateConflict(
                "Update receipt conflicted without an existing receipt."
            )
        identity = (
            existing["destination_type"],
            existing["addressed_destination_id"],
            existing["outcome"],
            existing["outcome_detail"],
        )
        requested = (destination_kind, destination, outcome, detail)
        if identity != requested:
            raise OperationalStateConflict(
                "Update was already recorded with a different destination or outcome."
            )
        return UpdateReceiptResult(existing, False)


def advance_bot_cursor(connection, *, bot_id, update_id):
    """Advance only a receipted update in the caller transaction.

    The processor must lock the cursor before action handling and record the
    finalized action receipt before calling this function.
    """
    _require_active(connection)
    normalized_bot_id = _bot_id(bot_id)
    normalized_update_id = _update_id(update_id)
    lock_bot_cursor(connection, normalized_bot_id)
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT id
            FROM telegram_update_receipts
            WHERE bot_id = %s AND update_id = %s
            """,
            (normalized_bot_id, normalized_update_id),
        )
        if _fetchone_dict(cursor) is None:
            raise OperationalStateConflict(
                "Cursor cannot advance until the update action has a durable receipt."
            )
        cursor.execute(
            """
            UPDATE telegram_poll_cursors
            SET next_update_id = GREATEST(next_update_id, %s),
                updated_at = NOW()
            WHERE bot_id = %s
            RETURNING *
            """,
            (normalized_update_id + 1, normalized_bot_id),
        )
        row = _fetchone_dict(cursor)
    if row is None:
        raise OperationalStateNotFound("Verified Telegram bot cursor has not been provisioned.")
    return row
