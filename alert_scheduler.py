"""Periodic Telegram alert worker for deployments where Streamlit is idle."""

import json
import os
import signal
import time as time_module
from calendar import monthrange
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

import autopilot
import notification_identity
import staging_database
import settings_persistence
import telegram_alerts
import telegram_inbox


STOP_REQUESTED = False


def _normalize_database_url(raw_url):
    return str(raw_url or "").strip()


def _ensure_sslmode(url):
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault("sslmode", "require")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def database_url_candidates(environ=None):
    if staging_database.is_staging(environ):
        return [("DATABASE_URL", staging_database.staging_candidate(environ))]
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
        for key in ("due_date", "scheduled_date", "scheduled_end_date"):
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
        reminder["notification_source_id"] = notification_identity.notification_source_id(item, "reminder_id")
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
                        "notification_source_id": notification_identity.notification_source_id(raw_item, "item_id"),
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
            SELECT id, title, category, priority, status, due_date, scheduled_date,
                   scheduled_end_date, scheduled_time
            FROM tasks
            WHERE status <> 'completed'
            """
        )
        tasks = _normalize_tasks(cursor.fetchall())
        cursor.execute("SELECT payload FROM app_settings WHERE id = 1")
        row = cursor.fetchone()
        settings = json.loads(row["payload"] or "{}") if row and row.get("payload") else {}
    return tasks, settings


class SchedulerSettingsPersistenceError(RuntimeError):
    """Raised when scheduler operational state cannot be safely persisted."""


def _utc_datetime(value):
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _merge_alert_history(current, baseline, desired):
    if not all(isinstance(value, dict) for value in (current, baseline, desired)):
        raise ValueError("Telegram alert history must be a mapping.")

    merged = dict(current)
    missing = object()
    for key in set(baseline) | set(desired):
        before = baseline.get(key, missing)
        after = desired.get(key, missing)
        if before == after:
            continue
        present = merged.get(key, missing)
        if after is missing:
            if present == before:
                merged.pop(key, None)
        elif present is missing or present == before or present == after:
            merged[key] = after
    return merged


def _merge_pending_notifications(current, baseline, desired):
    if not all(
        values is None or isinstance(values, list)
        for values in (current, baseline, desired)
    ):
        raise ValueError("Pending autopilot notifications must be lists.")
    current = list(current or [])
    baseline = list(baseline or [])
    desired = list(desired or [])

    remaining = list(current)
    removed = {}
    for value in baseline:
        removed[value] = removed.get(value, 0) + 1
    for value in desired:
        if removed.get(value, 0):
            removed[value] -= 1
    for value, count in removed.items():
        for _ in range(count):
            if value in remaining:
                remaining.remove(value)

    additions = {}
    for value in desired:
        additions[value] = additions.get(value, 0) + 1
    for value in baseline:
        if additions.get(value, 0):
            additions[value] -= 1
    for value, count in additions.items():
        if count and value not in remaining:
            remaining.extend([value] * count)
    return remaining


def _merge_operational_settings(current, baseline, desired):
    """Apply only scheduler-owned deltas to a newly read durable payload."""
    if not all(isinstance(value, dict) for value in (current, baseline, desired)):
        raise ValueError("Scheduler settings snapshots must be mappings.")

    merged = dict(current)
    for key in ("telegram_alert_history",):
        before = baseline.get(key, {})
        after = desired.get(key, before)
        if after != before:
            merged[key] = _merge_alert_history(merged.get(key, {}), before, after)

    before_pending = baseline.get("autopilot_pending_notify", [])
    after_pending = desired.get("autopilot_pending_notify", before_pending)
    if after_pending != before_pending:
        merged["autopilot_pending_notify"] = _merge_pending_notifications(
            merged.get("autopilot_pending_notify", []),
            before_pending,
            after_pending,
        )

    before_offset = baseline.get("telegram_update_offset")
    after_offset = desired.get("telegram_update_offset", before_offset)
    if after_offset != before_offset:
        try:
            parsed_after = int(after_offset)
            parsed_current = int(merged.get("telegram_update_offset"))
        except (TypeError, ValueError):
            if merged.get("telegram_update_offset", before_offset) == before_offset:
                merged["telegram_update_offset"] = after_offset
        else:
            merged["telegram_update_offset"] = max(parsed_after, parsed_current)

    before_checked = baseline.get("telegram_last_checked_at")
    after_checked = desired.get("telegram_last_checked_at", before_checked)
    if after_checked != before_checked:
        current_checked = merged.get("telegram_last_checked_at", before_checked)
        if current_checked == before_checked:
            merged["telegram_last_checked_at"] = after_checked
        else:
            current_dt = _utc_datetime(current_checked)
            desired_dt = _utc_datetime(after_checked)
            if current_dt is not None and desired_dt is not None and desired_dt > current_dt:
                merged["telegram_last_checked_at"] = after_checked
    return merged


def save_alert_state(connection, settings, baseline_settings, max_attempts=3):
    """CAS-merge scheduler-owned deltas without replacing unrelated user settings."""
    if type(max_attempts) is not int or not 1 <= max_attempts <= 5:
        raise ValueError("max_attempts must be between 1 and 5.")
    if not isinstance(baseline_settings, dict) or not isinstance(settings, dict):
        raise ValueError("Scheduler settings and baseline must be mappings.")
    if connection is None or connection.closed:
        raise SchedulerSettingsPersistenceError(
            "Scheduler state could not be saved because the database connection is unavailable."
        )

    try:
        for _ in range(max_attempts):
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute("SELECT payload FROM app_settings WHERE id = 1")
                row = cursor.fetchone()
                current_payload = row["payload"] if row else None
                if current_payload:
                    current_settings = json.loads(current_payload)
                    if not isinstance(current_settings, dict):
                        raise ValueError("Durable app settings payload must be a JSON object.")
                else:
                    current_settings = {}

                merged = _merge_operational_settings(
                    current_settings,
                    baseline_settings,
                    settings,
                )
                payload_text = json.dumps(merged, default=str)
                if settings_persistence.compare_and_swap_payload(
                    cursor,
                    payload_text,
                    current_payload,
                ):
                    return
        connection.rollback()
        raise SchedulerSettingsPersistenceError(
            "Scheduler state was not saved because app settings kept changing."
        )
    except SchedulerSettingsPersistenceError:
        raise
    except (psycopg.Error, TypeError, ValueError, json.JSONDecodeError, AttributeError) as exc:
        connection.rollback()
        raise SchedulerSettingsPersistenceError(
            "Scheduler state could not be safely persisted."
        ) from exc


def run_autopilot_rollover(connection, tasks, settings, now_local, notify=False):
    """Roll undone scheduled tasks to today and log it. Never raises; returns the count moved.

    With notify=True the event is queued in settings for a Telegram message with an Undo button.
    """
    config = autopilot.autopilot_config(settings)
    if not config["rollover_enabled"]:
        return 0
    try:
        changes = autopilot.plan_rollover(tasks, now_local, config)
        if not changes:
            return 0
        autopilot.ensure_ai_events_table(connection)
        applied = autopilot.apply_rollover(connection, changes)
        event_id = autopilot.log_rollover(connection, now_local.date(), applied)
        connection.commit()
    except psycopg.Error as exc:
        connection.rollback()
        print(f"Autopilot rollover failed: {exc}", flush=True)
        return 0

    if notify and event_id is not None:
        settings["autopilot_pending_notify"] = list(settings.get("autopilot_pending_notify") or []) + [event_id]
    applied_by_id = {change["task_id"]: change for change in applied}
    for task in tasks:
        change = applied_by_id.get(task.get("id"))
        if change:
            task["scheduled_date"] = change["after"]["scheduled_date"]
            task["scheduled_time"] = change["after"]["scheduled_time"]
            task["scheduled_end_date"] = None
    return len(applied)


def run_once(connection=None):
    if connection is not None and staging_database.is_staging():
        raise staging_database.StagingDatabaseError(staging_database.ERROR)
    owns_connection = connection is None
    if owns_connection:
        candidates = database_url_candidates()
        if not candidates:
            return {"skipped": True, "reason": "No database URL configured."}
        connection = staging_database.connect(candidates[0][1], psycopg.connect)

    try:
        tasks, settings = load_state(connection)
        baseline_settings = deepcopy(settings)
        config = telegram_alerts.build_telegram_config(settings, os.environ)

        try:
            timezone = ZoneInfo(config.get("timezone") or "America/Denver")
        except (KeyError, TypeError, ValueError):
            timezone = ZoneInfo("America/Denver")
        now_local = datetime.now(timezone)

        rolled_over = run_autopilot_rollover(connection, tasks, settings, now_local, notify=bool(config.get("enabled")))

        if not config.get("enabled"):
            return {"skipped": True, "reason": "Telegram alerts disabled.", "rolled_over": rolled_over}

        inbox_result = {"handled": 0, "ignored": 0, "error": ""}
        if config.get("bot_token") and config.get("chat_id"):
            inbox_result = telegram_inbox.process_updates(connection, config, settings, now_local)
            if inbox_result["error"]:
                print(f"Telegram inbox: {inbox_result['error']}", flush=True)
            telegram_inbox.flush_pending_notifications(connection, config, settings, now_local)
            # Replies may have moved tasks, so alerts should see current data.
            tasks, _ = load_state(connection)

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
            app_settings=settings,
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
                if alert.get("autopilot_kind"):
                    autopilot.log_event(
                        connection, now_local.date(), "autopilot", "insight", "info",
                        f"Sent {alert['autopilot_kind'].replace('_', ' ')} via Telegram",
                    )
            else:
                failed.append(error_text or "Unknown Telegram error")

        settings["telegram_alert_history"] = telegram_alerts.prune_alert_history(history, now_value=now_local)
        settings["telegram_last_checked_at"] = now_local.isoformat(timespec="seconds")
        save_alert_state(connection, settings, baseline_settings)
        connection.commit()
        return {
            "skipped": False,
            "sent_count": sent_count,
            "queued_count": len(alerts),
            "failed": failed,
            "rolled_over": rolled_over,
            "replies_handled": inbox_result["handled"],
        }
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