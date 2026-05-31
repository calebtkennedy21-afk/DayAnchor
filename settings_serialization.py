import json
from datetime import date, datetime, time


def json_fallback(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def dumps_json_safe(payload):
    return json.dumps(payload, default=json_fallback)
