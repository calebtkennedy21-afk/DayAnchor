import os
import json
import html
import re
import calendar
import textwrap
from io import BytesIO
from datetime import date, datetime, time, timedelta
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
from scheduling_core import (
    build_week_rebalance_moves,
    clinic_visit_templates,
    personal_schedule_templates,
    priority_rank,
    safe_int,
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
if "lead_huddle_logs" not in st.session_state:
    st.session_state.lead_huddle_logs = []


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
}


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
        return date.today()
    if cleaned == "tomorrow":
        return date.today() + timedelta(days=1)
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

        due_date = parse_date_value(item.get("due_date")) or date.today()
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
                        status,
                        notes,
                        education_url,
                        education_notes,
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
    status="planned",
    notes="",
    education_url="",
    education_notes="",
):
    stream_value = case_stream.strip()
    procedure_value = procedure_name.strip()
    location_value = anatomical_location.strip()
    facility_value = (or_facility or "Mercy OR").strip()
    notes_value = notes.strip()
    education_url_value = education_url.strip()
    education_notes_value = education_notes.strip()
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
                        status,
                        notes,
                        education_url,
                        education_notes,
                        created_date
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        case_date,
                        stream_value,
                        procedure_value,
                        location_value,
                        facility_value,
                        status,
                        notes_value,
                        education_url_value,
                        education_notes_value,
                        date.today(),
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
            "status": status,
            "notes": notes_value,
            "education_url": education_url_value,
            "education_notes": education_notes_value,
            "created_date": date.today(),
        }
    )


def update_surgical_case(case_id, **fields):
    allowed_fields = {
        "case_date",
        "case_stream",
        "procedure_name",
        "anatomical_location",
        "or_facility",
        "status",
        "notes",
        "education_url",
        "education_notes",
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
                        date.today(),
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
            "created_date": date.today(),
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
                        (case_id, protocol_id, date.today()),
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
                        date.today(),
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
            "created_date": date.today(),
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
                        date.today(),
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
            "created_date": date.today(),
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
                    (review_value, date.today()),
                )
        return

    next_id = max([item.get("id", 0) for item in st.session_state.anatomy_quiz_review_queue], default=0) + 1
    st.session_state.anatomy_quiz_review_queue.append(
        {
            "id": next_id,
            "review_text": review_value,
            "created_date": date.today(),
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
        day = date.today() + timedelta(days=offset)
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
        st.session_state[month_key] = date.today().replace(day=1)

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
            payload_text = json.dumps(merged)
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
            "due_date": date.today(),
            "scheduled_date": date.today(),
            "scheduled_time": time(8, 30),
            "scheduled_minutes": 30,
        },
        {
            "title": "Personal finance check-in",
            "description": "Quick budget review and upcoming bill check.",
            "category": "Personal",
            "priority": "medium",
            "due_date": date.today(),
            "scheduled_date": date.today(),
            "scheduled_time": time(19, 0),
            "scheduled_minutes": 45,
        },
        {
            "title": "Inbox zero sprint",
            "description": "Process starred messages and archive the rest.",
            "category": "Personal",
            "priority": "low",
            "due_date": date.today(),
            "scheduled_date": date.today(),
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
                        date.today(),
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
            "created_date": date.today(),
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
    anchor = reference_date or date.today()
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
        reminder_today = date.today().strftime("%A") in reminder_days
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
        enriched_goal["today_checked_in"] = any(item.get("checked_in_date") == date.today() for item in goal_checkins)
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
                        date.today(),
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
            "created_date": date.today(),
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
    checkin_date = date.today()
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
                    (goal_id, checkin_date, note_value, date.today()),
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
            "created_date": date.today(),
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

    update_task(task_id, status="completed", completed_date=date.today(), completed_at=datetime.utcnow())

    recurrence_rule = task.get("recurrence_rule")
    recurrence_interval = max(1, int(task.get("recurrence_interval") or 1))
    if recurrence_rule in ("daily", "weekly"):
        next_due = shift_date_by_rule(task.get("due_date") or date.today(), recurrence_rule, recurrence_interval)
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
            value=task.get("due_date") or date.today(),
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
                value=task.get("scheduled_date") or date.today(),
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
            edit_end_default = task.get("scheduled_end_date") or task.get("scheduled_date") or date.today()
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
    today = date.today()
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
    st_module.session_state[f"{form_key}_due_date"] = date.today()
    st_module.session_state[f"{form_key}_schedule_enabled"] = template["schedule_enabled"]
    st_module.session_state[f"{form_key}_scheduled_date"] = date.today()
    st_module.session_state[f"{form_key}_scheduled_time"] = template["scheduled_time"]
    st_module.session_state[f"{form_key}_scheduled_minutes"] = template["scheduled_minutes"]
    st_module.session_state[f"{form_key}_recurrence_rule"] = "none"
    st_module.session_state[f"{form_key}_recurrence_interval"] = 1


def apply_clinic_visit_template_from_state(form_key, template_state_key, st_module=st):
    apply_clinic_visit_template(form_key, st_module.session_state.get(template_state_key, "blank"), st_module=st_module)


def apply_personal_schedule_template(form_key, template_key, st_module=st):
    templates = personal_schedule_templates()
    template = templates.get(template_key, templates["blank"])
    start_date = date.today()
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
                date.today(),
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
        st.session_state[month_key] = date.today().replace(day=1)

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
        goal_reminder_days = st.multiselect("Reminder days", PERSONAL_GOAL_WEEKDAY_NAMES, default=[date.today().strftime("%A")])
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
        today = date.today()
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
            today = date.today()
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
                pin_date = st.date_input("Pin date", value=date.today(), key=f"{panel_key}_pin_date")
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
                            scheduled_date=date.today() + timedelta(days=1),
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
            personal_date = st.date_input("Start date", value=date.today(), key=f"{panel_key}_personal_capture_scheduled_date")
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


def render_review_command_panel(active_tasks, completed_today, app_settings, panel_key="review"):
    clinic_completed = [task for task in completed_today if task.get("category") == "Clinic"]
    personal_completed = [task for task in completed_today if task.get("category") == "Personal"]
    clinic_open = [task for task in active_tasks if task.get("category") == "Clinic"]

    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Shift Debrief</h3><span>Capture what happened and what should happen next</span></div>', unsafe_allow_html=True)
    metric_cols = st.columns(4)
    metric_cols[0].metric("Completed today", len(completed_today))
    metric_cols[1].metric("Clinic completed", len(clinic_completed))
    metric_cols[2].metric("Personal completed", len(personal_completed))
    metric_cols[3].metric("Active clinic tasks", len(clinic_open))

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

    with right_col:
        st.markdown('<div class="panel-title"><h3>Debrief Prompts</h3><span>Focus your reflection</span></div>', unsafe_allow_html=True)
        st.markdown(
            "<div class='ai-list'>"
            "<li>Did clinic flow stay on time?</li>"
            "<li>What should be pre-charted or prepped before the next clinic block?</li>"
            "<li>Which personal tasks can wait until the next non-clinic window?</li>"
            "</div>",
            unsafe_allow_html=True,
        )
        if clinic_completed:
            st.caption(f"Clinic completions today: {', '.join(task['title'] for task in clinic_completed[:4])}")
        if not st.session_state.daily_review_text and not st.session_state.daily_review_error:
            st.markdown('<div class="empty-state">Run the debrief after a clinic or non-clinic day to capture the transition to tomorrow.</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)


def render_metrics_row():
    tasks = load_tasks()
    active_tasks = [task for task in tasks if task.get("status") != "completed"]
    due_today = [task for task in active_tasks if task.get("due_date") == date.today()]
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
    render_review_command_panel(active_tasks, completed_today_all, app_settings, panel_key=panel_key)

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


def render_notifications_panel(tasks, active_tasks, panel_key="notifications"):
    render_metrics_row()
    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)

    overdue_all = sorted(
        [task for task in active_tasks if task.get("due_date") and task["due_date"] < date.today()],
        key=lambda task: task_attention_sort_key(task, date.today()),
    )
    blocked_all = [task for task in active_tasks if task.get("status") == "blocked"]
    unscheduled_high = sorted(
        [task for task in active_tasks if task.get("priority") == "high" and not (task.get("scheduled_date") and task.get("scheduled_time"))],
        key=lambda task: task_attention_sort_key(task, date.today()),
    )
    due_tomorrow = [task for task in active_tasks if task.get("due_date") == (date.today() + timedelta(days=1))]

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
            sorted([task for task in overdue_all + blocked_all + unscheduled_high if task.get("category") == "Clinic"], key=lambda task: task_attention_sort_key(task, date.today())),
            "notif_clinic_alerts",
            "No clinic-specific alerts right now.",
        )
    with alert_cols[1]:
        render_task_list_panel(
            "Personal Alerts",
            "Personal overdue, blocked, and unscheduled items",
            sorted([task for task in overdue_all + blocked_all + unscheduled_high if task.get("category") == "Personal"], key=lambda task: task_attention_sort_key(task, date.today())),
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


def render_ma_lead_panel(active_tasks, clinic_tasks_all, panel_key="ma_lead"):
    render_metrics_row()
    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)

    lead_issues = load_lead_clinical_issues() or []
    sop_entries = load_lead_sop_entries() or []
    relationship_touchpoints = load_lead_relationship_touchpoints() or []
    huddle_logs = load_lead_huddle_logs() or []

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
    resolved_today = [item for item in lead_issues if item.get("resolved_date") == date.today()]
    clinical_overdue = [
        task
        for task in clinic_tasks_all
        if task.get("status") != "completed" and task.get("due_date") and task.get("due_date") < date.today()
    ]

    headline = st.columns(5)
    headline[0].metric("Needs action now", len(open_issues))
    headline[1].metric("Waiting on PSR", len(waiting_psr))
    headline[2].metric("Waiting on manager/supervisor", len(waiting_leadership))
    headline[3].metric("Escalated", len(escalated_issues))
    headline[4].metric("Resolved today", len(resolved_today))

    st.markdown('<div style="height: 0.6rem;"></div>', unsafe_allow_html=True)
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Weekly Leadership Summary</h3><span>One-click update for PSR lead, manager, and supervisor</span></div>', unsafe_allow_html=True)

    summary_cols = st.columns([1.2, 1.2, 2])
    with summary_cols[0]:
        summary_anchor_date = st.date_input("Week of", value=date.today(), key=f"{panel_key}_summary_anchor")
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

    wins_lines = [
        f"- Resolved {len(issues_resolved_week)} triage issue(s) this week.",
        f"- Logged {len(huddles_week)} huddle note(s) to keep team alignment visible.",
        f"- Completed {len(followups_completed_week)} relationship follow-up(s).",
    ]
    risks_lines = [
        f"- {len(open_end_of_week)} issue(s) remain open in the lead queue.",
        f"- {psr_waiting_count} item(s) are waiting on PSR lane follow-through.",
        f"- {leadership_waiting_count} item(s) are waiting on manager/supervisor decisions.",
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

    st.markdown('<div style="height: 0.6rem;"></div>', unsafe_allow_html=True)
    command_tab, triage_tab, huddle_tab, sop_tab, relationship_tab = st.tabs(
        ["Command Center", "Clinical Triage Queue", "Daily Huddle", "SOP Playbook", "Relationship Tracker"]
    )

    with command_tab:
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
            st.caption(f"Clinic overdue tasks: {len(clinical_overdue)}")
            st.caption(f"SOP entries: {len(sop_entries)}")
            due_followups = [
                person
                for person in relationship_touchpoints
                if person.get("next_follow_up_date") and person.get("next_follow_up_date") <= date.today()
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

    with triage_tab:
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
                due_date = st.date_input("Due date", value=date.today())
            with create_cols[2]:
                due_time = st.time_input("Due time", value=time(16, 0))
                escalation_target = st.selectbox("Escalation target", ["none", "psr_lead", "manager", "supervisor"])
                decision_needed_by = st.date_input("Decision needed by", value=date.today() + timedelta(days=1))
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
                        update_lead_clinical_issue(issue_id, status="resolved", resolved_date=date.today())
                        st.rerun()
                    if action_cols[3].button("Reopen", key=f"{panel_key}_issue_reopen_{issue_id}"):
                        update_lead_clinical_issue(issue_id, status="new", resolved_date=None)
                        st.rerun()
        else:
            st.markdown('<div class="empty-state">No open triage issues.</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    with huddle_tab:
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.markdown('<div class="panel-title"><h3>Daily Huddle Builder</h3><span>Auto-generate agenda and send-ready recap notes</span></div>', unsafe_allow_html=True)

        today_open = [item for item in open_issues if item.get("due_date") in (None, date.today())]
        top_escalations = [item for item in escalated_issues if item.get("escalation_target") in ("manager", "supervisor")]
        relationship_followups = [
            person
            for person in relationship_touchpoints
            if person.get("next_follow_up_date") and person.get("next_follow_up_date") <= date.today()
        ]

        generated_agenda = (
            f"- Clinic overdue tasks: {len(clinical_overdue)}\n"
            f"- Open triage issues due today: {len(today_open)}\n"
            f"- Escalations for leadership: {len(top_escalations)}\n"
            f"- PSR handoffs waiting: {len(waiting_psr)}\n"
            f"- Relationship follow-ups due: {len(relationship_followups)}"
        )

        huddle_date = st.date_input("Huddle date", value=date.today(), key=f"{panel_key}_huddle_date")
        priority_focus = st.text_area(
            "Priority focus",
            value=generated_agenda,
            key=f"{panel_key}_priority_focus",
            height=120,
        )
        staffing_notes = st.text_area(
            "Staffing notes",
            placeholder="Coverage gaps, rooming constraints, late starts, etc.",
            key=f"{panel_key}_staffing_notes",
            height=90,
        )
        escalation_notes = st.text_area(
            "Escalation notes",
            placeholder="What you need from PSR lead, manager, or supervisor today.",
            key=f"{panel_key}_escalation_notes",
            height=90,
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

        st.markdown("#### Recent huddles")
        if huddle_logs:
            for log in huddle_logs[:10]:
                with st.expander(f"{log.get('huddle_date')} · recap to {log.get('recap_sent_to') or 'not set'}", expanded=False):
                    st.markdown(f"**Priority focus**\n{log.get('priority_focus') or 'No notes'}")
                    st.markdown(f"**Staffing notes**\n{log.get('staffing_notes') or 'No notes'}")
                    st.markdown(f"**Escalation notes**\n{log.get('escalation_notes') or 'No notes'}")
                    st.markdown(f"**Shift recap**\n{log.get('shift_notes') or 'No notes'}")
        else:
            st.caption("No huddle logs saved yet.")
        st.markdown('</div>', unsafe_allow_html=True)

    with sop_tab:
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

    with relationship_tab:
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
                last_touch_date = st.date_input("Last touch", value=date.today())
                next_follow_up_date = st.date_input("Next follow-up", value=date.today() + timedelta(days=7))
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
                due_flag = " (due)" if followup_date and followup_date <= date.today() else ""
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
                            last_touch_date=date.today(),
                            next_follow_up_date=date.today() + timedelta(days=7),
                        )
                        st.rerun()
        else:
            st.caption("No relationship touchpoints tracked yet.")
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

    if st.button("Save Settings", type="primary"):
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
            }
        )
        st.success("Settings saved.")
        st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)


def render_analytics_panel(tasks, active_tasks, scheduled_tasks, panel_key="analytics"):
    render_metrics_row()
    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    overdue_tasks = [task for task in active_tasks if task.get("due_date") and task["due_date"] < date.today()]
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

    upcoming_3_days = len([task for task in active_tasks if task.get("due_date") and task["due_date"] <= (date.today() + timedelta(days=3))])
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

    lookback_start = date.today() - timedelta(days=41)
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
        and item.get("case_date") >= (date.today() - timedelta(days=90))
        and item.get("status") in ("planned", "completed")
    ]
    covered_cases = 0
    for item in coverage_cases:
        if ref_suggest_protocols_for_case(item, protocol_documents, max_items=1):
            covered_cases += 1
    protocol_coverage = round((covered_cases / len(coverage_cases)) * 100, 1) if coverage_cases else 0.0

    week_starts = []
    current_week_start = date.today() - timedelta(days=date.today().weekday())
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
    upcoming_predicted = [item for item in predicted_days if item[0] >= date.today()]

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
            case_date = st.date_input("Case date", value=date.today())
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
        st.session_state[month_key] = date.today().replace(day=1)

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
    today = date.today()
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
    today = date.today()
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

    due_today_tasks = [task for task in active_tasks if task.get("due_date") == date.today()]
    overdue_tasks_today = [task for task in active_tasks if task.get("due_date") and task["due_date"] < date.today()]
    unscheduled_high = [task for task in active_tasks if task.get("priority") == "high" and not (task.get("scheduled_date") and task.get("scheduled_time"))]
    clinic_backlog = [task for task in active_tasks if task.get("category") == "Clinic"]
    personal_backlog = [task for task in active_tasks if task.get("category") == "Personal"]

    overview_focus = sorted(active_tasks, key=lambda task: (0 if task.get("due_date") == date.today() else 1 if task.get("due_date") else 2, priority_rank(task["priority"]), task.get("scheduled_time") or time(23, 59)))[:4]
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
            quick_due = st.date_input("Due date", value=date.today())
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
        due_date = st.date_input("Due date", value=date.today(), key=f"{form_key}_due_date")
        schedule_enabled = st.checkbox("Schedule this task", key=f"{form_key}_schedule_enabled")
        schedule_cols = st.columns(3)
        with schedule_cols[0]:
            scheduled_date = st.date_input("Scheduled date", value=date.today(), disabled=not schedule_enabled, key=f"{form_key}_scheduled_date")
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
            completed_today_tasks = [task for task in tasks if task.get("status") == "completed" and task.get("completed_date") == date.today()]
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
    timeline_start = date.today()
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
load_lead_huddle_logs = partial(data_access.load_lead_huddle_logs, db_enabled, get_connection, st_module=st)
add_lead_huddle_log = partial(data_access.add_lead_huddle_log, db_enabled_fn=db_enabled, get_connection_fn=get_connection, st_module=st)

parse_ai_suggestions = ai_workflows.parse_ai_suggestions
parse_ai_schedule_updates = ai_workflows.parse_ai_schedule_updates
task_snapshot_for_ai = ai_workflows.task_snapshot_for_ai
generate_ai_plan = partial(ai_workflows.generate_ai_plan, ai_enabled_fn=ai_enabled, ai_api_key_fn=ai_api_key, ai_model_name_fn=ai_model_name, openai_cls=OpenAI)
generate_ai_schedule = partial(ai_workflows.generate_ai_schedule, ai_enabled_fn=ai_enabled, ai_api_key_fn=ai_api_key, ai_model_name_fn=ai_model_name, openai_cls=OpenAI)
generate_daily_review = partial(ai_workflows.generate_daily_review, ai_enabled_fn=ai_enabled, ai_api_key_fn=ai_api_key, ai_model_name_fn=ai_model_name, openai_cls=OpenAI)

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
    "priority_rank": priority_rank,
    "task_attention_signal": task_attention_signal,
    "task_attention_sort_key": task_attention_sort_key,
    "clinic_day_summary": clinic_day_summary,
    "schedule_workload_snapshot": schedule_workload_snapshot,
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
        "render_task_list_panel": render_task_list_panel,
        "render_ai_panel": render_ai_panel,
        "render_review_command_panel": render_review_command_panel,
        "render_notifications_panel": render_notifications_panel,
        "render_ma_lead_panel": render_ma_lead_panel,
        "render_settings_panel": render_settings_panel,
        "render_analytics_panel": render_analytics_panel,
        "render_daily_review_panel": render_daily_review_panel,
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
