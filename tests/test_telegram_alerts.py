import io
import json
from datetime import date, datetime, time
from urllib import error
from zoneinfo import ZoneInfo

import pytest

import telegram_alerts
from telegram_alerts import (
    TelegramSendOutcome,
    build_telegram_config,
    collect_due_alerts,
    send_telegram_message,
    send_telegram_message_typed,
)


MOUNTAIN = ZoneInfo("America/Denver")
TELEGRAM_CONFIG = {
    "bot_token": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
    "chat_id": "987654321",
}


class FakeResponse:
    def __init__(self, body, status=200):
        self.body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body

    def getcode(self):
        return self.status


def _http_error(status, body):
    return error.HTTPError(
        "https://api.telegram.org/bot123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi/sendMessage",
        status,
        "Telegram error",
        {},
        io.BytesIO(json.dumps(body).encode()),
    )


def _typed_send(monkeypatch, response=None, exception=None):
    def fake_urlopen(*args, **kwargs):
        if exception:
            raise exception
        return response

    monkeypatch.setattr(telegram_alerts.request, "urlopen", fake_urlopen)
    return send_telegram_message_typed(TELEGRAM_CONFIG, "Test notification")


def test_typed_send_confirms_success_and_returns_message_id(monkeypatch):
    result = _typed_send(
        monkeypatch,
        FakeResponse(json.dumps({"ok": True, "result": {"message_id": 45}}).encode()),
    )

    assert result.outcome is TelegramSendOutcome.CONFIRMED_SUCCESS
    assert result.message_id == 45
    assert result.http_status == 200
    assert result.error_details is None


@pytest.mark.parametrize("result", [{}, {"message_id": 0}, {"message_id": -1}, {"message_id": True}])
def test_typed_send_missing_or_invalid_message_id_is_uncertain(monkeypatch, result):
    response = FakeResponse(json.dumps({"ok": True, "result": result}).encode())

    assert _typed_send(monkeypatch, response).outcome is TelegramSendOutcome.UNCERTAIN


def test_typed_send_rate_limit_returns_retry_after(monkeypatch):
    response = _http_error(
        429,
        {
            "ok": False,
            "error_code": 429,
            "description": "Too Many Requests",
            "parameters": {"retry_after": 17},
        },
    )
    result = _typed_send(monkeypatch, exception=response)

    assert result.outcome is TelegramSendOutcome.DEFINITE_TRANSIENT_FAILURE
    assert result.http_status == 429
    assert result.telegram_error_code == 429
    assert result.retry_after == 17


def test_typed_send_explicit_rate_limit_envelope_is_transient(monkeypatch):
    body = {"ok": False, "error_code": 429, "description": "Too Many Requests"}
    result = _typed_send(monkeypatch, FakeResponse(json.dumps(body).encode()))

    assert result.outcome is TelegramSendOutcome.DEFINITE_TRANSIENT_FAILURE
    assert result.retry_after is None


def test_typed_send_explicit_permanent_destination_rejection(monkeypatch):
    body = {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}
    result = _typed_send(monkeypatch, exception=_http_error(400, body))

    assert result.outcome is TelegramSendOutcome.DEFINITE_PERMANENT_FAILURE
    assert result.telegram_error_code == 400
    assert result.telegram_description == body["description"]


def test_typed_send_unclassified_client_rejection_is_uncertain(monkeypatch):
    result = _typed_send(monkeypatch, exception=_http_error(400, {"ok": False, "error_code": 400}))

    assert result.outcome is TelegramSendOutcome.UNCERTAIN


@pytest.mark.parametrize(
    "exception",
    [
        TimeoutError("request timed out"),
        error.URLError(ConnectionResetError("connection reset")),
    ],
)
def test_typed_send_network_failures_are_uncertain(monkeypatch, exception):
    assert _typed_send(monkeypatch, exception=exception).outcome is TelegramSendOutcome.UNCERTAIN


def test_typed_send_malformed_json_is_uncertain(monkeypatch):
    result = _typed_send(monkeypatch, FakeResponse(b"{invalid"))

    assert result.outcome is TelegramSendOutcome.UNCERTAIN
    assert result.error_details == "Telegram returned a malformed or incomplete JSON response."


def test_typed_send_server_error_is_uncertain(monkeypatch):
    result = _typed_send(monkeypatch, exception=_http_error(503, {"ok": False, "description": "Unavailable"}))

    assert result.outcome is TelegramSendOutcome.UNCERTAIN
    assert result.http_status == 503


@pytest.mark.parametrize("body", [[], {"ok": True, "result": []}, {"ok": "true", "result": {"message_id": 3}}])
def test_typed_send_unexpected_response_shapes_are_uncertain(monkeypatch, body):
    result = _typed_send(monkeypatch, FakeResponse(json.dumps(body).encode()))

    assert result.outcome is TelegramSendOutcome.UNCERTAIN


def test_typed_send_redacts_token_from_transport_diagnostics(monkeypatch):
    token = TELEGRAM_CONFIG["bot_token"]
    result = _typed_send(monkeypatch, exception=error.URLError(f"failed URL contained {token}"))

    assert result.outcome is TelegramSendOutcome.UNCERTAIN
    assert token not in (result.error_details or "")
    assert "[redacted]" in result.error_details


def test_typed_send_redacts_token_from_telegram_error_details(monkeypatch):
    token = TELEGRAM_CONFIG["bot_token"]
    body = {"ok": False, "error_code": 400, "description": f"Bad Request: invalid {token}"}
    result = _typed_send(monkeypatch, exception=_http_error(400, body))

    assert token not in (result.telegram_description or "")
    assert token not in (result.error_details or "")


def test_typed_send_redacts_token_from_rate_limit_details(monkeypatch):
    token = TELEGRAM_CONFIG["bot_token"]
    body = {
        "ok": False,
        "error_code": 429,
        "description": f"Too Many Requests: {token}",
        "parameters": {"retry_after": 3},
    }
    result = _typed_send(monkeypatch, exception=_http_error(429, body))

    assert result.outcome is TelegramSendOutcome.DEFINITE_TRANSIENT_FAILURE
    assert token not in (result.telegram_description or "")
    assert token not in (result.error_details or "")


def test_legacy_send_message_return_contract_is_unchanged(monkeypatch):
    monkeypatch.setattr(
        telegram_alerts.request,
        "urlopen",
        lambda *args, **kwargs: FakeResponse(b'{"ok":true,"result":{"message_id":45}}'),
    )

    assert send_telegram_message(TELEGRAM_CONFIG, "Legacy caller") == (True, "")


@pytest.mark.parametrize("disabled_value", ["false", "0", "no", "off", "", "invalid"])
def test_telegram_disable_flag_overrides_saved_settings(disabled_value):
    config = build_telegram_config(
        {"telegram_alerts_enabled": True, "telegram_bot_token": "saved-token", "telegram_chat_id": "123"},
        {"TELEGRAM_ALERTS_ENABLED": disabled_value},
    )

    assert config["enabled"] is False
    assert config["disabled_by_env"] is True
    assert config["enabled_source"] == "disabled_by_env"
    assert config["bot_token"] == "saved-token"
    assert config["chat_id"] == "123"


def test_telegram_enable_behavior_is_preserved_when_flag_is_unset_or_true():
    settings = {"telegram_alerts_enabled": True}
    assert build_telegram_config(settings, {})["enabled"] is True
    assert build_telegram_config({}, {"TELEGRAM_ALERTS_ENABLED": "true"})["enabled"] is True
    assert build_telegram_config({}, {})["enabled"] is False


def test_disabled_telegram_blocks_all_network_entry_points(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALERTS_ENABLED", "false")

    def unexpected_network(*args, **kwargs):
        pytest.fail("Disabled Telegram must not make network calls")

    monkeypatch.setattr(telegram_alerts.request, "urlopen", unexpected_network)
    config = {**TELEGRAM_CONFIG, "enabled": True}
    ok, error_text = telegram_alerts.call_api(config, "getUpdates", {})
    assert not ok and "disabled" in error_text
    ok, error_text = send_telegram_message(config, "Manual test")
    assert not ok and "disabled" in error_text
    typed = send_telegram_message_typed(config, "Outbox")
    assert typed.outcome is TelegramSendOutcome.DEFINITE_TRANSIENT_FAILURE
    assert "disabled" in typed.error_details


def test_explicit_disabled_config_blocks_send_without_environment(monkeypatch):
    monkeypatch.delenv("TELEGRAM_ALERTS_ENABLED", raising=False)
    config = build_telegram_config({"telegram_alerts_enabled": True}, {"TELEGRAM_ALERTS_ENABLED": "false"})
    ok, error_text = send_telegram_message(config, "Manual test")
    assert not ok and "disabled" in error_text


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
