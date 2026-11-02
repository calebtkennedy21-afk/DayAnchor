"""Shared envelope for AI pillar output, plus adapters and a logging wrapper.

Generators in ai_workflows.py stay pure. The wrapper normalizes their return
shapes into an AIEnvelope and records it in ai_events without altering the
value the caller receives.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, time
from functools import wraps
from typing import Any
from zoneinfo import ZoneInfo

MOUNTAIN_TIMEZONE = ZoneInfo("America/Denver")

PILLARS = {
    "capture", "planning", "scheduling", "summary", "review",
    "reflection", "morning", "coaching", "family", "signals", "autopilot",
}
KINDS = {"suggestion", "decision", "schedule_change", "insight", "warning"}
STATUSES = {"proposed", "accepted", "rejected", "expired", "applied", "undone", "info"}
SOURCES = {"ai", "fallback", "rules"}

SUMMARY_MAX_CHARS = 280

AI_EVENTS_SCHEMA_SQL = (
    """
    CREATE TABLE IF NOT EXISTS ai_events (
        id BIGSERIAL PRIMARY KEY,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        day DATE NOT NULL,
        pillar TEXT NOT NULL,
        kind TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'info',
        source TEXT NOT NULL DEFAULT 'ai',
        summary TEXT NOT NULL,
        text TEXT NOT NULL DEFAULT '',
        payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
        task_ids BIGINT[] NOT NULL DEFAULT '{}',
        context_refs BIGINT[] NOT NULL DEFAULT '{}',
        expires_at TIMESTAMPTZ,
        resolved_at TIMESTAMPTZ,
        outcome TEXT,
        outcome_note TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_ai_events_day_pillar ON ai_events (day DESC, pillar)",
    "CREATE INDEX IF NOT EXISTS idx_ai_events_open ON ai_events (status) WHERE status = 'proposed'",
    "CREATE INDEX IF NOT EXISTS idx_ai_events_task_ids ON ai_events USING GIN (task_ids)",
)


@dataclass
class AIItem:
    type: str
    data: dict = field(default_factory=dict)
    task_ids: list = field(default_factory=list)
    confidence: float | None = None


@dataclass
class AIEnvelope:
    pillar: str
    kind: str
    summary: str
    text: str = ""
    error: str = ""
    items: list = field(default_factory=list)
    status: str = "info"
    source: str = "ai"
    model: str = ""
    context_refs: list = field(default_factory=list)
    expires_at: datetime | None = None

    def task_ids(self):
        seen = []
        for item in self.items:
            for task_id in item.task_ids:
                if task_id not in seen:
                    seen.append(task_id)
        return seen

    def payload(self):
        return {
            "items": [asdict(item) for item in self.items],
            "error": self.error,
            "model": self.model,
        }


def first_line(text, max_chars=SUMMARY_MAX_CHARS):
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip().lstrip("#*- ").strip()
        if line:
            return line if len(line) <= max_chars else line[: max_chars - 1].rstrip() + "…"
    return ""


def end_of_day(day, tz=MOUNTAIN_TIMEZONE):
    return datetime.combine(day, time(23, 59, 59), tzinfo=tz)


def _item_task_ids(data):
    task_id = data.get("task_id") if isinstance(data, dict) else None
    return [task_id] if isinstance(task_id, int) else []


def _proposal_status(items):
    return "proposed" if items else "info"


def envelope_from_plan(result):
    text, error, suggestions = result
    items = [AIItem("task_suggestion", dict(s)) for s in (suggestions or []) if isinstance(s, dict)]
    summary = f"Suggested {len(items)} task(s)" if items else first_line(text) or error
    return AIEnvelope("planning", "suggestion", summary, text or "", error or "", items, _proposal_status(items))


def envelope_from_schedule(result):
    text, error, updates = result
    items = [
        AIItem("schedule_update", dict(u), _item_task_ids(u))
        for u in (updates or [])
        if isinstance(u, dict)
    ]
    summary = f"Proposed {len(items)} schedule change(s)" if items else first_line(text) or error
    return AIEnvelope("scheduling", "schedule_change", summary, text or "", error or "", items, _proposal_status(items))


def envelope_from_triage(result):
    triage, error = result
    items = [AIItem("triage", dict(t)) for t in (triage or []) if isinstance(t, dict)]
    # Raw brain dump content is intentionally not copied into the summary.
    summary = f"Triaged {len(items)} inbox item(s)" if items else (error or "No triage output")
    return AIEnvelope("capture", "decision", summary, "", error or "", items, _proposal_status(items))


def envelope_from_review(result):
    review_text, tomorrow_text, error = result
    summary = first_line(review_text) or error
    return AIEnvelope("review", "insight", summary, review_text or "", error or "")


def text_adapter(pillar, kind="insight"):
    def adapt(result):
        text, error = result
        return AIEnvelope(pillar, kind, first_line(text) or error, text or "", error or "")

    return adapt


def envelope_from_signal(signal):
    signal = signal or {}
    return AIEnvelope(
        "signals",
        "warning",
        first_line(signal.get("title") or signal.get("detail")),
        str(signal.get("detail") or ""),
        source="rules",
        items=[AIItem("signal", {k: v for k, v in signal.items() if k != "task_payload"}, confidence=_as_float(signal.get("confidence")))],
    )


def _as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def with_event_logging(generator, adapter, log_fn, ai_enabled_fn, today_fn, model_name_fn=None, context_fn=None):
    """Wrap a generator so each call is logged; the return value is untouched.

    When context_fn is given, its text is passed as shared_context unless the
    caller supplied one. Logging and context failures never propagate.
    """

    @wraps(generator)
    def wrapped(*args, **kwargs):
        if context_fn is not None and "shared_context" not in kwargs:
            try:
                kwargs["shared_context"] = context_fn() or ""
            except Exception:
                kwargs["shared_context"] = ""
        result = generator(*args, **kwargs)
        try:
            envelope = adapter(result)
            if not ai_enabled_fn():
                envelope.source = "fallback"
            elif model_name_fn:
                envelope.model = str(model_name_fn() or "")
            day = today_fn()
            if envelope.status == "proposed":
                envelope.expires_at = end_of_day(day)
            log_fn(envelope, day)
        except Exception:
            pass
        return result

    return wrapped
