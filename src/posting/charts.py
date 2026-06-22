from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import sqlite3

from src.config import ROOT, indicator_settings
from src.db import daily_readings_since
from src.fetch import fetch_chart_history
from src.posting.compose import THEME_HEADLINES
from src.posting.models import AlertTrigger

CHART_DIR = ROOT / "data" / "charts"
MAX_BYTES = 2 * 1024 * 1024

GREEN = "#22c55e"
RED = "#ef4444"
BG = "#0f172a"
PANEL = "#1e293b"
TEXT = "#f8fafc"
MUTED = "#94a3b8"
THRESHOLD_COLOR = "#f59e0b"

SHORT_NAMES: dict[str, str] = {
    "btc": "BTC",
    "eth": "ETH",
    "sol": "SOL",
    "sp500": "SPY",
    "nasdaq100": "QQQ",
    "vix": "VIX",
    "dxy": "DXY",
    "treasury_10y": "10Y Yield",
    "yield_curve": "10Y-2Y",
    "cpi_yoy": "CPI",
    "oil": "Oil",
    "gold": "Gold",
    "fed_funds": "Fed Funds",
    "mortgage_30y": "30Y Mortgage",
    "hy_spread": "HY Spread",
    "fear_greed": "Fear & Greed",
    "consumer_sentiment": "Sentiment",
    "unemployment": "Unemployment",
    "pmi_manufacturing": "Philly Fed",
    "ism_services": "Chi Nonmfg",
    "btc_funding": "BTC Funding",
    "eth_funding": "ETH Funding",
    "sol_funding": "SOL Funding",
    "btc_basis": "BTC Basis",
    "eth_basis": "ETH Basis",
    "sol_basis": "SOL Basis",
    "btc_exchange_spread": "BTC Spread",
    "eth_exchange_spread": "ETH Spread",
    "sol_exchange_spread": "SOL Spread",
    "btc_liquidations": "BTC Liqs",
    "eth_liquidations": "ETH Liqs",
    "sol_liquidations": "SOL Liqs",
}

THEME_SUBTITLES: dict[str, str] = {
    "risk_on": "Risk appetite increasing",
    "risk_off": "Risk-off signal strengthening",
    "crypto": "Crypto breadth improving",
    "inflation_pressure": "Inflation pressure building",
    "disinflation": "Disinflation signal",
    "easing_conditions": "Easing conditions building",
    "tightening_conditions": "Tightening pressure rising",
    "housing": "Housing stress emerging",
    "equities": "Equity move in focus",
}


def _short_name(alert: AlertTrigger) -> str:
    return SHORT_NAMES.get(alert.indicator, alert.name.split()[0][:12])


def _move_value(alert: AlertTrigger) -> float:
    if alert.prev_value is None or alert.prev_value == 0:
        return 0.0
    if alert.alert_unit == "absolute":
        return alert.value - alert.prev_value
    return (alert.value - alert.prev_value) / abs(alert.prev_value) * 100


def _format_move(alert: AlertTrigger) -> str:
    if alert.prev_value is None:
        return "—"
    if alert.alert_unit == "absolute":
        ch = alert.value - alert.prev_value
        sign = "+" if ch > 0 else "-"
        bps = abs(ch) * 100
        if bps >= 1:
            return f"{sign}{bps:.0f}bps"
        return f"{sign}{abs(ch):.2f}pp"
    pct = _move_value(alert)
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.1f}%"


def _threshold_levels(settings: dict[str, Any], alert: AlertTrigger) -> list[tuple[float, str]]:
    levels: list[tuple[float, str]] = []
    for rule in settings.get("rules") or []:
        rtype = rule.get("type")
        if rtype in ("crosses_above", "crosses_below", "above", "below"):
            val = float(rule["value"])
            label = f"Threshold {val:g}"
            levels.append((val, label))

    if not levels and alert.prev_value is not None:
        normal = settings.get("normal_alert")
        if normal is not None:
            normal = float(normal)
            if alert.alert_unit == "absolute":
                levels.append((alert.prev_value + normal, f"+{normal:g} trigger"))
                levels.append((alert.prev_value - normal, f"-{normal:g} trigger"))
            else:
                levels.append((alert.prev_value * (1 + normal / 100), f"+{normal:g}% trigger"))
                levels.append((alert.prev_value * (1 - normal / 100), f"-{normal:g}% trigger"))
    return levels


def _save_fig(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(path, dpi=100, bbox_inches="tight", facecolor=BG, edgecolor="none")
    plt.close()
    return path


def render_line_chart(
    conn: sqlite3.Connection,
    alert: AlertTrigger,
    cfg: dict[str, Any],
    *,
    months: int = 6,
) -> Path | None:
    settings = indicator_settings(cfg, alert.indicator)
    daily = daily_readings_since(conn, alert.indicator, months=months)
    if len(daily) < 10:
        try:
            fetched = fetch_chart_history(settings, months=months)
            if len(fetched) >= 2:
                daily = fetched
        except Exception:
            pass
    if len(daily) < 2:
        return None

    dates = [datetime.fromisoformat(d) for d, _ in daily]
    values = [v for _, v in daily]

    up = alert.prev_value is not None and alert.value >= (alert.prev_value or alert.value)
    line_color = GREEN if up else RED

    fig, ax = plt.subplots(figsize=(12, 6.75), facecolor=BG)
    ax.set_facecolor(PANEL)
    ax.plot(dates, values, color=line_color, linewidth=2.5, label=_short_name(alert))
    ax.scatter([dates[-1]], [values[-1]], color=line_color, s=80, zorder=5)

    for level, label in _threshold_levels(settings, alert):
        ax.axhline(level, color=THRESHOLD_COLOR, linestyle="--", linewidth=1.2, alpha=0.85)
        ax.text(dates[-1], level, f" {label}", color=THRESHOLD_COLOR, fontsize=9, va="bottom")

    vmin, vmax = min(values), max(values)
    six_m_hi = max(values)
    six_m_lo = min(values)
    current = values[-1]
    ctx = f"Now: {current:g}  |  6M high: {six_m_hi:g}  |  6M low: {six_m_lo:g}"
    if alert.prev_value is not None:
        ch = _format_move(alert)
        ctx += f"  |  Move: {ch}"

    ax.set_title(f"{alert.name} — 6 Month", color=TEXT, fontsize=16, fontweight="bold", pad=16)
    ax.text(0.5, 1.02, ctx, transform=ax.transAxes, ha="center", color=MUTED, fontsize=10)
    ax.tick_params(colors=MUTED)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
    for spine in ax.spines.values():
        spine.set_color("#334155")
    ax.grid(True, alpha=0.2, color=MUTED)

    fname = f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{alert.indicator}_line.png"
    return _save_fig(CHART_DIR / fname)


def render_multi_card(
    alerts: list[AlertTrigger],
    theme: str | None,
) -> Path:
    theme = theme or (alerts[0].themes[0] if alerts[0].themes else "markets")
    headline = THEME_HEADLINES.get(theme, "Market move").upper()
    subtitle = THEME_SUBTITLES.get(theme, "")

    ranked = sorted(alerts, key=lambda a: abs(_move_value(a)), reverse=True)
    strongest = _short_name(ranked[0])

    fig, ax = plt.subplots(figsize=(12, 6.75), facecolor=BG)
    ax.set_facecolor(BG)
    ax.axis("off")

    y = 0.88
    ax.text(0.5, y, headline, ha="center", va="top", color=TEXT, fontsize=28, fontweight="bold")
    y -= 0.08
    if subtitle:
        ax.text(0.5, y, subtitle, ha="center", va="top", color=MUTED, fontsize=14)
        y -= 0.1

    y -= 0.04
    for alert in ranked[:5]:
        move = _format_move(alert)
        up = move.startswith("+") or (move[0].isdigit() and _move_value(alert) > 0)
        color = GREEN if up else RED if move.startswith("-") else TEXT
        name = _short_name(alert)
        ax.text(0.22, y, name, ha="left", va="center", color=TEXT, fontsize=20, fontfamily="monospace")
        ax.text(0.78, y, move, ha="right", va="center", color=color, fontsize=20, fontweight="bold", fontfamily="monospace")
        y -= 0.11

    y -= 0.02
    ax.text(0.5, y, f"Strongest mover: {strongest}", ha="center", va="top", color=MUTED, fontsize=13)

    fname = f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_multi_{theme}.png"
    return _save_fig(CHART_DIR / fname)


def chart_for_decision(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    *,
    tweet_type: str,
    alerts: list[AlertTrigger],
    theme: str | None,
    is_emergency: bool,
) -> Path | None:
    if tweet_type == "multi":
        return render_multi_card(alerts, theme)
    if is_emergency and len(alerts) == 1:
        return render_line_chart(conn, alerts[0], cfg)
    return None