from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import sqlite3


def _pct_change(old: float, new: float) -> float:
    if old == 0:
        return float("inf") if new != 0 else 0.0
    return ((new - old) / abs(old)) * 100


def _in_cooldown(last_alert_at: str | None, hours: float) -> bool:
    if not last_alert_at:
        return False
    last = datetime.fromisoformat(last_alert_at)
    return datetime.now(timezone.utc) - last < timedelta(hours=hours)


def check_alert(
    conn: sqlite3.Connection,
    settings: dict[str, Any],
    value: float,
) -> tuple[bool, str | None]:
    key = settings["key"]
    name = settings["name"]
    threshold_pct = settings.get("threshold_percent")
    threshold_low = settings.get("threshold_low")
    threshold_high = settings.get("threshold_high")
    cooldown = float(settings.get("cooldown_hours", 24))

    prev_row = conn.execute(
        "SELECT value FROM readings WHERE indicator = ? ORDER BY recorded_at DESC LIMIT 1",
        (key,),
    ).fetchone()
    alert_row = conn.execute(
        "SELECT last_alert_at FROM alert_log WHERE indicator = ?",
        (key,),
    ).fetchone()
    last_alert_at = alert_row["last_alert_at"] if alert_row else None

    if _in_cooldown(last_alert_at, cooldown):
        return False, None

    reasons: list[str] = []

    if threshold_low is not None and value < threshold_low:
        reasons.append(f"crossed below {threshold_low:g}")
    if threshold_high is not None and value > threshold_high:
        reasons.append(f"crossed above {threshold_high:g}")

    if prev_row is not None and threshold_pct is not None:
        prev = float(prev_row["value"])
        change = _pct_change(prev, value)
        if abs(change) >= threshold_pct:
            direction = "up" if change > 0 else "down"
            reasons.append(f"moved {abs(change):.1f}% {direction} (threshold ±{threshold_pct:g}%)")

    if not reasons:
        return False, None

    prev_text = f"{float(prev_row['value']):g}" if prev_row else "n/a"
    message = (
        f"📊 {name} alert\n"
        f"Now: {value:g}\n"
        f"Prior: {prev_text}\n"
        f"Trigger: {'; '.join(reasons)}"
    )
    return True, message