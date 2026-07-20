import json
import os
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from urllib import error, parse, request

MOUNTAIN_TIMEZONE = ZoneInfo("America/Denver")


def _parse_time(raw_value, fallback):
    if isinstance(raw_value, time):
        return raw_value
    text = str(raw_value or "").strip()
    if not text:
        return fallback
    try:
        if len(text) == 5 and text[2] == ":":
            text = f"{text}:00"
        return time.fromisoformat(text)
    except ValueError:
        return fallback


def _parse_iso_datetime(raw_value):
    text = str(raw_value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _time_label(value):
    return value.strftime("%I:%M %p").lstrip("0")


def build_telegram_config(app_settings=None, environ=None):
    settings = app_settings or {}
    env = environ or os.environ
    token = str(env.get("TELEGRAM_BOT_TOKEN") or settings.get("telegram_bot_token") or "").strip()
    chat_id = str(settings.get("telegram_chat_id") or env.get("TELEGRAM_CHAT_ID") or "").strip()

    return {
        "enabled": bool(settings.get("telegram_alerts_enabled", False)),
        "bot_token": token,
        "chat_id": chat_id,
        "timezone": str(settings.get("telegram_timezone") or "America/Denver").strip() or "America/Denver",
        "quiet_start": _parse_time(settings.get("telegram_quiet_hours_start"), time(21, 30)),
        "quiet_end": _parse_time(settings.get("telegram_quiet_hours_end"), time(6, 0)),
        "morning_time": _parse_time(settings.get("telegram_morning_ritual_time"), time(6, 30)),
        "daily_review_time": _parse_time(settings.get("telegram_daily_review_time"), time(20, 30)),
        "alert_window_minutes": max(1, int(settings.get("telegram_alert_window_minutes") or 5)),
        "reminder_followup_minutes": max(1, int(settings.get("telegram_reminder_followup_minutes") or 10)),
        "app_url": str(settings.get("notification_app_url") or env.get("DAYANCHOR_APP_URL") or "").strip(),
        "offset_minutes": [60, 15, 0],
    }


def is_quiet_hours(now_value, quiet_start, quiet_end):
    now_time = now_value.time()
    if quiet_start == quiet_end:
        return False
    if quiet_start < quiet_end:
        return quiet_start <= now_time < quiet_end
    return now_time >= quiet_start or now_time < quiet_end


def _alert_due(now_value, target_value, window_minutes):
    delta_minutes = (now_value - target_value).total_seconds() / 60.0
    return 0 <= delta_minutes < max(1, int(window_minutes))


def _task_target_datetime(task_item):
    due_date = task_item.get("due_date")
    if not isinstance(due_date, date):
        return None
    if task_item.get("scheduled_date") == due_date and isinstance(task_item.get("scheduled_time"), time):
        return datetime.combine(due_date, task_item["scheduled_time"])
    return datetime.combine(due_date, time(17, 0))


def _build_open_button(app_url):
    if not app_url:
        return None
    return {"inline_keyboard": [[{"text": "Open DayAnchor", "url": app_url}]]}


def collect_due_alerts(
    tasks,
    reminders,
    morning_checkins,
    nightly_reflections,
    history,
    config,
    now_value=None,
):
    now_local = now_value or datetime.now(ZoneInfo(config.get("timezone") or "America/Denver"))
    window_minutes = int(config.get("alert_window_minutes") or 5)
    app_url = config.get("app_url")

    sent_history = dict(history or {})
    alerts = []

    active_tasks = [item for item in tasks if item.get("status") != "completed"]

    for task in active_tasks:
        sched_date = task.get("scheduled_date")
        sched_time = task.get("scheduled_time")
        if not isinstance(sched_date, date) or not isinstance(sched_time, time):
            continue

        start_dt = datetime.combine(sched_date, sched_time)
        if start_dt < now_local - timedelta(hours=2):
            continue

        for offset in config.get("offset_minutes") or [60, 15, 0]:
            trigger_dt = start_dt - timedelta(minutes=int(offset))
            alert_key = f"task:{task.get('id')}:{sched_date.isoformat()}:{_time_label(sched_time)}:{int(offset)}"
            if sent_history.get(alert_key):
                continue
            if _alert_due(now_local, trigger_dt, window_minutes):
                offset_label = "starting now" if int(offset) == 0 else f"starts in {int(offset)} min"
                alerts.append(
                    {
                        "key": alert_key,
                        "critical": False,
                        "message": (
                            f"DayAnchor appointment alert\n"
                            f"{task.get('title') or 'Untitled task'}\n"
                            f"When: {sched_date.strftime('%a %b %d')} at {_time_label(sched_time)}\n"
                            f"Priority: {str(task.get('priority') or 'medium').title()}\n"
                            f"Status: {offset_label}"
                        ),
                        "reply_markup": _build_open_button(app_url),
                    }
                )

    for reminder in reminders:
        if str(reminder.get("status") or "").lower() != "active":
            continue
        remind_date = reminder.get("remind_date")
        if not isinstance(remind_date, date):
            continue
        remind_time = reminder.get("remind_time") if isinstance(reminder.get("remind_time"), time) else time(9, 0)
        due_dt = datetime.combine(remind_date, remind_time)
        if due_dt < now_local - timedelta(days=2):
            continue

        primary_key = f"reminder:{reminder.get('reminder_id')}:{remind_date.isoformat()}:primary"
        if (not sent_history.get(primary_key)) and _alert_due(now_local, due_dt, window_minutes):
            alerts.append(
                {
                    "key": primary_key,
                    "critical": False,
                    "message": (
                        f"DayAnchor reminder\n"
                        f"{reminder.get('text') or 'Reminder'}\n"
                        f"When: {remind_date.strftime('%a %b %d')} at {_time_label(remind_time)}\n"
                        f"Category: {reminder.get('category') or 'General'}"
                    ),
                    "reply_markup": _build_open_button(app_url),
                }
            )

        followup_key = f"reminder:{reminder.get('reminder_id')}:{remind_date.isoformat()}:followup"
        followup_dt = due_dt + timedelta(minutes=int(config.get("reminder_followup_minutes") or 10))
        if (not sent_history.get(followup_key)) and _alert_due(now_local, followup_dt, window_minutes):
            alerts.append(
                {
                    "key": followup_key,
                    "critical": False,
                    "message": (
                        f"DayAnchor reminder follow-up\n"
                        f"{reminder.get('text') or 'Reminder'} is still active.\n"
                        f"Use DayAnchor to dismiss or snooze."
                    ),
                    "reply_markup": _build_open_button(app_url),
                }
            )

    today_key = now_local.date().isoformat()
    morning_key = f"ritual:morning:{today_key}"
    if not sent_history.get(morning_key):
        morning_due_dt = datetime.combine(now_local.date(), config.get("morning_time") or time(6, 30))
        if _alert_due(now_local, morning_due_dt, window_minutes) and not morning_checkins.get(today_key):
            alerts.append(
                {
                    "key": morning_key,
                    "critical": False,
                    "message": "DayAnchor Morning Ritual\nStart your day check-in and set your top intention.",
                    "reply_markup": _build_open_button(app_url),
                }
            )

    review_key = f"ritual:daily_review:{today_key}"
    if not sent_history.get(review_key):
        review_due_dt = datetime.combine(now_local.date(), config.get("daily_review_time") or time(20, 30))
        if _alert_due(now_local, review_due_dt, window_minutes) and not nightly_reflections.get(today_key):
            alerts.append(
                {
                    "key": review_key,
                    "critical": False,
                    "message": "DayAnchor Daily Review\nCapture tonight's reflection and tomorrow plan.",
                    "reply_markup": _build_open_button(app_url),
                }
            )

    for task in active_tasks:
        if str(task.get("category") or "") != "Clinic":
            continue
        if str(task.get("priority") or "") != "high":
            continue
        base_dt = _task_target_datetime(task)
        if not base_dt:
            continue
        if now_local < base_dt:
            continue

        for stage_minutes in (15, 45):
            stage_key = f"clinic_escalation:{task.get('id')}:{base_dt.date().isoformat()}:{stage_minutes}"
            if sent_history.get(stage_key):
                continue
            stage_dt = base_dt + timedelta(minutes=stage_minutes)
            if _alert_due(now_local, stage_dt, window_minutes):
                alerts.append(
                    {
                        "key": stage_key,
                        "critical": True,
                        "message": (
                            f"CRITICAL clinic escalation\n"
                            f"{task.get('title') or 'Clinic task'} is still open {stage_minutes} minutes after due time.\n"
                            f"Due date: {task.get('due_date') or 'Unknown'}"
                        ),
                        "reply_markup": _build_open_button(app_url),
                    }
                )

    return alerts


def prune_alert_history(history, now_value=None, max_entries=2000, max_age_days=45):
    now_local = now_value or datetime.now(MOUNTAIN_TIMEZONE)
    entries = []
    for key, sent_at in (history or {}).items():
        sent_dt = _parse_iso_datetime(sent_at)
        if sent_dt is None:
            continue
        if now_local - sent_dt > timedelta(days=max_age_days):
            continue
        entries.append((key, sent_dt))

    entries.sort(key=lambda item: item[1], reverse=True)
    trimmed = entries[: max(50, int(max_entries))]
    return {key: value.isoformat(timespec="seconds") for key, value in trimmed}


def send_telegram_message(config, message, reply_markup=None):
    token = str(config.get("bot_token") or "").strip()
    chat_id = str(config.get("chat_id") or "").strip()
    if not token or not chat_id:
        return False, "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID."

    payload = {
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    req = request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=10) as response:
            body = response.read().decode("utf-8", errors="replace")
        if '"ok":true' not in body:
            return False, f"Telegram API error: {body[:220]}"
        return True, ""
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else str(exc)
        return False, f"HTTP {exc.code}: {body[:220]}"
    except Exception as exc:
        return False, str(exc)
