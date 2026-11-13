import ast
import html
from datetime import date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

import page_renderers
from scheduling_core import task_attention_sort_key


TODAY = date(2026, 11, 13)


class RerunRequested(Exception):
    pass


class FakeStreamlit:
    def __init__(self, clicked=None):
        self.clicked = clicked
        self.keys = set()
        self.buttons = set()
        self.cards = []
        self.messages = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def _widget(self, key):
        if key is not None:
            if key in self.keys:
                raise AssertionError(f"Duplicate widget key: {key}")
            self.keys.add(key)

    def columns(self, spec, **kwargs):
        return [self for _ in range(spec if isinstance(spec, int) else len(spec))]

    def button(self, label, key=None, **kwargs):
        self._widget(key)
        self.buttons.add((label, key))
        return key == self.clicked

    def selectbox(self, label, options, index=0, key=None, **kwargs):
        self._widget(key)
        if self.clicked == "notifications_page_alert_status_apply_1" and key == "notifications_page_alert_status_select_1":
            return "in_progress"
        return options[index]

    def text_input(self, label, value="", key=None, **kwargs):
        self._widget(key)
        return value

    def text_area(self, label, value="", key=None, **kwargs):
        self._widget(key)
        return value

    def checkbox(self, label, value=False, key=None, **kwargs):
        self._widget(key)
        return value

    def date_input(self, label, value=None, key=None, **kwargs):
        self._widget(key)
        return value

    def time_input(self, label, value=None, key=None, **kwargs):
        self._widget(key)
        return value

    def number_input(self, label, value=None, key=None, **kwargs):
        self._widget(key)
        return value

    def markdown(self, text, **kwargs):
        self.messages.append(text)

    def html(self, content):
        self.cards.append(content)

    def metric(self, *args, **kwargs):
        pass

    def caption(self, text):
        self.messages.append(text)

    info = warning = error = success = caption

    def expander(self, *args, **kwargs):
        return self

    def form(self, *args, **kwargs):
        return self

    def form_submit_button(self, *args, **kwargs):
        return False

    def rerun(self):
        raise RerunRequested


def task(task_id, **overrides):
    item = {
        "id": task_id,
        "title": f"Task {task_id}",
        "description": "",
        "category": "Clinic",
        "priority": "medium",
        "status": "todo",
        "due_date": None,
        "scheduled_date": None,
        "scheduled_time": None,
    }
    item.update(overrides)
    return item


@pytest.fixture
def renderer():
    # Extract only the renderers: importing streamlit_app executes app startup and DB initialization.
    source = Path(__file__).resolve().parents[1] / "streamlit_app.py"
    tree = ast.parse(source.read_text())
    names = {"render_task_card", "render_notifications_panel"}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in nodes} == names
    namespace = {
        "html": html,
        "os": SimpleNamespace(environ={}),
        "date": date,
        "datetime": datetime,
        "time": time,
        "timedelta": timedelta,
        "page_renderers": page_renderers,
        "mountain_today": lambda: TODAY,
        "task_attention_sort_key": task_attention_sort_key,
        "task_attention_signal": lambda item: {"tier": 4, "age_days": 0},
        "status_label": lambda value: value,
        "format_due_badge": lambda item: "",
        "format_schedule_badge": lambda item: "",
        "format_recurrence_badge": lambda item: "",
        "render_metrics_row": Mock(),
        "telegram_alerts": SimpleNamespace(build_telegram_config=lambda *args: {}),
        "normalize_quick_reminders": lambda items: [],
        "complete_task": Mock(),
        "set_task_status": Mock(),
        "delete_task": Mock(),
        "update_task": Mock(),
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)

    def render(tasks, clicked=None):
        st = FakeStreamlit(clicked=clicked)
        namespace["st"] = st
        namespace["render_notifications_panel"](tasks, tasks, {}, panel_key="notifications_page")
        return st

    return render, namespace


def test_single_category_has_one_card_and_reason(renderer):
    render, _ = renderer
    st = render([task(1, due_date=TODAY - timedelta(days=1))])
    assert len(st.cards) == 1
    assert "Overdue" in st.cards[0]
    assert "Blocked</span>" not in st.cards[0]
    assert "Unscheduled high-priority" not in st.cards[0]


def test_multiple_categories_show_all_reasons_once_without_duplicate_keys(renderer):
    render, _ = renderer
    overlapping = task(1, status="blocked", priority="high", due_date=TODAY - timedelta(days=1))
    st = render([overlapping])
    assert len(st.cards) == 1
    for reason in ("Overdue", "Blocked", "Unscheduled high-priority"):
        assert f">{reason}</span>" in st.cards[0]
    assert "notifications_page_alert_complete_1" in st.keys
    assert any("1 overdue task(s)" in message for message in st.messages)
    assert any("1 blocked task(s)" in message for message in st.messages)
    assert any("1 high-priority task(s)" in message for message in st.messages)


def test_multiple_tasks_in_same_category_and_other_category(renderer):
    render, _ = renderer
    st = render([
        task(1, status="blocked"),
        task(2, status="blocked"),
        task(3, status="blocked", category="Other"),
    ])
    assert len(st.cards) == 3
    assert {f"notifications_page_alert_complete_{i}" for i in (1, 2, 3)} <= st.keys


def test_keys_stay_stable_across_renders_and_reason_changes(renderer):
    render, _ = renderer
    original = task(1, status="blocked", priority="high", due_date=TODAY - timedelta(days=1))
    first = render([original, task(2, status="blocked", category="Personal")])
    second = render([task(2, status="blocked", category="Personal"), original])
    changed = render([task(1, status="blocked", category="Personal")])
    assert first.keys == second.keys
    assert {key for key in first.keys if key.startswith("notifications_page_alert_") and key.endswith("_1")} == {
        key for key in changed.keys if key.startswith("notifications_page_alert_") and key.endswith("_1")
    }


def test_due_tomorrow_summary_and_reason_when_task_already_in_alerts(renderer):
    render, _ = renderer
    st = render([task(1, status="blocked", due_date=TODAY + timedelta(days=1)),
                 task(2, due_date=TODAY + timedelta(days=1))])
    assert len(st.cards) == 1
    assert ">Due tomorrow</span>" in st.cards[0]
    assert any("2 task(s) are due tomorrow" in message for message in st.messages)


@pytest.mark.parametrize(
    ("button_suffix", "callback"),
    [
        ("complete", "complete_task"),
        ("status_apply", "set_task_status"),
        ("delete", "delete_task"),
        ("save", "update_task"),
    ],
)
def test_task_actions_remain_available_and_call_existing_handlers(renderer, button_suffix, callback):
    render, namespace = renderer
    item = task(1, status="blocked", priority="high", due_date=TODAY - timedelta(days=1))
    key = f"notifications_page_alert_{button_suffix}_1"
    with pytest.raises(RerunRequested):
        render([item], clicked=key)
    handler = namespace[callback]
    handler.assert_called_once()
    assert handler.call_args.args[0] == 1
    if callback == "set_task_status":
        assert handler.call_args.args == (1, "in_progress")
    if callback == "update_task":
        assert handler.call_args.kwargs["title"] == "Task 1"
        assert handler.call_args.kwargs["priority"] == "high"


def test_all_existing_task_and_telegram_buttons_are_rendered(renderer):
    render, _ = renderer
    st = render([task(1, status="blocked")])
    expected = {
        ("Mark complete", "notifications_page_alert_complete_1"),
        ("Apply status", "notifications_page_alert_status_apply_1"),
        ("Delete", "notifications_page_alert_delete_1"),
        ("Save changes", "notifications_page_alert_save_1"),
        ("Send test message", "notifications_page_telegram_test"),
        ("Run alert check now", "notifications_page_telegram_run_now"),
        ("Clear alert history", "notifications_page_telegram_clear_history"),
    }
    assert expected <= st.buttons


def test_real_streamlit_rerenders_overlapping_alerts_and_runs_task_action():
    script = """
import ast
import html
from datetime import date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
import streamlit as st
import page_renderers
from scheduling_core import task_attention_sort_key

source = Path("streamlit_app.py")
tree = ast.parse(source.read_text())
names = {"render_task_card", "render_notifications_panel"}
nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), globals())
os = SimpleNamespace(environ={})
today = date(2026, 11, 13)
mountain_today = lambda: today
task_attention_signal = lambda task: {"tier": 4, "age_days": 0}
status_label = lambda value: value
format_due_badge = format_schedule_badge = format_recurrence_badge = lambda task: ""
render_metrics_row = lambda: None
telegram_alerts = SimpleNamespace(build_telegram_config=lambda *args: {})
normalize_quick_reminders = lambda items: []
def complete_task(task_id):
    st.session_state["completed_id"] = task_id
tasks = [
    {"id": 1, "title": "Overlapping", "category": "Clinic", "priority": "high",
     "status": "blocked", "due_date": today - timedelta(days=1)},
    {"id": 2, "title": "Second", "category": "Clinic", "priority": "medium",
     "status": "blocked", "due_date": None},
]
render_notifications_panel(tasks, tasks, {}, panel_key="notifications_page")
"""
    app = AppTest.from_string(script).run()
    assert not app.exception
    expected_keys = {widget.key for widget in app.button}
    assert "notifications_page_alert_complete_1" in expected_keys
    assert "notifications_page_alert_complete_2" in expected_keys
    app.run()
    assert not app.exception
    assert {widget.key for widget in app.button} == expected_keys
    app.button(key="notifications_page_alert_complete_1").click().run()
    assert not app.exception
    assert app.session_state["completed_id"] == 1
