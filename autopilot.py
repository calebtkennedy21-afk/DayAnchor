"""Autonomous, reversible housekeeping for DayAnchor.

Scope is intentionally narrow: roll undone scheduled tasks forward to today and
build reminder alerts (clinic closeout checklist, high-priority digest).
Nothing here touches surgical cases. Every automatic change is logged to
ai_events with a before/after snapshot so it can be undone.
"""

import json
from datetime import date, datetime, time, timedelta

import psycopg

from ai_envelope import AI_EVENTS_SCHEMA_SQL

ROLLOVER_MAX_PER_RUN = 25
ROLLOVER_SKIP_STATUSES = ("completed", "blocked")
PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


def _as_bool(value, default):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def _parse_time(value, fallback):
    if isinstance(value, time):
        return value
    text = str(value or "").strip()
    if len(text) == 5 and text[2] == ":":
        text = f"{text}:00"
    try:
        return time.fromisoformat(text) if text else fallback
    except ValueError:
        return fallback


def _parse_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "").strip()[:10])
    except ValueError:
        return None


def autopilot_config(settings):
    settings = settings or {}
    try:
        max_age = max(1, int(settings.get("autopilot_rollover_max_age_days") or 14))
    except (TypeError, ValueError):
        max_age = 14
    return {
        "rollover_enabled": _as_bool(settings.get("autopilot_rollover_enabled"), True),
        "reminders_enabled": _as_bool(settings.get("autopilot_reminders_enabled"), True),
        "rollover_max_age_days": max_age,
        "closeout_time": _parse_time(settings.get("autopilot_closeout_reminder_time"), time(16, 30)),
        "high_priority_time": _parse_time(settings.get("autopilot_high_priority_time"), time(8, 0)),
    }


def _snapshot(task):
    return {
        "scheduled_date": task.get("scheduled_date"),
        "scheduled_time": task.get("scheduled_time"),
        "scheduled_end_date": task.get("scheduled_end_date"),
    }


def plan_rollover(tasks, now_local, config):
    """Return changes that move undone, past-scheduled tasks to today.

    Due dates are never changed. Multi-day tasks still in progress, blocked
    tasks, and tasks older than rollover_max_age_days are left for a human.
    """
    today = now_local.date()
    max_age = config["rollover_max_age_days"]
    candidates = []
    for task in tasks or []:
        if str(task.get("status") or "") in ROLLOVER_SKIP_STATUSES:
            continue
        scheduled = _parse_date(task.get("scheduled_date"))
        if scheduled is None or scheduled >= today:
            continue
        if (today - scheduled).days > max_age:
            continue
        end_date = _parse_date(task.get("scheduled_end_date"))
        if end_date is not None and end_date >= today:
            continue
        candidates.append((PRIORITY_RANK.get(str(task.get("priority") or "medium"), 1), scheduled, task))

    changes = []
    for _, scheduled, task in sorted(candidates, key=lambda item: (item[0], item[1], str(item[2].get("id")))):
        old_time = task.get("scheduled_time")
        keep_time = isinstance(old_time, time) and old_time > now_local.time()
        changes.append(
            {
                "task_id": int(task["id"]),
                "title": str(task.get("title") or ""),
                "before": _snapshot(task),
                # An end date earlier than the new start would be invalid, so it is cleared.
                "after": {
                    "scheduled_date": today,
                    "scheduled_time": old_time if keep_time else None,
                    "scheduled_end_date": None,
                },
            }
        )
    return changes[:ROLLOVER_MAX_PER_RUN]


def ensure_ai_events_table(connection):
    with connection.transaction():
        with connection.cursor() as cursor:
            for statement in AI_EVENTS_SCHEMA_SQL:
                cursor.execute(statement)


def apply_rollover(connection, changes):
    """Apply planned changes, guarding on the original date. Returns applied changes."""
    applied = []
    with connection.cursor() as cursor:
        for change in changes:
            cursor.execute(
                """
                UPDATE tasks
                SET scheduled_date = %s, scheduled_time = %s, scheduled_end_date = %s
                WHERE id = %s AND scheduled_date = %s AND status NOT IN ('completed', 'blocked')
                """,
                (
                    change["after"]["scheduled_date"],
                    change["after"]["scheduled_time"],
                    change["after"]["scheduled_end_date"],
                    change["task_id"],
                    change["before"]["scheduled_date"],
                ),
            )
            if cursor.rowcount:
                applied.append(change)
    return applied


def log_event(connection, day, pillar, kind, status, summary, outcome=None, task_ids=None, payload=None, text=""):
    """Insert an ai_events row inside a savepoint so a failure never poisons the caller's transaction."""
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO ai_events (day, pillar, kind, status, source, summary, text,
                                           payload_json, task_ids, outcome, resolved_at)
                    VALUES (%s, %s, %s, %s, 'rules', %s, %s, %s::jsonb, %s, %s,
                            CASE WHEN %s::text IS NULL THEN NULL ELSE NOW() END)
                    """,
                    (
                        day, pillar, kind, status, summary, text,
                        json.dumps(payload or {}, default=str),
                        [int(t) for t in (task_ids or [])], outcome, outcome,
                    ),
                )
        return True
    except psycopg.Error:
        return False


def log_rollover(connection, day, applied):
    if not applied:
        return False
    items = [{"type": "rollover", "data": change, "task_ids": [change["task_id"]]} for change in applied]
    return log_event(
        connection, day, "autopilot", "schedule_change", "applied",
        f"Rolled {len(applied)} undone task(s) forward to today",
        outcome="auto_applied",
        task_ids=[change["task_id"] for change in applied],
        payload={"items": items},
    )


def _due(now_local, target, window_minutes, lateness_minutes):
    delta = (now_local - target).total_seconds() / 60.0
    return -max(1, int(window_minutes)) <= delta <= max(0, int(lateness_minutes))


def _open_button(app_url):
    if not app_url:
        return None
    return {"inline_keyboard": [[{"text": "Open DayAnchor", "url": app_url}]]}


def collect_reminder_alerts(tasks, settings, history, telegram_config, now_local):
    """Closeout-checklist and high-priority reminders, in the same shape as collect_due_alerts."""
    config = autopilot_config(settings)
    if not config["reminders_enabled"]:
        return []

    history = history or {}
    window = telegram_config.get("alert_window_minutes") or 5
    lateness = telegram_config.get("alert_lateness_minutes") or 0
    button = _open_button(telegram_config.get("app_url"))
    today = now_local.date()
    tzinfo = now_local.tzinfo
    alerts = []

    template = [str(item).strip() for item in (settings.get("clinic_day_closeout_template") or []) if str(item).strip()]
    closeout_key = f"autopilot:closeout:{today.isoformat()}"
    if template and today.weekday() < 5 and not history.get(closeout_key):
        due_dt = datetime.combine(today, config["closeout_time"], tzinfo=tzinfo)
        if _due(now_local, due_dt, window, lateness):
            entry = (settings.get("clinic_day_closeout_log") or {}).get(today.isoformat()) or {}
            raw_done = entry.get("completed_items") if isinstance(entry, dict) else entry
            done = {str(item).strip().lower() for item in (raw_done or [])}
            remaining = [item for item in template if item.lower() not in done]
            if remaining:
                shown = "\n".join(f"- {item}" for item in remaining[:6])
                extra = f"\n(+{len(remaining) - 6} more)" if len(remaining) > 6 else ""
                alerts.append(
                    {
                        "key": closeout_key,
                        "critical": False,
                        "autopilot_kind": "closeout_reminder",
                        "message": f"DayAnchor clinic closeout\n{len(remaining)} item(s) still open today:\n{shown}{extra}",
                        "reply_markup": button,
                    }
                )

    priority_key = f"autopilot:high_priority:{today.isoformat()}"
    if not history.get(priority_key):
        due_dt = datetime.combine(today, config["high_priority_time"], tzinfo=tzinfo)
        if _due(now_local, due_dt, window, lateness):
            urgent = []
            for task in tasks or []:
                if str(task.get("status") or "") == "completed" or str(task.get("priority") or "") != "high":
                    continue
                due_date = _parse_date(task.get("due_date"))
                scheduled = _parse_date(task.get("scheduled_date"))
                if (due_date is not None and due_date <= today) or scheduled == today:
                    urgent.append((due_date or today, task))
            if urgent:
                urgent.sort(key=lambda pair: pair[0])
                lines = []
                for due_date, task in urgent[:6]:
                    tag = " (overdue)" if due_date < today else ""
                    lines.append(f"- {task.get('title') or 'Untitled task'}{tag}")
                extra = f"\n(+{len(urgent) - 6} more)" if len(urgent) > 6 else ""
                alerts.append(
                    {
                        "key": priority_key,
                        "critical": False,
                        "autopilot_kind": "high_priority_digest",
                        "message": f"DayAnchor high-priority today\n" + "\n".join(lines) + extra,
                        "reply_markup": button,
                    }
                )
    return alerts


def plan_undo(payload, current_tasks_by_id):
    """Return [(task_id, fields)] that restore a rollover, skipping tasks changed since."""
    restores = []
    for item in (payload or {}).get("items", []):
        change = item.get("data") or {}
        task = current_tasks_by_id.get(change.get("task_id"))
        if not task or str(task.get("status") or "") == "completed":
            continue
        after = change.get("after") or {}
        if _parse_date(task.get("scheduled_date")) != _parse_date(after.get("scheduled_date")):
            continue
        before = change.get("before") or {}
        restores.append(
            (
                change["task_id"],
                {
                    "scheduled_date": _parse_date(before.get("scheduled_date")),
                    "scheduled_time": _parse_time(before.get("scheduled_time"), None) if before.get("scheduled_time") else None,
                    "scheduled_end_date": _parse_date(before.get("scheduled_end_date")),
                },
            )
        )
    return restores
