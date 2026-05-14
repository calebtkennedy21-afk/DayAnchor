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
            background: linear-gradient(180deg, rgba(18, 33, 45, 0.98), rgba(17, 24, 39, 0.94));
            border-right: 1px solid rgba(255, 255, 255, 0.08);
        }

        section[data-testid="stSidebar"] * {
            color: #f8fafc;
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


initialize_database()

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
    timeline_days = st.slider("Timeline window (days)", min_value=3, max_value=21, value=7)

    st.markdown("---")
    st.markdown("### AI")
    if ai_enabled():
        st.success(f"AI ready ({ai_model_name()})")
    else:
        st.info("AI disabled. Set OPENAI_API_KEY to enable.")

st.markdown('<p class="section-lead">Capture work quickly and split it between your personal lane and clinic lane.</p>', unsafe_allow_html=True)

tasks = load_tasks()
query = (search_query or "").strip().lower()


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
    [
        task
        for task in active_tasks
        if task.get("scheduled_date") and task.get("scheduled_time")
    ],
    key=lambda task: (task["scheduled_date"], task["scheduled_time"], priority_rank(task["priority"])),
)

metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)
metric_col1.metric("Active Tasks", len(active_tasks))
metric_col2.metric("Due Today", len(due_today))
metric_col3.metric("Completed", len(completed_tasks))
metric_col4.metric("Scheduled", len(scheduled_tasks))

if len(filtered_tasks) != len(tasks):
    st.caption(f"Showing {len(filtered_tasks)} of {len(tasks)} tasks based on current filters.")

st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
st.markdown('<div class="panel">', unsafe_allow_html=True)
st.markdown('<div class="panel-title"><h3>AI Planner</h3><span>Task-aware guidance</span></div>', unsafe_allow_html=True)
default_prompt = "Give me a focused plan for today."
ai_prompt = st.text_area("Ask AI", value=default_prompt, height=90)
action_cols = st.columns(2)
with action_cols[0]:
    generate_plan_clicked = st.button("Generate AI Plan")
with action_cols[1]:
    auto_schedule_clicked = st.button("Auto-Schedule Tasks")

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
    if st.button("Add Suggested Tasks", type="primary"):
        apply_ai_suggestions(st.session_state.ai_suggestions)
        added_count = len(st.session_state.ai_suggestions)
        st.session_state.ai_suggestions = []
        st.success(f"Added {added_count} suggested task(s).")
        st.rerun()
if st.session_state.ai_schedule_updates:
    st.caption(f"Schedule updates detected: {len(st.session_state.ai_schedule_updates)}")
    if st.button("Apply Auto-Schedule", type="secondary"):
        apply_ai_schedule_updates(st.session_state.ai_schedule_updates)
        applied_count = len(st.session_state.ai_schedule_updates)
        st.session_state.ai_schedule_updates = []
        st.success(f"Applied {applied_count} schedule update(s).")
        st.rerun()
if not st.session_state.ai_response and not st.session_state.ai_error:
    st.markdown('<div class="empty-state">AI planner is ready when you are.</div>', unsafe_allow_html=True)
st.markdown('</div>', unsafe_allow_html=True)

left, right = st.columns([1.1, 1], gap="large")
with left:
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Add Task</h3><span>Quick capture</span></div>', unsafe_allow_html=True)
    with st.form("add_task_form"):
        title = st.text_input("Task title")
        description = st.text_area("Description", height=100)
        category = st.selectbox("Category", ["Personal", "Clinic"])
        priority = st.selectbox("Priority", ["high", "medium", "low"], index=1)
        due_date = st.date_input("Due date", value=date.today())
        schedule_enabled = st.checkbox("Schedule this task")
        schedule_cols = st.columns(3)
        with schedule_cols[0]:
            scheduled_date = st.date_input("Scheduled date", value=date.today(), disabled=not schedule_enabled)
        with schedule_cols[1]:
            scheduled_time = st.time_input("Scheduled time", value=time(9, 0), disabled=not schedule_enabled)
        with schedule_cols[2]:
            scheduled_minutes = st.selectbox("Duration (minutes)", [15, 30, 45, 60, 90, 120], index=3, disabled=not schedule_enabled)
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

with right:
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Today</h3><span>What needs attention now</span></div>', unsafe_allow_html=True)
    if due_today:
        for task in sorted(due_today, key=lambda item: priority_rank(item["priority"])):
            render_task_card(task, key_prefix="today")
    else:
        st.markdown('<div class="empty-state">No tasks due today.</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
st.markdown('<div class="panel">', unsafe_allow_html=True)
st.markdown('<div class="panel-title"><h3>Upcoming Schedule</h3><span>Planned work blocks</span></div>', unsafe_allow_html=True)
if scheduled_tasks:
    for task in scheduled_tasks:
        block_label = format_schedule(task)
        minutes = task.get("scheduled_minutes")
        if minutes:
            block_label = f"{block_label} · {minutes} min"
        st.markdown(
            f"**{block_label}**",
            unsafe_allow_html=False,
        )
        render_task_card(task, key_prefix="schedule")
else:
    st.markdown('<div class="empty-state">No scheduled tasks yet.</div>', unsafe_allow_html=True)
st.markdown('</div>', unsafe_allow_html=True)

st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
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

col1, col2 = st.columns(2, gap="large")
with col1:
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Personal lane</h3><span>Active tasks</span></div>', unsafe_allow_html=True)
    if personal_tasks:
        for task in personal_tasks:
            render_task_card(task, key_prefix="personal")
    else:
        st.markdown('<div class="empty-state">No personal tasks yet.</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

with col2:
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Clinic lane</h3><span>Active tasks</span></div>', unsafe_allow_html=True)
    if clinic_tasks:
        for task in clinic_tasks:
            render_task_card(task, key_prefix="clinic")
    else:
        st.markdown('<div class="empty-state">No clinic tasks yet.</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

st.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
overdue_col1, overdue_col2 = st.columns(2, gap="large")
with overdue_col1:
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Overdue</h3><span>Needs triage</span></div>', unsafe_allow_html=True)
    if overdue_tasks:
        for task in overdue_tasks:
            render_task_card(task, key_prefix="overdue")
    else:
        st.markdown('<div class="empty-state">Nothing overdue right now.</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

with overdue_col2:
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Completed</h3><span>Finished work</span></div>', unsafe_allow_html=True)
    if completed_tasks:
        for task in completed_tasks:
            render_task_card(task, key_prefix="completed")
    else:
        st.markdown('<div class="empty-state">No completed tasks yet.</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)
