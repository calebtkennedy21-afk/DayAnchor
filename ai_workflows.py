import json
import re
from datetime import date, timedelta

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


def extract_json_block(text):
    if not text:
        return None
    json_blocks = re.findall(r"```json\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if json_blocks:
        return json_blocks[-1]
    match = re.search(r"(\{\s*\"suggested_tasks\"\s*:\s*\[.*\]\s*\})", text, flags=re.DOTALL)
    return match.group(1) if match else None


def parse_date_value(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def parse_time_value(value):
    if not value:
        return None
    if hasattr(value, "hour") and hasattr(value, "minute"):
        return value
    cleaned = str(value).strip()
    if not cleaned:
        return None
    if len(cleaned) == 5 and cleaned[2] == ":":
        cleaned = f"{cleaned}:00"
    from datetime import time

    try:
        return time.fromisoformat(cleaned)
    except ValueError:
        return None


def parse_ai_suggestions(text):
    json_blob = extract_json_block(text)
    if not json_blob:
        return []

    try:
        payload = json.loads(json_blob)
    except json.JSONDecodeError:
        return []

    raw_items = payload.get("suggested_tasks", []) if isinstance(payload, dict) else []
    suggestions = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue

        title = str(item.get("title", "")).strip()
        if not title:
            continue

        category = str(item.get("category", "Personal")).strip().title()
        if category not in ("Personal", "Clinic"):
            category = "Personal"

        priority = str(item.get("priority", "medium")).strip().lower()
        if priority not in ("high", "medium", "low"):
            priority = "medium"

        due_date = parse_date_value(item.get("due_date")) or date.today()
        scheduled_date = parse_date_value(item.get("scheduled_date"))
        scheduled_time = parse_time_value(item.get("scheduled_time"))

        raw_minutes = item.get("scheduled_minutes")
        try:
            scheduled_minutes = int(raw_minutes) if raw_minutes is not None else None
        except (TypeError, ValueError):
            scheduled_minutes = None

        suggestions.append(
            {
                "title": title,
                "description": str(item.get("description", "")).strip(),
                "category": category,
                "priority": priority,
                "due_date": due_date,
                "scheduled_date": scheduled_date,
                "scheduled_time": scheduled_time,
                "scheduled_minutes": scheduled_minutes,
            }
        )

    return suggestions[:5]


def parse_ai_schedule_updates(text):
    json_blob = extract_json_block(text)
    if not json_blob:
        return []

    try:
        payload = json.loads(json_blob)
    except json.JSONDecodeError:
        return []

    raw_items = payload.get("schedule_updates", []) if isinstance(payload, dict) else []
    updates = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        try:
            task_id = int(item.get("task_id"))
        except (TypeError, ValueError):
            continue

        try:
            scheduled_minutes = int(item.get("scheduled_minutes") or 30)
        except (TypeError, ValueError):
            scheduled_minutes = 30

        updates.append(
            {
                "task_id": task_id,
                "scheduled_date": parse_date_value(item.get("scheduled_date")),
                "scheduled_time": parse_time_value(item.get("scheduled_time")),
                "scheduled_minutes": scheduled_minutes,
            }
        )

    cleaned = []
    for item in updates:
        if item["scheduled_date"] and item["scheduled_time"] and item["scheduled_minutes"] > 0:
            cleaned.append(item)
    return cleaned[:20]


def task_snapshot_for_ai(tasks, max_items=20):
    if not tasks:
        return "No tasks available."

    lines = []
    for idx, task in enumerate(tasks[:max_items], start=1):
        lines.append(
            " | ".join(
                [
                    f"#{idx}",
                    f"id={task.get('id')}",
                    f"title={task.get('title', '')}",
                    f"category={task.get('category', '')}",
                    f"priority={task.get('priority', '')}",
                    f"status={task.get('status', '')}",
                    f"due_date={task.get('due_date') or 'none'}",
                    f"scheduled_date={task.get('scheduled_date') or 'none'}",
                    f"scheduled_time={task.get('scheduled_time') or 'none'}",
                    f"scheduled_minutes={task.get('scheduled_minutes') or 'none'}",
                    f"recurrence_rule={task.get('recurrence_rule') or 'none'}",
                    f"recurrence_interval={task.get('recurrence_interval') or 'none'}",
                ]
            )
        )
    return "\n".join(lines)


def generate_ai_plan(tasks, user_prompt, ai_enabled_fn, ai_api_key_fn, ai_model_name_fn, openai_cls=OpenAI):
    if not ai_enabled_fn():
        return "", "AI is not configured. Add OPENAI_API_KEY to enable it.", []

    try:
        client = openai_cls(api_key=ai_api_key_fn())
        task_snapshot = task_snapshot_for_ai(tasks)
        response = client.chat.completions.create(
            model=ai_model_name_fn(),
            temperature=0.4,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are DayAnchor AI planner. Build an execution-focused plan that feels like a senior ops assistant. "
                        "Prioritize concrete next actions, realistic sequencing, and risk management. Avoid generic motivation."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"User request: {user_prompt}\n\n"
                        "Current tasks:\n"
                        f"{task_snapshot}\n\n"
                        "Return a structured response with these sections exactly:\n"
                        "## Executive Summary\n"
                        "## Focus Blocks for Today\n"
                        "## Risks and Blockers\n"
                        "## Suggested Task Additions\n"
                        "## JSON Payload\n"
                        "The JSON block must use this exact shape:\n"
                        "```json\n"
                        "{\n"
                        "  \"suggested_tasks\": [\n"
                        "    {\n"
                        "      \"title\": \"...\",\n"
                        "      \"description\": \"...\",\n"
                        "      \"category\": \"Personal\" or \"Clinic\",\n"
                        "      \"priority\": \"high\" | \"medium\" | \"low\",\n"
                        "      \"due_date\": \"YYYY-MM-DD\",\n"
                        "      \"scheduled_date\": \"YYYY-MM-DD\",\n"
                        "      \"scheduled_time\": \"HH:MM\",\n"
                        "      \"scheduled_minutes\": 30\n"
                        "    }\n"
                        "  ]\n"
                        "}\n"
                        "```\n"
                        "Keep suggested_tasks to at most 3 items and make them realistic follow-up tasks, not duplicates of the existing board."
                    ),
                },
            ],
        )
        text = response.choices[0].message.content if response.choices else ""
        if not text:
            return "", "AI returned an empty response.", []
        suggestions = parse_ai_suggestions(text)
        return text, "", suggestions
    except Exception as exc:
        return "", f"AI request failed: {exc}", []


def generate_ai_schedule(tasks, user_prompt, ai_enabled_fn, ai_api_key_fn, ai_model_name_fn, openai_cls=OpenAI):
    if not ai_enabled_fn():
        return "", "AI is not configured. Add OPENAI_API_KEY to enable it.", []

    schedulable = [item for item in tasks if item.get("status") != "completed"]
    if not schedulable:
        return "", "No active tasks available for auto-scheduling.", []

    try:
        client = openai_cls(api_key=ai_api_key_fn())
        task_snapshot = task_snapshot_for_ai(schedulable)
        response = client.chat.completions.create(
            model=ai_model_name_fn(),
            temperature=0.2,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a precise scheduling assistant. Build a realistic day plan around the existing board "
                        "without overbooking or ignoring priority order. Return concise rationale plus strict JSON."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Scheduling context: {user_prompt}\n\n"
                        "Tasks:\n"
                        f"{task_snapshot}\n\n"
                        "Return a short rationale plus a JSON block with this exact shape:\n"
                        "```json\n"
                        "{\n"
                        "  \"schedule_updates\": [\n"
                        "    {\n"
                        "      \"task_id\": 123,\n"
                        "      \"scheduled_date\": \"YYYY-MM-DD\",\n"
                        "      \"scheduled_time\": \"HH:MM\",\n"
                        "      \"scheduled_minutes\": 30\n"
                        "    }\n"
                        "  ]\n"
                        "}\n"
                        "```\n"
                        "Do not include completed tasks. Favor the most important unscheduled or overdue work first."
                    ),
                },
            ],
        )
        text = response.choices[0].message.content if response.choices else ""
        if not text:
            return "", "AI returned an empty auto-schedule response.", []
        updates = parse_ai_schedule_updates(text)
        if not updates:
            return text, "AI returned no valid schedule updates.", []
        return text, "", updates
    except Exception as exc:
        return "", f"AI auto-schedule failed: {exc}", []


def generate_daily_review(active_tasks, completed_today, user_notes, ai_enabled_fn, ai_api_key_fn, ai_model_name_fn, openai_cls=OpenAI):
    completed_lines = [f"- {task.get('title')}" for task in completed_today]
    active_lines = [
        f"- {task.get('title')} ({task.get('priority')}, due={task.get('due_date') or 'none'}, status={task.get('status')})"
        for task in active_tasks[:20]
    ]

    completed_text = "\n".join(completed_lines) if completed_lines else "- None"
    active_text = "\n".join(active_lines) if active_lines else "- None"

    if not ai_enabled_fn():
        fallback_review = (
            "## End-of-Day Review\n"
            f"Completed today: {len(completed_today)} tasks\n"
            f"Active remaining: {len(active_tasks)} tasks\n"
            "\n"
            "AI is not configured, so this is a local summary."
        )
        fallback_tomorrow = "Focus first on overdue and high-priority active tasks, then schedule unscheduled items."
        return fallback_review, fallback_tomorrow, ""

    try:
        client = openai_cls(api_key=ai_api_key_fn())
        response = client.chat.completions.create(
            model=ai_model_name_fn(),
            temperature=0.4,
            messages=[
                {
                    "role": "system",
                    "content": "You are a concise productivity coach preparing an end-of-day review and next-day plan.",
                },
                {
                    "role": "user",
                    "content": (
                        f"Notes: {user_notes}\n\n"
                        "Completed today:\n"
                        f"{completed_text}\n\n"
                        "Active remaining:\n"
                        f"{active_text}\n\n"
                        "Return markdown with two sections exactly:\n"
                        "## End-of-Day Review\n"
                        "## Tomorrow Draft Plan"
                    ),
                },
            ],
        )
        text = response.choices[0].message.content if response.choices else ""
        if not text:
            return "", "AI returned an empty review response.", ""
        parts = re.split(r"(?m)^##\s+Tomorrow Draft Plan\s*$", text, maxsplit=1)
        review_text = parts[0].strip()
        tomorrow_text = f"## Tomorrow Draft Plan\n{parts[1].strip()}" if len(parts) > 1 else ""
        return review_text, tomorrow_text, ""
    except Exception as exc:
        return "", "", f"AI review failed: {exc}"


def _fallback_top_urgent_task(active_tasks):
    if not active_tasks:
        return None

    priority_order = {"high": 0, "medium": 1, "low": 2}

    def sort_key(task):
        due = task.get("due_date")
        return (
            priority_order.get(task.get("priority"), 1),
            due or date.max,
            str(task.get("title") or "").lower(),
        )

    return sorted(active_tasks, key=sort_key)[0]


def generate_ai_daily_summary(
    tasks,
    active_tasks,
    added_today,
    completed_today,
    ai_enabled_fn,
    ai_api_key_fn,
    ai_model_name_fn,
    openai_cls=OpenAI,
):
    top_urgent = _fallback_top_urgent_task(active_tasks)
    top_urgent_line = "No urgent active task detected."
    if top_urgent:
        top_urgent_line = (
            f"{top_urgent.get('title')} "
            f"(priority={top_urgent.get('priority')}, due={top_urgent.get('due_date') or 'none'})"
        )

    added_lines = [f"- {item.get('title')}" for item in (added_today or [])[:20]]
    completed_lines = [f"- {item.get('title')}" for item in (completed_today or [])[:20]]

    added_text = "\n".join(added_lines) if added_lines else "- None"
    completed_text = "\n".join(completed_lines) if completed_lines else "- None"
    active_snapshot = task_snapshot_for_ai(active_tasks or [], max_items=20)

    if not ai_enabled_fn():
        fallback = (
            "## AI Daily Summary\n"
            f"- Added today: {len(added_today or [])}\n"
            f"- Completed today: {len(completed_today or [])}\n"
            f"- Start first with: {top_urgent_line}\n"
            "- Suggested improvement for tomorrow: pre-plan your first hour before ending tonight's routine."
        )
        return fallback, ""

    try:
        client = openai_cls(api_key=ai_api_key_fn())
        response = client.chat.completions.create(
            model=ai_model_name_fn(),
            temperature=0.35,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a concise productivity coach. Produce a daily operational summary that is direct, specific, and actionable."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"All visible tasks: {len(tasks or [])}\n"
                        f"Added today count: {len(added_today or [])}\n"
                        f"Completed today count: {len(completed_today or [])}\n"
                        f"Top urgent candidate: {top_urgent_line}\n\n"
                        "Added today:\n"
                        f"{added_text}\n\n"
                        "Completed today:\n"
                        f"{completed_text}\n\n"
                        "Active tasks snapshot:\n"
                        f"{active_snapshot}\n\n"
                        "Return markdown with exactly these sections:\n"
                        "## Added Tasks Today\n"
                        "## Completed Tasks Today\n"
                        "## Highest-Urgency Task First\n"
                        "## Suggested Improvement for Tomorrow\n"
                        "Keep it short and operational (4-8 lines total)."
                    ),
                },
            ],
        )
        text = response.choices[0].message.content if response.choices else ""
        if not text:
            return "", "AI returned an empty daily summary response."
        return text.strip(), ""
    except Exception as exc:
        return "", f"AI daily summary failed: {exc}"


def generate_weekly_nightly_insight(
    weekly_trends,
    recent_reflections,
    ai_enabled_fn,
    ai_api_key_fn,
    ai_model_name_fn,
    openai_cls=OpenAI,
):
    trends = weekly_trends or {}
    feel_counts = trends.get("feel_counts") or {}
    rough = int(feel_counts.get("Rough") or 0)
    heavy = int(feel_counts.get("Heavy") or 0)
    steady = int(feel_counts.get("Steady") or 0)
    good = int(feel_counts.get("Good") or 0)
    great = int(feel_counts.get("Great") or 0)
    consistency_pct = int(round(float(trends.get("consistency_rate") or 0.0) * 100))
    morning_rate = trends.get("morning_completion_rate")
    morning_rate_label = "N/A" if morning_rate is None else f"{int(round(morning_rate * 100))}%"

    recent_lines = []
    for day_text, item in (recent_reflections or [])[:5]:
        recent_lines.append(
            f"- {day_text}: morning={item.get('morning_goal_status', 'Not applicable today')}, "
            f"feel={item.get('day_feel', 'Steady')}, win={str(item.get('one_win') or '').strip() or 'none'}"
        )
    recent_text = "\n".join(recent_lines) if recent_lines else "- None"

    if not ai_enabled_fn():
        if consistency_pct < 60:
            focus = "Set a fixed nightly alarm 30 minutes before bedtime to protect reflection consistency."
        elif rough + heavy >= max(2, good + great):
            focus = "Cut one low-value evening task tomorrow and protect a calmer shutdown window before journaling."
        else:
            focus = "Keep your current ritual and add one sentence after reading on what to repeat tomorrow."
        return (
            "### Weekly AI Insight\n"
            f"Actionable adjustment: {focus}"
        ), ""

    try:
        client = openai_cls(api_key=ai_api_key_fn())
        response = client.chat.completions.create(
            model=ai_model_name_fn(),
            temperature=0.3,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a concise habit coach. Return one actionable weekly adjustment for the user's nightly ritual."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Weekly trend snapshot:\n"
                        f"- check-ins: {trends.get('checkin_count', 0)}/{trends.get('window_days', 7)}\n"
                        f"- consistency: {consistency_pct}%\n"
                        f"- average feel: {trends.get('average_feel_label', 'No data')}\n"
                        f"- morning goal hit rate: {morning_rate_label}\n"
                        f"- wins logged: {trends.get('wins_logged', 0)}\n"
                        f"- improvements logged: {trends.get('improvements_logged', 0)}\n"
                        f"- feel spread: rough={rough}, heavy={heavy}, steady={steady}, good={good}, great={great}\n\n"
                        "Recent nightly reflections:\n"
                        f"{recent_text}\n\n"
                        "Return markdown with exactly:\n"
                        "### Weekly AI Insight\n"
                        "One short paragraph (1-3 sentences) and exactly one actionable adjustment sentence starting with 'Actionable adjustment:'."
                    ),
                },
            ],
        )
        text = response.choices[0].message.content if response.choices else ""
        if not text:
            return "", "AI returned an empty weekly insight response."
        return text.strip(), ""
    except Exception as exc:
        return "", f"AI weekly insight failed: {exc}"


def generate_family_schedule_insight(
    family_summary,
    recent_items,
    ai_enabled_fn,
    ai_api_key_fn,
    ai_model_name_fn,
    openai_cls=OpenAI,
):
    summary = family_summary or {}
    upcoming_count = int(summary.get("upcoming_count") or 0)
    appointment_count = int(summary.get("appointment_count") or 0)
    trip_count = int(summary.get("trip_count") or 0)
    camp_count = int(summary.get("camp_count") or 0)
    multi_day_count = int(summary.get("multi_day_count") or 0)
    conflict_count = int(summary.get("conflict_count") or 0)
    weekend_count = int(summary.get("weekend_count") or 0)

    recent_lines = []
    for item in (recent_items or [])[:6]:
        item_date = item.get("start_date") or item.get("date") or "unknown date"
        title = str(item.get("title") or "Untitled").strip()
        item_type = str(item.get("item_type") or "family item").strip()
        recent_lines.append(f"- {item_date}: {title} ({item_type})")
    recent_text = "\n".join(recent_lines) if recent_lines else "- None"

    if not ai_enabled_fn():
        if conflict_count:
            focus = "Move one family item off the busiest day and protect a buffer before the next work block."
        elif trip_count or camp_count:
            focus = "Group trip or camp prep into one checklist block so the calendar does not get fragmented."
        elif multi_day_count:
            focus = "Mark the multi-day block early and add a reminder for the day before it starts."
        else:
            focus = "Add one shared family planning check-in each week so appointments do not land by surprise."
        return (
            "### Weekly Family Insight\n"
            f"Actionable adjustment: {focus}"
        ), ""

    try:
        client = openai_cls(api_key=ai_api_key_fn())
        response = client.chat.completions.create(
            model=ai_model_name_fn(),
            temperature=0.3,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a concise family logistics coach. Return one weekly insight and one concrete adjustment for the family calendar."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Family schedule snapshot:\n"
                        f"- upcoming items: {upcoming_count}\n"
                        f"- appointments: {appointment_count}\n"
                        f"- trips: {trip_count}\n"
                        f"- camps/sports: {camp_count}\n"
                        f"- multi-day items: {multi_day_count}\n"
                        f"- conflict days: {conflict_count}\n"
                        f"- weekend items: {weekend_count}\n\n"
                        "Recent family items:\n"
                        f"{recent_text}\n\n"
                        "Return markdown with exactly:\n"
                        "### Weekly Family Insight\n"
                        "One short paragraph (1-3 sentences) and exactly one actionable adjustment sentence starting with 'Actionable adjustment:'."
                    ),
                },
            ],
        )
        text = response.choices[0].message.content if response.choices else ""
        if not text:
            return "", "AI returned an empty family insight response."
        return text.strip(), ""
    except Exception as exc:
        return "", f"AI family insight failed: {exc}"


def generate_family_weekly_briefing(
    family_summary,
    recent_items,
    ai_enabled_fn,
    ai_api_key_fn,
    ai_model_name_fn,
    openai_cls=OpenAI,
):
    summary = family_summary or {}
    upcoming_count = int(summary.get("upcoming_count") or 0)
    appointment_count = int(summary.get("appointment_count") or 0)
    trip_count = int(summary.get("trip_count") or 0)
    camp_count = int(summary.get("camp_count") or 0)
    recurring_count = int(summary.get("recurring_count") or 0)
    conflict_count = int(summary.get("conflict_count") or 0)
    weekend_count = int(summary.get("weekend_count") or 0)
    checklist_count = int(summary.get("items_with_checklists") or 0)

    recent_lines = []
    for item in (recent_items or [])[:8]:
        item_date = item.get("start_date") or item.get("date") or "unknown date"
        title = str(item.get("title") or "Untitled").strip()
        item_type = str(item.get("item_type") or "family item").strip()
        recent_lines.append(f"- {item_date}: {title} ({item_type})")
    recent_text = "\n".join(recent_lines) if recent_lines else "- None"

    if not ai_enabled_fn():
        focus_lines = []
        if conflict_count:
            focus_lines.append("- Resolve one overlap early by shifting timing or assigning coverage.")
        if trip_count or camp_count:
            focus_lines.append("- Schedule one prep block for packing, forms, and logistics.")
        if checklist_count < max(1, upcoming_count // 2):
            focus_lines.append("- Add checklist notes to high-friction items to reduce last-minute misses.")
        if recurring_count:
            focus_lines.append("- Review recurring events for exceptions this week.")
        if not focus_lines:
            focus_lines.append("- Keep the current plan and add one short Sunday planning check-in.")

        return (
            "### Weekly Family Briefing\n"
            f"This week has {upcoming_count} family items with {conflict_count} potential conflict day(s).\n\n"
            "## Recommended Actions\n"
            + "\n".join(focus_lines[:3])
        ), ""

    try:
        client = openai_cls(api_key=ai_api_key_fn())
        response = client.chat.completions.create(
            model=ai_model_name_fn(),
            temperature=0.3,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a concise family logistics coach producing a weekly planning briefing. Keep it practical and short."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Family schedule snapshot:\n"
                        f"- upcoming items: {upcoming_count}\n"
                        f"- appointments: {appointment_count}\n"
                        f"- trips: {trip_count}\n"
                        f"- camps/sports: {camp_count}\n"
                        f"- recurring occurrences: {recurring_count}\n"
                        f"- conflict days: {conflict_count}\n"
                        f"- weekend items: {weekend_count}\n"
                        f"- items with checklists: {checklist_count}\n\n"
                        "Recent upcoming items:\n"
                        f"{recent_text}\n\n"
                        "Return markdown with exactly these sections:\n"
                        "### Weekly Family Briefing\n"
                        "## This Week\n"
                        "## Friction Risks\n"
                        "## Recommended Actions\n"
                        "Requirements:\n"
                        "- Keep it to 6-10 short lines total.\n"
                        "- In Recommended Actions, include exactly 3 bullet points.\n"
                        "- Actions must be concrete and calendar-oriented."
                    ),
                },
            ],
        )
        text = response.choices[0].message.content if response.choices else ""
        if not text:
            return "", "AI returned an empty family briefing response."
        return text.strip(), ""
    except Exception as exc:
        return "", f"AI family briefing failed: {exc}"


def generate_weekly_morning_ritual_insight(
    weekly_trends,
    recent_checkins,
    latest_nightly_improvement,
    ai_enabled_fn,
    ai_api_key_fn,
    ai_model_name_fn,
    openai_cls=OpenAI,
):
    trends = weekly_trends or {}
    sleep_counts = trends.get("sleep_counts") or {}
    energy_counts = trends.get("energy_counts") or {}
    mood_counts = trends.get("mood_counts") or {}
    checkin_count = int(trends.get("checkin_count") or 0)
    consistency_pct = int(round(float(trends.get("consistency_rate") or 0.0) * 100))
    planned_rate = (
        int(round((float(trends.get("planned_yes_count") or 0) / float(checkin_count)) * 100))
        if checkin_count
        else 0
    )

    recent_lines = []
    for day_text, item in (recent_checkins or [])[:5]:
        recent_lines.append(
            f"- {day_text}: sleep={item.get('sleep_quality', 'Good')}, energy={item.get('energy_level', 'Medium')}, mood={item.get('mood', 'Neutral')}, "
            f"intention={str(item.get('top_intention') or '').strip() or 'none'}"
        )
    recent_text = "\n".join(recent_lines) if recent_lines else "- None"
    carry_over = str(latest_nightly_improvement or "").strip() or "No nightly carry-over note was logged."

    if not ai_enabled_fn():
        if consistency_pct < 60:
            focus = "Make the first step smaller: open the morning ritual and record one line before checking messages."
        elif sleep_counts.get("Poor", 0) + sleep_counts.get("Fair", 0) > sleep_counts.get("Good", 0) + sleep_counts.get("Great", 0):
            focus = "Keep the ritual, but lead with a calmer first action and protect the first 10 minutes from interruption."
        elif planned_rate < 50:
            focus = "Name one concrete morning goal before the day starts so the ritual turns into action faster."
        else:
            focus = "Keep the current pattern and add one sentence about what made the best mornings work."
        return (
            "### Weekly AI Insight\n"
            f"Actionable adjustment: {focus}"
        ), ""

    try:
        client = openai_cls(api_key=ai_api_key_fn())
        response = client.chat.completions.create(
            model=ai_model_name_fn(),
            temperature=0.3,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a concise morning routine coach. Return one practical weekly insight with one specific adjustment."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Weekly morning ritual snapshot:\n"
                        f"- check-ins: {checkin_count}/7\n"
                        f"- consistency: {consistency_pct}%\n"
                        f"- planned morning goals rate: {planned_rate}%\n"
                        f"- average sleep: {trends.get('average_sleep_label', 'No data')}\n"
                        f"- average energy: {trends.get('average_energy_label', 'No data')}\n"
                        f"- average mood: {trends.get('average_mood_label', 'No data')}\n"
                        f"- sleep spread: poor={sleep_counts.get('Poor', 0)}, fair={sleep_counts.get('Fair', 0)}, good={sleep_counts.get('Good', 0)}, great={sleep_counts.get('Great', 0)}\n"
                        f"- energy spread: low={energy_counts.get('Low', 0)}, medium={energy_counts.get('Medium', 0)}, high={energy_counts.get('High', 0)}\n"
                        f"- mood spread: drained={mood_counts.get('Drained', 0)}, neutral={mood_counts.get('Neutral', 0)}, positive={mood_counts.get('Positive', 0)}, focused={mood_counts.get('Focused', 0)}\n"
                        f"- nightly carry-over note: {carry_over}\n\n"
                        "Recent morning check-ins:\n"
                        f"{recent_text}\n\n"
                        "Return markdown with exactly:\n"
                        "### Weekly AI Insight\n"
                        "One short paragraph (1-3 sentences) and exactly one actionable adjustment sentence starting with 'Actionable adjustment:'."
                    ),
                },
            ],
        )
        text = response.choices[0].message.content if response.choices else ""
        if not text:
            return "", "AI returned an empty morning insight response."
        return text.strip(), ""
    except Exception as exc:
        return "", f"AI morning insight failed: {exc}"


def generate_ai_morning_ritual_brief(
    active_tasks,
    latest_nightly_improvement,
    sleep_quality,
    energy_level,
    mood,
    top_intention,
    morning_goals_planned,
    grounding_selected,
    ai_enabled_fn,
    ai_api_key_fn,
    ai_model_name_fn,
    openai_cls=OpenAI,
):
    top_urgent = _fallback_top_urgent_task(active_tasks or [])
    if top_urgent:
        top_urgent_line = (
            f"{top_urgent.get('title')} "
            f"(priority={top_urgent.get('priority')}, due={top_urgent.get('due_date') or 'none'})"
        )
    else:
        top_urgent_line = "No urgent active task detected."

    improvement_note = (latest_nightly_improvement or "").strip()
    if not improvement_note:
        improvement_note = "No improvement note was logged last night."

    if not ai_enabled_fn():
        grounding_line = (
            "Complete your optional reading/grounding step before starting deep work."
            if not grounding_selected
            else "Optional reading/grounding is already complete."
        )
        fallback = (
            "## Morning Priority\n"
            f"Start first with: {top_urgent_line}\n\n"
            "## Behavior Adjustment From Last Night\n"
            f"Carry this forward today: {improvement_note}\n\n"
            "## Grounding Cue\n"
            f"{grounding_line}"
        )
        return fallback, ""

    try:
        client = openai_cls(api_key=ai_api_key_fn())
        response = client.chat.completions.create(
            model=ai_model_name_fn(),
            temperature=0.3,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a concise morning operations coach. Keep output practical and short."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Morning inputs: sleep={sleep_quality}, energy={energy_level}, mood={mood}, "
                        f"morning_goals_planned={morning_goals_planned}, grounding_selected={grounding_selected}\n"
                        f"Top intention: {top_intention or 'none'}\n"
                        f"Highest urgency task candidate: {top_urgent_line}\n"
                        f"Last night improvement note: {improvement_note}\n\n"
                        "Return markdown with exactly these sections:\n"
                        "## Morning Priority\n"
                        "## Behavior Adjustment From Last Night\n"
                        "## Grounding Cue\n"
                        "Requirements:\n"
                        "- In Morning Priority, explicitly name the one highest-urgency task to do first.\n"
                        "- In Behavior Adjustment, convert last night's improvement note into one concrete action for today.\n"
                        "- Keep all sections combined to 4-8 short lines."
                    ),
                },
            ],
        )
        text = response.choices[0].message.content if response.choices else ""
        if not text:
            return "", "AI returned an empty morning brief response."
        return text.strip(), ""
    except Exception as exc:
        return "", f"AI morning brief failed: {exc}"
