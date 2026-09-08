from collections import Counter
from datetime import date, timedelta, time


def _safe_int(value, fallback=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _parse_date(value):
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def _coerce_date(value):
    if isinstance(value, date):
        return value
    if hasattr(value, "date"):
        try:
            return value.date()
        except Exception:
            return None
    return None


def _build_insight(insight_id, title, detail, evidence, action, confidence, task_payload):
    return {
        "id": insight_id,
        "title": title,
        "detail": detail,
        "evidence": evidence,
        "action": action,
        "confidence": max(0, min(100, int(confidence))),
        "task_payload": task_payload,
    }


def _fallback_next_weekday_date(weekday_name, reference_day=None):
    anchor = reference_day or date.today()
    name_to_index = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    target = name_to_index.get(str(weekday_name or "").strip().lower())
    if target is None:
        return None
    delta = (target - anchor.weekday()) % 7
    return anchor + timedelta(days=delta)


def build_brain_context(
    today_value,
    active_tasks,
    family_items,
    morning_checkins,
    life_entries,
    default_duration,
    default_time,
    life_categories,
):
    return {
        "today_value": today_value,
        "active_tasks": list(active_tasks or []),
        "family_items": list(family_items or []),
        "morning_checkins": dict(morning_checkins or {}),
        "life_entries": list(life_entries or []),
        "default_duration": max(15, _safe_int(default_duration, 60)),
        "default_time": default_time if isinstance(default_time, time) else time(9, 0),
        "life_categories": list(life_categories or []),
    }


def generate_brain_signals(context, next_weekday_date_fn=None):
    today_value = context["today_value"]
    active_tasks = context["active_tasks"]
    family_items = context["family_items"]
    morning_checkins = context["morning_checkins"]
    life_entries = context["life_entries"]
    default_duration = context["default_duration"]
    default_time = context["default_time"]
    life_categories = context["life_categories"]

    if not next_weekday_date_fn:
        next_weekday_date_fn = _fallback_next_weekday_date

    insights = []

    overdue_clinic = [
        item
        for item in active_tasks
        if item.get("category") == "Clinic" and item.get("due_date") and item.get("due_date") < today_value
    ]
    this_week_start = today_value - timedelta(days=today_value.weekday())
    last_week_start = this_week_start - timedelta(days=7)
    last_week_end = this_week_start - timedelta(days=1)

    overdue_this_week = [
        item
        for item in overdue_clinic
        if item.get("due_date") and item["due_date"] >= this_week_start
    ]
    overdue_last_week = [
        item
        for item in overdue_clinic
        if item.get("due_date") and last_week_start <= item["due_date"] <= last_week_end
    ]

    if overdue_this_week or overdue_last_week:
        delta = len(overdue_this_week) - len(overdue_last_week)
        direction = "up" if delta > 0 else "down" if delta < 0 else "flat"
        confidence = 55 + min(35, (len(overdue_this_week) + len(overdue_last_week)) * 4)
        insights.append(
            _build_insight(
                "clinic_overdue_wow",
                "Clinic overdue week-over-week pressure",
                f"Current week overdue clinic load is {len(overdue_this_week)} vs {len(overdue_last_week)} last week ({direction}, {delta:+d}).",
                "Computed from active overdue clinic tasks grouped by ISO week.",
                "If pressure is rising, run a 20-minute clinic triage block early each weekday.",
                confidence,
                {
                    "title": "Clinic triage block (intelligence)",
                    "description": "20-minute weekday triage to prevent overdue clinic buildup.",
                    "category": "Clinic",
                    "priority": "high" if delta > 0 else "medium",
                    "due_date": today_value,
                    "scheduled_date": today_value,
                    "scheduled_time": time(8, 0),
                    "scheduled_minutes": 20,
                },
            )
        )

    overdue_by_weekday = Counter(item["due_date"].strftime("%A") for item in overdue_clinic if item.get("due_date"))
    if overdue_by_weekday:
        top_day, top_count = overdue_by_weekday.most_common(1)[0]
        top_share = top_count / max(1, len(overdue_clinic))
        confidence = 50 + min(45, int(top_share * 100 * 0.5) + top_count * 4)
        if top_day == "Wednesday" and top_count >= 2:
            insights.append(
                _build_insight(
                    "wednesday_backlog",
                    "Wednesday clinic backlog pattern",
                    f"You currently have {top_count} overdue clinic tasks landing on Wednesday, your highest-overdue weekday.",
                    f"Wednesday represents {int(round(top_share * 100))}% of overdue clinic tasks.",
                    "Front-load Tuesday scheduling and add a Wednesday noon backlog sweep block.",
                    confidence,
                    {
                        "title": "Wednesday backlog sweep (intelligence)",
                        "description": "Clear high-risk clinic carryover before late-week pileup.",
                        "category": "Clinic",
                        "priority": "high",
                        "due_date": today_value,
                        "scheduled_date": next_weekday_date_fn("Wednesday", reference_day=today_value - timedelta(days=1)) or today_value,
                        "scheduled_time": time(12, 0),
                        "scheduled_minutes": 30,
                    },
                )
            )
        else:
            insights.append(
                _build_insight(
                    "clinic_overdue_concentration",
                    "Clinic overdue concentration",
                    f"{top_day} has the highest overdue clinic load ({top_count} task(s)).",
                    f"Top-day share: {int(round(top_share * 100))}% of overdue clinic tasks.",
                    f"Protect one recurring {top_day} triage block to prevent spillover.",
                    confidence,
                    {
                        "title": f"{top_day} clinic triage block (intelligence)",
                        "description": "Recurring triage block to reduce concentrated overdue load.",
                        "category": "Clinic",
                        "priority": "medium",
                        "due_date": today_value,
                        "scheduled_date": next_weekday_date_fn(top_day, reference_day=today_value - timedelta(days=1)) or today_value,
                        "scheduled_time": time(8, 30),
                        "scheduled_minutes": 25,
                    },
                )
            )

    family_week_bucket_counts = Counter()
    for item in family_items:
        start_day = item.get("start_date")
        if not isinstance(start_day, date):
            continue
        week_bucket = ((start_day.day - 1) // 7) + 1
        family_week_bucket_counts[week_bucket] += 1
    if family_week_bucket_counts:
        ranked_buckets = family_week_bucket_counts.most_common()
        top_bucket, bucket_count = ranked_buckets[0]
        second_count = ranked_buckets[1][1] if len(ranked_buckets) > 1 else 0
        suffix = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth"}.get(top_bucket, f"week {top_bucket}")
        gap = bucket_count - second_count
        confidence = 45 + min(45, bucket_count * 5 + max(0, gap * 8))
        insights.append(
            _build_insight(
                "family_monthly_rhythm",
                "Family schedule monthly rhythm",
                f"Family events are heaviest in the {suffix} week of each month ({bucket_count} item(s) in your current dataset).",
                f"Lead over next busiest week: {gap:+d} item(s).",
                f"Plan prep/admin tasks in the week before the {suffix} week to reduce conflict pressure.",
                confidence,
                {
                    "title": "Family prep buffer (intelligence)",
                    "description": f"Prep block before the {suffix} week to lower family conflict risk.",
                    "category": "Personal",
                    "priority": "medium",
                    "due_date": today_value + timedelta(days=3),
                    "scheduled_date": today_value + timedelta(days=2),
                    "scheduled_time": time(18, 30),
                    "scheduled_minutes": 45,
                },
            )
        )

    ritual_rows = []
    for day_key, entry in morning_checkins.items():
        parsed_day = _parse_date(day_key)
        if not parsed_day:
            continue
        ritual_rows.append(
            {
                "day": parsed_day,
                "sleep_quality": entry.get("sleep_quality"),
                "energy_level": entry.get("energy_level"),
                "grounding": bool(entry.get("optional_grounding_complete")),
                "morning_goals": entry.get("planned_morning_goals"),
            }
        )
    ritual_rows.sort(key=lambda row: row["day"])

    if len(ritual_rows) >= 4:
        poor_sleep = [row for row in ritual_rows if row["sleep_quality"] in ("Poor", "Fair")]
        good_sleep = [row for row in ritual_rows if row["sleep_quality"] in ("Good", "Great")]
        poor_sleep_low_energy = [row for row in poor_sleep if row["energy_level"] == "Low"]
        good_sleep_low_energy = [row for row in good_sleep if row["energy_level"] == "Low"]

        poor_rate = (len(poor_sleep_low_energy) / len(poor_sleep)) if poor_sleep else 0.0
        good_rate = (len(good_sleep_low_energy) / len(good_sleep)) if good_sleep else 0.0

        if poor_sleep and good_sleep:
            rate_diff = poor_rate - good_rate
            confidence = 40 + min(50, len(poor_sleep) * 5 + len(good_sleep) * 3 + int(abs(rate_diff) * 100 * 0.4))
            insights.append(
                _build_insight(
                    "sleep_energy_pattern",
                    "Sleep-energy pattern",
                    (
                        f"Low energy appears on {int(round(poor_rate * 100))}% of Poor/Fair sleep days vs "
                        f"{int(round(good_rate * 100))}% of Good/Great sleep days."
                    ),
                    f"Sample size: {len(poor_sleep)} low-sleep days and {len(good_sleep)} recovered-sleep days.",
                    "On Poor/Fair sleep mornings, reduce context switching and schedule one protected focus block first.",
                    confidence,
                    {
                        "title": "Low-sleep morning focus block (intelligence)",
                        "description": "Single protected focus block for low-energy mornings.",
                        "category": "Personal",
                        "priority": "medium",
                        "due_date": today_value,
                        "scheduled_date": today_value,
                        "scheduled_time": default_time,
                        "scheduled_minutes": default_duration,
                    },
                )
            )

        grounding_days = [row for row in ritual_rows if row["grounding"]]
        non_grounding_days = [row for row in ritual_rows if not row["grounding"]]
        grounding_goal_yes = [row for row in grounding_days if row["morning_goals"] == "Yes"]
        non_grounding_goal_yes = [row for row in non_grounding_days if row["morning_goals"] == "Yes"]
        if len(grounding_days) >= 2 and len(non_grounding_days) >= 2:
            grounding_rate = len(grounding_goal_yes) / len(grounding_days)
            non_grounding_rate = len(non_grounding_goal_yes) / len(non_grounding_days)
            lift = grounding_rate - non_grounding_rate
            confidence = 42 + min(50, len(grounding_days) * 4 + len(non_grounding_days) * 4 + int(abs(lift) * 100 * 0.4))
            insights.append(
                _build_insight(
                    "grounding_execution_correlation",
                    "Grounding-to-execution correlation",
                    (
                        f"Morning-goal completion is {int(round(grounding_rate * 100))}% on grounding days "
                        f"vs {int(round(non_grounding_rate * 100))}% on non-grounding days."
                    ),
                    f"Completion-rate lift: {int(round(lift * 100)):+d} points.",
                    "Keep the reading/grounding habit before high-stakes days to improve follow-through.",
                    confidence,
                    {
                        "title": "Reading/grounding ritual checkpoint (intelligence)",
                        "description": "Keep morning grounding consistent before key execution windows.",
                        "category": "Personal",
                        "priority": "medium",
                        "due_date": today_value,
                    },
                )
            )

    if len(life_entries) >= 6:
        recent = life_entries[:3]
        prior = life_entries[3:6]
        trend_rows = []
        for cat in life_categories:
            key = cat.get("key")
            label = cat.get("label")
            if not key or not label:
                continue
            recent_scores = [entry.get("scores", {}).get(key) for entry in recent if entry.get("scores", {}).get(key) is not None]
            prior_scores = [entry.get("scores", {}).get(key) for entry in prior if entry.get("scores", {}).get(key) is not None]
            if not recent_scores or not prior_scores:
                continue
            recent_avg = sum(recent_scores) / len(recent_scores)
            prior_avg = sum(prior_scores) / len(prior_scores)
            delta = round(recent_avg - prior_avg, 2)
            trend_rows.append((delta, label))
        if trend_rows:
            trend_rows.sort(key=lambda item: item[0])
            most_down = trend_rows[0]
            most_up = trend_rows[-1]
            confidence = 50 + min(40, len(trend_rows) * 4 + int(abs(most_down[0] - most_up[0]) * 12))
            insights.append(
                _build_insight(
                    "life_directional_trend",
                    "Life area directional trend",
                    (
                        f"Largest upward shift: {most_up[1]} ({most_up[0]:+0.2f}). "
                        f"Largest downward shift: {most_down[1]} ({most_down[0]:+0.2f}) over the last 6 weeks."
                    ),
                    "Derived from two 3-week windows in Life Dashboard scoring history.",
                    f"Protect recent gains in {most_up[1]} and set one corrective micro-goal for {most_down[1]} this week.",
                    confidence,
                    {
                        "title": f"Life micro-goal: {most_down[1]} (intelligence)",
                        "description": f"Corrective micro-goal based on 6-week downward trend in {most_down[1]}.",
                        "category": "Personal",
                        "priority": "medium",
                        "due_date": today_value + timedelta(days=2),
                    },
                )
            )

    insights = sorted(insights, key=lambda item: item.get("confidence", 0), reverse=True)
    return {
        "insights": insights,
        "metrics": {
            "overdue_clinic_count": len(overdue_clinic),
            "morning_checkin_count": len(ritual_rows),
            "family_item_count": len(family_items),
        },
    }


def build_case_signal_snapshot(today_value, surgical_cases, protocol_documents, protocol_match_fn):
    cases = list(surgical_cases or [])
    docs = list(protocol_documents or [])

    lookback_start = today_value - timedelta(days=41)
    recent_cases = []
    for item in cases:
        case_day = _coerce_date(item.get("case_date"))
        if case_day and case_day >= lookback_start:
            recent_cases.append(item)

    canceled_recent = [item for item in recent_cases if item.get("status") == "canceled"]
    cancel_rate = round((len(canceled_recent) / len(recent_cases)) * 100, 1) if recent_cases else 0.0

    coverage_cases = []
    for item in cases:
        case_day = _coerce_date(item.get("case_date"))
        if not case_day:
            continue
        if case_day < (today_value - timedelta(days=90)):
            continue
        if item.get("status") not in ("planned", "completed"):
            continue
        coverage_cases.append(item)

    covered_cases = 0
    for item in coverage_cases:
        if protocol_match_fn(item, docs, max_items=1):
            covered_cases += 1
    protocol_coverage = round((covered_cases / len(coverage_cases)) * 100, 1) if coverage_cases else 0.0

    week_starts = []
    current_week_start = today_value - timedelta(days=today_value.weekday())
    for offset in range(5, -1, -1):
        week_starts.append(current_week_start - timedelta(days=7 * offset))

    cancel_trend = {}
    coverage_trend = {}
    for week_start in week_starts:
        week_end = week_start + timedelta(days=6)
        week_label = week_start.strftime("%b %d")
        week_cases = []
        for item in cases:
            case_day = _coerce_date(item.get("case_date"))
            if case_day and week_start <= case_day <= week_end:
                week_cases.append(item)

        week_canceled = [item for item in week_cases if item.get("status") == "canceled"]
        week_coverage_candidates = [item for item in week_cases if item.get("status") in ("planned", "completed")]
        week_covered = 0
        for item in week_coverage_candidates:
            if protocol_match_fn(item, docs, max_items=1):
                week_covered += 1

        cancel_trend[week_label] = len(week_canceled)
        coverage_trend[week_label] = round((week_covered / len(week_coverage_candidates)) * 100, 1) if week_coverage_candidates else 0.0

    performed_cases = [item for item in cases if item.get("status") == "completed"]
    surgery_type_counts = {}
    for item in performed_cases:
        procedure_name = str(item.get("procedure_name") or "").strip() or "Unspecified procedure"
        surgery_type_counts[procedure_name] = surgery_type_counts.get(procedure_name, 0) + 1

    sorted_surgery_counts = sorted(
        surgery_type_counts.items(),
        key=lambda entry: entry[1],
        reverse=True,
    )

    return {
        "recent_cases_count": len(recent_cases),
        "canceled_recent_count": len(canceled_recent),
        "cancel_rate": cancel_rate,
        "protocol_coverage": protocol_coverage,
        "cancel_trend": cancel_trend,
        "coverage_trend": coverage_trend,
        "completed_cases_count": len(performed_cases),
        "unique_surgery_type_count": len(sorted_surgery_counts),
        "sorted_surgery_counts": sorted_surgery_counts,
    }
