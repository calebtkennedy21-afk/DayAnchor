from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import autopilot
import telegram_alerts

TZ = ZoneInfo("America/Denver")
CONFIG = autopilot.autopilot_config({})


def _now(day=date(2026, 11, 4), hour=12, minute=0):
    return datetime.combine(day, time(hour, minute), tzinfo=TZ)


def _task(task_id, **overrides):
    task = {
        "id": task_id,
        "title": f"Task {task_id}",
        "priority": "medium",
        "status": "todo",
        "scheduled_date": date(2026, 11, 3),
        "scheduled_time": None,
        "scheduled_end_date": None,
        "due_date": None,
    }
    task.update(overrides)
    return task


def test_rollover_moves_past_scheduled_undone_tasks_to_today():
    changes = autopilot.plan_rollover([_task(1)], _now(), CONFIG)
    assert len(changes) == 1
    assert changes[0]["after"]["scheduled_date"] == date(2026, 11, 4)
    assert changes[0]["before"]["scheduled_date"] == date(2026, 11, 3)


def test_rollover_skips_completed_blocked_current_future_and_ancient_tasks():
    tasks = [
        _task(1, status="completed"),
        _task(2, status="blocked"),
        _task(3, scheduled_date=date(2026, 11, 4)),
        _task(4, scheduled_date=date(2026, 11, 9)),
        _task(5, scheduled_date=date(2026, 9, 1)),
        _task(6, scheduled_date=None),
        _task(7, scheduled_date=date(2026, 11, 2), scheduled_end_date=date(2026, 11, 5)),
    ]
    assert autopilot.plan_rollover(tasks, _now(), CONFIG) == []


def test_rollover_keeps_time_only_if_still_ahead_and_never_touches_due_date():
    tasks = [
        _task(1, scheduled_time=time(15, 0), due_date=date(2026, 11, 3)),
        _task(2, scheduled_time=time(9, 0)),
    ]
    changes = {c["task_id"]: c for c in autopilot.plan_rollover(tasks, _now(hour=12), CONFIG)}
    assert changes[1]["after"]["scheduled_time"] == time(15, 0)
    assert changes[2]["after"]["scheduled_time"] is None
    assert all("due_date" not in c["after"] for c in changes.values())


def test_rollover_orders_high_priority_first_and_caps_per_run():
    tasks = [_task(i, priority="low") for i in range(1, 40)] + [_task(99, priority="high")]
    changes = autopilot.plan_rollover(tasks, _now(), CONFIG)
    assert len(changes) == autopilot.ROLLOVER_MAX_PER_RUN
    assert changes[0]["task_id"] == 99


def test_rollover_can_be_disabled_via_settings():
    config = autopilot.autopilot_config({"autopilot_rollover_enabled": False})
    assert config["rollover_enabled"] is False
    assert autopilot.autopilot_config({})["rollover_enabled"] is True


def test_undo_restores_unchanged_tasks_and_skips_edited_or_completed_ones():
    changes = autopilot.plan_rollover([_task(1), _task(2), _task(3)], _now(), CONFIG)
    payload = {"items": [{"data": c} for c in changes]}
    current = {
        1: _task(1, scheduled_date=date(2026, 11, 4)),
        2: _task(2, scheduled_date=date(2026, 11, 10)),
        3: _task(3, scheduled_date=date(2026, 11, 4), status="completed"),
    }
    restores = autopilot.plan_undo(payload, current)
    assert [task_id for task_id, _ in restores] == [1]
    assert restores[0][1]["scheduled_date"] == date(2026, 11, 3)


def _telegram_config():
    return {"alert_window_minutes": 5, "alert_lateness_minutes": 60, "app_url": ""}


def test_closeout_reminder_lists_remaining_items_on_weekdays_only():
    settings = {
        "clinic_day_closeout_template": ["Chart", "Orders", "Restock"],
        "clinic_day_closeout_log": {"2026-11-04": {"completed_items": ["chart"]}},
    }
    now = _now(hour=16, minute=32)
    alerts = autopilot.collect_reminder_alerts([], settings, {}, _telegram_config(), now)
    assert len(alerts) == 1
    assert alerts[0]["key"] == "autopilot:closeout:2026-11-04"
    assert "2 item(s)" in alerts[0]["message"] and "Chart" not in alerts[0]["message"]

    saturday = _now(day=date(2026, 11, 7), hour=16, minute=32)
    assert autopilot.collect_reminder_alerts([], settings, {}, _telegram_config(), saturday) == []


def test_closeout_reminder_silent_when_done_or_already_sent():
    done = {
        "clinic_day_closeout_template": ["Chart"],
        "clinic_day_closeout_log": {"2026-11-04": {"completed_items": ["Chart"]}},
    }
    now = _now(hour=16, minute=31)
    assert autopilot.collect_reminder_alerts([], done, {}, _telegram_config(), now) == []
    pending = {"clinic_day_closeout_template": ["Chart"]}
    sent = {"autopilot:closeout:2026-11-04": "x"}
    assert autopilot.collect_reminder_alerts([], pending, sent, _telegram_config(), now) == []


def test_high_priority_digest_includes_due_today_and_overdue_only():
    tasks = [
        _task(1, priority="high", due_date=date(2026, 11, 4), scheduled_date=None, title="Due today"),
        _task(2, priority="high", due_date=date(2026, 11, 1), scheduled_date=None, title="Late one"),
        _task(3, priority="high", due_date=date(2026, 11, 20), scheduled_date=None, title="Far future"),
        _task(4, priority="medium", due_date=date(2026, 11, 4), scheduled_date=None, title="Not high"),
        _task(5, priority="high", status="completed", due_date=date(2026, 11, 4), title="Done"),
    ]
    alerts = autopilot.collect_reminder_alerts(tasks, {}, {}, _telegram_config(), _now(hour=8, minute=1))
    assert len(alerts) == 1
    message = alerts[0]["message"]
    assert "Due today" in message and "Late one (overdue)" in message
    assert "Far future" not in message and "Not high" not in message and "Done" not in message


def test_reminders_respect_settings_toggle():
    settings = {"autopilot_reminders_enabled": False, "clinic_day_closeout_template": ["Chart"]}
    assert autopilot.collect_reminder_alerts([], settings, {}, _telegram_config(), _now(hour=16, minute=31)) == []


def test_collect_due_alerts_includes_autopilot_reminders_when_settings_passed():
    config = telegram_alerts.build_telegram_config({"telegram_alerts_enabled": True}, {})
    settings = {"clinic_day_closeout_template": ["Chart"]}
    now = _now(hour=16, minute=31)
    without = telegram_alerts.collect_due_alerts([], [], {}, {}, {}, config, now_value=now)
    with_settings = telegram_alerts.collect_due_alerts([], [], {}, {}, {}, config, now_value=now, app_settings=settings)
    assert not any(a["key"].startswith("autopilot:") for a in without)
    assert any(a["key"].startswith("autopilot:closeout") for a in with_settings)
