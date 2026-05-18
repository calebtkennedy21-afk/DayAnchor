from datetime import date, time, timedelta

from scheduling_core import priority_rank, safe_int


def clinic_day_profiles(app_settings):
    patient_target = safe_int(app_settings.get("surgeon_clinic_patient_target", 25), 25)
    general_patient_target = safe_int(app_settings.get("general_clinic_patient_target", 25), 25)
    procedure_target = safe_int(app_settings.get("procedure_friday_procedure_target", 8), 8)
    visit_minutes = max(8, safe_int(app_settings.get("clinic_visit_minutes", 12), 12))
    admin_buffer = max(30, safe_int(app_settings.get("clinic_admin_buffer_minutes", 60), 60))
    procedure_block_minutes = max(20, safe_int(app_settings.get("procedure_block_minutes", 30), 30))

    return {
        "surgeon_clinic": {
            "key": "surgeon_clinic",
            "label": "Surgeon clinic day",
            "volume_label": "patients",
            "volume_target": patient_target,
            "visit_minutes": visit_minutes,
            "prep_minutes": 30,
            "admin_buffer_minutes": admin_buffer,
            "focus": "front-load patient flow, protect note-writing time, and leave slack for follow-ups.",
        },
        "general_clinic": {
            "key": "general_clinic",
            "label": "General clinic day",
            "volume_label": "patients",
            "volume_target": general_patient_target,
            "visit_minutes": visit_minutes,
            "prep_minutes": 30,
            "admin_buffer_minutes": admin_buffer,
            "focus": "treat this like a steady patient-volume day with minimal context switching.",
        },
        "procedure_friday": {
            "key": "procedure_friday",
            "label": "Procedure Friday",
            "volume_label": "procedures",
            "volume_target": procedure_target,
            "visit_minutes": procedure_block_minutes,
            "prep_minutes": 45,
            "admin_buffer_minutes": max(45, admin_buffer),
            "focus": "optimize room turnover, pre-charting, and post-procedure documentation.",
        },
    }


def build_time_blocks(profile):
    total_minutes = 8 * 60
    core_minutes = max(120, total_minutes - profile["prep_minutes"] - profile["admin_buffer_minutes"])
    per_block_minutes = max(15, min(profile["visit_minutes"], profile["visit_minutes"] if profile["key"] != "procedure_friday" else profile["visit_minutes"]))
    if profile["key"] == "procedure_friday":
        per_block_minutes = max(20, profile["visit_minutes"])
    target_count = max(1, profile["volume_target"])
    estimated_blocks = max(2, min(target_count, core_minutes // per_block_minutes))
    block_minutes = max(15, core_minutes // estimated_blocks)

    morning_volume = max(1, estimated_blocks // 2)
    afternoon_volume = max(1, estimated_blocks - morning_volume)

    return {
        "total_minutes": total_minutes,
        "core_minutes": core_minutes,
        "block_minutes": block_minutes,
        "estimated_blocks": estimated_blocks,
        "morning_volume": morning_volume,
        "afternoon_volume": afternoon_volume,
        "slack_minutes": max(0, total_minutes - profile["prep_minutes"] - profile["admin_buffer_minutes"] - (estimated_blocks * block_minutes)),
    }


def clinic_day_summary(clinic_tasks, active_tasks, app_settings, mode_key, reference_date=None):
    today = reference_date or date.today()
    profiles = clinic_day_profiles(app_settings)
    profile = profiles.get(mode_key, profiles["general_clinic"])
    block_plan = build_time_blocks(profile)
    clinic_open = [task for task in clinic_tasks if task.get("status") != "completed"]
    top_clinic_tasks = sorted(clinic_open, key=lambda task: (priority_rank(task["priority"]), task.get("due_date") or date.max))[:5]
    clinic_unscheduled = [task for task in clinic_open if not (task.get("scheduled_date") and task.get("scheduled_time"))]
    due_soon = [task for task in clinic_open if task.get("due_date") and task["due_date"] <= today + timedelta(days=3)]

    return {
        "profile": profile,
        "block_plan": block_plan,
        "top_clinic_tasks": top_clinic_tasks,
        "clinic_unscheduled_count": len(clinic_unscheduled),
        "due_soon_count": len(due_soon),
        "active_clinic_count": len(clinic_open),
        "clinic_backlog_count": len([task for task in active_tasks if task.get("category") == "Clinic"]),
    }


def personal_focus_summary(personal_tasks, active_tasks, app_settings):
    focus_minutes = safe_int(app_settings.get("personal_focus_minutes", 90), 90)
    sorted_tasks = sorted(personal_tasks, key=lambda task: (priority_rank(task["priority"]), task.get("due_date") or date.max))
    focus_tasks = sorted_tasks[:5]
    focus_driver = focus_tasks[0] if focus_tasks else None
    focus_name = focus_driver["title"] if focus_driver else "No personal task ready"
    total_personal = len([task for task in active_tasks if task.get("category") == "Personal"])
    return {
        "focus_minutes": focus_minutes,
        "focus_tasks": focus_tasks,
        "focus_name": focus_name,
        "personal_count": total_personal,
        "blocked_count": len([task for task in personal_tasks if task.get("status") == "blocked"]),
    }


def schedule_workload_snapshot(active_tasks):
    upcoming = sorted(
        [task for task in active_tasks if task.get("scheduled_date") and task.get("scheduled_time")],
        key=lambda task: (task["scheduled_date"], task["scheduled_time"], priority_rank(task["priority"])),
    )
    unscheduled = [task for task in active_tasks if not (task.get("scheduled_date") and task.get("scheduled_time"))]
    return {
        "upcoming": upcoming,
        "unscheduled": unscheduled,
        "unscheduled_high": [task for task in unscheduled if task.get("priority") == "high"],
        "capacity_gap": len(unscheduled) - len(upcoming),
    }


def overview_runtime_settings(app_settings):
    return {
        "day_mode": app_settings.get("overview_day_mode", "Auto"),
        "role_label": app_settings.get("overview_role_label", "Medical Assistant"),
        "site_label": app_settings.get("overview_site_label", "Mercy Orthopedics"),
        "patient_target": safe_int(app_settings.get("overview_patient_target", 25), 25),
        "procedure_target": safe_int(app_settings.get("overview_procedure_target", 8), 8),
        "admin_buffer_minutes": safe_int(app_settings.get("overview_admin_buffer_minutes", 60), 60),
        "shift_minutes": safe_int(app_settings.get("overview_shift_minutes", 480), 480),
        "focus_window_minutes": safe_int(app_settings.get("overview_focus_window_minutes", 90), 90),
        "clinic_weekdays": app_settings.get("overview_clinic_weekdays", ["Thursday", "Monday"]),
        "admin_weekdays": app_settings.get("overview_admin_weekdays", ["Tuesday"]),
        "procedure_friday_frequency_weeks": safe_int(app_settings.get("overview_procedure_friday_frequency_weeks", 2), 2),
        "procedure_friday_cycle_offset": safe_int(app_settings.get("overview_procedure_friday_cycle_offset", 0), 0),
    }


def resolve_overview_day_context(overview_settings, active_tasks, personal_tasks, clinic_tasks, reference_date=None):
    today = reference_date or date.today()
    mode = overview_settings.get("day_mode", "Auto")
    weekday_name = today.strftime("%A")
    clinic_weekdays = overview_settings.get("clinic_weekdays") or ["Monday", "Thursday"]
    admin_weekdays = overview_settings.get("admin_weekdays") or ["Tuesday"]
    cadence_weeks = max(1, safe_int(overview_settings.get("procedure_friday_frequency_weeks", 2), 2))
    cycle_offset = safe_int(overview_settings.get("procedure_friday_cycle_offset", 0), 0)
    week_number = today.isocalendar().week

    auto_mode = "Mixed day"
    reason_text = "Use the board signal to stay flexible when the weekly pattern is unclear."
    if today.weekday() == 4 and ((week_number + cycle_offset) % cadence_weeks == 0):
        auto_mode = "Procedure Friday"
        reason_text = f"Friday matches the {cadence_weeks}-week procedure cadence."
    elif weekday_name == "Wednesday":
        auto_mode = "Mixed day"
        reason_text = "Wednesday is a work-from-home catch-up day, so the board balances personal and clinic work."
    elif weekday_name in admin_weekdays:
        auto_mode = "Admin catch-up"
        reason_text = f"{weekday_name} is marked as an admin catch-up day in your settings."
    elif weekday_name in clinic_weekdays:
        auto_mode = "Outpatient clinic"
        reason_text = f"{weekday_name} is marked as a clinic day in your settings."
    elif len([task for task in clinic_tasks if task.get("priority") == "high"]) > len(personal_tasks):
        auto_mode = "Outpatient clinic"
        reason_text = "Clinic pressure is heavier than personal work, so the page is leaning toward patient flow."
    elif len(personal_tasks) > len(clinic_tasks):
        auto_mode = "Mixed day"
        reason_text = "Personal work is heavier, so the page keeps the day balanced instead of clinic-dominant."

    resolved_mode = auto_mode if mode == "Auto" else mode
    if mode != "Auto":
        reason_text = "You pinned this mode manually."

    if resolved_mode == "Procedure Friday":
        target_label = "procedures"
        target_value = overview_settings["procedure_target"]
        focus_text = "Prioritize room turnover, pre-charting, and post-procedure documentation."
        signal_text = "Keep procedures contiguous and protect charting time."
    elif resolved_mode == "Admin catch-up":
        target_label = "admin blocks"
        target_value = max(2, overview_settings["shift_minutes"] // 120)
        focus_text = "Use the day for documentation, inbox cleanup, results follow-up, and callbacks."
        signal_text = "Minimize patient-facing interruptions and batch the desk work."
    elif resolved_mode == "Mixed day":
        target_label = "work blocks"
        target_value = max(overview_settings["patient_target"] // 3, 6)
        focus_text = "Balance outpatient flow with personal catch-up and preserve one buffer block."
        signal_text = "Switch tasks only when the clinic queue or schedule demands it."
    else:
        target_label = "patients"
        target_value = overview_settings["patient_target"]
        focus_text = "Front-load the patient queue, protect note-writing time, and hold a buffer for spillover."
        signal_text = "Keep the day moving while leaving slack for walk-ins, calls, and documentation."

    clinic_pressure = len([task for task in clinic_tasks if task.get("status") != "completed"])
    personal_pressure = len([task for task in personal_tasks if task.get("status") != "completed"])
    active_pressure = len([task for task in active_tasks if task.get("priority") == "high" and not (task.get("scheduled_date") and task.get("scheduled_time"))])

    return {
        "mode": resolved_mode,
        "target_label": target_label,
        "target_value": target_value,
        "focus_text": focus_text,
        "signal_text": signal_text,
        "reason_text": reason_text,
        "clinic_pressure": clinic_pressure,
        "personal_pressure": personal_pressure,
        "active_pressure": active_pressure,
        "timeline_window_minutes": overview_settings["shift_minutes"],
    }
