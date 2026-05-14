from datetime import date, timedelta

import streamlit as st


st.set_page_config(page_title="DayAnchor", page_icon="⛵", layout="wide")


if "tasks" not in st.session_state:
    st.session_state.tasks = []


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


def task_matches(task, lane):
    return task["category"] == lane and task["status"] != "completed"


def add_task(title, description, category, priority, due_date):
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
            "completed_date": None,
        }
    )


def delete_task(task_id):
    st.session_state.tasks = [task for task in st.session_state.tasks if task["id"] != task_id]


def complete_task(task_id):
    for task in st.session_state.tasks:
        if task["id"] == task_id:
            task["status"] = "completed"
            task["completed_date"] = date.today()
            return


def render_task_card(task):
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
        </div>''',
        unsafe_allow_html=True,
    )
    cols = st.columns(2)
    with cols[0]:
        if task["status"] != "completed" and st.button("Mark complete", key=f"complete_{task['id']}"):
            complete_task(task["id"])
            st.rerun()
    with cols[1]:
        if st.button("Delete", key=f"delete_{task['id']}"):
            delete_task(task["id"])
            st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)


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
    st.caption("This version keeps everything in session memory for now.")
    st.caption("You can restore persistence later once the core flow is settled.")

st.markdown('<p class="section-lead">Capture work quickly and split it between your personal lane and clinic lane.</p>', unsafe_allow_html=True)

active_tasks = [task for task in st.session_state.tasks if task["status"] != "completed"]
completed_tasks = [task for task in st.session_state.tasks if task["status"] == "completed"]
personal_tasks = sorted([task for task in active_tasks if task["category"] == "Personal"], key=lambda task: (priority_rank(task["priority"]), task["due_date"] or date.max))
clinic_tasks = sorted([task for task in active_tasks if task["category"] == "Clinic"], key=lambda task: (priority_rank(task["priority"]), task["due_date"] or date.max))
due_today = [task for task in active_tasks if task.get("due_date") == date.today()]
overdue_tasks = [task for task in active_tasks if task.get("due_date") and task["due_date"] < date.today()]

metric_col1, metric_col2, metric_col3 = st.columns(3)
metric_col1.metric("Active Tasks", len(active_tasks))
metric_col2.metric("Due Today", len(due_today))
metric_col3.metric("Completed", len(completed_tasks))

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
        submitted = st.form_submit_button("Add task")

    if submitted:
        if not title.strip():
            st.warning("Add a task title first.")
        else:
            add_task(title, description, category, priority, due_date)
            st.success("Task added.")
            st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

with right:
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Today</h3><span>What needs attention now</span></div>', unsafe_allow_html=True)
    if due_today:
        for task in sorted(due_today, key=lambda item: priority_rank(item["priority"])):
            render_task_card(task)
    else:
        st.markdown('<div class="empty-state">No tasks due today.</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

col1, col2 = st.columns(2, gap="large")
with col1:
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Personal lane</h3><span>Active tasks</span></div>', unsafe_allow_html=True)
    if personal_tasks:
        for task in personal_tasks:
            render_task_card(task)
    else:
        st.markdown('<div class="empty-state">No personal tasks yet.</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

with col2:
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Clinic lane</h3><span>Active tasks</span></div>', unsafe_allow_html=True)
    if clinic_tasks:
        for task in clinic_tasks:
            render_task_card(task)
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
            render_task_card(task)
    else:
        st.markdown('<div class="empty-state">Nothing overdue right now.</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

with overdue_col2:
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title"><h3>Completed</h3><span>Finished work</span></div>', unsafe_allow_html=True)
    if completed_tasks:
        for task in completed_tasks:
            render_task_card(task)
    else:
        st.markdown('<div class="empty-state">No completed tasks yet.</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)
