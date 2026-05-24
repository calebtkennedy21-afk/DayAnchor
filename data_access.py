from datetime import date, datetime, time, timedelta

import psycopg
from psycopg.rows import dict_row
import streamlit as st


def _task_visible_in_app(task, reference_time=None):
    if task.get("status") != "completed":
        return True

    now = reference_time or datetime.utcnow()
    completed_at = task.get("completed_at")
    if isinstance(completed_at, datetime):
        return now - completed_at <= timedelta(hours=24)

    completed_date = task.get("completed_date")
    if isinstance(completed_date, date):
        return now - datetime.combine(completed_date, time.min) <= timedelta(hours=24)

    return True


def load_tasks(db_enabled_fn, get_connection_fn, st_module=st):
    if not db_enabled_fn():
        return [task for task in st_module.session_state.tasks if _task_visible_in_app(task)]
    try:
        with get_connection_fn() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        title,
                        description,
                        category,
                        priority,
                        status,
                        created_date,
                        due_date,
                        scheduled_date,
                        scheduled_time,
                        scheduled_minutes,
                        recurrence_rule,
                        recurrence_interval,
                        completed_date,
                        completed_at
                    FROM tasks
                    WHERE status <> 'completed'
                       OR completed_at IS NULL
                       OR completed_at >= NOW() - INTERVAL '24 hours'
                    ORDER BY created_date DESC, id DESC
                    """
                )
                return cur.fetchall()
    except psycopg.Error:
        return [task for task in st_module.session_state.tasks if _task_visible_in_app(task)]


def load_surgical_cases(db_enabled_fn, get_connection_fn, st_module=st):
    if not db_enabled_fn():
        return st_module.session_state.surgical_cases
    try:
        with get_connection_fn() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        case_date,
                        case_stream,
                        procedure_name,
                        anatomical_location,
                        cpt_codes,
                        status,
                        notes,
                        education_url,
                        education_notes,
                        created_date
                    FROM surgical_cases
                    ORDER BY case_date DESC, id DESC
                    """
                )
                return cur.fetchall()
    except psycopg.Error:
        return st_module.session_state.surgical_cases


def add_surgical_case(
    case_date,
    case_stream,
    procedure_name,
    anatomical_location,
    cpt_codes="",
    status="planned",
    notes="",
    education_url="",
    education_notes="",
    db_enabled_fn=None,
    get_connection_fn=None,
    st_module=st,
):
    stream_value = case_stream.strip()
    procedure_value = procedure_name.strip()
    location_value = anatomical_location.strip()
    cpt_codes_value = cpt_codes.strip()
    notes_value = notes.strip()
    education_url_value = education_url.strip()
    education_notes_value = education_notes.strip()
    if not stream_value or not procedure_value:
        return

    if db_enabled_fn and db_enabled_fn():
        with get_connection_fn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO surgical_cases (
                        case_date,
                        case_stream,
                        procedure_name,
                        anatomical_location,
                        cpt_codes,
                        status,
                        notes,
                        education_url,
                        education_notes,
                        created_date
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        case_date,
                        stream_value,
                        procedure_value,
                        location_value,
                        cpt_codes_value,
                        status,
                        notes_value,
                        education_url_value,
                        education_notes_value,
                        date.today(),
                    ),
                )
        return

    next_id = max([item.get("id", 0) for item in st_module.session_state.surgical_cases], default=0) + 1
    st_module.session_state.surgical_cases.append(
        {
            "id": next_id,
            "case_date": case_date,
            "case_stream": stream_value,
            "procedure_name": procedure_value,
            "anatomical_location": location_value,
            "cpt_codes": cpt_codes_value,
            "status": status,
            "notes": notes_value,
            "education_url": education_url_value,
            "education_notes": education_notes_value,
            "created_date": date.today(),
        }
    )


def update_surgical_case(case_id, db_enabled_fn=None, get_connection_fn=None, st_module=st, **fields):
    allowed_fields = {
        "case_date",
        "case_stream",
        "procedure_name",
        "anatomical_location",
        "cpt_codes",
        "status",
        "notes",
        "education_url",
        "education_notes",
    }
    sanitized = {key: value for key, value in fields.items() if key in allowed_fields}
    if not sanitized:
        return

    if db_enabled_fn and db_enabled_fn():
        set_parts = []
        values = []
        for key, value in sanitized.items():
            set_parts.append(f"{key} = %s")
            values.append(value)
        values.append(case_id)
        with get_connection_fn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE surgical_cases SET {', '.join(set_parts)} WHERE id = %s", tuple(values))
        return

    for item in st_module.session_state.surgical_cases:
        if item.get("id") == case_id:
            item.update(sanitized)
            return


def delete_surgical_case(case_id, db_enabled_fn=None, get_connection_fn=None, st_module=st):
    if db_enabled_fn and db_enabled_fn():
        with get_connection_fn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM surgical_cases WHERE id = %s", (case_id,))
        return
    st_module.session_state.surgical_cases = [item for item in st_module.session_state.surgical_cases if item.get("id") != case_id]
    st_module.session_state["case_protocol_links"] = [
        item for item in st_module.session_state.get("case_protocol_links", []) if item.get("case_id") != case_id
    ]


def load_protocol_documents(db_enabled_fn, get_connection_fn, st_module=st):
    if not db_enabled_fn():
        return st_module.session_state.protocol_documents
    try:
        with get_connection_fn() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        surgeon_label,
                        protocol_name,
                        file_name,
                        file_mime,
                        file_bytes,
                        notes,
                        created_date
                    FROM protocol_documents
                    ORDER BY created_date DESC, id DESC
                    """
                )
                return cur.fetchall()
    except psycopg.Error:
        return st_module.session_state.protocol_documents


def add_protocol_document(
    surgeon_label,
    protocol_name,
    upload_name,
    upload_mime,
    upload_bytes,
    notes="",
    db_enabled_fn=None,
    get_connection_fn=None,
    st_module=st,
):
    surgeon_value = surgeon_label.strip() or "Dr. Braden Boyer (BB)"
    protocol_value = protocol_name.strip() or upload_name
    notes_value = notes.strip()
    if not upload_bytes:
        return

    if db_enabled_fn and db_enabled_fn():
        with get_connection_fn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO protocol_documents (
                        surgeon_label,
                        protocol_name,
                        file_name,
                        file_mime,
                        file_bytes,
                        notes,
                        created_date
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        surgeon_value,
                        protocol_value,
                        upload_name,
                        upload_mime,
                        upload_bytes,
                        notes_value,
                        date.today(),
                    ),
                )
                inserted = cur.fetchone()
                return inserted[0] if inserted else None

    next_id = max([item.get("id", 0) for item in st_module.session_state.protocol_documents], default=0) + 1
    st_module.session_state.protocol_documents.append(
        {
            "id": next_id,
            "surgeon_label": surgeon_value,
            "protocol_name": protocol_value,
            "file_name": upload_name,
            "file_mime": upload_mime,
            "file_bytes": upload_bytes,
            "notes": notes_value,
            "created_date": date.today(),
        }
    )
    return next_id


def load_case_protocol_links(db_enabled_fn, get_connection_fn, st_module=st):
    if not db_enabled_fn():
        return st_module.session_state.get("case_protocol_links", [])
    try:
        with get_connection_fn() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT case_id, protocol_id
                    FROM surgical_case_protocol_links
                    ORDER BY protocol_id, case_id
                    """
                )
                return cur.fetchall()
    except psycopg.Error:
        return st_module.session_state.get("case_protocol_links", [])


def set_protocol_case_links(protocol_id, case_ids, db_enabled_fn=None, get_connection_fn=None, st_module=st):
    normalized_case_ids = sorted({int(item) for item in (case_ids or []) if item is not None})

    if db_enabled_fn and db_enabled_fn():
        with get_connection_fn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM surgical_case_protocol_links WHERE protocol_id = %s", (protocol_id,))
                for case_id in normalized_case_ids:
                    cur.execute(
                        """
                        INSERT INTO surgical_case_protocol_links (case_id, protocol_id, created_date)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (case_id, protocol_id) DO NOTHING
                        """,
                        (case_id, protocol_id, date.today()),
                    )
        return

    existing = st_module.session_state.get("case_protocol_links", [])
    st_module.session_state["case_protocol_links"] = [
        item
        for item in existing
        if item.get("protocol_id") != protocol_id
    ]
    st_module.session_state["case_protocol_links"].extend(
        [{"case_id": case_id, "protocol_id": protocol_id} for case_id in normalized_case_ids]
    )


def update_protocol_document(
    doc_id,
    surgeon_label,
    protocol_name,
    notes="",
    upload_name=None,
    upload_mime=None,
    upload_bytes=None,
    db_enabled_fn=None,
    get_connection_fn=None,
    st_module=st,
):
    surgeon_value = (surgeon_label or "").strip() or "Dr. Braden Boyer (BB)"
    protocol_value = (protocol_name or "").strip()
    notes_value = (notes or "").strip()

    if db_enabled_fn and db_enabled_fn():
        set_parts = [
            "surgeon_label = %s",
            "protocol_name = %s",
            "notes = %s",
        ]
        values = [surgeon_value, protocol_value, notes_value]
        if upload_bytes:
            set_parts.extend(["file_name = %s", "file_mime = %s", "file_bytes = %s"])
            values.extend([upload_name, upload_mime, upload_bytes])
        values.append(doc_id)
        with get_connection_fn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE protocol_documents SET {', '.join(set_parts)} WHERE id = %s", tuple(values))
        return

    for item in st_module.session_state.protocol_documents:
        if item.get("id") == doc_id:
            item["surgeon_label"] = surgeon_value
            item["protocol_name"] = protocol_value
            item["notes"] = notes_value
            if upload_bytes:
                item["file_name"] = upload_name
                item["file_mime"] = upload_mime
                item["file_bytes"] = upload_bytes
            return


def delete_protocol_document(doc_id, db_enabled_fn=None, get_connection_fn=None, st_module=st):
    if db_enabled_fn and db_enabled_fn():
        with get_connection_fn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM protocol_documents WHERE id = %s", (doc_id,))
        return
    st_module.session_state.protocol_documents = [item for item in st_module.session_state.protocol_documents if item.get("id") != doc_id]
    st_module.session_state["case_protocol_links"] = [
        item for item in st_module.session_state.get("case_protocol_links", []) if item.get("protocol_id") != doc_id
    ]


def load_lead_clinical_issues(db_enabled_fn, get_connection_fn, st_module=st):
    if not db_enabled_fn():
        return list(st_module.session_state.get("lead_clinical_issues", []))
    try:
        with get_connection_fn() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        title,
                        details,
                        issue_type,
                        source_lane,
                        urgency,
                        status,
                        owner_name,
                        due_date,
                        due_time,
                        escalation_target,
                        escalation_reason,
                        decision_needed_by,
                        dependency_owner,
                        resolved_date,
                        created_date
                    FROM lead_clinical_issues
                    ORDER BY created_date DESC, id DESC
                    """
                )
                return cur.fetchall()
    except psycopg.Error:
        return list(st_module.session_state.get("lead_clinical_issues", []))


def add_lead_clinical_issue(
    title,
    details,
    issue_type,
    source_lane,
    urgency,
    owner_name,
    due_date,
    due_time,
    escalation_target="none",
    escalation_reason="",
    decision_needed_by=None,
    dependency_owner="",
    db_enabled_fn=None,
    get_connection_fn=None,
    st_module=st,
):
    title_value = str(title or "").strip()
    if not title_value:
        return None

    details_value = str(details or "").strip()
    issue_type_value = str(issue_type or "Clinical task").strip() or "Clinical task"
    source_lane_value = str(source_lane or "clinical_staff").strip() or "clinical_staff"
    urgency_value = str(urgency or "medium").strip() or "medium"
    owner_value = str(owner_name or "").strip()
    escalation_target_value = str(escalation_target or "none").strip() or "none"
    escalation_reason_value = str(escalation_reason or "").strip()
    dependency_owner_value = str(dependency_owner or "").strip()

    if db_enabled_fn and db_enabled_fn():
        with get_connection_fn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO lead_clinical_issues (
                        title,
                        details,
                        issue_type,
                        source_lane,
                        urgency,
                        status,
                        owner_name,
                        due_date,
                        due_time,
                        escalation_target,
                        escalation_reason,
                        decision_needed_by,
                        dependency_owner,
                        created_date
                    ) VALUES (%s, %s, %s, %s, %s, 'new', %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        title_value,
                        details_value,
                        issue_type_value,
                        source_lane_value,
                        urgency_value,
                        owner_value,
                        due_date,
                        due_time,
                        escalation_target_value,
                        escalation_reason_value,
                        decision_needed_by,
                        dependency_owner_value,
                        date.today(),
                    ),
                )
                inserted = cur.fetchone()
                return inserted[0] if inserted else None

    issues = st_module.session_state.setdefault("lead_clinical_issues", [])
    next_id = max([item.get("id", 0) for item in issues], default=0) + 1
    issues.append(
        {
            "id": next_id,
            "title": title_value,
            "details": details_value,
            "issue_type": issue_type_value,
            "source_lane": source_lane_value,
            "urgency": urgency_value,
            "status": "new",
            "owner_name": owner_value,
            "due_date": due_date,
            "due_time": due_time,
            "escalation_target": escalation_target_value,
            "escalation_reason": escalation_reason_value,
            "decision_needed_by": decision_needed_by,
            "dependency_owner": dependency_owner_value,
            "resolved_date": None,
            "created_date": date.today(),
        }
    )
    return next_id


def update_lead_clinical_issue(issue_id, db_enabled_fn=None, get_connection_fn=None, st_module=st, **fields):
    allowed_fields = {
        "title",
        "details",
        "issue_type",
        "source_lane",
        "urgency",
        "status",
        "owner_name",
        "due_date",
        "due_time",
        "escalation_target",
        "escalation_reason",
        "decision_needed_by",
        "dependency_owner",
        "resolved_date",
    }
    sanitized = {key: value for key, value in fields.items() if key in allowed_fields}
    if not sanitized:
        return

    if db_enabled_fn and db_enabled_fn():
        set_parts = []
        values = []
        for key, value in sanitized.items():
            set_parts.append(f"{key} = %s")
            values.append(value)
        values.append(issue_id)
        with get_connection_fn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE lead_clinical_issues SET {', '.join(set_parts)} WHERE id = %s", tuple(values))
        return

    issues = st_module.session_state.setdefault("lead_clinical_issues", [])
    for item in issues:
        if item.get("id") == issue_id:
            item.update(sanitized)
            return


def load_lead_sop_entries(db_enabled_fn, get_connection_fn, st_module=st):
    if not db_enabled_fn():
        return list(st_module.session_state.get("lead_sop_entries", []))
    try:
        with get_connection_fn() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        title,
                        topic,
                        owner_name,
                        version_tag,
                        quick_steps,
                        link_url,
                        status,
                        updated_date,
                        created_date
                    FROM lead_sop_entries
                    ORDER BY updated_date DESC, id DESC
                    """
                )
                return cur.fetchall()
    except psycopg.Error:
        return list(st_module.session_state.get("lead_sop_entries", []))


def add_lead_sop_entry(
    title,
    topic,
    owner_name,
    version_tag,
    quick_steps,
    link_url="",
    status="active",
    db_enabled_fn=None,
    get_connection_fn=None,
    st_module=st,
):
    title_value = str(title or "").strip()
    if not title_value:
        return None
    topic_value = str(topic or "General").strip() or "General"
    owner_value = str(owner_name or "").strip()
    version_value = str(version_tag or "v1.0").strip() or "v1.0"
    quick_steps_value = str(quick_steps or "").strip()
    link_value = str(link_url or "").strip()
    status_value = str(status or "active").strip() or "active"

    if db_enabled_fn and db_enabled_fn():
        with get_connection_fn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO lead_sop_entries (
                        title,
                        topic,
                        owner_name,
                        version_tag,
                        quick_steps,
                        link_url,
                        status,
                        updated_date,
                        created_date
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        title_value,
                        topic_value,
                        owner_value,
                        version_value,
                        quick_steps_value,
                        link_value,
                        status_value,
                        date.today(),
                        date.today(),
                    ),
                )
                inserted = cur.fetchone()
                return inserted[0] if inserted else None

    entries = st_module.session_state.setdefault("lead_sop_entries", [])
    next_id = max([item.get("id", 0) for item in entries], default=0) + 1
    entries.append(
        {
            "id": next_id,
            "title": title_value,
            "topic": topic_value,
            "owner_name": owner_value,
            "version_tag": version_value,
            "quick_steps": quick_steps_value,
            "link_url": link_value,
            "status": status_value,
            "updated_date": date.today(),
            "created_date": date.today(),
        }
    )
    return next_id


def load_lead_relationship_touchpoints(db_enabled_fn, get_connection_fn, st_module=st):
    if not db_enabled_fn():
        return list(st_module.session_state.get("lead_relationship_touchpoints", []))
    try:
        with get_connection_fn() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        person_name,
                        role_label,
                        relationship_type,
                        status_label,
                        last_touch_date,
                        next_follow_up_date,
                        open_asks,
                        recent_win,
                        notes,
                        created_date
                    FROM lead_relationship_touchpoints
                    ORDER BY next_follow_up_date NULLS LAST, created_date DESC, id DESC
                    """
                )
                return cur.fetchall()
    except psycopg.Error:
        return list(st_module.session_state.get("lead_relationship_touchpoints", []))


def add_lead_relationship_touchpoint(
    person_name,
    role_label,
    relationship_type,
    status_label,
    last_touch_date,
    next_follow_up_date,
    open_asks,
    recent_win,
    notes,
    db_enabled_fn=None,
    get_connection_fn=None,
    st_module=st,
):
    person_value = str(person_name or "").strip()
    if not person_value:
        return None
    role_value = str(role_label or "").strip()
    relationship_value = str(relationship_type or "Clinical staff").strip() or "Clinical staff"
    status_value = str(status_label or "green").strip() or "green"
    asks_value = str(open_asks or "").strip()
    win_value = str(recent_win or "").strip()
    notes_value = str(notes or "").strip()

    if db_enabled_fn and db_enabled_fn():
        with get_connection_fn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO lead_relationship_touchpoints (
                        person_name,
                        role_label,
                        relationship_type,
                        status_label,
                        last_touch_date,
                        next_follow_up_date,
                        open_asks,
                        recent_win,
                        notes,
                        created_date
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        person_value,
                        role_value,
                        relationship_value,
                        status_value,
                        last_touch_date,
                        next_follow_up_date,
                        asks_value,
                        win_value,
                        notes_value,
                        date.today(),
                    ),
                )
                inserted = cur.fetchone()
                return inserted[0] if inserted else None

    touchpoints = st_module.session_state.setdefault("lead_relationship_touchpoints", [])
    next_id = max([item.get("id", 0) for item in touchpoints], default=0) + 1
    touchpoints.append(
        {
            "id": next_id,
            "person_name": person_value,
            "role_label": role_value,
            "relationship_type": relationship_value,
            "status_label": status_value,
            "last_touch_date": last_touch_date,
            "next_follow_up_date": next_follow_up_date,
            "open_asks": asks_value,
            "recent_win": win_value,
            "notes": notes_value,
            "created_date": date.today(),
        }
    )
    return next_id


def update_lead_relationship_touchpoint(touchpoint_id, db_enabled_fn=None, get_connection_fn=None, st_module=st, **fields):
    allowed_fields = {
        "person_name",
        "role_label",
        "relationship_type",
        "status_label",
        "last_touch_date",
        "next_follow_up_date",
        "open_asks",
        "recent_win",
        "notes",
    }
    sanitized = {key: value for key, value in fields.items() if key in allowed_fields}
    if not sanitized:
        return

    if db_enabled_fn and db_enabled_fn():
        set_parts = []
        values = []
        for key, value in sanitized.items():
            set_parts.append(f"{key} = %s")
            values.append(value)
        values.append(touchpoint_id)
        with get_connection_fn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE lead_relationship_touchpoints SET {', '.join(set_parts)} WHERE id = %s", tuple(values))
        return

    touchpoints = st_module.session_state.setdefault("lead_relationship_touchpoints", [])
    for item in touchpoints:
        if item.get("id") == touchpoint_id:
            item.update(sanitized)
            return


def load_lead_huddle_logs(db_enabled_fn, get_connection_fn, st_module=st):
    if not db_enabled_fn():
        return list(st_module.session_state.get("lead_huddle_logs", []))
    try:
        with get_connection_fn() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        huddle_date,
                        priority_focus,
                        staffing_notes,
                        escalation_notes,
                        recap_sent_to,
                        shift_notes,
                        created_date
                    FROM lead_huddle_logs
                    ORDER BY huddle_date DESC, id DESC
                    LIMIT 90
                    """
                )
                return cur.fetchall()
    except psycopg.Error:
        return list(st_module.session_state.get("lead_huddle_logs", []))


def add_lead_huddle_log(
    huddle_date,
    priority_focus,
    staffing_notes,
    escalation_notes,
    recap_sent_to,
    shift_notes,
    db_enabled_fn=None,
    get_connection_fn=None,
    st_module=st,
):
    if db_enabled_fn and db_enabled_fn():
        with get_connection_fn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO lead_huddle_logs (
                        huddle_date,
                        priority_focus,
                        staffing_notes,
                        escalation_notes,
                        recap_sent_to,
                        shift_notes,
                        created_date
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        huddle_date,
                        str(priority_focus or "").strip(),
                        str(staffing_notes or "").strip(),
                        str(escalation_notes or "").strip(),
                        str(recap_sent_to or "").strip(),
                        str(shift_notes or "").strip(),
                        date.today(),
                    ),
                )
        return

    logs = st_module.session_state.setdefault("lead_huddle_logs", [])
    next_id = max([item.get("id", 0) for item in logs], default=0) + 1
    logs.append(
        {
            "id": next_id,
            "huddle_date": huddle_date,
            "priority_focus": str(priority_focus or "").strip(),
            "staffing_notes": str(staffing_notes or "").strip(),
            "escalation_notes": str(escalation_notes or "").strip(),
            "recap_sent_to": str(recap_sent_to or "").strip(),
            "shift_notes": str(shift_notes or "").strip(),
            "created_date": date.today(),
        }
    )


def load_lead_skill_signoffs(db_enabled_fn, get_connection_fn, st_module=st):
    if not db_enabled_fn():
        return list(st_module.session_state.get("lead_skill_signoffs", []))
    try:
        with get_connection_fn() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        staff_name,
                        role_label,
                        skill_name,
                        status,
                        due_date,
                        signed_off_date,
                        signed_off_by,
                        notes,
                        created_date
                    FROM lead_skill_signoffs
                    ORDER BY due_date NULLS LAST, created_date DESC, id DESC
                    """
                )
                return cur.fetchall()
    except psycopg.Error:
        return list(st_module.session_state.get("lead_skill_signoffs", []))


def add_lead_skill_signoff(
    staff_name,
    role_label,
    skill_name,
    due_date,
    notes="",
    status="pending",
    signed_off_date=None,
    signed_off_by="",
    db_enabled_fn=None,
    get_connection_fn=None,
    st_module=st,
):
    staff_value = str(staff_name or "").strip()
    skill_value = str(skill_name or "").strip()
    if not staff_value or not skill_value:
        return None

    role_value = str(role_label or "").strip()
    status_value = str(status or "pending").strip() or "pending"
    notes_value = str(notes or "").strip()
    signed_by_value = str(signed_off_by or "").strip()

    if db_enabled_fn and db_enabled_fn():
        with get_connection_fn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO lead_skill_signoffs (
                        staff_name,
                        role_label,
                        skill_name,
                        status,
                        due_date,
                        signed_off_date,
                        signed_off_by,
                        notes,
                        created_date
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        staff_value,
                        role_value,
                        skill_value,
                        status_value,
                        due_date,
                        signed_off_date,
                        signed_by_value,
                        notes_value,
                        date.today(),
                    ),
                )
                inserted = cur.fetchone()
                return inserted[0] if inserted else None

    items = st_module.session_state.setdefault("lead_skill_signoffs", [])
    next_id = max([item.get("id", 0) for item in items], default=0) + 1
    items.append(
        {
            "id": next_id,
            "staff_name": staff_value,
            "role_label": role_value,
            "skill_name": skill_value,
            "status": status_value,
            "due_date": due_date,
            "signed_off_date": signed_off_date,
            "signed_off_by": signed_by_value,
            "notes": notes_value,
            "created_date": date.today(),
        }
    )
    return next_id


def update_lead_skill_signoff(signoff_id, db_enabled_fn=None, get_connection_fn=None, st_module=st, **fields):
    allowed_fields = {
        "staff_name",
        "role_label",
        "skill_name",
        "status",
        "due_date",
        "signed_off_date",
        "signed_off_by",
        "notes",
    }
    sanitized = {key: value for key, value in fields.items() if key in allowed_fields}
    if not sanitized:
        return

    if db_enabled_fn and db_enabled_fn():
        set_parts = []
        values = []
        for key, value in sanitized.items():
            set_parts.append(f"{key} = %s")
            values.append(value)
        values.append(signoff_id)
        with get_connection_fn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE lead_skill_signoffs SET {', '.join(set_parts)} WHERE id = %s", tuple(values))
        return

    items = st_module.session_state.setdefault("lead_skill_signoffs", [])
    for item in items:
        if item.get("id") == signoff_id:
            item.update(sanitized)
            return


def load_lead_education_requests(db_enabled_fn, get_connection_fn, st_module=st):
    if not db_enabled_fn():
        return list(st_module.session_state.get("lead_education_requests", []))
    try:
        with get_connection_fn() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        request_title,
                        requesting_team,
                        topic,
                        priority,
                        status,
                        needed_by_date,
                        session_date,
                        owner_name,
                        notes,
                        created_date
                    FROM lead_education_requests
                    ORDER BY needed_by_date NULLS LAST, created_date DESC, id DESC
                    """
                )
                return cur.fetchall()
    except psycopg.Error:
        return list(st_module.session_state.get("lead_education_requests", []))


def add_lead_education_request(
    request_title,
    requesting_team,
    topic,
    priority,
    needed_by_date,
    session_date=None,
    owner_name="",
    notes="",
    status="new",
    db_enabled_fn=None,
    get_connection_fn=None,
    st_module=st,
):
    title_value = str(request_title or "").strip()
    if not title_value:
        return None

    team_value = str(requesting_team or "").strip()
    topic_value = str(topic or "General").strip() or "General"
    priority_value = str(priority or "medium").strip() or "medium"
    status_value = str(status or "new").strip() or "new"
    owner_value = str(owner_name or "").strip()
    notes_value = str(notes or "").strip()

    if db_enabled_fn and db_enabled_fn():
        with get_connection_fn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO lead_education_requests (
                        request_title,
                        requesting_team,
                        topic,
                        priority,
                        status,
                        needed_by_date,
                        session_date,
                        owner_name,
                        notes,
                        created_date
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        title_value,
                        team_value,
                        topic_value,
                        priority_value,
                        status_value,
                        needed_by_date,
                        session_date,
                        owner_value,
                        notes_value,
                        date.today(),
                    ),
                )
                inserted = cur.fetchone()
                return inserted[0] if inserted else None

    items = st_module.session_state.setdefault("lead_education_requests", [])
    next_id = max([item.get("id", 0) for item in items], default=0) + 1
    items.append(
        {
            "id": next_id,
            "request_title": title_value,
            "requesting_team": team_value,
            "topic": topic_value,
            "priority": priority_value,
            "status": status_value,
            "needed_by_date": needed_by_date,
            "session_date": session_date,
            "owner_name": owner_value,
            "notes": notes_value,
            "created_date": date.today(),
        }
    )
    return next_id


def update_lead_education_request(request_id, db_enabled_fn=None, get_connection_fn=None, st_module=st, **fields):
    allowed_fields = {
        "request_title",
        "requesting_team",
        "topic",
        "priority",
        "status",
        "needed_by_date",
        "session_date",
        "owner_name",
        "notes",
    }
    sanitized = {key: value for key, value in fields.items() if key in allowed_fields}
    if not sanitized:
        return

    if db_enabled_fn and db_enabled_fn():
        set_parts = []
        values = []
        for key, value in sanitized.items():
            set_parts.append(f"{key} = %s")
            values.append(value)
        values.append(request_id)
        with get_connection_fn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE lead_education_requests SET {', '.join(set_parts)} WHERE id = %s", tuple(values))
        return

    items = st_module.session_state.setdefault("lead_education_requests", [])
    for item in items:
        if item.get("id") == request_id:
            item.update(sanitized)
            return


def load_autoclave_maintenance_items(db_enabled_fn, get_connection_fn, st_module=st):
    if not db_enabled_fn():
        return list(st_module.session_state.get("autoclave_maintenance_items", []))
    try:
        with get_connection_fn() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        unit_label,
                        maintenance_type,
                        frequency_label,
                        last_completed_date,
                        next_due_date,
                        status,
                        owner_name,
                        vendor_contact,
                        notes,
                        created_date
                    FROM autoclave_maintenance_items
                    ORDER BY next_due_date NULLS LAST, created_date DESC, id DESC
                    """
                )
                return cur.fetchall()
    except psycopg.Error:
        return list(st_module.session_state.get("autoclave_maintenance_items", []))


def add_autoclave_maintenance_item(
    unit_label,
    maintenance_type,
    frequency_label,
    next_due_date,
    last_completed_date=None,
    status="due_soon",
    owner_name="",
    vendor_contact="",
    notes="",
    db_enabled_fn=None,
    get_connection_fn=None,
    st_module=st,
):
    unit_value = str(unit_label or "").strip()
    if not unit_value:
        return None

    maintenance_value = str(maintenance_type or "Routine check").strip() or "Routine check"
    frequency_value = str(frequency_label or "Weekly").strip() or "Weekly"
    status_value = str(status or "due_soon").strip() or "due_soon"
    owner_value = str(owner_name or "").strip()
    vendor_value = str(vendor_contact or "").strip()
    notes_value = str(notes or "").strip()

    if db_enabled_fn and db_enabled_fn():
        with get_connection_fn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO autoclave_maintenance_items (
                        unit_label,
                        maintenance_type,
                        frequency_label,
                        last_completed_date,
                        next_due_date,
                        status,
                        owner_name,
                        vendor_contact,
                        notes,
                        created_date
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        unit_value,
                        maintenance_value,
                        frequency_value,
                        last_completed_date,
                        next_due_date,
                        status_value,
                        owner_value,
                        vendor_value,
                        notes_value,
                        date.today(),
                    ),
                )
                inserted = cur.fetchone()
                return inserted[0] if inserted else None

    items = st_module.session_state.setdefault("autoclave_maintenance_items", [])
    next_id = max([item.get("id", 0) for item in items], default=0) + 1
    items.append(
        {
            "id": next_id,
            "unit_label": unit_value,
            "maintenance_type": maintenance_value,
            "frequency_label": frequency_value,
            "last_completed_date": last_completed_date,
            "next_due_date": next_due_date,
            "status": status_value,
            "owner_name": owner_value,
            "vendor_contact": vendor_value,
            "notes": notes_value,
            "created_date": date.today(),
        }
    )
    return next_id


def update_autoclave_maintenance_item(item_id, db_enabled_fn=None, get_connection_fn=None, st_module=st, **fields):
    allowed_fields = {
        "unit_label",
        "maintenance_type",
        "frequency_label",
        "last_completed_date",
        "next_due_date",
        "status",
        "owner_name",
        "vendor_contact",
        "notes",
    }
    sanitized = {key: value for key, value in fields.items() if key in allowed_fields}
    if not sanitized:
        return

    if db_enabled_fn and db_enabled_fn():
        set_parts = []
        values = []
        for key, value in sanitized.items():
            set_parts.append(f"{key} = %s")
            values.append(value)
        values.append(item_id)
        with get_connection_fn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE autoclave_maintenance_items SET {', '.join(set_parts)} WHERE id = %s", tuple(values))
        return

    items = st_module.session_state.setdefault("autoclave_maintenance_items", [])
    for item in items:
        if item.get("id") == item_id:
            item.update(sanitized)
            return
