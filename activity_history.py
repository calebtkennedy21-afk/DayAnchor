"""Read-only daily history from existing activity records."""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from psycopg.rows import dict_row


MOUNTAIN_TIMEZONE = ZoneInfo("America/Denver")


def load_history_sources(day, db_enabled_fn, get_connection_fn, session_state):
    if not db_enabled_fn():
        return (
            list(session_state.get("tasks", [])),
            list(session_state.get("lead_clinical_issues", [])),
        )

    start = datetime.combine(day, time.min, MOUNTAIN_TIMEZONE).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time.min, MOUNTAIN_TIMEZONE).astimezone(timezone.utc)
    with get_connection_fn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id, title, status, recurrence_rule, completed_date, completed_at
                FROM tasks
                WHERE status = 'completed'
                  AND (completed_date = %s OR
                       (completed_date IS NULL AND completed_at >= %s AND completed_at < %s))
                """,
                (day, start.replace(tzinfo=None), end.replace(tzinfo=None)),
            )
            tasks = cur.fetchall()
            cur.execute(
                """
                SELECT id, status, resolved_date FROM lead_clinical_issues
                WHERE status = 'resolved' AND resolved_date = %s
                """,
                (day,),
            )
            issues = cur.fetchall()
    return tasks, issues


def _reminder_timestamp(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        return None
    return stamp.astimezone(MOUNTAIN_TIMEZONE)


def build_daily_history(day, tasks, reminders, issues):
    entries = []
    seen = set()

    def add(source, identifier, name, activity_type, outcome, stamp):
        key = (source, identifier)
        if identifier is None or key in seen:
            return
        seen.add(key)
        entries.append(
            {"name": name, "type": activity_type, "outcome": outcome, "timestamp": stamp}
        )

    for task in tasks:
        if task.get("status") != "completed":
            continue
        stamp = task.get("completed_at")
        completed_date = task.get("completed_date")
        if isinstance(stamp, datetime):
            # Startup backfilled older date-only records with naive midnight.
            if stamp.tzinfo is None and completed_date and stamp == datetime.combine(completed_date, time.min):
                stamp = None
            else:
                stamp = (stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp).astimezone(MOUNTAIN_TIMEZONE)
        else:
            stamp = None
        if (completed_date or (stamp.date() if stamp else None)) != day:
            continue
        add(
            "task", task.get("id"), str(task.get("title") or "Untitled task"),
            "Recurring task" if task.get("recurrence_rule") in ("daily", "weekly") else "Task",
            "Completed", stamp,
        )

    for index, reminder in enumerate(reminders):
        if not isinstance(reminder, dict) or str(reminder.get("status") or "").lower() != "dismissed":
            continue
        stamp = _reminder_timestamp(reminder.get("updated_at"))
        if stamp and stamp.date() == day:
            add(
                "reminder", reminder.get("reminder_id") or f"legacy-{index}",
                str(reminder.get("text") or reminder.get("title") or "Reminder"),
                "Reminder", "Dismissed", stamp,
            )

    for issue in issues:
        if issue.get("status") == "resolved" and issue.get("resolved_date") == day:
            add("issue", issue.get("id"), "Clinical issue", "Clinical issue", "Resolved", None)

    entries.sort(
        key=lambda item: item["timestamp"].astimezone(timezone.utc)
        if item["timestamp"] else datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return entries
