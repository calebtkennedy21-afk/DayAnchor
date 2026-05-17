from datetime import date

import streamlit as st


def run_app(context, st_module=st):
    initialize_database = context["initialize_database"]
    load_app_settings = context["load_app_settings"]
    inject_styles = context["inject_styles"]
    render_hero = context["render_hero"]
    db_health_status = context["db_health_status"]
    configured_database_env_names = context["configured_database_env_names"]
    ai_enabled = context["ai_enabled"]
    ai_model_name = context["ai_model_name"]
    seed_sample_tasks = context["seed_sample_tasks"]
    load_tasks = context["load_tasks"]
    load_personal_goals = context["load_personal_goals"]
    personal_goal_dashboard_summary = context["personal_goal_dashboard_summary"]
    load_surgical_cases = context["load_surgical_cases"]
    load_protocol_documents = context["load_protocol_documents"]
    priority_rank = context["priority_rank"]
    format_due = context["format_due"]
    status_label = context["status_label"]
    render_page_banner = context["render_page_banner"]
    overview_runtime_settings = context["overview_runtime_settings"]
    render_overview_control_tower = context["render_overview_control_tower"]
    render_add_task_panel = context["render_add_task_panel"]
    render_personal_focus_panel = context["render_personal_focus_panel"]
    render_personal_goals_panel = context["render_personal_goals_panel"]
    render_personal_goal_reminders_panel = context["render_personal_goal_reminders_panel"]
    render_personal_goal_review_panel = context["render_personal_goal_review_panel"]
    render_personal_goal_history_panel = context["render_personal_goal_history_panel"]
    render_clinic_command_center = context["render_clinic_command_center"]
    render_surgical_cases_panel = context["render_surgical_cases_panel"]
    render_task_calendar_panel = context["render_task_calendar_panel"]
    render_schedule_builder_panel = context["render_schedule_builder_panel"]
    render_task_list_panel = context["render_task_list_panel"]
    render_ai_panel = context["render_ai_panel"]
    render_review_command_panel = context["render_review_command_panel"]
    render_notifications_panel = context["render_notifications_panel"]
    render_settings_panel = context["render_settings_panel"]
    render_analytics_panel = context["render_analytics_panel"]
    render_daily_review_panel = context["render_daily_review_panel"]
    render_page_footer = context["render_page_footer"]
    render_hero_timeline = context.get("render_hero_timeline")
    render_clinic_overview_panel = context.get("render_clinic_overview_panel")
    render_personal_overview_panel = context.get("render_personal_overview_panel")
    render_personal_quick_capture = context["render_personal_quick_capture"]
    render_personal_one_thing = context["render_personal_one_thing"]

    initialize_database()
    app_settings = load_app_settings()

    inject_styles()
    render_hero()

    personal_goals = load_personal_goals()
    personal_goal_summary = personal_goal_dashboard_summary(personal_goals)

    with st_module.sidebar:
        st_module.markdown(
            """
            <div style="padding: 1rem 1rem 1.15rem; margin-bottom: 1rem; border-radius: 20px; background: linear-gradient(135deg, rgba(15, 118, 110, 0.28), rgba(21, 94, 239, 0.24)); border: 1px solid rgba(255, 255, 255, 0.1);">
                <h2 style="margin: 0; color: white; font-size: 1.2rem;">DayAnchor</h2>
                <p style="margin: 0.45rem 0 0; color: rgba(248, 250, 252, 0.82); font-size: 0.9rem;">Task capture with Postgres persistence and optional AI planning.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if context["db_enabled"]():
            source = context["DB_CANDIDATE_SOURCE"] or "database URL"
            st_module.caption(f"Connected to Postgres via {source}.")
            st_module.caption("Tasks persist across restarts and deployments.")
        elif context["DB_ERROR"]:
            st_module.caption("Database connection failed.")
            st_module.caption("Using session-only fallback until DB is reachable.")
        else:
            st_module.caption("No DATABASE_URL or DATABASE_PUBLIC_URL found.")
            st_module.caption("Running in session-only fallback mode.")

        if personal_goal_summary["streak_leader"]:
            leader = personal_goal_summary["streak_leader"]
            st_module.markdown(
                f"<div style='margin:0.9rem 0; padding:0.8rem 0.9rem; border-radius:16px; background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.12);'>"
                f"<div style='color: rgba(248,250,252,0.7); font-size:0.78rem; text-transform: uppercase; letter-spacing:0.08em;'>Personal streak</div>"
                f"<div style='color: white; font-weight:700; font-size:1rem; margin-top:0.15rem;'>{leader.get('title')}</div>"
                f"<div style='color: rgba(248,250,252,0.82); font-size:0.88rem;'>Current: {int(leader.get('current_streak') or 0)} days · This week: {personal_goal_summary['week_checkins']} check-ins</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
        else:
            st_module.caption("No active personal streak yet.")

        st_module.markdown("---")
        st_module.markdown("### Navigation")
        current_page = st_module.radio(
            "Go to",
            ["Overview", "Personal", "Clinic", "Cases", "Schedule", "Anatomy", "AI", "Analytics", "Notifications", "Daily Review", "Settings"],
            label_visibility="collapsed",
        )

        st_module.markdown("---")
        st_module.markdown("### Data Controls")
        health_state, health_message = db_health_status()
        detected_names = configured_database_env_names()
        if detected_names:
            st_module.caption(f"Detected DB vars: {', '.join(detected_names)}")
        else:
            st_module.caption("Detected DB vars: none")
            st_module.caption("Tip: ensure the web app service has DATABASE_URL or DATABASE_PUBLIC_URL set in Railway.")
        if health_state == "ok":
            st_module.success(f"DB Health: {health_message}")
        elif health_state == "error":
            st_module.warning(f"DB Health: {health_message}")
        else:
            st_module.info(f"DB Health: {health_message}")

        if st_module.button("Seed Sample Tasks", use_container_width=True):
            seed_sample_tasks()
            st_module.success("Sample tasks added.")
            st_module.rerun()

        st_module.markdown("---")
        st_module.markdown("### View Controls")
        search_query = st_module.text_input("Search tasks", placeholder="Title or description")
        category_filter = st_module.multiselect("Category", ["Personal", "Clinic"], default=["Personal", "Clinic"])
        priority_filter = st_module.multiselect("Priority", ["high", "medium", "low"], default=["high", "medium", "low"])
        status_filter = st_module.multiselect(
            "Status",
            ["todo", "in_progress", "blocked", "completed"],
            default=["todo", "in_progress", "blocked", "completed"],
            format_func=status_label,
        )
        scheduled_only = st_module.checkbox("Scheduled tasks only", value=False)
        timeline_days = st_module.slider(
            "Timeline window (days)",
            min_value=3,
            max_value=21,
            value=int(app_settings.get("timeline_days", 7)),
        )

        st_module.markdown("---")
        st_module.markdown("### AI")
        if ai_enabled():
            st_module.success(f"AI ready ({ai_model_name()})")
        else:
            st_module.info("AI disabled. Set OPENAI_API_KEY to enable.")

    st_module.markdown('<p class="section-lead">Navigate by lane and workflow area from the sidebar.</p>', unsafe_allow_html=True)

    tasks = load_tasks()
    surgical_cases = load_surgical_cases()
    protocol_documents = load_protocol_documents()
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

    if len(filtered_tasks) != len(tasks):
        st_module.caption(f"Showing {len(filtered_tasks)} of {len(tasks)} tasks based on current filters.")

    if current_page == "Overview":
        render_page_banner("overview", "Control Tower", "High-level triage, fast capture, and the day’s most important work.")
        overview_settings = st_module.session_state.get("overview_page_settings", overview_runtime_settings(app_settings))
        st_module.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
        render_overview_control_tower(tasks, active_tasks, completed_today_all, personal_tasks, clinic_tasks, scheduled_tasks, app_settings, overview_settings, panel_key="overview_page")
        st_module.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
        render_add_task_panel("overview_add_task", app_settings, default_category="Clinic")
        st_module.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
        render_personal_focus_panel(personal_tasks, active_tasks, app_settings, panel_key="overview_personal")
        st_module.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
        render_clinic_command_center(clinic_tasks, active_tasks, app_settings, panel_key="overview_clinic")
        st_module.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
        render_task_calendar_panel(tasks, "overview_tasks", "Task Calendar", "Mixed load across tasks, due dates, and completions", app_settings=app_settings)
    elif current_page == "Personal":
        render_page_banner("personal", "Personal Focus", "Keep your own work clear, bounded, and visible.")
        render_personal_quick_capture("personal_quick_capture", app_settings)
        st_module.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
        render_personal_goals_panel(personal_goals, panel_key="personal_goals")
        st_module.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
        personal_goal_cols = st_module.columns(2, gap="large")
        with personal_goal_cols[0]:
            render_personal_goal_reminders_panel(personal_goals, panel_key="personal_goal_reminders")
        with personal_goal_cols[1]:
            render_personal_goal_review_panel(personal_goals, panel_key="personal_goal_review")
        st_module.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
        render_personal_goal_history_panel(personal_goals, panel_key="personal_goal_history")
        st_module.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
        render_personal_one_thing(personal_tasks, "personal_one_thing")
        st_module.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
        render_task_list_panel("Personal Tasks", "Work that belongs outside clinic", personal_tasks, "personal_task", "No personal tasks match the current filters.")
    elif current_page == "Clinic":
        render_page_banner("clinic", "Clinic Command Center", "Track outpatient load, follow-up flow, and clinic-first work.")
        render_add_task_panel("clinic_add_task", app_settings, default_category="Clinic")
        st_module.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
        render_clinic_command_center(clinic_tasks, active_tasks, app_settings, panel_key="clinic_page")
    elif current_page == "Cases":
        render_page_banner("clinic", "Surgical Cases", "Non-PHI case tracking with protocol support and OR cadence.")
        render_surgical_cases_panel(surgical_cases, protocol_documents, app_settings, panel_key="cases_page")
    elif current_page == "Schedule":
        render_page_banner("schedule", "Schedule Builder", "Place work into realistic blocks and keep the week coherent.")
        render_schedule_builder_panel(active_tasks, app_settings, panel_key="schedule_page")
        st_module.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
        render_task_calendar_panel(tasks, "schedule_tasks", "Schedule Calendar", "Mixed load across tasks, due dates, and completions", app_settings=app_settings)
    elif current_page == "Anatomy":
        render_page_banner("clinic", "MSK Anatomy", "Foot and ankle emphasis with extension to the knee.")
        context["render_msk_anatomy_panel"](surgical_cases, protocol_documents, panel_key="anatomy_page")
    elif current_page == "AI":
        render_page_banner("ai", "AI Workbench", "Planner, scheduler, and review in one place.")
        render_ai_panel(tasks, active_tasks, panel_key="ai_page")
    elif current_page == "Analytics":
        render_page_banner("overview", "Analytics", "See load, status balance, and schedule pressure at a glance.")
        render_analytics_panel(tasks, active_tasks, scheduled_tasks, panel_key="analytics_page")
    elif current_page == "Notifications":
        render_page_banner("overview", "Notifications", "A focused inbox for reminders and follow-ups.")
        render_notifications_panel(tasks, active_tasks, panel_key="notifications_page")
    elif current_page == "Daily Review":
        render_page_banner("review", "Daily Review", "Close the loop on today and draft tomorrow.")
        render_daily_review_panel(tasks, active_tasks, completed_today_all, app_settings, panel_key="review_page")
    elif current_page == "Settings":
        render_page_banner("overview", "Settings", "Tune defaults, cadence, and app behavior.")
        render_settings_panel(app_settings, panel_key="settings_page")
    else:
        render_page_banner("overview", "Control Tower", "High-level triage, fast capture, and the day’s most important work.")
        render_overview_control_tower(tasks, active_tasks, completed_today_all, personal_tasks, clinic_tasks, scheduled_tasks, app_settings, st_module.session_state.get("overview_page_settings", overview_runtime_settings(app_settings)), panel_key="overview_page")

    render_page_footer()
