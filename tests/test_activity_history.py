from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

import psycopg
import pytest
from streamlit.testing.v1 import AppTest

from activity_history import build_daily_history, load_history_sources


DAY = date(2026, 11, 1)


def test_history_uses_retained_occurrences_and_distinct_outcomes_without_details():
    tasks = [
        {"id": 1, "title": "Repeat", "status": "completed", "recurrence_rule": "daily",
         "completed_date": DAY, "completed_at": datetime(2026, 11, 1, 7, 30),
         "description": "private task details"},
        {"id": 1, "title": "Repeat", "status": "completed", "recurrence_rule": "daily",
         "completed_date": DAY, "completed_at": datetime(2026, 11, 1, 7, 30)},
        {"id": 2, "title": "Repeat", "status": "completed", "recurrence_rule": "daily",
         "completed_date": DAY - timedelta(days=1), "completed_at": datetime(2026, 10, 31, 16)},
        {"id": 3, "title": "Active", "status": "todo", "recurrence_rule": "daily"},
        {"id": 4, "title": "Old task", "status": "completed", "completed_date": DAY,
         "completed_at": datetime(2026, 11, 1)},
    ]
    reminders = [
        {"reminder_id": "r1", "text": "Remember", "status": "dismissed",
         "updated_at": "2026-11-01T01:15:00-07:00", "notes": "private reminder details"},
        {"text": "Legacy reminder", "status": "dismissed", "updated_at": "2026-11-01T12:00:00-07:00"},
        {"reminder_id": "r2", "text": "No timestamp", "status": "dismissed"},
        {"reminder_id": "r3", "text": "Active", "status": "active",
         "updated_at": "2026-11-01T12:00:00-07:00"},
    ]
    issues = [{"id": 1, "title": "private clinical detail", "status": "resolved", "resolved_date": DAY}]
    originals = deepcopy((tasks, reminders, issues))

    entries = build_daily_history(DAY, tasks, reminders, issues)

    assert [(entry["name"], entry["type"], entry["outcome"]) for entry in entries] == [
        ("Legacy reminder", "Reminder", "Dismissed"),
        ("Remember", "Reminder", "Dismissed"),
        ("Repeat", "Recurring task", "Completed"),
        ("Old task", "Task", "Completed"),
        ("Clinical issue", "Clinical issue", "Resolved"),
    ]
    assert entries[1]["timestamp"].utcoffset() == timedelta(hours=-7)
    assert entries[2]["timestamp"].utcoffset() == timedelta(hours=-6)
    assert entries[3]["timestamp"] is None
    assert entries[4]["timestamp"] is None
    assert "private" not in repr(entries)
    assert (tasks, reminders, issues) == originals
    assert [entry["name"] for entry in build_daily_history(DAY - timedelta(days=1), tasks, [], [])] == ["Repeat"]


def test_missing_completed_date_uses_utc_timestamp_in_mountain_time():
    task = {"id": 1, "title": "Evening", "status": "completed",
            "completed_at": datetime(2026, 11, 2, 6, 30, tzinfo=timezone.utc)}
    assert build_daily_history(DAY, [task], [], [])[0]["timestamp"].hour == 23
    assert build_daily_history(DAY + timedelta(days=1), [task], [], []) == []


class Cursor:
    def __init__(self):
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def execute(self, query, parameters):
        self.queries.append((query, parameters))

    def fetchall(self):
        return []


class Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def cursor(self, **_kwargs):
        return self._cursor


def test_database_history_is_unfiltered_by_active_window_and_handles_dst():
    cursor = Cursor()
    assert load_history_sources(DAY, lambda: True, lambda: Connection(cursor), {}) == ([], [])
    sql, parameters = cursor.queries[0]
    assert "24 hours" not in sql
    assert parameters == (DAY, datetime(2026, 11, 1, 6), datetime(2026, 11, 2, 7))
    assert "SELECT id, status, resolved_date" in cursor.queries[1][0]
    assert cursor.queries[1][1] == (DAY,)


def test_session_history_reads_full_unfiltered_sources_and_database_errors_propagate():
    session = {"tasks": [{"id": 1}], "lead_clinical_issues": [{"id": 2}]}
    assert load_history_sources(DAY, lambda: False, None, session) == (
        session["tasks"], session["lead_clinical_issues"]
    )

    def fail():
        raise psycopg.OperationalError("unavailable")

    with pytest.raises(psycopg.OperationalError):
        load_history_sources(DAY, lambda: True, fail, session)


def test_history_widget_defaults_to_today_and_updates_on_date_selection():
    app = AppTest.from_string(
        """
from datetime import date, datetime
from page_sections import render_daily_activity_history
def load(day):
    return ([{"id": 1, "title": "Past task", "status": "completed",
              "completed_date": date(2026, 10, 31), "completed_at": datetime(2026, 10, 31, 18)}], [])
render_daily_activity_history(date(2026, 11, 1), "test", {}, load)
""",
        default_timeout=20,
    ).run()
    assert not app.exception
    assert app.date_input(key="test_activity_date").value == DAY
    assert any("0 completed" in element.value for element in app.caption)
    app.date_input(key="test_activity_date").set_value(DAY - timedelta(days=1)).run()
    assert not app.exception
    assert any("1 completed" in element.value for element in app.caption)
    assert app.dataframe[0].value.iloc[0]["Activity"] == "Past task"
    app.date_input(key="test_activity_date").set_value(date(2000, 1, 1)).run()
    assert not app.exception
    assert any("0 completed" in element.value for element in app.caption)


def test_history_widget_surfaces_database_failure():
    app = AppTest.from_string(
        """
from datetime import date
import psycopg
from page_sections import render_daily_activity_history
def load(day):
    raise psycopg.OperationalError("unavailable")
render_daily_activity_history(date(2026, 11, 1), "test", {}, load)
"""
    ).run()
    assert not app.exception
    assert "could not be loaded" in app.error[0].value


def test_planner_integration_places_history_after_overview_without_hiding_active_work():
    app = AppTest.from_string(
        """
from datetime import date
import page_sections
from page_sections import render_overview_control_tower
today = date(2026, 11, 1)
original_today = page_sections.mountain_today
original_details = page_sections._render_overview_control_tower_details
page_sections.mountain_today = lambda: today
page_sections._render_overview_control_tower_details = lambda *args, **kwargs: None
tasks = [{"id": 1, "title": "Active work", "status": "todo",
          "category": "Personal", "priority": "high"}]
deps = {"task_attention_sort_key": lambda task, day: (0, task["title"]),
        "load_daily_history_sources": lambda day: ([], [])}
try:
    render_overview_control_tower(
        tasks, tasks, [], tasks, [], [], {}, {}, "planner", deps,
        daily_overview=True, unfiltered_active_tasks=tasks,
    )
finally:
    page_sections.mountain_today = original_today
    page_sections._render_overview_control_tower_details = original_details
""",
        default_timeout=20,
    ).run()
    assert not app.exception
    assert any("Active work" in element.value for element in app.get("markdown"))
    assert [element.label for element in app.expander] == [
        "Daily Activity History", "Detailed overview and quick actions"
    ]
