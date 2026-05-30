from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import calendar

import streamlit as st


MOUNTAIN_TIMEZONE = ZoneInfo("America/Denver")


def mountain_today():
    return datetime.now(MOUNTAIN_TIMEZONE).date()


WEEKDAY_ASSIGNMENT_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


def _default_calendar_weekday_assignments():
    return {
        "Monday": ["BB clinic"],
        "Tuesday": ["Office day"],
        "Wednesday": ["WFH personal catch-up"],
        "Thursday": ["BB clinic"],
        "Friday": ["Dr. Rozek TenJet"],
    }


def _normalize_labels(value):
    if isinstance(value, list):
        labels = [str(item).strip() for item in value if str(item).strip()]
    elif isinstance(value, str):
        labels = [item.strip() for item in value.split(",") if item.strip()]
    else:
        labels = []
    seen = set()
    deduped = []
    for label in labels:
        key = label.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(label)
    return deduped


def _normalize_weekday_assignments(raw):
    defaults = _default_calendar_weekday_assignments()
    normalized = {}
    for day in WEEKDAY_ASSIGNMENT_DAYS:
        labels = []
        if isinstance(raw, dict):
            labels = _normalize_labels(raw.get(day))
        normalized[day] = labels or list(defaults[day])
    return normalized


def _normalize_date_overrides(raw):
    if not isinstance(raw, dict):
        return {}
    normalized = {}
    for raw_date, raw_labels in raw.items():
        try:
            parsed = date.fromisoformat(str(raw_date))
        except ValueError:
            continue
        labels = _normalize_labels(raw_labels)
        if labels:
            normalized[parsed.isoformat()] = labels
    return dict(sorted(normalized.items()))


def _format_date_overrides_text(overrides):
    if not overrides:
        return ""
    lines = []
    for day, labels in sorted(overrides.items()):
        lines.append(f"{day} = {', '.join(labels)}")
    return "\n".join(lines)


def _parse_date_overrides_text(text):
    if not text.strip():
        return {}, None
    overrides = {}
    errors = []
    for index, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if "=" not in line:
            errors.append(f"line {index}: missing '='")
            continue
        day_text, labels_text = line.split("=", 1)
        day_text = day_text.strip()
        labels = _normalize_labels(labels_text)
        try:
            parsed_day = date.fromisoformat(day_text)
        except ValueError:
            errors.append(f"line {index}: invalid date '{day_text}'")
            continue
        if not labels:
            continue
        overrides[parsed_day.isoformat()] = labels

    if errors:
        return None, "; ".join(errors)
    return dict(sorted(overrides.items())), None


def render_task_list_panel(title, subtitle, tasks_to_render, key_prefix, empty_text, render_task_card_fn, st_module=st, max_items=None):
    st_module.markdown('<div class="panel">', unsafe_allow_html=True)
    st_module.markdown(f'<div class="panel-title"><h3>{title}</h3><span>{subtitle}</span></div>', unsafe_allow_html=True)
    if tasks_to_render:
        visible_tasks = tasks_to_render
        if isinstance(max_items, int) and max_items > 0:
            visible_tasks = tasks_to_render[:max_items]
        for task in visible_tasks:
            render_task_card_fn(task, key_prefix=key_prefix)
        if len(visible_tasks) < len(tasks_to_render):
            st_module.caption(f"Showing {len(visible_tasks)} of {len(tasks_to_render)} tasks.")
    else:
        st_module.markdown(f'<div class="empty-state">{empty_text}</div>', unsafe_allow_html=True)
    st_module.markdown('</div>', unsafe_allow_html=True)


def render_task_calendar_panel(tasks, panel_key, title, subtitle, render_task_calendar_compact_fn, app_settings=None, save_app_settings_fn=None, st_module=st):
    st_module.markdown('<div class="panel">', unsafe_allow_html=True)
    st_module.markdown(f'<div class="panel-title"><h3>{title}</h3><span>{subtitle}</span></div>', unsafe_allow_html=True)
    st_module.markdown(
        """
        <div style='display:flex; flex-wrap:wrap; gap:0.5rem; align-items:center; margin:0.2rem 0 0.8rem;'>
            <span style='display:inline-flex; align-items:center; gap:0.35rem; font-size:0.8rem;'><span style='width:0.8rem; height:0.8rem; border-radius:999px; background:#dbeafe; display:inline-block;'></span>Scheduled</span>
            <span style='display:inline-flex; align-items:center; gap:0.35rem; font-size:0.8rem;'><span style='width:0.8rem; height:0.8rem; border-radius:999px; background:#fee2e2; display:inline-block;'></span>Due</span>
            <span style='display:inline-flex; align-items:center; gap:0.35rem; font-size:0.8rem;'><span style='width:0.8rem; height:0.8rem; border-radius:999px; background:#dcfce7; display:inline-block;'></span>Completed</span>
            <span style='display:inline-flex; align-items:center; gap:0.35rem; font-size:0.8rem;'><span style='width:0.8rem; height:0.8rem; border-radius:999px; background:#d1fae5; display:inline-block;'></span>Provider/Assignment</span>
            <span style='display:inline-flex; align-items:center; gap:0.35rem; font-size:0.8rem;'><span style='width:0.8rem; height:0.8rem; border-radius:999px; background:#e0e7ff; display:inline-block;'></span>OR</span>
            <span style='display:inline-flex; align-items:center; gap:0.35rem; font-size:0.8rem;'><span style='width:0.8rem; height:0.8rem; border-radius:999px; background:#ffedd5; display:inline-block;'></span>Procedure Friday</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    month_key = f"{panel_key}_month_anchor"
    if month_key not in st_module.session_state:
        st_module.session_state[month_key] = mountain_today().replace(day=1)

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

    if save_app_settings_fn and isinstance(app_settings, dict):
        with st_module.expander("Edit calendar assignments", expanded=False):
            st_module.caption("Set recurring weekday assignments and optional date-specific overrides when the week changes.")
            reset_flag_key = f"{panel_key}_reset_calendar_editor_pending"
            override_text_key = f"{panel_key}_date_overrides_text"
            if st_module.session_state.pop(reset_flag_key, False):
                defaults = _default_calendar_weekday_assignments()
                for day in WEEKDAY_ASSIGNMENT_DAYS:
                    day_key = f"{panel_key}_assignment_{day.lower()}"
                    st_module.session_state[day_key] = ", ".join(defaults[day])
                st_module.session_state[override_text_key] = ""

            current_assignments = _normalize_weekday_assignments(app_settings.get("calendar_weekday_assignments"))
            assignment_cols = st_module.columns(5)
            for index, day in enumerate(WEEKDAY_ASSIGNMENT_DAYS):
                day_key = f"{panel_key}_assignment_{day.lower()}"
                if day_key not in st_module.session_state:
                    st_module.session_state[day_key] = ", ".join(current_assignments[day])
                with assignment_cols[index]:
                    st_module.text_input(day, key=day_key, help="Comma-separated labels")

            current_overrides = _normalize_date_overrides(app_settings.get("calendar_date_overrides"))
            if override_text_key not in st_module.session_state:
                st_module.session_state[override_text_key] = _format_date_overrides_text(current_overrides)
            st_module.text_area(
                "Date overrides",
                key=override_text_key,
                height=120,
                help="One per line: YYYY-MM-DD = Label A, Label B",
            )

            save_col, reset_col = st_module.columns(2)
            with save_col:
                if st_module.button("Save calendar assignments", key=f"{panel_key}_save_calendar_assignments", type="secondary"):
                    updated_assignments = {}
                    defaults = _default_calendar_weekday_assignments()
                    for day in WEEKDAY_ASSIGNMENT_DAYS:
                        day_key = f"{panel_key}_assignment_{day.lower()}"
                        labels = _normalize_labels(st_module.session_state.get(day_key, ""))
                        updated_assignments[day] = labels or defaults[day]

                    parsed_overrides, parse_error = _parse_date_overrides_text(st_module.session_state.get(override_text_key, ""))
                    if parse_error:
                        st_module.error(f"Unable to save overrides: {parse_error}")
                    else:
                        save_app_settings_fn(
                            {
                                **app_settings,
                                "calendar_weekday_assignments": updated_assignments,
                                "calendar_date_overrides": parsed_overrides,
                            }
                        )
                        st_module.success("Calendar assignments saved.")
                        st_module.rerun()
            with reset_col:
                if st_module.button("Reset editor", key=f"{panel_key}_reset_calendar_editor"):
                    st_module.session_state[reset_flag_key] = True
                    st_module.rerun()

    render_task_calendar_compact_fn(tasks, st_module.session_state[month_key], app_settings)
    st_module.markdown('</div>', unsafe_allow_html=True)
