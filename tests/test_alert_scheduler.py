from datetime import date, time

from alert_scheduler import _expand_family_items, _normalize_reminders


def test_scheduler_normalizes_json_reminder_dates_and_times():
    reminders = _normalize_reminders(
        [{"reminder_id": "r1", "text": "Call school", "remind_date": "2026-09-08", "remind_time": "09:15:00"}]
    )

    assert reminders[0]["remind_date"] == date(2026, 9, 8)
    assert reminders[0]["remind_time"] == time(9, 15)


def test_scheduler_expands_recurring_family_events():
    events = _expand_family_items(
        [
            {
                "item_id": "family_1",
                "title": "School pickup",
                "start_date": "2026-09-01",
                "start_time": "15:30",
                "recurrence_rule": "weekly",
                "recurrence_interval": 1,
            }
        ],
        anchor_day=date(2026, 9, 8),
        window_days=3,
    )

    assert len(events) == 1
    assert events[0]["start_date"] == date(2026, 9, 8)
    assert events[0]["start_time"] == time(15, 30)