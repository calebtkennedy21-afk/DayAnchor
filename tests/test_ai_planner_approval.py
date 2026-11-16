import ast
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Event, Lock, RLock
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import data_access
from ai_envelope import AIEnvelope, AIItem


class SessionState(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


class FakeStreamlit:
    def __init__(self):
        self.session_state = SessionState(tasks=[], surgical_cases=[])


def _suggestion(title):
    return {
        "title": title,
        "description": f"Description for {title}",
        "category": "Personal",
        "priority": "medium",
        "due_date": date(2026, 11, 13),
        "scheduled_date": None,
        "scheduled_time": None,
        "scheduled_minutes": None,
    }


def _proposal(st, suggestions, expires_at=None):
    envelope = AIEnvelope(
        pillar="planning",
        kind="suggestion",
        summary=f"Suggested {len(suggestions)} task(s)",
        items=[AIItem("task_suggestion", dict(item)) for item in suggestions],
        status="proposed",
        expires_at=expires_at or datetime.now(data_access.MOUNTAIN_TIMEZONE) + timedelta(days=1),
    )
    return data_access.log_ai_event(envelope, date.today(), st_module=st)


def _factory(st):
    def create(item):
        task_id = data_access.allocate_task_id(st.session_state)
        st.session_state.tasks.append({"id": task_id, **item})
        return task_id

    return create


def _apply(st, event_id, task_factory=None):
    return data_access.apply_planner_task_suggestions(
        event_id,
        date(2026, 11, 13),
        task_factory=task_factory or _factory(st),
        st_module=st,
    )


def test_approval_is_bound_to_the_selected_event_payload():
    st = FakeStreamlit()
    first_id = _proposal(st, [_suggestion("First proposal")])
    second_id = _proposal(st, [_suggestion("Second proposal")])

    assert _apply(st, second_id) == [1]
    assert [task["title"] for task in st.session_state.tasks] == ["Second proposal"]
    events = {event["id"]: event for event in st.session_state.ai_events}
    assert events[first_id]["status"] == "expired"
    assert events[second_id]["status"] == "applied"


def test_rejection_does_not_create_tasks():
    st = FakeStreamlit()
    event_id = _proposal(st, [_suggestion("Rejected")])

    assert data_access.reject_planner_task_suggestions(event_id, st_module=st)
    assert st.session_state.tasks == []
    assert st.session_state.ai_events[0]["status"] == "rejected"


@pytest.mark.parametrize("resolution", ["expired", "superseded"])
def test_expired_or_superseded_proposal_cannot_be_applied(resolution):
    st = FakeStreamlit()
    expires_at = (
        datetime.now(data_access.MOUNTAIN_TIMEZONE) - timedelta(seconds=1)
        if resolution == "expired"
        else datetime.now(data_access.MOUNTAIN_TIMEZONE) + timedelta(days=1)
    )
    event_id = _proposal(st, [_suggestion("Unavailable")], expires_at=expires_at)
    if resolution == "superseded":
        _proposal(st, [_suggestion("New proposal")])

    with pytest.raises(ValueError):
        _apply(st, event_id)
    assert st.session_state.tasks == []
    original = next(event for event in st.session_state.ai_events if event["id"] == event_id)
    assert original["status"] == "expired"


def test_repeated_approval_cannot_duplicate_tasks():
    st = FakeStreamlit()
    event_id = _proposal(st, [_suggestion("Once")])
    assert _apply(st, event_id) == [1]

    with pytest.raises(ValueError):
        _apply(st, event_id)
    assert [task["title"] for task in st.session_state.tasks] == ["Once"]


def test_failure_after_partial_session_creation_rolls_back_and_can_retry():
    st = FakeStreamlit()
    suggestions = [_suggestion("First"), _suggestion("Second")]
    event_id = _proposal(st, suggestions)
    attempts = 0

    def fail_after_second_insert(item):
        nonlocal attempts
        task_id = _factory(st)(item)
        attempts += 1
        if attempts == 2:
            raise RuntimeError("simulated task creation failure")
        return task_id

    original_cases = deepcopy(st.session_state.surgical_cases)
    with pytest.raises(RuntimeError, match="simulated task creation failure"):
        _apply(st, event_id, fail_after_second_insert)
    assert st.session_state.tasks == []
    assert st.session_state.get("task_id_high_watermark", 0) == 0
    assert st.session_state.ai_events[0]["status"] == "proposed"
    assert st.session_state.surgical_cases == original_cases

    assert _apply(st, event_id) == [1, 2]
    assert [task["title"] for task in st.session_state.tasks] == ["First", "Second"]


def test_session_fallback_payload_is_the_immutable_preview_source():
    st = FakeStreamlit()
    suggestions = [_suggestion("Saved payload")]
    event_id = _proposal(st, suggestions)
    suggestions[0]["title"] = "Mutated caller copy"

    event = data_access.get_ai_event(event_id, st_module=st)
    assert data_access.planner_task_suggestions(event)[0]["title"] == "Saved payload"
    assert _apply(st, event_id)[0] == 1
    assert st.session_state.tasks[0]["title"] == "Saved payload"


class _TransactionalDatabase:
    def __init__(self, event, fail=None, pause_first_lock=False):
        self.state = {"event": deepcopy(event), "tasks": [], "next_task_id": 20}
        self.fail = fail
        self.lock = RLock()
        self.lock_attempts_lock = Lock()
        self.lock_attempts = 0
        self.first_lock_acquired = Event()
        self.second_lock_attempting = Event()
        self.release_first_lock = Event()
        self.pause_first_lock = pause_first_lock

    def connect(self):
        return _TransactionalConnection(self)


class _TransactionalConnection:
    def __init__(self, database):
        self.database = database
        self.snapshot = None
        self.lock_acquired = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            if exc_type is not None and self.snapshot is not None:
                self.database.state.clear()
                self.database.state.update(self.snapshot)
        finally:
            if self.lock_acquired:
                self.database.lock.release()
        return False

    def cursor(self, row_factory=None):
        return _TransactionalCursor(self)


class _TransactionalCursor:
    def __init__(self, connection):
        self.connection = connection
        self.database = connection.database
        self.rowcount = 0
        self.result = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params):
        if "FOR UPDATE" in query:
            with self.database.lock_attempts_lock:
                self.database.lock_attempts += 1
                attempt = self.database.lock_attempts
            if attempt == 2:
                self.database.second_lock_attempting.set()
            self.database.lock.acquire()
            self.connection.lock_acquired = True
            self.connection.snapshot = deepcopy(self.database.state)
            if attempt == 1:
                self.database.first_lock_acquired.set()
                if self.database.pause_first_lock:
                    assert self.database.release_first_lock.wait(timeout=5)
            event = self.database.state["event"]
            self.result = event if params[0] == event["id"] else None
        elif query.lstrip().startswith("INSERT INTO tasks"):
            if self.database.fail == "task_insert":
                self.database.fail = None
                raise RuntimeError("simulated task insertion failure")
            task_id = self.database.state["next_task_id"]
            self.database.state["next_task_id"] += 1
            self.database.state["tasks"].append(task_id)
            self.result = {"id": task_id}
        elif query.lstrip().startswith("UPDATE ai_events"):
            if self.database.fail == "event_resolution":
                self.database.fail = None
                raise RuntimeError("simulated event-resolution failure")
            event = self.database.state["event"]
            if "task_ids = %s" in query:
                task_ids, note, event_id = params
                if event["id"] == event_id and event["status"] == "proposed":
                    event.update(
                        status="applied",
                        outcome="accepted",
                        task_ids=task_ids,
                        outcome_note=note,
                        resolved_at=datetime.now(data_access.MOUNTAIN_TIMEZONE),
                    )
                    self.rowcount = 1
            elif event["id"] == params[0] and event["status"] == "proposed":
                event.update(
                    status="expired",
                    outcome="ignored",
                    resolved_at=datetime.now(data_access.MOUNTAIN_TIMEZONE),
                )
                self.rowcount = 1

    def fetchone(self):
        return self.result


def _database_for_event(st, event_id, fail=None, pause_first_lock=False):
    event = next(event for event in st.session_state.ai_events if event["id"] == event_id)
    return _TransactionalDatabase(event, fail=fail, pause_first_lock=pause_first_lock)


def _apply_database(database, event_id):
    return data_access.apply_planner_task_suggestions(
        event_id,
        date(2026, 11, 13),
        db_enabled_fn=lambda: True,
        get_connection_fn=database.connect,
    )


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("task_insert", "task insertion failure"),
        ("event_resolution", "event-resolution failure"),
    ],
)
def test_database_transaction_failures_roll_back_and_allow_retry(failure, message):
    st = FakeStreamlit()
    event_id = _proposal(st, [_suggestion("Atomic task"), _suggestion("Second task")])
    database = _database_for_event(st, event_id, fail=failure)
    original_event = deepcopy(database.state["event"])

    with pytest.raises(RuntimeError, match=message):
        _apply_database(database, event_id)

    assert database.state["tasks"] == []
    assert database.state["event"] == original_event
    assert _apply_database(database, event_id) == [20, 21]
    assert database.state["tasks"] == [20, 21]
    assert database.state["event"]["status"] == "applied"
    assert database.state["event"]["outcome"] == "accepted"
    assert database.state["event"]["task_ids"] == [20, 21]
    assert database.state["event"]["outcome_note"] == "2 task(s) added"

    with pytest.raises(ValueError, match="no longer available"):
        _apply_database(database, event_id)
    assert database.state["tasks"] == [20, 21]


def test_concurrent_database_approvals_serialize_on_proposal_row():
    st = FakeStreamlit()
    event_id = _proposal(st, [_suggestion("Only once")])
    database = _database_for_event(st, event_id, pause_first_lock=True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_apply_database, database, event_id)
        assert database.first_lock_acquired.wait(timeout=5)
        second = pool.submit(_apply_database, database, event_id)
        assert database.second_lock_attempting.wait(timeout=5)
        database.release_first_lock.set()

        assert first.result(timeout=5) == [20]
        with pytest.raises(ValueError, match="no longer available"):
            second.result(timeout=5)

    assert database.state["tasks"] == [20]
    assert database.state["event"]["status"] == "applied"
    assert database.state["event"]["outcome"] == "accepted"
    assert database.state["event"]["task_ids"] == [20]


def test_normal_session_task_creation_and_case_records_remain_untouched():
    st = FakeStreamlit()
    before_cases = deepcopy(st.session_state.surgical_cases)
    task_id = _factory(st)(_suggestion("Ordinary task"))

    assert task_id == 1
    assert st.session_state.tasks[0]["title"] == "Ordinary task"
    assert st.session_state.surgical_cases == before_cases


@pytest.mark.parametrize(
    "note",
    ["proposal expired", "superseded by a newer Planner proposal"],
)
def test_ai_activity_shows_expired_lifecycle_separately_from_outcome(note):
    class ActivityStreamlit:
        def __init__(self):
            self.messages = []

        def markdown(self, text, **kwargs):
            self.messages.append(text)

        def columns(self, count):
            return [self] * (count if isinstance(count, int) else len(count))

        def button(self, *args, **kwargs):
            return False

    st = ActivityStreamlit()
    event = {
        "id": 7,
        "pillar": "planning",
        "status": "expired",
        "outcome": "ignored",
        "outcome_note": note,
        "summary": "Suggested 1 task(s)",
    }
    data = SimpleNamespace(
        expire_stale_ai_events=Mock(),
        load_recent_ai_events=Mock(return_value=[event]),
    )
    source = Path(__file__).resolve().parents[1] / "streamlit_app.py"
    tree = ast.parse(source.read_text())
    render_node = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "render_ai_activity_panel"
    )
    namespace = {"st": st, "data_access": data, "_ai_db_kwargs": lambda: {}}
    exec(compile(ast.Module(body=[render_node], type_ignores=[]), str(source), "exec"), namespace)

    namespace["render_ai_activity_panel"]("test")

    rendered = "\n".join(st.messages)
    assert "· expired ·" in rendered
    assert "Outcome: ignored" in rendered
    assert note in rendered
