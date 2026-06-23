"""Compose X/Twitter alert text.

Tweet style (max 280 chars):
  headline
  [blank line]
  data (values/%% on separate lines when useful)
  [blank line]
  context (rarity, one short line)
  [blank line]
  → takeaway

Professional and data-driven. Succinct — no text blobs.
Emojis: max 1 per post, only for exceptional moves; most posts have none.
"""

from __future__ import annotations

from typing import Any

from src.posting.history import MoveHistory
from src.posting.models import AlertTrigger

MACRO_INDICATORS = {
    "cpi_yoy", "fed_funds", "unemployment", "jobless_claims",
    "pmi_manufacturing", "ism_services", "consumer_sentiment", "m2",
}

KEY_LEVEL_CROSS_INDICATORS = frozenset({"vix", "yield_curve", "cpi_yoy", "fed_funds"})
LIQ_LONG_PREFIX = "long_liq_usd:"
LIQ_SHORT_PREFIX = "short_liq_usd:"
LIQ_SKEW_THRESHOLD = 0.75

THEME_HEADLINES: dict[str, str] = {
    "risk_on": "Risk-on shift",
    "risk_off": "Risk-off building",
    "crypto": "Crypto momentum",
    "inflation_pressure": "Inflation pressure",
    "disinflation": "Disinflation signal",
    "easing_conditions": "Easing conditions",
    "tightening_conditions": "Tightening pressure",
    "housing": "Housing stress",
    "equities": "Equity move",
}

# Short closing lines (→ prefixed in output)
TAKEAWAY_HINTS: dict[str, str] = {
    "btc": "Watch follow-through across risk assets.",
    "eth": "Alt breadth matters more than one name.",
    "sol": "High-beta move — risk appetite signal.",

    "btc_funding": "Crowded longs — squeeze risk elevated.",
    "eth_funding": "Funding extremes can front-run alt flows.",
    "sol_funding": "Positioning unwind risk on watch.",
    "sp500": "Rates and USD reaction next.",
    "vix": "Vol elevated — multiples under pressure.",
    "treasury_10y": "Rippling through equities and housing.",
    "yield_curve": "Growth vs recession repricing.",
    "cpi_yoy": "Fed expectations and real yields in focus.",
    "fear_greed": "Sentiment extreme — trend or contrarian?",
    "btc_exchange_spread": "Cross-venue gap — watch arb and liquidity stress.",
    "eth_exchange_spread": "Cross-venue gap — watch arb and liquidity stress.",
    "sol_exchange_spread": "Cross-venue gap — watch arb and liquidity stress.",
}

def _assemble_tweet(
    *,
    headline: str,
    data_lines: list[str] | None = None,
    context: str | None = None,
    takeaway: str | None = None,
) -> str:
    """Join sections with blank lines; trim to 280 chars."""
    parts: list[str] = [headline.strip()]
    if data_lines:
        parts.append("\n".join(line for line in data_lines if line))
    if context:
        parts.append(context.strip())
    if takeaway:
        parts.append(f"→ {takeaway.strip().rstrip('.')}.")
    return "\n\n".join(parts)[:280]


def _format_pct_line(pct: float, *, up: bool) -> str:
    sign = "+" if up else "-"
    mag = abs(pct)
    if mag >= 1000:
        return f"{sign}{mag:,.0f}%"
    if mag >= 100:
        return f"{sign}{mag:,.1f}%"
    return f"{sign}{mag:.1f}%"


def liq_breakdown_tokens(alert: AlertTrigger) -> list[str]:
    tokens: list[str] = []
    if alert.liq_long_usd is not None:
        tokens.append(f"{LIQ_LONG_PREFIX}{alert.liq_long_usd:.0f}")
    if alert.liq_short_usd is not None:
        tokens.append(f"{LIQ_SHORT_PREFIX}{alert.liq_short_usd:.0f}")
    return tokens


def hydrate_liq_from_reasons(alert: AlertTrigger) -> None:
    if alert.liq_long_usd is not None and alert.liq_short_usd is not None:
        return
    for reason in alert.reasons:
        if reason.startswith(LIQ_LONG_PREFIX):
            alert.liq_long_usd = float(reason.removeprefix(LIQ_LONG_PREFIX))
        elif reason.startswith(LIQ_SHORT_PREFIX):
            alert.liq_short_usd = float(reason.removeprefix(LIQ_SHORT_PREFIX))


def _ensure_liq_breakdown(alert: AlertTrigger) -> None:
    """Fill long/short split from reasons or live OKX fetch (queued alerts may lack tokens)."""
    hydrate_liq_from_reasons(alert)
    if alert.liq_long_usd is not None and alert.liq_short_usd is not None:
        return
    if not alert.indicator.endswith("_liquidations"):
        return
    try:
        from src.config import indicator_settings, load_config
        from src.crypto_metrics import fetch_liquidation_metric

        settings = indicator_settings(load_config(), alert.indicator)
        if settings.get("source") != "okx_liquidations":
            return
        _, long_usd, short_usd, _ = fetch_liquidation_metric(settings)
        alert.liq_long_usd = long_usd
        alert.liq_short_usd = short_usd
    except Exception:
        return


def _format_liq_usd(value: float, *, precision: int = 1) -> str:
    if value >= 1_000_000:
        m = value / 1_000_000
        if precision == 0:
            return f"${round(m)}M"
        return f"${m:.1f}M"
    return f"${value:,.0f}"


def _liq_skew(alert: AlertTrigger) -> str | None:
    """Return 'long', 'short', or None for balanced liquidations."""
    long_usd, short_usd = _liq_breakdown(alert)
    if long_usd is None or short_usd is None:
        return None
    total = long_usd + short_usd
    if total <= 0:
        return None
    long_share = long_usd / total
    if long_share >= LIQ_SKEW_THRESHOLD:
        return "long"
    if long_share <= 1 - LIQ_SKEW_THRESHOLD:
        return "short"
    return None


def _liq_breakdown(alert: AlertTrigger) -> tuple[float | None, float | None]:
    hydrate_liq_from_reasons(alert)
    return alert.liq_long_usd, alert.liq_short_usd


def _liquidation_headline(alert: AlertTrigger) -> str:
    asset = alert.indicator.split("_")[0].upper()
    skew = _liq_skew(alert)
    if skew == "long":
        return f"{asset} LONG FLUSH"
    if skew == "short":
        return f"{asset} SHORT FLUSH"
    return f"{asset} LIQUIDATIONS"


def _data_lines_for_liquidation(alert: AlertTrigger, history: MoveHistory) -> list[str]:
    lines = [f"{_format_liq_usd(alert.value)} liquidations (1H)"]
    long_usd, short_usd = _liq_breakdown(alert)
    pct = abs(history.pct_change or alert.magnitude_pct) if alert.prev_value is not None else 0.0
    pct_suffix = f" ({_format_pct_line(pct, up=_direction_up(alert))})" if pct > 0 else ""
    if long_usd is not None and short_usd is not None:
        lines.append(
            f"Longs {_format_liq_usd(long_usd, precision=0)} | "
            f"Shorts {_format_liq_usd(short_usd, precision=0)}{pct_suffix}"
        )
    elif pct > 0:
        lines.append(_format_pct_line(pct, up=_direction_up(alert)))
    return lines


def _liquidation_context(alert: AlertTrigger, history: MoveHistory) -> str | None:
    if history.days_since_larger_move and history.days_since_larger_move >= 14:
        return f"Biggest spike in {history.days_since_larger_move} days."
    if history.is_largest_ytd and history.ytd_move_count >= 5:
        return "Biggest spike of the year."
    if alert.alert_tier == "emergency":
        return "Historic liquidation spike."
    skew = _liq_skew(alert)
    if skew == "long":
        return "Longs flushed on selloff — forced deleveraging."
    if skew == "short":
        return "Shorts flushed on rally — forced cover wave."
    return "Mixed liquidation flow — both sides hit."


def _liquidation_takeaway(alert: AlertTrigger) -> str:
    skew = _liq_skew(alert)
    if skew == "long":
        return "Price under pressure — watch for follow-through or exhaustion."
    if skew == "short":
        return "Upside pressure — watch for cover exhaust or continuation."
    asset = alert.indicator.split("_")[0].upper()
    return f"{asset} liquidation wave — watch for follow-through or fade."


def _exchange_spread_headline(alert: AlertTrigger) -> str:
    asset = alert.indicator.split("_")[0].upper()
    if alert.prev_value is not None:
        if alert.value < alert.prev_value:
            return f"{asset} SPREAD COMPRESS"
        if alert.value > alert.prev_value:
            return f"{asset} SPREAD WIDEN"
    return f"{asset} EXCHANGE SPREAD"


def _data_lines_for_exchange_spread(alert: AlertTrigger, history: MoveHistory) -> list[str]:
    line = f"{_format_value(alert)} Kraken vs Coinbase"
    if alert.prev_value is not None:
        pct = abs(history.pct_change or alert.magnitude_pct)
        if pct > 0:
            line += f" ({_format_pct_line(pct, up=_direction_up(alert))} vs prior)"
    return [line]


def _exchange_spread_context(alert: AlertTrigger, history: MoveHistory) -> str | None:
    compressing = alert.prev_value is not None and alert.value < alert.prev_value
    widening = alert.prev_value is not None and alert.value > alert.prev_value
    if history.days_since_larger_move and history.days_since_larger_move >= 14:
        word = "compression" if compressing else "widening" if widening else "move"
        return f"Largest spread {word} in {history.days_since_larger_move} days."
    if compressing:
        return "Cross-exchange arb gap compressed."
    if widening:
        return "Cross-exchange arb gap widened."
    return None


def _exchange_spread_takeaway(alert: AlertTrigger) -> str:
    if alert.prev_value is not None and alert.value < alert.prev_value:
        return "Fragmentation easing — watch basis and follow-through."
    if alert.prev_value is not None and alert.value > alert.prev_value:
        return "Fragmentation rising — arb stress on watch."
    return _takeaway_line(alert)


def _data_lines_for_alert(alert: AlertTrigger, history: MoveHistory) -> list[str]:
    if alert.indicator.endswith("_liquidations"):
        return _data_lines_for_liquidation(alert, history)
    if alert.indicator.endswith("_exchange_spread"):
        return _data_lines_for_exchange_spread(alert, history)
    lines = [_format_value(alert)]
    if alert.alert_unit == "percent" and alert.prev_value is not None:
        pct = abs(history.pct_change or alert.magnitude_pct)
        if pct > 0:
            lines[0] = f"{lines[0]} ({_format_pct_line(pct, up=_direction_up(alert))})"
    elif alert.alert_unit == "absolute" and alert.prev_value is not None:
        if alert.indicator.endswith("_basis"):
            delta = history.abs_change
            sign = "+" if _direction_up(alert) else "-"
            lines[0] = f"{lines[0]} ({sign}{abs(delta):.1f} bps vs prior)"
        else:
            bps = abs(history.abs_change) * 100
            if bps >= 0.01:
                sign = "+" if _direction_up(alert) else "-"
                move = f"{bps:.0f} bps" if bps >= 1 else f"{abs(history.abs_change):.2f} pp"
                lines[0] = f"{lines[0]} ({sign}{move})"
    return lines


def _headline_name(alert: AlertTrigger, *, major: bool) -> str:
    if alert.indicator.endswith("_liquidations"):
        return _liquidation_headline(alert)
    return _display_name(alert)


def _headline_for_alert(alert: AlertTrigger, *, major: bool, emoji: str) -> str:
    if alert.indicator.endswith("_liquidations"):
        return f"{emoji}{_liquidation_headline(alert)}".strip()
    name = _headline_name(alert, major=major)
    body = f"MAJOR MOVE: {name}" if major else name
    return f"{emoji}{body}".strip()


def _context_line(alert: AlertTrigger, history: MoveHistory) -> str | None:
    up = _direction_up(alert)
    if history.is_all_time_high:
        if history.prior_record is not None:
            prior = _format_value(AlertTrigger(
                alert.indicator, alert.name, history.prior_record, None, [], [], [], "", False, alert.timestamp,
            ))
            return f"New ATH — prior record {prior}."
        return "New all-time high."
    if history.days_since_larger_move and history.days_since_larger_move >= 14:
        word = "gain" if up else "decline"
        return f"Largest daily {word} in {history.days_since_larger_move} days."
    if history.is_largest_ytd and history.ytd_move_count >= 5:
        return "Largest move of the year."
    if alert.alert_tier == "emergency":
        return "Historic spike."
    return None


def _takeaway_line(alert: AlertTrigger) -> str:
    if alert.indicator in TAKEAWAY_HINTS:
        return TAKEAWAY_HINTS[alert.indicator]
    return "Watch follow-through."


def _display_name(alert: AlertTrigger) -> str:
    return (
        alert.name.replace("S&P 500", "SPY")
        .replace("NASDAQ 100", "QQQ")
        .replace("CPI Inflation (YoY %)", "CPI inflation")
    )


def _format_value(alert: AlertTrigger) -> str:
    v = alert.value
    if alert.indicator.endswith("_funding"):
        return f"{v * 100:.4f}%"
    if alert.indicator.endswith(("_basis", "_exchange_spread")):
        return f"{v:.1f} bps"
    if alert.indicator.endswith("_liquidations"):
        if v >= 1_000_000:
            return f"${v / 1_000_000:.1f}M"
        return f"${v:,.0f}"
    if alert.indicator in ("cpi_yoy", "unemployment", "fed_funds", "treasury_10y", "mortgage_30y"):
        return f"{v:.2f}%"
    if v >= 1000:
        return f"${v:,.0f}"
    if v >= 100:
        return f"{v:,.1f}"
    if abs(v) < 10:
        return f"{v:.2f}"
    return f"{v:.1f}"


def _direction_up(alert: AlertTrigger) -> bool:
    if alert.prev_value is None:
        return True
    return alert.value > alert.prev_value


def _cross_direction(alert: AlertTrigger) -> str | None:
    for reason in alert.reasons:
        if "crossed above" in reason:
            return "above"
        if "crossed below" in reason:
            return "below"
    return None


def _is_key_level_cross(alert: AlertTrigger) -> bool:
    if alert.indicator not in KEY_LEVEL_CROSS_INDICATORS:
        return False
    return any(r in ("crosses_above", "crosses_below") for r in alert.rule_types)


def _cross_context_line(alert: AlertTrigger) -> str | None:
    direction = _cross_direction(alert)
    if not direction:
        return None
    if alert.indicator == "yield_curve":
        return "Uninverted." if direction == "above" else "Inverted."
    bound = next((r.split()[-1] for r in alert.reasons if "crossed" in r), None)
    if bound:
        word = "above" if direction == "above" else "below"
        return f"Crossed {word} {bound}."
    return "Key level break."


def _major_for_headline(
    alert: AlertTrigger,
    history: MoveHistory,
    posting_cfg: dict[str, Any],
    *,
    is_emergency: bool,
) -> bool:
    standout = _is_standout(alert, history, posting_cfg, is_emergency=is_emergency)
    if history.is_all_time_high:
        return True
    if _is_key_level_cross(alert):
        return standout
    return is_emergency or alert.alert_tier == "emergency" or (
        standout and (alert.standalone_major or alert.score >= float(posting_cfg.get("high_single_threshold", 85)))
    )


def _cross_sections(
    alert: AlertTrigger,
    history: MoveHistory,
    posting_cfg: dict[str, Any],
    *,
    is_emergency: bool,
    emoji: str | None = None,
) -> dict[str, Any]:
    standout = _is_standout(alert, history, posting_cfg, is_emergency=is_emergency)
    resolved_emoji = emoji if emoji is not None else _emoji_for_post(
        alert, history, posting_cfg, standout=standout, is_emergency=is_emergency,
    )
    return {
        "headline": _headline_for_alert(alert, major=standout, emoji=resolved_emoji),
        "data_lines": [_format_value(alert)],
        "context": _cross_context_line(alert),
        "takeaway": _takeaway_line(alert),
    }


def _multi_data_lines_for_alert(alert: AlertTrigger, history: MoveHistory) -> list[str]:
    if _is_key_level_cross(alert):
        return [_format_value(alert)]
    if alert.indicator.endswith("_exchange_spread"):
        short = _exchange_spread_headline(alert)
        val = _format_value(alert)
        line = f"{short} {val}"
        if alert.prev_value is not None:
            pct = abs(history.pct_change or alert.magnitude_pct)
            if pct > 0:
                line += f" ({_format_pct_line(pct, up=_direction_up(alert))})"
        return [line]
    short = _headline_name(alert, major=False)
    val = _format_value(alert)
    line = f"{short} {val}"
    if alert.prev_value is not None and alert.alert_unit == "percent":
        pct = abs(history.pct_change or alert.magnitude_pct)
        if pct > 0:
            line += f" ({_format_pct_line(pct, up=_direction_up(alert))})"
    elif alert.prev_value is not None and alert.alert_unit == "absolute":
        if alert.indicator.endswith("_basis"):
            delta = history.abs_change
            sign = "+" if _direction_up(alert) else "-"
            line += f" ({sign}{abs(delta):.1f} bps vs prior)"
        else:
            bps = abs(history.abs_change) * 100
            if bps >= 0.01:
                sign = "+" if _direction_up(alert) else "-"
                move = f"{bps:.0f} bps" if bps >= 1 else f"{abs(history.abs_change):.2f} pp"
                line += f" ({sign}{move})"
    return [line]


def _is_standout(alert: AlertTrigger, history: MoveHistory, posting_cfg: dict[str, Any], *, is_emergency: bool) -> bool:
    if is_emergency or alert.alert_tier == "emergency":
        return True
    if alert.score >= float(posting_cfg.get("emergency_threshold", 90)):
        return True
    if history.is_all_time_high or history.is_largest_ytd:
        return True
    if alert.standalone_major and alert.alert_tier in ("major", "emergency"):
        return True
    if any(r in ("crosses_above", "crosses_below") for r in alert.rule_types):
        if alert.indicator in ("vix", "yield_curve", "cpi_yoy", "fed_funds"):
            return True
    return False


def _emoji_for_post(
    alert: AlertTrigger,
    history: MoveHistory,
    posting_cfg: dict[str, Any],
    *,
    standout: bool,
    is_emergency: bool,
) -> str:
    """At most one emoji; only for exceptional moves."""
    if is_emergency or alert.alert_tier == "emergency":
        return "🚨 "
    if history.is_all_time_high:
        return "📈 "
    if alert.standalone_major and alert.indicator.endswith("_liquidations"):
        return "🚨 "
    if any(r in ("crosses_above", "crosses_below") for r in alert.rule_types):
        if alert.indicator in ("vix", "yield_curve", "cpi_yoy", "fed_funds"):
            return "⚠️ "
    return ""


def _multi_emoji(
    alerts: list[AlertTrigger],
    histories: dict[str, MoveHistory],
    posting_cfg: dict[str, Any],
    *,
    is_emergency: bool,
) -> str:
    """Pick at most one emoji for a grouped post; exceptional moves only."""
    if is_emergency or any(a.alert_tier == "emergency" for a in alerts):
        return "🚨 "
    if any(histories.get(a.indicator, MoveHistory()).is_all_time_high for a in alerts):
        return "📈 "
    if any(a.standalone_major and a.indicator.endswith("_liquidations") for a in alerts):
        return "🚨 "
    for a in alerts:
        if any(r in ("crosses_above", "crosses_below") for r in a.rule_types):
            if a.indicator in ("vix", "yield_curve", "cpi_yoy", "fed_funds"):
                return "⚠️ "
    return ""


def _template_ath(alert: AlertTrigger, history: MoveHistory, posting_cfg: dict[str, Any], *, is_emergency: bool) -> str:
    emoji = _emoji_for_post(alert, history, posting_cfg, standout=True, is_emergency=is_emergency)
    headline = _headline_for_alert(alert, major=True, emoji=emoji)
    return _assemble_tweet(
        headline=headline,
        data_lines=[_format_value(alert)],
        context=_context_line(alert, history),
        takeaway=_takeaway_line(alert),
    )


def _template_cross(
    alert: AlertTrigger,
    history: MoveHistory,
    posting_cfg: dict[str, Any],
    *,
    is_emergency: bool,
) -> str:
    if not _cross_direction(alert):
        return _template_major_move(alert, history, posting_cfg, is_emergency=is_emergency)
    return _assemble_tweet(**_cross_sections(alert, history, posting_cfg, is_emergency=is_emergency))


def _template_macro_release(
    alert: AlertTrigger,
    history: MoveHistory,
    posting_cfg: dict[str, Any],
    *,
    is_emergency: bool,
) -> str:
    standout = _is_standout(alert, history, posting_cfg, is_emergency=is_emergency)
    emoji = _emoji_for_post(alert, history, posting_cfg, standout=standout, is_emergency=is_emergency)
    headline = _headline_for_alert(alert, major=standout, emoji=emoji)
    data_lines = _data_lines_for_alert(alert, history) if alert.prev_value is not None else [_format_value(alert)]
    return _assemble_tweet(
        headline=headline,
        data_lines=data_lines,
        context=_context_line(alert, history),
        takeaway=_takeaway_line(alert),
    )


def _template_exchange_spread(
    alert: AlertTrigger,
    history: MoveHistory,
    posting_cfg: dict[str, Any],
    *,
    is_emergency: bool,
) -> str:
    standout = _is_standout(alert, history, posting_cfg, is_emergency=is_emergency)
    emoji = _emoji_for_post(alert, history, posting_cfg, standout=standout, is_emergency=is_emergency)
    return _assemble_tweet(
        headline=f"{emoji}{_exchange_spread_headline(alert)}".strip(),
        data_lines=_data_lines_for_exchange_spread(alert, history),
        context=_exchange_spread_context(alert, history),
        takeaway=_exchange_spread_takeaway(alert),
    )


def _template_liquidation(
    alert: AlertTrigger,
    history: MoveHistory,
    posting_cfg: dict[str, Any],
    *,
    is_emergency: bool,
) -> str:
    _ensure_liq_breakdown(alert)
    standout = _is_standout(alert, history, posting_cfg, is_emergency=is_emergency)
    emoji = _emoji_for_post(alert, history, posting_cfg, standout=standout, is_emergency=is_emergency)
    return _assemble_tweet(
        headline=_headline_for_alert(alert, major=False, emoji=emoji),
        data_lines=_data_lines_for_liquidation(alert, history),
        context=_liquidation_context(alert, history),
        takeaway=_liquidation_takeaway(alert),
    )


def _template_major_move(
    alert: AlertTrigger,
    history: MoveHistory,
    posting_cfg: dict[str, Any],
    *,
    is_emergency: bool,
) -> str:
    standout = _is_standout(alert, history, posting_cfg, is_emergency=is_emergency)
    emoji = _emoji_for_post(alert, history, posting_cfg, standout=standout, is_emergency=is_emergency)
    headline = _headline_for_alert(
        alert, major=_major_for_headline(alert, history, posting_cfg, is_emergency=is_emergency), emoji=emoji,
    )
    data_lines = _data_lines_for_alert(alert, history) if alert.prev_value is not None else [_format_value(alert)]
    return _assemble_tweet(
        headline=headline,
        data_lines=data_lines,
        context=_context_line(alert, history),
        takeaway=_takeaway_line(alert),
    )


def _pick_single_template(
    alert: AlertTrigger,
    history: MoveHistory,
    posting_cfg: dict[str, Any],
    *,
    is_emergency: bool,
) -> str:
    if alert.indicator.endswith("_liquidations"):
        return _template_liquidation(alert, history, posting_cfg, is_emergency=is_emergency)

    if alert.indicator.endswith("_exchange_spread"):
        return _template_exchange_spread(alert, history, posting_cfg, is_emergency=is_emergency)

    if is_emergency or alert.alert_tier == "emergency":
        return _template_major_move(alert, history, posting_cfg, is_emergency=True)

    if history.is_all_time_high:
        return _template_ath(alert, history, posting_cfg, is_emergency=is_emergency)

    if _is_key_level_cross(alert):
        return _template_cross(alert, history, posting_cfg, is_emergency=is_emergency)

    if alert.indicator in MACRO_INDICATORS or alert.is_macro:
        return _template_macro_release(alert, history, posting_cfg, is_emergency=is_emergency)

    return _template_major_move(alert, history, posting_cfg, is_emergency=is_emergency)


def compose_single_tweet(
    alert: AlertTrigger,
    *,
    history: MoveHistory | None = None,
    posting_cfg: dict[str, Any] | None = None,
    is_emergency: bool = False,
) -> str:
    history = history or MoveHistory()
    posting_cfg = posting_cfg or {}
    return _pick_single_template(alert, history, posting_cfg, is_emergency=is_emergency)


def compose_multi_tweet(
    alerts: list[AlertTrigger],
    theme: str | None = None,
    *,
    histories: dict[str, MoveHistory] | None = None,
    posting_cfg: dict[str, Any] | None = None,
    is_emergency: bool = False,
) -> str:
    histories = histories or {}
    posting_cfg = posting_cfg or {}
    if len(alerts) == 1:
        a = alerts[0]
        return compose_single_tweet(
            a,
            history=histories.get(a.indicator, MoveHistory()),
            posting_cfg=posting_cfg,
            is_emergency=is_emergency,
        )

    theme = theme or (alerts[0].themes[0] if alerts[0].themes else "markets")
    top = max(alerts, key=lambda x: x.score)
    top_hist = histories.get(top.indicator, MoveHistory())
    emoji = _multi_emoji(alerts, histories, posting_cfg, is_emergency=is_emergency)

    data_lines: list[str] = []
    for a in sorted(alerts, key=lambda x: x.score, reverse=True)[:4]:
        data_lines.extend(_multi_data_lines_for_alert(a, histories.get(a.indicator, MoveHistory())))

    if _is_key_level_cross(top):
        cross = _cross_sections(top, top_hist, posting_cfg, is_emergency=is_emergency, emoji=emoji)
        return _assemble_tweet(headline=cross["headline"], data_lines=data_lines, context=cross["context"], takeaway=cross["takeaway"])

    implications = {
        "risk_on": "Risk-on across assets.",
        "risk_off": "Defensive tone building.",
        "crypto": "Crypto momentum building.",
        "inflation_pressure": "Inflation signals stacking.",
        "easing_conditions": "Easing tone building.",
        "tightening_conditions": "Tightening pressure rising.",
        "housing": "Housing indicators diverging.",
    }
    headline = _headline_for_alert(
        top, major=_major_for_headline(top, top_hist, posting_cfg, is_emergency=is_emergency), emoji=emoji,
    )
    return _assemble_tweet(
        headline=headline,
        data_lines=data_lines,
        context=_context_line(top, top_hist) or implications.get(theme),
        takeaway="Watch follow-through across the cluster.",
    )