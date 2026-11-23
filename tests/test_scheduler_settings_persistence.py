import json

import psycopg
import pytest

import alert_scheduler
import settings_persistence


class SettingsDatabase:
    def __init__(self, payload, *, after_select=None, fail=False):
        self.payload = payload
        self.after_select = after_select
        self.fail = fail
        self.write_count = 0
        self.rollback_count = 0
        self.commit_count = 0


class FakeConnection:
    def __init__(self, database):
        self.database = database
        self.closed = False

    def cursor(self, row_factory=None):
        return FakeCursor(self.database)

    def commit(self):
        self.database.commit_count += 1

    def rollback(self):
        self.database.rollback_count += 1


class FakeCursor:
    def __init__(self, database):
        self.database = database
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params=()):
        normalized = " ".join(query.split())
        self.row = None
        if normalized.startswith("SELECT payload FROM app_settings"):
            self.row = {"payload": self.database.payload} if self.database.payload is not None else None
            if self.database.after_select:
                callback = self.database.after_select
                self.database.after_select = None
                callback(self.database)
        elif normalized.startswith("UPDATE app_settings"):
            self.database.write_count += 1
            if self.database.fail:
                self.database.fail = False
                raise psycopg.OperationalError("simulated database write failure")
            payload_text, expected_payload = params
            if self.database.payload == expected_payload:
                self.database.payload = payload_text
                self.row = {"id": 1}
        elif normalized.startswith("INSERT INTO app_settings"):
            self.database.write_count += 1
            if self.database.payload is None:
                self.database.payload = params[0]
                self.row = {"id": 1}
        else:
            raise AssertionError(f"Unexpected settings SQL: {normalized}")

    def fetchone(self):
        return self.row


def _json(settings):
    return json.dumps(settings)


def _save(database, desired, baseline, **kwargs):
    return alert_scheduler.save_alert_state(
        FakeConnection(database),
        desired,
        baseline,
        **kwargs,
    )


def test_scheduler_save_against_unchanged_payload_updates_only_operational_fields():
    baseline = {
        "theme": "dark",
        "telegram_alert_history": {"old": "2026-11-20T00:00:00+00:00"},
        "telegram_update_offset": 4,
        "autopilot_pending_notify": [1],
        "telegram_last_checked_at": "2026-11-20T00:00:00+00:00",
        "unrelated_operational_key": {"keep": True},
    }
    desired = {
        **baseline,
        "telegram_alert_history": {
            "old": "2026-11-20T00:00:00+00:00",
            "new": "2026-11-22T20:00:00+00:00",
        },
        "telegram_update_offset": 5,
        "autopilot_pending_notify": [1, 2],
        "telegram_last_checked_at": "2026-11-22T20:00:00+00:00",
    }
    database = SettingsDatabase(_json(baseline))

    _save(database, desired, baseline)

    persisted = json.loads(database.payload)
    assert persisted["theme"] == "dark"
    assert persisted["unrelated_operational_key"] == {"keep": True}
    assert persisted["telegram_alert_history"] == desired["telegram_alert_history"]
    assert persisted["telegram_update_offset"] == 5
    assert persisted["autopilot_pending_notify"] == [1, 2]
    assert persisted["telegram_last_checked_at"] == desired["telegram_last_checked_at"]


def test_scheduler_save_after_streamlit_edit_retries_against_fresh_baseline():
    baseline = {
        "theme": "old",
        "telegram_alert_history": {"old-alert": "old-time"},
        "telegram_update_offset": 10,
        "autopilot_pending_notify": [1],
    }
    concurrent = {
        **baseline,
        "theme": "new from Streamlit",
        "telegram_alert_history": {
            **baseline["telegram_alert_history"],
            "new-streamlit-alert": "new-time",
        },
        "telegram_update_offset": 12,
        "autopilot_pending_notify": [1, 99],
    }
    desired = {
        **baseline,
        "telegram_alert_history": {
            "scheduler-alert": "scheduler-time",
        },
        "telegram_update_offset": 11,
        "autopilot_pending_notify": [2],
    }
    database = SettingsDatabase(
        _json(baseline),
        after_select=lambda db: setattr(db, "payload", _json(concurrent)),
    )

    _save(database, desired, baseline)

    persisted = json.loads(database.payload)
    assert persisted["theme"] == "new from Streamlit"
    assert persisted["telegram_alert_history"] == {
        "new-streamlit-alert": "new-time",
        "scheduler-alert": "scheduler-time",
    }
    assert persisted["telegram_update_offset"] == 12
    assert persisted["autopilot_pending_notify"] == [99, 2]
    assert database.write_count == 2


def test_scheduler_history_delta_does_not_remove_concurrently_changed_value():
    baseline = {"telegram_alert_history": {"alert": "old"}}
    concurrent = {"telegram_alert_history": {"alert": "newer"}}
    desired = {"telegram_alert_history": {}}
    database = SettingsDatabase(
        _json(baseline),
        after_select=lambda db: setattr(db, "payload", _json(concurrent)),
    )

    _save(database, desired, baseline)

    assert json.loads(database.payload)["telegram_alert_history"] == {"alert": "newer"}


def test_cas_conflict_is_retried_only_with_a_fresh_payload():
    baseline = {"theme": "original", "telegram_update_offset": 2}
    database = SettingsDatabase(
        _json(baseline),
        after_select=lambda db: setattr(
            db,
            "payload",
            _json({"theme": "newer", "telegram_update_offset": 3}),
        ),
    )

    _save(database, {"telegram_update_offset": 4}, baseline)

    assert json.loads(database.payload) == {
        "theme": "newer",
        "telegram_update_offset": 4,
    }
    assert database.write_count == 2


def test_repeated_conflicts_fail_visibly_without_overwriting_latest_settings():
    baseline = {"theme": "original", "telegram_update_offset": 2}
    database = SettingsDatabase(_json(baseline))
    original_execute = FakeCursor.execute
    races = {"count": 0}

    def always_race(cursor, query, params=()):
        normalized = " ".join(query.split())
        if normalized.startswith("SELECT payload FROM app_settings"):
            cursor.row = {"payload": database.payload}
            races["count"] += 1
            database.payload = _json(
                {"theme": f"concurrent-{races['count']}", "telegram_update_offset": 2}
            )
        else:
            original_execute(cursor, query, params)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(FakeCursor, "execute", always_race)
    try:
        with pytest.raises(
            alert_scheduler.SchedulerSettingsPersistenceError,
            match="kept changing",
        ):
            _save(
                database,
                {"telegram_update_offset": 4},
                baseline,
                max_attempts=3,
            )
    finally:
        monkeypatch.undo()

    assert database.write_count == 3
    assert database.rollback_count == 1
    assert json.loads(database.payload)["theme"] == "concurrent-3"


def test_database_write_failure_is_explicit_and_rolled_back():
    baseline = {"theme": "saved", "telegram_update_offset": 2}
    database = SettingsDatabase(_json(baseline), fail=True)

    with pytest.raises(alert_scheduler.SchedulerSettingsPersistenceError) as error:
        _save(database, {"telegram_update_offset": 3}, baseline)

    assert isinstance(error.value.__cause__, psycopg.OperationalError)
    assert database.payload == _json(baseline)
    assert database.rollback_count == 1


def test_unavailable_or_session_only_database_does_not_claim_success():
    with pytest.raises(alert_scheduler.SchedulerSettingsPersistenceError):
        alert_scheduler.save_alert_state(
            None,
            {"telegram_update_offset": 3},
            {"telegram_update_offset": 2},
        )


def test_legacy_history_and_inbox_offset_survive_other_scheduler_changes():
    baseline = {
        "telegram_alert_history": {"legacy-key": "2026-11-21T20:00:00+00:00"},
        "telegram_update_offset": 38,
        "theme": "saved",
    }
    desired = {
        **baseline,
        "telegram_alert_history": {
            **baseline["telegram_alert_history"],
            "new-key": "2026-11-22T20:00:00+00:00",
        },
        "telegram_update_offset": 39,
    }
    database = SettingsDatabase(_json(baseline))

    _save(database, desired, baseline)

    persisted = json.loads(database.payload)
    assert persisted["telegram_alert_history"]["legacy-key"] == baseline["telegram_alert_history"]["legacy-key"]
    assert persisted["telegram_update_offset"] == 39


def test_settings_cas_retry_does_not_repeat_telegram_send(monkeypatch):
    baseline = {"theme": "old", "telegram_alert_history": {}}
    concurrent = {"theme": "new", "telegram_alert_history": {"from_streamlit": "now"}}
    database = SettingsDatabase(
        _json(baseline),
        after_select=lambda db: setattr(db, "payload", _json(concurrent)),
    )
    sends = []
    monkeypatch.setattr(alert_scheduler.staging_database, "is_staging", lambda: False)
    monkeypatch.setattr(
        alert_scheduler,
        "load_state",
        lambda connection: ([], dict(baseline)),
    )
    monkeypatch.setattr(alert_scheduler, "run_autopilot_rollover", lambda *args, **kwargs: 0)
    monkeypatch.setattr(
        alert_scheduler.telegram_alerts,
        "build_telegram_config",
        lambda *args: {
            "enabled": True,
            "timezone": "UTC",
            "quiet_start": None,
            "quiet_end": None,
            "bot_token": "",
            "chat_id": "",
        },
    )
    monkeypatch.setattr(
        alert_scheduler.telegram_alerts,
        "collect_due_alerts",
        lambda *args, **kwargs: [{"key": "new-alert", "message": "hello"}],
    )
    monkeypatch.setattr(alert_scheduler.telegram_alerts, "is_quiet_hours", lambda *args: False)
    monkeypatch.setattr(
        alert_scheduler.telegram_alerts,
        "send_telegram_message",
        lambda *args, **kwargs: (sends.append("sent") or True, None),
    )

    result = alert_scheduler.run_once(connection=FakeConnection(database))

    assert result["sent_count"] == 1
    assert sends == ["sent"]
    assert database.write_count == 2
    persisted = json.loads(database.payload)
    assert persisted["theme"] == "new"
    assert persisted["telegram_alert_history"]["from_streamlit"] == "now"
    assert "new-alert" in persisted["telegram_alert_history"]


def test_shared_streamlit_cas_primitive_keeps_compare_and_swap_conditions():
    class Cursor:
        def __init__(self, row):
            self.row = row
            self.query = None
            self.params = None

        def execute(self, query, params):
            self.query = " ".join(query.split())
            self.params = params

        def fetchone(self):
            return self.row

    cursor = Cursor({"id": 1})

    assert settings_persistence.compare_and_swap_payload(cursor, "new", "old")
    assert "WHERE id = 1 AND payload = %s" in cursor.query
    assert cursor.params == ("new", "old")
