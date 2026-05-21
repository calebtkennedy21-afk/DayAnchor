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
