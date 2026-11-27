import json
import os
from datetime import date, datetime, time, timedelta
from dataclasses import dataclass
from enum import Enum
from zoneinfo import ZoneInfo
from urllib import error, parse, request
import http.client
import re
import socket

import autopilot

MOUNTAIN_TIMEZONE = ZoneInfo("America/Denver")


class TelegramSendOutcome(str, Enum):
    CONFIRMED_SUCCESS = "confirmed_success"
    DEFINITE_TRANSIENT_FAILURE = "definite_transient_failure"
    DEFINITE_PERMANENT_FAILURE = "definite_permanent_failure"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class TelegramSendResult:
    outcome: TelegramSendOutcome
    message_id: int | None = None
    http_status: int | None = None
    telegram_error_code: int | None = None
    telegram_description: str | None = None
    retry_after: int | None = None
    error_details: str | None = None


_TOKEN_PATTERN = re.compile(r"\b\d{5,}:[A-Za-z0-9_-]{20,}\b")
_PERMANENT_TELEGRAM_ERRORS = (
    "unauthorized",
    "chat not found",
    "bot was blocked by the user",
    "user is deactivated",
    "bot can't initiate conversation",
    "bot cannot initiate conversation",
    "not enough rights to send",
    "have no rights to send",
    "can't parse entities",
    "message is too long",
)


def _safe_telegram_text(value, token="", limit=500):
    text = str(value or "")
    if token:
        text = text.replace(token, "[redacted]")
    text = _TOKEN_PATTERN.sub("[redacted]", text)
    return text[:limit]


def _telegram_error_fields(body):
    if not isinstance(body, dict):
        return None, None, None
    code = body.get("error_code")
    if type(code) is not int:
        code = None
    description = body.get("description")
    if not isinstance(description, str):
        description = None
    retry_after = None
    parameters = body.get("parameters")
    if isinstance(parameters, dict):
        value = parameters.get("retry_after")
        if type(value) is int and value >= 0:
            retry_after = value
    return code, description, retry_after


def _classify_telegram_rejection(http_status, body, token=""):
    error_code, description, retry_after = _telegram_error_fields(body)
    if isinstance(http_status, int) and 500 <= http_status < 600:
        return TelegramSendResult(
            TelegramSendOutcome.UNCERTAIN,
            http_status=http_status,
            telegram_error_code=error_code,
            telegram_description=_safe_telegram_text(description, token) if description else None,
            error_details=_safe_telegram_text(description or f"Telegram server error (HTTP {http_status}).", token),
        )
    if http_status == 429 or error_code == 429:
        return TelegramSendResult(
            TelegramSendOutcome.DEFINITE_TRANSIENT_FAILURE,
            http_status=http_status,
            telegram_error_code=error_code,
            telegram_description=_safe_telegram_text(description, token) if description else None,
            retry_after=retry_after,
            error_details=_safe_telegram_text(description or "Telegram rate limited the request.", token),
        )

    normalized_description = (description or "").lower()
    permanent = (
        error_code in {400, 401, 403}
        and any(marker in normalized_description for marker in _PERMANENT_TELEGRAM_ERRORS)
    )
    if permanent:
        return TelegramSendResult(
            TelegramSendOutcome.DEFINITE_PERMANENT_FAILURE,
            http_status=http_status,
            telegram_error_code=error_code,
            telegram_description=_safe_telegram_text(description, token),
            error_details=_safe_telegram_text(description, token),
        )

    return TelegramSendResult(
        TelegramSendOutcome.UNCERTAIN,
        http_status=http_status,
        telegram_error_code=error_code,
        telegram_description=_safe_telegram_text(description, token) if description else None,
        retry_after=retry_after,
        error_details=_safe_telegram_text(description or f"Unclassified Telegram response (HTTP {http_status}).", token),
    )


def send_telegram_message_typed(config, message, reply_markup=None, timeout=10):
    """Send via sendMessage and return structured evidence for a durable dispatcher.

    This API is intentionally separate from send_telegram_message(), whose
    legacy (success, error_text) return contract remains unchanged.
    """
    if telegram_disabled(config):
        return TelegramSendResult(
            TelegramSendOutcome.DEFINITE_TRANSIENT_FAILURE,
            error_details="Telegram is disabled by deployment configuration.",
        )
    token = str(config.get("bot_token") or "").strip()
    chat_id = str(config.get("chat_id") or "").strip()
    if not token or not chat_id:
        return TelegramSendResult(
            TelegramSendOutcome.DEFINITE_PERMANENT_FAILURE,
            error_details="Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID.",
        )

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
        with request.urlopen(req, timeout=timeout) as response:
            http_status = response.getcode()
            raw_body = response.read()
    except error.HTTPError as exc:
        http_status = exc.code
        try:
            raw_body = exc.read()
        except (OSError, http.client.HTTPException):
            raw_body = b""
        try:
            body = json.loads(raw_body.decode("utf-8", errors="replace"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = None
        return _classify_telegram_rejection(http_status, body, token)
    except (TimeoutError, socket.timeout, ConnectionError, OSError, error.URLError, http.client.HTTPException) as exc:
        detail = getattr(exc, "reason", None) or exc
        return TelegramSendResult(
            TelegramSendOutcome.UNCERTAIN,
            error_details=_safe_telegram_text(detail, token),
        )

    if not isinstance(http_status, int) or not 200 <= http_status < 300:
        return _classify_telegram_rejection(
            http_status if isinstance(http_status, int) else None,
            None,
            token,
        )

    try:
        body = json.loads(raw_body.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
        return TelegramSendResult(
            TelegramSendOutcome.UNCERTAIN,
            http_status=http_status,
            error_details="Telegram returned a malformed or incomplete JSON response.",
        )

    if not isinstance(body, dict):
        return TelegramSendResult(
            TelegramSendOutcome.UNCERTAIN,
            http_status=http_status,
            error_details="Telegram returned an unexpected response shape.",
        )
    if body.get("ok") is not True:
        return _classify_telegram_rejection(http_status, body, token)

    result = body.get("result")
    message_id = result.get("message_id") if isinstance(result, dict) else None
    if type(message_id) is not int or message_id < 1:
        return TelegramSendResult(
            TelegramSendOutcome.UNCERTAIN,
            http_status=http_status,
            error_details="Telegram success response did not contain a valid message ID.",
        )
    return TelegramSendResult(
        TelegramSendOutcome.CONFIRMED_SUCCESS,
        message_id=message_id,
        http_status=http_status,
    )


def _as_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "y", "on"):
        return True
    if text in ("0", "false", "no", "n", "off"):
        return False
    return default


def telegram_disabled(config=None, environ=None):
    env = os.environ if environ is None else environ
    return bool((config or {}).get("disabled_by_env")) or (
        "TELEGRAM_ALERTS_ENABLED" in env
        and not _as_bool(env["TELEGRAM_ALERTS_ENABLED"])
    )


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
    env = os.environ if environ is None else environ
    token = str(env.get("TELEGRAM_BOT_TOKEN") or settings.get("telegram_bot_token") or "").strip()
    chat_id = str(settings.get("telegram_chat_id") or env.get("TELEGRAM_CHAT_ID") or "").strip()
    env_enabled = _as_bool(env.get("TELEGRAM_ALERTS_ENABLED"), default=False)
    settings_enabled = _as_bool(settings.get("telegram_alerts_enabled"), default=False)
    disabled_by_env = telegram_disabled(environ=env)
    enabled = not disabled_by_env and (settings_enabled or env_enabled)
    if disabled_by_env:
        enabled_source = "disabled_by_env"
    elif settings_enabled and env_enabled:
        enabled_source = "settings+env"
    elif settings_enabled:
        enabled_source = "settings"
    elif env_enabled:
        enabled_source = "env"
    else:
        enabled_source = "none"

    return {
        "enabled": bool(enabled),
        "enabled_source": enabled_source,
        "disabled_by_env": disabled_by_env,
        "enabled_from_settings": bool(settings_enabled),
        "enabled_from_env": bool(env_enabled),
        "bot_token": token,
        "chat_id": chat_id,
        "timezone": str(settings.get("telegram_timezone") or "America/Denver").strip() or "America/Denver",
        "quiet_start": _parse_time(settings.get("telegram_quiet_hours_start"), time(21, 30)),
        "quiet_end": _parse_time(settings.get("telegram_quiet_hours_end"), time(6, 0)),
        "morning_time": _parse_time(settings.get("telegram_morning_ritual_time"), time(6, 30)),
        "daily_review_time": _parse_time(settings.get("telegram_daily_review_time"), time(20, 30)),
        "alert_window_minutes": max(1, int(settings.get("telegram_alert_window_minutes") or 5)),
        "alert_lateness_minutes": max(0, int(settings.get("telegram_alert_lateness_minutes") or 60)),
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


def _alert_due(now_value, target_value, window_minutes, lateness_minutes=0):
    delta_minutes = (now_value - target_value).total_seconds() / 60.0
    alert_window = max(1, int(window_minutes))
    alert_lateness = max(0, int(lateness_minutes))
    return -alert_window <= delta_minutes <= alert_lateness


def _task_target_datetime(task_item, tzinfo=None):
    due_date = task_item.get("due_date")
    if not isinstance(due_date, date):
        return None
    if task_item.get("scheduled_date") == due_date and isinstance(task_item.get("scheduled_time"), time):
        return datetime.combine(due_date, task_item["scheduled_time"], tzinfo=tzinfo)
    return datetime.combine(due_date, time(17, 0), tzinfo=tzinfo)


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
    family_items=None,
    now_value=None,
    app_settings=None,
):
    now_local = now_value or datetime.now(ZoneInfo(config.get("timezone") or "America/Denver"))
    local_tz = now_local.tzinfo
    window_minutes = int(config.get("alert_window_minutes") or 5)
    lateness_minutes = int(config.get("alert_lateness_minutes") or 0)
    app_url = config.get("app_url")

    sent_history = dict(history or {})
    alerts = []

    active_tasks = [item for item in tasks if item.get("status") != "completed"]

    for task in active_tasks:
        sched_date = task.get("scheduled_date")
        sched_time = task.get("scheduled_time")
        if not isinstance(sched_date, date) or not isinstance(sched_time, time):
            continue

        start_dt = datetime.combine(sched_date, sched_time, tzinfo=local_tz)
        if start_dt < now_local - timedelta(hours=2):
            continue

        for offset in config.get("offset_minutes") or [60, 15, 0]:
            trigger_dt = start_dt - timedelta(minutes=int(offset))
            alert_key = f"task:{task.get('id')}:{sched_date.isoformat()}:{_time_label(sched_time)}:{int(offset)}"
            if sent_history.get(alert_key):
                continue
            if _alert_due(now_local, trigger_dt, window_minutes, lateness_minutes):
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

    for event in family_items or []:
        if str(event.get("status") or "planned").lower() in ("canceled", "cancelled", "completed"):
            continue
        event_date = event.get("start_date")
        if not isinstance(event_date, date):
            continue
        event_time = event.get("start_time") if isinstance(event.get("start_time"), time) else time(9, 0)
        start_dt = datetime.combine(event_date, event_time, tzinfo=local_tz)
        if start_dt < now_local - timedelta(hours=2):
            continue

        for offset in config.get("offset_minutes") or [60, 15, 0]:
            trigger_dt = start_dt - timedelta(minutes=int(offset))
            event_key = f"event:{event.get('item_id')}:{event_date.isoformat()}:{_time_label(event_time)}:{int(offset)}"
            if sent_history.get(event_key):
                continue
            if _alert_due(now_local, trigger_dt, window_minutes, lateness_minutes):
                offset_label = "starting now" if int(offset) == 0 else f"starts in {int(offset)} min"
                alerts.append(
                    {
                        "key": event_key,
                        "critical": False,
                        "message": (
                            f"DayAnchor family event alert\n"
                            f"{event.get('title') or 'Family event'}\n"
                            f"When: {event_date.strftime('%a %b %d')} at {_time_label(event_time)}\n"
                            f"Type: {event.get('item_type') or 'Event'}\n"
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
        due_dt = datetime.combine(remind_date, remind_time, tzinfo=local_tz)
        if due_dt < now_local - timedelta(days=2):
            continue

        primary_key = f"reminder:{reminder.get('reminder_id')}:{remind_date.isoformat()}:primary"
        if (not sent_history.get(primary_key)) and _alert_due(now_local, due_dt, window_minutes, lateness_minutes):
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
        if (not sent_history.get(followup_key)) and _alert_due(now_local, followup_dt, window_minutes, lateness_minutes):
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
        morning_due_dt = datetime.combine(now_local.date(), config.get("morning_time") or time(6, 30), tzinfo=local_tz)
        if _alert_due(now_local, morning_due_dt, window_minutes, lateness_minutes) and not morning_checkins.get(today_key):
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
        review_due_dt = datetime.combine(now_local.date(), config.get("daily_review_time") or time(20, 30), tzinfo=local_tz)
        if _alert_due(now_local, review_due_dt, window_minutes, lateness_minutes) and not nightly_reflections.get(today_key):
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
        base_dt = _task_target_datetime(task, tzinfo=local_tz)
        if not base_dt:
            continue
        if now_local < base_dt:
            continue

        for stage_minutes in (15, 45):
            stage_key = f"clinic_escalation:{task.get('id')}:{base_dt.date().isoformat()}:{stage_minutes}"
            if sent_history.get(stage_key):
                continue
            stage_dt = base_dt + timedelta(minutes=stage_minutes)
            if _alert_due(now_local, stage_dt, window_minutes, lateness_minutes):
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

    if app_settings is not None:
        alerts.extend(autopilot.collect_reminder_alerts(tasks, app_settings, sent_history, config, now_local))

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


def call_api(config, method, payload, timeout=10):
    """Generic Telegram Bot API call. Returns (ok, result_or_error_text)."""
    if telegram_disabled(config):
        return False, "Telegram is disabled by deployment configuration."
    token = str(config.get("bot_token") or "").strip()
    if not token:
        return False, "Missing TELEGRAM_BOT_TOKEN."
    req = request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8", errors="replace"))
        if not body.get("ok"):
            return False, f"Telegram API error: {str(body)[:220]}"
        return True, body.get("result")
    except error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else str(exc)
        return False, f"HTTP {exc.code}: {text[:220]}"
    except Exception as exc:
        return False, str(exc)


def send_telegram_message(config, message, reply_markup=None):
    if telegram_disabled(config):
        return False, "Telegram is disabled by deployment configuration."
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
