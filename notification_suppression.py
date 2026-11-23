"""Persistence for historical Telegram alert suppression during outbox cutover.

This repository records history but never imports application settings or sends
notifications. Each public database operation owns a short transaction.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import re

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row


RESOLUTION_STATUSES = frozenset(
    {"resolved_suppressed", "unresolved", "ambiguous", "destination_unverified"}
)
_IDENTIFIER_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_MAX_SOURCE_KEY_LENGTH = 255


class SuppressionConnectionError(RuntimeError):
    """Raised when an operation cannot safely own its database transaction."""


class SuppressionImportConflict(RuntimeError):
    """Raised when an idempotency key is reused for different history."""


@dataclass(frozen=True)
class SuppressionDecision:
    """A safe dispatch gate: only ``clear`` permits a non-suppressed decision."""

    decision: str
    reason: str
    entry_id: int | None = None
    error_type: str | None = None


def _text(value, name, maximum, *, pattern=None):
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string.")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum or "\x00" in normalized:
        raise ValueError(f"{name} must be non-empty and at most {maximum} characters.")
    if pattern is not None and not pattern.fullmatch(normalized):
        raise ValueError(f"{name} has an invalid format.")
    return normalized


def _optional_source_identity(source_type, source_key):
    if (source_type is None) != (source_key is None):
        raise ValueError("source_type and source_key must be supplied together.")
    if source_type is None:
        return None, None
    return (
        _text(source_type, "source_type", 64, pattern=_IDENTIFIER_PATTERN),
        _text(source_key, "source_key", _MAX_SOURCE_KEY_LENGTH),
    )


def _require_idle_connection(connection):
    if connection is None or connection.closed:
        raise SuppressionConnectionError("Suppression operations require an open PostgreSQL connection.")
    try:
        status = connection.info.transaction_status
    except psycopg.Error as exc:
        raise SuppressionConnectionError(
            "Could not verify PostgreSQL connection transaction status."
        ) from exc
    if status != TransactionStatus.IDLE:
        raise SuppressionConnectionError(
            "Suppression operations require an idle connection; caller-owned transactions are not allowed."
        )


@contextmanager
def _operation_transaction(connection):
    _require_idle_connection(connection)
    with connection.transaction():
        yield


def record_legacy_suppression(
    connection,
    *,
    legacy_key,
    legacy_recorded_at=None,
    destination_type="telegram",
    destination_id=None,
    destination_verified=False,
    source_type=None,
    source_key=None,
    ambiguous=False,
    provenance,
    migration_batch_id,
    resolution_note=None,
):
    """Record one historical key idempotently; only verified, explicit mappings suppress."""
    key = _text(legacy_key, "legacy_key", 500)
    destination_kind = _text(
        destination_type, "destination_type", 64, pattern=_IDENTIFIER_PATTERN
    )
    batch = _text(migration_batch_id, "migration_batch_id", 128)
    origin = _text(provenance, "provenance", 128)
    if type(destination_verified) is not bool or type(ambiguous) is not bool:
        raise ValueError("destination_verified and ambiguous must be booleans.")

    normalized_destination = (
        _text(destination_id, "destination_id", 255) if destination_id is not None else None
    )
    if destination_verified and normalized_destination is None:
        raise ValueError("A verified destination requires an explicit destination_id.")
    normalized_source_type, normalized_source_key = _optional_source_identity(
        source_type, source_key
    )
    if ambiguous:
        status = "ambiguous"
    elif not destination_verified:
        status = "destination_unverified"
    elif normalized_source_type is None:
        status = "unresolved"
    else:
        status = "resolved_suppressed"

    note = None
    if resolution_note is not None:
        note = _text(resolution_note, "resolution_note", 1000)

    values = (
        key,
        legacy_recorded_at,
        destination_kind,
        normalized_destination,
        destination_verified,
        normalized_source_type,
        normalized_source_key,
        origin,
        batch,
        status,
        note,
    )
    with _operation_transaction(connection):
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                INSERT INTO legacy_notification_suppressions (
                    legacy_key, legacy_recorded_at, destination_type, destination_id,
                    destination_verified_at, source_type, source_key, provenance,
                    migration_batch_id, resolution_status, resolution_note
                )
                VALUES (
                    %s, %s, %s, %s,
                    CASE WHEN %s THEN NOW() ELSE NULL END,
                    %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT DO NOTHING
                RETURNING *
                """,
                values,
            )
            row = cursor.fetchone()
            if row is not None:
                return dict(row)

            cursor.execute(
                """
                SELECT *
                FROM legacy_notification_suppressions
                WHERE migration_batch_id = %s
                  AND legacy_key = %s
                  AND destination_type = %s
                  AND destination_id IS NOT DISTINCT FROM %s
                """,
                (batch, key, destination_kind, normalized_destination),
            )
            existing = cursor.fetchone()
            if existing is None:
                raise SuppressionImportConflict(
                    "The history row conflicted with an unrelated suppression record."
                )

            comparable = (
                existing["legacy_key"],
                existing["legacy_recorded_at"],
                existing["destination_type"],
                existing["destination_id"],
                existing["destination_verified_at"] is not None,
                existing["source_type"],
                existing["source_key"],
                existing["provenance"],
                existing["migration_batch_id"],
                existing["resolution_status"],
                existing["resolution_note"],
            )
            if comparable != values:
                raise SuppressionImportConflict(
                    "The migration batch already contains different data for this legacy key."
                )
            return dict(existing)


def lookup_suppression(
    connection,
    *,
    destination_type,
    destination_id,
    source_type,
    source_key,
    destination_verified,
):
    """Return suppressed, clear, or blocked; database errors produce a blocked decision."""
    if destination_verified is not True:
        raise ValueError("Suppression lookup requires explicit destination verification.")
    destination_kind = _text(
        destination_type, "destination_type", 64, pattern=_IDENTIFIER_PATTERN
    )
    normalized_destination = _text(destination_id, "destination_id", 255)
    normalized_source_type, normalized_source_key = _optional_source_identity(
        source_type, source_key
    )
    if normalized_source_type is None:
        raise ValueError("Suppression lookup requires a complete source identity.")

    try:
        with _operation_transaction(connection):
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT id
                    FROM legacy_notification_suppressions
                    WHERE resolution_status = 'resolved_suppressed'
                      AND destination_type = %s AND destination_id = %s
                      AND source_type = %s AND source_key = %s
                    ORDER BY id
                    LIMIT 1
                    """,
                    (
                        destination_kind,
                        normalized_destination,
                        normalized_source_type,
                        normalized_source_key,
                    ),
                )
                row = cursor.fetchone()
                if row is not None:
                    return SuppressionDecision(
                        "suppressed",
                        "An exact historical alert was recorded for this verified destination and source.",
                        row["id"],
                    )

                cursor.execute(
                    """
                    SELECT id
                    FROM legacy_notification_suppressions
                    WHERE resolution_status <> 'resolved_suppressed'
                      AND (
                          (destination_type = %s AND destination_id = %s)
                          OR destination_id IS NULL
                      )
                    ORDER BY id
                    LIMIT 1
                    """,
                    (destination_kind, normalized_destination),
                )
                unresolved = cursor.fetchone()
                if unresolved is not None:
                    return SuppressionDecision(
                        "blocked",
                        "Unresolved legacy history may overlap this destination; sending is not cleared.",
                        unresolved["id"],
                    )
                return SuppressionDecision(
                    "clear",
                    "No matching or unresolved legacy suppression record exists.",
                )
    except psycopg.Error as exc:
        return SuppressionDecision(
            "blocked",
            "Suppression lookup failed; delivery is not cleared.",
            error_type=type(exc).__name__,
        )


def list_unresolved_suppressions(connection, *, migration_batch_id=None, limit=500):
    """List unresolved, ambiguous, and destination-unverified historical records."""
    if type(limit) is not int or not 1 <= limit <= 5000:
        raise ValueError("limit must be an integer between 1 and 5000.")
    batch = (
        _text(migration_batch_id, "migration_batch_id", 128)
        if migration_batch_id is not None
        else None
    )
    with _operation_transaction(connection):
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT *
                FROM legacy_notification_suppressions
                WHERE resolution_status <> 'resolved_suppressed'
                  AND (%s IS NULL OR migration_batch_id = %s)
                ORDER BY id
                LIMIT %s
                """,
                (batch, batch, limit),
            )
            return [dict(row) for row in cursor.fetchall()]


def get_suppression_import_summary(connection, *, migration_batch_id=None):
    """Return import totals grouped by explicit resolution status."""
    batch = (
        _text(migration_batch_id, "migration_batch_id", 128)
        if migration_batch_id is not None
        else None
    )
    summary = {status: 0 for status in RESOLUTION_STATUSES}
    summary["total"] = 0
    with _operation_transaction(connection):
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT resolution_status, COUNT(*) AS entry_count
                FROM legacy_notification_suppressions
                WHERE (%s IS NULL OR migration_batch_id = %s)
                GROUP BY resolution_status
                """,
                (batch, batch),
            )
            for row in cursor.fetchall():
                summary[row["resolution_status"]] = row["entry_count"]
                summary["total"] += row["entry_count"]
    return summary
