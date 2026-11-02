from datetime import date, datetime
from types import SimpleNamespace

import data_access
from ai_envelope import (
    envelope_from_plan,
    envelope_from_review,
    envelope_from_schedule,
    envelope_from_triage,
    first_line,
    text_adapter,
    with_event_logging,
)


class DummyStreamlit:
    def __init__(self):
        self.session_state = SimpleNamespace()
        self.session_state.setdefault = lambda key, default: self.session_state.__dict__.setdefault(key, default)
        self.session_state.get = lambda key, default=None: self.session_state.__dict__.get(key, default)


def _log(envelope, day, st_module):
    return data_access.log_ai_event(envelope, day, st_module=st_module)


def test_plan_adapter_marks_suggestions_as_proposed():
    env = envelope_from_plan(("## Plan\n- a", "", [{"title": "A"}, {"title": "B"}]))
    assert env.pillar == "planning"
    assert env.status == "proposed"
    assert env.summary == "Suggested 2 task(s)"


def test_schedule_adapter_collects_task_ids():
    updates = [{"task_id": 4, "scheduled_minutes": 30}, {"task_id": 9, "scheduled_minutes": 15}]
    env = envelope_from_schedule(("text", "", updates))
    assert env.kind == "schedule_change"
    assert env.task_ids() == [4, 9]


def test_triage_summary_does_not_copy_raw_text():
    env = envelope_from_triage(([{"dump_id": "1", "title": "secret thing"}], ""))
    assert "secret" not in env.summary
    assert env.text == ""


def test_review_and_text_adapters_handle_errors():
    assert envelope_from_review(("", "", "boom")).summary == "boom"
    env = text_adapter("family")(("", "failed"))
    assert env.pillar == "family" and env.status == "info"


def test_first_line_truncates_and_strips_markdown():
    assert first_line("## Title\nbody") == "Title"
    assert len(first_line("x" * 500)) == 280


def test_wrapper_returns_original_result_and_logs_fallback_source():
    st_module = DummyStreamlit()
    result = ("hello", "")
    wrapped = with_event_logging(
        lambda: result,
        text_adapter("summary"),
        lambda env, day: _log(env, day, st_module),
        ai_enabled_fn=lambda: False,
        today_fn=lambda: date(2026, 11, 2),
    )
    assert wrapped() is result
    event = st_module.session_state.ai_events[0]
    assert event["source"] == "fallback"
    assert event["day"] == date(2026, 11, 2)


def test_wrapper_swallows_logging_errors():
    def boom(env, day):
        raise RuntimeError("db down")

    wrapped = with_event_logging(
        lambda: ("x", ""), text_adapter("summary"), boom, lambda: True, lambda: date(2026, 11, 2)
    )
    assert wrapped() == ("x", "")


def test_proposed_events_expire_end_of_day():
    st_module = DummyStreamlit()
    wrapped = with_event_logging(
        lambda: ("t", "", [{"task_id": 1, "scheduled_minutes": 30}]),
        envelope_from_schedule,
        lambda env, day: _log(env, day, st_module),
        lambda: True,
        lambda: date(2026, 11, 2),
        lambda: "gpt-test",
    )
    wrapped()
    event = st_module.session_state.ai_events[0]
    assert event["expires_at"].hour == 23
    assert event["payload_json"]["model"] == "gpt-test"


def test_session_fallback_load_resolve_and_conflicts():
    st_module = DummyStreamlit()
    day = data_access.mountain_today()
    env = envelope_from_schedule(("t", "", [{"task_id": 7, "scheduled_minutes": 30}]))
    first_id = data_access.log_ai_event(env, day, st_module=st_module)
    second_id = data_access.log_ai_event(env, day, st_module=st_module)

    loaded = data_access.load_recent_ai_events(days=1, st_module=st_module)
    assert [e["id"] for e in loaded] == [second_id, first_id]

    conflicts = data_access.find_conflicting_events([7], day, exclude_id=second_id, st_module=st_module)
    assert [e["id"] for e in conflicts] == [first_id]

    assert data_access.resolve_ai_event(first_id, "accepted", "accepted", st_module=st_module)
    assert data_access.find_conflicting_events([7], day, exclude_id=second_id, st_module=st_module) == []


def test_shared_context_block_caps_length_and_skips_fallback():
    now = datetime(2026, 11, 2, 9, 5)
    events = [
        {"pillar": "planning", "status": "accepted", "summary": "Moved 3 tasks", "created_at": now, "source": "ai"},
        {"pillar": "summary", "status": "info", "summary": "boilerplate", "created_at": now, "source": "fallback"},
    ]
    block = data_access.shared_context_block(events)
    assert "Moved 3 tasks" in block and "boilerplate" not in block
    assert len(data_access.shared_context_block(events * 50, max_chars=100)) <= 100


class _FakeOpenAI:
    prompts = []

    def __init__(self, api_key=None):
        outer = self

        class _Completions:
            def create(self, model, messages, temperature=None):
                _FakeOpenAI.prompts.append(messages[-1]["content"])
                message = SimpleNamespace(content="## End-of-Day Review\nok\n## Tomorrow Draft Plan\nnext")
                return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        self.chat = SimpleNamespace(completions=_Completions())


def _run_review(shared_context):
    from ai_workflows import generate_daily_review

    _FakeOpenAI.prompts = []
    generate_daily_review(
        [], [], "notes", lambda: True, lambda: "key", lambda: "m",
        openai_cls=_FakeOpenAI, shared_context=shared_context,
    )
    return _FakeOpenAI.prompts[0]


def test_shared_context_is_added_to_prompt_only_when_present():
    with_ctx = _run_review("- [planning/accepted 09:00] Moved 3 tasks")
    assert "Moved 3 tasks" in with_ctx
    assert "other DayAnchor assistants" in with_ctx
    assert "other DayAnchor assistants" not in _run_review("")


def test_wrapper_injects_context_and_respects_explicit_override():
    seen = []

    def gen(shared_context=""):
        seen.append(shared_context)
        return ("t", "")

    wrapped = with_event_logging(
        gen, text_adapter("summary"), lambda e, d: None, lambda: True,
        lambda: date(2026, 11, 2), context_fn=lambda: "CTX",
    )
    wrapped()
    wrapped(shared_context="mine")
    assert seen == ["CTX", "mine"]


def test_wrapper_survives_context_failure():
    seen = []

    def boom():
        raise RuntimeError("db down")

    wrapped = with_event_logging(
        lambda shared_context="": seen.append(shared_context) or ("t", ""),
        text_adapter("summary"), lambda e, d: None, lambda: True,
        lambda: date(2026, 11, 2), context_fn=boom,
    )
    assert wrapped() == ("t", "")
    assert seen == [""]


def test_expire_stale_events_only_touches_past_due_proposals():
    from datetime import timedelta

    st_module = DummyStreamlit()
    day = data_access.mountain_today()
    env = envelope_from_schedule(("t", "", [{"task_id": 1, "scheduled_minutes": 30}]))
    now = datetime.now(data_access.MOUNTAIN_TIMEZONE)
    env.expires_at = now - timedelta(hours=1)
    stale_id = data_access.log_ai_event(env, day, st_module=st_module)
    env.expires_at = now + timedelta(hours=1)
    fresh_id = data_access.log_ai_event(env, day, st_module=st_module)

    assert data_access.expire_stale_ai_events(now=now, st_module=st_module) == 1
    by_id = {e["id"]: e for e in st_module.session_state.ai_events}
    assert by_id[stale_id]["status"] == "expired" and by_id[stale_id]["outcome"] == "ignored"
    assert by_id[fresh_id]["status"] == "proposed"


def test_record_task_outcome_upgrades_only_accepted_applied_events():
    st_module = DummyStreamlit()
    day = data_access.mountain_today()
    env = envelope_from_schedule(("t", "", [{"task_id": 5, "scheduled_minutes": 30}]))
    applied_id = data_access.log_ai_event(env, day, st_module=st_module)
    open_id = data_access.log_ai_event(env, day, st_module=st_module)
    data_access.resolve_ai_event(applied_id, "applied", "accepted", st_module=st_module)

    assert data_access.record_task_outcome(5, "completed", st_module=st_module) == 1
    by_id = {e["id"]: e for e in st_module.session_state.ai_events}
    assert by_id[applied_id]["outcome"] == "completed"
    assert by_id[open_id]["outcome"] is None
    assert data_access.record_task_outcome(99, "completed", st_module=st_module) == 0
