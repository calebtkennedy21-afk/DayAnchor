from datetime import datetime

from data_access import MOUNTAIN_TIMEZONE


_BRAIN_DUMP_NOTE_KEYS = {
    "Clinic": ("clinical_notes", "clinical_notes_updated_at"),
    "Family": ("family_notes", "family_notes_updated_at"),
}


def apply_brain_dump_decision(entry, decision, app_settings, updates, status_prefix=""):
    """Apply non-task brain-dump decisions to the pending settings updates."""
    item_type = decision.get("item_type") or "task"
    if item_type == "task":
        raise ValueError("Task decisions must use the atomic brain-dump conversion operation.")

    category = decision.get("category") or entry.get("category") or "Personal"
    title = decision.get("title") or entry.get("title") or entry.get("raw_text") or "Untitled thought"
    now_iso = datetime.now(MOUNTAIN_TIMEZONE).isoformat(timespec="seconds")
    if item_type == "reminder":
        reminders = updates.setdefault("quick_reminders", list(app_settings.get("quick_reminders") or []))
        reminders.append(
            {
                "reminder_id": str(entry.get("dump_id")),
                "text": title,
                "category": category,
                "remind_date": decision.get("due_date"),
                "remind_time": decision.get("scheduled_time"),
                "status": "active",
                "created_at": now_iso,
            }
        )
    else:
        note_key, stamp_key = _BRAIN_DUMP_NOTE_KEYS.get(category, ("personal_notes", "personal_notes_updated_at"))
        existing_note = str(updates.get(note_key, app_settings.get(note_key)) or "").strip()
        updates[note_key] = f"{existing_note}\n- {entry.get('raw_text') or title}".strip()
        updates[stamp_key] = now_iso
    entry["status"] = f"{status_prefix}converted_{item_type}"


def handle_manual_brain_dump_task(
    entry,
    app_settings,
    today,
    *,
    convert_fn,
    db_enabled_fn,
    get_connection_fn,
    st_module,
):
    result = convert_fn(
        [{**entry, "item_type": "task"}],
        today,
        session_settings=app_settings,
        db_enabled_fn=db_enabled_fn,
        get_connection_fn=get_connection_fn,
        st_module=st_module,
    )
    if result.status in {"created", "already_applied"}:
        st_module.session_state["app_settings"] = result.settings
        if result.mode == "session":
            st_module.info("Task conversion is available for this session only.")
        else:
            st_module.success("Converted to task.")
        return result, True

    st_module.error(result.message)
    return result, False


def handle_ai_brain_dump_apply(
    entries,
    ai_triage,
    app_settings,
    today,
    panel_key,
    triage_key,
    triage_error_key,
    *,
    convert_fn,
    db_enabled_fn,
    get_connection_fn,
    resolve_proposal_fn,
    st_module,
):
    entry_by_id = {str(item.get("dump_id")): item for item in entries}
    decisions = []
    for proposal in ai_triage:
        dump_id = str(proposal.get("dump_id"))
        entry = entry_by_id.get(dump_id)
        if not entry or str(entry.get("status") or "new") != "new":
            continue
        if not st_module.session_state.get(f"{panel_key}_ai_apply_{dump_id}", True):
            continue
        decisions.append(
            {
                **proposal,
                "item_type": st_module.session_state.get(f"{panel_key}_ai_type_{dump_id}")
                or proposal.get("item_type"),
                "title": st_module.session_state.get(f"{panel_key}_ai_title_{dump_id}")
                or proposal.get("title"),
            }
        )

    if not decisions:
        resolve_proposal_fn("capture", "rejected", "rejected", "nothing selected")
        st_module.session_state[triage_key] = []
        st_module.session_state[triage_error_key] = ""
        return None, True

    event_id = st_module.session_state.get("ai_open_event_ids", {}).get("capture")
    if event_id is None:
        st_module.error("The AI triage proposal could not be verified. No decisions were applied.")
        return None, False

    result = convert_fn(
        decisions,
        today,
        session_settings=app_settings,
        event_id=event_id,
        status_prefix="ai_",
        db_enabled_fn=db_enabled_fn,
        get_connection_fn=get_connection_fn,
        st_module=st_module,
    )
    if result.status in {"created", "already_applied", "applied"}:
        st_module.session_state["app_settings"] = result.settings
        st_module.session_state[triage_key] = []
        st_module.session_state[triage_error_key] = ""
        open_ids = st_module.session_state.get("ai_open_event_ids", {})
        if open_ids.get("capture") == event_id:
            open_ids.pop("capture", None)
        if result.mode == "session":
            st_module.info(f"{result.message} These changes are session-only.")
        else:
            st_module.success(result.message)
        return result, True

    st_module.error(result.message)
    return result, False
