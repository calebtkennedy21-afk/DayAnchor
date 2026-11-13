import ast
from datetime import date, datetime, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import data_access
import page_renderers
from scheduling_core import shift_date_by_rule, task_attention_sort_key


TODAY = date(2026, 11, 13)


class SessionState(dict):
    def __getattr__(self, name):
        return self[name]

    def __setattr__(self, name, value):
        self[name] = value


class FakeStreamlit:
    def __init__(self):
        self.session_state = SessionState(tasks=[])
        self.messages = []
        self.cards = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def columns(self, count):
        return [self] * count

    def markdown(self, text, **kwargs):
        self.messages.append(text)

    error = warning = info = success = markdown


@pytest.fixture
def tasks_app():
    source = Path(__file__).resolve().parents[1] / "streamlit_app.py"
    tree = ast.parse(source.read_text())
    names = {
        "add_task", "update_task", "delete_task", "get_task_by_id", "complete_task",
        "seed_sample_tasks", "apply_ai_suggestions", "capture_task_from_quick_entry",
    }
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in nodes} == names
    st = FakeStreamlit()
    namespace = {
        "st": st,
        "data_access": SimpleNamespace(
            initialize_task_id_counter=data_access.initialize_task_id_counter,
            allocate_task_id=data_access.allocate_task_id,
            record_task_outcome=Mock(),
        ),
        "db_enabled": lambda: False,
        "get_connection": Mock(side_effect=AssertionError("Database access is forbidden")),
        "mountain_today": lambda: TODAY,
        "datetime": datetime,
        "date": date,
        "time": time,
        "shift_date_by_rule": shift_date_by_rule,
        "_ai_db_kwargs": lambda: {},
        "parse_quick_capture_input": lambda raw, settings: {"confidence": 0.0},
        "apply_post_capture_autoschedule": lambda *args: "",
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)
    data_access.initialize_task_id_counter(st.session_state)
    return namespace, st


def create_task(app, title, **kwargs):
    return app["add_task"](title, "", "Clinic", "medium", TODAY, **kwargs)


def test_delete_create_update_complete_and_notifications_keep_tasks_distinct(tasks_app):
    app, st = tasks_app
    first = create_task(app, "First")
    second = create_task(app, "Second")
    app["delete_task"](first)
    third = create_task(app, "Third")
    assert (first, second, third) == (1, 2, 3)

    app["update_task"](third, title="Third updated", status="blocked")
    app["update_task"](second, status="blocked")
    assert app["get_task_by_id"](second)["title"] == "Second"
    assert app["get_task_by_id"](third)["title"] == "Third updated"

    def record_card(task, key_prefix, alert_reasons):
        st.cards.append((task["id"], task["title"], key_prefix, alert_reasons))

    page_renderers.render_notification_alerts(
        st.session_state.tasks, TODAY, "notifications", record_card, task_attention_sort_key, st_module=st
    )
    assert {(card[0], card[1]) for card in st.cards} == {(second, "Second"), (third, "Third updated")}

    app["complete_task"](third)
    assert app["get_task_by_id"](third)["status"] == "completed"
    assert app["get_task_by_id"](second)["status"] == "blocked"
    app["delete_task"](third)
    assert [task["id"] for task in st.session_state.tasks] == [second]
    assert app["get_task_by_id"](second)["title"] == "Second"
    app["delete_task"](second)
    assert create_task(app, "Fourth") == 4
    app["get_connection"].assert_not_called()


def test_counter_initializes_from_legacy_tasks_and_advances_past_new_external_ids(tasks_app):
    app, st = tasks_app
    st.session_state.tasks = [{"id": 41}, {"id": 7}]
    data_access.initialize_task_id_counter(st.session_state)
    app["delete_task"](41)
    assert create_task(app, "Next") == 42
    st.session_state.tasks.append({"id": 100})
    assert create_task(app, "After imported task") == 101
    app["delete_task"](101)
    assert create_task(app, "After deletion") == 102


def test_all_creation_entry_points_share_the_allocator(tasks_app):
    app, st = tasks_app
    assert create_task(app, "Direct", recurrence_rule="daily") == 1
    app["seed_sample_tasks"]()
    app["apply_ai_suggestions"]([{
        "title": "Suggestion", "description": "", "category": "Personal",
        "priority": "low", "due_date": TODAY,
    }])
    quick = app["capture_task_from_quick_entry"]("Quick", "Clinic", "medium", TODAY, {})
    assert quick["task_id"] == 6
    app["delete_task"](2)
    app["complete_task"](1)
    assert [task["id"] for task in st.session_state.tasks] == [1, 3, 4, 5, 6, 7]
    assert st.session_state.tasks[-1]["title"] == "Direct"
    assert create_task(app, "Last") == 8
    app["get_connection"].assert_not_called()


@pytest.mark.parametrize("second_id", [1, "1"])
def test_notifications_reject_duplicate_ids_even_when_one_task_has_no_alert(tasks_app, second_id):
    _, st = tasks_app
    tasks = [
        {"id": 1, "title": "Blocked", "status": "blocked", "priority": "medium", "category": "Clinic"},
        {"id": second_id, "title": "Not urgent", "status": "todo", "priority": "medium", "category": "Personal"},
    ]
    render_card = Mock()
    page_renderers.render_notification_alerts(
        tasks, TODAY, "notifications", render_card, task_attention_sort_key, st_module=st
    )
    render_card.assert_not_called()
    assert any("multiple tasks share an ID" in message for message in st.messages)
