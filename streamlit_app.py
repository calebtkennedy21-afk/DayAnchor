import os
from datetime import date, timedelta

import streamlit as st
from openai import OpenAI

from db import get_connection, init_db


api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=api_key) if api_key else None


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
        }

        @media (max-width: 900px) {
            .hero h1 {
                font-size: 2.2rem;
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
    due_text = f"Due {task['due_date']}" if task.get("due_date") else "No due date"
    description = task.get("description") or "No additional notes yet."
    meta = [
        priority_badge(task.get("priority", "medium")),
        f'<span class="pill pill-category">{task.get("category", "General")}</span>',
        f'<span class="pill pill-status">{due_text}</span>',
    ]
    if show_status:
        meta.append(f'<span class="pill pill-status">{task.get("status", "todo").title()}</span>')

    st.markdown(
        f"""
        <article class="task-card">
            <div class="task-topline">
                <div class="task-title">{task['title']}</div>
            </div>
            <div>{description}</div>
            <div class="task-meta">{''.join(meta)}</div>
        </article>
        """,
        unsafe_allow_html=True,
    )


def render_panel(title, subtitle):
    st.markdown(
        f"""
        <div class="panel-title">
            <h3>{title}</h3>
            <span>{subtitle}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_empty_state(message):
    st.markdown(f'<div class="empty-state">{message}</div>', unsafe_allow_html=True)


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


st.set_page_config(page_title="DayAnchor", page_icon="⛵", layout="wide")
inject_styles()
init_db()

with st.sidebar:
    st.markdown(
        """
        <div class="sidebar-brand">
            <h2>⛵ DayAnchor</h2>
            <p>Plan the day. Split the lanes. Let AI sharpen the order.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.header("Workspace")
    page = st.radio("Select View", ["Dashboard", "Add Task", "My Tasks", "AI Suggestions"])
    st.caption("Connect Railway Postgres with PGHOST, PGPORT, PGDATABASE, PGUSER, and PGPASSWORD.")

render_hero()

if page == "Dashboard":
    all_tasks = fetch_tasks()
    active_tasks = [task for task in all_tasks if task["status"] != "completed"]
    personal_tasks = sorted(
        [task for task in active_tasks if task["category"] == "Personal"],
        key=lambda task: (priority_rank(task["priority"]), task["due_date"] or date.max),
    )
    clinic_tasks = sorted(
        [task for task in active_tasks if task["category"] == "Clinic"],
        key=lambda task: (priority_rank(task["priority"]), task["due_date"] or date.max),
    )
    due_today = [task for task in active_tasks if task.get("due_date") == date.today()]

    st.markdown('<p class="section-lead">A quick view of what needs attention across both halves of your day.</p>', unsafe_allow_html=True)
    metric_col1, metric_col2, metric_col3 = st.columns(3)
    metric_col1.metric("Active Tasks", len(active_tasks))
    metric_col2.metric("Due Today", len(due_today))
    metric_col3.metric("AI Ready", "On" if client else "Offline")

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
        st.error("OpenAI API key not configured. Add OPENAI_API_KEY to enable AI suggestions.")
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

