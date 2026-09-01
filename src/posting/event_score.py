"""Event scorecard for posts that already cleared the posting threshold.

Computes the metrics that explain *why* a move is notable, then formats:

    event_score = 87 / 100
    severity = HIGH

    Reasons:
    + 98th percentile 1h move
    + largest move in 41 days
    + confirmed by VIX
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlite3

from src.posting.history import MoveHistory
from src.posting.models import CATEGORY_GROUPS, AlertTrigger
from src.stats import daily_closes, percentile, percentile_rank, z_score

ROLLING_WINDOW = 90
MIN_SERIES = 10
MOVE_ELEVATED = 120.0
MAX_REASONS = 4
TWEET_LIMIT = 280

# Peer should move this way when the primary indicator goes UP.
CONFIRMATION_PEERS: dict[str, list[tuple[str, int]]] = {
    "btc": [("eth", 1), ("sol", 1), ("vix", -1), ("sp500", 1)],
    "eth": [("btc", 1), ("sol", 1), ("vix", -1)],
    "sol": [("btc", 1), ("eth", 1)],
    "sp500": [("nasdaq100", 1), ("qqq", 1), ("vix", -1), ("hy_spread", -1)],
    "nasdaq100": [("sp500", 1), ("qqq", 1), ("vix", -1)],
    "qqq": [("sp500", 1), ("nasdaq100", 1), ("vix", -1)],
    "vix": [("sp500", -1), ("nasdaq100", -1), ("hy_spread", 1), ("move", 1)],
    "move": [("treasury_10y", 1), ("vix", 1), ("hy_spread", 1)],
    "treasury_10y": [("treasury_2y", 1), ("move", 1), ("dxy", 1)],
    "treasury_2y": [("treasury_10y", 1), ("move", 1)],
    "dxy": [("gold", -1), ("treasury_10y", 1)],
    "gold": [("silver", 1), ("dxy", -1)],
    "silver": [("gold", 1)],
    "oil": [("gold", 1)],
    "hy_spread": [("vix", 1), ("sp500", -1), ("move", 1)],
    "mortgage_30y": [("treasury_10y", 1)],
    "btc_liquidations": [("eth_liquidations", 1), ("sol_liquidations", 1), ("vix", 1)],
    "eth_liquidations": [("btc_liquidations", 1), ("sol_liquidations", 1)],
    "sol_liquidations": [("btc_liquidations", 1), ("eth_liquidations", 1)],
    "btc_funding": [("eth_funding", 1), ("btc_basis", 1)],
    "eth_funding": [("btc_funding", 1), ("eth_basis", 1)],
    "sol_funding": [("btc_funding", 1)],
    "btc_basis": [("eth_basis", 1), ("btc_funding", 1)],
    "eth_basis": [("btc_basis", 1)],
    "fear_greed": [("btc", 1), ("vix", -1)],
    "cpi_yoy": [("treasury_10y", 1)],
    "unemployment": [("jobless_claims", 1)],
    "jobless_claims": [("unemployment", 1)],
    "yield_curve": [("treasury_10y", 1), ("treasury_2y", 1)],
}

PEER_LABELS: dict[str, str] = {
    "vix": "VIX",
    "move": "MOVE",
    "hy_spread": "HY spreads",
    "sp500": "S&P 500",
    "nasdaq100": "NASDAQ",
    "qqq": "QQQ",
    "btc": "BTC",
    "eth": "ETH",
    "sol": "SOL",
    "dxy": "DXY",
    "gold": "gold",
    "silver": "silver",
    "oil": "oil",
    "treasury_10y": "10Y yield",
    "treasury_2y": "2Y yield",
    "btc_liquidations": "BTC liquidations",
    "eth_liquidations": "ETH liquidations",
    "sol_liquidations": "SOL liquidations",
    "fear_greed": "Fear & Greed",
    "btc_funding": "BTC funding",
    "eth_funding": "ETH funding",
    "sol_funding": "SOL funding",
    "btc_basis": "BTC basis",
    "eth_basis": "ETH basis",
    "jobless_claims": "jobless claims",
    "unemployment": "unemployment",
    "cpi_yoy": "CPI",
}

RATES_VOL_FOR_MOVE = frozenset({
    "treasury_10y", "treasury_2y", "yield_curve", "fed_funds",
    "move", "hy_spread", "dxy", "mortgage_30y", "bond_etf_agg", "bond_etf_bnd",
})

LEVEL_INDICATORS = frozenset({
    "vix", "move", "fear_greed", "eth_onchain_volume", "eth_whale", "eth_trader",
})
CHANGE_RULES = frozenset({
    "percent_change", "absolute_change", "liquidation_spike", "percentile_move",
})


@dataclass
class EventMetrics:
    abs_change: float | None = None
    rolling_percentile: float | None = None
    z_score: float | None = None
    rarity_days: int | None = None
    rarity_label: str | None = None
    velocity: float | None = None
    persistence: int | None = None
    confirmations: list[str] = field(default_factory=list)
    confirmation_labels: list[str] = field(default_factory=list)
    data_confidence: float = 0.0
    sample_size: int = 0
    horizon: str = "daily"
    uses_level: bool = False


@dataclass
class EventScorecard:
    indicator: str
    score: int
    severity: str
    reasons: list[str]
    metrics: EventMetrics

    def format_block(self, *, max_reasons: int | None = None) -> str:
        reasons = self.reasons if max_reasons is None else self.reasons[:max_reasons]
        lines = [
            f"event_score = {self.score} / 100",
            f"severity = {self.severity}",
        ]
        if reasons:
            lines.append("")
            lines.append("Reasons:")
            lines.extend(f"+ {reason}" for reason in reasons)
        return "\n".join(lines)

    def metrics_summary(self) -> str:
        m = self.metrics
        parts = [
            f"abs={_fmt_num(m.abs_change)}",
            f"pctile={_fmt_num(m.rolling_percentile)}",
            f"z={_fmt_num(m.z_score)}",
            f"rarity={m.rarity_days}d" if m.rarity_days is not None else "rarity=—",
            f"vel={_fmt_num(m.velocity)}x" if m.velocity is not None else "vel=—",
            f"persist={m.persistence or 0}",
            f"conf={','.join(m.confirmations) or '—'}",
            f"data={m.data_confidence:.0f}",
            f"n={m.sample_size}",
        ]
        return " ".join(parts)


def log_event_scorecard(card: EventScorecard) -> None:
    print(f"[event-score] {card.indicator} {card.score}/100 {card.severity}")
    print(card.format_block())
    print(f"[event-score] metrics: {card.metrics_summary()}")


def inject_scorecard(text: str, card: EventScorecard) -> str:
    """Replace the context section with the scorecard; keep headline/data/takeaway."""
    if not card.reasons:
        return text

    parts = [p for p in text.split("\n\n") if p.strip()]
    if not parts:
        return card.format_block()[:TWEET_LIMIT]

    headline = parts[0]
    takeaway = parts[-1] if parts[-1].startswith("→") else None
    middle = parts[1:-1] if takeaway else parts[1:]
    data = middle[0] if middle else None

    n_reasons = len(card.reasons)
    for with_takeaway in (True, False):
        for n in range(n_reasons, 0, -1):
            built = _join_tweet(headline, data, card.format_block(max_reasons=n), takeaway if with_takeaway else None)
            if len(built) <= TWEET_LIMIT:
                return built
    built = _join_tweet(headline, data, card.format_block(max_reasons=1), None)
    return built[:TWEET_LIMIT]


def build_event_scorecard(
    conn: sqlite3.Connection,
    alert: AlertTrigger,
    history: MoveHistory | None = None,
    *,
    peer_alerts: list[AlertTrigger] | None = None,
    cfg: dict[str, Any] | None = None,
) -> EventScorecard:
    history = history or MoveHistory()
    metrics = _compute_metrics(conn, alert, history, peer_alerts=peer_alerts or [], cfg=cfg)
    reasons = _build_reasons(alert, history, metrics)
    score = _score_metrics(alert, metrics)
    severity = _severity_for(score)
    return EventScorecard(
        indicator=alert.indicator,
        score=score,
        severity=severity,
        reasons=reasons,
        metrics=metrics,
    )


def _join_tweet(
    headline: str,
    data: str | None,
    block: str,
    takeaway: str | None,
) -> str:
    parts = [headline]
    if data:
        parts.append(data)
    parts.append(block)
    if takeaway:
        parts.append(takeaway)
    return "\n\n".join(parts)


def _fmt_num(value: float | None) -> str:
    if value is None:
        return "—"
    if abs(value) >= 100:
        return f"{value:.0f}"
    return f"{value:.1f}"


def _uses_level(alert: AlertTrigger) -> bool:
    if alert.indicator.endswith("_liquidations") or alert.indicator in LEVEL_INDICATORS:
        return True
    if any(rule in CHANGE_RULES for rule in alert.rule_types):
        return False
    return True


def _horizon(alert: AlertTrigger) -> str:
    if alert.indicator.endswith("_liquidations"):
        return "1h"
    if alert.indicator.endswith(("_funding", "_basis", "_exchange_spread")):
        return "session"
    if alert.is_macro:
        return "print"
    return "daily"


def _signed_move(alert: AlertTrigger, history: MoveHistory) -> float | None:
    if _uses_level(alert):
        return alert.value
    if alert.alert_unit == "absolute":
        if history.abs_change != 0:
            return history.abs_change
        if alert.prev_value is None:
            return None
        return alert.value - alert.prev_value
    if history.pct_change != 0:
        return history.pct_change
    if alert.prev_value is None or alert.prev_value == 0:
        return None
    return (alert.value - alert.prev_value) / abs(alert.prev_value) * 100


def _abs_change(alert: AlertTrigger, history: MoveHistory) -> float | None:
    if alert.prev_value is not None:
        return alert.value - alert.prev_value
    if history.abs_change != 0:
        return history.abs_change
    return None


def _recent_values(conn: sqlite3.Connection, indicator: str, limit: int = ROLLING_WINDOW) -> list[float]:
    rows = conn.execute(
        """SELECT value FROM readings
           WHERE indicator = ? ORDER BY recorded_at DESC LIMIT ?""",
        (indicator, limit),
    ).fetchall()
    values = [float(row["value"]) for row in rows]
    values.reverse()
    return values


def _changes(values: list[float], *, pct: bool) -> list[float]:
    out: list[float] = []
    for i in range(1, len(values)):
        prev, cur = values[i - 1], values[i]
        if pct:
            if prev == 0:
                continue
            out.append((cur - prev) / abs(prev) * 100)
        else:
            out.append(cur - prev)
    return out


def _series_for(conn: sqlite3.Connection, alert: AlertTrigger) -> list[float]:
    """Historical observations in the same units as the metric being scored."""
    if _uses_level(alert):
        if alert.indicator.endswith("_liquidations") or alert.indicator.endswith(
            ("_funding", "_basis", "_exchange_spread")
        ):
            return _recent_values(conn, alert.indicator)
        daily = daily_closes(conn, alert.indicator)
        return [value for _, value in daily[-ROLLING_WINDOW:]]

    daily = daily_closes(conn, alert.indicator)
    if len(daily) >= 3:
        values = [value for _, value in daily[-ROLLING_WINDOW - 1:]]
        return _changes(values, pct=alert.alert_unit != "absolute")

    raw = _recent_values(conn, alert.indicator, ROLLING_WINDOW + 1)
    return _changes(raw, pct=alert.alert_unit != "absolute")


def _direction(alert: AlertTrigger, history: MoveHistory) -> int:
    move = _signed_move(alert, history)
    if move is None or _uses_level(alert):
        if alert.prev_value is None:
            return 0
        if alert.value > alert.prev_value:
            return 1
        if alert.value < alert.prev_value:
            return -1
        return 0
    if move > 0:
        return 1
    if move < 0:
        return -1
    return 0


def _persistence(series: list[float], current: float | None, *, uses_level: bool) -> int:
    if current is None or len(series) < 3:
        return 0
    if uses_level:
        baseline = percentile(series[:-1], 50) if len(series) > 1 else None
        if baseline is None:
            return 0
        elevated = current >= baseline if current >= 0 else current <= baseline
        if not elevated:
            return 0
        count = 0
        for value in reversed(series):
            if (value >= baseline) == (current >= baseline) and abs(value) >= abs(baseline):
                count += 1
            else:
                break
        return count

    direction = 1 if current > 0 else -1 if current < 0 else 0
    if direction == 0:
        return 0
    count = 0
    for value in reversed(series):
        d = 1 if value > 0 else -1 if value < 0 else 0
        if d == direction:
            count += 1
        else:
            break
    return count


def _velocity(series: list[float], current: float | None) -> float | None:
    if current is None or len(series) < MIN_SERIES:
        return None
    prior = [abs(v) for v in series[:-1]] or [abs(v) for v in series]
    typical = percentile(prior, 50)
    if typical < 1e-15:
        return None
    return abs(current) / typical


def _data_confidence(
    alert: AlertTrigger,
    sample_size: int,
    cfg: dict[str, Any] | None,
) -> float:
    if sample_size >= 60:
        base = 90.0
    elif sample_size >= 30:
        base = 75.0
    elif sample_size >= MIN_SERIES:
        base = 55.0
    elif sample_size >= 5:
        base = 40.0
    else:
        base = 25.0

    if alert.prev_value is None:
        base -= 15.0

    age_hours = (datetime.now(timezone.utc) - alert.timestamp).total_seconds() / 3600
    if age_hours > 24:
        base -= 10.0
    elif age_hours > 12:
        base -= 5.0

    if cfg:
        from src.config import indicator_settings

        try:
            settings = indicator_settings(cfg, alert.indicator)
        except Exception:
            settings = {}
        if (settings.get("quality") or {}).get("verify"):
            base += 5.0

    return max(0.0, min(100.0, base))


def _peer_list(indicator: str) -> list[tuple[str, int]]:
    if indicator in CONFIRMATION_PEERS:
        return CONFIRMATION_PEERS[indicator]
    for members in CATEGORY_GROUPS.values():
        if indicator in members:
            return [(peer, 1) for peer in members if peer != indicator]
    return []


def _latest_two(
    conn: sqlite3.Connection,
    indicator: str,
    *,
    as_of: datetime,
    max_age_hours: float,
) -> tuple[float, float] | None:
    rows = conn.execute(
        """SELECT value, observed_at FROM readings
           WHERE indicator = ? ORDER BY recorded_at DESC LIMIT 2""",
        (indicator,),
    ).fetchall()
    if len(rows) < 2:
        return None
    observed = str(rows[0]["observed_at"])
    try:
        if "T" in observed:
            ts = datetime.fromisoformat(observed.replace("Z", "+00:00"))
        else:
            ts = datetime.strptime(observed[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        as_of_aware = as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
        if as_of_aware - ts > timedelta(hours=max_age_hours):
            return None
    except ValueError:
        return None
    return float(rows[0]["value"]), float(rows[1]["value"])


def _peer_settings(cfg: dict[str, Any] | None, indicator: str) -> dict[str, Any]:
    if not cfg:
        return {}
    from src.config import indicator_settings

    try:
        return indicator_settings(cfg, indicator)
    except Exception:
        return {}


def _peer_move_confirms(
    conn: sqlite3.Connection,
    peer: str,
    required_dir: int,
    *,
    alert: AlertTrigger,
    cfg: dict[str, Any] | None,
) -> bool:
    settings = _peer_settings(cfg, peer)
    if not settings:
        return False
    pair = _latest_two(conn, peer, as_of=alert.timestamp, max_age_hours=48)
    if pair is None:
        return False
    latest, prev = pair
    unit = settings.get("alert_unit", "percent")
    if unit == "absolute":
        move = latest - prev
        threshold = float(settings.get("normal_alert") or 0)
    else:
        if prev == 0:
            return False
        move = (latest - prev) / abs(prev) * 100
        threshold = float(settings.get("normal_alert") or 0)
    if required_dir > 0 and move <= 0:
        return False
    if required_dir < 0 and move >= 0:
        return False
    floor = max(threshold * 0.5, 1e-9)
    return abs(move) >= floor


def _move_index_elevated(conn: sqlite3.Connection, alert: AlertTrigger) -> bool:
    pair = _latest_two(conn, "move", as_of=alert.timestamp, max_age_hours=72)
    if pair is None:
        return False
    return pair[0] >= MOVE_ELEVATED


def _collect_confirmations(
    conn: sqlite3.Connection,
    alert: AlertTrigger,
    history: MoveHistory,
    peer_alerts: list[AlertTrigger],
    cfg: dict[str, Any] | None,
) -> tuple[list[str], list[str]]:
    primary_dir = _direction(alert, history)
    names: list[str] = []
    labels: list[str] = []
    allowed = {peer for peer, _sign in _peer_list(alert.indicator)}

    def add(peer: str, label: str) -> None:
        if peer == alert.indicator or peer in names:
            return
        names.append(peer)
        labels.append(label)

    for peer in peer_alerts:
        key = peer.indicator
        if key == alert.indicator:
            continue
        if key == "move" and (
            peer.value >= MOVE_ELEVATED or alert.indicator in RATES_VOL_FOR_MOVE
        ):
            add("move", "Treasury volatility elevated")
        elif key == "vix" and (key in allowed or alert.indicator in RATES_VOL_FOR_MOVE):
            add("vix", "confirmed by VIX")
        elif key in allowed:
            if key == "move":
                add("move", "Treasury volatility elevated")
            elif key == "vix":
                add("vix", "confirmed by VIX")
            else:
                add(key, f"confirmed by {PEER_LABELS.get(key, key)}")

    if primary_dir != 0:
        for peer, sign in _peer_list(alert.indicator):
            if peer in names:
                continue
            required = primary_dir * sign
            if not _peer_move_confirms(conn, peer, required, alert=alert, cfg=cfg):
                continue
            if peer == "vix":
                add("vix", "confirmed by VIX")
            elif peer == "move":
                add("move", "Treasury volatility elevated")
            else:
                add(peer, f"confirmed by {PEER_LABELS.get(peer, peer)}")
            if len(names) >= 3:
                break

    if (
        "move" not in names
        and alert.indicator in RATES_VOL_FOR_MOVE
        and _move_index_elevated(conn, alert)
    ):
        add("move", "Treasury volatility elevated")

    return names, labels


def _compute_metrics(
    conn: sqlite3.Connection,
    alert: AlertTrigger,
    history: MoveHistory,
    *,
    peer_alerts: list[AlertTrigger],
    cfg: dict[str, Any] | None,
) -> EventMetrics:
    uses_level = _uses_level(alert)
    series = _series_for(conn, alert)
    current = _signed_move(alert, history)
    if current is not None and series:
        last = series[-1]
        if abs(last - current) > max(1e-9, abs(current) * 0.01):
            series = (series + [current])[-ROLLING_WINDOW:]
    elif current is not None and not series:
        series = [current]

    abs_rank_series = [abs(v) for v in series] if not uses_level else series
    abs_current = abs(current) if current is not None and not uses_level else current

    rolling = None
    z = None
    if alert.indicator == "eth_onchain_volume":
        z = alert.value
    if abs_current is not None and len(abs_rank_series) >= MIN_SERIES:
        rolling = percentile_rank(abs_current, abs_rank_series)
    if z is None and current is not None and len(series) >= MIN_SERIES:
        z = z_score(current, series)

    rarity_days = history.days_since_larger_move
    rarity_label = None
    if history.is_all_time_high:
        rarity_label = "new all-time high"
    elif history.level_extreme:
        rarity_label = history.level_extreme.rstrip(".")
    elif history.liquidation_rank:
        rarity_label = history.liquidation_rank.rstrip(".")
    elif rarity_days and rarity_days >= 7:
        rarity_label = f"largest move in {rarity_days} days"

    confirmations, confirmation_labels = _collect_confirmations(
        conn, alert, history, peer_alerts, cfg,
    )

    return EventMetrics(
        abs_change=_abs_change(alert, history),
        rolling_percentile=rolling,
        z_score=z,
        rarity_days=rarity_days,
        rarity_label=rarity_label,
        velocity=_velocity(series, current),
        persistence=_persistence(series, current, uses_level=uses_level),
        confirmations=confirmations,
        confirmation_labels=confirmation_labels,
        data_confidence=_data_confidence(alert, len(series), cfg),
        sample_size=len(series),
        horizon=_horizon(alert),
        uses_level=uses_level,
    )


def _percentile_reason(metrics: EventMetrics) -> str | None:
    if metrics.rolling_percentile is None or metrics.rolling_percentile < 90:
        return None
    pct = min(99, int(round(metrics.rolling_percentile)))
    if metrics.horizon == "1h":
        return f"{pct}th percentile 1h move"
    if metrics.uses_level:
        return f"{pct}th percentile reading"
    if metrics.horizon == "daily":
        return f"{pct}th percentile daily move"
    return f"{pct}th percentile move"


def _persistence_reason(alert: AlertTrigger, metrics: EventMetrics) -> str | None:
    n = metrics.persistence or 0
    if n < 3:
        return None
    if metrics.uses_level:
        unit = "hours" if metrics.horizon == "1h" else "sessions"
        return f"elevated for {n} {unit}"
    up = alert.prev_value is None or alert.value >= alert.prev_value
    direction = "up" if up else "down"
    ordinal = {3: "3rd", 4: "4th", 5: "5th"}.get(n, f"{n}th")
    return f"{ordinal} consecutive {direction} session"


def _build_reasons(
    alert: AlertTrigger,
    history: MoveHistory,
    metrics: EventMetrics,
) -> list[str]:
    ranked: list[tuple[int, str]] = []

    pct_reason = _percentile_reason(metrics)
    if pct_reason:
        priority = 10 if metrics.rolling_percentile and metrics.rolling_percentile >= 95 else 6
        ranked.append((priority, pct_reason))

    if metrics.rarity_label:
        ranked.append((9, metrics.rarity_label))
    elif metrics.rarity_days and metrics.rarity_days >= 14:
        ranked.append((9, f"largest move in {metrics.rarity_days} days"))

    if (
        metrics.z_score is not None
        and abs(metrics.z_score) >= 2
        and (metrics.rolling_percentile is None or metrics.rolling_percentile < 90)
    ):
        ranked.append((7, f"{abs(metrics.z_score):.1f}σ move"))

    for label in metrics.confirmation_labels:
        priority = 8 if label in ("confirmed by VIX", "Treasury volatility elevated") else 5
        ranked.append((priority, label))

    if metrics.velocity is not None and metrics.velocity >= 2 and not pct_reason:
        ranked.append((4, f"{metrics.velocity:.1f}x typical {metrics.horizon} velocity"))

    persist = _persistence_reason(alert, metrics)
    if persist:
        ranked.append((3, persist))

    ranked.sort(key=lambda item: item[0], reverse=True)
    seen: set[str] = set()
    reasons: list[str] = []
    for _priority, reason in ranked:
        if reason in seen:
            continue
        seen.add(reason)
        reasons.append(reason)
        if len(reasons) >= MAX_REASONS:
            break
    return reasons


def _score_metrics(alert: AlertTrigger, metrics: EventMetrics) -> int:
    parts: list[tuple[float, float]] = []
    if metrics.rolling_percentile is not None:
        parts.append((metrics.rolling_percentile, 0.25))
    if metrics.z_score is not None:
        parts.append((min(abs(metrics.z_score) / 3.0, 1.0) * 100, 0.15))
    if metrics.rarity_days is not None:
        parts.append((min(metrics.rarity_days / 90.0, 1.0) * 100, 0.20))
    elif metrics.rarity_label:
        parts.append((90.0, 0.20))
    if metrics.velocity is not None:
        parts.append((min(max(metrics.velocity - 1.0, 0.0) / 4.0, 1.0) * 100, 0.15))
    if metrics.persistence:
        parts.append((min(metrics.persistence / 5.0, 1.0) * 100, 0.10))
    if metrics.confirmations:
        parts.append((min(len(metrics.confirmations) / 3.0, 1.0) * 100, 0.15))

    if not parts:
        raw = 35.0
    else:
        total_w = sum(weight for _, weight in parts)
        raw = sum(value * weight for value, weight in parts) / total_w

    raw *= 0.75 + 0.25 * (metrics.data_confidence / 100.0)

    if alert.alert_tier == "emergency":
        raw = max(raw, 85.0)
    elif alert.alert_tier == "major":
        raw = max(raw, 70.0)

    return int(round(max(0.0, min(100.0, raw))))


def _severity_for(score: int) -> str:
    if score >= 90:
        return "EXTREME"
    if score >= 75:
        return "HIGH"
    if score >= 55:
        return "MEDIUM"
    return "LOW"
