import ast
import json
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest

import notification_identity
import settings_serialization
import settings_persistence


class SessionState(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


class StopRequested(Exception):
    pass


class FakeStreamlit:
    def __init__(self, **state):
        self.session_state = SessionState(state)
        self.messages = []

    def success(self, message):
        self.messages.append(("success", message))

    def info(self, message):
        self.messages.append(("info", message))

    def warning(self, message):
        self.messages.append(("warning", message))

    def error(self, message):
        self.messages.append(("error", message))

    def stop(self):
        raise StopRequested


class SettingsDatabase:
    def __init__(self, fail=False, payload=None, after_select=None):
        self.fail = fail
        self.payload = payload
        self.after_select = after_select
        self.write_count = 0

    def connect(self):
        return SettingsConnection(self)


class SettingsConnection:
    def __init__(self, database):
        self.database = database
        self.pending_payload = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is None and self.pending_payload is not None:
            self.database.payload = self.pending_payload
        return False

    def cursor(self, row_factory=None):
        return SettingsCursor(self)


class SettingsCursor:
    def __init__(self, connection):
        self.connection = connection
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params=()):
        normalized_query = query.lstrip()
        if normalized_query.startswith("SELECT payload FROM app_settings"):
            self.row = {"payload": self.connection.database.payload} if self.connection.database.payload is not None else None
            if self.connection.database.after_select:
                self.connection.database.after_select(self.connection.database)
                self.connection.database.after_select = None
        elif normalized_query.startswith("INSERT INTO app_settings"):
            self.connection.database.write_count += 1
            if self.connection.database.payload is None:
                self.connection.pending_payload = params[0]
                self.row = {"id": 1}
            else:
                self.row = None
        elif normalized_query.startswith("UPDATE app_settings"):
            self.connection.database.write_count += 1
            if self.connection.database.fail:
                self.connection.database.fail = False
                raise psycopg.OperationalError("sensitive connection details")
            payload_text, expected_payload = params
            if self.connection.database.payload == expected_payload:
                self.connection.pending_payload = payload_text
                self.row = {"id": 1}
            else:
                self.row = None

    def fetchone(self):
        return self.row


def _settings_functions():
    source = Path(__file__).resolve().parents[1] / "streamlit_app.py"
    tree = ast.parse(source.read_text())
    wanted = {
        "SettingsSaveResult",
        "load_app_settings",
        "_persist_settings_candidate",
        "_store_pending_settings_draft",
        "backfill_notification_source_identities",
        "save_app_settings",
        "retry_pending_app_settings",
    }
    nodes = [
        node for node in tree.body
        if (
            isinstance(node, ast.ClassDef) and node.name in wanted
        ) or (
            isinstance(node, ast.FunctionDef) and node.name in wanted
        )
    ]
    assert {node.name for node in nodes} == wanted
    return nodes, source


def _app(st, db, enabled=True):
    nodes, source = _settings_functions()
    namespace = {
        "st": st,
        "db_enabled": lambda: enabled,
        "get_connection": db.connect,
        "dict_row": object(),
        "psycopg": psycopg,
        "staging_database": SimpleNamespace(StagingDatabaseError=RuntimeError),
        "json": json,
        "DEFAULT_APP_SETTINGS": {"feature_flags": {}},
        "normalize_feature_flags": lambda flags: dict(flags or {}),
        "dumps_json_safe": settings_serialization.dumps_json_safe,
        "notification_identity": notification_identity,
        "settings_persistence": settings_persistence,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)
    return namespace


def test_database_save_is_dict_compatible_and_commits_cache():
    initial = {"feature_flags": {}, "theme": "old"}
    st = FakeStreamlit(app_settings=initial)
    db = SettingsDatabase(payload=json.dumps(initial))
    app = _app(st, db)
    app["load_app_settings"]()

    result = app["save_app_settings"]({"theme": "new"})

    assert isinstance(result, dict)
    assert result.status == "persisted"
    assert result["theme"] == "new"
    assert json.loads(db.payload)["theme"] == "new"
    assert st.session_state["app_settings"]["theme"] == "new"
    assert st.session_state["app_settings_database_baseline"]["payload"] == db.payload
    assert "pending_app_settings" not in st.session_state
    assert any(kind == "success" and "database" in message for kind, message in st.messages)


def test_database_unavailable_save_is_explicitly_session_only():
    st = FakeStreamlit(app_settings={"theme": "old"})
    app = _app(st, SettingsDatabase(), enabled=False)

    with pytest.raises(StopRequested):
        app["save_app_settings"]({"theme": "temporary"})

    assert st.session_state["app_settings"]["theme"] == "temporary"
    assert "pending_app_settings" not in st.session_state
    assert st.session_state["last_settings_save_result"].status == "session_only"
    assert any(kind == "info" and "only for this session" in message for kind, message in st.messages)
    assert not any(kind == "success" for kind, _ in st.messages)


def test_durable_schedule_confirmation_is_not_enabled_by_session_only_save():
    committed = {
        "feature_flags": {},
        "overview_clinic_schedule_confirmed": False,
    }
    st = FakeStreamlit(app_settings=committed)
    app = _app(st, SettingsDatabase(), enabled=False)

    result = app["save_app_settings"](
        {
            **committed,
            "overview_clinic_schedule_confirmed": True,
            "overview_clinic_weekdays": ["Tuesday"],
        },
        require_durable=True,
    )

    assert result.status == "session_only"
    assert st.session_state["app_settings"] == committed
    assert st.session_state["last_settings_save_result"].status == "session_only"
    assert any("not durably configured" in message for _, message in st.messages)


def test_durable_schedule_confirmation_requires_successful_database_save():
    committed = {
        "feature_flags": {},
        "overview_clinic_schedule_confirmed": False,
        "overview_clinic_weekdays": ["Tuesday", "Thursday"],
        "overview_admin_weekdays": ["Monday", "Friday"],
    }
    st = FakeStreamlit(app_settings=committed)
    db = SettingsDatabase(fail=True, payload=json.dumps(committed))
    app = _app(st, db)
    app["load_app_settings"]()

    result = app["save_app_settings"](
        {
            **committed,
            "overview_clinic_schedule_confirmed": True,
            "overview_clinic_weekdays": ["Tuesday"],
        },
        require_durable=True,
    )

    assert result.status == "failed"
    assert st.session_state["app_settings"] == committed
    assert st.session_state["pending_app_settings"]["settings"]["overview_clinic_schedule_confirmed"] is False
    assert json.loads(db.payload) == committed
    assert not any(kind == "success" for kind, _ in st.messages)


def test_durable_schedule_confirmation_is_enabled_after_database_save():
    committed = {
        "feature_flags": {},
        "overview_clinic_schedule_confirmed": False,
        "overview_clinic_weekdays": ["Tuesday", "Thursday"],
        "overview_admin_weekdays": ["Monday", "Friday"],
    }
    st = FakeStreamlit(app_settings=committed)
    db = SettingsDatabase(payload=json.dumps(committed))
    app = _app(st, db)
    app["load_app_settings"]()

    result = app["save_app_settings"](
        {
            **committed,
            "overview_clinic_schedule_confirmed": True,
            "overview_clinic_weekdays": ["Tuesday"],
        },
        require_durable=True,
    )

    assert result.status == "persisted"
    assert json.loads(db.payload)["overview_clinic_schedule_confirmed"] is True
    assert st.session_state["app_settings"]["overview_clinic_schedule_confirmed"] is True


def test_database_write_failure_preserves_committed_cache_and_pending_edits():
    committed = {"feature_flags": {}, "theme": "saved"}
    st = FakeStreamlit(app_settings=committed)
    db = SettingsDatabase(fail=True, payload=json.dumps(committed))
    app = _app(st, db)
    app["load_app_settings"]()

    with pytest.raises(StopRequested):
        app["save_app_settings"]({"theme": "pending"})

    assert st.session_state["app_settings"] == committed
    assert db.payload == json.dumps(committed)
    assert st.session_state["pending_app_settings"]["settings"]["theme"] == "pending"
    assert st.session_state["pending_app_settings"]["base_payload"] == db.payload
    assert st.session_state["pending_app_settings"]["baseline_known"] is True
    assert st.session_state["last_settings_save_result"].status == "failed"
    assert any(kind == "error" for kind, _ in st.messages)
    assert not any(kind == "success" for kind, _ in st.messages)


def test_notification_identity_backfill_persists_through_settings_cas():
    committed = {
        "feature_flags": {},
        "quick_reminders": [{"text": "Call back"}],
        "family_schedule_items": [{"title": "Practice"}],
    }
    st = FakeStreamlit(app_settings=committed)
    db = SettingsDatabase(payload=json.dumps(committed))
    app = _app(st, db)
    settings = app["load_app_settings"]()

    result = app["backfill_notification_source_identities"](settings)

    assert result.status == "persisted"
    assert result.durable_ready is True
    persisted = json.loads(db.payload)
    assert persisted["quick_reminders"][0]["notification_source_id"]
    assert persisted["family_schedule_items"][0]["notification_source_id"]
    assert st.session_state["app_settings_database_baseline"]["payload"] == db.payload


def test_notification_identity_backfill_cas_conflict_does_not_overwrite_newer_settings():
    committed = {"feature_flags": {}, "quick_reminders": [{"text": "Call back"}]}
    newer = {"feature_flags": {}, "theme": "newer", "quick_reminders": [{"text": "Changed"}]}
    st = FakeStreamlit(app_settings=committed)
    db = SettingsDatabase(
        payload=json.dumps(committed),
        after_select=lambda database: setattr(database, "payload", json.dumps(newer)),
    )
    app = _app(st, db)
    settings = app["load_app_settings"]()

    result = app["backfill_notification_source_identities"](settings)

    assert result.status == "conflict"
    assert result.durable_ready is False
    assert db.payload == json.dumps(newer)
    assert db.write_count == 0
    assert st.session_state["pending_app_settings"]["conflict"] is True
    assert not any(kind == "success" for kind, _ in st.messages)


def test_failed_notification_identity_backfill_is_not_durable_ready():
    committed = {"feature_flags": {}, "quick_reminders": [{"text": "Call back"}]}
    st = FakeStreamlit(app_settings=committed)
    db = SettingsDatabase(fail=True, payload=json.dumps(committed))
    app = _app(st, db)
    settings = app["load_app_settings"]()

    result = app["backfill_notification_source_identities"](settings)

    assert result.status == "failed"
    assert result.durable_ready is False
    assert "not ready for durable notification enqueueing" in result.message
    assert db.payload == json.dumps(committed)
    assert st.session_state["app_settings"] == committed
    assert st.session_state["pending_app_settings"]["settings"]["quick_reminders"][0]["notification_source_id"]


def test_session_only_notification_identity_backfill_is_not_durable_ready():
    committed = {"feature_flags": {}, "quick_reminders": [{"text": "Call back"}]}
    st = FakeStreamlit(app_settings=committed)
    app = _app(st, SettingsDatabase(), enabled=False)
    settings = app["load_app_settings"]()

    result = app["backfill_notification_source_identities"](settings)

    assert result.status == "session_only"
    assert result.durable_ready is False
    assert "not durable" in result.message
    assert "notification_source_id" not in st.session_state["app_settings"]["quick_reminders"][0]
    assert "notification_source_id" in result.settings["quick_reminders"][0]


def test_serialization_failure_preserves_edits_without_exposing_exception():
    st = FakeStreamlit(app_settings={"feature_flags": {}, "theme": "saved"})
    db = SettingsDatabase(payload=json.dumps(st.session_state["app_settings"]))
    app = _app(st, db)
    app["load_app_settings"]()
    attempted = {"theme": "pending", "unsupported": object()}

    with pytest.raises(StopRequested):
        app["save_app_settings"](attempted)

    assert st.session_state["app_settings"]["theme"] == "saved"
    assert st.session_state["pending_app_settings"]["settings"]["theme"] == "pending"
    assert "object at" not in st.session_state["last_settings_save_result"].message
    assert not any(kind == "success" for kind, _ in st.messages)


def test_loading_newer_durable_settings_does_not_overlay_pending_draft():
    committed = {"feature_flags": {}, "theme": "saved"}
    newer_durable = {"feature_flags": {}, "theme": "changed elsewhere"}
    st = FakeStreamlit(
        app_settings=committed,
        pending_app_settings={
            "settings": {"feature_flags": {}, "theme": "pending"},
            "base_payload": json.dumps(committed),
            "baseline_known": True,
            "conflict": False,
        },
    )
    db = SettingsDatabase(payload=json.dumps(newer_durable))
    app = _app(st, db)

    loaded = app["load_app_settings"]()

    assert loaded["theme"] == "changed elsewhere"
    assert st.session_state["pending_app_settings"]["settings"]["theme"] == "pending"
    assert st.session_state["app_settings_database_baseline"]["payload"] == db.payload


def test_safe_retry_persists_only_when_durable_baseline_is_unchanged():
    committed = {"feature_flags": {}, "theme": "saved"}
    pending = {
        "settings": {"feature_flags": {}, "theme": "pending"},
        "base_payload": json.dumps(committed),
        "baseline_known": True,
        "conflict": False,
    }
    st = FakeStreamlit(
        app_settings=committed,
        pending_app_settings=pending,
        app_settings_database_baseline={"known": True, "payload": pending["base_payload"]},
    )
    db = SettingsDatabase(fail=True, payload=pending["base_payload"])
    app = _app(st, db)

    with pytest.raises(StopRequested):
        app["retry_pending_app_settings"]()
    assert db.payload == pending["base_payload"]
    assert st.session_state["pending_app_settings"]["settings"]["theme"] == "pending"

    result = app["retry_pending_app_settings"]()

    assert result.status == "persisted"
    assert json.loads(db.payload)["theme"] == "pending"
    assert st.session_state["app_settings"]["theme"] == "pending"
    assert "pending_app_settings" not in st.session_state


def test_conflicting_retry_does_not_write_and_preserves_pending_draft():
    baseline = {"feature_flags": {}, "theme": "saved"}
    newer_durable = {"feature_flags": {}, "theme": "changed elsewhere"}
    pending = {
        "settings": {"feature_flags": {}, "theme": "pending"},
        "base_payload": json.dumps(baseline),
        "baseline_known": True,
        "conflict": False,
    }
    st = FakeStreamlit(
        app_settings=baseline,
        pending_app_settings=pending,
        app_settings_database_baseline={"known": True, "payload": pending["base_payload"]},
    )
    db = SettingsDatabase(payload=json.dumps(newer_durable))
    app = _app(st, db)

    with pytest.raises(StopRequested):
        app["retry_pending_app_settings"]()

    assert db.write_count == 0
    assert db.payload == json.dumps(newer_durable)
    assert st.session_state["pending_app_settings"]["settings"]["theme"] == "pending"
    assert st.session_state["pending_app_settings"]["conflict"] is True
    assert st.session_state["last_settings_save_result"].status == "conflict"
    assert not any(kind == "success" for kind, _ in st.messages)


def test_conditional_update_rejects_race_after_baseline_check():
    baseline = {"feature_flags": {}, "theme": "saved"}
    concurrent = {"feature_flags": {}, "theme": "concurrent"}
    pending = {
        "settings": {"feature_flags": {}, "theme": "pending"},
        "base_payload": json.dumps(baseline),
        "baseline_known": True,
        "conflict": False,
    }
    st = FakeStreamlit(pending_app_settings=pending)
    db = SettingsDatabase(
        payload=json.dumps(baseline),
        after_select=lambda database: setattr(database, "payload", json.dumps(concurrent)),
    )
    app = _app(st, db)

    with pytest.raises(StopRequested):
        app["retry_pending_app_settings"]()

    assert db.payload == json.dumps(concurrent)
    assert st.session_state["pending_app_settings"]["settings"]["theme"] == "pending"
    assert st.session_state["pending_app_settings"]["conflict"] is True


def test_successful_retry_clears_only_the_draft_it_applied():
    baseline = {"feature_flags": {}, "theme": "saved"}
    original_draft = {
        "settings": {"feature_flags": {}, "theme": "original draft"},
        "base_payload": json.dumps(baseline),
        "baseline_known": True,
        "conflict": False,
    }
    newer_draft = {
        "settings": {"feature_flags": {}, "theme": "newer draft"},
        "base_payload": json.dumps(baseline),
        "baseline_known": True,
        "conflict": False,
    }
    st = FakeStreamlit(pending_app_settings=newer_draft)
    db = SettingsDatabase(payload=json.dumps(baseline))
    app = _app(st, db)

    result = app["_persist_settings_candidate"](
        original_draft["settings"],
        original_draft["base_payload"],
        True,
        pending_draft=original_draft,
    )

    assert result.status == "persisted"
    assert st.session_state["pending_app_settings"] is newer_draft


def test_retry_without_a_verified_baseline_does_not_write():
    draft = {
        "settings": {"feature_flags": {}, "theme": "pending"},
        "base_payload": None,
        "baseline_known": False,
        "conflict": False,
    }
    st = FakeStreamlit(pending_app_settings=draft)
    db = SettingsDatabase()
    app = _app(st, db)

    with pytest.raises(StopRequested):
        app["retry_pending_app_settings"]()

    assert db.write_count == 0
    assert st.session_state["pending_app_settings"]["settings"]["theme"] == "pending"
    assert st.session_state["pending_app_settings"]["conflict"] is True
    assert st.session_state["last_settings_save_result"].status == "conflict"


@pytest.mark.parametrize(
    "changed",
    [
        {"default_duration": 45},
        {"quick_reminders": [{"text": "Call back", "status": "active"}]},
        {"calendar_weekday_assignments": {"Monday": ["Office"]}},
        {"workout_logs": [{"date": "2026-11-16", "exercises": []}]},
    ],
    ids=["settings-panel", "reminders", "calendar", "workout"],
)
def test_representative_settings_callers_share_the_explicit_save_contract(changed):
    st = FakeStreamlit(app_settings={"feature_flags": {}, "baseline": "kept"})
    db = SettingsDatabase(fail=True, payload=json.dumps(st.session_state["app_settings"]))
    app = _app(st, db)
    app["load_app_settings"]()

    with pytest.raises(StopRequested):
        app["save_app_settings"]({**st.session_state["app_settings"], **changed})

    assert st.session_state["app_settings"] == {"feature_flags": {}, "baseline": "kept"}
    assert st.session_state["pending_app_settings"]["settings"]["baseline"] == "kept"
    for key, value in changed.items():
        assert st.session_state["pending_app_settings"]["settings"][key] == value
    assert not any(kind == "success" for kind, _ in st.messages)
