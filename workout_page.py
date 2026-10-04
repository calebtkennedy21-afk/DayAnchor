from datetime import date

import pandas as pd
import streamlit as st

import workout_core as wc


def _days_to_rows(days):
    rows = []
    for day in days:
        for ex in day["exercises"] or [None]:
            rows.append(
                {
                    "Day": day["name"],
                    "Focus": day["focus"],
                    "Exercise": ex["name"] if ex else "",
                    "Sets": ex["sets"] if ex else 0,
                    "Reps": ex["reps"] if ex else "",
                    "Notes": ex["notes"] if ex else day["notes"],
                }
            )
    return rows


def _rows_to_days(rows):
    days = {}
    for row in rows:
        day_name = str(row.get("Day") or "").strip() or "Day 1"
        day = days.setdefault(day_name, {"name": day_name, "focus": "", "exercises": [], "notes": ""})
        day["focus"] = day["focus"] or str(row.get("Focus") or "").strip()
        exercise_name = str(row.get("Exercise") or "").strip()
        if exercise_name:
            day["exercises"].append(
                {"name": exercise_name, "sets": row.get("Sets"), "reps": row.get("Reps"), "notes": row.get("Notes")}
            )
        elif row.get("Notes"):
            day["notes"] = str(row["Notes"])
    return wc.normalize_plan_days(list(days.values()))


def render_workout_panel(app_settings, save_app_settings_fn, parse_plan_fn, today=None, panel_key="workouts", st_module=st):
    today = today or date.today()
    plans = wc.normalize_plans(app_settings.get("workout_plans"))
    logs = wc.normalize_logs(app_settings.get("workout_logs"))

    def save(new_plans=None, new_logs=None):
        save_app_settings_fn(
            {
                **app_settings,
                "workout_plans": new_plans if new_plans is not None else plans,
                "workout_logs": new_logs if new_logs is not None else logs,
            }
        )

    plans_tab, log_tab, progress_tab, history_tab = st_module.tabs(["Plans", "Log Workout", "Progress", "History"])

    with plans_tab:
        _render_plans_tab(st_module, plans, save, parse_plan_fn, today, panel_key)
    with log_tab:
        _render_log_tab(st_module, plans, logs, save, today, panel_key)
    with progress_tab:
        _render_progress_tab(st_module, plans, logs, today, panel_key)
    with history_tab:
        _render_history_tab(st_module, logs, save, panel_key)


def _render_plans_tab(st_module, plans, save, parse_plan_fn, today, panel_key):
    st_module.markdown("#### Import a plan")
    upload = st_module.file_uploader("Workout plan PDF", type=["pdf"], key=f"{panel_key}_upload")
    pasted = st_module.text_area("...or paste plan text", height=120, key=f"{panel_key}_paste")
    draft_key = f"{panel_key}_draft"

    if st_module.button("Parse plan", key=f"{panel_key}_parse", disabled=not (upload or pasted.strip())):
        text, source = pasted, "pasted text"
        if upload:
            source = upload.name
            try:
                text = wc.extract_pdf_text(upload.getvalue())
            except Exception as exc:
                st_module.error(f"Could not read PDF: {exc}")
                text = ""
        if text.strip():
            with st_module.spinner("Parsing plan..."):
                days, message = parse_plan_fn(text)
            if message:
                st_module.warning(message)
            if days:
                st_module.session_state[draft_key] = {"days": days, "source": source}
            else:
                st_module.error("No workout days were found. If the PDF is scanned images, paste the text instead.")
        elif upload:
            st_module.error("No extractable text in the PDF. If it is scanned images, paste the text instead.")

    draft = st_module.session_state.get(draft_key)
    if draft:
        st_module.markdown("#### Review parsed plan")
        st_module.caption("Edit any cell, add or remove rows, then save.")
        plan_name = st_module.text_input("Plan name", value=draft["source"].rsplit(".", 1)[0], key=f"{panel_key}_plan_name")
        edited = st_module.data_editor(
            pd.DataFrame(_days_to_rows(draft["days"])),
            num_rows="dynamic",
            use_container_width=True,
            key=f"{panel_key}_draft_editor",
        )
        save_col, discard_col = st_module.columns(2)
        if save_col.button("Save as active plan", type="primary", key=f"{panel_key}_save_plan"):
            days = _rows_to_days(edited.fillna("").to_dict("records"))
            if not days:
                st_module.warning("Plan has no days.")
            else:
                new_plan = wc.build_plan(plan_name, days, draft["source"], today)
                updated = [{**plan, "active": False} for plan in plans] + [new_plan]
                del st_module.session_state[draft_key]
                save(new_plans=updated)
                st_module.rerun()
        if discard_col.button("Discard", key=f"{panel_key}_discard_plan"):
            del st_module.session_state[draft_key]
            st_module.rerun()

    st_module.markdown("#### Saved plans")
    if not plans:
        st_module.caption("No plans yet. Upload a PDF above.")
    for plan in plans:
        label = f"{plan['name']}{' (active)' if plan['active'] else ''}"
        with st_module.expander(label, expanded=plan["active"]):
            st_module.caption(f"Source: {plan['source_file'] or 'manual'} | Added {plan['created_date']}")
            for day in plan["days"]:
                st_module.markdown(f"**{day['name']}**{' - ' + day['focus'] if day['focus'] else ''}")
                for ex in day["exercises"]:
                    sets_reps = f"{ex['sets']} x {ex['reps']}" if ex["sets"] else ex["reps"]
                    note = f" _{ex['notes']}_" if ex["notes"] else ""
                    st_module.markdown(f"- {ex['name']}: {sets_reps}{note}")
            action_cols = st_module.columns(2)
            if not plan["active"] and action_cols[0].button("Make active", key=f"{panel_key}_activate_{plan['plan_id']}"):
                save(new_plans=[{**p, "active": p["plan_id"] == plan["plan_id"]} for p in plans])
                st_module.rerun()
            if action_cols[1].button("Delete plan", key=f"{panel_key}_delete_{plan['plan_id']}"):
                save(new_plans=[p for p in plans if p["plan_id"] != plan["plan_id"]])
                st_module.rerun()


def _render_log_tab(st_module, plans, logs, save, today, panel_key):
    active = next((plan for plan in plans if plan["active"]), None)
    plan_days = active["days"] if active else []

    log_date = st_module.date_input("Date", value=today, key=f"{panel_key}_log_date")
    day_options = [day["name"] for day in plan_days] + ["Freestyle"]
    if active:
        done_this_week = {
            log["day_name"]
            for log in logs
            if log["plan_id"] == active["plan_id"] and wc.week_start(date.fromisoformat(log["date"])) == wc.week_start(log_date)
        }
        st_module.caption(
            f"Active plan: {active['name']}. Done this week: {', '.join(sorted(done_this_week)) or 'none yet'}."
        )
    else:
        st_module.caption("No active plan; logging a freestyle workout.")
    day_name = st_module.selectbox("Workout day", day_options, key=f"{panel_key}_log_day")

    selected = next((day for day in plan_days if day["name"] == day_name), None)
    seed = [
        {"Exercise": ex["name"], "Sets": ex["sets"], "Reps": ex["reps"], "Weight": 0.0, "Done": True}
        for ex in (selected["exercises"] if selected else [])
    ] or [{"Exercise": "", "Sets": 3, "Reps": "10", "Weight": 0.0, "Done": True}]

    if selected:
        planned_notes = [f"{ex['name']}: {ex['notes']}" for ex in selected["exercises"] if ex["notes"]]
        if planned_notes:
            with st_module.expander("Plan notes"):
                for line in planned_notes:
                    st_module.markdown(f"- {line}")

    editor_key = f"{panel_key}_log_editor_{active['plan_id'] if active else 'none'}_{day_name}"
    edited = st_module.data_editor(
        pd.DataFrame(seed),
        num_rows="dynamic",
        use_container_width=True,
        key=editor_key,
        column_config={
            "Weight": st_module.column_config.NumberColumn("Weight (lb)", min_value=0.0, step=2.5),
            "Done": st_module.column_config.CheckboxColumn("Done"),
        },
    )
    duration_col, effort_col = st_module.columns(2)
    duration = duration_col.number_input("Duration (min)", min_value=0, max_value=600, value=45, step=5, key=f"{panel_key}_log_duration")
    effort = effort_col.slider("Effort (RPE 1-10)", 1, 10, 7, key=f"{panel_key}_log_effort")
    notes = st_module.text_area("Notes", key=f"{panel_key}_log_notes", height=80)

    if st_module.button("Save workout", type="primary", key=f"{panel_key}_log_save"):
        rows = edited.fillna("").to_dict("records")
        exercises = [
            {"name": r["Exercise"], "sets": r["Sets"], "reps": r["Reps"], "weight": r["Weight"], "done": bool(r["Done"])}
            for r in rows
        ]
        entry = wc.build_log(log_date, active["plan_id"] if active and selected else "", day_name, exercises, duration, effort, notes)
        if not entry["exercises"]:
            st_module.warning("Add at least one exercise.")
        else:
            save(new_logs=[entry] + logs)
            st_module.success("Workout saved.")
            st_module.rerun()


def _render_progress_tab(st_module, plans, logs, today, panel_key):
    if not logs:
        st_module.info("Log a workout to see progress.")
        return
    active = next((plan for plan in plans if plan["active"]), None)
    planned = len([d for d in active["days"] if d["exercises"]]) if active else 0
    weekly = wc.weekly_summary(logs, planned, today=today)
    this_week = weekly[-1]

    metric_cols = st_module.columns(4)
    metric_cols[0].metric("This week", f"{this_week['workouts']}" + (f" / {planned}" if planned else ""))
    metric_cols[1].metric("Adherence (8 wk)", _avg_adherence(weekly))
    metric_cols[2].metric("Week streak", wc.current_streak_weeks(weekly))
    efforts = [row["avg_effort"] for row in weekly if row["avg_effort"]]
    metric_cols[3].metric("Avg effort", f"{sum(efforts) / len(efforts):.1f}" if efforts else "-")

    frame = pd.DataFrame(weekly).set_index("week")
    st_module.markdown("**Workouts per week**")
    st_module.bar_chart(frame["workouts"])
    if planned:
        st_module.markdown("**Adherence % per week**")
        st_module.bar_chart(frame["adherence_pct"])
    st_module.markdown("**Training volume per week** (sets x reps x weight)")
    st_module.line_chart(frame["volume"])

    names = wc.logged_exercise_names(logs)
    st_module.markdown("**Exercise progression**")
    choice = st_module.selectbox("Exercise", names, key=f"{panel_key}_progress_exercise")
    points = wc.exercise_progression(logs, choice)
    if points:
        st_module.line_chart(pd.DataFrame(points).set_index("date")["top_weight"])
    else:
        st_module.caption("No completed sets for this exercise yet.")


def _avg_adherence(weekly):
    values = [row["adherence_pct"] for row in weekly if row["adherence_pct"] is not None]
    return f"{round(sum(values) / len(values))}%" if values else "-"


def _render_history_tab(st_module, logs, save, panel_key):
    if not logs:
        st_module.caption("No workouts logged yet.")
        return
    for log in logs[:50]:
        title = f"{log['date']} - {log['day_name'] or 'Workout'} ({log['duration_min']} min, RPE {log['effort']})"
        with st_module.expander(title):
            for ex in log["exercises"]:
                weight = f" @ {ex['weight']:g} lb" if ex["weight"] else ""
                mark = "" if ex["done"] else " (skipped)"
                st_module.markdown(f"- {ex['name']}: {ex['sets']} x {ex['reps']}{weight}{mark}")
            if log["notes"]:
                st_module.caption(log["notes"])
            if st_module.button("Delete", key=f"{panel_key}_hist_del_{log['log_id']}"):
                save(new_logs=[item for item in logs if item["log_id"] != log["log_id"]])
                st_module.rerun()
