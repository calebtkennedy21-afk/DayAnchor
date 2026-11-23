import ast
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import telegram_alerts
import notification_identity
from notification_identity import (
    new_notification_source_id,
    notification_source_id,
    prepare_notification_identity_backfill,
)


ROOT = Path(__file__).resolve().parents[1]
MOUNTAIN = ZoneInfo("America/Denver")


def _normalizers():
    source = ROOT / "streamlit_app.py"
    tree = ast.parse(source.read_text())
    names = {"normalize_family_schedule_items", "normalize_quick_reminders"}
    nodes = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    namespace = {
        "date": date,
        "time": time,
        "notification_identity": notification_identity,
        "parse_date_value": lambda value: date.fromisoformat(str(value)[:10]) if value else None,
        "parse_time_value": lambda value: time.fromisoformat(str(value)) if value else None,
        "safe_int": lambda value, fallback=0: int(value) if value is not None and str(value).strip() else fallback,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)
    return namespace


def _legacy_alerts(now, *, reminders=(), family_items=(), history=None):
    return telegram_alerts.collect_due_alerts(
        tasks=[],
        reminders=list(reminders),
        morning_checkins={},
        nightly_reflections={},
        history=history or {},
        config={
            "timezone": "America/Denver",
            "alert_window_minutes": 5,
            "alert_lateness_minutes": 60,
            "morning_time": time(6, 30),
            "daily_review_time": time(20, 30),
            "reminder_followup_minutes": 10,
            "offset_minutes": [60, 15, 0],
        },
        family_items=list(family_items),
        now_value=now,
    )


def test_new_reminder_and_family_identities_are_uuid_values():
    reminder = {
        "reminder_id": new_notification_source_id(),
        "notification_source_id": new_notification_source_id(),
    }
    family_item = {
        "item_id": new_notification_source_id(),
        "notification_source_id": new_notification_source_id(),
    }

    assert notification_source_id(reminder, "reminder_id") == reminder["notification_source_id"]
    assert notification_source_id(family_item, "item_id") == family_item["notification_source_id"]
    assert all(len(value) == 32 for value in (
        reminder["reminder_id"],
        reminder["notification_source_id"],
        family_item["item_id"],
        family_item["notification_source_id"],
    ))


def test_persisted_source_ids_survive_normalization_and_text_edits():
    normalizers = _normalizers()
    reminder = {
        "reminder_id": "legacy-reminder-id",
        "notification_source_id": "reminder-source-id",
        "text": "Call back",
        "remind_date": "2026-09-08",
        "remind_time": "09:15",
    }
    family_item = {
        "item_id": "legacy-family-id",
        "notification_source_id": "family-source-id",
        "title": "Soccer practice",
        "start_date": "2026-09-08",
    }

    normalized_reminder = normalizers["normalize_quick_reminders"]([reminder])[0]
    normalized_family = normalizers["normalize_family_schedule_items"]([family_item])[0]
    edited_settings, counts = prepare_notification_identity_backfill(
        {
            "quick_reminders": [{**reminder, "text": "Call the school"}],
            "family_schedule_items": [{**family_item, "title": "Practice"}],
        }
    )
    reordered_settings, reordered_counts = prepare_notification_identity_backfill(
        {
            "quick_reminders": [
                {"reminder_id": "other", "notification_source_id": "other-id", "text": "Other"},
                edited_settings["quick_reminders"][0],
            ],
            "family_schedule_items": [
                {"item_id": "other-family", "notification_source_id": "other-family-id", "title": "Other"},
                edited_settings["family_schedule_items"][0],
            ],
        }
    )

    assert normalized_reminder["notification_source_id"] == "reminder-source-id"
    assert normalized_family["notification_source_id"] == "family-source-id"
    assert counts == {"quick_reminders": 0, "family_schedule_items": 0}
    assert reordered_counts == {"quick_reminders": 0, "family_schedule_items": 0}
    assert reordered_settings["quick_reminders"][1]["notification_source_id"] == "reminder-source-id"
    assert reordered_settings["family_schedule_items"][1]["notification_source_id"] == "family-source-id"


def test_legacy_normalization_keys_are_preserved_while_backfill_adds_new_identity():
    normalizers = _normalizers()
    settings = {
        "quick_reminders": [{"text": "Call back", "remind_date": "2026-09-08"}],
        "family_schedule_items": [{"title": "Soccer practice", "start_date": "2026-09-08"}],
    }
    before_reminder = normalizers["normalize_quick_reminders"](settings["quick_reminders"])[0]
    before_family = normalizers["normalize_family_schedule_items"](settings["family_schedule_items"])[0]
    generated = iter(("stable-reminder-id", "stable-family-id"))
    backfilled, _ = prepare_notification_identity_backfill(settings, lambda: next(generated))
    after_reminder = normalizers["normalize_quick_reminders"](backfilled["quick_reminders"])[0]
    after_family = normalizers["normalize_family_schedule_items"](backfilled["family_schedule_items"])[0]

    assert before_reminder["notification_source_id"] is None
    assert before_family["notification_source_id"] is None
    assert after_reminder["notification_source_id"] == "stable-reminder-id"
    assert after_family["notification_source_id"] == "stable-family-id"
    assert after_reminder["reminder_id"] == before_reminder["reminder_id"]
    assert after_family["item_id"] == before_family["item_id"]


def test_backfill_is_idempotent_and_does_not_change_legacy_identity_fields():
    calls = iter(("reminder-backfill-id", "family-backfill-id"))
    original = {
        "quick_reminders": [{"text": "Call back"}],
        "family_schedule_items": [{"title": "Soccer practice", "start_date": "2026-09-08"}],
    }
    backfilled, counts = prepare_notification_identity_backfill(original, lambda: next(calls))
    repeated, repeated_counts = prepare_notification_identity_backfill(backfilled, lambda: "unexpected")

    assert counts == {"quick_reminders": 1, "family_schedule_items": 1}
    assert repeated_counts == {"quick_reminders": 0, "family_schedule_items": 0}
    assert repeated == backfilled
    assert "reminder_id" not in backfilled["quick_reminders"][0]
    assert "item_id" not in backfilled["family_schedule_items"][0]
    edited, edited_counts = prepare_notification_identity_backfill(
        {
            "quick_reminders": [{**backfilled["quick_reminders"][0], "text": "Updated reminder"}],
            "family_schedule_items": [{**backfilled["family_schedule_items"][0], "title": "Updated event"}],
        },
        lambda: "unexpected",
    )
    assert edited_counts == {"quick_reminders": 0, "family_schedule_items": 0}
    assert edited["quick_reminders"][0]["notification_source_id"] == "reminder-backfill-id"
    assert edited["family_schedule_items"][0]["notification_source_id"] == "family-backfill-id"


def test_backfill_uses_existing_durable_ids_without_rewriting_legacy_keys():
    original = {
        "quick_reminders": [{"reminder_id": "reminder-7", "text": "Call back"}],
        "family_schedule_items": [{"item_id": "family-9", "title": "Practice"}],
    }
    backfilled, counts = prepare_notification_identity_backfill(original)

    assert backfilled["quick_reminders"][0]["notification_source_id"] == "reminder-7"
    assert backfilled["family_schedule_items"][0]["notification_source_id"] == "family-9"
    assert backfilled["quick_reminders"][0]["reminder_id"] == "reminder-7"
    assert backfilled["family_schedule_items"][0]["item_id"] == "family-9"
    assert counts == {"quick_reminders": 1, "family_schedule_items": 1}


def test_legacy_alert_history_keys_remain_compatible_after_identity_backfill():
    now = datetime(2026, 9, 8, 9, 2, tzinfo=MOUNTAIN)
    reminder = {
        "reminder_id": "quick_reminder_0_call_back",
        "notification_source_id": "new-reminder-source",
        "text": "Call back",
        "status": "active",
        "remind_date": date(2026, 9, 8),
        "remind_time": time(9, 0),
    }
    family_item = {
        "item_id": "family_0_2026-09-08_soccer_practice",
        "notification_source_id": "new-family-source",
        "title": "Soccer practice",
        "status": "planned",
        "start_date": date(2026, 9, 8),
        "start_time": time(9, 0),
    }
    history = {
        "reminder:quick_reminder_0_call_back:2026-09-08:primary": "2026-09-08T09:00:00-06:00",
        "event:family_0_2026-09-08_soccer_practice:2026-09-08:9:00 AM:0": "2026-09-08T09:00:00-06:00",
    }

    alerts = _legacy_alerts(now, reminders=[reminder], family_items=[family_item], history=history)

    assert not any(alert["key"] in history for alert in alerts)


def test_identity_processing_does_not_send_notifications(monkeypatch):
    sends = []
    monkeypatch.setattr(telegram_alerts, "send_telegram_message", lambda *args, **kwargs: sends.append(args))
    prepare_notification_identity_backfill(
        {"quick_reminders": [{"text": "Call back"}], "family_schedule_items": [{"title": "Practice"}]}
    )

    assert sends == []
