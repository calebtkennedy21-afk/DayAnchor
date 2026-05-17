from datetime import date, time, timedelta


def shift_date_by_rule(value, rule, interval):
    if not value:
        return None
    safe_interval = max(1, int(interval or 1))
    if rule == "daily":
        return value + timedelta(days=safe_interval)
    if rule == "weekly":
        return value + timedelta(days=7 * safe_interval)
    return value


def priority_rank(priority):
    return {"high": 0, "medium": 1, "low": 2}.get(priority, 1)


def task_attention_signal(task, reference_date=None):
    today = reference_date or date.today()
    due_date = task.get("due_date")
    created_date = task.get("created_date")
    scheduled_date = task.get("scheduled_date")
    scheduled_time = task.get("scheduled_time")

    age_days = max(0, (today - created_date).days) if hasattr(created_date, "toordinal") else 0
    overdue_days = max(0, (today - due_date).days) if hasattr(due_date, "toordinal") else 0
    due_in_days = (due_date - today).days if hasattr(due_date, "toordinal") else None
    has_schedule = bool(scheduled_date and scheduled_time)
    high_unscheduled = task.get("priority") == "high" and not has_schedule

    if overdue_days > 0:
        return {
            "tier": 0,
            "sort_key": (0, -overdue_days, priority_rank(task.get("priority")), due_date or date.min),
            "label": f"Overdue by {overdue_days}d",
            "detail": f"{overdue_days} day(s) overdue",
            "age_days": age_days,
            "overdue_days": overdue_days,
            "due_in_days": due_in_days,
            "high_unscheduled": high_unscheduled,
        }

    if due_date == today:
        return {
            "tier": 1,
            "sort_key": (1, priority_rank(task.get("priority")), scheduled_time or time(23, 59), -age_days),
            "label": "Due today",
            "detail": "Due today",
            "age_days": age_days,
            "overdue_days": 0,
            "due_in_days": 0,
            "high_unscheduled": high_unscheduled,
        }

    if high_unscheduled:
        attention_label = f"Aging {age_days}d" if age_days else "High priority"
        return {
            "tier": 2,
            "sort_key": (2, -age_days, priority_rank(task.get("priority")), due_date or date.max),
            "label": attention_label,
            "detail": "High-priority task waiting for a slot",
            "age_days": age_days,
            "overdue_days": 0,
            "due_in_days": due_in_days,
            "high_unscheduled": True,
        }

    if due_in_days is not None and due_in_days <= 3:
        return {
            "tier": 3,
            "sort_key": (3, due_in_days, priority_rank(task.get("priority")), -age_days),
            "label": f"Due in {due_in_days}d",
            "detail": "Due soon",
            "age_days": age_days,
            "overdue_days": 0,
            "due_in_days": due_in_days,
            "high_unscheduled": False,
        }

    return {
        "tier": 4,
        "sort_key": (4, due_date or date.max, priority_rank(task.get("priority")), -age_days),
        "label": f"Age {age_days}d" if age_days >= 7 else "Routine",
        "detail": "Routine",
        "age_days": age_days,
        "overdue_days": 0,
        "due_in_days": due_in_days,
        "high_unscheduled": False,
    }


def task_attention_sort_key(task, reference_date=None):
    return task_attention_signal(task, reference_date).get("sort_key")


def safe_int(value, fallback):
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(fallback)


def clinic_visit_templates():
    return {
        "blank": {
            "label": "Blank clinic capture",
            "title": "",
            "description": "",
            "priority": "medium",
            "schedule_enabled": False,
            "scheduled_time": time(9, 0),
            "scheduled_minutes": 30,
        },
        "new_consult": {
            "label": "New consult",
            "title": "Clinic consult block",
            "description": "Initial evaluation, exam, imaging review, and plan discussion.",
            "priority": "high",
            "schedule_enabled": True,
            "scheduled_time": time(8, 30),
            "scheduled_minutes": 45,
        },
        "post_op_follow_up": {
            "label": "Post-op follow-up",
            "title": "Post-op follow-up",
            "description": "Wound check, symptom review, restrictions, and next-step planning.",
            "priority": "medium",
            "schedule_enabled": True,
            "scheduled_time": time(9, 15),
            "scheduled_minutes": 20,
        },
        "imaging_review": {
            "label": "Imaging review",
            "title": "Imaging review visit",
            "description": "Review studies, confirm the working diagnosis, and define the next step.",
            "priority": "medium",
            "schedule_enabled": True,
            "scheduled_time": time(10, 0),
            "scheduled_minutes": 20,
        },
        "procedure_checkin": {
            "label": "Procedure check-in",
            "title": "Procedure planning visit",
            "description": "Procedure discussion, consent prep, and day-of logistics.",
            "priority": "high",
            "schedule_enabled": True,
            "scheduled_time": time(11, 0),
            "scheduled_minutes": 30,
        },
        "phone_follow_up": {
            "label": "Phone follow-up",
            "title": "Phone follow-up",
            "description": "Brief check-in, results review, and next steps without an in-person slot.",
            "priority": "low",
            "schedule_enabled": False,
            "scheduled_time": time(9, 0),
            "scheduled_minutes": 15,
        },
        "urgent_add_on": {
            "label": "Urgent add-on",
            "title": "Urgent add-on visit",
            "description": "High-priority add-on with focused assessment and rapid decision-making.",
            "priority": "high",
            "schedule_enabled": True,
            "scheduled_time": time(13, 0),
            "scheduled_minutes": 20,
        },
    }


def personal_schedule_templates():
    return {
        "blank": {
            "label": "Blank — custom block",
            "title": "",
            "description": "",
            "priority": "medium",
            "scheduled_time": time(18, 0),
            "scheduled_minutes": 60,
            "all_day": False,
        },
        "dinner": {
            "label": "Dinner",
            "title": "Dinner",
            "description": "Protected personal time for dinner, social plans, or a calm evening block.",
            "priority": "medium",
            "scheduled_time": time(18, 30),
            "scheduled_minutes": 90,
            "all_day": False,
        },
        "event": {
            "label": "Event",
            "title": "Evening event",
            "description": "Concert, outing, family event, or another fixed personal commitment.",
            "priority": "medium",
            "scheduled_time": time(19, 0),
            "scheduled_minutes": 120,
            "all_day": False,
        },
        "appointment": {
            "label": "Appointment",
            "title": "Personal appointment",
            "description": "Medical, dental, or life-admin appointment that needs a real calendar slot.",
            "priority": "high",
            "scheduled_time": time(9, 0),
            "scheduled_minutes": 60,
            "all_day": False,
        },
        "travel": {
            "label": "Travel",
            "title": "Travel block",
            "description": "Transit, airport time, or commute buffer.",
            "priority": "medium",
            "scheduled_time": time(8, 0),
            "scheduled_minutes": 180,
            "all_day": False,
        },
        "vacation": {
            "label": "Vacation / trip",
            "title": "Vacation block",
            "description": "All-day personal block for a trip, day off, or protected downtime.",
            "priority": "low",
            "scheduled_time": time(8, 0),
            "scheduled_minutes": 480,
            "all_day": True,
            "scheduled_end_offset_days": 4,
        },
        "clinic_shift": {
            "label": "Clinic shift",
            "title": "Clinic shift",
            "description": "Scheduled clinic session or on-call block.",
            "priority": "high",
            "scheduled_time": time(7, 0),
            "scheduled_minutes": 480,
            "all_day": False,
        },
        "meeting": {
            "label": "Meeting",
            "title": "Meeting",
            "description": "Team meeting, case conference, or scheduled call.",
            "priority": "medium",
            "scheduled_time": time(10, 0),
            "scheduled_minutes": 60,
            "all_day": False,
        },
    }


def scheduled_date_range(task):
    scheduled_date = task.get("scheduled_date")
    if not scheduled_date:
        return []
    scheduled_end_date = task.get("scheduled_end_date") or scheduled_date
    if scheduled_end_date < scheduled_date:
        scheduled_end_date = scheduled_date
    span = (scheduled_end_date - scheduled_date).days
    return [scheduled_date + timedelta(days=offset) for offset in range(span + 1)]


def scheduled_span_position(task, day):
    dates = scheduled_date_range(task)
    if not dates or day not in dates:
        return None
    if len(dates) == 1:
        return "single"
    if day == dates[0]:
        return "start"
    if day == dates[-1]:
        return "end"
    return "middle"


def scheduled_minutes_on_day(task, day):
    if day not in scheduled_date_range(task):
        return 0
    try:
        return max(0, int(task.get("scheduled_minutes") or 0))
    except (TypeError, ValueError):
        return 0


def build_week_rebalance_moves(upcoming_tasks, week_days, daily_capacity_minutes):
    scheduled_by_day = {day: [] for day in week_days}
    scheduled_minutes_by_day = {day: 0 for day in week_days}

    for task in upcoming_tasks:
        for task_day in scheduled_date_range(task):
            if task_day in scheduled_by_day:
                scheduled_by_day[task_day].append(task)
                scheduled_minutes_by_day[task_day] += scheduled_minutes_on_day(task, task_day)

    moves = []
    moved_task_ids = set()
    overloaded_days = [day for day in week_days if scheduled_minutes_by_day[day] > daily_capacity_minutes]

    for source_day in overloaded_days:
        if scheduled_minutes_by_day[source_day] <= daily_capacity_minutes:
            continue

        # Rebalance only low-priority single-day blocks so the action is safe and predictable.
        candidates = [
            task
            for task in scheduled_by_day[source_day]
            if task.get("id") not in moved_task_ids
            and task.get("priority") == "low"
            and task.get("scheduled_date") == source_day
            and (task.get("scheduled_end_date") is None or task.get("scheduled_end_date") == source_day)
            and task.get("scheduled_time")
            and scheduled_minutes_on_day(task, source_day) > 0
        ]
        candidates.sort(
            key=lambda task: (
                task.get("due_date") or date.max,
                task.get("scheduled_time") or time(23, 59),
            ),
            reverse=True,
        )

        for task in candidates:
            if scheduled_minutes_by_day[source_day] <= daily_capacity_minutes:
                break

            task_minutes = scheduled_minutes_on_day(task, source_day)
            if task_minutes <= 0:
                continue

            target_day = None
            search_days = [day for day in week_days if day > source_day] + [day for day in week_days if day < source_day]
            for candidate_day in search_days:
                if scheduled_minutes_by_day[candidate_day] + task_minutes > daily_capacity_minutes:
                    continue
                due_date = task.get("due_date")
                if due_date and candidate_day > due_date:
                    continue
                target_day = candidate_day
                break

            if not target_day:
                continue

            moves.append({"task": task, "source_day": source_day, "target_day": target_day, "minutes": task_minutes})
            moved_task_ids.add(task.get("id"))
            scheduled_minutes_by_day[source_day] -= task_minutes
            scheduled_minutes_by_day[target_day] += task_minutes

    return moves
