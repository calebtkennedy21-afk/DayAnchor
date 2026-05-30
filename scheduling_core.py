import csv
import re
import zipfile
from datetime import date, datetime, time, timedelta
from io import BytesIO
from xml.etree import ElementTree as ET


_SCHEDULE_MONTH_NAMES = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _schedule_file_bytes(file_bytes):
    if isinstance(file_bytes, memoryview):
        return bytes(file_bytes)
    return file_bytes or b""


def _schedule_file_text(file_bytes):
    raw_bytes = _schedule_file_bytes(file_bytes)
    if not raw_bytes:
        return ""
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", errors="ignore")


def _infer_schedule_year(source_name, fallback_year=None):
    reference_year = fallback_year or date.today().year
    text = str(source_name or "").lower()
    match = re.search(r"(19|20)\d{2}", text)
    if match:
        return int(match.group(0))
    return reference_year


def _coerce_schedule_date(value, year_hint=None):
    if isinstance(value, date):
        return value

    raw_value = str(value or "").strip()
    if not raw_value:
        return None

    cleaned_value = raw_value.replace("\u2013", "-").replace("\u2014", "-")
    cleaned_value = re.sub(r"\s+", " ", cleaned_value)

    for pattern in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%m-%d-%Y",
        "%m-%d-%y",
        "%b %d %Y",
        "%b %d, %Y",
        "%B %d %Y",
        "%B %d, %Y",
    ):
        try:
            return datetime.strptime(cleaned_value, pattern).date()
        except ValueError:
            continue

    if re.fullmatch(r"\d{1,2}/\d{1,2}", cleaned_value):
        try:
            return datetime.strptime(f"{cleaned_value}/{year_hint or date.today().year}", "%m/%d/%Y").date()
        except ValueError:
            return None

    month_day_match = re.fullmatch(r"([A-Za-z]{3,9})\s+(\d{1,2})", cleaned_value)
    if month_day_match:
        month_value = _SCHEDULE_MONTH_NAMES.get(month_day_match.group(1).lower())
        if month_value:
            try:
                return date(year_hint or date.today().year, month_value, int(month_day_match.group(2)))
            except ValueError:
                return None

    if re.fullmatch(r"\d{5}(?:\.0+)?", cleaned_value):
        try:
            serial_value = int(float(cleaned_value))
        except ValueError:
            return None
        if 30000 <= serial_value <= 70000:
            return date(1899, 12, 30) + timedelta(days=serial_value)

    return None


def _xlsx_column_index(cell_reference):
    match = re.match(r"[A-Z]+", str(cell_reference or ""))
    if not match:
        return 0
    index_value = 0
    for character in match.group(0):
        index_value = index_value * 26 + (ord(character) - ord("A") + 1)
    return index_value - 1


def _read_xlsx_shared_strings(archive):
    shared_strings = []
    if "xl/sharedStrings.xml" not in archive.namelist():
        return shared_strings
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    namespace = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    for string_item in root.findall(".//a:si", namespace):
        text_bits = [node.text or "" for node in string_item.findall(".//a:t", namespace)]
        shared_strings.append("".join(text_bits))
    return shared_strings


def _read_xlsx_sheet_rows(file_bytes):
    raw_bytes = _schedule_file_bytes(file_bytes)
    if not raw_bytes:
        return []

    with zipfile.ZipFile(BytesIO(raw_bytes)) as archive:
        sheet_name = next(
            (name for name in archive.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")),
            None,
        )
        if not sheet_name:
            return []

        shared_strings = _read_xlsx_shared_strings(archive)
        root = ET.fromstring(archive.read(sheet_name))
        namespace = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        rows = []
        for row_node in root.findall(".//a:sheetData/a:row", namespace):
            indexed_values = []
            max_index = -1
            for cell_node in row_node.findall("a:c", namespace):
                cell_ref = cell_node.attrib.get("r", "")
                cell_index = _xlsx_column_index(cell_ref) if cell_ref else len(indexed_values)
                max_index = max(max_index, cell_index)
                cell_type = cell_node.attrib.get("t")
                cell_value = ""
                if cell_type == "s":
                    shared_string_index = cell_node.findtext("a:v", default="", namespaces=namespace)
                    if shared_string_index.isdigit() and int(shared_string_index) < len(shared_strings):
                        cell_value = shared_strings[int(shared_string_index)]
                elif cell_type == "inlineStr":
                    cell_value = cell_node.findtext(".//a:t", default="", namespaces=namespace)
                else:
                    cell_value = cell_node.findtext("a:v", default="", namespaces=namespace)
                indexed_values.append((cell_index, str(cell_value or "").strip()))

            if max_index < 0:
                continue

            row_values = [""] * (max_index + 1)
            for cell_index, cell_value in indexed_values:
                row_values[cell_index] = cell_value
            rows.append(row_values)

    return rows


def _split_schedule_text_rows(text_value):
    raw_text = str(text_value or "")
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if not lines:
        return []

    sample = "\n".join(lines[:5])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters="\t|,;")
        reader = csv.reader(lines, dialect)
        rows = [[str(cell).strip() for cell in row] for row in reader]
        if any(rows):
            return rows
    except csv.Error:
        pass

    rows = []
    for line in lines:
        if "\t" in line:
            rows.append([cell.strip() for cell in line.split("\t")])
        elif "|" in line:
            rows.append([cell.strip() for cell in line.split("|")])
        elif ";" in line:
            rows.append([cell.strip() for cell in line.split(";")])
        elif "," in line:
            rows.append([cell.strip() for cell in line.split(",")])
        else:
            rows.append([line])
    return rows


def _extract_schedule_entries_from_rows(rows, source_name=None, year_hint=None):
    normalized_rows = [list(row) for row in rows if any(str(cell or "").strip() for cell in row)]
    if not normalized_rows:
        return []

    inferred_year = _infer_schedule_year(source_name, year_hint)

    def looks_like_header(cell_value):
        normalized = str(cell_value or "").strip().lower()
        return any(
            marker in normalized
            for marker in ("date", "day", "provider", "on call", "on-call", "coverage", "call")
        )

    header_row_index = None
    date_column_index = None
    provider_column_index = None
    for row_index, row in enumerate(normalized_rows[:3]):
        header_hits = [index for index, value in enumerate(row) if looks_like_header(value)]
        if not header_hits:
            continue
        header_row_index = row_index
        for index in header_hits:
            lowered = str(row[index]).strip().lower()
            if date_column_index is None and any(marker in lowered for marker in ("date", "day")):
                date_column_index = index
            if provider_column_index is None and any(marker in lowered for marker in ("provider", "on call", "on-call", "coverage", "call")):
                provider_column_index = index
        if date_column_index is not None and provider_column_index is not None:
            break

    entries = []

    def add_entry(entry_date, provider_text):
        provider_value = str(provider_text or "").strip()
        if not entry_date or not provider_value:
            return
        entries.append({"date": entry_date, "providers": [provider_value]})

    if header_row_index is not None and date_column_index is not None and provider_column_index is not None:
        for row in normalized_rows[header_row_index + 1 :]:
            if max(date_column_index, provider_column_index) >= len(row):
                continue
            entry_date = _coerce_schedule_date(row[date_column_index], inferred_year)
            provider_text = row[provider_column_index].strip()
            if entry_date and provider_text:
                add_entry(entry_date, provider_text)

    if entries:
        return entries

    for row in normalized_rows:
        date_candidates = []
        for index, cell_value in enumerate(row):
            entry_date = _coerce_schedule_date(cell_value, inferred_year)
            if entry_date:
                date_candidates.append((index, entry_date))

        if len(date_candidates) != 1:
            continue

        date_column, entry_date = date_candidates[0]
        provider_bits = [str(cell).strip() for index, cell in enumerate(row) if index != date_column and str(cell or "").strip()]
        if not provider_bits:
            continue
        add_entry(entry_date, "\n".join(provider_bits))

    if entries:
        return entries

    for row in normalized_rows:
        row_text = " | ".join(str(cell).strip() for cell in row if str(cell or "").strip())
        if not row_text:
            continue
        for separator in (" - ", " – ", " — ", " : ", " | ", "\t"):
            if separator not in row_text:
                continue
            left_value, right_value = row_text.split(separator, 1)
            entry_date = _coerce_schedule_date(left_value, inferred_year)
            if not entry_date:
                continue
            add_entry(entry_date, right_value)
            break

    return entries


def _extract_schedule_entries_from_freeform_lines(text_value, source_name=None, year_hint=None):
    raw_lines = [line.strip() for line in str(text_value or "").splitlines() if line.strip()]
    if not raw_lines:
        return []

    inferred_year = _infer_schedule_year(source_name, year_hint)
    entries = []
    for line in raw_lines:
        for separator in (" - ", " – ", " — ", " : ", " | ", "\t"):
            if separator not in line:
                continue
            left_value, right_value = line.split(separator, 1)
            entry_date = _coerce_schedule_date(left_value, inferred_year)
            if not entry_date:
                continue
            provider_value = str(right_value or "").strip()
            if provider_value:
                entries.append({"date": entry_date, "providers": [provider_value]})
            break
    return entries


def parse_on_call_schedule_document(file_name, file_bytes):
    source_name = str(file_name or "on-call schedule")
    extension = source_name.lower().rsplit(".", 1)[-1] if "." in source_name else ""

    if extension == "xlsx":
        rows = _read_xlsx_sheet_rows(file_bytes)
        entries = _extract_schedule_entries_from_rows(rows, source_name=source_name)
    else:
        raw_text = _schedule_file_text(file_bytes)
        entries = _extract_schedule_entries_from_freeform_lines(raw_text, source_name=source_name)
        if not entries:
            rows = _split_schedule_text_rows(raw_text)
            entries = _extract_schedule_entries_from_rows(rows, source_name=source_name)

    by_date = {}
    for entry in entries:
        entry_date = entry.get("date")
        if not entry_date:
            continue
        by_date.setdefault(entry_date, [])
        for provider_value in entry.get("providers", []):
            normalized_provider = str(provider_value or "").strip()
            if normalized_provider and normalized_provider not in by_date[entry_date]:
                by_date[entry_date].append(normalized_provider)

    return {
        "source_name": source_name,
        "entry_count": len(entries),
        "by_date": by_date,
        "entries": entries,
    }


def providers_for_schedule_date(schedule_document, target_date):
    if not schedule_document or not target_date:
        return ""
    providers = schedule_document.get("by_date", {}).get(target_date, [])
    return "\n".join([provider for provider in providers if str(provider or "").strip()])


def shift_date_by_rule(value, rule, interval):
    if not value:
        return None
    safe_interval = max(1, int(interval or 1))
    if rule == "daily":
        return value + timedelta(days=safe_interval)
    if rule == "weekly":
        return value + timedelta(days=7 * safe_interval)
    return value


def priority_rank(priority):
    return {"high": 0, "medium": 1, "low": 2}.get(priority, 1)


def task_attention_signal(task, reference_date=None):
    today = reference_date or date.today()
    due_date = task.get("due_date")
    created_date = task.get("created_date")
    scheduled_date = task.get("scheduled_date")
    scheduled_time = task.get("scheduled_time")

    age_days = max(0, (today - created_date).days) if hasattr(created_date, "toordinal") else 0
    overdue_days = max(0, (today - due_date).days) if hasattr(due_date, "toordinal") else 0
    due_in_days = (due_date - today).days if hasattr(due_date, "toordinal") else None
    has_schedule = bool(scheduled_date and scheduled_time)
    high_unscheduled = task.get("priority") == "high" and not has_schedule

    if overdue_days > 0:
        return {
            "tier": 0,
            "sort_key": (0, -overdue_days, priority_rank(task.get("priority")), due_date or date.min),
            "label": f"Overdue by {overdue_days}d",
            "detail": f"{overdue_days} day(s) overdue",
            "age_days": age_days,
            "overdue_days": overdue_days,
            "due_in_days": due_in_days,
            "high_unscheduled": high_unscheduled,
        }

    if due_date == today:
        return {
            "tier": 1,
            "sort_key": (1, priority_rank(task.get("priority")), scheduled_time or time(23, 59), -age_days),
            "label": "Due today",
            "detail": "Due today",
            "age_days": age_days,
            "overdue_days": 0,
            "due_in_days": 0,
            "high_unscheduled": high_unscheduled,
        }

    if high_unscheduled:
        attention_label = f"Aging {age_days}d" if age_days else "High priority"
        return {
            "tier": 2,
            "sort_key": (2, -age_days, priority_rank(task.get("priority")), due_date or date.max),
            "label": attention_label,
            "detail": "High-priority task waiting for a slot",
            "age_days": age_days,
            "overdue_days": 0,
            "due_in_days": due_in_days,
            "high_unscheduled": True,
        }

    if due_in_days is not None and due_in_days <= 3:
        return {
            "tier": 3,
            "sort_key": (3, due_in_days, priority_rank(task.get("priority")), -age_days),
            "label": f"Due in {due_in_days}d",
            "detail": "Due soon",
            "age_days": age_days,
            "overdue_days": 0,
            "due_in_days": due_in_days,
            "high_unscheduled": False,
        }

    return {
        "tier": 4,
        "sort_key": (4, due_date or date.max, priority_rank(task.get("priority")), -age_days),
        "label": f"Age {age_days}d" if age_days >= 7 else "Routine",
        "detail": "Routine",
        "age_days": age_days,
        "overdue_days": 0,
        "due_in_days": due_in_days,
        "high_unscheduled": False,
    }


def task_attention_sort_key(task, reference_date=None):
    return task_attention_signal(task, reference_date).get("sort_key")


def safe_int(value, fallback):
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(fallback)


def clinic_visit_templates():
    return {
        "blank": {
            "label": "Blank clinic capture",
            "title": "",
            "description": "",
            "priority": "medium",
            "schedule_enabled": False,
            "scheduled_time": time(9, 0),
            "scheduled_minutes": 30,
        },
        "new_consult": {
            "label": "New consult",
            "title": "Clinic consult block",
            "description": "Initial evaluation, exam, imaging review, and plan discussion.",
            "priority": "high",
            "schedule_enabled": True,
            "scheduled_time": time(8, 30),
            "scheduled_minutes": 45,
        },
        "post_op_follow_up": {
            "label": "Post-op follow-up",
            "title": "Post-op follow-up",
            "description": "Wound check, symptom review, restrictions, and next-step planning.",
            "priority": "medium",
            "schedule_enabled": True,
            "scheduled_time": time(9, 15),
            "scheduled_minutes": 20,
        },
        "imaging_review": {
            "label": "Imaging review",
            "title": "Imaging review visit",
            "description": "Review studies, confirm the working diagnosis, and define the next step.",
            "priority": "medium",
            "schedule_enabled": True,
            "scheduled_time": time(10, 0),
            "scheduled_minutes": 20,
        },
        "procedure_checkin": {
            "label": "Procedure check-in",
            "title": "Procedure planning visit",
            "description": "Procedure discussion, consent prep, and day-of logistics.",
            "priority": "high",
            "schedule_enabled": True,
            "scheduled_time": time(11, 0),
            "scheduled_minutes": 30,
        },
        "phone_follow_up": {
            "label": "Phone follow-up",
            "title": "Phone follow-up",
            "description": "Brief check-in, results review, and next steps without an in-person slot.",
            "priority": "low",
            "schedule_enabled": False,
            "scheduled_time": time(9, 0),
            "scheduled_minutes": 15,
        },
        "urgent_add_on": {
            "label": "Urgent add-on",
            "title": "Urgent add-on visit",
            "description": "High-priority add-on with focused assessment and rapid decision-making.",
            "priority": "high",
            "schedule_enabled": True,
            "scheduled_time": time(13, 0),
            "scheduled_minutes": 20,
        },
    }


def personal_schedule_templates():
    return {
        "blank": {
            "label": "Blank — custom block",
            "title": "",
            "description": "",
            "priority": "medium",
            "scheduled_time": time(18, 0),
            "scheduled_minutes": 60,
            "all_day": False,
        },
        "dinner": {
            "label": "Dinner",
            "title": "Dinner",
            "description": "Protected personal time for dinner, social plans, or a calm evening block.",
            "priority": "medium",
            "scheduled_time": time(18, 30),
            "scheduled_minutes": 90,
            "all_day": False,
        },
        "event": {
            "label": "Event",
            "title": "Evening event",
            "description": "Concert, outing, family event, or another fixed personal commitment.",
            "priority": "medium",
            "scheduled_time": time(19, 0),
            "scheduled_minutes": 120,
            "all_day": False,
        },
        "appointment": {
            "label": "Appointment",
            "title": "Personal appointment",
            "description": "Medical, dental, or life-admin appointment that needs a real calendar slot.",
            "priority": "high",
            "scheduled_time": time(9, 0),
            "scheduled_minutes": 60,
            "all_day": False,
        },
        "travel": {
            "label": "Travel",
            "title": "Travel block",
            "description": "Transit, airport time, or commute buffer.",
            "priority": "medium",
            "scheduled_time": time(8, 0),
            "scheduled_minutes": 180,
            "all_day": False,
        },
        "vacation": {
            "label": "Vacation / trip",
            "title": "Vacation block",
            "description": "All-day personal block for a trip, day off, or protected downtime.",
            "priority": "low",
            "scheduled_time": time(8, 0),
            "scheduled_minutes": 480,
            "all_day": True,
            "scheduled_end_offset_days": 4,
        },
        "clinic_shift": {
            "label": "Clinic shift",
            "title": "Clinic shift",
            "description": "Scheduled clinic session or on-call block.",
            "priority": "high",
            "scheduled_time": time(7, 0),
            "scheduled_minutes": 480,
            "all_day": False,
        },
        "meeting": {
            "label": "Meeting",
            "title": "Meeting",
            "description": "Team meeting, case conference, or scheduled call.",
            "priority": "medium",
            "scheduled_time": time(10, 0),
            "scheduled_minutes": 60,
            "all_day": False,
        },
    }


def scheduled_date_range(task):
    scheduled_date = task.get("scheduled_date")
    if not scheduled_date:
        return []
    scheduled_end_date = task.get("scheduled_end_date") or scheduled_date
    if scheduled_end_date < scheduled_date:
        scheduled_end_date = scheduled_date
    span = (scheduled_end_date - scheduled_date).days
    return [scheduled_date + timedelta(days=offset) for offset in range(span + 1)]


def scheduled_span_position(task, day):
    dates = scheduled_date_range(task)
    if not dates or day not in dates:
        return None
    if len(dates) == 1:
        return "single"
    if day == dates[0]:
        return "start"
    if day == dates[-1]:
        return "end"
    return "middle"


def scheduled_minutes_on_day(task, day):
    if day not in scheduled_date_range(task):
        return 0
    try:
        return max(0, int(task.get("scheduled_minutes") or 0))
    except (TypeError, ValueError):
        return 0


def build_week_rebalance_moves(upcoming_tasks, week_days, daily_capacity_minutes):
    scheduled_by_day = {day: [] for day in week_days}
    scheduled_minutes_by_day = {day: 0 for day in week_days}

    for task in upcoming_tasks:
        for task_day in scheduled_date_range(task):
            if task_day in scheduled_by_day:
                scheduled_by_day[task_day].append(task)
                scheduled_minutes_by_day[task_day] += scheduled_minutes_on_day(task, task_day)

    moves = []
    moved_task_ids = set()
    overloaded_days = [day for day in week_days if scheduled_minutes_by_day[day] > daily_capacity_minutes]

    for source_day in overloaded_days:
        if scheduled_minutes_by_day[source_day] <= daily_capacity_minutes:
            continue

        # Rebalance only low-priority single-day blocks so the action is safe and predictable.
        candidates = [
            task
            for task in scheduled_by_day[source_day]
            if task.get("id") not in moved_task_ids
            and task.get("priority") == "low"
            and task.get("scheduled_date") == source_day
            and (task.get("scheduled_end_date") is None or task.get("scheduled_end_date") == source_day)
            and task.get("scheduled_time")
            and scheduled_minutes_on_day(task, source_day) > 0
        ]
        candidates.sort(
            key=lambda task: (
                task.get("due_date") or date.max,
                task.get("scheduled_time") or time(23, 59),
            ),
            reverse=True,
        )

        for task in candidates:
            if scheduled_minutes_by_day[source_day] <= daily_capacity_minutes:
                break

            task_minutes = scheduled_minutes_on_day(task, source_day)
            if task_minutes <= 0:
                continue

            target_day = None
            search_days = [day for day in week_days if day > source_day] + [day for day in week_days if day < source_day]
            for candidate_day in search_days:
                if scheduled_minutes_by_day[candidate_day] + task_minutes > daily_capacity_minutes:
                    continue
                due_date = task.get("due_date")
                if due_date and candidate_day > due_date:
                    continue
                target_day = candidate_day
                break

            if not target_day:
                continue

            moves.append({"task": task, "source_day": source_day, "target_day": target_day, "minutes": task_minutes})
            moved_task_ids.add(task.get("id"))
            scheduled_minutes_by_day[source_day] -= task_minutes
            scheduled_minutes_by_day[target_day] += task_minutes

    return moves
