import os
import json
import re
import calendar
from datetime import date, time, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg
from psycopg.rows import dict_row
import streamlit as st

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
    "overview_day_mode": "Auto",
    "overview_role_label": "Medical assistant",
    "overview_site_label": "Outpatient hospital",
    "overview_patient_target": 25,
    "overview_procedure_target": 8,
    "overview_admin_buffer_minutes": 60,
    "overview_shift_minutes": 480,
    "overview_focus_window_minutes": 90,
    "overview_clinic_weekdays": ["Monday", "Tuesday", "Thursday"],
    "overview_admin_weekdays": ["Wednesday"],
    "overview_procedure_friday_frequency_weeks": 2,
    "overview_procedure_friday_cycle_offset": 0,
    "or_fixed_weekday": "Friday",
    "or_alternating_days": ["Monday", "Wednesday"],
    "or_alternating_cycle_offset": 0,
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


def status_rank(status):
    return {"todo": 0, "in_progress": 1, "blocked": 2, "completed": 3}.get(status, 4)


def status_label(status):
    return {
        "todo": "Todo",
        "in_progress": "In Progress",
        "blocked": "Blocked",
        "completed": "Completed",
    }.get(status, status.replace("_", " ").title())


def recurrence_label(rule, interval):
    if not rule:
        return "No recurrence"
    if rule == "daily":
        return f"Every {interval} day(s)"
    if rule == "weekly":
        return f"Every {interval} week(s)"
    return "No recurrence"


def shift_date_by_rule(value, rule, interval):
    if not value:
        return None
    safe_interval = max(1, int(interval or 1))
    if rule == "daily":
        return value + timedelta(days=safe_interval)
    if rule == "weekly":
        return value + timedelta(days=7 * safe_interval)
    return value


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
                            scheduled_time TIME,
                            scheduled_minutes INTEGER,
                            recurrence_rule TEXT,
                            recurrence_interval INTEGER,
                            completed_date DATE
                        )
                        """
                    )
                    cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS recurrence_rule TEXT")
                    cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS recurrence_interval INTEGER")
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
                        CREATE TABLE IF NOT EXISTS surgical_cases (
                            id BIGSERIAL PRIMARY KEY,
                            case_date DATE NOT NULL,
                            case_stream TEXT NOT NULL,
                            procedure_name TEXT NOT NULL,
                            anatomical_location TEXT NOT NULL DEFAULT '',
                            status TEXT NOT NULL DEFAULT 'planned',
                            notes TEXT NOT NULL DEFAULT '',
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


def load_tasks():
    if not db_enabled():
        return st.session_state.tasks
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
                        scheduled_time,
                        scheduled_minutes,
                        recurrence_rule,
                        recurrence_interval,
                        completed_date
                    FROM tasks
                    ORDER BY created_date DESC, id DESC
                    """
                )
                return cur.fetchall()
    except psycopg.Error:
        return st.session_state.tasks


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
                        created_date
                    FROM surgical_cases
                    ORDER BY case_date DESC, id DESC
                    """
                )
                return cur.fetchall()
    except psycopg.Error:
        return st.session_state.surgical_cases


def add_surgical_case(case_date, case_stream, procedure_name, anatomical_location, status="planned", notes=""):
    stream_value = case_stream.strip()
    procedure_value = procedure_name.strip()
    location_value = anatomical_location.strip()
    notes_value = notes.strip()
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
                        status,
                        notes,
                        created_date
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        case_date,
                        stream_value,
                        procedure_value,
                        location_value,
                        status,
                        notes_value,
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
            "status": status,
            "notes": notes_value,
            "created_date": date.today(),
        }
    )


def update_surgical_case(case_id, **fields):
    allowed_fields = {"case_date", "case_stream", "procedure_name", "anatomical_location", "status", "notes"}
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


def weekday_index_to_name(index):
    return {
        0: "Monday",
        1: "Tuesday",
        2: "Wednesday",
        3: "Thursday",
        4: "Friday",
        5: "Saturday",
        6: "Sunday",
    }.get(index, "Monday")


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


def predicted_or_days(app_settings, horizon_days=28):
    fixed_weekday = weekday_name_to_index(app_settings.get("or_fixed_weekday", "Friday"))
    alternating_days = app_settings.get("or_alternating_days") or ["Monday", "Wednesday"]
    if len(alternating_days) < 2:
        alternating_days = ["Monday", "Wednesday"]
    alt_day_a = weekday_name_to_index(alternating_days[0])
    alt_day_b = weekday_name_to_index(alternating_days[1])
    cycle_offset = safe_int(app_settings.get("or_alternating_cycle_offset", 0), 0)

    out = []
    for offset in range(horizon_days):
        day = date.today() + timedelta(days=offset)
        iso_week = day.isocalendar().week
        weekday = day.weekday()
        if weekday == fixed_weekday:
            out.append((day, "OR day"))
            continue
        alternating_weekday = alt_day_a if ((iso_week + cycle_offset) % 2 == 0) else alt_day_b
        if weekday == alternating_weekday:
            out.append((day, "Alternating OR day"))
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
                        return merged
        except (psycopg.Error, json.JSONDecodeError):
            pass

    stored = st.session_state.get("app_settings")
    if isinstance(stored, dict):
        merged = dict(DEFAULT_APP_SETTINGS)
        merged.update(stored)
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
            --bg: #f4f1ea;
            --surface: rgba(255, 252, 246, 0.88);
            --ink: #1f2933;
            --muted: #667085;
            --line: rgba(31, 41, 51, 0.08);
            --shadow: 0 20px 60px rgba(15, 23, 42, 0.08);
            --radius: 22px;
        }

        .stApp {
            background:
                radial-gradient(circle at top left, rgba(15, 118, 110, 0.16), transparent 28%),
                radial-gradient(circle at top right, rgba(249, 115, 22, 0.14), transparent 24%),
                linear-gradient(180deg, #f8f4ec 0%, var(--bg) 46%, #efe9dc 100%);
            color: var(--ink);
            font-family: 'DM Sans', sans-serif;
        }

        p, li, label, .stMarkdown, .stCaption, .stText, [data-testid="stMarkdownContainer"] {
            color: #1f2933;
        }

        h1, h2, h3, h4, .stMarkdown strong {
            font-family: 'Space Grotesk', sans-serif;
            color: #12212d;
            letter-spacing: -0.03em;
        }

        section[data-testid="stSidebar"] {
            background:
                radial-gradient(circle at 16% 8%, rgba(56, 189, 248, 0.22), transparent 22%),
                radial-gradient(circle at 88% 18%, rgba(249, 115, 22, 0.18), transparent 24%),
                linear-gradient(180deg, rgba(12, 24, 35, 0.98), rgba(15, 23, 42, 0.96));
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
                radial-gradient(circle at top right, rgba(255, 255, 255, 0.34), transparent 26%),
                linear-gradient(135deg, #0f766e 0%, #155eef 52%, #fb923c 100%);
            color: white;
            box-shadow: 0 28px 80px rgba(15, 118, 110, 0.28);
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
            background: rgba(255, 253, 248, 0.95);
            border: 1px solid rgba(18, 33, 45, 0.08);
            border-radius: 18px;
            padding: 1rem;
            margin-bottom: 0.9rem;
            box-shadow: var(--shadow);
        }

        .task-title {
            font-family: 'Space Grotesk', sans-serif;
            font-size: 1.05rem;
            font-weight: 700;
            color: #12212d;
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

        .pill-priority-high { color: #991b1b; background: #fee2e2; }
        .pill-priority-medium { color: #92400e; background: #ffedd5; }
        .pill-priority-low { color: #166534; background: #dcfce7; }
        .pill-category { color: #0f172a; background: #e2e8f0; }
        .pill-status { color: #0f766e; background: #ccfbf1; }
        .pill-status-todo { color: #1e3a8a; background: #dbeafe; }
        .pill-status-in_progress { color: #7c2d12; background: #ffedd5; }
        .pill-status-blocked { color: #991b1b; background: #fee2e2; }
        .pill-status-completed { color: #166534; background: #dcfce7; }

        .empty-state {
            border: 1px dashed rgba(18, 33, 45, 0.15);
            background: rgba(255, 255, 255, 0.45);
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
                radial-gradient(circle at top right, rgba(255, 255, 255, 0.22), transparent 24%),
                linear-gradient(135deg, rgba(15, 118, 110, 0.98), rgba(21, 94, 239, 0.96));
            color: white;
            border: 1px solid rgba(255, 255, 255, 0.12);
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
            background: rgba(255, 255, 255, 0.12);
            border: 1px solid rgba(255, 255, 255, 0.16);
            box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.12);
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
            background: rgba(255, 255, 255, 0.7);
            border: 1px solid rgba(18, 33, 45, 0.08);
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
            background: rgba(15, 23, 42, 0.06);
            border: 1px solid rgba(15, 23, 42, 0.08);
            color: #12212d;
            font-size: 0.8rem;
            font-weight: 600;
        }

        .ai-response-card {
            background: rgba(255, 255, 255, 0.86);
            border: 1px solid rgba(18, 33, 45, 0.08);
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
            border: 1px solid rgba(18, 33, 45, 0.08);
            background: rgba(255, 255, 255, 0.58);
            box-shadow: 0 18px 40px rgba(15, 23, 42, 0.05);
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


def priority_rank(priority):
    return {"high": 0, "medium": 1, "low": 2}.get(priority, 1)


def format_due(task):
    due_date = task.get("due_date")
    if not due_date:
        return "No due date"
    return due_date.strftime("%b %d, %Y") if hasattr(due_date, "strftime") else str(due_date)


def format_schedule(task):
    scheduled_date = task.get("scheduled_date")
    scheduled_time = task.get("scheduled_time")
    if not scheduled_date or not scheduled_time:
        return "Unscheduled"
    return f'{scheduled_date.strftime("%b %d")}, {scheduled_time.strftime("%I:%M %p").lstrip("0")}'


def task_matches(task, lane):
    return task["category"] == lane and task["status"] != "completed"


def add_task(
    title,
    description,
    category,
    priority,
    due_date,
    scheduled_date=None,
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
                        scheduled_time,
                        scheduled_minutes,
                        recurrence_rule,
                        recurrence_interval,
                        completed_date
                    ) VALUES (%s, %s, %s, %s, 'todo', %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        title.strip(),
                        description.strip(),
                        category,
                        priority,
                        date.today(),
                        due_date,
                        scheduled_date,
                        scheduled_time,
                        scheduled_minutes,
                        recurrence_rule,
                        max(1, int(recurrence_interval or 1)),
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
            "scheduled_time": scheduled_time,
            "scheduled_minutes": scheduled_minutes,
            "recurrence_rule": recurrence_rule,
            "recurrence_interval": max(1, int(recurrence_interval or 1)),
            "completed_date": None,
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
        "scheduled_time",
        "scheduled_minutes",
        "recurrence_rule",
        "recurrence_interval",
        "completed_date",
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
                        completed_date
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

    update_task(task_id, status="completed", completed_date=date.today())

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
        update_task(task_id, status=new_status, completed_date=None)


def render_task_card(task, key_prefix="task"):
    st.markdown('<div class="task-card">', unsafe_allow_html=True)
    st.markdown(f'<div class="task-title">{task["title"]}</div>', unsafe_allow_html=True)
    if task.get("description"):
        st.write(task["description"])
    st.markdown(
        f'''<div class="task-meta">
            <span class="pill pill-priority-{task["priority"]}">Priority: {task["priority"].title()}</span>
            <span class="pill pill-category">{task["category"]}</span>
            <span class="pill pill-status pill-status-{task["status"]}">{status_label(task["status"])}</span>
            <span class="pill">Due: {format_due(task)}</span>
            <span class="pill">Schedule: {format_schedule(task)}</span>
            <span class="pill">Repeat: {recurrence_label(task.get("recurrence_rule"), task.get("recurrence_interval") or 1)}</span>
        </div>''',
        unsafe_allow_html=True,
    )
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
                scheduled_time=edit_sched_time if edit_has_schedule else None,
                scheduled_minutes=edit_sched_minutes if edit_has_schedule else None,
                recurrence_rule=None if edit_recurrence_rule == "none" else edit_recurrence_rule,
                recurrence_interval=int(edit_recurrence_interval),
            )
            st.success("Task updated.")
            st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)


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
    in_progress = [task for task in active_tasks if task.get("status") == "in_progress"]
    completed_today = [task for task in tasks if task.get("status") == "completed" and task.get("completed_date") == today]

    if overdue:
        recommended = sorted(overdue, key=lambda task: (task.get("due_date") or date.min, priority_rank(task.get("priority"))))[0]
        focus_label = f"Overdue: {recommended.get('title')}"
    elif due_today:
        recommended = sorted(due_today, key=lambda task: (priority_rank(task.get("priority")), task.get("scheduled_time") or time(23, 59)))[0]
        focus_label = f"Due today: {recommended.get('title')}"
    elif unscheduled_high:
        recommended = sorted(unscheduled_high, key=lambda task: (task.get("due_date") or date.max, priority_rank(task.get("priority"))))[0]
        focus_label = f"High priority and unscheduled: {recommended.get('title')}"
    elif blocked:
        recommended = sorted(blocked, key=lambda task: (task.get("due_date") or date.max, priority_rank(task.get("priority"))))[0]
        focus_label = f"Blocked first: {recommended.get('title')}"
    elif in_progress:
        recommended = sorted(in_progress, key=lambda task: (task.get("due_date") or date.max, priority_rank(task.get("priority"))))[0]
        focus_label = f"Keep moving: {recommended.get('title')}"
    elif active_tasks:
        recommended = sorted(active_tasks, key=lambda task: (task.get("due_date") or date.max, priority_rank(task.get("priority"))))[0]
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
        "completed_today_count": len(completed_today),
        "focus_label": focus_label,
        "recommended_task": recommended,
        "overdue": overdue[:3],
        "due_soon": due_soon[:3],
        "blocked": blocked[:3],
        "unscheduled_high": unscheduled_high[:3],
    }


def safe_int(value, fallback):
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(fallback)


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


def clinic_day_summary(clinic_tasks, active_tasks, app_settings, mode_key):
    profiles = clinic_day_profiles(app_settings)
    profile = profiles.get(mode_key, profiles["general_clinic"])
    block_plan = build_time_blocks(profile)
    clinic_open = [task for task in clinic_tasks if task.get("status") != "completed"]
    top_clinic_tasks = sorted(clinic_open, key=lambda task: (priority_rank(task["priority"]), task.get("due_date") or date.max))[:5]
    clinic_unscheduled = [task for task in clinic_open if not (task.get("scheduled_date") and task.get("scheduled_time"))]
    due_soon = [task for task in clinic_open if task.get("due_date") and task["due_date"] <= date.today() + timedelta(days=3)]

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
        "role_label": app_settings.get("overview_role_label", "Medical assistant"),
        "site_label": app_settings.get("overview_site_label", "Outpatient hospital"),
        "patient_target": safe_int(app_settings.get("overview_patient_target", 25), 25),
        "procedure_target": safe_int(app_settings.get("overview_procedure_target", 8), 8),
        "admin_buffer_minutes": safe_int(app_settings.get("overview_admin_buffer_minutes", 60), 60),
        "shift_minutes": safe_int(app_settings.get("overview_shift_minutes", 480), 480),
        "focus_window_minutes": safe_int(app_settings.get("overview_focus_window_minutes", 90), 90),
        "clinic_weekdays": app_settings.get("overview_clinic_weekdays", ["Monday", "Tuesday", "Thursday"]),
        "admin_weekdays": app_settings.get("overview_admin_weekdays", ["Wednesday"]),
        "procedure_friday_frequency_weeks": safe_int(app_settings.get("overview_procedure_friday_frequency_weeks", 2), 2),
        "procedure_friday_cycle_offset": safe_int(app_settings.get("overview_procedure_friday_cycle_offset", 0), 0),
    }


def overview_mode_label(mode_key):
    return {
        "Auto": "Auto",
        "Outpatient clinic": "Outpatient clinic",
        "Procedure Friday": "Procedure Friday",
        "Admin catch-up": "Admin catch-up",
        "Mixed day": "Mixed day",
    }.get(mode_key, "Auto")


def resolve_overview_day_context(overview_settings, active_tasks, personal_tasks, clinic_tasks):
    today = date.today()
    mode = overview_settings.get("day_mode", "Auto")
    weekday_name = today.strftime("%A")
    clinic_weekdays = overview_settings.get("clinic_weekdays") or ["Monday", "Tuesday", "Thursday"]
    admin_weekdays = overview_settings.get("admin_weekdays") or ["Wednesday"]
    cadence_weeks = max(1, safe_int(overview_settings.get("procedure_friday_frequency_weeks", 2), 2))
    cycle_offset = safe_int(overview_settings.get("procedure_friday_cycle_offset", 0), 0)
    week_number = today.isocalendar().week

    auto_mode = "Mixed day"
    reason_text = "Use the board signal to stay flexible when the weekly pattern is unclear."
    if today.weekday() == 4 and ((week_number + cycle_offset) % cadence_weeks == 0):
        auto_mode = "Procedure Friday"
        reason_text = f"Friday matches the {cadence_weeks}-week procedure cadence."
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
            default=current["clinic_weekdays"] if isinstance(current.get("clinic_weekdays"), list) else ["Monday", "Tuesday", "Thursday"],
            key=f"{panel_key}_clinic_weekdays",
        )

    with right_col:
        admin_buffer = st.slider("Admin buffer minutes", min_value=30, max_value=150, value=current["admin_buffer_minutes"], step=15, key=f"{panel_key}_admin_buffer")
        shift_minutes = st.slider("Shift length minutes", min_value=240, max_value=600, value=current["shift_minutes"], step=15, key=f"{panel_key}_shift_minutes")
        focus_window_minutes = st.slider("Focus window minutes", min_value=30, max_value=180, value=current["focus_window_minutes"], step=15, key=f"{panel_key}_focus_window")
        admin_weekdays = st.multiselect(
            "Admin weekdays",
            ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
            default=current["admin_weekdays"] if isinstance(current.get("admin_weekdays"), list) else ["Wednesday"],
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
        "role_label": role_label.strip() or "Medical assistant",
        "site_label": site_label.strip() or "Outpatient hospital",
        "patient_target": int(patient_target),
        "procedure_target": int(procedure_target),
        "admin_buffer_minutes": int(admin_buffer),
        "shift_minutes": int(shift_minutes),
        "focus_window_minutes": int(focus_window_minutes),
        "clinic_weekdays": clinic_weekdays or ["Monday", "Tuesday", "Thursday"],
        "admin_weekdays": admin_weekdays or ["Wednesday"],
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
                st.session_state[focus_key] = chosen["title"]
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

    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Schedule Builder</h3><span>Preview the next best blocks before you pin times</span></div>', unsafe_allow_html=True)
    metric_cols = st.columns(4)
    metric_cols[0].metric("Scheduled", len(snapshot["upcoming"]))
    metric_cols[1].metric("Unscheduled", len(snapshot["unscheduled"]))
    metric_cols[2].metric("High-priority unscheduled", len(snapshot["unscheduled_high"]))
    metric_cols[3].metric("Capacity gap", max(0, snapshot["capacity_gap"]))

    left_col, right_col = st.columns([1.1, 0.9], gap="large")
    with left_col:
        st.markdown('<div class="panel-title"><h3>Draft Order</h3><span>Ranked according to the selected lens</span></div>', unsafe_allow_html=True)
        if ranked_tasks:
            for task in ranked_tasks:
                st.markdown(
                    f"- <strong>{task['title']}</strong> · {task['category']} · {task['priority'].title()} · {task.get('due_date') or 'No due date'}",
                    unsafe_allow_html=True,
                )
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


def render_surgical_cases_panel(surgical_cases, app_settings, panel_key="cases"):
    predicted_days = predicted_or_days(app_settings, horizon_days=120)
    predicted_labels = {day: label for day, label in predicted_days}
    upcoming_predicted = [item for item in predicted_days if item[0] >= date.today()]

    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Surgical Cases</h3><span>Non-PHI case log for surgery and TenJet procedures</span></div>', unsafe_allow_html=True)
    st.caption("Store procedure name, date, and anatomical location only. Do not enter patient identifiers.")

    metrics = st.columns(4)
    planned_cases = [item for item in surgical_cases if item.get("status") == "planned"]
    completed_cases = [item for item in surgical_cases if item.get("status") == "completed"]
    tenjet_cases = [item for item in surgical_cases if item.get("case_stream") == "TenJet"]
    main_or_cases = [item for item in surgical_cases if item.get("case_stream") == "Main OR"]
    metrics[0].metric("Planned", len(planned_cases))
    metrics[1].metric("Completed", len(completed_cases))
    metrics[2].metric("Main OR", len(main_or_cases))
    metrics[3].metric("TenJet", len(tenjet_cases))

    top_left, top_right = st.columns([1.1, 0.9], gap="large")
    with top_left:
        with st.form(f"{panel_key}_new_case_form"):
            case_date = st.date_input("Case date", value=date.today())
            case_stream = st.selectbox("Case stream", ["Main OR", "TenJet"])
            procedure_name = st.text_input("Procedure performed")
            anatomical_location = st.text_input("Anatomical location")
            status = st.selectbox("Status", ["planned", "completed", "canceled"])
            notes = st.text_area("Notes (non-PHI)", height=80)
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
                    status=status,
                    notes=notes,
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
    if surgical_cases:
        for item in surgical_cases[:20]:
            case_id = item.get("id")
            case_date_value = item.get("case_date")
            date_label = case_date_value.strftime("%b %d, %Y") if hasattr(case_date_value, "strftime") else str(case_date_value)
            or_hint = predicted_labels.get(case_date_value)
            hint_suffix = f" · {or_hint}" if or_hint else ""
            st.markdown(
                f"<div class='task-card'><div class='task-title'>{item.get('procedure_name')}</div>"
                f"<div class='task-meta'><span class='pill'>{date_label}{hint_suffix}</span><span class='pill pill-category'>{item.get('case_stream')}</span><span class='pill pill-status'>{str(item.get('status', 'planned')).title()}</span><span class='pill'>{item.get('anatomical_location') or 'Location not specified'}</span></div>"
                f"<p style='margin-top:0.6rem;'>{item.get('notes') or ''}</p></div>",
                unsafe_allow_html=True,
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
        st.markdown('<div class="empty-state">No surgical cases logged yet.</div>', unsafe_allow_html=True)

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
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Add Task</h3><span>Quick capture</span></div>', unsafe_allow_html=True)
    with st.form(form_key):
        title = st.text_input("Task title")
        description = st.text_area("Description", height=100)
        category_options = ["Personal", "Clinic"]
        resolved_default_category = default_category or defaults.get("default_category", "Personal")
        category_index = category_options.index(resolved_default_category) if resolved_default_category in category_options else 0
        category = st.selectbox("Category", category_options, index=category_index)
        priority_options = ["high", "medium", "low"]
        default_priority = defaults.get("default_priority", "medium")
        priority_index = priority_options.index(default_priority) if default_priority in priority_options else 1
        priority = st.selectbox("Priority", priority_options, index=priority_index)
        due_date = st.date_input("Due date", value=date.today())
        schedule_enabled = st.checkbox("Schedule this task")
        schedule_cols = st.columns(3)
        with schedule_cols[0]:
            scheduled_date = st.date_input("Scheduled date", value=date.today(), disabled=not schedule_enabled)
        with schedule_cols[1]:
            scheduled_time = st.time_input(
                "Scheduled time",
                value=parse_time_value(defaults.get("default_schedule_time")) or time(9, 0),
                disabled=not schedule_enabled,
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
            )
        recurrence_cols = st.columns(2)
        with recurrence_cols[0]:
            recurrence_rule = st.selectbox(
                "Recurrence",
                ["none", "daily", "weekly"],
                format_func=lambda value: "None" if value == "none" else value.title(),
            )
        with recurrence_cols[1]:
            recurrence_interval = st.number_input(
                "Every",
                min_value=1,
                max_value=30,
                value=1,
                step=1,
                disabled=recurrence_rule == "none",
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


initialize_database()

app_settings = load_app_settings()

inject_styles()
render_hero()

with st.sidebar:
    st.markdown(
        """
        <div style="padding: 1rem 1rem 1.15rem; margin-bottom: 1rem; border-radius: 20px; background: linear-gradient(135deg, rgba(15, 118, 110, 0.28), rgba(21, 94, 239, 0.24)); border: 1px solid rgba(255, 255, 255, 0.1);">
            <h2 style="margin: 0; color: white; font-size: 1.2rem;">DayAnchor</h2>
            <p style="margin: 0.45rem 0 0; color: rgba(248, 250, 252, 0.82); font-size: 0.9rem;">Task capture with Postgres persistence and optional AI planning.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if db_enabled():
        source = DB_CANDIDATE_SOURCE or "database URL"
        st.caption(f"Connected to Postgres via {source}.")
        st.caption("Tasks persist across restarts and deployments.")
    elif DB_ERROR:
        st.caption("Database connection failed.")
        st.caption("Using session-only fallback until DB is reachable.")
    else:
        st.caption("No DATABASE_URL or DATABASE_PUBLIC_URL found.")
        st.caption("Running in session-only fallback mode.")

    st.markdown("---")
    st.markdown("### Navigation")
    current_page = st.radio(
        "Go to",
        ["Overview", "Personal", "Clinic", "Cases", "Schedule", "AI", "Analytics", "Notifications", "Daily Review", "Settings"],
        label_visibility="collapsed",
    )

    st.markdown("---")
    st.markdown("### Data Controls")
    health_state, health_message = db_health_status()
    detected_names = configured_database_env_names()
    if detected_names:
        st.caption(f"Detected DB vars: {', '.join(detected_names)}")
    else:
        st.caption("Detected DB vars: none")
        st.caption("Tip: ensure the web app service has DATABASE_URL or DATABASE_PUBLIC_URL set in Railway.")
    if health_state == "ok":
        st.success(f"DB Health: {health_message}")
    elif health_state == "error":
        st.warning(f"DB Health: {health_message}")
    else:
        st.info(f"DB Health: {health_message}")

    if st.button("Seed Sample Tasks", use_container_width=True):
        seed_sample_tasks()
        st.success("Sample tasks added.")
        st.rerun()

    st.markdown("---")
    st.markdown("### View Controls")
    search_query = st.text_input("Search tasks", placeholder="Title or description")
    category_filter = st.multiselect("Category", ["Personal", "Clinic"], default=["Personal", "Clinic"])
    priority_filter = st.multiselect("Priority", ["high", "medium", "low"], default=["high", "medium", "low"])
    status_filter = st.multiselect(
        "Status",
        ["todo", "in_progress", "blocked", "completed"],
        default=["todo", "in_progress", "blocked", "completed"],
        format_func=status_label,
    )
    scheduled_only = st.checkbox("Scheduled tasks only", value=False)
    timeline_days = st.slider(
        "Timeline window (days)",
        min_value=3,
        max_value=21,
        value=int(app_settings.get("timeline_days", 7)),
    )

    st.markdown("---")
    st.markdown("### AI")
    if ai_enabled():
        st.success(f"AI ready ({ai_model_name()})")
    else:
        st.info("AI disabled. Set OPENAI_API_KEY to enable.")

st.markdown('<p class="section-lead">Navigate by lane and workflow area from the sidebar.</p>', unsafe_allow_html=True)

tasks = load_tasks()
surgical_cases = load_surgical_cases()
query = (search_query or "").strip().lower()
all_active_tasks = [task for task in tasks if task.get("status") != "completed"]
all_completed_tasks = [task for task in tasks if task.get("status") == "completed"]
completed_today_all = [task for task in all_completed_tasks if task.get("completed_date") == date.today()]


def task_matches_filters(task):
    if category_filter and task.get("category") not in category_filter:
        return False
    if priority_filter and task.get("priority") not in priority_filter:
        return False
    if status_filter and task.get("status") not in status_filter:
        return False
    if scheduled_only and not (task.get("scheduled_date") and task.get("scheduled_time")):
        return False
    if query:
        title = str(task.get("title", "")).lower()
        description = str(task.get("description", "")).lower()
        if query not in title and query not in description:
            return False
    return True


filtered_tasks = [task for task in tasks if task_matches_filters(task)]
active_tasks = [task for task in filtered_tasks if task["status"] != "completed"]
completed_tasks = [task for task in filtered_tasks if task["status"] == "completed"]
personal_tasks = sorted([task for task in active_tasks if task["category"] == "Personal"], key=lambda task: (priority_rank(task["priority"]), task["due_date"] or date.max))
clinic_tasks = sorted([task for task in active_tasks if task["category"] == "Clinic"], key=lambda task: (priority_rank(task["priority"]), task["due_date"] or date.max))
due_today = [task for task in active_tasks if task.get("due_date") == date.today()]
overdue_tasks = [task for task in active_tasks if task.get("due_date") and task["due_date"] < date.today()]
scheduled_tasks = sorted(
    [task for task in active_tasks if task.get("scheduled_date") and task.get("scheduled_time")],
    key=lambda task: (task["scheduled_date"], task["scheduled_time"], priority_rank(task["priority"])),
)


def render_metrics_row():
    metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)
    metric_col1.metric("Active Tasks", len(active_tasks))
    metric_col2.metric("Due Today", len(due_today))
    metric_col3.metric("Completed", len(completed_tasks))
    metric_col4.metric("Scheduled", len(scheduled_tasks))


if len(filtered_tasks) != len(tasks):
    st.caption(f"Showing {len(filtered_tasks)} of {len(tasks)} tasks based on current filters.")

if current_page == "Overview":
    render_page_banner("overview", "Control Tower", "High-level triage, fast capture, and the day’s most important work.")
    overview_settings = render_overview_tuning_panel(app_settings, panel_key="overview_page")
    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    render_overview_control_tower(tasks, active_tasks, completed_today_all, personal_tasks, clinic_tasks, scheduled_tasks, app_settings, overview_settings, panel_key="overview_page")

    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    cols = st.columns(2, gap="large")
    with cols[0]:
        render_add_task_panel("add_task_form_overview", app_settings)
    with cols[1]:
        render_task_list_panel("Due Today", "Only the highest attention work", sorted(due_today, key=lambda item: priority_rank(item["priority"])), "today", "No tasks due today.")

elif current_page == "Personal":
    render_page_banner("personal", "Personal Lane", "Private tasks, self-management, and low-friction planning.")
    render_metrics_row()
    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    render_personal_focus_panel(personal_tasks, active_tasks, app_settings, panel_key="personal_page")
    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    left, right = st.columns([1, 1.2], gap="large")
    with left:
        render_add_task_panel("add_task_form_personal", app_settings, default_category="Personal")
    with right:
        render_task_list_panel("Personal Tasks", "Your personal workflow", personal_tasks, "personal_page", "No personal tasks match your filters.")

elif current_page == "Clinic":
    render_page_banner("clinic", "Clinic Lane", "Operational work, patient-facing tasks, and service flow.")
    render_metrics_row()
    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    render_clinic_command_center(clinic_tasks, active_tasks, app_settings, panel_key="clinic_page")
    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    left, right = st.columns([1, 1.2], gap="large")
    with left:
        render_add_task_panel("add_task_form_clinic", app_settings, default_category="Clinic")
    with right:
        render_task_list_panel("Clinic Tasks", "Operational and patient-facing work", clinic_tasks, "clinic_page", "No clinic tasks match your filters.")

elif current_page == "Cases":
    render_page_banner("clinic", "Surgical Cases", "Track surgery and TenJet case scheduling without PHI.")
    render_surgical_cases_panel(surgical_cases, app_settings, panel_key="cases_page")

elif current_page == "Schedule":
    render_page_banner("schedule", "Schedule View", "A timeline-first view for blocking work into realistic chunks.")
    render_metrics_row()
    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    render_schedule_builder_panel(active_tasks, app_settings, panel_key="schedule_page")
    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    render_timeline_panel(scheduled_tasks, timeline_days)

    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    unscheduled_tasks = [task for task in active_tasks if not (task.get("scheduled_date") and task.get("scheduled_time"))]
    cols = st.columns(2, gap="large")
    with cols[0]:
        render_task_list_panel("Scheduled Blocks", "Chronological", scheduled_tasks, "schedule_page", "No scheduled tasks yet.")
    with cols[1]:
        render_task_list_panel("Unscheduled Tasks", "Good candidates for AI auto-schedule", unscheduled_tasks, "unscheduled_page", "Everything is scheduled.")

elif current_page == "AI":
    render_page_banner("ai", "AI Workbench", "Plan, schedule, and review from one dedicated command center.")
    render_metrics_row()
    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    render_ai_panel(filtered_tasks, active_tasks, panel_key="ai_page")

    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    render_task_list_panel("Blocked Tasks", "AI can help unblock these", [task for task in active_tasks if task.get("status") == "blocked"], "ai_blocked", "No blocked tasks right now.")

elif current_page == "Analytics":
    render_page_banner("analytics", "Analytics Board", "A quicker read on workload, execution, and bottlenecks.")
    render_metrics_row()
    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    analytics_cols = st.columns(4)
    analytics_cols[0].metric("Clinic active", len([task for task in active_tasks if task.get("category") == "Clinic"]))
    analytics_cols[1].metric("Personal active", len([task for task in active_tasks if task.get("category") == "Personal"]))
    analytics_cols[2].metric("Clinic overdue", len([task for task in overdue_tasks if task.get("category") == "Clinic"]))
    analytics_cols[3].metric("High unscheduled", len([task for task in active_tasks if task.get("priority") == "high" and not (task.get("scheduled_date") and task.get("scheduled_time"))]))

    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Analytics</h3><span>Snapshot of workload and execution</span></div>', unsafe_allow_html=True)

    status_counts = {
        "Todo": len([task for task in filtered_tasks if task.get("status") == "todo"]),
        "In Progress": len([task for task in filtered_tasks if task.get("status") == "in_progress"]),
        "Blocked": len([task for task in filtered_tasks if task.get("status") == "blocked"]),
        "Completed": len([task for task in filtered_tasks if task.get("status") == "completed"]),
    }
    category_counts = {
        "Personal": len([task for task in filtered_tasks if task.get("category") == "Personal"]),
        "Clinic": len([task for task in filtered_tasks if task.get("category") == "Clinic"]),
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

elif current_page == "Notifications":
    render_page_banner("notifications", "Alerts", "A focused triage board for overdue, blocked, and unscheduled work.")
    render_metrics_row()
    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)

    overdue_all = [task for task in all_active_tasks if task.get("due_date") and task["due_date"] < date.today()]
    blocked_all = [task for task in all_active_tasks if task.get("status") == "blocked"]
    unscheduled_high = [
        task
        for task in all_active_tasks
        if task.get("priority") == "high" and not (task.get("scheduled_date") and task.get("scheduled_time"))
    ]
    due_tomorrow = [task for task in all_active_tasks if task.get("due_date") == (date.today() + timedelta(days=1))]

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
        render_task_list_panel("Clinic Alerts", "Clinic overdue, blocked, and unscheduled items", [task for task in overdue_all + blocked_all + unscheduled_high if task.get("category") == "Clinic"], "notif_clinic_alerts", "No clinic-specific alerts right now.")
    with alert_cols[1]:
        render_task_list_panel("Personal Alerts", "Personal overdue, blocked, and unscheduled items", [task for task in overdue_all + blocked_all + unscheduled_high if task.get("category") == "Personal"], "notif_personal_alerts", "No personal-specific alerts right now.")

    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    cols = st.columns(2, gap="large")
    with cols[0]:
        render_task_list_panel("Overdue Tasks", "Highest urgency", overdue_all, "notif_overdue", "No overdue tasks.")
    with cols[1]:
        render_task_list_panel("Blocked Tasks", "Needs intervention", blocked_all, "notif_blocked", "No blocked tasks.")

elif current_page == "Daily Review":
    render_page_banner("review", "Daily Review", "Close the loop on today and draft the next move.")
    render_metrics_row()
    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    render_review_command_panel(all_active_tasks, completed_today_all, app_settings, panel_key="daily_review_page")

    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    render_task_list_panel("Completed Today", "What you finished", completed_today_all, "daily_completed", "No tasks completed today yet.")

elif current_page == "Settings":
    render_page_banner("settings", "Settings", "Tune the defaults that shape capture, scheduling, and timelines.")
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

    st.markdown("### OR Cadence Defaults")
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
                "or_fixed_weekday": settings_or_fixed_weekday,
                "or_alternating_days": settings_or_alternating_days,
                "or_alternating_cycle_offset": int(settings_or_cycle_offset),
            }
        )
        st.success("Settings saved.")
        st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)

else:
    st.info("Select a page from the sidebar navigation.")
