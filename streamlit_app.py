import os
import json
import re
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


DEFAULT_APP_SETTINGS = {
    "default_category": "Personal",
    "default_priority": "medium",
    "default_duration": 60,
    "default_schedule_time": "09:00",
    "timeline_days": 7,
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
                        "You are DayAnchor AI planner. Create practical, concise planning guidance "
                        "using the provided tasks. Focus on priority, due dates, and schedule blocks."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"User request: {user_prompt}\n\n"
                        "Current tasks:\n"
                        f"{task_snapshot}\n\n"
                        "Return:\n"
                        "1) A short prioritized plan for today\n"
                        "2) Scheduling adjustments if needed\n"
                        "3) Any blockers or risks\n"
                        "4) A JSON code block with this exact shape:\n"
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
                        "Keep suggested_tasks to at most 3 items."
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
                        "You are a precise scheduling assistant. Create realistic schedule blocks "
                        "for active tasks and return strict JSON."
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
                        "Do not include completed tasks."
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
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>AI Planner</h3><span>Task-aware guidance</span></div>', unsafe_allow_html=True)
    default_prompt = "Give me a focused plan for today."
    ai_prompt = st.text_area("Ask AI", value=default_prompt, height=90, key=f"{panel_key}_ai_prompt")
    action_cols = st.columns(2)
    with action_cols[0]:
        generate_plan_clicked = st.button("Generate AI Plan", key=f"{panel_key}_gen")
    with action_cols[1]:
        auto_schedule_clicked = st.button("Auto-Schedule Tasks", key=f"{panel_key}_auto")

    if generate_plan_clicked:
        result, error, suggestions = generate_ai_plan(tasks, ai_prompt)
        st.session_state.ai_response = result
        st.session_state.ai_error = error
        st.session_state.ai_suggestions = suggestions

    if auto_schedule_clicked:
        schedule_text, schedule_error, schedule_updates = generate_ai_schedule(active_tasks, ai_prompt)
        st.session_state.ai_schedule_error = schedule_error
        st.session_state.ai_schedule_updates = schedule_updates
        if schedule_text:
            st.session_state.ai_response = schedule_text

    if st.session_state.ai_error:
        st.warning(st.session_state.ai_error)
    if st.session_state.ai_schedule_error:
        st.warning(st.session_state.ai_schedule_error)
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
    if st.session_state.ai_schedule_updates:
        st.caption(f"Schedule updates detected: {len(st.session_state.ai_schedule_updates)}")
        if st.button("Apply Auto-Schedule", type="secondary", key=f"{panel_key}_apply_schedule"):
            apply_ai_schedule_updates(st.session_state.ai_schedule_updates)
            applied_count = len(st.session_state.ai_schedule_updates)
            st.session_state.ai_schedule_updates = []
            st.success(f"Applied {applied_count} schedule update(s).")
            st.rerun()
    if not st.session_state.ai_response and not st.session_state.ai_error:
        st.markdown('<div class="empty-state">AI planner is ready when you are.</div>', unsafe_allow_html=True)
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
        ["Overview", "Personal", "Clinic", "Schedule", "AI", "Analytics", "Notifications", "Daily Review", "Settings"],
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
    render_metrics_row()
    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    left, right = st.columns([1.1, 1], gap="large")
    with left:
        render_add_task_panel("add_task_form_overview", app_settings)
    with right:
        render_task_list_panel("Today", "What needs attention now", sorted(due_today, key=lambda item: priority_rank(item["priority"])), "today", "No tasks due today.")

    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    render_task_list_panel("Upcoming Schedule", "Planned work blocks", scheduled_tasks, "overview_schedule", "No scheduled tasks yet.")

    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    cols = st.columns(2, gap="large")
    with cols[0]:
        render_task_list_panel("Personal lane", "Active tasks", personal_tasks, "overview_personal", "No personal tasks yet.")
    with cols[1]:
        render_task_list_panel("Clinic lane", "Active tasks", clinic_tasks, "overview_clinic", "No clinic tasks yet.")

elif current_page == "Personal":
    render_metrics_row()
    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    left, right = st.columns([1, 1.2], gap="large")
    with left:
        render_add_task_panel("add_task_form_personal", app_settings, default_category="Personal")
    with right:
        render_task_list_panel("Personal Tasks", "Your personal workflow", personal_tasks, "personal_page", "No personal tasks match your filters.")

elif current_page == "Clinic":
    render_metrics_row()
    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    left, right = st.columns([1, 1.2], gap="large")
    with left:
        render_add_task_panel("add_task_form_clinic", app_settings, default_category="Clinic")
    with right:
        render_task_list_panel("Clinic Tasks", "Operational and patient-facing work", clinic_tasks, "clinic_page", "No clinic tasks match your filters.")

elif current_page == "Schedule":
    render_metrics_row()
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
    render_metrics_row()
    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    render_ai_panel(filtered_tasks, active_tasks, panel_key="ai_page")

    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    render_task_list_panel("Blocked Tasks", "AI can help unblock these", [task for task in active_tasks if task.get("status") == "blocked"], "ai_blocked", "No blocked tasks right now.")

elif current_page == "Analytics":
    render_metrics_row()
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

    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    cols = st.columns(2, gap="large")
    with cols[0]:
        render_task_list_panel("Overdue Tasks", "Highest urgency", overdue_all, "notif_overdue", "No overdue tasks.")
    with cols[1]:
        render_task_list_panel("Blocked Tasks", "Needs intervention", blocked_all, "notif_blocked", "No blocked tasks.")

elif current_page == "Daily Review":
    render_metrics_row()
    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)

    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Daily Review</h3><span>End-of-day recap and tomorrow draft plan</span></div>', unsafe_allow_html=True)

    review_notes = st.text_area(
        "Context notes for today",
        placeholder="Anything important from today? Wins, blockers, meetings, constraints...",
        height=100,
        key="daily_review_notes",
    )

    if st.button("Generate Daily Review", key="gen_daily_review"):
        review_text, tomorrow_text, review_error = generate_daily_review(all_active_tasks, completed_today_all, review_notes)
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
        st.markdown('<div class="empty-state">Generate a review to get your end-of-day summary and tomorrow draft plan.</div>', unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
    render_task_list_panel("Completed Today", "What you finished", completed_today_all, "daily_completed", "No tasks completed today yet.")

elif current_page == "Settings":
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

    if st.button("Save Settings", type="primary"):
        app_settings = save_app_settings(
            {
                "default_category": settings_category,
                "default_priority": settings_priority,
                "default_duration": int(settings_duration),
                "default_schedule_time": settings_time.strftime("%H:%M"),
                "timeline_days": int(settings_timeline_days),
            }
        )
        st.success("Settings saved.")
        st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)

else:
    st.info("Select a page from the sidebar navigation.")
