from datetime import datetime
from zoneinfo import ZoneInfo

import telegram_inbox

TZ = ZoneInfo("America/Denver")
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=TZ)
CHAT = "555"


def _callback(data, chat="555", update_id=1):
    return {
        "update_id": update_id,
        "callback_query": {"id": "cb1", "data": data, "message": {"message_id": 9, "chat": {"id": int(chat)}}},
    }


def _text(text, chat="555", date=None, update_id=1):
    return {
        "update_id": update_id,
        "message": {"text": text, "chat": {"id": int(chat)}, "date": date or int(NOW.timestamp())},
    }


def test_parse_callback_actions():
    action = telegram_inbox.parse_update(_callback("da:undo:42"), CHAT)
    assert action["action"] == "undo" and action["event_id"] == 42 and action["message_id"] == 9
    assert telegram_inbox.parse_update(_callback("da:apply:7"), CHAT)["action"] == "apply"
    assert telegram_inbox.parse_update(_callback("da:dismiss:7"), CHAT)["action"] == "dismiss"


def test_parse_rejects_other_chats_bad_data_and_missing_chat_id():
    assert telegram_inbox.parse_update(_callback("da:undo:42", chat="999"), CHAT) is None
    assert telegram_inbox.parse_update(_text("undo", chat="999"), CHAT) is None
    assert telegram_inbox.parse_update(_callback("da:delete:42"), CHAT) is None
    assert telegram_inbox.parse_update(_callback("da:undo:abc"), CHAT) is None
    assert telegram_inbox.parse_update(_callback("da:undo:42"), "") is None


def test_text_commands_limited_to_fresh_undo():
    now_ts = int(NOW.timestamp())
    assert telegram_inbox.parse_update(_text("Undo"), CHAT, now_ts)["action"] == "undo_latest"
    assert telegram_inbox.parse_update(_text("/undo"), CHAT, now_ts)["action"] == "undo_latest"
    assert telegram_inbox.parse_update(_text("delete everything"), CHAT, now_ts) is None
    assert telegram_inbox.parse_update(_text("undo", date=now_ts - 3600), CHAT, now_ts) is None


def test_markup_uses_prefixed_callback_data_within_telegram_limit():
    buttons = telegram_inbox.proposal_markup(123456789)["inline_keyboard"][0]
    assert [b["callback_data"] for b in buttons] == ["da:apply:123456789", "da:dismiss:123456789"]
    assert all(len(b["callback_data"]) <= 64 for b in buttons)
    assert telegram_inbox.undo_markup(5)["inline_keyboard"][0][0]["callback_data"] == "da:undo:5"


class FakeConnection:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


def test_process_updates_handles_actions_advances_offset_and_acknowledges(monkeypatch):
    calls = []

    def fake_api(config, method, payload, timeout=10):
        calls.append((method, payload))
        if method == "getUpdates":
            return True, [
                _callback("da:undo:42", update_id=10),
                _callback("da:undo:43", chat="999", update_id=11),
            ]
        return True, {}

    seen = []
    monkeypatch.setattr(
        telegram_inbox, "handle_action", lambda conn, action, now: seen.append(action["event_id"]) or "Undone."
    )
    settings = {"telegram_update_offset": 9}
    connection = FakeConnection()
    result = telegram_inbox.process_updates(connection, {"chat_id": CHAT}, settings, NOW, api=fake_api)

    assert seen == [42]
    assert result["handled"] == 1 and result["ignored"] == 1
    assert settings["telegram_update_offset"] == 12
    assert calls[0][1]["offset"] == 9
    methods = [method for method, _ in calls]
    assert "answerCallbackQuery" in methods and "editMessageReplyMarkup" in methods
    assert connection.commits == 1


def test_process_updates_reports_api_error_without_touching_offset():
    settings = {"telegram_update_offset": 5}
    result = telegram_inbox.process_updates(
        FakeConnection(), {"chat_id": CHAT}, settings, NOW, api=lambda *a, **k: (False, "HTTP 409")
    )
    assert result["error"] == "HTTP 409" and settings["telegram_update_offset"] == 5
