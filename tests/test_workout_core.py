from datetime import date

import workout_core as wc

PLAN = """Day 1 - Upper Body
- Bench Press 3x8-10
  Keep elbows tucked
- Plank 3 x 30 sec
Day 2: Legs
1. Squat 4 sets of 6
"""


def test_parse_workout_text():
    days = wc.parse_workout_text(PLAN)
    assert [d["name"] for d in days] == ["Day 1", "Day 2"]
    assert days[0]["focus"] == "Upper Body"
    bench = days[0]["exercises"][0]
    assert (bench["name"], bench["sets"], bench["reps"]) == ("Bench Press", 3, "8-10")
    assert "elbows" in bench["notes"]
    assert days[1]["exercises"][0]["sets"] == 4


def test_weekly_summary_and_progression():
    logs = wc.normalize_logs(
        [
            wc.build_log(date(2026, 10, 1), "p", "Day 1", [{"name": "Squat", "sets": 3, "reps": "5", "weight": 100}], 40, 8),
            wc.build_log(date(2026, 10, 3), "p", "Day 2", [{"name": "Squat", "sets": 3, "reps": "5", "weight": 105}], 50, 6),
        ]
    )
    rows = wc.weekly_summary(logs, 2, today=date(2026, 10, 4))
    assert rows[-1]["workouts"] == 2 and rows[-1]["adherence_pct"] == 100
    assert rows[-1]["volume"] == 3 * 5 * 100 + 3 * 5 * 105
    assert [p["top_weight"] for p in wc.exercise_progression(logs, "squat")] == [100, 105]
    assert wc.current_streak_weeks(rows) == 1


def test_multi_week_cardio_plan_keeps_weeks_separate():
    text = "Week 1\nMonday - Aerobic base\n- Easy run: 3 miles\nTuesday - Low-impact\nPeloton: 20-30 min\nWeek 2\nMonday - Aerobic base\nEasy run: 4 miles\n"
    days = wc.parse_workout_text(text)
    assert [(d["week"], d["name"]) for d in days] == [("Week 1", "Monday"), ("Week 1", "Tuesday"), ("Week 2", "Monday")]
    assert days[0]["exercises"][0]["name"] == "Easy run"
    assert days[0]["exercises"][0]["reps"] == "3 miles"
