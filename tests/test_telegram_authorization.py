from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime, timezone
from threading import Event, RLock
from types import SimpleNamespace

import psycopg
from psycopg.pq import TransactionStatus
import pytest

import notification_operational_state
import telegram_authorization as authorization
import telegram_identity
import telegram_inbox


NOW = datetime(2026, 11, 22, 23, 0, tzinfo=timezone.utc)
_DEFAULT_AUTHORITY = object()


class FakeDatabase:
    def __init__(self):
        self.bindings = {}
        self.senders = {}
        self.lock = RLock()
        self.missing_schema = False
        self.fail_query = None
        self.fail_message = "simulated database failure"
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
            raise AssertionError("Owned transaction requires an idle connection.")
        self.database.lock.acquire()
        self.snapshot = (
            deepcopy(self.database.bindings),
            deepcopy(self.database.senders),
        )
        self.connection.info.transaction_status = TransactionStatus.INTRANS
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is not None:
            self.database.bindings, self.database.senders = self.snapshot
        self.connection.info.transaction_status = TransactionStatus.IDLE
        self.database.lock.release()
        return False


class FakeCursor:
    def __init__(self, database, connection):
        self.database = database
        self.connection = connection
        self.rows = []

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
            raise AssertionError("Authorization SQL requires a transaction.")
        normalized = " ".join(query.split())
        self.rows = []
        if self.database.missing_schema:
            raise psycopg.errors.UndefinedTable("authorization schema missing")
        if self.database.fail_query and self.database.fail_query in normalized:
            raise psycopg.OperationalError(self.database.fail_message)

        if normalized.startswith("SELECT * FROM telegram_destination_bindings"):
            self._select_bindings(normalized, params)
        elif normalized.startswith("INSERT INTO telegram_destination_bindings"):
            self._insert_binding(params)
        elif normalized.startswith("UPDATE telegram_destination_bindings"):
            self._revoke_binding(params)
        elif normalized.startswith("SELECT * FROM telegram_authorized_senders"):
            self._select_senders(normalized, params)
        elif normalized.startswith("SELECT sender.* FROM telegram_authorized_senders"):
            self._select_active_senders(params[0])
        elif normalized.startswith("INSERT INTO telegram_authorized_senders"):
            self._insert_sender(params)
        elif normalized.startswith("UPDATE telegram_authorized_senders"):
            self._revoke_senders(normalized, params)
        elif normalized.startswith("SELECT binding.binding_id"):
            self._authorize(params)
        else:
            raise AssertionError(f"Unrecognized authorization SQL: {normalized}")

    def _select_bindings(self, query, params):
        rows = list(self.database.bindings.values())
        if "WHERE bot_id = %s AND approval_status = 'approved'" in query:
            rows = [
                row for row in rows
                if row["bot_id"] == params[0] and row["approval_status"] == "approved"
            ]
        elif "WHERE binding_id = %s" in query:
            rows = [row for row in rows if row["binding_id"] == params[0]]
        elif "WHERE bot_id = %s" in query:
            rows = [row for row in rows if row["bot_id"] == params[0]]
        elif "WHERE approval_status = 'approved'" in query:
            rows = [row for row in rows if row["approval_status"] == "approved"]
        rows.sort(key=lambda row: (row["bot_id"], row["destination_id"]))
        self.rows = [deepcopy(row) for row in rows]

    def _insert_binding(self, params):
        binding_id, bot_id, destination_id, verified_at, identity_provenance, actor, provenance = params
        if any(
            row["bot_id"] == bot_id and row["approval_status"] == "approved"
            for row in self.database.bindings.values()
        ):
            return
        row = {
            "binding_id": binding_id,
            "bot_id": bot_id,
            "destination_type": "telegram",
            "destination_id": destination_id,
            "approval_status": "approved",
            "bot_identity_verified_at": verified_at,
            "bot_identity_provenance": identity_provenance,
            "approved_at": NOW,
            "approver_actor_id": actor,
            "approval_provenance": provenance,
            "revoked_at": None,
            "revoked_by_actor_id": None,
            "revocation_provenance": None,
            "created_at": NOW,
            "updated_at": NOW,
        }
        self.database.bindings[binding_id] = row
        self.rows = [deepcopy(row)]

    def _revoke_binding(self, params):
        actor, provenance, binding_id = params
        row = self.database.bindings.get(binding_id)
        if row and row["approval_status"] == "approved":
            row.update(
                approval_status="revoked",
                revoked_at=NOW,
                revoked_by_actor_id=actor,
                revocation_provenance=provenance,
                updated_at=NOW,
            )
            self.rows = [deepcopy(row)]

    def _select_senders(self, query, params):
        rows = list(self.database.senders.values())
        if "sender_approval_id = %s" in query:
            rows = [row for row in rows if row["sender_approval_id"] == params[0]]
        elif "sender_user_id = %s" in query:
            binding_id, sender_id = params
            rows = [
                row for row in rows
                if row["binding_id"] == binding_id
                and row["sender_user_id"] == sender_id
                and row["approval_status"] == "approved"
            ]
        elif "binding_id = %s" in query:
            rows = [row for row in rows if row["binding_id"] == params[0]]
        elif "bot_id = %s" in query:
            rows = [row for row in rows if row["bot_id"] == params[0]]
        rows.sort(key=lambda row: row["sender_user_id"])
        self.rows = [deepcopy(row) for row in rows]

    def _select_active_senders(self, binding_id):
        parent = self.database.bindings.get(binding_id)
        rows = [
            row for row in self.database.senders.values()
            if row["binding_id"] == binding_id
            and row["approval_status"] == "approved"
            and parent is not None
            and parent["approval_status"] == "approved"
        ]
        self.rows = [deepcopy(row) for row in rows]

    def _insert_sender(self, params):
        sender_approval_id, binding_id, bot_id, sender_id, actor, provenance = params
        if any(
            row["binding_id"] == binding_id
            and row["sender_user_id"] == sender_id
            and row["approval_status"] == "approved"
            for row in self.database.senders.values()
        ):
            return
        row = {
            "sender_approval_id": sender_approval_id,
            "binding_id": binding_id,
            "bot_id": bot_id,
            "sender_user_id": sender_id,
            "approval_status": "approved",
            "approved_at": NOW,
            "approver_actor_id": actor,
            "approval_provenance": provenance,
            "revoked_at": None,
            "revoked_by_actor_id": None,
            "revocation_provenance": None,
            "created_at": NOW,
            "updated_at": NOW,
        }
        self.database.senders[sender_approval_id] = row
        self.rows = [deepcopy(row)]

    def _revoke_senders(self, query, params):
        if "WHERE binding_id = %s AND approval_status = 'approved'" in query:
            actor, provenance, binding_id = params
            rows = [
                row for row in self.database.senders.values()
                if row["binding_id"] == binding_id and row["approval_status"] == "approved"
            ]
        else:
            actor, provenance, sender_approval_id = params
            rows = [
                row for row in self.database.senders.values()
                if row["sender_approval_id"] == sender_approval_id
                and row["approval_status"] == "approved"
            ]
        for row in rows:
            row.update(
                approval_status="revoked",
                revoked_at=NOW,
                revoked_by_actor_id=actor,
                revocation_provenance=provenance,
                updated_at=NOW,
            )
        if "RETURNING *" in query:
            self.rows = [deepcopy(row) for row in rows]

    def _authorize(self, params):
        bot_id, destination_id, sender_id = params
        binding = next(
            (
                row for row in self.database.bindings.values()
                if row["bot_id"] == bot_id
                and row["destination_type"] == "telegram"
                and row["destination_id"] == destination_id
                and row["approval_status"] == "approved"
            ),
            None,
        )
        sender = next(
            (
                row for row in self.database.senders.values()
                if binding is not None
                and row["binding_id"] == binding["binding_id"]
                and row["bot_id"] == bot_id
                and row["sender_user_id"] == sender_id
                and row["approval_status"] == "approved"
            ),
            None,
        )
        self.rows = [{"binding_id": binding["binding_id"]}] if binding and sender else []

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        rows, self.rows = self.rows, []
        return rows


class FakeOwnerAuthority(authorization.OwnerAuthorizationAuthority):
    def __init__(self, *, allow=True):
        self.allow = allow
        self.actions = []

    def authorize_telegram_change(self, action, resource):
        self.actions.append((action, dict(resource)))
        if not self.allow:
            return None
        return authorization.OwnerAuthorizationGrant(
            actor_identity="owner:unit-test",
            provenance="authenticated_owner_ui",
        )


def _identity(bot_id=123456):
    return telegram_identity.verify_bot_identity(
        "nonpersisted-test-token",
        lambda _token: {"ok": True, "result": {"id": bot_id, "is_bot": True}},
        now=NOW,
    )


def _binding(
    database,
    *,
    bot_id=123456,
    destination_id="555",
    authority=_DEFAULT_AUTHORITY,
):
    if authority is _DEFAULT_AUTHORITY:
        authority = FakeOwnerAuthority()
    return authorization.approve_destination_binding(
        database.connect(),
        identity=_identity(bot_id),
        destination_id=destination_id,
        authority=authority,
        now=NOW,
    )


def _sender(database, binding, sender_id=777, authority=_DEFAULT_AUTHORITY):
    if authority is _DEFAULT_AUTHORITY:
        authority = FakeOwnerAuthority()
    return authorization.approve_sender(
        database.connect(),
        binding_id=binding["binding_id"],
        sender_user_id=sender_id,
        authority=authority,
    )


def test_approved_destination_and_unauthorized_destination_lookup():
    database = FakeDatabase()
    binding = _binding(database)

    assert authorization.read_approved_destination_bindings(database.connect(), bot_id=123456) == [binding]
    connection = database.connect()
    with connection.transaction():
        denied = authorization.authorize_destination_sender(
            connection, bot_id=123456, destination_id="556", sender_user_id=777
        )
    assert not denied.allowed
    assert denied.reason_code == "not_authorized"


def test_approved_and_unauthorized_sender_lookup():
    database = FakeDatabase()
    binding = _binding(database)
    sender = _sender(database, binding)

    assert authorization.read_authorized_senders(
        database.connect(), binding_id=binding["binding_id"]
    ) == [sender]
    connection = database.connect()
    with connection.transaction():
        allowed = authorization.authorize_destination_sender(
            connection, bot_id=123456, destination_id="555", sender_user_id=777
        )
    assert allowed.allowed
    assert allowed.binding_id == binding["binding_id"]

    with connection.transaction():
        denied = authorization.authorize_destination_sender(
            connection, bot_id=123456, destination_id="555", sender_user_id=888
        )
    assert not denied.allowed


def test_missing_sender_identity_fails_closed():
    database = FakeDatabase()
    _binding(database)
    connection = database.connect()
    with connection.transaction():
        result = authorization.authorize_destination_sender(
            connection, bot_id=123456, destination_id="555", sender_user_id=None
        )
    assert result.reason_code == "identity_incomplete"


def test_revoking_destination_removes_effective_sender_authorization():
    database = FakeDatabase()
    binding = _binding(database)
    sender = _sender(database, binding)
    revoked = authorization.revoke_destination_binding(
        database.connect(),
        binding_id=binding["binding_id"],
        authority=FakeOwnerAuthority(),
    )

    assert revoked["approval_status"] == "revoked"
    assert database.senders[sender["sender_approval_id"]]["approval_status"] == "revoked"
    assert authorization.read_approved_destination_bindings(database.connect()) == []
    assert authorization.read_authorized_senders(
        database.connect(), binding_id=binding["binding_id"]
    ) == []


def test_revoked_sender_is_not_authorized_and_history_is_retained():
    database = FakeDatabase()
    binding = _binding(database)
    sender = _sender(database, binding)
    revoked = authorization.revoke_sender(
        database.connect(),
        binding_id=binding["binding_id"],
        sender_user_id=777,
        authority=FakeOwnerAuthority(),
    )
    connection = database.connect()
    with connection.transaction():
        result = authorization.authorize_destination_sender(
            connection, bot_id=123456, destination_id="555", sender_user_id=777
        )
    history = authorization.get_approval_history(
        database.connect(), binding_id=binding["binding_id"]
    )

    assert revoked["approval_status"] == "revoked"
    assert history["senders"][0]["sender_approval_id"] == sender["sender_approval_id"]
    assert not result.allowed


def test_duplicate_approval_is_idempotent_and_conflicting_destination_rejected():
    database = FakeDatabase()
    first = _binding(database)
    duplicate = _binding(database)

    assert first["binding_id"] == duplicate["binding_id"]
    with pytest.raises(authorization.AuthorizationConflict, match="different active"):
        _binding(database, destination_id="556")
    assert len(database.bindings) == 1


def test_duplicate_sender_approval_is_idempotent():
    database = FakeDatabase()
    binding = _binding(database)

    first = _sender(database, binding)
    repeated = _sender(database, binding)

    assert first["sender_approval_id"] == repeated["sender_approval_id"]
    assert len(database.senders) == 1


def test_concurrent_duplicate_destination_approvals_are_idempotent():
    database = FakeDatabase()
    with ThreadPoolExecutor(max_workers=2) as executor:
        rows = list(executor.map(lambda _: _binding(database), range(2)))

    assert rows[0]["binding_id"] == rows[1]["binding_id"]
    assert len(database.bindings) == 1


def test_authorization_transaction_serializes_concurrent_revocation():
    database = FakeDatabase()
    binding = _binding(database)
    _sender(database, binding)
    authorization_checked = Event()
    allow_action_commit = Event()
    revocation_started = Event()

    def check_and_hold_transaction():
        connection = database.connect()
        with connection.transaction():
            decision = authorization.authorize_destination_sender(
                connection,
                bot_id=123456,
                destination_id="555",
                sender_user_id=777,
            )
            authorization_checked.set()
            if not allow_action_commit.wait(timeout=2):
                raise TimeoutError("test action transaction did not release")
        return decision

    def revoke():
        revocation_started.set()
        return authorization.revoke_destination_binding(
            database.connect(),
            binding_id=binding["binding_id"],
            authority=FakeOwnerAuthority(),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        action = executor.submit(check_and_hold_transaction)
        assert authorization_checked.wait(timeout=1)
        revocation = executor.submit(revoke)
        assert revocation_started.wait(timeout=1)
        with pytest.raises(TimeoutError):
            revocation.result(timeout=0.05)
        allow_action_commit.set()
        assert action.result(timeout=1).allowed
        assert revocation.result(timeout=1)["approval_status"] == "revoked"


def test_bot_replacement_does_not_inherit_bindings_or_senders():
    database = FakeDatabase()
    original = _binding(database, bot_id=123456)
    _sender(database, original)
    replacement = _binding(database, bot_id=654321)

    assert replacement["binding_id"] != original["binding_id"]
    assert replacement["bot_id"] == "654321"
    assert authorization.read_authorized_senders(
        database.connect(), binding_id=replacement["binding_id"]
    ) == []


def test_destination_replacement_requires_revocation_and_new_approval():
    database = FakeDatabase()
    original = _binding(database)
    authorization.revoke_destination_binding(
        database.connect(),
        binding_id=original["binding_id"],
        authority=FakeOwnerAuthority(),
    )
    replacement = _binding(database, destination_id="-100555")

    assert replacement["binding_id"] != original["binding_id"]
    assert replacement["destination_id"] == "-100555"
    history = authorization.get_approval_history(database.connect(), bot_id=123456)
    assert [row["approval_status"] for row in history["destinations"]] == ["approved", "revoked"]


def test_missing_or_untrusted_owner_authority_fails_closed():
    database = FakeDatabase()
    with pytest.raises(authorization.OwnerAuthorizationDenied, match="provider"):
        _binding(database, authority=None)
    with pytest.raises(authorization.OwnerAuthorizationDenied, match="did not authorize"):
        _binding(database, authority=FakeOwnerAuthority(allow=False))
    with pytest.raises(authorization.OwnerAuthorizationDenied, match="provider"):
        _binding(database, authority=object())
    assert database.bindings == {}


def test_caller_supplied_approval_flag_is_not_an_authorization_context():
    database = FakeDatabase()

    with pytest.raises(TypeError):
        authorization.approve_destination_binding(
            database.connect(),
            identity=_identity(),
            destination_id="555",
            authority=FakeOwnerAuthority(),
            approved=True,
            now=NOW,
        )

    assert database.bindings == {}


def test_unverified_or_stale_bot_identity_cannot_be_approved():
    database = FakeDatabase()
    with pytest.raises(authorization.OwnerAuthorizationDenied, match="fresh authenticated"):
        authorization.approve_destination_binding(
            database.connect(),
            identity=None,
            destination_id="555",
            authority=FakeOwnerAuthority(),
            now=NOW,
        )
    stale = telegram_identity.verify_bot_identity(
        "token",
        lambda _token: {"ok": True, "result": {"id": 123456, "is_bot": True}},
        now=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    with pytest.raises(authorization.OwnerAuthorizationDenied):
        authorization.approve_destination_binding(
            database.connect(),
            identity=stale,
            destination_id="555",
            authority=FakeOwnerAuthority(),
            now=NOW,
        )
    assert database.bindings == {}


def test_invalid_provenance_or_actor_from_authority_is_rejected():
    class InvalidAuthority(FakeOwnerAuthority):
        def authorize_telegram_change(self, action, resource):
            return authorization.OwnerAuthorizationGrant(
                actor_identity=" ",
                provenance="untrusted",
            )

    with pytest.raises(ValueError):
        _binding(FakeDatabase(), authority=InvalidAuthority())


def test_authority_exception_is_replaced_with_safe_bounded_error():
    token_like_value = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk"

    class BrokenAuthority(FakeOwnerAuthority):
        def authorize_telegram_change(self, action, resource):
            raise RuntimeError(f"authorization service leaked {token_like_value}")

    with pytest.raises(authorization.OwnerAuthorizationDenied) as raised:
        _binding(FakeDatabase(), authority=BrokenAuthority())

    assert token_like_value not in str(raised.value)
    assert "authorization service leaked" not in str(raised.value)


def test_database_failure_and_missing_schema_do_not_authorize():
    database = FakeDatabase()
    database.missing_schema = True
    with pytest.raises(authorization.AuthorizationStorageError) as missing_schema:
        _binding(database)
    assert "authorization schema missing" not in str(missing_schema.value)

    database.missing_schema = False
    database.fail_query = "INSERT INTO telegram_destination_bindings"
    database.fail_message = "secret-token-like database diagnostic"
    with pytest.raises(authorization.AuthorizationStorageError) as database_failure:
        _binding(database)
    assert str(database_failure.value) == "Telegram authorization storage is unavailable."
    assert "secret-token-like" not in str(database_failure.value)
    assert database.bindings == {}


def test_transaction_rollback_removes_approval_and_history():
    database = FakeDatabase()
    binding = _binding(database)
    sender = _sender(database, binding)
    database.fail_query = "UPDATE telegram_authorized_senders"
    with pytest.raises(
        authorization.AuthorizationStorageError,
        match="Telegram authorization storage is unavailable",
    ):
        authorization.revoke_destination_binding(
            database.connect(),
            binding_id=binding["binding_id"],
            authority=FakeOwnerAuthority(),
        )

    assert database.bindings[binding["binding_id"]]["approval_status"] == "approved"
    assert database.senders[sender["sender_approval_id"]]["approval_status"] == "approved"


def test_owned_operations_reject_active_transaction_and_authorize_requires_one():
    database = FakeDatabase()
    connection = database.connect()
    connection.info.transaction_status = TransactionStatus.INTRANS
    with pytest.raises(authorization.AuthorizationConnectionError, match="idle connection"):
        authorization.read_approved_destination_bindings(connection)
    connection.info.transaction_status = TransactionStatus.IDLE
    with pytest.raises(authorization.AuthorizationConnectionError, match="caller-owned"):
        authorization.authorize_destination_sender(
            connection, bot_id=123456, destination_id="555", sender_user_id=777
        )


def test_approval_storage_does_not_persist_tokens_or_call_telegram():
    database = FakeDatabase()
    authority = FakeOwnerAuthority()
    token = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk"
    result = authorization.approve_destination_binding(
        database.connect(),
        identity=_identity(),
        destination_id="555",
        authority=authority,
        now=NOW,
    )
    serialized = repr(database.bindings) + repr(result) + repr(authority.actions)

    assert token not in serialized
    assert database.telegram_calls == 0
    assert "token" not in repr(result).lower()


def test_repository_has_no_active_inbox_or_cursor_provisioning_side_effects(monkeypatch):
    database = FakeDatabase()
    binding = _binding(database)
    _sender(database, binding)
    provisioning_calls = []
    inbox_calls = []
    monkeypatch.setattr(
        notification_operational_state,
        "initialize_verified_bot_cursor",
        lambda *_args, **_kwargs: provisioning_calls.append(True),
    )
    monkeypatch.setattr(
        telegram_inbox,
        "process_updates",
        lambda *_args, **_kwargs: inbox_calls.append(True),
    )

    connection = database.connect()
    with connection.transaction():
        decision = authorization.authorize_destination_sender(
            connection, bot_id=123456, destination_id="555", sender_user_id=777
        )

    assert decision.allowed
    assert provisioning_calls == []
    assert inbox_calls == []
