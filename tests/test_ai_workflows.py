from datetime import date

from ai_workflows import generate_family_schedule_insight, generate_family_weekly_briefing


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


def test_generate_family_weekly_briefing_fallback_contains_actions():
    summary = {
        "upcoming_count": 5,
        "appointment_count": 2,
        "trip_count": 1,
        "camp_count": 1,
        "multi_day_count": 1,
        "recurring_count": 2,
        "conflict_count": 1,
        "weekend_count": 2,
        "items_with_checklists": 3,
    }
    recent_items = [
        {"start_date": date(2026, 6, 1), "title": "Ortho follow-up", "item_type": "Appointment"},
        {"start_date": date(2026, 6, 2), "title": "Summer sports camp", "item_type": "Sports camp"},
    ]

    text, error = generate_family_weekly_briefing(
        summary,
        recent_items,
        lambda: False,
        lambda: "",
        lambda: "gpt-4o-mini",
    )

    assert error == ""
    assert "Weekly Family Briefing" in text
    assert "## Recommended Actions" in text
