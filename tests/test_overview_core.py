from datetime import date, time

from overview_core import (
    build_time_blocks,
    clinic_day_profiles,
    clinic_day_summary,
    overview_runtime_settings,
    personal_focus_summary,
    resolve_overview_day_context,
    schedule_workload_snapshot,
)


def test_clinic_day_profiles_honors_settings():
    settings = {
        "surgeon_clinic_patient_target": 30,
        "general_clinic_patient_target": 22,
        "procedure_friday_procedure_target": 10,
        "clinic_visit_minutes": 15,
        "clinic_admin_buffer_minutes": 75,
        "procedure_block_minutes": 40,
    }
    profiles = clinic_day_profiles(settings)
    assert profiles["surgeon_clinic"]["volume_target"] == 30
    assert profiles["general_clinic"]["volume_target"] == 22
    assert profiles["procedure_friday"]["volume_target"] == 10
    assert profiles["procedure_friday"]["visit_minutes"] == 40


def test_build_time_blocks_outputs_positive_plan():
    profile = {
        "key": "general_clinic",
        "volume_target": 20,
        "visit_minutes": 12,
        "prep_minutes": 30,
        "admin_buffer_minutes": 60,
    }
    block_plan = build_time_blocks(profile)
    assert block_plan["estimated_blocks"] >= 2
    assert block_plan["block_minutes"] >= 15
    assert block_plan["slack_minutes"] >= 0


def test_clinic_day_summary_counts_unscheduled_and_due_soon():
    app_settings = {}
    clinic_tasks = [
        {
            "id": 1,
            "title": "Task A",
            "priority": "high",
            "status": "todo",
            "due_date": date(2026, 5, 18),
            "scheduled_date": None,
            "scheduled_time": None,
            "category": "Clinic",
        },
        {
            "id": 2,
            "title": "Task B",
            "priority": "medium",
            "status": "todo",
            "due_date": date(2026, 5, 27),
            "scheduled_date": date(2026, 5, 20),
            "scheduled_time": time(9, 0),
            "category": "Clinic",
        },
    ]
    active_tasks = clinic_tasks + [{"id": 3, "category": "Personal", "priority": "low", "status": "todo"}]
    summary = clinic_day_summary(clinic_tasks, active_tasks, app_settings, "general_clinic", reference_date=date(2026, 5, 17))
    assert summary["clinic_unscheduled_count"] == 1
    assert summary["due_soon_count"] == 1
    assert summary["clinic_backlog_count"] == 2


def test_personal_focus_summary_picks_highest_priority_first():
    personal_tasks = [
        {"title": "Low", "priority": "low", "due_date": date(2026, 5, 20), "status": "todo", "category": "Personal"},
        {"title": "High", "priority": "high", "due_date": date(2026, 5, 22), "status": "todo", "category": "Personal"},
    ]
    active_tasks = personal_tasks + [{"title": "Clinic", "priority": "high", "status": "todo", "category": "Clinic"}]
    summary = personal_focus_summary(personal_tasks, active_tasks, {"personal_focus_minutes": 120})
    assert summary["focus_minutes"] == 120
    assert summary["focus_tasks"][0]["title"] == "High"
    assert summary["personal_count"] == 2


def test_schedule_workload_snapshot_counts_and_orders():
    tasks = [
        {
            "title": "Later",
            "priority": "medium",
            "scheduled_date": date(2026, 5, 18),
            "scheduled_time": time(13, 0),
        },
        {
            "title": "Sooner",
            "priority": "high",
            "scheduled_date": date(2026, 5, 18),
            "scheduled_time": time(9, 0),
        },
        {"title": "Unscheduled high", "priority": "high", "scheduled_date": None, "scheduled_time": None},
    ]
    snapshot = schedule_workload_snapshot(tasks)
    assert snapshot["upcoming"][0]["title"] == "Sooner"
    assert len(snapshot["unscheduled_high"]) == 1
    assert snapshot["capacity_gap"] == -1


def test_overview_runtime_settings_defaults_and_casts():
    settings = {
        "overview_patient_target": "27",
        "overview_procedure_target": "9",
    }
    runtime = overview_runtime_settings(settings)
    assert runtime["patient_target"] == 27
    assert runtime["procedure_target"] == 9
    assert runtime["day_mode"] == "Auto"


def test_resolve_overview_day_context_auto_tuesday_admin_day():
    overview_settings = {
        "day_mode": "Auto",
        "patient_target": 25,
        "procedure_target": 8,
        "shift_minutes": 480,
        "clinic_weekdays": ["Thursday", "Monday"],
        "admin_weekdays": ["Tuesday", "Wednesday"],
        "procedure_friday_frequency_weeks": 2,
        "procedure_friday_cycle_offset": 0,
    }
    context = resolve_overview_day_context(
        overview_settings,
        active_tasks=[],
        personal_tasks=[],
        clinic_tasks=[],
        reference_date=date(2026, 5, 19),
    )
    assert context["mode"] == "Admin catch-up"
    assert context["target_label"] == "admin blocks"


def test_resolve_overview_day_context_wednesday_wfh_mixed_day():
    overview_settings = {
        "day_mode": "Auto",
        "patient_target": 25,
        "procedure_target": 8,
        "shift_minutes": 480,
        "clinic_weekdays": ["Thursday", "Monday"],
        "admin_weekdays": ["Tuesday", "Wednesday"],
        "procedure_friday_frequency_weeks": 2,
        "procedure_friday_cycle_offset": 0,
    }
    context = resolve_overview_day_context(
        overview_settings,
        active_tasks=[],
        personal_tasks=[],
        clinic_tasks=[],
        reference_date=date(2026, 5, 20),
    )
    assert context["mode"] == "Mixed day"
    assert "work-from-home" in context["reason_text"]


def test_resolve_overview_day_context_manual_override():
    overview_settings = {
        "day_mode": "Outpatient clinic",
        "patient_target": 25,
        "procedure_target": 8,
        "shift_minutes": 480,
        "clinic_weekdays": ["Thursday", "Monday"],
        "admin_weekdays": ["Wednesday"],
        "procedure_friday_frequency_weeks": 2,
        "procedure_friday_cycle_offset": 0,
    }
    context = resolve_overview_day_context(
        overview_settings,
        active_tasks=[],
        personal_tasks=[],
        clinic_tasks=[],
        reference_date=date(2026, 5, 20),
    )
    assert context["mode"] == "Outpatient clinic"
    assert context["reason_text"] == "You pinned this mode manually."
