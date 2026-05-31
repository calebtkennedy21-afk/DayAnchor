import json
import sys
from datetime import date, datetime, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from settings_serialization import dumps_json_safe


def test_dumps_json_safe_serializes_date_time_and_datetime():
    payload = {
        "family_schedule_items": [
            {
                "title": "Ortho follow-up",
                "start_date": date(2026, 5, 31),
                "start_time": time(8, 30),
                "updated_at": datetime(2026, 5, 31, 10, 15, 45),
            }
        ]
    }

    serialized = dumps_json_safe(payload)
    parsed = json.loads(serialized)
    item = parsed["family_schedule_items"][0]

    assert item["start_date"] == "2026-05-31"
    assert item["start_time"] == "08:30:00"
    assert item["updated_at"] == "2026-05-31T10:15:45"
