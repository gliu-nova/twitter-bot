from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def is_us_equity_session(now: datetime | None = None) -> bool:
    """Regular US cash session Mon–Fri 9:30–16:00 ET."""
    now = now or datetime.now(ET)
    if now.weekday() >= 5:
        return False
    return time(9, 30) <= now.time() <= time(16, 0)


def market_hours_note(schedule: str, now: datetime | None = None) -> str | None:
    if schedule != "us_equity":
        return None
    if is_us_equity_session(now):
        return None
    return "outside US equity session (9:30–16:00 ET Mon–Fri)"