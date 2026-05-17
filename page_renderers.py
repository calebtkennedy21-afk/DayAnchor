from datetime import date, timedelta
import calendar

import streamlit as st


def render_task_list_panel(title, subtitle, tasks_to_render, key_prefix, empty_text, render_task_card_fn, st_module=st):
    st_module.markdown('<div class="panel">', unsafe_allow_html=True)
    st_module.markdown(f'<div class="panel-title"><h3>{title}</h3><span>{subtitle}</span></div>', unsafe_allow_html=True)
    if tasks_to_render:
        for task in tasks_to_render:
            render_task_card_fn(task, key_prefix=key_prefix)
    else:
        st_module.markdown(f'<div class="empty-state">{empty_text}</div>', unsafe_allow_html=True)
    st_module.markdown('</div>', unsafe_allow_html=True)


def render_task_calendar_panel(tasks, panel_key, title, subtitle, render_task_calendar_compact_fn, st_module=st):
    st_module.markdown('<div class="panel">', unsafe_allow_html=True)
    st_module.markdown(f'<div class="panel-title"><h3>{title}</h3><span>{subtitle}</span></div>', unsafe_allow_html=True)
    st_module.caption("Legend: S = scheduled tasks, D = tasks due, C = tasks completed. Darker day badges indicate heavier total load.")

    month_key = f"{panel_key}_month_anchor"
    if month_key not in st_module.session_state:
        st_module.session_state[month_key] = date.today().replace(day=1)

    controls = st_module.columns([1, 2, 1])
    with controls[0]:
        if st_module.button("Prev month", key=f"{panel_key}_prev"):
            current = st_module.session_state[month_key]
            previous_month_end = current - timedelta(days=1)
            st_module.session_state[month_key] = previous_month_end.replace(day=1)
            st_module.rerun()
    with controls[1]:
        anchor = st_module.session_state[month_key]
        st_module.markdown(
            f"<div style='text-align:center; font-weight:700; margin-top:0.4rem;'>{calendar.month_name[anchor.month]} {anchor.year}</div>",
            unsafe_allow_html=True,
        )
    with controls[2]:
        if st_module.button("Next month", key=f"{panel_key}_next"):
            current = st_module.session_state[month_key]
            next_month_start = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
            st_module.session_state[month_key] = next_month_start
            st_module.rerun()

    render_task_calendar_compact_fn(tasks, st_module.session_state[month_key])
    st_module.markdown('</div>', unsafe_allow_html=True)
