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


def _eval_rule(rule: dict[str, Any], prev: float | None, value: float) -> str | None:
    rtype = rule["type"]

    if rtype == "percent_change":
        if prev is None:
            return None
        change = _pct_change(prev, value)
        threshold = float(rule["threshold"])
        if abs(change) >= threshold:
            direction = "up" if change > 0 else "down"
            return f"moved {abs(change):.1f}% {direction} (limit ±{threshold:g}%)"
        return None

    if rtype == "above":
        if value > float(rule["value"]):
            return f"above {rule['value']:g}"
        return None

    if rtype == "below":
        if value < float(rule["value"]):
            return f"below {rule['value']:g}"
        return None

    if rtype == "crosses_above":
        bound = float(rule["value"])
        if prev is not None and prev < bound <= value:
            return f"crossed above {bound:g}"
        return None

    if rtype == "crosses_below":
        bound = float(rule["value"])
        if prev is not None and prev > bound >= value:
            return f"crossed below {bound:g}"
        return None

    if rtype == "percent_from_baseline":
        baseline = float(rule["baseline"])
        threshold = float(rule["threshold"])
        change = _pct_change(baseline, value)
        if abs(change) >= threshold:
            direction = "up" if change > 0 else "down"
            return f"{abs(change):.1f}% {direction} from baseline {baseline:g} (limit ±{threshold:g}%)"
        return None

    return None


def check_alert(
    conn: sqlite3.Connection,
    settings: dict[str, Any],
    value: float,
) -> tuple[bool, str | None]:
    key = settings["key"]
    name = settings["name"]
    rules = settings.get("rules") or []
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

    prev = float(prev_row["value"]) if prev_row else None
    reasons = [msg for rule in rules if (msg := _eval_rule(rule, prev, value))]

    if not reasons:
        return False, None

    prev_text = f"{prev:g}" if prev is not None else "n/a"
    message = (
        f"📊 {name} alert\n"
        f"Now: {value:g}\n"
        f"Prior: {prev_text}\n"
        f"Trigger: {'; '.join(reasons)}"
    )
    return True, message