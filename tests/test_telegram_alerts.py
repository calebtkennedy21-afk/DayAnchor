from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from telegram_alerts import build_telegram_config, collect_due_alerts


MOUNTAIN = ZoneInfo("America/Denver")


def alert_config(**overrides):
    config = {
        "timezone": "America/Denver",
        "alert_window_minutes": 5,
        "alert_lateness_minutes": 60,
        "morning_time": time(6, 30),
        "daily_review_time": time(20, 30),
        "reminder_followup_minutes": 10,
        "offset_minutes": [60, 15, 0],
        "app_url": "",
    }
    config.update(overrides)
    return config


def test_late_morning_and_daily_review_alerts_are_caught_up():
    now = datetime(2026, 9, 8, 7, 15, tzinfo=MOUNTAIN)

    alerts = collect_due_alerts(
        tasks=[],
        reminders=[],
        morning_checkins={},
        nightly_reflections={},
        history={},
        config=alert_config(),
        now_value=now,
    )

    assert any(alert["key"] == "ritual:morning:2026-09-08" for alert in alerts)


def test_late_scheduled_task_alert_is_caught_up_once():
    now = datetime(2026, 9, 8, 9, 2, tzinfo=MOUNTAIN)
    task = {
        "id": 42,
        "title": "Call PT",
        "status": "todo",
        "priority": "high",
        "scheduled_date": date(2026, 9, 8),
        "scheduled_time": time(9, 0),
    }

    alerts = collect_due_alerts(
        tasks=[task],
        reminders=[],
        morning_checkins={},
        nightly_reflections={},
        history={},
        config=alert_config(),
        now_value=now,
    )

    task_alerts = [alert for alert in alerts if alert["key"].startswith("task:42:")]
    assert task_alerts
    assert any(alert["key"].endswith(":0") for alert in task_alerts)


def test_scheduled_task_alert_fires_before_start_time():
    now = datetime(2026, 9, 8, 8, 58, tzinfo=MOUNTAIN)
    task = {
        "id": 43,
        "title": "Clinic huddle",
        "status": "todo",
        "priority": "medium",
        "scheduled_date": date(2026, 9, 8),
        "scheduled_time": time(9, 0),
    }

    alerts = collect_due_alerts(
        tasks=[task],
        reminders=[],
        morning_checkins={},
        nightly_reflections={},
        history={},
        config=alert_config(),
        now_value=now,
    )

    assert "task:43:2026-09-08:9:00 AM:0" in {alert["key"] for alert in alerts}


def test_family_event_alert_is_collected():
    now = datetime(2026, 9, 8, 14, 2, tzinfo=MOUNTAIN)
    event = {
        "item_id": "family_1",
        "title": "Soccer practice",
        "item_type": "Sports",
        "status": "planned",
        "start_date": date(2026, 9, 8),
        "start_time": time(14, 0),
    }

    alerts = collect_due_alerts(
        tasks=[],
        reminders=[],
        morning_checkins={},
        nightly_reflections={},
        history={},
        config=alert_config(),
        family_items=[event],
        now_value=now,
    )

    assert any(alert["key"].startswith("event:family_1:") for alert in alerts)
    assert any("Soccer practice" in alert["message"] for alert in alerts)


def test_alert_lateness_setting_is_loaded():
    config = build_telegram_config({"telegram_alert_lateness_minutes": 90}, environ={})

    assert config["alert_lateness_minutes"] == 90
