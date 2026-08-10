from datetime import date, time, timedelta

from dayanchor_brain import build_brain_context, build_case_signal_snapshot, generate_brain_signals


LIFE_CATEGORIES = [
    {"key": "health", "label": "Health"},
    {"key": "family", "label": "Family"},
]


def _signal_ids(signal_payload):
    return {item.get("id") for item in signal_payload.get("insights", [])}


def test_generate_brain_signals_includes_clinic_pressure_patterns():
    today_value = date(2026, 8, 10)
    active_tasks = [
        {"category": "Clinic", "due_date": today_value - timedelta(days=1)},
        {"category": "Clinic", "due_date": today_value - timedelta(days=2)},
        {"category": "Clinic", "due_date": today_value - timedelta(days=9)},
        {"category": "Personal", "due_date": today_value - timedelta(days=1)},
    ]

    context = build_brain_context(
        today_value=today_value,
        active_tasks=active_tasks,
        family_items=[],
        morning_checkins={},
        life_entries=[],
        default_duration=60,
        default_time=time(9, 0),
        life_categories=LIFE_CATEGORIES,
    )
    result = generate_brain_signals(context)

    ids = _signal_ids(result)
    assert "clinic_overdue_wow" in ids
    assert "clinic_overdue_concentration" in ids or "wednesday_backlog" in ids
    assert result["metrics"]["overdue_clinic_count"] == 3


def test_generate_brain_signals_includes_family_ritual_and_life_signals():
    today_value = date(2026, 8, 10)
    family_items = [
        {"start_date": date(2026, 8, 2)},
        {"start_date": date(2026, 8, 4)},
        {"start_date": date(2026, 8, 5)},
        {"start_date": date(2026, 8, 18)},
    ]
    morning_checkins = {
        "2026-08-06": {
            "sleep_quality": "Poor",
            "energy_level": "Low",
            "optional_grounding_complete": True,
            "planned_morning_goals": "Yes",
        },
        "2026-08-07": {
            "sleep_quality": "Fair",
            "energy_level": "Low",
            "optional_grounding_complete": True,
            "planned_morning_goals": "Yes",
        },
        "2026-08-08": {
            "sleep_quality": "Good",
            "energy_level": "High",
            "optional_grounding_complete": False,
            "planned_morning_goals": "No",
        },
        "2026-08-09": {
            "sleep_quality": "Great",
            "energy_level": "High",
            "optional_grounding_complete": False,
            "planned_morning_goals": "No",
        },
    }
    life_entries = [
        {"scores": {"health": 4, "family": 5}},
        {"scores": {"health": 4, "family": 5}},
        {"scores": {"health": 3, "family": 4}},
        {"scores": {"health": 2, "family": 4}},
        {"scores": {"health": 2, "family": 3}},
        {"scores": {"health": 1, "family": 3}},
    ]

    context = build_brain_context(
        today_value=today_value,
        active_tasks=[],
        family_items=family_items,
        morning_checkins=morning_checkins,
        life_entries=life_entries,
        default_duration=45,
        default_time=time(8, 30),
        life_categories=LIFE_CATEGORIES,
    )
    result = generate_brain_signals(context)

    ids = _signal_ids(result)
    assert "family_monthly_rhythm" in ids
    assert "sleep_energy_pattern" in ids
    assert "grounding_execution_correlation" in ids
    assert "life_directional_trend" in ids
    assert result["metrics"]["morning_checkin_count"] == 4
    assert result["metrics"]["family_item_count"] == 4


def test_build_case_signal_snapshot_computes_metrics_and_trends():
    today_value = date(2026, 8, 10)
    surgical_cases = [
        {"case_date": date(2026, 8, 9), "status": "completed", "procedure_name": "TenJet"},
        {"case_date": date(2026, 8, 8), "status": "planned", "procedure_name": "Ankle Scope"},
        {"case_date": date(2026, 8, 7), "status": "canceled", "procedure_name": "TenJet"},
        {"case_date": date(2026, 7, 15), "status": "completed", "procedure_name": "TenJet"},
    ]
    protocol_documents = [{"id": 1, "protocol_name": "PT protocol"}]

    def protocol_match_fn(case_item, docs, max_items=1):
        del docs, max_items
        return [1] if case_item.get("status") in ("planned", "completed") else []

    snapshot = build_case_signal_snapshot(
        today_value=today_value,
        surgical_cases=surgical_cases,
        protocol_documents=protocol_documents,
        protocol_match_fn=protocol_match_fn,
    )

    assert snapshot["recent_cases_count"] == 4
    assert snapshot["canceled_recent_count"] == 1
    assert snapshot["protocol_coverage"] == 100.0
    assert snapshot["completed_cases_count"] == 2
    assert snapshot["unique_surgery_type_count"] == 1
    assert "TenJet" in dict(snapshot["sorted_surgery_counts"])
    assert len(snapshot["cancel_trend"]) == 6
    assert len(snapshot["coverage_trend"]) == 6
