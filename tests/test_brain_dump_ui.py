from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import brain_dump_ui
import data_access


TODAY = date(2026, 11, 16)


class SessionState(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


class FakeStreamlit:
    def __init__(self, **state):
        self.session_state = SessionState(state)
        self.messages = []

    def success(self, message):
        self.messages.append(("success", message))

    def info(self, message):
        self.messages.append(("info", message))

    def error(self, message):
        self.messages.append(("error", message))


def _result(status, mode="database", message="Conversion result.", settings=None):
    return SimpleNamespace(
        status=status,
        mode=mode,
        message=message,
        settings=settings or {"saved": True},
    )


def _conversion_dependencies(convert_fn):
    return {
        "convert_fn": convert_fn,
        "db_enabled_fn": lambda: False,
        "get_connection_fn": None,
    }


def test_manual_task_handler_calls_conversion_operation_and_reports_success():
    entry = {"dump_id": "dump-manual", "status": "new"}
    st = FakeStreamlit()
    calls = []

    def convert_fn(decisions, today, **kwargs):
        calls.append((decisions, today, kwargs))
        return _result("created", settings={"converted": True})

    result, should_rerun = brain_dump_ui.handle_manual_brain_dump_task(
        entry,
        {"theme": "dark"},
        TODAY,
        **_conversion_dependencies(convert_fn),
        st_module=st,
    )

    assert should_rerun is True
    assert calls[0][0] == [{**entry, "item_type": "task"}]
    assert calls[0][1] == TODAY
    assert calls[0][2]["session_settings"] == {"theme": "dark"}
    assert result.status == "created"
    assert st.session_state["app_settings"] == {"converted": True}
    assert st.messages == [("success", "Converted to task.")]


def test_manual_task_failure_has_no_success_feedback():
    st = FakeStreamlit()
    convert = lambda *args, **kwargs: _result("failed", message="Could not convert.")

    _, should_rerun = brain_dump_ui.handle_manual_brain_dump_task(
        {"dump_id": "dump-manual"},
        {},
        TODAY,
        **_conversion_dependencies(convert),
        st_module=st,
    )

    assert should_rerun is False
    assert st.messages == [("error", "Could not convert.")]
    assert "app_settings" not in st.session_state


def test_ai_handler_passes_only_selected_eligible_decisions_and_clears_on_success():
    triage = [
        {"dump_id": "dump-a", "item_type": "task", "title": "A"},
        {"dump_id": "dump-b", "item_type": "note", "title": "B"},
        {"dump_id": "dump-stale", "item_type": "task", "title": "Stale"},
    ]
    entries = [
        {"dump_id": "dump-a", "status": "new"},
        {"dump_id": "dump-b", "status": "new"},
        {"dump_id": "dump-stale", "status": "converted_task"},
    ]
    st = FakeStreamlit(
        **{
            "panel_ai_triage": deepcopy(triage),
            "panel_ai_triage_error": "",
            "ai_open_event_ids": {"capture": 42, "planning": 77},
            "panel_ai_apply_dump-b": False,
            "panel_ai_title_dump-a": "Edited title",
        }
    )
    calls = []

    def convert_fn(decisions, today, **kwargs):
        calls.append((decisions, today, kwargs))
        return _result("created", settings={"converted": True})

    _, should_rerun = brain_dump_ui.handle_ai_brain_dump_apply(
        entries,
        triage,
        {"theme": "dark"},
        TODAY,
        "panel",
        "panel_ai_triage",
        "panel_ai_triage_error",
        **_conversion_dependencies(convert_fn),
        resolve_proposal_fn=lambda *args: None,
        st_module=st,
    )

    assert should_rerun is True
    decisions, today, kwargs = calls[0]
    assert [decision["dump_id"] for decision in decisions] == ["dump-a"]
    assert decisions[0]["title"] == "Edited title"
    assert today == TODAY
    assert kwargs["event_id"] == 42
    assert kwargs["status_prefix"] == "ai_"
    assert st.session_state["panel_ai_triage"] == []
    assert st.session_state["panel_ai_triage_error"] == ""
    assert st.session_state["ai_open_event_ids"] == {"planning": 77}
    assert st.session_state["app_settings"] == {"converted": True}
    assert st.messages == [("success", "Conversion result.")]


def test_ai_failure_preserves_triage_and_proposal_for_retry_then_success_clears_them():
    triage = [{"dump_id": "dump-a", "item_type": "task", "title": "A"}]
    st = FakeStreamlit(
        panel_ai_triage=deepcopy(triage),
        panel_ai_triage_error="",
        ai_open_event_ids={"capture": 43},
    )
    calls = []
    responses = iter(
        [
            _result("failed", message="Proposal resolution failed."),
            _result("created"),
        ]
    )

    def convert_fn(decisions, today, **kwargs):
        calls.append((decisions, kwargs["event_id"]))
        return next(responses)

    args = (
        [{"dump_id": "dump-a", "status": "new"}],
        triage,
        {},
        TODAY,
        "panel",
        "panel_ai_triage",
        "panel_ai_triage_error",
    )
    kwargs = {
        **_conversion_dependencies(convert_fn),
        "resolve_proposal_fn": lambda *args: None,
        "st_module": st,
    }
    _, retry = brain_dump_ui.handle_ai_brain_dump_apply(*args, **kwargs)

    assert retry is False
    assert st.messages == [("error", "Proposal resolution failed.")]
    assert st.session_state["panel_ai_triage"] == triage
    assert st.session_state["ai_open_event_ids"] == {"capture": 43}

    _, retry = brain_dump_ui.handle_ai_brain_dump_apply(*args, **kwargs)
    assert retry is True
    assert [event_id for _, event_id in calls] == [43, 43]
    assert st.session_state["panel_ai_triage"] == []
    assert st.session_state["ai_open_event_ids"] == {}
    assert [kind for kind, _ in st.messages] == ["error", "success"]


def test_ai_missing_event_id_does_not_call_conversion_or_clear_triage():
    triage = [{"dump_id": "dump-a", "item_type": "task"}]
    st = FakeStreamlit(
        panel_ai_triage=deepcopy(triage),
        panel_ai_triage_error="",
        ai_open_event_ids={},
    )
    calls = []

    _, should_rerun = brain_dump_ui.handle_ai_brain_dump_apply(
        [{"dump_id": "dump-a", "status": "new"}],
        triage,
        {},
        TODAY,
        "panel",
        "panel_ai_triage",
        "panel_ai_triage_error",
        **_conversion_dependencies(lambda *args, **kwargs: calls.append(args)),
        resolve_proposal_fn=lambda *args: None,
        st_module=st,
    )

    assert should_rerun is False
    assert calls == []
    assert st.session_state["panel_ai_triage"] == triage
    assert st.messages[0][0] == "error"


def test_manual_session_only_conversion_is_labeled_temporary():
    settings = {
        "brain_dump_entries": [
            {"dump_id": "dump-session", "status": "new", "title": "Session task"}
        ]
    }
    st = FakeStreamlit(app_settings=deepcopy(settings), tasks=[])
    result, should_rerun = brain_dump_ui.handle_manual_brain_dump_task(
        settings["brain_dump_entries"][0],
        settings,
        TODAY,
        **_conversion_dependencies(data_access.convert_brain_dump_entries),
        st_module=st,
    )

    assert should_rerun is True
    assert result.status == "created"
    assert result.mode == "session"
    assert [task["title"] for task in st.session_state["tasks"]] == ["Session task"]
    assert st.messages == [("info", "Task conversion is available for this session only.")]


def test_ai_session_only_conversion_is_labeled_temporary():
    triage = [{"dump_id": "dump-session-ai", "item_type": "task", "title": "Session AI task"}]
    st = FakeStreamlit(
        panel_ai_triage=deepcopy(triage),
        panel_ai_triage_error="",
        ai_open_event_ids={"capture": 92},
    )
    result, should_rerun = brain_dump_ui.handle_ai_brain_dump_apply(
        [{"dump_id": "dump-session-ai", "status": "new"}],
        triage,
        {},
        TODAY,
        "panel",
        "panel_ai_triage",
        "panel_ai_triage_error",
        **_conversion_dependencies(
            lambda *args, **kwargs: _result(
                "created",
                mode="session",
                message="AI decision applied.",
            )
        ),
        resolve_proposal_fn=lambda *args: None,
        st_module=st,
    )

    assert should_rerun is True
    assert result.mode == "session"
    assert st.messages == [("info", "AI decision applied. These changes are session-only.")]


def test_pending_settings_draft_blocks_manual_handler_before_task_creation():
    settings = {
        "brain_dump_entries": [
            {"dump_id": "dump-pending", "status": "new", "title": "Pending task"}
        ]
    }
    draft = {"settings": deepcopy(settings)}
    st = FakeStreamlit(
        app_settings=deepcopy(settings),
        pending_app_settings=draft,
        tasks=[],
    )
    result, should_rerun = brain_dump_ui.handle_manual_brain_dump_task(
        settings["brain_dump_entries"][0],
        settings,
        TODAY,
        **_conversion_dependencies(data_access.convert_brain_dump_entries),
        st_module=st,
    )

    assert should_rerun is False
    assert result.status == "rejected"
    assert st.session_state["tasks"] == []
    assert st.session_state["pending_app_settings"] == draft
    assert [kind for kind, _ in st.messages] == ["error"]


def test_pending_settings_draft_blocks_ai_handler_before_task_creation():
    settings = {
        "brain_dump_entries": [
            {"dump_id": "dump-ai-pending", "status": "new", "title": "Pending AI task"}
        ]
    }
    proposal = [{"dump_id": "dump-ai-pending", "item_type": "task", "title": "Pending AI task"}]
    draft = {"settings": deepcopy(settings)}
    st = FakeStreamlit(
        app_settings=deepcopy(settings),
        pending_app_settings=draft,
        tasks=[],
        panel_ai_triage=deepcopy(proposal),
        panel_ai_triage_error="",
        ai_open_event_ids={"capture": 91},
    )
    result, should_rerun = brain_dump_ui.handle_ai_brain_dump_apply(
        settings["brain_dump_entries"],
        proposal,
        settings,
        TODAY,
        "panel",
        "panel_ai_triage",
        "panel_ai_triage_error",
        **_conversion_dependencies(data_access.convert_brain_dump_entries),
        resolve_proposal_fn=lambda *args: None,
        st_module=st,
    )

    assert should_rerun is False
    assert result.status == "rejected"
    assert st.session_state["tasks"] == []
    assert st.session_state["pending_app_settings"] == draft
    assert st.session_state["panel_ai_triage"] == proposal
    assert st.session_state["ai_open_event_ids"] == {"capture": 91}
    assert [kind for kind, _ in st.messages] == ["error"]


def test_legacy_helper_rejects_task_without_creating_one():
    entry = {"dump_id": "dump-legacy", "status": "new"}
    updates = {}
    tasks = []

    try:
        brain_dump_ui.apply_brain_dump_decision(
            entry,
            {"item_type": "task", "title": "Must not bypass"},
            {},
            updates,
        )
    except ValueError as exc:
        assert "atomic brain-dump conversion" in str(exc)
    else:
        raise AssertionError("Task decisions must be rejected by the legacy helper.")

    assert tasks == []
    assert updates == {}
    assert entry["status"] == "new"


def test_legacy_helper_preserves_reminder_and_note_behavior():
    reminder_entry = {"dump_id": "dump-reminder", "status": "new"}
    reminder_updates = {}
    brain_dump_ui.apply_brain_dump_decision(
        reminder_entry,
        {"item_type": "reminder", "title": "Call back"},
        {"quick_reminders": []},
        reminder_updates,
    )
    assert reminder_entry["status"] == "converted_reminder"
    assert reminder_updates["quick_reminders"][0]["reminder_id"] == "dump-reminder"

    note_entry = {"dump_id": "dump-note", "status": "new", "raw_text": "Write this down"}
    note_updates = {}
    brain_dump_ui.apply_brain_dump_decision(
        note_entry,
        {"item_type": "note", "category": "Family"},
        {"family_notes": "Existing note"},
        note_updates,
    )
    assert note_entry["status"] == "converted_note"
    assert note_updates["family_notes"] == "Existing note\n- Write this down"
