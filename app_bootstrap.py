from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import streamlit as st


MOUNTAIN_TIMEZONE = ZoneInfo("America/Denver")


def mountain_today():
    return datetime.now(MOUNTAIN_TIMEZONE).date()


def summarize_schedule_conflicts(scheduled_tasks, fallback_minutes=60, daily_capacity_minutes=480):
    conflicts = []
    over_capacity_days = []
    by_day = {}

    for task in scheduled_tasks:
        scheduled_date = task.get("scheduled_date")
        scheduled_time = task.get("scheduled_time")
        if not scheduled_date or not scheduled_time:
            continue

        minutes_raw = task.get("scheduled_minutes")
        try:
            minutes_value = int(minutes_raw) if minutes_raw is not None else int(fallback_minutes)
        except (TypeError, ValueError):
            minutes_value = int(fallback_minutes)
        if minutes_value <= 0:
            minutes_value = int(fallback_minutes)

        start_dt = datetime.combine(scheduled_date, scheduled_time)
        end_dt = start_dt + timedelta(minutes=minutes_value)
        by_day.setdefault(scheduled_date, []).append((start_dt, end_dt, task, minutes_value))

    for day_value, entries in by_day.items():
        entries.sort(key=lambda item: item[0])
        total_minutes = sum(item[3] for item in entries)
        if total_minutes > int(daily_capacity_minutes):
            over_capacity_days.append((day_value, total_minutes))

        for idx in range(1, len(entries)):
            previous_entry = entries[idx - 1]
            current_entry = entries[idx]
            if current_entry[0] < previous_entry[1]:
                conflicts.append((day_value, previous_entry[2], current_entry[2]))

    return conflicts, over_capacity_days


def run_app(context, st_module=st):
    initialize_database = context["initialize_database"]
    load_app_settings = context["load_app_settings"]
    save_app_settings = context["save_app_settings"]
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
    render_physical_therapy_protocols_panel = context["render_physical_therapy_protocols_panel"]
    render_task_calendar_panel = context["render_task_calendar_panel"]
    render_schedule_builder_panel = context["render_schedule_builder_panel"]
    render_family_schedule_panel = context["render_family_schedule_panel"]
    render_task_list_panel = context["render_task_list_panel"]
    render_ai_panel = context["render_ai_panel"]
    render_review_command_panel = context["render_review_command_panel"]
    render_notifications_panel = context["render_notifications_panel"]
    render_ma_lead_panel = context["render_ma_lead_panel"]
    render_settings_panel = context["render_settings_panel"]
    render_analytics_panel = context["render_analytics_panel"]
    render_daily_review_panel = context["render_daily_review_panel"]
    render_morning_ritual_panel = context["render_morning_ritual_panel"]
    render_page_footer = context["render_page_footer"]
    render_hero_timeline = context.get("render_hero_timeline")
    render_clinic_overview_panel = context.get("render_clinic_overview_panel")
    render_personal_overview_panel = context.get("render_personal_overview_panel")
    render_personal_quick_capture = context["render_personal_quick_capture"]
    render_personal_one_thing = context["render_personal_one_thing"]
    fetch_health_news = context.get("fetch_health_news")
    summarize_news_with_ai = context.get("summarize_news_with_ai")
    render_morning_digest_panel = context.get("render_morning_digest_panel")
    render_full_news_page = context.get("render_full_news_page")
    add_task = context["add_task"]
    news_manual_refresh_requested = False

    def render_saved_notes_panel(title, subtitle, setting_key, updated_key, panel_key, help_text):
        nonlocal app_settings
        st_module.markdown('<div class="panel">', unsafe_allow_html=True)
        st_module.markdown(f'<div class="panel-title"><h3>{title}</h3><span>{subtitle}</span></div>', unsafe_allow_html=True)
        notes_state_key = f"{panel_key}_notes_input"
        if notes_state_key not in st_module.session_state:
            st_module.session_state[notes_state_key] = app_settings.get(setting_key, "")

        st_module.text_area(
            "",
            key=notes_state_key,
            height=220,
            placeholder=help_text,
            label_visibility="collapsed",
        )
        save_col, reset_col = st_module.columns([1, 1])
        with save_col:
            if st_module.button("Save notes", key=f"{panel_key}_save_notes", type="secondary", use_container_width=True):
                app_settings = save_app_settings(
                    {
                        **app_settings,
                        setting_key: st_module.session_state.get(notes_state_key, "").strip(),
                        updated_key: datetime.now(MOUNTAIN_TIMEZONE).strftime("%Y-%m-%d %H:%M MT"),
                    }
                )
                st_module.success("Notes saved.")
                st_module.rerun()
        with reset_col:
            if st_module.button("Reset to saved", key=f"{panel_key}_reset_notes", use_container_width=True):
                st_module.session_state[notes_state_key] = app_settings.get(setting_key, "")
                st_module.rerun()

        updated_at = app_settings.get(updated_key)
        if updated_at:
            st_module.caption(f"Last saved: {updated_at}")
        st_module.markdown('</div>', unsafe_allow_html=True)

    def render_quick_reminder_capture(panel_key="overview_quick_reminder"):
        nonlocal app_settings
        reminders = list(app_settings.get("quick_reminders") or [])
        active_reminders = [
            item
            for item in reminders
            if isinstance(item, dict) and str(item.get("status") or "active").lower() == "active"
        ]
        active_count = len(active_reminders)

        st_module.markdown('<div class="panel">', unsafe_allow_html=True)
        st_module.markdown('<div class="panel-title"><h3>Quick Reminder</h3><span>Capture it now without creating a task</span></div>', unsafe_allow_html=True)
        st_module.caption(f"Active reminders: {active_count}")

        with st_module.form(f"{panel_key}_form", clear_on_submit=True):
            reminder_text = st_module.text_input("Reminder", placeholder="What do you want to remember?")
            reminder_cols = st_module.columns(3)
            with reminder_cols[0]:
                reminder_category = st_module.selectbox("Category", ["General", "Personal", "Family", "Clinic"], index=0)
            with reminder_cols[1]:
                has_date = st_module.checkbox("Set date", value=False)
                reminder_date = st_module.date_input("Remind on", value=mountain_today(), disabled=not has_date, key=f"{panel_key}_date")
            with reminder_cols[2]:
                has_time = st_module.checkbox("Set time", value=False, disabled=not has_date)
                reminder_time = st_module.time_input("At", disabled=(not has_date) or (not has_time), key=f"{panel_key}_time")

            reminder_notes = st_module.text_area("Details (optional)", height=70, placeholder="Optional context")
            submitted = st_module.form_submit_button("Save reminder", type="primary")

        if submitted:
            if not reminder_text.strip():
                st_module.warning("Add reminder text before saving.")
            else:
                now_iso = datetime.now(MOUNTAIN_TIMEZONE).isoformat(timespec="seconds")
                reminders.append(
                    {
                        "reminder_id": f"quick_{datetime.now(MOUNTAIN_TIMEZONE).strftime('%Y%m%d%H%M%S%f')}",
                        "text": reminder_text.strip(),
                        "category": reminder_category,
                        "notes": reminder_notes.strip(),
                        "remind_date": reminder_date if has_date else None,
                        "remind_time": reminder_time if has_date and has_time else None,
                        "status": "active",
                        "created_at": now_iso,
                        "updated_at": now_iso,
                    }
                )
                app_settings = save_app_settings(
                    {
                        **app_settings,
                        "quick_reminders": reminders,
                    }
                )
                st_module.success("Reminder saved.")
                st_module.rerun()

        if active_reminders:
            st_module.markdown('<div class="panel-title" style="margin-top:0.8rem;"><h3>Active reminders</h3><span>Live preview from your reminders inbox</span></div>', unsafe_allow_html=True)
            for reminder in active_reminders[:6]:
                reminder_text_value = str(reminder.get("text") or "").strip() or "Untitled reminder"
                reminder_category = str(reminder.get("category") or "General").strip() or "General"
                remind_date = reminder.get("remind_date")
                remind_time = reminder.get("remind_time")
                when_label = "Anytime"
                if remind_date and remind_time and hasattr(remind_date, "strftime") and hasattr(remind_time, "strftime"):
                    when_label = f"{remind_date.strftime('%b %d')} at {remind_time.strftime('%I:%M %p').lstrip('0')}"
                elif remind_date and hasattr(remind_date, "strftime"):
                    when_label = remind_date.strftime("%b %d")
                elif remind_date:
                    when_label = str(remind_date)

                st_module.markdown(
                    f"- <strong>{reminder_text_value}</strong> · {reminder_category} · {when_label}",
                    unsafe_allow_html=True,
                )
        else:
            st_module.caption("No active reminders yet. Add one above to see it here instantly.")

        st_module.markdown('</div>', unsafe_allow_html=True)

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
        personal_pages = ["Morning Ritual", "Personal", "Schedule", "Daily Review", "Notifications"]
        family_pages = ["Family Schedule"]
        clinical_pages = ["Clinic", "Cases", "Anatomy", "Physical Therapy Protocols", "MA Lead"]
        additional_pages = ["News", "AI", "Analytics", "Settings"]
        all_pages = ["Overview"] + personal_pages + family_pages + clinical_pages + additional_pages

        current_page = st_module.session_state.get("current_page", "Overview")
        if current_page not in all_pages:
            current_page = "Overview"
            st_module.session_state["current_page"] = current_page

        def render_nav_button(label, page_name, key_suffix):
            button_type = "primary" if current_page == page_name else "secondary"
            if st_module.button(label, key=f"sidebar_nav_{key_suffix}", use_container_width=True, type=button_type):
                st_module.session_state["current_page"] = page_name
                st_module.rerun()

        render_nav_button("Overview", "Overview", "overview")

        with st_module.expander("Personal", expanded=current_page in personal_pages):
            for page in personal_pages:
                render_nav_button(page, page, page.lower().replace(" ", "_"))

        with st_module.expander("Family", expanded=current_page in family_pages):
            for page in family_pages:
                render_nav_button(page, page, page.lower().replace(" ", "_"))

        with st_module.expander("Clinical", expanded=current_page in clinical_pages):
            for page in clinical_pages:
                render_nav_button(page, page, page.lower().replace(" ", "_"))

        with st_module.expander("More", expanded=current_page in additional_pages):
            for page in additional_pages:
                render_nav_button(page, page, page.lower().replace(" ", "_"))

        current_page = st_module.session_state.get("current_page", "Overview")

        st_module.markdown("---")
        with st_module.expander("Quick capture", expanded=False):
            with st_module.form("sidebar_quick_capture", clear_on_submit=True):
                quick_title = st_module.text_input("Task title", placeholder="What needs to get done?")
                quick_category = st_module.selectbox("Category", ["Personal", "Clinic"])
                quick_priority = st_module.selectbox("Priority", ["high", "medium", "low"], index=1)
                quick_due = st_module.date_input("Due date", value=mountain_today())
                quick_submit = st_module.form_submit_button("Add task", type="primary")
            if quick_submit:
                if not quick_title.strip():
                    st_module.warning("Add a task title first.")
                else:
                    context["add_task"](quick_title.strip(), "", quick_category, quick_priority, quick_due)
                    st_module.success("Quick task added.")
                    st_module.rerun()

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
        view_search_key = "view_search_query"
        view_category_key = "view_category_filter"
        view_priority_key = "view_priority_filter"
        view_status_key = "view_status_filter"
        view_scheduled_only_key = "view_scheduled_only"
        view_preset_pending_key = "view_preset_pending"

        if view_search_key not in st_module.session_state:
            st_module.session_state[view_search_key] = ""
        if view_category_key not in st_module.session_state:
            st_module.session_state[view_category_key] = ["Personal", "Clinic"]
        if view_priority_key not in st_module.session_state:
            st_module.session_state[view_priority_key] = ["high", "medium", "low"]
        if view_status_key not in st_module.session_state:
            st_module.session_state[view_status_key] = ["todo", "in_progress", "blocked", "completed"]
        if view_scheduled_only_key not in st_module.session_state:
            st_module.session_state[view_scheduled_only_key] = False

        if view_preset_pending_key in st_module.session_state:
            pending_view_preset = st_module.session_state.pop(view_preset_pending_key)
            preset_values = {
                "all": {
                    view_search_key: "",
                    view_category_key: ["Personal", "Clinic"],
                    view_priority_key: ["high", "medium", "low"],
                    view_status_key: ["todo", "in_progress", "blocked", "completed"],
                    view_scheduled_only_key: False,
                },
                "today_focus": {
                    view_search_key: "",
                    view_category_key: ["Personal", "Clinic"],
                    view_priority_key: ["high", "medium"],
                    view_status_key: ["todo", "in_progress", "blocked"],
                    view_scheduled_only_key: False,
                },
                "clinic_ops": {
                    view_search_key: "",
                    view_category_key: ["Clinic"],
                    view_priority_key: ["high", "medium", "low"],
                    view_status_key: ["todo", "in_progress", "blocked", "completed"],
                    view_scheduled_only_key: False,
                },
                "personal_sprint": {
                    view_search_key: "",
                    view_category_key: ["Personal"],
                    view_priority_key: ["high", "medium"],
                    view_status_key: ["todo", "in_progress", "blocked", "completed"],
                    view_scheduled_only_key: False,
                },
                "scheduled": {
                    view_search_key: "",
                    view_category_key: ["Personal", "Clinic"],
                    view_priority_key: ["high", "medium", "low"],
                    view_status_key: ["todo", "in_progress", "blocked", "completed"],
                    view_scheduled_only_key: True,
                },
            }
            for key_name, key_value in preset_values.get(pending_view_preset, preset_values["all"]).items():
                st_module.session_state[key_name] = key_value

        preset_row = st_module.columns(5)
        with preset_row[0]:
            if st_module.button("All", key="view_preset_all", use_container_width=True):
                st_module.session_state[view_preset_pending_key] = "all"
                st_module.rerun()
        with preset_row[1]:
            if st_module.button("Today", key="view_preset_today", use_container_width=True):
                st_module.session_state[view_preset_pending_key] = "today_focus"
                st_module.rerun()
        with preset_row[2]:
            if st_module.button("Clinic", key="view_preset_clinic", use_container_width=True):
                st_module.session_state[view_preset_pending_key] = "clinic_ops"
                st_module.rerun()
        with preset_row[3]:
            if st_module.button("Personal", key="view_preset_personal", use_container_width=True):
                st_module.session_state[view_preset_pending_key] = "personal_sprint"
                st_module.rerun()
        with preset_row[4]:
            if st_module.button("Scheduled", key="view_preset_scheduled", use_container_width=True):
                st_module.session_state[view_preset_pending_key] = "scheduled"
                st_module.rerun()

        search_query = st_module.text_input("Search tasks", placeholder="Title or description", key=view_search_key)
        category_filter = st_module.multiselect("Category", ["Personal", "Clinic"], key=view_category_key)
        priority_filter = st_module.multiselect("Priority", ["high", "medium", "low"], key=view_priority_key)
        status_filter = st_module.multiselect(
            "Status",
            ["todo", "in_progress", "blocked", "completed"],
            key=view_status_key,
            format_func=status_label,
        )
        scheduled_only = st_module.checkbox("Scheduled tasks only", key=view_scheduled_only_key)
        timeline_days = st_module.slider(
            "Timeline window (days)",
            min_value=3,
            max_value=21,
            value=int(app_settings.get("timeline_days", 7)),
        )

        st_module.markdown("---")
        st_module.markdown("### Display")
        focus_mode = st_module.toggle(
            "Focus mode",
            value=bool(st_module.session_state.get("focus_mode", False)),
            help="Show only core execution panels and hide secondary context.",
        )
        st_module.session_state["focus_mode"] = bool(focus_mode)
        density_options = ["Comfortable", "Compact"]
        density_key = "density_preset"
        if st_module.session_state.get(density_key) not in density_options:
            st_module.session_state[density_key] = "Comfortable"
        density_preset = st_module.selectbox("Density", density_options, key=density_key)

        st_module.markdown("---")
        st_module.markdown("### AI")
        if ai_enabled():
            st_module.success(f"AI ready ({ai_model_name()})")
        else:
            st_module.info("AI disabled. Set OPENAI_API_KEY to enable.")

        st_module.markdown("---")
        st_module.markdown("### News")
        last_news_refresh = st_module.session_state.get("news_last_refreshed_at")
        if last_news_refresh:
            st_module.caption(f"Last refreshed: {last_news_refresh}")
        else:
            st_module.caption("News has not been refreshed in this session yet.")
        news_manual_refresh_requested = st_module.button("Refresh News Now", use_container_width=True)

        st_module.markdown("---")
        st_module.markdown("### My Apps")
        st_module.link_button("📊 Signal Scanner", "https://tradingbot-production-ed44.up.railway.app/?auth=eyJlbWFpbCI6ImNhbGViLnQua2VubmVkeTIxQGdtYWlsLmNvbSIsImV4cCI6MTc3OTkzODAxNX0.2zIv6Ip3AFgaTsAHNWzYU9GhlKIGUq8f_B_gA-7nBKE", use_container_width=True)
        st_module.link_button("💰 Budgeting Bot", "https://budgetingbot-production.up.railway.app/", use_container_width=True)

    st_module.markdown('<p class="section-lead">Navigate by lane and workflow area from the sidebar.</p>', unsafe_allow_html=True)

    tasks = load_tasks()
    surgical_cases = load_surgical_cases()
    protocol_documents = load_protocol_documents()
    
    # Fetch and cache news for the day (auto-refresh each morning + manual refresh option)
    import os
    news_api_key = os.getenv("NEWSAPI_KEY")
    today_key = datetime.now(MOUNTAIN_TIMEZONE).date().isoformat()
    force_news_refresh = news_manual_refresh_requested or bool(st_module.session_state.pop("news_force_refresh", False))
    needs_daily_refresh = st_module.session_state.get("news_cache_date") != today_key
    cache_missing = "news_articles_cache" not in st_module.session_state

    if cache_missing or needs_daily_refresh or force_news_refresh:
        if fetch_health_news:
            st_module.session_state.news_articles_cache = fetch_health_news(news_api_key, max_articles=10)
        else:
            st_module.session_state.news_articles_cache = []
        st_module.session_state.news_cache_date = today_key
        st_module.session_state.news_last_refreshed_at = datetime.now(MOUNTAIN_TIMEZONE).strftime("%Y-%m-%d %H:%M MT")

        # Recompute summary/takeaways whenever articles are refreshed.
        if summarize_news_with_ai and st_module.session_state.news_articles_cache and ai_enabled():
            summary, takeaways = summarize_news_with_ai(st_module.session_state.news_articles_cache, ai_model_name, ai_enabled)
            st_module.session_state.news_summary_cache = summary
            st_module.session_state.news_takeaways_cache = takeaways
        else:
            st_module.session_state.news_summary_cache = None
            st_module.session_state.news_takeaways_cache = None
    
    news_articles = st_module.session_state.news_articles_cache
    
    # Backfill summary cache if missing but articles are present.
    if "news_summary_cache" not in st_module.session_state or "news_takeaways_cache" not in st_module.session_state:
        if summarize_news_with_ai and news_articles and ai_enabled():
            summary, takeaways = summarize_news_with_ai(news_articles, ai_model_name, ai_enabled)
            st_module.session_state.news_summary_cache = summary
            st_module.session_state.news_takeaways_cache = takeaways
        else:
            st_module.session_state.news_summary_cache = None
            st_module.session_state.news_takeaways_cache = None
    
    news_summary = st_module.session_state.news_summary_cache
    news_takeaways = st_module.session_state.news_takeaways_cache
    
    query = (search_query or "").strip().lower()
    all_active_tasks = [task for task in tasks if task.get("status") != "completed"]
    all_completed_tasks = [task for task in tasks if task.get("status") == "completed"]
    completed_today_all = [task for task in all_completed_tasks if task.get("completed_date") == mountain_today()]

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
    clinic_tasks_all = sorted(
        [task for task in filtered_tasks if task["category"] == "Clinic"],
        key=lambda task: (
            0 if task.get("status") != "completed" else 1,
            priority_rank(task["priority"]),
            task.get("due_date") or date.max,
            task.get("completed_date") or date.max,
        ),
    )
    due_today = [task for task in active_tasks if task.get("due_date") == mountain_today()]
    overdue_tasks = [task for task in active_tasks if task.get("due_date") and task["due_date"] < mountain_today()]
    scheduled_tasks = sorted(
        [task for task in active_tasks if task.get("scheduled_date") and task.get("scheduled_time")],
        key=lambda task: (task["scheduled_date"], task["scheduled_time"], priority_rank(task["priority"])),
    )

    with st_module.form("global_quick_command_bar", clear_on_submit=True):
        st_module.markdown("### Quick Command Bar")
        command_cols = st_module.columns([2.8, 1.1, 1.1, 1.2, 1])
        with command_cols[0]:
            command_title = st_module.text_input("Task", placeholder="Add task from any page", label_visibility="collapsed")
        with command_cols[1]:
            command_category = st_module.selectbox("Lane", ["Personal", "Clinic"], index=0, label_visibility="collapsed")
        with command_cols[2]:
            command_priority = st_module.selectbox("Priority", ["high", "medium", "low"], index=1, label_visibility="collapsed")
        with command_cols[3]:
            command_due = st_module.date_input("Due", value=mountain_today(), label_visibility="collapsed")
        with command_cols[4]:
            command_submit = st_module.form_submit_button("Add", type="primary")

        if command_submit:
            if not command_title.strip():
                st_module.warning("Add a task title before submitting the command bar.")
            else:
                add_task(command_title.strip(), "", command_category, command_priority, command_due)
                st_module.success("Task captured from command bar.")
                st_module.rerun()

    ritual_started_key = "day_ritual_started_on"
    ritual_closed_key = "day_ritual_closed_on"
    ritual_snapshot_key = "day_ritual_snapshot"
    ritual_started_at_key = "day_ritual_started_at"
    ritual_closed_at_key = "day_ritual_closed_at"
    ritual_submit_date_key = "day_ritual_submit_date"
    ritual_submit_time_key = "day_ritual_submit_time"

    mountain_now = datetime.now(MOUNTAIN_TIMEZONE)

    if ritual_submit_date_key not in st_module.session_state:
        st_module.session_state[ritual_submit_date_key] = mountain_now.date()
    if ritual_submit_time_key not in st_module.session_state:
        st_module.session_state[ritual_submit_time_key] = mountain_now.replace(second=0, microsecond=0).time()

    submission_cols = st_module.columns([1, 1, 5])
    with submission_cols[0]:
        st_module.date_input("Submission date", key=ritual_submit_date_key)
    with submission_cols[1]:
        st_module.time_input("Submission time", key=ritual_submit_time_key, step=60)
    with submission_cols[2]:
        st_module.caption("Select the date and time you want recorded for Start My Day and Close My Day (Mountain Time).")

    ritual_cols = st_module.columns([1.2, 1.2, 5])
    with ritual_cols[0]:
        if st_module.button("Start My Day", key="day_ritual_start", use_container_width=True):
            submitted_date = st_module.session_state.get(ritual_submit_date_key, mountain_now.date())
            submitted_time = st_module.session_state.get(ritual_submit_time_key) or mountain_now.replace(second=0, microsecond=0).time()
            submitted_at = datetime.combine(submitted_date, submitted_time)
            st_module.session_state[ritual_started_key] = submitted_date.isoformat()
            st_module.session_state[ritual_started_at_key] = submitted_at.isoformat(timespec="minutes")
            st_module.session_state[ritual_closed_key] = None
            st_module.session_state[ritual_closed_at_key] = None
            st_module.session_state[ritual_snapshot_key] = {
                "started_active": len(active_tasks),
                "started_due_today": len(due_today),
            }
            st_module.session_state["current_page"] = "Morning Ritual"
            st_module.rerun()
    with ritual_cols[1]:
        if st_module.button("Close My Day", key="day_ritual_close", use_container_width=True):
            submitted_date = st_module.session_state.get(ritual_submit_date_key, mountain_now.date())
            submitted_time = st_module.session_state.get(ritual_submit_time_key) or mountain_now.replace(second=0, microsecond=0).time()
            submitted_at = datetime.combine(submitted_date, submitted_time)
            st_module.session_state[ritual_closed_key] = submitted_date.isoformat()
            st_module.session_state[ritual_closed_at_key] = submitted_at.isoformat(timespec="minutes")
            st_module.session_state[ritual_snapshot_key] = {
                "completed_today": len(completed_today_all),
                "remaining_active": len(active_tasks),
            }
            st_module.session_state["current_page"] = "Daily Review"
            st_module.rerun()
    with ritual_cols[2]:
        started_on = st_module.session_state.get(ritual_started_key)
        closed_on = st_module.session_state.get(ritual_closed_key)
        started_at_raw = st_module.session_state.get(ritual_started_at_key)
        closed_at_raw = st_module.session_state.get(ritual_closed_at_key)

        started_at = None
        closed_at = None
        try:
            if started_at_raw:
                started_at = datetime.fromisoformat(str(started_at_raw))
        except ValueError:
            started_at = None
        try:
            if closed_at_raw:
                closed_at = datetime.fromisoformat(str(closed_at_raw))
        except ValueError:
            closed_at = None

        ritual_snapshot = st_module.session_state.get(ritual_snapshot_key) or {}
        if closed_at:
            closed_label = closed_at.strftime("%Y-%m-%d %I:%M %p").replace(" 0", " ")
            st_module.caption(
                f"Closed at {closed_label}: {ritual_snapshot.get('completed_today', 0)} completed · {ritual_snapshot.get('remaining_active', 0)} remaining active."
            )
        elif started_at:
            started_label = started_at.strftime("%Y-%m-%d %I:%M %p").replace(" 0", " ")
            st_module.caption(
                f"Started at {started_label}: {ritual_snapshot.get('started_active', len(active_tasks))} active · {ritual_snapshot.get('started_due_today', len(due_today))} due that day."
            )
        elif closed_on:
            st_module.caption(
                f"Closed on {closed_on}: {ritual_snapshot.get('completed_today', 0)} completed · {ritual_snapshot.get('remaining_active', 0)} remaining active."
            )
        elif started_on:
            st_module.caption(
                f"Started on {started_on}: {ritual_snapshot.get('started_active', len(active_tasks))} active · {ritual_snapshot.get('started_due_today', len(due_today))} due that day."
            )
        else:
            st_module.caption("Use Start My Day and Close My Day for a lightweight daily ritual.")

    if len(filtered_tasks) != len(tasks):
        st_module.caption(f"Showing {len(filtered_tasks)} of {len(tasks)} tasks based on current filters.")

    base_list_limit = 7 if density_preset == "Comfortable" else 4
    list_preview_limit = min(base_list_limit, 3) if focus_mode else base_list_limit

    if current_page == "Overview":
        render_page_banner("overview", "DayAnchor Hub", "Personal, clinical, family, and reminders in one place.")
        overview_settings = st_module.session_state.get("overview_page_settings", overview_runtime_settings(app_settings))
        st_module.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
        render_overview_control_tower(tasks, active_tasks, completed_today_all, personal_tasks, clinic_tasks, scheduled_tasks, app_settings, overview_settings, panel_key="overview_page")

        if not focus_mode:
            st_module.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
            st_module.markdown('<div class="panel-title"><h3>Notes and reminders</h3><span>Editable capture for the personal, clinical, and family lanes</span></div>', unsafe_allow_html=True)
            note_cols = st_module.columns(3, gap="large")
            with note_cols[0]:
                render_saved_notes_panel(
                    "Personal Notes",
                    "Quick access from overview.",
                    "personal_notes",
                    "personal_notes_updated_at",
                    "overview_personal_notes",
                    "Write personal notes, reminders, and planning thoughts here...",
                )
            with note_cols[1]:
                render_saved_notes_panel(
                    "Clinical Notes",
                    "Quick access from overview.",
                    "clinical_notes",
                    "clinical_notes_updated_at",
                    "overview_clinical_notes",
                    "Write clinic notes, operational reminders, and follow-ups (non-PHI) here...",
                )
            with note_cols[2]:
                render_saved_notes_panel(
                    "Family Notes",
                    "Shared context for the family lane.",
                    "family_notes",
                    "family_notes_updated_at",
                    "overview_family_notes",
                    "Write family planning notes, packing lists, reminders, and coordination thoughts here...",
                )

            st_module.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
            reminder_and_links = st_module.columns([1.1, 0.9], gap="large")
            with reminder_and_links[0]:
                render_quick_reminder_capture("overview_quick_reminder")
            with reminder_and_links[1]:
                st_module.markdown('<div class="panel">', unsafe_allow_html=True)
                st_module.markdown('<div class="panel-title"><h3>More Tools</h3><span>Calendar and news live here when you need them</span></div>', unsafe_allow_html=True)
                st_module.markdown(
                    '<div class="empty-state" style="text-align:left;"><strong>Calendar:</strong> Use the schedule page for dense planning.<br /><strong>News:</strong> Keep the digest here as an optional read.</div>',
                    unsafe_allow_html=True,
                )
                if st_module.button("Open Schedule", key="overview_open_schedule_tools", use_container_width=True):
                    st_module.session_state["current_page"] = "Schedule"
                    st_module.rerun()
                if st_module.button("Open Family Schedule", key="overview_open_family_tools", use_container_width=True):
                    st_module.session_state["current_page"] = "Family Schedule"
                    st_module.rerun()
                st_module.markdown('</div>', unsafe_allow_html=True)

            if not focus_mode:
                with st_module.expander("Optional context", expanded=False):
                    if render_task_calendar_panel:
                        render_task_calendar_panel(tasks, "overview_tasks", "Task Calendar", "Mixed load across tasks, due dates, and completions", app_settings=app_settings)
                    if render_morning_digest_panel and news_articles:
                        render_morning_digest_panel(news_articles, news_summary, news_takeaways, panel_key="overview_news")
                    elif render_morning_digest_panel:
                        st_module.info("No news digest is available right now.")
    elif current_page == "Morning Ritual":
        render_page_banner("personal", "Morning Ritual", "Start intentionally before the day gets noisy.")
        render_morning_ritual_panel(tasks, active_tasks, app_settings, panel_key="morning_ritual_page")
    elif current_page == "Personal":
        render_page_banner("personal", "Personal Focus", "Keep your own work clear, bounded, and visible.")
        render_personal_one_thing(personal_tasks, "personal_one_thing")
        st_module.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
        render_task_list_panel(
            "Personal Tasks",
            "Work that belongs outside clinic",
            personal_tasks,
            "personal_task",
            "No personal tasks match the current filters.",
            max_items=5,
            show_remaining_dropdown=True,
        )
        st_module.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
        render_saved_notes_panel(
            "Personal Notes",
            "Capture reminders, ideas, and follow-ups for your personal lane.",
            "personal_notes",
            "personal_notes_updated_at",
            "personal_page",
            "Write personal notes, reminders, and planning thoughts here...",
        )
        if not focus_mode:
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
    elif current_page == "Clinic":
        render_page_banner("clinic", "Clinic Command Center", "Track outpatient load, follow-up flow, and clinic-first work.")
        render_clinic_command_center(clinic_tasks, active_tasks, app_settings, panel_key="clinic_page")
        if not focus_mode:
            st_module.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
        render_task_list_panel(
            "Clinic Tasks",
            "All clinic-related tasks, including completed work",
            clinic_tasks_all,
            "clinic_task",
            "No clinic tasks match the current filters.",
            max_items=5,
            show_remaining_dropdown=True,
        )
        st_module.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
        render_saved_notes_panel(
            "Clinical Notes",
            "Capture clinic workflows, reminders, and non-PHI operational notes.",
            "clinical_notes",
            "clinical_notes_updated_at",
            "clinic_page",
            "Write clinic notes, operational reminders, and follow-ups (non-PHI) here...",
        )
    elif current_page == "Cases":
        render_page_banner("clinic", "Surgical Cases", "Non-PHI case tracking with protocol support and OR cadence.")
        render_surgical_cases_panel(surgical_cases, protocol_documents, app_settings, panel_key="cases_page")
    elif current_page == "Schedule":
        render_page_banner("schedule", "Schedule Builder", "Plan work and personal blocks, then pin them into real time.")
        schedule_header_cols = st_module.columns([1.4, 1])
        with schedule_header_cols[1]:
            if st_module.button("Open Family Schedule", key="schedule_open_family", use_container_width=True):
                st_module.session_state["current_page"] = "Family Schedule"
                st_module.rerun()
        schedule_conflicts, over_capacity_days = summarize_schedule_conflicts(
            scheduled_tasks,
            fallback_minutes=int(app_settings.get("default_duration", 60)),
            daily_capacity_minutes=int(app_settings.get("schedule_daily_capacity_minutes", 480)),
        )
        if schedule_conflicts or over_capacity_days:
            st_module.markdown('<div class="panel">', unsafe_allow_html=True)
            st_module.markdown('<div class="panel-title"><h3>Schedule Alerts</h3><span>Conflicts and capacity risks detected</span></div>', unsafe_allow_html=True)
            if over_capacity_days:
                for day_value, total_minutes in over_capacity_days[:7]:
                    st_module.warning(f"{day_value}: scheduled {total_minutes} min exceeds daily capacity.")
            if schedule_conflicts:
                for day_value, first_task, second_task in schedule_conflicts[:8]:
                    first_label = first_task.get("title") or "Task"
                    second_label = second_task.get("title") or "Task"
                    st_module.warning(f"{day_value}: overlap between '{first_label}' and '{second_label}'.")
            st_module.markdown('</div>', unsafe_allow_html=True)
            st_module.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
        render_schedule_builder_panel(active_tasks, app_settings, panel_key="schedule_page")
        if not focus_mode:
            st_module.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)
            render_task_calendar_panel(tasks, "schedule_tasks", "Schedule Calendar", "Mixed load across tasks, due dates, and completions", app_settings=app_settings)
    elif current_page == "Family Schedule":
        render_page_banner("personal", "Family Schedule", "Dedicated planning space for family events, travel, camps, and appointments.")
        render_family_schedule_panel(active_tasks, app_settings, panel_key="family_schedule_page")
    elif current_page == "Anatomy":
        render_page_banner("clinic", "MSK Anatomy", "Foot and ankle emphasis with extension to the knee.")
        context["render_msk_anatomy_panel"](surgical_cases, protocol_documents, panel_key="anatomy_page")
    elif current_page == "Physical Therapy Protocols":
        render_page_banner("pt", "Physical Therapy Protocols", "Upload, edit, and link PT protocols with surgical and non-operative cases.")
        render_physical_therapy_protocols_panel(surgical_cases, protocol_documents, panel_key="pt_page")
    elif current_page == "News":
        render_page_banner("overview", "Morning News", "Health, fitness, and medical news curated for you.")
        if render_full_news_page:
            render_full_news_page(news_articles, news_summary, news_takeaways, panel_key="news_page")
        else:
            st_module.info("News rendering not available. Please ensure news functions are properly loaded.")
    elif current_page == "AI":
        render_page_banner("ai", "AI Workbench", "Planner, scheduler, and review in one place.")
        render_ai_panel(tasks, active_tasks, panel_key="ai_page")
    elif current_page == "Analytics":
        render_page_banner("overview", "Analytics", "See load, status balance, and schedule pressure at a glance.")
        render_analytics_panel(tasks, active_tasks, scheduled_tasks, panel_key="analytics_page")
    elif current_page == "Notifications":
        render_page_banner("overview", "Notifications", "A focused inbox for reminders and follow-ups.")
        render_notifications_panel(tasks, active_tasks, app_settings, panel_key="notifications_page")
    elif current_page == "MA Lead":
        render_page_banner("clinic", "MA Lead", "Lead queue, huddles, playbooks, and relationship follow-through.")
        render_ma_lead_panel(active_tasks, clinic_tasks_all, panel_key="ma_lead_page")
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
