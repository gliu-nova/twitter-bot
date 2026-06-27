"""Shared statistical helpers for percentile-based alerts."""

from __future__ import annotations

import sqlite3


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * (pct / 100)
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (k - lo) * (ordered[hi] - ordered[lo])


def daily_closes(conn: sqlite3.Connection, indicator: str) -> list[tuple[str, float]]:
    rows = conn.execute(
        """SELECT value, observed_at FROM readings
           WHERE indicator = ? ORDER BY observed_at ASC""",
        (indicator,),
    ).fetchall()
    by_day: dict[str, float] = {}
    for row in rows:
        day = str(row["observed_at"])[:10]
        by_day[day] = float(row["value"])
    return sorted(by_day.items())


def daily_pct_changes(conn: sqlite3.Connection, indicator: str) -> list[float]:
    daily = daily_closes(conn, indicator)
    changes: list[float] = []
    for i in range(1, len(daily)):
        _prev_day, prev_val = daily[i - 1]
        _day, cur_val = daily[i]
        if prev_val == 0:
            continue
        changes.append((cur_val - prev_val) / abs(prev_val) * 100)
    return changes


def percentile_rank(value: float, history: list[float]) -> float | None:
    if not history:
        return None
    return 100.0 * sum(1 for v in history if v <= value) / len(history)


def trailing_average(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    chunk = values[-window:]
    return sum(chunk) / len(chunk)