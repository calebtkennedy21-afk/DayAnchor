from datetime import date


def status_label(status):
    return {
        "todo": "Todo",
        "in_progress": "In Progress",
        "blocked": "Blocked",
        "completed": "Completed",
    }.get(status, status.replace("_", " ").title())


def recurrence_label(rule, interval):
    if not rule:
        return "No recurrence"
    if rule == "daily":
        return f"Every {interval} day(s)"
    if rule == "weekly":
        return f"Every {interval} week(s)"
    return "No recurrence"


def format_due(task):
    due_date = task.get("due_date")
    if not due_date:
        return "No due date"
    return due_date.strftime("%b %d, %Y") if hasattr(due_date, "strftime") else str(due_date)


def format_due_badge(task):
    due_date = task.get("due_date")
    if not due_date:
        return "No due"
    if not hasattr(due_date, "strftime"):
        return str(due_date)
    if due_date.year == date.today().year:
        return due_date.strftime("%b %d")
    return due_date.strftime("%b %d, %Y")


def format_schedule(task):
    scheduled_date = task.get("scheduled_date")
    scheduled_time = task.get("scheduled_time")
    scheduled_end_date = task.get("scheduled_end_date")
    if not scheduled_date:
        return "Unscheduled"
    if scheduled_end_date and scheduled_end_date != scheduled_date:
        if scheduled_time:
            return f'{scheduled_date.strftime("%b %d")} - {scheduled_end_date.strftime("%b %d")}, {scheduled_time.strftime("%I:%M %p").lstrip("0")}'
        return f'{scheduled_date.strftime("%b %d")} - {scheduled_end_date.strftime("%b %d")}'
    if not scheduled_time:
        return scheduled_date.strftime("%b %d")
    return f'{scheduled_date.strftime("%b %d")}, {scheduled_time.strftime("%I:%M %p").lstrip("0")}'


def format_schedule_badge(task):
    scheduled_date = task.get("scheduled_date")
    scheduled_time = task.get("scheduled_time")
    scheduled_end_date = task.get("scheduled_end_date")
    if not scheduled_date:
        return "Unscheduled"
    if scheduled_end_date and scheduled_end_date != scheduled_date:
        if scheduled_date.year == date.today().year and scheduled_end_date.year == date.today().year:
            date_label = f"{scheduled_date.strftime('%b %d')} - {scheduled_end_date.strftime('%b %d')}"
        else:
            date_label = f"{scheduled_date.strftime('%b %d, %Y')} - {scheduled_end_date.strftime('%b %d, %Y')}"
        if scheduled_time:
            return f"{date_label}, {scheduled_time.strftime('%I:%M %p').lstrip('0')}"
        return date_label
    if scheduled_date.year == date.today().year:
        date_label = scheduled_date.strftime("%b %d")
    else:
        date_label = scheduled_date.strftime("%b %d, %Y")
    if not scheduled_time:
        return date_label
    return f"{date_label}, {scheduled_time.strftime('%I:%M %p').lstrip('0')}"


def format_recurrence_badge(task):
    label = recurrence_label(task.get("recurrence_rule"), task.get("recurrence_interval") or 1)
    if label == "No recurrence":
        return "No repeat"
    return label
