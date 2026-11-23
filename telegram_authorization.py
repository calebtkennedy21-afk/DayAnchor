"""Audited storage for owner-approved Telegram destinations and senders."""

from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping
from uuid import UUID, uuid4

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row

import telegram_identity


APPROVAL_PROVENANCES = frozenset({"authenticated_owner_ui"})
IDENTITY_PROVENANCE = "telegram_get_me"
MAX_ACTOR_ID_LENGTH = 128


class TelegramAuthorizationError(RuntimeError):
    """Base error for safe authorization repository failures."""


class AuthorizationConnectionError(TelegramAuthorizationError):
    """Raised when a repository operation has unsafe transaction ownership."""


class AuthorizationConflict(TelegramAuthorizationError):
    """Raised when an approval or revocation conflicts with current state."""


class AuthorizationNotFound(TelegramAuthorizationError):
    """Raised when the requested binding or sender approval does not exist."""


class AuthorizationStorageError(TelegramAuthorizationError):
    """Raised when PostgreSQL cannot complete an authorization operation."""


class OwnerAuthorizationDenied(TelegramAuthorizationError):
    """Raised when a trusted owner/operator authorization is unavailable."""


class OwnerAuthorizationAuthority(ABC):
    """Caller-supplied policy hook, not cryptographic proof of authentication.

    Production callers must be restricted by trusted application composition;
    arbitrary Python code can implement this interface.
    """

    @abstractmethod
    def authorize_telegram_change(
        self, action: str, resource: Mapping[str, str]
    ) -> "OwnerAuthorizationGrant | None":
        """Return a grant only for an authenticated, authorized DayAnchor operator."""


@dataclass(frozen=True)
class OwnerAuthorizationGrant:
    """Audit values returned by an authority hook; not proof by themselves."""

    actor_identity: str
    provenance: str


@dataclass(frozen=True)
class SenderAuthorizationDecision:
    allowed: bool
    reason_code: str
    binding_id: UUID | None = None


def _text(value: object, name: str, maximum: int) -> str:
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
    return normalized


def _bot_id(identity: telegram_identity.BotIdentityVerification, now: datetime) -> str:
    bot_id = telegram_identity.get_fresh_verified_bot_id(identity, now=now)
    if bot_id is None:
        raise OwnerAuthorizationDenied(
            "A fresh authenticated Telegram bot identity is required."
        )
    return bot_id


def _destination_id(value: object) -> str:
    destination = telegram_identity.normalize_telegram_destination_id(value)
    if destination is None:
        raise ValueError("destination_id must be a numeric Telegram destination ID.")
    return destination


def _sender_id(value: object) -> str:
    sender = telegram_identity.normalize_authorized_sender_id(value)
    if sender is None:
        raise ValueError("sender_user_id must be a positive numeric Telegram user ID.")
    return sender


def _binding_id(value: object) -> UUID:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("binding_id must be a valid UUID.") from exc


def _require_idle(connection: psycopg.Connection) -> None:
    if connection is None or connection.closed:
        raise AuthorizationConnectionError(
            "Telegram authorization operations require an open PostgreSQL connection."
        )
    try:
        status = connection.info.transaction_status
    except psycopg.Error as exc:
        raise AuthorizationConnectionError(
            "Could not verify PostgreSQL transaction status."
        ) from exc
    if status != TransactionStatus.IDLE:
        raise AuthorizationConnectionError(
            "This operation owns its transaction and requires an idle connection."
        )


def _require_active(connection: psycopg.Connection) -> None:
    if connection is None or connection.closed:
        raise AuthorizationConnectionError(
            "Telegram authorization requires an open PostgreSQL connection."
        )
    try:
        status = connection.info.transaction_status
    except psycopg.Error as exc:
        raise AuthorizationConnectionError(
            "Could not verify PostgreSQL transaction status."
        ) from exc
    if status != TransactionStatus.INTRANS:
        raise AuthorizationConnectionError(
            "This operation participates in a caller-owned transaction."
        )


@contextmanager
def _owned_transaction(connection: psycopg.Connection):
    _require_idle(connection)
    try:
        with connection.transaction():
            yield
    except psycopg.Error:
        raise AuthorizationStorageError(
            "Telegram authorization storage is unavailable."
        ) from None


def _fetchone_dict(cursor) -> dict | None:
    row = cursor.fetchone()
    return dict(row) if row is not None else None


def _fetchall_dict(cursor) -> list[dict]:
    return [dict(row) for row in cursor.fetchall()]


def _trusted_grant(
    authority: OwnerAuthorizationAuthority | None,
    action: str,
    resource: Mapping[str, str],
) -> OwnerAuthorizationGrant:
    if not isinstance(authority, OwnerAuthorizationAuthority):
        raise OwnerAuthorizationDenied(
            "An authenticated owner/operator authorization provider is required."
        )
    try:
        grant = authority.authorize_telegram_change(action, resource)
    except Exception:
        raise OwnerAuthorizationDenied(
            "The authenticated owner/operator could not be verified."
        ) from None
    if not isinstance(grant, OwnerAuthorizationGrant):
        raise OwnerAuthorizationDenied(
            "The authenticated owner/operator did not authorize this change."
        )
    actor = _text(grant.actor_identity, "actor_identity", MAX_ACTOR_ID_LENGTH)
    if grant.provenance not in APPROVAL_PROVENANCES:
        raise OwnerAuthorizationDenied(
            "The owner authorization provider returned invalid provenance."
        )
    return OwnerAuthorizationGrant(actor_identity=actor, provenance=grant.provenance)


def _now_utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware.")
    return current.astimezone(timezone.utc)


def read_approved_destination_bindings(
    connection: psycopg.Connection, *, bot_id: object | None = None
) -> list[dict]:
    """Read active bindings; the short read transaction is repository-owned."""
    normalized_bot_id = None
    if bot_id is not None:
        normalized_bot_id = telegram_identity.normalize_verified_bot_id(bot_id)
        if normalized_bot_id is None:
            raise ValueError("bot_id must be a positive numeric Telegram bot identity.")
    with _owned_transaction(connection):
        with connection.cursor(row_factory=dict_row) as cursor:
            if normalized_bot_id is None:
                cursor.execute(
                    """
                    SELECT *
                    FROM telegram_destination_bindings
                    WHERE approval_status = 'approved'
                    ORDER BY bot_id, approved_at, binding_id
                    """
                )
            else:
                cursor.execute(
                    """
                    SELECT *
                    FROM telegram_destination_bindings
                    WHERE approval_status = 'approved' AND bot_id = %s
                    ORDER BY approved_at, binding_id
                    """,
                    (normalized_bot_id,),
                )
            return _fetchall_dict(cursor)


def read_authorized_senders(
    connection: psycopg.Connection, *, binding_id: object
) -> list[dict]:
    normalized_binding_id = _binding_id(binding_id)
    with _owned_transaction(connection):
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT sender.*
                FROM telegram_authorized_senders AS sender
                JOIN telegram_destination_bindings AS binding
                  ON binding.binding_id = sender.binding_id
                WHERE sender.binding_id = %s
                  AND sender.approval_status = 'approved'
                  AND binding.approval_status = 'approved'
                ORDER BY sender.sender_user_id
                """,
                (normalized_binding_id,),
            )
            return _fetchall_dict(cursor)


def approve_destination_binding(
    connection: psycopg.Connection,
    *,
    identity: telegram_identity.BotIdentityVerification,
    destination_id: object,
    authority: OwnerAuthorizationAuthority | None,
    now: datetime | None = None,
) -> dict:
    """Create an immutable binding only after verified identity and owner grant."""
    current_time = _now_utc(now)
    bot_id = _bot_id(identity, current_time)
    destination = _destination_id(destination_id)
    verified_at = identity.verified_at
    if verified_at is None:
        raise OwnerAuthorizationDenied(
            "A fresh authenticated Telegram bot identity is required."
        )
    grant = _trusted_grant(
        authority,
        "approve_destination",
        {"bot_id": bot_id, "destination_type": "telegram", "destination_id": destination},
    )

    with _owned_transaction(connection):
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT *
                FROM telegram_destination_bindings
                WHERE bot_id = %s AND approval_status = 'approved'
                FOR UPDATE
                """,
                (bot_id,),
            )
            existing = _fetchone_dict(cursor)
            if existing is not None and existing["destination_id"] != destination:
                raise AuthorizationConflict(
                    "This bot already has a different active destination binding."
                )
            if existing is not None:
                return existing

            cursor.execute(
                """
                INSERT INTO telegram_destination_bindings (
                    binding_id, bot_id, destination_type, destination_id,
                    bot_identity_verified_at, bot_identity_provenance,
                    approver_actor_id, approval_provenance
                )
                VALUES (%s, %s, 'telegram', %s, %s, %s, %s, %s)
                ON CONFLICT (bot_id) WHERE approval_status = 'approved'
                DO NOTHING
                RETURNING *
                """,
                (
                    uuid4(),
                    bot_id,
                    destination,
                    verified_at.astimezone(timezone.utc),
                    IDENTITY_PROVENANCE,
                    grant.actor_identity,
                    grant.provenance,
                ),
            )
            row = _fetchone_dict(cursor)
            if row is not None:
                return row
            cursor.execute(
                """
                SELECT *
                FROM telegram_destination_bindings
                WHERE bot_id = %s AND approval_status = 'approved'
                FOR UPDATE
                """,
                (bot_id,),
            )
            concurrent = _fetchone_dict(cursor)
            if concurrent is None or concurrent["destination_id"] != destination:
                raise AuthorizationConflict(
                    "A concurrent destination approval conflicts with this request."
                )
            return concurrent


def approve_sender(
    connection: psycopg.Connection,
    *,
    binding_id: object,
    sender_user_id: object,
    authority: OwnerAuthorizationAuthority | None,
) -> dict:
    """Approve one sender; sender-list replacement is intentionally unsupported."""
    normalized_binding_id = _binding_id(binding_id)
    sender_id = _sender_id(sender_user_id)

    with _owned_transaction(connection):
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT *
                FROM telegram_destination_bindings
                WHERE binding_id = %s
                FOR UPDATE
                """,
                (normalized_binding_id,),
            )
            binding = _fetchone_dict(cursor)
            if binding is None or binding["approval_status"] != "approved":
                raise AuthorizationNotFound(
                    "An active approved destination binding is required."
                )
            grant = _trusted_grant(
                authority,
                "approve_sender",
                {
                    "binding_id": str(binding["binding_id"]),
                    "bot_id": binding["bot_id"],
                    "destination_type": binding["destination_type"],
                    "destination_id": binding["destination_id"],
                    "sender_user_id": sender_id,
                },
            )
            cursor.execute(
                """
                SELECT *
                FROM telegram_authorized_senders
                WHERE binding_id = %s
                  AND sender_user_id = %s
                  AND approval_status = 'approved'
                FOR UPDATE
                """,
                (normalized_binding_id, sender_id),
            )
            existing = _fetchone_dict(cursor)
            if existing is not None:
                return existing

            cursor.execute(
                """
                INSERT INTO telegram_authorized_senders (
                    sender_approval_id, binding_id, bot_id, sender_user_id,
                    approver_actor_id, approval_provenance
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (binding_id, sender_user_id)
                    WHERE approval_status = 'approved'
                DO NOTHING
                RETURNING *
                """,
                (
                    uuid4(),
                    normalized_binding_id,
                    binding["bot_id"],
                    sender_id,
                    grant.actor_identity,
                    grant.provenance,
                ),
            )
            row = _fetchone_dict(cursor)
            if row is not None:
                return row
            cursor.execute(
                """
                SELECT *
                FROM telegram_authorized_senders
                WHERE binding_id = %s
                  AND sender_user_id = %s
                  AND approval_status = 'approved'
                FOR UPDATE
                """,
                (normalized_binding_id, sender_id),
            )
            concurrent = _fetchone_dict(cursor)
            if concurrent is None:
                raise AuthorizationConflict(
                    "A concurrent sender approval conflicts with this request."
                )
            return concurrent


def revoke_destination_binding(
    connection: psycopg.Connection,
    *,
    binding_id: object,
    authority: OwnerAuthorizationAuthority | None,
) -> dict:
    normalized_binding_id = _binding_id(binding_id)
    with _owned_transaction(connection):
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT *
                FROM telegram_destination_bindings
                WHERE binding_id = %s
                FOR UPDATE
                """,
                (normalized_binding_id,),
            )
            binding = _fetchone_dict(cursor)
            if binding is None:
                raise AuthorizationNotFound("Destination binding was not found.")
            if binding["approval_status"] != "approved":
                raise AuthorizationConflict("Destination binding is already revoked.")
            grant = _trusted_grant(
                authority,
                "revoke_destination",
                {
                    "binding_id": str(binding["binding_id"]),
                    "bot_id": binding["bot_id"],
                    "destination_type": binding["destination_type"],
                    "destination_id": binding["destination_id"],
                },
            )
            cursor.execute(
                """
                UPDATE telegram_destination_bindings
                SET approval_status = 'revoked',
                    revoked_at = NOW(),
                    revoked_by_actor_id = %s,
                    revocation_provenance = %s,
                    updated_at = NOW()
                WHERE binding_id = %s AND approval_status = 'approved'
                RETURNING *
                """,
                (grant.actor_identity, grant.provenance, normalized_binding_id),
            )
            revoked = _fetchone_dict(cursor)
            if revoked is None:
                raise AuthorizationConflict(
                    "Destination binding changed before revocation completed."
                )
            cursor.execute(
                """
                UPDATE telegram_authorized_senders
                SET approval_status = 'revoked',
                    revoked_at = NOW(),
                    revoked_by_actor_id = %s,
                    revocation_provenance = %s,
                    updated_at = NOW()
                WHERE binding_id = %s AND approval_status = 'approved'
                """,
                (grant.actor_identity, grant.provenance, normalized_binding_id),
            )
            return revoked


def revoke_sender(
    connection: psycopg.Connection,
    *,
    binding_id: object,
    sender_user_id: object,
    authority: OwnerAuthorizationAuthority | None,
) -> dict:
    normalized_binding_id = _binding_id(binding_id)
    sender_id = _sender_id(sender_user_id)
    with _owned_transaction(connection):
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT *
                FROM telegram_destination_bindings
                WHERE binding_id = %s
                FOR UPDATE
                """,
                (normalized_binding_id,),
            )
            binding = _fetchone_dict(cursor)
            if binding is None:
                raise AuthorizationNotFound("Destination binding was not found.")
            cursor.execute(
                """
                SELECT *
                FROM telegram_authorized_senders
                WHERE binding_id = %s
                  AND sender_user_id = %s
                  AND approval_status = 'approved'
                FOR UPDATE
                """,
                (normalized_binding_id, sender_id),
            )
            sender = _fetchone_dict(cursor)
            if sender is None:
                raise AuthorizationNotFound("Active sender approval was not found.")
            grant = _trusted_grant(
                authority,
                "revoke_sender",
                {
                    "binding_id": str(binding["binding_id"]),
                    "bot_id": binding["bot_id"],
                    "destination_type": binding["destination_type"],
                    "destination_id": binding["destination_id"],
                    "sender_user_id": sender_id,
                },
            )
            cursor.execute(
                """
                UPDATE telegram_authorized_senders
                SET approval_status = 'revoked',
                    revoked_at = NOW(),
                    revoked_by_actor_id = %s,
                    revocation_provenance = %s,
                    updated_at = NOW()
                WHERE sender_approval_id = %s
                  AND approval_status = 'approved'
                RETURNING *
                """,
                (
                    grant.actor_identity,
                    grant.provenance,
                    sender["sender_approval_id"],
                ),
            )
            revoked = _fetchone_dict(cursor)
            if revoked is None:
                raise AuthorizationConflict(
                    "Sender approval changed before revocation completed."
                )
            return revoked


def authorize_destination_sender(
    connection: psycopg.Connection,
    *,
    bot_id: object,
    destination_id: object,
    sender_user_id: object,
) -> SenderAuthorizationDecision:
    """Check authorization while locking approvals through the caller's commit.

    An inbox processor must call this inside the same transaction that records
    and commits the action. The row locks make revocation wait for that commit;
    if revocation commits first, this check must deny. Checking authorization
    outside the action transaction would reintroduce a revocation race.
    """
    _require_active(connection)
    normalized_bot_id = telegram_identity.normalize_verified_bot_id(bot_id)
    destination = telegram_identity.normalize_telegram_destination_id(destination_id)
    sender = telegram_identity.normalize_authorized_sender_id(sender_user_id)
    if normalized_bot_id is None or destination is None or sender is None:
        return SenderAuthorizationDecision(False, "identity_incomplete")

    try:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT binding.binding_id
                FROM telegram_destination_bindings AS binding
                JOIN telegram_authorized_senders AS authorized
                  ON authorized.binding_id = binding.binding_id
                 AND authorized.bot_id = binding.bot_id
                WHERE binding.bot_id = %s
                  AND binding.destination_type = 'telegram'
                  AND binding.destination_id = %s
                  AND binding.approval_status = 'approved'
                  AND authorized.sender_user_id = %s
                  AND authorized.approval_status = 'approved'
                FOR SHARE OF binding, authorized
                """,
                (normalized_bot_id, destination, sender),
            )
            row = _fetchone_dict(cursor)
    except psycopg.Error:
        raise AuthorizationStorageError(
            "Telegram authorization storage is unavailable."
        ) from None
    if row is None:
        return SenderAuthorizationDecision(False, "not_authorized")
    return SenderAuthorizationDecision(True, "authorized", row["binding_id"])


def get_approval_history(
    connection: psycopg.Connection,
    *,
    bot_id: object | None = None,
    binding_id: object | None = None,
) -> dict[str, list[dict]]:
    normalized_bot_id = None
    normalized_binding_id = None
    if bot_id is not None:
        normalized_bot_id = telegram_identity.normalize_verified_bot_id(bot_id)
        if normalized_bot_id is None:
            raise ValueError("bot_id must be a positive numeric Telegram bot identity.")
    if binding_id is not None:
        normalized_binding_id = _binding_id(binding_id)
    if normalized_bot_id is not None and normalized_binding_id is not None:
        raise ValueError("Specify either bot_id or binding_id when reading approval history.")

    with _owned_transaction(connection):
        with connection.cursor(row_factory=dict_row) as cursor:
            if normalized_binding_id is not None:
                cursor.execute(
                    """
                    SELECT *
                    FROM telegram_destination_bindings
                    WHERE binding_id = %s
                    ORDER BY approved_at, binding_id
                    """,
                    (normalized_binding_id,),
                )
                bindings = _fetchall_dict(cursor)
                cursor.execute(
                    """
                    SELECT *
                    FROM telegram_authorized_senders
                    WHERE binding_id = %s
                    ORDER BY approved_at, sender_approval_id
                    """,
                    (normalized_binding_id,),
                )
            elif normalized_bot_id is not None:
                cursor.execute(
                    """
                    SELECT *
                    FROM telegram_destination_bindings
                    WHERE bot_id = %s
                    ORDER BY approved_at, binding_id
                    """,
                    (normalized_bot_id,),
                )
                bindings = _fetchall_dict(cursor)
                cursor.execute(
                    """
                    SELECT *
                    FROM telegram_authorized_senders
                    WHERE bot_id = %s
                    ORDER BY approved_at, sender_approval_id
                    """,
                    (normalized_bot_id,),
                )
            else:
                cursor.execute(
                    """
                    SELECT *
                    FROM telegram_destination_bindings
                    ORDER BY bot_id, approved_at, binding_id
                    """
                )
                bindings = _fetchall_dict(cursor)
                cursor.execute(
                    """
                    SELECT *
                    FROM telegram_authorized_senders
                    ORDER BY bot_id, approved_at, sender_approval_id
                    """
                )
            return {"destinations": bindings, "senders": _fetchall_dict(cursor)}
