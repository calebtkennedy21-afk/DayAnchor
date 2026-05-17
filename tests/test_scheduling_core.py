from datetime import date, time

from scheduling_core import (
    build_week_rebalance_moves,
    clinic_visit_templates,
    personal_schedule_templates,
    priority_rank,
    safe_int,
    scheduled_date_range,
    scheduled_minutes_on_day,
    scheduled_span_position,
    shift_date_by_rule,
    task_attention_signal,
    task_attention_sort_key,
)


def test_shift_date_by_rule_daily_and_weekly():
    d = date(2026, 5, 17)
    assert shift_date_by_rule(d, "daily", 2) == date(2026, 5, 19)
    assert shift_date_by_rule(d, "weekly", 2) == date(2026, 5, 31)
    assert shift_date_by_rule(d, "none", 2) == d


def test_priority_rank_order():
    assert priority_rank("high") < priority_rank("medium") < priority_rank("low")


def test_safe_int_fallback_on_bad_values():
    assert safe_int("7", 1) == 7
    assert safe_int(None, 3) == 3
    assert safe_int("oops", 9) == 9


def test_task_attention_signal_overdue():
    task = {
        "priority": "high",
        "created_date": date(2026, 5, 1),
        "due_date": date(2026, 5, 10),
        "scheduled_date": None,
        "scheduled_time": None,
    }
    signal = task_attention_signal(task, reference_date=date(2026, 5, 17))
    assert signal["tier"] == 0
    assert signal["overdue_days"] == 7
    assert signal["label"] == "Overdue by 7d"


def test_task_attention_signal_high_unscheduled():
    task = {
        "priority": "high",
        "created_date": date(2026, 5, 10),
        "due_date": date(2026, 5, 30),
        "scheduled_date": None,
        "scheduled_time": None,
    }
    signal = task_attention_signal(task, reference_date=date(2026, 5, 17))
    assert signal["tier"] == 2
    assert signal["high_unscheduled"] is True


def test_task_attention_sort_key_returns_tuple():
    task = {
        "priority": "medium",
        "created_date": date(2026, 5, 10),
        "due_date": date(2026, 5, 20),
        "scheduled_date": date(2026, 5, 18),
        "scheduled_time": time(9, 0),
    }
    key = task_attention_sort_key(task, reference_date=date(2026, 5, 17))
    assert isinstance(key, tuple)


def test_scheduled_date_range_single_and_multi_day():
    single = {"scheduled_date": date(2026, 5, 17), "scheduled_end_date": date(2026, 5, 17)}
    multi = {"scheduled_date": date(2026, 5, 17), "scheduled_end_date": date(2026, 5, 19)}
    assert scheduled_date_range(single) == [date(2026, 5, 17)]
    assert scheduled_date_range(multi) == [date(2026, 5, 17), date(2026, 5, 18), date(2026, 5, 19)]


def test_scheduled_span_position():
    task = {"scheduled_date": date(2026, 5, 17), "scheduled_end_date": date(2026, 5, 19)}
    assert scheduled_span_position(task, date(2026, 5, 17)) == "start"
    assert scheduled_span_position(task, date(2026, 5, 18)) == "middle"
    assert scheduled_span_position(task, date(2026, 5, 19)) == "end"
    assert scheduled_span_position(task, date(2026, 5, 20)) is None


def test_scheduled_minutes_on_day_handles_invalid_minutes():
    task = {
        "scheduled_date": date(2026, 5, 17),
        "scheduled_end_date": date(2026, 5, 17),
        "scheduled_minutes": "bad",
    }
    assert scheduled_minutes_on_day(task, date(2026, 5, 17)) == 0


def test_build_week_rebalance_moves_moves_low_priority_single_day_only():
    week_days = [date(2026, 5, 18), date(2026, 5, 19), date(2026, 5, 20), date(2026, 5, 21), date(2026, 5, 22)]
    upcoming = [
        {
            "id": 1,
            "title": "Low block",
            "priority": "low",
            "due_date": date(2026, 5, 25),
            "scheduled_date": date(2026, 5, 18),
            "scheduled_end_date": date(2026, 5, 18),
            "scheduled_time": time(9, 0),
            "scheduled_minutes": 180,
        },
        {
            "id": 2,
            "title": "High block",
            "priority": "high",
            "due_date": date(2026, 5, 25),
            "scheduled_date": date(2026, 5, 18),
            "scheduled_end_date": date(2026, 5, 18),
            "scheduled_time": time(13, 0),
            "scheduled_minutes": 180,
        },
    ]
    moves = build_week_rebalance_moves(upcoming, week_days, daily_capacity_minutes=300)
    assert len(moves) == 1
    assert moves[0]["task"]["id"] == 1
    assert moves[0]["source_day"] == date(2026, 5, 18)
    assert moves[0]["target_day"] in week_days[1:]


def test_clinic_visit_templates_contains_expected_defaults():
    templates = clinic_visit_templates()
    assert "new_consult" in templates
    assert templates["new_consult"]["scheduled_minutes"] == 45
    assert templates["phone_follow_up"]["schedule_enabled"] is False


def test_personal_schedule_templates_contains_vacation_defaults():
    templates = personal_schedule_templates()
    assert "vacation" in templates
    assert templates["vacation"]["all_day"] is True
    assert templates["vacation"]["scheduled_end_offset_days"] == 4
