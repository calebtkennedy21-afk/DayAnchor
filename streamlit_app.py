import streamlit as st
from datetime import date, datetime, timedelta
from db import get_connection, init_db
import os
from openai import OpenAI

# Initialize OpenAI client
api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=api_key) if api_key else None

st.set_page_config(page_title="DayAnchor", page_icon="⛵", layout="wide")

st.title("⛵ DayAnchor")
st.markdown("Your daily task companion for personal and clinic responsibilities with AI-powered insights")

# Initialize database
init_db()

# Sidebar for navigation
st.sidebar.header("Navigation")
page = st.sidebar.radio("Select View", ["Dashboard", "Add Task", "My Tasks", "AI Suggestions"])

# Helper functions
def get_ai_suggestion(category, existing_tasks):
    """Get AI-powered task suggestions using OpenAI"""
    if not client:
        return None
    
    try:
        task_context = "\n".join([f"- {t['title']}" for t in existing_tasks[:5]]) if existing_tasks else "None yet"
        
        prompt = f"""You are a productivity assistant helping manage daily tasks. 
Based on the {category} category and existing tasks:
{task_context}

Suggest 2-3 new tasks that would be beneficial to add today. Focus on productivity and health.
Format as a numbered list with task titles only."""
        
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.7
        )
        return response.choices[0].message.content
    except Exception as e:
        st.warning(f"AI suggestion unavailable: {str(e)}")
        return None

def get_ai_priority(task_title, category):
    """Get AI-recommended priority for a task"""
    if not client:
        return "medium"
    
    try:
        prompt = f"""Given a {category} task: "{task_title}", what priority level (high, medium, low) would you recommend?
Answer with ONLY the priority level word."""
        
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=10,
            temperature=0.5
        )
        priority = response.choices[0].message.content.strip().lower()
        return priority if priority in ["high", "medium", "low"] else "medium"
    except:
        return "medium"

# PAGE 1: Dashboard
if page == "Dashboard":
    col1, col2 = st.columns(2)
    
    conn = get_connection()
    if conn:
        with conn.cursor() as cur:
            # Personal tasks
            cur.execute(
                "SELECT * FROM tasks WHERE category = 'Personal' AND status != 'completed' ORDER BY priority DESC"
            )
            personal_tasks = cur.fetchall()
            
            # Clinic tasks
            cur.execute(
                "SELECT * FROM tasks WHERE category = 'Clinic' AND status != 'completed' ORDER BY priority DESC"
            )
            clinic_tasks = cur.fetchall()
        conn.close()
        
        with col1:
            st.subheader("📋 Personal Tasks")
            if personal_tasks:
                for task in personal_tasks:
                    priority_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(task['priority'], "🟡")
                    st.write(f"{priority_emoji} **{task['title']}**")
                    if task['due_date']:
                        st.caption(f"Due: {task['due_date']}")
                st.metric("Active Tasks", len(personal_tasks))
            else:
                st.info("No active personal tasks")
        
        with col2:
            st.subheader("🏥 Clinic Tasks")
            if clinic_tasks:
                for task in clinic_tasks:
                    priority_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(task['priority'], "🟡")
                    st.write(f"{priority_emoji} **{task['title']}**")
                    if task['due_date']:
                        st.caption(f"Due: {task['due_date']}")
                st.metric("Active Tasks", len(clinic_tasks))
            else:
                st.info("No active clinic tasks")

# PAGE 2: Add Task
elif page == "Add Task":
    st.subheader("➕ Create New Task")
    
    with st.form("add_task_form"):
        col1, col2 = st.columns(2)
        
        with col1:
            task_title = st.text_input("Task Title", placeholder="Enter task title")
            category = st.selectbox("Category", ["Personal", "Clinic"])
        
        with col2:
            priority = st.selectbox("Priority", ["low", "medium", "high"])
            due_date = st.date_input("Due Date", value=date.today() + timedelta(days=1))
        
        description = st.text_area("Description (optional)", placeholder="Add more details...")
        
        col1, col2, col3 = st.columns([1, 1, 2])
        
        with col1:
            submitted = st.form_submit_button("✅ Add Task")
        
        with col2:
            if st.form_submit_button("🤖 AI Priority"):
                if task_title:
                    priority = get_ai_priority(task_title, category)
                    st.rerun()
        
        if submitted and task_title:
            conn = get_connection()
            if conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO tasks (title, description, category, priority, status, created_date, due_date)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        (task_title, description, category, priority, "todo", date.today(), due_date)
                    )
                    conn.commit()
                st.success(f"✅ Task '{task_title}' added successfully!")
                conn.close()
                st.rerun()

# PAGE 3: My Tasks
elif page == "My Tasks":
    st.subheader("📝 My Tasks")
    
    col1, col2 = st.columns([3, 1])
    with col2:
        category_filter = st.selectbox("Filter by Category", ["All", "Personal", "Clinic"])
    
    conn = get_connection()
    if conn:
        with conn.cursor() as cur:
            if category_filter == "All":
                cur.execute("SELECT * FROM tasks WHERE status != 'completed' ORDER BY priority DESC, due_date")
            else:
                cur.execute(
                    "SELECT * FROM tasks WHERE category = %s AND status != 'completed' ORDER BY priority DESC, due_date",
                    (category_filter,)
                )
            tasks = cur.fetchall()
        conn.close()
        
        if tasks:
            for task in tasks:
                with st.container():
                    col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
                    
                    priority_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(task['priority'], "🟡")
                    category_emoji = "📋" if task['category'] == "Personal" else "🏥"
                    
                    with col1:
                        st.write(f"{priority_emoji} {category_emoji} **{task['title']}**")
                        if task['description']:
                            st.caption(task['description'])
                    
                    with col2:
                        if task['due_date']:
                            st.caption(f"📅 {task['due_date']}")
                    
                    with col3:
                        st.caption(f"Status: {task['status']}")
                    
                    with col4:
                        if st.button("✓ Complete", key=f"complete_{task['id']}"):
                            conn = get_connection()
                            if conn:
                                with conn.cursor() as cur:
                                    cur.execute(
                                        "UPDATE tasks SET status = %s, completed_date = %s WHERE id = %s",
                                        ("completed", date.today(), task['id'])
                                    )
                                    conn.commit()
                                st.success("Task completed!")
                                conn.close()
                                st.rerun()
                    
                    st.divider()
        else:
            st.info("No active tasks in this category")

# PAGE 4: AI Suggestions
elif page == "AI Suggestions":
    st.subheader("🤖 AI-Powered Task Suggestions")
    
    if not client:
        st.error("⚠️ OpenAI API key not configured. Set OPENAI_API_KEY environment variable.")
    else:
        col1, col2 = st.columns(2)
        
        with col1:
            st.write("### 📋 Personal Suggestions")
            if st.button("Get Personal Task Ideas"):
                conn = get_connection()
                if conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT * FROM tasks WHERE category = 'Personal' LIMIT 5")
                        personal_tasks = cur.fetchall()
                    conn.close()
                    
                    suggestion = get_ai_suggestion("Personal", personal_tasks)
                    if suggestion:
                        st.info(suggestion)
        
        with col2:
            st.write("### 🏥 Clinic Suggestions")
            if st.button("Get Clinic Task Ideas"):
                conn = get_connection()
                if conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT * FROM tasks WHERE category = 'Clinic' LIMIT 5")
                        clinic_tasks = cur.fetchall()
                    conn.close()
                    
                    suggestion = get_ai_suggestion("Clinic", clinic_tasks)
                    if suggestion:
                        st.info(suggestion)

