import os
from datetime import date, timedelta
from html import escape
from textwrap import dedent

import streamlit as st
from openai import OpenAI

from db import get_connection, get_db_key_diagnostics, init_db


def init_openai_client():
    raw_key = (os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_KEY") or "").strip()
    cleaned_key = raw_key.strip('"').strip("'")
    if not cleaned_key:
        return None, "Missing OPENAI_API_KEY"
    if cleaned_key.lower().startswith("sk-your"):
        return None, "OPENAI_API_KEY looks like a placeholder"
    return OpenAI(api_key=cleaned_key), "Configured"


client, ai_status = init_openai_client()


def inject_styles():
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&family=DM+Sans:wght@400;500;700&display=swap');

        :root {
            --bg: #f4f1ea;
            --surface: rgba(255, 252, 246, 0.85);
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

        p,
        li,
        label,
        .stMarkdown,
        .stCaption,
        .stText,
        [data-testid="stMarkdownContainer"] {
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

        section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"],
        section[data-testid="stSidebar"] label,
        section[data-testid="stSidebar"] p {
            color: #f8fafc !important;
        }

        .block-container {
            padding-top: 2.2rem;
            padding-bottom: 2rem;
            max-width: 1180px;
        }

        div[data-testid="stMetric"] {
            background: rgba(255, 253, 248, 0.82);
            border: 1px solid rgba(18, 33, 45, 0.08);
            padding: 1rem 1.1rem;
            border-radius: 18px;
            box-shadow: var(--shadow);
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
            max-width: 660px;
            font-size: 1.02rem;
            opacity: 0.95;
            margin-bottom: 1rem;
        }

        .hero-badges {
            display: flex;
            flex-wrap: wrap;
            gap: 0.65rem;
        }

        .hero-badge {
            padding: 0.45rem 0.8rem;
            border-radius: 999px;
            background: rgba(255, 255, 255, 0.16);
            border: 1px solid rgba(255, 255, 255, 0.2);
            font-size: 0.9rem;
            backdrop-filter: blur(8px);
        }

        .panel,
        .task-card,
        .soft-card {
            background: var(--surface);
            border: 1px solid var(--line);
            border-radius: var(--radius);
            box-shadow: var(--shadow);
            backdrop-filter: blur(14px);
        }

        .panel,
        .soft-card {
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

        .panel-title span,
        .section-lead {
            color: var(--muted);
        }

        .task-card {
            padding: 1rem 1rem 0.9rem;
            margin-bottom: 0.9rem;
        }

        .task-topline {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 0.8rem;
            margin-bottom: 0.45rem;
        }

        .task-title {
            font-family: 'Space Grotesk', sans-serif;
            font-size: 1.04rem;
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

        .pill-priority-high {
            color: #991b1b;
            background: #fee2e2;
        }

        .pill-priority-medium {
            color: #92400e;
            background: #ffedd5;
        }

        .pill-priority-low {
            color: #166534;
            background: #dcfce7;
        }

        .pill-category {
            color: #0f172a;
            background: #e2e8f0;
        }

        .pill-status {
            color: #0f766e;
            background: #ccfbf1;
        }

        .empty-state {
            border: 1px dashed rgba(18, 33, 45, 0.15);
            background: rgba(255, 255, 255, 0.45);
            border-radius: 18px;
            padding: 1rem;
            color: var(--muted);
            text-align: center;
        }

        .sidebar-brand {
            padding: 1rem 1rem 1.15rem;
            margin-bottom: 1rem;
            border-radius: 20px;
            background: linear-gradient(135deg, rgba(15, 118, 110, 0.28), rgba(21, 94, 239, 0.24));
            border: 1px solid rgba(255, 255, 255, 0.1);
        }

        .sidebar-brand h2 {
            color: white;
            margin: 0;
            font-size: 1.2rem;
        }

        .sidebar-brand p {
            margin: 0.45rem 0 0;
            color: rgba(248, 250, 252, 0.82);
            font-size: 0.9rem;
        }

        .dashboard-band {
            display: grid;
            grid-template-columns: 1.5fr 1fr;
            gap: 1rem;
            margin: 1.1rem 0 1.35rem;
        }

        .focus-card {
            padding: 1.25rem;
            border-radius: 24px;
            background: linear-gradient(135deg, rgba(18, 33, 45, 0.96), rgba(15, 118, 110, 0.94));
            color: white;
            box-shadow: 0 28px 80px rgba(18, 33, 45, 0.22);
        }

        .focus-card h3,
        .focus-card strong {
            color: white;
        }

        .focus-kicker {
            text-transform: uppercase;
            letter-spacing: 0.12em;
            font-size: 0.72rem;
            opacity: 0.72;
            font-weight: 700;
        }

        .focus-title {
            font-family: 'Space Grotesk', sans-serif;
            font-size: 1.65rem;
            margin: 0.45rem 0;
        }

        .focus-meta {
            display: flex;
            flex-wrap: wrap;
            gap: 0.55rem;
            margin-top: 0.9rem;
        }

        .focus-pill {
            background: rgba(255, 255, 255, 0.12);
            border: 1px solid rgba(255, 255, 255, 0.14);
            border-radius: 999px;
            padding: 0.35rem 0.75rem;
            font-size: 0.8rem;
        }

        .gauge-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 0.85rem;
        }

        .gauge-card {
            padding: 1rem;
            border-radius: 22px;
            background: rgba(255, 252, 246, 0.88);
            border: 1px solid rgba(18, 33, 45, 0.08);
            box-shadow: var(--shadow);
            text-align: center;
        }

        .gauge-ring {
            --angle: 180deg;
            width: 116px;
            height: 116px;
            margin: 0 auto 0.8rem;
            border-radius: 50%;
            display: grid;
            place-items: center;
            background: conic-gradient(#155eef 0deg, #0f766e var(--angle), rgba(18, 33, 45, 0.08) var(--angle));
        }

        .gauge-ring::before {
            content: "";
            width: 84px;
            height: 84px;
            border-radius: 50%;
            background: #fffdf8;
            position: absolute;
        }

        .gauge-value {
            position: relative;
            z-index: 1;
            font-family: 'Space Grotesk', sans-serif;
            font-size: 1.35rem;
            font-weight: 700;
            color: #12212d;
        }

        .gauge-label {
            font-family: 'Space Grotesk', sans-serif;
            font-size: 0.96rem;
            color: #12212d;
        }

        .gauge-copy {
            color: var(--muted);
            font-size: 0.82rem;
            margin-top: 0.25rem;
        }

        .rhythm-strip {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.85rem;
            margin-bottom: 1.35rem;
        }

        .rhythm-card {
            padding: 1rem;
            border-radius: 20px;
            background: rgba(255, 252, 246, 0.72);
            border: 1px solid rgba(18, 33, 45, 0.08);
            box-shadow: var(--shadow);
        }

        .rhythm-label {
            color: var(--muted);
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            font-weight: 700;
        }

        .rhythm-value {
            font-family: 'Space Grotesk', sans-serif;
            font-size: 1.6rem;
            color: #12212d;
            margin-top: 0.3rem;
        }

        .rhythm-copy {
            color: var(--muted);
            font-size: 0.85rem;
        }

        .mini-list {
            display: grid;
            gap: 0.7rem;
        }

        .mini-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 0.75rem;
            padding: 0.75rem 0.9rem;
            border-radius: 16px;
            background: rgba(255, 255, 255, 0.48);
            border: 1px solid rgba(18, 33, 45, 0.06);
        }

        .mini-title {
            font-weight: 700;
            color: #12212d;
        }

        .mini-subtitle {
            color: var(--muted);
            font-size: 0.84rem;
        }

        .lane-bar {
            width: 100%;
            height: 10px;
            margin-top: 0.45rem;
            border-radius: 999px;
            background: rgba(18, 33, 45, 0.08);
            overflow: hidden;
        }

        .lane-bar-fill {
            height: 100%;
            border-radius: 999px;
            background: linear-gradient(90deg, #0f766e, #155eef, #fb923c);
        }

        .pill-status-overdue {
            color: #991b1b;
            background: #fee2e2;
        }

        .stButton > button,
        .stFormSubmitButton > button {
            border-radius: 14px;
            border: none;
            padding: 0.6rem 1rem;
            font-weight: 700;
            background: linear-gradient(135deg, #12212d, #0f766e);
            color: white;
            box-shadow: 0 12px 24px rgba(18, 33, 45, 0.16);
        }

        .stTextInput input,
        .stTextArea textarea,
        .stDateInput input,
        .stSelectbox [data-baseweb="select"] > div {
            border-radius: 14px !important;
            background: rgba(255, 255, 255, 0.72) !important;
            color: #10212e !important;
            border: 1px solid rgba(16, 33, 46, 0.22) !important;
        }

        .stTextInput input::placeholder,
        .stTextArea textarea::placeholder {
            color: #475467 !important;
            opacity: 1;
        }

        [data-testid="stWidgetLabel"] p,
        [data-testid="stWidgetLabel"] label,
        [data-testid="stWidgetLabel"] span {
            color: #10212e !important;
            font-weight: 600;
        }

        .stSelectbox [data-baseweb="select"] [data-testid="stMarkdownContainer"],
        .stDateInput [data-testid="stMarkdownContainer"] {
            color: #10212e !important;
        }

        .stAlert p,
        .stAlert div {
            color: #10212e !important;
        }

        @media (max-width: 900px) {
            .hero h1 {
                font-size: 2.2rem;
            }

            .dashboard-band,
            .rhythm-strip {
                grid-template-columns: 1fr;
            }

            .gauge-grid {
                grid-template-columns: 1fr 1fr;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_hero():
    st.markdown(
        """
        <section class="hero">
            <h1>DayAnchor</h1>
            <p>Organize personal life and clinic work in one calm command center, then use AI to shape priorities and suggest what should happen next.</p>
            <div class="hero-badges">
                <span class="hero-badge">Personal + Clinic lanes</span>
                <span class="hero-badge">AI priority suggestions</span>
                <span class="hero-badge">Focused daily execution</span>
            </div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def priority_rank(priority):
    return {"high": 0, "medium": 1, "low": 2}.get(priority, 3)


def priority_badge(priority):
    label = {"high": "High", "medium": "Medium", "low": "Low"}.get(priority, "Medium")
    return f'<span class="pill pill-priority-{priority}">{label}</span>'


def render_task_card(task, show_status=False):
    if task.get("due_date") and task["status"] != "completed" and task["due_date"] < date.today():
        due_text = f"Overdue since {task['due_date']}"
        due_class = "pill-status-overdue"
    else:
        due_text = f"Due {task['due_date']}" if task.get("due_date") else "No due date"
        due_class = "pill-status"
    description = escape(task.get("description") or "No additional notes yet.")
    meta = [
        priority_badge(task.get("priority", "medium")),
        f'<span class="pill pill-category">{escape(task.get("category", "General"))}</span>',
        f'<span class="pill {due_class}">{escape(due_text)}</span>',
    ]
    if show_status:
        meta.append(f'<span class="pill pill-status">{escape(task.get("status", "todo").title())}</span>')

    st.markdown(
        dedent(
            f"""
            <article class="task-card">
                <div class="task-topline">
                    <div class="task-title">{escape(task['title'])}</div>
                </div>
                <div>{description}</div>
                <div class="task-meta">{''.join(meta)}</div>
            </article>
            """
        ).strip(),
        unsafe_allow_html=True,
    )


def render_panel(title, subtitle):
    st.markdown(
        dedent(
            f"""
            <div class="panel-title">
                <h3>{title}</h3>
                <span>{subtitle}</span>
            </div>
            """
        ).strip(),
        unsafe_allow_html=True,
    )


def render_empty_state(message):
    st.markdown(f'<div class="empty-state">{message}</div>', unsafe_allow_html=True)


def render_dashboard_spotlight(active_tasks, overdue_tasks, due_today, completed_this_week):
    top_task = None
    if active_tasks:
        top_task = sorted(
            active_tasks,
            key=lambda task: (priority_rank(task["priority"]), task["due_date"] or date.max),
        )[0]

    if top_task:
        description = escape(top_task.get("description") or "No extra context added yet.")
        spotlight = dedent(
            f"""
            <div class="focus-card">
                <div class="focus-kicker">Primary Focus</div>
                <div class="focus-title">{escape(top_task['title'])}</div>
                <div>{description}</div>
                <div class="focus-meta">
                    <span class="focus-pill">{escape(top_task['category'])}</span>
                    <span class="focus-pill">Priority: {escape(top_task['priority'].title())}</span>
                    <span class="focus-pill">{escape('Due ' + str(top_task['due_date']) if top_task.get('due_date') else 'No due date')}</span>
                </div>
            </div>
            """
        ).strip()
    else:
        spotlight = dedent(
            """
            <div class="focus-card">
                <div class="focus-kicker">Primary Focus</div>
                <div class="focus-title">No active tasks yet</div>
                <div>Start by adding one personal task and one clinic task so the dashboard can shape the day.</div>
            </div>
            """
        ).strip()

    week_total = completed_this_week + len(active_tasks)
    done_pct = int((completed_this_week / week_total) * 100) if week_total else 0
    calm_pct = max(0, 100 - min(100, len(overdue_tasks) * 20))

    gauges = dedent(
        f"""
        <div class="gauge-grid">
            <div class="gauge-card">
                <div class="gauge-ring" style="--angle: {max(done_pct, 1) * 3.6}deg;">
                    <div class="gauge-value">{done_pct}%</div>
                </div>
                <div class="gauge-label">Weekly closure</div>
                <div class="gauge-copy">{completed_this_week} completed this week</div>
            </div>
            <div class="gauge-card">
                <div class="gauge-ring" style="--angle: {max(calm_pct, 1) * 3.6}deg;">
                    <div class="gauge-value">{len(due_today)}</div>
                </div>
                <div class="gauge-label">Due today</div>
                <div class="gauge-copy">{len(overdue_tasks)} overdue tasks need recovery</div>
            </div>
        </div>
        """
    ).strip()

    st.markdown(f'<div class="dashboard-band">{spotlight}{gauges}</div>', unsafe_allow_html=True)


def render_rhythm_strip(active_tasks, overdue_tasks, personal_tasks, clinic_tasks):
    high_priority = [task for task in active_tasks if task["priority"] == "high"]
    personal_share = int((len(personal_tasks) / len(active_tasks)) * 100) if active_tasks else 0
    clinic_share = int((len(clinic_tasks) / len(active_tasks)) * 100) if active_tasks else 0
    st.markdown(
        dedent(
            f"""
            <div class="rhythm-strip">
                <div class="rhythm-card">
                    <div class="rhythm-label">High Priority</div>
                    <div class="rhythm-value">{len(high_priority)}</div>
                    <div class="rhythm-copy">Tasks likely to shape the day</div>
                </div>
                <div class="rhythm-card">
                    <div class="rhythm-label">Overdue</div>
                    <div class="rhythm-value">{len(overdue_tasks)}</div>
                    <div class="rhythm-copy">Recovery items that need a decision</div>
                </div>
                <div class="rhythm-card">
                    <div class="rhythm-label">Personal Share</div>
                    <div class="rhythm-value">{personal_share}%</div>
                    <div class="lane-bar"><div class="lane-bar-fill" style="width: {personal_share}%;"></div></div>
                </div>
                <div class="rhythm-card">
                    <div class="rhythm-label">Clinic Share</div>
                    <div class="rhythm-value">{clinic_share}%</div>
                    <div class="lane-bar"><div class="lane-bar-fill" style="width: {clinic_share}%;"></div></div>
                </div>
            </div>
            """
        ).strip(),
        unsafe_allow_html=True,
    )


def render_upcoming_list(tasks, empty_message):
    if not tasks:
        render_empty_state(empty_message)
        return

    items = []
    for task in tasks[:4]:
        due = f"Due {task['due_date']}" if task.get("due_date") else "No due date"
        items.append(
            f"""
            <div class="mini-row">
                <div>
                    <div class="mini-title">{escape(task['title'])}</div>
                    <div class="mini-subtitle">{escape(task['category'])} • {escape(due)}</div>
                </div>
                {priority_badge(task.get('priority', 'medium'))}
            </div>
            """
        )

    st.markdown(f'<div class="mini-list">{"".join(items)}</div>', unsafe_allow_html=True)


def fetch_tasks():
    conn = get_connection()
    if not conn:
        return []
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM tasks ORDER BY due_date NULLS LAST, created_at DESC")
        tasks = cur.fetchall()
    conn.close()
    return tasks


def get_ai_suggestion(category, existing_tasks):
    if not client:
        return None

    try:
        task_context = "\n".join([f"- {task['title']}" for task in existing_tasks[:5]]) if existing_tasks else "None yet"
        prompt = f"""You are a productivity assistant helping manage daily tasks.
Based on the {category} category and existing tasks:
{task_context}

Suggest 3 concrete tasks that would be useful to add today. Keep the suggestions practical and specific.
Format as short bullet points."""
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=220,
            temperature=0.7,
        )
        return response.choices[0].message.content
    except Exception as error:
        st.warning(f"AI suggestion unavailable: {error}")
        return None


def get_ai_priority(task_title, category):
    if not client:
        return "medium"

    try:
        prompt = (
            f'Given this {category.lower()} task: "{task_title}", choose one priority: '
            "high, medium, or low. Respond with one word only."
        )
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=10,
            temperature=0.3,
        )
        priority = response.choices[0].message.content.strip().lower()
        return priority if priority in {"high", "medium", "low"} else "medium"
    except Exception:
        return "medium"


st.set_page_config(page_title="DayAnchor", layout="wide")
inject_styles()
init_db()

with st.sidebar:
    st.markdown(
        """
        <div class="sidebar-brand">
            <h2>DayAnchor</h2>
            <p>Plan the day. Split the lanes. Let AI sharpen the order.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.header("Workspace")
    page = st.radio("Select View", ["Dashboard", "Add Task", "My Tasks", "AI Suggestions"])
    st.caption("Connect Railway Postgres with DATABASE_URL/DATABASE_PUBLIC_URL, or use PGHOST, PGPORT, PGDATABASE, PGUSER, and PGPASSWORD.")
    st.caption(f"DB key detection (safe): {get_db_key_diagnostics()}")
    st.caption(f"AI status: {ai_status}")

render_hero()

if page == "Dashboard":
    all_tasks = fetch_tasks()
    active_tasks = [task for task in all_tasks if task["status"] != "completed"]
    completed_this_week = [
        task for task in all_tasks
        if task["status"] == "completed"
        and task.get("completed_date")
        and task["completed_date"] >= date.today() - timedelta(days=date.today().weekday())
    ]
    personal_tasks = sorted(
        [task for task in active_tasks if task["category"] == "Personal"],
        key=lambda task: (priority_rank(task["priority"]), task["due_date"] or date.max),
    )
    clinic_tasks = sorted(
        [task for task in active_tasks if task["category"] == "Clinic"],
        key=lambda task: (priority_rank(task["priority"]), task["due_date"] or date.max),
    )
    due_today = [task for task in active_tasks if task.get("due_date") == date.today()]
    overdue_tasks = [task for task in active_tasks if task.get("due_date") and task["due_date"] < date.today()]
    upcoming_tasks = sorted(
        [task for task in active_tasks if task.get("due_date") and task["due_date"] >= date.today()],
        key=lambda task: (task["due_date"], priority_rank(task["priority"])),
    )

    st.markdown('<p class="section-lead">A quick view of what needs attention across both halves of your day.</p>', unsafe_allow_html=True)
    metric_col1, metric_col2, metric_col3 = st.columns(3)
    metric_col1.metric("Active Tasks", len(active_tasks))
    metric_col2.metric("Due Today", len(due_today))
    metric_col3.metric("AI Ready", "On" if client else "Offline")
    render_dashboard_spotlight(active_tasks, overdue_tasks, due_today, len(completed_this_week))
    render_rhythm_strip(active_tasks, overdue_tasks, personal_tasks, clinic_tasks)

    col1, col2 = st.columns(2, gap="large")
    with col1:
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        render_panel("Personal lane", f"{len(personal_tasks)} active")
        if personal_tasks:
            for task in personal_tasks[:5]:
                render_task_card(task)
        else:
            render_empty_state("No personal tasks yet. Add one to start shaping your day.")
        st.markdown('</div>', unsafe_allow_html=True)

    with col2:
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        render_panel("Clinic lane", f"{len(clinic_tasks)} active")
        if clinic_tasks:
            for task in clinic_tasks[:5]:
                render_task_card(task)
        else:
            render_empty_state("No clinic tasks yet. Add one to keep the workstream visible.")
        st.markdown('</div>', unsafe_allow_html=True)

    lower_left, lower_right = st.columns(2, gap="large")
    with lower_left:
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        render_panel("Upcoming queue", "What is arriving next")
        render_upcoming_list(upcoming_tasks, "No upcoming deadlines yet.")
        st.markdown('</div>', unsafe_allow_html=True)

    with lower_right:
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        render_panel("Recovery lane", "Overdue items that need triage")
        render_upcoming_list(overdue_tasks, "Nothing overdue right now.")
        st.markdown('</div>', unsafe_allow_html=True)

elif page == "Add Task":
    st.markdown('<p class="section-lead">Capture work quickly, then let AI help decide how hard it should hit the day.</p>', unsafe_allow_html=True)
    info_col, form_col = st.columns([1, 1.35], gap="large")

    with info_col:
        st.markdown(
            """
            <div class="soft-card">
                <h3>Sharper intake</h3>
                <p>Use Personal for home, health, admin, and life maintenance. Use Clinic for patients, operations, follow-up, and logistics.</p>
                <p>If AI is connected, ask for a recommended priority before saving the task.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with form_col:
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        render_panel("Create task", "One clear outcome per task")

        suggested_priority = st.session_state.get("suggested_priority", "medium")
        with st.form("add_task_form"):
            col1, col2 = st.columns(2)
            with col1:
                task_title = st.text_input("Task Title", placeholder="Call pharmacy, finish charting, prep tomorrow list...")
                category = st.selectbox("Category", ["Personal", "Clinic"])
            with col2:
                priority = st.selectbox("Priority", ["low", "medium", "high"], index=["low", "medium", "high"].index(suggested_priority))
                due_date = st.date_input("Due Date", value=date.today() + timedelta(days=1))

            description = st.text_area("Description", placeholder="Context, constraints, or what done looks like.")
            action_col1, action_col2 = st.columns(2)
            with action_col1:
                submitted = st.form_submit_button("Add Task")
            with action_col2:
                asked_ai = st.form_submit_button("Suggest Priority")

        if asked_ai:
            if task_title:
                st.session_state["suggested_priority"] = get_ai_priority(task_title, category)
                st.rerun()
            st.warning("Add a task title first so AI has something to rank.")

        if submitted and task_title:
            conn = get_connection()
            if conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO tasks (title, description, category, priority, status, created_date, due_date)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (task_title, description, category, priority, "todo", date.today(), due_date),
                    )
                    conn.commit()
                conn.close()
                st.session_state["suggested_priority"] = "medium"
                st.success(f"Task added: {task_title}")
                st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

elif page == "My Tasks":
    st.markdown('<p class="section-lead">Everything active, sortable by lane, with fast completion for items you finish.</p>', unsafe_allow_html=True)
    filter_col, _ = st.columns([1, 3])
    with filter_col:
        category_filter = st.selectbox("Filter", ["All", "Personal", "Clinic"])

    all_tasks = fetch_tasks()
    tasks = [task for task in all_tasks if task["status"] != "completed"]
    if category_filter != "All":
        tasks = [task for task in tasks if task["category"] == category_filter]
    tasks = sorted(tasks, key=lambda task: (priority_rank(task["priority"]), task["due_date"] or date.max))

    if tasks:
        for task in tasks:
            left_col, right_col = st.columns([5, 1])
            with left_col:
                render_task_card(task, show_status=True)
            with right_col:
                st.write("")
                if st.button("Complete", key=f"complete_{task['id']}"):
                    conn = get_connection()
                    if conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                "UPDATE tasks SET status = %s, completed_date = %s WHERE id = %s",
                                ("completed", date.today(), task["id"]),
                            )
                            conn.commit()
                        conn.close()
                        st.success("Task completed")
                        st.rerun()
    else:
        render_empty_state("No active tasks in this view.")

elif page == "AI Suggestions":
    st.markdown('<p class="section-lead">Use the current task list as context and let AI propose the next sensible moves.</p>', unsafe_allow_html=True)
    if not client:
        st.error("OpenAI API key not configured. Add OPENAI_API_KEY in Railway Variables and redeploy.")
        st.info("If the value is wrapped in quotes, the app now strips them automatically. Ensure the variable is set on the app service, not only the database service.")
    else:
        all_tasks = fetch_tasks()
        personal_tasks = [task for task in all_tasks if task["category"] == "Personal"]
        clinic_tasks = [task for task in all_tasks if task["category"] == "Clinic"]

        col1, col2 = st.columns(2, gap="large")
        with col1:
            st.markdown('<div class="panel">', unsafe_allow_html=True)
            render_panel("Personal suggestions", "AI uses current personal tasks as context")
            if st.button("Generate Personal Ideas"):
                suggestion = get_ai_suggestion("Personal", personal_tasks)
                if suggestion:
                    st.info(suggestion)
            st.markdown('</div>', unsafe_allow_html=True)

        with col2:
            st.markdown('<div class="panel">', unsafe_allow_html=True)
            render_panel("Clinic suggestions", "AI uses current clinic tasks as context")
            if st.button("Generate Clinic Ideas"):
                suggestion = get_ai_suggestion("Clinic", clinic_tasks)
                if suggestion:
                    st.info(suggestion)
            st.markdown('</div>', unsafe_allow_html=True)

