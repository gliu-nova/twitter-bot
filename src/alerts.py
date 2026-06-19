from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import sqlite3

from src.posting.models import AlertTrigger


def _pct_change(old: float, new: float) -> float:
    if old == 0:
        return float("inf") if new != 0 else 0.0
    return ((new - old) / abs(old)) * 100


def _in_cooldown(last_alert_at: str | None, hours: float) -> bool:
    if not last_alert_at:
        return False
    last = datetime.fromisoformat(last_alert_at)
    return datetime.now(timezone.utc) - last < timedelta(hours=hours)


def _eval_rule(rule: dict[str, Any], prev: float | None, value: float) -> tuple[str | None, str | None]:
    """Return (human reason, rule_type) or (None, None)."""
    rtype = rule["type"]

    if rtype == "percent_change":
        if prev is None:
            return None, None
        change = _pct_change(prev, value)
        threshold = float(rule["threshold"])
        if abs(change) >= threshold:
            direction = "up" if change > 0 else "down"
            return f"moved {abs(change):.1f}% {direction} (limit ±{threshold:g}%)", rtype
        return None, None

    if rtype == "above":
        if value > float(rule["value"]):
            return f"above {rule['value']:g}", rtype
        return None, None

    if rtype == "below":
        if value < float(rule["value"]):
            return f"below {rule['value']:g}", rtype
        return None, None

    if rtype == "crosses_above":
        bound = float(rule["value"])
        if prev is not None and prev < bound <= value:
            return f"crossed above {bound:g}", rtype
        return None, None

    if rtype == "crosses_below":
        bound = float(rule["value"])
        if prev is not None and prev > bound >= value:
            return f"crossed below {bound:g}", rtype
        return None, None

    if rtype == "percent_from_baseline":
        baseline = float(rule["baseline"])
        threshold = float(rule["threshold"])
        change = _pct_change(baseline, value)
        if abs(change) >= threshold:
            direction = "up" if change > 0 else "down"
            return (
                f"{abs(change):.1f}% {direction} from baseline {baseline:g} (limit ±{threshold:g}%)",
                rtype,
            )
        return None, None

    return None, None


def check_alert(
    conn: sqlite3.Connection,
    settings: dict[str, Any],
    value: float,
) -> tuple[bool, AlertTrigger | None]:
    key = settings["key"]
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
    reasons: list[str] = []
    rule_types: list[str] = []
    for rule in rules:
        msg, rtype = _eval_rule(rule, prev, value)
        if msg and rtype:
            reasons.append(msg)
            rule_types.append(rtype)

    if not reasons:
        return False, None

    magnitude_pct = abs(_pct_change(prev, value)) if prev is not None else 0.0
    quality = settings.get("quality") or {}
    is_macro = quality.get("schedule") == "macro"

    alert = AlertTrigger(
        indicator=key,
        name=settings["name"],
        value=value,
        prev_value=prev,
        reasons=reasons,
        rule_types=rule_types,
        themes=list(settings.get("themes") or []),
        category=str(settings.get("category") or "other"),
        is_macro=is_macro,
        timestamp=datetime.now(timezone.utc),
        magnitude_pct=magnitude_pct if magnitude_pct != float("inf") else 100.0,
        standalone_major=bool(settings.get("standalone_major")),
    )
    return True, alert