import os
import json
import html
import re
import calendar
import textwrap
from collections import Counter
from io import BytesIO
from datetime import date, datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import psycopg
from psycopg.rows import dict_row
import streamlit as st
import streamlit.components.v1 as components
from functools import partial

from clinical_reference import (
    anatomy_structure_map as ref_anatomy_structure_map,
    anatomy_bones_map as ref_anatomy_bones_map,
    anatomy_fractures_map as ref_anatomy_fractures_map,
    filter_anatomy_xray_images as ref_filter_anatomy_xray_images,
    render_anatomy_structure_spotlight as ref_render_anatomy_structure_spotlight,
    suggest_cpt_codes_for_case as ref_suggest_cpt_codes_for_case,
    suggest_protocols_for_case as ref_suggest_protocols_for_case,
)
from cpt_reference import CPT_REFERENCE
import ai_workflows
import app_bootstrap
import data_access
from family_goals_core import family_goal_dashboard_summary, normalize_family_goals
from formatting_core import (
    format_due,
    format_due_badge,
    format_recurrence_badge,
    format_schedule,
    format_schedule_badge,
    recurrence_label,
    status_label,
)
from overview_core import (
    build_time_blocks,
    clinic_day_profiles,
    clinic_day_summary,
    overview_runtime_settings,
    personal_focus_summary,
    resolve_overview_day_context,
    schedule_workload_snapshot,
)
import page_renderers
import page_sections
from settings_serialization import dumps_json_safe
from scheduling_core import (
    build_week_rebalance_moves,
    clinic_visit_templates,
    personal_schedule_templates,
    priority_rank,
    parse_on_call_schedule_document,
    safe_int,
    providers_for_schedule_date,
    scheduled_date_range,
    scheduled_minutes_on_day,
    scheduled_span_position,
    shift_date_by_rule,
    task_attention_signal,
    task_attention_sort_key,
)

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


st.set_page_config(page_title="DayAnchor", page_icon="⛵", layout="wide")


MOUNTAIN_TIMEZONE = ZoneInfo("America/Denver")


def mountain_today():
    return datetime.now(MOUNTAIN_TIMEZONE).date()


if "tasks" not in st.session_state:
    st.session_state.tasks = []
if "ai_response" not in st.session_state:
    st.session_state.ai_response = ""
if "ai_error" not in st.session_state:
    st.session_state.ai_error = ""
if "ai_suggestions" not in st.session_state:
    st.session_state.ai_suggestions = []
if "ai_schedule_error" not in st.session_state:
    st.session_state.ai_schedule_error = ""
if "ai_schedule_updates" not in st.session_state:
    st.session_state.ai_schedule_updates = []
if "daily_review_text" not in st.session_state:
    st.session_state.daily_review_text = ""
if "tomorrow_plan_text" not in st.session_state:
    st.session_state.tomorrow_plan_text = ""
if "daily_review_error" not in st.session_state:
    st.session_state.daily_review_error = ""
if "surgical_cases" not in st.session_state:
    st.session_state.surgical_cases = []
if "protocol_documents" not in st.session_state:
    st.session_state.protocol_documents = []
if "case_protocol_links" not in st.session_state:
    st.session_state.case_protocol_links = []
if "anatomy_xray_images" not in st.session_state:
    st.session_state.anatomy_xray_images = []
if "personal_goals" not in st.session_state:
    st.session_state.personal_goals = []
if "personal_goal_checkins" not in st.session_state:
    st.session_state.personal_goal_checkins = []
if "anatomy_quiz_attempts" not in st.session_state:
    st.session_state.anatomy_quiz_attempts = []
if "anatomy_quiz_review_queue" not in st.session_state:
    st.session_state.anatomy_quiz_review_queue = []
if "lead_clinical_issues" not in st.session_state:
    st.session_state.lead_clinical_issues = []
if "lead_sop_entries" not in st.session_state:
    st.session_state.lead_sop_entries = []
if "lead_relationship_touchpoints" not in st.session_state:
    st.session_state.lead_relationship_touchpoints = []
if "lead_ma_assignments" not in st.session_state:
    st.session_state.lead_ma_assignments = []
if "lead_huddle_logs" not in st.session_state:
    st.session_state.lead_huddle_logs = []
if "lead_skill_signoffs" not in st.session_state:
    st.session_state.lead_skill_signoffs = []
if "lead_education_requests" not in st.session_state:
    st.session_state.lead_education_requests = []
if "autoclave_maintenance_items" not in st.session_state:
    st.session_state.autoclave_maintenance_items = []
if "lead_documents" not in st.session_state:
    st.session_state.lead_documents = []


DEFAULT_APP_SETTINGS = {
    "default_category": "Personal",
    "default_priority": "medium",
    "default_duration": 60,
    "default_schedule_time": "09:00",
    "timeline_days": 7,
    "surgeon_clinic_patient_target": 25,
    "general_clinic_patient_target": 25,
    "procedure_friday_procedure_target": 8,
    "clinic_visit_minutes": 12,
    "clinic_admin_buffer_minutes": 60,
    "procedure_block_minutes": 30,
    "personal_focus_minutes": 90,
    "schedule_daily_capacity_minutes": 480,
    "schedule_capacity_days_per_week": 5,
    "overview_day_mode": "Auto",
    "overview_role_label": "Medical Assistant",
    "overview_site_label": "MOA (Mercy Orthopedic Associates)",
    "overview_patient_target": 25,
    "overview_procedure_target": 8,
    "overview_admin_buffer_minutes": 60,
    "overview_shift_minutes": 480,
    "overview_focus_window_minutes": 90,
    "overview_clinic_weekdays": ["Thursday", "Monday"],
    "overview_admin_weekdays": ["Tuesday"],
    "calendar_weekday_assignments": {
        "Monday": ["BB clinic"],
        "Tuesday": ["Office day"],
        "Wednesday": ["WFH personal catch-up"],
        "Thursday": ["BB clinic"],
        "Friday": ["Dr. Rozek TenJet"],
    },
    "calendar_date_overrides": {},
    "overview_procedure_friday_frequency_weeks": 2,
    "overview_procedure_friday_cycle_offset": 0,
    "or_fixed_weekday": "Friday",
    "or_alternating_days": ["Monday", "Wednesday"],
    "or_alternating_cycle_offset": 0,
    "default_surgeon_label": "Dr. Braden Boyer (BB)",
    "personal_notes": "",
    "clinical_notes": "",
    "personal_notes_updated_at": "",
    "clinical_notes_updated_at": "",
    "nightly_reflections": {},
    "morning_ritual_checkins": {},
    "family_schedule_items": [],
    "family_goals": [],
    "family_weekly_notes": [],
    "family_notes": "",
    "family_notes_updated_at": "",
    "home_routine_checklists": {},
    "quick_reminders": [],
    "clinic_day_closeout_template": [
        "Confirm all charting is complete",
        "Review and send pending patient messages",
        "Confirm orders, labs, and imaging follow-through",
        "Verify post-op and follow-up scheduling",
        "Restock key room and procedure supplies",
    ],
    "clinic_day_closeout_log": {},
    "ma_lead_weekly_metric_targets": {},
    "ma_lead_weekly_metrics_log": {},
    "ma_lead_rollout_30_day_start_date": "",
    "ma_lead_rollout_30_day_template": [],
    "ma_lead_rollout_30_day_log": {},
    "ma_lead_biweekly_checkins": [],
    "ma_lead_biweekly_action_items": [],
    "ma_lead_biweekly_template": {},
    "ma_lead_biweekly_settings": {},
}


MORNING_GOAL_STATUS_OPTIONS = ["Yes", "No", "Not applicable today"]
DAY_FEEL_OPTIONS = ["Rough", "Heavy", "Steady", "Good", "Great"]
MORNING_SLEEP_OPTIONS = ["Poor", "Fair", "Good", "Great"]
MORNING_ENERGY_OPTIONS = ["Low", "Medium", "High"]
MORNING_MOOD_OPTIONS = ["Drained", "Neutral", "Positive", "Focused"]
MORNING_PLANNED_OPTIONS = ["Yes", "No"]
CLINIC_DAY_CLOSEOUT_TEMPLATE_DEFAULTS = [
    "Confirm all charting is complete",
    "Review and send pending patient messages",
    "Confirm orders, labs, and imaging follow-through",
    "Verify post-op and follow-up scheduling",
    "Restock key room and procedure supplies",
]

MA_LEAD_WEEKLY_METRIC_DEFAULTS = [
    {
        "key": "first_patient_on_time_start_rate",
        "label": "First patient on-time start rate",
        "unit": "%",
        "direction": "higher_is_better",
        "target": 90.0,
    },
    {
        "key": "rooming_cycle_minutes",
        "label": "Rooming cycle time",
        "unit": "min",
        "direction": "lower_is_better",
        "target": 12.0,
    },
    {
        "key": "inbasket_turnaround_hours",
        "label": "In-basket turnaround",
        "unit": "hrs",
        "direction": "lower_is_better",
        "target": 4.0,
    },
    {
        "key": "refill_turnaround_hours",
        "label": "Refill turnaround",
        "unit": "hrs",
        "direction": "lower_is_better",
        "target": 8.0,
    },
    {
        "key": "no_show_recovery_rate",
        "label": "No-show recovery rate",
        "unit": "%",
        "direction": "higher_is_better",
        "target": 50.0,
    },
    {
        "key": "staff_overtime_minutes",
        "label": "Staff overtime",
        "unit": "min",
        "direction": "lower_is_better",
        "target": 120.0,
    },
]

MA_LEAD_ROLLOUT_30_DAY_TEMPLATE_DEFAULTS = [
    "Map top 5 clinic bottlenecks from this week.",
    "Document who owns each high-friction workflow.",
    "Launch start-of-day huddle rhythm with clear timing.",
    "Launch midday reset and assign real-time coverage float.",
    "Launch end-of-day debrief and capture one improvement action.",
    "Publish escalation rules: what, who, and by when.",
    "Create one-page rooming playbook with done definitions.",
    "Create one-page refill routing playbook with done definitions.",
    "Create one-page prior-auth kickoff playbook with done definitions.",
    "Set first patient on-time start and rooming cycle baselines.",
    "Set in-basket and refill turnaround baselines.",
    "Set no-show recovery and overtime baselines.",
    "Review baseline trends with team (no-blame format).",
    "Pilot weekly metrics dashboard with this week's values.",
    "Cross-train at least one backup for each critical task.",
    "Confirm closed-loop communication behavior for urgent requests.",
    "Run one after-action review on a recent bottleneck.",
    "Tighten handoff points that caused delays this week.",
    "Publish updated workflow expectations from lessons learned.",
    "Recognize visible wins and reinforce reliability behaviors.",
]

MA_LEAD_BIWEEKLY_TEMPLATE_DEFAULTS = {
    "wins_prompt": "Wins since last check-in",
    "blockers_prompt": "Current blockers",
    "clarifications_prompt": "Clarifications needed",
    "coaching_focus_prompt": "Coaching focus (one behavior)",
    "support_needed_prompt": "Support needed from MA Lead",
}

MA_LEAD_BIWEEKLY_SETTINGS_DEFAULTS = {
    "cadence_days": 14,
    "reminder_lead_days": 2,
    "include_private_notes_in_export": False,
}

MINIMUM_HOME_ROUTINE_GOAL_TEMPLATES = [
    {
        "cadence": "daily",
        "title": "Daily minimum home routine",
        "reset_text": "Resets every day at midnight.",
        "items": [
            "Make bed",
            "Wash the dishes",
            "Clean countertop and sink",
            "Put away clutter",
            "Keep kitchen organized",
        ],
    },
    {
        "cadence": "weekly",
        "title": "Weekly minimum home routine",
        "reset_text": "Resets on Sunday at midnight.",
        "items": [
            "Change bed sheets",
            "Clean bathrooms",
            "Mop floors",
            "Organize surfaces",
            "Quick fridge clean out",
            "Check expired food",
        ],
    },
    {
        "cadence": "monthly",
        "title": "Monthly minimum home routine",
        "reset_text": "Resets at month end midnight.",
        "items": [
            "Clean cabinets",
            "Organize drawers",
            "Deep clean kitchen and oven",
            "Review stored products",
            "Get rid of what we don't use",
        ],
    },
]


def minimum_home_routine_items(cadence):
    for entry in MINIMUM_HOME_ROUTINE_GOAL_TEMPLATES:
        if str(entry.get("cadence") or "").strip().lower() == cadence:
            return [str(item).strip() for item in (entry.get("items") or []) if str(item).strip()]
    return []


def home_routine_period_key(cadence, day_value=None):
    anchor = day_value or mountain_today()
    normalized = str(cadence or "").strip().lower()
    if normalized == "daily":
        return anchor.isoformat()
    if normalized == "weekly":
        sunday_start = anchor - timedelta(days=(anchor.weekday() + 1) % 7)
        return sunday_start.isoformat()
    if normalized == "monthly":
        return f"{anchor.year:04d}-{anchor.month:02d}"
    return anchor.isoformat()


def normalize_home_routine_checklists(raw_value, day_value=None):
    normalized_source = raw_value if isinstance(raw_value, dict) else {}
    anchor = day_value or mountain_today()
    normalized_output = {}

    for cadence in ("daily", "weekly", "monthly"):
        cadence_items = minimum_home_routine_items(cadence)
        cadence_lookup = {item.lower(): item for item in cadence_items}
        expected_period_key = home_routine_period_key(cadence, anchor)
        raw_entry = normalized_source.get(cadence)
        if not isinstance(raw_entry, dict):
            raw_entry = {}

        raw_period_key = str(raw_entry.get("period_key") or "").strip()
        raw_completed_items = raw_entry.get("completed_items")
        normalized_completed_items = []
        if raw_period_key == expected_period_key and isinstance(raw_completed_items, list):
            seen = set()
            for raw_item in raw_completed_items:
                item_text = str(raw_item or "").strip()
                if not item_text:
                    continue
                canonical = cadence_lookup.get(item_text.lower())
                if not canonical:
                    continue
                if canonical in seen:
                    continue
                normalized_completed_items.append(canonical)
                seen.add(canonical)

        normalized_output[cadence] = {
            "period_key": expected_period_key,
            "completed_items": normalized_completed_items,
            "updated_at": str(raw_entry.get("updated_at") or "").strip(),
        }

    return normalized_output


def normalize_database_url(raw_url):
    if not raw_url:
        return None
    cleaned = raw_url.strip().strip('"').strip("'")
    return cleaned or None


def ensure_sslmode(url):
    parsed = urlsplit(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if "sslmode" not in query:
        # Railway Postgres commonly requires SSL; default here avoids extra env vars.
        query["sslmode"] = "require"
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def database_url_candidates():
    candidates = []
    seen = set()

    # Preferred names for this project.
    preferred_names = (
        "DATABASE_URL",
        "DATABASE_PUBLIC_URL",
        # Common Railway/host aliases as compatibility fallbacks.
        "DATABASE_PRIVATE_URL",
        "POSTGRES_URL",
        "POSTGRESQL_URL",
    )

    for env_name in preferred_names:
        raw = os.getenv(env_name)
        normalized = normalize_database_url(raw)
        if normalized:
            url = ensure_sslmode(normalized)
            if url not in seen:
                candidates.append((env_name, url))
                seen.add(url)

    # Fallback: build a DSN from PG* vars if URL vars are absent.
    pghost = normalize_database_url(os.getenv("PGHOST"))
    pgport = normalize_database_url(os.getenv("PGPORT"))
    pguser = normalize_database_url(os.getenv("PGUSER"))
    pgpassword = normalize_database_url(os.getenv("PGPASSWORD"))
    pgdatabase = normalize_database_url(os.getenv("PGDATABASE"))

    if pghost and pguser and pgpassword and pgdatabase:
        port = pgport or "5432"
        dsn = f"postgresql://{pguser}:{pgpassword}@{pghost}:{port}/{pgdatabase}"
        url = ensure_sslmode(dsn)
        if url not in seen:
            candidates.append(("PG*", url))
            seen.add(url)

    return candidates


def configured_database_env_names():
    names = []
    for name in (
        "DATABASE_URL",
        "DATABASE_PUBLIC_URL",
        "DATABASE_PRIVATE_URL",
        "POSTGRES_URL",
        "POSTGRESQL_URL",
        "PGHOST",
        "PGPORT",
        "PGUSER",
        "PGPASSWORD",
        "PGDATABASE",
    ):
        if normalize_database_url(os.getenv(name)):
            names.append(name)
    return names


def ai_model_name():
    return os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def ai_api_key():
    return normalize_database_url(os.getenv("OPENAI_API_KEY"))


def ai_enabled():
    return OpenAI is not None and bool(ai_api_key())


def parse_date_value(raw_value):
    if raw_value is None:
        return None
    cleaned = str(raw_value).strip().lower()
    if not cleaned:
        return None
    if cleaned in ("today", "now"):
        return mountain_today()
    if cleaned == "tomorrow":
        return mountain_today() + timedelta(days=1)
    try:
        return date.fromisoformat(cleaned)
    except ValueError:
        return None


def parse_time_value(raw_value):
    if raw_value is None:
        return None
    cleaned = str(raw_value).strip()
    if not cleaned:
        return None
    try:
        return time.fromisoformat(cleaned)
    except ValueError:
        pass
    if len(cleaned) == 5 and cleaned[2] == ":":
        try:
            return time.fromisoformat(f"{cleaned}:00")
        except ValueError:
            return None
    return None


def extract_json_block(text):
    if not text:
        return None
    json_blocks = re.findall(r"```json\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if json_blocks:
        return json_blocks[-1]
    match = re.search(r"(\{\s*\"suggested_tasks\"\s*:\s*\[.*\]\s*\})", text, flags=re.DOTALL)
    return match.group(1) if match else None


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

        due_date = parse_date_value(item.get("due_date")) or mountain_today()
        scheduled_date = parse_date_value(item.get("scheduled_date"))
        scheduled_end_date = parse_date_value(item.get("scheduled_end_date")) or scheduled_date
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
                "scheduled_end_date": scheduled_end_date,
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
                "scheduled_end_date": parse_date_value(item.get("scheduled_end_date")),
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
                    f"attention={task_attention_signal(task).get('label')}",
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


def generate_ai_plan(tasks, user_prompt):
    if not ai_enabled():
        return "", "AI is not configured. Add OPENAI_API_KEY to enable it.", []

    try:
        client = OpenAI(api_key=ai_api_key())
        task_snapshot = task_snapshot_for_ai(tasks)
        response = client.chat.completions.create(
            model=ai_model_name(),
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


def apply_ai_suggestions(suggestions):
    for item in suggestions:
        add_task(
            item["title"],
            item["description"],
            item["category"],
            item["priority"],
            item["due_date"],
            scheduled_date=item.get("scheduled_date"),
            scheduled_time=item.get("scheduled_time"),
            scheduled_minutes=item.get("scheduled_minutes"),
        )


def generate_ai_schedule(tasks, user_prompt):
    if not ai_enabled():
        return "", "AI is not configured. Add OPENAI_API_KEY to enable it.", []

    schedulable = [item for item in tasks if item.get("status") != "completed"]
    if not schedulable:
        return "", "No active tasks available for auto-scheduling.", []

    try:
        client = OpenAI(api_key=ai_api_key())
        task_snapshot = task_snapshot_for_ai(schedulable)
        response = client.chat.completions.create(
            model=ai_model_name(),
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


def apply_ai_schedule_updates(updates):
    for item in updates:
        update_task(
            item["task_id"],
            scheduled_date=item["scheduled_date"],
            scheduled_time=item["scheduled_time"],
            scheduled_minutes=item["scheduled_minutes"],
        )


def generate_daily_review(active_tasks, completed_today, user_notes):
    completed_lines = [f"- {task.get('title')}" for task in completed_today]
    active_lines = [
        f"- {task.get('title')} ({task.get('priority')}, due={task.get('due_date') or 'none'}, status={task.get('status')})"
        for task in active_tasks[:20]
    ]

    completed_text = "\n".join(completed_lines) if completed_lines else "- None"
    active_text = "\n".join(active_lines) if active_lines else "- None"

    if not ai_enabled():
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
        client = OpenAI(api_key=ai_api_key())
        response = client.chat.completions.create(
            model=ai_model_name(),
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
            return "", "", "AI returned an empty daily review response."

        split_marker = "## Tomorrow Draft Plan"
        if split_marker in text:
            head, tail = text.split(split_marker, 1)
            review_text = head.strip()
            tomorrow_text = f"## Tomorrow Draft Plan{tail}".strip()
        else:
            review_text = text.strip()
            tomorrow_text = "## Tomorrow Draft Plan\nNo structured tomorrow plan was returned."
        return review_text, tomorrow_text, ""
    except Exception as exc:
        return "", "", f"Daily review generation failed: {exc}"


def normalize_nightly_reflections(raw_reflections):
    if not isinstance(raw_reflections, dict):
        return {}

    normalized = {}
    for raw_day, raw_entry in raw_reflections.items():
        try:
            parsed_day = date.fromisoformat(str(raw_day))
        except ValueError:
            continue
        if not isinstance(raw_entry, dict):
            continue

        morning_status = str(raw_entry.get("morning_goal_status") or "Not applicable today").strip()
        if morning_status not in MORNING_GOAL_STATUS_OPTIONS:
            morning_status = "Not applicable today"

        day_feel = str(raw_entry.get("day_feel") or "Steady").strip()
        if day_feel not in DAY_FEEL_OPTIONS:
            day_feel = "Steady"

        normalized[parsed_day.isoformat()] = {
            "morning_goal_status": morning_status,
            "day_feel": day_feel,
            "area_of_improvement": str(raw_entry.get("area_of_improvement") or "").strip(),
            "one_win": str(raw_entry.get("one_win") or "").strip(),
            "journal_prompt": str(raw_entry.get("journal_prompt") or "").strip(),
            "saved_at": str(raw_entry.get("saved_at") or "").strip(),
        }

    return dict(sorted(normalized.items()))


def normalize_morning_ritual_checkins(raw_checkins):
    if not isinstance(raw_checkins, dict):
        return {}

    normalized = {}
    for raw_day, raw_entry in raw_checkins.items():
        try:
            parsed_day = date.fromisoformat(str(raw_day))
        except ValueError:
            continue
        if not isinstance(raw_entry, dict):
            continue

        sleep_quality = str(raw_entry.get("sleep_quality") or "Good").strip()
        if sleep_quality not in MORNING_SLEEP_OPTIONS:
            sleep_quality = "Good"

        energy_level = str(raw_entry.get("energy_level") or "Medium").strip()
        if energy_level not in MORNING_ENERGY_OPTIONS:
            energy_level = "Medium"

        mood = str(raw_entry.get("mood") or "Neutral").strip()
        if mood not in MORNING_MOOD_OPTIONS:
            mood = "Neutral"

        planned_morning_goals = str(raw_entry.get("planned_morning_goals") or "Yes").strip()
        if planned_morning_goals not in MORNING_PLANNED_OPTIONS:
            planned_morning_goals = "Yes"

        normalized[parsed_day.isoformat()] = {
            "sleep_quality": sleep_quality,
            "energy_level": energy_level,
            "mood": mood,
            "top_intention": str(raw_entry.get("top_intention") or "").strip(),
            "planned_morning_goals": planned_morning_goals,
            "optional_grounding_complete": bool(raw_entry.get("optional_grounding_complete")),
            "morning_brief_text": str(raw_entry.get("morning_brief_text") or "").strip(),
            "saved_at": str(raw_entry.get("saved_at") or "").strip(),
        }

    return dict(sorted(normalized.items()))


def normalize_family_schedule_items(raw_items):
    if not isinstance(raw_items, list):
        return []

    normalized = []
    for source_index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, dict):
            continue

        title = str(raw_item.get("title") or "").strip()
        start_date = parse_date_value(raw_item.get("start_date") or raw_item.get("date"))
        if not title or not start_date:
            continue

        end_date = parse_date_value(raw_item.get("end_date")) or start_date
        if end_date < start_date:
            end_date = start_date

        item_type = str(raw_item.get("item_type") or raw_item.get("category") or "Appointment").strip() or "Appointment"
        priority = str(raw_item.get("priority") or "medium").strip().lower()
        if priority not in ("high", "medium", "low"):
            priority = "medium"

        recurrence_rule = str(raw_item.get("recurrence_rule") or "none").strip().lower()
        if recurrence_rule not in ("none", "daily", "weekly", "monthly", "yearly"):
            recurrence_rule = "none"
        recurrence_interval = safe_int(raw_item.get("recurrence_interval"), 1)
        recurrence_interval = max(1, recurrence_interval)
        recurrence_end_date = parse_date_value(raw_item.get("recurrence_end_date"))

        raw_checklist = raw_item.get("checklist_items")
        checklist_items = []
        if isinstance(raw_checklist, list):
            checklist_items = [str(item).strip() for item in raw_checklist if str(item).strip()]
        elif isinstance(raw_checklist, str):
            checklist_items = [line.strip("- ").strip() for line in raw_checklist.splitlines() if line.strip()]

        normalized.append(
            {
                "item_id": str(raw_item.get("item_id") or f"family_{source_index}_{start_date.isoformat()}_{title.lower().replace(' ', '_')}").strip(),
                "source_index": source_index,
                "title": title,
                "item_type": item_type,
                "family_member": str(raw_item.get("family_member") or "").strip(),
                "start_date": start_date,
                "end_date": end_date,
                "start_time": parse_time_value(raw_item.get("start_time")),
                "priority": priority,
                "notes": str(raw_item.get("notes") or "").strip(),
                "location": str(raw_item.get("location") or "").strip(),
                "status": str(raw_item.get("status") or "planned").strip() or "planned",
                "all_day": bool(raw_item.get("all_day")),
                "checklist_items": checklist_items,
                "recurrence_rule": recurrence_rule,
                "recurrence_interval": recurrence_interval,
                "recurrence_end_date": recurrence_end_date,
            }
        )

    return sorted(normalized, key=lambda item: (item["start_date"], item["start_time"] or time(23, 59), item["title"]))


def family_checklist_template(item_type):
    normalized_type = str(item_type or "").strip().lower()
    if "appointment" in normalized_type:
        return ["Insurance card", "Intake paperwork", "Medication list", "Arrival 15 minutes early"]
    if "camp" in normalized_type or "sports" in normalized_type or "tournament" in normalized_type:
        return ["Registration confirmation", "Uniform/gear", "Water/snacks", "Drop-off and pickup plan"]
    if "camping" in normalized_type:
        return ["Tent and sleeping bags", "Camp meals and cooler", "Weather check", "Departure checklist"]
    if "trip" in normalized_type or "travel" in normalized_type:
        return ["Travel confirmations", "Packing list", "Transportation plan", "Return-day reset items"]
    return ["Confirm who is attending", "Confirm timing", "Add a reminder 24h before"]


def _family_shift_months(value, months):
    if not value:
        return value
    total_months = (value.year * 12 + (value.month - 1)) + months
    year_value = total_months // 12
    month_value = (total_months % 12) + 1
    max_day = calendar.monthrange(year_value, month_value)[1]
    return date(year_value, month_value, min(value.day, max_day))


def _family_shift_recurrence(value, recurrence_rule, recurrence_interval):
    safe_interval = max(1, int(recurrence_interval or 1))
    if recurrence_rule == "daily":
        return value + timedelta(days=safe_interval)
    if recurrence_rule == "weekly":
        return value + timedelta(days=7 * safe_interval)
    if recurrence_rule == "monthly":
        return _family_shift_months(value, safe_interval)
    if recurrence_rule == "yearly":
        return _family_shift_months(value, 12 * safe_interval)
    return value


def expand_family_schedule_items(family_items, end_day=None, window_days=14):
    safe_window_days = max(7, int(window_days or 14))
    anchor_day = end_day or mountain_today()
    window_end = anchor_day + timedelta(days=safe_window_days - 1)
    expanded = []

    for source_item in family_items:
        start_date = source_item.get("start_date")
        end_date = source_item.get("end_date") or start_date
        if not start_date:
            continue

        recurrence_rule = str(source_item.get("recurrence_rule") or "none").strip().lower()
        recurrence_interval = max(1, safe_int(source_item.get("recurrence_interval"), 1))
        recurrence_end_date = source_item.get("recurrence_end_date")
        span_days = max(0, (end_date - start_date).days)

        occurrence_start = start_date
        occurrence_end = end_date

        if recurrence_rule in ("daily", "weekly") and occurrence_start < anchor_day:
            step_days = recurrence_interval if recurrence_rule == "daily" else recurrence_interval * 7
            diff_days = (anchor_day - occurrence_start).days
            if step_days > 0 and diff_days > 0:
                jump_steps = max(0, diff_days // step_days)
                occurrence_start = occurrence_start + timedelta(days=jump_steps * step_days)
                occurrence_end = occurrence_start + timedelta(days=span_days)

        occurrence_index = 0
        safety_limit = 180
        while occurrence_start <= window_end and occurrence_index < safety_limit:
            if recurrence_end_date and occurrence_start > recurrence_end_date:
                break

            if occurrence_end >= anchor_day and occurrence_start <= window_end:
                expanded_item = dict(source_item)
                expanded_item["start_date"] = occurrence_start
                expanded_item["end_date"] = occurrence_end
                expanded_item["source_start_date"] = start_date
                expanded_item["occurrence_index"] = occurrence_index
                expanded_item["is_recurring_occurrence"] = recurrence_rule != "none"
                expanded.append(expanded_item)

            if recurrence_rule == "none":
                break

            next_start = _family_shift_recurrence(occurrence_start, recurrence_rule, recurrence_interval)
            if next_start == occurrence_start:
                break
            occurrence_start = next_start
            occurrence_end = occurrence_start + timedelta(days=span_days)
            occurrence_index += 1

    return sorted(expanded, key=lambda item: (item["start_date"], item.get("start_time") or time(23, 59), item["title"]))


def weekly_family_schedule_summary(family_items, end_day=None, window_days=14):
    safe_window_days = max(7, int(window_days or 14))
    anchor_day = end_day or mountain_today()
    upcoming = expand_family_schedule_items(family_items, end_day=anchor_day, window_days=safe_window_days)
    by_type = {}
    priority_counts = {"high": 0, "medium": 0, "low": 0}
    weekend_count = 0
    multi_day_count = 0
    recurring_count = 0
    checklist_count = 0

    for item in upcoming:
        normalized_type = str(item.get("item_type") or "Appointment").strip() or "Appointment"
        by_type[normalized_type] = by_type.get(normalized_type, 0) + 1
        priority = str(item.get("priority") or "medium").lower()
        if priority in priority_counts:
            priority_counts[priority] += 1
        if item.get("start_date") and item["start_date"].weekday() >= 5:
            weekend_count += 1
        if item.get("end_date") and item.get("end_date") > item.get("start_date"):
            multi_day_count += 1
        if item.get("is_recurring_occurrence"):
            recurring_count += 1
        if item.get("checklist_items"):
            checklist_count += 1

    appointment_count = sum(1 for item in upcoming if "appointment" in str(item.get("item_type") or "").lower())
    trip_count = sum(1 for item in upcoming if any(keyword in str(item.get("item_type") or "").lower() for keyword in ("trip", "travel")))
    camp_count = sum(1 for item in upcoming if any(keyword in str(item.get("item_type") or "").lower() for keyword in ("camp", "sports", "game")))

    return {
        "window_days": safe_window_days,
        "upcoming_count": len(upcoming),
        "appointment_count": appointment_count,
        "trip_count": trip_count,
        "camp_count": camp_count,
        "multi_day_count": multi_day_count,
        "recurring_count": recurring_count,
        "weekend_count": weekend_count,
        "items_with_checklists": checklist_count,
        "priority_counts": priority_counts,
        "by_type": by_type,
        "upcoming_items": upcoming,
    }


def normalize_family_weekly_notes(raw_notes):
    if not isinstance(raw_notes, list):
        return []

    normalized = []
    for index, raw_item in enumerate(raw_notes):
        if not isinstance(raw_item, dict):
            continue
        week_start = parse_date_value(raw_item.get("week_start"))
        digest_text = str(raw_item.get("digest_text") or "").strip()
        if not week_start or not digest_text:
            continue
        normalized.append(
            {
                "note_id": str(raw_item.get("note_id") or f"family_weekly_note_{index}_{week_start.isoformat()}").strip(),
                "source_index": index,
                "week_start": week_start,
                "digest_text": digest_text,
                "saved_at": str(raw_item.get("saved_at") or "").strip(),
            }
        )

    return sorted(normalized, key=lambda item: item["week_start"], reverse=True)


def normalize_quick_reminders(raw_reminders):
    if not isinstance(raw_reminders, list):
        return []

    normalized = []
    for source_index, raw_item in enumerate(raw_reminders):
        if not isinstance(raw_item, dict):
            continue
        text = str(raw_item.get("text") or raw_item.get("title") or "").strip()
        if not text:
            continue

        reminder_date = parse_date_value(raw_item.get("remind_date") or raw_item.get("due_date"))
        reminder_time = parse_time_value(raw_item.get("remind_time") or raw_item.get("due_time"))
        status = str(raw_item.get("status") or "active").strip().lower()
        if status not in ("active", "dismissed"):
            status = "active"

        normalized.append(
            {
                "reminder_id": str(raw_item.get("reminder_id") or f"quick_reminder_{source_index}_{text.lower().replace(' ', '_')}").strip(),
                "source_index": source_index,
                "text": text,
                "category": str(raw_item.get("category") or "General").strip() or "General",
                "notes": str(raw_item.get("notes") or "").strip(),
                "remind_date": reminder_date,
                "remind_time": reminder_time,
                "status": status,
                "created_at": str(raw_item.get("created_at") or "").strip(),
                "updated_at": str(raw_item.get("updated_at") or "").strip(),
            }
        )

    return sorted(
        normalized,
        key=lambda item: (
            0 if item.get("status") == "active" else 1,
            item.get("remind_date") or date.max,
            item.get("remind_time") or time(23, 59),
            item.get("text") or "",
        ),
    )


def normalize_clinic_day_closeout_template(raw_template):
    if isinstance(raw_template, str):
        raw_items = raw_template.splitlines()
    elif isinstance(raw_template, list):
        raw_items = raw_template
    else:
        raw_items = []

    cleaned = []
    seen = set()
    for raw_item in raw_items:
        item = str(raw_item or "").strip().strip("- ").strip()
        if not item:
            continue
        item_key = item.lower()
        if item_key in seen:
            continue
        cleaned.append(item)
        seen.add(item_key)

    if cleaned:
        return cleaned
    return list(CLINIC_DAY_CLOSEOUT_TEMPLATE_DEFAULTS)


def normalize_clinic_day_closeout_log(raw_log, allowed_items=None):
    if not isinstance(raw_log, dict):
        return {}

    allowed_lookup = None
    if isinstance(allowed_items, list):
        allowed_lookup = {str(item).strip().lower() for item in allowed_items if str(item).strip()}

    normalized = {}
    for raw_day, raw_entry in raw_log.items():
        try:
            day_value = date.fromisoformat(str(raw_day))
        except ValueError:
            continue

        if isinstance(raw_entry, dict):
            raw_completed = raw_entry.get("completed_items")
            notes_value = str(raw_entry.get("notes") or "").strip()
            saved_at_value = str(raw_entry.get("saved_at") or "").strip()
        else:
            raw_completed = raw_entry
            notes_value = ""
            saved_at_value = ""

        completed_items = []
        if isinstance(raw_completed, list):
            for item in raw_completed:
                text = str(item or "").strip()
                if not text:
                    continue
                if allowed_lookup is not None and text.lower() not in allowed_lookup:
                    continue
                if text not in completed_items:
                    completed_items.append(text)

        normalized[day_value.isoformat()] = {
            "completed_items": completed_items,
            "notes": notes_value,
            "saved_at": saved_at_value,
        }

    return dict(sorted(normalized.items()))


def normalize_ma_lead_weekly_metric_targets(raw_targets):
    targets_lookup = {}
    if isinstance(raw_targets, dict):
        targets_lookup = raw_targets

    normalized = []
    for metric in MA_LEAD_WEEKLY_METRIC_DEFAULTS:
        metric_key = metric["key"]
        raw_target = targets_lookup.get(metric_key, metric.get("target"))
        try:
            target_value = float(raw_target)
        except (TypeError, ValueError):
            target_value = float(metric.get("target") or 0.0)
        normalized.append(
            {
                "key": metric_key,
                "label": metric["label"],
                "unit": metric["unit"],
                "direction": metric["direction"],
                "target": round(target_value, 2),
            }
        )
    return normalized


def normalize_ma_lead_weekly_metrics_log(raw_log):
    if not isinstance(raw_log, dict):
        return {}

    valid_metric_keys = {item["key"] for item in MA_LEAD_WEEKLY_METRIC_DEFAULTS}
    normalized = {}
    for raw_week, raw_entry in raw_log.items():
        try:
            week_start = date.fromisoformat(str(raw_week))
        except ValueError:
            continue

        values = {}
        notes_value = ""
        saved_at_value = ""
        if isinstance(raw_entry, dict):
            notes_value = str(raw_entry.get("notes") or "").strip()
            saved_at_value = str(raw_entry.get("saved_at") or "").strip()
            raw_values = raw_entry.get("values")
            if isinstance(raw_values, dict):
                for key, raw_value in raw_values.items():
                    if key not in valid_metric_keys:
                        continue
                    try:
                        values[key] = round(float(raw_value), 2)
                    except (TypeError, ValueError):
                        continue

        normalized[week_start.isoformat()] = {
            "values": values,
            "notes": notes_value,
            "saved_at": saved_at_value,
        }

    return dict(sorted(normalized.items()))


def normalize_ma_lead_rollout_template(raw_template):
    if isinstance(raw_template, str):
        raw_items = raw_template.splitlines()
    elif isinstance(raw_template, list):
        raw_items = raw_template
    else:
        raw_items = []

    cleaned = []
    seen = set()
    for raw_item in raw_items:
        item = str(raw_item or "").strip().strip("- ").strip()
        if not item:
            continue
        item_key = item.lower()
        if item_key in seen:
            continue
        cleaned.append(item)
        seen.add(item_key)

    if cleaned:
        return cleaned
    return list(MA_LEAD_ROLLOUT_30_DAY_TEMPLATE_DEFAULTS)


def normalize_ma_lead_rollout_log(raw_log, allowed_items=None):
    if not isinstance(raw_log, dict):
        return {}

    allowed_lookup = None
    if isinstance(allowed_items, list):
        allowed_lookup = {str(item).strip().lower() for item in allowed_items if str(item).strip()}

    normalized = {}
    for raw_day, raw_entry in raw_log.items():
        try:
            day_value = date.fromisoformat(str(raw_day))
        except ValueError:
            continue

        completed_items = []
        notes_value = ""
        saved_at_value = ""
        if isinstance(raw_entry, dict):
            notes_value = str(raw_entry.get("notes") or "").strip()
            saved_at_value = str(raw_entry.get("saved_at") or "").strip()
            raw_completed_items = raw_entry.get("completed_items")
            if isinstance(raw_completed_items, list):
                for raw_item in raw_completed_items:
                    item = str(raw_item or "").strip()
                    if not item:
                        continue
                    if allowed_lookup is not None and item.lower() not in allowed_lookup:
                        continue
                    if item not in completed_items:
                        completed_items.append(item)

        normalized[day_value.isoformat()] = {
            "completed_items": completed_items,
            "notes": notes_value,
            "saved_at": saved_at_value,
        }

    return dict(sorted(normalized.items()))


def normalize_ma_lead_biweekly_template(raw_template):
    normalized = dict(MA_LEAD_BIWEEKLY_TEMPLATE_DEFAULTS)
    if isinstance(raw_template, dict):
        for key in normalized.keys():
            value = str(raw_template.get(key) or "").strip()
            if value:
                normalized[key] = value
    return normalized


def normalize_ma_lead_biweekly_settings(raw_settings):
    normalized = dict(MA_LEAD_BIWEEKLY_SETTINGS_DEFAULTS)
    if isinstance(raw_settings, dict):
        cadence_days = safe_int(raw_settings.get("cadence_days"), MA_LEAD_BIWEEKLY_SETTINGS_DEFAULTS["cadence_days"])
        reminder_lead_days = safe_int(raw_settings.get("reminder_lead_days"), MA_LEAD_BIWEEKLY_SETTINGS_DEFAULTS["reminder_lead_days"])
        normalized["cadence_days"] = max(7, min(30, cadence_days))
        normalized["reminder_lead_days"] = max(0, min(14, reminder_lead_days))
        normalized["include_private_notes_in_export"] = bool(raw_settings.get("include_private_notes_in_export"))
    return normalized


def normalize_ma_lead_biweekly_checkins(raw_items):
    if not isinstance(raw_items, list):
        return []

    status_options = {"on_track", "needs_support", "at_risk"}
    normalized = []
    for source_index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, dict):
            continue

        ma_name = str(raw_item.get("ma_name") or "").strip()
        checkin_date = parse_date_value(raw_item.get("checkin_date"))
        if not ma_name or not checkin_date:
            continue

        next_due_date = parse_date_value(raw_item.get("next_due_date"))
        if not next_due_date:
            next_due_date = checkin_date + timedelta(days=14)

        status_value = str(raw_item.get("status") or "on_track").strip().lower()
        if status_value not in status_options:
            status_value = "on_track"

        confidence_score = max(1, min(5, safe_int(raw_item.get("confidence_score"), 3)))
        workload_score = max(1, min(5, safe_int(raw_item.get("workload_score"), 3)))

        normalized.append(
            {
                "checkin_id": str(raw_item.get("checkin_id") or f"ma_checkin_{source_index}_{ma_name.lower().replace(' ', '_')}_{checkin_date.isoformat()}").strip(),
                "source_index": source_index,
                "ma_name": ma_name,
                "checkin_date": checkin_date,
                "next_due_date": next_due_date,
                "status": status_value,
                "confidence_score": confidence_score,
                "workload_score": workload_score,
                "wins": str(raw_item.get("wins") or "").strip(),
                "blockers": str(raw_item.get("blockers") or "").strip(),
                "clarifications": str(raw_item.get("clarifications") or "").strip(),
                "coaching_focus": str(raw_item.get("coaching_focus") or "").strip(),
                "support_needed": str(raw_item.get("support_needed") or "").strip(),
                "public_notes": str(raw_item.get("public_notes") or "").strip(),
                "private_notes": str(raw_item.get("private_notes") or "").strip(),
                "created_at": str(raw_item.get("created_at") or "").strip(),
                "updated_at": str(raw_item.get("updated_at") or "").strip(),
            }
        )

    return sorted(
        normalized,
        key=lambda item: (
            item.get("checkin_date") or date.min,
            str(item.get("ma_name") or "").lower(),
            str(item.get("checkin_id") or ""),
        ),
        reverse=True,
    )


def normalize_ma_lead_biweekly_action_items(raw_items):
    if not isinstance(raw_items, list):
        return []

    status_options = {"open", "completed", "canceled"}
    normalized = []
    for source_index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, dict):
            continue

        action_text = str(raw_item.get("action_text") or "").strip()
        ma_name = str(raw_item.get("ma_name") or "").strip()
        if not action_text or not ma_name:
            continue

        status_value = str(raw_item.get("status") or "open").strip().lower()
        if status_value not in status_options:
            status_value = "open"

        due_date = parse_date_value(raw_item.get("due_date"))
        completed_date = parse_date_value(raw_item.get("completed_date"))

        normalized.append(
            {
                "action_id": str(raw_item.get("action_id") or f"ma_action_{source_index}_{ma_name.lower().replace(' ', '_')}").strip(),
                "source_index": source_index,
                "checkin_id": str(raw_item.get("checkin_id") or "").strip(),
                "ma_name": ma_name,
                "action_text": action_text,
                "owner_name": str(raw_item.get("owner_name") or "MA Lead").strip() or "MA Lead",
                "due_date": due_date,
                "status": status_value,
                "completed_date": completed_date,
                "notes": str(raw_item.get("notes") or "").strip(),
                "created_at": str(raw_item.get("created_at") or "").strip(),
                "updated_at": str(raw_item.get("updated_at") or "").strip(),
            }
        )

    return sorted(
        normalized,
        key=lambda item: (
            0 if item.get("status") == "open" else 1,
            item.get("due_date") or date.max,
            str(item.get("ma_name") or "").lower(),
        ),
    )


def monday_week_bounds(day_value):
    week_start = day_value - timedelta(days=day_value.weekday())
    week_end = week_start + timedelta(days=6)
    return week_start, week_end


def monday_week_days(day_value):
    week_start, _ = monday_week_bounds(day_value)
    return [week_start + timedelta(days=offset) for offset in range(7)]


def _day_feel_label_from_score(score):
    if score is None:
        return "No data"
    if score >= 4.5:
        return "Great"
    if score >= 3.5:
        return "Good"
    if score >= 2.5:
        return "Steady"
    if score >= 1.5:
        return "Heavy"
    return "Rough"


def weekly_morning_ritual_trends(checkins, end_day=None, window_days=7):
    anchor_day = end_day or mountain_today()
    window = monday_week_days(anchor_day)

    sleep_score_map = {"Poor": 1, "Fair": 2, "Good": 3, "Great": 4}
    energy_score_map = {"Low": 1, "Medium": 2, "High": 3}
    mood_score_map = {"Drained": 1, "Neutral": 2, "Positive": 3, "Focused": 4}

    entries = []
    sleep_series = []
    energy_series = []
    mood_series = []
    day_labels = []
    for day_value in window:
        day_key = day_value.isoformat()
        day_labels.append(day_value.strftime("%a"))
        entry = checkins.get(day_key)
        if isinstance(entry, dict):
            entries.append((day_value, entry))
            sleep_series.append(sleep_score_map.get(entry.get("sleep_quality")))
            energy_series.append(energy_score_map.get(entry.get("energy_level")))
            mood_series.append(mood_score_map.get(entry.get("mood")))
        else:
            sleep_series.append(None)
            energy_series.append(None)
            mood_series.append(None)

    checkin_count = len(entries)
    consistency_rate = checkin_count / 7.0

    sleep_counts = {label: 0 for label in MORNING_SLEEP_OPTIONS}
    energy_counts = {label: 0 for label in MORNING_ENERGY_OPTIONS}
    mood_counts = {label: 0 for label in MORNING_MOOD_OPTIONS}
    planned_yes_count = 0
    grounding_complete_count = 0

    sleep_scores = []
    energy_scores = []
    mood_scores = []

    for _, entry in entries:
        sleep_quality = entry.get("sleep_quality")
        if sleep_quality in sleep_counts:
            sleep_counts[sleep_quality] += 1
            sleep_scores.append(sleep_score_map[sleep_quality])

        energy_level = entry.get("energy_level")
        if energy_level in energy_counts:
            energy_counts[energy_level] += 1
            energy_scores.append(energy_score_map[energy_level])

        mood = entry.get("mood")
        if mood in mood_counts:
            mood_counts[mood] += 1
            mood_scores.append(mood_score_map[mood])

        if entry.get("planned_morning_goals") == "Yes":
            planned_yes_count += 1
        if entry.get("optional_grounding_complete"):
            grounding_complete_count += 1

    average_sleep = (sum(sleep_scores) / len(sleep_scores)) if sleep_scores else None
    average_energy = (sum(energy_scores) / len(energy_scores)) if energy_scores else None
    average_mood = (sum(mood_scores) / len(mood_scores)) if mood_scores else None

    def nearest_label(value, score_map):
        if value is None:
            return "No data"
        best_label = None
        best_distance = None
        for label, score in score_map.items():
            distance = abs(float(value) - float(score))
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_label = label
        return best_label or "No data"

    return {
        "window_days": 7,
        "week_start": window[0],
        "week_end": window[-1],
        "checkin_count": checkin_count,
        "consistency_rate": consistency_rate,
        "sleep_counts": sleep_counts,
        "energy_counts": energy_counts,
        "mood_counts": mood_counts,
        "average_sleep_label": nearest_label(average_sleep, sleep_score_map),
        "average_energy_label": nearest_label(average_energy, energy_score_map),
        "average_mood_label": nearest_label(average_mood, mood_score_map),
        "planned_yes_count": planned_yes_count,
        "grounding_complete_count": grounding_complete_count,
        "sleep_series": sleep_series,
        "energy_series": energy_series,
        "mood_series": mood_series,
        "day_labels": day_labels,
    }


def morning_ritual_weekly_history(checkins, max_weeks=12):
    week_starts = set()
    for day_key in checkins.keys():
        try:
            day_value = date.fromisoformat(str(day_key))
        except ValueError:
            continue
        week_starts.add(monday_week_bounds(day_value)[0])

    history = []
    for week_start in sorted(week_starts, reverse=True)[: max(1, int(max_weeks or 12))]:
        trend = weekly_morning_ritual_trends(checkins, end_day=week_start)
        history.append(
            {
                "week_start": trend["week_start"],
                "week_end": trend["week_end"],
                "checkin_count": trend["checkin_count"],
                "consistency_rate": trend["consistency_rate"],
                "average_sleep_label": trend["average_sleep_label"],
                "average_energy_label": trend["average_energy_label"],
                "planned_yes_count": trend["planned_yes_count"],
                "grounding_complete_count": trend["grounding_complete_count"],
            }
        )
    return history


def morning_ritual_monthly_history(weekly_history, max_months=6):
    monthly = {}
    for row in weekly_history:
        week_start = row.get("week_start")
        if not week_start:
            continue
        month_key = week_start.strftime("%Y-%m")
        month_bucket = monthly.setdefault(
            month_key,
            {
                "month_key": month_key,
                "week_count": 0,
                "checkin_count": 0,
                "planned_yes_count": 0,
                "grounding_complete_count": 0,
            },
        )
        month_bucket["week_count"] += 1
        month_bucket["checkin_count"] += int(row.get("checkin_count") or 0)
        month_bucket["planned_yes_count"] += int(row.get("planned_yes_count") or 0)
        month_bucket["grounding_complete_count"] += int(row.get("grounding_complete_count") or 0)

    rows = []
    for month_key in sorted(monthly.keys(), reverse=True)[: max(1, int(max_months or 6))]:
        bucket = monthly[month_key]
        weeks = max(1, int(bucket["week_count"]))
        checkins = int(bucket["checkin_count"])
        rows.append(
            {
                **bucket,
                "consistency_rate": checkins / float(weeks * 7),
                "planned_yes_rate": (bucket["planned_yes_count"] / float(checkins)) if checkins else None,
                "grounding_rate": (bucket["grounding_complete_count"] / float(checkins)) if checkins else None,
            }
        )
    return rows


def render_mini_sparkline(label, values, max_value, day_labels):
    bars = []
    for index, value in enumerate(values):
        day_label = day_labels[index] if index < len(day_labels) else ""
        if value is None:
            bars.append(
                f"<div title='{day_label}: no check-in' style='width:10px; height:8px; border-radius:3px; border:1px dashed rgba(148,163,184,0.45); background:transparent;'></div>"
            )
            continue

        normalized = float(value - 1) / float(max(1, max_value - 1))
        if normalized >= 0.67:
            color = "#22c55e"
        elif normalized >= 0.34:
            color = "#f59e0b"
        else:
            color = "#ef4444"
        height = 8 + int(round(normalized * 14))
        bars.append(
            f"<div title='{day_label}: {value}/{max_value}' style='width:10px; height:{height}px; border-radius:3px; background:{color}; opacity:0.95;'></div>"
        )

    return (
        "<div style='margin:0.25rem 0 0.5rem;'>"
        f"<div style='font-size:0.78rem; color:rgba(226,232,240,0.9); margin-bottom:0.2rem;'>{label}</div>"
        "<div style='display:flex; align-items:flex-end; gap:4px;'>"
        + "".join(bars)
        + "</div></div>"
    )


def weekly_nightly_reflection_trends(reflections, end_day=None, window_days=7):
    anchor_day = end_day or mountain_today()
    window = monday_week_days(anchor_day)

    feel_score_map = {
        "Rough": 1,
        "Heavy": 2,
        "Steady": 3,
        "Good": 4,
        "Great": 5,
    }

    entries = []
    day_labels = []
    feel_series = []
    morning_series = []
    for day_value in window:
        day_key = day_value.isoformat()
        day_labels.append(day_value.strftime("%a"))
        entry = reflections.get(day_key)
        if isinstance(entry, dict):
            entries.append((day_value, entry))
            feel_series.append(feel_score_map.get(entry.get("day_feel")))
            morning_status = entry.get("morning_goal_status")
            if morning_status == "Yes":
                morning_series.append(2)
            elif morning_status == "No":
                morning_series.append(1)
            else:
                morning_series.append(None)
        else:
            feel_series.append(None)
            morning_series.append(None)

    checkin_count = len(entries)
    consistency_rate = checkin_count / 7.0

    morning_yes_count = 0
    morning_applicable_count = 0
    feel_counts = {label: 0 for label in DAY_FEEL_OPTIONS}
    wins_logged = 0
    improvements_logged = 0

    for _, entry in entries:
        morning_status = entry.get("morning_goal_status")
        if morning_status == "Yes":
            morning_yes_count += 1
            morning_applicable_count += 1
        elif morning_status == "No":
            morning_applicable_count += 1

        day_feel = entry.get("day_feel")
        if day_feel in feel_counts:
            feel_counts[day_feel] += 1

        if str(entry.get("one_win") or "").strip():
            wins_logged += 1
        if str(entry.get("area_of_improvement") or "").strip():
            improvements_logged += 1

    scored_entries = [feel_score_map[entry.get("day_feel")] for _, entry in entries if entry.get("day_feel") in feel_score_map]
    average_feel_score = (sum(scored_entries) / len(scored_entries)) if scored_entries else None

    average_feel_label = _day_feel_label_from_score(average_feel_score)

    morning_completion_rate = None
    if morning_applicable_count > 0:
        morning_completion_rate = morning_yes_count / float(morning_applicable_count)

    return {
        "window_days": 7,
        "week_start": window[0],
        "week_end": window[-1],
        "checkin_count": checkin_count,
        "consistency_rate": consistency_rate,
        "morning_yes_count": morning_yes_count,
        "morning_applicable_count": morning_applicable_count,
        "morning_completion_rate": morning_completion_rate,
        "feel_counts": feel_counts,
        "average_feel_label": average_feel_label,
        "average_feel_score": average_feel_score,
        "wins_logged": wins_logged,
        "improvements_logged": improvements_logged,
        "feel_series": feel_series,
        "morning_series": morning_series,
        "day_labels": day_labels,
    }


def nightly_reflection_weekly_history(reflections, max_weeks=12):
    week_starts = set()
    for day_key in reflections.keys():
        try:
            day_value = date.fromisoformat(str(day_key))
        except ValueError:
            continue
        week_starts.add(monday_week_bounds(day_value)[0])

    history = []
    for week_start in sorted(week_starts, reverse=True)[: max(1, int(max_weeks or 12))]:
        trend = weekly_nightly_reflection_trends(reflections, end_day=week_start)
        history.append(
            {
                "week_start": trend["week_start"],
                "week_end": trend["week_end"],
                "checkin_count": trend["checkin_count"],
                "consistency_rate": trend["consistency_rate"],
                "average_feel_score": trend["average_feel_score"],
                "average_feel_label": trend["average_feel_label"],
                "morning_yes_count": trend["morning_yes_count"],
                "morning_applicable_count": trend["morning_applicable_count"],
                "wins_logged": trend["wins_logged"],
                "improvements_logged": trend["improvements_logged"],
            }
        )
    return history


def nightly_reflection_monthly_history(weekly_history, max_months=6):
    monthly = {}
    for row in weekly_history:
        week_start = row.get("week_start")
        if not week_start:
            continue
        month_key = week_start.strftime("%Y-%m")
        month_bucket = monthly.setdefault(
            month_key,
            {
                "month_key": month_key,
                "week_count": 0,
                "checkin_count": 0,
                "morning_yes_count": 0,
                "morning_applicable_count": 0,
                "wins_logged": 0,
                "improvements_logged": 0,
                "feel_score_weighted_sum": 0.0,
                "feel_score_weighted_count": 0,
            },
        )
        checkins = int(row.get("checkin_count") or 0)
        month_bucket["week_count"] += 1
        month_bucket["checkin_count"] += checkins
        month_bucket["morning_yes_count"] += int(row.get("morning_yes_count") or 0)
        month_bucket["morning_applicable_count"] += int(row.get("morning_applicable_count") or 0)
        month_bucket["wins_logged"] += int(row.get("wins_logged") or 0)
        month_bucket["improvements_logged"] += int(row.get("improvements_logged") or 0)
        feel_score = row.get("average_feel_score")
        if feel_score is not None and checkins > 0:
            month_bucket["feel_score_weighted_sum"] += float(feel_score) * float(checkins)
            month_bucket["feel_score_weighted_count"] += checkins

    rows = []
    for month_key in sorted(monthly.keys(), reverse=True)[: max(1, int(max_months or 6))]:
        bucket = monthly[month_key]
        weeks = max(1, int(bucket["week_count"]))
        checkins = int(bucket["checkin_count"])
        weighted_count = int(bucket["feel_score_weighted_count"])
        avg_feel_score = (
            bucket["feel_score_weighted_sum"] / float(weighted_count)
            if weighted_count > 0
            else None
        )
        rows.append(
            {
                **bucket,
                "consistency_rate": checkins / float(weeks * 7),
                "morning_completion_rate": (
                    bucket["morning_yes_count"] / float(bucket["morning_applicable_count"])
                    if bucket["morning_applicable_count"]
                    else None
                ),
                "average_feel_score": avg_feel_score,
                "average_feel_label": _day_feel_label_from_score(avg_feel_score),
            }
        )
    return rows


def generate_nightly_journal_prompt(
    review_day,
    morning_goal_status,
    day_feel,
    area_of_improvement,
    one_win,
    completed_count,
    active_count,
):
    prompt_openers = [
        "Write about how today shaped who you are becoming.",
        "Reflect on the moments that mattered most today.",
        "Describe the lesson hidden inside today's pace.",
        "Capture what today taught you about your priorities.",
        "Close the day by naming what to carry forward and what to release.",
    ]
    opener = prompt_openers[review_day.toordinal() % len(prompt_openers)]

    morning_line = {
        "Yes": "You followed through on your morning goals.",
        "No": "Your morning goals did not fully land today.",
        "Not applicable today": "Morning goals were not in play today.",
    }.get(morning_goal_status, "Morning goals were not in play today.")

    improvement_line = area_of_improvement.strip() or "one habit to improve tomorrow"
    win_line = one_win.strip() or "one win you are proud of"

    return (
        f"{opener} {morning_line} You completed {completed_count} task(s) and have {active_count} active task(s) left. "
        f"The day felt {day_feel.lower()}. Journal about {win_line}, then write one concrete next step to improve {improvement_line}."
    )


DB_CANDIDATE_SOURCE = None
DB_URL = None
DB_ERROR = None


def db_enabled():
    return bool(DB_URL) and DB_ERROR is None


def get_connection():
    if not DB_URL:
        raise RuntimeError("Database URL is not configured.")
    return psycopg.connect(DB_URL)


def initialize_database():
    global DB_URL
    global DB_ERROR
    global DB_CANDIDATE_SOURCE

    DB_URL = None
    DB_ERROR = None
    DB_CANDIDATE_SOURCE = None

    candidates = database_url_candidates()
    if not candidates:
        return

    errors = []
    for source_name, candidate_url in candidates:
        try:
            with psycopg.connect(candidate_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS tasks (
                            id BIGSERIAL PRIMARY KEY,
                            title TEXT NOT NULL,
                            description TEXT NOT NULL DEFAULT '',
                            category TEXT NOT NULL,
                            priority TEXT NOT NULL,
                            status TEXT NOT NULL DEFAULT 'todo',
                            created_date DATE NOT NULL,
                            due_date DATE,
                            scheduled_date DATE,
                            scheduled_end_date DATE,
                            scheduled_time TIME,
                            scheduled_minutes INTEGER,
                            recurrence_rule TEXT,
                            recurrence_interval INTEGER,
                            completed_date DATE,
                            completed_at TIMESTAMP
                        )
                        """
                    )
                    cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS recurrence_rule TEXT")
                    cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS recurrence_interval INTEGER")
                    cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS scheduled_end_date DATE")
                    cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP")
                    cur.execute(
                        """
                        UPDATE tasks
                        SET completed_at = completed_date::timestamp
                        WHERE status = 'completed'
                          AND completed_at IS NULL
                          AND completed_date IS NOT NULL
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS app_settings (
                            id INTEGER PRIMARY KEY,
                            payload TEXT NOT NULL,
                            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS personal_goals (
                            id BIGSERIAL PRIMARY KEY,
                            title TEXT NOT NULL,
                            category TEXT NOT NULL,
                            target_frequency INTEGER NOT NULL DEFAULT 3,
                            notes TEXT NOT NULL DEFAULT '',
                            reminder_days TEXT NOT NULL DEFAULT '[]',
                            status TEXT NOT NULL DEFAULT 'active',
                            created_date DATE NOT NULL
                        )
                        """
                    )
                    cur.execute("ALTER TABLE personal_goals ADD COLUMN IF NOT EXISTS reminder_days TEXT NOT NULL DEFAULT '[]'")
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS personal_goal_checkins (
                            id BIGSERIAL PRIMARY KEY,
                            goal_id BIGINT NOT NULL REFERENCES personal_goals(id) ON DELETE CASCADE,
                            checked_in_date DATE NOT NULL,
                            note TEXT NOT NULL DEFAULT '',
                            created_date DATE NOT NULL,
                            UNIQUE(goal_id, checked_in_date)
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS surgical_cases (
                            id BIGSERIAL PRIMARY KEY,
                            case_date DATE NOT NULL,
                            case_stream TEXT NOT NULL,
                            procedure_name TEXT NOT NULL,
                            anatomical_location TEXT NOT NULL DEFAULT '',
                            status TEXT NOT NULL DEFAULT 'planned',
                            notes TEXT NOT NULL DEFAULT '',
                            education_url TEXT,
                            education_notes TEXT NOT NULL DEFAULT '',
                            created_date DATE NOT NULL
                        )
                        """
                    )
                    cur.execute("ALTER TABLE surgical_cases ADD COLUMN IF NOT EXISTS education_url TEXT")
                    cur.execute("ALTER TABLE surgical_cases ADD COLUMN IF NOT EXISTS education_notes TEXT NOT NULL DEFAULT ''")
                    cur.execute("ALTER TABLE surgical_cases ADD COLUMN IF NOT EXISTS cpt_codes TEXT NOT NULL DEFAULT ''")
                    cur.execute("ALTER TABLE surgical_cases ADD COLUMN IF NOT EXISTS or_facility TEXT NOT NULL DEFAULT 'Mercy OR'")
                    cur.execute("ALTER TABLE surgical_cases ADD COLUMN IF NOT EXISTS pt_destination TEXT NOT NULL DEFAULT ''")
                    cur.execute("ALTER TABLE surgical_cases ADD COLUMN IF NOT EXISTS pt_protocol TEXT NOT NULL DEFAULT ''")
                    cur.execute("ALTER TABLE surgical_cases ADD COLUMN IF NOT EXISTS dme_dispensed TEXT NOT NULL DEFAULT ''")
                    cur.execute("ALTER TABLE surgical_cases ADD COLUMN IF NOT EXISTS post_op_plan TEXT NOT NULL DEFAULT ''")
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS protocol_documents (
                            id BIGSERIAL PRIMARY KEY,
                            surgeon_label TEXT NOT NULL,
                            protocol_name TEXT NOT NULL,
                            file_name TEXT NOT NULL,
                            file_mime TEXT,
                            file_bytes BYTEA NOT NULL,
                            notes TEXT NOT NULL DEFAULT '',
                            created_date DATE NOT NULL
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS surgical_case_protocol_links (
                            id BIGSERIAL PRIMARY KEY,
                            case_id BIGINT NOT NULL REFERENCES surgical_cases(id) ON DELETE CASCADE,
                            protocol_id BIGINT NOT NULL REFERENCES protocol_documents(id) ON DELETE CASCADE,
                            created_date DATE NOT NULL,
                            UNIQUE(case_id, protocol_id)
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS anatomy_xray_images (
                            id BIGSERIAL PRIMARY KEY,
                            body_part TEXT NOT NULL,
                            fracture_type TEXT NOT NULL,
                            view_label TEXT NOT NULL,
                            image_name TEXT NOT NULL,
                            image_mime TEXT,
                            image_bytes BYTEA NOT NULL,
                            notes TEXT NOT NULL DEFAULT '',
                            created_date DATE NOT NULL
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS anatomy_quiz_attempts (
                            id BIGSERIAL PRIMARY KEY,
                            quiz_mode TEXT NOT NULL,
                            prompt TEXT NOT NULL,
                            expected_answer TEXT NOT NULL,
                            submitted_answer TEXT NOT NULL,
                            confidence INTEGER,
                            is_correct BOOLEAN NOT NULL,
                            explanation TEXT NOT NULL DEFAULT '',
                            created_date DATE NOT NULL
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS anatomy_quiz_review_queue (
                            id BIGSERIAL PRIMARY KEY,
                            review_text TEXT NOT NULL,
                            created_date DATE NOT NULL
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS lead_clinical_issues (
                            id BIGSERIAL PRIMARY KEY,
                            title TEXT NOT NULL,
                            details TEXT NOT NULL DEFAULT '',
                            issue_type TEXT NOT NULL DEFAULT 'Clinical task',
                            source_lane TEXT NOT NULL DEFAULT 'clinical_staff',
                            urgency TEXT NOT NULL DEFAULT 'medium',
                            status TEXT NOT NULL DEFAULT 'new',
                            owner_name TEXT NOT NULL DEFAULT '',
                            due_date DATE,
                            due_time TIME,
                            escalation_target TEXT NOT NULL DEFAULT 'none',
                            escalation_reason TEXT NOT NULL DEFAULT '',
                            decision_needed_by DATE,
                            dependency_owner TEXT NOT NULL DEFAULT '',
                            resolved_date DATE,
                            created_date DATE NOT NULL
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS lead_sop_entries (
                            id BIGSERIAL PRIMARY KEY,
                            title TEXT NOT NULL,
                            topic TEXT NOT NULL DEFAULT 'General',
                            owner_name TEXT NOT NULL DEFAULT '',
                            version_tag TEXT NOT NULL DEFAULT 'v1.0',
                            quick_steps TEXT NOT NULL DEFAULT '',
                            link_url TEXT NOT NULL DEFAULT '',
                            status TEXT NOT NULL DEFAULT 'active',
                            updated_date DATE NOT NULL,
                            created_date DATE NOT NULL
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS lead_relationship_touchpoints (
                            id BIGSERIAL PRIMARY KEY,
                            person_name TEXT NOT NULL,
                            role_label TEXT NOT NULL DEFAULT '',
                            relationship_type TEXT NOT NULL DEFAULT 'Clinical staff',
                            status_label TEXT NOT NULL DEFAULT 'green',
                            last_touch_date DATE,
                            next_follow_up_date DATE,
                            open_asks TEXT NOT NULL DEFAULT '',
                            recent_win TEXT NOT NULL DEFAULT '',
                            notes TEXT NOT NULL DEFAULT '',
                            created_date DATE NOT NULL
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS lead_ma_assignments (
                            id BIGSERIAL PRIMARY KEY,
                            ma_name TEXT NOT NULL,
                            provider_name TEXT NOT NULL DEFAULT '',
                            stocking_rooms TEXT NOT NULL DEFAULT '',
                            additional_tasks TEXT NOT NULL DEFAULT '',
                            clinic_days TEXT NOT NULL DEFAULT '',
                            status TEXT NOT NULL DEFAULT 'active',
                            created_date DATE NOT NULL,
                            updated_date DATE NOT NULL
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS lead_huddle_logs (
                            id BIGSERIAL PRIMARY KEY,
                            huddle_date DATE NOT NULL,
                            priority_focus TEXT NOT NULL DEFAULT '',
                            staffing_notes TEXT NOT NULL DEFAULT '',
                            escalation_notes TEXT NOT NULL DEFAULT '',
                            recap_sent_to TEXT NOT NULL DEFAULT '',
                            shift_notes TEXT NOT NULL DEFAULT '',
                            created_date DATE NOT NULL
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS lead_skill_signoffs (
                            id BIGSERIAL PRIMARY KEY,
                            staff_name TEXT NOT NULL,
                            role_label TEXT NOT NULL DEFAULT '',
                            skill_name TEXT NOT NULL,
                            status TEXT NOT NULL DEFAULT 'pending',
                            due_date DATE,
                            signed_off_date DATE,
                            signed_off_by TEXT NOT NULL DEFAULT '',
                            notes TEXT NOT NULL DEFAULT '',
                            created_date DATE NOT NULL
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS lead_education_requests (
                            id BIGSERIAL PRIMARY KEY,
                            request_title TEXT NOT NULL,
                            requesting_team TEXT NOT NULL DEFAULT '',
                            topic TEXT NOT NULL DEFAULT 'General',
                            priority TEXT NOT NULL DEFAULT 'medium',
                            status TEXT NOT NULL DEFAULT 'new',
                            needed_by_date DATE,
                            session_date DATE,
                            owner_name TEXT NOT NULL DEFAULT '',
                            notes TEXT NOT NULL DEFAULT '',
                            created_date DATE NOT NULL
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS autoclave_maintenance_items (
                            id BIGSERIAL PRIMARY KEY,
                            unit_label TEXT NOT NULL,
                            maintenance_type TEXT NOT NULL DEFAULT 'Routine check',
                            frequency_label TEXT NOT NULL DEFAULT 'Weekly',
                            last_completed_date DATE,
                            next_due_date DATE,
                            status TEXT NOT NULL DEFAULT 'due_soon',
                            owner_name TEXT NOT NULL DEFAULT '',
                            vendor_contact TEXT NOT NULL DEFAULT '',
                            notes TEXT NOT NULL DEFAULT '',
                            created_date DATE NOT NULL
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS lead_documents (
                            id BIGSERIAL PRIMARY KEY,
                            section_key TEXT NOT NULL DEFAULT 'General',
                            record_type TEXT,
                            record_id BIGINT,
                            title TEXT NOT NULL,
                            file_name TEXT NOT NULL,
                            file_mime TEXT,
                            file_bytes BYTEA NOT NULL,
                            notes TEXT NOT NULL DEFAULT '',
                            uploaded_by TEXT NOT NULL DEFAULT '',
                            created_date DATE NOT NULL
                        )
                        """
                    )
                    cur.execute("ALTER TABLE lead_documents ADD COLUMN IF NOT EXISTS record_type TEXT")
                    cur.execute("ALTER TABLE lead_documents ADD COLUMN IF NOT EXISTS record_id BIGINT")
            DB_URL = candidate_url
            DB_CANDIDATE_SOURCE = source_name
            return
        except psycopg.Error as exc:
            errors.append(f"{source_name}: {exc}")

    DB_ERROR = " | ".join(errors)


def _task_visible_in_app(task, reference_time=None):
    if task.get("status") != "completed":
        return True

    now = reference_time or datetime.utcnow()
    completed_at = task.get("completed_at")
    if isinstance(completed_at, datetime):
        return now - completed_at <= timedelta(hours=24)

    completed_date = task.get("completed_date")
    if isinstance(completed_date, date):
        return now - datetime.combine(completed_date, time.min) <= timedelta(hours=24)

    return True


def load_tasks():
    if not db_enabled():
        return [task for task in st.session_state.tasks if _task_visible_in_app(task)]
    try:
        with get_connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        title,
                        description,
                        category,
                        priority,
                        status,
                        created_date,
                        due_date,
                        scheduled_date,
                        scheduled_end_date,
                        scheduled_time,
                        scheduled_minutes,
                        recurrence_rule,
                        recurrence_interval,
                        completed_date,
                        completed_at
                    FROM tasks
                    WHERE status <> 'completed'
                       OR completed_at IS NULL
                       OR completed_at >= NOW() - INTERVAL '24 hours'
                    ORDER BY created_date DESC, id DESC
                    """
                )
                return cur.fetchall()
    except psycopg.Error:
        return [task for task in st.session_state.tasks if _task_visible_in_app(task)]


def load_surgical_cases():
    if not db_enabled():
        return st.session_state.surgical_cases
    try:
        with get_connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        case_date,
                        case_stream,
                        procedure_name,
                        anatomical_location,
                        cpt_codes,
                        status,
                        notes,
                        education_url,
                        education_notes,
                        pt_destination,
                        pt_protocol,
                        dme_dispensed,
                        post_op_plan,
                        created_date
                    FROM surgical_cases
                    ORDER BY case_date DESC, id DESC
                    """
                )
                return cur.fetchall()
    except psycopg.Error:
        return st.session_state.surgical_cases


def add_surgical_case(
    case_date,
    case_stream,
    procedure_name,
    anatomical_location,
    or_facility="Mercy OR",
    cpt_codes="",
    status="planned",
    notes="",
    education_url="",
    education_notes="",
    pt_destination="",
    pt_protocol="",
    dme_dispensed="",
    post_op_plan="",
):
    stream_value = case_stream.strip()
    procedure_value = procedure_name.strip()
    location_value = anatomical_location.strip()
    facility_value = (or_facility or "Mercy OR").strip()
    cpt_codes_value = cpt_codes.strip()
    notes_value = notes.strip()
    education_url_value = education_url.strip()
    education_notes_value = education_notes.strip()
    pt_destination_value = pt_destination.strip()
    pt_protocol_value = pt_protocol.strip()
    dme_dispensed_value = dme_dispensed.strip()
    post_op_plan_value = post_op_plan.strip()
    if not stream_value or not procedure_value:
        return

    if db_enabled():
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO surgical_cases (
                        case_date,
                        case_stream,
                        procedure_name,
                        anatomical_location,
                        or_facility,
                        cpt_codes,
                        status,
                        notes,
                        education_url,
                        education_notes,
                        pt_destination,
                        pt_protocol,
                        dme_dispensed,
                        post_op_plan,
                        created_date
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        case_date,
                        stream_value,
                        procedure_value,
                        location_value,
                        facility_value,
                        cpt_codes_value,
                        status,
                        notes_value,
                        education_url_value,
                        education_notes_value,
                        pt_destination_value,
                        pt_protocol_value,
                        dme_dispensed_value,
                        post_op_plan_value,
                        mountain_today(),
                    ),
                )
        return

    next_id = max([item.get("id", 0) for item in st.session_state.surgical_cases], default=0) + 1
    st.session_state.surgical_cases.append(
        {
            "id": next_id,
            "case_date": case_date,
            "case_stream": stream_value,
            "procedure_name": procedure_value,
            "anatomical_location": location_value,
            "or_facility": facility_value,
            "cpt_codes": cpt_codes_value,
            "status": status,
            "notes": notes_value,
            "education_url": education_url_value,
            "education_notes": education_notes_value,
            "pt_destination": pt_destination_value,
            "pt_protocol": pt_protocol_value,
            "dme_dispensed": dme_dispensed_value,
            "post_op_plan": post_op_plan_value,
            "created_date": mountain_today(),
        }
    )


def update_surgical_case(case_id, **fields):
    allowed_fields = {
        "case_date",
        "case_stream",
        "procedure_name",
        "anatomical_location",
        "or_facility",
        "cpt_codes",
        "status",
        "notes",
        "education_url",
        "education_notes",
        "pt_destination",
        "pt_protocol",
        "dme_dispensed",
        "post_op_plan",
    }
    sanitized = {key: value for key, value in fields.items() if key in allowed_fields}
    if not sanitized:
        return

    if db_enabled():
        set_parts = []
        values = []
        for key, value in sanitized.items():
            set_parts.append(f"{key} = %s")
            values.append(value)
        values.append(case_id)
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE surgical_cases SET {', '.join(set_parts)} WHERE id = %s", tuple(values))
        return

    for item in st.session_state.surgical_cases:
        if item.get("id") == case_id:
            item.update(sanitized)
            return


def delete_surgical_case(case_id):
    if db_enabled():
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM surgical_cases WHERE id = %s", (case_id,))
        return
    st.session_state.surgical_cases = [item for item in st.session_state.surgical_cases if item.get("id") != case_id]
    st.session_state.case_protocol_links = [item for item in st.session_state.case_protocol_links if item.get("case_id") != case_id]


def load_protocol_documents():
    if not db_enabled():
        return st.session_state.protocol_documents
    try:
        with get_connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        surgeon_label,
                        protocol_name,
                        file_name,
                        file_mime,
                        file_bytes,
                        notes,
                        created_date
                    FROM protocol_documents
                    ORDER BY created_date DESC, id DESC
                    """
                )
                return cur.fetchall()
    except psycopg.Error:
        return st.session_state.protocol_documents


def add_protocol_document(surgeon_label, protocol_name, upload_name, upload_mime, upload_bytes, notes=""):
    surgeon_value = surgeon_label.strip() or "Dr. Braden Boyer (BB)"
    protocol_value = protocol_name.strip() or upload_name
    notes_value = notes.strip()
    if not upload_bytes:
        return

    if db_enabled():
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO protocol_documents (
                        surgeon_label,
                        protocol_name,
                        file_name,
                        file_mime,
                        file_bytes,
                        notes,
                        created_date
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        surgeon_value,
                        protocol_value,
                        upload_name,
                        upload_mime,
                        upload_bytes,
                        notes_value,
                        mountain_today(),
                    ),
                )
                inserted = cur.fetchone()
                return inserted[0] if inserted else None

    next_id = max([item.get("id", 0) for item in st.session_state.protocol_documents], default=0) + 1
    st.session_state.protocol_documents.append(
        {
            "id": next_id,
            "surgeon_label": surgeon_value,
            "protocol_name": protocol_value,
            "file_name": upload_name,
            "file_mime": upload_mime,
            "file_bytes": upload_bytes,
            "notes": notes_value,
            "created_date": mountain_today(),
        }
    )
    return next_id


def update_protocol_document(doc_id, surgeon_label, protocol_name, notes="", upload_name=None, upload_mime=None, upload_bytes=None):
    surgeon_value = (surgeon_label or "").strip() or "Dr. Braden Boyer (BB)"
    protocol_value = (protocol_name or "").strip()
    notes_value = (notes or "").strip()

    if db_enabled():
        set_parts = [
            "surgeon_label = %s",
            "protocol_name = %s",
            "notes = %s",
        ]
        values = [surgeon_value, protocol_value, notes_value]
        if upload_bytes:
            set_parts.extend(["file_name = %s", "file_mime = %s", "file_bytes = %s"])
            values.extend([upload_name, upload_mime, upload_bytes])
        values.append(doc_id)
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE protocol_documents SET {', '.join(set_parts)} WHERE id = %s", tuple(values))
        return

    for item in st.session_state.protocol_documents:
        if item.get("id") == doc_id:
            item["surgeon_label"] = surgeon_value
            item["protocol_name"] = protocol_value
            item["notes"] = notes_value
            if upload_bytes:
                item["file_name"] = upload_name
                item["file_mime"] = upload_mime
                item["file_bytes"] = upload_bytes
            return


def load_case_protocol_links():
    if not db_enabled():
        return st.session_state.case_protocol_links
    try:
        with get_connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT case_id, protocol_id
                    FROM surgical_case_protocol_links
                    ORDER BY protocol_id, case_id
                    """
                )
                return cur.fetchall()
    except psycopg.Error:
        return st.session_state.case_protocol_links


def set_protocol_case_links(protocol_id, case_ids):
    normalized_case_ids = sorted({int(item) for item in (case_ids or []) if item is not None})

    if db_enabled():
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM surgical_case_protocol_links WHERE protocol_id = %s", (protocol_id,))
                for case_id in normalized_case_ids:
                    cur.execute(
                        """
                        INSERT INTO surgical_case_protocol_links (case_id, protocol_id, created_date)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (case_id, protocol_id) DO NOTHING
                        """,
                        (case_id, protocol_id, mountain_today()),
                    )
        return

    st.session_state.case_protocol_links = [
        item
        for item in st.session_state.case_protocol_links
        if item.get("protocol_id") != protocol_id
    ]
    st.session_state.case_protocol_links.extend(
        [
            {"case_id": case_id, "protocol_id": protocol_id}
            for case_id in normalized_case_ids
        ]
    )


def delete_protocol_document(doc_id):
    if db_enabled():
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM protocol_documents WHERE id = %s", (doc_id,))
        return
    st.session_state.protocol_documents = [item for item in st.session_state.protocol_documents if item.get("id") != doc_id]
    st.session_state.case_protocol_links = [item for item in st.session_state.case_protocol_links if item.get("protocol_id") != doc_id]


def load_anatomy_xray_images():
    if not db_enabled():
        return st.session_state.anatomy_xray_images
    try:
        with get_connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        body_part,
                        fracture_type,
                        view_label,
                        image_name,
                        image_mime,
                        image_bytes,
                        notes,
                        created_date
                    FROM anatomy_xray_images
                    ORDER BY created_date DESC, id DESC
                    """
                )
                return cur.fetchall()
    except psycopg.Error:
        return st.session_state.anatomy_xray_images


def add_anatomy_xray_image(body_part, fracture_type, view_label, image_name, image_mime, image_bytes, notes=""):
    body_part_value = str(body_part or "").strip()
    fracture_type_value = str(fracture_type or "").strip() or "Unspecified"
    view_label_value = str(view_label or "").strip() or "Unspecified"
    notes_value = str(notes or "").strip()

    if not body_part_value or not image_name or not image_bytes:
        return None

    if db_enabled():
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO anatomy_xray_images (
                        body_part,
                        fracture_type,
                        view_label,
                        image_name,
                        image_mime,
                        image_bytes,
                        notes,
                        created_date
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        body_part_value,
                        fracture_type_value,
                        view_label_value,
                        image_name,
                        image_mime,
                        image_bytes,
                        notes_value,
                        mountain_today(),
                    ),
                )
                inserted = cur.fetchone()
                return inserted[0] if inserted else None

    next_id = max([item.get("id", 0) for item in st.session_state.anatomy_xray_images], default=0) + 1
    st.session_state.anatomy_xray_images.append(
        {
            "id": next_id,
            "body_part": body_part_value,
            "fracture_type": fracture_type_value,
            "view_label": view_label_value,
            "image_name": image_name,
            "image_mime": image_mime,
            "image_bytes": image_bytes,
            "notes": notes_value,
            "created_date": mountain_today(),
        }
    )
    return next_id


def delete_anatomy_xray_image(image_id):
    if db_enabled():
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM anatomy_xray_images WHERE id = %s", (image_id,))
        return

    st.session_state.anatomy_xray_images = [
        item for item in st.session_state.anatomy_xray_images if item.get("id") != image_id
    ]


def load_anatomy_quiz_attempts():
    if not db_enabled():
        return list(st.session_state.anatomy_quiz_attempts)
    try:
        with get_connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        quiz_mode,
                        prompt,
                        expected_answer,
                        submitted_answer,
                        confidence,
                        is_correct,
                        explanation,
                        created_date
                    FROM anatomy_quiz_attempts
                    ORDER BY created_date DESC, id DESC
                    LIMIT 500
                    """
                )
                return cur.fetchall()
    except psycopg.Error:
        return list(st.session_state.anatomy_quiz_attempts)


def add_anatomy_quiz_attempt(
    quiz_mode,
    prompt,
    expected_answer,
    submitted_answer,
    confidence,
    is_correct,
    explanation="",
):
    quiz_mode_value = str(quiz_mode or "Unknown").strip() or "Unknown"
    prompt_value = str(prompt or "").strip()
    expected_value = str(expected_answer or "").strip()
    submitted_value = str(submitted_answer or "").strip()
    explanation_value = str(explanation or "").strip()
    confidence_value = None
    try:
        if confidence is not None:
            confidence_value = int(confidence)
    except (TypeError, ValueError):
        confidence_value = None

    if db_enabled():
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO anatomy_quiz_attempts (
                        quiz_mode,
                        prompt,
                        expected_answer,
                        submitted_answer,
                        confidence,
                        is_correct,
                        explanation,
                        created_date
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        quiz_mode_value,
                        prompt_value,
                        expected_value,
                        submitted_value,
                        confidence_value,
                        bool(is_correct),
                        explanation_value,
                        mountain_today(),
                    ),
                )
        return

    next_id = max([item.get("id", 0) for item in st.session_state.anatomy_quiz_attempts], default=0) + 1
    st.session_state.anatomy_quiz_attempts.append(
        {
            "id": next_id,
            "quiz_mode": quiz_mode_value,
            "prompt": prompt_value,
            "expected_answer": expected_value,
            "submitted_answer": submitted_value,
            "confidence": confidence_value,
            "is_correct": bool(is_correct),
            "explanation": explanation_value,
            "created_date": mountain_today(),
        }
    )


def load_anatomy_quiz_review_queue():
    if not db_enabled():
        return list(st.session_state.anatomy_quiz_review_queue)
    try:
        with get_connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT id, review_text, created_date
                    FROM anatomy_quiz_review_queue
                    ORDER BY created_date DESC, id DESC
                    LIMIT 500
                    """
                )
                return cur.fetchall()
    except psycopg.Error:
        return list(st.session_state.anatomy_quiz_review_queue)


def add_anatomy_quiz_review_item(review_text):
    review_value = str(review_text or "").strip()
    if not review_value:
        return

    if db_enabled():
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO anatomy_quiz_review_queue (review_text, created_date)
                    VALUES (%s, %s)
                    """,
                    (review_value, mountain_today()),
                )
        return

    next_id = max([item.get("id", 0) for item in st.session_state.anatomy_quiz_review_queue], default=0) + 1
    st.session_state.anatomy_quiz_review_queue.append(
        {
            "id": next_id,
            "review_text": review_value,
            "created_date": mountain_today(),
        }
    )


def clear_anatomy_quiz_review_queue():
    if db_enabled():
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM anatomy_quiz_review_queue")
        return

    st.session_state.anatomy_quiz_review_queue = []


def text_keywords(value):
    cleaned = re.sub(r"[^a-z0-9\s]", " ", (value or "").lower())
    tokens = [item for item in cleaned.split() if len(item) > 2]
    stop_words = {
        "and",
        "the",
        "for",
        "with",
        "from",
        "into",
        "procedure",
        "protocol",
        "case",
        "notes",
        "day",
        "dr",
        "bb",
    }
    return [item for item in tokens if item not in stop_words]


def anatomy_related_resources(topic_name, topic_terms, surgical_cases, protocol_documents, max_items=4):
    topic_set = set(text_keywords(" ".join(topic_terms)))

    case_ranked = []
    for item in surgical_cases:
        case_text = " ".join(
            [
                str(item.get("procedure_name") or ""),
                str(item.get("anatomical_location") or ""),
                str(item.get("education_notes") or ""),
                str(item.get("notes") or ""),
            ]
        )
        case_terms = set(text_keywords(case_text))
        overlap = sorted(list(case_terms.intersection(topic_set)))
        if overlap:
            case_ranked.append((len(overlap), overlap[:6], item))
    case_ranked.sort(key=lambda item: item[0], reverse=True)

    protocol_ranked = []
    for doc in protocol_documents:
        doc_text = " ".join(
            [
                str(doc.get("protocol_name") or ""),
                str(doc.get("file_name") or ""),
                str(doc.get("notes") or ""),
            ]
        )
        doc_terms = set(text_keywords(doc_text))
        overlap = sorted(list(doc_terms.intersection(topic_set)))
        if overlap:
            protocol_ranked.append((len(overlap), overlap[:6], doc))
    protocol_ranked.sort(key=lambda item: item[0], reverse=True)

    return case_ranked[:max_items], protocol_ranked[:max_items]


def render_anatomy_related_widget(topic_name, topic_terms, surgical_cases, protocol_documents, panel_key):
    case_matches, protocol_matches = anatomy_related_resources(topic_name, topic_terms, surgical_cases, protocol_documents, max_items=4)
    st.markdown(f"#### Related {topic_name} Cases & Protocols")
    if not case_matches and not protocol_matches:
        st.markdown('<div class="empty-state">No related cases or protocols found yet. Add case anatomy terms and protocol notes to improve matching.</div>', unsafe_allow_html=True)
        return

    if case_matches:
        st.markdown("**Cases**")
        for score, overlap_terms, item in case_matches:
            case_date_value = item.get("case_date")
            case_date_label = case_date_value.strftime("%b %d, %Y") if hasattr(case_date_value, "strftime") else str(case_date_value)
            st.markdown(
                f"- **{item.get('procedure_name')}** · {item.get('case_stream')} · {case_date_label} · match {score} ({', '.join(overlap_terms)})",
                unsafe_allow_html=True,
            )

    if protocol_matches:
        st.markdown("**Protocols**")
        for score, overlap_terms, doc in protocol_matches:
            doc_id = doc.get("id")
            doc_bytes = doc.get("file_bytes")
            if isinstance(doc_bytes, memoryview):
                doc_bytes = bytes(doc_bytes)
            st.markdown(
                f"- **{doc.get('protocol_name')}** · match {score} ({', '.join(overlap_terms)})",
                unsafe_allow_html=True,
            )
            if doc_bytes:
                with st.expander(f"View {doc.get('file_name') or 'protocol.pdf'}", expanded=False):
                    looks_like_pdf = (str(doc.get("file_mime") or "").lower() == "application/pdf") or str(doc.get("file_name") or "").lower().endswith(".pdf")
                    start_page = 1
                    if looks_like_pdf:
                        start_page = int(
                            st.number_input(
                                "Start page",
                                min_value=1,
                                value=1,
                                step=1,
                                key=f"{panel_key}_anatomy_protocol_start_page_{doc_id}_{topic_name.lower().replace(' ', '_')}",
                            )
                        )
                    page_sections._render_protocol_pdf_preview(
                        st,
                        file_bytes=doc_bytes,
                        file_mime=doc.get("file_mime"),
                        file_name=doc.get("file_name") or "protocol.pdf",
                        height=420,
                        start_page=start_page,
                    )


ref_render_anatomy_related_widget = render_anatomy_related_widget


def weekday_name_to_index(name):
    mapping = {
        "Monday": 0,
        "Tuesday": 1,
        "Wednesday": 2,
        "Thursday": 3,
        "Friday": 4,
        "Saturday": 5,
        "Sunday": 6,
    }
    return mapping.get(name, 4)


def default_calendar_weekday_assignments():
    return {
        "Monday": ["BB clinic"],
        "Tuesday": ["Office day"],
        "Wednesday": ["WFH personal catch-up"],
        "Thursday": ["BB clinic"],
        "Friday": ["Dr. Rozek TenJet"],
    }


def normalize_calendar_labels(value):
    if isinstance(value, list):
        labels = [str(item).strip() for item in value if str(item).strip()]
    elif isinstance(value, str):
        labels = [item.strip() for item in value.split(",") if item.strip()]
    else:
        labels = []

    seen = set()
    deduped = []
    for label in labels:
        key = label.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(label)
    return deduped


def normalize_calendar_weekday_assignments(raw_assignments):
    defaults = default_calendar_weekday_assignments()
    normalized = {}
    for day, fallback_labels in defaults.items():
        labels = []
        if isinstance(raw_assignments, dict):
            labels = normalize_calendar_labels(raw_assignments.get(day))
        normalized[day] = labels or list(fallback_labels)
    return normalized


def normalize_calendar_date_overrides(raw_overrides):
    if not isinstance(raw_overrides, dict):
        return {}
    normalized = {}
    for raw_day, raw_labels in raw_overrides.items():
        try:
            parsed_day = date.fromisoformat(str(raw_day))
        except ValueError:
            continue
        labels = normalize_calendar_labels(raw_labels)
        if labels:
            normalized[parsed_day.isoformat()] = labels
    return dict(sorted(normalized.items()))


def calendar_badge_palette(label):
    lower_label = str(label).lower()
    if "office" in lower_label or "paperwork" in lower_label:
        return "rgba(12, 74, 110, 0.75)", "#bae6fd"
    if "wfh" in lower_label or "home" in lower_label or "personal" in lower_label:
        return "rgba(91, 33, 182, 0.75)", "#e9d5ff"
    if "rozek" in lower_label or "tenjet" in lower_label:
        return "rgba(124, 45, 18, 0.74)", "#fed7aa"
    if "bb" in lower_label or "boyer" in lower_label or "clinic" in lower_label:
        return "rgba(20, 83, 45, 0.75)", "#bbf7d0"
    return "rgba(51, 65, 85, 0.8)", "#e2e8f0"


def or_cadence_label_for_day(day, app_settings):
    fixed_weekday = weekday_name_to_index(app_settings.get("or_fixed_weekday", "Friday"))
    alternating_days = app_settings.get("or_alternating_days") or ["Monday", "Wednesday"]
    if len(alternating_days) < 2:
        alternating_days = ["Monday", "Wednesday"]
    alt_day_a = weekday_name_to_index(alternating_days[0])
    alt_day_b = weekday_name_to_index(alternating_days[1])
    cycle_offset = safe_int(app_settings.get("or_alternating_cycle_offset", 0), 0)

    weekday = day.weekday()
    if weekday == fixed_weekday:
        return f"OR {day.strftime('%a')}"

    alternating_weekday = alt_day_a if ((day.isocalendar().week + cycle_offset) % 2 == 0) else alt_day_b
    if weekday == alternating_weekday:
        return f"Alt OR {day.strftime('%a')}"

    return None


def predicted_or_days(app_settings, horizon_days=28):
    out = []
    for offset in range(horizon_days):
        day = mountain_today() + timedelta(days=offset)
        label = or_cadence_label_for_day(day, app_settings)
        if label:
            out.append((day, label))
    return out


def render_or_calendar_compact(surgical_cases, predicted_labels, month_anchor, panel_key):
    cal = calendar.Calendar(firstweekday=0)
    weeks = cal.monthdatescalendar(month_anchor.year, month_anchor.month)
    cases_by_day = {}
    for item in surgical_cases:
        case_day = item.get("case_date")
        if case_day:
            cases_by_day.setdefault(case_day, []).append(item)

    headers = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    table_lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]

    for week in weeks:
        cells = []
        for day in week:
            if day.month != month_anchor.month:
                cells.append(" ")
                continue

            day_cases = cases_by_day.get(day, [])
            completed_count = len([item for item in day_cases if item.get("status") == "completed"])
            main_or_count = len([item for item in day_cases if item.get("case_stream") == "Main OR"])
            tenjet_count = len([item for item in day_cases if item.get("case_stream") == "TenJet"])

            parts = [f"**{day.day}**"]
            if predicted_labels.get(day):
                parts.append("OR")
            if day_cases:
                parts.append(f"{len(day_cases)} case(s)")
                parts.append(f"M{main_or_count}/T{tenjet_count}")
            if completed_count:
                parts.append(f"{completed_count} done")

            cells.append("<br>".join(parts))

        table_lines.append("| " + " | ".join(cells) + " |")

    st.markdown("\n".join(table_lines), unsafe_allow_html=True)


def render_task_calendar_compact(tasks, month_anchor, app_settings=None):
    cal = calendar.Calendar(firstweekday=0)
    weeks = cal.monthdatescalendar(month_anchor.year, month_anchor.month)

    due_by_day = {}
    scheduled_by_day = {}
    completed_by_day = {}
    for item in tasks:
        due_day = item.get("due_date")
        completed_day = item.get("completed_date")
        if due_day:
            due_by_day[due_day] = due_by_day.get(due_day, 0) + 1
        for scheduled_day in scheduled_date_range(item):
            scheduled_by_day[scheduled_day] = scheduled_by_day.get(scheduled_day, 0) + 1
        if completed_day:
            completed_by_day[completed_day] = completed_by_day.get(completed_day, 0) + 1

    settings = app_settings or DEFAULT_APP_SETTINGS
    weekday_assignments = normalize_calendar_weekday_assignments(settings.get("calendar_weekday_assignments"))
    date_overrides = normalize_calendar_date_overrides(settings.get("calendar_date_overrides"))
    procedure_friday_frequency = max(1, int(settings.get("overview_procedure_friday_frequency_weeks", 2) or 2))
    procedure_friday_cycle_offset = int(settings.get("overview_procedure_friday_cycle_offset", 0) or 0)

    headers = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    table_lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]

    for week in weeks:
        cells = []
        for day in week:
            if day.month != month_anchor.month:
                cells.append(" ")
                continue

            due_count = due_by_day.get(day, 0)
            scheduled_count = scheduled_by_day.get(day, 0)
            completed_count = completed_by_day.get(day, 0)
            load_score = due_count + scheduled_count
            if load_score >= 6:
                day_bg = "rgba(127, 29, 29, 0.55)"
            elif load_score >= 4:
                day_bg = "rgba(120, 53, 15, 0.52)"
            elif load_score >= 2:
                day_bg = "rgba(30, 64, 175, 0.5)"
            else:
                day_bg = "rgba(51, 65, 85, 0.52)"

            parts = [
                f"<span style='display:inline-block; font-weight:700; padding:0.1rem 0.4rem; border-radius:999px; background:{day_bg};'>{day.day}</span>"
            ]
            badges = []
            custom_labels = date_overrides.get(day.isoformat())
            if custom_labels is None:
                custom_labels = weekday_assignments.get(day.strftime("%A"), [])
            for label in custom_labels:
                bg, fg = calendar_badge_palette(label)
                badges.append((label, bg, fg))
            or_label = or_cadence_label_for_day(day, settings)
            if or_label:
                badges.append((or_label, "rgba(49, 46, 129, 0.75)", "#c7d2fe"))
            if day.weekday() == 4 and ((day.isocalendar().week + procedure_friday_cycle_offset) % procedure_friday_frequency == 0):
                badges.append(("Procedure Friday", "rgba(124, 45, 18, 0.74)", "#fed7aa"))

            for label, bg, fg in badges:
                parts.append(
                    f"<span style='display:inline-block; padding:0.05rem 0.35rem; border-radius:999px; background:{bg}; color:{fg}; font-size:0.76rem;'>{label}</span>"
                )
            day_spans = [item for item in tasks if scheduled_span_position(item, day) and scheduled_span_position(item, day) != "single"]
            for item in day_spans[:2]:
                span_label = item.get("title") if scheduled_span_position(item, day) != "start" else f"{item['title']}"
                parts.append(render_span_block(item, day, label_text=span_label, compact=True))
            if scheduled_count:
                sched_bg = "rgba(30, 64, 175, 0.68)" if scheduled_count < 3 else "rgba(59, 130, 246, 0.68)"
                parts.append(f"<span style='display:inline-block; padding:0.05rem 0.35rem; border-radius:999px; background:{sched_bg}; color:#dbeafe; font-size:0.76rem;'>S{scheduled_count}</span>")
            if due_count:
                due_bg = "rgba(127, 29, 29, 0.7)" if due_count < 3 else "rgba(153, 27, 27, 0.74)"
                parts.append(f"<span style='display:inline-block; padding:0.05rem 0.35rem; border-radius:999px; background:{due_bg}; color:#fecaca; font-size:0.76rem;'>D{due_count}</span>")
            if completed_count:
                done_bg = "rgba(20, 83, 45, 0.68)" if completed_count < 3 else "rgba(22, 101, 52, 0.7)"
                parts.append(f"<span style='display:inline-block; padding:0.05rem 0.35rem; border-radius:999px; background:{done_bg}; color:#bbf7d0; font-size:0.76rem;'>C{completed_count}</span>")

            cells.append("<br>".join(parts))

        table_lines.append("| " + " | ".join(cells) + " |")

    st.markdown("\n".join(table_lines), unsafe_allow_html=True)


def render_task_calendar_panel(tasks, panel_key, title, subtitle, app_settings=None):
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown(f'<div class="panel-title"><h3>{title}</h3><span>{subtitle}</span></div>', unsafe_allow_html=True)
    st.markdown(
        """
        <div style='display:flex; flex-wrap:wrap; gap:0.5rem; align-items:center; margin:0.2rem 0 0.8rem;'>
            <span style='display:inline-flex; align-items:center; gap:0.35rem; font-size:0.8rem;'><span style='width:0.8rem; height:0.8rem; border-radius:999px; background:rgba(30,64,175,0.68); display:inline-block;'></span>Scheduled</span>
            <span style='display:inline-flex; align-items:center; gap:0.35rem; font-size:0.8rem;'><span style='width:0.8rem; height:0.8rem; border-radius:4px; background:linear-gradient(90deg,rgba(120,53,15,0.7),rgba(146,64,14,0.68)); border:1px solid #f59e0b; display:inline-block;'></span>Vacation / personal range</span>
            <span style='display:inline-flex; align-items:center; gap:0.35rem; font-size:0.8rem;'><span style='width:0.8rem; height:0.8rem; border-radius:4px; background:linear-gradient(90deg,rgba(15,118,110,0.66),rgba(13,148,136,0.64)); border:1px solid #10b981; display:inline-block;'></span>Clinic multi-day</span>
            <span style='display:inline-flex; align-items:center; gap:0.35rem; font-size:0.8rem;'><span style='width:0.8rem; height:0.8rem; border-radius:999px; background:rgba(127,29,29,0.7); display:inline-block;'></span>Due</span>
            <span style='display:inline-flex; align-items:center; gap:0.35rem; font-size:0.8rem;'><span style='width:0.8rem; height:0.8rem; border-radius:999px; background:rgba(20,83,45,0.68); display:inline-block;'></span>Completed</span>
            <span style='display:inline-flex; align-items:center; gap:0.35rem; font-size:0.8rem;'><span style='width:0.8rem; height:0.8rem; border-radius:999px; background:rgba(20,83,45,0.75); display:inline-block;'></span>Clinic</span>
            <span style='display:inline-flex; align-items:center; gap:0.35rem; font-size:0.8rem;'><span style='width:0.8rem; height:0.8rem; border-radius:999px; background:rgba(49,46,129,0.75); display:inline-block;'></span>OR</span>
            <span style='display:inline-flex; align-items:center; gap:0.35rem; font-size:0.8rem;'><span style='width:0.8rem; height:0.8rem; border-radius:999px; background:rgba(124,45,18,0.74); display:inline-block;'></span>Procedure Friday</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    month_key = f"{panel_key}_month_anchor"
    if month_key not in st.session_state:
        st.session_state[month_key] = mountain_today().replace(day=1)

    controls = st.columns([1, 2, 1])
    with controls[0]:
        if st.button("Prev month", key=f"{panel_key}_prev"):
            current = st.session_state[month_key]
            previous_month_end = current - timedelta(days=1)
            st.session_state[month_key] = previous_month_end.replace(day=1)
            st.rerun()
    with controls[1]:
        anchor = st.session_state[month_key]
        st.markdown(
            f"<div style='text-align:center; font-weight:700; margin-top:0.4rem;'>{calendar.month_name[anchor.month]} {anchor.year}</div>",
            unsafe_allow_html=True,
        )
    with controls[2]:
        if st.button("Next month", key=f"{panel_key}_next"):
            current = st.session_state[month_key]
            next_month_start = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
            st.session_state[month_key] = next_month_start
            st.rerun()

    render_task_calendar_compact(tasks, st.session_state[month_key], app_settings)
    st.markdown('</div>', unsafe_allow_html=True)


def db_health_status():
    if not database_url_candidates():
        return "missing", "No database URL configured."
    if DB_ERROR:
        return "error", "Database setup failed; running in fallback mode."
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return "ok", "Database reachable."
    except psycopg.Error:
        return "error", "Database unreachable right now."


def load_app_settings():
    if db_enabled():
        try:
            with get_connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute("SELECT payload FROM app_settings WHERE id = 1")
                    row = cur.fetchone()
                    if row and row.get("payload"):
                        payload = json.loads(row["payload"])
                        merged = dict(DEFAULT_APP_SETTINGS)
                        merged.update(payload)
                        if merged.get("overview_site_label") == "Outpatient hospital":
                            merged["overview_site_label"] = "MOA (Mercy Orthopedic Associates)"
                        return merged
        except (psycopg.Error, json.JSONDecodeError):
            pass

    stored = st.session_state.get("app_settings")
    if isinstance(stored, dict):
        merged = dict(DEFAULT_APP_SETTINGS)
        merged.update(stored)
        if merged.get("overview_site_label") == "Outpatient hospital":
            merged["overview_site_label"] = "MOA (Mercy Orthopedic Associates)"
        return merged
    return dict(DEFAULT_APP_SETTINGS)


def save_app_settings(settings):
    merged = dict(DEFAULT_APP_SETTINGS)
    merged.update(settings)

    if db_enabled():
        try:
            payload_text = dumps_json_safe(merged)
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO app_settings (id, payload, updated_at)
                        VALUES (1, %s, NOW())
                        ON CONFLICT (id)
                        DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW()
                        """,
                        (payload_text,),
                    )
        except psycopg.Error:
            pass

    st.session_state["app_settings"] = merged
    return merged


def seed_sample_tasks():
    sample_data = [
        {
            "title": "Prep tomorrow clinic huddle",
            "description": "Review patient list and note high-priority follow-ups.",
            "category": "Clinic",
            "priority": "high",
            "due_date": mountain_today(),
            "scheduled_date": mountain_today(),
            "scheduled_time": time(8, 30),
            "scheduled_minutes": 30,
        },
        {
            "title": "Personal finance check-in",
            "description": "Quick budget review and upcoming bill check.",
            "category": "Personal",
            "priority": "medium",
            "due_date": mountain_today(),
            "scheduled_date": mountain_today(),
            "scheduled_time": time(19, 0),
            "scheduled_minutes": 45,
        },
        {
            "title": "Inbox zero sprint",
            "description": "Process starred messages and archive the rest.",
            "category": "Personal",
            "priority": "low",
            "due_date": mountain_today(),
            "scheduled_date": mountain_today(),
            "scheduled_time": time(16, 0),
            "scheduled_minutes": 30,
        },
    ]

    for item in sample_data:
        add_task(
            item["title"],
            item["description"],
            item["category"],
            item["priority"],
            item["due_date"],
            scheduled_date=item["scheduled_date"],
            scheduled_time=item["scheduled_time"],
            scheduled_minutes=item["scheduled_minutes"],
        )


def inject_styles():
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&family=DM+Sans:wght@400;500;700&display=swap');

        :root {
            --bg: #04060c;
            --surface: rgba(9, 14, 24, 0.86);
            --ink: #e6eefb;
            --muted: #8ea3c2;
            --line: rgba(100, 116, 139, 0.28);
            --shadow: 0 20px 60px rgba(2, 6, 23, 0.68);
            --radius: 22px;
        }

        .stApp {
            background:
                radial-gradient(circle at top left, rgba(20, 184, 166, 0.12), transparent 30%),
                radial-gradient(circle at top right, rgba(59, 130, 246, 0.12), transparent 26%),
                linear-gradient(180deg, #060a14 0%, var(--bg) 44%, #02040a 100%);
            color: var(--ink);
            font-family: 'DM Sans', sans-serif;
        }

        p, li, label, .stMarkdown, .stCaption, .stText, [data-testid="stMarkdownContainer"] {
            color: var(--ink);
        }

        h1, h2, h3, h4, .stMarkdown strong {
            font-family: 'Space Grotesk', sans-serif;
            color: #f3f7ff;
            letter-spacing: -0.03em;
        }

        .stMain [data-baseweb="input"] > div,
        .stMain [data-baseweb="textarea"] > div,
        .stMain [data-baseweb="select"] > div,
        .stMain [data-baseweb="tag"] {
            background: rgba(15, 23, 42, 0.72) !important;
            border: 1px solid rgba(148, 163, 184, 0.34) !important;
            color: var(--ink) !important;
        }

        .stMain [data-baseweb="input"] input,
        .stMain [data-baseweb="textarea"] textarea,
        .stMain [data-baseweb="select"] input,
        .stMain [data-baseweb="select"] span,
        .stMain [data-baseweb="tag"] span {
            color: var(--ink) !important;
        }

        .stMain [data-baseweb="input"] input::placeholder,
        .stMain [data-baseweb="textarea"] textarea::placeholder {
            color: rgba(203, 213, 225, 0.68) !important;
            opacity: 1 !important;
        }

        section[data-testid="stSidebar"] {
            background:
                radial-gradient(circle at 16% 8%, rgba(56, 189, 248, 0.14), transparent 22%),
                radial-gradient(circle at 88% 18%, rgba(245, 158, 11, 0.12), transparent 24%),
                linear-gradient(180deg, rgba(8, 16, 28, 0.98), rgba(8, 13, 24, 0.97));
            border-right: 1px solid rgba(255, 255, 255, 0.14);
            box-shadow: inset -1px 0 0 rgba(255, 255, 255, 0.06);
        }

        section[data-testid="stSidebar"] * {
            color: #f8fafc;
        }

        section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
        section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] li,
        section[data-testid="stSidebar"] label,
        section[data-testid="stSidebar"] .stCaption,
        section[data-testid="stSidebar"] .stText,
        section[data-testid="stSidebar"] .stMarkdown {
            color: #f8fafc !important;
            opacity: 1 !important;
        }

        section[data-testid="stSidebar"] [data-baseweb="input"] input,
        section[data-testid="stSidebar"] [data-baseweb="textarea"] textarea,
        section[data-testid="stSidebar"] [data-baseweb="select"] input,
        section[data-testid="stSidebar"] [data-baseweb="select"] span,
        section[data-testid="stSidebar"] [data-baseweb="tag"] span {
            color: #f8fafc !important;
            opacity: 1 !important;
        }

        section[data-testid="stSidebar"] [data-baseweb="input"] input::placeholder,
        section[data-testid="stSidebar"] [data-baseweb="textarea"] textarea::placeholder {
            color: rgba(248, 250, 252, 0.75) !important;
            opacity: 1 !important;
        }

        section[data-testid="stSidebar"] [data-baseweb="input"] > div,
        section[data-testid="stSidebar"] [data-baseweb="textarea"] > div,
        section[data-testid="stSidebar"] [data-baseweb="select"] > div {
            background: rgba(255, 255, 255, 0.08) !important;
            border: 1px solid rgba(255, 255, 255, 0.18) !important;
            border-radius: 12px !important;
        }

        section[data-testid="stSidebar"] [data-baseweb="radio"] label,
        section[data-testid="stSidebar"] [data-baseweb="radio"] div {
            color: #f8fafc !important;
            opacity: 1 !important;
        }

        section[data-testid="stSidebar"] .stButton > button {
            background: linear-gradient(135deg, rgba(20, 184, 166, 0.9), rgba(37, 99, 235, 0.86));
            border: 1px solid rgba(255, 255, 255, 0.2);
            color: #ffffff !important;
            font-weight: 700;
            border-radius: 12px;
        }

        section[data-testid="stSidebar"] .stButton > button:hover {
            border-color: rgba(255, 255, 255, 0.35);
            filter: brightness(1.03);
        }

        /* ── Main content buttons ── */
        .stMainBlockContainer .stButton > button,
        .stMain .stButton > button,
        [data-testid="stAppViewBlockContainer"] .stButton > button {
            background: linear-gradient(135deg, #0b5f5f, #1e40af);
            color: #ffffff !important;
            font-weight: 600;
            border: none;
            border-radius: 12px;
            box-shadow: 0 2px 8px rgba(15, 23, 42, 0.48);
        }

        .stMainBlockContainer .stButton > button:hover,
        .stMain .stButton > button:hover,
        [data-testid="stAppViewBlockContainer"] .stButton > button:hover {
            filter: brightness(1.05);
            box-shadow: 0 4px 16px rgba(15, 23, 42, 0.58);
        }

        .stMainBlockContainer .stButton > button:active,
        .stMain .stButton > button:active,
        [data-testid="stAppViewBlockContainer"] .stButton > button:active {
            filter: brightness(0.97);
        }

        .block-container {
            padding-top: 2rem;
            padding-bottom: 2rem;
            max-width: 1180px;
        }

        .hero {
            position: relative;
            overflow: hidden;
            padding: 2rem;
            margin-bottom: 1.5rem;
            border-radius: 28px;
            background:
                radial-gradient(circle at top right, rgba(148, 163, 184, 0.24), transparent 30%),
                linear-gradient(135deg, #082f49 0%, #0f172a 52%, #1f2937 100%);
            color: white;
            box-shadow: 0 28px 80px rgba(2, 6, 23, 0.72);
            border: 1px solid rgba(100, 116, 139, 0.28);
        }

        .hero h1 {
            color: white;
            font-size: 3rem;
            margin-bottom: 0.2rem;
        }

        .hero p {
            max-width: 700px;
            font-size: 1.02rem;
            opacity: 0.95;
            margin-bottom: 0;
        }

        .panel {
            background: var(--surface);
            border: 1px solid var(--line);
            border-radius: var(--radius);
            box-shadow: var(--shadow);
            backdrop-filter: blur(14px);
            padding: 1.2rem;
        }

        .panel-title {
            display: flex;
            justify-content: space-between;
            align-items: baseline;
            gap: 1rem;
            margin-bottom: 0.9rem;
        }

        .panel-title h3 {
            margin: 0;
            font-size: 1.15rem;
        }

        .panel-title span, .section-lead {
            color: var(--muted);
        }

        .task-card {
            background: rgba(15, 23, 42, 0.78);
            border: 1px solid rgba(148, 163, 184, 0.2);
            border-radius: 18px;
            padding: 1rem;
            margin-bottom: 0.9rem;
            box-shadow: var(--shadow);
        }

        .task-title {
            font-family: 'Space Grotesk', sans-serif;
            font-size: 1.05rem;
            font-weight: 700;
            color: #ecf3ff;
        }

        .task-meta {
            display: flex;
            flex-wrap: wrap;
            gap: 0.45rem;
            margin-top: 0.7rem;
        }

        .pill {
            display: inline-flex;
            align-items: center;
            gap: 0.35rem;
            padding: 0.28rem 0.7rem;
            border-radius: 999px;
            font-size: 0.78rem;
            font-weight: 700;
            letter-spacing: 0.01em;
        }

        .pill-priority-high { color: #fecaca; background: rgba(127, 29, 29, 0.4); }
        .pill-priority-medium { color: #fed7aa; background: rgba(124, 45, 18, 0.4); }
        .pill-priority-low { color: #bbf7d0; background: rgba(20, 83, 45, 0.42); }
        .pill-category { color: #dbeafe; background: #1e293b; }
        .pill-status { color: #99f6e4; background: rgba(17, 94, 89, 0.4); }
        .pill-status-todo { color: #bfdbfe; background: rgba(30, 58, 138, 0.38); }
        .pill-status-in_progress { color: #fde68a; background: rgba(120, 53, 15, 0.42); }
        .pill-status-blocked { color: #fecaca; background: rgba(127, 29, 29, 0.4); }
        .pill-status-completed { color: #bbf7d0; background: rgba(20, 83, 45, 0.42); }

        .empty-state {
            border: 1px dashed rgba(148, 163, 184, 0.4);
            background: rgba(15, 23, 42, 0.45);
            border-radius: 18px;
            padding: 1rem;
            color: var(--muted);
            text-align: center;
        }

        .ai-shell {
            display: grid;
            gap: 1rem;
        }

        .ai-hero {
            background:
                radial-gradient(circle at top right, rgba(148, 163, 184, 0.2), transparent 24%),
                linear-gradient(135deg, rgba(8, 47, 73, 0.98), rgba(15, 23, 42, 0.98));
            color: white;
            border: 1px solid rgba(100, 116, 139, 0.3);
        }

        .ai-hero .panel-title h3,
        .ai-hero .panel-title span,
        .ai-hero p,
        .ai-hero li,
        .ai-hero label {
            color: white !important;
        }

        .ai-stat-card {
            border-radius: 18px;
            padding: 0.95rem 1rem;
            background: rgba(15, 23, 42, 0.4);
            border: 1px solid rgba(148, 163, 184, 0.3);
            box-shadow: inset 0 1px 0 rgba(148, 163, 184, 0.16);
        }

        .ai-stat-label {
            font-size: 0.78rem;
            text-transform: uppercase;
            letter-spacing: 0.12em;
            opacity: 0.84;
        }

        .ai-stat-value {
            font-family: 'Space Grotesk', sans-serif;
            font-size: 1.55rem;
            font-weight: 700;
            margin-top: 0.2rem;
        }

        .ai-stat-note {
            margin-top: 0.25rem;
            font-size: 0.86rem;
            opacity: 0.9;
        }

        .ai-command {
            background: rgba(15, 23, 42, 0.78);
            border: 1px solid rgba(148, 163, 184, 0.22);
        }

        .ai-chip-grid {
            display: flex;
            flex-wrap: wrap;
            gap: 0.45rem;
            margin: 0.3rem 0 0.8rem;
        }

        .ai-chip {
            display: inline-flex;
            align-items: center;
            border-radius: 999px;
            padding: 0.35rem 0.7rem;
            background: rgba(51, 65, 85, 0.5);
            border: 1px solid rgba(148, 163, 184, 0.24);
            color: #dbe7fb;
            font-size: 0.8rem;
            font-weight: 600;
        }

        .ai-response-card {
            background: rgba(15, 23, 42, 0.82);
            border: 1px solid rgba(148, 163, 184, 0.24);
        }

        .ai-list {
            list-style: none;
            margin: 0.55rem 0 0;
            padding: 0;
        }

        .ai-list li {
            margin-bottom: 0.55rem;
            padding-left: 0.9rem;
            position: relative;
        }

        .ai-list li::before {
            content: '';
            position: absolute;
            left: 0;
            top: 0.55rem;
            width: 0.45rem;
            height: 0.45rem;
            border-radius: 999px;
            background: linear-gradient(135deg, #0f766e, #155eef);
        }

        .page-banner {
            border-radius: 22px;
            padding: 1rem 1.15rem;
            margin: 0 0 1rem;
            border: 1px solid rgba(148, 163, 184, 0.24);
            background: rgba(15, 23, 42, 0.72);
            box-shadow: 0 18px 40px rgba(2, 6, 23, 0.45);
        }

        .page-banner h2 {
            margin: 0;
            font-size: 1.12rem;
        }

        .page-banner p {
            margin: 0.35rem 0 0;
            color: var(--muted);
        }

        .page-banner-overview { border-left: 5px solid #0f766e; }
        .page-banner-personal { border-left: 5px solid #2563eb; }
        .page-banner-clinic { border-left: 5px solid #7c3aed; }
        .page-banner-schedule { border-left: 5px solid #d97706; }
        .page-banner-ai { border-left: 5px solid #155eef; }
        .page-banner-analytics { border-left: 5px solid #0f172a; }
        .page-banner-notifications { border-left: 5px solid #dc2626; }
        .page-banner-review { border-left: 5px solid #db2777; }
        .page-banner-settings { border-left: 5px solid #475569; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_hero():
    st.markdown(
        """
        <div class="hero">
            <h1>DayAnchor</h1>
            <p>A focused task board for personal and clinic work, with optional AI planning and Postgres persistence.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_page_banner(page_key, title, subtitle):
    st.markdown(
        f"""
        <div class="page-banner page-banner-{page_key}">
            <h2>{title}</h2>
            <p>{subtitle}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_span_block(task, day, label_text=None, compact=False):
    position = scheduled_span_position(task, day)
    if not position:
        return ""

    is_vacation_span = (
        task.get("scheduled_end_date")
        and task.get("scheduled_end_date") != task.get("scheduled_date")
        and task.get("category") == "Personal"
    )
    is_clinic_span = (
        task.get("scheduled_end_date")
        and task.get("scheduled_end_date") != task.get("scheduled_date")
        and task.get("category") == "Clinic"
    )
    if is_vacation_span:
        bg = "linear-gradient(90deg, rgba(120, 53, 15, 0.8), rgba(146, 64, 14, 0.78))"
        fg = "#fde68a"
        border = "#f59e0b"
    elif is_clinic_span:
        bg = "linear-gradient(90deg, rgba(15, 118, 110, 0.76), rgba(13, 148, 136, 0.74))"
        fg = "#ccfbf1"
        border = "#10b981"
    else:
        bg = "rgba(30, 41, 59, 0.92)"
        fg = "#bfdbfe"
        border = "#60a5fa"

    border_radius = {
        "single": "999px",
        "start": "999px 0 0 999px",
        "middle": "0",
        "end": "0 999px 999px 0",
    }.get(position, "999px")
    min_height = "1.45rem" if compact else "1.65rem"
    text = label_text or task["title"]
    if position == "start" or position == "single":
        if is_vacation_span:
            text = f"\u2600\ufe0f {text}"
        elif is_clinic_span:
            text = f"\U0001f3e5 {text}"
    else:
        text = ""

    return (
        f"<div style='width:100%; box-sizing:border-box; margin:0.22rem 0 0; padding:0.18rem 0.45rem; min-height:{min_height}; "
        f"border:1px solid {border}; border-radius:{border_radius}; background:{bg}; color:{fg}; "
        f"font-size:0.75rem; font-weight:700; overflow:hidden; white-space:nowrap; text-overflow:ellipsis;'>"
        f"{text}"
        f"</div>"
    )


def add_task(
    title,
    description,
    category,
    priority,
    due_date,
    scheduled_date=None,
    scheduled_end_date=None,
    scheduled_time=None,
    scheduled_minutes=None,
    recurrence_rule=None,
    recurrence_interval=1,
):
    if db_enabled():
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO tasks (
                        title,
                        description,
                        category,
                        priority,
                        status,
                        created_date,
                        due_date,
                        scheduled_date,
                        scheduled_end_date,
                        scheduled_time,
                        scheduled_minutes,
                        recurrence_rule,
                        recurrence_interval,
                        completed_date,
                        completed_at
                    ) VALUES (%s, %s, %s, %s, 'todo', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        title.strip(),
                        description.strip(),
                        category,
                        priority,
                        mountain_today(),
                        due_date,
                        scheduled_date,
                        scheduled_end_date,
                        scheduled_time,
                        scheduled_minutes,
                        recurrence_rule,
                        max(1, int(recurrence_interval or 1)),
                        None,
                        None,
                    ),
                )
        return

    st.session_state.tasks.append(
        {
            "id": len(st.session_state.tasks) + 1,
            "title": title.strip(),
            "description": description.strip(),
            "category": category,
            "priority": priority,
            "status": "todo",
            "created_date": mountain_today(),
            "due_date": due_date,
            "scheduled_date": scheduled_date,
            "scheduled_end_date": scheduled_end_date,
            "scheduled_time": scheduled_time,
            "scheduled_minutes": scheduled_minutes,
            "recurrence_rule": recurrence_rule,
            "recurrence_interval": max(1, int(recurrence_interval or 1)),
            "completed_date": None,
            "completed_at": None,
        }
    )


def update_task(task_id, **fields):
    allowed_fields = {
        "title",
        "description",
        "category",
        "priority",
        "status",
        "due_date",
        "scheduled_date",
        "scheduled_end_date",
        "scheduled_time",
        "scheduled_minutes",
        "recurrence_rule",
        "recurrence_interval",
        "completed_date",
        "completed_at",
    }
    sanitized = {key: value for key, value in fields.items() if key in allowed_fields}
    if not sanitized:
        return

    if db_enabled():
        set_parts = []
        values = []
        for key, value in sanitized.items():
            set_parts.append(f"{key} = %s")
            values.append(value)
        values.append(task_id)
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE tasks SET {', '.join(set_parts)} WHERE id = %s", tuple(values))
        return

    for task in st.session_state.tasks:
        if task["id"] == task_id:
            task.update(sanitized)
            return


def delete_task(task_id):
    if db_enabled():
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM tasks WHERE id = %s", (task_id,))
        return
    st.session_state.tasks = [task for task in st.session_state.tasks if task["id"] != task_id]


def personal_goal_week_start(reference_date=None):
    anchor = reference_date or mountain_today()
    return anchor - timedelta(days=anchor.weekday())


PERSONAL_GOAL_WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def personal_goal_normalize_reminder_days(raw_days):
    if not raw_days:
        return []
    if isinstance(raw_days, str):
        candidate = raw_days.strip()
        if not candidate:
            return []
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, list):
                raw_days = parsed
            else:
                raw_days = [candidate]
        except json.JSONDecodeError:
            raw_days = [part.strip() for part in candidate.split(",") if part.strip()]
    normalized = []
    for day in raw_days:
        if day in PERSONAL_GOAL_WEEKDAY_NAMES and day not in normalized:
            normalized.append(day)
    return normalized


def personal_goal_reminder_days_label(reminder_days):
    normalized = personal_goal_normalize_reminder_days(reminder_days)
    return ", ".join(day[:3] for day in normalized) if normalized else "No reminders"


def _enrich_personal_goals(goals, checkins):
    week_start = personal_goal_week_start()
    checkins_by_goal = {}
    for checkin in checkins:
        goal_id = checkin.get("goal_id")
        if goal_id is None:
            continue
        checkins_by_goal.setdefault(goal_id, []).append(checkin)

    enriched_goals = []
    for goal in goals:
        goal_checkins = checkins_by_goal.get(goal.get("id"), [])
        goal_checkin_dates = sorted({item.get("checked_in_date") for item in goal_checkins if item.get("checked_in_date")})
        week_checkins = [item for item in goal_checkins if item.get("checked_in_date") and item["checked_in_date"] >= week_start]
        last_check_in_date = max([item.get("checked_in_date") for item in goal_checkins if item.get("checked_in_date")], default=None)
        reminder_days = personal_goal_normalize_reminder_days(goal.get("reminder_days"))
        reminder_today = mountain_today().strftime("%A") in reminder_days
        current_streak = 0
        if goal_checkin_dates:
            cursor = goal_checkin_dates[-1]
            current_streak = 1
            while (cursor - timedelta(days=1)) in goal_checkin_dates:
                cursor -= timedelta(days=1)
                current_streak += 1
        best_streak = 0
        run_length = 0
        previous_day = None
        for checkin_day in goal_checkin_dates:
            if previous_day and checkin_day == previous_day + timedelta(days=1):
                run_length += 1
            else:
                run_length = 1
            best_streak = max(best_streak, run_length)
            previous_day = checkin_day
        enriched_goal = dict(goal)
        enriched_goal["total_checkins"] = len(goal_checkins)
        enriched_goal["week_checkins"] = len(week_checkins)
        enriched_goal["last_check_in_date"] = last_check_in_date
        enriched_goal["today_checked_in"] = any(item.get("checked_in_date") == mountain_today() for item in goal_checkins)
        enriched_goal["current_streak"] = current_streak
        enriched_goal["best_streak"] = best_streak
        enriched_goal["reminder_days"] = reminder_days
        enriched_goal["reminder_days_label"] = personal_goal_reminder_days_label(reminder_days)
        enriched_goal["reminder_today"] = reminder_today
        enriched_goals.append(enriched_goal)
    return enriched_goals


def personal_goal_dashboard_summary(personal_goals):
    active_goals = [goal for goal in personal_goals if goal.get("status", "active") == "active"]
    on_track_goals = [goal for goal in active_goals if int(goal.get("week_checkins") or 0) >= int(goal.get("target_frequency") or 1)]
    attention_goals = [goal for goal in active_goals if int(goal.get("week_checkins") or 0) < int(goal.get("target_frequency") or 1)]
    streak_leader = max(
        active_goals,
        key=lambda goal: (
            int(goal.get("current_streak") or 0),
            int(goal.get("week_checkins") or 0),
            int(goal.get("total_checkins") or 0),
        ),
    ) if active_goals else None
    reminder_goals = sorted(
        attention_goals,
        key=lambda goal: (
            bool(goal.get("today_checked_in")),
            int(goal.get("target_frequency") or 1) - int(goal.get("week_checkins") or 0),
            -int(goal.get("current_streak") or 0),
        ),
    )
    return {
        "active_goals": active_goals,
        "on_track_goals": on_track_goals,
        "attention_goals": attention_goals,
        "reminder_goals": reminder_goals,
        "streak_leader": streak_leader,
        "week_checkins": sum(int(goal.get("week_checkins") or 0) for goal in active_goals),
        "total_checkins": sum(int(goal.get("total_checkins") or 0) for goal in active_goals),
    }


def load_personal_goals():
    if not db_enabled():
        return _enrich_personal_goals(st.session_state.personal_goals, st.session_state.personal_goal_checkins)

    try:
        with get_connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        title,
                        category,
                        target_frequency,
                        notes,
                        reminder_days,
                        status,
                        created_date
                    FROM personal_goals
                    ORDER BY created_date DESC, id DESC
                    """
                )
                goals = cur.fetchall()
                cur.execute(
                    """
                    SELECT
                        id,
                        goal_id,
                        checked_in_date,
                        note,
                        created_date
                    FROM personal_goal_checkins
                    ORDER BY checked_in_date DESC, id DESC
                    """
                )
                checkins = cur.fetchall()
                return _enrich_personal_goals(goals, checkins)
    except psycopg.Error:
        return _enrich_personal_goals(st.session_state.personal_goals, st.session_state.personal_goal_checkins)


def load_personal_goal_checkins():
    if not db_enabled():
        return list(st.session_state.personal_goal_checkins)

    try:
        with get_connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        goal_id,
                        checked_in_date,
                        note,
                        created_date
                    FROM personal_goal_checkins
                    ORDER BY checked_in_date DESC, id DESC
                    """
                )
                return cur.fetchall()
    except psycopg.Error:
        return list(st.session_state.personal_goal_checkins)


def add_personal_goal(title, category, target_frequency, notes="", reminder_days=None):
    title_value = title.strip()
    category_value = category.strip()
    notes_value = notes.strip()
    target_value = max(1, int(target_frequency or 1))
    reminder_days_value = personal_goal_normalize_reminder_days(reminder_days)
    if not title_value or not category_value:
        return

    if db_enabled():
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO personal_goals (
                        title,
                        category,
                        target_frequency,
                        notes,
                        reminder_days,
                        status,
                        created_date
                    ) VALUES (%s, %s, %s, %s, %s, 'active', %s)
                    """,
                    (
                        title_value,
                        category_value,
                        target_value,
                        notes_value,
                        json.dumps(reminder_days_value),
                        mountain_today(),
                    ),
                )
        return

    st.session_state.personal_goals.append(
        {
            "id": len(st.session_state.personal_goals) + 1,
            "title": title_value,
            "category": category_value,
            "target_frequency": target_value,
            "notes": notes_value,
            "reminder_days": reminder_days_value,
            "status": "active",
            "created_date": mountain_today(),
        }
    )


def update_personal_goal(goal_id, **fields):
    allowed_fields = {"title", "category", "target_frequency", "notes", "status", "reminder_days"}
    sanitized = {key: value for key, value in fields.items() if key in allowed_fields}
    if not sanitized:
        return

    if "target_frequency" in sanitized:
        sanitized["target_frequency"] = max(1, int(sanitized["target_frequency"] or 1))
    if "reminder_days" in sanitized:
        sanitized["reminder_days"] = json.dumps(personal_goal_normalize_reminder_days(sanitized["reminder_days"]))

    if db_enabled():
        set_parts = []
        values = []
        for key, value in sanitized.items():
            set_parts.append(f"{key} = %s")
            values.append(value)
        values.append(goal_id)
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE personal_goals SET {', '.join(set_parts)} WHERE id = %s", tuple(values))
        return

    for goal in st.session_state.personal_goals:
        if goal.get("id") == goal_id:
            if "reminder_days" in sanitized:
                sanitized["reminder_days"] = personal_goal_normalize_reminder_days(sanitized["reminder_days"])
            goal.update(sanitized)
            return


def delete_personal_goal(goal_id):
    if db_enabled():
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM personal_goals WHERE id = %s", (goal_id,))
        return
    st.session_state.personal_goals = [goal for goal in st.session_state.personal_goals if goal.get("id") != goal_id]
    st.session_state.personal_goal_checkins = [checkin for checkin in st.session_state.personal_goal_checkins if checkin.get("goal_id") != goal_id]


def log_personal_goal_checkin(goal_id, note=""):
    checkin_date = mountain_today()
    note_value = note.strip()

    if db_enabled():
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO personal_goal_checkins (
                        goal_id,
                        checked_in_date,
                        note,
                        created_date
                    ) VALUES (%s, %s, %s, %s)
                    ON CONFLICT (goal_id, checked_in_date) DO NOTHING
                    """,
                    (goal_id, checkin_date, note_value, mountain_today()),
                )
        return

    if any(checkin.get("goal_id") == goal_id and checkin.get("checked_in_date") == checkin_date for checkin in st.session_state.personal_goal_checkins):
        return
    st.session_state.personal_goal_checkins.append(
        {
            "id": len(st.session_state.personal_goal_checkins) + 1,
            "goal_id": goal_id,
            "checked_in_date": checkin_date,
            "note": note_value,
            "created_date": mountain_today(),
        }
    )


def get_task_by_id(task_id):
    if db_enabled():
        with get_connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        title,
                        description,
                        category,
                        priority,
                        status,
                        created_date,
                        due_date,
                        scheduled_date,
                        scheduled_time,
                        scheduled_minutes,
                        recurrence_rule,
                        recurrence_interval,
                        completed_date,
                        completed_at
                    FROM tasks
                    WHERE id = %s
                    """,
                    (task_id,),
                )
                return cur.fetchone()

    for task in st.session_state.tasks:
        if task["id"] == task_id:
            return task
    return None


def complete_task(task_id):
    task = get_task_by_id(task_id)
    if not task:
        return

    if task.get("status") == "completed":
        return

    update_task(task_id, status="completed", completed_date=mountain_today(), completed_at=datetime.utcnow())

    recurrence_rule = task.get("recurrence_rule")
    recurrence_interval = max(1, int(task.get("recurrence_interval") or 1))
    if recurrence_rule in ("daily", "weekly"):
        next_due = shift_date_by_rule(task.get("due_date") or mountain_today(), recurrence_rule, recurrence_interval)
        next_sched_date = shift_date_by_rule(task.get("scheduled_date"), recurrence_rule, recurrence_interval)
        add_task(
            title=task.get("title", ""),
            description=task.get("description", ""),
            category=task.get("category", "Personal"),
            priority=task.get("priority", "medium"),
            due_date=next_due,
            scheduled_date=next_sched_date,
            scheduled_time=task.get("scheduled_time"),
            scheduled_minutes=task.get("scheduled_minutes"),
            recurrence_rule=recurrence_rule,
            recurrence_interval=recurrence_interval,
        )


def set_task_status(task_id, new_status):
    task = get_task_by_id(task_id)
    if not task:
        return
    if task.get("status") == new_status:
        return

    if new_status == "completed":
        complete_task(task_id)
    else:
        update_task(task_id, status=new_status, completed_date=None, completed_at=None)


def render_task_card(task, key_prefix="task"):
    attention = task_attention_signal(task)
    attention_pill = f"<span class='pill pill-attention'>Attention: {attention['label']}</span>" if attention["tier"] < 4 or attention["age_days"] >= 7 else ""
    desc_html = f"<div style='margin-top:0.45rem; color:var(--muted);'>{html.escape(str(task.get('description') or ''))}</div>" if task.get("description") else ""
    card_html = f"<div class=\"task-card\"><div class=\"task-title\">{html.escape(str(task['title']))}</div>{desc_html}<div class=\"task-meta\"><span class=\"pill pill-priority-{task['priority']}\">Priority: {task['priority'].title()}</span><span class=\"pill pill-category\">{html.escape(str(task['category']))}</span><span class=\"pill pill-status pill-status-{task['status']}\">{status_label(task['status'])}</span>{attention_pill}<span class=\"pill\">Due {format_due_badge(task)}</span><span class=\"pill\">At {format_schedule_badge(task)}</span><span class=\"pill\">Repeat {format_recurrence_badge(task)}</span></div></div>"
    st.html(card_html)
    cols = st.columns(3)
    with cols[0]:
        if task["status"] != "completed" and st.button("Mark complete", key=f"{key_prefix}_complete_{task['id']}"):
            complete_task(task["id"])
            st.rerun()
    with cols[1]:
        status_options = ["todo", "in_progress", "blocked", "completed"]
        current_index = status_options.index(task["status"]) if task["status"] in status_options else 0
        next_status = st.selectbox(
            "Status",
            status_options,
            index=current_index,
            format_func=status_label,
            key=f"{key_prefix}_status_select_{task['id']}",
            label_visibility="collapsed",
        )
        if st.button("Apply status", key=f"{key_prefix}_status_apply_{task['id']}"):
            set_task_status(task["id"], next_status)
            st.rerun()
    with cols[2]:
        if st.button("Delete", key=f"{key_prefix}_delete_{task['id']}"):
            delete_task(task["id"])
            st.rerun()

    with st.expander("Edit task", expanded=False):
        edit_title = st.text_input("Task title", value=task.get("title", ""), key=f"{key_prefix}_edit_title_{task['id']}")
        edit_description = st.text_area(
            "Description",
            value=task.get("description", ""),
            height=80,
            key=f"{key_prefix}_edit_description_{task['id']}",
        )
        edit_category = st.selectbox(
            "Category",
            ["Personal", "Clinic"],
            index=0 if task.get("category") == "Personal" else 1,
            key=f"{key_prefix}_edit_category_{task['id']}",
        )
        edit_priority = st.selectbox(
            "Priority",
            ["high", "medium", "low"],
            index=["high", "medium", "low"].index(task.get("priority", "medium")),
            key=f"{key_prefix}_edit_priority_{task['id']}",
        )
        edit_due = st.date_input(
            "Due date",
            value=task.get("due_date") or mountain_today(),
            key=f"{key_prefix}_edit_due_{task['id']}",
        )

        has_schedule = bool(task.get("scheduled_date") and task.get("scheduled_time"))
        edit_has_schedule = st.checkbox(
            "Keep schedule",
            value=has_schedule,
            key=f"{key_prefix}_edit_has_schedule_{task['id']}",
        )
        edit_multi_day = st.checkbox(
            "Block multiple days",
            value=bool(task.get("scheduled_end_date") and task.get("scheduled_end_date") != task.get("scheduled_date")),
            key=f"{key_prefix}_edit_multi_day_{task['id']}",
        )
        sched_cols = st.columns(3)
        with sched_cols[0]:
            edit_sched_date = st.date_input(
                "Scheduled date",
                value=task.get("scheduled_date") or mountain_today(),
                disabled=not edit_has_schedule,
                key=f"{key_prefix}_edit_sched_date_{task['id']}",
            )
        with sched_cols[1]:
            edit_sched_time = st.time_input(
                "Scheduled time",
                value=task.get("scheduled_time") or time(9, 0),
                disabled=not edit_has_schedule,
                key=f"{key_prefix}_edit_sched_time_{task['id']}",
            )
        with sched_cols[2]:
            current_minutes = task.get("scheduled_minutes")
            minute_options = [15, 30, 45, 60, 90, 120]
            minute_index = minute_options.index(current_minutes) if current_minutes in minute_options else 3
            edit_sched_minutes = st.selectbox(
                "Duration",
                minute_options,
                index=minute_index,
                disabled=not edit_has_schedule,
                key=f"{key_prefix}_edit_sched_minutes_{task['id']}",
            )
        if edit_multi_day:
            edit_end_default = task.get("scheduled_end_date") or task.get("scheduled_date") or mountain_today()
            if edit_end_default < edit_sched_date:
                edit_end_default = edit_sched_date
            edit_sched_end = st.date_input(
                "Scheduled end date",
                value=edit_end_default,
                min_value=edit_sched_date,
                disabled=not edit_has_schedule,
                key=f"{key_prefix}_edit_sched_end_{task['id']}",
            )
        else:
            edit_sched_end = edit_sched_date

        recurrence_options = ["none", "daily", "weekly"]
        current_rule = task.get("recurrence_rule") or "none"
        if current_rule not in recurrence_options:
            current_rule = "none"
        rec_cols = st.columns(2)
        with rec_cols[0]:
            edit_recurrence_rule = st.selectbox(
                "Recurrence",
                recurrence_options,
                index=recurrence_options.index(current_rule),
                format_func=lambda value: "None" if value == "none" else value.title(),
                key=f"{key_prefix}_edit_recurrence_rule_{task['id']}",
            )
        with rec_cols[1]:
            edit_recurrence_interval = st.number_input(
                "Every",
                min_value=1,
                max_value=30,
                value=int(task.get("recurrence_interval") or 1),
                step=1,
                disabled=edit_recurrence_rule == "none",
                key=f"{key_prefix}_edit_recurrence_interval_{task['id']}",
            )

        if st.button("Save changes", key=f"{key_prefix}_save_{task['id']}"):
            update_task(
                task["id"],
                title=edit_title.strip(),
                description=edit_description.strip(),
                category=edit_category,
                priority=edit_priority,
                due_date=edit_due,
                scheduled_date=edit_sched_date if edit_has_schedule else None,
                scheduled_end_date=edit_sched_end if edit_has_schedule else None,
                scheduled_time=edit_sched_time if edit_has_schedule else None,
                scheduled_minutes=edit_sched_minutes if edit_has_schedule else None,
                recurrence_rule=None if edit_recurrence_rule == "none" else edit_recurrence_rule,
                recurrence_interval=int(edit_recurrence_interval),
            )
            st.success("Task updated.")
            st.rerun()


def render_task_list_panel(title, subtitle, tasks_to_render, key_prefix, empty_text):
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown(f'<div class="panel-title"><h3>{title}</h3><span>{subtitle}</span></div>', unsafe_allow_html=True)
    if tasks_to_render:
        for task in tasks_to_render:
            render_task_card(task, key_prefix=key_prefix)
    else:
        st.markdown(f'<div class="empty-state">{empty_text}</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)


def ai_workbench_summary(tasks, active_tasks):
    today = mountain_today()
    soon = today + timedelta(days=3)
    overdue = [task for task in active_tasks if task.get("due_date") and task["due_date"] < today]
    due_today = [task for task in active_tasks if task.get("due_date") == today]
    due_soon = [task for task in active_tasks if task.get("due_date") and today <= task["due_date"] <= soon]
    blocked = [task for task in active_tasks if task.get("status") == "blocked"]
    unscheduled_high = [
        task
        for task in active_tasks
        if task.get("priority") == "high" and not (task.get("scheduled_date") and task.get("scheduled_time"))
    ]
    aging_high = sorted(unscheduled_high, key=lambda task: task_attention_sort_key(task, today))
    in_progress = [task for task in active_tasks if task.get("status") == "in_progress"]
    completed_today = [task for task in tasks if task.get("status") == "completed" and task.get("completed_date") == today]

    if overdue:
        recommended = sorted(overdue, key=lambda task: task_attention_sort_key(task, today))[0]
        focus_label = f"Overdue: {recommended.get('title')}"
    elif due_today:
        recommended = sorted(due_today, key=lambda task: task_attention_sort_key(task, today))[0]
        focus_label = f"Due today: {recommended.get('title')}"
    elif unscheduled_high:
        recommended = aging_high[0]
        focus_label = f"High priority and unscheduled: {recommended.get('title')}"
    elif blocked:
        recommended = sorted(blocked, key=lambda task: task_attention_sort_key(task, today))[0]
        focus_label = f"Blocked first: {recommended.get('title')}"
    elif in_progress:
        recommended = sorted(in_progress, key=lambda task: task_attention_sort_key(task, today))[0]
        focus_label = f"Keep moving: {recommended.get('title')}"
    elif active_tasks:
        recommended = sorted(active_tasks, key=lambda task: task_attention_sort_key(task, today))[0]
        focus_label = f"Best next task: {recommended.get('title')}"
    else:
        recommended = None
        focus_label = "No active tasks right now."

    return {
        "active_count": len(active_tasks),
        "overdue_count": len(overdue),
        "due_today_count": len(due_today),
        "due_soon_count": len(due_soon),
        "blocked_count": len(blocked),
        "unscheduled_high_count": len(unscheduled_high),
        "aging_high_count": len(aging_high),
        "completed_today_count": len(completed_today),
        "focus_label": focus_label,
        "recommended_task": recommended,
        "overdue": overdue[:3],
        "due_soon": due_soon[:3],
        "blocked": blocked[:3],
        "unscheduled_high": aging_high[:3],
    }


def apply_clinic_visit_template(form_key, template_key, st_module=st):
    templates = clinic_visit_templates()
    template = templates.get(template_key, templates["blank"])
    st_module.session_state[f"{form_key}_title"] = template["title"]
    st_module.session_state[f"{form_key}_description"] = template["description"]
    st_module.session_state[f"{form_key}_category"] = "Clinic"
    st_module.session_state[f"{form_key}_priority"] = template["priority"]
    st_module.session_state[f"{form_key}_due_date"] = mountain_today()
    st_module.session_state[f"{form_key}_schedule_enabled"] = template["schedule_enabled"]
    st_module.session_state[f"{form_key}_scheduled_date"] = mountain_today()
    st_module.session_state[f"{form_key}_scheduled_time"] = template["scheduled_time"]
    st_module.session_state[f"{form_key}_scheduled_minutes"] = template["scheduled_minutes"]
    st_module.session_state[f"{form_key}_recurrence_rule"] = "none"
    st_module.session_state[f"{form_key}_recurrence_interval"] = 1


def apply_clinic_visit_template_from_state(form_key, template_state_key, st_module=st):
    apply_clinic_visit_template(form_key, st_module.session_state.get(template_state_key, "blank"), st_module=st_module)


def apply_personal_schedule_template(form_key, template_key, st_module=st):
    templates = personal_schedule_templates()
    template = templates.get(template_key, templates["blank"])
    start_date = mountain_today()
    end_offset_days = int(template.get("scheduled_end_offset_days", 0))
    end_date = start_date + timedelta(days=end_offset_days)
    if end_date < start_date:
        end_date = start_date
    st_module.session_state[f"{form_key}_title"] = template["title"]
    st_module.session_state[f"{form_key}_description"] = template["description"]
    st_module.session_state[f"{form_key}_priority"] = template["priority"]
    st_module.session_state[f"{form_key}_scheduled_date"] = start_date
    st_module.session_state[f"{form_key}_scheduled_time"] = template["scheduled_time"]
    st_module.session_state[f"{form_key}_scheduled_minutes"] = template["scheduled_minutes"]
    st_module.session_state[f"{form_key}_all_day"] = template["all_day"]
    st_module.session_state[f"{form_key}_scheduled_end_date"] = end_date
    st_module.session_state[f"{form_key}_multi_day"] = end_date > start_date


def apply_personal_schedule_template_from_state(form_key, template_state_key, st_module=st):
    apply_personal_schedule_template(form_key, st_module.session_state.get(template_state_key, "blank"), st_module=st_module)


def render_overview_tuning_panel(app_settings, panel_key="overview"):
    settings_key = f"{panel_key}_settings"
    if settings_key not in st.session_state:
        st.session_state[settings_key] = overview_runtime_settings(app_settings)

    current = st.session_state[settings_key]
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Today\'s Setup</h3><span>Edit this whenever the day changes</span></div>', unsafe_allow_html=True)
    left_col, right_col = st.columns([1.1, 0.9], gap="large")

    with left_col:
        day_mode = st.selectbox(
            "Day mode",
            ["Auto", "Outpatient clinic", "Procedure Friday", "Admin catch-up", "Mixed day"],
            index=["Auto", "Outpatient clinic", "Procedure Friday", "Admin catch-up", "Mixed day"].index(current["day_mode"]) if current["day_mode"] in ["Auto", "Outpatient clinic", "Procedure Friday", "Admin catch-up", "Mixed day"] else 0,
            key=f"{panel_key}_day_mode",
        )
        role_label = st.text_input("Role label", value=current["role_label"], key=f"{panel_key}_role_label")
        site_label = st.text_input("Work setting", value=current["site_label"], key=f"{panel_key}_site_label")
        patient_target = st.slider("Expected patient load", min_value=10, max_value=40, value=current["patient_target"], step=1, key=f"{panel_key}_patient_target")
        procedure_target = st.slider("Procedure target", min_value=2, max_value=20, value=current["procedure_target"], step=1, key=f"{panel_key}_procedure_target")
        clinic_weekdays = st.multiselect(
            "Clinic weekdays",
            ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
            default=current["clinic_weekdays"] if isinstance(current.get("clinic_weekdays"), list) else ["Monday", "Thursday"],
            key=f"{panel_key}_clinic_weekdays",
        )

    with right_col:
        admin_buffer = st.slider("Admin buffer minutes", min_value=30, max_value=150, value=current["admin_buffer_minutes"], step=15, key=f"{panel_key}_admin_buffer")
        shift_minutes = st.slider("Shift length minutes", min_value=240, max_value=600, value=current["shift_minutes"], step=15, key=f"{panel_key}_shift_minutes")
        focus_window_minutes = st.slider("Focus window minutes", min_value=30, max_value=180, value=current["focus_window_minutes"], step=15, key=f"{panel_key}_focus_window")
        admin_weekdays = st.multiselect(
            "Admin weekdays",
            ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
            default=current["admin_weekdays"] if isinstance(current.get("admin_weekdays"), list) else ["Tuesday"],
            key=f"{panel_key}_admin_weekdays",
        )
        procedure_frequency = st.selectbox(
            "Procedure Friday cadence",
            [1, 2, 3, 4],
            index=[1, 2, 3, 4].index(int(current.get("procedure_friday_frequency_weeks", 2))) if int(current.get("procedure_friday_frequency_weeks", 2)) in [1, 2, 3, 4] else 1,
            key=f"{panel_key}_procedure_frequency",
        )
        procedure_cycle_offset = st.selectbox(
            "Procedure cycle offset",
            [0, 1],
            index=[0, 1].index(int(current.get("procedure_friday_cycle_offset", 0))) if int(current.get("procedure_friday_cycle_offset", 0)) in [0, 1] else 0,
            format_func=lambda value: "This week" if value == 0 else "Next week",
            key=f"{panel_key}_procedure_cycle_offset",
        )
        st.caption("These controls are session-editable so the overview can flex from clinic days to procedure days to admin catch-up.")

    updated = {
        "day_mode": day_mode,
        "role_label": role_label.strip() or "Medical Assistant",
        "site_label": site_label.strip() or "Mercy Orthopedics",
        "patient_target": int(patient_target),
        "procedure_target": int(procedure_target),
        "admin_buffer_minutes": int(admin_buffer),
        "shift_minutes": int(shift_minutes),
        "focus_window_minutes": int(focus_window_minutes),
        "clinic_weekdays": clinic_weekdays or ["Thursday", "Monday"],
        "admin_weekdays": admin_weekdays or ["Tuesday"],
        "procedure_friday_frequency_weeks": int(procedure_frequency),
        "procedure_friday_cycle_offset": int(procedure_cycle_offset),
    }
    st.session_state[settings_key] = updated

    if st.button("Save overview defaults", key=f"{panel_key}_save_overview_defaults", type="secondary"):
        app_settings = save_app_settings(
            {
                **app_settings,
                "overview_day_mode": updated["day_mode"],
                "overview_role_label": updated["role_label"],
                "overview_site_label": updated["site_label"],
                "overview_patient_target": updated["patient_target"],
                "overview_procedure_target": updated["procedure_target"],
                "overview_admin_buffer_minutes": updated["admin_buffer_minutes"],
                "overview_shift_minutes": updated["shift_minutes"],
                "overview_focus_window_minutes": updated["focus_window_minutes"],
                "overview_clinic_weekdays": updated["clinic_weekdays"],
                "overview_admin_weekdays": updated["admin_weekdays"],
                "overview_procedure_friday_frequency_weeks": updated["procedure_friday_frequency_weeks"],
                "overview_procedure_friday_cycle_offset": updated["procedure_friday_cycle_offset"],
            }
        )
        st.success("Overview defaults saved.")
        st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)
    return updated


def render_personal_focus_panel(personal_tasks, active_tasks, app_settings, panel_key="personal"):
    summary = personal_focus_summary(personal_tasks, active_tasks, app_settings)
    focus_key = f"{panel_key}_focus_task"
    sprint_key = f"{panel_key}_sprint_minutes"

    if sprint_key not in st.session_state:
        st.session_state[sprint_key] = summary["focus_minutes"]

    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Focus Sprint</h3><span>Pick one task and protect a working block</span></div>', unsafe_allow_html=True)
    left_col, right_col = st.columns([1.15, 0.85], gap="large")

    with left_col:
        st.markdown(f"<div class='ai-chip-grid'><span class='ai-chip'>{summary['personal_count']} active personal tasks</span><span class='ai-chip'>{summary['blocked_count']} blocked</span><span class='ai-chip'>{summary['focus_minutes']}-minute default sprint</span></div>", unsafe_allow_html=True)
        focus_options = [task["title"] for task in summary["focus_tasks"]] or ["No personal task ready"]
        if focus_key not in st.session_state:
            st.session_state[focus_key] = focus_options[0]
        selected_focus = st.selectbox("Focus task", focus_options, key=focus_key)
        sprint_minutes = st.slider("Sprint length (minutes)", min_value=30, max_value=180, value=int(st.session_state[sprint_key]), step=15, key=sprint_key)
        st.caption("Use this page to isolate one personal objective before the day gets noisy.")
        if st.button("Start Focus Sprint", key=f"{panel_key}_start_sprint", type="primary"):
            chosen = next((task for task in summary["focus_tasks"] if task["title"] == selected_focus), None)
            if chosen:
                set_task_status(chosen["id"], "in_progress")
                st.success(f"Pulled '{chosen['title']}' into in-progress for a {sprint_minutes}-minute sprint.")
                st.rerun()

    with right_col:
        st.markdown('<div class="panel-title"><h3>Personal Stack</h3><span>Ranked by urgency and friction</span></div>', unsafe_allow_html=True)
        if summary["focus_tasks"]:
            for task in summary["focus_tasks"]:
                st.markdown(
                    f"- <strong>{task['title']}</strong> · {task['priority'].title()} · {task.get('due_date') or 'No due date'} · {status_label(task.get('status', 'todo'))}",
                    unsafe_allow_html=True,
                )
        else:
            st.markdown('<div class="empty-state">No personal task is ready to pull into a sprint.</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)


def render_personal_quick_capture(form_key, defaults):
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Quick Capture</h3><span>Title and priority — everything else defaults</span></div>', unsafe_allow_html=True)
    with st.form(form_key, clear_on_submit=True):
        title = st.text_input("Task title", placeholder="What needs to get done?", key=f"{form_key}_title")
        priority = st.selectbox(
            "Priority",
            ["medium", "high", "low"],
            format_func=lambda v: v.title(),
            key=f"{form_key}_priority",
        )
        submitted = st.form_submit_button("Capture →", type="primary")
    if submitted:
        if not title.strip():
            st.warning("Add a title to capture the task.")
        else:
            add_task(
                title.strip(),
                "",
                "Personal",
                priority,
                mountain_today(),
                scheduled_date=None,
                scheduled_time=None,
                scheduled_minutes=None,
                recurrence_rule=None,
                recurrence_interval=1,
            )
            st.success(f"\'{title.strip()}\' captured.")
            st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)


def render_personal_one_thing(personal_tasks, panel_key):
    pin_key = f"{panel_key}_pinned_id"
    pinned_id = st.session_state.get(pin_key)
    pinned_task = next((t for t in personal_tasks if t.get("id") == pinned_id), None) if pinned_id else None
    ready = [t for t in personal_tasks if t.get("status") != "completed"]
    pick_key = f"{panel_key}_pick"

    if ready and pick_key not in st.session_state:
        st.session_state[pick_key] = ready[0]["title"]

    st.markdown(
        '<div class="panel" style="background: linear-gradient(135deg, rgba(15,118,110,0.10), rgba(29,78,216,0.09)); border: 1.5px solid rgba(15,118,110,0.22);">',
        unsafe_allow_html=True,
    )
    st.markdown(
        "<div class='panel-title'><h3>Today's One Thing</h3><span>One task you will not let slip today</span></div>",
        unsafe_allow_html=True,
    )

    if pinned_task:
        due_str = (
            pinned_task["due_date"].strftime("%b %d")
            if pinned_task.get("due_date") and hasattr(pinned_task["due_date"], "strftime")
            else "No due date"
        )
        st.markdown(
            f"<div class='task-card' style='background: rgba(255,255,255,0.9); border: 1.5px solid rgba(15,118,110,0.3);'>"
            f"<div class='task-title' style='font-size: 1.18rem;'>{pinned_task['title']}</div>"
            f"<div class='task-meta'>"
            f"<span class='pill pill-priority-{pinned_task['priority']}'>{pinned_task['priority'].title()}</span>"
            f"<span class='pill pill-status pill-status-{pinned_task['status']}'>{status_label(pinned_task['status'])}</span>"
            f"<span class='pill'>Due: {due_str}</span>"
            f"</div></div>",
            unsafe_allow_html=True,
        )
        action_cols = st.columns([1, 1, 3])
        with action_cols[0]:
            if st.button("Mark done", key=f"{panel_key}_pin_complete", type="primary"):
                complete_task(pinned_task["id"])
                st.session_state.pop(pin_key, None)
                st.rerun()
        with action_cols[1]:
            if st.button("Unpin", key=f"{panel_key}_unpin"):
                st.session_state.pop(pin_key, None)
                st.rerun()
        if ready:
            options = [t["title"] for t in ready]
            if st.session_state.get(pick_key) not in options:
                st.session_state[pick_key] = options[0]
            st.markdown("<div style='height:0.5rem;'></div>", unsafe_allow_html=True)
            selected = st.selectbox("Switch pinned task", options, key=pick_key)
            selected_task = next((t for t in ready if t["title"] == selected), None)
            can_repin = bool(selected_task) and selected_task.get("id") != pinned_task.get("id")
            if st.button("Pin selected task", key=f"{panel_key}_repin_btn", type="secondary", disabled=not can_repin):
                st.session_state[pin_key] = selected_task["id"]
                st.rerun()
    else:
        if ready:
            options = [t["title"] for t in ready]
            if st.session_state.get(pick_key) not in options:
                st.session_state[pick_key] = options[0]
            selected = st.selectbox("Choose your one thing for today", options, key=pick_key)
            if st.button("Pin this →", key=f"{panel_key}_pin_btn", type="primary"):
                chosen = next((t for t in ready if t["title"] == selected), None)
                if chosen:
                    st.session_state[pin_key] = chosen["id"]
                    st.rerun()
        else:
            st.markdown(
                '<div class="empty-state">No personal tasks ready to pin. Capture one above.</div>',
                unsafe_allow_html=True,
            )

    st.markdown('</div>', unsafe_allow_html=True)


def render_personal_goal_reminders_panel(personal_goals, panel_key="personal_goal_reminders"):
    summary = personal_goal_dashboard_summary(personal_goals)
    reminder_goals = summary["reminder_goals"]

    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Goal Reminders</h3><span>What needs attention before the week closes</span></div>', unsafe_allow_html=True)
    if reminder_goals:
        for goal in reminder_goals[:5]:
            missing = max(0, int(goal.get("target_frequency") or 1) - int(goal.get("week_checkins") or 0))
            reminder_text = "Check in today" if goal.get("reminder_today") and not goal.get("today_checked_in") else "Already checked in today" if goal.get("today_checked_in") else "Scheduled reminder this week"
            st.markdown(
                f"<div class='task-card' style='margin-bottom:0.75rem;'><div class='task-title' style='font-size:1rem;'>{goal.get('title')}</div>"
                f"<div class='task-meta'><span class='pill pill-category'>{goal.get('category')}</span><span class='pill'>Need {missing} more this week</span><span class='pill'>Current streak: {int(goal.get('current_streak') or 0)}</span><span class='pill'>Remind: {goal.get('reminder_days_label') or 'None'}</span></div>"
                f"<div style='margin-top:0.4rem; color:#475467;'>{reminder_text}</div></div>",
                unsafe_allow_html=True,
            )
    else:
        st.markdown('<div class="empty-state">No reminders right now. Your active goals are on pace for the week.</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)


def render_personal_goal_review_panel(personal_goals, panel_key="personal_goal_review"):
    summary = personal_goal_dashboard_summary(personal_goals)
    active_goals = summary["active_goals"]
    streak_leader = summary["streak_leader"]

    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Weekly Review</h3><span>Track progress across fitness, reading, journaling, and more</span></div>', unsafe_allow_html=True)
    review_cols = st.columns(4)
    review_cols[0].metric("Active goals", len(active_goals))
    review_cols[1].metric("On track", len(summary["on_track_goals"]))
    review_cols[2].metric("Needs attention", len(summary["attention_goals"]))
    review_cols[3].metric("This week", summary["week_checkins"])

    if active_goals:
        best_streak_text = "No streaks yet"
        if streak_leader:
            best_streak_text = f"{streak_leader.get('title')} · {int(streak_leader.get('current_streak') or 0)} day streak"
        st.markdown(
            f"<div class='empty-state' style='text-align:left;'><strong>Summary:</strong> You logged {summary['week_checkins']} goal check-ins this week across {len(active_goals)} active goals.<br /><strong>Leader:</strong> {best_streak_text}<br /><strong>Focus:</strong> {len(summary['attention_goals'])} goal(s) still need check-ins to hit weekly targets.</div>",
            unsafe_allow_html=True,
        )
        if summary["on_track_goals"]:
            st.markdown(
                "<div class='panel-title' style='margin-top:0.75rem;'><h3>On Track</h3><span>Goals already meeting the weekly target</span></div>",
                unsafe_allow_html=True,
            )
            for goal in summary["on_track_goals"][:4]:
                st.markdown(f"- <strong>{goal['title']}</strong> · {goal.get('category')} · {int(goal.get('week_checkins') or 0)}/{int(goal.get('target_frequency') or 1)}", unsafe_allow_html=True)
    else:
        st.markdown('<div class="empty-state">Add a few goals to get a weekly review here.</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)


def render_personal_goal_history_panel(personal_goals, panel_key="personal_goal_history"):
    goals_by_id = {goal.get("id"): goal for goal in personal_goals}
    checkins = load_personal_goal_checkins()

    month_key = f"{panel_key}_month_anchor"
    if month_key not in st.session_state:
        st.session_state[month_key] = mountain_today().replace(day=1)

    month_anchor = st.session_state[month_key]
    month_start = month_anchor.replace(day=1)
    next_month_start = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    month_end = next_month_start - timedelta(days=1)
    month_checkins = [item for item in checkins if item.get("checked_in_date") and month_start <= item["checked_in_date"] <= month_end]
    checkins_by_day = {}
    for item in month_checkins:
        checkins_by_day.setdefault(item["checked_in_date"], []).append(item)

    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Monthly Goal History</h3><span>See how your goal check-ins land across the month</span></div>', unsafe_allow_html=True)
    controls = st.columns([1, 2, 1])
    with controls[0]:
        if st.button("Prev month", key=f"{panel_key}_prev"):
            st.session_state[month_key] = (month_start - timedelta(days=1)).replace(day=1)
            st.rerun()
    with controls[1]:
        st.markdown(f"<div style='text-align:center; font-weight:700; margin-top:0.4rem;'>{month_start.strftime('%B %Y')}</div>", unsafe_allow_html=True)
    with controls[2]:
        if st.button("Next month", key=f"{panel_key}_next"):
            st.session_state[month_key] = next_month_start
            st.rerun()

    history_cols = st.columns(4)
    history_cols[0].metric("Check-ins", len(month_checkins))
    history_cols[1].metric("Active goals", len([goal for goal in personal_goals if goal.get("status") == "active"]))
    history_cols[2].metric("Days with progress", len(checkins_by_day))
    history_cols[3].metric("Best streak", max([int(goal.get("current_streak") or 0) for goal in personal_goals], default=0))

    cal = calendar.Calendar(firstweekday=0)
    weeks = cal.monthdatescalendar(month_start.year, month_start.month)
    headers = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    table_lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for week in weeks:
        cells = []
        for day in week:
            if day.month != month_start.month:
                cells.append(" ")
                continue
            day_items = checkins_by_day.get(day, [])
            if not day_items:
                cells.append(f"**{day.day}**")
                continue
            goal_names = []
            for item in day_items[:3]:
                goal = goals_by_id.get(item.get("goal_id"))
                if goal:
                    goal_names.append(goal.get("title", "Goal"))
            remaining = len(day_items) - len(goal_names)
            label_text = "<br>".join([f"**{day.day}**", f"{len(day_items)} log(s)"] + goal_names + ([f"+{remaining} more"] if remaining > 0 else []))
            cells.append(label_text)
        table_lines.append("| " + " | ".join(cells) + " |")
    st.markdown("\n".join(table_lines), unsafe_allow_html=True)

    if month_checkins:
        st.markdown('<div class="panel-title" style="margin-top:0.75rem;"><h3>Recent Check-ins</h3><span>Newest goal activity</span></div>', unsafe_allow_html=True)
        for item in month_checkins[:10]:
            goal = goals_by_id.get(item.get("goal_id"))
            goal_title = goal.get("title") if goal else f"Goal #{item.get('goal_id')}"
            note_text = item.get("note") or ""
            date_label = item["checked_in_date"].strftime("%b %d") if hasattr(item["checked_in_date"], "strftime") else str(item.get("checked_in_date"))
            st.markdown(f"- <strong>{date_label}</strong> · {goal_title}{(' · ' + note_text) if note_text else ''}", unsafe_allow_html=True)
    else:
        st.markdown('<div class="empty-state">No goal check-ins in this month yet.</div>', unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)


def render_personal_goals_panel(personal_goals, panel_key="personal_goals"):
    summary = personal_goal_dashboard_summary(personal_goals)
    active_goals = summary["active_goals"]
    week_checkins = summary["week_checkins"]
    on_track_count = len(summary["on_track_goals"])
    total_checkins = summary["total_checkins"]
    best_streak = summary["streak_leader"]["current_streak"] if summary["streak_leader"] else 0

    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Personal Goals</h3><span>Build and track fitness, reading, journaling, and other repeat goals</span></div>', unsafe_allow_html=True)
    goal_cols = st.columns(4)
    goal_cols[0].metric("Active goals", len(active_goals))
    goal_cols[1].metric("Check-ins this week", week_checkins)
    goal_cols[2].metric("On track", on_track_count)
    goal_cols[3].metric("Best streak", best_streak)

    st.caption(f"Total logs across active goals: {total_checkins}")

    with st.form(f"{panel_key}_add_goal"):
        goal_title = st.text_input("Goal title", placeholder="Fitness, reading, journaling, etc.")
        goal_category = st.selectbox("Goal category", ["Fitness", "Reading", "Journaling", "Custom"])
        goal_target = st.slider("Target check-ins per week", min_value=1, max_value=14, value=3, step=1)
        goal_reminder_days = st.multiselect("Reminder days", PERSONAL_GOAL_WEEKDAY_NAMES, default=[mountain_today().strftime("%A")])
        goal_notes = st.text_area("Notes", height=80, placeholder="How you want to approach this goal, reminders, and what success looks like.")
        goal_submit = st.form_submit_button("Add goal", type="primary")

    if goal_submit:
        if not goal_title.strip():
            st.warning("Add a goal title first.")
        else:
            add_personal_goal(goal_title, goal_category, goal_target, goal_notes, reminder_days=goal_reminder_days)
            st.success("Personal goal added.")
            st.rerun()

    if personal_goals:
        for goal in personal_goals:
            goal_id = goal.get("id")
            target_frequency = max(1, int(goal.get("target_frequency") or 1))
            week_progress = int(goal.get("week_checkins") or 0)
            progress_ratio = min(1.0, week_progress / target_frequency)
            last_check_in = goal.get("last_check_in_date")
            last_check_in_label = last_check_in.strftime("%b %d") if hasattr(last_check_in, "strftime") else "Never"
            goal_status = goal.get("status", "active")
            current_streak = int(goal.get("current_streak") or 0)
            best_goal_streak = int(goal.get("best_streak") or 0)
            reminder_days_label = goal.get("reminder_days_label") or personal_goal_reminder_days_label(goal.get("reminder_days"))

            st.markdown('<div class="task-card">', unsafe_allow_html=True)
            st.markdown(f'<div class="task-title">{goal.get("title")}</div>', unsafe_allow_html=True)
            if goal.get("notes"):
                st.caption(goal.get("notes"))
            st.markdown(
                f"<div class='task-meta'><span class='pill pill-category'>{goal.get('category')}</span><span class='pill'>This week: {week_progress}/{target_frequency}</span><span class='pill'>Streak: {current_streak}</span><span class='pill'>Best: {best_goal_streak}</span><span class='pill'>Last check-in: {last_check_in_label}</span><span class='pill'>Remind: {reminder_days_label}</span><span class='pill pill-status pill-status-{goal_status}'>{goal_status.title()}</span></div>",
                unsafe_allow_html=True,
            )
            st.progress(int(progress_ratio * 100))
            st.caption(f"{week_progress}/{target_frequency} check-ins this week. Keep the streak alive with one small step today.")

            action_cols = st.columns([1, 1, 1, 2])
            with action_cols[0]:
                if st.button("Log today", key=f"{panel_key}_log_{goal_id}", type="primary", disabled=bool(goal.get("today_checked_in"))):
                    log_personal_goal_checkin(goal_id)
                    st.success(f"Logged progress for '{goal.get('title')}'.")
                    st.rerun()
            with action_cols[1]:
                if st.button("Toggle status", key=f"{panel_key}_toggle_{goal_id}"):
                    new_status = "completed" if goal_status == "active" else "active"
                    update_personal_goal(goal_id, status=new_status)
                    st.rerun()
            with action_cols[2]:
                if st.button("Delete", key=f"{panel_key}_delete_{goal_id}"):
                    delete_personal_goal(goal_id)
                    st.success("Goal deleted.")
                    st.rerun()
            with action_cols[3]:
                if goal.get("today_checked_in"):
                    st.caption("Already checked in today.")
                elif progress_ratio >= 1.0:
                    st.caption("Weekly target reached. Keep the streak going or raise the bar.")
                else:
                    st.caption("Use Log today to record a workout, chapter, journal entry, or other completed step.")
            st.markdown('</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="empty-state">Add goals like fitness, reading, or journaling to start tracking them here.</div>', unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)


def render_clinic_command_center(clinic_tasks, active_tasks, app_settings, panel_key="clinic"):
    profiles = clinic_day_profiles(app_settings)
    mode_key = f"{panel_key}_mode"
    focus_task_key = f"{panel_key}_focus_task"
    if mode_key not in st.session_state:
        st.session_state[mode_key] = "surgeon_clinic"

    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Clinic Command Center</h3><span>Build clinic-day structure around patient volume</span></div>', unsafe_allow_html=True)
    mode_choice = st.radio(
        "Clinic day type",
        ["surgeon_clinic", "general_clinic", "procedure_friday"],
        index=["surgeon_clinic", "general_clinic", "procedure_friday"].index(st.session_state[mode_key]),
        format_func=lambda value: profiles[value]["label"],
        horizontal=True,
        key=mode_key,
    )
    summary = clinic_day_summary(clinic_tasks, active_tasks, app_settings, mode_choice)
    profile = summary["profile"]
    blocks = summary["block_plan"]

    metric_cols = st.columns(4)
    metric_cols[0].metric(profile["volume_label"].title(), profile["volume_target"])
    metric_cols[1].metric("Estimated blocks", blocks["estimated_blocks"])
    metric_cols[2].metric("Clinic tasks", summary["active_clinic_count"])
    metric_cols[3].metric("Unscheduled", summary["clinic_unscheduled_count"])

    body_left, body_right = st.columns([1.15, 0.85], gap="large")
    with body_left:
        st.markdown(
            f"<div class='empty-state' style='text-align:left;'><strong>{profile['label']}</strong><br />Target {profile['volume_target']} {profile['volume_label']} · {profile['focus']}<br />Prep {profile['prep_minutes']} min · Admin buffer {profile['admin_buffer_minutes']} min · Estimated slack {blocks['slack_minutes']} min</div>",
            unsafe_allow_html=True,
        )
        st.markdown("<div class='panel-title' style='margin-top:1rem;'><h3>Clinic Day Blueprint</h3><span>Time-block estimate for the selected day type</span></div>", unsafe_allow_html=True)
        st.markdown(
            f"- Prep and huddle: {profile['prep_minutes']} minutes\n"
            f"- Morning clinic/procedures: about {blocks['morning_volume']} blocks\n"
            f"- Midday admin buffer: {profile['admin_buffer_minutes']} minutes\n"
            f"- Afternoon clinic/procedures: about {blocks['afternoon_volume']} blocks\n"
            f"- Remaining slack: {blocks['slack_minutes']} minutes",
            unsafe_allow_html=True,
        )
        st.caption("The goal is to protect note time and keep the schedule realistic, not to pack every minute.")

    with body_right:
        st.markdown('<div class="panel-title"><h3>Top Clinic Tasks</h3><span>What should be handled first</span></div>', unsafe_allow_html=True)
        if summary["top_clinic_tasks"]:
            task_titles = [task["title"] for task in summary["top_clinic_tasks"]]
            if focus_task_key not in st.session_state:
                st.session_state[focus_task_key] = task_titles[0]
            selected_task = st.selectbox("Clinic focus task", task_titles, key=focus_task_key)
            if st.button("Move clinic focus to In Progress", key=f"{panel_key}_start_clinic_focus", type="primary"):
                target = next((task for task in summary["top_clinic_tasks"] if task["title"] == selected_task), None)
                if target:
                    set_task_status(target["id"], "in_progress")
                    st.success(f"'{target['title']}' moved to In Progress.")
                    st.rerun()
            for task in summary["top_clinic_tasks"]:
                st.markdown(
                    f"- <strong>{task['title']}</strong> · {task['priority'].title()} · {task.get('due_date') or 'No due date'} · {status_label(task.get('status', 'todo'))}",
                    unsafe_allow_html=True,
                )
        else:
            st.markdown('<div class="empty-state">No clinic tasks are currently active.</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)


def render_schedule_builder_panel(active_tasks, app_settings, panel_key="schedule"):
    snapshot = schedule_workload_snapshot(active_tasks)
    personal_tasks = [task for task in active_tasks if task.get("category") == "Personal"]
    lens_key = f"{panel_key}_lens"
    if lens_key not in st.session_state:
        st.session_state[lens_key] = "Balanced"

    lens_choice = st.radio(
        "Planning lens",
        ["Balanced", "Clinic-first", "Personal-first", "Urgent-first"],
        horizontal=True,
        key=lens_key,
    )

    def sort_key(task):
        due_value = task.get("due_date") or date.max
        if lens_choice == "Clinic-first":
            return (0 if task.get("category") == "Clinic" else 1, priority_rank(task["priority"]), due_value)
        if lens_choice == "Personal-first":
            return (0 if task.get("category") == "Personal" else 1, priority_rank(task["priority"]), due_value)
        if lens_choice == "Urgent-first":
            return (priority_rank(task["priority"]), due_value, 0 if task.get("status") == "blocked" else 1)
        return (due_value, priority_rank(task["priority"]), 0 if task.get("scheduled_date") else 1)

    ranked_tasks = sorted(snapshot["unscheduled"], key=sort_key)[:8]
    schedule_time_default = parse_time_value(app_settings.get("default_schedule_time")) or time(9, 0)
    duration_options = [15, 30, 45, 60, 90, 120]
    default_duration = int(app_settings.get("default_duration", 60) or 60)
    default_duration_index = duration_options.index(default_duration) if default_duration in duration_options else 3
    pin_task_key = f"{panel_key}_pin_task"
    if pin_task_key not in st.session_state and ranked_tasks:
        st.session_state[pin_task_key] = ranked_tasks[0]["id"]
    week_key = f"{panel_key}_week_anchor"
    if week_key not in st.session_state:
        today = mountain_today()
        st.session_state[week_key] = today - timedelta(days=today.weekday())

    week_start = st.session_state[week_key]
    week_days = [week_start + timedelta(days=offset) for offset in range(7)]
    daily_capacity_minutes = max(60, safe_int(app_settings.get("schedule_daily_capacity_minutes", 480), 480))
    capacity_days_per_week = max(1, min(7, safe_int(app_settings.get("schedule_capacity_days_per_week", 5), 5)))
    weekly_capacity_minutes = daily_capacity_minutes * capacity_days_per_week
    scheduled_by_day = {day: [] for day in week_days}
    scheduled_minutes_by_day = {day: 0 for day in week_days}
    for task in snapshot["upcoming"]:
        for task_day in scheduled_date_range(task):
            if task_day in scheduled_by_day:
                scheduled_by_day[task_day].append(task)
                scheduled_minutes_by_day[task_day] += scheduled_minutes_on_day(task, task_day)
    for day_tasks in scheduled_by_day.values():
        day_tasks.sort(key=lambda task: (task.get("scheduled_time") or time(23, 59), priority_rank(task["priority"]), task["title"]))

    due_by_day = {day: [] for day in week_days}
    for task in active_tasks:
        due_day = task.get("due_date")
        if due_day in due_by_day:
            due_by_day[due_day].append(task)
    for day_tasks in due_by_day.values():
        day_tasks.sort(key=lambda task: (priority_rank(task["priority"]), task.get("scheduled_time") or time(23, 59), task.get("title") or ""))

    week_planned_minutes = sum(scheduled_minutes_by_day.values())
    overloaded_days = [day for day in week_days if scheduled_minutes_by_day[day] > daily_capacity_minutes]
    remaining_week_capacity = weekly_capacity_minutes - week_planned_minutes

    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Schedule Builder</h3><span>Plan work, pin personal blocks, and move tasks into real time</span></div>', unsafe_allow_html=True)
    metric_cols = st.columns(4)
    metric_cols[0].metric("Scheduled", len(snapshot["upcoming"]))
    metric_cols[1].metric("Unscheduled", len(snapshot["unscheduled"]))
    metric_cols[2].metric("High-priority unscheduled", len(snapshot["unscheduled_high"]))
    metric_cols[3].metric("Capacity gap", max(0, snapshot["capacity_gap"]))

    guardrail_cols = st.columns(4)
    guardrail_cols[0].metric("Planned this week", f"{week_planned_minutes} min")
    guardrail_cols[1].metric("Weekly capacity", f"{weekly_capacity_minutes} min")
    guardrail_cols[2].metric("Remaining", f"{remaining_week_capacity} min")
    guardrail_cols[3].metric("Overloaded days", len(overloaded_days))

    if remaining_week_capacity < 0:
        st.error(
            f"Weekly plan is over capacity by {abs(remaining_week_capacity)} minutes. "
            "Move or shorten blocks to protect realistic execution."
        )
    elif remaining_week_capacity <= daily_capacity_minutes // 2:
        st.warning("Weekly capacity is nearly full. Reserve some buffer for overrun and handoffs.")
    else:
        st.success("Weekly load is within configured capacity.")

    if overloaded_days:
        overloaded_labels = ", ".join(day.strftime("%a %b %d") for day in overloaded_days[:4])
        more_count = len(overloaded_days) - 4
        if more_count > 0:
            overloaded_labels = f"{overloaded_labels}, +{more_count} more"
        st.warning(
            f"Daily overload detected on: {overloaded_labels}. "
            f"Target per day is {daily_capacity_minutes} minutes."
        )
        if st.button("Rebalance this week", key=f"{panel_key}_rebalance_week", type="secondary"):
            proposed_moves = build_week_rebalance_moves(snapshot["upcoming"], week_days, daily_capacity_minutes)
            if not proposed_moves:
                st.info("No safe low-priority moves were found inside this week.")
            else:
                for move in proposed_moves:
                    task = move["task"]
                    update_task(
                        task["id"],
                        scheduled_date=move["target_day"],
                        scheduled_end_date=move["target_day"],
                        scheduled_time=task.get("scheduled_time"),
                        scheduled_minutes=task.get("scheduled_minutes"),
                    )
                st.success(f"Rebalanced {len(proposed_moves)} low-priority block(s) across this week.")
                st.rerun()

    week_controls = st.columns([1, 2, 1])
    with week_controls[0]:
        if st.button("Prev week", key=f"{panel_key}_prev_week"):
            st.session_state[week_key] = week_start - timedelta(days=7)
            st.rerun()
    with week_controls[1]:
        st.markdown(
            f"<div style='text-align:center; font-weight:700; margin-top:0.4rem;'>{week_start.strftime('%b %d')} - {(week_start + timedelta(days=6)).strftime('%b %d, %Y')}</div>",
            unsafe_allow_html=True,
        )
    with week_controls[2]:
        if st.button("Next week", key=f"{panel_key}_next_week"):
            st.session_state[week_key] = week_start + timedelta(days=7)
            st.rerun()

    jump_cols = st.columns([1.15, 1, 0.9, 0.9, 0.9])
    with jump_cols[0]:
        jump_target = st.date_input(
            "Jump to week of",
            value=week_start,
            key=f"{panel_key}_jump_week_of",
            help="Pick any date and the planner will jump to that week.",
        )
    with jump_cols[1]:
        if st.button("Go to week", key=f"{panel_key}_go_to_week"):
            st.session_state[week_key] = jump_target - timedelta(days=jump_target.weekday())
            st.rerun()
    with jump_cols[2]:
        if st.button("+2 weeks", key=f"{panel_key}_plus_2_weeks"):
            st.session_state[week_key] = week_start + timedelta(days=14)
            st.rerun()
    with jump_cols[3]:
        if st.button("+4 weeks", key=f"{panel_key}_plus_4_weeks"):
            st.session_state[week_key] = week_start + timedelta(days=28)
            st.rerun()
    with jump_cols[4]:
        if st.button("Today", key=f"{panel_key}_jump_today"):
            today = mountain_today()
            st.session_state[week_key] = today - timedelta(days=today.weekday())
            st.rerun()

    st.markdown('<div class="panel-title" style="margin-top:0.75rem;"><h3>Week Planner</h3><span>Move tasks into the week one day at a time</span></div>', unsafe_allow_html=True)

    st.markdown('<div class="panel-title" style="margin-top:0.5rem;"><h3>Week Calendar</h3><span>Persistent 7-day view of what is already scheduled</span></div>', unsafe_allow_html=True)
    planner_cols = st.columns(7)
    for index, day in enumerate(week_days):
        with planner_cols[index]:
            st.markdown(
                f"<div class='task-card' style='min-height: 14rem;'><div class='task-title'>{day.strftime('%a')}</div><div class='task-meta'><span class='pill'>{day.strftime('%b %d')}</span><span class='pill'>{len(scheduled_by_day[day])} scheduled</span><span class='pill'>{len(due_by_day[day])} due</span><span class='pill'>{scheduled_minutes_by_day[day]} / {daily_capacity_minutes} min</span></div>",
                unsafe_allow_html=True,
            )
            if scheduled_by_day[day]:
                for task in scheduled_by_day[day][:3]:
                    scheduled_time = task.get("scheduled_time")
                    time_label = scheduled_time.strftime("%I:%M %p").lstrip("0") if scheduled_time else "Any time"
                    span_label = f"{task['title']} · {time_label}" if scheduled_span_position(task, day) == "start" else None
                    st.markdown(render_span_block(task, day, label_text=span_label, compact=True), unsafe_allow_html=True)
                if len(scheduled_by_day[day]) > 3:
                    st.caption(f"+ {len(scheduled_by_day[day]) - 3} more")
                with st.expander(f"View all scheduled ({len(scheduled_by_day[day])})", expanded=False):
                    for task in scheduled_by_day[day]:
                        scheduled_time = task.get("scheduled_time")
                        time_label = scheduled_time.strftime("%I:%M %p").lstrip("0") if scheduled_time else "Any time"
                        minutes_label = f"{task.get('scheduled_minutes') or '-'} min"
                        st.markdown(
                            f"- **{task['title']}** · {task['priority'].title()} · {status_label(task.get('status', 'todo'))} · {time_label} · {minutes_label}",
                            unsafe_allow_html=True,
                        )
            else:
                st.caption("No blocks yet.")

            if due_by_day[day]:
                with st.expander(f"View all due ({len(due_by_day[day])})", expanded=False):
                    for task in due_by_day[day]:
                        if task.get("scheduled_date") == day:
                            schedule_note = "scheduled today"
                        elif task.get("scheduled_date"):
                            schedule_note = f"scheduled {task['scheduled_date']}"
                        else:
                            schedule_note = "not scheduled"
                        st.markdown(
                            f"- **{task['title']}** · {task['category']} · {task['priority'].title()} · {status_label(task.get('status', 'todo'))} · {schedule_note}",
                            unsafe_allow_html=True,
                        )
            st.markdown('</div>', unsafe_allow_html=True)

    if ranked_tasks:
        st.markdown('<div class="panel-title" style="margin-top:0.75rem;"><h3>Placement Controls</h3><span>Pin an unscheduled task into the week calendar</span></div>', unsafe_allow_html=True)
        selected_task_id = st.selectbox(
            "Task to place",
            [task["id"] for task in ranked_tasks],
            key=pin_task_key,
            format_func=lambda task_id: next(
                (
                    f"{task['title']} · {task['category']} · {task['priority'].title()} · {task.get('due_date') or 'No due date'}"
                    for task in ranked_tasks
                    if task["id"] == task_id
                ),
                str(task_id),
            ),
        )
        st.caption("Use the buttons below to pin the selected task into a day, then fine-tune it in the list panel.")
        placement_cols = st.columns(7)
        for index, day in enumerate(week_days):
            with placement_cols[index]:
                place_label = "Place here"
                if st.button(place_label, key=f"{panel_key}_place_day_{day.isoformat()}", disabled=not ranked_tasks):
                    target = next((task for task in ranked_tasks if task["id"] == selected_task_id), None)
                    if target:
                        update_task(
                            target["id"],
                            scheduled_date=day,
                            scheduled_time=schedule_time_default,
                            scheduled_minutes=default_duration,
                        )
                        st.success(f"Placed '{target['title']}' on {day.strftime('%a %b %d')}.")
                        st.rerun()
    else:
        st.markdown('<div class="empty-state">No unscheduled tasks are waiting for placement this week.</div>', unsafe_allow_html=True)

    left_col, right_col = st.columns([1.1, 0.9], gap="large")
    with left_col:
        st.markdown('<div class="panel-title"><h3>Draft Order</h3><span>Ranked according to the selected lens</span></div>', unsafe_allow_html=True)
        if ranked_tasks:
            selected_task_id = st.session_state.get(pin_task_key, ranked_tasks[0]["id"])
            pin_cols = st.columns([1, 1, 1])
            with pin_cols[0]:
                pin_date = st.date_input("Pin date", value=mountain_today(), key=f"{panel_key}_pin_date")
            with pin_cols[1]:
                pin_time = st.time_input("Pin time", value=schedule_time_default, key=f"{panel_key}_pin_time")
            with pin_cols[2]:
                pin_minutes = st.selectbox(
                    "Duration",
                    duration_options,
                    index=default_duration_index,
                    key=f"{panel_key}_pin_minutes",
                )
            if st.button("Pin selected task", key=f"{panel_key}_pin_selected", type="primary"):
                target = next((task for task in ranked_tasks if task["id"] == selected_task_id), None)
                if target:
                    update_task(
                        target["id"],
                        scheduled_date=pin_date,
                        scheduled_time=pin_time,
                        scheduled_minutes=pin_minutes,
                    )
                    st.success(f"Pinned '{target['title']}' to {pin_date} at {pin_time.strftime('%I:%M %p').lstrip('0')}.")
                    st.rerun()

            st.markdown('<div style="height: 0.6rem;"></div>', unsafe_allow_html=True)
            st.markdown('<div class="panel-title"><h3>Ranked items</h3><span>Quick actions on the next few unscheduled tasks</span></div>', unsafe_allow_html=True)
            for task in ranked_tasks:
                scheduled_label = format_schedule(task)
                st.markdown(
                    f"<div class='task-card'><div class='task-title'>{task['title']}</div><div class='task-meta'><span class='pill pill-category'>{task['category']}</span><span class='pill pill-priority-{task['priority']}'>Priority: {task['priority'].title()}</span><span class='pill'>{task.get('due_date') or 'No due date'}</span><span class='pill'>{scheduled_label}</span></div></div>",
                    unsafe_allow_html=True,
                )
                action_cols = st.columns(3)
                with action_cols[0]:
                    if st.button("Pin tomorrow", key=f"{panel_key}_pin_tomorrow_{task['id']}"):
                        update_task(
                            task["id"],
                            scheduled_date=mountain_today() + timedelta(days=1),
                            scheduled_time=schedule_time_default,
                            scheduled_minutes=default_duration,
                        )
                        st.success(f"Pinned '{task['title']}' for tomorrow.")
                        st.rerun()
                with action_cols[1]:
                    if task.get("status") != "in_progress" and st.button("Start", key=f"{panel_key}_start_{task['id']}"):
                        set_task_status(task["id"], "in_progress")
                        st.success(f"'{task['title']}' moved to In Progress.")
                        st.rerun()
                with action_cols[2]:
                    if st.button("Clear schedule", key=f"{panel_key}_clear_{task['id']}"):
                        update_task(task["id"], scheduled_date=None, scheduled_time=None, scheduled_minutes=None)
                        st.success(f"Cleared the schedule for '{task['title']}'.")
                        st.rerun()
        else:
            st.markdown('<div class="empty-state">No unscheduled tasks need ordering right now.</div>', unsafe_allow_html=True)
    with right_col:
        st.markdown('<div class="panel-title"><h3>Scheduling Rules</h3><span>Keep the day coherent</span></div>', unsafe_allow_html=True)
        st.markdown(
            "<div class='ai-list'>"
            "<li>Protect the first two hours for the highest-value work.</li>"
            "<li>Keep clinic items contiguous when the selected lens is clinic-first.</li>"
            "<li>Leave one buffer block each day for notes, handoffs, or overruns.</li>"
            "</div>",
            unsafe_allow_html=True,
        )
        if snapshot["unscheduled_high"]:
            st.caption(f"Top unscheduled high-priority task: {snapshot['unscheduled_high'][0]['title']}")

        st.markdown('<div style="height: 0.9rem;"></div>', unsafe_allow_html=True)
        st.markdown('<div class="panel-title"><h3>Schedule Template</h3><span>Add events, dinners, travel, vacation, and clinic blocks to the calendar</span></div>', unsafe_allow_html=True)
        personal_template_key = f"{panel_key}_personal_template"
        if personal_template_key not in st.session_state:
            st.session_state[personal_template_key] = "blank"
        personal_templates = personal_schedule_templates()
        st.selectbox(
            "Template",
            list(personal_templates.keys()),
            key=personal_template_key,
            format_func=lambda key: personal_templates[key]["label"],
            on_change=apply_personal_schedule_template_from_state,
            args=(f"{panel_key}_personal_capture", personal_template_key),
        )
        active_personal_template = st.session_state.get(personal_template_key, "blank")
        is_vacation_template = active_personal_template == "vacation"
        with st.form(f"{panel_key}_personal_capture", clear_on_submit=True):
            personal_title = st.text_input("Title", key=f"{panel_key}_personal_capture_title")
            personal_description = st.text_area("Notes", height=90, key=f"{panel_key}_personal_capture_description")
            block_category = st.selectbox(
                "Category",
                ["Personal", "Clinic"],
                key=f"{panel_key}_personal_capture_category",
            )
            personal_date = st.date_input("Start date", value=mountain_today(), key=f"{panel_key}_personal_capture_scheduled_date")
            all_day_key = f"{panel_key}_personal_capture_all_day"
            multi_day_key = f"{panel_key}_personal_capture_multi_day"
            if is_vacation_template:
                st.session_state[all_day_key] = True
                st.session_state[multi_day_key] = True

            personal_all_day = st.checkbox(
                "Treat as all-day block",
                key=all_day_key,
                disabled=is_vacation_template,
            )
            if multi_day_key not in st.session_state:
                st.session_state[multi_day_key] = False
            personal_multi_day = st.checkbox(
                "Block multiple days",
                key=multi_day_key,
                disabled=is_vacation_template,
            )
            if is_vacation_template:
                st.caption("Vacation mode uses all-day blocks and a required date range.")
                personal_time = time(8, 0)
                personal_minutes = 480
            else:
                personal_time = st.time_input(
                    "Start time",
                    value=personal_schedule_templates()[st.session_state[personal_template_key]]["scheduled_time"],
                    disabled=personal_all_day,
                    key=f"{panel_key}_personal_capture_scheduled_time",
                )
                personal_minutes = st.selectbox(
                    "Duration (minutes)",
                    [15, 30, 45, 60, 90, 120, 180, 240, 480],
                    index=[15, 30, 45, 60, 90, 120, 180, 240, 480].index(
                        personal_schedule_templates()[st.session_state[personal_template_key]]["scheduled_minutes"]
                    ),
                    disabled=personal_all_day,
                    key=f"{panel_key}_personal_capture_scheduled_minutes",
                )
            personal_priority = st.selectbox(
                "Priority",
                ["high", "medium", "low"],
                index=["high", "medium", "low"].index(personal_schedule_templates()[st.session_state[personal_template_key]]["priority"]),
                format_func=lambda value: value.title(),
                key=f"{panel_key}_personal_capture_priority",
            )
            if personal_multi_day:
                min_end_date = personal_date + timedelta(days=1) if is_vacation_template else personal_date
                personal_end_default = st.session_state.get(f"{panel_key}_personal_capture_scheduled_end_date", min_end_date)
                if personal_end_default < min_end_date:
                    personal_end_default = min_end_date
                personal_end_date = st.date_input(
                    "End date",
                    value=personal_end_default,
                    min_value=min_end_date,
                    key=f"{panel_key}_personal_capture_scheduled_end_date",
                )
            else:
                personal_end_date = personal_date
            submit_label = "Add vacation range" if is_vacation_template else "Add to schedule"
            personal_submit = st.form_submit_button(submit_label, type="primary")

        if personal_submit:
            if not personal_title.strip():
                st.warning("Add a title for the block.")
            else:
                scheduled_time = time(8, 0) if personal_all_day else personal_time
                scheduled_minutes = 480 if personal_all_day else int(personal_minutes)
                add_task(
                    personal_title.strip(),
                    personal_description.strip(),
                    block_category,
                    personal_priority,
                    personal_date,
                    scheduled_date=personal_date,
                    scheduled_end_date=personal_end_date if personal_multi_day else personal_date,
                    scheduled_time=scheduled_time,
                    scheduled_minutes=scheduled_minutes,
                    recurrence_rule=None,
                    recurrence_interval=1,
                )
                st.success(f"Added {block_category} block: {personal_title.strip()} starting {personal_date.strftime('%b %d')}.")
                st.rerun()

        all_scheduled = sorted(
            [task for task in active_tasks if task.get("scheduled_date") and task.get("scheduled_time")],
            key=lambda task: (task.get("scheduled_date") or date.max, task.get("scheduled_time") or time(23, 59)),
        )[:6]
        if all_scheduled:
            st.markdown('<div class="panel-title" style="margin-top:0.8rem;"><h3>Upcoming scheduled blocks</h3><span>What is already on the calendar</span></div>', unsafe_allow_html=True)
            for task in all_scheduled:
                cat_badge = f"<span style='font-size:0.72rem;color:#888;'>[{task.get('category','—')}]</span>"
                st.markdown(
                    f"- <strong>{task['title']}</strong> {cat_badge} · {task.get('scheduled_date')} · {format_schedule(task)} · {task.get('scheduled_minutes') or '-'} min",
                    unsafe_allow_html=True,
                )
        else:
            st.markdown('<div class="empty-state">No scheduled blocks yet. Use the template above to add events, trips, appointments, or clinic blocks.</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)


def render_family_schedule_panel(active_tasks, app_settings, panel_key="family_schedule"):
    raw_family_items = list((app_settings or {}).get("family_schedule_items") or [])
    raw_family_goals = list((app_settings or {}).get("family_goals") or [])
    raw_family_weekly_notes = list((app_settings or {}).get("family_weekly_notes") or [])
    raw_family_notes = str((app_settings or {}).get("family_notes") or "")
    raw_family_notes_updated_at = str((app_settings or {}).get("family_notes_updated_at") or "")
    raw_home_routine_checklists = dict((app_settings or {}).get("home_routine_checklists") or {})
    family_items = normalize_family_schedule_items(raw_family_items)
    family_goals = normalize_family_goals(raw_family_goals, reference_date=mountain_today())
    family_weekly_notes = normalize_family_weekly_notes(raw_family_weekly_notes)

    def _save_family_state(
        updated_raw_items=None,
        updated_raw_goals=None,
        updated_raw_weekly_notes=None,
        updated_family_notes=None,
        updated_family_notes_updated_at=None,
        updated_home_routine_checklists=None,
    ):
        save_app_settings(
            {
                **(app_settings or {}),
                "family_schedule_items": updated_raw_items if updated_raw_items is not None else raw_family_items,
                "family_goals": updated_raw_goals if updated_raw_goals is not None else raw_family_goals,
                "family_weekly_notes": updated_raw_weekly_notes if updated_raw_weekly_notes is not None else raw_family_weekly_notes,
                "family_notes": updated_family_notes if updated_family_notes is not None else raw_family_notes,
                "family_notes_updated_at": (
                    updated_family_notes_updated_at
                    if updated_family_notes_updated_at is not None
                    else raw_family_notes_updated_at
                ),
                "home_routine_checklists": (
                    updated_home_routine_checklists
                    if updated_home_routine_checklists is not None
                    else raw_home_routine_checklists
                ),
            }
        )

    def _save_family_items(updated_raw_items):
        _save_family_state(updated_raw_items=updated_raw_items)

    def _save_family_goals(updated_raw_goals):
        _save_family_state(updated_raw_goals=updated_raw_goals)

    def _save_family_weekly_notes(updated_raw_weekly_notes):
        _save_family_state(updated_raw_weekly_notes=updated_raw_weekly_notes)

    def _save_family_notes(notes_text):
        _save_family_state(
            updated_family_notes=notes_text,
            updated_family_notes_updated_at=datetime.now(MOUNTAIN_TIMEZONE).isoformat(timespec="seconds"),
        )

    def _save_home_routine_checklists(updated_home_routine_checklists):
        _save_family_state(updated_home_routine_checklists=updated_home_routine_checklists)

    def _apply_update(item, updates):
        source_index = item.get("source_index")
        if source_index is None or source_index >= len(raw_family_items):
            return False
        if not isinstance(raw_family_items[source_index], dict):
            return False
        raw_family_items[source_index].update(updates)
        _save_family_items(raw_family_items)
        return True

    def _apply_goal_update(goal, updates):
        source_index = goal.get("source_index")
        if source_index is None or source_index >= len(raw_family_goals):
            return False
        if not isinstance(raw_family_goals[source_index], dict):
            return False
        raw_family_goals[source_index].update(updates)
        _save_family_goals(raw_family_goals)
        return True

    week_key = f"{panel_key}_week_anchor"
    if week_key not in st.session_state:
        today = mountain_today()
        st.session_state[week_key] = today - timedelta(days=today.weekday())

    week_start = st.session_state[week_key]
    scheduled_by_day = {}
    for task in active_tasks:
        for task_day in scheduled_date_range(task):
            scheduled_by_day.setdefault(task_day, []).append(task)

    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Family Schedule</h3><span>Dedicated space for appointments, trips, camps, and family logistics</span></div>', unsafe_allow_html=True)

    week_controls = st.columns([1, 2, 1])
    with week_controls[0]:
        if st.button("Prev week", key=f"{panel_key}_prev_week"):
            st.session_state[week_key] = week_start - timedelta(days=7)
            st.rerun()
    with week_controls[1]:
        st.markdown(
            f"<div style='text-align:center; font-weight:700; margin-top:0.4rem;'>{week_start.strftime('%b %d')} - {(week_start + timedelta(days=13)).strftime('%b %d, %Y')}</div>",
            unsafe_allow_html=True,
        )
    with week_controls[2]:
        if st.button("Next week", key=f"{panel_key}_next_week"):
            st.session_state[week_key] = week_start + timedelta(days=7)
            st.rerun()

    family_summary = weekly_family_schedule_summary(family_items, end_day=week_start, window_days=14)
    family_conflict_count = sum(1 for item in family_summary["upcoming_items"] if scheduled_by_day.get(item["start_date"]))
    family_metrics = st.columns(4)
    family_metrics[0].metric("Upcoming", family_summary["upcoming_count"])
    family_metrics[1].metric("Appointments", family_summary["appointment_count"])
    family_metrics[2].metric("Trips/Camps", family_summary["trip_count"] + family_summary["camp_count"])
    family_metrics[3].metric("Conflict days", family_conflict_count)
    st.caption(
        f"Recurring occurrences: {family_summary['recurring_count']} · "
        f"Items with checklists: {family_summary['items_with_checklists']} · "
        f"Weekend items: {family_summary['weekend_count']}"
    )

    st.markdown('<div class="panel-title" style="margin-top:0.8rem;"><h3>Family Notes</h3><span>Shared notes, reminders, and planning context</span></div>', unsafe_allow_html=True)
    family_notes_key = f"{panel_key}_family_notes_text"
    if family_notes_key not in st.session_state:
        st.session_state[family_notes_key] = raw_family_notes
    st.text_area(
        "Notes",
        key=family_notes_key,
        height=140,
        placeholder="Capture family logistics notes, ideas, and follow-ups...",
    )
    notes_cols = st.columns(2)
    with notes_cols[0]:
        if st.button("Save family notes", key=f"{panel_key}_save_family_notes", type="secondary"):
            _save_family_notes(str(st.session_state.get(family_notes_key) or "").strip())
            st.success("Family notes saved.")
            st.rerun()
    with notes_cols[1]:
        if raw_family_notes_updated_at:
            st.caption(f"Last saved: {raw_family_notes_updated_at}")

    family_goal_summary = family_goal_dashboard_summary(family_goals)
    today_value = mountain_today()
    home_routine_checklists = normalize_home_routine_checklists(raw_home_routine_checklists, day_value=today_value)
    if home_routine_checklists != raw_home_routine_checklists:
        _save_home_routine_checklists(home_routine_checklists)

    st.markdown('<div class="panel-title" style="margin-top:0.8rem;"><h3>Minimum Home Routines</h3><span>Persistent checklists with automatic resets by cadence</span></div>', unsafe_allow_html=True)
    routine_cols = st.columns(3)
    home_routine_updated = False
    for col_index, template in enumerate(MINIMUM_HOME_ROUTINE_GOAL_TEMPLATES):
        cadence = str(template.get("cadence") or "").strip().lower()
        cadence_title = str(template.get("title") or cadence.title()).strip()
        cadence_items = [str(item).strip() for item in (template.get("items") or []) if str(item).strip()]
        cadence_state = home_routine_checklists.get(cadence) or {
            "period_key": home_routine_period_key(cadence, today_value),
            "completed_items": [],
            "updated_at": "",
        }
        completed_items = list(cadence_state.get("completed_items") or [])
        completed_lookup = set(completed_items)

        with routine_cols[col_index % len(routine_cols)]:
            st.markdown(f"**{cadence_title}**")
            st.caption(str(template.get("reset_text") or ""))

            selected_items = []
            for item_index, item_text in enumerate(cadence_items):
                checkbox_key = f"{panel_key}_home_routine_{cadence}_{cadence_state['period_key']}_{item_index}"
                if checkbox_key not in st.session_state:
                    st.session_state[checkbox_key] = item_text in completed_lookup
                if st.checkbox(item_text, key=checkbox_key):
                    selected_items.append(item_text)

            st.caption(f"Progress: {len(selected_items)}/{len(cadence_items)}")
            if selected_items != completed_items:
                home_routine_checklists[cadence] = {
                    "period_key": cadence_state["period_key"],
                    "completed_items": selected_items,
                    "updated_at": datetime.now(MOUNTAIN_TIMEZONE).isoformat(timespec="seconds"),
                }
                home_routine_updated = True

    if home_routine_updated:
        _save_home_routine_checklists(home_routine_checklists)

    timeline_days = [today_value + timedelta(days=offset) for offset in range(4)]
    timeline_by_day = {
        day: [item for item in family_summary["upcoming_items"] if item.get("start_date") == day]
        for day in timeline_days
    }
    st.markdown('<div class="panel-title" style="margin-top:0.8rem;"><h3>Daily Timeline</h3><span>Today + next 3 days</span></div>', unsafe_allow_html=True)
    timeline_cols = st.columns(4)
    for idx, day_value in enumerate(timeline_days):
        with timeline_cols[idx]:
            day_items = sorted(
                timeline_by_day.get(day_value, []),
                key=lambda item: (item.get("start_time") or time(23, 59), priority_rank(item.get("priority"))),
            )
            st.markdown(f"**{day_value.strftime('%a %b %d')}**")
            if len(day_items) > 1:
                st.caption("Conflict risk day")
            if not day_items:
                st.caption("No family events")
            for item in day_items[:4]:
                tlabel = item.get("start_time").strftime("%I:%M %p").lstrip("0") if item.get("start_time") else "All day"
                member = item.get("family_member") or "Family"
                st.markdown(f"- {tlabel} · {item.get('title')} ({member})")

    st.markdown('<div class="panel-title" style="margin-top:0.8rem;"><h3>Who Is Where</h3><span>Next 7 days by family member</span></div>', unsafe_allow_html=True)
    board_window_end = today_value + timedelta(days=6)
    board_items = [
        item
        for item in family_summary["upcoming_items"]
        if item.get("start_date") and today_value <= item["start_date"] <= board_window_end
    ]
    by_member = {}
    for item in board_items:
        member = item.get("family_member") or "Family"
        by_member.setdefault(member, []).append(item)
    if by_member:
        member_cols = st.columns(min(3, len(by_member)))
        for idx, member in enumerate(sorted(by_member.keys())):
            with member_cols[idx % len(member_cols)]:
                st.markdown(f"**{member}**")
                for item in sorted(by_member[member], key=lambda value: (value["start_date"], value.get("start_time") or time(23, 59)))[:6]:
                    st.markdown(f"- {item['start_date'].strftime('%a %b %d')}: {item.get('title')}")
    else:
        st.caption("No family items in the next 7 days.")

    st.markdown('<div class="panel-title" style="margin-top:0.8rem;"><h3>Reminder Engine</h3><span>What needs attention now</span></div>', unsafe_allow_html=True)
    reminder_rows = []
    for item in family_summary["upcoming_items"]:
        start_day = item.get("start_date")
        if not start_day:
            continue
        item_type = str(item.get("item_type") or "").lower()
        reminder_offsets = [1]
        if any(keyword in item_type for keyword in ("trip", "travel", "camp", "camping")):
            reminder_offsets = [2, 1]
        elif "appointment" in item_type:
            reminder_offsets = [1, 0]
        for offset in reminder_offsets:
            remind_on = start_day - timedelta(days=offset)
            if remind_on <= today_value:
                reminder_rows.append(
                    {
                        "item": item,
                        "remind_on": remind_on,
                        "offset": offset,
                    }
                )

    reminder_rows.sort(key=lambda row: (row["remind_on"], row["item"].get("start_date"), row["item"].get("title")))
    if reminder_rows:
        for row in reminder_rows[:8]:
            item = row["item"]
            due_label = "today" if row["remind_on"] == today_value else f"{abs((today_value - row['remind_on']).days)}d overdue"
            lead_label = "same-day" if row["offset"] == 0 else f"T-{row['offset']}"
            st.markdown(
                f"- **{item.get('title')}** · {item.get('item_type')} · starts {item.get('start_date')} · {lead_label} reminder ({due_label})"
            )
    else:
        st.caption("No reminder actions due right now.")

    family_conflict_days = {}
    for item in family_summary["upcoming_items"]:
        family_conflict_days.setdefault(item["start_date"], []).append(item)
    family_conflict_days = {day: items for day, items in family_conflict_days.items() if len(items) > 1}

    if family_conflict_days:
        st.markdown('<div class="panel-title" style="margin-top:0.8rem;"><h3>Conflict Solver</h3><span>Fast actions for overlap days</span></div>', unsafe_allow_html=True)
        for day_value, day_items in list(sorted(family_conflict_days.items()))[:3]:
            st.markdown(f"**{day_value.strftime('%a %b %d')}** · {len(day_items)} family items")
            candidate = sorted(
                day_items,
                key=lambda item: (
                    0 if not item.get("is_recurring_occurrence") else 1,
                    -priority_rank(item.get("priority")),
                    item.get("start_time") or time(23, 59),
                ),
            )[0]
            st.caption(f"Suggested move: {candidate.get('title')} to next day")
            action_cols = st.columns(2)
            with action_cols[0]:
                if st.button("Move suggested +1 day", key=f"{panel_key}_conflict_move_{day_value.isoformat()}_{candidate.get('item_id')}"):
                    new_start = candidate.get("start_date") + timedelta(days=1)
                    span_days = max(0, (candidate.get("end_date") - candidate.get("start_date")).days)
                    new_end = new_start + timedelta(days=span_days)
                    if _apply_update(candidate, {"start_date": new_start, "end_date": new_end}):
                        st.success("Suggested event moved to next day.")
                        st.rerun()
            with action_cols[1]:
                if st.button("Create prep task", key=f"{panel_key}_conflict_prep_{day_value.isoformat()}_{candidate.get('item_id')}"):
                    prep_due = max(today_value, candidate.get("start_date") - timedelta(days=1))
                    add_task(
                        f"Prep: {candidate.get('title')}",
                        candidate.get("notes") or "Family prep action",
                        "Personal",
                        "medium",
                        prep_due,
                    )
                    st.success("Prep task added to Personal tasks.")
                    st.rerun()

    with st.form(f"{panel_key}_item_form", clear_on_submit=True):
        family_title = st.text_input("Event title")
        family_type = st.selectbox(
            "Type",
            ["Appointment", "Trip", "Sports camp", "Camping", "Tournament", "Travel", "Other"],
            index=0,
        )
        family_member = st.text_input("Family member / group", placeholder="Sam, kids, whole family")
        family_date = st.date_input("Start date", value=week_start, key=f"{panel_key}_start_date")
        family_multi_day = st.checkbox("Multi-day item", key=f"{panel_key}_multi_day")
        if family_multi_day:
            family_end_min = family_date + timedelta(days=1)
            family_end_default = st.session_state.get(f"{panel_key}_end_date", family_end_min)
            if family_end_default < family_end_min:
                family_end_default = family_end_min
            family_end_date = st.date_input(
                "End date",
                value=family_end_default,
                min_value=family_end_min,
                key=f"{panel_key}_end_date",
            )
        else:
            family_end_date = family_date
        family_timed = st.checkbox(
            "Timed event",
            value=family_type == "Appointment",
            key=f"{panel_key}_timed",
        )
        if family_timed and not family_multi_day:
            family_time = st.time_input("Start time", value=time(9, 0), key=f"{panel_key}_time")
        else:
            family_time = None
        family_location = st.text_input("Location", placeholder="Clinic, airport, campground, field")
        family_priority = st.selectbox("Priority", ["high", "medium", "low"], index=1)
        recurrence_mode = st.selectbox(
            "Repeats",
            ["Does not repeat", "Daily", "Weekly", "Monthly", "Yearly"],
            index=0,
        )
        if recurrence_mode != "Does not repeat":
            recurrence_interval = st.number_input("Repeat every", min_value=1, max_value=24, value=1, step=1)
            recurrence_end_enabled = st.checkbox("Set recurrence end date", value=False)
            recurrence_end_date = st.date_input(
                "Repeat until",
                value=family_date + timedelta(days=90),
                min_value=family_date,
                disabled=not recurrence_end_enabled,
                key=f"{panel_key}_recurrence_end_date",
            )
        else:
            recurrence_interval = 1
            recurrence_end_enabled = False
            recurrence_end_date = None

        template_items = family_checklist_template(family_type)
        apply_template_checklist = st.checkbox("Apply checklist template", value=True)
        if template_items:
            st.caption("Template checklist: " + " | ".join(template_items))
        checklist_extra = st.text_area(
            "Additional checklist items",
            height=70,
            placeholder="One item per line",
        )
        family_notes = st.text_area("Notes", height=80, placeholder="Packing list, who is attending, confirmation details...")
        family_submit = st.form_submit_button("Add family item", type="primary")

    if family_submit:
        if not family_title.strip():
            st.warning("Add an event title before saving.")
        else:
            extra_checklist_items = [line.strip() for line in checklist_extra.splitlines() if line.strip()]
            combined_checklist = []
            if apply_template_checklist:
                combined_checklist.extend(template_items)
            combined_checklist.extend(extra_checklist_items)
            deduped_checklist = []
            seen_items = set()
            for item in combined_checklist:
                normalized = item.lower()
                if normalized in seen_items:
                    continue
                seen_items.add(normalized)
                deduped_checklist.append(item)

            updated_family_items = list(raw_family_items)
            updated_family_items.append(
                {
                    "item_id": uuid4().hex,
                    "title": family_title.strip(),
                    "item_type": family_type,
                    "family_member": family_member.strip(),
                    "start_date": family_date,
                    "end_date": family_end_date if family_multi_day else family_date,
                    "start_time": family_time if family_timed and not family_multi_day else None,
                    "priority": family_priority,
                    "location": family_location.strip(),
                    "notes": family_notes.strip(),
                    "status": "planned",
                    "all_day": (not family_timed) or family_multi_day,
                    "checklist_items": deduped_checklist,
                    "recurrence_rule": {
                        "Does not repeat": "none",
                        "Daily": "daily",
                        "Weekly": "weekly",
                        "Monthly": "monthly",
                        "Yearly": "yearly",
                    }[recurrence_mode],
                    "recurrence_interval": int(recurrence_interval),
                    "recurrence_end_date": recurrence_end_date if recurrence_end_enabled else None,
                }
            )
            _save_family_items(updated_family_items)
            st.success("Family schedule item saved.")
            st.rerun()

    family_insight_key = f"{panel_key}_{week_start.isoformat()}_family_weekly_briefing"
    family_insight_error_key = f"{panel_key}_{week_start.isoformat()}_family_weekly_briefing_error"
    if st.button("Generate Weekly Family Briefing", key=f"{panel_key}_generate_family_briefing", type="secondary"):
        family_ai_summary = dict(family_summary)
        family_ai_summary["conflict_count"] = family_conflict_count
        insight_text, insight_error = generate_family_weekly_briefing(
            family_ai_summary,
            family_summary["upcoming_items"],
        )
        st.session_state[family_insight_key] = insight_text
        st.session_state[family_insight_error_key] = insight_error
    if st.session_state.get(family_insight_error_key):
        st.warning(st.session_state[family_insight_error_key])
    if st.session_state.get(family_insight_key):
        st.markdown(st.session_state[family_insight_key])

    st.markdown('<div class="panel-title" style="margin-top:0.8rem;"><h3>Weekly Planning Mode</h3><span>Prep priorities for the upcoming week</span></div>', unsafe_allow_html=True)
    planning_actions = []
    for item in family_summary["upcoming_items"][:12]:
        if item.get("checklist_items"):
            planning_actions.append(
                f"{item.get('start_date').strftime('%a %b %d')}: Prep {item.get('title')} ({len(item.get('checklist_items'))} checklist items)"
            )
        elif any(keyword in str(item.get("item_type") or "").lower() for keyword in ("trip", "travel", "camp", "appointment")):
            planning_actions.append(
                f"{item.get('start_date').strftime('%a %b %d')}: Add prep checklist for {item.get('title')}"
            )
    if planning_actions:
        for line in planning_actions[:6]:
            st.markdown(f"- {line}")
    else:
        st.caption("No prep-heavy items identified for the next two weeks.")

    if family_summary["upcoming_items"]:
        st.markdown('<div class="panel-title" style="margin-top:0.8rem;"><h3>Upcoming family items</h3><span>Next 14 days</span></div>', unsafe_allow_html=True)
        for item in family_summary["upcoming_items"][:10]:
            date_range = item["start_date"].strftime("%b %d")
            if item.get("end_date") and item["end_date"] != item["start_date"]:
                date_range = f"{date_range} - {item['end_date'].strftime('%b %d')}"
            time_label = item["start_time"].strftime("%I:%M %p").lstrip("0") if item.get("start_time") else "All day"
            family_member_label = item.get("family_member") or "Family"
            recurrence_label = ""
            if item.get("is_recurring_occurrence"):
                recurrence_label = " · recurring"
            st.markdown(
                f"- <strong>{item['title']}</strong> · {item.get('item_type')} · {date_range} · {time_label} · {family_member_label}{recurrence_label}",
                unsafe_allow_html=True,
            )
            if item.get("location"):
                st.caption(f"Location: {item.get('location')}")
            if item.get("notes"):
                st.caption(item.get("notes"))
            if item.get("checklist_items"):
                checklist_preview = ", ".join(item.get("checklist_items")[:4])
                st.caption(f"Checklist: {checklist_preview}")
    else:
        st.markdown('<div class="empty-state">No family items yet. Add appointments, trips, camps, or camping plans here.</div>', unsafe_allow_html=True)

    st.markdown('<div class="panel-title" style="margin-top:0.8rem;"><h3>Manage Family Items</h3><span>Edit, complete, cancel, or delete</span></div>', unsafe_allow_html=True)
    if family_items:
        for item in family_items[:18]:
            label_date = item.get("start_date").strftime("%b %d") if item.get("start_date") else "No date"
            with st.expander(f"{item.get('title')} · {label_date} · {item.get('status')}", expanded=False):
                edit_title = st.text_input("Title", value=item.get("title") or "", key=f"{panel_key}_edit_title_{item.get('item_id')}")
                edit_member = st.text_input("Family member", value=item.get("family_member") or "", key=f"{panel_key}_edit_member_{item.get('item_id')}")
                edit_type = st.selectbox(
                    "Type",
                    ["Appointment", "Trip", "Sports camp", "Camping", "Tournament", "Travel", "Other"],
                    index=["Appointment", "Trip", "Sports camp", "Camping", "Tournament", "Travel", "Other"].index(item.get("item_type")) if item.get("item_type") in ["Appointment", "Trip", "Sports camp", "Camping", "Tournament", "Travel", "Other"] else 0,
                    key=f"{panel_key}_edit_type_{item.get('item_id')}",
                )
                edit_cols = st.columns(3)
                with edit_cols[0]:
                    edit_start = st.date_input("Start", value=item.get("start_date") or today_value, key=f"{panel_key}_edit_start_{item.get('item_id')}")
                with edit_cols[1]:
                    edit_end = st.date_input("End", value=item.get("end_date") or edit_start, min_value=edit_start, key=f"{panel_key}_edit_end_{item.get('item_id')}")
                with edit_cols[2]:
                    timed_default = bool(item.get("start_time")) and not bool(item.get("all_day"))
                    edit_timed = st.checkbox("Timed", value=timed_default, key=f"{panel_key}_edit_timed_{item.get('item_id')}")
                    edit_time = st.time_input("Time", value=item.get("start_time") or time(9, 0), disabled=not edit_timed, key=f"{panel_key}_edit_time_{item.get('item_id')}")

                edit_priority = st.selectbox("Priority", ["high", "medium", "low"], index=["high", "medium", "low"].index(item.get("priority")) if item.get("priority") in ["high", "medium", "low"] else 1, key=f"{panel_key}_edit_priority_{item.get('item_id')}")
                edit_location = st.text_input("Location", value=item.get("location") or "", key=f"{panel_key}_edit_location_{item.get('item_id')}")
                edit_notes = st.text_area("Notes", value=item.get("notes") or "", key=f"{panel_key}_edit_notes_{item.get('item_id')}", height=80)
                edit_checklist_text = st.text_area(
                    "Checklist items (one per line)",
                    value="\n".join(item.get("checklist_items") or []),
                    key=f"{panel_key}_edit_checklist_{item.get('item_id')}",
                    height=80,
                )
                recurrence_options = ["none", "daily", "weekly", "monthly", "yearly"]
                edit_recurrence_rule = st.selectbox(
                    "Recurrence",
                    recurrence_options,
                    index=recurrence_options.index(item.get("recurrence_rule")) if item.get("recurrence_rule") in recurrence_options else 0,
                    key=f"{panel_key}_edit_recurrence_rule_{item.get('item_id')}",
                )
                edit_recurrence_interval = st.number_input(
                    "Recurrence interval",
                    min_value=1,
                    max_value=24,
                    value=max(1, safe_int(item.get("recurrence_interval"), 1)),
                    key=f"{panel_key}_edit_recurrence_interval_{item.get('item_id')}",
                    disabled=edit_recurrence_rule == "none",
                )
                edit_recurrence_end_enabled = st.checkbox(
                    "Set recurrence end",
                    value=bool(item.get("recurrence_end_date")),
                    key=f"{panel_key}_edit_recurrence_end_enabled_{item.get('item_id')}",
                    disabled=edit_recurrence_rule == "none",
                )
                edit_recurrence_end = st.date_input(
                    "Recurrence end date",
                    value=item.get("recurrence_end_date") or edit_start,
                    min_value=edit_start,
                    key=f"{panel_key}_edit_recurrence_end_{item.get('item_id')}",
                    disabled=(edit_recurrence_rule == "none") or (not edit_recurrence_end_enabled),
                )

                status_cols = st.columns(4)
                if status_cols[0].button("Save changes", key=f"{panel_key}_save_{item.get('item_id')}"):
                    updated_checklist = [line.strip() for line in edit_checklist_text.splitlines() if line.strip()]
                    if _apply_update(
                        item,
                        {
                            "title": edit_title.strip(),
                            "family_member": edit_member.strip(),
                            "item_type": edit_type,
                            "start_date": edit_start,
                            "end_date": edit_end,
                            "start_time": edit_time if edit_timed else None,
                            "all_day": not edit_timed,
                            "priority": edit_priority,
                            "location": edit_location.strip(),
                            "notes": edit_notes.strip(),
                            "checklist_items": updated_checklist,
                            "recurrence_rule": edit_recurrence_rule,
                            "recurrence_interval": int(edit_recurrence_interval),
                            "recurrence_end_date": edit_recurrence_end if edit_recurrence_end_enabled and edit_recurrence_rule != "none" else None,
                        },
                    ):
                        st.success("Family item updated.")
                        st.rerun()
                if status_cols[1].button("Mark completed", key=f"{panel_key}_complete_{item.get('item_id')}"):
                    if _apply_update(item, {"status": "completed"}):
                        st.success("Marked as completed.")
                        st.rerun()
                if status_cols[2].button("Cancel", key=f"{panel_key}_cancel_{item.get('item_id')}"):
                    if _apply_update(item, {"status": "canceled"}):
                        st.success("Marked as canceled.")
                        st.rerun()
                if status_cols[3].button("Delete", key=f"{panel_key}_delete_{item.get('item_id')}"):
                    source_index = item.get("source_index")
                    if source_index is not None and source_index < len(raw_family_items):
                        updated_raw = [entry for idx, entry in enumerate(raw_family_items) if idx != source_index]
                        _save_family_items(updated_raw)
                        st.success("Family item deleted.")
                        st.rerun()
    else:
        st.caption("No saved family items to manage yet.")

    st.markdown('<div class="panel-title" style="margin-top:0.8rem;"><h3>Family Goals</h3><span>Set shared targets and track weekly progress</span></div>', unsafe_allow_html=True)
    goal_metrics = st.columns(4)
    goal_metrics[0].metric("Active goals", len(family_goal_summary["active_goals"]))
    goal_metrics[1].metric("On track", len(family_goal_summary["on_track_goals"]))
    goal_metrics[2].metric("This week logs", family_goal_summary["week_checkins"])
    goal_metrics[3].metric("Best streak", family_goal_summary["best_streak"])

    family_digest_key = f"{panel_key}_{week_start.isoformat()}_family_weekly_digest"
    family_digest_error_key = f"{panel_key}_{week_start.isoformat()}_family_weekly_digest_error"
    if st.button("Generate Family Weekly Digest", key=f"{panel_key}_generate_family_digest", type="secondary"):
        digest_schedule_summary = dict(family_summary)
        digest_schedule_summary["conflict_count"] = family_conflict_count
        digest_goal_summary = {
            "active_goal_count": len(family_goal_summary["active_goals"]),
            "on_track_count": len(family_goal_summary["on_track_goals"]),
            "attention_count": len(family_goal_summary["attention_goals"]),
            "week_checkins": family_goal_summary["week_checkins"],
            "best_streak": family_goal_summary["best_streak"],
        }
        digest_text, digest_error = generate_family_weekly_digest(
            digest_schedule_summary,
            digest_goal_summary,
            family_summary["upcoming_items"],
            family_goal_summary["active_goals"],
        )
        st.session_state[family_digest_key] = digest_text
        st.session_state[family_digest_error_key] = digest_error
    if st.session_state.get(family_digest_error_key):
        st.warning(st.session_state[family_digest_error_key])
    if st.session_state.get(family_digest_key):
        st.markdown(st.session_state[family_digest_key])
        digest_action_cols = st.columns(2)
        with digest_action_cols[0]:
            if st.button("Save digest to weekly notes", key=f"{panel_key}_save_family_digest"):
                digest_text_to_save = str(st.session_state.get(family_digest_key) or "").strip()
                if not digest_text_to_save:
                    st.warning("Generate a digest before saving.")
                else:
                    updated_notes = []
                    for entry in raw_family_weekly_notes:
                        if not isinstance(entry, dict):
                            continue
                        if parse_date_value(entry.get("week_start")) == week_start:
                            continue
                        updated_notes.append(entry)

                    updated_notes.append(
                        {
                            "note_id": uuid4().hex,
                            "week_start": week_start,
                            "digest_text": digest_text_to_save,
                            "saved_at": datetime.now(MOUNTAIN_TIMEZONE).isoformat(timespec="seconds"),
                        }
                    )
                    _save_family_weekly_notes(updated_notes)
                    st.success("Digest saved to weekly notes.")
                    st.rerun()
        with digest_action_cols[1]:
            st.caption("Saving a digest for this week replaces any previously saved digest for the same week.")

    if family_weekly_notes:
        st.markdown('<div class="panel-title" style="margin-top:0.8rem;"><h3>Saved Weekly Digests</h3><span>Historical family planning notes</span></div>', unsafe_allow_html=True)
        for note in family_weekly_notes[:8]:
            week_label = note.get("week_start").strftime("%b %d, %Y") if note.get("week_start") else "Unknown week"
            saved_at_label = note.get("saved_at") or ""
            with st.expander(f"Week of {week_label}", expanded=False):
                if saved_at_label:
                    st.caption(f"Saved: {saved_at_label}")
                st.markdown(note.get("digest_text") or "")
                if st.button("Delete saved digest", key=f"{panel_key}_delete_saved_digest_{note.get('note_id')}"):
                    source_index = note.get("source_index")
                    if source_index is not None and source_index < len(raw_family_weekly_notes):
                        updated_notes = [entry for idx, entry in enumerate(raw_family_weekly_notes) if idx != source_index]
                        _save_family_weekly_notes(updated_notes)
                        st.success("Saved digest deleted.")
                        st.rerun()

    family_goal_coaching_key = f"{panel_key}_{week_start.isoformat()}_family_goal_coaching"
    family_goal_coaching_error_key = f"{panel_key}_{week_start.isoformat()}_family_goal_coaching_error"
    if st.button("Generate Weekly Goal Coaching", key=f"{panel_key}_generate_goal_coaching", type="secondary"):
        coaching_summary = {
            "active_goal_count": len(family_goal_summary["active_goals"]),
            "on_track_count": len(family_goal_summary["on_track_goals"]),
            "attention_count": len(family_goal_summary["attention_goals"]),
            "week_checkins": family_goal_summary["week_checkins"],
            "best_streak": family_goal_summary["best_streak"],
        }
        coaching_text, coaching_error = generate_family_goal_coaching(
            coaching_summary,
            family_goal_summary["active_goals"],
        )
        st.session_state[family_goal_coaching_key] = coaching_text
        st.session_state[family_goal_coaching_error_key] = coaching_error
    if st.session_state.get(family_goal_coaching_error_key):
        st.warning(st.session_state[family_goal_coaching_error_key])
    if st.session_state.get(family_goal_coaching_key):
        st.markdown(st.session_state[family_goal_coaching_key])

    with st.form(f"{panel_key}_goal_form", clear_on_submit=True):
        goal_cols = st.columns(3)
        with goal_cols[0]:
            family_goal_title = st.text_input("Goal title", placeholder="Example: Family dinner together")
        with goal_cols[1]:
            family_goal_owner = st.text_input("Owner", placeholder="Family / parent / child")
        with goal_cols[2]:
            family_goal_target = st.number_input("Weekly target", min_value=1, max_value=14, value=3, step=1)
        family_goal_notes = st.text_area("Goal notes", placeholder="What success looks like and any constraints", height=80)
        family_goal_submit = st.form_submit_button("Add family goal", type="primary")

    if family_goal_submit:
        if not family_goal_title.strip():
            st.warning("Add a goal title before saving.")
        else:
            updated_family_goals = list(raw_family_goals)
            updated_family_goals.append(
                {
                    "goal_id": uuid4().hex,
                    "title": family_goal_title.strip(),
                    "owner": family_goal_owner.strip() or "Family",
                    "target_frequency": int(family_goal_target),
                    "notes": family_goal_notes.strip(),
                    "status": "active",
                    "created_date": today_value,
                    "checkin_dates": [],
                }
            )
            _save_family_goals(updated_family_goals)
            st.success("Family goal added.")
            st.rerun()

    if family_goals:
        for goal in family_goals[:14]:
            goal_status = goal.get("status") or "active"
            target = int(goal.get("target_frequency") or 1)
            progress = int(goal.get("week_checkins") or 0)
            with st.expander(f"{goal.get('title')} · {goal.get('owner')} · {goal_status}", expanded=False):
                st.caption(f"Weekly progress: {progress}/{target} · Total check-ins: {goal.get('total_checkins') or 0}")
                st.progress(min(1.0, float(progress) / float(max(1, target))))
                if goal.get("notes"):
                    st.markdown(goal.get("notes"))
                if goal.get("checkin_dates"):
                    recent_dates = ", ".join([day.strftime("%b %d") for day in goal.get("checkin_dates")[-5:]])
                    st.caption(f"Recent check-ins: {recent_dates}")

                goal_action_cols = st.columns(4)
                if goal_action_cols[0].button("Log check-in today", key=f"{panel_key}_goal_checkin_{goal.get('goal_id')}"):
                    if goal.get("today_checked_in"):
                        st.info("This goal is already checked in today.")
                    else:
                        updated_dates = [day.isoformat() for day in goal.get("checkin_dates")]
                        updated_dates.append(today_value.isoformat())
                        if _apply_goal_update(goal, {"checkin_dates": sorted(set(updated_dates))}):
                            st.success("Check-in logged for today.")
                            st.rerun()

                if goal_action_cols[1].button("Pause/Activate", key=f"{panel_key}_goal_toggle_{goal.get('goal_id')}"):
                    new_status = "active" if goal_status == "paused" else "paused"
                    if goal_status == "completed":
                        new_status = "active"
                    if _apply_goal_update(goal, {"status": new_status}):
                        st.success(f"Goal status updated to {new_status}.")
                        st.rerun()

                if goal_action_cols[2].button("Mark completed", key=f"{panel_key}_goal_done_{goal.get('goal_id')}"):
                    if _apply_goal_update(goal, {"status": "completed"}):
                        st.success("Goal marked as completed.")
                        st.rerun()

                if goal_action_cols[3].button("Delete", key=f"{panel_key}_goal_delete_{goal.get('goal_id')}"):
                    source_index = goal.get("source_index")
                    if source_index is not None and source_index < len(raw_family_goals):
                        updated_goals = [entry for idx, entry in enumerate(raw_family_goals) if idx != source_index]
                        _save_family_goals(updated_goals)
                        st.success("Family goal deleted.")
                        st.rerun()
    else:
        st.caption("No family goals yet. Add one and start checking in daily.")

    st.markdown('</div>', unsafe_allow_html=True)


def render_review_command_panel(tasks, active_tasks, completed_today, app_settings, panel_key="review"):
    today = mountain_today()
    clinic_completed = [task for task in completed_today if task.get("category") == "Clinic"]
    personal_completed = [task for task in completed_today if task.get("category") == "Personal"]
    clinic_open = [task for task in active_tasks if task.get("category") == "Clinic"]
    added_today = [task for task in tasks if task.get("created_date") == today]

    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Shift Debrief</h3><span>Capture what happened and what should happen next</span></div>', unsafe_allow_html=True)
    metric_cols = st.columns(4)
    metric_cols[0].metric("Completed today", len(completed_today))
    metric_cols[1].metric("Clinic completed", len(clinic_completed))
    metric_cols[2].metric("Personal completed", len(personal_completed))
    metric_cols[3].metric("Active clinic tasks", len(clinic_open))

    nightly_reflections = normalize_nightly_reflections((app_settings or {}).get("nightly_reflections"))
    today_key = today.isoformat()
    today_reflection = nightly_reflections.get(today_key, {})

    morning_status_key = f"{panel_key}_{today_key}_morning_status"
    day_feel_key = f"{panel_key}_{today_key}_day_feel"
    area_improvement_key = f"{panel_key}_{today_key}_area_improvement"
    one_win_key = f"{panel_key}_{today_key}_one_win"

    default_morning_status = today_reflection.get("morning_goal_status") or "Not applicable today"
    if default_morning_status not in MORNING_GOAL_STATUS_OPTIONS:
        default_morning_status = "Not applicable today"
    default_day_feel = today_reflection.get("day_feel") or "Steady"
    if default_day_feel not in DAY_FEEL_OPTIONS:
        default_day_feel = "Steady"

    if morning_status_key not in st.session_state:
        st.session_state[morning_status_key] = default_morning_status
    if day_feel_key not in st.session_state:
        st.session_state[day_feel_key] = default_day_feel
    if area_improvement_key not in st.session_state:
        st.session_state[area_improvement_key] = today_reflection.get("area_of_improvement", "")
    if one_win_key not in st.session_state:
        st.session_state[one_win_key] = today_reflection.get("one_win", "")

    nightly_prompt_text = generate_nightly_journal_prompt(
        today,
        st.session_state[morning_status_key],
        st.session_state[day_feel_key],
        st.session_state[area_improvement_key],
        st.session_state[one_win_key],
        len(completed_today),
        len(active_tasks),
    )

    daily_summary_key = f"{panel_key}_daily_ai_summary"
    daily_summary_error_key = f"{panel_key}_daily_ai_summary_error"
    weekly_insight_key = f"{panel_key}_weekly_ai_insight"
    weekly_insight_error_key = f"{panel_key}_weekly_ai_insight_error"

    left_col, right_col = st.columns([1.05, 0.95], gap="large")
    with left_col:
        notes = st.text_area(
            "Review notes",
            placeholder="What moved, what stalled, what surprised you, and what needs to happen tomorrow?",
            height=120,
            key=f"{panel_key}_notes",
        )
        if st.button("Generate Daily Review", key=f"{panel_key}_generate", type="primary"):
            review_text, tomorrow_text, review_error = generate_daily_review(active_tasks, completed_today, notes)
            st.session_state.daily_review_text = review_text
            st.session_state.tomorrow_plan_text = tomorrow_text
            st.session_state.daily_review_error = review_error
        if st.session_state.daily_review_error:
            st.warning(st.session_state.daily_review_error)
        if st.session_state.daily_review_text:
            st.markdown(st.session_state.daily_review_text)
        if st.session_state.tomorrow_plan_text:
            st.markdown(st.session_state.tomorrow_plan_text)

        if st.button("Generate AI Daily Summary", key=f"{panel_key}_generate_ai_daily_summary", type="secondary"):
            summary_text, summary_error = generate_ai_daily_summary(tasks, active_tasks, added_today, completed_today)
            st.session_state[daily_summary_key] = summary_text
            st.session_state[daily_summary_error_key] = summary_error
        if st.session_state.get(daily_summary_error_key):
            st.warning(st.session_state[daily_summary_error_key])
        if st.session_state.get(daily_summary_key):
            st.markdown(st.session_state[daily_summary_key])

        st.markdown('<div style="height: 0.8rem;"></div>', unsafe_allow_html=True)
        st.markdown('<div class="panel-title"><h3>Nightly Reflection</h3><span>Set questions for every night before you unplug</span></div>', unsafe_allow_html=True)

        morning_status = st.radio(
            "Did you complete your morning personal goals?",
            MORNING_GOAL_STATUS_OPTIONS,
            key=morning_status_key,
            horizontal=True,
        )
        day_feel = st.select_slider(
            "How did the day feel overall?",
            options=DAY_FEEL_OPTIONS,
            key=day_feel_key,
        )
        area_of_improvement = st.text_area(
            "One area of improvement",
            placeholder="What can you tighten up tomorrow?",
            key=area_improvement_key,
            height=85,
        )
        one_win = st.text_area(
            "One win",
            placeholder="What went well today?",
            key=one_win_key,
            height=85,
        )

        nightly_prompt_text = generate_nightly_journal_prompt(
            today,
            morning_status,
            day_feel,
            area_of_improvement,
            one_win,
            len(completed_today),
            len(active_tasks),
        )
        st.caption("Auto-generated journal prompt for tonight")
        st.info(nightly_prompt_text)

        if st.button("Save tonight's reflection", key=f"{panel_key}_save_nightly_reflection", type="secondary"):
            updated_reflections = dict(nightly_reflections)
            updated_reflections[today_key] = {
                "morning_goal_status": morning_status,
                "day_feel": day_feel,
                "area_of_improvement": area_of_improvement.strip(),
                "one_win": one_win.strip(),
                "journal_prompt": nightly_prompt_text,
                "saved_at": datetime.now(MOUNTAIN_TIMEZONE).isoformat(timespec="seconds"),
            }
            save_app_settings({
                **(app_settings or {}),
                "nightly_reflections": updated_reflections,
            })
            st.success("Nightly reflection saved.")
            st.rerun()

    with right_col:
        st.markdown('<div class="panel-title"><h3>Night Ritual</h3><span>Transition into journaling, stretching, and reading</span></div>', unsafe_allow_html=True)
        st.markdown(
            "<div class='ai-list'>"
            "<li>1) Save your nightly reflection above.</li>"
            "<li>2) Journal on the prompt shown for tonight.</li>"
            "<li>3) Move into your stretching block.</li>"
            "<li>4) Finish with reading before bed.</li>"
            "</div>",
            unsafe_allow_html=True,
        )
        st.caption("Tonight's prompt")
        st.info(nightly_prompt_text)

        weekly_trends = weekly_nightly_reflection_trends(nightly_reflections, end_day=today)
        week_start = weekly_trends["week_start"]
        week_end = weekly_trends["week_end"]
        st.markdown(
            f"<div class=\"panel-title\" style=\"margin-top:0.9rem;\"><h3>Weekly Trend Summary</h3><span>Current week: {week_start.strftime('%b %d')} - {week_end.strftime('%b %d')} (Mon-Sun)</span></div>",
            unsafe_allow_html=True,
        )
        trend_cols = st.columns(4)
        trend_cols[0].metric("Check-ins", f"{weekly_trends['checkin_count']}/7")
        trend_cols[1].metric("Consistency", f"{int(round(weekly_trends['consistency_rate'] * 100))}%")
        trend_cols[2].metric("Avg day feel", weekly_trends["average_feel_label"])
        if weekly_trends["morning_completion_rate"] is None:
            trend_cols[3].metric("Morning goal hit rate", "N/A")
        else:
            trend_cols[3].metric("Morning goal hit rate", f"{int(round(weekly_trends['morning_completion_rate'] * 100))}%")

        st.markdown(
            render_mini_sparkline("Nightly feel sparkline", weekly_trends["feel_series"], 5, weekly_trends["day_labels"]),
            unsafe_allow_html=True,
        )
        st.markdown(
            render_mini_sparkline("Morning goal sparkline", weekly_trends["morning_series"], 2, weekly_trends["day_labels"]),
            unsafe_allow_html=True,
        )

        st.markdown(
            "<div class='ai-list'>"
            f"<li>Wins logged: {weekly_trends['wins_logged']} of {weekly_trends['checkin_count']} check-ins.</li>"
            f"<li>Improvement areas logged: {weekly_trends['improvements_logged']} of {weekly_trends['checkin_count']} check-ins.</li>"
            f"<li>Day feel spread: Rough {weekly_trends['feel_counts']['Rough']} · Heavy {weekly_trends['feel_counts']['Heavy']} · Steady {weekly_trends['feel_counts']['Steady']} · Good {weekly_trends['feel_counts']['Good']} · Great {weekly_trends['feel_counts']['Great']}.</li>"
            "</div>",
            unsafe_allow_html=True,
        )

        recent_reflections = sorted(nightly_reflections.items(), reverse=True)[:5]
        weekly_history = nightly_reflection_weekly_history(nightly_reflections, max_weeks=12)
        monthly_history = nightly_reflection_monthly_history(weekly_history, max_months=6)

        if weekly_history:
            st.markdown('<div class="panel-title" style="margin-top:0.9rem;"><h3>Weekly Productivity Comparison</h3><span>Saved week-over-week reflection trends</span></div>', unsafe_allow_html=True)
            for row in weekly_history[:8]:
                st.markdown(
                    f"- **{row['week_start'].strftime('%b %d')} - {row['week_end'].strftime('%b %d')}**"
                    f" · check-ins {row['checkin_count']}/7"
                    f" · consistency {int(round(row['consistency_rate'] * 100))}%"
                    f" · avg feel {row['average_feel_label']}"
                )

        if monthly_history:
            st.markdown('<div class="panel-title" style="margin-top:0.9rem;"><h3>Monthly Productivity Comparison</h3><span>Week-level reflection rollup by month</span></div>', unsafe_allow_html=True)
            for row in monthly_history[:6]:
                morning_completion_text = "N/A"
                if row.get("morning_completion_rate") is not None:
                    morning_completion_text = f"{int(round(float(row['morning_completion_rate']) * 100))}%"
                st.markdown(
                    f"- **{row['month_key']}**"
                    f" · weeks tracked {row['week_count']}"
                    f" · consistency {int(round(row['consistency_rate'] * 100))}%"
                    f" · avg feel {row['average_feel_label']}"
                    f" · morning goal hit {morning_completion_text}"
                )

        if st.button("Generate Weekly AI Insight", key=f"{panel_key}_generate_weekly_ai_insight", type="secondary"):
            insight_text, insight_error = generate_weekly_nightly_insight(weekly_trends, recent_reflections)
            st.session_state[weekly_insight_key] = insight_text
            st.session_state[weekly_insight_error_key] = insight_error
        if st.session_state.get(weekly_insight_error_key):
            st.warning(st.session_state[weekly_insight_error_key])
        if st.session_state.get(weekly_insight_key):
            st.markdown(st.session_state[weekly_insight_key])

        if recent_reflections:
            st.markdown('<div class="panel-title" style="margin-top:0.9rem;"><h3>Recent Reflections</h3><span>Your recent nightly check-ins</span></div>', unsafe_allow_html=True)
            for day_text, entry in recent_reflections:
                st.markdown(
                    f"- **{day_text}** · Morning goals: {entry.get('morning_goal_status', 'Not applicable today')} · Day feel: {entry.get('day_feel', 'Steady')}"
                )
                if entry.get("one_win"):
                    st.caption(f"Win: {entry.get('one_win')}")
                if entry.get("area_of_improvement"):
                    st.caption(f"Improve: {entry.get('area_of_improvement')}")

        if clinic_completed:
            st.caption(f"Clinic completions today: {', '.join(task['title'] for task in clinic_completed[:4])}")
        if not st.session_state.daily_review_text and not st.session_state.daily_review_error:
            st.markdown('<div class="empty-state">Run the debrief after a clinic or non-clinic day to capture the transition to tomorrow.</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)


def render_morning_ritual_panel(tasks, active_tasks, app_settings, panel_key="morning_ritual"):
    today = mountain_today()
    today_key = today.isoformat()

    morning_checkins = normalize_morning_ritual_checkins((app_settings or {}).get("morning_ritual_checkins"))
    today_checkin = morning_checkins.get(today_key, {})
    nightly_reflections = normalize_nightly_reflections((app_settings or {}).get("nightly_reflections"))
    recent_nightly = sorted(nightly_reflections.items(), reverse=True)
    latest_nightly_improvement = ""
    for _, entry in recent_nightly:
        improvement = str(entry.get("area_of_improvement") or "").strip()
        if improvement:
            latest_nightly_improvement = improvement
            break

    sleep_key = f"{panel_key}_{today_key}_sleep_quality"
    energy_key = f"{panel_key}_{today_key}_energy_level"
    mood_key = f"{panel_key}_{today_key}_mood"
    intention_key = f"{panel_key}_{today_key}_top_intention"
    planned_key = f"{panel_key}_{today_key}_planned_morning_goals"
    grounding_key = f"{panel_key}_{today_key}_optional_grounding"
    brief_key = f"{panel_key}_{today_key}_brief"
    brief_error_key = f"{panel_key}_{today_key}_brief_error"

    if sleep_key not in st.session_state:
        st.session_state[sleep_key] = today_checkin.get("sleep_quality") or "Good"
    if energy_key not in st.session_state:
        st.session_state[energy_key] = today_checkin.get("energy_level") or "Medium"
    if mood_key not in st.session_state:
        st.session_state[mood_key] = today_checkin.get("mood") or "Neutral"
    if intention_key not in st.session_state:
        st.session_state[intention_key] = today_checkin.get("top_intention") or ""
    if planned_key not in st.session_state:
        st.session_state[planned_key] = today_checkin.get("planned_morning_goals") or "Yes"
    if grounding_key not in st.session_state:
        st.session_state[grounding_key] = bool(today_checkin.get("optional_grounding_complete"))
    if brief_key not in st.session_state and today_checkin.get("morning_brief_text"):
        st.session_state[brief_key] = today_checkin.get("morning_brief_text")

    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Morning Ritual</h3><span>Set your daily intention and lock in the first move</span></div>', unsafe_allow_html=True)

    metric_cols = st.columns(4)
    due_today = [task for task in active_tasks if task.get("due_date") == today]
    high_unscheduled = [
        task
        for task in active_tasks
        if task.get("priority") == "high" and not (task.get("scheduled_date") and task.get("scheduled_time"))
    ]
    metric_cols[0].metric("Active tasks", len(active_tasks))
    metric_cols[1].metric("Due today", len(due_today))
    metric_cols[2].metric("High unscheduled", len(high_unscheduled))
    metric_cols[3].metric("Captured today", len([task for task in tasks if task.get("created_date") == today]))

    left_col, right_col = st.columns([1.05, 0.95], gap="large")
    with left_col:
        st.select_slider("Sleep quality", options=MORNING_SLEEP_OPTIONS, key=sleep_key)
        st.select_slider("Energy level", options=MORNING_ENERGY_OPTIONS, key=energy_key)
        st.select_slider("Mood", options=MORNING_MOOD_OPTIONS, key=mood_key)
        st.text_area(
            "Top intention for today",
            placeholder="What matters most today?",
            key=intention_key,
            height=85,
        )
        st.radio(
            "Morning personal goals completed?",
            MORNING_PLANNED_OPTIONS,
            key=planned_key,
            horizontal=True,
        )
        st.checkbox(
            "Optional reading/grounding complete",
            key=grounding_key,
            help="A short grounding or reading block before deep work.",
        )

        if st.button("Generate AI Morning Brief", key=f"{panel_key}_generate_morning_brief", type="primary"):
            brief_text, brief_error = generate_ai_morning_ritual_brief(
                active_tasks,
                latest_nightly_improvement,
                st.session_state[sleep_key],
                st.session_state[energy_key],
                st.session_state[mood_key],
                st.session_state[intention_key],
                st.session_state[planned_key],
                st.session_state[grounding_key],
            )
            st.session_state[brief_key] = brief_text
            st.session_state[brief_error_key] = brief_error

        if st.session_state.get(brief_error_key):
            st.warning(st.session_state[brief_error_key])
        if st.session_state.get(brief_key):
            st.markdown(st.session_state[brief_key])

        if st.button("Save morning ritual", key=f"{panel_key}_save", type="secondary"):
            updated = dict(morning_checkins)
            updated[today_key] = {
                "sleep_quality": st.session_state[sleep_key],
                "energy_level": st.session_state[energy_key],
                "mood": st.session_state[mood_key],
                "top_intention": st.session_state[intention_key].strip(),
                "planned_morning_goals": st.session_state[planned_key],
                "optional_grounding_complete": bool(st.session_state[grounding_key]),
                "morning_brief_text": str(st.session_state.get(brief_key) or "").strip(),
                "saved_at": datetime.now(MOUNTAIN_TIMEZONE).isoformat(timespec="seconds"),
            }
            save_app_settings({
                **(app_settings or {}),
                "morning_ritual_checkins": updated,
            })
            st.success("Morning ritual saved.")
            st.rerun()

    with right_col:
        st.markdown('<div class="panel-title"><h3>Morning Focus</h3><span>Bridge last night into today</span></div>', unsafe_allow_html=True)
        if latest_nightly_improvement:
            st.info(f"Carry-over from last night: {latest_nightly_improvement}")
        else:
            st.caption("No nightly improvement note found yet. Use Daily Review tonight to create one.")

        morning_trends = weekly_morning_ritual_trends(morning_checkins, end_day=today)
        week_start = morning_trends["week_start"]
        week_end = morning_trends["week_end"]
        recent_checkins = sorted(morning_checkins.items(), reverse=True)[:5]
        weekly_insight_key = f"{panel_key}_{today.isocalendar().year}_w{today.isocalendar().week}_weekly_ai_insight"
        weekly_insight_error_key = f"{panel_key}_{today.isocalendar().year}_w{today.isocalendar().week}_weekly_ai_insight_error"
        st.markdown(
            f"<div class=\"panel-title\" style=\"margin-top:0.9rem;\"><h3>Morning Trend Summary</h3><span>Current week: {week_start.strftime('%b %d')} - {week_end.strftime('%b %d')} (Mon-Sun)</span></div>",
            unsafe_allow_html=True,
        )
        trend_cols = st.columns(4)
        trend_cols[0].metric("Check-ins", f"{morning_trends['checkin_count']}/7")
        trend_cols[1].metric("Consistency", f"{int(round(morning_trends['consistency_rate'] * 100))}%")
        trend_cols[2].metric("Avg sleep", morning_trends["average_sleep_label"])
        trend_cols[3].metric("Avg energy", morning_trends["average_energy_label"])
        st.markdown(
            render_mini_sparkline("Sleep week sparkline", morning_trends["sleep_series"], 4, morning_trends["day_labels"]),
            unsafe_allow_html=True,
        )
        st.markdown(
            render_mini_sparkline("Energy week sparkline", morning_trends["energy_series"], 3, morning_trends["day_labels"]),
            unsafe_allow_html=True,
        )
        st.markdown(
            render_mini_sparkline("Mood week sparkline", morning_trends["mood_series"], 4, morning_trends["day_labels"]),
            unsafe_allow_html=True,
        )
        st.caption(
            "Mood trend: "
            f"{morning_trends['average_mood_label']} · "
            f"Completed goals: {morning_trends['planned_yes_count']}/{morning_trends['checkin_count']} · "
            f"Grounding complete: {morning_trends['grounding_complete_count']}/{morning_trends['checkin_count']}"
        )
        st.markdown(
            "<div class='ai-list'>"
            f"<li>Sleep spread: Poor {morning_trends['sleep_counts']['Poor']} · Fair {morning_trends['sleep_counts']['Fair']} · Good {morning_trends['sleep_counts']['Good']} · Great {morning_trends['sleep_counts']['Great']}.</li>"
            f"<li>Energy spread: Low {morning_trends['energy_counts']['Low']} · Medium {morning_trends['energy_counts']['Medium']} · High {morning_trends['energy_counts']['High']}.</li>"
            f"<li>Mood spread: Drained {morning_trends['mood_counts']['Drained']} · Neutral {morning_trends['mood_counts']['Neutral']} · Positive {morning_trends['mood_counts']['Positive']} · Focused {morning_trends['mood_counts']['Focused']}.</li>"
            "</div>",
            unsafe_allow_html=True,
        )

        weekly_history = morning_ritual_weekly_history(morning_checkins, max_weeks=12)
        monthly_history = morning_ritual_monthly_history(weekly_history, max_months=6)

        if weekly_history:
            st.markdown('<div class="panel-title" style="margin-top:0.9rem;"><h3>Weekly Productivity Comparison</h3><span>Morning ritual consistency by week</span></div>', unsafe_allow_html=True)
            for row in weekly_history[:8]:
                st.markdown(
                    f"- **{row['week_start'].strftime('%b %d')} - {row['week_end'].strftime('%b %d')}**"
                    f" · check-ins {row['checkin_count']}/7"
                    f" · consistency {int(round(row['consistency_rate'] * 100))}%"
                    f" · avg sleep {row['average_sleep_label']}"
                    f" · avg energy {row['average_energy_label']}"
                )

        if monthly_history:
            st.markdown('<div class="panel-title" style="margin-top:0.9rem;"><h3>Monthly Productivity Comparison</h3><span>Week-level morning trend rollup by month</span></div>', unsafe_allow_html=True)
            for row in monthly_history[:6]:
                planned_rate_text = "N/A"
                if row.get("planned_yes_rate") is not None:
                    planned_rate_text = f"{int(round(float(row['planned_yes_rate']) * 100))}%"
                grounding_rate_text = "N/A"
                if row.get("grounding_rate") is not None:
                    grounding_rate_text = f"{int(round(float(row['grounding_rate']) * 100))}%"
                st.markdown(
                    f"- **{row['month_key']}**"
                    f" · weeks tracked {row['week_count']}"
                    f" · consistency {int(round(row['consistency_rate'] * 100))}%"
                    f" · morning goals done {planned_rate_text}"
                    f" · grounding done {grounding_rate_text}"
                )

        if st.button("Generate Weekly AI Insight", key=f"{panel_key}_generate_weekly_morning_insight", type="secondary"):
            insight_text, insight_error = generate_weekly_morning_ritual_insight(
                morning_trends,
                recent_checkins,
                latest_nightly_improvement,
            )
            st.session_state[weekly_insight_key] = insight_text
            st.session_state[weekly_insight_error_key] = insight_error

        if st.session_state.get(weekly_insight_error_key):
            st.warning(st.session_state[weekly_insight_error_key])
        if st.session_state.get(weekly_insight_key):
            st.markdown(st.session_state[weekly_insight_key])

        top_urgent = sorted(
            active_tasks,
            key=lambda task: task_attention_sort_key(task, today),
        )[:1]
        if top_urgent:
            task = top_urgent[0]
            st.markdown('<div class="panel-title" style="margin-top:0.9rem;"><h3>Start Here</h3><span>Highest urgency task first</span></div>', unsafe_allow_html=True)
            st.markdown(
                f"- **{task.get('title')}** · {task.get('priority', 'medium').title()} priority · due {format_due(task)}"
            )
        else:
            st.markdown('<div class="empty-state">No active tasks available. Add one from Quick Command Bar.</div>', unsafe_allow_html=True)

        if recent_checkins:
            st.markdown('<div class="panel-title" style="margin-top:0.9rem;"><h3>Recent Morning Check-ins</h3><span>Last few starts</span></div>', unsafe_allow_html=True)
            for day_text, entry in recent_checkins:
                st.markdown(
                    f"- **{day_text}** · Sleep {entry.get('sleep_quality', 'Good')} · Energy {entry.get('energy_level', 'Medium')} · Mood {entry.get('mood', 'Neutral')}"
                )
                if entry.get("top_intention"):
                    st.caption(f"Intention: {entry.get('top_intention')}")
    st.markdown('</div>', unsafe_allow_html=True)


def render_metrics_row():
    tasks = load_tasks()
    active_tasks = [task for task in tasks if task.get("status") != "completed"]
    due_today = [task for task in active_tasks if task.get("due_date") == mountain_today()]
    completed_tasks = [task for task in tasks if task.get("status") == "completed"]
    scheduled_tasks = [
        task
        for task in active_tasks
        if task.get("scheduled_date") and task.get("scheduled_time")
    ]

    metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)
    metric_col1.metric("Active Tasks", len(active_tasks))
    metric_col2.metric("Due Today", len(due_today))
    metric_col3.metric("Completed", len(completed_tasks))
    metric_col4.metric("Scheduled", len(scheduled_tasks))


def render_daily_review_panel(tasks, active_tasks, completed_today_all, app_settings, panel_key="review"):
    render_metrics_row()
    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    render_review_command_panel(tasks, active_tasks, completed_today_all, app_settings, panel_key=panel_key)

    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Clinic Day Closeout Checklist</h3><span>Generic end-of-day checklist not tied to tasks</span></div>', unsafe_allow_html=True)

    checklist_template = normalize_clinic_day_closeout_template((app_settings or {}).get("clinic_day_closeout_template"))
    checklist_log = normalize_clinic_day_closeout_log(
        (app_settings or {}).get("clinic_day_closeout_log"),
        allowed_items=checklist_template,
    )

    selected_day = st.date_input(
        "Clinic day",
        value=mountain_today(),
        key=f"{panel_key}_clinic_closeout_day",
    )
    selected_day_key = selected_day.isoformat()
    day_entry = checklist_log.get(selected_day_key, {"completed_items": [], "notes": "", "saved_at": ""})
    completed_items = list(day_entry.get("completed_items") or [])

    completion_cols = st.columns([1, 1, 2])
    completion_cols[0].metric("Completed", len(completed_items))
    completion_cols[1].metric("Remaining", max(0, len(checklist_template) - len(completed_items)))
    completion_cols[2].caption(f"Last saved: {day_entry.get('saved_at') or 'Not saved yet'}")

    quick_action_cols = st.columns([1, 1, 3])
    if quick_action_cols[0].button("Mark all complete", key=f"{panel_key}_clinic_closeout_mark_all", use_container_width=True):
        checklist_log[selected_day_key] = {
            "completed_items": list(checklist_template),
            "notes": day_entry.get("notes") or "",
            "saved_at": datetime.now(MOUNTAIN_TIMEZONE).isoformat(timespec="minutes"),
        }
        save_app_settings(
            {
                **(app_settings or {}),
                "clinic_day_closeout_template": checklist_template,
                "clinic_day_closeout_log": checklist_log,
            }
        )
        st.success("Marked all closeout items complete.")
        st.rerun()
    if quick_action_cols[1].button("Reset day", key=f"{panel_key}_clinic_closeout_reset_day", use_container_width=True):
        checklist_log.pop(selected_day_key, None)
        save_app_settings(
            {
                **(app_settings or {}),
                "clinic_day_closeout_template": checklist_template,
                "clinic_day_closeout_log": checklist_log,
            }
        )
        st.success("Closeout checklist reset for the selected day.")
        st.rerun()
    quick_action_cols[2].caption("Quick actions apply to the selected clinic day.")

    with st.form(f"{panel_key}_clinic_closeout_form"):
        selected_items = st.multiselect(
            "Mark completed items",
            checklist_template,
            default=[item for item in completed_items if item in checklist_template],
            help="This checklist is independent from clinic task completion.",
        )
        closeout_notes = st.text_area(
            "Closeout notes (optional)",
            value=day_entry.get("notes") or "",
            height=90,
            placeholder="Anything to carry forward or double-check tomorrow",
        )
        save_closeout = st.form_submit_button("Save closeout checklist", type="primary")

    if save_closeout:
        checklist_log[selected_day_key] = {
            "completed_items": list(selected_items),
            "notes": closeout_notes.strip(),
            "saved_at": datetime.now(MOUNTAIN_TIMEZONE).isoformat(timespec="minutes"),
        }
        save_app_settings(
            {
                **(app_settings or {}),
                "clinic_day_closeout_template": checklist_template,
                "clinic_day_closeout_log": checklist_log,
            }
        )
        st.success("Clinic closeout checklist saved.")
        st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    render_task_list_panel(
        "Completed Today",
        "What you finished",
        completed_today_all,
        f"{panel_key}_completed",
        "No tasks completed today yet.",
    )


def render_page_footer():
    st.markdown(
        "<div style='margin-top: 2.5rem; padding: 1rem 0; text-align: center; opacity: 0.45; font-size: 0.8rem;'>DayAnchor · personal and clinic task capture · optional AI planning</div>",
        unsafe_allow_html=True,
    )


def render_notifications_panel(tasks, active_tasks, app_settings=None, panel_key="notifications"):
    render_metrics_row()
    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)

    raw_quick_reminders = list((app_settings or {}).get("quick_reminders") or [])
    quick_reminders = normalize_quick_reminders(raw_quick_reminders)
    active_reminders = [item for item in quick_reminders if item.get("status") == "active"]
    today_value = mountain_today()
    due_today_reminders = [
        item
        for item in active_reminders
        if item.get("remind_date") and item.get("remind_date") <= today_value
    ]

    def _save_quick_reminders(updated_raw_reminders):
        save_app_settings(
            {
                **(app_settings or {}),
                "quick_reminders": updated_raw_reminders,
            }
        )

    def _update_quick_reminder(item, updates):
        source_index = item.get("source_index")
        if source_index is None or source_index >= len(raw_quick_reminders):
            return False
        if not isinstance(raw_quick_reminders[source_index], dict):
            return False
        raw_quick_reminders[source_index].update(updates)
        _save_quick_reminders(raw_quick_reminders)
        return True

    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Quick Reminders</h3><span>Capture things to remember without creating a task</span></div>', unsafe_allow_html=True)
    reminder_metrics = st.columns(3)
    reminder_metrics[0].metric("Active reminders", len(active_reminders))
    reminder_metrics[1].metric("Due now", len(due_today_reminders))
    reminder_metrics[2].metric("Total saved", len(quick_reminders))

    with st.form(f"{panel_key}_quick_reminder_form", clear_on_submit=True):
        reminder_text = st.text_input("Reminder", placeholder="What do you want to remember?")
        reminder_cols = st.columns(3)
        with reminder_cols[0]:
            reminder_category = st.selectbox("Category", ["General", "Personal", "Family", "Clinic"], index=0)
        with reminder_cols[1]:
            has_date = st.checkbox("Set date", value=False)
            reminder_date = st.date_input("Remind on", value=today_value, disabled=not has_date, key=f"{panel_key}_quick_reminder_date")
        with reminder_cols[2]:
            has_time = st.checkbox("Set time", value=False, disabled=not has_date)
            reminder_time = st.time_input("At", value=time(9, 0), disabled=(not has_date) or (not has_time), key=f"{panel_key}_quick_reminder_time")
        reminder_note = st.text_area("Details (optional)", height=70, placeholder="Context, names, or follow-up notes")
        reminder_submit = st.form_submit_button("Save reminder", type="primary")

    if reminder_submit:
        if not reminder_text.strip():
            st.warning("Add reminder text before saving.")
        else:
            updated_reminders = list(raw_quick_reminders)
            updated_reminders.append(
                {
                    "reminder_id": uuid4().hex,
                    "text": reminder_text.strip(),
                    "category": reminder_category,
                    "notes": reminder_note.strip(),
                    "remind_date": reminder_date if has_date else None,
                    "remind_time": reminder_time if has_date and has_time else None,
                    "status": "active",
                    "created_at": datetime.now(MOUNTAIN_TIMEZONE).isoformat(timespec="seconds"),
                    "updated_at": datetime.now(MOUNTAIN_TIMEZONE).isoformat(timespec="seconds"),
                }
            )
            _save_quick_reminders(updated_reminders)
            st.success("Reminder saved.")
            st.rerun()

    if active_reminders:
        for item in active_reminders[:12]:
            remind_date = item.get("remind_date")
            remind_time = item.get("remind_time")
            when_label = "Anytime"
            if remind_date and remind_time:
                when_label = f"{remind_date.strftime('%b %d')} at {remind_time.strftime('%I:%M %p').lstrip('0')}"
            elif remind_date:
                when_label = remind_date.strftime("%b %d")

            due_tag = " · due" if remind_date and remind_date <= today_value else ""
            with st.expander(f"{item.get('text')} ({item.get('category')}) · {when_label}{due_tag}", expanded=False):
                if item.get("notes"):
                    st.caption(item.get("notes"))
                action_cols = st.columns(3)
                if action_cols[0].button("Dismiss", key=f"{panel_key}_dismiss_reminder_{item.get('reminder_id')}"):
                    if _update_quick_reminder(
                        item,
                        {
                            "status": "dismissed",
                            "updated_at": datetime.now(MOUNTAIN_TIMEZONE).isoformat(timespec="seconds"),
                        },
                    ):
                        st.success("Reminder dismissed.")
                        st.rerun()
                if action_cols[1].button("Snooze +1 day", key=f"{panel_key}_snooze_reminder_{item.get('reminder_id')}"):
                    next_date = (item.get("remind_date") or today_value) + timedelta(days=1)
                    if _update_quick_reminder(
                        item,
                        {
                            "remind_date": next_date,
                            "status": "active",
                            "updated_at": datetime.now(MOUNTAIN_TIMEZONE).isoformat(timespec="seconds"),
                        },
                    ):
                        st.success("Reminder snoozed to tomorrow.")
                        st.rerun()
                if action_cols[2].button("Delete", key=f"{panel_key}_delete_reminder_{item.get('reminder_id')}"):
                    source_index = item.get("source_index")
                    if source_index is not None and source_index < len(raw_quick_reminders):
                        updated_reminders = [entry for idx, entry in enumerate(raw_quick_reminders) if idx != source_index]
                        _save_quick_reminders(updated_reminders)
                        st.success("Reminder deleted.")
                        st.rerun()
    else:
        st.caption("No active reminders. Capture one above when something pops into your head.")

    st.markdown('</div>', unsafe_allow_html=True)
    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)

    overdue_all = sorted(
        [task for task in active_tasks if task.get("due_date") and task["due_date"] < mountain_today()],
        key=lambda task: task_attention_sort_key(task, mountain_today()),
    )
    blocked_all = [task for task in active_tasks if task.get("status") == "blocked"]
    unscheduled_high = sorted(
        [task for task in active_tasks if task.get("priority") == "high" and not (task.get("scheduled_date") and task.get("scheduled_time"))],
        key=lambda task: task_attention_sort_key(task, mountain_today()),
    )
    due_tomorrow = [task for task in active_tasks if task.get("due_date") == (mountain_today() + timedelta(days=1))]

    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Alerts</h3><span>Actionable items that need attention</span></div>', unsafe_allow_html=True)

    if overdue_all:
        st.error(f"{len(overdue_all)} overdue task(s) need triage.")
    if blocked_all:
        st.warning(f"{len(blocked_all)} blocked task(s) are waiting on unblock actions.")
    if unscheduled_high:
        st.warning(f"{len(unscheduled_high)} high-priority task(s) are unscheduled.")
    if due_tomorrow:
        st.info(f"{len(due_tomorrow)} task(s) are due tomorrow.")
    if not (overdue_all or blocked_all or unscheduled_high or due_tomorrow):
        st.success("No urgent alerts right now.")

    alert_cols = st.columns(2)
    with alert_cols[0]:
        render_task_list_panel(
            "Clinic Alerts",
            "Clinic overdue, blocked, and unscheduled items",
            sorted([task for task in overdue_all + blocked_all + unscheduled_high if task.get("category") == "Clinic"], key=lambda task: task_attention_sort_key(task, mountain_today())),
            "notif_clinic_alerts",
            "No clinic-specific alerts right now.",
        )
    with alert_cols[1]:
        render_task_list_panel(
            "Personal Alerts",
            "Personal overdue, blocked, and unscheduled items",
            sorted([task for task in overdue_all + blocked_all + unscheduled_high if task.get("category") == "Personal"], key=lambda task: task_attention_sort_key(task, mountain_today())),
            "notif_personal_alerts",
            "No personal-specific alerts right now.",
        )

    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    cols = st.columns(2, gap="large")
    with cols[0]:
        render_task_list_panel("Overdue Tasks", "Highest urgency", overdue_all, "notif_overdue", "No overdue tasks.")
    with cols[1]:
        render_task_list_panel("Blocked Tasks", "Needs intervention", blocked_all, "notif_blocked", "No blocked tasks.")


def autoclave_next_due_date(last_completed_date, frequency_label, current_next_due=None):
    if not isinstance(last_completed_date, date):
        return current_next_due

    normalized = str(frequency_label or "").strip().lower()
    days_by_frequency = {
        "daily": 1,
        "weekly": 7,
        "monthly": 30,
        "quarterly": 90,
    }
    if normalized not in days_by_frequency:
        return current_next_due
    return last_completed_date + timedelta(days=days_by_frequency[normalized])


def render_ma_lead_panel(active_tasks, clinic_tasks_all, panel_key="ma_lead"):
    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)

    app_settings = load_app_settings()
    lead_issues = load_lead_clinical_issues() or []
    sop_entries = load_lead_sop_entries() or []
    relationship_touchpoints = load_lead_relationship_touchpoints() or []
    ma_assignments = load_lead_ma_assignments() or []
    huddle_logs = load_lead_huddle_logs() or []
    skill_signoffs = load_lead_skill_signoffs() or []
    education_requests = load_lead_education_requests() or []
    autoclave_items = load_autoclave_maintenance_items() or []
    lead_documents = load_lead_documents() or []
    weekly_metric_targets = normalize_ma_lead_weekly_metric_targets(app_settings.get("ma_lead_weekly_metric_targets"))
    weekly_metrics_log = normalize_ma_lead_weekly_metrics_log(app_settings.get("ma_lead_weekly_metrics_log"))
    rollout_start_date = parse_date_value(app_settings.get("ma_lead_rollout_30_day_start_date")) or mountain_today()
    rollout_template = normalize_ma_lead_rollout_template(app_settings.get("ma_lead_rollout_30_day_template"))
    rollout_log = normalize_ma_lead_rollout_log(
        app_settings.get("ma_lead_rollout_30_day_log"),
        allowed_items=rollout_template,
    )
    raw_biweekly_checkins = list(app_settings.get("ma_lead_biweekly_checkins") or [])
    raw_biweekly_actions = list(app_settings.get("ma_lead_biweekly_action_items") or [])
    biweekly_template = normalize_ma_lead_biweekly_template(app_settings.get("ma_lead_biweekly_template"))
    biweekly_settings = normalize_ma_lead_biweekly_settings(app_settings.get("ma_lead_biweekly_settings"))
    biweekly_checkins = normalize_ma_lead_biweekly_checkins(raw_biweekly_checkins)
    biweekly_actions = normalize_ma_lead_biweekly_action_items(raw_biweekly_actions)

    def _save_ma_lead_settings(
        updated_weekly_targets=None,
        updated_weekly_log=None,
        updated_rollout_start_date=None,
        updated_rollout_template=None,
        updated_rollout_log=None,
        updated_biweekly_checkins=None,
        updated_biweekly_actions=None,
        updated_biweekly_template=None,
        updated_biweekly_settings=None,
    ):
        save_app_settings(
            {
                **app_settings,
                "ma_lead_weekly_metric_targets": updated_weekly_targets if updated_weekly_targets is not None else app_settings.get("ma_lead_weekly_metric_targets", {}),
                "ma_lead_weekly_metrics_log": updated_weekly_log if updated_weekly_log is not None else app_settings.get("ma_lead_weekly_metrics_log", {}),
                "ma_lead_rollout_30_day_start_date": (
                    updated_rollout_start_date.isoformat()
                    if isinstance(updated_rollout_start_date, date)
                    else (updated_rollout_start_date or app_settings.get("ma_lead_rollout_30_day_start_date", ""))
                ),
                "ma_lead_rollout_30_day_template": updated_rollout_template if updated_rollout_template is not None else app_settings.get("ma_lead_rollout_30_day_template", []),
                "ma_lead_rollout_30_day_log": updated_rollout_log if updated_rollout_log is not None else app_settings.get("ma_lead_rollout_30_day_log", {}),
                "ma_lead_biweekly_checkins": updated_biweekly_checkins if updated_biweekly_checkins is not None else app_settings.get("ma_lead_biweekly_checkins", []),
                "ma_lead_biweekly_action_items": updated_biweekly_actions if updated_biweekly_actions is not None else app_settings.get("ma_lead_biweekly_action_items", []),
                "ma_lead_biweekly_template": updated_biweekly_template if updated_biweekly_template is not None else app_settings.get("ma_lead_biweekly_template", {}),
                "ma_lead_biweekly_settings": updated_biweekly_settings if updated_biweekly_settings is not None else app_settings.get("ma_lead_biweekly_settings", {}),
            }
        )

    def _load_on_call_schedule_document(document_items):
        schedule_keywords = ("on call", "on-call", "schedule", "roster")
        candidate_documents = []
        for document in document_items:
            searchable_text = " ".join(
                [
                    str(document.get("section_key") or ""),
                    str(document.get("title") or ""),
                    str(document.get("file_name") or ""),
                    str(document.get("notes") or ""),
                ]
            ).lower()
            if document.get("section_key") == "Daily Huddle" or any(keyword in searchable_text for keyword in schedule_keywords):
                candidate_documents.append(document)

        candidate_documents.sort(
            key=lambda item: (
                item.get("created_date") or date.min,
                item.get("id") or 0,
            ),
            reverse=True,
        )

        for document in candidate_documents:
            file_bytes = document.get("file_bytes")
            if isinstance(file_bytes, memoryview):
                file_bytes = bytes(file_bytes)
            schedule_document = parse_on_call_schedule_document(document.get("file_name") or document.get("title") or "on-call schedule", file_bytes)
            if schedule_document.get("entry_count"):
                return document, schedule_document
        return None, None

    unresolved_statuses = {"new", "in_review", "escalated"}
    open_issues = [item for item in lead_issues if item.get("status") in unresolved_statuses]
    escalated_issues = [item for item in open_issues if item.get("status") == "escalated"]
    waiting_psr = [
        item
        for item in open_issues
        if item.get("source_lane") == "psr"
        or item.get("escalation_target") == "psr_lead"
    ]
    waiting_leadership = [
        item
        for item in open_issues
        if item.get("escalation_target") in ("manager", "supervisor")
    ]
    resolved_today = [item for item in lead_issues if item.get("resolved_date") == mountain_today()]

    headline = st.columns(5)
    headline[0].metric("Needs action now", len(open_issues))
    headline[1].metric("Waiting on PSR", len(waiting_psr))
    headline[2].metric("Waiting on manager/supervisor", len(waiting_leadership))
    headline[3].metric("Escalated", len(escalated_issues))
    headline[4].metric("Resolved today", len(resolved_today))
    pending_signoffs = len([item for item in skill_signoffs if item.get("status") in ("pending", "in_progress")])
    open_education_requests = len([item for item in education_requests if item.get("status") in ("new", "preparing", "delivered")])
    autoclave_due_count = len([item for item in autoclave_items if item.get("status") in ("due_soon", "overdue")])
    active_ma_assignments = len([item for item in ma_assignments if item.get("status") == "active"])
    st.caption(
        f"Preceptor sign-offs pending: {pending_signoffs} · Education requests open: {open_education_requests} · Autoclave checks due: {autoclave_due_count} · Active MA assignments: {active_ma_assignments}"
    )

    # Keep the top of the page focused on today's actions before deeper tab workflows.
    today_value = mountain_today()
    cadence_days = max(7, int(biweekly_settings.get("cadence_days") or 14))

    latest_checkin_by_ma = {}
    for item in biweekly_checkins:
        ma_name = str(item.get("ma_name") or "").strip()
        checkin_date = item.get("checkin_date")
        if not ma_name or not isinstance(checkin_date, date):
            continue
        current = latest_checkin_by_ma.get(ma_name)
        if not current or checkin_date > current.get("checkin_date"):
            latest_checkin_by_ma[ma_name] = item

    checkins_due_rows = []
    for ma_name, latest in latest_checkin_by_ma.items():
        next_due = latest.get("next_due_date")
        if not isinstance(next_due, date):
            next_due = latest.get("checkin_date") + timedelta(days=cadence_days)
        if isinstance(next_due, date) and next_due <= today_value:
            checkins_due_rows.append((ma_name, next_due))
    checkins_due_rows.sort(key=lambda item: (item[1], item[0].lower()))

    open_biweekly_actions = [item for item in biweekly_actions if item.get("status") == "open"]
    overdue_biweekly_actions = [
        item
        for item in open_biweekly_actions
        if isinstance(item.get("due_date"), date) and item.get("due_date") < today_value
    ]

    open_tab_state_key = f"{panel_key}_preferred_tab"
    severity_colors = {
        "critical": "#b91c1c",
        "high": "#c2410c",
        "medium": "#0369a1",
        "normal": "#475569",
    }

    top_priority_items = []
    for item in sorted(escalated_issues, key=lambda issue: issue.get("due_date") or date.max)[:3]:
        top_priority_items.append(
            {
                "severity": "critical",
                "label": f"Escalation: {item.get('title')} (owner: {item.get('owner_name') or 'unassigned'})",
                "target_tab": "Clinical Triage Queue",
                "target_ma_name": "",
            }
        )
    for ma_name, due_date in checkins_due_rows[:3]:
        top_priority_items.append(
            {
                "severity": "high",
                "label": f"Biweekly check-in due: {ma_name} (due {due_date.strftime('%b %d')})",
                "target_tab": "Biweekly Check-ins",
                "target_ma_name": ma_name,
            }
        )
    for item in sorted(overdue_biweekly_actions, key=lambda action: action.get("due_date") or date.max)[:3]:
        top_priority_items.append(
            {
                "severity": "high",
                "label": f"Follow-up overdue: {item.get('ma_name')} - {item.get('action_text')}",
                "target_tab": "Biweekly Check-ins",
                "target_ma_name": str(item.get("ma_name") or "").strip(),
            }
        )
    if not top_priority_items:
        top_priority_items = [
            {
                "severity": "normal",
                "label": "No urgent priority items right now.",
                "target_tab": "Command Center",
                "target_ma_name": "",
            }
        ]

    st.markdown('<div style="height: 0.45rem;"></div>', unsafe_allow_html=True)
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>MA Lead Focus - Today</h3><span>Start here for urgent actions, then move into detailed tabs</span></div>', unsafe_allow_html=True)

    focus_cols = st.columns([1.8, 1.2], gap="large")
    with focus_cols[0]:
        st.markdown("#### Priority queue")
        for index, item in enumerate(top_priority_items[:8]):
            row_cols = st.columns([4, 1])
            with row_cols[0]:
                severity = item.get("severity") or "normal"
                severity_color = severity_colors.get(severity, severity_colors["normal"])
                st.markdown(
                    f"<span style='display:inline-block; padding:0.08rem 0.45rem; border-radius:999px; background:{severity_color}; color:#fff; font-size:0.72rem; font-weight:600; margin-right:0.45rem;'>{severity.title()}</span>{item.get('label')}",
                    unsafe_allow_html=True,
                )
            with row_cols[1]:
                if st.button("Open", key=f"{panel_key}_focus_open_{index}"):
                    target_tab = str(item.get("target_tab") or "Command Center")
                    st.session_state[open_tab_state_key] = target_tab
                    target_ma_name = str(item.get("target_ma_name") or "").strip()
                    if target_ma_name:
                        st.session_state[f"{panel_key}_biweekly_ma_name"] = target_ma_name
                    st.rerun()
    with focus_cols[1]:
        st.markdown("#### Quick snapshot")
        quick_metrics = st.columns(2)
        quick_metrics[0].metric("Escalations", len(escalated_issues))
        quick_metrics[1].metric("Biweekly due", len(checkins_due_rows))
        quick_metrics[0].metric("Action items overdue", len(overdue_biweekly_actions))
        quick_metrics[1].metric("Open triage issues", len(open_issues))
        st.caption("Use tabs: Triage for issue resolution, Biweekly for coaching loops, Daily Huddle for shift communication.")

    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div style="height: 0.6rem;"></div>', unsafe_allow_html=True)
    show_relationship_tracker = False
    show_advanced_tabs = st.toggle(
        "Show advanced MA Lead sections",
        value=False,
        key=f"{panel_key}_show_advanced_tabs",
        help="Turn off to keep the first-screen workflow focused on core daily operations.",
    )

    primary_tab_labels = [
        "Command Center",
        "Clinical Triage Queue",
        "MA Assignments",
        "Daily Huddle",
        "SOP Playbook",
        "Biweekly Check-ins",
    ]

    advanced_tab_labels = [
        "Preceptor Sign-offs",
        "Education Liaison",
        "Autoclave Maintenance",
        "Documents",
        "Weekly Metrics Dashboard",
        "30-Day Rollout",
    ]

    if show_relationship_tracker:
        advanced_tab_labels.insert(0, "Relationship Tracker")

    visible_tab_labels = list(primary_tab_labels)
    if show_advanced_tabs:
        visible_tab_labels.extend(advanced_tab_labels)

    preferred_tab_label = str(st.session_state.get(open_tab_state_key) or "").strip()
    if preferred_tab_label in visible_tab_labels:
        ordered_tab_labels = [preferred_tab_label] + [label for label in visible_tab_labels if label != preferred_tab_label]
    else:
        ordered_tab_labels = list(visible_tab_labels)

    tab_objects = st.tabs(ordered_tab_labels)
    tab_lookup = dict(zip(ordered_tab_labels, tab_objects))

    def render_record_attachments(section_key, record_type, record_id, default_title):
        st.markdown("##### Attachments")
        linked_documents = [
            doc
            for doc in lead_documents
            if doc.get("record_type") == record_type and doc.get("record_id") == record_id
        ]

        def _looks_like_pdf(document_item):
            mime_value = str(document_item.get("file_mime") or "").lower()
            file_name_value = str(document_item.get("file_name") or "").lower()
            return mime_value == "application/pdf" or file_name_value.endswith(".pdf")

        attachment_file = st.file_uploader(
            "Attach file",
            key=f"{panel_key}_attach_file_{record_type}_{record_id}",
            help="Upload supporting files for this specific record.",
        )
        if st.button("Upload attachment", key=f"{panel_key}_attach_upload_{record_type}_{record_id}"):
            if not attachment_file:
                st.warning("Choose a file before uploading.")
            else:
                file_bytes = attachment_file.getvalue()
                if len(file_bytes) > 25 * 1024 * 1024:
                    st.warning("File is over 25 MB and cannot be uploaded.")
                else:
                    add_lead_document(
                        section_key=section_key,
                        record_type=record_type,
                        record_id=record_id,
                        title=default_title,
                        file_name=attachment_file.name,
                        file_mime=getattr(attachment_file, "type", None),
                        file_bytes=file_bytes,
                    )
                    st.success("Attachment uploaded.")
                    st.rerun()

        if linked_documents:
            linked_pdf_documents = []
            for doc in linked_documents:
                doc_bytes = doc.get("file_bytes")
                if isinstance(doc_bytes, memoryview):
                    doc_bytes = bytes(doc_bytes)
                if not doc_bytes or not _looks_like_pdf(doc):
                    continue
                linked_pdf_documents.append((doc, doc_bytes))

            if linked_pdf_documents:
                st.markdown("**PDF preview**")
                pdf_option_labels = [
                    f"{doc.get('file_name') or 'document.pdf'} ({doc.get('created_date') or 'unknown date'})"
                    for doc, _ in linked_pdf_documents
                ]
                selected_pdf_label = st.selectbox(
                    "Choose a PDF",
                    pdf_option_labels,
                    key=f"{panel_key}_record_pdf_preview_select_{record_type}_{record_id}",
                )
                selected_pdf_index = pdf_option_labels.index(selected_pdf_label)
                selected_pdf_doc, selected_pdf_bytes = linked_pdf_documents[selected_pdf_index]
                selected_pdf_id = selected_pdf_doc.get("id")
                selected_start_page = int(
                    st.number_input(
                        "Start page",
                        min_value=1,
                        value=1,
                        step=1,
                        key=f"{panel_key}_record_pdf_preview_start_page_{record_type}_{record_id}_{selected_pdf_id}",
                    )
                )
                page_sections._render_protocol_pdf_preview(
                    st,
                    file_bytes=selected_pdf_bytes,
                    file_mime=selected_pdf_doc.get("file_mime"),
                    file_name=selected_pdf_doc.get("file_name") or "document.pdf",
                    height=460,
                    start_page=selected_start_page,
                )

            for doc in linked_documents:
                doc_id = doc.get("id")
                doc_bytes = doc.get("file_bytes")
                if isinstance(doc_bytes, memoryview):
                    doc_bytes = bytes(doc_bytes)
                row = st.columns([2.2, 1.2, 1])
                with row[0]:
                    st.caption(f"{doc.get('file_name')} ({doc.get('created_date')})")
                with row[1]:
                    st.download_button(
                        "Download",
                        data=doc_bytes,
                        file_name=doc.get("file_name") or f"document_{doc_id}",
                        mime=doc.get("file_mime") or "application/octet-stream",
                        key=f"{panel_key}_record_doc_download_{record_type}_{record_id}_{doc_id}",
                    )
                with row[2]:
                    if st.button("Delete", key=f"{panel_key}_record_doc_delete_{record_type}_{record_id}_{doc_id}"):
                        delete_lead_document(doc_id)
                        st.success("Attachment deleted.")
                        st.rerun()
        else:
            st.caption("No attachments for this item yet.")

    with tab_lookup["Command Center"]:
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.markdown('<div class="panel-title"><h3>MA Lead Command Center</h3><span>Go-to view for staffing, escalations, and cross-team dependencies</span></div>', unsafe_allow_html=True)
        alert_cols = st.columns(2)
        with alert_cols[0]:
            if open_issues:
                st.warning(f"{len(open_issues)} open issue(s) need active ownership.")
            else:
                st.success("No open lead queue issues right now.")
            if waiting_psr:
                st.info(f"{len(waiting_psr)} issue(s) waiting on PSR lane.")
            if waiting_leadership:
                st.warning(f"{len(waiting_leadership)} issue(s) waiting on manager/supervisor input.")
        with alert_cols[1]:
            st.caption(f"Open lead queue issues: {len(open_issues)}")
            st.caption(f"SOP entries: {len(sop_entries)}")
            due_followups = [
                person
                for person in relationship_touchpoints
                if person.get("next_follow_up_date") and person.get("next_follow_up_date") <= mountain_today()
            ]
            st.caption(f"Relationship follow-ups due: {len(due_followups)}")

        queue_cols = st.columns(2, gap="large")
        with queue_cols[0]:
            st.markdown("#### Needs action now")
            urgent_open = sorted(
                open_issues,
                key=lambda item: (
                    0 if item.get("urgency") == "critical" else 1 if item.get("urgency") == "high" else 2,
                    item.get("due_date") or date.max,
                ),
            )
            if urgent_open:
                for item in urgent_open[:7]:
                    due_label = format_due(item.get("due_date"))
                    st.markdown(f"- **{item.get('title')}** · {item.get('urgency')} · {item.get('status')} · due {due_label}")
            else:
                st.markdown("No active issues.")

        with queue_cols[1]:
            st.markdown("#### Resolved today")
            if resolved_today:
                for item in resolved_today[:7]:
                    st.markdown(f"- **{item.get('title')}** · closed by {item.get('owner_name') or 'unassigned'}")
            else:
                st.markdown("No items marked resolved today.")
        st.markdown('</div>', unsafe_allow_html=True)

    with tab_lookup["Clinical Triage Queue"]:
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.markdown('<div class="panel-title"><h3>Clinical Issue Triage Queue</h3><span>Capture requests from clinical staff, PSR, and leadership with clear escalation paths</span></div>', unsafe_allow_html=True)

        with st.form(f"{panel_key}_new_issue", clear_on_submit=True):
            create_cols = st.columns(3)
            with create_cols[0]:
                issue_title = st.text_input("Issue title")
                issue_type = st.selectbox("Issue type", ["Clinical task", "PSR handoff", "Workflow blocker", "Staffing", "Patient callback", "Referral/authorization"])
                source_lane = st.selectbox("Source", ["clinical_staff", "psr", "manager", "supervisor"])
            with create_cols[1]:
                urgency = st.selectbox("Urgency", ["critical", "high", "medium", "low"], index=2)
                owner_name = st.text_input("Owner")
                due_date = st.date_input("Due date", value=mountain_today())
            with create_cols[2]:
                due_time = st.time_input("Due time", value=time(16, 0))
                escalation_target = st.selectbox("Escalation target", ["none", "psr_lead", "manager", "supervisor"])
                decision_needed_by = st.date_input("Decision needed by", value=mountain_today() + timedelta(days=1))
            details = st.text_area("Details", height=100, placeholder="What happened, who is affected, and what outcome is needed?")
            dependency_owner = st.text_input("Dependency owner (optional)")
            escalation_reason = st.text_area("Escalation reason (optional)", height=80)
            submit_issue = st.form_submit_button("Add to triage queue", type="primary")

        if submit_issue:
            if not issue_title.strip():
                st.warning("Issue title is required.")
            else:
                add_lead_clinical_issue(
                    title=issue_title,
                    details=details,
                    issue_type=issue_type,
                    source_lane=source_lane,
                    urgency=urgency,
                    owner_name=owner_name,
                    due_date=due_date,
                    due_time=due_time,
                    escalation_target=escalation_target,
                    escalation_reason=escalation_reason,
                    decision_needed_by=decision_needed_by,
                    dependency_owner=dependency_owner,
                )
                st.success("Issue added to clinical triage queue.")
                st.rerun()

        st.markdown("#### Open queue")
        open_queue = sorted(
            [item for item in lead_issues if item.get("status") in unresolved_statuses],
            key=lambda item: (
                0 if item.get("urgency") == "critical" else 1 if item.get("urgency") == "high" else 2,
                item.get("due_date") or date.max,
                item.get("id") or 0,
            ),
        )
        if open_queue:
            for item in open_queue[:20]:
                issue_id = item.get("id")
                with st.expander(f"#{issue_id} · {item.get('title')} · {item.get('urgency')} · {item.get('status')}", expanded=False):
                    st.markdown(f"**Type:** {item.get('issue_type')}  ")
                    st.markdown(f"**Source:** {item.get('source_lane')}  ")
                    st.markdown(f"**Owner:** {item.get('owner_name') or 'Unassigned'}  ")
                    st.markdown(f"**Escalation target:** {item.get('escalation_target') or 'none'}")
                    if item.get("details"):
                        st.markdown(f"**Details:** {item.get('details')}")
                    if item.get("dependency_owner"):
                        st.markdown(f"**Dependency owner:** {item.get('dependency_owner')}")
                    if item.get("escalation_reason"):
                        st.markdown(f"**Escalation reason:** {item.get('escalation_reason')}")

                    action_cols = st.columns(4)
                    if action_cols[0].button("Mark In Review", key=f"{panel_key}_issue_review_{issue_id}"):
                        update_lead_clinical_issue(issue_id, status="in_review")
                        st.rerun()
                    if action_cols[1].button("Escalate", key=f"{panel_key}_issue_escalate_{issue_id}"):
                        update_lead_clinical_issue(issue_id, status="escalated")
                        st.rerun()
                    if action_cols[2].button("Resolve", key=f"{panel_key}_issue_resolve_{issue_id}"):
                        update_lead_clinical_issue(issue_id, status="resolved", resolved_date=mountain_today())
                        st.rerun()
                    if action_cols[3].button("Reopen", key=f"{panel_key}_issue_reopen_{issue_id}"):
                        update_lead_clinical_issue(issue_id, status="new", resolved_date=None)
                        st.rerun()
        else:
            st.markdown('<div class="empty-state">No open triage issues.</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    with tab_lookup["MA Assignments"]:
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.markdown('<div class="panel-title"><h3>MA Assignment Tracker</h3><span>Track MA provider pairings, room stocking ownership, additional tasks, and clinic days</span></div>', unsafe_allow_html=True)

        weekday_options = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        with st.form(f"{panel_key}_ma_assignment_form", clear_on_submit=True):
            assignment_cols = st.columns(3)
            with assignment_cols[0]:
                ma_name = st.text_input("MA name")
                provider_name = st.text_input("Provider")
            with assignment_cols[1]:
                stocking_rooms = st.text_input("Rooms assigned for stocking", placeholder="Room 1, Room 3, Cast room")
                assignment_status = st.selectbox("Status", ["active", "backup", "inactive"], index=0)
            with assignment_cols[2]:
                clinic_days = st.multiselect("Clinic days", weekday_options, default=["Monday", "Thursday"])
            additional_tasks = st.text_area(
                "Additional tasks",
                placeholder="Phone coverage, supply audit, callback list, pre-chart prep...",
                height=90,
            )
            submit_assignment = st.form_submit_button("Add MA assignment", type="primary")

        if submit_assignment:
            if not ma_name.strip():
                st.warning("MA name is required.")
            else:
                add_lead_ma_assignment(
                    ma_name=ma_name,
                    provider_name=provider_name,
                    stocking_rooms=stocking_rooms,
                    additional_tasks=additional_tasks,
                    clinic_days=", ".join(clinic_days),
                    status=assignment_status,
                )
                st.success("MA assignment saved.")
                st.rerun()

        st.markdown("#### Weekly coverage summary")
        active_assignments = [item for item in ma_assignments if item.get("status") in ("active", "backup")]
        if active_assignments:
            weekday_columns = st.columns(len(weekday_options))
            for idx, weekday in enumerate(weekday_options):
                day_matches = []
                for item in active_assignments:
                    assigned_days = [
                        day.strip()
                        for day in str(item.get("clinic_days") or "").split(",")
                        if day.strip()
                    ]
                    if weekday in assigned_days:
                        day_matches.append(item)

                active_count = len([item for item in day_matches if item.get("status") == "active"])
                backup_count = len([item for item in day_matches if item.get("status") == "backup"])

                with weekday_columns[idx]:
                    st.markdown(f"**{weekday}**")
                    st.caption(f"Active: {active_count} · Backup: {backup_count}")
                    if day_matches:
                        for item in day_matches:
                            ma_label = item.get("ma_name") or "Unnamed MA"
                            provider_label = item.get("provider_name") or "No provider"
                            status_label = item.get("status") or "active"
                            st.markdown(f"- {ma_label} ({provider_label}) [{status_label}]")
                    else:
                        st.caption("No assignment")
        else:
            st.caption("No active or backup assignments available for weekly coverage yet.")

        st.markdown("#### Current assignments")
        ordered_assignments = sorted(
            ma_assignments,
            key=lambda item: (
                0 if item.get("status") == "active" else 1 if item.get("status") == "backup" else 2,
                str(item.get("ma_name") or "").lower(),
            ),
        )
        if ordered_assignments:
            for item in ordered_assignments:
                assignment_id = item.get("id")
                ma_label = item.get("ma_name") or "Unnamed MA"
                provider_label = item.get("provider_name") or "No provider assigned"
                status_label = item.get("status") or "active"
                with st.expander(f"{ma_label} · {provider_label} · {status_label}", expanded=False):
                    st.markdown(f"**Rooms assigned for stocking:** {item.get('stocking_rooms') or 'Not set'}")
                    st.markdown(f"**Clinic days:** {item.get('clinic_days') or 'Not set'}")
                    st.markdown(f"**Additional tasks:** {item.get('additional_tasks') or 'None listed'}")

                    edit_cols = st.columns(3)
                    with edit_cols[0]:
                        edit_provider = st.text_input("Provider", value=item.get("provider_name") or "", key=f"{panel_key}_ma_provider_{assignment_id}")
                        edit_rooms = st.text_input(
                            "Rooms assigned",
                            value=item.get("stocking_rooms") or "",
                            key=f"{panel_key}_ma_rooms_{assignment_id}",
                        )
                    with edit_cols[1]:
                        current_days = [
                            day.strip()
                            for day in str(item.get("clinic_days") or "").split(",")
                            if day.strip() in weekday_options
                        ]
                        edit_days = st.multiselect(
                            "Clinic days",
                            weekday_options,
                            default=current_days,
                            key=f"{panel_key}_ma_days_{assignment_id}",
                        )
                        edit_status = st.selectbox(
                            "Status",
                            ["active", "backup", "inactive"],
                            index=["active", "backup", "inactive"].index(status_label) if status_label in ["active", "backup", "inactive"] else 0,
                            key=f"{panel_key}_ma_status_{assignment_id}",
                        )
                    with edit_cols[2]:
                        edit_tasks = st.text_area(
                            "Additional tasks",
                            value=item.get("additional_tasks") or "",
                            key=f"{panel_key}_ma_tasks_{assignment_id}",
                            height=100,
                        )
                        if st.button("Save assignment", key=f"{panel_key}_ma_save_{assignment_id}", type="secondary"):
                            update_lead_ma_assignment(
                                assignment_id,
                                provider_name=edit_provider,
                                stocking_rooms=edit_rooms,
                                clinic_days=", ".join(edit_days),
                                additional_tasks=edit_tasks,
                                status=edit_status,
                            )
                            st.success("Assignment updated.")
                            st.rerun()
        else:
            st.caption("No MA assignments tracked yet.")

        st.markdown('</div>', unsafe_allow_html=True)

    with tab_lookup["Daily Huddle"]:
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.markdown('<div class="panel-title"><h3>Daily Huddle Builder</h3><span>Auto-generate agenda and send-ready recap notes</span></div>', unsafe_allow_html=True)

        today_open = [item for item in open_issues if item.get("due_date") in (None, mountain_today())]
        top_escalations = [item for item in escalated_issues if item.get("escalation_target") in ("manager", "supervisor")]
        relationship_followups = [
            person
            for person in relationship_touchpoints
            if person.get("next_follow_up_date") and person.get("next_follow_up_date") <= mountain_today()
        ]

        huddle_date = st.date_input("Huddle date", value=mountain_today(), key=f"{panel_key}_huddle_date")
        st.caption("Daily huddle template")
        attendees = st.text_input(
            "Attendees",
            placeholder="Kayleigh, Savannah, Zach, Carla, Kara, Manny, Liz, Tania, Maylon, Jenna",
            key=f"{panel_key}_attendees",
        )

        on_call_schedule_document, on_call_schedule = _load_on_call_schedule_document(lead_documents)

        st.markdown("**Daily metrics table**")
        table_cols = st.columns([1.1, 1.1, 0.8, 1.0, 1.1, 1.0, 1.2])
        with table_cols[0]:
            provider_label = st.text_input("Provider", value="Clinic-", key=f"{panel_key}_provider_label")
        with table_cols[1]:
            schedule_utilization = st.text_input("Schedule Utilization", value="", key=f"{panel_key}_schedule_utilization")
        with table_cols[2]:
            pts_count = st.number_input("# of pts", min_value=0, step=1, key=f"{panel_key}_pts_count")
        with table_cols[3]:
            open_spots = st.number_input("# of open spots", min_value=0, step=1, key=f"{panel_key}_open_spots")
        with table_cols[4]:
            referral_wq_status = st.text_input("Referral WQ Status", value="", key=f"{panel_key}_referral_wq_status")
        with table_cols[5]:
            oldest_referral_age = st.text_input("Oldest Referral Age", value="", key=f"{panel_key}_oldest_referral_age")
        with table_cols[6]:
            waitlist_recall_status = st.text_input(
                "Waitlist/Recall List Status",
                value="",
                key=f"{panel_key}_waitlist_recall_status",
            )

        providers_on_call_key = f"{panel_key}_providers_on_call"
        providers_on_call_seed_key = f"{panel_key}_providers_on_call_seed"
        auto_filled_providers_on_call = ""
        on_call_schedule_source_label = ""
        if on_call_schedule:
            auto_filled_providers_on_call = providers_for_schedule_date(on_call_schedule, huddle_date)
            on_call_schedule_source_label = on_call_schedule_document.get("title") or on_call_schedule_document.get("file_name") or "on-call schedule"
            current_providers_value = str(st.session_state.get(providers_on_call_key) or "")
            previous_seed_value = st.session_state.get(providers_on_call_seed_key)
            if auto_filled_providers_on_call and (not current_providers_value or current_providers_value == previous_seed_value):
                st.session_state[providers_on_call_key] = auto_filled_providers_on_call
                st.session_state[providers_on_call_seed_key] = auto_filled_providers_on_call

        if on_call_schedule and on_call_schedule_source_label:
            schedule_caption_cols = st.columns([4, 1])
            with schedule_caption_cols[0]:
                if auto_filled_providers_on_call:
                    st.caption(f"Auto-filled from {on_call_schedule_source_label} for {huddle_date.strftime('%m/%d/%y')}.")
                else:
                    st.caption(f"Loaded {on_call_schedule_source_label}, but no entry matched {huddle_date.strftime('%m/%d/%y')} yet.")
            with schedule_caption_cols[1]:
                if st.button("Use schedule", key=f"{panel_key}_use_on_call_schedule"):
                    if auto_filled_providers_on_call:
                        st.session_state[providers_on_call_key] = auto_filled_providers_on_call
                        st.session_state[providers_on_call_seed_key] = auto_filled_providers_on_call
                        st.rerun()

        providers_on_call = st.text_area(
            "Providers on Call",
            placeholder="List providers on call for this day.",
            key=providers_on_call_key,
            height=80,
        )
        st.markdown("**Barriers/Gaps Identified**")
        providers_in_clinic = st.text_area(
            "Providers in clinic",
            placeholder="Clinic coverage, rooming balance, assignment gaps...",
            key=f"{panel_key}_providers_in_clinic",
            height=70,
        )
        out_pto = st.text_area(
            "Out/PTO",
            placeholder="Who is out and how coverage is being handled.",
            key=f"{panel_key}_out_pto",
            height=70,
        )
        break_coverage = st.text_area(
            "Break Coverage",
            placeholder="Lunch/break coverage plan.",
            key=f"{panel_key}_break_coverage",
            height=70,
        )
        need_help_with = st.text_area(
            "What do we need help with",
            value=(
                f"Open triage issues due today: {len(today_open)}\n"
                f"Escalations for leadership: {len(top_escalations)}\n"
                f"PSR handoffs waiting: {len(waiting_psr)}"
            ),
            key=f"{panel_key}_need_help_with",
            height=90,
        )
        items_to_work_on = st.text_area(
            "Items we need to work on",
            value=(
                f"Open lead queue issues: {len(open_issues)}\n"
                f"Relationship follow-ups due: {len(relationship_followups)}"
            ),
            key=f"{panel_key}_items_to_work_on",
            height=90,
        )
        safety_concerns = st.text_area(
            "Safety Concerns/Great Catch",
            placeholder="Capture safety concerns or great catches from the team.",
            key=f"{panel_key}_safety_concerns",
            height=80,
        )
        goals_identified = st.text_area(
            "Goals Identified",
            placeholder="Daily huddle goals identified by the team.",
            key=f"{panel_key}_goals_identified",
            height=80,
        )
        dad_joke = st.text_input(
            "Dad Joke",
            placeholder="Optional team icebreaker.",
            key=f"{panel_key}_dad_joke",
        )
        barriers_gaps_closeout = st.text_area(
            "Barriers/Gaps Identified (closeout)",
            placeholder="Capture end-of-huddle barriers/gaps updates.",
            key=f"{panel_key}_barriers_gaps_closeout",
            height=80,
        )

        recap_sent_to = st.text_input(
            "Recap sent to",
            placeholder="PSR lead, clinic manager, supervisor",
            key=f"{panel_key}_recap_sent_to",
        )
        shift_notes = st.text_area(
            "End-of-day recap draft",
            placeholder="Wins, unresolved items, and tomorrow handoff.",
            key=f"{panel_key}_shift_notes",
            height=90,
        )

        priority_focus = (
            f"Attendees: {attendees.strip()}\n"
            "| Provider | Schedule Utilization | # of pts | # of open spots | Referral WQ Status | Oldest Referral Age | Waitlist/Recall List Status |\n"
            "| --- | --- | --- | --- | --- | --- | --- |\n"
            f"| {provider_label.strip() or '-'} | {schedule_utilization.strip() or '-'} | {int(pts_count)} | {int(open_spots)} | {referral_wq_status.strip() or '-'} | {oldest_referral_age.strip() or '-'} | {waitlist_recall_status.strip() or '-'} |\n\n"
            "Providers on Call:\n"
            f"{providers_on_call.strip()}\n\n"
            "Barriers/Gaps Identified:\n"
            f"- Providers in clinic: {providers_in_clinic.strip()}\n"
            f"- Out/PTO: {out_pto.strip()}\n"
            f"- Break Coverage: {break_coverage.strip()}\n"
            f"- What do we need help with: {need_help_with.strip()}\n"
            f"- Items we need to work on: {items_to_work_on.strip()}\n"
            f"- Safety Concerns/Great Catch: {safety_concerns.strip()}"
        )
        staffing_notes = (
            "Goals Identified:\n"
            f"{goals_identified.strip()}\n\n"
            "Dad Joke:\n"
            f"{dad_joke.strip()}"
        )
        escalation_notes = (
            "Barriers/Gaps Identified (closeout):\n"
            f"{barriers_gaps_closeout.strip()}"
        )

        huddle_date_label = huddle_date.strftime("%m/%d/%y") if hasattr(huddle_date, "strftime") else str(huddle_date)
        email_body = (
            f"Morning Huddle {huddle_date_label}\n\n"
            f"Attendees: {attendees.strip() or 'N/A'}\n\n"
            "Daily Metrics\n"
            f"Provider: {provider_label.strip() or '-'}\n"
            f"Schedule Utilization: {schedule_utilization.strip() or '-'}\n"
            f"# of pts: {int(pts_count)}\n"
            f"# of open spots: {int(open_spots)}\n"
            f"Referral WQ Status: {referral_wq_status.strip() or '-'}\n"
            f"Oldest Referral Age: {oldest_referral_age.strip() or '-'}\n"
            f"Waitlist/Recall List Status: {waitlist_recall_status.strip() or '-'}\n\n"
            f"{priority_focus}\n\n"
            f"{staffing_notes}\n\n"
            f"{escalation_notes}\n\n"
            f"Shift recap:\n{shift_notes.strip() or 'No notes'}\n"
        )

        copy_ready_key = f"{panel_key}_copy_email_ready"
        copy_text_key = f"{panel_key}_copy_email_text"
        button_cols = st.columns(2)
        with button_cols[0]:
            if st.button("Save huddle note", key=f"{panel_key}_save_huddle", type="primary"):
                add_lead_huddle_log(
                    huddle_date=huddle_date,
                    priority_focus=priority_focus,
                    staffing_notes=staffing_notes,
                    escalation_notes=escalation_notes,
                    recap_sent_to=recap_sent_to,
                    shift_notes=shift_notes,
                )
                st.success("Huddle note saved.")
                st.rerun()
        with button_cols[1]:
            if st.button("Copy Email Format", key=f"{panel_key}_copy_email", type="secondary"):
                st.session_state[copy_ready_key] = True

        if st.session_state.get(copy_ready_key):
            st.caption("Copy and paste this into your huddle email.")
            st.text_area(
                "Email format",
                value=email_body,
                key=copy_text_key,
                height=320,
            )

        st.markdown("#### Recent huddles")
        if huddle_logs:
            for log in huddle_logs[:10]:
                with st.expander(f"{log.get('huddle_date')} · recap to {log.get('recap_sent_to') or 'not set'}", expanded=False):
                    st.markdown(f"**Huddle Template Snapshot**\n{log.get('priority_focus') or 'No notes'}")
                    st.markdown(f"**Goals + Dad Joke**\n{log.get('staffing_notes') or 'No notes'}")
                    st.markdown(f"**Barriers/Gaps Closeout**\n{log.get('escalation_notes') or 'No notes'}")
                    st.markdown(f"**Shift recap**\n{log.get('shift_notes') or 'No notes'}")
        else:
            st.caption("No huddle logs saved yet.")
        st.markdown('</div>', unsafe_allow_html=True)

    with tab_lookup["SOP Playbook"]:
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.markdown('<div class="panel-title"><h3>SOP + Playbook Library</h3><span>Quick-reference workflows for consistent clinic execution</span></div>', unsafe_allow_html=True)

        with st.form(f"{panel_key}_sop_form", clear_on_submit=True):
            sop_cols = st.columns(3)
            with sop_cols[0]:
                sop_title = st.text_input("SOP title")
                sop_topic = st.selectbox("Topic", ["Rooming", "Referrals", "Imaging", "Authorizations", "Patient callbacks", "General"])
            with sop_cols[1]:
                sop_owner = st.text_input("Owner")
                sop_version = st.text_input("Version", value="v1.0")
            with sop_cols[2]:
                sop_status = st.selectbox("Status", ["active", "draft", "archived"], index=0)
                sop_link = st.text_input("Link (optional)")
            sop_steps = st.text_area("Quick steps", height=110, placeholder="Step-by-step playbook summary")
            submit_sop = st.form_submit_button("Add SOP entry", type="primary")

        if submit_sop:
            if not sop_title.strip():
                st.warning("SOP title is required.")
            else:
                add_lead_sop_entry(
                    title=sop_title,
                    topic=sop_topic,
                    owner_name=sop_owner,
                    version_tag=sop_version,
                    quick_steps=sop_steps,
                    link_url=sop_link,
                    status=sop_status,
                )
                st.success("SOP entry added.")
                st.rerun()

        sop_search = st.text_input("Search SOP library", key=f"{panel_key}_sop_search", placeholder="Topic, title, or owner")
        query = (sop_search or "").strip().lower()
        filtered_sops = [
            item
            for item in sop_entries
            if not query
            or query in str(item.get("title", "")).lower()
            or query in str(item.get("topic", "")).lower()
            or query in str(item.get("owner_name", "")).lower()
        ]
        if filtered_sops:
            for item in filtered_sops[:40]:
                with st.expander(f"{item.get('title')} · {item.get('topic')} · {item.get('version_tag')}", expanded=False):
                    st.markdown(f"**Owner:** {item.get('owner_name') or 'Not set'}")
                    st.markdown(f"**Status:** {item.get('status')}")
                    st.markdown(f"**Updated:** {item.get('updated_date')}")
                    st.markdown(item.get("quick_steps") or "No quick steps added yet.")
                    if item.get("link_url"):
                        st.write(item.get("link_url"))
        else:
            st.markdown('<div class="empty-state">No SOP entries match that search.</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    if "Relationship Tracker" in tab_lookup:
        with tab_lookup["Relationship Tracker"]:
            st.markdown('<div class="panel">', unsafe_allow_html=True)
            st.markdown('<div class="panel-title"><h3>Relationship Health Tracker</h3><span>Maintain strong working loops with PSR lead, manager, and supervisor</span></div>', unsafe_allow_html=True)

            with st.form(f"{panel_key}_relationship_form", clear_on_submit=True):
                rel_cols = st.columns(3)
                with rel_cols[0]:
                    person_name = st.text_input("Name")
                    role_label = st.text_input("Role/Title")
                with rel_cols[1]:
                    relationship_type = st.selectbox("Relationship lane", ["PSR lead", "Manager", "Supervisor", "Clinical staff"])
                    status_label = st.selectbox("Health", ["green", "yellow", "red"], index=0)
                with rel_cols[2]:
                    last_touch_date = st.date_input("Last touch", value=mountain_today())
                    next_follow_up_date = st.date_input("Next follow-up", value=mountain_today() + timedelta(days=7))
                open_asks = st.text_area("Open asks", height=80)
                recent_win = st.text_area("Recent win", height=80)
                rel_notes = st.text_area("Notes", height=80)
                submit_relationship = st.form_submit_button("Save touchpoint", type="primary")

            if submit_relationship:
                if not person_name.strip():
                    st.warning("Name is required.")
                else:
                    add_lead_relationship_touchpoint(
                        person_name=person_name,
                        role_label=role_label,
                        relationship_type=relationship_type,
                        status_label=status_label,
                        last_touch_date=last_touch_date,
                        next_follow_up_date=next_follow_up_date,
                        open_asks=open_asks,
                        recent_win=recent_win,
                        notes=rel_notes,
                    )
                    st.success("Relationship touchpoint saved.")
                    st.rerun()

            due_followups = sorted(
                relationship_touchpoints,
                key=lambda item: item.get("next_follow_up_date") or date.max,
            )
            if due_followups:
                for item in due_followups[:30]:
                    item_id = item.get("id")
                    followup_date = item.get("next_follow_up_date")
                    due_flag = " (due)" if followup_date and followup_date <= mountain_today() else ""
                    with st.expander(f"{item.get('person_name')} · {item.get('relationship_type')} · {item.get('status_label')}{due_flag}", expanded=False):
                        st.markdown(f"**Role:** {item.get('role_label') or 'Not set'}")
                        st.markdown(f"**Last touch:** {item.get('last_touch_date') or 'Not set'}")
                        st.markdown(f"**Next follow-up:** {followup_date or 'Not set'}")
                        if item.get("open_asks"):
                            st.markdown(f"**Open asks:** {item.get('open_asks')}")
                        if item.get("recent_win"):
                            st.markdown(f"**Recent win:** {item.get('recent_win')}")
                        if item.get("notes"):
                            st.markdown(f"**Notes:** {item.get('notes')}")
                        if st.button("Log touch today + move follow-up 7 days", key=f"{panel_key}_touch_{item_id}"):
                            update_lead_relationship_touchpoint(
                                item_id,
                                last_touch_date=mountain_today(),
                                next_follow_up_date=mountain_today() + timedelta(days=7),
                            )
                            st.rerun()
            else:
                st.caption("No relationship touchpoints tracked yet.")
            st.markdown('</div>', unsafe_allow_html=True)

    if "Preceptor Sign-offs" in tab_lookup:
        with tab_lookup["Preceptor Sign-offs"]:
            st.markdown('<div class="panel">', unsafe_allow_html=True)
            st.markdown('<div class="panel-title"><h3>Preceptor Skill Sign-offs</h3><span>Track onboarding skills and formal sign-off status</span></div>', unsafe_allow_html=True)
    
            with st.form(f"{panel_key}_signoff_form", clear_on_submit=True):
                signoff_cols = st.columns(3)
                with signoff_cols[0]:
                    signoff_staff_name = st.text_input("Staff name")
                    signoff_role = st.selectbox("Role", ["Medical Assistant", "Extern", "Nurse", "Other"])
                with signoff_cols[1]:
                    signoff_skill = st.text_input("Skill")
                    signoff_due_date = st.date_input("Sign-off due date", value=mountain_today() + timedelta(days=7))
                with signoff_cols[2]:
                    signoff_status = st.selectbox("Status", ["pending", "in_progress", "signed_off"], index=0)
                    signoff_by = st.text_input("Signed off by", value="")
                signoff_notes = st.text_area("Notes", height=90)
                submit_signoff = st.form_submit_button("Add skill sign-off", type="primary")
    
            if submit_signoff:
                if not signoff_staff_name.strip() or not signoff_skill.strip():
                    st.warning("Staff name and skill are required.")
                else:
                    add_lead_skill_signoff(
                        staff_name=signoff_staff_name,
                        role_label=signoff_role,
                        skill_name=signoff_skill,
                        due_date=signoff_due_date,
                        notes=signoff_notes,
                        status=signoff_status,
                        signed_off_date=mountain_today() if signoff_status == "signed_off" else None,
                        signed_off_by=signoff_by,
                    )
                    st.success("Skill sign-off added.")
                    st.rerun()
    
            st.markdown("#### Skill tracker")
            ordered_signoffs = sorted(
                skill_signoffs,
                key=lambda item: (
                    0 if item.get("status") in ("pending", "in_progress") else 1,
                    item.get("due_date") or date.max,
                ),
            )
            if ordered_signoffs:
                default_signer = st.text_input("Default signer", value="", key=f"{panel_key}_default_signer")
                for item in ordered_signoffs[:40]:
                    signoff_id = item.get("id")
                    due_label = format_due(item.get("due_date"))
                    with st.expander(f"{item.get('staff_name')} · {item.get('skill_name')} · {item.get('status')} · due {due_label}", expanded=False):
                        st.markdown(f"**Role:** {item.get('role_label') or 'Not set'}")
                        if item.get("notes"):
                            st.markdown(f"**Notes:** {item.get('notes')}")
                        if item.get("signed_off_date"):
                            st.caption(f"Signed off on {item.get('signed_off_date')} by {item.get('signed_off_by') or 'Not recorded'}")
    
                        action_cols = st.columns(3)
                        if action_cols[0].button("Mark In Progress", key=f"{panel_key}_signoff_progress_{signoff_id}"):
                            update_lead_skill_signoff(signoff_id, status="in_progress")
                            st.rerun()
                        if action_cols[1].button("Sign Off", key=f"{panel_key}_signoff_complete_{signoff_id}"):
                            update_lead_skill_signoff(
                                signoff_id,
                                status="signed_off",
                                signed_off_date=mountain_today(),
                                signed_off_by=default_signer,
                            )
                            st.rerun()
                        if action_cols[2].button("Reopen", key=f"{panel_key}_signoff_reopen_{signoff_id}"):
                            update_lead_skill_signoff(signoff_id, status="pending", signed_off_date=None)
                            st.rerun()
    
                        render_record_attachments(
                            "Preceptor Sign-offs",
                            "skill_signoff",
                            signoff_id,
                            f"{item.get('staff_name')} - {item.get('skill_name')}",
                        )
            else:
                st.caption("No skill sign-offs tracked yet.")
            st.markdown('</div>', unsafe_allow_html=True)
    
    if "Education Liaison" in tab_lookup:
        with tab_lookup["Education Liaison"]:
            st.markdown('<div class="panel">', unsafe_allow_html=True)
            st.markdown('<div class="panel-title"><h3>Education Team Liaison</h3><span>Track hospital education requests, prep, and delivery status</span></div>', unsafe_allow_html=True)
    
            with st.form(f"{panel_key}_education_form", clear_on_submit=True):
                edu_cols = st.columns(3)
                with edu_cols[0]:
                    edu_title = st.text_input("Request title")
                    edu_team = st.text_input("Requesting team", value="Hospital Education Team")
                with edu_cols[1]:
                    edu_topic = st.selectbox("Topic", ["Clinical skills", "Workflow orientation", "Safety", "Sterile processing", "Other"])
                    edu_priority = st.selectbox("Priority", ["high", "medium", "low"], index=1)
                with edu_cols[2]:
                    edu_needed_by = st.date_input("Needed by", value=mountain_today() + timedelta(days=5))
                    edu_session_date = st.date_input("Session date", value=mountain_today() + timedelta(days=7))
                edu_owner = st.text_input("Owner", value="")
                edu_notes = st.text_area("Notes", height=90)
                submit_edu = st.form_submit_button("Add education request", type="primary")
    
            if submit_edu:
                if not edu_title.strip():
                    st.warning("Request title is required.")
                else:
                    add_lead_education_request(
                        request_title=edu_title,
                        requesting_team=edu_team,
                        topic=edu_topic,
                        priority=edu_priority,
                        needed_by_date=edu_needed_by,
                        session_date=edu_session_date,
                        owner_name=edu_owner,
                        notes=edu_notes,
                    )
                    st.success("Education request saved.")
                    st.rerun()
    
            st.markdown("#### Request queue")
            ordered_requests = sorted(
                education_requests,
                key=lambda item: (
                    0 if item.get("status") in ("new", "preparing") else 1,
                    item.get("needed_by_date") or date.max,
                ),
            )
            if ordered_requests:
                for item in ordered_requests[:40]:
                    request_id = item.get("id")
                    with st.expander(f"{item.get('request_title')} · {item.get('status')} · need by {item.get('needed_by_date')}", expanded=False):
                        st.markdown(f"**Team:** {item.get('requesting_team') or 'Not set'}")
                        st.markdown(f"**Topic:** {item.get('topic')}")
                        st.markdown(f"**Priority:** {item.get('priority')}")
                        st.markdown(f"**Owner:** {item.get('owner_name') or 'Unassigned'}")
                        if item.get("notes"):
                            st.markdown(f"**Notes:** {item.get('notes')}")
    
                        action_cols = st.columns(4)
                        if action_cols[0].button("Preparing", key=f"{panel_key}_edu_prepare_{request_id}"):
                            update_lead_education_request(request_id, status="preparing")
                            st.rerun()
                        if action_cols[1].button("Delivered", key=f"{panel_key}_edu_delivered_{request_id}"):
                            update_lead_education_request(request_id, status="delivered", session_date=mountain_today())
                            st.rerun()
                        if action_cols[2].button("Close", key=f"{panel_key}_edu_close_{request_id}"):
                            update_lead_education_request(request_id, status="closed")
                            st.rerun()
                        if action_cols[3].button("Reopen", key=f"{panel_key}_edu_reopen_{request_id}"):
                            update_lead_education_request(request_id, status="new")
                            st.rerun()
    
                        render_record_attachments(
                            "Education Liaison",
                            "education_request",
                            request_id,
                            item.get("request_title") or "Education Request",
                        )
            else:
                st.caption("No education liaison requests tracked yet.")
            st.markdown('</div>', unsafe_allow_html=True)
    
    if "Autoclave Maintenance" in tab_lookup:
        with tab_lookup["Autoclave Maintenance"]:
            st.markdown('<div class="panel">', unsafe_allow_html=True)
            st.markdown('<div class="panel-title"><h3>Autoclave Maintenance</h3><span>Monitor sterilizer checks, service, and overdue maintenance</span></div>', unsafe_allow_html=True)
    
            with st.form(f"{panel_key}_autoclave_form", clear_on_submit=True):
                auto_cols = st.columns(3)
                with auto_cols[0]:
                    auto_unit = st.text_input("Autoclave unit")
                    auto_type = st.selectbox("Maintenance type", ["Spore test", "Biological indicator", "Routine cleaning", "Preventive service", "Repair"])
                with auto_cols[1]:
                    auto_frequency = st.selectbox("Frequency", ["Daily", "Weekly", "Monthly", "Quarterly", "As needed"], index=1)
                    auto_next_due = st.date_input("Next due", value=mountain_today() + timedelta(days=7))
                with auto_cols[2]:
                    auto_last_done = st.date_input("Last completed", value=mountain_today() - timedelta(days=7))
                    auto_status = st.selectbox("Status", ["due_soon", "overdue", "completed", "out_of_service"], index=0)
                auto_owner = st.text_input("Owner")
                auto_vendor = st.text_input("Vendor/contact")
                auto_notes = st.text_area("Notes", height=90)
                submit_auto = st.form_submit_button("Add maintenance item", type="primary")
    
            if submit_auto:
                if not auto_unit.strip():
                    st.warning("Autoclave unit is required.")
                else:
                    add_autoclave_maintenance_item(
                        unit_label=auto_unit,
                        maintenance_type=auto_type,
                        frequency_label=auto_frequency,
                        next_due_date=auto_next_due,
                        last_completed_date=auto_last_done,
                        status=auto_status,
                        owner_name=auto_owner,
                        vendor_contact=auto_vendor,
                        notes=auto_notes,
                    )
                    st.success("Maintenance item saved.")
                    st.rerun()
    
            st.markdown("#### Maintenance board")
            ordered_auto = sorted(
                autoclave_items,
                key=lambda item: (
                    0 if item.get("status") == "overdue" else 1 if item.get("status") == "due_soon" else 2,
                    item.get("next_due_date") or date.max,
                ),
            )
            if ordered_auto:
                for item in ordered_auto[:50]:
                    auto_id = item.get("id")
                    with st.expander(f"{item.get('unit_label')} · {item.get('maintenance_type')} · {item.get('status')} · next due {item.get('next_due_date')}", expanded=False):
                        st.markdown(f"**Frequency:** {item.get('frequency_label')}")
                        st.markdown(f"**Owner:** {item.get('owner_name') or 'Not set'}")
                        if item.get("vendor_contact"):
                            st.markdown(f"**Vendor/contact:** {item.get('vendor_contact')}")
                        if item.get("notes"):
                            st.markdown(f"**Notes:** {item.get('notes')}")
    
                        action_cols = st.columns(4)
                        if action_cols[0].button("Mark Completed", key=f"{panel_key}_auto_complete_{auto_id}"):
                            completed_on = mountain_today()
                            next_due = autoclave_next_due_date(
                                completed_on,
                                item.get("frequency_label"),
                                item.get("next_due_date"),
                            )
                            update_autoclave_maintenance_item(
                                auto_id,
                                status="completed",
                                last_completed_date=completed_on,
                                next_due_date=next_due,
                            )
                            st.rerun()
                        if action_cols[1].button("Mark Due Soon", key=f"{panel_key}_auto_due_{auto_id}"):
                            update_autoclave_maintenance_item(auto_id, status="due_soon")
                            st.rerun()
                        if action_cols[2].button("Mark Overdue", key=f"{panel_key}_auto_overdue_{auto_id}"):
                            update_autoclave_maintenance_item(auto_id, status="overdue")
                            st.rerun()
                        if action_cols[3].button("Out of Service", key=f"{panel_key}_auto_oos_{auto_id}"):
                            update_autoclave_maintenance_item(auto_id, status="out_of_service")
                            st.rerun()
    
                        render_record_attachments(
                            "Autoclave Maintenance",
                            "autoclave_item",
                            auto_id,
                            f"{item.get('unit_label')} - {item.get('maintenance_type')}",
                        )
            else:
                st.caption("No autoclave maintenance items tracked yet.")
            st.markdown('</div>', unsafe_allow_html=True)
    
    if "Documents" in tab_lookup:
        with tab_lookup["Documents"]:
            st.markdown('<div class="panel">', unsafe_allow_html=True)
            st.markdown('<div class="panel-title"><h3>MA Lead Documents</h3><span>Upload and organize files for each leadership section</span></div>', unsafe_allow_html=True)

            section_options = [
                "Command Center",
                "Clinical Triage Queue",
                "Daily Huddle",
                "SOP Playbook",
                "Relationship Tracker",
                "Preceptor Sign-offs",
                "Education Liaison",
                "Autoclave Maintenance",
                "General",
            ]

            with st.form(f"{panel_key}_documents_form", clear_on_submit=True):
                doc_cols = st.columns(3)
                with doc_cols[0]:
                    doc_section = st.selectbox("Section", section_options)
                    doc_title = st.text_input("Title (optional)")
                with doc_cols[1]:
                    doc_uploaded_by = st.text_input("Uploaded by")
                    doc_notes = st.text_area("Notes", height=90)
                with doc_cols[2]:
                    doc_files = st.file_uploader(
                        "Select file(s)",
                        accept_multiple_files=True,
                        key=f"{panel_key}_documents_uploader",
                        help="Upload documents, images, PDFs, spreadsheets, or other support files.",
                    )
                    st.caption("For monthly on-call schedules, use a file with a Date column and a Provider/On Call column.")
                submit_docs = st.form_submit_button("Upload files", type="primary")

            if submit_docs:
                if not doc_files:
                    st.warning("Select at least one file to upload.")
                else:
                    uploaded_count = 0
                    skipped_count = 0
                    for doc_file in doc_files:
                        file_bytes = doc_file.getvalue()
                        if len(file_bytes) > 25 * 1024 * 1024:
                            skipped_count += 1
                            continue
                        add_lead_document(
                            section_key=doc_section,
                            title=doc_title,
                            file_name=doc_file.name,
                            file_mime=getattr(doc_file, "type", None),
                            file_bytes=file_bytes,
                            notes=doc_notes,
                            uploaded_by=doc_uploaded_by,
                        )
                        uploaded_count += 1

                    if uploaded_count:
                        st.success(f"Uploaded {uploaded_count} file(s).")
                    if skipped_count:
                        st.warning(f"Skipped {skipped_count} file(s) over 25 MB.")
                    if uploaded_count:
                        st.rerun()

            filter_cols = st.columns(2)
            with filter_cols[0]:
                section_filter = st.selectbox(
                    "Filter by section",
                    ["All sections"] + section_options,
                    key=f"{panel_key}_documents_section_filter",
                )
            with filter_cols[1]:
                document_query = st.text_input(
                    "Search files",
                    key=f"{panel_key}_documents_query",
                    placeholder="Title, filename, notes, or uploader",
                )

            normalized_query = (document_query or "").strip().lower()
            visible_documents = []
            for item in lead_documents:
                if section_filter != "All sections" and item.get("section_key") != section_filter:
                    continue
                if normalized_query:
                    searchable = " ".join(
                        [
                            str(item.get("title") or ""),
                            str(item.get("file_name") or ""),
                            str(item.get("notes") or ""),
                            str(item.get("uploaded_by") or ""),
                        ]
                    ).lower()
                    if normalized_query not in searchable:
                        continue
                visible_documents.append(item)

            st.caption(f"Showing {len(visible_documents)} of {len(lead_documents)} file(s)")
            if visible_documents:
                pdf_documents = []
                for item in visible_documents:
                    file_bytes = item.get("file_bytes")
                    if isinstance(file_bytes, memoryview):
                        file_bytes = bytes(file_bytes)
                    looks_like_pdf = (str(item.get("file_mime") or "").lower() == "application/pdf") or str(item.get("file_name") or "").lower().endswith(".pdf")
                    if file_bytes and looks_like_pdf:
                        pdf_documents.append((item, file_bytes))

                if pdf_documents:
                    st.markdown("#### PDF Preview")
                    preview_options = [
                        f"{doc.get('file_name') or 'document.pdf'} | {doc.get('section_key') or 'General'} | {doc.get('created_date') or 'unknown date'}"
                        for doc, _ in pdf_documents
                    ]
                    selected_preview_label = st.selectbox(
                        "Select a PDF to preview",
                        preview_options,
                        key=f"{panel_key}_documents_pdf_preview_select",
                    )
                    selected_preview_index = preview_options.index(selected_preview_label)
                    selected_preview_doc, selected_preview_bytes = pdf_documents[selected_preview_index]
                    selected_preview_doc_id = selected_preview_doc.get("id")
                    selected_preview_start_page = int(
                        st.number_input(
                            "Preview start page",
                            min_value=1,
                            value=1,
                            step=1,
                            key=f"{panel_key}_documents_pdf_preview_start_page_{selected_preview_doc_id}",
                        )
                    )
                    page_sections._render_protocol_pdf_preview(
                        st,
                        file_bytes=selected_preview_bytes,
                        file_mime=selected_preview_doc.get("file_mime"),
                        file_name=selected_preview_doc.get("file_name") or "document.pdf",
                        height=500,
                        start_page=selected_preview_start_page,
                    )

                for item in visible_documents[:120]:
                    doc_id = item.get("id")
                    file_bytes = item.get("file_bytes")
                    if isinstance(file_bytes, memoryview):
                        file_bytes = bytes(file_bytes)
                    with st.expander(f"{item.get('title') or item.get('file_name')} · {item.get('section_key')} · {item.get('created_date')}", expanded=False):
                        st.markdown(f"**File:** {item.get('file_name')}")
                        st.markdown(f"**Section:** {item.get('section_key')}")
                        if item.get("record_type") and item.get("record_id") is not None:
                            st.markdown(f"**Linked record:** {item.get('record_type')} #{item.get('record_id')}")
                        st.markdown(f"**Uploaded by:** {item.get('uploaded_by') or 'Not set'}")
                        if item.get("notes"):
                            st.markdown(f"**Notes:** {item.get('notes')}")
                        button_cols = st.columns(2)
                        with button_cols[0]:
                            st.download_button(
                                "Download",
                                data=file_bytes,
                                file_name=item.get("file_name") or f"document_{doc_id}",
                                mime=item.get("file_mime") or "application/octet-stream",
                                key=f"{panel_key}_doc_download_{doc_id}",
                            )
                        with button_cols[1]:
                            if st.button("Delete", key=f"{panel_key}_doc_delete_{doc_id}"):
                                delete_lead_document(doc_id)
                                st.success("Document deleted.")
                                st.rerun()
            else:
                st.caption("No documents found for the selected filters.")

            st.markdown('</div>', unsafe_allow_html=True)

    with tab_lookup["Biweekly Check-ins"]:
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.markdown('<div class="panel-title"><h3>Biweekly MA Check-ins</h3><span>Run repeatable 1:1s, track actions, and export leadership summaries</span></div>', unsafe_allow_html=True)

        today_value = mountain_today()
        cadence_days = max(7, int(biweekly_settings.get("cadence_days") or 14))
        reminder_lead_days = max(0, int(biweekly_settings.get("reminder_lead_days") or 2))
        cycle_start = today_value - timedelta(days=cadence_days - 1)

        roster_names = sorted(
            {
                str(item.get("ma_name") or "").strip()
                for item in ma_assignments + biweekly_checkins + biweekly_actions
                if str(item.get("ma_name") or "").strip()
            }
        )

        latest_checkin_by_ma = {}
        for item in biweekly_checkins:
            ma_name = str(item.get("ma_name") or "").strip()
            checkin_date = item.get("checkin_date")
            if not ma_name or not isinstance(checkin_date, date):
                continue
            current = latest_checkin_by_ma.get(ma_name)
            if not current or checkin_date > current.get("checkin_date"):
                latest_checkin_by_ma[ma_name] = item

        queue_rows = []
        for ma_name in roster_names:
            latest = latest_checkin_by_ma.get(ma_name)
            last_date = latest.get("checkin_date") if latest else None
            next_due = latest.get("next_due_date") if latest else today_value
            if not isinstance(next_due, date):
                next_due = (last_date + timedelta(days=cadence_days)) if isinstance(last_date, date) else today_value
            queue_rows.append(
                {
                    "ma_name": ma_name,
                    "last_date": last_date,
                    "next_due": next_due,
                    "status": (latest.get("status") if latest else "needs_support") or "needs_support",
                    "latest": latest,
                    "is_overdue": next_due < today_value,
                    "is_due_now": next_due <= (today_value + timedelta(days=reminder_lead_days)),
                }
            )

        queue_rows = sorted(
            queue_rows,
            key=lambda item: (
                0 if item["is_overdue"] else 1 if item["is_due_now"] else 2,
                item["next_due"],
                item["ma_name"].lower(),
            ),
        )

        open_actions_by_ma = Counter()
        overdue_open_actions_by_ma = Counter()
        for action_item in biweekly_actions:
            if action_item.get("status") != "open":
                continue
            ma_name = str(action_item.get("ma_name") or "").strip()
            if not ma_name:
                continue
            open_actions_by_ma[ma_name] += 1
            action_due_date = action_item.get("due_date")
            if isinstance(action_due_date, date) and action_due_date < today_value:
                overdue_open_actions_by_ma[ma_name] += 1

        status_priority = {"at_risk": 0, "needs_support": 1, "on_track": 2}
        suggested_ma_name = ""
        if queue_rows:
            prioritized_queue = sorted(
                queue_rows,
                key=lambda item: (
                    0 if item["is_overdue"] else 1 if item["is_due_now"] else 2,
                    -overdue_open_actions_by_ma.get(item["ma_name"], 0),
                    -open_actions_by_ma.get(item["ma_name"], 0),
                    status_priority.get(str(item.get("status") or "on_track"), 3),
                    item["next_due"],
                    item["ma_name"].lower(),
                ),
            )
            suggested_ma_name = prioritized_queue[0]["ma_name"]

        completed_this_cycle = [item for item in biweekly_checkins if isinstance(item.get("checkin_date"), date) and item.get("checkin_date") >= cycle_start]
        open_actions = [item for item in biweekly_actions if item.get("status") == "open"]
        overdue_actions = [item for item in open_actions if isinstance(item.get("due_date"), date) and item.get("due_date") < today_value]
        due_now_count = len([item for item in queue_rows if item["is_due_now"]])
        overdue_count = len([item for item in queue_rows if item["is_overdue"]])

        confidence_values = [item.get("confidence_score") for item in completed_this_cycle if isinstance(item.get("confidence_score"), int)]
        workload_values = [item.get("workload_score") for item in completed_this_cycle if isinstance(item.get("workload_score"), int)]
        average_confidence = (sum(confidence_values) / len(confidence_values)) if confidence_values else None
        average_workload = (sum(workload_values) / len(workload_values)) if workload_values else None

        metric_cols = st.columns(5)
        metric_cols[0].metric("Check-ins due now", due_now_count)
        metric_cols[1].metric("Completed this cycle", len(completed_this_cycle))
        metric_cols[2].metric("Overdue check-ins", overdue_count)
        metric_cols[3].metric("Open follow-up actions", len(open_actions))
        metric_cols[4].metric("Overdue actions", len(overdue_actions))
        st.caption(
            f"Cycle window: {cycle_start.strftime('%b %d')} - {today_value.strftime('%b %d')} · "
            f"Avg confidence: {average_confidence:.1f}/5" if average_confidence is not None else
            f"Cycle window: {cycle_start.strftime('%b %d')} - {today_value.strftime('%b %d')} · Avg confidence: n/a"
        )
        if average_workload is not None:
            st.caption(f"Avg workload: {average_workload:.1f}/5")

        with st.expander("Biweekly check-in settings and template", expanded=False):
            setting_cols = st.columns(3)
            cadence_input = setting_cols[0].number_input("Cadence (days)", min_value=7, max_value=30, value=cadence_days, step=1)
            reminder_input = setting_cols[1].number_input("Reminder lead days", min_value=0, max_value=14, value=reminder_lead_days, step=1)
            include_private_export = setting_cols[2].checkbox(
                "Include private notes in export",
                value=bool(biweekly_settings.get("include_private_notes_in_export")),
            )

            template_wins = st.text_input("Prompt - Wins", value=biweekly_template.get("wins_prompt") or MA_LEAD_BIWEEKLY_TEMPLATE_DEFAULTS["wins_prompt"])
            template_blockers = st.text_input("Prompt - Blockers", value=biweekly_template.get("blockers_prompt") or MA_LEAD_BIWEEKLY_TEMPLATE_DEFAULTS["blockers_prompt"])
            template_clarifications = st.text_input("Prompt - Clarifications", value=biweekly_template.get("clarifications_prompt") or MA_LEAD_BIWEEKLY_TEMPLATE_DEFAULTS["clarifications_prompt"])
            template_coaching = st.text_input("Prompt - Coaching Focus", value=biweekly_template.get("coaching_focus_prompt") or MA_LEAD_BIWEEKLY_TEMPLATE_DEFAULTS["coaching_focus_prompt"])
            template_support = st.text_input("Prompt - Support Needed", value=biweekly_template.get("support_needed_prompt") or MA_LEAD_BIWEEKLY_TEMPLATE_DEFAULTS["support_needed_prompt"])

            if st.button("Save biweekly settings", key=f"{panel_key}_save_biweekly_settings", type="secondary"):
                _save_ma_lead_settings(
                    updated_biweekly_settings={
                        "cadence_days": int(cadence_input),
                        "reminder_lead_days": int(reminder_input),
                        "include_private_notes_in_export": bool(include_private_export),
                    },
                    updated_biweekly_template={
                        "wins_prompt": template_wins,
                        "blockers_prompt": template_blockers,
                        "clarifications_prompt": template_clarifications,
                        "coaching_focus_prompt": template_coaching,
                        "support_needed_prompt": template_support,
                    },
                )
                st.success("Biweekly settings saved.")
                st.rerun()

        st.markdown("#### Due queue")
        if queue_rows:
            for row in queue_rows:
                due_text = row["next_due"].strftime("%b %d") if isinstance(row.get("next_due"), date) else "n/a"
                status_tag = "overdue" if row["is_overdue"] else "due soon" if row["is_due_now"] else "on schedule"
                last_text = row["last_date"].strftime("%b %d") if isinstance(row.get("last_date"), date) else "none"
                with st.expander(f"{row['ma_name']} · next due {due_text} · {status_tag}", expanded=row["is_overdue"]):
                    st.caption(f"Last check-in: {last_text} · latest status: {row['status']}")
                    latest = row.get("latest") or {}
                    if latest.get("coaching_focus"):
                        st.markdown(f"**Latest coaching focus:** {latest.get('coaching_focus')}")
                    if latest.get("support_needed"):
                        st.markdown(f"**Latest support needed:** {latest.get('support_needed')}")
                    if st.button("Use this MA in check-in form", key=f"{panel_key}_queue_select_{row['ma_name']}"):
                        st.session_state[f"{panel_key}_biweekly_ma_name"] = row["ma_name"]
                        st.rerun()
        else:
            st.caption("No MA roster yet. Add MA assignments or create the first check-in.")

        st.markdown('<div style="height: 0.5rem;"></div>', unsafe_allow_html=True)
        st.markdown("#### Capture check-in")
        default_ma_name = st.session_state.get(f"{panel_key}_biweekly_ma_name")
        if not default_ma_name:
            default_ma_name = suggested_ma_name or (roster_names[0] if roster_names else "")
        if suggested_ma_name:
            st.caption(f"Suggested next check-in: {suggested_ma_name}")
        with st.form(f"{panel_key}_biweekly_checkin_form", clear_on_submit=False):
            form_cols = st.columns(3)
            with form_cols[0]:
                checkin_ma_name = st.text_input("MA name", value=default_ma_name)
                checkin_date_value = st.date_input("Check-in date", value=today_value)
                next_due_default = checkin_date_value + timedelta(days=cadence_days)
                next_due_date_value = st.date_input("Next due date", value=next_due_default)
            with form_cols[1]:
                checkin_status = st.selectbox("Current status", ["on_track", "needs_support", "at_risk"], index=1)
                confidence_score = st.slider("Confidence", min_value=1, max_value=5, value=3)
                workload_score = st.slider("Workload pressure", min_value=1, max_value=5, value=3)
            with form_cols[2]:
                checkin_public_notes = st.text_area("General notes", height=90, placeholder="Quick summary of this check-in")
                checkin_private_notes = st.text_area("Private notes", height=90, placeholder="Only include what should stay internal")

            wins_text = st.text_area(biweekly_template.get("wins_prompt") or MA_LEAD_BIWEEKLY_TEMPLATE_DEFAULTS["wins_prompt"], height=80)
            blockers_text = st.text_area(biweekly_template.get("blockers_prompt") or MA_LEAD_BIWEEKLY_TEMPLATE_DEFAULTS["blockers_prompt"], height=80)
            clarifications_text = st.text_area(biweekly_template.get("clarifications_prompt") or MA_LEAD_BIWEEKLY_TEMPLATE_DEFAULTS["clarifications_prompt"], height=80)
            coaching_focus_text = st.text_area(biweekly_template.get("coaching_focus_prompt") or MA_LEAD_BIWEEKLY_TEMPLATE_DEFAULTS["coaching_focus_prompt"], height=80)
            support_needed_text = st.text_area(biweekly_template.get("support_needed_prompt") or MA_LEAD_BIWEEKLY_TEMPLATE_DEFAULTS["support_needed_prompt"], height=80)

            follow_up_actions_text = st.text_area(
                "Follow-up actions (one per line: Action | Owner | YYYY-MM-DD)",
                height=90,
                placeholder="Confirm refill workflow SOP update | MA Lead | 2026-07-03",
            )

            submit_checkin = st.form_submit_button("Save check-in", type="primary")

        if submit_checkin:
            if not checkin_ma_name.strip():
                st.warning("MA name is required.")
            else:
                now_text = datetime.now(MOUNTAIN_TIMEZONE).isoformat(timespec="seconds")
                checkin_id = uuid4().hex
                updated_checkins = list(raw_biweekly_checkins)
                updated_checkins.append(
                    {
                        "checkin_id": checkin_id,
                        "ma_name": checkin_ma_name.strip(),
                        "checkin_date": checkin_date_value,
                        "next_due_date": next_due_date_value,
                        "status": checkin_status,
                        "confidence_score": int(confidence_score),
                        "workload_score": int(workload_score),
                        "wins": wins_text.strip(),
                        "blockers": blockers_text.strip(),
                        "clarifications": clarifications_text.strip(),
                        "coaching_focus": coaching_focus_text.strip(),
                        "support_needed": support_needed_text.strip(),
                        "public_notes": checkin_public_notes.strip(),
                        "private_notes": checkin_private_notes.strip(),
                        "created_at": now_text,
                        "updated_at": now_text,
                    }
                )

                updated_actions = list(raw_biweekly_actions)
                for line in follow_up_actions_text.splitlines():
                    trimmed = line.strip()
                    if not trimmed:
                        continue
                    parts = [part.strip() for part in trimmed.split("|")]
                    action_text = parts[0] if parts else ""
                    owner_name = parts[1] if len(parts) >= 2 and parts[1] else "MA Lead"
                    due_date_value = parse_date_value(parts[2]) if len(parts) >= 3 else None
                    if not due_date_value:
                        due_date_value = checkin_date_value + timedelta(days=7)
                    if not action_text:
                        continue
                    updated_actions.append(
                        {
                            "action_id": uuid4().hex,
                            "checkin_id": checkin_id,
                            "ma_name": checkin_ma_name.strip(),
                            "action_text": action_text,
                            "owner_name": owner_name,
                            "due_date": due_date_value,
                            "status": "open",
                            "completed_date": None,
                            "notes": "",
                            "created_at": now_text,
                            "updated_at": now_text,
                        }
                    )

                st.session_state[f"{panel_key}_biweekly_ma_name"] = checkin_ma_name.strip()
                _save_ma_lead_settings(
                    updated_biweekly_checkins=updated_checkins,
                    updated_biweekly_actions=updated_actions,
                )
                st.success("Biweekly check-in saved.")
                st.rerun()

        st.markdown('<div style="height: 0.5rem;"></div>', unsafe_allow_html=True)
        st.markdown("#### Follow-up action tracker")
        action_filter = st.selectbox("Action view", ["Open", "Completed", "All"], index=0, key=f"{panel_key}_biweekly_action_filter")
        visible_actions = biweekly_actions
        if action_filter == "Open":
            visible_actions = [item for item in biweekly_actions if item.get("status") == "open"]
        elif action_filter == "Completed":
            visible_actions = [item for item in biweekly_actions if item.get("status") == "completed"]

        if visible_actions:
            for item in visible_actions[:120]:
                due_label = item.get("due_date").strftime("%b %d") if isinstance(item.get("due_date"), date) else "No due date"
                status_label_text = item.get("status") or "open"
                with st.expander(f"{item.get('ma_name')} · {item.get('action_text')} · {status_label_text} · due {due_label}", expanded=False):
                    st.caption(f"Owner: {item.get('owner_name') or 'MA Lead'}")
                    if item.get("notes"):
                        st.caption(item.get("notes"))
                    action_cols = st.columns(3)
                    source_index = item.get("source_index")
                    if action_cols[0].button("Mark completed", key=f"{panel_key}_action_complete_{item.get('action_id')}"):
                        if source_index is not None and source_index < len(raw_biweekly_actions):
                            raw_biweekly_actions[source_index].update(
                                {
                                    "status": "completed",
                                    "completed_date": today_value,
                                    "updated_at": datetime.now(MOUNTAIN_TIMEZONE).isoformat(timespec="seconds"),
                                }
                            )
                            _save_ma_lead_settings(updated_biweekly_actions=raw_biweekly_actions)
                            st.success("Action marked completed.")
                            st.rerun()
                    if action_cols[1].button("Reopen", key=f"{panel_key}_action_reopen_{item.get('action_id')}"):
                        if source_index is not None and source_index < len(raw_biweekly_actions):
                            raw_biweekly_actions[source_index].update(
                                {
                                    "status": "open",
                                    "completed_date": None,
                                    "updated_at": datetime.now(MOUNTAIN_TIMEZONE).isoformat(timespec="seconds"),
                                }
                            )
                            _save_ma_lead_settings(updated_biweekly_actions=raw_biweekly_actions)
                            st.success("Action reopened.")
                            st.rerun()
                    if action_cols[2].button("Delete", key=f"{panel_key}_action_delete_{item.get('action_id')}"):
                        if source_index is not None and source_index < len(raw_biweekly_actions):
                            updated_actions = [entry for idx, entry in enumerate(raw_biweekly_actions) if idx != source_index]
                            _save_ma_lead_settings(updated_biweekly_actions=updated_actions)
                            st.success("Action deleted.")
                            st.rerun()
        else:
            st.caption("No action items match this filter.")

        st.markdown('<div style="height: 0.5rem;"></div>', unsafe_allow_html=True)
        st.markdown("#### Check-in trends")
        status_counts = Counter(item.get("status") for item in completed_this_cycle if item.get("status"))
        trend_cols = st.columns(3)
        trend_cols[0].metric("On track", status_counts.get("on_track", 0))
        trend_cols[1].metric("Needs support", status_counts.get("needs_support", 0))
        trend_cols[2].metric("At risk", status_counts.get("at_risk", 0))

        if biweekly_checkins:
            recent_by_ma = {}
            for item in biweekly_checkins:
                ma_name = item.get("ma_name")
                if not ma_name or ma_name in recent_by_ma:
                    continue
                recent_by_ma[ma_name] = item
            for ma_name in sorted(recent_by_ma.keys())[:30]:
                item = recent_by_ma[ma_name]
                st.markdown(
                    f"- **{ma_name}** · {item.get('status')} · confidence {item.get('confidence_score')}/5 · "
                    f"workload {item.get('workload_score')}/5 · next due {item.get('next_due_date').strftime('%b %d') if isinstance(item.get('next_due_date'), date) else 'n/a'}"
                )
                if item.get("coaching_focus"):
                    st.caption(f"Coaching focus: {item.get('coaching_focus')}")
        else:
            st.caption("No check-ins saved yet.")

        st.markdown('<div style="height: 0.5rem;"></div>', unsafe_allow_html=True)
        st.markdown("#### Leadership summary export")
        summary_window_days = st.number_input("Summary lookback (days)", min_value=7, max_value=60, value=max(14, cadence_days), step=1, key=f"{panel_key}_biweekly_summary_days")
        summary_start = today_value - timedelta(days=int(summary_window_days) - 1)
        summary_checkins = [item for item in biweekly_checkins if isinstance(item.get("checkin_date"), date) and item.get("checkin_date") >= summary_start]
        summary_actions = [item for item in biweekly_actions if item.get("status") in ("open", "completed")]

        blocker_counter = Counter()
        for item in summary_checkins:
            raw_blockers = str(item.get("blockers") or "")
            for token in re.split(r"[\n;]+", raw_blockers):
                blocker_text = token.strip(" -\t").strip()
                if blocker_text:
                    blocker_counter[blocker_text] += 1
        top_blockers = blocker_counter.most_common(3)

        latest_support_requests = []
        latest_by_ma = {}
        for item in summary_checkins:
            ma_name = item.get("ma_name")
            checkin_date = item.get("checkin_date")
            if not ma_name or not isinstance(checkin_date, date):
                continue
            existing = latest_by_ma.get(ma_name)
            if not existing or checkin_date > existing.get("checkin_date"):
                latest_by_ma[ma_name] = item
        for ma_name, item in sorted(latest_by_ma.items()):
            support_text = str(item.get("support_needed") or "").strip()
            if support_text:
                latest_support_requests.append(f"- {ma_name}: {support_text}")

        completed_actions = [item for item in summary_actions if item.get("status") == "completed"]
        followup_rate = (len(completed_actions) / len(summary_actions)) if summary_actions else 0.0
        avg_conf_summary = (
            sum(item.get("confidence_score") for item in summary_checkins if isinstance(item.get("confidence_score"), int)) / max(1, len([item for item in summary_checkins if isinstance(item.get("confidence_score"), int)]))
            if summary_checkins else 0.0
        )
        avg_workload_summary = (
            sum(item.get("workload_score") for item in summary_checkins if isinstance(item.get("workload_score"), int)) / max(1, len([item for item in summary_checkins if isinstance(item.get("workload_score"), int)]))
            if summary_checkins else 0.0
        )

        blocker_lines = [f"- {label} ({count})" for label, count in top_blockers] or ["- No blocker themes captured."]
        support_lines = latest_support_requests or ["- No active support requests captured."]
        blocker_block = "\n".join(blocker_lines)
        support_block = "\n".join(support_lines)
        private_note_lines = []
        if biweekly_settings.get("include_private_notes_in_export"):
            private_note_lines = [
                f"- {item.get('ma_name')}: {item.get('private_notes')}"
                for item in summary_checkins
                if str(item.get("private_notes") or "").strip()
            ][:10]

        biweekly_summary_text = (
            f"Subject: MA Lead Biweekly Check-in Summary ({summary_start.isoformat()} to {today_value.isoformat()})\n"
            "\n"
            "Team Morale/Capacity Trend\n"
            f"- Check-ins captured: {len(summary_checkins)}\n"
            f"- Average confidence: {avg_conf_summary:.1f}/5\n"
            f"- Average workload pressure: {avg_workload_summary:.1f}/5\n"
            f"- Status mix: on_track={status_counts.get('on_track', 0)}, needs_support={status_counts.get('needs_support', 0)}, at_risk={status_counts.get('at_risk', 0)}\n"
            "\n"
            "Top Friction Themes\n"
            f"{blocker_block}\n"
            "\n"
            "Open Support Requests\n"
            f"{support_block}\n"
            "\n"
            "Follow-up Completion\n"
            f"- Follow-up completion rate: {int(round(followup_rate * 100))}% ({len(completed_actions)}/{len(summary_actions)})\n"
            f"- Open follow-up actions: {len([item for item in summary_actions if item.get('status') == 'open'])}\n"
        )

        if private_note_lines:
            biweekly_summary_text += "\nPrivate Notes Included\n" + "\n".join(private_note_lines) + "\n"

        st.text_area(
            "Generated biweekly summary",
            value=biweekly_summary_text,
            height=250,
            key=f"{panel_key}_biweekly_summary_preview",
        )
        st.download_button(
            "Download biweekly summary",
            data=biweekly_summary_text,
            file_name=f"ma_lead_biweekly_summary_{today_value.isoformat()}.txt",
            mime="text/plain",
            key=f"{panel_key}_download_biweekly_summary",
        )
        biweekly_copy_payload = json.dumps(biweekly_summary_text).replace("</", "<\\/")
        components.html(
            f"""
            <div style=\"display:flex; align-items:center; gap:0.6rem;\"> 
                <button id=\"{panel_key}_biweekly_copy_btn\" style=\"padding:0.35rem 0.75rem; border-radius:0.45rem; border:1px solid #64748b; background:#111827; color:#f8fafc; cursor:pointer;\">Copy biweekly summary</button>
                <span id=\"{panel_key}_biweekly_copy_status\" style=\"font-size:0.82rem; color:#0f766e;\"></span>
            </div>
            <script>
                const biweeklyPayload = {biweekly_copy_payload};
                const biweeklyButton = document.getElementById("{panel_key}_biweekly_copy_btn");
                const biweeklyStatus = document.getElementById("{panel_key}_biweekly_copy_status");
                biweeklyButton.addEventListener("click", async () => {{
                    try {{
                        await navigator.clipboard.writeText(biweeklyPayload);
                        biweeklyStatus.textContent = "Copied to clipboard.";
                    }} catch (error) {{
                        biweeklyStatus.textContent = "Copy blocked by browser. Use Ctrl+C from the preview box.";
                    }}
                }});
            </script>
            """,
            height=48,
        )

        st.markdown('</div>', unsafe_allow_html=True)

    if "Weekly Metrics Dashboard" in tab_lookup:
        with tab_lookup["Weekly Metrics Dashboard"]:
            st.markdown('<div class="panel">', unsafe_allow_html=True)
            st.markdown('<div class="panel-title"><h3>Weekly MA Lead Metrics Dashboard</h3><span>Track the core operations metrics every week and review trends</span></div>', unsafe_allow_html=True)
    
            week_col, caption_col = st.columns([1.2, 2.8])
            with week_col:
                metrics_anchor = st.date_input("Week of", value=mountain_today(), key=f"{panel_key}_metrics_week_anchor")
            selected_week_start = metrics_anchor - timedelta(days=metrics_anchor.weekday())
            selected_week_end = selected_week_start + timedelta(days=6)
            with caption_col:
                st.caption(f"Tracking window: {selected_week_start.strftime('%b %d')} - {selected_week_end.strftime('%b %d, %Y')} (Mon-Sun)")
    
            selected_week_key = selected_week_start.isoformat()
            selected_week_entry = weekly_metrics_log.get(selected_week_key, {"values": {}, "notes": "", "saved_at": ""})
            selected_week_values = dict(selected_week_entry.get("values") or {})
    
            metric_columns = st.columns(3)
            for index, metric in enumerate(weekly_metric_targets):
                metric_key = metric["key"]
                metric_label = metric["label"]
                unit = metric["unit"]
                target_value = float(metric["target"])
                value = selected_week_values.get(metric_key)
    
                value_text = "Not set"
                delta_text = f"Target {target_value:g}{unit}"
                if isinstance(value, (int, float)):
                    value_text = f"{float(value):g}{unit}"
                    variance = float(value) - target_value
                    if metric["direction"] == "higher_is_better":
                        delta_text = f"{variance:+g}{unit} vs target"
                    else:
                        delta_text = f"{-variance:+g}{unit} to target"
    
                with metric_columns[index % 3]:
                    st.metric(metric_label, value_text, delta=delta_text)
    
            st.markdown('<div style="height: 0.4rem;"></div>', unsafe_allow_html=True)
            with st.form(f"{panel_key}_weekly_metrics_form"):
                input_cols = st.columns(2)
                metric_inputs = {}
                for index, metric in enumerate(weekly_metric_targets):
                    metric_key = metric["key"]
                    with input_cols[index % 2]:
                        metric_inputs[metric_key] = st.number_input(
                            f"{metric['label']} ({metric['unit']})",
                            min_value=0.0,
                            step=1.0,
                            value=float(selected_week_values.get(metric_key, metric.get("target", 0.0))),
                            key=f"{panel_key}_weekly_metric_input_{metric_key}",
                        )
    
                weekly_notes = st.text_area(
                    "Weekly notes",
                    value=selected_week_entry.get("notes") or "",
                    height=90,
                    placeholder="What improved, what slipped, and what action to take next week",
                )
                save_weekly_metrics = st.form_submit_button("Save weekly metrics", type="primary")
    
            if save_weekly_metrics:
                updated_log = dict(weekly_metrics_log)
                updated_log[selected_week_key] = {
                    "values": {key: round(float(value), 2) for key, value in metric_inputs.items()},
                    "notes": weekly_notes.strip(),
                    "saved_at": datetime.now(MOUNTAIN_TIMEZONE).isoformat(timespec="minutes"),
                }
                _save_ma_lead_settings(updated_weekly_log=updated_log)
                st.success("Weekly metrics saved.")
                st.rerun()
    
            with st.expander("Edit weekly metric targets", expanded=False):
                target_inputs = {}
                target_cols = st.columns(2)
                for index, metric in enumerate(weekly_metric_targets):
                    with target_cols[index % 2]:
                        target_inputs[metric["key"]] = st.number_input(
                            f"Target - {metric['label']} ({metric['unit']})",
                            min_value=0.0,
                            step=1.0,
                            value=float(metric.get("target", 0.0)),
                            key=f"{panel_key}_weekly_metric_target_{metric['key']}",
                        )
                if st.button("Save targets", key=f"{panel_key}_save_weekly_metric_targets", type="secondary"):
                    _save_ma_lead_settings(updated_weekly_targets={key: round(float(value), 2) for key, value in target_inputs.items()})
                    st.success("Weekly metric targets saved.")
                    st.rerun()
    
            st.markdown("#### Recent week trend")
            available_weeks = sorted(weekly_metrics_log.keys(), reverse=True)
            if available_weeks:
                for week_key in available_weeks[:8]:
                    entry = weekly_metrics_log.get(week_key) or {}
                    week_values = entry.get("values") or {}
                    met_count = 0
                    checked_count = 0
                    for metric in weekly_metric_targets:
                        value = week_values.get(metric["key"])
                        if not isinstance(value, (int, float)):
                            continue
                        checked_count += 1
                        if metric["direction"] == "higher_is_better" and float(value) >= float(metric["target"]):
                            met_count += 1
                        if metric["direction"] == "lower_is_better" and float(value) <= float(metric["target"]):
                            met_count += 1
                    week_start_day = date.fromisoformat(week_key)
                    week_end_day = week_start_day + timedelta(days=6)
                    st.markdown(
                        f"- **{week_start_day.strftime('%b %d')} - {week_end_day.strftime('%b %d')}** · "
                        f"goals hit: {met_count}/{checked_count or len(weekly_metric_targets)} · "
                        f"saved: {entry.get('saved_at') or 'n/a'}"
                    )
                    if entry.get("notes"):
                        st.caption(entry.get("notes"))
            else:
                st.caption("No weekly metric snapshots saved yet.")
    
            st.markdown('</div>', unsafe_allow_html=True)
    
    if "30-Day Rollout" in tab_lookup:
        with tab_lookup["30-Day Rollout"]:
            st.markdown('<div class="panel">', unsafe_allow_html=True)
            st.markdown('<div class="panel-title"><h3>30-Day MA Lead Rollout Checklist</h3><span>Track rollout execution day by day with one editable template</span></div>', unsafe_allow_html=True)
    
            rollout_cols = st.columns([1.2, 1.2, 2.6])
            with rollout_cols[0]:
                selected_rollout_day = st.date_input("Checklist day", value=mountain_today(), key=f"{panel_key}_rollout_day")
            with rollout_cols[1]:
                start_date_input = st.date_input("Rollout start date", value=rollout_start_date, key=f"{panel_key}_rollout_start_date")
                if start_date_input != rollout_start_date:
                    _save_ma_lead_settings(updated_rollout_start_date=start_date_input)
                    st.rerun()
            day_number = (selected_rollout_day - start_date_input).days + 1
            phase_label = "Outside 30-day window"
            if 1 <= day_number <= 7:
                phase_label = "Week 1: Observe and map bottlenecks"
            elif 8 <= day_number <= 14:
                phase_label = "Week 2: Huddles and escalation reliability"
            elif 15 <= day_number <= 21:
                phase_label = "Week 3: Playbooks and metrics baseline"
            elif 22 <= day_number <= 30:
                phase_label = "Week 4: Tighten handoffs and cross-train"
            with rollout_cols[2]:
                st.caption(f"Day {day_number} from rollout start · {phase_label}")
    
            selected_rollout_day_key = selected_rollout_day.isoformat()
            rollout_entry = rollout_log.get(selected_rollout_day_key, {"completed_items": [], "notes": "", "saved_at": ""})
            rollout_completed_items = list(rollout_entry.get("completed_items") or [])
    
            completion_cols = st.columns([1, 1, 2])
            completion_cols[0].metric("Completed", len(rollout_completed_items))
            completion_cols[1].metric("Remaining", max(0, len(rollout_template) - len(rollout_completed_items)))
            completion_cols[2].caption(f"Last saved: {rollout_entry.get('saved_at') or 'Not saved yet'}")
    
            action_cols = st.columns([1, 1, 3])
            if action_cols[0].button("Mark all complete", key=f"{panel_key}_rollout_mark_all", use_container_width=True):
                updated_rollout_log = dict(rollout_log)
                updated_rollout_log[selected_rollout_day_key] = {
                    "completed_items": list(rollout_template),
                    "notes": rollout_entry.get("notes") or "",
                    "saved_at": datetime.now(MOUNTAIN_TIMEZONE).isoformat(timespec="minutes"),
                }
                _save_ma_lead_settings(updated_rollout_log=updated_rollout_log)
                st.success("Marked all rollout checklist items complete for this day.")
                st.rerun()
            if action_cols[1].button("Reset day", key=f"{panel_key}_rollout_reset_day", use_container_width=True):
                updated_rollout_log = dict(rollout_log)
                updated_rollout_log.pop(selected_rollout_day_key, None)
                _save_ma_lead_settings(updated_rollout_log=updated_rollout_log)
                st.success("Rollout checklist reset for this day.")
                st.rerun()
            action_cols[2].caption("Use the same template each day to keep rollout execution visible.")
    
            with st.form(f"{panel_key}_rollout_day_form"):
                selected_rollout_items = st.multiselect(
                    "Checklist items completed",
                    rollout_template,
                    default=[item for item in rollout_completed_items if item in rollout_template],
                )
                rollout_notes = st.text_area(
                    "Rollout notes",
                    value=rollout_entry.get("notes") or "",
                    height=90,
                    placeholder="What moved forward today and what needs support tomorrow",
                )
                save_rollout_day = st.form_submit_button("Save rollout day", type="primary")
    
            if save_rollout_day:
                updated_rollout_log = dict(rollout_log)
                updated_rollout_log[selected_rollout_day_key] = {
                    "completed_items": list(selected_rollout_items),
                    "notes": rollout_notes.strip(),
                    "saved_at": datetime.now(MOUNTAIN_TIMEZONE).isoformat(timespec="minutes"),
                }
                _save_ma_lead_settings(updated_rollout_log=updated_rollout_log)
                st.success("Rollout checklist day saved.")
                st.rerun()
    
            with st.expander("Edit 30-day rollout template", expanded=False):
                template_text = st.text_area(
                    "Template items (one per line)",
                    value="\n".join(rollout_template),
                    height=220,
                    key=f"{panel_key}_rollout_template_editor",
                )
                if st.button("Save rollout template", key=f"{panel_key}_save_rollout_template", type="secondary"):
                    parsed_template = normalize_ma_lead_rollout_template(template_text)
                    normalized_log = normalize_ma_lead_rollout_log(rollout_log, allowed_items=parsed_template)
                    _save_ma_lead_settings(
                        updated_rollout_template=parsed_template,
                        updated_rollout_log=normalized_log,
                    )
                    st.success("30-day rollout template saved.")
                    st.rerun()

        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div style="height: 0.6rem;"></div>', unsafe_allow_html=True)
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Weekly Leadership Summary</h3><span>One-click update for PSR lead, manager, and supervisor</span></div>', unsafe_allow_html=True)

    summary_cols = st.columns([1.2, 1.2, 2])
    with summary_cols[0]:
        summary_anchor_date = st.date_input("Week of", value=mountain_today(), key=f"{panel_key}_summary_anchor")
    week_start = summary_anchor_date - timedelta(days=summary_anchor_date.weekday())
    week_end = week_start + timedelta(days=6)
    with summary_cols[1]:
        summary_recipients = st.text_input(
            "Recipients",
            value="PSR lead, manager, supervisor",
            key=f"{panel_key}_summary_recipients",
        )
    with summary_cols[2]:
        summary_focus = st.text_input(
            "Weekly focus",
            value="Clinical flow, escalation closure, and team communication",
            key=f"{panel_key}_summary_focus",
        )
    summary_format = st.radio(
        "Summary format",
        ["Full leadership email", "Executive short version"],
        horizontal=True,
        key=f"{panel_key}_summary_format",
    )

    def _is_date_in_week(value):
        return isinstance(value, date) and week_start <= value <= week_end

    issues_created_week = [item for item in lead_issues if _is_date_in_week(item.get("created_date"))]
    issues_resolved_week = [item for item in lead_issues if _is_date_in_week(item.get("resolved_date"))]
    escalations_week = [
        item
        for item in lead_issues
        if item.get("status") == "escalated"
        and (
            _is_date_in_week(item.get("created_date"))
            or _is_date_in_week(item.get("decision_needed_by"))
        )
    ]
    huddles_week = [item for item in huddle_logs if _is_date_in_week(item.get("huddle_date"))]
    followups_due_week = [
        person
        for person in relationship_touchpoints
        if _is_date_in_week(person.get("next_follow_up_date"))
    ]
    followups_completed_week = [
        person
        for person in relationship_touchpoints
        if _is_date_in_week(person.get("last_touch_date"))
    ]
    open_end_of_week = [item for item in lead_issues if item.get("status") in unresolved_statuses]

    top_open_items = sorted(
        open_end_of_week,
        key=lambda item: (
            0 if item.get("urgency") == "critical" else 1 if item.get("urgency") == "high" else 2,
            item.get("due_date") or date.max,
        ),
    )[:5]
    top_open_lines = [
        f"- {item.get('title')} ({item.get('urgency')}, owner: {item.get('owner_name') or 'unassigned'})"
        for item in top_open_items
    ]
    if not top_open_lines:
        top_open_lines = ["- None"]

    psr_waiting_count = len(
        [
            item
            for item in open_end_of_week
            if item.get("source_lane") == "psr" or item.get("escalation_target") == "psr_lead"
        ]
    )
    leadership_waiting_count = len(
        [item for item in open_end_of_week if item.get("escalation_target") in ("manager", "supervisor")]
    )
    signoffs_pending_count = len([item for item in skill_signoffs if item.get("status") in ("pending", "in_progress")])
    signoffs_completed_week = len(
        [
            item
            for item in skill_signoffs
            if item.get("status") == "signed_off" and _is_date_in_week(item.get("signed_off_date"))
        ]
    )
    education_open_count = len([item for item in education_requests if item.get("status") in ("new", "preparing", "delivered")])
    education_delivered_week = len(
        [
            item
            for item in education_requests
            if item.get("status") == "delivered" and _is_date_in_week(item.get("session_date"))
        ]
    )
    autoclave_overdue_count = len([item for item in autoclave_items if item.get("status") == "overdue"])
    autoclave_due_soon_count = len([item for item in autoclave_items if item.get("status") == "due_soon"])

    wins_lines = [
        f"- Resolved {len(issues_resolved_week)} triage issue(s) this week.",
        f"- Logged {len(huddles_week)} huddle note(s) to keep team alignment visible.",
        f"- Completed {len(followups_completed_week)} relationship follow-up(s).",
        f"- Signed off {signoffs_completed_week} skill competency item(s).",
        f"- Delivered {education_delivered_week} education request(s).",
    ]
    risks_lines = [
        f"- {len(open_end_of_week)} issue(s) remain open in the lead queue.",
        f"- {psr_waiting_count} item(s) are waiting on PSR lane follow-through.",
        f"- {leadership_waiting_count} item(s) are waiting on manager/supervisor decisions.",
        f"- {signoffs_pending_count} skill sign-off(s) are still pending/in progress.",
        f"- Autoclave maintenance has {autoclave_overdue_count} overdue and {autoclave_due_soon_count} due-soon item(s).",
    ]
    asks_lines = [
        "- Support escalation closure on items with near-term due dates.",
        "- Confirm owner coverage for unresolved PSR-clinical handoffs.",
        "- Align on turnaround expectation for manager/supervisor decisions next week.",
    ]
    next_week_lines = [
        "- Close critical/high triage items first.",
        "- Resolve PSR handoff dependencies within 24 hours.",
        "- Escalate unresolved leadership decisions with explicit due dates.",
    ]
    top_open_section = "\n".join(top_open_lines)

    full_weekly_summary_text = (
        f"Subject: MA Lead Weekly Update ({week_start.isoformat()} to {week_end.isoformat()})\n"
        f"To: {summary_recipients}\n"
        f"Focus: {summary_focus}\n\n"
        "Hello team,\n\n"
        "Here is this week's MA lead update.\n\n"
        "Wins\n"
        f"{'\n'.join(wins_lines)}\n\n"
        "Operations Snapshot\n"
        f"- Issues created: {len(issues_created_week)}\n"
        f"- Issues resolved: {len(issues_resolved_week)}\n"
        f"- Active escalations this week: {len(escalations_week)}\n"
        f"- Huddles logged: {len(huddles_week)}\n"
        f"- Open queue (current): {len(open_end_of_week)}\n"
        f"- Waiting on PSR lane: {psr_waiting_count}\n"
        f"- Waiting on manager/supervisor: {leadership_waiting_count}\n"
        f"- Follow-ups due this week: {len(followups_due_week)}\n"
        f"- Follow-ups completed this week: {len(followups_completed_week)}\n\n"
        "Preceptor + Education + Sterile Processing Snapshot\n"
        f"- Skill sign-offs pending/in progress: {signoffs_pending_count}\n"
        f"- Skill sign-offs completed this week: {signoffs_completed_week}\n"
        f"- Education requests open: {education_open_count}\n"
        f"- Education sessions delivered this week: {education_delivered_week}\n"
        f"- Autoclave overdue: {autoclave_overdue_count}\n"
        f"- Autoclave due soon: {autoclave_due_soon_count}\n\n"
        "Risks\n"
        f"{'\n'.join(risks_lines)}\n\n"
        "Asks\n"
        f"{'\n'.join(asks_lines)}\n\n"
        "Top Open Items\n"
        f"{top_open_section}\n\n"
        "Next Week Plan\n"
        f"{'\n'.join(next_week_lines)}\n\n"
        "Thank you,\n"
        "MA Lead\n"
    )

    executive_short_summary_text = (
        f"Subject: MA Lead Executive Snapshot ({week_start.isoformat()} to {week_end.isoformat()})\n"
        f"To: {summary_recipients}\n\n"
        f"- Focus: {summary_focus}\n"
        f"- Created/Resolved: {len(issues_created_week)}/{len(issues_resolved_week)}\n"
        f"- Open queue: {len(open_end_of_week)} (PSR wait: {psr_waiting_count}, leadership wait: {leadership_waiting_count})\n"
        f"- Escalations: {len(escalations_week)}\n"
        f"- Skill sign-offs pending: {signoffs_pending_count} (completed this week: {signoffs_completed_week})\n"
        f"- Education requests open: {education_open_count} (delivered this week: {education_delivered_week})\n"
        f"- Autoclave overdue/due soon: {autoclave_overdue_count}/{autoclave_due_soon_count}\n"
        f"- Huddles logged: {len(huddles_week)}\n"
        f"- Relationship follow-ups completed: {len(followups_completed_week)}\n"
        f"- Top risk: {top_open_items[0].get('title') if top_open_items else 'No critical risk currently open'}\n"
        "- This week ask: Help close unresolved PSR and leadership dependencies with due dates.\n"
        "- Next week plan: Prioritize critical/high items and close escalations early in the week.\n"
    )

    weekly_summary_text = (
        executive_short_summary_text
        if summary_format == "Executive short version"
        else full_weekly_summary_text
    )

    st.text_area(
        "Generated weekly leadership email",
        value=weekly_summary_text,
        height=280,
        key=f"{panel_key}_weekly_summary_preview",
    )
    st.download_button(
        "Download leadership email",
        data=weekly_summary_text,
        file_name=(
            f"ma_lead_executive_snapshot_{week_start.isoformat()}.txt"
            if summary_format == "Executive short version"
            else f"ma_lead_weekly_email_{week_start.isoformat()}.txt"
        ),
        mime="text/plain",
        key=f"{panel_key}_download_weekly_summary",
    )
    copy_payload = json.dumps(weekly_summary_text).replace("</", "<\\/")
    components.html(
        f"""
        <div style=\"display:flex; align-items:center; gap:0.6rem;\">
            <button id=\"{panel_key}_copy_btn\" style=\"padding:0.35rem 0.75rem; border-radius:0.45rem; border:1px solid #64748b; background:#111827; color:#f8fafc; cursor:pointer;\">Copy leadership email</button>
            <span id=\"{panel_key}_copy_status\" style=\"font-size:0.82rem; color:#0f766e;\"></span>
        </div>
        <script>
            const payload = {copy_payload};
            const button = document.getElementById("{panel_key}_copy_btn");
            const status = document.getElementById("{panel_key}_copy_status");
            button.addEventListener("click", async () => {{
                try {{
                    await navigator.clipboard.writeText(payload);
                    status.textContent = "Copied to clipboard.";
                }} catch (error) {{
                    status.textContent = "Copy blocked by browser. Use Ctrl+C from the preview box.";
                }}
            }});
        </script>
        """,
        height=48,
    )
    st.markdown('</div>', unsafe_allow_html=True)


def render_settings_panel(app_settings, panel_key="settings"):
    render_metrics_row()
    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)

    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Settings</h3><span>Default values and planning preferences</span></div>', unsafe_allow_html=True)

    settings_category = st.selectbox(
        "Default category",
        ["Personal", "Clinic"],
        index=0 if app_settings.get("default_category") == "Personal" else 1,
    )
    settings_priority = st.selectbox(
        "Default priority",
        ["high", "medium", "low"],
        index=["high", "medium", "low"].index(app_settings.get("default_priority", "medium"))
        if app_settings.get("default_priority", "medium") in ["high", "medium", "low"]
        else 1,
    )
    settings_duration = st.selectbox(
        "Default duration (minutes)",
        [15, 30, 45, 60, 90, 120],
        index=[15, 30, 45, 60, 90, 120].index(app_settings.get("default_duration", 60))
        if app_settings.get("default_duration", 60) in [15, 30, 45, 60, 90, 120]
        else 3,
    )
    settings_time = st.time_input(
        "Default schedule time",
        value=parse_time_value(app_settings.get("default_schedule_time")) or time(9, 0),
    )
    settings_timeline_days = st.slider(
        "Default timeline window (days)",
        min_value=3,
        max_value=21,
        value=max(3, min(21, int(app_settings.get("timeline_days", 7)))),
    )

    st.markdown("### Clinic Planning Defaults")
    settings_surgeon_patient_target = st.slider(
        "Surgeon clinic patient target",
        min_value=15,
        max_value=40,
        value=safe_int(app_settings.get("surgeon_clinic_patient_target", 25), 25),
    )
    settings_general_patient_target = st.slider(
        "General clinic patient target",
        min_value=15,
        max_value=40,
        value=safe_int(app_settings.get("general_clinic_patient_target", 25), 25),
    )
    settings_procedure_target = st.slider(
        "Procedure Friday target",
        min_value=4,
        max_value=16,
        value=safe_int(app_settings.get("procedure_friday_procedure_target", 8), 8),
    )
    settings_visit_minutes = st.slider(
        "Clinic visit minutes",
        min_value=8,
        max_value=20,
        value=safe_int(app_settings.get("clinic_visit_minutes", 12), 12),
    )
    settings_admin_buffer = st.slider(
        "Clinic admin buffer minutes",
        min_value=30,
        max_value=120,
        value=safe_int(app_settings.get("clinic_admin_buffer_minutes", 60), 60),
        step=15,
    )
    settings_procedure_block = st.slider(
        "Procedure block minutes",
        min_value=20,
        max_value=60,
        value=safe_int(app_settings.get("procedure_block_minutes", 30), 30),
        step=5,
    )

    st.markdown("### Personal Planning Defaults")
    settings_focus_minutes = st.slider(
        "Personal focus sprint minutes",
        min_value=30,
        max_value=180,
        value=safe_int(app_settings.get("personal_focus_minutes", 90), 90),
        step=15,
    )

    st.markdown("### Schedule Capacity Guardrails")
    settings_daily_capacity_minutes = st.slider(
        "Daily planning capacity (minutes)",
        min_value=180,
        max_value=720,
        value=safe_int(app_settings.get("schedule_daily_capacity_minutes", 480), 480),
        step=30,
    )
    settings_capacity_days_per_week = st.slider(
        "Capacity days per week",
        min_value=1,
        max_value=7,
        value=safe_int(app_settings.get("schedule_capacity_days_per_week", 5), 5),
        step=1,
    )

    st.markdown("### OR Cadence Defaults")
    settings_default_surgeon_label = st.text_input(
        "Default surgeon label",
        value=app_settings.get("default_surgeon_label", "Dr. Braden Boyer (BB)"),
    )
    settings_or_fixed_weekday = st.selectbox(
        "Weekly fixed OR day",
        ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
        index=["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"].index(app_settings.get("or_fixed_weekday", "Friday"))
        if app_settings.get("or_fixed_weekday", "Friday") in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        else 4,
    )
    settings_or_alternating_days = st.multiselect(
        "Alternating OR weekdays (choose two)",
        ["Monday", "Tuesday", "Wednesday", "Thursday"],
        default=app_settings.get("or_alternating_days", ["Monday", "Wednesday"]),
    )
    if len(settings_or_alternating_days) < 2:
        settings_or_alternating_days = ["Monday", "Wednesday"]
    elif len(settings_or_alternating_days) > 2:
        settings_or_alternating_days = settings_or_alternating_days[:2]

    settings_or_cycle_offset = st.selectbox(
        "Alternating week starts with",
        [0, 1],
        index=[0, 1].index(safe_int(app_settings.get("or_alternating_cycle_offset", 0), 0))
        if safe_int(app_settings.get("or_alternating_cycle_offset", 0), 0) in [0, 1]
        else 0,
        format_func=lambda value: settings_or_alternating_days[0] if value == 0 else settings_or_alternating_days[1],
    )

    st.markdown("### Clinic Day Closeout Checklist")
    closeout_template_items = normalize_clinic_day_closeout_template(app_settings.get("clinic_day_closeout_template"))
    settings_closeout_template_text = st.text_area(
        "Checklist items (one per line)",
        value="\n".join(closeout_template_items),
        height=140,
        help="This list powers the generic end-of-clinic-day checklist on the Daily Review page.",
    )

    if st.button("Save Settings", type="primary"):
        parsed_closeout_template = normalize_clinic_day_closeout_template(settings_closeout_template_text)
        normalized_closeout_log = normalize_clinic_day_closeout_log(
            app_settings.get("clinic_day_closeout_log"),
            allowed_items=parsed_closeout_template,
        )
        app_settings = save_app_settings(
            {
                "default_category": settings_category,
                "default_priority": settings_priority,
                "default_duration": int(settings_duration),
                "default_schedule_time": settings_time.strftime("%H:%M"),
                "timeline_days": int(settings_timeline_days),
                "surgeon_clinic_patient_target": int(settings_surgeon_patient_target),
                "general_clinic_patient_target": int(settings_general_patient_target),
                "procedure_friday_procedure_target": int(settings_procedure_target),
                "clinic_visit_minutes": int(settings_visit_minutes),
                "clinic_admin_buffer_minutes": int(settings_admin_buffer),
                "procedure_block_minutes": int(settings_procedure_block),
                "personal_focus_minutes": int(settings_focus_minutes),
                "schedule_daily_capacity_minutes": int(settings_daily_capacity_minutes),
                "schedule_capacity_days_per_week": int(settings_capacity_days_per_week),
                "default_surgeon_label": settings_default_surgeon_label.strip() or "Dr. Braden Boyer (BB)",
                "or_fixed_weekday": settings_or_fixed_weekday,
                "or_alternating_days": settings_or_alternating_days,
                "or_alternating_cycle_offset": int(settings_or_cycle_offset),
                "clinic_day_closeout_template": parsed_closeout_template,
                "clinic_day_closeout_log": normalized_closeout_log,
            }
        )
        st.success("Settings saved.")
        st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)


def render_analytics_panel(tasks, active_tasks, scheduled_tasks, panel_key="analytics"):
    render_metrics_row()
    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    overdue_tasks = [task for task in active_tasks if task.get("due_date") and task["due_date"] < mountain_today()]
    analytics_cols = st.columns(4)
    analytics_cols[0].metric("Clinic active", len([task for task in active_tasks if task.get("category") == "Clinic"]))
    analytics_cols[1].metric("Personal active", len([task for task in active_tasks if task.get("category") == "Personal"]))
    analytics_cols[2].metric("Clinic overdue", len([task for task in overdue_tasks if task.get("category") == "Clinic"]))
    analytics_cols[3].metric("High unscheduled", len([task for task in active_tasks if task.get("priority") == "high" and not (task.get("scheduled_date") and task.get("scheduled_time"))]))

    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Analytics</h3><span>Snapshot of workload and execution</span></div>', unsafe_allow_html=True)

    status_counts = {
        "Todo": len([task for task in tasks if task.get("status") == "todo"]),
        "In Progress": len([task for task in tasks if task.get("status") == "in_progress"]),
        "Blocked": len([task for task in tasks if task.get("status") == "blocked"]),
        "Completed": len([task for task in tasks if task.get("status") == "completed"]),
    }
    category_counts = {
        "Personal": len([task for task in tasks if task.get("category") == "Personal"]),
        "Clinic": len([task for task in tasks if task.get("category") == "Clinic"]),
    }

    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("By Status")
        st.bar_chart(status_counts)
    with col_b:
        st.subheader("By Category")
        st.bar_chart(category_counts)

    upcoming_3_days = len([task for task in active_tasks if task.get("due_date") and task["due_date"] <= (mountain_today() + timedelta(days=3))])
    recurring_count = len([task for task in active_tasks if task.get("recurrence_rule") in ("daily", "weekly")])
    insight_cols = st.columns(3)
    insight_cols[0].metric("Overdue", len(overdue_tasks))
    insight_cols[1].metric("Due in 3 Days", upcoming_3_days)
    insight_cols[2].metric("Recurring Active", recurring_count)

    st.markdown('</div>', unsafe_allow_html=True)

    surgical_cases = []
    protocol_documents = []
    try:
        surgical_cases = load_surgical_cases() or []
    except Exception:
        surgical_cases = []
    try:
        protocol_documents = load_protocol_documents() or []
    except Exception:
        protocol_documents = []

    lookback_start = mountain_today() - timedelta(days=41)
    recent_cases = [
        item
        for item in surgical_cases
        if item.get("case_date") and item.get("case_date") >= lookback_start
    ]
    canceled_recent = [item for item in recent_cases if item.get("status") == "canceled"]
    cancel_rate = round((len(canceled_recent) / len(recent_cases)) * 100, 1) if recent_cases else 0.0

    coverage_cases = [
        item
        for item in surgical_cases
        if item.get("case_date")
        and item.get("case_date") >= (mountain_today() - timedelta(days=90))
        and item.get("status") in ("planned", "completed")
    ]
    covered_cases = 0
    for item in coverage_cases:
        if ref_suggest_protocols_for_case(item, protocol_documents, max_items=1):
            covered_cases += 1
    protocol_coverage = round((covered_cases / len(coverage_cases)) * 100, 1) if coverage_cases else 0.0

    week_starts = []
    current_week_start = mountain_today() - timedelta(days=mountain_today().weekday())
    for offset in range(5, -1, -1):
        week_starts.append(current_week_start - timedelta(days=7 * offset))

    cancel_trend = {}
    coverage_trend = {}
    for week_start in week_starts:
        week_end = week_start + timedelta(days=6)
        week_label = week_start.strftime("%b %d")
        week_cases = [
            item
            for item in surgical_cases
            if item.get("case_date") and week_start <= item.get("case_date") <= week_end
        ]
        week_canceled = [item for item in week_cases if item.get("status") == "canceled"]
        week_coverage_candidates = [item for item in week_cases if item.get("status") in ("planned", "completed")]
        week_covered = 0
        for item in week_coverage_candidates:
            if ref_suggest_protocols_for_case(item, protocol_documents, max_items=1):
                week_covered += 1

        cancel_trend[week_label] = len(week_canceled)
        coverage_trend[week_label] = round((week_covered / len(week_coverage_candidates)) * 100, 1) if week_coverage_candidates else 0.0

    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Case Signal Snapshot</h3><span>Cancellation trend and protocol usage coverage</span></div>', unsafe_allow_html=True)

    case_signal_cols = st.columns(4)
    case_signal_cols[0].metric("Recent cases (6w)", len(recent_cases))
    case_signal_cols[1].metric("Canceled (6w)", len(canceled_recent))
    case_signal_cols[2].metric("Cancel rate", f"{cancel_rate}%")
    case_signal_cols[3].metric("Protocol coverage (90d)", f"{protocol_coverage}%")

    signal_left, signal_right = st.columns(2)
    with signal_left:
        st.subheader("Weekly cancellation trend")
        st.line_chart(cancel_trend)
    with signal_right:
        st.subheader("Weekly protocol coverage (%)")
        st.line_chart(coverage_trend)

    st.markdown('</div>', unsafe_allow_html=True)

    performed_cases = [
        item for item in surgical_cases if item.get("status") == "completed"
    ]
    surgery_type_counts = {}
    for item in performed_cases:
        procedure_name = str(item.get("procedure_name") or "").strip() or "Unspecified procedure"
        surgery_type_counts[procedure_name] = surgery_type_counts.get(procedure_name, 0) + 1

    sorted_surgery_counts = sorted(
        surgery_type_counts.items(),
        key=lambda entry: entry[1],
        reverse=True,
    )

    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Surgery Type Counts</h3><span>Completed case count by procedure</span></div>', unsafe_allow_html=True)

    surgery_cols = st.columns(2)
    surgery_cols[0].metric("Completed cases", len(performed_cases))
    surgery_cols[1].metric("Unique surgery types", len(sorted_surgery_counts))

    if sorted_surgery_counts:
        st.bar_chart({name: count for name, count in sorted_surgery_counts})
        st.markdown("**Count by surgery type**")
        for procedure_name, count in sorted_surgery_counts:
            st.markdown(f"- {procedure_name}: {count}")
    else:
        st.markdown('<div class="empty-state">No completed surgical cases yet, so surgery type counts are not available.</div>', unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)


def render_msk_anatomy_panel(surgical_cases, protocol_documents, panel_key="anatomy"):
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>MSK Anatomy Atlas</h3><span>Foot and ankle first, with related orthopedic pathways and chain context</span></div>', unsafe_allow_html=True)
    st.caption("Educational reference only. This section is not diagnostic or treatment advice.")

    anatomy_atlas = {
        "Upper Extremity": {
            "Bones": [
                "Clavicle/scapula orientation drives glenohumeral rhythm and scapulothoracic control.",
                "Humerus, radius, and ulna alignment determine elbow carrying angle and forearm rotation.",
            ],
            "Joints": [
                "Shoulder stabilizes with dynamic cuff and static labral/capsular systems.",
                "Elbow and distal radioulnar joints are key to functional pronation/supination.",
            ],
            "Muscle Attachments": [
                "Rotator cuff insertions center the humeral head for overhead mechanics.",
                "Flexor-pronator and extensor-supinator origins explain many overuse syndromes.",
            ],
            "Neurovascular": [
                "Brachial plexus and axillary artery relationships matter in trauma and dislocation.",
                "Radial/ulnar nerve corridors should be considered during splinting and surgery.",
            ],
            "clinical": [
                "Clinical relevance: shoulder instability, cuff tears, and elbow overuse often present with referred pain patterns.",
            ],
        },
        "Lower Extremity": {
            "Bones": [
                "Pelvis-femur-tibia-foot alignment determines load transfer and gait efficiency.",
                "Tibia/fibula relationship is central for ankle mortise congruence and rotational control.",
            ],
            "Joints": [
                "Hip, knee, and ankle work as a chain; malalignment at one level drives overload distally.",
                "Subtalar and midfoot joints tune shock absorption and propulsion.",
            ],
            "Muscle Attachments": [
                "Gluteal, quadriceps, hamstring, and triceps surae attachments coordinate stance and push-off.",
                "Posterior tibial and peroneal insertions are major dynamic stabilizers of the foot arches.",
            ],
            "Neurovascular": [
                "Femoral, sciatic, tibial, and peroneal distributions help localize weakness and sensory changes.",
                "Popliteal and tibial vessel anatomy guides trauma and compartment-risk evaluation.",
            ],
            "clinical": [
                "Clinical relevance: valgus collapse, tendon dysfunction, and stress injury patterns are often chain-mediated.",
            ],
        },
        "Spine": {
            "Bones": [
                "Cervical, thoracic, and lumbar vertebral morphology influences motion and load tolerance.",
                "Facet orientation patterns explain region-specific movement behavior.",
            ],
            "Joints": [
                "Intervertebral discs and facets share load differently across posture and activity.",
                "Sacroiliac mechanics affect lower-extremity force transfer and pelvic control.",
            ],
            "Muscle Attachments": [
                "Deep multifidus and segmental stabilizers contribute to spinal control.",
                "Thoracolumbar fascia links trunk function to hip mechanics.",
            ],
            "Neurovascular": [
                "Dermatomal and myotomal mapping helps separate spinal from peripheral causes.",
                "Radicular patterns may mimic distal limb pathology in orthopedic clinics.",
            ],
            "clinical": [
                "Clinical relevance: referred spinal pain can mimic hip, knee, and foot pathology.",
            ],
        },
        "Chest": {
            "Bones": [
                "Rib, sternum, and thoracic vertebral structures influence breathing and shoulder mechanics.",
                "Scapulothoracic articulation bridges chest wall function and upper-extremity motion.",
            ],
            "Joints": [
                "Costovertebral and sternocostal joints modulate thoracic expansion.",
                "Clavicular articulations influence shoulder position and kinetic chain efficiency.",
            ],
            "Muscle Attachments": [
                "Intercostals, serratus, pecs, and diaphragm coordinate respiration and posture.",
                "Scapular muscle balance strongly affects shoulder pain and impingement risk.",
            ],
            "Neurovascular": [
                "Intercostal bundles and thoracic outlet boundaries matter for compression syndromes.",
                "Supraclavicular regions should be screened in unexplained upper-limb symptoms.",
            ],
            "clinical": [
                "Clinical relevance: thoracic stiffness and rib dysfunction can drive neck/shoulder overload.",
            ],
        },
        "Pelvis": {
            "Bones": [
                "Ilium, ischium, and pubis orientation governs acetabular coverage and hip stability.",
                "Sacrum and innominate relationships affect gait asymmetry and force transfer.",
            ],
            "Joints": [
                "Hip and SI joints coordinate frontal and transverse-plane control.",
                "Pubic symphysis contributes to ring stability during dynamic activity.",
            ],
            "Muscle Attachments": [
                "Abductors and rotators at the greater trochanter are central for single-leg stability.",
                "Adductor and hamstring origins often relate to groin and posterior chain symptoms.",
            ],
            "Neurovascular": [
                "Femoral triangle and sciatic pathways should be considered in trauma and entrapment patterns.",
                "Pelvic vascular anatomy is important in high-energy injuries.",
            ],
            "clinical": [
                "Clinical relevance: pelvic control deficits commonly cascade into knee/ankle overload.",
            ],
        },
    }

    fracture_deep_dives = {
        "Colles fracture": {
            "mechanism": "FOOSH with wrist extension causing distal radius dorsal displacement.",
            "key_signs": "Dinner-fork deformity, dorsal tilt on lateral X-ray, radial shortening.",
            "pitfalls": "Missing associated ulnar styloid injury or DRUJ instability.",
            "common_misses": "Subtle intra-articular extension and occult carpal injuries.",
            "treatment_path": "Reduction/splinting then operative fixation if unstable or significantly displaced.",
        },
        "Intertrochanteric femur fracture": {
            "mechanism": "Low-energy fall in osteoporotic bone or high-energy trauma in younger patients.",
            "key_signs": "External rotation and shortening, fracture lines between greater and lesser trochanters.",
            "pitfalls": "Underestimating comminution and medial calcar compromise.",
            "common_misses": "Occult extension into subtrochanteric region.",
            "treatment_path": "Prompt fixation with cephalomedullary nail or sliding hip device based on pattern.",
        },
        "Pilon fracture": {
            "mechanism": "Axial load with distal tibial plafond impaction and metaphyseal comminution.",
            "key_signs": "Plafond incongruity, metaphyseal-diaphyseal dissociation, severe swelling.",
            "pitfalls": "Rushing definitive fixation before soft-tissue recovery.",
            "common_misses": "Associated fibula fracture alignment effect on plafond reduction.",
            "treatment_path": "Staged management: temporizing external fixation then definitive ORIF when soft tissue allows.",
        },
        "Tibial plateau fracture": {
            "mechanism": "Valgus/varus force with axial loading causing split, depression, or bicondylar patterns.",
            "key_signs": "Lipohemarthrosis, articular depression, condylar widening on AP view.",
            "pitfalls": "Ignoring meniscal/ligament injury and posterior slope changes.",
            "common_misses": "Posterolateral depression not obvious on plain films.",
            "treatment_path": "CT-guided classification, restore alignment/articular surface, protect soft tissue envelope.",
        },
    }

    case_learning_tracks = [
        {
            "title": "Twisting ankle injury at sport",
            "history": "23-year-old athlete with inversion injury and immediate lateral ankle swelling.",
            "exam": "Tenderness over ATFL/CFL region, mild positive anterior drawer, no syndesmotic pain.",
            "imaging_hint": "AP/mortise/lateral ankle films without gross fracture; assess talar tilt and avulsion fragments.",
            "differential": ["Lateral ligament sprain", "Occult distal fibula avulsion", "Peroneal tendon injury"],
            "final_dx": "Lateral ankle ligament complex injury (ATFL-predominant).",
            "pearls": ["Compare to contralateral side.", "Screen syndesmosis and peroneals in all significant sprains."],
            "board_question": "Most commonly injured ligament in inversion ankle sprain?",
            "board_answer": "ATFL",
        },
        {
            "title": "Forefoot pain after mileage increase",
            "history": "31-year-old runner with progressive forefoot pain and focal tenderness over second metatarsal.",
            "exam": "Pain with forefoot loading, no acute trauma history.",
            "imaging_hint": "Early X-ray may be negative; MRI or repeat radiographs can reveal stress pattern.",
            "differential": ["Metatarsal stress injury", "Morton neuroma", "Second MTP synovitis"],
            "final_dx": "Second metatarsal stress injury.",
            "pearls": ["Negative initial X-ray does not exclude stress injury.", "Training load and nutrition history matter."],
            "board_question": "Best next test when stress injury is strongly suspected but X-ray is negative?",
            "board_answer": "MRI",
        },
        {
            "title": "Elderly fall with ankle deformity",
            "history": "68-year-old with rotational ankle injury and inability to bear weight.",
            "exam": "Medial/lateral malleolar tenderness with swelling and instability concern.",
            "imaging_hint": "Mortise disruption on ankle radiographs; evaluate posterior malleolus and syndesmosis.",
            "differential": ["Bimalleolar fracture", "Trimalleolar fracture", "Syndesmotic injury without fracture"],
            "final_dx": "Unstable bimalleolar/trimalleolar ankle fracture pattern.",
            "pearls": ["Assess neurovascular status before and after reduction.", "CT can clarify posterior malleolar involvement."],
            "board_question": "Key determinant for urgent reduction in displaced ankle fracture-dislocation?",
            "board_answer": "Neurovascular compromise and skin at risk",
        },
    ]

    atlas_tab, pathways_tab, differential_tab, foot_tab, ankle_tab, lower_leg_tab, knee_tab, bones_tab, fractures_tab, xray_tab, quiz_tab, case_tracks_tab, exam_tab, imaging_tab = st.tabs([
        "Atlas 2.0",
        "Clinical Pathways",
        "Differential Builder",
        "Foot",
        "Ankle",
        "Lower Leg",
        "Knee",
        "Bones",
        "Fractures",
        "X-ray Library",
        "Quiz Lab",
        "Case Tracks",
        "Exam Library",
        "Imaging Helper",
    ])

    with atlas_tab:
        st.markdown("### Structured Anatomy Atlas")
        st.caption("Region-based and layered review with direct clinical relevance.")

        atlas_region = st.selectbox(
            "Region",
            list(anatomy_atlas.keys()),
            key=f"{panel_key}_atlas_region",
        )
        atlas_layers = st.multiselect(
            "Layer(s)",
            ["Bones", "Joints", "Muscle Attachments", "Neurovascular"],
            default=["Bones", "Joints"],
            key=f"{panel_key}_atlas_layers",
        )

        region_payload = anatomy_atlas.get(atlas_region, {})
        if atlas_layers:
            for layer_name in atlas_layers:
                st.markdown(f"#### {layer_name}")
                for bullet in region_payload.get(layer_name, []):
                    st.markdown(f"- {bullet}")
        else:
            st.markdown('<div class="empty-state">Select at least one layer to display atlas content.</div>', unsafe_allow_html=True)

        for line in region_payload.get("clinical", []):
            st.info(line)

    with pathways_tab:
        st.markdown("### Foot and Ankle Clinical Pathways")
        st.caption("Start with foot and ankle patterns, then extend to related structures when needed.")

        pathway_map = {
            "Plantar heel pain": {
                "region": "Foot",
                "differentials": ["Plantar fasciitis", "Calcaneal stress injury", "Baxter neuritis", "Insertional Achilles overlap"],
                "exam": ["Windlass test", "First-step pain pattern", "Calcaneal squeeze", "Achilles insertion palpation"],
                "imaging": "Weight-bearing foot X-ray first if bony concern; ultrasound or MRI for persistent soft-tissue or stress-injury concern.",
                "related": ["Calf tightness", "Hip abductor weakness", "Lumbar referred pain"],
                "terms": ["heel", "plantar", "fascia", "calcaneus", "achilles"],
            },
            "Medial ankle pain": {
                "region": "Ankle",
                "differentials": ["Posterior tibial tendon dysfunction", "Deltoid sprain", "Spring ligament injury", "Tarsal tunnel irritation"],
                "exam": ["Single-leg heel raise", "Too-many-toes sign", "Deltoid tenderness", "Tinel at tarsal tunnel"],
                "imaging": "Weight-bearing ankle/foot radiographs for alignment; MRI when tendon-ligament injury staging is needed.",
                "related": ["Knee valgus pattern", "Hip rotation control", "Gait chain overload"],
                "terms": ["medial ankle", "posterior tibial", "deltoid", "spring ligament", "flatfoot"],
            },
            "Lateral ankle instability": {
                "region": "Ankle",
                "differentials": ["ATFL/CFL insufficiency", "Peroneal tendon pathology", "Syndesmotic injury", "Osteochondral lesion"],
                "exam": ["Anterior drawer", "Talar tilt", "Peroneal subluxation assessment", "Syndesmosis squeeze"],
                "imaging": "X-ray first for fracture/alignment, then MRI when instability is recurrent or associated intra-articular injury is suspected.",
                "related": ["Proprioception deficits", "Core/hip neuromuscular control", "Return-to-sport progression"],
                "terms": ["atfl", "cfl", "ankle sprain", "peroneal", "syndesmosis"],
            },
            "Forefoot overload": {
                "region": "Foot",
                "differentials": ["Metatarsalgia", "Second MTP synovitis", "Morton neuroma", "Transfer metatarsalgia"],
                "exam": ["Web-space compression", "Plantar plate stress", "First-ray mobility", "Callus distribution"],
                "imaging": "Weight-bearing forefoot radiographs for alignment, ultrasound/MRI for neuroma or plantar-plate detail.",
                "related": ["Hallux mechanics", "Gastrocnemius tightness", "Knee and hip loading strategy"],
                "terms": ["metatarsal", "forefoot", "neuroma", "plantar plate", "hallux"],
            },
        }

        pathway_choice = st.selectbox("Presentation pattern", list(pathway_map.keys()), key=f"{panel_key}_pathway_choice")
        pathway = pathway_map[pathway_choice]

        p_cols = st.columns(2)
        with p_cols[0]:
            st.markdown(f"**Primary region:** {pathway['region']}")
            st.markdown("**Likely differentials**")
            for item in pathway["differentials"]:
                st.markdown(f"- {item}")
            st.markdown("**Exam focus**")
            for item in pathway["exam"]:
                st.markdown(f"- {item}")
        with p_cols[1]:
            st.markdown("**Initial imaging strategy**")
            st.write(pathway["imaging"])
            st.markdown("**Related orthopedics to screen**")
            for item in pathway["related"]:
                st.markdown(f"- {item}")

        ref_render_anatomy_related_widget(
            f"{pathway['region']} Pathway",
            pathway["terms"],
            surgical_cases,
            protocol_documents,
            panel_key=f"{panel_key}_pathways_related",
        )

    with differential_tab:
        st.markdown("### Foot and Ankle Differential Builder")
        st.caption("Build a ranked differential from symptom pattern and exam findings, then jump to related cases and protocols.")

        differential_library = [
            {
                "name": "Plantar fasciitis",
                "locations": {"Plantar heel", "Medial heel"},
                "onsets": {"Overuse/chronic"},
                "behaviors": {"First-step morning pain", "Pain after prolonged standing", "Pain with running/push-off"},
                "exam": {"Windlass positive", "Point tenderness at plantar heel"},
                "imaging": "Weight-bearing foot radiographs first if bony concern; ultrasound can support plantar fascia thickening.",
                "terms": ["plantar", "heel", "fascia", "foot"],
            },
            {
                "name": "Posterior tibial tendon dysfunction",
                "locations": {"Medial ankle", "Medial midfoot"},
                "onsets": {"Overuse/chronic"},
                "behaviors": {"Pain after prolonged standing", "Progressive arch collapse"},
                "exam": {"Single-leg heel raise weakness", "Too-many-toes sign"},
                "imaging": "Weight-bearing foot/ankle radiographs for alignment, MRI for tendon and spring-ligament staging.",
                "terms": ["posterior tibial", "medial ankle", "flatfoot", "arch"],
            },
            {
                "name": "Lateral ankle ligament injury",
                "locations": {"Lateral ankle"},
                "onsets": {"Acute traumatic"},
                "behaviors": {"Pain with running/push-off", "Instability/giving-way"},
                "exam": {"Anterior drawer laxity", "Talar tilt asymmetry"},
                "imaging": "Weight-bearing ankle radiographs first; MRI if recurrent instability or persistent pain.",
                "terms": ["lateral ankle", "atfl", "cfl", "ankle sprain"],
            },
            {
                "name": "Syndesmotic injury",
                "locations": {"High ankle"},
                "onsets": {"Acute traumatic"},
                "behaviors": {"Pain with running/push-off", "Instability/giving-way"},
                "exam": {"Squeeze test positive", "External rotation stress pain"},
                "imaging": "Ankle radiographs and stress views; MRI if high ankle sprain severity is unclear.",
                "terms": ["syndesmosis", "high ankle", "aitfl", "ankle"],
            },
            {
                "name": "Achilles tendinopathy",
                "locations": {"Posterior heel", "Posterior ankle"},
                "onsets": {"Overuse/chronic", "Subacute"},
                "behaviors": {"Pain with running/push-off", "Morning stiffness"},
                "exam": {"Achilles insertion tenderness", "Pain with resisted plantarflexion"},
                "imaging": "Ultrasound is useful for tendon morphology; MRI for insertional or partial-thickness concern.",
                "terms": ["achilles", "posterior heel", "tendon"],
            },
            {
                "name": "Morton neuroma / intermetatarsal neuritis",
                "locations": {"Forefoot", "Plantar forefoot"},
                "onsets": {"Overuse/chronic", "Subacute"},
                "behaviors": {"Forefoot numbness/tingling", "Pain after prolonged standing"},
                "exam": {"Web-space compression pain", "Mulder click"},
                "imaging": "Weight-bearing forefoot radiographs first; ultrasound or MRI for neuroma confirmation.",
                "terms": ["neuroma", "forefoot", "intermetatarsal", "metatarsal"],
            },
            {
                "name": "Calcaneal or metatarsal stress injury",
                "locations": {"Plantar heel", "Forefoot", "Midfoot"},
                "onsets": {"Overuse/chronic", "Subacute"},
                "behaviors": {"Pain with running/push-off", "Night pain"},
                "exam": {"Calcaneal squeeze pain", "Focal bony tenderness"},
                "imaging": "Start with weight-bearing radiographs; MRI when stress reaction is suspected despite negative X-ray.",
                "terms": ["stress", "calcaneus", "metatarsal", "fracture"],
            },
        ]

        input_cols = st.columns(2)
        with input_cols[0]:
            pain_location = st.selectbox(
                "Primary pain location",
                [
                    "Plantar heel",
                    "Medial heel",
                    "Medial ankle",
                    "Lateral ankle",
                    "High ankle",
                    "Posterior heel",
                    "Posterior ankle",
                    "Forefoot",
                    "Plantar forefoot",
                    "Midfoot",
                    "Medial midfoot",
                ],
                key=f"{panel_key}_diff_pain_location",
            )
            onset = st.selectbox(
                "Onset pattern",
                ["Acute traumatic", "Subacute", "Overuse/chronic"],
                key=f"{panel_key}_diff_onset",
            )
        with input_cols[1]:
            pain_behaviors = st.multiselect(
                "Pain behavior",
                [
                    "First-step morning pain",
                    "Pain after prolonged standing",
                    "Pain with running/push-off",
                    "Instability/giving-way",
                    "Forefoot numbness/tingling",
                    "Morning stiffness",
                    "Progressive arch collapse",
                    "Night pain",
                ],
                key=f"{panel_key}_diff_behaviors",
            )
            exam_findings = st.multiselect(
                "Exam findings",
                [
                    "Windlass positive",
                    "Point tenderness at plantar heel",
                    "Single-leg heel raise weakness",
                    "Too-many-toes sign",
                    "Anterior drawer laxity",
                    "Talar tilt asymmetry",
                    "Squeeze test positive",
                    "External rotation stress pain",
                    "Achilles insertion tenderness",
                    "Pain with resisted plantarflexion",
                    "Web-space compression pain",
                    "Mulder click",
                    "Calcaneal squeeze pain",
                    "Focal bony tenderness",
                ],
                key=f"{panel_key}_diff_exam",
            )

        ranked = []
        selected_behaviors = set(pain_behaviors)
        selected_exam = set(exam_findings)
        for item in differential_library:
            score = 0
            if pain_location in item["locations"]:
                score += 4
            if onset in item["onsets"]:
                score += 2
            score += len(selected_behaviors.intersection(item["behaviors"])) * 2
            score += len(selected_exam.intersection(item["exam"])) * 3
            if score > 0:
                matched_behaviors = sorted(selected_behaviors.intersection(item["behaviors"]))
                matched_exam = sorted(selected_exam.intersection(item["exam"]))
                ranked.append((score, item, matched_behaviors, matched_exam))

        ranked.sort(key=lambda entry: entry[0], reverse=True)
        top_matches = ranked[:5]

        if top_matches:
            st.markdown("#### Ranked Differential")
            for rank_index, (score, item, matched_behaviors, matched_exam) in enumerate(top_matches, start=1):
                confidence = "High" if score >= 10 else "Moderate" if score >= 6 else "Low"
                with st.expander(f"{rank_index}. {item['name']} (score {score} · {confidence})", expanded=(rank_index == 1)):
                    if matched_behaviors:
                        st.markdown(f"**Matched behavior cues:** {', '.join(matched_behaviors)}")
                    if matched_exam:
                        st.markdown(f"**Matched exam cues:** {', '.join(matched_exam)}")
                    st.markdown(f"**Initial imaging strategy:** {item['imaging']}")

            top_terms = top_matches[0][1]["terms"]
            ref_render_anatomy_related_widget(
                "Differential Match",
                top_terms,
                surgical_cases,
                protocol_documents,
                panel_key=f"{panel_key}_differential_related",
            )
        else:
            st.markdown('<div class="empty-state">Choose a pain location, behavior, and exam clues to generate ranked differentials.</div>', unsafe_allow_html=True)

        st.markdown("#### Suggested PT Protocol Links for a Case")
        st.caption("Select a case to surface matching PT protocols and link them in one click.")

        case_options = [item.get("id") for item in surgical_cases if item.get("id") is not None]
        case_label_map = {}
        case_by_id = {}
        for item in sorted(surgical_cases, key=lambda row: (row.get("case_date") or date.min, row.get("id") or 0), reverse=True):
            case_id = item.get("id")
            if case_id is None:
                continue
            case_date_value = item.get("case_date")
            case_date_label = case_date_value.strftime("%b %d, %Y") if hasattr(case_date_value, "strftime") else "No date"
            case_label_map[case_id] = f"{case_date_label} - {item.get('procedure_name') or 'Unnamed case'} ({item.get('case_stream') or 'Unknown stream'})"
            case_by_id[case_id] = item

        if not case_options:
            st.markdown('<div class="empty-state">No cases available yet. Add a case first to link PT protocols.</div>', unsafe_allow_html=True)
        else:
            selected_case_id = st.selectbox(
                "Case",
                options=case_options,
                key=f"{panel_key}_diff_selected_case",
                format_func=lambda case_id: case_label_map.get(case_id, f"Case {case_id}"),
            )
            selected_case = case_by_id.get(selected_case_id)

            case_protocol_links = []
            try:
                case_protocol_links = load_case_protocol_links() or []
            except Exception:
                case_protocol_links = []

            links_by_protocol = {}
            linked_protocol_ids_for_case = set()
            for link_item in case_protocol_links:
                protocol_id = link_item.get("protocol_id")
                case_id = link_item.get("case_id")
                if protocol_id is None or case_id is None:
                    continue
                links_by_protocol.setdefault(protocol_id, set()).add(case_id)
                if case_id == selected_case_id:
                    linked_protocol_ids_for_case.add(protocol_id)

            pt_suggestions = []
            if selected_case:
                raw_suggestions = ref_suggest_protocols_for_case(selected_case, protocol_documents, max_items=10)
                pt_suggestions = [
                    (score, overlap_terms, doc)
                    for score, overlap_terms, doc in raw_suggestions
                    if str(doc.get("surgeon_label")).strip().lower() == "physical therapy"
                ]

            if pt_suggestions:
                auto_link_cols = st.columns([1, 2])
                with auto_link_cols[0]:
                    if st.button("Auto-link top 3 PT suggestions", key=f"{panel_key}_diff_auto_link_pt"):
                        for score, overlap_terms, doc in pt_suggestions[:3]:
                            protocol_id = doc.get("id")
                            if protocol_id is None:
                                continue
                            existing_case_ids = set(links_by_protocol.get(protocol_id, set()))
                            existing_case_ids.add(selected_case_id)
                            set_protocol_case_links(protocol_id, sorted(existing_case_ids))
                        st.success("Top PT protocol suggestions linked to selected case.")
                        st.rerun()

                for idx, (score, overlap_terms, doc) in enumerate(pt_suggestions[:6], start=1):
                    protocol_id = doc.get("id")
                    linked_already = protocol_id in linked_protocol_ids_for_case
                    row_cols = st.columns([2.4, 1.6, 1.2])
                    with row_cols[0]:
                        st.markdown(f"**{idx}. {doc.get('protocol_name') or 'Unnamed PT Protocol'}**")
                        st.caption(f"Keywords: {', '.join(overlap_terms) if overlap_terms else 'No overlap terms'}")
                    with row_cols[1]:
                        status_text = "Linked" if linked_already else "Not linked"
                        st.caption(f"Match score: {score} · {status_text}")
                    with row_cols[2]:
                        if linked_already:
                            st.button("Linked", key=f"{panel_key}_diff_linked_{selected_case_id}_{protocol_id}", disabled=True)
                        elif st.button("Link", key=f"{panel_key}_diff_link_{selected_case_id}_{protocol_id}"):
                            existing_case_ids = set(links_by_protocol.get(protocol_id, set()))
                            existing_case_ids.add(selected_case_id)
                            set_protocol_case_links(protocol_id, sorted(existing_case_ids))
                            st.success("PT protocol linked to selected case.")
                            st.rerun()
            else:
                st.markdown('<div class="empty-state">No PT protocol suggestions found for this case yet. Add PT protocol notes with matching anatomy/procedure terms.</div>', unsafe_allow_html=True)

    with foot_tab:
        ref_render_anatomy_structure_spotlight("Foot", ref_anatomy_structure_map("Foot"), panel_key=f"{panel_key}_foot_spotlight")
        st.markdown("### Osteology and Surface Anatomy")
        st.markdown(
            "- Tarsals: talus, calcaneus, navicular, cuboid, and the three cuneiforms form the hindfoot/midfoot scaffold.\n"
            "- Metatarsals I-V define the rays and create the forefoot lever arm for push-off and balance.\n"
            "- Phalanges and sesamoids matter most at the first MTP joint, where flexor hallucis brevis and sesamoids amplify load transfer."
        )
        st.markdown("### Soft Tissue, Compartments, and Exam Relevance")
        st.markdown(
            "- Plantar fascia is the central tension band of the arch and is commonly palpated at the medial calcaneal tubercle.\n"
            "- Intrinsic muscles stabilize the metatarsal heads and support the transverse arch during stance.\n"
            "- Key exam landmarks include the navicular tuberosity, base of the fifth metatarsal, sesamoids, and first MTP dorsiflexion.",
        )
        st.markdown("### Imaging and Surgical Landmarks")
        st.markdown(
            "- Radiographs often hinge on weight-bearing alignment, first-ray position, and calcaneal pitch.\n"
            "- Ultrasound is useful for plantar fascia, peroneal tendons, and focal soft-tissue pain.\n"
            "- Medial column procedures usually orient around the talonavicular, naviculocuneiform, and first TMT complexes."
        )
        ref_render_anatomy_related_widget(
            "Foot",
            ["foot", "plantar", "metatarsal", "hallux", "sesamoid", "fascia", "ray", "midfoot", "forefoot"],
            surgical_cases,
            protocol_documents,
            panel_key=f"{panel_key}_foot",
        )

    with ankle_tab:
        ref_render_anatomy_structure_spotlight("Ankle", ref_anatomy_structure_map("Ankle"), panel_key=f"{panel_key}_ankle_spotlight")
        st.markdown("### Articulation, Stability, and Motion")
        st.markdown(
            "- The talocrural joint is a true hinge: dorsiflexion closes the mortise, plantarflexion relaxes it.\n"
            "- The subtalar joint couples inversion and eversion with hindfoot valgus/varus alignment.\n"
            "- Syndesmotic integrity depends on the AITFL, PITFL, interosseous ligament, and interosseous membrane."
        )
        st.markdown("### Ligament Complexes and Pathology Patterns")
        st.markdown(
            "- Lateral ligament injuries most often begin with the ATFL, then progress to the CFL.\n"
            "- The deltoid complex resists talar tilt and external rotation; syndesmotic injury changes mortise congruence.\n"
            "- The spring ligament and posterior tibial tendon are major medial arch stabilizers."
        )
        st.markdown("### Exam and Imaging")
        st.markdown(
            "- Point tenderness over the ATFL, CFL, and syndesmosis separates most inversion injuries from higher-grade injuries.\n"
            "- Weight-bearing radiographs and stress views are useful for mortise widening and talar tilt.\n"
            "- MRI highlights ligament continuity, osteochondral lesions, and peroneal tendon pathology."
        )
        ref_render_anatomy_related_widget(
            "Ankle",
            ["ankle", "achilles", "peroneal", "atfl", "cfl", "deltoid", "syndesmosis", "talocrural", "subtalar"],
            surgical_cases,
            protocol_documents,
            panel_key=f"{panel_key}_ankle",
        )

    with lower_leg_tab:
        ref_render_anatomy_structure_spotlight("Lower Leg", ref_anatomy_structure_map("Lower Leg"), panel_key=f"{panel_key}_lower_leg_spotlight")
        st.markdown("### Compartment Anatomy")
        st.markdown(
            "- Anterior compartment: tibialis anterior, extensor hallucis longus, extensor digitorum longus, and peroneus tertius.\n"
            "- Lateral compartment: peroneus longus and brevis, important for eversion and first-ray support.\n"
            "- Posterior compartment: gastrocnemius, soleus, plantaris, tibialis posterior, FDL, FHL, and deep neurovascular structures."
        )
        st.markdown("### Calf and Achilles Unit")
        st.markdown(
            "- The gastrocnemius crosses both knee and ankle; soleus is the deeper endurance plantarflexor.\n"
            "- Achilles tendon is the common confluence and a major load-transfer structure during gait and push-off.\n"
            "- Sural nerve and small saphenous vein travel posterolaterally and are useful surface orientation landmarks."
        )
        st.markdown("### Clinical Relevance")
        st.markdown(
            "- Calf pain differentials often separate muscle strain, Achilles pathology, and vascular causes by exam pattern and focal tenderness.\n"
            "- Compartment anatomy matters for swelling, overuse syndromes, and postoperative incision planning.\n"
            "- Ultrasound can evaluate Achilles continuity and dynamic tendon motion; MRI is better for deeper compartment and insertional detail."
        )
        ref_render_anatomy_related_widget(
            "Lower Leg",
            ["calf", "lower leg", "gastrocnemius", "soleus", "achilles", "peroneal", "fibula", "tibia", "compartment"],
            surgical_cases,
            protocol_documents,
            panel_key=f"{panel_key}_lower_leg",
        )

    with knee_tab:
        ref_render_anatomy_structure_spotlight("Knee", ref_anatomy_structure_map("Knee"), panel_key=f"{panel_key}_knee_spotlight")
        st.markdown("### Osseous and Articular Anatomy")
        st.markdown(
            "- Tibiofemoral articulation is a bicondylar hinge with roll-and-glide mechanics across flexion arcs.\n"
            "- Patellofemoral articulation tracks the patella within the trochlear groove and influences extensor efficiency.\n"
            "- Menisci provide load sharing, shock absorption, joint congruence, and rotational stability.",
        )
        st.markdown("### Ligament and Capsular Stabilizers")
        st.markdown(
            "- ACL and PCL control anterior/posterior translation and help regulate rotational stability.\n"
            "- MCL and LCL resist valgus and varus stress, while posterolateral/posteromedial corners manage complex rotation.\n"
            "- Capsular structures and the IT band/pes anserinus contribute dynamic restraint and palpable landmarks."
        )
        st.markdown("### Imaging, Exam, and Procedure Relevance")
        st.markdown(
            "- Effusion, joint line tenderness, Lachman, pivot shift, valgus, varus, and Thessaly-type maneuvers help localize pathology.\n"
            "- X-ray alignment and MRI anatomy are most useful for meniscus, cruciate, cartilage, and extensor mechanism detail.\n"
            "- Surgical planning often references the anteromedial and anterolateral portals, tibial tubercle, and posteromedial corner."
        )
        ref_render_anatomy_related_widget(
            "Knee",
            ["knee", "acl", "pcl", "meniscus", "mcl", "lcl", "patella", "patellar", "tibiofemoral", "patellofemoral"],
            surgical_cases,
            protocol_documents,
            panel_key=f"{panel_key}_knee",
        )

    with bones_tab:
        st.markdown("### Skeletal Anatomy (Osteology) Reference")
        st.markdown("Detailed bone structures and landmarks for the foot, ankle, lower leg, and knee.")
        
        bones_region = st.radio(
            "Select region:",
            ["Foot Bones", "Ankle Bones", "Lower Leg Bones", "Knee Bones"],
            key=f"{panel_key}_bones_region",
            horizontal=True,
        )
        
        if bones_region == "Foot Bones":
            bones_data = ref_anatomy_bones_map("Foot")
        elif bones_region == "Ankle Bones":
            bones_data = ref_anatomy_bones_map("Ankle")
        elif bones_region == "Lower Leg Bones":
            bones_data = ref_anatomy_bones_map("Lower Leg")
        else:
            bones_data = ref_anatomy_bones_map("Knee")

        for bone_name, bone_info in bones_data.items():
            with st.expander(f"**{bone_name}**", expanded=False):
                st.markdown(f"**Summary:** {bone_info.get('summary', '')}")
                
                for key in ["anatomy", "landmarks", "function", "imaging", "procedure"]:
                    if key in bone_info:
                        label = key.replace("_", " ").title()
                        st.markdown(f"**{label}:** {bone_info[key]}")
                
                if "pearls" in bone_info:
                    st.markdown("**Pearls**")
                    for pearl in bone_info["pearls"]:
                        st.markdown(f"- {pearl}")

        if bones_region == "Foot Bones":
            st.markdown("### Related Foot Cases & Protocols")
            ref_render_anatomy_related_widget(
                "Foot Bone and Fracture",
                [
                    "foot",
                    "hindfoot",
                    "midfoot",
                    "forefoot",
                    "metatarsal",
                    "phalanges",
                    "sesamoid",
                    "talus",
                    "calcaneus",
                    "navicular",
                    "cuboid",
                    "cuneiform",
                    "fracture",
                    "jones",
                    "lisfranc",
                ],
                surgical_cases,
                protocol_documents,
                panel_key=f"{panel_key}_bones_foot_related",
            )

    with fractures_tab:
        st.markdown("### Fracture Types and Locations")
        st.markdown("Common fracture patterns by anatomical region, with mechanisms, imaging, and clinical significance.")
        
        fracture_region = st.radio(
            "Select region:",
            ["Foot Fractures", "Ankle Fractures", "Lower Leg Fractures", "Knee Fractures"],
            key=f"{panel_key}_fractures_region",
            horizontal=True,
        )
        
        if fracture_region == "Foot Fractures":
            fractures_data = ref_anatomy_fractures_map("Foot")
        elif fracture_region == "Ankle Fractures":
            fractures_data = ref_anatomy_fractures_map("Ankle")
        elif fracture_region == "Lower Leg Fractures":
            fractures_data = ref_anatomy_fractures_map("Lower Leg")
        else:
            fractures_data = ref_anatomy_fractures_map("Knee")

        for fracture_name, fracture_info in fractures_data.items():
            with st.expander(f"**{fracture_name}**", expanded=False):
                for key in ["location", "mechanism", "types", "clinical", "imaging", "treatment", "complications"]:
                    if key in fracture_info:
                        label = key.replace("_", " ").title()
                        st.markdown(f"**{label}:** {fracture_info[key]}")
                
                if "pearls" in fracture_info:
                    st.markdown("**Pearls**")
                    for pearl in fracture_info["pearls"]:
                        st.markdown(f"- {pearl}")

        if fracture_region == "Foot Fractures":
            st.markdown("### Related Foot Cases & Protocols")
            ref_render_anatomy_related_widget(
                "Foot Bone and Fracture",
                [
                    "foot",
                    "hindfoot",
                    "midfoot",
                    "forefoot",
                    "metatarsal",
                    "phalanges",
                    "sesamoid",
                    "talus",
                    "calcaneus",
                    "navicular",
                    "cuboid",
                    "cuneiform",
                    "fracture",
                    "jones",
                    "lisfranc",
                ],
                surgical_cases,
                protocol_documents,
                panel_key=f"{panel_key}_fractures_foot_related",
            )

        st.markdown("### Fracture Pattern Deep Dives")
        deep_dive_name = st.selectbox(
            "Select fracture pattern",
            list(fracture_deep_dives.keys()),
            key=f"{panel_key}_fracture_deep_dive",
        )
        deep_dive = fracture_deep_dives.get(deep_dive_name, {})
        dd_cols = st.columns(2)
        with dd_cols[0]:
            st.markdown(f"**Mechanism:** {deep_dive.get('mechanism', '')}")
            st.markdown(f"**Key radiographic signs:** {deep_dive.get('key_signs', '')}")
            st.markdown(f"**Pitfalls:** {deep_dive.get('pitfalls', '')}")
        with dd_cols[1]:
            st.markdown(f"**Common misses:** {deep_dive.get('common_misses', '')}")
            st.markdown(f"**Treatment pathway:** {deep_dive.get('treatment_path', '')}")

    with xray_tab:
        st.markdown("### X-ray Image Library")
        st.caption("Upload and organize non-PHI X-rays by body part and fracture type.")
        pending_delete_key = f"{panel_key}_xray_delete_pending"
        batch_selection_key = f"{panel_key}_xray_batch_selection"
        batch_pending_key = f"{panel_key}_xray_batch_pending"
        rotation_map_key = f"{panel_key}_xray_rotation_map"
        if rotation_map_key not in st.session_state:
            st.session_state[rotation_map_key] = {}

        with st.form(f"{panel_key}_xray_upload_form", clear_on_submit=True):
            upload_cols = st.columns(2)
            with upload_cols[0]:
                body_part = st.selectbox(
                    "Body part",
                    ["Foot", "Ankle", "Lower Leg", "Knee", "Other"],
                    key=f"{panel_key}_xray_body_part",
                )
                if body_part == "Other":
                    body_part = st.text_input("Custom body part", key=f"{panel_key}_xray_body_part_custom").strip() or "Other"
                fracture_type = st.selectbox(
                    "Fracture type",
                    [
                        "Unspecified",
                        "Avulsion",
                        "Stress",
                        "Jones",
                        "Lisfranc",
                        "Bimalleolar",
                        "Trimalleolar",
                        "Pilon",
                        "Tibial Plateau",
                        "Other",
                    ],
                    key=f"{panel_key}_xray_fracture_type",
                )
                if fracture_type == "Other":
                    fracture_type = st.text_input("Custom fracture type", key=f"{panel_key}_xray_fracture_custom").strip() or "Other"
            with upload_cols[1]:
                view_label = st.selectbox(
                    "X-ray view",
                    ["AP", "Lateral", "Oblique", "Mortise", "Sunrise", "Other"],
                    key=f"{panel_key}_xray_view_label",
                )
                if view_label == "Other":
                    view_label = st.text_input("Custom view", key=f"{panel_key}_xray_view_custom").strip() or "Other"
                xray_notes = st.text_area(
                    "Notes",
                    height=90,
                    placeholder="Optional educational notes (no PHI).",
                    key=f"{panel_key}_xray_notes",
                )

            xray_files = st.file_uploader(
                "X-ray image(s)",
                type=["png", "jpg", "jpeg", "webp"],
                key=f"{panel_key}_xray_file",
                accept_multiple_files=True,
                help="Upload non-PHI images only.",
            )
            no_phi_confirmed = st.checkbox(
                "I confirm this image contains no patient identifiers (no PHI).",
                key=f"{panel_key}_xray_phi_confirm",
            )
            xray_submit = st.form_submit_button("Upload X-ray", type="primary")

        if xray_submit:
            if not xray_files:
                st.warning("Select at least one X-ray image to upload.")
            elif not no_phi_confirmed:
                st.warning("Confirm the image contains no PHI before uploading.")
            else:
                uploaded_count = 0
                skipped_large = 0
                for xray_file in xray_files:
                    image_bytes = xray_file.getvalue()
                    if len(image_bytes) > 10 * 1024 * 1024:
                        skipped_large += 1
                        continue
                    add_anatomy_xray_image(
                        body_part=body_part,
                        fracture_type=fracture_type,
                        view_label=view_label,
                        image_name=xray_file.name,
                        image_mime=getattr(xray_file, "type", None),
                        image_bytes=image_bytes,
                        notes=xray_notes,
                    )
                    uploaded_count += 1
                if uploaded_count:
                    st.success(f"Uploaded {uploaded_count} X-ray image(s).")
                if skipped_large:
                    st.warning(f"Skipped {skipped_large} file(s) over 10 MB.")
                if uploaded_count:
                    st.rerun()

        xray_images = load_anatomy_xray_images()
        if xray_images:
            body_part_options = ["All"] + sorted({str(item.get("body_part") or "Unspecified") for item in xray_images})
            fracture_options = ["All"] + sorted({str(item.get("fracture_type") or "Unspecified") for item in xray_images})
            view_options = ["All"] + sorted({str(item.get("view_label") or "Unspecified") for item in xray_images})

            filter_cols = st.columns([1.1, 1.1, 1.1, 1.6, 1.2, 1.1])
            with filter_cols[0]:
                body_part_filter = st.selectbox("Body part filter", body_part_options, key=f"{panel_key}_xray_body_filter")
            with filter_cols[1]:
                fracture_filter = st.selectbox("Fracture filter", fracture_options, key=f"{panel_key}_xray_fracture_filter")
            with filter_cols[2]:
                view_filter = st.selectbox("View filter", view_options, key=f"{panel_key}_xray_view_filter")
            with filter_cols[3]:
                query = st.text_input("Search", placeholder="Filename or notes", key=f"{panel_key}_xray_query")
            with filter_cols[4]:
                xray_sort = st.selectbox(
                    "Sort",
                    ["Newest", "Oldest", "Body Part A-Z", "Fracture A-Z"],
                    key=f"{panel_key}_xray_sort",
                )
            with filter_cols[5]:
                view_mode = st.selectbox(
                    "Display",
                    ["List", "Grid"],
                    key=f"{panel_key}_xray_view_mode",
                )

            filtered_xrays = ref_filter_anatomy_xray_images(
                xray_images,
                body_part_filter=body_part_filter,
                fracture_filter=fracture_filter,
                view_filter=view_filter,
                query=query,
            )

            if xray_sort == "Oldest":
                filtered_xrays = sorted(
                    filtered_xrays,
                    key=lambda item: (item.get("created_date") or date.min, item.get("id") or 0),
                )
            elif xray_sort == "Body Part A-Z":
                filtered_xrays = sorted(
                    filtered_xrays,
                    key=lambda item: (str(item.get("body_part") or ""), str(item.get("fracture_type") or ""), str(item.get("image_name") or "")),
                )
            elif xray_sort == "Fracture A-Z":
                filtered_xrays = sorted(
                    filtered_xrays,
                    key=lambda item: (str(item.get("fracture_type") or ""), str(item.get("body_part") or ""), str(item.get("image_name") or "")),
                )
            else:
                filtered_xrays = sorted(
                    filtered_xrays,
                    key=lambda item: (item.get("created_date") or date.min, item.get("id") or 0),
                    reverse=True,
                )

            st.caption(f"Showing {len(filtered_xrays)} of {len(xray_images)} X-ray image(s)")

            st.markdown("### X-ray Teaching Mode")
            st.caption("Use side-by-side comparison with a structured read checklist and landmark overlays.")

            teach_options = {
                f"#{item.get('id')} | {item.get('body_part') or 'Unspecified'} | {item.get('fracture_type') or 'Unspecified'} | {item.get('view_label') or 'Unspecified'}": item
                for item in filtered_xrays
                if item.get("id") is not None
            }
            option_labels = list(teach_options.keys())
            if option_labels:
                teach_cols = st.columns([1.4, 1.4, 1.2])
                with teach_cols[0]:
                    normal_label = st.selectbox(
                        "Reference image",
                        option_labels,
                        key=f"{panel_key}_xray_teach_reference",
                    )
                with teach_cols[1]:
                    pathology_label = st.selectbox(
                        "Pathology image",
                        option_labels,
                        key=f"{panel_key}_xray_teach_pathology",
                    )
                with teach_cols[2]:
                    overlay_mode = st.multiselect(
                        "Overlay tools",
                        ["Center lines", "Quadrant grid", "Measurement guide"],
                        key=f"{panel_key}_xray_teach_overlay_mode",
                    )

                ref_item = teach_options.get(normal_label)
                path_item = teach_options.get(pathology_label)

                def _teaching_render(item, image_label, render_key):
                    if not item:
                        st.markdown('<div class="empty-state">No image selected.</div>', unsafe_allow_html=True)
                        return
                    image_bytes = item.get("image_bytes")
                    if isinstance(image_bytes, memoryview):
                        image_bytes = bytes(image_bytes)
                    if not image_bytes:
                        st.markdown('<div class="empty-state">Image bytes unavailable.</div>', unsafe_allow_html=True)
                        return

                    render_bytes = image_bytes
                    if overlay_mode:
                        try:
                            from PIL import Image, ImageDraw

                            image = Image.open(BytesIO(image_bytes)).convert("RGB")
                            draw = ImageDraw.Draw(image)
                            width, height = image.size
                            line_color = (231, 76, 60)
                            if "Center lines" in overlay_mode:
                                draw.line([(width // 2, 0), (width // 2, height)], fill=line_color, width=3)
                                draw.line([(0, height // 2), (width, height // 2)], fill=line_color, width=3)
                            if "Quadrant grid" in overlay_mode:
                                draw.line([(width // 4, 0), (width // 4, height)], fill=(52, 152, 219), width=2)
                                draw.line([(3 * width // 4, 0), (3 * width // 4, height)], fill=(52, 152, 219), width=2)
                                draw.line([(0, height // 4), (width, height // 4)], fill=(52, 152, 219), width=2)
                                draw.line([(0, 3 * height // 4), (width, 3 * height // 4)], fill=(52, 152, 219), width=2)
                            if "Measurement guide" in overlay_mode:
                                draw.line([(int(width * 0.15), int(height * 0.82)), (int(width * 0.85), int(height * 0.82))], fill=(46, 204, 113), width=3)
                            buffer = BytesIO()
                            image.save(buffer, format="PNG")
                            render_bytes = buffer.getvalue()
                        except Exception:
                            render_bytes = image_bytes

                    st.image(render_bytes, caption=image_label, use_container_width=True)
                    st.caption(
                        f"{item.get('body_part') or 'Unspecified'} | {item.get('fracture_type') or 'Unspecified'} | {item.get('view_label') or 'Unspecified'}"
                    )

                compare_cols = st.columns(2)
                with compare_cols[0]:
                    _teaching_render(ref_item, "Reference", "reference")
                with compare_cols[1]:
                    _teaching_render(path_item, "Pathology", "pathology")

                st.markdown("#### Stepwise Read Checklist")
                checklist_cols = st.columns(2)
                with checklist_cols[0]:
                    st.checkbox("Image quality adequate (exposure/rotation)", key=f"{panel_key}_xray_check_quality")
                    st.checkbox("Alignment reviewed", key=f"{panel_key}_xray_check_alignment")
                    st.checkbox("Bones reviewed for cortical interruption", key=f"{panel_key}_xray_check_bones")
                with checklist_cols[1]:
                    st.checkbox("Joint congruence reviewed", key=f"{panel_key}_xray_check_joints")
                    st.checkbox("Soft tissue and effusion reviewed", key=f"{panel_key}_xray_check_soft_tissue")
                    st.checkbox("Mechanism/pathology fit verified", key=f"{panel_key}_xray_check_mechanism")
            else:
                st.markdown('<div class="empty-state">Add images to use teaching mode.</div>', unsafe_allow_html=True)

            batch_label_to_id = {}
            batch_options = []
            for item in filtered_xrays:
                image_id = item.get("id")
                if image_id is None:
                    continue
                option_label = f"#{image_id} | {item.get('body_part') or 'Unspecified'} | {item.get('fracture_type') or 'Unspecified'} | {item.get('image_name') or 'X-ray'}"
                batch_options.append(option_label)
                batch_label_to_id[option_label] = image_id

            batch_cols = st.columns([2.4, 1, 1, 1, 2.2])
            with batch_cols[0]:
                selected_batch_labels = st.multiselect(
                    "Batch select",
                    options=batch_options,
                    key=batch_selection_key,
                    placeholder="Select images for batch delete",
                )
            with batch_cols[1]:
                if st.button("Select All", key=f"{panel_key}_xray_select_all_batch", use_container_width=True):
                    st.session_state[batch_selection_key] = list(batch_options)
                    st.rerun()
            with batch_cols[2]:
                if st.button("Stage Delete", key=f"{panel_key}_xray_stage_batch_delete", use_container_width=True):
                    selected_ids = [batch_label_to_id[label] for label in selected_batch_labels if label in batch_label_to_id]
                    if not selected_ids:
                        st.warning("Select at least one image to delete.")
                    else:
                        st.session_state[batch_pending_key] = selected_ids
                        st.session_state.pop(pending_delete_key, None)
                        st.rerun()
            with batch_cols[3]:
                if st.button("Clear", key=f"{panel_key}_xray_clear_batch_select", use_container_width=True):
                    st.session_state[batch_selection_key] = []
                    st.session_state.pop(batch_pending_key, None)
                    st.rerun()

            pending_batch_ids = st.session_state.get(batch_pending_key) or []
            if pending_batch_ids:
                st.warning(f"Confirm batch deletion of {len(pending_batch_ids)} image(s). This action cannot be undone.")
                confirm_batch_cols = st.columns([1, 1, 4])
                with confirm_batch_cols[0]:
                    if st.button("Confirm Batch", key=f"{panel_key}_xray_confirm_batch_delete", use_container_width=True):
                        for image_id in pending_batch_ids:
                            delete_anatomy_xray_image(image_id)
                        st.session_state[batch_pending_key] = []
                        st.session_state[batch_selection_key] = []
                        st.success(f"Deleted {len(pending_batch_ids)} image(s).")
                        st.rerun()
                with confirm_batch_cols[1]:
                    if st.button("Cancel Batch", key=f"{panel_key}_xray_cancel_batch_delete", use_container_width=True):
                        st.session_state[batch_pending_key] = []
                        st.rerun()

            def _render_xray_item(item, prefix):
                image_id = item.get("id")
                image_bytes = item.get("image_bytes")
                if isinstance(image_bytes, memoryview):
                    image_bytes = bytes(image_bytes)

                rotation_map = st.session_state.get(rotation_map_key, {})
                current_rotation = int(rotation_map.get(image_id, 0)) % 360

                rotated_bytes = None
                if image_bytes and current_rotation:
                    try:
                        from PIL import Image

                        pil_image = Image.open(BytesIO(image_bytes))
                        rotated = pil_image.rotate(-current_rotation, expand=True)
                        buffer = BytesIO()
                        save_format = pil_image.format or "PNG"
                        rotated.save(buffer, format=save_format)
                        rotated_bytes = buffer.getvalue()
                    except Exception:
                        rotated_bytes = None

                st.markdown('<div style="border:1px solid #d8dee7; border-radius:12px; padding:0.85rem; margin:0.75rem 0; background:#fff;">', unsafe_allow_html=True)
                meta_cols = st.columns([2.2, 1.1, 1.1, 1.1, 1.1])
                with meta_cols[0]:
                    st.markdown(f"**{item.get('image_name') or 'X-ray'}**")
                    st.caption(
                        f"{item.get('body_part') or 'Unspecified'} · {item.get('fracture_type') or 'Unspecified'} · {item.get('view_label') or 'Unspecified'} · rotation {current_rotation}°"
                    )
                with meta_cols[1]:
                    if image_bytes:
                        st.download_button(
                            "Download",
                            data=image_bytes,
                            file_name=item.get("image_name") or "xray_image",
                            mime=item.get("image_mime") or "application/octet-stream",
                            key=f"{panel_key}_xray_download_{prefix}_{image_id}",
                        )
                with meta_cols[2]:
                    if st.button("Rotate -90", key=f"{panel_key}_xray_rotate_left_{prefix}_{image_id}"):
                        rotation_map[image_id] = (current_rotation - 90) % 360
                        st.session_state[rotation_map_key] = rotation_map
                        st.rerun()
                with meta_cols[3]:
                    if st.button("Rotate +90", key=f"{panel_key}_xray_rotate_right_{prefix}_{image_id}"):
                        rotation_map[image_id] = (current_rotation + 90) % 360
                        st.session_state[rotation_map_key] = rotation_map
                        st.rerun()
                with meta_cols[4]:
                    if st.button("Delete", key=f"{panel_key}_xray_delete_{prefix}_{image_id}"):
                        st.session_state[pending_delete_key] = image_id
                        st.session_state.pop(batch_pending_key, None)
                        st.rerun()

                rotate_reset_cols = st.columns([1.3, 1.7, 3.0])
                with rotate_reset_cols[0]:
                    if st.button("Reset Rotation", key=f"{panel_key}_xray_reset_rotate_{prefix}_{image_id}", use_container_width=True):
                        rotation_map[image_id] = 0
                        st.session_state[rotation_map_key] = rotation_map
                        st.rerun()
                with rotate_reset_cols[1]:
                    if current_rotation and rotated_bytes:
                        rotated_name = item.get("image_name") or "xray_image"
                        if "." in rotated_name:
                            base_name, ext = rotated_name.rsplit(".", 1)
                            rotated_name = f"{base_name}_rot{current_rotation}.{ext}"
                        else:
                            rotated_name = f"{rotated_name}_rot{current_rotation}"
                        st.download_button(
                            "Download Rotated",
                            data=rotated_bytes,
                            file_name=rotated_name,
                            mime=item.get("image_mime") or "application/octet-stream",
                            key=f"{panel_key}_xray_download_rotated_{prefix}_{image_id}",
                            use_container_width=True,
                        )
                with rotate_reset_cols[2]:
                    created_value = item.get("created_date")
                    created_label = created_value.strftime("%b %d, %Y") if hasattr(created_value, "strftime") else str(created_value or "")
                    st.caption(f"Uploaded {created_label}")

                if st.session_state.get(pending_delete_key) == image_id:
                    st.warning("Confirm image deletion. This action cannot be undone.")
                    confirm_cols = st.columns([1, 1, 3])
                    with confirm_cols[0]:
                        if st.button("Confirm Delete", key=f"{panel_key}_xray_confirm_delete_{prefix}_{image_id}", use_container_width=True):
                            delete_anatomy_xray_image(image_id)
                            st.session_state.pop(pending_delete_key, None)
                            st.success("X-ray image deleted.")
                            st.rerun()
                    with confirm_cols[1]:
                        if st.button("Cancel", key=f"{panel_key}_xray_cancel_delete_{prefix}_{image_id}", use_container_width=True):
                            st.session_state.pop(pending_delete_key, None)
                            st.rerun()

                if image_bytes:
                    render_bytes = rotated_bytes if (current_rotation and rotated_bytes) else image_bytes
                    if current_rotation and not rotated_bytes:
                        st.caption("Unable to rotate this image in-app.")
                    st.image(render_bytes, caption=item.get("view_label") or "X-ray", use_container_width=True)
                if item.get("notes"):
                    st.caption(item.get("notes"))
                st.markdown("</div>", unsafe_allow_html=True)

            if view_mode == "Grid":
                grid_cols = st.columns(3)
                for idx, item in enumerate(filtered_xrays):
                    with grid_cols[idx % 3]:
                        _render_xray_item(item, "grid")
            else:
                for item in filtered_xrays:
                    _render_xray_item(item, "list")
        else:
            st.markdown('<div class="empty-state">No X-ray images uploaded yet. Add your first non-PHI image above.</div>', unsafe_allow_html=True)

    with quiz_tab:
        st.markdown("### Interactive Quiz Engine")
        st.caption("Train anatomy labels and fracture diagnosis with confidence scoring and spaced review.")

        missed_queue_key = f"{panel_key}_quiz_missed_queue"
        quiz_attempts = load_anatomy_quiz_attempts() or []
        review_queue_rows = load_anatomy_quiz_review_queue() or []
        review_queue = [
            str(item.get("review_text") or "").strip()
            for item in review_queue_rows
            if str(item.get("review_text") or "").strip()
        ]
        st.session_state[missed_queue_key] = review_queue

        total_attempts = len(quiz_attempts)
        correct_attempts = sum(1 for item in quiz_attempts if bool(item.get("is_correct")))
        accuracy = round((correct_attempts / total_attempts) * 100, 1) if total_attempts else 0.0

        quiz_metric_cols = st.columns(3)
        quiz_metric_cols[0].metric("Attempts", total_attempts)
        quiz_metric_cols[1].metric("Accuracy", f"{accuracy}%")
        quiz_metric_cols[2].metric("Review queue", len(review_queue))

        with st.expander("Recent Quiz History", expanded=False):
            if not quiz_attempts:
                st.caption("No quiz attempts yet. Submit a label or diagnosis quiz to populate history.")
            else:
                history_rows = []
                for attempt in quiz_attempts[:10]:
                    created_value = attempt.get("created_date")
                    created_label = created_value.strftime("%Y-%m-%d") if hasattr(created_value, "strftime") else str(created_value or "")
                    confidence_value = attempt.get("confidence")
                    confidence_label = f"{confidence_value}%" if confidence_value is not None else "-"
                    history_rows.append(
                        {
                            "Date": created_label,
                            "Mode": str(attempt.get("quiz_mode") or ""),
                            "Result": "Correct" if bool(attempt.get("is_correct")) else "Miss",
                            "Confidence": confidence_label,
                            "Prompt": str(attempt.get("prompt") or ""),
                            "Expected": str(attempt.get("expected_answer") or ""),
                            "Submitted": str(attempt.get("submitted_answer") or ""),
                            "Teaching Note": str(attempt.get("explanation") or ""),
                        }
                    )

                st.caption("Showing the latest 10 quiz attempts.")
                st.dataframe(history_rows, use_container_width=True, hide_index=True)

        quiz_mode = st.radio(
            "Quiz mode",
            ["Label Quiz", "Diagnosis Quiz", "Review Missed"],
            horizontal=True,
            key=f"{panel_key}_quiz_mode",
        )

        quiz_images = load_anatomy_xray_images() or []
        if not quiz_images and quiz_mode != "Review Missed":
            st.markdown('<div class="empty-state">Upload X-rays first to enable interactive quiz modes.</div>', unsafe_allow_html=True)
        elif quiz_mode == "Review Missed":
            queue = st.session_state.get(missed_queue_key, [])
            if not queue:
                st.success("No missed items in queue.")
            else:
                st.markdown("#### Spaced Repetition Queue")
                for idx, item in enumerate(queue, start=1):
                    st.markdown(f"{idx}. {item}")
                if st.button("Clear Review Queue", key=f"{panel_key}_quiz_clear_queue"):
                    clear_anatomy_quiz_review_queue()
                    st.session_state[missed_queue_key] = []
                    st.rerun()
        elif quiz_mode == "Label Quiz":
            question_item = quiz_images[0]
            if len(quiz_images) > 1:
                question_index = st.selectbox(
                    "Question image",
                    options=list(range(len(quiz_images))),
                    format_func=lambda idx: f"#{quiz_images[idx].get('id')} {quiz_images[idx].get('image_name') or 'X-ray'}",
                    key=f"{panel_key}_quiz_label_index",
                )
                question_item = quiz_images[question_index]

            image_bytes = question_item.get("image_bytes")
            if isinstance(image_bytes, memoryview):
                image_bytes = bytes(image_bytes)
            if image_bytes:
                st.image(image_bytes, caption="Label this image", use_container_width=True)

            body_part_options = sorted({str(item.get("body_part") or "Unspecified") for item in quiz_images})
            fracture_options = sorted({str(item.get("fracture_type") or "Unspecified") for item in quiz_images})
            view_options = sorted({str(item.get("view_label") or "Unspecified") for item in quiz_images})

            answer_cols = st.columns(4)
            with answer_cols[0]:
                body_guess = st.selectbox("Body part", body_part_options, key=f"{panel_key}_quiz_guess_body")
            with answer_cols[1]:
                fracture_guess = st.selectbox("Fracture", fracture_options, key=f"{panel_key}_quiz_guess_fracture")
            with answer_cols[2]:
                view_guess = st.selectbox("View", view_options, key=f"{panel_key}_quiz_guess_view")
            with answer_cols[3]:
                confidence = st.slider("Confidence %", min_value=0, max_value=100, value=70, key=f"{panel_key}_quiz_confidence")

            if st.button("Submit Label Quiz", key=f"{panel_key}_quiz_submit_label", type="primary"):
                expected_body = str(question_item.get("body_part") or "Unspecified")
                expected_fracture = str(question_item.get("fracture_type") or "Unspecified")
                expected_view = str(question_item.get("view_label") or "Unspecified")
                prompt_text = f"Label quiz image #{question_item.get('id') or 'unknown'}"
                submitted_text = f"{body_guess} / {fracture_guess} / {view_guess}"
                expected_text = f"{expected_body} / {expected_fracture} / {expected_view}"

                correct_parts = []
                if body_guess == expected_body:
                    correct_parts.append("body part")
                if fracture_guess == expected_fracture:
                    correct_parts.append("fracture type")
                if view_guess == expected_view:
                    correct_parts.append("view")

                if len(correct_parts) == 3:
                    add_anatomy_quiz_attempt(
                        "Label Quiz",
                        prompt_text,
                        expected_text,
                        submitted_text,
                        confidence,
                        True,
                        "All label fields matched.",
                    )
                    st.success(f"Correct with {confidence}% confidence. Great read.")
                else:
                    missed_summary = f"Label quiz miss: expected {expected_body} / {expected_fracture} / {expected_view}"
                    add_anatomy_quiz_review_item(missed_summary)
                    add_anatomy_quiz_attempt(
                        "Label Quiz",
                        prompt_text,
                        expected_text,
                        submitted_text,
                        confidence,
                        False,
                        "Use body-part landmarks first, then classify fracture pattern, then confirm view orientation.",
                    )
                    st.warning(
                        "Not fully correct. "
                        f"Correct answers: {expected_body} / {expected_fracture} / {expected_view}."
                    )
                    st.info("Teaching explanation: Use body-part landmarks first, then classify fracture pattern, then confirm view orientation.")
        else:
            diagnosis_names = list(fracture_deep_dives.keys())
            diagnosis_case = st.selectbox(
                "Scenario",
                diagnosis_names,
                key=f"{panel_key}_quiz_dx_case",
            )
            payload = fracture_deep_dives.get(diagnosis_case, {})
            st.markdown(f"**Mechanism clue:** {payload.get('mechanism', '')}")
            st.markdown(f"**Radiographic clue:** {payload.get('key_signs', '')}")

            diagnosis_guess = st.selectbox(
                "Most likely diagnosis",
                diagnosis_names,
                key=f"{panel_key}_quiz_dx_guess",
            )
            dx_confidence = st.slider("Confidence %", min_value=0, max_value=100, value=65, key=f"{panel_key}_quiz_dx_conf")
            if st.button("Submit Diagnosis Quiz", key=f"{panel_key}_quiz_submit_dx", type="primary"):
                submitted_text = diagnosis_guess
                expected_text = diagnosis_case
                if diagnosis_guess == diagnosis_case:
                    add_anatomy_quiz_attempt(
                        "Diagnosis Quiz",
                        payload.get("mechanism", "Diagnosis scenario"),
                        expected_text,
                        submitted_text,
                        dx_confidence,
                        True,
                        payload.get("pitfalls", ""),
                    )
                    st.success(f"Correct diagnosis at {dx_confidence}% confidence.")
                    st.info(f"Why: {payload.get('pitfalls', '')}")
                else:
                    add_anatomy_quiz_review_item(f"Diagnosis miss: expected {diagnosis_case}")
                    add_anatomy_quiz_attempt(
                        "Diagnosis Quiz",
                        payload.get("mechanism", "Diagnosis scenario"),
                        expected_text,
                        submitted_text,
                        dx_confidence,
                        False,
                        payload.get("common_misses", ""),
                    )
                    st.warning(f"Incorrect. Expected diagnosis: {diagnosis_case}.")
                    st.info(f"Teaching explanation: {payload.get('common_misses', '')}")

    with case_tracks_tab:
        st.markdown("### Case-Based Learning Tracks")
        st.caption("Work unknowns in a progressive reveal format from history to final diagnosis.")

        case_names = [item["title"] for item in case_learning_tracks]
        selected_case_name = st.selectbox(
            "Unknown case",
            case_names,
            key=f"{panel_key}_case_track_name",
        )
        selected_case = next((item for item in case_learning_tracks if item["title"] == selected_case_name), case_learning_tracks[0])

        reveal_stage = st.slider(
            "Reveal stage",
            min_value=1,
            max_value=5,
            value=2,
            help="1=History, 2=Exam, 3=Imaging clue, 4=Differential, 5=Final diagnosis",
            key=f"{panel_key}_case_track_stage",
        )

        st.markdown("#### Progressive Reveal")
        st.markdown(f"**History:** {selected_case['history']}")
        if reveal_stage >= 2:
            st.markdown(f"**Exam:** {selected_case['exam']}")
        if reveal_stage >= 3:
            st.markdown(f"**Imaging clue:** {selected_case['imaging_hint']}")
        if reveal_stage >= 4:
            st.markdown("**Differential:**")
            for dx in selected_case["differential"]:
                st.markdown(f"- {dx}")
        if reveal_stage >= 5:
            st.success(f"Final diagnosis: {selected_case['final_dx']}")
            st.markdown("**Teaching pearls**")
            for pearl in selected_case["pearls"]:
                st.markdown(f"- {pearl}")

        st.markdown("#### Board-Style Check")
        st.markdown(f"**Question:** {selected_case['board_question']}")
        board_response = st.text_input("Your answer", key=f"{panel_key}_case_track_board_response")
        if st.button("Check Answer", key=f"{panel_key}_case_track_check_answer"):
            normalized_response = board_response.strip().lower()
            normalized_answer = selected_case["board_answer"].strip().lower()
            if normalized_response and normalized_answer in normalized_response:
                st.success("Correct.")
            else:
                st.info(f"Expected answer: {selected_case['board_answer']}")

    with exam_tab:
        st.markdown("### Orthopedic Exam Library")
        st.caption("Foot and ankle emphasized, with adjacent-joint and kinetic-chain screening.")

        exam_library = {
            "Foot": [
                ("Windlass test", "Plantar fascia irritability", "Reproduction of medial plantar heel pain with hallux dorsiflexion"),
                ("Mulder click", "Morton neuroma", "Forefoot squeeze with symptomatic web-space click/pain"),
                ("Single-leg heel rise", "Posterior tibial tendon function", "Pain, weakness, or inability suggests dysfunction"),
            ],
            "Ankle": [
                ("Anterior drawer", "ATFL laxity", "Increased anterior talar translation versus contralateral side"),
                ("Talar tilt", "CFL/lateral complex", "Excess inversion tilt compared to opposite ankle"),
                ("Squeeze test", "Syndesmosis", "Pain over distal tibiofibular syndesmosis with proximal squeeze"),
            ],
            "Related Chain (Knee/Hip/Spine)": [
                ("Single-leg squat", "Dynamic valgus control", "Poor frontal-plane control can drive foot/ankle overload"),
                ("Hip abductor endurance", "Pelvic control in gait", "Trendelenburg pattern may increase distal load"),
                ("Neurodynamic screen", "Lumbar referred symptoms", "Radicular symptoms can mimic distal pain patterns"),
            ],
        }

        exam_region = st.radio(
            "Exam region",
            list(exam_library.keys()),
            horizontal=True,
            key=f"{panel_key}_exam_region",
        )
        for test_name, target, positive_clue in exam_library[exam_region]:
            with st.expander(test_name, expanded=False):
                st.markdown(f"**Targets:** {target}")
                st.markdown(f"**Helpful positive clue:** {positive_clue}")

    with imaging_tab:
        st.markdown("### Imaging Helper")
        st.caption("Quick orthopedic imaging pathways anchored to foot and ankle practice.")

        imaging_scenarios = {
            "Acute ankle injury": {
                "first": "Weight-bearing ankle radiographs (or Ottawa-rule directed views).",
                "next": "MRI if persistent pain/instability, osteochondral concern, or unclear soft tissue injury.",
                "notes": "Always correlate with syndesmosis and deltoid exam findings.",
            },
            "Chronic heel pain": {
                "first": "Weight-bearing foot radiographs.",
                "next": "Ultrasound for plantar fascia/Achilles; MRI for stress injury or refractory symptoms.",
                "notes": "Check proximal kinetic-chain drivers when imaging is not proportional to symptoms.",
            },
            "Medial arch collapse": {
                "first": "Weight-bearing AP/lateral foot and ankle radiographs.",
                "next": "MRI when posterior tibial tendon or spring ligament staging is needed.",
                "notes": "Useful for operative planning and protocol selection.",
            },
            "Forefoot neurologic pain": {
                "first": "Weight-bearing forefoot radiographs.",
                "next": "Ultrasound or MRI for neuroma/plantar plate differentiation.",
                "notes": "Combine with footwear and biomechanical assessment.",
            },
        }

        imaging_choice = st.selectbox("Scenario", list(imaging_scenarios.keys()), key=f"{panel_key}_imaging_scenario")
        selected_plan = imaging_scenarios[imaging_choice]
        st.markdown(f"**First-line imaging:** {selected_plan['first']}")
        st.markdown(f"**Escalation:** {selected_plan['next']}")
        st.markdown(f"**Clinical note:** {selected_plan['notes']}")

    st.markdown('</div>', unsafe_allow_html=True)


def render_surgical_cases_panel(surgical_cases, protocol_documents, app_settings, panel_key="cases"):
    predicted_days = predicted_or_days(app_settings, horizon_days=120)
    predicted_labels = {day: label for day, label in predicted_days}
    upcoming_predicted = [item for item in predicted_days if item[0] >= mountain_today()]

    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Surgical Cases</h3><span>Non-PHI case log for surgery and TenJet procedures</span></div>', unsafe_allow_html=True)
    st.caption("Store procedure name, date, and anatomical location only. Do not enter patient identifiers.")

    st.markdown('<div class="panel-title" style="margin-top:0.5rem;"><h3>Protocol Library</h3><span>Upload and reference BB protocols</span></div>', unsafe_allow_html=True)
    with st.form(f"{panel_key}_protocol_upload_form"):
        protocol_surgeon_label = st.text_input("Surgeon label", value=app_settings.get("default_surgeon_label", "Dr. Braden Boyer (BB)"))
        protocol_name = st.text_input("Protocol title")
        protocol_notes = st.text_area("Protocol notes", height=80, placeholder="Key steps, pearls, contraindications, follow-up details...")
        protocol_file = st.file_uploader(
            "Protocol file",
            type=["pdf", "doc", "docx", "txt", "md"],
            key=f"{panel_key}_protocol_file",
            help="Upload non-PHI protocol documents only.",
        )
        protocol_submit = st.form_submit_button("Upload protocol", type="secondary")

    if protocol_submit:
        if not protocol_file:
            st.warning("Select a protocol file to upload.")
        else:
            file_bytes = protocol_file.getvalue()
            if len(file_bytes) > 12 * 1024 * 1024:
                st.warning("File is too large. Keep uploads under 12 MB.")
            else:
                add_protocol_document(
                    surgeon_label=protocol_surgeon_label,
                    protocol_name=protocol_name,
                    upload_name=protocol_file.name,
                    upload_mime=getattr(protocol_file, "type", None),
                    upload_bytes=file_bytes,
                    notes=protocol_notes,
                )
                st.success("Protocol uploaded.")
                st.rerun()

    if filtered_protocols:
        for doc in filtered_protocols[:12]:
            doc_id = doc.get("id")
            doc_bytes = doc.get("file_bytes")
            if isinstance(doc_bytes, memoryview):
                doc_bytes = bytes(doc_bytes)
            st.markdown(
                f"- <strong>{doc.get('protocol_name')}</strong> · {doc.get('surgeon_label')} · {doc.get('file_name')}",
                unsafe_allow_html=True,
            )
            if doc.get("notes"):
                st.caption(doc.get("notes"))
            doc_cols = st.columns([1, 1, 1])
            with doc_cols[0]:
                if doc_bytes:
                    st.download_button(
                        label="Download",
                        data=doc_bytes,
                        file_name=doc.get("file_name") or "protocol.pdf",
                        mime=doc.get("file_mime") or "application/octet-stream",
                        key=f"{panel_key}_protocol_download_{doc_id}",
                    )
            with doc_cols[1]:
                if st.button("Delete", key=f"{panel_key}_protocol_delete_{doc_id}"):
                    delete_protocol_document(doc_id)
                    st.success("Protocol deleted.")
                    st.rerun()
    else:
        st.markdown('<div class="empty-state">No protocols uploaded yet. Add BB protocols to reference during case prep.</div>', unsafe_allow_html=True)

    st.markdown('<div style="height: 0.8rem;"></div>', unsafe_allow_html=True)

    metrics = st.columns(4)
    planned_cases = [item for item in filtered_cases if item.get("status") == "planned"]
    completed_cases = [item for item in filtered_cases if item.get("status") == "completed"]
    tenjet_cases = [item for item in filtered_cases if item.get("case_stream") == "TenJet"]
    main_or_cases = [item for item in filtered_cases if item.get("case_stream") == "Main OR"]
    metrics[0].metric("Planned", len(planned_cases))
    metrics[1].metric("Completed", len(completed_cases))
    metrics[2].metric("Main OR", len(main_or_cases))
    metrics[3].metric("TenJet", len(tenjet_cases))

    filter_row = st.columns([1.35, 0.85, 0.85, 0.9])
    with filter_row[0]:
        search_term = st.text_input(
            "Search cases and protocols",
            placeholder="Procedure, anatomy, note, or protocol title...",
            key=f"{panel_key}_search",
        ).strip().lower()
    with filter_row[1]:
        stream_filter = st.selectbox("Stream", ["All", "Main OR", "DSC OR", "TenJet"], key=f"{panel_key}_stream_filter")
    with filter_row[2]:
        status_filter = st.selectbox("Status", ["All", "planned", "completed", "canceled"], key=f"{panel_key}_status_filter")
    with filter_row[3]:
        region_filter = st.selectbox("Region", ["All", "Foot", "Ankle", "Lower Leg", "Knee"], key=f"{panel_key}_region_filter")

    region_tokens = {
        "Foot": ["foot", "plantar", "metatarsal", "hallux", "midfoot", "forefoot", "tarsal"],
        "Ankle": ["ankle", "achilles", "peroneal", "talocrural", "subtalar", "syndesmosis"],
        "Lower Leg": ["calf", "lower leg", "gastrocnemius", "soleus", "tibia", "fibula"],
        "Knee": ["knee", "acl", "pcl", "meniscus", "patella", "tibiofemoral"],
    }

    def matches_case_filters(item):
        combined_text = " ".join(
            [
                str(item.get("procedure_name") or ""),
                str(item.get("anatomical_location") or ""),
                str(item.get("notes") or ""),
                str(item.get("education_notes") or ""),
            ]
        ).lower()
        if search_term and search_term not in combined_text:
            return False
        if stream_filter != "All" and item.get("case_stream") != stream_filter:
            return False
        if status_filter != "All" and item.get("status") != status_filter:
            return False
        if region_filter != "All" and not any(token in combined_text for token in region_tokens.get(region_filter, [])):
            return False
        return True

    def matches_protocol_filters(doc):
        combined_text = " ".join(
            [
                str(doc.get("protocol_name") or ""),
                str(doc.get("file_name") or ""),
                str(doc.get("notes") or ""),
            ]
        ).lower()
        if search_term and search_term not in combined_text:
            return False
        if region_filter != "All" and not any(token in combined_text for token in region_tokens.get(region_filter, [])):
            return False
        return True

    filtered_cases = [item for item in surgical_cases if matches_case_filters(item)]
    filtered_protocols = [item for item in protocol_documents if matches_protocol_filters(item)]
    if search_term or stream_filter != "All" or status_filter != "All" or region_filter != "All":
        st.caption(f"Showing {len(filtered_cases)} case(s) and {len(filtered_protocols)} protocol(s) after filters.")

    top_left, top_right = st.columns([1.1, 0.9], gap="large")
    with top_left:
        with st.form(f"{panel_key}_new_case_form"):
            case_date = st.date_input("Case date", value=mountain_today())
            case_stream = st.selectbox("Case stream", ["Main OR", "DSC OR", "TenJet"])
            or_facility = st.selectbox("OR facility", ["Mercy OR", "DSC OR"])
            procedure_name = st.text_input("Procedure performed")
            anatomical_location = st.text_input("Anatomical location")
            status = st.selectbox("Status", ["planned", "completed", "canceled"])
            notes = st.text_area("Notes (non-PHI)", height=80)
            education_url = st.text_input("Education link (optional)", placeholder="https://...")
            education_notes = st.text_area("Educational description", height=90, placeholder="What the case is, key anatomy, technical pearls, postop points...")
            submit_case = st.form_submit_button("Add surgical case", type="primary")

        if submit_case:
            if not procedure_name.strip():
                st.warning("Add the procedure name before saving.")
            else:
                add_surgical_case(
                    case_date=case_date,
                    case_stream=case_stream,
                    procedure_name=procedure_name,
                    anatomical_location=anatomical_location,
                    or_facility=or_facility,
                    status=status,
                    notes=notes,
                    education_url=education_url,
                    education_notes=education_notes,
                )
                st.success("Surgical case saved.")
                st.rerun()

    with top_right:
        st.markdown('<div class="panel-title"><h3>Predicted OR Days</h3><span>Every Friday + alternating weekday pattern</span></div>', unsafe_allow_html=True)
        if upcoming_predicted:
            for day, label in upcoming_predicted[:10]:
                st.markdown(f"- <strong>{day.strftime('%a %b %d')}</strong> · {label}", unsafe_allow_html=True)
        else:
            st.markdown('<div class="empty-state">No OR days predicted for the selected cadence.</div>', unsafe_allow_html=True)

    st.markdown('<div style="height: 0.8rem;"></div>', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>OR Calendar</h3><span>Month view of OR cadence and logged cases</span></div>', unsafe_allow_html=True)
    month_key = f"{panel_key}_month_anchor"
    if month_key not in st.session_state:
        st.session_state[month_key] = mountain_today().replace(day=1)

    calendar_controls = st.columns([1, 2, 1])
    with calendar_controls[0]:
        if st.button("Prev month", key=f"{panel_key}_prev_month"):
            current_anchor = st.session_state[month_key]
            previous_month_end = current_anchor - timedelta(days=1)
            st.session_state[month_key] = previous_month_end.replace(day=1)
            st.rerun()
    with calendar_controls[1]:
        st.markdown(
            f"<div style='text-align:center; font-weight:700; margin-top:0.4rem;'>{calendar.month_name[st.session_state[month_key].month]} {st.session_state[month_key].year}</div>",
            unsafe_allow_html=True,
        )
    with calendar_controls[2]:
        if st.button("Next month", key=f"{panel_key}_next_month"):
            current_anchor = st.session_state[month_key]
            next_month_start = (current_anchor.replace(day=28) + timedelta(days=4)).replace(day=1)
            st.session_state[month_key] = next_month_start
            st.rerun()

    render_or_calendar_compact(surgical_cases, predicted_labels, st.session_state[month_key], panel_key)

    st.markdown('<div style="height: 0.8rem;"></div>', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Recent Cases</h3><span>Track what was scheduled and what was done</span></div>', unsafe_allow_html=True)
    if filtered_cases:
        for item in filtered_cases[:20]:
            case_id = item.get("id")
            case_date_value = item.get("case_date")
            date_label = case_date_value.strftime("%b %d, %Y") if hasattr(case_date_value, "strftime") else str(case_date_value)
            or_hint = predicted_labels.get(case_date_value)
            hint_suffix = f" · {or_hint}" if or_hint else ""
            st.markdown(
                f"<div class='task-card'><div class='task-title'>{item.get('procedure_name')}</div>"
                f"<div class='task-meta'><span class='pill'>{date_label}{hint_suffix}</span><span class='pill pill-category'>{item.get('case_stream')}</span><span class='pill pill-status-in_progress'>{item.get('or_facility') or 'Mercy OR'}</span><span class='pill pill-status'>{str(item.get('status', 'planned')).title()}</span><span class='pill'>{item.get('anatomical_location') or 'Location not specified'}</span></div>"
                f"<p style='margin-top:0.6rem;'>{item.get('notes') or ''}</p></div>",
                unsafe_allow_html=True,
            )
            if item.get("education_url"):
                st.markdown(f"[Case Education Link]({item.get('education_url')})")
            if item.get("education_notes"):
                with st.expander("Educational Description", expanded=False):
                    st.write(item.get("education_notes"))

            suggestions = ref_suggest_protocols_for_case(item, protocol_documents, max_items=3)
            if suggestions:
                st.markdown("**Suggested Protocols**")
                for score, overlap_terms, doc in suggestions:
                    doc_id = doc.get("id")
                    doc_bytes = doc.get("file_bytes")
                    if isinstance(doc_bytes, memoryview):
                        doc_bytes = bytes(doc_bytes)
                    st.markdown(
                        f"- **{doc.get('protocol_name')}** (match score: {score}) · keywords: {', '.join(overlap_terms)}",
                        unsafe_allow_html=True,
                    )
                    if doc_bytes:
                        st.download_button(
                            label=f"Download {doc.get('file_name')}",
                            data=doc_bytes,
                            file_name=doc.get("file_name") or "protocol.pdf",
                            mime=doc.get("file_mime") or "application/octet-stream",
                            key=f"{panel_key}_case_suggested_download_{case_id}_{doc_id}",
                        )
            row_cols = st.columns([1, 1, 1])
            with row_cols[0]:
                new_status = st.selectbox(
                    "Status",
                    ["planned", "completed", "canceled"],
                    index=["planned", "completed", "canceled"].index(item.get("status", "planned")) if item.get("status", "planned") in ["planned", "completed", "canceled"] else 0,
                    key=f"{panel_key}_status_{case_id}",
                    label_visibility="collapsed",
                )
            with row_cols[1]:
                if st.button("Update", key=f"{panel_key}_update_{case_id}"):
                    update_surgical_case(case_id, status=new_status)
                    st.success("Case status updated.")
                    st.rerun()
            with row_cols[2]:
                if st.button("Delete", key=f"{panel_key}_delete_{case_id}"):
                    delete_surgical_case(case_id)
                    st.success("Case deleted.")
                    st.rerun()
    else:
        st.markdown('<div class="empty-state">No surgical cases match the current filters.</div>' if surgical_cases else '<div class="empty-state">No surgical cases logged yet.</div>', unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)


def overview_lens_options(app_settings):
    return [
        "Auto",
        "Clinic day",
        "Procedure Friday",
        "Personal focus",
        "Schedule pressure",
    ]


def resolve_overview_lens(active_tasks, personal_tasks, clinic_tasks, app_settings, lens_choice):
    today = mountain_today()
    friday_profile = clinic_day_profiles(app_settings)["procedure_friday"] if today.weekday() == 4 and today.isocalendar().week % 2 == 0 else clinic_day_profiles(app_settings)["general_clinic"]
    if lens_choice == "Clinic day":
        return clinic_day_profiles(app_settings)["surgeon_clinic"]
    if lens_choice == "Procedure Friday":
        return friday_profile
    if lens_choice == "Personal focus":
        return {
            "label": "Personal focus",
            "focus": "Protect one deep-work block for personal admin, planning, and catch-up.",
            "priority_set": "personal",
        }
    if lens_choice == "Schedule pressure":
        return {
            "label": "Schedule pressure",
            "focus": "Clear the most urgent unscheduled work and protect one buffer block.",
            "priority_set": "schedule",
        }

    if today.weekday() == 4:
        return friday_profile

    clinic_pressure = len(clinic_tasks) + len([task for task in active_tasks if task.get("category") == "Clinic" and task.get("priority") == "high" and not (task.get("scheduled_date") and task.get("scheduled_time"))])
    personal_pressure = len(personal_tasks) + len([task for task in active_tasks if task.get("category") == "Personal" and task.get("priority") == "high" and not (task.get("scheduled_date") and task.get("scheduled_time"))])

    if clinic_pressure >= personal_pressure and clinic_pressure > 0:
        return clinic_day_profiles(app_settings)["surgeon_clinic"]
    if personal_pressure > 0:
        return {
            "label": "Personal focus",
            "focus": "The board is lighter outside clinic, so protect a clean personal work block.",
            "priority_set": "personal",
        }
    return {
        "label": "Balanced day",
        "focus": "Keep one eye on clinic flow, one on tasks, and one on the schedule runway.",
        "priority_set": "balanced",
    }


def fetch_health_news(news_api_key, max_articles=5):
    """Fetch medical and clinical news from NewsAPI, with nutrition/wellness focus."""
    try:
        import requests
    except ImportError:
        return []
    
    if not news_api_key:
        return []
    
    try:
        now_utc = datetime.utcnow()
        window_start = now_utc - timedelta(hours=36)
        from_iso = window_start.strftime("%Y-%m-%dT%H:%M:%SZ")
        to_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        request_headers = {
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
        page_size = max(max_articles * 2, 20)

        # Fetch primary medical & surgical news
        medical_url = "https://newsapi.org/v2/everything"
        medical_params = {
            "q": "medical surgery orthopedic healthcare treatment procedure clinical",
            "sortBy": "publishedAt",
            "searchIn": "title,description",
            "language": "en",
            "from": from_iso,
            "to": to_iso,
            "pageSize": page_size,
            "apiKey": news_api_key,
        }
        medical_response = requests.get(medical_url, params=medical_params, headers=request_headers, timeout=8)
        medical_articles = medical_response.json().get("articles", [])[: max(1, int(max_articles * 0.65))]
        
        # Fetch nutrition & health science news
        nutrition_url = "https://newsapi.org/v2/everything"
        nutrition_params = {
            "q": "nutrition health science wellness diet research medical",
            "sortBy": "publishedAt",
            "searchIn": "title,description",
            "language": "en",
            "from": from_iso,
            "to": to_iso,
            "pageSize": page_size,
            "apiKey": news_api_key,
        }
        nutrition_response = requests.get(nutrition_url, params=nutrition_params, headers=request_headers, timeout=8)
        nutrition_articles = nutrition_response.json().get("articles", [])[: max(1, int(max_articles * 0.35))]

        # Fetch top health headlines as a freshness fallback.
        headlines_url = "https://newsapi.org/v2/top-headlines"
        headlines_params = {
            "category": "health",
            "language": "en",
            "pageSize": page_size,
            "apiKey": news_api_key,
        }
        headlines_response = requests.get(headlines_url, params=headlines_params, headers=request_headers, timeout=8)
        headline_articles = headlines_response.json().get("articles", [])[: max(1, int(max_articles * 0.35))]
        
        # Combine and deduplicate by title
        all_articles = medical_articles + nutrition_articles + headline_articles
        seen_titles = set()
        unique_articles = []
        for article in all_articles:
            title = article.get("title", "").lower()
            if title not in seen_titles:
                seen_titles.add(title)
                unique_articles.append(article)

        # Keep the most recent entries first in case APIs return mixed ordering.
        def published_key(item):
            raw = (item.get("publishedAt") or "").replace("Z", "+00:00")
            try:
                return datetime.fromisoformat(raw)
            except ValueError:
                return datetime.min

        unique_articles.sort(key=published_key, reverse=True)
        
        return unique_articles[:max_articles]
    except Exception as e:
        st.warning(f"Could not fetch news: {str(e)}")
        return []


def summarize_news_with_ai(articles, ai_model_name_fn, ai_enabled_fn):
    """Use OpenAI to summarize news articles and generate motivational takeaways."""
    if not ai_enabled_fn() or not articles:
        return None, None
    
    try:
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        
        articles_text = "\n\n".join([
            f"Title: {article.get('title', '')}\n"
            f"Source: {article.get('source', {}).get('name', 'Unknown')}\n"
            f"Description: {article.get('description', '')}"
            for article in articles[:5]
        ])
        
        response = client.chat.completions.create(
            model=ai_model_name_fn(),
            messages=[
                {
                    "role": "system",
                    "content": "You are a health and wellness expert who creates brief, inspiring morning briefings. "
                               "Summarize the key news in 2-3 sentences, then provide 3 motivational takeaways or actionable insights."
                }
                ,
                {
                    "role": "user",
                    "content": f"Please create a brief morning news digest and motivational briefing from these articles:\n\n{articles_text}"
                }
            ],
            temperature=0.7,
            max_tokens=400,
        )
        
        full_response = response.choices[0].message.content
        
        # Split response into summary and motivational takeaways
        if "motivational" in full_response.lower() or "takeaway" in full_response.lower():
            parts = re.split(r'(?:motivational takeaway|actionable insight|takeaway)[s]?[:\n]', full_response, flags=re.IGNORECASE)
            summary = parts[0].strip() if len(parts) > 0 else full_response
            takeaways = parts[1].strip() if len(parts) > 1 else ""
        else:
            lines = full_response.split('\n')
            summary = '\n'.join(lines[:3]) if len(lines) > 3 else full_response
            takeaways = '\n'.join(lines[3:]) if len(lines) > 3 else ""
        
        return summary, takeaways
    except Exception as e:
        st.error(f"AI summarization error: {str(e)}")
        return None, None


def render_morning_digest_panel(articles, summary, takeaways, panel_key="morning_digest"):
    """Render the morning news digest panel."""
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>📰 Morning News Digest</h3><span>Health, fitness, and medical news for today</span></div>', unsafe_allow_html=True)
    
    if not articles:
        st.info("No news articles available today. Check your NewsAPI key or try again later.")
        st.markdown('</div>', unsafe_allow_html=True)
        return
    
    if summary:
        st.markdown("### 📌 Today's Headlines")
        st.markdown(summary)
    
    if takeaways:
        st.markdown("### 💡 Motivational Takeaways")
        st.markdown(takeaways)
    
    st.markdown("### 📑 Featured Articles")
    for i, article in enumerate(articles[:5], 1):
        with st.expander(f"{i}. {article.get('title', 'Untitled')}", expanded=False):
            st.markdown(f"**Source:** {article.get('source', {}).get('name', 'Unknown')}")
            st.markdown(f"**Published:** {article.get('publishedAt', 'Unknown date')[:10]}")
            if article.get('description'):
                st.markdown(f"**Description:** {article.get('description', '')}")
            if article.get('url'):
                st.markdown(f"[Read full article →]({article.get('url')})")
    
    st.markdown('</div>', unsafe_allow_html=True)


def render_full_news_page(articles, summary, takeaways, panel_key="news_page"):
    """Render a full page dedicated to news."""
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>📰 Health & Medical News</h3><span>Curated news on health, fitness, and surgical topics</span></div>', unsafe_allow_html=True)

    refresh_cols = st.columns([1, 1.8])
    with refresh_cols[0]:
        if st.button("Refresh news now", key=f"{panel_key}_refresh_news", type="secondary"):
            st.session_state["news_force_refresh"] = True
            st.rerun()
    with refresh_cols[1]:
        last_refreshed = st.session_state.get("news_last_refreshed_at")
        if last_refreshed:
            st.caption(f"Last refreshed: {last_refreshed}")
        else:
            st.caption("No refresh timestamp yet.")
    
    if not articles:
        st.info("No news articles available. Check your NewsAPI key configuration.")
        st.markdown('</div>', unsafe_allow_html=True)
        return
    
    tab1, tab2, tab3 = st.tabs(["Digest", "Full Articles", "Article Details"])
    
    with tab1:
        st.markdown("## 📌 Today's Digest")
        if summary:
            st.markdown(summary)
        else:
            st.info("AI summary unavailable.")
        
        if takeaways:
            st.markdown("## 💡 Key Takeaways & Motivation")
            st.markdown(takeaways)
    
    with tab2:
        st.markdown("## 📑 All Featured Articles")
        for i, article in enumerate(articles, 1):
            st.markdown(f"### {i}. {article.get('title', 'Untitled')}")
            st.markdown(f"**Source:** {article.get('source', {}).get('name', 'Unknown')} | **Date:** {article.get('publishedAt', 'Unknown')[:10]}")
            if article.get('description'):
                st.markdown(article.get('description', ''))
            if article.get('url'):
                st.markdown(f"[Read full article →]({article.get('url')})", unsafe_allow_html=True)
            st.divider()
    
    with tab3:
        st.markdown("## 🔍 Article Details & Sources")
        article_choice = st.selectbox(
            "Select an article",
            [f"{i}. {article.get('title', 'Untitled')[:60]}..." for i, article in enumerate(articles, 1)],
            key=f"{panel_key}_article_select"
        )
        if article_choice:
            idx = int(article_choice.split(".")[0]) - 1
            if 0 <= idx < len(articles):
                article = articles[idx]
                st.markdown(f"## {article.get('title', 'Untitled')}")
                
                col1, col2 = st.columns(2)
                with col1:
                    st.metric("Source", article.get('source', {}).get('name', 'Unknown'))
                    st.metric("Published", article.get('publishedAt', 'Unknown')[:10])
                with col2:
                    if article.get('author'):
                        st.metric("Author", article.get('author', 'Unknown'))
                    if article.get('url'):
                        st.markdown(f"[Open in browser →]({article.get('url')})")
                
                st.markdown("### Content")
                if article.get('description'):
                    st.markdown(article.get('description', ''))
                if article.get('content'):
                    st.markdown(article.get('content', ''))
                
                if article.get('urlToImage'):
                    st.image(article.get('urlToImage'), use_column_width=True)
    
    st.markdown('</div>', unsafe_allow_html=True)


def render_overview_control_tower(tasks, active_tasks, completed_today_all, personal_tasks, clinic_tasks, scheduled_tasks, app_settings, overview_settings, panel_key="overview"):
    today = mountain_today()
    lens_key = f"{panel_key}_lens"
    if lens_key not in st.session_state:
        st.session_state[lens_key] = "Auto"

    lens_choice = st.selectbox("Overview lens", overview_lens_options(app_settings), key=lens_key)
    lens = resolve_overview_lens(active_tasks, personal_tasks, clinic_tasks, app_settings, lens_choice)
    day_context = resolve_overview_day_context(overview_settings, active_tasks, personal_tasks, clinic_tasks)
    if lens_choice == "Clinic day":
        clinic_mode_key = "surgeon_clinic"
    elif lens_choice == "Procedure Friday":
        clinic_mode_key = "procedure_friday"
    elif lens_choice == "Auto" and today.weekday() == 4 and today.isocalendar().week % 2 == 0:
        clinic_mode_key = "procedure_friday"
    else:
        clinic_mode_key = "general_clinic"

    due_today_tasks = [task for task in active_tasks if task.get("due_date") == mountain_today()]
    overdue_tasks_today = [task for task in active_tasks if task.get("due_date") and task["due_date"] < mountain_today()]
    unscheduled_high = [task for task in active_tasks if task.get("priority") == "high" and not (task.get("scheduled_date") and task.get("scheduled_time"))]
    clinic_backlog = [task for task in active_tasks if task.get("category") == "Clinic"]
    personal_backlog = [task for task in active_tasks if task.get("category") == "Personal"]

    overview_focus = sorted(active_tasks, key=lambda task: (0 if task.get("due_date") == mountain_today() else 1 if task.get("due_date") else 2, priority_rank(task["priority"]), task.get("scheduled_time") or time(23, 59)))[:4]
    next_scheduled = scheduled_tasks[:4]
    clinic_summary = clinic_day_summary(clinic_tasks, active_tasks, app_settings, clinic_mode_key)
    schedule_snapshot = schedule_workload_snapshot(active_tasks)

    metric_cols = st.columns(4)
    metric_cols[0].metric("Active", len(active_tasks))
    metric_cols[1].metric("Due today", len(due_today_tasks))
    metric_cols[2].metric("Overdue", len(overdue_tasks_today))
    metric_cols[3].metric("Scheduled", len(scheduled_tasks))

    top_left, top_right = st.columns([1.25, 0.85], gap="large")
    with top_left:
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.markdown('<div class="panel-title"><h3>Today at a Glance</h3><span>Fast read on the day’s operating mode</span></div>', unsafe_allow_html=True)
        st.markdown(
            f"<div class='empty-state' style='text-align:left;'><strong>{overview_settings['role_label']} at {overview_settings['site_label']}</strong><br />{day_context['mode']} · {day_context['focus_text']}<br />Clinic: {len(clinic_backlog)} active · Personal: {len(personal_backlog)} active · High-priority unscheduled: {len(unscheduled_high)}</div>",
            unsafe_allow_html=True,
        )
        st.caption(day_context["reason_text"])
        st.markdown(
            f"<div class='ai-chip-grid'><span class='ai-chip'>Target: {day_context['target_value']} {day_context['target_label']}</span><span class='ai-chip'>Shift: {overview_settings['shift_minutes']} min</span><span class='ai-chip'>Focus window: {overview_settings['focus_window_minutes']} min</span></div>",
            unsafe_allow_html=True,
        )
        if overview_focus:
            st.markdown('<div class="panel-title" style="margin-top:1rem;"><h3>Next actions</h3><span>What should move first</span></div>', unsafe_allow_html=True)
            for task in overview_focus:
                st.markdown(
                    f"- <strong>{task['title']}</strong> · {task['category']} · {task['priority'].title()} · {format_due(task)}",
                    unsafe_allow_html=True,
                )
        else:
            st.markdown('<div class="empty-state">No active tasks need attention right now.</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    with top_right:
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.markdown('<div class="panel-title"><h3>Outpatient Load</h3><span>Editable patient and procedure planning</span></div>', unsafe_allow_html=True)
        st.metric("Day mode", day_context["mode"])
        st.caption(f"{overview_settings['site_label']} · {overview_settings['role_label']} · buffer {overview_settings['admin_buffer_minutes']} min")
        st.markdown(
            f"<div class='ai-chip-grid'><span class='ai-chip'>Clinic active: {clinic_summary['active_clinic_count']}</span><span class='ai-chip'>Unscheduled: {clinic_summary['clinic_unscheduled_count']}</span><span class='ai-chip'>Due soon: {clinic_summary['due_soon_count']}</span><span class='ai-chip'>Active pressure: {day_context['active_pressure']}</span></div>",
            unsafe_allow_html=True,
        )
        if clinic_summary["top_clinic_tasks"]:
            st.markdown("<div class='panel-title' style='margin-top:0.75rem;'><h3>Top outpatient priorities</h3><span>First things first</span></div>", unsafe_allow_html=True)
            for task in clinic_summary["top_clinic_tasks"][:3]:
                st.markdown(
                    f"- <strong>{task['title']}</strong> · {task['priority'].title()} · {format_due(task)}",
                    unsafe_allow_html=True,
                )
        st.markdown('</div>', unsafe_allow_html=True)

    lower_left, lower_right = st.columns(2, gap="large")
    with lower_left:
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.markdown('<div class="panel-title"><h3>Schedule Runway</h3><span>What can still be placed cleanly</span></div>', unsafe_allow_html=True)
        st.caption(f"{len(schedule_snapshot['unscheduled'])} unscheduled tasks, {len(schedule_snapshot['unscheduled_high'])} high-priority ones.")
        st.markdown(
            f"<div class='empty-state' style='text-align:left;'><strong>Default buffer:</strong> {overview_settings['admin_buffer_minutes']} min<br /><strong>Focus window:</strong> {overview_settings['focus_window_minutes']} min<br /><strong>Recommended mode:</strong> {lens['label']}</div>",
            unsafe_allow_html=True,
        )
        for task in next_scheduled:
            scheduled_time = task.get("scheduled_time").strftime("%I:%M %p").lstrip("0") if task.get("scheduled_time") else "Any time"
            st.markdown(
                f"- <strong>{task['title']}</strong> · {task['scheduled_date']} at {scheduled_time} · {task.get('scheduled_minutes') or '-'} min",
                unsafe_allow_html=True,
            )
        if not next_scheduled:
            st.markdown('<div class="empty-state">No scheduled blocks yet. Use the Schedule page to place work into the week.</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    with lower_right:
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.markdown('<div class="panel-title"><h3>Quick Capture</h3><span>Add a task without leaving the overview</span></div>', unsafe_allow_html=True)
        with st.form(f"{panel_key}_quick_capture"):
            quick_title = st.text_input("Task title")
            quick_category = st.selectbox("Category", ["Personal", "Clinic"], index=0 if lens_choice == "Personal focus" else 1 if lens_choice in ("Clinic day", "Procedure Friday") else 0)
            quick_priority = st.selectbox("Priority", ["high", "medium", "low"], index=1)
            quick_due = st.date_input("Due date", value=mountain_today())
            quick_submit = st.form_submit_button("Add quick task")
        if quick_submit:
            if not quick_title.strip():
                st.warning("Add a task title first.")
            else:
                add_task(quick_title, "", quick_category, quick_priority, quick_due)
                st.success("Quick task added.")
                st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)



def render_add_task_panel(form_key, defaults, default_category=None):
    templates = clinic_visit_templates() if default_category == "Clinic" else None
    template_key = f"{form_key}_template"
    selected_template = "blank"
    if templates:
        template_labels = [(key, template["label"]) for key, template in templates.items()]
        if template_key not in st.session_state:
            st.session_state[template_key] = "blank"
        selected_template = st.selectbox(
            "Clinic visit template",
            [key for key, _ in template_labels],
            key=template_key,
            format_func=lambda key: dict(template_labels)[key],
            on_change=apply_clinic_visit_template_from_state,
            args=(form_key, template_key),
        )
        if selected_template == "blank":
            st.caption("Pick a common visit type to prefill the capture form with a standard clinic pattern.")
        else:
            template = templates[selected_template]
            st.caption(f"Prefill: {template['title']} · {template['scheduled_minutes']} min · {template['priority']} priority")

    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Add Task</h3><span>Quick capture</span></div>', unsafe_allow_html=True)
    with st.form(form_key):
        title = st.text_input("Task title", key=f"{form_key}_title")
        description = st.text_area("Description", height=100, key=f"{form_key}_description")
        category_options = ["Personal", "Clinic"]
        resolved_default_category = default_category or defaults.get("default_category", "Personal")
        category_index = category_options.index(resolved_default_category) if resolved_default_category in category_options else 0
        category = st.selectbox("Category", category_options, index=category_index, key=f"{form_key}_category")
        priority_options = ["high", "medium", "low"]
        default_priority = defaults.get("default_priority", "medium")
        priority_index = priority_options.index(default_priority) if default_priority in priority_options else 1
        priority = st.selectbox("Priority", priority_options, index=priority_index, key=f"{form_key}_priority")
        due_date = st.date_input("Due date", value=mountain_today(), key=f"{form_key}_due_date")
        schedule_enabled = st.checkbox("Schedule this task", key=f"{form_key}_schedule_enabled")
        schedule_cols = st.columns(3)
        with schedule_cols[0]:
            scheduled_date = st.date_input("Scheduled date", value=mountain_today(), disabled=not schedule_enabled, key=f"{form_key}_scheduled_date")
        with schedule_cols[1]:
            scheduled_time = st.time_input(
                "Scheduled time",
                value=parse_time_value(defaults.get("default_schedule_time")) or time(9, 0),
                disabled=not schedule_enabled,
                key=f"{form_key}_scheduled_time",
            )
        with schedule_cols[2]:
            duration_options = [15, 30, 45, 60, 90, 120]
            default_duration = int(defaults.get("default_duration", 60))
            duration_index = duration_options.index(default_duration) if default_duration in duration_options else 3
            scheduled_minutes = st.selectbox(
                "Duration (minutes)",
                duration_options,
                index=duration_index,
                disabled=not schedule_enabled,
                key=f"{form_key}_scheduled_minutes",
            )
        multi_day_enabled = st.checkbox("Multi-day span (e.g., vacation)", value=False, disabled=not schedule_enabled, key=f"{form_key}_multi_day_enabled")
        if multi_day_enabled and schedule_enabled:
            scheduled_end_date = st.date_input("End date", value=scheduled_date, disabled=False, key=f"{form_key}_scheduled_end_date")
        else:
            scheduled_end_date = None
        recurrence_cols = st.columns(2)
        with recurrence_cols[0]:
            recurrence_rule = st.selectbox(
                "Recurrence",
                ["none", "daily", "weekly"],
                format_func=lambda value: "None" if value == "none" else value.title(),
                key=f"{form_key}_recurrence_rule",
            )
        with recurrence_cols[1]:
            recurrence_interval = st.number_input(
                "Every",
                min_value=1,
                max_value=30,
                value=1,
                step=1,
                disabled=recurrence_rule == "none",
                key=f"{form_key}_recurrence_interval",
            )
        submitted = st.form_submit_button("Add task")

    if submitted:
        if not title.strip():
            st.warning("Add a task title first.")
        else:
            add_task(
                title,
                description,
                category,
                priority,
                due_date,
                scheduled_date=scheduled_date if schedule_enabled else None,
                scheduled_end_date=scheduled_end_date if schedule_enabled and multi_day_enabled else None,
                scheduled_time=scheduled_time if schedule_enabled else None,
                scheduled_minutes=scheduled_minutes if schedule_enabled else None,
                recurrence_rule=None if recurrence_rule == "none" else recurrence_rule,
                recurrence_interval=int(recurrence_interval),
            )
            st.success("Task added.")
            st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)


def render_ai_panel(tasks, active_tasks, panel_key="main"):
    summary = ai_workbench_summary(tasks, active_tasks)
    prompt_key = f"{panel_key}_ai_prompt"
    default_prompt = "Build a focused plan for today and call out the first two actions I should take."
    if prompt_key not in st.session_state:
        st.session_state[prompt_key] = default_prompt

    st.markdown('<div class="ai-shell">', unsafe_allow_html=True)
    st.markdown(
        "<div class='panel ai-hero'>"
        "<div class='panel-title'><h3>AI Workbench</h3><span>Planning, scheduling, and review in one command center</span></div>"
        f"<p>AI sees {summary['active_count']} active tasks, {summary['overdue_count']} overdue items, and {summary['unscheduled_high_count']} high-priority tasks still waiting for a slot.</p>"
        "</div>",
        unsafe_allow_html=True,
    )

    stat_cols = st.columns(4)
    stat_payload = [
        ("Active", summary["active_count"], summary["focus_label"]),
        ("Due today", summary["due_today_count"], "Use this for immediate triage."),
        ("Overdue", summary["overdue_count"], "These should dominate the plan."),
        ("Completed today", summary["completed_today_count"], "Useful for closing the loop."),
    ]
    for col, (label, value, note) in zip(stat_cols, stat_payload):
        with col:
            st.markdown(
                f"<div class='ai-stat-card'><div class='ai-stat-label'>{label}</div><div class='ai-stat-value'>{value}</div><div class='ai-stat-note'>{note}</div></div>",
                unsafe_allow_html=True,
            )

    st.markdown('<div class="panel ai-command">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Prompt Studio</h3><span>Shape the output before you generate it</span></div>', unsafe_allow_html=True)
    command_col, insight_col = st.columns([1.35, 1], gap="large")
    presets = [
        ("Today focus", "Build a focused plan for today, sorted by urgency and energy cost."),
        ("Rescue mode", "I need help recovering from a messy day. Prioritize overdue, blocked, and unscheduled high-priority work."),
        ("Clinic shift", "Organize this like a clinic operations block with practical sequencing and low-friction tasks first."),
        ("Schedule pass", "Reschedule the active tasks into realistic blocks and flag anything that should be deferred."),
    ]
    with command_col:
        preset_cols = st.columns(2)
        for idx, (label, prompt) in enumerate(presets):
            if preset_cols[idx % 2].button(label, key=f"{panel_key}_preset_{idx}"):
                st.session_state[prompt_key] = prompt
                st.rerun()
        ai_prompt = st.text_area("Ask AI", height=120, key=prompt_key)
        action_cols = st.columns(2)
        with action_cols[0]:
            generate_plan_clicked = st.button("Generate AI Plan", key=f"{panel_key}_gen", type="primary")
        with action_cols[1]:
            auto_schedule_clicked = st.button("Auto-Schedule Tasks", key=f"{panel_key}_auto")

    with insight_col:
        st.markdown('<div class="panel-title"><h3>What AI sees</h3><span>Operational signals used for planning</span></div>', unsafe_allow_html=True)
        st.markdown(
            "<div class='ai-chip-grid'>"
            f"<span class='ai-chip'>Due soon: {summary['due_soon_count']}</span>"
            f"<span class='ai-chip'>Blocked: {summary['blocked_count']}</span>"
            f"<span class='ai-chip'>High priority unscheduled: {summary['unscheduled_high_count']}</span>"
            "</div>",
            unsafe_allow_html=True,
        )
        insight_lines = [
            f"Best next move: {summary['focus_label']}",
            f"Due soon (3 days): {summary['due_soon_count']}",
            f"Blocked tasks: {summary['blocked_count']}",
            f"High-priority unscheduled: {summary['unscheduled_high_count']}",
        ]
        st.markdown("<ul class='ai-list'>" + "".join(f"<li>{line}</li>" for line in insight_lines) + "</ul>", unsafe_allow_html=True)
        if summary["recommended_task"]:
            recommended = summary["recommended_task"]
            st.markdown(
                "<div class='empty-state' style='text-align:left; margin-top:0.85rem;'>"
                f"<strong>Recommended task:</strong> {recommended.get('title')}<br />"
                f"{recommended.get('priority', '').title()} priority, due {format_due(recommended)}, status {status_label(recommended.get('status', 'todo'))}."
                "</div>",
                unsafe_allow_html=True,
            )

    planner_tab, scheduler_tab, review_tab = st.tabs(["Planner", "Scheduler", "Review"])

    with planner_tab:
        st.markdown('<div class="panel ai-response-card">', unsafe_allow_html=True)
        st.markdown('<div class="panel-title"><h3>Plan Builder</h3><span>Ask for a focused day plan or a task rescue plan</span></div>', unsafe_allow_html=True)
        if generate_plan_clicked:
            result, error, suggestions = generate_ai_plan(tasks, ai_prompt)
            st.session_state.ai_response = result
            st.session_state.ai_error = error
            st.session_state.ai_suggestions = suggestions
        if st.session_state.ai_error:
            st.warning(st.session_state.ai_error)
        if st.session_state.ai_response:
            st.markdown(st.session_state.ai_response)
        if st.session_state.ai_suggestions:
            st.caption(f"Suggested tasks detected: {len(st.session_state.ai_suggestions)}")
            if st.button("Add Suggested Tasks", type="primary", key=f"{panel_key}_apply_suggested"):
                apply_ai_suggestions(st.session_state.ai_suggestions)
                added_count = len(st.session_state.ai_suggestions)
                st.session_state.ai_suggestions = []
                st.success(f"Added {added_count} suggested task(s).")
                st.rerun()
        if not st.session_state.ai_response and not st.session_state.ai_error:
            st.markdown('<div class="empty-state">Generate a plan to turn the task board into a sequence of actions.</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    with scheduler_tab:
        st.markdown('<div class="panel ai-response-card">', unsafe_allow_html=True)
        st.markdown('<div class="panel-title"><h3>Scheduler</h3><span>Auto-place work into realistic blocks</span></div>', unsafe_allow_html=True)
        if auto_schedule_clicked:
            schedule_text, schedule_error, schedule_updates = generate_ai_schedule(active_tasks, ai_prompt)
            st.session_state.ai_schedule_error = schedule_error
            st.session_state.ai_schedule_updates = schedule_updates
            if schedule_text:
                st.session_state.ai_response = schedule_text
        if st.session_state.ai_schedule_error:
            st.warning(st.session_state.ai_schedule_error)
        if st.session_state.ai_response:
            st.markdown(st.session_state.ai_response)
        if st.session_state.ai_schedule_updates:
            st.caption(f"Schedule updates detected: {len(st.session_state.ai_schedule_updates)}")
            if st.button("Apply Auto-Schedule", type="secondary", key=f"{panel_key}_apply_schedule"):
                apply_ai_schedule_updates(st.session_state.ai_schedule_updates)
                applied_count = len(st.session_state.ai_schedule_updates)
                st.session_state.ai_schedule_updates = []
                st.success(f"Applied {applied_count} schedule update(s).")
                st.rerun()
        if st.session_state.ai_schedule_updates:
            st.markdown("<div class='empty-state' style='text-align:left;'>AI generated schedule updates are ready to apply.</div>", unsafe_allow_html=True)
        elif not st.session_state.ai_schedule_error:
            st.markdown('<div class="empty-state">Run auto-schedule to slot tasks into the week.</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    with review_tab:
        st.markdown('<div class="panel ai-response-card">', unsafe_allow_html=True)
        st.markdown('<div class="panel-title"><h3>Review Lens</h3><span>Use AI as a fast retrospective and tomorrow planner</span></div>', unsafe_allow_html=True)
        review_input = st.text_area(
            "Review notes",
            value="Highlight what slipped today, what got done, and what should happen first tomorrow.",
            height=100,
            key=f"{panel_key}_review_prompt",
        )
        if st.button("Generate Review Summary", key=f"{panel_key}_gen_review", type="primary"):
            completed_today_tasks = [task for task in tasks if task.get("status") == "completed" and task.get("completed_date") == mountain_today()]
            review_text, tomorrow_text, review_error = generate_daily_review(active_tasks, completed_today_tasks, review_input)
            st.session_state.daily_review_text = review_text
            st.session_state.tomorrow_plan_text = tomorrow_text
            st.session_state.daily_review_error = review_error
        if st.session_state.daily_review_error:
            st.warning(st.session_state.daily_review_error)
        if st.session_state.daily_review_text:
            st.markdown(st.session_state.daily_review_text)
        if st.session_state.tomorrow_plan_text:
            st.markdown(st.session_state.tomorrow_plan_text)
        if not st.session_state.daily_review_text and not st.session_state.daily_review_error:
            st.markdown('<div class="empty-state">Use the review tab to close out the day and draft tomorrow.</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)


def render_timeline_panel(scheduled_tasks, timeline_days):
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Schedule Timeline</h3><span>Calendar-style view</span></div>', unsafe_allow_html=True)
    timeline_start = mountain_today()
    timeline_end = timeline_start + timedelta(days=int(timeline_days) - 1)
    timeline_tasks = [
        task
        for task in scheduled_tasks
        if task.get("scheduled_date") and timeline_start <= task["scheduled_date"] <= timeline_end
    ]

    if timeline_tasks:
        for offset in range(int(timeline_days)):
            day = timeline_start + timedelta(days=offset)
            day_items = [item for item in timeline_tasks if item.get("scheduled_date") == day]
            if not day_items:
                continue
            st.markdown(f"**{day.strftime('%A, %b %d')}**")
            day_items = sorted(day_items, key=lambda item: (item.get("scheduled_time") or time(23, 59), priority_rank(item.get("priority"))))
            for item in day_items:
                at = item.get("scheduled_time").strftime("%I:%M %p").lstrip("0") if item.get("scheduled_time") else "Any time"
                mins = item.get("scheduled_minutes") or "-"
                st.markdown(f"- {at} · {mins} min · {item.get('title')} ({status_label(item.get('status', 'todo'))})")
    else:
        st.markdown('<div class="empty-state">No scheduled tasks in this timeline window.</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)


load_tasks = partial(data_access.load_tasks, db_enabled, get_connection, st_module=st)
load_surgical_cases = partial(data_access.load_surgical_cases, db_enabled, get_connection, st_module=st)
add_surgical_case = partial(data_access.add_surgical_case, db_enabled_fn=db_enabled, get_connection_fn=get_connection, st_module=st)
update_surgical_case = partial(data_access.update_surgical_case, db_enabled_fn=db_enabled, get_connection_fn=get_connection, st_module=st)
delete_surgical_case = partial(data_access.delete_surgical_case, db_enabled_fn=db_enabled, get_connection_fn=get_connection, st_module=st)
load_protocol_documents = partial(data_access.load_protocol_documents, db_enabled, get_connection, st_module=st)
add_protocol_document = partial(data_access.add_protocol_document, db_enabled_fn=db_enabled, get_connection_fn=get_connection, st_module=st)
update_protocol_document = partial(data_access.update_protocol_document, db_enabled_fn=db_enabled, get_connection_fn=get_connection, st_module=st)
delete_protocol_document = partial(data_access.delete_protocol_document, db_enabled_fn=db_enabled, get_connection_fn=get_connection, st_module=st)
load_case_protocol_links = partial(data_access.load_case_protocol_links, db_enabled, get_connection, st_module=st)
set_protocol_case_links = partial(data_access.set_protocol_case_links, db_enabled_fn=db_enabled, get_connection_fn=get_connection, st_module=st)
load_lead_clinical_issues = partial(data_access.load_lead_clinical_issues, db_enabled, get_connection, st_module=st)
add_lead_clinical_issue = partial(data_access.add_lead_clinical_issue, db_enabled_fn=db_enabled, get_connection_fn=get_connection, st_module=st)
update_lead_clinical_issue = partial(data_access.update_lead_clinical_issue, db_enabled_fn=db_enabled, get_connection_fn=get_connection, st_module=st)
load_lead_sop_entries = partial(data_access.load_lead_sop_entries, db_enabled, get_connection, st_module=st)
add_lead_sop_entry = partial(data_access.add_lead_sop_entry, db_enabled_fn=db_enabled, get_connection_fn=get_connection, st_module=st)
load_lead_relationship_touchpoints = partial(data_access.load_lead_relationship_touchpoints, db_enabled, get_connection, st_module=st)
add_lead_relationship_touchpoint = partial(data_access.add_lead_relationship_touchpoint, db_enabled_fn=db_enabled, get_connection_fn=get_connection, st_module=st)
update_lead_relationship_touchpoint = partial(data_access.update_lead_relationship_touchpoint, db_enabled_fn=db_enabled, get_connection_fn=get_connection, st_module=st)
load_lead_ma_assignments = partial(data_access.load_lead_ma_assignments, db_enabled, get_connection, st_module=st)
add_lead_ma_assignment = partial(data_access.add_lead_ma_assignment, db_enabled_fn=db_enabled, get_connection_fn=get_connection, st_module=st)
update_lead_ma_assignment = partial(data_access.update_lead_ma_assignment, db_enabled_fn=db_enabled, get_connection_fn=get_connection, st_module=st)
load_lead_huddle_logs = partial(data_access.load_lead_huddle_logs, db_enabled, get_connection, st_module=st)
add_lead_huddle_log = partial(data_access.add_lead_huddle_log, db_enabled_fn=db_enabled, get_connection_fn=get_connection, st_module=st)
load_lead_skill_signoffs = partial(data_access.load_lead_skill_signoffs, db_enabled, get_connection, st_module=st)
add_lead_skill_signoff = partial(data_access.add_lead_skill_signoff, db_enabled_fn=db_enabled, get_connection_fn=get_connection, st_module=st)
update_lead_skill_signoff = partial(data_access.update_lead_skill_signoff, db_enabled_fn=db_enabled, get_connection_fn=get_connection, st_module=st)
load_lead_education_requests = partial(data_access.load_lead_education_requests, db_enabled, get_connection, st_module=st)
add_lead_education_request = partial(data_access.add_lead_education_request, db_enabled_fn=db_enabled, get_connection_fn=get_connection, st_module=st)
update_lead_education_request = partial(data_access.update_lead_education_request, db_enabled_fn=db_enabled, get_connection_fn=get_connection, st_module=st)
load_autoclave_maintenance_items = partial(data_access.load_autoclave_maintenance_items, db_enabled, get_connection, st_module=st)
add_autoclave_maintenance_item = partial(data_access.add_autoclave_maintenance_item, db_enabled_fn=db_enabled, get_connection_fn=get_connection, st_module=st)
update_autoclave_maintenance_item = partial(data_access.update_autoclave_maintenance_item, db_enabled_fn=db_enabled, get_connection_fn=get_connection, st_module=st)
load_lead_documents = partial(data_access.load_lead_documents, db_enabled, get_connection, st_module=st)
add_lead_document = partial(data_access.add_lead_document, db_enabled_fn=db_enabled, get_connection_fn=get_connection, st_module=st)
delete_lead_document = partial(data_access.delete_lead_document, db_enabled_fn=db_enabled, get_connection_fn=get_connection, st_module=st)

parse_ai_suggestions = ai_workflows.parse_ai_suggestions
parse_ai_schedule_updates = ai_workflows.parse_ai_schedule_updates
task_snapshot_for_ai = ai_workflows.task_snapshot_for_ai
generate_ai_plan = partial(ai_workflows.generate_ai_plan, ai_enabled_fn=ai_enabled, ai_api_key_fn=ai_api_key, ai_model_name_fn=ai_model_name, openai_cls=OpenAI)
generate_ai_schedule = partial(ai_workflows.generate_ai_schedule, ai_enabled_fn=ai_enabled, ai_api_key_fn=ai_api_key, ai_model_name_fn=ai_model_name, openai_cls=OpenAI)
generate_daily_review = partial(ai_workflows.generate_daily_review, ai_enabled_fn=ai_enabled, ai_api_key_fn=ai_api_key, ai_model_name_fn=ai_model_name, openai_cls=OpenAI)
generate_ai_daily_summary = partial(ai_workflows.generate_ai_daily_summary, ai_enabled_fn=ai_enabled, ai_api_key_fn=ai_api_key, ai_model_name_fn=ai_model_name, openai_cls=OpenAI)
generate_weekly_nightly_insight = partial(ai_workflows.generate_weekly_nightly_insight, ai_enabled_fn=ai_enabled, ai_api_key_fn=ai_api_key, ai_model_name_fn=ai_model_name, openai_cls=OpenAI)
generate_weekly_morning_ritual_insight = partial(ai_workflows.generate_weekly_morning_ritual_insight, ai_enabled_fn=ai_enabled, ai_api_key_fn=ai_api_key, ai_model_name_fn=ai_model_name, openai_cls=OpenAI)
generate_family_schedule_insight = partial(ai_workflows.generate_family_schedule_insight, ai_enabled_fn=ai_enabled, ai_api_key_fn=ai_api_key, ai_model_name_fn=ai_model_name, openai_cls=OpenAI)
generate_family_weekly_briefing = partial(ai_workflows.generate_family_weekly_briefing, ai_enabled_fn=ai_enabled, ai_api_key_fn=ai_api_key, ai_model_name_fn=ai_model_name, openai_cls=OpenAI)
generate_family_goal_coaching = partial(ai_workflows.generate_family_goal_coaching, ai_enabled_fn=ai_enabled, ai_api_key_fn=ai_api_key, ai_model_name_fn=ai_model_name, openai_cls=OpenAI)
generate_family_weekly_digest = partial(ai_workflows.generate_family_weekly_digest, ai_enabled_fn=ai_enabled, ai_api_key_fn=ai_api_key, ai_model_name_fn=ai_model_name, openai_cls=OpenAI)
generate_ai_morning_ritual_brief = partial(ai_workflows.generate_ai_morning_ritual_brief, ai_enabled_fn=ai_enabled, ai_api_key_fn=ai_api_key, ai_model_name_fn=ai_model_name, openai_cls=OpenAI)

render_task_list_panel = partial(page_renderers.render_task_list_panel, render_task_card_fn=render_task_card, st_module=st)
render_task_calendar_panel = partial(
    page_renderers.render_task_calendar_panel,
    render_task_calendar_compact_fn=render_task_calendar_compact,
    save_app_settings_fn=save_app_settings,
    st_module=st,
)

page_shared_deps = {
    "overview_lens_options": overview_lens_options,
    "resolve_overview_lens": resolve_overview_lens,
    "resolve_overview_day_context": resolve_overview_day_context,
    "personal_focus_summary": personal_focus_summary,
    "priority_rank": priority_rank,
    "task_attention_signal": task_attention_signal,
    "task_attention_sort_key": task_attention_sort_key,
    "clinic_day_summary": clinic_day_summary,
    "schedule_workload_snapshot": schedule_workload_snapshot,
    "normalize_family_schedule_items": normalize_family_schedule_items,
    "weekly_family_schedule_summary": weekly_family_schedule_summary,
    "normalize_quick_reminders": normalize_quick_reminders,
    "format_due": format_due,
    "add_task": add_task,
    "update_task": update_task,
    "set_task_status": set_task_status,
    "load_surgical_cases": load_surgical_cases,
    "load_protocol_documents": load_protocol_documents,
    "load_case_protocol_links": load_case_protocol_links,
    "set_protocol_case_links": set_protocol_case_links,
    "update_surgical_case": update_surgical_case,
    "predicted_or_days": predicted_or_days,
    "render_or_calendar_compact": render_or_calendar_compact,
    "suggest_cpt_codes_for_case": ref_suggest_cpt_codes_for_case,
    "suggest_protocols_for_case": ref_suggest_protocols_for_case,
    "cpt_reference": CPT_REFERENCE,
    "status_label": status_label,
    "generate_ai_plan": generate_ai_plan,
    "generate_ai_schedule": generate_ai_schedule,
    "generate_daily_review": generate_daily_review,
    "apply_ai_suggestions": apply_ai_suggestions,
    "apply_ai_schedule_updates": apply_ai_schedule_updates,
    "ai_workbench_summary": ai_workbench_summary,
}

render_overview_control_tower = partial(page_sections.render_overview_control_tower, deps=page_shared_deps, st_module=st)
render_surgical_cases_panel = partial(page_sections.render_surgical_cases_panel, deps={**page_shared_deps, "add_surgical_case": add_surgical_case, "update_surgical_case": update_surgical_case, "delete_surgical_case": delete_surgical_case, "add_protocol_document": add_protocol_document, "update_protocol_document": update_protocol_document, "delete_protocol_document": delete_protocol_document}, st_module=st)
render_physical_therapy_protocols_panel = partial(page_sections.render_physical_therapy_protocols_panel, deps={**page_shared_deps, "add_protocol_document": add_protocol_document, "update_protocol_document": update_protocol_document, "delete_protocol_document": delete_protocol_document}, st_module=st)
render_ai_panel = partial(page_sections.render_ai_panel, deps=page_shared_deps, st_module=st)

app_bootstrap.run_app(
    {
        "initialize_database": initialize_database,
        "load_app_settings": load_app_settings,
        "save_app_settings": save_app_settings,
        "inject_styles": inject_styles,
        "render_hero": render_hero,
        "db_health_status": db_health_status,
        "configured_database_env_names": configured_database_env_names,
        "ai_enabled": ai_enabled,
        "ai_model_name": ai_model_name,
        "seed_sample_tasks": seed_sample_tasks,
        "db_enabled": db_enabled,
        "DB_CANDIDATE_SOURCE": DB_CANDIDATE_SOURCE,
        "DB_ERROR": DB_ERROR,
        "load_tasks": load_tasks,
        "load_personal_goals": load_personal_goals,
        "personal_goal_dashboard_summary": personal_goal_dashboard_summary,
        "load_surgical_cases": load_surgical_cases,
        "load_protocol_documents": load_protocol_documents,
        "priority_rank": priority_rank,
        "format_due": format_due,
        "status_label": status_label,
        "render_page_banner": render_page_banner,
        "overview_runtime_settings": overview_runtime_settings,
        "add_task": add_task,
        "render_overview_control_tower": render_overview_control_tower,
        "render_add_task_panel": render_add_task_panel,
        "render_personal_focus_panel": render_personal_focus_panel,
        "render_personal_goals_panel": render_personal_goals_panel,
        "render_personal_goal_reminders_panel": render_personal_goal_reminders_panel,
        "render_personal_goal_review_panel": render_personal_goal_review_panel,
        "render_personal_goal_history_panel": render_personal_goal_history_panel,
        "render_clinic_command_center": render_clinic_command_center,
        "render_surgical_cases_panel": render_surgical_cases_panel,
        "render_physical_therapy_protocols_panel": render_physical_therapy_protocols_panel,
        "render_task_calendar_panel": render_task_calendar_panel,
        "render_schedule_builder_panel": render_schedule_builder_panel,
        "render_family_schedule_panel": render_family_schedule_panel,
        "render_task_list_panel": render_task_list_panel,
        "render_ai_panel": render_ai_panel,
        "render_review_command_panel": render_review_command_panel,
        "render_notifications_panel": render_notifications_panel,
        "render_ma_lead_panel": render_ma_lead_panel,
        "render_settings_panel": render_settings_panel,
        "render_analytics_panel": render_analytics_panel,
        "render_daily_review_panel": render_daily_review_panel,
        "render_morning_ritual_panel": render_morning_ritual_panel,
        "render_page_footer": render_page_footer,
        "render_msk_anatomy_panel": render_msk_anatomy_panel,
        "render_personal_quick_capture": render_personal_quick_capture,
        "render_personal_one_thing": render_personal_one_thing,
        "fetch_health_news": fetch_health_news,
        "summarize_news_with_ai": summarize_news_with_ai,
        "render_morning_digest_panel": render_morning_digest_panel,
        "render_full_news_page": render_full_news_page,
    }
)

st.stop()
