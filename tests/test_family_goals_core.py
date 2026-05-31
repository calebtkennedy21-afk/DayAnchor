from datetime import date
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from family_goals_core import family_goal_dashboard_summary, normalize_family_goals


def test_normalize_family_goals_parses_checkins_and_week_progress():
    raw_goals = [
        {
            "goal_id": "goal_1",
            "title": "Family dinner together",
            "owner": "Family",
            "target_frequency": 4,
            "status": "active",
            "checkin_dates": ["2026-05-26", "2026-05-27", "2026-05-28", "2026-05-30"],
        }
    ]

    normalized = normalize_family_goals(raw_goals, reference_date=date(2026, 5, 31))

    assert len(normalized) == 1
    goal = normalized[0]
    assert goal["week_checkins"] == 4
    assert goal["total_checkins"] == 4
    assert goal["current_streak"] == 1
    assert goal["today_checked_in"] is False


def test_family_goal_dashboard_summary_flags_attention_goals():
    goals = [
        {
            "title": "Read with kids",
            "status": "active",
            "target_frequency": 5,
            "week_checkins": 3,
            "total_checkins": 10,
            "current_streak": 2,
        },
        {
            "title": "Evening walk",
            "status": "active",
            "target_frequency": 3,
            "week_checkins": 3,
            "total_checkins": 8,
            "current_streak": 4,
        },
        {
            "title": "Completed goal",
            "status": "completed",
            "target_frequency": 2,
            "week_checkins": 2,
            "total_checkins": 12,
            "current_streak": 0,
        },
    ]

    summary = family_goal_dashboard_summary(goals)

    assert len(summary["active_goals"]) == 2
    assert len(summary["on_track_goals"]) == 1
    assert len(summary["attention_goals"]) == 1
    assert summary["week_checkins"] == 6
    assert summary["best_streak"] == 4
