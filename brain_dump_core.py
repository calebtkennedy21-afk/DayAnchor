import re
from datetime import date, timedelta, time


_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _next_weekday(day_name, today):
    target = _WEEKDAYS.get(day_name.lower())
    if target is None:
        return None
    delta = (target - today.weekday()) % 7
    return today + timedelta(days=delta or 7)


def _parse_time(text):
    match = re.search(r"\b(?:at\s+)?(\d{1,2})(?::([0-5]\d))?\s*(am|pm|a|p)\b", text, re.IGNORECASE)
    if not match:
        match = re.search(r"\b(?:at\s+)?([01]?\d|2[0-3]):([0-5]\d)\b", text)
        if not match:
            return None
        return time(int(match.group(1)), int(match.group(2)))
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    suffix = match.group(3).lower()
    if suffix.startswith("p") and hour != 12:
        hour += 12
    if suffix.startswith("a") and hour == 12:
        hour = 0
    return time(hour, minute)


def _parse_date(text, today):
    lowered = text.lower()
    if re.search(r"\b(today|now)\b", lowered):
        return today
    if re.search(r"\b(tomorrow|tonight)\b", lowered):
        return today + timedelta(days=1)
    weekday_match = re.search(r"\b(?:next\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", lowered)
    if weekday_match:
        return _next_weekday(weekday_match.group(1), today)
    iso_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", lowered)
    if iso_match:
        try:
            return date.fromisoformat(iso_match.group(1))
        except ValueError:
            return None
    return None


def _clean_title(text):
    cleaned = re.sub(r"\b(?:today|now|tomorrow|tonight|next\s+week)\b", " ", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:next\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:at\s+)?\d{1,2}(?::[0-5]\d)?\s*(?:am|pm|a|p)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:high|urgent|asap|critical|medium|normal|low|later|someday)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:clinic|personal|family)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -,:;")
    return cleaned or text.strip()


def parse_brain_dump_line(raw_text, today=None, defaults=None):
    today = today or date.today()
    defaults = defaults or {}
    raw = str(raw_text or "").strip()
    if not raw:
        return None

    lowered = raw.lower()
    item_type = "task"
    if re.match(r"^(note|notes|idea|ideas)\s*:", lowered):
        item_type = "note" if lowered.startswith(("note", "notes")) else "idea"
    elif re.match(r"^(remind me|reminder|remember)\b", lowered):
        item_type = "reminder"

    category = "Clinic" if re.search(r"\b(clinic|patient|chart|referral|huddle|post-op|postop)\b", lowered) else defaults.get("default_category", "Personal")
    if re.search(r"\bfamily\b", lowered):
        category = "Family"
    priority = "high" if re.search(r"\b(urgent|asap|critical|high)\b", lowered) else "low" if re.search(r"\b(low|later|someday)\b", lowered) else defaults.get("default_priority", "medium")
    due_date = _parse_date(raw, today)
    scheduled_time = _parse_time(raw)
    title = re.sub(r"^(note|notes|idea|ideas|remind me|reminder|remember|task)\s*:\s*", "", raw, flags=re.IGNORECASE)
    title = _clean_title(title)
    if item_type in ("note", "idea"):
        due_date = None
        scheduled_time = None

    return {
        "raw_text": raw,
        "title": title,
        "item_type": item_type,
        "category": category if category in ("Personal", "Clinic", "Family") else "Personal",
        "priority": priority if priority in ("high", "medium", "low") else "medium",
        "due_date": due_date,
        "scheduled_date": due_date if scheduled_time else None,
        "scheduled_time": scheduled_time,
        "status": "new",
        "confidence": round(min(0.98, 0.55 + (0.12 if due_date else 0) + (0.12 if scheduled_time else 0) + (0.08 if item_type != "task" else 0)), 2),
    }


def parse_brain_dump(text, today=None, defaults=None):
    entries = []
    for line in str(text or "").splitlines():
        parsed = parse_brain_dump_line(line, today=today, defaults=defaults)
        if parsed:
            entries.append(parsed)
    return entries
