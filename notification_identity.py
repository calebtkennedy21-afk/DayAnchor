"""Stable source identities for durable notification deduplication."""

from dataclasses import dataclass
from uuid import uuid4


@dataclass(frozen=True)
class NotificationIdentityBackfillResult:
    settings: dict
    status: str
    durable_ready: bool
    reminders_assigned: int = 0
    family_items_assigned: int = 0
    message: str = ""


def new_notification_source_id():
    """Create an ID to persist with a newly created reminder or family item."""
    return uuid4().hex


def notification_source_id(item, legacy_id_field):
    """Return a persisted source ID without relying on synthesized legacy IDs."""
    source_id = item.get("notification_source_id")
    if source_id is None or not str(source_id).strip():
        source_id = item.get(legacy_id_field)
    if source_id is None or not str(source_id).strip():
        return None
    return str(source_id).strip()


def prepare_notification_identity_backfill(settings, id_factory=new_notification_source_id):
    """Prepare a copy with stable IDs while preserving legacy alert-key fields."""
    updated = dict(settings or {})
    counts = {"quick_reminders": 0, "family_schedule_items": 0}

    for collection_key, legacy_id_field in (
        ("quick_reminders", "reminder_id"),
        ("family_schedule_items", "item_id"),
    ):
        raw_items = updated.get(collection_key)
        if not isinstance(raw_items, list):
            continue
        copied_items = []
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                copied_items.append(raw_item)
                continue
            item = dict(raw_item)
            source_id = notification_source_id(item, legacy_id_field)
            if source_id is None:
                source_id = id_factory()
                if not isinstance(source_id, str) or not source_id.strip():
                    raise ValueError("id_factory must return a non-empty string.")
                source_id = source_id.strip()
            if item.get("notification_source_id") != source_id:
                item["notification_source_id"] = source_id
                counts[collection_key] += 1
            copied_items.append(item)
        updated[collection_key] = copied_items

    return updated, counts


def persist_notification_identity_backfill(
    settings,
    *,
    current_settings,
    baseline,
    database_available,
    pending_draft,
    persist,
    id_factory=new_notification_source_id,
):
    """Persist an explicit backfill through a caller-provided CAS operation."""
    if not isinstance(settings, dict) or settings != current_settings:
        return NotificationIdentityBackfillResult(
            dict(settings or {}),
            "conflict",
            False,
            message="Settings changed in this session; reload before assigning durable notification identities.",
        )
    if pending_draft is not None:
        return NotificationIdentityBackfillResult(
            dict(settings),
            "conflict",
            False,
            message="A pending settings draft must be reconciled before assigning durable notification identities.",
        )

    updated, counts = prepare_notification_identity_backfill(settings, id_factory)
    assigned = counts["quick_reminders"] + counts["family_schedule_items"]
    if not database_available:
        return NotificationIdentityBackfillResult(
            updated,
            "session_only",
            False,
            counts["quick_reminders"],
            counts["family_schedule_items"],
            "The database is unavailable; these identities are not durable and are not ready for notification enqueueing.",
        )
    if not isinstance(baseline, dict) or not baseline.get("known"):
        return NotificationIdentityBackfillResult(
            updated,
            "conflict",
            False,
            counts["quick_reminders"],
            counts["family_schedule_items"],
            "The durable settings baseline is unknown; identities are not ready for notification enqueueing.",
        )
    if assigned == 0:
        return NotificationIdentityBackfillResult(
            updated,
            "already_persisted",
            True,
            message="Stable notification source identities are already present in the loaded durable settings.",
        )

    save_result = persist(updated, baseline.get("payload"), True)
    if getattr(save_result, "status", None) == "persisted":
        return NotificationIdentityBackfillResult(
            updated,
            "persisted",
            True,
            counts["quick_reminders"],
            counts["family_schedule_items"],
            "Stable notification source identities were persisted.",
        )

    save_status = getattr(save_result, "status", "failed")
    save_message = getattr(save_result, "message", "Settings persistence did not complete.")
    return NotificationIdentityBackfillResult(
        updated,
        save_status,
        False,
        counts["quick_reminders"],
        counts["family_schedule_items"],
        f"{save_message} Items are not ready for durable notification enqueueing.",
    )
