from copy import deepcopy
from datetime import date, time, timedelta
from pathlib import Path
import socket

from page_sections import (
    PLANNER_DAILY_NAVIGATION,
    build_planner_daily_overview,
    render_planner_daily_overview,
)
from streamlit.testing.v1 import AppTest


TODAY = date(2026, 11, 23)


def _attention_key(task, _today):
    priority_rank = {"high": 0, "medium": 1, "low": 2}
    return (
        priority_rank.get(task.get("priority"), 1),
        task.get("due_date") or date.max,
        str(task.get("title") or ""),
    )


def _daily_overview(
    tasks=(),
    family_items=(),
    workspace="Balanced",
    lead_followups=(),
    personal_goal_summary=None,
):
    return build_planner_daily_overview(
        tasks,
        family_items,
        workspace,
        TODAY,
        _attention_key,
        lead_followups=lead_followups,
        personal_goal_summary=personal_goal_summary,
    )


def _task(task_id, category, title, *, priority="medium", due_date=None, scheduled_date=None, scheduled_time=None):
    return {
        "id": task_id,
        "category": category,
        "title": title,
        "status": "todo",
        "priority": priority,
        "due_date": due_date,
        "scheduled_date": scheduled_date,
        "scheduled_time": scheduled_time,
    }


def test_clinical_overview_prioritizes_clinic_tasks_and_keeps_personal_deadlines():
    overview = _daily_overview(
        [
            _task(1, "Clinic", "Clinic follow-up", priority="high"),
            _task(2, "Personal", "Personal due item", due_date=TODAY + timedelta(days=1)),
            _task(3, "Personal", "Personal backlog", priority="low"),
        ],
        workspace="Clinical",
    )

    assert [task["title"] for task in overview["priority_tasks"]] == ["Clinic follow-up"]
    assert [task["title"] for task in overview["deadlines"]] == ["Personal due item"]
    assert all(task["title"] != "Personal backlog" for task in overview["priority_tasks"])


def test_personal_overview_prioritizes_personal_tasks_and_surfaces_personal_goal():
    goals = {
        "streak_leader": {
            "title": "Weekly exercise",
            "week_checkins": 1,
            "target_frequency": 3,
        }
    }
    overview = _daily_overview(
        [
            _task(1, "Clinic", "Clinic backlog", priority="high"),
            _task(2, "Personal", "Personal priority", priority="high"),
        ],
        workspace="Personal",
        personal_goal_summary=goals,
    )

    assert [task["title"] for task in overview["priority_tasks"]] == ["Personal priority"]
    assert overview["personal_goal"]["title"] == "Weekly exercise"


def test_balanced_overview_includes_both_contexts_and_unfinished_work():
    overview = _daily_overview(
        [
            _task(1, "Clinic", "Clinical priority", priority="high"),
            _task(2, "Personal", "Personal priority", priority="medium"),
            _task(3, "Personal", "Unfinished item", priority="low"),
        ],
    )

    assert [task["title"] for task in overview["priority_tasks"]] == [
        "Clinical priority",
        "Personal priority",
        "Unfinished item",
    ]
    assert overview["unfinished_tasks"] == []


def test_today_schedule_keeps_task_and_family_times_and_includes_untimed_commitments():
    overview = _daily_overview(
        [
            _task(1, "Clinic", "Clinic visit", scheduled_date=TODAY, scheduled_time=time(9, 30)),
            _task(2, "Personal", "Date-only task", scheduled_date=TODAY),
        ],
        [
            {
                "item_id": "family-1",
                "title": "School appointment",
                "start_date": TODAY,
                "end_date": TODAY,
                "start_time": time(14, 0),
            }
        ],
    )

    assert [(item["title"], item["time"]) for item in overview["commitments"]] == [
        ("Clinic visit", time(9, 30)),
        ("School appointment", time(14, 0)),
        ("Date-only task", None),
    ]


def test_unscheduled_responsibilities_are_separate_from_todays_scheduled_items():
    overview = _daily_overview(
        [
            _task(1, "Personal", "Scheduled today", scheduled_date=TODAY, scheduled_time=time(10, 0)),
            _task(2, "Personal", "No schedule", priority="high"),
        ],
        workspace="Personal",
    )

    assert [item["title"] for item in overview["commitments"]] == ["Scheduled today"]
    assert [task["title"] for task in overview["priority_tasks"]] == ["No schedule"]
    assert not overview["unfinished_tasks"]


def test_family_commitments_include_today_and_next_week_without_repetition():
    family = [
        {
            "item_id": "today",
            "title": "Practice",
            "start_date": TODAY,
            "end_date": TODAY,
            "start_time": time(17, 0),
        },
        {
            "item_id": "future",
            "title": "School meeting",
            "start_date": TODAY + timedelta(days=2),
            "end_date": TODAY + timedelta(days=2),
            "start_time": None,
        },
    ]

    overview = _daily_overview(family_items=family)

    assert [item["title"] for item in overview["commitments"]] == ["Practice"]
    assert [item["title"] for item in overview["upcoming_family"]] == ["School meeting"]


def test_cross_context_deadlines_remain_visible_in_every_workspace():
    clinical_task = _task(1, "Clinic", "Clinic deadline", due_date=TODAY)
    personal_task = _task(2, "Personal", "Personal deadline", due_date=TODAY + timedelta(days=2))

    for workspace in ("Clinical", "Personal", "Balanced"):
        overview = _daily_overview([clinical_task, personal_task], workspace=workspace)
        assert {task["title"] for task in overview["deadlines"]} == {
            "Clinic deadline",
            "Personal deadline",
        }


def test_ma_lead_followups_due_within_a_week_are_surfaced():
    overview = _daily_overview(
        lead_followups=[
            {"id": 1, "person_name": "Team contact", "next_follow_up_date": TODAY},
            {"id": 2, "person_name": "Later contact", "next_follow_up_date": TODAY + timedelta(days=10)},
        ],
    )

    assert [item["person_name"] for item in overview["lead_followups"]] == ["Team contact"]


def test_empty_or_session_only_inputs_produce_stable_empty_overview():
    overview = _daily_overview(tasks=None, family_items=None, lead_followups=None)

    assert overview["commitments"] == []
    assert overview["priority_tasks"] == []
    assert overview["deadlines"] == []
    assert overview["unfinished_tasks"] == []
    assert overview["upcoming_family"] == []


def test_duplicate_tasks_and_family_occurrences_are_not_repeated():
    task = _task(1, "Clinic", "Same task", scheduled_date=TODAY, scheduled_time=time(9, 0))
    family_item = {
        "item_id": "event-1",
        "title": "Same event",
        "start_date": TODAY,
        "end_date": TODAY,
        "start_time": time(11, 0),
    }
    overview = _daily_overview(
        [task, dict(task)],
        [family_item, dict(family_item)],
    )

    assert [item["title"] for item in overview["commitments"]] == ["Same task", "Same event"]


def test_daily_overview_build_does_not_mutate_source_records():
    tasks = [_task(1, "Personal", "Leave unchanged", priority="high")]
    family = [
        {
            "item_id": "family-1",
            "title": "Leave this unchanged",
            "start_date": TODAY,
            "end_date": TODAY,
        }
    ]
    original_tasks = deepcopy(tasks)
    original_family = deepcopy(family)

    _daily_overview(tasks, family)

    assert tasks == original_tasks
    assert family == original_family


class _FakeColumn:
    def __init__(self, streamlit):
        self.streamlit = streamlit

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def button(self, label, **_kwargs):
        self.streamlit.buttons.append(label)
        return False


class _FakeStreamlit:
    def __init__(self):
        self.session_state = {}
        self.buttons = []
        self.output = []

    def markdown(self, value, **_kwargs):
        self.output.append(str(value))

    def caption(self, value):
        self.output.append(str(value))

    def write(self, value):
        self.output.append(str(value))

    def columns(self, count, **_kwargs):
        return [_FakeColumn(self) for _ in range(count)]

    def button(self, label, **_kwargs):
        self.buttons.append(label)
        return False

    def rerun(self):
        raise AssertionError("A navigation rerun should not occur without a click.")


def test_daily_view_displays_timed_and_untimed_schedule_and_existing_navigation():
    streamlit = _FakeStreamlit()
    overview = _daily_overview(
        [
            _task(1, "Clinic", "Timed visit", scheduled_date=TODAY, scheduled_time=time(9, 0)),
            _task(2, "Personal", "Untimed commitment", scheduled_date=TODAY),
        ]
    )

    render_planner_daily_overview(overview, TODAY, "planner_daily", st_module=streamlit)

    assert any("9:00 AM · Timed visit" in line for line in streamlit.output)
    assert any("Time not set · Untimed commitment" in line for line in streamlit.output)
    assert tuple(
        (label.removeprefix("Open "), page)
        for label, page in PLANNER_DAILY_NAVIGATION
    ) == (
        ("Schedule", "Schedule"),
        ("Clinic", "Clinic"),
        ("MA Lead", "MA Lead"),
        ("Family", "Family Schedule"),
        ("Personal", "Personal"),
    )
    assert streamlit.buttons == [f"Open {label}" for label, _ in PLANNER_DAILY_NAVIGATION]


def test_no_schedule_renders_explicit_empty_state():
    streamlit = _FakeStreamlit()

    render_planner_daily_overview(_daily_overview(), TODAY, "planner_daily", st_module=streamlit)

    assert "No commitments are scheduled for today." in streamlit.output


def test_clinical_render_does_not_contradict_visible_lead_followups():
    streamlit = _FakeStreamlit()
    overview = _daily_overview(
        workspace="Clinical",
        lead_followups=[
            {"person_name": "Team contact", "next_follow_up_date": TODAY}
        ],
    )

    render_planner_daily_overview(overview, TODAY, "planner_daily", st_module=streamlit)

    assert any("MA Lead · Team contact" in line for line in streamlit.output)
    assert "No MA Lead follow-ups due in the next seven days." not in streamlit.output


def test_streamlit_component_rerun_keeps_workspace_selection_and_daily_view():
    app = AppTest.from_string(
        """
import streamlit as st
from datetime import date, time
from overview_core import resolve_planner_workspace
from page_sections import render_planner_daily_overview

mode = st.selectbox("Planner workspace", ["Auto", "Clinical", "Personal"], key="planner_workspace_mode")
workspace = resolve_planner_workspace(
    mode, date(2026, 11, 23), ["Monday"], True, ["Tuesday"]
)
st.caption(f"{workspace['mode']} workspace")
render_planner_daily_overview(
    {
        "workspace": workspace["mode"],
        "commitments": [
            {
                "time": time(9, 0),
                "title": "Clinic visit",
                "category": "Clinic",
                "minutes": 30,
                "due_date": None,
            }
        ],
        "priority_tasks": [],
        "deadlines": [],
        "unfinished_tasks": [],
        "lead_followups": [],
        "upcoming_family": [],
        "personal_goal": None,
    },
    date(2026, 11, 23),
    "acceptance",
)
"""
    ).run()

    assert not app.exception
    assert any("Clinical workspace" in element.value for element in app.caption)
    assert any("Clinic visit" in element.value for element in app.get("markdown"))

    app.selectbox(key="planner_workspace_mode").set_value("Personal").run()

    assert not app.exception
    assert app.session_state["planner_workspace_mode"] == "Personal"
    assert any("Personal workspace" in element.value for element in app.caption)
    assert any("Clinic visit" in element.value for element in app.get("markdown"))

    app.selectbox(key="planner_workspace_mode").set_value("Auto").run()

    assert not app.exception
    assert app.session_state["planner_workspace_mode"] == "Auto"
    assert any("Clinical workspace" in element.value for element in app.caption)


def test_real_workspace_confirmation_is_session_only_and_rejects_conflicts():
    app = AppTest.from_string(
        """
from datetime import date
from app_bootstrap import render_planner_workspace
render_planner_workspace(
    {"overview_clinic_weekdays": ["Monday"], "overview_admin_weekdays": ["Tuesday"]},
    date(2026, 11, 23),
)
"""
    ).run()
    assert not app.exception
    assert any("Balanced workspace" in item.value for item in app.info)

    app.multiselect[1].set_value(["Monday"])
    app.button[0].click().run()
    assert any("cannot be configured as both" in item.value for item in app.warning)
    assert "planner_session_schedule" not in app.session_state

    app.multiselect[1].set_value(["Tuesday"])
    app.button[0].click().run()
    assert not app.exception
    assert app.session_state["planner_session_schedule"] == {
        "clinic": ["Monday"],
        "admin": ["Tuesday"],
    }
    assert any("Clinical workspace" in item.value for item in app.caption)

    app.selectbox(key="planner_workspace_mode").set_value("Personal").run()
    assert any("Personal workspace" in item.value for item in app.caption)
    app.selectbox(key="planner_workspace_mode").set_value("Auto").run()
    assert not app.exception
    assert any("Clinical workspace" in element.value for element in app.caption)


def test_real_planner_renders_daily_overview_and_navigation_offline(monkeypatch):
    for name in (
        "DATABASE_URL", "DATABASE_PUBLIC_URL", "PGHOST", "PGPORT", "PGUSER",
        "PGPASSWORD", "PGDATABASE", "OPENAI_API_KEY", "NEWSAPI_KEY",
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_ALERTS_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)

    def blocked_network(*args, **kwargs):
        raise OSError("Network disabled for local smoke test")

    monkeypatch.setattr(socket.socket, "connect", blocked_network)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked_network)
    monkeypatch.setattr(socket, "create_connection", blocked_network)
    source = Path(__file__).resolve().parents[1] / "streamlit_app.py"
    app = AppTest.from_file(str(source), default_timeout=30)
    app.session_state["current_page"] = "Planner"
    app.run()
    assert not app.exception
    assert any(item.label == "Planner workspace" for item in app.selectbox)
    assert any("Today ·" in item.value for item in app.markdown)
    assert any(item.label == "Detailed overview and quick actions" for item in app.expander)

    app.button(key="planner_system_overview_daily_daily_open_schedule").click().run()
    assert not app.exception
    assert app.session_state["current_page"] == "Schedule"
