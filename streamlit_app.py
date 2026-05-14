import os
from datetime import date, time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg
from psycopg.rows import dict_row
import streamlit as st


st.set_page_config(page_title="DayAnchor", page_icon="⛵", layout="wide")


if "tasks" not in st.session_state:
    st.session_state.tasks = []


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
                            completed_date DATE
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
            <p>A focused daily task board for personal and clinic work. No backend, no AI, just a clean place to capture and manage the day.</p>
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
                        completed_date
                    ) VALUES (%s, %s, %s, %s, 'todo', %s, %s, %s, %s, %s, %s)
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
            "completed_date": None,
        }
    )


def delete_task(task_id):
    if db_enabled():
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM tasks WHERE id = %s", (task_id,))
        return
    st.session_state.tasks = [task for task in st.session_state.tasks if task["id"] != task_id]


def complete_task(task_id):
    if db_enabled():
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE tasks
                    SET status = 'completed', completed_date = %s
                    WHERE id = %s
                    """,
                    (date.today(), task_id),
                )
        return

    for task in st.session_state.tasks:
        if task["id"] == task_id:
            task["status"] = "completed"
            task["completed_date"] = date.today()
            return


def render_task_card(task, key_prefix="task"):
    st.markdown('<div class="task-card">', unsafe_allow_html=True)
    st.markdown(f'<div class="task-title">{task["title"]}</div>', unsafe_allow_html=True)
    if task.get("description"):
        st.write(task["description"])
    st.markdown(
        f'''<div class="task-meta">
            <span class="pill pill-priority-{task["priority"]}">Priority: {task["priority"].title()}</span>
            <span class="pill pill-category">{task["category"]}</span>
            <span class="pill pill-status">{task["status"].title()}</span>
            <span class="pill">Due: {format_due(task)}</span>
            <span class="pill">Schedule: {format_schedule(task)}</span>
        </div>''',
        unsafe_allow_html=True,
    )
    cols = st.columns(2)
    with cols[0]:
        if task["status"] != "completed" and st.button("Mark complete", key=f"{key_prefix}_complete_{task['id']}"):
            complete_task(task["id"])
            st.rerun()
    with cols[1]:
        if st.button("Delete", key=f"{key_prefix}_delete_{task['id']}"):
            delete_task(task["id"])
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
            <p style="margin: 0.45rem 0 0; color: rgba(248, 250, 252, 0.82); font-size: 0.9rem;">Simple task capture without database or AI.</p>
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

st.markdown('<p class="section-lead">Capture work quickly and split it between your personal lane and clinic lane.</p>', unsafe_allow_html=True)

tasks = load_tasks()
active_tasks = [task for task in tasks if task["status"] != "completed"]
completed_tasks = [task for task in tasks if task["status"] == "completed"]
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
