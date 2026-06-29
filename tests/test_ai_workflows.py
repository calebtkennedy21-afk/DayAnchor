from datetime import date
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_workflows import (
    generate_family_goal_coaching,
    generate_family_schedule_insight,
    generate_family_weekly_briefing,
    generate_family_weekly_digest,
    generate_ma_lead_coaching,
)


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


def test_generate_family_goal_coaching_fallback_contains_moves():
    summary = {
        "active_goal_count": 3,
        "on_track_count": 1,
        "attention_count": 2,
        "week_checkins": 4,
        "best_streak": 3,
    }
    goals = [
        {"title": "Family dinner", "owner": "Family", "status": "active", "week_checkins": 2, "target_frequency": 4},
        {"title": "Read together", "owner": "Kids", "status": "active", "week_checkins": 1, "target_frequency": 3},
    ]

    text, error = generate_family_goal_coaching(
        summary,
        goals,
        lambda: False,
        lambda: "",
        lambda: "gpt-4o-mini",
    )

    assert error == ""
    assert "Weekly Family Goal Coaching" in text
    assert "## Next 3 Moves" in text


def test_generate_family_weekly_digest_fallback_contains_priority_moves():
    schedule_summary = {
        "upcoming_count": 6,
        "appointment_count": 2,
        "trip_count": 1,
        "camp_count": 1,
        "conflict_count": 2,
    }
    goal_summary = {
        "active_goal_count": 3,
        "on_track_count": 1,
        "attention_count": 2,
        "week_checkins": 4,
        "best_streak": 3,
    }
    upcoming_items = [
        {"start_date": date(2026, 6, 2), "title": "Dentist", "item_type": "Appointment"},
        {"start_date": date(2026, 6, 4), "title": "Soccer camp", "item_type": "Sports camp"},
    ]
    active_goals = [
        {"title": "Family dinner", "owner": "Family", "week_checkins": 2, "target_frequency": 4},
        {"title": "Read together", "owner": "Kids", "week_checkins": 1, "target_frequency": 3},
    ]

    text, error = generate_family_weekly_digest(
        schedule_summary,
        goal_summary,
        upcoming_items,
        active_goals,
        lambda: False,
        lambda: "",
        lambda: "gpt-4o-mini",
    )

    assert error == ""
    assert "Family Weekly Digest" in text
    assert "## Priority Moves" in text


def test_generate_ma_lead_coaching_fallback_contains_sections():
    context = {
        "open_issues": 6,
        "escalated_issues": 2,
        "waiting_psr": 2,
        "waiting_leadership": 1,
        "overdue_actions": 3,
        "due_checkins": 2,
        "pending_signoffs": 4,
        "open_education_requests": 2,
        "autoclave_due": 1,
        "weekly_priorities": [
            "Close escalations within 24 hours",
            "Tighten callback handoff reliability",
        ],
        "top_items": [
            "Escalation: delayed callback closure",
            "Biweekly check-in due: Savannah",
        ],
    }

    text, error = generate_ma_lead_coaching(
        context,
        "Need prep for tomorrow morning huddle.",
        "Huddle framing",
        lambda: False,
        lambda: "",
        lambda: "gpt-4o-mini",
    )

    assert error == ""
    assert "MA Lead Coaching Brief" in text
    assert "## Talking Points" in text
    assert "## 24-Hour Follow-through" in text
