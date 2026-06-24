from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import sqlite3

from src.posting.models import AlertTrigger

# Only claim ATH for series that trend to new highs (not yields, spreads, macro)
ATH_INDICATORS = {"btc", "eth", "sol", "sp500", "nasdaq100", "gold", "silver"}

# Level-based high/low context (yields, spreads, vol — not ATH price series)
LEVEL_EXTREME_INDICATORS = {
    "treasury_10y",
    "fed_funds",
    "mortgage_30y",
    "hy_spread",
    "move",
    "vix",
    "dxy",
    "yield_curve",
    "cpi_yoy",
    "unemployment",
    "oil",
}


@dataclass
class MoveHistory:
    pct_change: float = 0.0
    abs_change: float = 0.0
    days_since_larger_move: int | None = None
    is_all_time_high: bool = False
    prior_record: float | None = None
    is_largest_ytd: bool = False
    ytd_move_count: int = 0
    level_extreme: str | None = None
    liquidation_rank: str | None = None


def _daily_closes(conn: sqlite3.Connection, indicator: str) -> list[tuple[str, float]]:
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


def _level_extreme_phrase(daily: list[tuple[str, float]], value: float) -> str | None:
    """Return e.g. '90-day high.' when value is at the window extreme."""
    if len(daily) < 5:
        return None
    try:
        last_day = datetime.fromisoformat(daily[-1][0])
    except ValueError:
        return None

    for window_days in (90, 60, 30, 14):
        cutoff = (last_day - timedelta(days=window_days)).date().isoformat()
        chunk = [(d, v) for d, v in daily if d >= cutoff]
        if len(chunk) < 5:
            continue
        vals = [v for _, v in chunk]
        hi, lo = max(vals), min(vals)
        if value >= hi - 1e-9:
            return f"{window_days}-day high."
        if value <= lo + 1e-9:
            return f"{window_days}-day low."
    return None


def _apply_level_extreme(
    daily: list[tuple[str, float]],
    alert: AlertTrigger,
    history: MoveHistory,
) -> MoveHistory:
    if alert.indicator not in LEVEL_EXTREME_INDICATORS:
        return history
    phrase = _level_extreme_phrase(daily, alert.value)
    if phrase:
        history.level_extreme = phrase
    return history


def build_move_history(conn: sqlite3.Connection, alert: AlertTrigger) -> MoveHistory:
    history = MoveHistory()
    if alert.indicator.endswith("_liquidations"):
        from src.liquidations import liquidation_rank_phrase

        history.liquidation_rank = liquidation_rank_phrase(conn, alert.indicator, alert.value)
    if alert.prev_value is None:
        return history

    prev = alert.prev_value
    value = alert.value
    history.pct_change = ((value - prev) / abs(prev) * 100) if prev != 0 else 0.0
    history.abs_change = value - prev

    daily = _daily_closes(conn, alert.indicator)
    if len(daily) < 2:
        return _apply_level_extreme(daily, alert, _apply_ath(conn, alert, history))

    changes: list[tuple[str, float]] = []
    for i in range(1, len(daily)):
        p_day, p_val = daily[i - 1]
        c_day, c_val = daily[i]
        if p_val == 0:
            continue
        pct = (c_val - p_val) / abs(p_val) * 100
        changes.append((c_day, pct))

    if not changes:
        return _apply_level_extreme(daily, alert, _apply_ath(conn, alert, history))

    # Prefer day-over-day change from stored daily closes
    last_pct = changes[-1][1]
    current_pct = last_pct
    history.pct_change = last_pct
    direction = 1 if current_pct > 0 else -1 if current_pct < 0 else 0
    abs_current = abs(current_pct)

    if direction != 0 and len(changes) >= 2:
        days_back = 0
        for _day, pct in reversed(changes[:-1]):
            days_back += 1
            if (pct > 0 and direction > 0) or (pct < 0 and direction < 0):
                if abs(pct) >= abs_current:
                    history.days_since_larger_move = days_back
                    break

    current_year = changes[-1][0][:4]
    ytd_moves = [abs(p) for d, p in changes if d.startswith(current_year)]
    if ytd_moves and abs_current >= max(ytd_moves):
        history.is_largest_ytd = True
        history.ytd_move_count = len(ytd_moves)

    return _apply_level_extreme(daily, alert, _apply_ath(conn, alert, history))


def _apply_ath(conn: sqlite3.Connection, alert: AlertTrigger, history: MoveHistory) -> MoveHistory:
    if alert.indicator not in ATH_INDICATORS:
        return history
    prior = conn.execute(
        """SELECT MAX(value) AS peak FROM readings
           WHERE indicator = ? AND value < ?""",
        (alert.indicator, alert.value * 0.9995),
    ).fetchone()
    if not prior or prior["peak"] is None:
        return history

    old_peak = float(prior["peak"])
    if alert.prev_value is not None and alert.value > old_peak and alert.prev_value <= old_peak * 1.001:
        history.is_all_time_high = True
        history.prior_record = old_peak
    return history


def rarity_phrase(history: MoveHistory, *, direction_up: bool, include_ath: bool = True) -> str | None:
    if include_ath and history.is_all_time_high:
        return "New all-time high."

    if history.days_since_larger_move and history.days_since_larger_move >= 14:
        move_word = "gain" if direction_up else "decline"
        return f"Largest daily {move_word} in {history.days_since_larger_move} days."

    if history.is_largest_ytd and history.ytd_move_count >= 5:
        return "One of the largest daily moves of the year."

    return None