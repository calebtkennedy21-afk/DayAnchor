"""Two-way Telegram: inline-button and text replies that resolve AI events.

Only messages from the configured chat id are honored. Every action is
idempotent: it checks the event's current status before changing anything.
"""

import re
import psycopg

import autopilot
import telegram_alerts

CALLBACK_PATTERN = re.compile(r"^da:(undo|apply|dismiss):(\d+)$")
UNDO_WORDS = {"undo", "/undo"}
MAX_UPDATES_PER_RUN = 50
MAX_TEXT_AGE_SECONDS = 600
MAX_PENDING_NOTIFICATIONS = 10


def callback_data(action, event_id):
    return f"da:{action}:{int(event_id)}"


def undo_markup(event_id):
    return {"inline_keyboard": [[{"text": "Undo", "callback_data": callback_data("undo", event_id)}]]}


def proposal_markup(event_id):
    return {
        "inline_keyboard": [
            [
                {"text": "Apply", "callback_data": callback_data("apply", event_id)},
                {"text": "Dismiss", "callback_data": callback_data("dismiss", event_id)},
            ]
        ]
    }


def parse_update(update, chat_id, now_ts=None):
    """Turn a Telegram update into an action dict, or None if it should be ignored."""
    wanted_chat = str(chat_id or "").strip()
    if not wanted_chat:
        return None

    callback = update.get("callback_query")
    if callback:
        message = callback.get("message") or {}
        if str((message.get("chat") or {}).get("id")) != wanted_chat:
            return None
        match = CALLBACK_PATTERN.match(str(callback.get("data") or ""))
        if not match:
            return None
        return {
            "kind": "callback",
            "action": match.group(1),
            "event_id": int(match.group(2)),
            "callback_id": callback.get("id"),
            "message_id": message.get("message_id"),
        }

    message = update.get("message")
    if message:
        if str((message.get("chat") or {}).get("id")) != wanted_chat:
            return None
        text = str(message.get("text") or "").strip().lower()
        if text not in UNDO_WORDS:
            return None
        sent_at = message.get("date")
        if now_ts is not None and isinstance(sent_at, int) and now_ts - sent_at > MAX_TEXT_AGE_SECONDS:
            return None
        return {"kind": "text", "action": "undo_latest"}
    return None


def _describe_undo(result):
    state, restored, skipped = result
    if state == "undone":
        extra = f" {skipped} changed since and were left alone." if skipped else ""
        return f"Undone: restored {restored} task(s).{extra}"
    if state == "already_handled":
        return "Already undone."
    if state == "not_undoable":
        return "That change can't be undone."
    return "I couldn't find that change."


def handle_action(connection, action, now_local):
    """Execute one parsed action and return a short reply."""
    kind = action["action"]
    if kind == "undo_latest":
        event_id = autopilot.latest_undoable_event_id(connection)
        if event_id is None:
            return "Nothing recent to undo."
        return _describe_undo(autopilot.undo_event(connection, event_id))
    if kind == "undo":
        return _describe_undo(autopilot.undo_event(connection, action["event_id"]))
    if kind == "apply":
        state, applied = autopilot.apply_proposal(connection, action["event_id"], now_local)
        return {
            "applied": f"Applied {applied} schedule update(s).",
            "already_handled": "Already handled.",
            "expired": "That proposal expired.",
            "unsupported": "That proposal can't be applied from Telegram.",
        }.get(state, "I couldn't find that proposal.")
    if kind == "dismiss":
        state = autopilot.dismiss_proposal(connection, action["event_id"])
        return {"dismissed": "Dismissed.", "already_handled": "Already handled."}.get(
            state, "I couldn't find that proposal."
        )
    return "Unknown action."


def process_updates(connection, config, settings, now_local, api=telegram_alerts.call_api):
    """Poll for replies, act on them, and record the next update offset in settings."""
    result = {"handled": 0, "ignored": 0, "error": ""}
    payload = {"timeout": 0, "limit": MAX_UPDATES_PER_RUN, "allowed_updates": ["message", "callback_query"]}
    try:
        offset = int(settings.get("telegram_update_offset") or 0)
    except (TypeError, ValueError):
        offset = 0
    if offset:
        payload["offset"] = offset

    ok, updates = api(config, "getUpdates", payload)
    if not ok:
        result["error"] = str(updates)
        return result

    now_ts = int(now_local.timestamp())
    for update in updates or []:
        settings["telegram_update_offset"] = int(update["update_id"]) + 1
        action = parse_update(update, config.get("chat_id"), now_ts)
        if not action:
            result["ignored"] += 1
            continue
        try:
            reply = handle_action(connection, action, now_local)
            connection.commit()
        except psycopg.Error as exc:
            connection.rollback()
            reply = "Something went wrong; nothing was changed."
            result["error"] = str(exc)[:200]
        result["handled"] += 1

        if action["kind"] == "callback":
            api(config, "answerCallbackQuery", {"callback_query_id": action["callback_id"], "text": reply[:180]})
            if action.get("message_id") is not None:
                api(
                    config,
                    "editMessageReplyMarkup",
                    {
                        "chat_id": config.get("chat_id"),
                        "message_id": action["message_id"],
                        "reply_markup": {"inline_keyboard": []},
                    },
                )
        else:
            telegram_alerts.send_telegram_message(config, reply)
    return result


def flush_pending_notifications(connection, config, settings, now_local):
    """Send queued 'autopilot moved your tasks' messages once quiet hours are over."""
    pending = [int(item) for item in (settings.get("autopilot_pending_notify") or [])]
    if not pending or telegram_alerts.is_quiet_hours(now_local, config["quiet_start"], config["quiet_end"]):
        return 0

    sent = 0
    remaining = []
    for event_id in pending[:MAX_PENDING_NOTIFICATIONS]:
        event = autopilot.load_event_summary(connection, event_id)
        if not event or event["status"] != "applied":
            continue
        titles = [
            str((item.get("data") or {}).get("title") or "Untitled task")
            for item in (autopilot._event_payload(event)).get("items", [])
        ]
        shown = "\n".join(f"- {title}" for title in titles[:8])
        extra = f"\n(+{len(titles) - 8} more)" if len(titles) > 8 else ""
        message = f"DayAnchor autopilot\nMoved {len(titles)} undone task(s) to today:\n{shown}{extra}"
        ok, _ = telegram_alerts.send_telegram_message(config, message, undo_markup(event_id))
        if ok:
            sent += 1
        else:
            remaining.append(event_id)
    settings["autopilot_pending_notify"] = remaining + pending[MAX_PENDING_NOTIFICATIONS:]
    return sent


def send_proposal_to_telegram(config, event_id, summary):
    """Send a proposal with Apply/Dismiss buttons. Returns True on success."""
    message = f"DayAnchor proposal\n{summary}\nApply it, or dismiss it?"
    ok, _ = telegram_alerts.send_telegram_message(config, message, proposal_markup(event_id))
    return ok
