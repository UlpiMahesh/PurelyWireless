from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Naive UTC datetime (SQLite has no tz support). Serialised with a 'Z' suffix."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
