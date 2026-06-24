from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
import sqlite3

from src.config import ROOT, indicator_settings
from src.db import daily_readings_since, liquidation_readings_since, readings_since
from src.fetch import fetch_chart_history
from src.scheduler import CRYPTO_KEYS
from src.posting.compose import THEME_HEADLINES, should_attach_chart
from src.posting.history import build_move_history
from src.posting.models import AlertTrigger

CHART_DIR = ROOT / "data" / "charts"
MAX_BYTES = 2 * 1024 * 1024
ET = ZoneInfo("America/New_York")

GREEN = "#22c55e"
RED = "#ef4444"
BG = "#0f172a"
PANEL = "#1e293b"
TEXT = "#f8fafc"
MUTED = "#94a3b8"
LONG_LIQ_COLOR = "#ef4444"
SHORT_LIQ_COLOR = "#22c55e"
HIGHLIGHT_EDGE = "#fbbf24"
HIGHLIGHT_FILL = "#fde68a"
HISTORY_ALPHA = 0.42

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


def _parse_chart_date(ts: str) -> datetime:
    if ts.isdigit():
        return datetime.fromtimestamp(int(ts), timezone.utc)
    if "T" in ts:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if len(ts) >= 16 and ts[10] == " ":
        return datetime.strptime(ts[:16], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    return datetime.strptime(ts[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _to_et(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ET)


def _use_intraday_history(alert: AlertTrigger, settings: dict[str, Any]) -> bool:
    if alert.indicator in CRYPTO_KEYS:
        return True
    return settings.get("source") in (
        "okx_funding",
        "hyperliquid_funding",
        "okx_basis",
        "hyperliquid_basis",
        "exchange_spread",
        "okx_liquidations",
    )


def _chart_history(
    conn: sqlite3.Connection,
    alert: AlertTrigger,
    settings: dict[str, Any],
    *,
    months: int,
) -> list[tuple[str, float]]:
    if _use_intraday_history(alert, settings):
        return readings_since(conn, alert.indicator, months=months)
    return daily_readings_since(conn, alert.indicator, months=months)


def _format_chart_value(alert: AlertTrigger, value: float) -> str:
    if alert.indicator.endswith("_liquidations"):
        if value >= 1_000_000:
            return f"${value / 1_000_000:.1f}M"
        if value >= 1_000:
            return f"${value / 1_000:.0f}K"
        return f"${value:,.0f}"
    if alert.indicator.endswith("_funding"):
        return f"{value * 100:.4f}%"
    if alert.indicator.endswith(("_basis", "_exchange_spread")):
        return f"{value:.1f} bps"
    if alert.indicator in ("cpi_yoy", "unemployment", "fed_funds", "treasury_10y", "mortgage_30y"):
        return f"{value:.2f}%"
    if value >= 1000:
        return f"${value:,.0f}"
    return f"{value:g}"


def _y_axis_label(alert: AlertTrigger) -> str:
    if alert.indicator.endswith("_liquidations"):
        return "1H Liquidations (USD)"
    if alert.indicator.endswith("_funding"):
        return "Funding Rate"
    if alert.indicator.endswith(("_basis", "_exchange_spread")):
        return "Basis (bps)"
    if alert.indicator in ("cpi_yoy", "unemployment", "fed_funds", "treasury_10y", "mortgage_30y"):
        return "Level (%)"
    return alert.name


def _y_tick_formatter(alert: AlertTrigger):
    def _fmt(value: float, _pos: int) -> str:
        if alert.indicator.endswith("_liquidations"):
            if abs(value) >= 1_000_000:
                return f"${value / 1_000_000:.1f}M"
            if abs(value) >= 1_000:
                return f"${value / 1_000:.0f}K"
            return f"${value:.0f}"
        if alert.indicator.endswith("_funding"):
            return f"{value * 100:.3f}%"
        if abs(value) >= 1000:
            return f"{value / 1000:.1f}k"
        return f"{value:g}"

    return FuncFormatter(_fmt)


def _chart_title(alert: AlertTrigger, dates: list[datetime]) -> str:
    if len(dates) < 2:
        return alert.name
    span = dates[-1] - dates[0]
    span_hours = max(span.total_seconds() / 3600, 1)
    if span_hours < 48:
        return f"{alert.name} — Last {int(span_hours)}h"
    span_days = max(span.days, 1)
    if span_days < 14:
        return f"{alert.name} — Last {span_days}d"
    if span_days < 120:
        return f"{alert.name} — Last {span_days // 30 or 1}mo"
    return f"{alert.name} — 6 Month"


def _liquidation_chart_title(alert: AlertTrigger, dates: list[datetime]) -> str:
    if len(dates) < 2:
        return alert.name
    max_gap_h = max(
        (dates[i + 1] - dates[i]).total_seconds() / 3600 for i in range(len(dates) - 1)
    )
    if max_gap_h > 36:
        return f"{alert.name} — Last {len(dates)} readings"
    span_hours = max((dates[-1] - dates[0]).total_seconds() / 3600, 1)
    if span_hours < 48:
        return f"{alert.name} — Last {int(span_hours)}h"
    span_days = max((dates[-1] - dates[0]).days, 1)
    if span_days < 14:
        return f"{alert.name} — Last {span_days}d"
    return f"{alert.name} — Last {len(dates)} readings"


def _configure_liquidation_x_axis(ax, x_pos: list[int], dates: list[datetime]) -> None:
    """One labeled ET timestamp per bar; x_pos is evenly spaced (no calendar gaps)."""
    if not dates:
        return
    ax.set_xticks(x_pos)
    ax.set_xticklabels(
        [d.strftime("%b %d %H:%M") for d in dates],
        rotation=40,
        ha="right",
        color=MUTED,
        fontsize=8,
    )
    pad = 0.6
    ax.set_xlim(x_pos[0] - pad, x_pos[-1] + pad)
    ax.tick_params(axis="x", colors=MUTED)


def _configure_x_axis(ax, dates: list[datetime]) -> None:
    if len(dates) < 2:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d", tz=ET))
        return
    span = dates[-1] - dates[0]
    if span < timedelta(hours=36):
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=max(1, int(span.total_seconds() / 3600 / 5)), tz=ET))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d %H:%M", tz=ET))
    elif span < timedelta(days=14):
        ax.xaxis.set_major_locator(mdates.DayLocator(interval=1, tz=ET))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d", tz=ET))
    elif span < timedelta(days=120):
        ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1, tz=ET))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d", tz=ET))
    else:
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1, tz=ET))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y", tz=ET))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=25, ha="right")


def _annotate_latest(ax, x: float, y: float, *, color: str = HIGHLIGHT_EDGE) -> None:
    ax.annotate(
        "LATEST",
        xy=(x, y),
        xytext=(0, 14),
        textcoords="offset points",
        ha="center",
        va="bottom",
        color=color,
        fontsize=10,
        fontweight="bold",
        bbox={
            "boxstyle": "round,pad=0.25",
            "facecolor": BG,
            "edgecolor": color,
            "linewidth": 1.5,
            "alpha": 0.95,
        },
        zorder=8,
    )


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
    series = _chart_history(conn, alert, settings, months=months)
    if len(series) < 10 and not _use_intraday_history(alert, settings):
        try:
            fetched = fetch_chart_history(settings, months=months)
            if len(fetched) >= 2:
                series = fetched
        except Exception:
            pass
    if len(series) < 2:
        return None

    dates = [_to_et(_parse_chart_date(d)) for d, _ in series]
    values = [v for _, v in series]

    up = alert.prev_value is not None and alert.value >= (alert.prev_value or alert.value)
    line_color = GREEN if up else RED
    series_label = _short_name(alert)

    fig, ax = plt.subplots(figsize=(12, 6.75), facecolor=BG)
    ax.set_facecolor(PANEL)

    if len(dates) >= 3:
        ax.plot(
            dates[:-1],
            values[:-1],
            color=line_color,
            linewidth=2.0,
            alpha=HISTORY_ALPHA,
            marker="o",
            markersize=3,
            zorder=2,
        )
        ax.plot(
            dates[-2:],
            values[-2:],
            color=line_color,
            linewidth=3.0,
            marker="o",
            markersize=5,
            label=series_label,
            zorder=3,
        )
    else:
        ax.plot(
            dates,
            values,
            color=line_color,
            linewidth=2.5,
            label=series_label,
            marker="o",
            markersize=4,
            zorder=3,
        )

    ax.scatter(
        [dates[-1]],
        [values[-1]],
        color=HIGHLIGHT_FILL,
        s=220,
        zorder=6,
        edgecolors=HIGHLIGHT_EDGE,
        linewidths=3.0,
    )
    _annotate_latest(ax, mdates.date2num(dates[-1]), values[-1])

    data_hi = max(values)
    data_lo = min(values)
    ylim_top = data_hi * 1.2
    ylim_bottom = 0.0
    if data_lo > 0 and data_hi / max(data_lo, 1e-9) > 4:
        ylim_bottom = max(0.0, data_lo * 0.85)
    ax.set_ylim(ylim_bottom, ylim_top)

    current = values[-1]
    ctx = (
        f"Now: {_format_chart_value(alert, current)}"
        f"  |  High: {_format_chart_value(alert, data_hi)}"
        f"  |  Low: {_format_chart_value(alert, data_lo)}"
    )
    if alert.prev_value is not None:
        ctx += f"  |  Move: {_format_move(alert)}"

    ax.set_title(_chart_title(alert, dates), color=TEXT, fontsize=16, fontweight="bold", pad=16)
    ax.text(0.5, 1.02, ctx, transform=ax.transAxes, ha="center", color=MUTED, fontsize=10)
    ax.set_xlabel("Date (ET)", color=MUTED, fontsize=11, labelpad=8)
    ax.set_ylabel(_y_axis_label(alert), color=MUTED, fontsize=11, labelpad=8)
    ax.yaxis.set_major_formatter(_y_tick_formatter(alert))
    ax.tick_params(colors=MUTED)
    _configure_x_axis(ax, dates)
    ax.legend(loc="upper left", framealpha=0.25, facecolor=PANEL, edgecolor="#334155", labelcolor=TEXT)
    for spine in ax.spines.values():
        spine.set_color("#334155")
    ax.grid(True, alpha=0.2, color=MUTED)

    fname = f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{alert.indicator}_line.png"
    return _save_fig(CHART_DIR / fname)


def render_liquidation_chart(
    conn: sqlite3.Connection,
    alert: AlertTrigger,
    cfg: dict[str, Any],
    *,
    days: int = 7,
) -> Path | None:
    rows = liquidation_readings_since(conn, alert.indicator, days=days)
    if len(rows) < 2:
        rows = [
            (ts, total, None, None)
            for ts, total in readings_since(conn, alert.indicator, months=1)
        ]
    if len(rows) < 2:
        return None

    dates = [_to_et(_parse_chart_date(ts)) for ts, _, _, _ in rows]
    long_vals = [long if long is not None else 0.0 for _, _, long, _ in rows]
    short_vals = [short if short is not None else 0.0 for _, _, _, short in rows]
    totals = [total for _, total, _, _ in rows]

    if not any(long_vals) and not any(short_vals):
        for i, total in enumerate(totals):
            long_vals[i] = total

    # Evenly spaced bar positions — avoids visual gaps when polls are missing for days
    x_pos = list(range(len(dates)))
    bar_width = 0.72

    fig, ax = plt.subplots(figsize=(12, 6.75), facecolor=BG)
    ax.set_facecolor(PANEL)

    hist_x = x_pos[:-1] if len(x_pos) > 1 else []
    if hist_x:
        ax.bar(
            hist_x,
            long_vals[:-1],
            width=bar_width,
            color=LONG_LIQ_COLOR,
            label="Long liquidations",
            alpha=HISTORY_ALPHA,
            edgecolor="none",
            zorder=2,
        )
        ax.bar(
            hist_x,
            short_vals[:-1],
            width=bar_width,
            bottom=long_vals[:-1],
            color=SHORT_LIQ_COLOR,
            label="Short liquidations",
            alpha=HISTORY_ALPHA,
            edgecolor="none",
            zorder=2,
        )

    last_idx = len(dates) - 1
    ax.bar(
        [x_pos[last_idx]],
        [long_vals[last_idx]],
        width=bar_width,
        color=LONG_LIQ_COLOR,
        edgecolor=HIGHLIGHT_EDGE,
        linewidth=3.5,
        zorder=5,
    )
    ax.bar(
        [x_pos[last_idx]],
        [short_vals[last_idx]],
        width=bar_width,
        bottom=[long_vals[last_idx]],
        color=SHORT_LIQ_COLOR,
        edgecolor=HIGHLIGHT_EDGE,
        linewidth=3.5,
        zorder=5,
    )
    ax.scatter(
        [x_pos[last_idx]],
        [totals[last_idx]],
        color=HIGHLIGHT_FILL,
        s=200,
        zorder=7,
        edgecolors=HIGHLIGHT_EDGE,
        linewidths=3.0,
    )
    _annotate_latest(ax, x_pos[last_idx], totals[last_idx])

    data_hi = max(totals)
    data_lo = min(totals)
    current = totals[-1]
    ctx = (
        f"Now: {_format_chart_value(alert, current)}"
        f"  |  High: {_format_chart_value(alert, data_hi)}"
        f"  |  Low: {_format_chart_value(alert, data_lo)}"
    )
    if alert.prev_value is not None:
        ctx += f"  |  Move: {_format_move(alert)}"

    ax.set_title(_liquidation_chart_title(alert, dates), color=TEXT, fontsize=16, fontweight="bold", pad=16)
    ax.text(0.5, 1.02, ctx, transform=ax.transAxes, ha="center", color=MUTED, fontsize=10)
    ax.set_xlabel("Time (ET)", color=MUTED, fontsize=11, labelpad=8)
    ax.set_ylabel("1H Liquidations (USD)", color=MUTED, fontsize=11, labelpad=8)
    ax.yaxis.set_major_formatter(_y_tick_formatter(alert))
    ax.tick_params(colors=MUTED)
    _configure_liquidation_x_axis(ax, x_pos, dates)
    ax.legend(loc="upper left", framealpha=0.25, facecolor=PANEL, edgecolor="#334155", labelcolor=TEXT)
    ax.set_ylim(0, data_hi * 1.2)
    for spine in ax.spines.values():
        spine.set_color("#334155")
    ax.grid(True, alpha=0.2, color=MUTED, axis="y")

    fname = f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{alert.indicator}_liq.png"
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
    posting_cfg: dict[str, Any] | None = None,
) -> Path | None:
    posting_cfg = posting_cfg or cfg.get("posting") or {}
    if tweet_type == "multi":
        return render_multi_card(alerts, theme)
    if len(alerts) != 1:
        return None

    alert = alerts[0]
    history = build_move_history(conn, alert)
    if not should_attach_chart(alert, history, posting_cfg, is_emergency=is_emergency):
        return None

    if alert.indicator.endswith("_liquidations"):
        return render_liquidation_chart(conn, alert, cfg)
    return render_line_chart(conn, alert, cfg)