from datetime import date, time

from formatting_core import (
    format_due,
    format_due_badge,
    format_recurrence_badge,
    format_schedule,
    format_schedule_badge,
    recurrence_label,
    status_label,
)


def test_status_label_known_and_fallback():
    assert status_label("todo") == "Todo"
    assert status_label("in_progress") == "In Progress"
    assert status_label("custom_state") == "Custom State"


def test_recurrence_label_values():
    assert recurrence_label(None, 1) == "No recurrence"
    assert recurrence_label("daily", 2) == "Every 2 day(s)"
    assert recurrence_label("weekly", 3) == "Every 3 week(s)"


def test_format_due_with_date_and_missing():
    assert format_due({"due_date": date(2026, 5, 17)}) == "May 17, 2026"
    assert format_due({}) == "No due date"


def test_format_due_badge_handles_year_logic_and_missing():
    current_year = date.today().year
    assert format_due_badge({"due_date": date(current_year, 5, 17)}) == "May 17"
    assert format_due_badge({"due_date": date(current_year + 1, 5, 17)}) == f"May 17, {current_year + 1}"
    assert format_due_badge({}) == "No due"


def test_format_schedule_unscheduled_and_single_with_time():
    assert format_schedule({}) == "Unscheduled"
    task = {
        "scheduled_date": date(2026, 5, 17),
        "scheduled_end_date": date(2026, 5, 17),
        "scheduled_time": time(8, 30),
    }
    assert format_schedule(task) == "May 17, 8:30 AM"


def test_format_schedule_multi_day_with_time():
    task = {
        "scheduled_date": date(2026, 5, 17),
        "scheduled_end_date": date(2026, 5, 21),
        "scheduled_time": time(9, 0),
    }
    assert format_schedule(task) == "May 17 - May 21, 9:00 AM"


def test_format_schedule_badge_single_and_multi_day():
    current_year = date.today().year
    single = {
        "scheduled_date": date(current_year, 5, 17),
        "scheduled_end_date": date(current_year, 5, 17),
        "scheduled_time": time(13, 15),
    }
    assert format_schedule_badge(single) == "May 17, 1:15 PM"

    multi = {
        "scheduled_date": date(current_year + 1, 5, 17),
        "scheduled_end_date": date(current_year + 1, 5, 21),
        "scheduled_time": time(13, 15),
    }
    assert format_schedule_badge(multi) == f"May 17, {current_year + 1} - May 21, {current_year + 1}, 1:15 PM"


def test_format_recurrence_badge_no_repeat_and_rule():
    assert format_recurrence_badge({"recurrence_rule": None, "recurrence_interval": 1}) == "No repeat"
    assert format_recurrence_badge({"recurrence_rule": "weekly", "recurrence_interval": 2}) == "Every 2 week(s)"
