"""Custom alert checkers for percentile / multi-metric indicators."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import sqlite3

from src.alerts import _absolute_change, _detect_tier, _in_cooldown, _pct_change
from src.etf_activity import EtfActivitySnapshot
from src.posting.models import AlertTrigger
from src.stats import daily_pct_changes, percentile, trailing_average


def _alert_row(conn: sqlite3.Connection, key: str) -> tuple[float | None, str | None]:
    prev_row = conn.execute(
        "SELECT value FROM readings WHERE indicator = ? ORDER BY recorded_at DESC LIMIT 1",
        (key,),
    ).fetchone()
    alert_row = conn.execute(
        "SELECT last_value, last_alert_at FROM alert_log WHERE indicator = ?",
        (key,),
    ).fetchone()
    prev = float(prev_row["value"]) if prev_row else None
    last_alert_at = alert_row["last_alert_at"] if alert_row else None
    return prev, last_alert_at


def _prev_aux_value(conn: sqlite3.Connection, key: str) -> float | None:
    row = conn.execute(
        """SELECT aux_value FROM readings
           WHERE indicator = ? AND aux_value IS NOT NULL
           ORDER BY recorded_at DESC LIMIT 1""",
        (key,),
    ).fetchone()
    return float(row["aux_value"]) if row and row["aux_value"] is not None else None


def _build_alert(
    settings: dict[str, Any],
    *,
    value: float,
    prev: float | None,
    reasons: list[str],
    rule_types: list[str],
    unit: str = "percent",
    aux_value: float | None = None,
    flow_usd: float | None = None,
    options_volume: float | None = None,
    options_pcr: float | None = None,
) -> AlertTrigger:
    magnitude_pct = abs(_pct_change(prev, value)) if prev is not None else 0.0
    if magnitude_pct == float("inf"):
        magnitude_pct = 100.0
    magnitude_abs = abs(_absolute_change(prev, value)) if prev is not None else 0.0
    tier = _detect_tier(settings, prev, value)
    quality = settings.get("quality") or {}
    return AlertTrigger(
        indicator=settings["key"],
        name=settings["name"],
        value=value,
        prev_value=prev,
        reasons=reasons,
        rule_types=rule_types,
        themes=list(settings.get("themes") or []),
        category=str(settings.get("category") or "other"),
        is_macro=quality.get("schedule") == "macro",
        timestamp=datetime.now(timezone.utc),
        magnitude_pct=magnitude_pct,
        magnitude_abs=magnitude_abs,
        alert_unit=unit,
        alert_tier=tier,
        standalone_major=bool(settings.get("standalone_major")),
        aux_value=aux_value,
        flow_usd=flow_usd,
        options_volume=options_volume,
        options_pcr=options_pcr,
    )


def check_qqq_alert(
    conn: sqlite3.Connection,
    settings: dict[str, Any],
    value: float,
) -> tuple[bool, AlertTrigger | None]:
    key = settings["key"]
    cooldown = float(settings.get("cooldown_hours", 24))
    prev, last_alert_at = _alert_row(conn, key)
    if prev is None:
        return False, None

    move = abs(_pct_change(prev, value))
    if move == float("inf"):
        move = 100.0

    fixed_floor = float(settings.get("normal_alert", 2.5))
    pct_threshold = float(settings.get("move_percentile", 97))
    min_history = int(settings.get("move_percentile_min_days", 60))

    reasons: list[str] = []
    rule_types: list[str] = []

    if move >= fixed_floor:
        direction = "up" if value > prev else "down"
        reasons.append(f"moved {move:.1f}% {direction} (limit ±{fixed_floor:g}%)")
        rule_types.append("percent_change")

    changes = [abs(c) for c in daily_pct_changes(conn, key)]
    if len(changes) >= min_history:
        p_move = percentile(changes, pct_threshold)
        if move >= p_move:
            reasons.append(
                f"{move:.1f}% move ≥ {p_move:.1f}% ({pct_threshold:.0f}th pct daily)"
            )
            rule_types.append("percentile_move")

    if not reasons:
        return False, None

    if _in_cooldown(last_alert_at, cooldown):
        tier = _detect_tier(settings, prev, value)
        alert_row = conn.execute(
            "SELECT last_value FROM alert_log WHERE indicator = ?",
            (key,),
        ).fetchone()
        last_val = float(alert_row["last_value"]) if alert_row and alert_row["last_value"] is not None else None
        mult = float(settings.get("emergency_escalation_multiplier", 2.0))
        if not (tier == "emergency" and last_val is not None and value >= last_val * mult):
            return False, None

    alert = _build_alert(settings, value=value, prev=prev, reasons=reasons, rule_types=rule_types)
    if any(r in ("crosses_above", "crosses_below") for r in rule_types):
        alert.alert_tier = "major" if alert.alert_tier == "normal" else alert.alert_tier
    return True, alert


def _dark_pool_history(symbol: str, *, days: int = 220) -> tuple[list[float], list[float]]:
    from src.finra_dark_pool import fetch_finra_dark_pool_both_history

    since = datetime.now(timezone.utc) - timedelta(days=days)
    volume_rows, pct_rows = fetch_finra_dark_pool_both_history(symbol, since=since)
    vol_vals = [v for _d, v in volume_rows]
    pct_vals = [v for _d, v in pct_rows]
    return vol_vals, pct_vals


def check_dark_pool_alert(
    conn: sqlite3.Connection,
    settings: dict[str, Any],
    volume: float,
    pct: float,
) -> tuple[bool, AlertTrigger | None]:
    key = settings["key"]
    cooldown = float(settings.get("cooldown_hours", 24))
    prev, last_alert_at = _alert_row(conn, key)
    prev_aux = _prev_aux_value(conn, key)

    vol_history, pct_history = _dark_pool_history(settings.get("symbol", "SPY"))
    if len(vol_history) < 10 or len(pct_history) < 10:
        return False, None

    reasons: list[str] = []
    rule_types: list[str] = []

    vol_tail = vol_history[:-1] if len(vol_history) > 1 else vol_history
    pct_tail = pct_history[:-1] if len(pct_history) > 1 else pct_history

    p95 = percentile(vol_tail, 95)
    p98 = percentile(vol_tail, 98)
    if volume >= p98:
        reasons.append(f"volume {volume:.1f}M ≥ {p98:.1f}M (98th pct)")
        rule_types.append("volume_percentile_98")
    elif volume >= p95:
        reasons.append(f"volume {volume:.1f}M ≥ {p95:.1f}M (95th pct)")
        rule_types.append("volume_percentile_95")

    avg_mults = {
        30: float(settings.get("volume_avg_30d_mult", 1.35)),
        60: float(settings.get("volume_avg_60d_mult", 1.50)),
        90: float(settings.get("volume_avg_90d_mult", 1.65)),
    }
    for window, label in ((30, "30d"), (60, "60d"), (90, "90d")):
        avg = trailing_average(vol_tail, window)
        if avg and avg > 0:
            ratio = volume / avg
            if ratio >= avg_mults[window]:
                reasons.append(f"volume {ratio:.2f}x {label} avg ({volume:.1f}M vs {avg:.1f}M)")
                rule_types.append(f"volume_vs_{label}_avg")
                break

    lookback_days = int(settings.get("pct_high_lookback_days", 180))
    pct_window = pct_tail[-lookback_days:] if len(pct_tail) >= lookback_days else pct_tail
    if pct_window and pct >= max(pct_window) - 1e-6:
        reasons.append(f"dark pool % {pct:.1f}% — highest in {len(pct_window)} sessions")
        rule_types.append("pct_session_high")

    p_pct = float(settings.get("pct_percentile", 97))
    p97 = percentile(pct_tail, p_pct)
    if pct >= p97:
        reasons.append(f"dark pool % {pct:.1f}% ≥ {p97:.1f}% ({p_pct:.0f}th pct)")
        rule_types.append("pct_percentile")

    if not reasons:
        return False, None

    if _in_cooldown(last_alert_at, cooldown):
        tier = _detect_tier(settings, prev, volume)
        alert_row = conn.execute(
            "SELECT last_value FROM alert_log WHERE indicator = ?",
            (key,),
        ).fetchone()
        last_val = float(alert_row["last_value"]) if alert_row and alert_row["last_value"] is not None else None
        mult = float(settings.get("emergency_escalation_multiplier", 2.0))
        if not (tier == "emergency" and last_val is not None and volume >= last_val * mult):
            return False, None

    alert = _build_alert(
        settings,
        value=volume,
        prev=prev,
        reasons=reasons,
        rule_types=rule_types,
        unit="absolute",
        aux_value=pct,
    )
    if prev_aux is not None and prev is not None:
        alert.magnitude_pct = abs(_pct_change(prev, volume))
        alert.magnitude_abs = abs(pct - prev_aux)
    return True, alert


def check_etf_activity_alert(
    conn: sqlite3.Connection,
    settings: dict[str, Any],
    snap: EtfActivitySnapshot,
) -> tuple[bool, AlertTrigger | None]:
    key = settings["key"]
    cooldown = float(settings.get("cooldown_hours", 24))
    prev, last_alert_at = _alert_row(conn, key)
    prev_assets = _prev_aux_value(conn, key)

    flow_usd: float | None = None
    if snap.net_assets_usd is not None and prev_assets is not None:
        flow_usd = snap.net_assets_usd - prev_assets

    reasons: list[str] = []
    rule_types: list[str] = []

    vol_ratio = snap.volume / snap.avg_volume_30d if snap.avg_volume_30d > 0 else 0.0
    vol_mult_floor = float(settings.get("normal_alert", 1.5))
    vol_pct_threshold = float(settings.get("volume_percentile", 95))

    if vol_ratio >= vol_mult_floor:
        reasons.append(
            f"volume {snap.volume / 1e6:.1f}M ({vol_ratio:.2f}x 30d avg)"
        )
        rule_types.append("unusual_volume")

    if snap.volume_percentile is not None and snap.volume_percentile >= vol_pct_threshold:
        reasons.append(
            f"volume ≥ {vol_pct_threshold:.0f}th pct ({snap.volume / 1e6:.1f}M shares)"
        )
        rule_types.append("volume_percentile")

    flow_floor = float(settings.get("flow_usd_floor", 75_000_000))
    if flow_usd is not None and abs(flow_usd) >= flow_floor:
        sign = "+" if flow_usd > 0 else "-"
        reasons.append(f"estimated flow {sign}${abs(flow_usd) / 1e6:.0f}M (AUM change)")
        rule_types.append("etf_flow")

    opt_vol_floor = float(settings.get("options_volume_floor", 150_000))
    if snap.options_volume is not None and snap.options_volume >= opt_vol_floor:
        reasons.append(f"options volume {snap.options_volume / 1e3:.0f}k contracts")
        rule_types.append("options_activity")

    pcr_high = float(settings.get("options_pcr_high", 2.5))
    pcr_low = float(settings.get("options_pcr_low", 0.5))
    if snap.options_pcr is not None and (snap.options_pcr >= pcr_high or snap.options_pcr <= pcr_low):
        reasons.append(f"put/call ratio {snap.options_pcr:.2f}")
        rule_types.append("options_skew")

    if not reasons:
        return False, None

    if _in_cooldown(last_alert_at, cooldown):
        tier = _detect_tier(settings, prev, snap.volume)
        alert_row = conn.execute(
            "SELECT last_value FROM alert_log WHERE indicator = ?",
            (key,),
        ).fetchone()
        last_val = float(alert_row["last_value"]) if alert_row and alert_row["last_value"] is not None else None
        mult = float(settings.get("emergency_escalation_multiplier", 2.0))
        if not (tier == "emergency" and last_val is not None and snap.volume >= last_val * mult):
            return False, None

    alert = _build_alert(
        settings,
        value=snap.volume,
        prev=prev,
        reasons=reasons,
        rule_types=rule_types,
        unit="absolute",
        aux_value=snap.net_assets_usd,
        flow_usd=flow_usd,
        options_volume=snap.options_volume,
        options_pcr=snap.options_pcr,
    )
    if vol_ratio >= float(settings.get("emergency_alert", 2.5)):
        alert.alert_tier = "emergency"
    elif vol_ratio >= float(settings.get("major_alert", 2.0)):
        alert.alert_tier = "major" if alert.alert_tier == "normal" else alert.alert_tier
    return True, alert