from datetime import datetime, timezone
from uuid import UUID


def dt_from_iso(datetime_string: str) -> datetime:
    if datetime_string.endswith("Z"):
        # This is not necessary anymore in python >= 3.11
        return datetime.fromisoformat(datetime_string.rstrip("Z")).astimezone(timezone.utc)
    return datetime.fromisoformat(datetime_string)


def is_valid_uuid(string: str) -> bool:
    try:
        UUID(string)
    except ValueError:
        return False
    return True
