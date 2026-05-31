from datetime import date, datetime, timedelta


def _safe_int(value, fallback=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _parse_date(raw_value):
    if raw_value is None:
        return None
    if isinstance(raw_value, date) and not isinstance(raw_value, datetime):
        return raw_value
    if isinstance(raw_value, datetime):
        return raw_value.date()
    if isinstance(raw_value, str):
        value = raw_value.strip()
        if not value:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def family_goal_week_start(reference_date=None):
    anchor = reference_date or date.today()
    return anchor - timedelta(days=anchor.weekday())


def normalize_family_goals(raw_goals, reference_date=None):
    if not isinstance(raw_goals, list):
        return []

    today_value = reference_date or date.today()
    week_start = family_goal_week_start(today_value)
    normalized = []

    for source_index, raw_goal in enumerate(raw_goals):
        if not isinstance(raw_goal, dict):
            continue

        title = str(raw_goal.get("title") or "").strip()
        if not title:
            continue

        status = str(raw_goal.get("status") or "active").strip().lower()
        if status not in ("active", "paused", "completed"):
            status = "active"

        target_frequency = max(1, min(14, _safe_int(raw_goal.get("target_frequency"), 3)))

        checkin_dates = []
        for value in (raw_goal.get("checkin_dates") or []):
            parsed = _parse_date(value)
            if parsed:
                checkin_dates.append(parsed)
        checkin_dates = sorted(set(checkin_dates))

        current_streak = 0
        if checkin_dates:
            cursor = checkin_dates[-1]
            current_streak = 1
            while (cursor - timedelta(days=1)) in checkin_dates:
                cursor = cursor - timedelta(days=1)
                current_streak += 1

        last_checkin_date = max(checkin_dates) if checkin_dates else None
        week_checkins = len([value for value in checkin_dates if value >= week_start])

        normalized.append(
            {
                "goal_id": str(raw_goal.get("goal_id") or f"family_goal_{source_index}_{title.lower().replace(' ', '_')}").strip(),
                "source_index": source_index,
                "title": title,
                "owner": str(raw_goal.get("owner") or raw_goal.get("family_member") or "Family").strip() or "Family",
                "target_frequency": target_frequency,
                "notes": str(raw_goal.get("notes") or "").strip(),
                "status": status,
                "created_date": _parse_date(raw_goal.get("created_date")),
                "checkin_dates": checkin_dates,
                "last_checkin_date": last_checkin_date,
                "week_checkins": week_checkins,
                "total_checkins": len(checkin_dates),
                "current_streak": current_streak,
                "today_checked_in": today_value in checkin_dates,
            }
        )

    status_order = {"active": 0, "paused": 1, "completed": 2}
    return sorted(
        normalized,
        key=lambda item: (
            status_order.get(item.get("status"), 9),
            -(item.get("week_checkins") or 0),
            item.get("title") or "",
        ),
    )


def family_goal_dashboard_summary(family_goals):
    active_goals = [goal for goal in family_goals if goal.get("status") == "active"]
    on_track_goals = [
        goal for goal in active_goals if int(goal.get("week_checkins") or 0) >= int(goal.get("target_frequency") or 1)
    ]
    attention_goals = [
        goal for goal in active_goals if int(goal.get("week_checkins") or 0) < int(goal.get("target_frequency") or 1)
    ]

    return {
        "active_goals": active_goals,
        "on_track_goals": on_track_goals,
        "attention_goals": attention_goals,
        "week_checkins": sum(int(goal.get("week_checkins") or 0) for goal in active_goals),
        "total_checkins": sum(int(goal.get("total_checkins") or 0) for goal in family_goals),
        "best_streak": max([int(goal.get("current_streak") or 0) for goal in family_goals], default=0),
    }
