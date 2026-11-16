import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, datetime, time, timedelta
from threading import RLock

import psycopg
import pytest

import data_access


TODAY = date(2026, 11, 16)


class SessionState(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


class FakeStreamlit:
    def __init__(self, settings=None, event=None):
        self.session_state = SessionState(
            tasks=[],
            app_settings=deepcopy(settings or {}),
            surgical_cases=[{"id": 4, "procedure_name": "Manual only"}],
        )
        if event is not None:
            self.session_state["ai_events"] = [deepcopy(event)]


def _entry(dump_id, status="new", **values):
    return {
        "dump_id": dump_id,
        "title": f"Entry {dump_id}",
        "raw_text": f"Do {dump_id}",
        "item_type": "task",
        "category": "Personal",
        "priority": "medium",
        "status": status,
        **values,
    }


def _decision(dump_id, item_type="task", **values):
    return {
        "dump_id": dump_id,
        "item_type": item_type,
        "title": f"Task {dump_id}",
        "description": f"Description {dump_id}",
        "category": "Personal",
        "priority": "high",
        "due_date": TODAY,
        "scheduled_date": None,
        "scheduled_time": None,
        **values,
    }


def _event(event_id, dump_ids, status="proposed"):
    return {
        "id": event_id,
        "pillar": "capture",
        "kind": "decision",
        "status": status,
        "payload_json": {
            "items": [
                {"type": "triage", "data": {"dump_id": dump_id}}
                for dump_id in dump_ids
            ]
        },
        "expires_at": datetime.now(data_access.MOUNTAIN_TIMEZONE) + timedelta(days=1),
        "task_ids": [],
    }


def _settings(*entries, **values):
    return {"brain_dump_entries": [deepcopy(entry) for entry in entries], **values}


class TransactionalDatabase:
    def __init__(self, settings_payload, event=None, fail=None):
        self.state = {
            "settings_payload": settings_payload,
            "tasks": [],
            "next_task_id": 41,
            "event": deepcopy(event),
        }
        self.fail = fail
        self.lock = RLock()
        self.insert_attempts = 0

    def connect(self):
        return TransactionalConnection(self)


class TransactionalConnection:
    def __init__(self, database):
        self.database = database
        self.snapshot = None
        self.locked = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            if exc_type is not None and self.snapshot is not None:
                self.database.state.clear()
                self.database.state.update(self.snapshot)
        finally:
            if self.locked:
                self.database.lock.release()
        return False

    def cursor(self, row_factory=None):
        return TransactionalCursor(self)


class TransactionalCursor:
    def __init__(self, connection):
        self.connection = connection
        self.database = connection.database
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params=()):
        query = " ".join(query.split())
        state = self.database.state
        if query.startswith("SELECT payload FROM app_settings"):
            self.database.lock.acquire()
            self.connection.locked = True
            self.connection.snapshot = deepcopy(state)
            payload = state["settings_payload"]
            self.row = {"payload": payload} if payload is not None else None
        elif query.startswith("SELECT id, pillar, kind, status, payload_json"):
            event = state["event"]
            self.row = deepcopy(event) if event and event["id"] == params[0] else None
        elif query.startswith("INSERT INTO tasks"):
            self.database.insert_attempts += 1
            if self.database.fail == f"task_insert_{self.database.insert_attempts}":
                raise psycopg.OperationalError("task insert failed")
            task_id = state["next_task_id"]
            state["next_task_id"] += 1
            state["tasks"].append({"id": task_id, "title": params[0]})
            self.row = {"id": task_id}
        elif query.startswith("UPDATE app_settings"):
            if self.database.fail == "settings_update":
                raise psycopg.OperationalError("settings update failed")
            payload, expected_payload = params
            if state["settings_payload"] == expected_payload:
                state["settings_payload"] = payload
                self.row = {"id": 1}
            else:
                self.row = None
        elif query.startswith("UPDATE ai_events"):
            if self.database.fail == "event_resolution":
                raise psycopg.OperationalError("event resolution failed")
            event = state["event"]
            task_ids, note, event_id = params
            if event and event["id"] == event_id and event["status"] == "proposed":
                event.update(
                    status="applied",
                    outcome="accepted",
                    task_ids=list(task_ids),
                    outcome_note=note,
                    resolved_at=datetime.now(data_access.MOUNTAIN_TIMEZONE),
                )
                self.row = {"id": event_id}
            else:
                self.row = None

    def fetchone(self):
        return self.row


def _run_session(st, decisions, event_id=None, status_prefix=""):
    return data_access.convert_brain_dump_entries(
        decisions,
        TODAY,
        session_settings=st.session_state.app_settings,
        event_id=event_id,
        status_prefix=status_prefix,
        db_enabled_fn=lambda: False,
        st_module=st,
    )


def _run_database(database, st, decisions, event_id=None, status_prefix=""):
    return data_access.convert_brain_dump_entries(
        decisions,
        TODAY,
        event_id=event_id,
        status_prefix=status_prefix,
        db_enabled_fn=lambda: True,
        get_connection_fn=database.connect,
        st_module=st,
    )


def test_manual_database_conversion_is_atomic_and_preserves_unrelated_settings():
    settings = _settings(_entry("dump-a"), theme="dark", quick_reminders=[])
    st = FakeStreamlit(settings)
    database = TransactionalDatabase(json.dumps(settings))

    result = _run_database(database, st, [_decision("dump-a")])

    saved = json.loads(database.state["settings_payload"])
    assert result.status == "created"
    assert result.created_task_ids == [41]
    assert database.state["tasks"] == [{"id": 41, "title": "Task dump-a"}]
    assert saved["theme"] == "dark"
    assert saved["brain_dump_entries"][0]["status"] == "converted_task"
    assert saved["brain_dump_entries"][0]["task_id"] == 41


def test_ai_assisted_conversion_resolves_the_bound_proposal_in_the_transaction():
    settings = _settings(_entry("dump-ai"), theme="kept")
    event = _event(7, ["dump-ai"])
    st = FakeStreamlit(settings, event)
    database = TransactionalDatabase(json.dumps(settings), event)

    result = _run_database(database, st, [_decision("dump-ai")], event_id=7, status_prefix="ai_")

    saved = json.loads(database.state["settings_payload"])
    assert result.status == "created"
    assert saved["brain_dump_entries"][0]["status"] == "ai_converted_task"
    assert saved["brain_dump_entries"][0]["task_id"] == 41
    assert database.state["event"]["status"] == "applied"
    assert database.state["event"]["task_ids"] == [41]


def test_repeated_conversion_returns_the_recorded_task_id_without_inserting():
    settings = _settings(_entry("dump-a", "converted_task", task_id=41))
    st = FakeStreamlit(settings)
    database = TransactionalDatabase(json.dumps(settings))
    database.state["tasks"] = [{"id": 41, "title": "Task dump-a"}]
    database.state["next_task_id"] = 42

    result = _run_database(database, st, [_decision("dump-a")])

    assert result.status == "already_applied"
    assert result.task_ids == [41]
    assert database.state["tasks"] == [{"id": 41, "title": "Task dump-a"}]


def test_concurrent_database_conversions_create_exactly_one_task():
    settings = _settings(_entry("dump-a"))
    st = FakeStreamlit(settings)
    database = TransactionalDatabase(json.dumps(settings))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(_run_database, database, st, [_decision("dump-a")])
            for _ in range(2)
        ]
        results = [future.result(timeout=5) for future in futures]

    assert sorted(result.status for result in results) == ["already_applied", "created"]
    assert len(database.state["tasks"]) == 1
    assert json.loads(database.state["settings_payload"])["brain_dump_entries"][0]["task_id"] == 41


def test_batch_conversion_commits_all_selected_entries():
    settings = _settings(_entry("dump-a"), _entry("dump-b"))
    st = FakeStreamlit(settings)
    database = TransactionalDatabase(json.dumps(settings))

    result = _run_database(database, st, [_decision("dump-a"), _decision("dump-b")])

    saved = json.loads(database.state["settings_payload"])
    assert result.created_task_ids == [41, 42]
    assert [entry["task_id"] for entry in saved["brain_dump_entries"]] == [41, 42]
    assert [task["id"] for task in database.state["tasks"]] == [41, 42]


def test_batch_insert_failure_rolls_back_earlier_tasks_and_entry_updates():
    settings = _settings(_entry("dump-a"), _entry("dump-b"))
    st = FakeStreamlit(settings)
    original_payload = json.dumps(settings)
    database = TransactionalDatabase(original_payload, fail="task_insert_2")

    result = _run_database(database, st, [_decision("dump-a"), _decision("dump-b")])

    assert result.status == "failed"
    assert database.state["tasks"] == []
    assert database.state["settings_payload"] == original_payload
    assert database.state["next_task_id"] == 41


@pytest.mark.parametrize("failure", ["task_insert_1", "settings_update", "event_resolution"])
def test_database_failures_roll_back_tasks_settings_and_proposal(failure):
    settings = _settings(_entry("dump-ai"), theme="original")
    event = _event(9, ["dump-ai"])
    st = FakeStreamlit(settings, event)
    original_payload = json.dumps(settings)
    database = TransactionalDatabase(original_payload, event, fail=failure)

    result = _run_database(
        database,
        st,
        [_decision("dump-ai")],
        event_id=9,
        status_prefix="ai_",
    )

    assert result.status == "failed"
    assert database.state["tasks"] == []
    assert database.state["settings_payload"] == original_payload
    assert database.state["event"]["status"] == "proposed"


def test_retry_after_commit_uncertainty_returns_existing_task_and_proposal():
    settings = _settings(_entry("dump-ai"))
    event = _event(10, ["dump-ai"])
    st = FakeStreamlit(settings, event)
    database = TransactionalDatabase(json.dumps(settings), event)

    first = _run_database(database, st, [_decision("dump-ai")], 10, "ai_")
    second = _run_database(database, st, [_decision("dump-ai")], 10, "ai_")

    assert first.created_task_ids == [41]
    assert second.status == "already_applied"
    assert second.task_ids == [41]
    assert len(database.state["tasks"]) == 1


def test_pending_settings_draft_blocks_before_task_or_event_changes():
    settings = _settings(_entry("dump-ai"))
    event = _event(11, ["dump-ai"])
    st = FakeStreamlit(settings, event)
    st.session_state["pending_app_settings"] = {"settings": settings}
    database = TransactionalDatabase(json.dumps(settings), event)

    result = _run_database(database, st, [_decision("dump-ai")], event_id=11, status_prefix="ai_")

    assert result.status == "rejected"
    assert database.state["tasks"] == []
    assert database.state["event"]["status"] == "proposed"
    assert st.session_state["pending_app_settings"]["settings"] == settings


def test_missing_settings_row_blocks_conversion_without_creating_tasks():
    settings = _settings(_entry("dump-a"))
    st = FakeStreamlit(settings)
    database = TransactionalDatabase(None)

    result = _run_database(database, st, [_decision("dump-a")])

    assert result.status == "rejected"
    assert database.state["tasks"] == []


def test_session_only_conversion_is_idempotent_and_does_not_touch_cases():
    settings = _settings(_entry("dump-a"))
    st = FakeStreamlit(settings)
    cases_before = deepcopy(st.session_state.surgical_cases)

    first = _run_session(st, [_decision("dump-a")])
    second = _run_session(st, [_decision("dump-a")])

    assert first.status == "created"
    assert second.status == "already_applied"
    assert [task["id"] for task in st.session_state.tasks] == [1]
    assert st.session_state.app_settings["brain_dump_entries"][0]["task_id"] == 1
    assert st.session_state.surgical_cases == cases_before


def test_session_batch_failure_restores_tasks_settings_and_id_counter(monkeypatch):
    settings = _settings(_entry("dump-a"), _entry("dump-b"))
    st = FakeStreamlit(settings)
    original_tasks = deepcopy(st.session_state.tasks)
    original_settings = deepcopy(st.session_state.app_settings)
    original_allocator = data_access.allocate_task_id
    calls = 0

    def fail_second_allocation(session_state):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated allocation failure")
        return original_allocator(session_state)

    monkeypatch.setattr(data_access, "allocate_task_id", fail_second_allocation)
    with pytest.raises(RuntimeError, match="simulated allocation failure"):
        _run_session(st, [_decision("dump-a"), _decision("dump-b")])

    assert st.session_state.tasks == original_tasks
    assert st.session_state.app_settings == original_settings
    assert "task_id_high_watermark" not in st.session_state


def test_non_task_ai_decision_keeps_existing_reminder_semantics():
    settings = _settings(_entry("dump-reminder"), quick_reminders=[])
    event = _event(12, ["dump-reminder"])
    st = FakeStreamlit(settings, event)

    result = _run_session(
        st,
        [_decision("dump-reminder", item_type="reminder")],
        event_id=12,
        status_prefix="ai_",
    )

    assert result.status == "applied"
    assert st.session_state.app_settings["quick_reminders"][0]["reminder_id"] == "dump-reminder"
    assert st.session_state.app_settings["brain_dump_entries"][0]["status"] == "ai_converted_reminder"
    assert st.session_state.ai_events[0]["status"] == "applied"


def test_session_ai_proposal_failure_rolls_back_conversion(monkeypatch):
    settings = _settings(_entry("dump-ai"))
    event = _event(13, ["dump-ai"])
    st = FakeStreamlit(settings, event)
    original_event = deepcopy(st.session_state.ai_events)
    original_allocator = data_access.allocate_task_id

    def fail_task_creation(_session_state):
        raise RuntimeError("simulated session task failure")

    monkeypatch.setattr(data_access, "allocate_task_id", fail_task_creation)
    with pytest.raises(RuntimeError, match="simulated session task failure"):
        _run_session(st, [_decision("dump-ai")], event_id=13, status_prefix="ai_")

    assert st.session_state.tasks == []
    assert st.session_state.app_settings == settings
    assert st.session_state.ai_events == original_event
    monkeypatch.setattr(data_access, "allocate_task_id", original_allocator)


def test_ai_conversion_rejects_an_unrelated_proposal():
    settings = _settings(_entry("dump-a"))
    event = _event(14, ["other-dump"])
    st = FakeStreamlit(settings, event)

    result = _run_session(st, [_decision("dump-a")], event_id=14, status_prefix="ai_")

    assert result.status == "rejected"
    assert st.session_state.tasks == []
    assert st.session_state.ai_events[0]["status"] == "proposed"
