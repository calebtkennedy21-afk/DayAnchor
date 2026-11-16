import json
from dataclasses import dataclass, field
from copy import deepcopy
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row
import streamlit as st


MOUNTAIN_TIMEZONE = ZoneInfo("America/Denver")


def mountain_today():
    return datetime.now(MOUNTAIN_TIMEZONE).date()


class TaskIdentityError(ValueError):
    pass


class LoadedTasks(list):
    def __init__(self, tasks, source, identity_tasks):
        super().__init__(tasks)
        self.source = source
        self.identity_tasks = identity_tasks


@dataclass
class BrainDumpConversionResult:
    status: str
    message: str
    task_ids: list = field(default_factory=list)
    created_task_ids: list = field(default_factory=list)
    settings: dict = field(default_factory=dict)
    mode: str = "database"


class _BrainDumpConversionAbort(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _brain_dump_date(value):
    if value is None or value == "":
        return None
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _brain_dump_time(value):
    if value is None or value == "":
        return None
    return value if isinstance(value, time) else time.fromisoformat(str(value))


def _brain_dump_proposal_ids(event):
    payload = event.get("payload_json") or {}
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return set()
    return {
        str(item.get("data", {}).get("dump_id"))
        for item in payload["items"]
        if isinstance(item, dict)
        and item.get("type") == "triage"
        and isinstance(item.get("data"), dict)
        and item["data"].get("dump_id") is not None
    }


def _prepare_brain_dump_decisions(settings, decisions, today, status_prefix):
    entries = settings.get("brain_dump_entries")
    if not isinstance(entries, list):
        raise _BrainDumpConversionAbort("rejected", "The brain-dump inbox is unavailable.")

    entry_by_id = {}
    for entry in entries:
        if isinstance(entry, dict) and entry.get("dump_id") is not None:
            entry_id = str(entry["dump_id"])
            if entry_id in entry_by_id:
                raise _BrainDumpConversionAbort("rejected", "Brain-dump entry identity is ambiguous.")
            entry_by_id[entry_id] = entry

    normalized = []
    seen = set()
    for raw_decision in decisions:
        if not isinstance(raw_decision, dict):
            raise _BrainDumpConversionAbort("rejected", "A brain-dump decision is invalid.")
        entry_id = str(raw_decision.get("dump_id") or "").strip()
        item_type = str(raw_decision.get("item_type") or "").strip().lower()
        if not entry_id or entry_id in seen or item_type not in {"task", "reminder", "note", "idea"}:
            raise _BrainDumpConversionAbort("rejected", "A brain-dump decision is invalid or duplicated.")
        entry = entry_by_id.get(entry_id)
        if entry is None:
            raise _BrainDumpConversionAbort("rejected", "A brain-dump entry is no longer available.")
        seen.add(entry_id)
        normalized.append((entry_id, entry, dict(raw_decision), item_type))

    if not normalized:
        raise _BrainDumpConversionAbort("rejected", "No brain-dump decisions were selected.")

    updates = []
    for entry_id, entry, decision, item_type in normalized:
        status = str(entry.get("status") or "new")
        converted_status = f"{status_prefix}converted_{item_type}"
        if status in {f"converted_{item_type}", f"ai_converted_{item_type}"}:
            if item_type == "task" and type(entry.get("task_id")) is not int:
                raise _BrainDumpConversionAbort(
                    "rejected",
                    "A converted brain-dump task is missing its recorded task ID.",
                )
            updates.append((entry_id, entry, decision, item_type, True))
            continue
        if status != "new":
            raise _BrainDumpConversionAbort(
                "rejected",
                "A brain-dump entry is no longer eligible for conversion.",
            )
        updates.append((entry_id, entry, decision, item_type, False))

    task_ids = [
        entry["task_id"]
        for _, entry, _, item_type, already_applied in updates
        if item_type == "task" and already_applied
    ]
    task_specs = []
    for entry_id, entry, decision, item_type, already_applied in updates:
        if already_applied:
            continue
        title = str(
            decision.get("title")
            or entry.get("title")
            or entry.get("raw_text")
            or "Untitled thought"
        ).strip()
        if item_type == "task":
            category = decision.get("category") or entry.get("category") or "Personal"
            description = str(decision.get("description") or entry.get("raw_text") or "")
            if category == "Family":
                description = f"[Family] {description}".strip()
            task_specs.append(
                {
                    "dump_id": entry_id,
                    "entry": entry,
                    "title": title,
                    "description": description,
                    "category": "Clinic" if category == "Clinic" else "Personal",
                    "priority": decision.get("priority")
                    if decision.get("priority") in ("high", "medium", "low")
                    else "medium",
                    "due_date": _brain_dump_date(decision.get("due_date") or today),
                    "scheduled_date": _brain_dump_date(decision.get("scheduled_date")),
                    "scheduled_time": _brain_dump_time(decision.get("scheduled_time")),
                }
            )
        elif item_type == "reminder":
            reminders = list(settings.get("quick_reminders") or [])
            reminders.append(
                {
                    "reminder_id": entry_id,
                    "text": title,
                    "category": decision.get("category") or entry.get("category") or "Personal",
                    "remind_date": decision.get("due_date"),
                    "remind_time": decision.get("scheduled_time"),
                    "status": "active",
                    "created_at": datetime.now(MOUNTAIN_TIMEZONE).isoformat(timespec="seconds"),
                }
            )
            settings["quick_reminders"] = reminders
        else:
            category = decision.get("category") or entry.get("category") or "Personal"
            note_key, stamp_key = {
                "Clinic": ("clinical_notes", "clinical_notes_updated_at"),
                "Family": ("family_notes", "family_notes_updated_at"),
            }.get(category, ("personal_notes", "personal_notes_updated_at"))
            existing_note = str(settings.get(note_key) or "").strip()
            settings[note_key] = f"{existing_note}\n- {entry.get('raw_text') or title}".strip()
            settings[stamp_key] = datetime.now(MOUNTAIN_TIMEZONE).isoformat(timespec="seconds")

        entry["status"] = converted_status
        if item_type == "task":
            entry.pop("task_id", None)
    return normalized, updates, task_specs, task_ids


def convert_brain_dump_entries(
    decisions,
    today,
    session_settings=None,
    event_id=None,
    status_prefix="",
    db_enabled_fn=None,
    get_connection_fn=None,
    st_module=st,
):
    """Atomically convert selected inbox decisions and resolve their AI proposal."""
    if st_module.session_state.get("pending_app_settings") is not None:
        return BrainDumpConversionResult(
            "rejected",
            "Conversion is blocked while settings changes are pending. Review or safely retry that draft first.",
            mode="session" if not (db_enabled_fn and db_enabled_fn()) else "database",
        )
    if status_prefix == "ai_" and event_id is None:
        return BrainDumpConversionResult(
            "rejected",
            "The AI triage proposal could not be verified. No decisions were applied.",
            mode="session" if not (db_enabled_fn and db_enabled_fn()) else "database",
        )

    if db_enabled_fn and db_enabled_fn():
        try:
            with get_connection_fn() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        "SELECT payload FROM app_settings WHERE id = 1 FOR UPDATE"
                    )
                    settings_row = cur.fetchone()
                    if settings_row is None or not settings_row.get("payload"):
                        raise _BrainDumpConversionAbort(
                            "rejected",
                            "The durable settings row is unavailable. No brain-dump tasks were created.",
                        )
                    original_payload = settings_row["payload"]
                    settings = json.loads(original_payload)
                    if not isinstance(settings, dict):
                        raise _BrainDumpConversionAbort(
                            "rejected",
                            "The durable settings payload is invalid. No brain-dump tasks were created.",
                        )

                    normalized, updates, task_specs, task_ids = _prepare_brain_dump_decisions(
                        settings,
                        decisions,
                        today,
                        status_prefix,
                    )
                    all_already_applied = all(item[-1] for item in updates)

                    event = None
                    if event_id is not None:
                        cur.execute(
                            """
                            SELECT id, pillar, kind, status, payload_json, expires_at, task_ids
                            FROM ai_events
                            WHERE id = %s
                            FOR UPDATE
                            """,
                            (event_id,),
                        )
                        event = cur.fetchone()
                        if event is None or event.get("pillar") != "capture" or event.get("kind") != "decision":
                            raise _BrainDumpConversionAbort(
                                "rejected",
                                "The AI triage proposal is unavailable. No conversion was applied.",
                            )
                        requested_ids = {entry_id for entry_id, *_ in normalized}
                        proposal_ids = _brain_dump_proposal_ids(event)
                        if not requested_ids.issubset(proposal_ids):
                            raise _BrainDumpConversionAbort(
                                "rejected",
                                "The selected entries do not match this AI triage proposal.",
                            )
                        if event.get("status") == "applied" and all_already_applied:
                            existing_ids = list(event.get("task_ids") or task_ids)
                            return BrainDumpConversionResult(
                                "already_applied",
                                "This AI brain-dump proposal was already applied.",
                                task_ids=existing_ids,
                                settings=settings,
                                mode="database",
                            )
                        if event.get("status") != "proposed":
                            raise _BrainDumpConversionAbort(
                                "rejected",
                                "The AI triage proposal is no longer eligible for conversion.",
                            )
                        if event.get("expires_at") and event["expires_at"] < datetime.now(MOUNTAIN_TIMEZONE):
                            raise _BrainDumpConversionAbort(
                                "rejected",
                                "The AI triage proposal has expired. No conversion was applied.",
                            )
                    elif all_already_applied:
                        return BrainDumpConversionResult(
                            "already_applied",
                            "This brain-dump entry was already converted.",
                            task_ids=task_ids,
                            settings=settings,
                            mode="database",
                        )

                    created_task_ids = []
                    for spec in task_specs:
                        cur.execute(
                            """
                            INSERT INTO tasks (
                                title, description, category, priority, status, created_date,
                                due_date, scheduled_date, scheduled_time, scheduled_minutes,
                                recurrence_rule, recurrence_interval, completed_date, completed_at
                            )
                            VALUES (%s, %s, %s, %s, 'todo', %s, %s, %s, %s, NULL, NULL, 1, NULL, NULL)
                            RETURNING id
                            """,
                            (
                                spec["title"],
                                spec["description"],
                                spec["category"],
                                spec["priority"],
                                today,
                                spec["due_date"],
                                spec["scheduled_date"],
                                spec["scheduled_time"],
                            ),
                        )
                        inserted = cur.fetchone()
                        if not inserted or type(inserted.get("id")) is not int:
                            raise _BrainDumpConversionAbort(
                                "failed",
                                "Task creation did not return a valid task ID. No conversion was committed.",
                            )
                        task_id = inserted["id"]
                        spec["entry"]["task_id"] = task_id
                        created_task_ids.append(task_id)
                        task_ids.append(task_id)

                    updated_payload = json.dumps(settings, default=str)
                    cur.execute(
                        """
                        UPDATE app_settings
                        SET payload = %s, updated_at = NOW()
                        WHERE id = 1 AND payload = %s
                        RETURNING id
                        """,
                        (updated_payload, original_payload),
                    )
                    if cur.fetchone() is None:
                        raise _BrainDumpConversionAbort(
                            "rejected",
                            "Settings changed during conversion. No brain-dump tasks were committed.",
                        )

                    if event is not None:
                        cur.execute(
                            """
                            UPDATE ai_events
                            SET status = 'applied', outcome = 'accepted', task_ids = %s,
                                outcome_note = %s, resolved_at = NOW()
                            WHERE id = %s AND status = 'proposed'
                            RETURNING id
                            """,
                            (
                                task_ids,
                                f"{len(normalized)} brain-dump decision(s) applied",
                                event_id,
                            ),
                        )
                        if cur.fetchone() is None:
                            raise _BrainDumpConversionAbort(
                                "failed",
                                "The AI proposal could not be resolved. No conversion was committed.",
                            )
        except _BrainDumpConversionAbort as exc:
            return BrainDumpConversionResult(
                exc.status,
                str(exc),
                mode="database",
            )
        except (psycopg.Error, TypeError, ValueError):
            return BrainDumpConversionResult(
                "failed",
                "Brain-dump conversion could not be persisted. No conversion was committed.",
                mode="database",
            )

        st_module.session_state["app_settings"] = settings
        st_module.session_state["app_settings_database_baseline"] = {
            "known": True,
            "payload": updated_payload,
        }
        return BrainDumpConversionResult(
            "already_applied"
            if all_already_applied
            else "created"
            if created_task_ids
            else "applied",
            "Brain-dump decisions were applied.",
            task_ids=task_ids,
            created_task_ids=created_task_ids,
            settings=settings,
            mode="database",
        )

    session_state = st_module.session_state
    original_tasks = deepcopy(session_state.get("tasks", []))
    original_watermark = session_state.get("task_id_high_watermark")
    had_settings = "app_settings" in session_state
    original_settings = deepcopy(session_state.get("app_settings"))
    original_events = deepcopy(session_state.get("ai_events", []))
    settings = deepcopy(
        session_state.get("app_settings")
        if isinstance(session_state.get("app_settings"), dict)
        else session_settings or {}
    )

    def restore_session_state():
        session_state["tasks"] = original_tasks
        if original_watermark is None:
            session_state.pop("task_id_high_watermark", None)
        else:
            session_state["task_id_high_watermark"] = original_watermark
        if had_settings:
            session_state["app_settings"] = original_settings
        else:
            session_state.pop("app_settings", None)
        session_state["ai_events"] = original_events

    try:
        normalized, updates, task_specs, task_ids = _prepare_brain_dump_decisions(
            settings,
            decisions,
            today,
            status_prefix,
        )
        all_already_applied = all(item[-1] for item in updates)
        event = None
        if event_id is not None:
            event = next(
                (item for item in session_state.get("ai_events", []) if item.get("id") == event_id),
                None,
            )
            if event is None or event.get("pillar") != "capture" or event.get("kind") != "decision":
                raise _BrainDumpConversionAbort("rejected", "The AI triage proposal is unavailable.")
            requested_ids = {entry_id for entry_id, *_ in normalized}
            if not requested_ids.issubset(_brain_dump_proposal_ids(event)):
                raise _BrainDumpConversionAbort("rejected", "The selected entries do not match this AI triage proposal.")
            if event.get("status") == "applied" and all_already_applied:
                return BrainDumpConversionResult(
                    "already_applied",
                    "This AI brain-dump proposal was already applied.",
                    task_ids=list(event.get("task_ids") or task_ids),
                    settings=settings,
                    mode="session",
                )
            if event.get("status") != "proposed":
                raise _BrainDumpConversionAbort("rejected", "The AI triage proposal is no longer eligible for conversion.")
            if event.get("expires_at") and event["expires_at"] < datetime.now(MOUNTAIN_TIMEZONE):
                raise _BrainDumpConversionAbort("rejected", "The AI triage proposal has expired.")
        elif all_already_applied:
            return BrainDumpConversionResult(
                "already_applied",
                "This brain-dump entry was already converted.",
                task_ids=task_ids,
                settings=settings,
                mode="session",
            )

        tasks = session_state.setdefault("tasks", [])
        created_task_ids = []
        for spec in task_specs:
            task_id = allocate_task_id(session_state)
            tasks.append(
                {
                    "id": task_id,
                    "title": spec["title"],
                    "description": spec["description"],
                    "category": spec["category"],
                    "priority": spec["priority"],
                    "status": "todo",
                    "created_date": today,
                    "due_date": spec["due_date"],
                    "scheduled_date": spec["scheduled_date"],
                    "scheduled_time": spec["scheduled_time"],
                    "scheduled_minutes": None,
                    "recurrence_rule": None,
                    "recurrence_interval": 1,
                    "completed_date": None,
                    "completed_at": None,
                }
            )
            spec["entry"]["task_id"] = task_id
            task_ids.append(task_id)
            created_task_ids.append(task_id)

        session_state["app_settings"] = settings
        if event is not None:
            event.update(
                status="applied",
                outcome="accepted",
                task_ids=task_ids,
                outcome_note=f"{len(normalized)} brain-dump decision(s) applied",
                resolved_at=datetime.now(MOUNTAIN_TIMEZONE),
            )
    except _BrainDumpConversionAbort as exc:
        restore_session_state()
        return BrainDumpConversionResult(exc.status, str(exc), mode="session")
    except (TaskIdentityError, TypeError, ValueError):
        restore_session_state()
        return BrainDumpConversionResult(
            "failed",
            "Brain-dump conversion failed. No session changes were retained.",
            mode="session",
        )
    except Exception:
        restore_session_state()
        raise

    return BrainDumpConversionResult(
        "already_applied"
        if all_already_applied
        else "created"
        if created_task_ids
        else "applied",
        "Brain-dump decisions are available for this session only.",
        task_ids=task_ids,
        created_task_ids=created_task_ids,
        settings=settings,
        mode="session",
    )


def initialize_task_id_counter(session_state):
    tasks = session_state["tasks"]
    if not isinstance(tasks, list):
        raise TaskIdentityError("Task identity cannot be established: the session task collection is invalid.")
    for task in tasks:
        if not isinstance(task, dict) or type(task.get("id")) is not int:
            raise TaskIdentityError("Task identity cannot be established: every session task must have an integer ID. No tasks were changed.")
    existing_max = max((task["id"] for task in tasks), default=0)
    watermark = session_state.get("task_id_high_watermark", 0)
    if type(watermark) is not int:
        raise TaskIdentityError("Task identity cannot be established: the session ID counter is invalid. No tasks were changed.")
    session_state["task_id_high_watermark"] = max(
        watermark, existing_max
    )


def observe_task_ids_or_stop(st_module):
    try:
        initialize_task_id_counter(st_module.session_state)
    except TaskIdentityError as exc:
        st_module.error(str(exc))
        st_module.stop()


def allocate_task_id(session_state):
    initialize_task_id_counter(session_state)
    session_state["task_id_high_watermark"] += 1
    return session_state["task_id_high_watermark"]


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
    def load_session_tasks(source):
        observe_task_ids_or_stop(st_module)
        tasks = st_module.session_state.tasks
        return LoadedTasks(
            [task for task in tasks if _task_visible_in_app(task)],
            source,
            list(tasks),
        )

    if not db_enabled_fn():
        return load_session_tasks("session")
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
                tasks = cur.fetchall()
                return LoadedTasks(tasks, "database", tasks)
    except psycopg.Error:
        return load_session_tasks("database_fallback")


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
                        pt_destination,
                        pt_protocol,
                        dme_dispensed,
                        post_op_plan,
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
    pt_destination="",
    pt_protocol="",
    dme_dispensed="",
    post_op_plan="",
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
    pt_destination_value = pt_destination.strip()
    pt_protocol_value = pt_protocol.strip()
    dme_dispensed_value = dme_dispensed.strip()
    post_op_plan_value = post_op_plan.strip()
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
                        pt_destination,
                        pt_protocol,
                        dme_dispensed,
                        post_op_plan,
                        created_date
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                        pt_destination_value,
                        pt_protocol_value,
                        dme_dispensed_value,
                        post_op_plan_value,
                        mountain_today(),
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
            "pt_destination": pt_destination_value,
            "pt_protocol": pt_protocol_value,
            "dme_dispensed": dme_dispensed_value,
            "post_op_plan": post_op_plan_value,
            "created_date": mountain_today(),
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
        "pt_destination",
        "pt_protocol",
        "dme_dispensed",
        "post_op_plan",
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
                        mountain_today(),
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
            "created_date": mountain_today(),
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
                        (case_id, protocol_id, mountain_today()),
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
                        mountain_today(),
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
            "created_date": mountain_today(),
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
                        mountain_today(),
                        mountain_today(),
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
            "updated_date": mountain_today(),
            "created_date": mountain_today(),
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
                        mountain_today(),
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
            "created_date": mountain_today(),
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


def load_lead_ma_assignments(db_enabled_fn, get_connection_fn, st_module=st):
    if not db_enabled_fn():
        return list(st_module.session_state.get("lead_ma_assignments", []))
    try:
        with get_connection_fn() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        ma_name,
                        provider_name,
                        stocking_rooms,
                        additional_tasks,
                        clinic_days,
                        status,
                        created_date,
                        updated_date
                    FROM lead_ma_assignments
                    ORDER BY ma_name ASC, updated_date DESC, id DESC
                    """
                )
                return cur.fetchall()
    except psycopg.Error:
        return list(st_module.session_state.get("lead_ma_assignments", []))


def add_lead_ma_assignment(
    ma_name,
    provider_name,
    stocking_rooms,
    additional_tasks,
    clinic_days,
    status="active",
    db_enabled_fn=None,
    get_connection_fn=None,
    st_module=st,
):
    ma_name_value = str(ma_name or "").strip()
    if not ma_name_value:
        return None

    provider_value = str(provider_name or "").strip()
    rooms_value = str(stocking_rooms or "").strip()
    tasks_value = str(additional_tasks or "").strip()
    days_value = str(clinic_days or "").strip()
    status_value = str(status or "active").strip() or "active"

    if db_enabled_fn and db_enabled_fn():
        with get_connection_fn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO lead_ma_assignments (
                        ma_name,
                        provider_name,
                        stocking_rooms,
                        additional_tasks,
                        clinic_days,
                        status,
                        created_date,
                        updated_date
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        ma_name_value,
                        provider_value,
                        rooms_value,
                        tasks_value,
                        days_value,
                        status_value,
                        mountain_today(),
                        mountain_today(),
                    ),
                )
                inserted = cur.fetchone()
                return inserted[0] if inserted else None

    assignments = st_module.session_state.setdefault("lead_ma_assignments", [])
    next_id = max([item.get("id", 0) for item in assignments], default=0) + 1
    assignments.append(
        {
            "id": next_id,
            "ma_name": ma_name_value,
            "provider_name": provider_value,
            "stocking_rooms": rooms_value,
            "additional_tasks": tasks_value,
            "clinic_days": days_value,
            "status": status_value,
            "created_date": mountain_today(),
            "updated_date": mountain_today(),
        }
    )
    return next_id


def update_lead_ma_assignment(assignment_id, db_enabled_fn=None, get_connection_fn=None, st_module=st, **fields):
    allowed_fields = {
        "ma_name",
        "provider_name",
        "stocking_rooms",
        "additional_tasks",
        "clinic_days",
        "status",
    }
    sanitized = {key: value for key, value in fields.items() if key in allowed_fields}
    if not sanitized:
        return

    sanitized["updated_date"] = mountain_today()

    if db_enabled_fn and db_enabled_fn():
        set_parts = []
        values = []
        for key, value in sanitized.items():
            set_parts.append(f"{key} = %s")
            values.append(value)
        values.append(assignment_id)
        with get_connection_fn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE lead_ma_assignments SET {', '.join(set_parts)} WHERE id = %s", tuple(values))
        return

    assignments = st_module.session_state.setdefault("lead_ma_assignments", [])
    for item in assignments:
        if item.get("id") == assignment_id:
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
                        mountain_today(),
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
            "created_date": mountain_today(),
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
                        mountain_today(),
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
            "created_date": mountain_today(),
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
                        mountain_today(),
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
            "created_date": mountain_today(),
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
                        mountain_today(),
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
            "created_date": mountain_today(),
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


def load_lead_documents(db_enabled_fn, get_connection_fn, st_module=st):
    if not db_enabled_fn():
        return list(st_module.session_state.get("lead_documents", []))
    try:
        with get_connection_fn() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        section_key,
                        record_type,
                        record_id,
                        title,
                        file_name,
                        file_mime,
                        file_bytes,
                        notes,
                        uploaded_by,
                        created_date
                    FROM lead_documents
                    ORDER BY created_date DESC, id DESC
                    """
                )
                return cur.fetchall()
    except psycopg.Error:
        return list(st_module.session_state.get("lead_documents", []))


def add_lead_document(
    section_key,
    title,
    file_name,
    file_mime,
    file_bytes,
    notes="",
    uploaded_by="",
    record_type=None,
    record_id=None,
    db_enabled_fn=None,
    get_connection_fn=None,
    st_module=st,
):
    section_value = str(section_key or "General").strip() or "General"
    file_name_value = str(file_name or "").strip()
    if not file_name_value or not file_bytes:
        return None

    title_value = str(title or "").strip() or file_name_value
    notes_value = str(notes or "").strip()
    uploaded_by_value = str(uploaded_by or "").strip()
    record_type_value = str(record_type or "").strip() or None
    try:
        record_id_value = int(record_id) if record_id is not None else None
    except (TypeError, ValueError):
        record_id_value = None

    if db_enabled_fn and db_enabled_fn():
        with get_connection_fn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO lead_documents (
                        section_key,
                        record_type,
                        record_id,
                        title,
                        file_name,
                        file_mime,
                        file_bytes,
                        notes,
                        uploaded_by,
                        created_date
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        section_value,
                        record_type_value,
                        record_id_value,
                        title_value,
                        file_name_value,
                        file_mime,
                        file_bytes,
                        notes_value,
                        uploaded_by_value,
                        mountain_today(),
                    ),
                )
                inserted = cur.fetchone()
                return inserted[0] if inserted else None

    docs = st_module.session_state.setdefault("lead_documents", [])
    next_id = max([item.get("id", 0) for item in docs], default=0) + 1
    docs.append(
        {
            "id": next_id,
            "section_key": section_value,
            "record_type": record_type_value,
            "record_id": record_id_value,
            "title": title_value,
            "file_name": file_name_value,
            "file_mime": file_mime,
            "file_bytes": file_bytes,
            "notes": notes_value,
            "uploaded_by": uploaded_by_value,
            "created_date": mountain_today(),
        }
    )
    return next_id


def delete_lead_document(document_id, db_enabled_fn=None, get_connection_fn=None, st_module=st):
    if db_enabled_fn and db_enabled_fn():
        with get_connection_fn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM lead_documents WHERE id = %s", (document_id,))
        return

    docs = st_module.session_state.setdefault("lead_documents", [])
    st_module.session_state["lead_documents"] = [
        item for item in docs if item.get("id") != document_id
    ]


def log_ai_event(envelope, day, db_enabled_fn=None, get_connection_fn=None, st_module=st):
    """Persist an AIEnvelope to ai_events. Returns the event id, or None on failure."""
    task_ids = [int(t) for t in envelope.task_ids()]
    if db_enabled_fn and db_enabled_fn():
        try:
            with get_connection_fn() as conn:
                with conn.cursor() as cur:
                    if (
                        envelope.pillar == "planning"
                        and envelope.kind == "suggestion"
                        and envelope.status == "proposed"
                    ):
                        # Serialize proposal creation so concurrent runs cannot leave two open plans.
                        cur.execute("SELECT pg_advisory_xact_lock(7319, 2042)")
                        cur.execute(
                            """
                            UPDATE ai_events
                            SET status = 'expired', outcome = 'ignored',
                                outcome_note = 'superseded by a newer Planner proposal',
                                resolved_at = NOW()
                            WHERE pillar = 'planning' AND kind = 'suggestion'
                              AND status = 'proposed'
                            """
                        )
                    cur.execute(
                        """
                        INSERT INTO ai_events (
                            day, pillar, kind, status, source, summary, text,
                            payload_json, task_ids, context_refs, expires_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                        RETURNING id
                        """,
                        (
                            day,
                            envelope.pillar,
                            envelope.kind,
                            envelope.status,
                            envelope.source,
                            envelope.summary,
                            envelope.text,
                            json.dumps(envelope.payload(), default=str),
                            task_ids,
                            list(envelope.context_refs),
                            envelope.expires_at,
                        ),
                    )
                    return cur.fetchone()[0]
        except psycopg.Error:
            return None

    events = st_module.session_state.setdefault("ai_events", [])
    if (
        envelope.pillar == "planning"
        and envelope.kind == "suggestion"
        and envelope.status == "proposed"
    ):
        now = datetime.now(MOUNTAIN_TIMEZONE)
        for event in events:
            if (
                event.get("pillar") == "planning"
                and event.get("kind") == "suggestion"
                and event.get("status") == "proposed"
            ):
                event.update(
                    status="expired",
                    outcome="ignored",
                    outcome_note="superseded by a newer Planner proposal",
                    resolved_at=now,
                )
    event_id = max([e["id"] for e in events], default=0) + 1
    events.append(
        {
            "id": event_id,
            "created_at": datetime.now(MOUNTAIN_TIMEZONE),
            "day": day,
            "pillar": envelope.pillar,
            "kind": envelope.kind,
            "status": envelope.status,
            "source": envelope.source,
            "summary": envelope.summary,
            "text": envelope.text,
            "payload_json": envelope.payload(),
            "task_ids": task_ids,
            "context_refs": list(envelope.context_refs),
            "expires_at": envelope.expires_at,
            "resolved_at": None,
            "outcome": None,
            "outcome_note": "",
        }
    )
    return event_id


def load_recent_ai_events(days=1, pillars=None, limit=30, db_enabled_fn=None, get_connection_fn=None, st_module=st):
    """Newest-first events from the last `days` app-local days."""
    cutoff = mountain_today() - timedelta(days=max(int(days), 1) - 1)
    pillar_list = list(pillars) if pillars else None
    if db_enabled_fn and db_enabled_fn():
        try:
            with get_connection_fn() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        """
                        SELECT id, created_at, day, pillar, kind, status, source, summary,
                               payload_json, task_ids, context_refs, expires_at,
                               resolved_at, outcome, outcome_note
                        FROM ai_events
                        WHERE day >= %s AND (%s::text[] IS NULL OR pillar = ANY(%s))
                        ORDER BY created_at DESC, id DESC
                        LIMIT %s
                        """,
                        (cutoff, pillar_list, pillar_list, limit),
                    )
                    return cur.fetchall()
        except psycopg.Error:
            return []

    events = st_module.session_state.get("ai_events", [])
    matches = [
        e for e in events
        if e["day"] >= cutoff and (pillar_list is None or e["pillar"] in pillar_list)
    ]
    return sorted(matches, key=lambda e: e["id"], reverse=True)[:limit]


def get_ai_event(event_id, db_enabled_fn=None, get_connection_fn=None, st_module=st):
    """Load one event without hiding persistence errors from an approval action."""
    if db_enabled_fn and db_enabled_fn():
        with get_connection_fn() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT id, created_at, day, pillar, kind, status, source, summary, text,
                           payload_json, task_ids, context_refs, expires_at, resolved_at,
                           outcome, outcome_note
                    FROM ai_events
                    WHERE id = %s
                    """,
                    (event_id,),
                )
                return cur.fetchone()

    return next(
        (event for event in st_module.session_state.get("ai_events", []) if event["id"] == event_id),
        None,
    )


def planner_task_suggestions(event):
    payload = event.get("payload_json") or {}
    if isinstance(payload, str):
        payload = json.loads(payload)
    if event.get("pillar") != "planning" or event.get("kind") != "suggestion":
        raise ValueError("This event is not a Planner task suggestion.")
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("The Planner proposal payload is invalid.")
    suggestions = []
    for item in payload["items"]:
        if not isinstance(item, dict) or item.get("type") != "task_suggestion":
            raise ValueError("The Planner proposal contains an invalid task suggestion.")
        data = item.get("data")
        if not isinstance(data, dict) or not str(data.get("title", "")).strip():
            raise ValueError("The Planner proposal contains a task without a title.")
        suggestions.append(data)
    if not suggestions:
        raise ValueError("The Planner proposal contains no tasks.")
    return deepcopy(suggestions)


def _parse_planner_date(value):
    if value is None or value == "":
        return None
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _parse_planner_time(value):
    if value is None or value == "":
        return None
    return value if isinstance(value, time) else time.fromisoformat(str(value))


def apply_planner_task_suggestions(
    event_id,
    today,
    task_factory=None,
    db_enabled_fn=None,
    get_connection_fn=None,
    st_module=st,
):
    """Atomically create a proposal's tasks and resolve its event as applied."""
    if db_enabled_fn and db_enabled_fn():
        with get_connection_fn() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT id, pillar, kind, status, payload_json, expires_at
                    FROM ai_events
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (event_id,),
                )
                event = cur.fetchone()
                if event is None or event["status"] != "proposed":
                    raise ValueError("This Planner proposal is no longer available.")
                if event.get("expires_at") and event["expires_at"] < datetime.now(MOUNTAIN_TIMEZONE):
                    cur.execute(
                        """
                        UPDATE ai_events
                        SET status = 'expired', outcome = 'ignored', resolved_at = NOW()
                        WHERE id = %s AND status = 'proposed'
                        """,
                        (event_id,),
                    )
                    expired = True
                else:
                    expired = False
                    suggestions = planner_task_suggestions(event)
                    task_ids = []
                    for item in suggestions:
                        cur.execute(
                            """
                            INSERT INTO tasks (
                                title, description, category, priority, status, created_date,
                                due_date, scheduled_date, scheduled_end_date, scheduled_time,
                                scheduled_minutes, recurrence_rule, recurrence_interval,
                                completed_date, completed_at
                            )
                            VALUES (%s, %s, %s, %s, 'todo', %s, %s, %s, %s, %s, %s, NULL, 1, NULL, NULL)
                            RETURNING id
                            """,
                            (
                                str(item["title"]).strip(),
                                str(item.get("description", "")).strip(),
                                item["category"],
                                item["priority"],
                                today,
                                _parse_planner_date(item.get("due_date")),
                                _parse_planner_date(item.get("scheduled_date")),
                                _parse_planner_date(item.get("scheduled_end_date")),
                                _parse_planner_time(item.get("scheduled_time")),
                                item.get("scheduled_minutes"),
                            ),
                        )
                        task_ids.append(cur.fetchone()["id"])
                    cur.execute(
                        """
                        UPDATE ai_events
                        SET status = 'applied', outcome = 'accepted', task_ids = %s,
                            outcome_note = %s, resolved_at = NOW()
                        WHERE id = %s AND status = 'proposed'
                        """,
                        (task_ids, f"{len(task_ids)} task(s) added", event_id),
                    )
                    if cur.rowcount != 1:
                        raise ValueError("The Planner proposal changed before it could be applied.")
            if expired:
                raise ValueError("This Planner proposal has expired.")
            return task_ids

    event = get_ai_event(event_id, st_module=st_module)
    if event is None or event["status"] != "proposed":
        raise ValueError("This Planner proposal is no longer available.")
    if event.get("expires_at") and event["expires_at"] < datetime.now(MOUNTAIN_TIMEZONE):
        event.update(
            status="expired",
            outcome="ignored",
            resolved_at=datetime.now(MOUNTAIN_TIMEZONE),
        )
        raise ValueError("This Planner proposal has expired.")
    suggestions = planner_task_suggestions(event)
    tasks = st_module.session_state.setdefault("tasks", [])
    original_tasks = list(tasks)
    original_watermark = st_module.session_state.get("task_id_high_watermark")
    original_event = dict(event)
    if task_factory is None:
        raise ValueError("Task creation is unavailable in session fallback mode.")
    try:
        task_ids = [task_factory(item) for item in suggestions]
        event.update(
            status="applied",
            outcome="accepted",
            task_ids=task_ids,
            outcome_note=f"{len(task_ids)} task(s) added",
            resolved_at=datetime.now(MOUNTAIN_TIMEZONE),
        )
        return task_ids
    except Exception:
        tasks[:] = original_tasks
        if original_watermark is None:
            st_module.session_state.pop("task_id_high_watermark", None)
        else:
            st_module.session_state["task_id_high_watermark"] = original_watermark
        event.clear()
        event.update(original_event)
        raise


def reject_planner_task_suggestions(event_id, db_enabled_fn=None, get_connection_fn=None, st_module=st):
    """Reject only a still-open, unexpired Planner task proposal."""
    if db_enabled_fn and db_enabled_fn():
        with get_connection_fn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE ai_events
                    SET status = 'rejected', outcome = 'rejected',
                        outcome_note = 'User rejected proposal', resolved_at = NOW()
                    WHERE id = %s AND pillar = 'planning' AND kind = 'suggestion'
                      AND status = 'proposed'
                      AND (expires_at IS NULL OR expires_at >= NOW())
                    """,
                    (event_id,),
                )
                if cur.rowcount != 1:
                    raise ValueError("This Planner proposal is no longer available.")
                return True

    event = get_ai_event(event_id, st_module=st_module)
    if event is None or event.get("pillar") != "planning" or event.get("kind") != "suggestion":
        raise ValueError("This Planner proposal is no longer available.")
    if event["status"] != "proposed":
        raise ValueError("This Planner proposal is no longer available.")
    if event.get("expires_at") and event["expires_at"] < datetime.now(MOUNTAIN_TIMEZONE):
        event.update(
            status="expired",
            outcome="ignored",
            resolved_at=datetime.now(MOUNTAIN_TIMEZONE),
        )
        raise ValueError("This Planner proposal has expired.")
    event.update(
        status="rejected",
        outcome="rejected",
        outcome_note="User rejected proposal",
        resolved_at=datetime.now(MOUNTAIN_TIMEZONE),
    )
    return True


def resolve_ai_event(event_id, status, outcome=None, note="", db_enabled_fn=None, get_connection_fn=None, st_module=st):
    """Record a lifecycle status and optional outcome for an event."""
    if db_enabled_fn and db_enabled_fn():
        try:
            with get_connection_fn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE ai_events
                        SET status = %s, outcome = %s, outcome_note = %s, resolved_at = NOW()
                        WHERE id = %s
                        """,
                        (status, outcome, note, event_id),
                    )
                    return cur.rowcount > 0
        except psycopg.Error:
            return False

    for event in st_module.session_state.get("ai_events", []):
        if event["id"] == event_id:
            event.update(
                status=status,
                outcome=outcome,
                outcome_note=note,
                resolved_at=datetime.now(MOUNTAIN_TIMEZONE),
            )
            return True
    return False


def find_conflicting_events(task_ids, day, exclude_id=None, db_enabled_fn=None, get_connection_fn=None, st_module=st):
    """Open (proposed) events for the day that touch any of the given task ids."""
    wanted = [int(t) for t in (task_ids or [])]
    if not wanted:
        return []
    if db_enabled_fn and db_enabled_fn():
        try:
            with get_connection_fn() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        """
                        SELECT id, pillar, kind, summary, task_ids
                        FROM ai_events
                        WHERE day = %s AND status = 'proposed' AND task_ids && %s
                          AND (%s::bigint IS NULL OR id <> %s)
                        ORDER BY id DESC
                        """,
                        (day, wanted, exclude_id, exclude_id),
                    )
                    return cur.fetchall()
        except psycopg.Error:
            return []

    return [
        e for e in st_module.session_state.get("ai_events", [])
        if e["day"] == day
        and e["status"] == "proposed"
        and e["id"] != exclude_id
        and set(e["task_ids"]) & set(wanted)
    ]


def shared_context_block(events, max_chars=1200):
    """Compact newest-first digest of events, safe to inject into other prompts."""
    lines = []
    used = 0
    for event in events or []:
        if event.get("source") == "fallback":
            continue
        stamp = event.get("created_at")
        stamp_text = stamp.strftime("%H:%M") if hasattr(stamp, "strftime") else ""
        state = event.get("outcome") or event.get("status")
        line = f"- [{event.get('pillar')}/{state} {stamp_text}] {event.get('summary')}".replace("  ", " ")
        if used + len(line) + 1 > max_chars:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


def expire_stale_ai_events(now=None, db_enabled_fn=None, get_connection_fn=None, st_module=st):
    """Mark proposals past their expires_at as expired/ignored. Returns the count."""
    now = now or datetime.now(MOUNTAIN_TIMEZONE)
    if db_enabled_fn and db_enabled_fn():
        try:
            with get_connection_fn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE ai_events
                        SET status = 'expired', outcome = 'ignored', resolved_at = NOW()
                        WHERE status = 'proposed' AND expires_at IS NOT NULL AND expires_at < %s
                        """,
                        (now,),
                    )
                    return cur.rowcount
        except psycopg.Error:
            return 0

    count = 0
    for event in st_module.session_state.get("ai_events", []):
        if event["status"] == "proposed" and event.get("expires_at") and event["expires_at"] < now:
            event.update(status="expired", outcome="ignored", resolved_at=now)
            count += 1
    return count


def record_task_outcome(task_id, outcome, db_enabled_fn=None, get_connection_fn=None, st_module=st):
    """Upgrade the outcome of applied AI events that touched a task (e.g. to 'completed')."""
    task_id = int(task_id)
    if db_enabled_fn and db_enabled_fn():
        try:
            with get_connection_fn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE ai_events
                        SET outcome = %s
                        WHERE status = 'applied' AND outcome IN ('accepted', 'auto_applied') AND task_ids @> %s::bigint[]
                        """,
                        (outcome, [task_id]),
                    )
                    return cur.rowcount
        except psycopg.Error:
            return 0

    count = 0
    for event in st_module.session_state.get("ai_events", []):
        if event["status"] == "applied" and event["outcome"] in ("accepted", "auto_applied") and task_id in event["task_ids"]:
            event["outcome"] = outcome
            count += 1
    return count
