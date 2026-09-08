from datetime import date, time

from ai_workflows import generate_brain_dump_triage, parse_brain_dump_triage


ENTRIES = [
    {
        "dump_id": "dump_1",
        "raw_text": "Call PT tomorrow at 9am",
        "title": "Call PT",
        "item_type": "task",
        "category": "Clinic",
        "priority": "medium",
        "status": "new",
    }
]


def test_brain_dump_triage_fallback_preserves_deterministic_parse():
    triage, error = generate_brain_dump_triage(
        ENTRIES,
        lambda: False,
        lambda: "",
        lambda: "gpt-4o-mini",
    )

    assert error == ""
    assert triage[0]["dump_id"] == "dump_1"
    assert triage[0]["item_type"] == "task"
    assert "AI is not configured" in triage[0]["reason"]


def test_parse_brain_dump_triage_validates_ai_payload():
    text = '''```json
{"triage": [{"dump_id": "dump_1", "item_type": "reminder", "title": "Call PT", "description": "Call the PT office", "category": "Clinic", "priority": "high", "due_date": "2026-09-09", "scheduled_time": "09:00", "reason": "It is a follow-up reminder."}]}
```'''

    triage = parse_brain_dump_triage(text, ENTRIES)

    assert triage[0]["item_type"] == "reminder"
    assert triage[0]["due_date"] == date(2026, 9, 9)
    assert triage[0]["scheduled_date"] == date(2026, 9, 9)
    assert triage[0]["scheduled_time"] == time(9, 0)


def test_parse_brain_dump_triage_discards_unknown_entries():
    triage = parse_brain_dump_triage(
        '{"triage": [{"dump_id": "unknown", "item_type": "task", "title": "Ignore"}]}',
        ENTRIES,
    )

    assert triage == []
