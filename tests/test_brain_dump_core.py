from datetime import date, time

from brain_dump_core import parse_brain_dump, parse_brain_dump_line


def test_parse_brain_dump_line_classifies_and_extracts_task_metadata():
    result = parse_brain_dump_line(
        "tomorrow 9am high clinic call PT",
        today=date(2026, 9, 8),
    )

    assert result["item_type"] == "task"
    assert result["title"] == "call PT"
    assert result["category"] == "Clinic"
    assert result["priority"] == "high"
    assert result["due_date"] == date(2026, 9, 9)
    assert result["scheduled_time"] == time(9, 0)


def test_parse_brain_dump_supports_notes_reminders_and_ideas():
    results = parse_brain_dump(
        "remember to buy batteries tomorrow\nnote: ask about summer camp\nidea: Sunday meal prep",
        today=date(2026, 9, 8),
    )

    assert [item["item_type"] for item in results] == ["reminder", "note", "idea"]
    assert results[0]["due_date"] == date(2026, 9, 9)
    assert results[1]["title"] == "ask about summer camp"
    assert results[2]["due_date"] is None
