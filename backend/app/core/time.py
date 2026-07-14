from __future__ import annotations

from datetime import datetime, timezone


def ensure_utc(dt: datetime) -> datetime:
    """SQLite drops tzinfo on reload even for a DateTime(timezone=True) column,
    so a value round-tripped through SQLite compares unequal to one that's
    still tz-aware in memory even when they're the same instant. Postgres
    preserves tzinfo, so this is a no-op there.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
