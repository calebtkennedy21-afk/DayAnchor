from datetime import date

from ai_workflows import generate_family_schedule_insight


def test_generate_family_schedule_insight_fallback_mentions_adjustment():
    summary = {
        "upcoming_count": 4,
        "appointment_count": 2,
        "trip_count": 1,
        "camp_count": 1,
        "multi_day_count": 1,
        "conflict_count": 2,
        "weekend_count": 1,
    }
    recent_items = [
        {"start_date": date(2026, 6, 1), "title": "Orthodontist", "item_type": "Appointment"},
        {"start_date": date(2026, 6, 3), "title": "Beach trip", "item_type": "Trip"},
    ]

    text, error = generate_family_schedule_insight(
        summary,
        recent_items,
        lambda: False,
        lambda: "",
        lambda: "gpt-4o-mini",
    )

    assert error == ""
    assert "Weekly Family Insight" in text
    assert "Actionable adjustment:" in text
