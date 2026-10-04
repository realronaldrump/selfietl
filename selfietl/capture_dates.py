from __future__ import annotations

from datetime import datetime


def parse_capture_datetime(value: str | datetime) -> datetime:
    """Preserve the photo's source clock time and calendar day.

    The catalog stores capture dates as naive wall times, like EXIF
    DateTimeOriginal. An ISO offset describes the source clock; converting to
    the server timezone would change calendar grouping and video date labels.
    Validate the offset, then remove it without moving the clock time.
    """
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=None)
