"""Periodic Telegram alert worker for deployments where Streamlit is idle."""

import json
import os
import signal
import time as time_module
from calendar import monthrange
from datetime import date, datetime, time, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

import telegram_alerts


STOP_REQUESTED = False


def _normalize_database_url(raw_url):
    return str(raw_url or "").strip()


def _ensure_sslmode(url):
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault("sslmode", "require")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def database_url_candidates(environ=None):
    env = environ or os.environ
    candidates = []
    for name in ("DATABASE_URL", "DATABASE_PUBLIC_URL"):
        value = _normalize_database_url(env.get(name))
        if value:
            candidates.append((name, _ensure_sslmode(value)))
    if candidates:
        return candidates

    parts = {
        "host": env.get("PGHOST"),
        "port": env.get("PGPORT"),
        "user": env.get("PGUSER"),
        "password": env.get("PGPASSWORD"),
        "dbname": env.get("PGDATABASE"),
    }
    if all(parts.values()):
        dsn = "host={host} port={port} user={user} password={password} dbname={dbname}".format(**parts)
        return [("PG_*", dsn)]
    return []


def _parse_date(value):
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "").strip())
    except ValueError:
        return None


def _parse_time(value):
    if isinstance(value, time):
        return value
    text = str(value or "").strip()
    if len(text) == 5 and text[2] == ":":
        text = f"{text}:00"
    try:
        return time.fromisoformat(text) if text else None
    except ValueError:
        return None


def _normalize_tasks(rows):
    tasks = []
    for row in rows:
        item = dict(row)
        for key in ("due_date", "scheduled_date"):
            item[key] = _parse_date(item.get(key))
        item["scheduled_time"] = _parse_time(item.get("scheduled_time"))
        tasks.append(item)
    return tasks


def _normalize_reminders(raw_reminders):
    normalized = []
    for item in raw_reminders if isinstance(raw_reminders, list) else []:
        if not isinstance(item, dict):
            continue
        reminder = dict(item)
        reminder["remind_date"] = _parse_date(reminder.get("remind_date") or reminder.get("due_date"))
        reminder["remind_time"] = _parse_time(reminder.get("remind_time") or reminder.get("due_time"))
        normalized.append(reminder)
    return normalized


def _expand_family_items(raw_items, anchor_day, window_days=3):
    expanded = []
    window_end = anchor_day + timedelta(days=max(1, window_days) - 1)
    for index, raw_item in enumerate(raw_items if isinstance(raw_items, list) else []):
        if not isinstance(raw_item, dict):
            continue
        title = str(raw_item.get("title") or "").strip()
        start_date = _parse_date(raw_item.get("start_date") or raw_item.get("date"))
        if not title or not start_date:
            continue
        end_date = _parse_date(raw_item.get("end_date")) or start_date
        span_days = max(0, (end_date - start_date).days)
        recurrence = str(raw_item.get("recurrence_rule") or "none").lower()
        interval = max(1, int(raw_item.get("recurrence_interval") or 1))
        recurrence_end = _parse_date(raw_item.get("recurrence_end_date"))
        current = start_date
        if recurrence in ("daily", "weekly") and current < anchor_day:
            step = interval if recurrence == "daily" else interval * 7
            current += timedelta(days=((anchor_day - current).days // step) * step)

        for occurrence_index in range(180):
            current_end = current + timedelta(days=span_days)
            if recurrence_end and current > recurrence_end:
                break
            if current_end >= anchor_day and current <= window_end:
                expanded.append(
                    {
                        "item_id": str(raw_item.get("item_id") or f"family_{index}_{start_date.isoformat()}_{title}"),
                        "title": title,
                        "item_type": str(raw_item.get("item_type") or raw_item.get("category") or "Appointment"),
                        "status": str(raw_item.get("status") or "planned"),
                        "start_date": current,
                        "start_time": _parse_time(raw_item.get("start_time")),
                        "occurrence_index": occurrence_index,
                    }
                )
            if recurrence == "none":
                break
            if recurrence == "daily":
                current += timedelta(days=interval)
            elif recurrence == "weekly":
                current += timedelta(days=interval * 7)
            elif recurrence == "monthly":
                month_index = current.year * 12 + current.month - 1 + interval
                year, month_zero = divmod(month_index, 12)
                month = month_zero + 1
                current = current.replace(year=year, month=month, day=min(current.day, monthrange(year, month)[1]))
            elif recurrence == "yearly":
                current = current.replace(year=current.year + interval, day=min(current.day, monthrange(current.year + interval, current.month)[1]))
            else:
                break
    return expanded


def load_state(connection):
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT id, title, category, priority, status, due_date, scheduled_date, scheduled_time
            FROM tasks
            WHERE status <> 'completed'
            """
        )
        tasks = _normalize_tasks(cursor.fetchall())
        cursor.execute("SELECT payload FROM app_settings WHERE id = 1")
        row = cursor.fetchone()
        settings = json.loads(row["payload"] or "{}") if row and row.get("payload") else {}
    return tasks, settings


def save_alert_state(connection, settings):
    payload = json.dumps(settings, default=str)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO app_settings (id, payload, updated_at)
            VALUES (1, %s, NOW())
            ON CONFLICT (id)
            DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW()
            """,
            (payload,),
        )


def run_once(connection=None):
    owns_connection = connection is None
    if owns_connection:
        candidates = database_url_candidates()
        if not candidates:
            return {"skipped": True, "reason": "No database URL configured."}
        connection = psycopg.connect(candidates[0][1])

    try:
        tasks, settings = load_state(connection)
        config = telegram_alerts.build_telegram_config(settings, os.environ)
        if not config.get("enabled"):
            return {"skipped": True, "reason": "Telegram alerts disabled."}

        try:
            timezone = ZoneInfo(config.get("timezone") or "America/Denver")
        except (KeyError, TypeError, ValueError):
            timezone = ZoneInfo("America/Denver")
        now_local = datetime.now(timezone)
        history = telegram_alerts.prune_alert_history(settings.get("telegram_alert_history") or {}, now_value=now_local)
        family_items = _expand_family_items(settings.get("family_schedule_items") or [], now_local.date())
        alerts = telegram_alerts.collect_due_alerts(
            tasks,
            _normalize_reminders(settings.get("quick_reminders") or []),
            settings.get("morning_ritual_checkins") or {},
            settings.get("nightly_reflections") or {},
            history,
            config,
            family_items=family_items,
            now_value=now_local,
        )

        sent_count = 0
        failed = []
        for alert in alerts:
            if telegram_alerts.is_quiet_hours(now_local, config["quiet_start"], config["quiet_end"]) and not alert.get("critical"):
                continue
            ok, error_text = telegram_alerts.send_telegram_message(config, alert["message"], alert.get("reply_markup"))
            if ok:
                history[alert["key"]] = now_local.isoformat(timespec="seconds")
                sent_count += 1
            else:
                failed.append(error_text or "Unknown Telegram error")

        settings["telegram_alert_history"] = telegram_alerts.prune_alert_history(history, now_value=now_local)
        settings["telegram_last_checked_at"] = now_local.isoformat(timespec="seconds")
        save_alert_state(connection, settings)
        connection.commit()
        return {"skipped": False, "sent_count": sent_count, "queued_count": len(alerts), "failed": failed}
    finally:
        if owns_connection:
            connection.close()


def _stop_handler(signum, frame):
    del signum, frame
    global STOP_REQUESTED
    STOP_REQUESTED = True


def run_forever(interval_seconds=60):
    signal.signal(signal.SIGTERM, _stop_handler)
    signal.signal(signal.SIGINT, _stop_handler)
    while not STOP_REQUESTED:
        try:
            result = run_once()
            if result.get("failed"):
                print(f"Alert scheduler: {result['failed']}", flush=True)
        except Exception as exc:
            print(f"Alert scheduler check failed: {exc}", flush=True)
        stop_at = datetime.now().timestamp() + max(15, int(interval_seconds))
        while not STOP_REQUESTED and datetime.now().timestamp() < stop_at:
            time_module.sleep(min(5, max(0, stop_at - datetime.now().timestamp())))


if __name__ == "__main__":
    if str(os.getenv("ALERT_SCHEDULER_ENABLED", "true")).lower() not in ("1", "true", "yes", "on"):
        print("Alert scheduler disabled.", flush=True)
    else:
        run_forever(int(os.getenv("ALERT_SCHEDULER_INTERVAL_SECONDS", "60")))