import json
import re
from collections import defaultdict
from datetime import date, timedelta
from uuid import uuid4

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_DAY_HEADER_RE = re.compile(
    r"^\s*(?:#+\s*)?((?:day|workout|session)\s*\d+|" + "|".join(DAY_NAMES) + r"|mon|tues?|wed|thu(?:rs?)?|fri|sat|sun)\b\.?\s*[:\-–—]?\s*(.*)$",
    re.IGNORECASE,
)
_WEEK_HEADER_RE = re.compile(r"^\s*(?:#+\s*)?(week\s*\d+)\b\s*[:\-–—]?\s*(.*)$", re.IGNORECASE)


def day_label(day):
    week = day.get("week") or ""
    return f"{week} - {day['name']}" if week else day["name"]
_SETS_REPS_RE = re.compile(
    r"(\d+)\s*(?:x|×|sets?\s*(?:of|x)?)\s*(\d+(?:\s*[-–]\s*\d+)?(?:\s*(?:sec|secs|seconds|s|min|mins|reps?))?)",
    re.IGNORECASE,
)
_BULLET_RE = re.compile(r"^\s*(?:[-*•▪◦]|\d+[.)])\s*")


def extract_pdf_text(file_bytes):
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(file_bytes)
    pages = []
    for index in range(len(pdf)):
        text_page = pdf[index].get_textpage()
        pages.append(text_page.get_text_range())
    return "\n".join(pages)


def _clean_name(text):
    cleaned = _BULLET_RE.sub("", text or "")
    return re.sub(r"\s+", " ", cleaned).strip(" :-–—|,;")


def parse_workout_text(text):
    """Deterministic fallback parser for plain-text workout plans."""
    days = []
    current = None
    last_exercise = None
    week = ""

    def start_day(name, focus=""):
        nonlocal current, last_exercise
        current = {"name": name, "week": week, "focus": focus, "exercises": [], "notes": ""}
        days.append(current)
        last_exercise = None

    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        week_header = _WEEK_HEADER_RE.match(line)
        if week_header:
            week = week_header.group(1).title()
            current = None
            last_exercise = None
            continue
        header = _DAY_HEADER_RE.match(line)
        if header and not _SETS_REPS_RE.search(line):
            start_day(header.group(1).title(), _clean_name(header.group(2)))
            continue
        match = _SETS_REPS_RE.search(line)
        if match:
            if current is None:
                start_day("Day 1")
            name = _clean_name((line[: match.start()] + " " + line[match.end():]))
            last_exercise = {
                "name": name or "Exercise",
                "sets": int(match.group(1)),
                "reps": re.sub(r"\s+", "", match.group(2)),
                "notes": "",
            }
            current["exercises"].append(last_exercise)
        elif current is not None and (_BULLET_RE.match(line) or ":" in line):
            # Cardio/rest style entries ("Easy run: 3 miles") have no sets x reps.
            name, _, detail = _clean_name(line).partition(":")
            last_exercise = {"name": name.strip() or "Activity", "sets": 0, "reps": detail.strip(), "notes": ""}
            current["exercises"].append(last_exercise)
        elif current is not None:
            note = _clean_name(line)
            if last_exercise is not None:
                last_exercise["notes"] = f"{last_exercise['notes']} {note}".strip()
            else:
                current["notes"] = f"{current['notes']} {note}".strip()
    return [day for day in days if day["exercises"] or day["notes"]]


def _to_int(value, default=0):
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _to_float(value, default=0.0):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def normalize_plan_days(raw_days):
    days = []
    for raw_day in raw_days if isinstance(raw_days, list) else []:
        if not isinstance(raw_day, dict):
            continue
        exercises = []
        for raw_ex in raw_day.get("exercises") or []:
            if not isinstance(raw_ex, dict):
                continue
            name = str(raw_ex.get("name") or "").strip()
            if not name:
                continue
            exercises.append(
                {
                    "name": name,
                    "sets": max(_to_int(raw_ex.get("sets"), 0), 0),
                    "reps": str(raw_ex.get("reps") or "").strip(),
                    "notes": str(raw_ex.get("notes") or "").strip(),
                }
            )
        name = str(raw_day.get("name") or "").strip() or f"Day {len(days) + 1}"
        days.append(
            {
                "name": name,
                "week": str(raw_day.get("week") or "").strip(),
                "focus": str(raw_day.get("focus") or "").strip(),
                "exercises": exercises,
                "notes": str(raw_day.get("notes") or "").strip(),
            }
        )
    return days


def build_plan(name, days, source_file="", today=None):
    return {
        "plan_id": uuid4().hex[:12],
        "name": str(name or "").strip() or "Workout plan",
        "source_file": str(source_file or ""),
        "created_date": (today or date.today()).isoformat(),
        "active": True,
        "days": normalize_plan_days(days),
    }


def normalize_plans(raw_plans):
    plans = []
    for raw in raw_plans if isinstance(raw_plans, list) else []:
        if not isinstance(raw, dict) or not raw.get("plan_id"):
            continue
        plans.append(
            {
                "plan_id": str(raw["plan_id"]),
                "name": str(raw.get("name") or "Workout plan"),
                "source_file": str(raw.get("source_file") or ""),
                "created_date": str(raw.get("created_date") or ""),
                "active": bool(raw.get("active", False)),
                "days": normalize_plan_days(raw.get("days")),
            }
        )
    return plans


def build_log(log_date, plan_id, day_name, exercises, duration_min, effort, notes=""):
    return {
        "log_id": uuid4().hex[:12],
        "date": log_date.isoformat() if isinstance(log_date, date) else str(log_date),
        "plan_id": str(plan_id or ""),
        "day_name": str(day_name or "").strip(),
        "duration_min": max(_to_int(duration_min), 0),
        "effort": min(max(_to_int(effort, 0), 0), 10),
        "notes": str(notes or "").strip(),
        "exercises": normalize_log_exercises(exercises),
    }


def normalize_log_exercises(raw_exercises):
    cleaned = []
    for raw in raw_exercises if isinstance(raw_exercises, list) else []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        cleaned.append(
            {
                "name": name,
                "sets": max(_to_int(raw.get("sets")), 0),
                "reps": str(raw.get("reps") or "").strip(),
                "weight": max(_to_float(raw.get("weight")), 0.0),
                "done": bool(raw.get("done", True)),
            }
        )
    return cleaned


def normalize_logs(raw_logs):
    logs = []
    for raw in raw_logs if isinstance(raw_logs, list) else []:
        if not isinstance(raw, dict) or not raw.get("log_id"):
            continue
        try:
            date.fromisoformat(str(raw.get("date")))
        except ValueError:
            continue
        logs.append(
            {
                "log_id": str(raw["log_id"]),
                "date": str(raw["date"]),
                "plan_id": str(raw.get("plan_id") or ""),
                "day_name": str(raw.get("day_name") or ""),
                "duration_min": max(_to_int(raw.get("duration_min")), 0),
                "effort": min(max(_to_int(raw.get("effort")), 0), 10),
                "notes": str(raw.get("notes") or ""),
                "exercises": normalize_log_exercises(raw.get("exercises")),
            }
        )
    return sorted(logs, key=lambda item: item["date"], reverse=True)


def _first_int(text, default=0):
    match = re.search(r"\d+", str(text or ""))
    return int(match.group()) if match else default


def exercise_volume(exercise):
    if not exercise.get("done", True):
        return 0.0
    return exercise["sets"] * _first_int(exercise["reps"]) * exercise["weight"]


def log_volume(log):
    return sum(exercise_volume(ex) for ex in log["exercises"])


def week_start(day_value):
    return day_value - timedelta(days=day_value.weekday())


def weekly_summary(logs, planned_per_week, today=None, weeks=8):
    """Per-week workout counts, adherence, volume, and average effort, oldest first."""
    today = today or date.today()
    start = week_start(today) - timedelta(weeks=weeks - 1)
    buckets = defaultdict(list)
    for log in logs:
        log_day = date.fromisoformat(log["date"])
        if log_day >= start:
            buckets[week_start(log_day)].append(log)
    rows = []
    for offset in range(weeks):
        bucket_start = start + timedelta(weeks=offset)
        entries = buckets.get(bucket_start, [])
        efforts = [entry["effort"] for entry in entries if entry["effort"]]
        adherence = min(len(entries) / planned_per_week, 1.0) if planned_per_week else None
        rows.append(
            {
                "week": bucket_start.isoformat(),
                "workouts": len(entries),
                "minutes": sum(entry["duration_min"] for entry in entries),
                "volume": round(sum(log_volume(entry) for entry in entries), 1),
                "avg_effort": round(sum(efforts) / len(efforts), 1) if efforts else None,
                "adherence_pct": round(adherence * 100) if adherence is not None else None,
            }
        )
    return rows


def exercise_progression(logs, exercise_name):
    """Date-ordered top weight and estimated volume for one exercise."""
    target = exercise_name.strip().lower()
    points = []
    for log in logs:
        matches = [ex for ex in log["exercises"] if ex["name"].lower() == target and ex["done"]]
        if matches:
            points.append(
                {
                    "date": log["date"],
                    "top_weight": max(ex["weight"] for ex in matches),
                    "volume": sum(exercise_volume(ex) for ex in matches),
                }
            )
    return sorted(points, key=lambda item: item["date"])


def logged_exercise_names(logs):
    return sorted({ex["name"] for log in logs for ex in log["exercises"]}, key=str.lower)


def current_streak_weeks(weekly_rows):
    """Consecutive weeks (ending this or last week) with at least one workout."""
    streak = 0
    rows = list(weekly_rows)
    if rows and rows[-1]["workouts"] == 0:
        rows = rows[:-1]
    for row in reversed(rows):
        if row["workouts"] <= 0:
            break
        streak += 1
    return streak


def parse_plan_json(text):
    if not text:
        return []
    blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    blob = blocks[-1] if blocks else None
    if not blob:
        match = re.search(r"(\{\s*\"days\"\s*:\s*\[.*\]\s*\})", text, flags=re.DOTALL)
        blob = match.group(1) if match else None
    if not blob:
        return []
    try:
        payload = json.loads(blob)
    except json.JSONDecodeError:
        return []
    return normalize_plan_days(payload.get("days") if isinstance(payload, dict) else None)


def generate_workout_plan_parse(text, ai_enabled_fn, ai_api_key_fn, ai_model_name_fn, openai_cls=OpenAI):
    """Returns (days, error). Falls back to the deterministic parser when AI is unavailable."""
    if not (text or "").strip():
        return [], "No text to parse."
    if not ai_enabled_fn():
        return parse_workout_text(text), ""
    try:
        client = openai_cls(api_key=ai_api_key_fn())
        response = client.chat.completions.create(
            model=ai_model_name_fn(),
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": "You extract structured workout plans from text. Never invent exercises that are not in the text.",
                },
                {
                    "role": "user",
                    "content": (
                        "Extract every training day with its exercises, as one entry per calendar day. If the plan spans multiple "
                        "weeks, emit a separate entry for each day of each week and set week (e.g. 'Week 2'); never merge the same "
                        "weekday across weeks. Keep sets as an integer (0 if unspecified, e.g. runs, rides, rest), "
                        "reps as a string holding the volume (e.g. '8-10', '30 sec', '3 miles', '20-30 min'), "
                        "and put coaching cues in notes.\n\n"
                        f"Plan text:\n{text[:24000]}\n\n"
                        "Return only this JSON in a json code block:\n"
                        "{\"days\": [{\"name\": \"Monday\", \"week\": \"Week 1\", \"focus\": \"Upper body\", \"notes\": \"\", "
                        "\"exercises\": [{\"name\": \"...\", \"sets\": 3, \"reps\": \"8-10\", \"notes\": \"...\"}]}]}"
                    ),
                },
            ],
        )
        content = response.choices[0].message.content if response.choices else ""
        days = parse_plan_json(content)
        if days:
            return days, ""
        return parse_workout_text(text), "AI returned no valid plan; used basic parser instead."
    except Exception as exc:
        return parse_workout_text(text), f"AI parse failed ({exc}); used basic parser instead."
