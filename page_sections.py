from datetime import date, time, timedelta
import calendar

import streamlit as st


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
            st_module.markdown('<div class="panel-title" style="margin-top:1rem;"><h3>Next actions</h3><span>What should move first</span></div>', unsafe_allow_html=True)
            for task in overview_focus:
                attention = deps["task_attention_signal"](task, date.today())
                st_module.markdown(
                    f"- <strong>{task['title']}</strong> · {attention['label']} · {task['category']} · {task['priority'].title()} · {deps['format_due'](task)}",
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
        st_module.markdown('<div class="panel-title"><h3>Capture</h3><span>Use one shared entry point</span></div>', unsafe_allow_html=True)
        default_lane = "Clinic" if lens_choice in ("Clinic day", "Procedure Friday") else "Personal"
        st_module.markdown(
            f"<div class='empty-state' style='text-align:left;'><strong>Quick capture moved to the sidebar.</strong><br />Open <strong>Quick capture</strong> to add tasks from any page.<br /><strong>Suggested lane right now:</strong> {default_lane}</div>",
            unsafe_allow_html=True,
        )
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
    st_module.caption("Store procedure name, date, and anatomical location only. Do not enter patient identifiers.")

    st_module.markdown('<div class="panel-title" style="margin-top:0.5rem;"><h3>Protocol Library</h3><span>Upload and reference BB protocols</span></div>', unsafe_allow_html=True)
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
        for doc in protocol_documents[:12]:
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
    else:
        st_module.markdown('<div class="empty-state">No protocols uploaded yet. Add BB protocols to reference during case prep.</div>', unsafe_allow_html=True)

    st_module.markdown('<div style="height: 0.8rem;"></div>', unsafe_allow_html=True)

    metrics = st_module.columns(4)
    planned_cases = [item for item in surgical_cases if item.get("status") == "planned"]
    completed_cases = [item for item in surgical_cases if item.get("status") == "completed"]
    tenjet_cases = [item for item in surgical_cases if item.get("case_stream") == "TenJet"]
    main_or_cases = [item for item in surgical_cases if item.get("case_stream") == "Main OR"]
    metrics[0].metric("Planned", len(planned_cases))
    metrics[1].metric("Completed", len(completed_cases))
    metrics[2].metric("Main OR", len(main_or_cases))
    metrics[3].metric("TenJet", len(tenjet_cases))

    top_left, top_right = st_module.columns([1.1, 0.9], gap="large")
    with top_left:
        with st_module.form(f"{panel_key}_new_case_form"):
            case_date = st_module.date_input("Case date", value=date.today())
            case_stream = st_module.selectbox("Case stream", ["Main OR", "DSC OR", "TenJet"])
            procedure_name = st_module.text_input("Procedure performed")
            anatomical_location = st_module.text_input("Anatomical location")
            status = st_module.selectbox("Status", ["planned", "completed", "canceled"])
            notes = st_module.text_area("Notes (non-PHI)", height=80)
            education_url = st_module.text_input("Education link (optional)", placeholder="https://...")
            education_notes = st_module.text_area("Educational description", height=90, placeholder="What the case is, key anatomy, technical pearls, postop points...")
            submit_case = st_module.form_submit_button("Add surgical case", type="primary")

        if submit_case:
            if not procedure_name.strip():
                st_module.warning("Add the procedure name before saving.")
            else:
                deps["add_surgical_case"](
                    case_date=case_date,
                    case_stream=case_stream,
                    procedure_name=procedure_name,
                    anatomical_location=anatomical_location,
                    status=status,
                    notes=notes,
                    education_url=education_url,
                    education_notes=education_notes,
                )
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
    st_module.markdown('<div class="panel-title"><h3>Recent Cases</h3><span>Track what was scheduled and what was done</span></div>', unsafe_allow_html=True)
    if surgical_cases:
        for item in surgical_cases[:20]:
            case_id = item.get("id")
            case_date_value = item.get("case_date")
            date_label = case_date_value.strftime("%b %d, %Y") if hasattr(case_date_value, "strftime") else str(case_date_value)
            or_hint = predicted_labels.get(case_date_value)
            hint_suffix = f" · {or_hint}" if or_hint else ""
            st_module.markdown(
                f"<div class='task-card'><div class='task-title'>{item.get('procedure_name')}</div>"
                f"<div class='task-meta'><span class='pill'>{date_label}{hint_suffix}</span><span class='pill pill-category'>{item.get('case_stream')}</span><span class='pill pill-status'>{str(item.get('status', 'planned')).title()}</span><span class='pill'>{item.get('anatomical_location') or 'Location not specified'}</span></div>"
                f"<p style='margin-top:0.6rem;'>{item.get('notes') or ''}</p></div>",
                unsafe_allow_html=True,
            )
            if item.get("education_url"):
                st_module.markdown(f"[Case Education Link]({item.get('education_url')})")
            if item.get("education_notes"):
                with st_module.expander("Educational Description", expanded=False):
                    st_module.write(item.get("education_notes"))

            suggestions = deps["suggest_protocols_for_case"](item, protocol_documents, max_items=3)
            if suggestions:
                st_module.markdown("**Suggested Protocols**")
                for score, overlap_terms, doc in suggestions:
                    doc_id = doc.get("id")
                    doc_bytes = doc.get("file_bytes")
                    if isinstance(doc_bytes, memoryview):
                        doc_bytes = bytes(doc_bytes)
                    st_module.markdown(
                        f"- **{doc.get('protocol_name')}** (match score: {score}) · keywords: {', '.join(overlap_terms)}",
                        unsafe_allow_html=True,
                    )
                    if doc_bytes:
                        st_module.download_button(
                            label=f"Download {doc.get('file_name')}",
                            data=doc_bytes,
                            file_name=doc.get("file_name") or "protocol.pdf",
                            mime=doc.get("file_mime") or "application/octet-stream",
                            key=f"{panel_key}_case_suggested_download_{case_id}_{doc_id}",
                        )
            row_cols = st_module.columns([1, 1, 1])
            with row_cols[0]:
                new_status = st_module.selectbox(
                    "Status",
                    ["planned", "completed", "canceled"],
                    index=["planned", "completed", "canceled"].index(item.get("status", "planned")) if item.get("status", "planned") in ["planned", "completed", "canceled"] else 0,
                    key=f"{panel_key}_status_{case_id}",
                    label_visibility="collapsed",
                )
            with row_cols[1]:
                if st_module.button("Update", key=f"{panel_key}_update_{case_id}"):
                    deps["update_surgical_case"](case_id, status=new_status)
                    st_module.success("Case status updated.")
                    st_module.rerun()
            with row_cols[2]:
                if st_module.button("Delete", key=f"{panel_key}_delete_{case_id}"):
                    deps["delete_surgical_case"](case_id)
                    st_module.success("Case deleted.")
                    st_module.rerun()
    else:
        st_module.markdown('<div class="empty-state">No surgical cases logged yet.</div>', unsafe_allow_html=True)

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
