from __future__ import annotations

from datetime import datetime, time
from typing import TYPE_CHECKING, Any, Literal

from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from src.posting.models import AlertTrigger

ET = ZoneInfo("America/New_York")

OffHoursAction = Literal["post", "queue", "drop"]


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


def vix_off_hours_immediate(alert: AlertTrigger) -> bool:
    """Major/emergency VIX moves may tweet outside the cash session."""
    if alert.indicator != "vix":
        return False
    if alert.alert_tier in ("emergency", "major"):
        return True
    return any(
        rtype == "crosses_above" and "crossed above 30" in reason
        for rtype, reason in zip(alert.rule_types, alert.reasons)
    )


def off_hours_equity_alert_action(
    settings: dict[str, Any],
    alert: AlertTrigger,
    skip_reason: str | None,
) -> OffHoursAction:
    """How to handle an alert blocked by us_equity market-hours gating."""
    if not skip_reason:
        return "post"
    schedule = (settings.get("quality") or {}).get("schedule", "macro")
    if schedule != "us_equity":
        return "post"
    if settings.get("key") == "vix":
        if vix_off_hours_immediate(alert):
            return "post"
        return "queue"
    return "drop"