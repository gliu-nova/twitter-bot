from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlite3

from src.alerts import _absolute_change, _detect_tier, _in_cooldown, _pct_change
from src.db import liquidation_readings_since
from src.posting.models import AlertTrigger

FLUSH_SKEW_THRESHOLD = 0.65


def _parse_observed(ts: str) -> datetime:
    if ts.isdigit():
        return datetime.fromtimestamp(int(ts), timezone.utc)
    if "T" in ts:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if len(ts) >= 16 and ts[10] == " ":
        return datetime.strptime(ts[:16], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    return datetime.strptime(ts[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * (pct / 100)
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (k - lo) * (ordered[hi] - ordered[lo])


def dynamic_liquidation_threshold(
    conn: sqlite3.Connection,
    indicator: str,
    settings: dict[str, Any],
) -> float:
    """max(fixed_floor, 30d p95, 3x 24h median) — excludes the current reading."""
    floor = float(settings.get("liquidation_floor_usd", 500_000))
    rows = liquidation_readings_since(conn, indicator, days=30)
    if len(rows) < 5:
        return floor

    totals = [total for _, total, _, _ in rows]
    p95 = _percentile(totals, 95)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    last_24h = [total for ts, total, _, _ in rows if _parse_observed(ts) >= cutoff]
    median_24h = statistics.median(last_24h) if last_24h else 0.0

    return max(floor, p95, 3 * median_24h)


def classify_liquidation_flush(
    long_usd: float | None,
    short_usd: float | None,
    total_usd: float,
) -> str:
    """Return 'long', 'short', or 'mixed'."""
    if long_usd is None or short_usd is None or total_usd <= 0:
        return "mixed"
    long_share = long_usd / total_usd
    if long_share >= FLUSH_SKEW_THRESHOLD:
        return "long"
    if short_usd / total_usd >= FLUSH_SKEW_THRESHOLD:
        return "short"
    return "mixed"


def liquidation_rank_phrase(
    conn: sqlite3.Connection,
    indicator: str,
    value: float,
) -> str | None:
    rows = liquidation_readings_since(conn, indicator, days=30)
    if len(rows) < 3:
        return None

    asset = indicator.split("_")[0].upper()
    now = datetime.now(timezone.utc)
    windows = (
        (7, "this week"),
        (14, "in 2 weeks"),
        (30, "this month"),
    )
    for days, label in windows:
        cutoff = now - timedelta(days=days)
        window_vals = [total for ts, total, _, _ in rows if _parse_observed(ts) >= cutoff]
        if len(window_vals) >= 3 and value >= max(window_vals) - 1e-6:
            return f"Largest {asset} liquidation {label}."
    return None


def check_liquidation_alert(
    conn: sqlite3.Connection,
    settings: dict[str, Any],
    total_usd: float,
    long_usd: float,
    short_usd: float,
) -> tuple[bool, AlertTrigger | None]:
    key = settings["key"]
    cooldown = float(settings.get("cooldown_hours", 24))

    prev_row = conn.execute(
        "SELECT value FROM readings WHERE indicator = ? ORDER BY recorded_at DESC LIMIT 1",
        (key,),
    ).fetchone()
    alert_row = conn.execute(
        "SELECT last_value, last_alert_at FROM alert_log WHERE indicator = ?",
        (key,),
    ).fetchone()
    last_alert_at = alert_row["last_alert_at"] if alert_row else None
    prev = float(prev_row["value"]) if prev_row else None

    threshold = dynamic_liquidation_threshold(conn, key, settings)
    if total_usd <= threshold:
        return False, None

    if _in_cooldown(last_alert_at, cooldown):
        tier = _detect_tier(settings, prev, total_usd)
        last_val = float(alert_row["last_value"]) if alert_row and alert_row["last_value"] is not None else None
        mult = float(settings.get("emergency_escalation_multiplier", 2.0))
        if not (tier == "emergency" and last_val is not None and total_usd >= last_val * mult):
            return False, None

    flush = classify_liquidation_flush(long_usd, short_usd, total_usd)
    reasons = [
        f"1H liquidations {_fmt_usd(total_usd)} > dynamic threshold {_fmt_usd(threshold)}",
        f"flush_type:{flush}",
    ]

    magnitude_pct = abs(_pct_change(prev, total_usd)) if prev is not None else 0.0
    if magnitude_pct == float("inf"):
        magnitude_pct = 100.0
    magnitude_abs = abs(_absolute_change(prev, total_usd)) if prev is not None else 0.0
    tier = _detect_tier(settings, prev, total_usd)

    quality = settings.get("quality") or {}
    alert = AlertTrigger(
        indicator=key,
        name=settings["name"],
        value=total_usd,
        prev_value=prev,
        reasons=reasons,
        rule_types=["liquidation_spike"],
        themes=list(settings.get("themes") or []),
        category=str(settings.get("category") or "other"),
        is_macro=quality.get("schedule") == "macro",
        timestamp=datetime.now(timezone.utc),
        magnitude_pct=magnitude_pct,
        magnitude_abs=magnitude_abs,
        alert_unit=str(settings.get("alert_unit", "percent")),
        alert_tier=tier,
        standalone_major=bool(settings.get("standalone_major")),
        liq_long_usd=long_usd,
        liq_short_usd=short_usd,
    )
    return True, alert


def _fmt_usd(value: float) -> str:
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"${value / 1_000:.0f}K"
    return f"${value:,.0f}"