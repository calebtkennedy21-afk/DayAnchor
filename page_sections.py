from datetime import date, time, timedelta
import calendar

import streamlit as st


def _render_protocol_pdf_preview(st_module, file_bytes, file_mime, file_name, height=420, start_page=1, max_preview_pages=5):
    if isinstance(file_bytes, memoryview):
        file_bytes = bytes(file_bytes)
    if not file_bytes:
        st_module.caption("No file available for preview.")
        return

    looks_like_pdf = (file_mime or "").lower() == "application/pdf" or str(file_name or "").lower().endswith(".pdf")
    if not looks_like_pdf:
        st_module.caption("Inline preview is available for PDF files.")
        return

    try:
        import pypdfium2 as pdfium
    except Exception:
        st_module.info("PDF preview dependency is not available yet. Use Download selected for now.")
        return

    try:
        pdf_document = pdfium.PdfDocument(file_bytes)
        total_pages = len(pdf_document)
        if total_pages == 0:
            st_module.caption("This PDF has no pages to preview.")
            return

        first_page = max(1, int(start_page or 1))
        if first_page > total_pages:
            first_page = total_pages
        start_index = first_page - 1
        preview_pages = max(1, int(max_preview_pages or 5))
        end_index = min(total_pages, start_index + preview_pages)

        st_module.caption(
            f"Previewing pages {start_index + 1}-{end_index} of {total_pages}."
        )
        for page_index in range(start_index, end_index):
            page = pdf_document[page_index]
            bitmap = page.render(scale=1.4)
            image = bitmap.to_pil()
            st_module.image(image, caption=f"Page {page_index + 1}", use_container_width=True)

        if end_index < total_pages:
            st_module.caption("Use the page jump control to continue previewing later pages.")
    except Exception:
        st_module.warning("Unable to render PDF preview in-app.")


def _resolve_default_schedule_time(value):
    raw_value = str(value or "09:00").strip()
    chunks = raw_value.split(":")
    if len(chunks) < 2:
        return time(9, 0)
    try:
        hour = int(chunks[0])
        minute = int(chunks[1])
    except ValueError:
        return time(9, 0)
    hour = max(0, min(23, hour))
    minute = max(0, min(59, minute))
    return time(hour, minute)


def build_today_plan(active_tasks, scheduled_tasks, attention_sort_key_fn):
    today = date.today()

    def sort_key(task):
        return attention_sort_key_fn(task, today)

    ordered = []
    seen_ids = set()
    for task in sorted(active_tasks, key=sort_key):
        task_id = task.get("id")
        if task_id in seen_ids:
            continue
        seen_ids.add(task_id)
        ordered.append(task)

    scheduled_today = [task for task in scheduled_tasks if task.get("scheduled_date") == today]
    unscheduled_high = [task for task in ordered if task.get("priority") == "high" and not (task.get("scheduled_date") and task.get("scheduled_time"))]
    urgent_due = [task for task in ordered if task.get("due_date") and task["due_date"] <= today]

    return {
        "ordered": ordered,
        "primary": ordered[0] if ordered else None,
        "scheduled_today": scheduled_today[:4],
        "urgent_due": urgent_due[:4],
        "unscheduled_high": unscheduled_high[:4],
        "unscheduled_count": len([task for task in active_tasks if not (task.get("scheduled_date") and task.get("scheduled_time"))]),
    }


def case_library_filter_preset_values(preset_name):
    preset_defaults = {
        "all": {
            "case_query": "",
            "stream_filter": "All streams",
            "date_filter": "All dates",
            "cards_limit": 20,
        },
        "upcoming_main_or": {
            "case_query": "",
            "stream_filter": "Main OR",
            "date_filter": "Next 30 days",
            "cards_limit": 20,
        },
        "tenjet_focus": {
            "case_query": "",
            "stream_filter": "TenJet",
            "date_filter": "All dates",
            "cards_limit": 20,
        },
        "recent": {
            "case_query": "",
            "stream_filter": "All streams",
            "date_filter": "Past 30 days",
            "cards_limit": 20,
        },
    }
    return preset_defaults.get(preset_name, preset_defaults["all"])


def case_matches_library_filters(case_item, stream_filter, date_filter, normalized_query, today_value=None):
    if stream_filter != "All streams" and case_item.get("case_stream") != stream_filter:
        return False

    case_date_value = case_item.get("case_date")
    if hasattr(case_date_value, "date"):
        case_date_value = case_date_value.date()
    if date_filter != "All dates":
        if not isinstance(case_date_value, date):
            return False
        if today_value is None:
            today_value = date.today()
        if date_filter == "Next 30 days":
            if not (today_value <= case_date_value <= today_value + timedelta(days=30)):
                return False
        elif date_filter == "Past 30 days":
            if not (today_value - timedelta(days=30) <= case_date_value <= today_value):
                return False
        elif date_filter == "This year":
            if case_date_value.year != today_value.year:
                return False

    if normalized_query:
        searchable_text = " ".join(
            [
                str(case_item.get("procedure_name") or ""),
                str(case_item.get("anatomical_location") or ""),
                str(case_item.get("cpt_codes") or ""),
                str(case_item.get("notes") or ""),
                str(case_item.get("education_notes") or ""),
            ]
        ).lower()
        if normalized_query not in searchable_text:
            return False

    return True


def build_bulk_case_option(case_item):
    case_id = case_item.get("id")
    if case_id is None:
        return None
    case_date_value = case_item.get("case_date")
    date_label = case_date_value.strftime("%Y-%m-%d") if hasattr(case_date_value, "strftime") else str(case_date_value)
    return f"#{case_id} | {case_item.get('case_stream') or 'Unknown'} | {date_label} | {case_item.get('procedure_name') or 'Untitled case'}"


def case_stream_options(surgical_cases):
    available_streams = ["Main OR", "DSC OR", "TenJet"]
    seen_streams = {item.get("case_stream") for item in surgical_cases}
    return ["All streams"] + [stream for stream in available_streams if stream in seen_streams]


def split_cases_by_status(case_list):
    groups = {"planned": [], "completed": [], "canceled": []}
    for case_item in case_list:
        status = case_item.get("status")
        if status in groups:
            groups[status].append(case_item)
    return groups


def render_overview_control_tower(
    tasks,
    active_tasks,
    completed_today_all,
    personal_tasks,
    clinic_tasks,
    scheduled_tasks,
    app_settings,
    overview_settings,
    panel_key,
    deps,
    st_module=st,
):
    today = date.today()
    lens_key = f"{panel_key}_lens"
    if lens_key not in st_module.session_state:
        st_module.session_state[lens_key] = "Auto"

    lens_choice = st_module.selectbox("Overview lens", deps["overview_lens_options"](app_settings), key=lens_key)
    lens = deps["resolve_overview_lens"](active_tasks, personal_tasks, clinic_tasks, app_settings, lens_choice)
    day_context = deps["resolve_overview_day_context"](overview_settings, active_tasks, personal_tasks, clinic_tasks)
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
    site_display_label = overview_settings["site_label"].split("(", 1)[0].strip()
    if not site_display_label:
        site_display_label = overview_settings["site_label"]

    today_plan = build_today_plan(active_tasks, scheduled_tasks, deps["task_attention_sort_key"])
    focus_key = f"{panel_key}_focus_task_id"
    pinned_focus_id = st_module.session_state.get(focus_key)
    pinned_focus = next((task for task in today_plan["ordered"] if task.get("id") == pinned_focus_id), None)
    focus_task = pinned_focus or today_plan["primary"]

    overview_focus = sorted(active_tasks, key=lambda task: deps["task_attention_sort_key"](task, date.today()))[:4]
    next_scheduled = scheduled_tasks[:4]
    clinic_summary = deps["clinic_day_summary"](clinic_tasks, active_tasks, app_settings, clinic_mode_key)
    schedule_snapshot = deps["schedule_workload_snapshot"](active_tasks)

    surgical_cases = []
    protocol_documents = []
    if deps.get("load_surgical_cases"):
        try:
            surgical_cases = deps["load_surgical_cases"]() or []
        except Exception:
            surgical_cases = []
    if deps.get("load_protocol_documents"):
        try:
            protocol_documents = deps["load_protocol_documents"]() or []
        except Exception:
            protocol_documents = []

    briefing_horizon_key = f"{panel_key}_briefing_horizon_days"
    briefing_queue_depth_key = f"{panel_key}_briefing_queue_depth"
    if briefing_horizon_key not in st_module.session_state:
        st_module.session_state[briefing_horizon_key] = 7
    if briefing_queue_depth_key not in st_module.session_state:
        st_module.session_state[briefing_queue_depth_key] = 3

    briefing_horizon_days = int(st_module.session_state.get(briefing_horizon_key, 7) or 7)
    briefing_queue_depth = int(st_module.session_state.get(briefing_queue_depth_key, 3) or 3)

    upcoming_cases = sorted(
        [
            item
            for item in surgical_cases
            if item.get("status") == "planned"
            and item.get("case_date")
            and item.get("case_date") <= (today + timedelta(days=briefing_horizon_days))
        ],
        key=lambda item: item.get("case_date"),
    )

    case_risk_rows = []
    for item in upcoming_cases:
        case_date = item.get("case_date")
        days_until = (case_date - today).days if case_date else 99
        has_cpt = bool(str(item.get("cpt_codes") or "").strip())
        has_protocol_match = bool(
            protocol_documents
            and deps["suggest_protocols_for_case"](item, protocol_documents, max_items=1)
        )
        risk_score = 0
        if not has_cpt:
            risk_score += 10
        if not has_protocol_match:
            risk_score += 8
        risk_score += max(0, 10 - max(days_until, 0))
        case_risk_rows.append(
            {
                "case": item,
                "risk_score": risk_score,
                "has_cpt": has_cpt,
                "has_protocol_match": has_protocol_match,
                "days_until": days_until,
            }
        )

    case_risk_rows.sort(key=lambda item: item["risk_score"], reverse=True)
    high_risk_cases = [item["case"] for item in case_risk_rows if item["risk_score"] > 0 and not item["has_cpt"]]
    missing_protocol_cases = [item["case"] for item in case_risk_rows if not item["has_protocol_match"]]
    top_case_row = case_risk_rows[0] if case_risk_rows else None

    st_module.markdown('<div class="panel">', unsafe_allow_html=True)
    st_module.markdown(
        '<div class="panel-title"><h3>Smart Daily Briefing</h3><span>Risk-first summary with one-click fixes</span></div>',
        unsafe_allow_html=True,
    )

    with st_module.expander("Briefing controls", expanded=False):
        st_module.slider(
            "Case lookahead window (days)",
            min_value=3,
            max_value=21,
            value=briefing_horizon_days,
            key=briefing_horizon_key,
            help="Controls how far ahead the case risk scan looks.",
        )
        st_module.selectbox(
            "Recommended sequence depth",
            [3, 4, 5],
            index=[3, 4, 5].index(briefing_queue_depth) if briefing_queue_depth in (3, 4, 5) else 0,
            key=briefing_queue_depth_key,
            help="How many tasks to include in the sequence list.",
        )

    briefing_cols = st_module.columns(4)
    briefing_cols[0].metric("Overdue tasks", len(overdue_tasks_today))
    briefing_cols[1].metric(f"Upcoming cases ({briefing_horizon_days}d)", len(upcoming_cases))
    briefing_cols[2].metric("Cases missing CPT", len(high_risk_cases))
    briefing_cols[3].metric("Cases missing protocol", len(missing_protocol_cases))

    summary_col, actions_col = st_module.columns([1.15, 0.85], gap="large")
    with summary_col:
        if overdue_tasks_today:
            top_overdue = sorted(overdue_tasks_today, key=lambda task: deps["task_attention_sort_key"](task, today))[0]
            st_module.markdown(
                f"- Overdue focus: <strong>{top_overdue.get('title')}</strong>",
                unsafe_allow_html=True,
            )
        else:
            top_overdue = None
            st_module.markdown("- Overdue focus: none")

        if today_plan["unscheduled_high"]:
            top_unscheduled_high = today_plan["unscheduled_high"][0]
            st_module.markdown(
                f"- Unscheduled high priority: <strong>{top_unscheduled_high.get('title')}</strong>",
                unsafe_allow_html=True,
            )
        else:
            top_unscheduled_high = None
            st_module.markdown("- Unscheduled high priority: none")

        if top_case_row:
            top_case = top_case_row["case"]
            case_date = top_case.get("case_date")
            case_date_label = case_date.strftime("%b %d") if hasattr(case_date, "strftime") else str(case_date)
            risk_reasons = []
            if not top_case_row["has_cpt"]:
                risk_reasons.append("missing CPT")
            if not top_case_row["has_protocol_match"]:
                risk_reasons.append("missing protocol")
            if not risk_reasons:
                risk_reasons.append("near-term case")
            st_module.markdown(
                f"- Highest-risk case: <strong>{top_case.get('procedure_name') or 'Untitled case'}</strong> ({case_date_label}) · {', '.join(risk_reasons)} · risk {top_case_row['risk_score']}",
                unsafe_allow_html=True,
            )
        else:
            top_case = None
            st_module.markdown("- Highest-risk case: none")

        if missing_protocol_cases:
            st_module.markdown(f"- Missing protocol links: {len(missing_protocol_cases)} upcoming case(s)")
        else:
            st_module.markdown("- Missing protocol links: none")

        st_module.markdown("- Recommended sequence:")
        if today_plan["ordered"]:
            for index, task in enumerate(today_plan["ordered"][:briefing_queue_depth], start=1):
                st_module.markdown(
                    f"  {index}. <strong>{task.get('title')}</strong> · {task.get('category')} · {task.get('priority', 'medium').title()}",
                    unsafe_allow_html=True,
                )
        else:
            st_module.markdown("  1. No active tasks in queue")

    with actions_col:
        st_module.markdown('<div class="panel-title"><h3>One-Click Fixes</h3><span>Resolve blockers quickly</span></div>', unsafe_allow_html=True)
        if top_overdue and deps.get("set_task_status"):
            if st_module.button("Start top overdue", key=f"{panel_key}_briefing_start_overdue", type="secondary"):
                deps["set_task_status"](top_overdue.get("id"), "in_progress")
                st_module.success("Top overdue task moved to In Progress.")
                st_module.rerun()

        if top_unscheduled_high and deps.get("update_task"):
            if st_module.button("Schedule top high-priority", key=f"{panel_key}_briefing_schedule_high", type="secondary"):
                deps["update_task"](
                    top_unscheduled_high.get("id"),
                    scheduled_date=today,
                    scheduled_time=_resolve_default_schedule_time(app_settings.get("default_schedule_time")),
                    scheduled_minutes=int(app_settings.get("default_duration", 60) or 60),
                )
                st_module.success("Top high-priority task scheduled for today.")
                st_module.rerun()

        if top_case and deps.get("update_surgical_case"):
            cpt_suggestions = deps["suggest_cpt_codes_for_case"](
                top_case,
                surgical_cases,
                max_items=1,
                cpt_reference=deps.get("cpt_reference"),
            )
            if cpt_suggestions:
                if st_module.button("Auto-fill top case CPT", key=f"{panel_key}_briefing_autofill_cpt", type="secondary"):
                    deps["update_surgical_case"](
                        top_case.get("id"),
                        cpt_codes=cpt_suggestions[0].get("cpt_codes"),
                    )
                    st_module.success("Top case updated with suggested CPT code(s).")
                    st_module.rerun()

        if missing_protocol_cases and deps.get("add_task"):
            reminder_title = "Upload or tag missing protocols for upcoming foot/ankle cases"
            existing_reminder = next(
                (
                    task
                    for task in active_tasks
                    if str(task.get("title") or "").strip().lower() == reminder_title.lower()
                ),
                None,
            )
            if existing_reminder:
                st_module.caption("Protocol reminder task already exists.")
            elif st_module.button("Create protocol upload reminder", key=f"{panel_key}_briefing_protocol_reminder", type="secondary"):
                deps["add_task"](
                    reminder_title,
                    f"{len(missing_protocol_cases)} upcoming case(s) have no clear protocol match.",
                    "Clinic",
                    "high",
                    today,
                )
                st_module.success("Reminder task created.")
                st_module.rerun()

    st_module.markdown('</div>', unsafe_allow_html=True)

    metric_cols = st_module.columns(4)
    metric_cols[0].metric("Active", len(active_tasks))
    metric_cols[1].metric("Due today", len(due_today_tasks))
    metric_cols[2].metric("Overdue", len(overdue_tasks_today))
    metric_cols[3].metric("Scheduled", len(scheduled_tasks))

    top_left, top_right = st_module.columns([1.25, 0.85], gap="large")
    with top_left:
        st_module.markdown('<div class="panel">', unsafe_allow_html=True)
        st_module.markdown('<div class="panel-title"><h3>Today at a Glance</h3><span>Fast read on the day’s operating mode</span></div>', unsafe_allow_html=True)
        st_module.markdown(
            f"<div class='empty-state' style='text-align:left;'><strong>{overview_settings['role_label']} at {site_display_label}</strong><br />{day_context['mode']} · {day_context['focus_text']}<br />Clinic: {len(clinic_backlog)} active · Personal: {len(personal_backlog)} active · High-priority unscheduled: {len(unscheduled_high)}</div>",
            unsafe_allow_html=True,
        )
        st_module.caption(day_context["reason_text"])
        st_module.markdown(
            f"<div class='ai-chip-grid'><span class='ai-chip'>Target: {day_context['target_value']} {day_context['target_label']}</span><span class='ai-chip'>Shift: {overview_settings['shift_minutes']} min</span><span class='ai-chip'>Focus window: {overview_settings['focus_window_minutes']} min</span></div>",
            unsafe_allow_html=True,
        )
        if overview_focus:
            st_module.markdown(
                f"<div class='empty-state' style='text-align:left;'><strong>Top action pressure:</strong> {len(overview_focus)} tasks in the immediate queue.<br /><strong>Highest signal:</strong> {overview_focus[0]['title']}</div>",
                unsafe_allow_html=True,
            )
        else:
            st_module.markdown('<div class="empty-state">No active tasks need attention right now.</div>', unsafe_allow_html=True)
        st_module.markdown('</div>', unsafe_allow_html=True)

    with top_right:
        st_module.markdown('<div class="panel">', unsafe_allow_html=True)
        st_module.markdown('<div class="panel-title"><h3>Outpatient Load</h3><span>Editable patient and procedure planning</span></div>', unsafe_allow_html=True)
        st_module.metric("Day mode", day_context["mode"])
        st_module.caption(f"{site_display_label} · {overview_settings['role_label']} · buffer {overview_settings['admin_buffer_minutes']} min")
        st_module.markdown(
            f"<div class='ai-chip-grid'><span class='ai-chip'>Clinic active: {clinic_summary['active_clinic_count']}</span><span class='ai-chip'>Unscheduled: {clinic_summary['clinic_unscheduled_count']}</span><span class='ai-chip'>Due soon: {clinic_summary['due_soon_count']}</span><span class='ai-chip'>Active pressure: {day_context['active_pressure']}</span></div>",
            unsafe_allow_html=True,
        )
        if clinic_summary["top_clinic_tasks"]:
            st_module.markdown("<div class='panel-title' style='margin-top:0.75rem;'><h3>Top outpatient priorities</h3><span>First things first</span></div>", unsafe_allow_html=True)
            for task in clinic_summary["top_clinic_tasks"][:3]:
                st_module.markdown(
                    f"- <strong>{task['title']}</strong> · {task['priority'].title()} · {deps['format_due'](task)}",
                    unsafe_allow_html=True,
                )
        st_module.markdown('</div>', unsafe_allow_html=True)

    lower_left, lower_right = st_module.columns(2, gap="large")
    with lower_left:
        st_module.markdown('<div class="panel">', unsafe_allow_html=True)
        st_module.markdown('<div class="panel-title"><h3>Today Plan</h3><span>One queue for execution</span></div>', unsafe_allow_html=True)
        st_module.caption(f"{today_plan['unscheduled_count']} unscheduled tasks, {len(today_plan['unscheduled_high'])} high-priority ones, {len(today_plan['scheduled_today'])} scheduled today.")
        if focus_task:
            is_focus_pinned = pinned_focus_id == focus_task.get("id")
            if focus_task in today_plan["urgent_due"]:
                why_text = "Overdue or due today"
            elif focus_task in today_plan["scheduled_today"]:
                why_text = "Already on today's schedule"
            else:
                why_text = "High-priority work waiting for an open slot"
            pin_text = "Pinned for today" if is_focus_pinned else "Not pinned"
            st_module.markdown(
                f"<div class='empty-state' style='text-align:left;'><strong>Start here:</strong> {focus_task['title']}<br /><strong>Why now:</strong> {why_text}<br /><strong>Focus status:</strong> {pin_text}</div>",
                unsafe_allow_html=True,
            )
            focus_controls = st_module.columns(2)
            with focus_controls[0]:
                if st_module.button("Pin focus task", key=f"{panel_key}_pin_focus", disabled=is_focus_pinned):
                    st_module.session_state[focus_key] = focus_task.get("id")
                    st_module.rerun()
            with focus_controls[1]:
                if st_module.button("Clear focus", key=f"{panel_key}_clear_focus", disabled=not bool(pinned_focus_id)):
                    st_module.session_state.pop(focus_key, None)
                    st_module.rerun()
        else:
            st_module.markdown('<div class="empty-state">No active tasks need attention right now.</div>', unsafe_allow_html=True)
        st_module.markdown(
            f"<div class='empty-state' style='text-align:left;'><strong>Default buffer:</strong> {overview_settings['admin_buffer_minutes']} min<br /><strong>Focus window:</strong> {overview_settings['focus_window_minutes']} min<br /><strong>Recommended mode:</strong> {lens['label']}</div>",
            unsafe_allow_html=True,
        )
        if today_plan["ordered"]:
            st_module.markdown('<div class="panel-title" style="margin-top:1rem;"><h3>Execution queue</h3><span>Ordered by urgency and priority</span></div>', unsafe_allow_html=True)
            for task in today_plan["ordered"][:4]:
                attention = deps["task_attention_signal"](task, date.today())
                tag = attention["label"]
                st_module.markdown(
                    f"- <strong>{task['title']}</strong> · {tag} · {task['category']} · {task['priority'].title()} · {deps['format_due'](task)}",
                    unsafe_allow_html=True,
                )
        if today_plan["scheduled_today"]:
            st_module.markdown('<div class="panel-title" style="margin-top:1rem;"><h3>Scheduled blocks</h3><span>Protected time already on the calendar</span></div>', unsafe_allow_html=True)
        for task in today_plan["scheduled_today"]:
            scheduled_time = task.get("scheduled_time").strftime("%I:%M %p").lstrip("0") if task.get("scheduled_time") else "Any time"
            st_module.markdown(
                f"- <strong>{task['title']}</strong> · {task['scheduled_date']} at {scheduled_time} · {task.get('scheduled_minutes') or '-'} min",
                unsafe_allow_html=True,
            )
        if not today_plan["scheduled_today"]:
            st_module.markdown('<div class="empty-state">No scheduled blocks for today yet. Use the Schedule page to protect a focus window.</div>', unsafe_allow_html=True)
        st_module.markdown('</div>', unsafe_allow_html=True)

    with lower_right:
        st_module.markdown('<div class="panel">', unsafe_allow_html=True)
        st_module.markdown('<div class="panel-title"><h3>Action Shortcuts</h3><span>Fast context before deciding the next move</span></div>', unsafe_allow_html=True)
        default_lane = "Clinic" if lens_choice in ("Clinic day", "Procedure Friday") else "Personal"
        st_module.markdown(
            f"<div class='empty-state' style='text-align:left;'><strong>Suggested lane:</strong> {default_lane}<br /><strong>Overdue right now:</strong> {len(overdue_tasks_today)}<br /><strong>Unscheduled high priority:</strong> {len(unscheduled_high)}<br />Use <strong>Quick capture</strong> in the sidebar to add a task instantly.</div>",
            unsafe_allow_html=True,
        )
        with st_module.form(f"{panel_key}_overview_quick_add", clear_on_submit=True):
            quick_title = st_module.text_input("Quick add from overview", placeholder="Enter task title")
            quick_priority = st_module.selectbox("Priority", ["high", "medium", "low"], index=1)
            quick_submit = st_module.form_submit_button("Add task", type="primary")
        if quick_submit:
            if not quick_title.strip():
                st_module.warning("Add a task title first.")
            else:
                deps["add_task"](quick_title.strip(), "", default_lane, quick_priority, date.today())
                st_module.success("Quick task added from overview.")
                st_module.rerun()
        st_module.markdown('</div>', unsafe_allow_html=True)


def render_surgical_cases_panel(
    surgical_cases,
    protocol_documents,
    app_settings,
    panel_key,
    deps,
    st_module=st,
):
    predicted_days = deps["predicted_or_days"](app_settings, horizon_days=120)
    predicted_labels = {day: label for day, label in predicted_days}
    upcoming_predicted = [item for item in predicted_days if item[0] >= date.today()]

    st_module.markdown('<div class="panel">', unsafe_allow_html=True)
    st_module.markdown('<div class="panel-title"><h3>Surgical Cases</h3><span>Non-PHI case log for surgery and TenJet procedures</span></div>', unsafe_allow_html=True)
    st_module.caption("Store procedure details (including CPT codes) only. Do not enter patient identifiers.")



    metrics = st_module.columns(5)
    planned_cases = [item for item in surgical_cases if item.get("status") == "planned"]
    completed_cases = [item for item in surgical_cases if item.get("status") == "completed"]
    tenjet_cases = [item for item in surgical_cases if item.get("case_stream") == "TenJet"]
    main_or_cases = [item for item in surgical_cases if item.get("case_stream") == "Main OR"]
    dsc_or_cases = [item for item in surgical_cases if item.get("case_stream") == "DSC OR"]
    metrics[0].metric("Planned", len(planned_cases))
    metrics[1].metric("Completed", len(completed_cases))
    metrics[2].metric("Main OR", len(main_or_cases))
    metrics[3].metric("DSC OR", len(dsc_or_cases))
    metrics[4].metric("TenJet", len(tenjet_cases))

    top_left, top_right = st_module.columns([1.1, 0.9], gap="large")
    with top_left:
        date_key = f"{panel_key}_new_case_date"
        stream_key = f"{panel_key}_new_case_stream"
        procedure_key = f"{panel_key}_new_case_procedure"
        location_key = f"{panel_key}_new_case_location"
        cpt_key = f"{panel_key}_new_case_cpt"
        status_key = f"{panel_key}_new_case_status"
        notes_key = f"{panel_key}_new_case_notes"
        education_url_key = f"{panel_key}_new_case_education_url"
        education_notes_key = f"{panel_key}_new_case_education_notes"
        cpt_reference_category_key = f"{panel_key}_cpt_reference_category"
        cpt_reference_select_key = f"{panel_key}_cpt_reference_select"
        cpt_prefill_key = f"{panel_key}_new_case_cpt_prefill"
        form_reset_key = f"{panel_key}_new_case_reset"

        if date_key not in st_module.session_state:
            st_module.session_state[date_key] = date.today()
        if stream_key not in st_module.session_state:
            st_module.session_state[stream_key] = "Main OR"
        if status_key not in st_module.session_state:
            st_module.session_state[status_key] = "planned"
        if cpt_reference_category_key not in st_module.session_state:
            st_module.session_state[cpt_reference_category_key] = "All"

        # Apply staged updates before creating any widgets bound to these keys.
        if st_module.session_state.pop(form_reset_key, False):
            st_module.session_state[procedure_key] = ""
            st_module.session_state[location_key] = ""
            st_module.session_state[cpt_key] = ""
            st_module.session_state[notes_key] = ""
            st_module.session_state[education_url_key] = ""
            st_module.session_state[education_notes_key] = ""
        if cpt_prefill_key in st_module.session_state:
            st_module.session_state[cpt_key] = st_module.session_state.pop(cpt_prefill_key)

        cpt_reference = deps.get("cpt_reference", [])
        reference_categories = ["All"] + sorted({item.get("category") or "Other" for item in cpt_reference})

        with st_module.expander("CPT Reference", expanded=False):
            st_module.caption("Searchable CPT list for quick lookup and one-click fill.")
            selected_category = st_module.selectbox(
                "Category",
                reference_categories,
                key=cpt_reference_category_key,
            )
            filtered_reference = [
                item
                for item in cpt_reference
                if selected_category == "All" or (item.get("category") or "Other") == selected_category
            ]
            reference_options = [""] + [
                f"{item.get('code', '')} - {item.get('description', '')}"
                for item in filtered_reference
            ]
            selected_reference = st_module.selectbox(
                "Code and description",
                reference_options,
                key=cpt_reference_select_key,
                help="Type in the dropdown to search by CPT code or description.",
            )

            if selected_reference:
                selected_code = selected_reference.split(" - ", 1)[0].strip()
                selected_item = next(
                    (item for item in filtered_reference if str(item.get("code") or "").strip() == selected_code),
                    None,
                )
                if selected_item:
                    st_module.caption(
                        f"{selected_item.get('category', 'Other')} · {selected_item.get('description', '')}"
                    )
                    if st_module.button("Use selected CPT", key=f"{panel_key}_use_reference_cpt"):
                        st_module.session_state[cpt_prefill_key] = selected_code
                        st_module.rerun()

        case_date = st_module.date_input("Case date", key=date_key)
        case_stream = st_module.selectbox("Case stream", ["Main OR", "DSC OR", "TenJet"], key=stream_key)
        procedure_name = st_module.text_input("Procedure performed", key=procedure_key)
        anatomical_location = st_module.text_input("Anatomical location", key=location_key)
        cpt_codes = st_module.text_input("CPT code(s)", key=cpt_key, placeholder="e.g., 27658, 29898")

        cpt_suggestions = []
        if procedure_name.strip() or anatomical_location.strip():
            cpt_suggestions = deps["suggest_cpt_codes_for_case"](
                {
                    "procedure_name": procedure_name,
                    "anatomical_location": anatomical_location,
                    "case_stream": case_stream,
                    "education_notes": st_module.session_state.get(education_notes_key, ""),
                    "notes": st_module.session_state.get(notes_key, ""),
                },
                surgical_cases,
                max_items=3,
                cpt_reference=cpt_reference,
            )

        if cpt_suggestions:
            st_module.caption("Suggested CPT code(s) from prior cases and the reference list")
            for idx, suggestion in enumerate(cpt_suggestions):
                suggestion_cols = st_module.columns([2.2, 1.4, 0.9])
                with suggestion_cols[0]:
                    st_module.write(f"{suggestion['cpt_codes']}")
                with suggestion_cols[1]:
                    source_label = "Reference" if suggestion["match_source"] == "reference" else "History"
                    detail_label = suggestion["matched_category"] or suggestion["matched_procedure_name"] or "Prior case"
                    st_module.caption(
                        f"{source_label} · match {suggestion['score']} · {detail_label}"
                    )
                with suggestion_cols[2]:
                    suggestion_key = suggestion["matched_case_id"] if suggestion["matched_case_id"] is not None else suggestion["cpt_codes"]
                    if st_module.button("Use", key=f"{panel_key}_cpt_suggestion_use_{idx}_{suggestion_key}"):
                        st_module.session_state[cpt_prefill_key] = suggestion["cpt_codes"]
                        st_module.rerun()
        elif procedure_name.strip() or anatomical_location.strip():
            st_module.caption("No close CPT matches found in history or the reference list yet.")

        status = st_module.selectbox("Status", ["planned", "completed", "canceled"], key=status_key)
        notes = st_module.text_area("Notes (non-PHI)", height=80, key=notes_key)
        education_url = st_module.text_input("Education link (optional)", key=education_url_key, placeholder="https://...")
        education_notes = st_module.text_area(
            "Educational description",
            height=90,
            key=education_notes_key,
            placeholder="What the case is, key anatomy, technical pearls, postop points...",
        )
        submit_case = st_module.button("Add surgical case", key=f"{panel_key}_new_case_submit", type="primary")

        if submit_case:
            if not procedure_name.strip():
                st_module.warning("Add the procedure name before saving.")
            else:
                cpt_codes_to_save = cpt_codes.strip()
                if not cpt_codes_to_save:
                    cpt_suggestions = deps["suggest_cpt_codes_for_case"](
                        {
                            "procedure_name": procedure_name,
                            "anatomical_location": anatomical_location,
                            "case_stream": case_stream,
                            "education_notes": education_notes,
                            "notes": notes,
                        },
                        surgical_cases,
                        max_items=1,
                        cpt_reference=cpt_reference,
                    )
                    if cpt_suggestions:
                        best_match = cpt_suggestions[0]
                        cpt_codes_to_save = best_match["cpt_codes"]
                        source_label = "reference" if best_match["match_source"] == "reference" else "a similar case"
                        st_module.info(
                            f"Auto-filled CPT code(s) from {source_label}: {cpt_codes_to_save}"
                        )

                deps["add_surgical_case"](
                    case_date=case_date,
                    case_stream=case_stream,
                    procedure_name=procedure_name,
                    anatomical_location=anatomical_location,
                    cpt_codes=cpt_codes_to_save,
                    status=status,
                    notes=notes,
                    education_url=education_url,
                    education_notes=education_notes,
                )
                st_module.session_state[form_reset_key] = True
                st_module.success("Surgical case saved.")
                st_module.rerun()

    with top_right:
        st_module.markdown('<div class="panel-title"><h3>Predicted OR Days</h3><span>Every Friday + alternating weekday pattern</span></div>', unsafe_allow_html=True)
        if upcoming_predicted:
            for day, label in upcoming_predicted[:10]:
                st_module.markdown(f"- <strong>{day.strftime('%a %b %d')}</strong> · {label}", unsafe_allow_html=True)
        else:
            st_module.markdown('<div class="empty-state">No OR days predicted for the selected cadence.</div>', unsafe_allow_html=True)

    st_module.markdown('<div style="height: 0.8rem;"></div>', unsafe_allow_html=True)
    st_module.markdown('<div class="panel-title"><h3>OR Calendar</h3><span>Month view of OR cadence and logged cases</span></div>', unsafe_allow_html=True)
    month_key = f"{panel_key}_month_anchor"
    if month_key not in st_module.session_state:
        st_module.session_state[month_key] = date.today().replace(day=1)

    calendar_controls = st_module.columns([1, 2, 1])
    with calendar_controls[0]:
        if st_module.button("Prev month", key=f"{panel_key}_prev_month"):
            current_anchor = st_module.session_state[month_key]
            previous_month_end = current_anchor - timedelta(days=1)
            st_module.session_state[month_key] = previous_month_end.replace(day=1)
            st_module.rerun()
    with calendar_controls[1]:
        st_module.markdown(
            f"<div style='text-align:center; font-weight:700; margin-top:0.4rem;'>{calendar.month_name[st_module.session_state[month_key].month]} {st_module.session_state[month_key].year}</div>",
            unsafe_allow_html=True,
        )
    with calendar_controls[2]:
        if st_module.button("Next month", key=f"{panel_key}_next_month"):
            current_anchor = st_module.session_state[month_key]
            next_month_start = (current_anchor.replace(day=28) + timedelta(days=4)).replace(day=1)
            st_module.session_state[month_key] = next_month_start
            st_module.rerun()

    deps["render_or_calendar_compact"](surgical_cases, predicted_labels, st_module.session_state[month_key], panel_key)

    st_module.markdown('<div style="height: 0.8rem;"></div>', unsafe_allow_html=True)
    st_module.markdown('<div class="panel-title"><h3>Case Library</h3><span>All logged surgical cases organized by status</span></div>', unsafe_allow_html=True)

    case_protocol_links = []
    if deps.get("load_case_protocol_links"):
        try:
            case_protocol_links = deps["load_case_protocol_links"]() or []
        except Exception:
            case_protocol_links = []

    links_by_case = {}
    links_by_protocol = {}
    for item in case_protocol_links:
        case_id = item.get("case_id")
        protocol_id = item.get("protocol_id")
        if case_id is None or protocol_id is None:
            continue
        links_by_case.setdefault(case_id, set()).add(protocol_id)
        links_by_protocol.setdefault(protocol_id, set()).add(case_id)

    protocol_by_id = {
        item.get("id"): item
        for item in protocol_documents
        if item.get("id") is not None
    }
    
    if surgical_cases:
        case_search_key = f"{panel_key}_case_search"
        case_stream_filter_key = f"{panel_key}_case_stream_filter"
        case_date_filter_key = f"{panel_key}_case_date_filter"
        case_limit_key = f"{panel_key}_case_cards_limit"
        case_preset_pending_key = f"{panel_key}_case_filter_preset_pending"
        case_bulk_selection_key = f"{panel_key}_bulk_case_selection"
        case_bulk_status_key = f"{panel_key}_bulk_case_status"
        case_bulk_undo_key = f"{panel_key}_bulk_case_undo"
        case_bulk_pending_key = f"{panel_key}_bulk_case_pending"
        case_delete_pending_key = f"{panel_key}_case_delete_pending"

        if case_preset_pending_key in st_module.session_state:
            pending_preset = st_module.session_state.pop(case_preset_pending_key)
            preset_values = case_library_filter_preset_values(pending_preset)
            st_module.session_state[case_search_key] = preset_values["case_query"]
            st_module.session_state[case_stream_filter_key] = preset_values["stream_filter"]
            st_module.session_state[case_date_filter_key] = preset_values["date_filter"]
            st_module.session_state[case_limit_key] = preset_values["cards_limit"]

        preset_cols = st_module.columns([1, 1, 1, 1])
        with preset_cols[0]:
            if st_module.button("Upcoming Main OR", key=f"{panel_key}_preset_upcoming_main_or", use_container_width=True):
                st_module.session_state[case_preset_pending_key] = "upcoming_main_or"
                st_module.rerun()
        with preset_cols[1]:
            if st_module.button("TenJet Focus", key=f"{panel_key}_preset_tenjet_focus", use_container_width=True):
                st_module.session_state[case_preset_pending_key] = "tenjet_focus"
                st_module.rerun()
        with preset_cols[2]:
            if st_module.button("Recent Cases", key=f"{panel_key}_preset_recent", use_container_width=True):
                st_module.session_state[case_preset_pending_key] = "recent"
                st_module.rerun()
        with preset_cols[3]:
            if st_module.button("Reset Filters", key=f"{panel_key}_preset_all", use_container_width=True):
                st_module.session_state[case_preset_pending_key] = "all"
                st_module.rerun()

        filter_cols = st_module.columns([2.2, 1.2, 1.2, 1])
        with filter_cols[0]:
            case_query = st_module.text_input(
                "Search cases",
                key=case_search_key,
                placeholder="Search procedure, location, CPT, notes, or education fields",
            )
        with filter_cols[1]:
            stream_options = case_stream_options(surgical_cases)
            case_stream_filter = st_module.selectbox(
                "Stream",
                stream_options,
                key=case_stream_filter_key,
            )
        with filter_cols[2]:
            case_date_filter = st_module.selectbox(
                "Date range",
                ["All dates", "Next 30 days", "Past 30 days", "This year"],
                key=case_date_filter_key,
            )
        with filter_cols[3]:
            case_cards_limit = st_module.selectbox(
                "Cards",
                [10, 20, 50, 100],
                index=1,
                key=case_limit_key,
            )

        normalized_case_query = case_query.strip().lower()

        filtered_cases = [
            item
            for item in surgical_cases
            if case_matches_library_filters(
                item,
                case_stream_filter,
                case_date_filter,
                normalized_case_query,
            )
        ]
        cases_by_status = split_cases_by_status(filtered_cases)
        planned_cases = cases_by_status["planned"]
        completed_cases = cases_by_status["completed"]
        canceled_cases = cases_by_status["canceled"]

        st_module.caption(
            f"Showing {len(filtered_cases)} of {len(surgical_cases)} cases"
        )

        bulk_case_options = []
        bulk_case_id_by_label = {}
        for case_item in filtered_cases:
            option_label = build_bulk_case_option(case_item)
            if not option_label:
                continue
            case_id = case_item.get("id")
            bulk_case_options.append(option_label)
            bulk_case_id_by_label[option_label] = case_id

        bulk_cols = st_module.columns([2.4, 1.2, 1, 1, 1])
        with bulk_cols[0]:
            selected_bulk_labels = st_module.multiselect(
                "Bulk select cases",
                bulk_case_options,
                key=case_bulk_selection_key,
                placeholder="Choose cases to update status in one action",
            )
        with bulk_cols[1]:
            bulk_target_status = st_module.selectbox(
                "Bulk status",
                ["planned", "completed", "canceled"],
                key=case_bulk_status_key,
            )
        with bulk_cols[2]:
            if st_module.button("Apply Bulk", key=f"{panel_key}_apply_bulk_status", use_container_width=True):
                selected_case_ids = [bulk_case_id_by_label[label] for label in selected_bulk_labels if label in bulk_case_id_by_label]
                if not selected_case_ids:
                    st_module.warning("Select at least one case before applying bulk status.")
                else:
                    st_module.session_state[case_bulk_pending_key] = {
                        "case_ids": selected_case_ids,
                        "target_status": bulk_target_status,
                    }
                    st_module.rerun()
        with bulk_cols[3]:
            if st_module.button("Clear", key=f"{panel_key}_clear_bulk_status", use_container_width=True):
                st_module.session_state[case_bulk_selection_key] = []
                st_module.session_state.pop(case_bulk_pending_key, None)
                st_module.rerun()
        with bulk_cols[4]:
            if st_module.button("Undo Bulk", key=f"{panel_key}_undo_bulk_status", use_container_width=True):
                undo_entries = st_module.session_state.get(case_bulk_undo_key) or []
                if not undo_entries:
                    st_module.warning("No bulk update to undo.")
                else:
                    for entry in undo_entries:
                        deps["update_surgical_case"](entry["id"], status=entry["status"])
                    st_module.session_state[case_bulk_undo_key] = []
                    st_module.success(f"Reverted {len(undo_entries)} case status updates.")
                    st_module.rerun()

        pending_bulk = st_module.session_state.get(case_bulk_pending_key)
        if pending_bulk:
            pending_ids = pending_bulk.get("case_ids") or []
            pending_status = pending_bulk.get("target_status") or "planned"
            st_module.warning(
                f"Confirm bulk update: set {len(pending_ids)} selected case(s) to {pending_status}."
            )
            confirm_cols = st_module.columns([1, 1, 3])
            with confirm_cols[0]:
                if st_module.button("Confirm Bulk", key=f"{panel_key}_confirm_bulk_status", use_container_width=True):
                    current_status_map = {
                        item.get("id"): item.get("status")
                        for item in surgical_cases
                        if item.get("id") is not None
                    }
                    st_module.session_state[case_bulk_undo_key] = [
                        {
                            "id": case_id,
                            "status": current_status_map.get(case_id, "planned"),
                        }
                        for case_id in pending_ids
                    ]
                    for case_id in pending_ids:
                        deps["update_surgical_case"](case_id, status=pending_status)
                    st_module.success(f"Updated {len(pending_ids)} cases to {pending_status}.")
                    st_module.session_state[case_bulk_selection_key] = []
                    st_module.session_state.pop(case_bulk_pending_key, None)
                    st_module.rerun()
            with confirm_cols[1]:
                if st_module.button("Cancel Bulk", key=f"{panel_key}_cancel_bulk_status", use_container_width=True):
                    st_module.session_state.pop(case_bulk_pending_key, None)
                    st_module.rerun()
        
        planned_tab, completed_tab, canceled_tab = st_module.tabs([
            f"Planned ({len(planned_cases)})",
            f"Completed ({len(completed_cases)})",
            f"Canceled ({len(canceled_cases)})"
        ])
        
        def _render_case_card(case_list, st_module_ref, deps_ref, protocol_docs, predicted_labels_ref, panel_key_ref):
            if not case_list:
                st_module_ref.markdown('<div class="empty-state">No cases in this category.</div>', unsafe_allow_html=True)
                return
            
            for item in case_list[:case_cards_limit]:
                case_id = item.get("id")
                case_date_value = item.get("case_date")
                date_label = case_date_value.strftime("%b %d, %Y") if hasattr(case_date_value, "strftime") else str(case_date_value)
                or_hint = predicted_labels_ref.get(case_date_value)
                hint_suffix = f" · {or_hint}" if or_hint else ""
                
                status = item.get("status", "planned")
                status_color_map = {
                    "planned": "#FFA500",
                    "completed": "#28A745",
                    "canceled": "#DC3545"
                }
                status_color = status_color_map.get(status, "#6C757D")
                status_surface_map = {
                    "planned": "linear-gradient(135deg, #fff8eb 0%, #ffffff 60%)",
                    "completed": "linear-gradient(135deg, #ecfff2 0%, #ffffff 60%)",
                    "canceled": "linear-gradient(135deg, #fff0f0 0%, #ffffff 60%)",
                }
                status_surface = status_surface_map.get(status, "#ffffff")
                
                stream = item.get("case_stream", "Unknown")
                
                st_module_ref.markdown(
                    f"<div style='padding: 1.05rem 1.15rem; margin: 0.8rem 0; background: {status_surface}; border: 1px solid #d9dee8; border-left: 5px solid {status_color}; border-radius: 0.65rem; box-shadow: 0 4px 14px rgba(15, 23, 42, 0.06);'>"
                    f"<div style='display: flex; justify-content: space-between; align-items: start; margin-bottom: 0.8rem;'>"
                    f"<div><div style='font-size: 1.08rem; font-weight: 700; margin-bottom: 0.35rem; color: #0f172a; line-height: 1.35;'>{item.get('procedure_name')}</div>"
                    f"<div style='font-size: 0.9rem; color: #334155;'>{item.get('anatomical_location') or 'Location not specified'}</div></div>"
                    f"<div style='text-align: right;'>"
                    f"<span style='display: inline-block; background: {status_color}; color: white; padding: 0.27rem 0.68rem; border-radius: 999px; font-size: 0.8rem; font-weight: 700; margin-left: 0.5rem;'>{str(status).title()}</span>"
                    f"<span style='display: inline-block; background: #eef2ff; color: #1e293b; padding: 0.27rem 0.68rem; border-radius: 999px; font-size: 0.8rem; font-weight: 600; margin-left: 0.45rem;'>{stream}</span>"
                    f"</div></div>"
                    f"<div style='font-size: 0.88rem; color: #475569; margin-bottom: 0.75rem; font-weight: 500;'>{date_label}{hint_suffix}</div>",
                    unsafe_allow_html=True,
                )
                
                if item.get("cpt_codes"):
                    st_module_ref.markdown(
                        f"<span style='display:inline-block; background:#f8fafc; border:1px solid #dbe2ea; color:#0f172a; border-radius:999px; padding:0.24rem 0.72rem; font-size:0.84rem; font-weight:600;'>CPT: {item.get('cpt_codes')}</span>",
                        unsafe_allow_html=True,
                    )
                
                if item.get("notes"):
                    st_module_ref.markdown(f"**Notes:** {item.get('notes')}")
                
                if item.get("education_url"):
                    st_module_ref.markdown(f"[Case Education Link]({item.get('education_url')})")
                
                if item.get("education_notes"):
                    with st_module_ref.expander("Educational Description", expanded=False):
                        st_module_ref.write(item.get("education_notes"))

                linked_protocol_ids = sorted(links_by_case.get(case_id, set()))
                linked_pt_docs = [
                    protocol_by_id.get(protocol_id)
                    for protocol_id in linked_protocol_ids
                    if protocol_by_id.get(protocol_id)
                    and str(protocol_by_id.get(protocol_id).get("surgeon_label")).strip().lower() == "physical therapy"
                ]
                if linked_pt_docs:
                    with st_module_ref.expander(f"Linked PT Protocols ({len(linked_pt_docs)})", expanded=False):
                        for linked_doc in linked_pt_docs:
                            linked_doc_id = linked_doc.get("id")
                            linked_doc_bytes = linked_doc.get("file_bytes")
                            if isinstance(linked_doc_bytes, memoryview):
                                linked_doc_bytes = bytes(linked_doc_bytes)

                            st_module_ref.markdown(f"**{linked_doc.get('protocol_name') or 'Untitled PT Protocol'}**")
                            if linked_doc.get("notes"):
                                st_module_ref.caption(linked_doc.get("notes"))

                            linked_cols = st_module_ref.columns([1, 1, 1, 2])
                            with linked_cols[0]:
                                if linked_doc_bytes:
                                    st_module_ref.download_button(
                                        label="Download",
                                        data=linked_doc_bytes,
                                        file_name=linked_doc.get("file_name") or "protocol.pdf",
                                        mime=linked_doc.get("file_mime") or "application/octet-stream",
                                        key=f"{panel_key_ref}_case_linked_download_{case_id}_{linked_doc_id}",
                                    )
                            with linked_cols[1]:
                                preview_key = f"{panel_key_ref}_case_linked_preview_{case_id}_{linked_doc_id}"
                                if st_module_ref.button("Preview", key=f"{panel_key_ref}_case_linked_preview_btn_{case_id}_{linked_doc_id}"):
                                    st_module_ref.session_state[preview_key] = not st_module_ref.session_state.get(preview_key, False)
                            with linked_cols[2]:
                                if st_module_ref.button("Unlink", key=f"{panel_key_ref}_case_linked_unlink_{case_id}_{linked_doc_id}"):
                                    if deps_ref.get("set_protocol_case_links") and linked_doc_id is not None:
                                        remaining_case_ids = sorted(
                                            case_link_id
                                            for case_link_id in links_by_protocol.get(linked_doc_id, set())
                                            if case_link_id != case_id
                                        )
                                        deps_ref["set_protocol_case_links"](linked_doc_id, remaining_case_ids)
                                        st_module_ref.success("PT protocol unlinked from this case.")
                                        st_module_ref.rerun()
                                    else:
                                        st_module_ref.warning("Unable to unlink protocol in this view.")

                            if st_module_ref.session_state.get(preview_key, False):
                                _render_protocol_pdf_preview(
                                    st_module_ref,
                                    linked_doc_bytes,
                                    linked_doc.get("file_mime"),
                                    linked_doc.get("file_name"),
                                    height=380,
                                )
                
                # Filter PT and non-PT protocols for suggestions
                suggestions = deps_ref["suggest_protocols_for_case"](item, protocol_docs, max_items=6)
                if suggestions:
                    with st_module_ref.expander("Suggested Protocols", expanded=False):
                        protocol_labels = []
                        protocol_map = {}
                        for score, overlap_terms, doc in suggestions:
                            pt_label = "[PT] " if str(doc.get("surgeon_label")).strip().lower() == "physical therapy" else ""
                            label = f"{pt_label}{doc.get('protocol_name')} (score: {score})"
                            protocol_labels.append(label)
                            protocol_map[label] = (score, overlap_terms, doc)

                        selected_protocol_label = st_module_ref.selectbox(
                            "Select protocol",
                            protocol_labels,
                            key=f"{panel_key_ref}_case_protocol_select_{case_id}",
                            label_visibility="collapsed",
                        )
                        selected_score, selected_overlap_terms, selected_doc = protocol_map[selected_protocol_label]
                        pt_tag = "**Physical Therapy Protocol**\n" if str(selected_doc.get("surgeon_label")).strip().lower() == "physical therapy" else ""
                        st_module_ref.caption(
                            f"{pt_tag}Selected: {selected_doc.get('protocol_name')} · keywords: {', '.join(selected_overlap_terms)} · file: {selected_doc.get('file_name')}"
                        )
                        if selected_doc.get("notes"):
                            st_module_ref.write(selected_doc.get("notes"))
                        selected_doc_bytes = selected_doc.get("file_bytes")
                        if isinstance(selected_doc_bytes, memoryview):
                            selected_doc_bytes = bytes(selected_doc_bytes)
                        selected_doc_id = selected_doc.get("id")
                        preview_visible_key = f"{panel_key_ref}_case_preview_visible_{case_id}_{selected_doc_id}"
                        preview_controls = st_module_ref.columns([1, 1, 3])
                        with preview_controls[0]:
                            if st_module_ref.button("View selected protocol", key=f"{panel_key_ref}_case_preview_show_{case_id}_{selected_doc_id}"):
                                st_module_ref.session_state[preview_visible_key] = True
                        with preview_controls[1]:
                            if st_module_ref.button("Hide preview", key=f"{panel_key_ref}_case_preview_hide_{case_id}_{selected_doc_id}"):
                                st_module_ref.session_state[preview_visible_key] = False

                        if st_module_ref.session_state.get(preview_visible_key, False):
                            _render_protocol_pdf_preview(
                                st_module_ref,
                                selected_doc_bytes,
                                selected_doc.get("file_mime"),
                                selected_doc.get("file_name"),
                                height=420,
                            )
                        if selected_doc_bytes:
                            st_module_ref.download_button(
                                label="Download selected",
                                data=selected_doc_bytes,
                                file_name=selected_doc.get("file_name") or "protocol.pdf",
                                mime=selected_doc.get("file_mime") or "application/octet-stream",
                                key=f"{panel_key_ref}_case_suggested_download_selected_{case_id}_{selected_doc.get('id')}",
                            )
                
                action_cols = st_module_ref.columns([1, 1, 1, 1])
                with action_cols[0]:
                    new_status = st_module_ref.selectbox(
                        "Status",
                        ["planned", "completed", "canceled"],
                        index=["planned", "completed", "canceled"].index(item.get("status", "planned")) if item.get("status", "planned") in ["planned", "completed", "canceled"] else 0,
                        key=f"{panel_key_ref}_status_{case_id}",
                        label_visibility="collapsed",
                    )
                with action_cols[1]:
                    updated_cpt_codes = st_module_ref.text_input(
                        "CPT",
                        value=item.get("cpt_codes") or "",
                        key=f"{panel_key_ref}_cpt_codes_{case_id}",
                        label_visibility="collapsed",
                        placeholder="CPT code(s)",
                    )
                with action_cols[2]:
                    if st_module_ref.button("Update", key=f"{panel_key_ref}_update_{case_id}", use_container_width=True):
                        deps_ref["update_surgical_case"](case_id, status=new_status, cpt_codes=updated_cpt_codes)
                        st_module_ref.success("Case updated.")
                        st_module_ref.rerun()
                with action_cols[3]:
                    if st_module_ref.button("Delete", key=f"{panel_key_ref}_delete_{case_id}", use_container_width=True):
                        st_module_ref.session_state[case_delete_pending_key] = case_id
                        st_module_ref.rerun()

                if st_module_ref.session_state.get(case_delete_pending_key) == case_id:
                    st_module_ref.warning("Confirm case deletion. This action cannot be undone.")
                    delete_confirm_cols = st_module_ref.columns([1, 1, 3])
                    with delete_confirm_cols[0]:
                        if st_module_ref.button("Confirm Delete", key=f"{panel_key_ref}_confirm_delete_{case_id}", use_container_width=True):
                            deps_ref["delete_surgical_case"](case_id)
                            st_module_ref.session_state.pop(case_delete_pending_key, None)
                            st_module_ref.success("Case deleted.")
                            st_module_ref.rerun()
                    with delete_confirm_cols[1]:
                        if st_module_ref.button("Cancel", key=f"{panel_key_ref}_cancel_delete_{case_id}", use_container_width=True):
                            st_module_ref.session_state.pop(case_delete_pending_key, None)
                            st_module_ref.rerun()
                
                st_module_ref.markdown('<div style="height: 0.4rem;"></div>', unsafe_allow_html=True)

            if len(case_list) > case_cards_limit:
                st_module_ref.caption(f"Showing first {case_cards_limit} of {len(case_list)} cases in this tab.")
        
        with planned_tab:
            _render_case_card(planned_cases, st_module, deps, protocol_documents, predicted_labels, panel_key)
        
        with completed_tab:
            _render_case_card(completed_cases, st_module, deps, protocol_documents, predicted_labels, panel_key)
        
        with canceled_tab:
            _render_case_card(canceled_cases, st_module, deps, protocol_documents, predicted_labels, panel_key)
    else:
        st_module.markdown('<div class="empty-state">No surgical cases logged yet.</div>', unsafe_allow_html=True)

    st_module.markdown('</div>', unsafe_allow_html=True)

    st_module.markdown('<div style="height: 1rem;"></div>', unsafe_allow_html=True)

    with st_module.expander("Protocol Library - Upload and reference BB protocols", expanded=False):
        with st_module.form(f"{panel_key}_protocol_upload_form"):
            protocol_surgeon_label = st_module.text_input("Surgeon label", value=app_settings.get("default_surgeon_label", "Dr. Braden Boyer (BB)"))
            protocol_name = st_module.text_input("Protocol title")
            protocol_notes = st_module.text_area("Protocol notes", height=80, placeholder="Key steps, pearls, contraindications, follow-up details...")
            protocol_file = st_module.file_uploader(
                "Protocol file",
                type=["pdf", "doc", "docx", "txt", "md"],
                key=f"{panel_key}_protocol_file",
                help="Upload non-PHI protocol documents only.",
            )
            protocol_submit = st_module.form_submit_button("Upload protocol", type="secondary")

        if protocol_submit:
            if not protocol_file:
                st_module.warning("Select a protocol file to upload.")
            else:
                file_bytes = protocol_file.getvalue()
                if len(file_bytes) > 12 * 1024 * 1024:
                    st_module.warning("File is too large. Keep uploads under 12 MB.")
                else:
                    deps["add_protocol_document"](
                        surgeon_label=protocol_surgeon_label,
                        protocol_name=protocol_name,
                        upload_name=protocol_file.name,
                        upload_mime=getattr(protocol_file, "type", None),
                        upload_bytes=file_bytes,
                        notes=protocol_notes,
                    )
                    st_module.success("Protocol uploaded.")
                    st_module.rerun()

        if protocol_documents:
            protocol_filter_col, protocol_sort_col, protocol_page_size_col = st_module.columns([2, 1, 1])
            with protocol_filter_col:
                protocol_query = st_module.text_input(
                    "Search protocols",
                    key=f"{panel_key}_protocol_search",
                    placeholder="Search title, surgeon, filename, or notes",
                )
            with protocol_sort_col:
                protocol_sort = st_module.selectbox(
                    "Sort",
                    ["Newest", "Oldest", "Title A-Z", "Title Z-A"],
                    index=0,
                    key=f"{panel_key}_protocol_sort",
                )
            with protocol_page_size_col:
                protocol_page_size = st_module.selectbox(
                    "Per page",
                    [10, 25, 50, 100],
                    index=1,
                    key=f"{panel_key}_protocol_page_size",
                )

            normalized_protocol_query = protocol_query.strip().lower()
            filtered_protocol_documents = protocol_documents
            if normalized_protocol_query:
                filtered_protocol_documents = [
                    item
                    for item in protocol_documents
                    if normalized_protocol_query
                    in " ".join(
                        [
                            str(item.get("protocol_name") or ""),
                            str(item.get("surgeon_label") or ""),
                            str(item.get("file_name") or ""),
                            str(item.get("notes") or ""),
                        ]
                    ).lower()
                ]

            if protocol_sort == "Oldest":
                filtered_protocol_documents = sorted(
                    filtered_protocol_documents,
                    key=lambda item: (
                        item.get("created_date") or date.min,
                        item.get("id") or 0,
                    ),
                )
            elif protocol_sort == "Title A-Z":
                filtered_protocol_documents = sorted(
                    filtered_protocol_documents,
                    key=lambda item: str(item.get("protocol_name") or "").lower(),
                )
            elif protocol_sort == "Title Z-A":
                filtered_protocol_documents = sorted(
                    filtered_protocol_documents,
                    key=lambda item: str(item.get("protocol_name") or "").lower(),
                    reverse=True,
                )

            total_filtered_protocols = len(filtered_protocol_documents)
            protocol_start_index = 0
            protocol_end_index = 0
            if not total_filtered_protocols:
                st_module.caption("No protocols match the current search.")
            else:
                total_protocol_pages = (total_filtered_protocols + protocol_page_size - 1) // protocol_page_size
                protocol_page_number = int(
                    st_module.number_input(
                        "Protocol page",
                        min_value=1,
                        max_value=total_protocol_pages,
                        value=1,
                        step=1,
                        key=f"{panel_key}_protocol_page_number",
                    )
                )
                protocol_start_index = (protocol_page_number - 1) * protocol_page_size
                protocol_end_index = min(protocol_start_index + protocol_page_size, total_filtered_protocols)
                st_module.caption(
                    f"Showing {protocol_start_index + 1}-{protocol_end_index} of {total_filtered_protocols} matching protocol(s)"
                    f" ({len(protocol_documents)} total)."
                )

            for doc in filtered_protocol_documents[protocol_start_index:protocol_end_index]:
                doc_id = doc.get("id")
                doc_bytes = doc.get("file_bytes")
                if isinstance(doc_bytes, memoryview):
                    doc_bytes = bytes(doc_bytes)
                st_module.markdown(
                    f"- <strong>{doc.get('protocol_name')}</strong> · {doc.get('surgeon_label')} · {doc.get('file_name')}",
                    unsafe_allow_html=True,
                )
                if doc.get("notes"):
                    st_module.caption(doc.get("notes"))
                doc_cols = st_module.columns([1, 1, 1])
                with doc_cols[0]:
                    if doc_bytes:
                        st_module.download_button(
                            label="Download",
                            data=doc_bytes,
                            file_name=doc.get("file_name") or "protocol.pdf",
                            mime=doc.get("file_mime") or "application/octet-stream",
                            key=f"{panel_key}_protocol_download_{doc_id}",
                        )
                with doc_cols[1]:
                    if st_module.button("Delete", key=f"{panel_key}_protocol_delete_{doc_id}"):
                        deps["delete_protocol_document"](doc_id)
                        st_module.success("Protocol deleted.")
                        st_module.rerun()
                with doc_cols[2]:
                    with st_module.expander("Edit protocol", expanded=False):
                        with st_module.form(f"{panel_key}_protocol_edit_form_{doc_id}"):
                            edit_surgeon_label = st_module.text_input(
                                "Surgeon label",
                                value=doc.get("surgeon_label") or app_settings.get("default_surgeon_label", "Dr. Braden Boyer (BB)"),
                                key=f"{panel_key}_protocol_edit_surgeon_{doc_id}",
                            )
                            edit_protocol_name = st_module.text_input(
                                "Protocol title",
                                value=doc.get("protocol_name") or "",
                                key=f"{panel_key}_protocol_edit_name_{doc_id}",
                            )
                            edit_protocol_notes = st_module.text_area(
                                "Protocol notes",
                                value=doc.get("notes") or "",
                                height=80,
                                key=f"{panel_key}_protocol_edit_notes_{doc_id}",
                            )
                            replacement_protocol_file = st_module.file_uploader(
                                "Replace protocol file (optional)",
                                type=["pdf", "doc", "docx", "txt", "md"],
                                key=f"{panel_key}_protocol_edit_file_{doc_id}",
                                help="Leave empty to keep the current file.",
                            )
                            edit_submit = st_module.form_submit_button("Save protocol changes", type="secondary")

                        if edit_submit:
                            replacement_bytes = None
                            replacement_name = None
                            replacement_mime = None
                            if replacement_protocol_file:
                                replacement_bytes = replacement_protocol_file.getvalue()
                                replacement_name = replacement_protocol_file.name
                                replacement_mime = getattr(replacement_protocol_file, "type", None)
                                if len(replacement_bytes) > 12 * 1024 * 1024:
                                    st_module.warning("File is too large. Keep uploads under 12 MB.")
                                    replacement_bytes = None
                                    replacement_name = None
                                    replacement_mime = None
                                    edit_submit = False

                            if edit_submit:
                                final_protocol_name = edit_protocol_name.strip() or (replacement_name or doc.get("file_name") or "Protocol")
                                deps["update_protocol_document"](
                                    doc_id=doc_id,
                                    surgeon_label=edit_surgeon_label,
                                    protocol_name=final_protocol_name,
                                    notes=edit_protocol_notes,
                                    upload_name=replacement_name,
                                    upload_mime=replacement_mime,
                                    upload_bytes=replacement_bytes,
                                )
                                st_module.success("Protocol updated.")
                                st_module.rerun()
                with st_module.expander("View PDF", expanded=False):
                    _render_protocol_pdf_preview(
                        st_module,
                        doc_bytes,
                        doc.get("file_mime"),
                        doc.get("file_name"),
                        height=460,
                    )
        else:
            st_module.markdown('<div class="empty-state">No protocols uploaded yet. Add BB protocols to reference during case prep.</div>', unsafe_allow_html=True)

    st_module.markdown('</div>', unsafe_allow_html=True)


def render_physical_therapy_protocols_panel(surgical_cases, protocol_documents, panel_key, deps, st_module=st):
    st_module.markdown('<div class="panel">', unsafe_allow_html=True)
    st_module.markdown('<div class="panel-title"><h3>PT Protocol Library</h3><span>Upload, edit, and link PT protocols to cases</span></div>', unsafe_allow_html=True)
    st_module.caption("Store only non-PHI rehabilitation protocols.")

    case_protocol_links = []
    if deps.get("load_case_protocol_links"):
        try:
            case_protocol_links = deps["load_case_protocol_links"]() or []
        except Exception:
            case_protocol_links = []

    sorted_cases = sorted(
        surgical_cases,
        key=lambda item: (item.get("case_date") or date.min, item.get("id") or 0),
        reverse=True,
    )
    case_options = [item.get("id") for item in sorted_cases if item.get("id") is not None]
    case_label_map = {}
    for item in sorted_cases:
        case_id = item.get("id")
        if case_id is None:
            continue
        case_date_value = item.get("case_date")
        case_date_label = case_date_value.strftime("%b %d, %Y") if hasattr(case_date_value, "strftime") else "No date"
        case_label_map[case_id] = f"{case_date_label} - {item.get('procedure_name') or 'Unnamed case'} ({item.get('case_stream') or 'Unknown stream'})"

    with st_module.form(f"{panel_key}_pt_upload_form"):
        pt_cols = st_module.columns(2)
        with pt_cols[0]:
            pt_protocol_name = st_module.text_input("Protocol title")
        with pt_cols[1]:
            pt_protocol_file = st_module.file_uploader(
                "Protocol file",
                type=["pdf", "doc", "docx", "txt", "md"],
                key=f"{panel_key}_pt_upload_file",
            )
        pt_protocol_notes = st_module.text_area("Protocol notes", height=90)
        pt_linked_case_ids = st_module.multiselect(
            "Link to cases (optional)",
            options=case_options,
            format_func=lambda case_id: case_label_map.get(case_id, f"Case {case_id}"),
        )
        pt_submit = st_module.form_submit_button("Upload PT protocol", type="primary")

    if pt_submit:
        if not pt_protocol_file:
            st_module.warning("Select a protocol file to upload.")
        else:
            file_bytes = pt_protocol_file.getvalue()
            if len(file_bytes) > 12 * 1024 * 1024:
                st_module.warning("File is too large. Keep uploads under 12 MB.")
            else:
                new_doc_id = deps["add_protocol_document"](
                    surgeon_label="Physical Therapy",
                    protocol_name=pt_protocol_name,
                    upload_name=pt_protocol_file.name,
                    upload_mime=getattr(pt_protocol_file, "type", None),
                    upload_bytes=file_bytes,
                    notes=pt_protocol_notes,
                )
                if new_doc_id and deps.get("set_protocol_case_links"):
                    deps["set_protocol_case_links"](new_doc_id, pt_linked_case_ids)
                st_module.success("PT protocol uploaded.")
                st_module.rerun()

    pt_query = st_module.text_input("Search PT protocols", placeholder="Title, filename, or notes")
    pt_protocols = [doc for doc in protocol_documents if str(doc.get("surgeon_label")).strip().lower() == "physical therapy"]
    if pt_query.strip():
        q = pt_query.strip().lower()
        pt_protocols = [
            doc
            for doc in pt_protocols
            if q in (str(doc.get("protocol_name") or "") + " " + str(doc.get("file_name") or "") + " " + str(doc.get("notes") or "")).lower()
        ]

    links_by_protocol = {}
    for item in case_protocol_links:
        protocol_id = item.get("protocol_id")
        case_id = item.get("case_id")
        if protocol_id is None or case_id is None:
            continue
        links_by_protocol.setdefault(protocol_id, set()).add(case_id)

    if pt_protocols:
        for doc in pt_protocols:
            doc_id = doc.get("id")
            doc_bytes = doc.get("file_bytes")
            if isinstance(doc_bytes, memoryview):
                doc_bytes = bytes(doc_bytes)

            linked_case_ids = sorted(case_id for case_id in links_by_protocol.get(doc_id, set()) if case_id in case_label_map)
            linked_case_text = ", ".join(case_label_map[case_id] for case_id in linked_case_ids) if linked_case_ids else "No linked cases"

            st_module.markdown(
                "<div style='border:1px solid #d8dee7; border-radius:14px; padding:1rem; margin:0.75rem 0; background:#ffffff;'>",
                unsafe_allow_html=True,
            )
            st_module.markdown(f"### {doc.get('protocol_name') or 'Untitled protocol'}")
            st_module.caption(f"File: {doc.get('file_name') or 'Unknown'}")
            st_module.caption(f"Linked cases: {linked_case_text}")

            action_cols = st_module.columns([1, 1, 2])
            with action_cols[0]:
                if doc_bytes:
                    st_module.download_button(
                        label="Download",
                        data=doc_bytes,
                        file_name=doc.get("file_name") or "protocol.pdf",
                        mime=doc.get("file_mime") or "application/octet-stream",
                        key=f"{panel_key}_pt_download_{doc_id}",
                    )
            with action_cols[1]:
                with st_module.expander("Preview", expanded=False):
                    _render_protocol_pdf_preview(
                        st_module,
                        doc_bytes,
                        doc.get("file_mime"),
                        doc.get("file_name") or "protocol.pdf",
                        height=380,
                    )

            with st_module.form(f"{panel_key}_pt_edit_form_{doc_id}"):
                edit_name = st_module.text_input("Protocol title", value=doc.get("protocol_name") or "")
                edit_notes = st_module.text_area("Protocol notes", value=doc.get("notes") or "", height=90)
                edit_linked_case_ids = st_module.multiselect(
                    "Linked cases",
                    options=case_options,
                    default=linked_case_ids,
                    format_func=lambda case_id: case_label_map.get(case_id, f"Case {case_id}"),
                )
                replacement_file = st_module.file_uploader(
                    "Replace file (optional)",
                    type=["pdf", "doc", "docx", "txt", "md"],
                    key=f"{panel_key}_pt_replace_{doc_id}",
                )
                edit_cols = st_module.columns([1, 1, 2])
                save_clicked = edit_cols[0].form_submit_button("Save changes", type="primary")
                delete_clicked = edit_cols[1].form_submit_button("Delete")

            if save_clicked:
                replacement_bytes = replacement_file.getvalue() if replacement_file else None
                deps["update_protocol_document"](
                    doc_id=doc_id,
                    surgeon_label="Physical Therapy",
                    protocol_name=edit_name,
                    notes=edit_notes,
                    upload_name=replacement_file.name if replacement_file else None,
                    upload_mime=getattr(replacement_file, "type", None) if replacement_file else None,
                    upload_bytes=replacement_bytes,
                )
                if deps.get("set_protocol_case_links"):
                    deps["set_protocol_case_links"](doc_id, edit_linked_case_ids)
                st_module.success("PT protocol updated.")
                st_module.rerun()

            if delete_clicked:
                deps["delete_protocol_document"](doc_id)
                st_module.success("PT protocol deleted.")
                st_module.rerun()

            st_module.markdown("</div>", unsafe_allow_html=True)
    else:
        st_module.markdown('<div class="empty-state">No PT protocols uploaded yet. Add one above and link it to relevant cases.</div>', unsafe_allow_html=True)

    st_module.markdown('</div>', unsafe_allow_html=True)


def render_ai_panel(tasks, active_tasks, panel_key, deps, st_module=st):
    summary = deps["ai_workbench_summary"](tasks, active_tasks)
    prompt_key = f"{panel_key}_ai_prompt"
    default_prompt = "Build a focused plan for today and call out the first two actions I should take."
    if prompt_key not in st_module.session_state:
        st_module.session_state[prompt_key] = default_prompt

    st_module.markdown('<div class="ai-shell">', unsafe_allow_html=True)
    st_module.markdown(
        "<div class='panel ai-hero'>"
        "<div class='panel-title'><h3>AI Workbench</h3><span>Planning, scheduling, and review in one command center</span></div>"
        f"<p>AI sees {summary['active_count']} active tasks, {summary['overdue_count']} overdue items, and {summary['unscheduled_high_count']} high-priority tasks still waiting for a slot.</p>"
        "</div>",
        unsafe_allow_html=True,
    )

    stat_cols = st_module.columns(4)
    stat_payload = [
        ("Active", summary["active_count"], summary["focus_label"]),
        ("Due today", summary["due_today_count"], "Use this for immediate triage."),
        ("Overdue", summary["overdue_count"], "These should dominate the plan."),
        ("Completed today", summary["completed_today_count"], "Useful for closing the loop."),
    ]
    for col, (label, value, note) in zip(stat_cols, stat_payload):
        with col:
            st_module.markdown(
                f"<div class='ai-stat-card'><div class='ai-stat-label'>{label}</div><div class='ai-stat-value'>{value}</div><div class='ai-stat-note'>{note}</div></div>",
                unsafe_allow_html=True,
            )

    st_module.markdown('<div class="panel ai-command">', unsafe_allow_html=True)
    st_module.markdown('<div class="panel-title"><h3>Prompt Studio</h3><span>Shape the output before you generate it</span></div>', unsafe_allow_html=True)
    command_col, insight_col = st_module.columns([1.35, 1], gap="large")
    presets = [
        ("Today focus", "Build a focused plan for today, sorted by urgency and energy cost."),
        ("Rescue mode", "I need help recovering from a messy day. Prioritize overdue, blocked, and unscheduled high-priority work."),
        ("Clinic shift", "Organize this like a clinic operations block with practical sequencing and low-friction tasks first."),
        ("Schedule pass", "Reschedule the active tasks into realistic blocks and flag anything that should be deferred."),
    ]
    with command_col:
        preset_cols = st_module.columns(2)
        for idx, (label, prompt) in enumerate(presets):
            if preset_cols[idx % 2].button(label, key=f"{panel_key}_preset_{idx}"):
                st_module.session_state[prompt_key] = prompt
                st_module.rerun()
        ai_prompt = st_module.text_area("Ask AI", height=120, key=prompt_key)
        action_cols = st_module.columns(2)
        with action_cols[0]:
            generate_plan_clicked = st_module.button("Generate AI Plan", key=f"{panel_key}_gen", type="primary")
        with action_cols[1]:
            auto_schedule_clicked = st_module.button("Auto-Schedule Tasks", key=f"{panel_key}_auto")

    with insight_col:
        st_module.markdown('<div class="panel-title"><h3>What AI sees</h3><span>Operational signals used for planning</span></div>', unsafe_allow_html=True)
        st_module.markdown(
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
        st_module.markdown("<ul class='ai-list'>" + "".join(f"<li>{line}</li>" for line in insight_lines) + "</ul>", unsafe_allow_html=True)
        if summary["recommended_task"]:
            recommended = summary["recommended_task"]
            st_module.markdown(
                "<div class='empty-state' style='text-align:left; margin-top:0.85rem;'>"
                f"<strong>Recommended task:</strong> {recommended.get('title')}<br />"
                f"{recommended.get('priority', '').title()} priority, due {deps['format_due'](recommended)}, status {deps['status_label'](recommended.get('status', 'todo'))}."
                "</div>",
                unsafe_allow_html=True,
            )

    planner_tab, scheduler_tab, review_tab = st_module.tabs(["Planner", "Scheduler", "Review"])

    with planner_tab:
        st_module.markdown('<div class="panel ai-response-card">', unsafe_allow_html=True)
        st_module.markdown('<div class="panel-title"><h3>Plan Builder</h3><span>Ask for a focused day plan or a task rescue plan</span></div>', unsafe_allow_html=True)
        if generate_plan_clicked:
            result, error, suggestions = deps["generate_ai_plan"](tasks, ai_prompt)
            st_module.session_state.ai_response = result
            st_module.session_state.ai_error = error
            st_module.session_state.ai_suggestions = suggestions
        if st_module.session_state.ai_error:
            st_module.warning(st_module.session_state.ai_error)
        if st_module.session_state.ai_response:
            st_module.markdown(st_module.session_state.ai_response)
        if st_module.session_state.ai_suggestions:
            st_module.caption(f"Suggested tasks detected: {len(st_module.session_state.ai_suggestions)}")
            if st_module.button("Add Suggested Tasks", type="primary", key=f"{panel_key}_apply_suggested"):
                deps["apply_ai_suggestions"](st_module.session_state.ai_suggestions)
                added_count = len(st_module.session_state.ai_suggestions)
                st_module.session_state.ai_suggestions = []
                st_module.success(f"Added {added_count} suggested task(s).")
                st_module.rerun()
        if not st_module.session_state.ai_response and not st_module.session_state.ai_error:
            st_module.markdown('<div class="empty-state">Generate a plan to turn the task board into a sequence of actions.</div>', unsafe_allow_html=True)
        st_module.markdown('</div>', unsafe_allow_html=True)

    with scheduler_tab:
        st_module.markdown('<div class="panel ai-response-card">', unsafe_allow_html=True)
        st_module.markdown('<div class="panel-title"><h3>Scheduler</h3><span>Auto-place work into realistic blocks</span></div>', unsafe_allow_html=True)
        if auto_schedule_clicked:
            schedule_text, schedule_error, schedule_updates = deps["generate_ai_schedule"](active_tasks, ai_prompt)
            st_module.session_state.ai_schedule_error = schedule_error
            st_module.session_state.ai_schedule_updates = schedule_updates
            if schedule_text:
                st_module.session_state.ai_response = schedule_text
        if st_module.session_state.ai_schedule_error:
            st_module.warning(st_module.session_state.ai_schedule_error)
        if st_module.session_state.ai_response:
            st_module.markdown(st_module.session_state.ai_response)
        if st_module.session_state.ai_schedule_updates:
            st_module.caption(f"Schedule updates detected: {len(st_module.session_state.ai_schedule_updates)}")
            if st_module.button("Apply Auto-Schedule", type="secondary", key=f"{panel_key}_apply_schedule"):
                deps["apply_ai_schedule_updates"](st_module.session_state.ai_schedule_updates)
                applied_count = len(st_module.session_state.ai_schedule_updates)
                st_module.session_state.ai_schedule_updates = []
                st_module.success(f"Applied {applied_count} schedule update(s).")
                st_module.rerun()
        if st_module.session_state.ai_schedule_updates:
            st_module.markdown("<div class='empty-state' style='text-align:left;'>AI generated schedule updates are ready to apply.</div>", unsafe_allow_html=True)
        elif not st_module.session_state.ai_schedule_error:
            st_module.markdown('<div class="empty-state">Run auto-schedule to slot tasks into the week.</div>', unsafe_allow_html=True)
        st_module.markdown('</div>', unsafe_allow_html=True)

    with review_tab:
        st_module.markdown('<div class="panel ai-response-card">', unsafe_allow_html=True)
        st_module.markdown('<div class="panel-title"><h3>Review Lens</h3><span>Use AI as a fast retrospective and tomorrow planner</span></div>', unsafe_allow_html=True)
        review_input = st_module.text_area(
            "Review notes",
            value="Highlight what slipped today, what got done, and what should happen first tomorrow.",
            height=100,
            key=f"{panel_key}_review_prompt",
        )
        if st_module.button("Generate Review Summary", key=f"{panel_key}_gen_review", type="primary"):
            completed_today_tasks = [task for task in tasks if task.get("status") == "completed" and task.get("completed_date") == date.today()]
            review_text, tomorrow_text, review_error = deps["generate_daily_review"](active_tasks, completed_today_tasks, review_input)
            st_module.session_state.daily_review_text = review_text
            st_module.session_state.tomorrow_plan_text = tomorrow_text
            st_module.session_state.daily_review_error = review_error
        if st_module.session_state.daily_review_error:
            st_module.warning(st_module.session_state.daily_review_error)
        if st_module.session_state.daily_review_text:
            st_module.markdown(st_module.session_state.daily_review_text)
        if st_module.session_state.tomorrow_plan_text:
            st_module.markdown(st_module.session_state.tomorrow_plan_text)
        if not st_module.session_state.daily_review_text and not st_module.session_state.daily_review_error:
            st_module.markdown('<div class="empty-state">Use the review tab to close out the day and draft tomorrow.</div>', unsafe_allow_html=True)
        st_module.markdown('</div>', unsafe_allow_html=True)

    st_module.markdown('</div>', unsafe_allow_html=True)
