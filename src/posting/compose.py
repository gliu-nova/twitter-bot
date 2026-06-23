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

CONTEXT_HINTS: dict[str, str] = {
    "btc": "Could pull risk assets higher if it holds — watching equities and credit.",
    "eth": "Alt rotation often follows — breadth matters more than one name.",
    "sol": "High-beta crypto move — risk appetite signal strengthening.",
    "sp500": "Equity tone sets the cross-asset mood — rates and USD next.",
    "nasdaq100": "Tech leadership move — growth vs defensives in focus.",
    "vix": "Elevated vol often pressures multiples — dip-buying or de-risking?",
    "treasury_10y": "Rates rippling through equities, housing, and crypto.",
    "yield_curve": "Curve shifts reprice growth vs recession — Fed path in play.",
    "cpi_yoy": "May influence Fed expectations and real yields.",
    "dxy": "Stronger dollar can pressure commodities and EM risk assets.",
    "fear_greed": "Extreme sentiment — contrarian setup or trend confirmation?",
    "mortgage_30y": "Housing affordability moves with mortgage rates — demand on watch.",
    "consumer_sentiment": "Spending expectations shifting — growth-sensitive assets react first.",
    "oil": "Energy moves feed inflation expectations — bullish for gold?",
    "hy_spread": "Credit stress signal — risk-off if spreads keep widening.",
    "pmi_manufacturing": "Factory activity leads the cycle — watch orders and employment.",
    "ism_services": "Services drive most of GDP — labor market implications ahead.",
    "fed_funds": "Policy rate shift — liquidity and risk assets repricing.",
    "unemployment": "Labor cooling or heating — Fed reaction function in focus.",
    "jobless_claims": "Early labor signal — soft landing or reacceleration?",
    "btc_funding": "Elevated funding often signals crowded longs — squeeze risk rises.",
    "eth_funding": "ETH funding extremes can front-run alt beta and DeFi flows.",
    "sol_funding": "SOL funding spikes often reflect high-beta positioning unwind risk.",
    "btc_basis": "Basis blowouts flag perp demand vs spot — arb and basis trades react first.",
    "eth_basis": "ETH basis dislocations often lead alt perp funding resets.",
    "sol_basis": "SOL basis stress can signal thin liquidity on high-beta perps.",
    "btc_exchange_spread": "Wide spot spreads hint at fragmentation or transfer/arbitrage stress.",
    "eth_exchange_spread": "ETH venue dislocations can precede volatile DeFi/perp resets.",
    "sol_exchange_spread": "SOL spread blowouts often reflect thin books across venues.",
    "btc_liquidations": "Liquidation clusters can trigger reflexive moves — watch follow-through.",
    "eth_liquidations": "ETH liquidation waves often spill into broader alt perp deleveraging.",
    "sol_liquidations": "SOL liquidation spikes can accelerate high-beta unwind cascades.",
}

# Short closing lines (→ prefixed in output)
TAKEAWAY_HINTS: dict[str, str] = {
    "btc": "Watch follow-through across risk assets.",
    "eth": "Alt breadth matters more than one name.",
    "sol": "High-beta move — risk appetite signal.",
    "btc_liquidations": "Watch for reflexive follow-through.",
    "eth_liquidations": "Watch for alt perp deleveraging.",
    "sol_liquidations": "Watch for high-beta unwind cascades.",
    "btc_funding": "Crowded longs — squeeze risk elevated.",
    "eth_funding": "Funding extremes can front-run alt flows.",
    "sol_funding": "Positioning unwind risk on watch.",
    "sp500": "Rates and USD reaction next.",
    "vix": "Vol elevated — multiples under pressure.",
    "treasury_10y": "Rippling through equities and housing.",
    "yield_curve": "Growth vs recession repricing.",
    "cpi_yoy": "Fed expectations and real yields in focus.",
    "fear_greed": "Sentiment extreme — trend or contrarian?",
}

CROSS_CONTEXT: dict[str, dict[str, str]] = {
    "vix": {
        "above": "Elevated volatility often signals rising market stress.",
        "below": "Markets pricing calmer conditions — complacency risk if it persists.",
    },
    "yield_curve": {
        "above": "Uninversion can signal growth optimism — recession fears easing.",
        "below": "Inversion deepens — recession pricing strengthening.",
    },
    "fear_greed": {
        "above": "Greed zone — euphoria or sustained risk-on?",
        "below": "Fear zone — capitulation or buying opportunity?",
    },
}

UP_STRONG = ("surged", "spiked", "jumped", "soared")
UP_MILD = ("climbed", "rose", "gained", "rallied")
DOWN_STRONG = ("plunged", "crashed", "tumbled", "collapsed")
DOWN_MILD = ("dropped", "slid", "fell", "pulled back")
CROSS_ABOVE = ("crossed above", "broke above", "cleared")
CROSS_BELOW = ("fell below", "slipped under", "dropped under")
FLIP_VERBS = ("flipped", "turned", "shifted")


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


def _data_lines_for_alert(alert: AlertTrigger, history: MoveHistory) -> list[str]:
    lines = [_format_value(alert)]
    if alert.alert_unit == "percent" and alert.prev_value is not None:
        pct = abs(history.pct_change or alert.magnitude_pct)
        if pct > 0:
            lines.append(_format_pct_line(pct, up=_direction_up(alert)))
    elif alert.alert_unit == "absolute" and alert.prev_value is not None:
        bps = abs(history.abs_change) * 100
        if bps >= 0.01:
            sign = "+" if _direction_up(alert) else "-"
            move = f"{bps:.0f} bps" if bps >= 1 else f"{abs(history.abs_change):.2f} pp"
            lines.append(f"{sign}{move}")
    return lines


def _headline_name(alert: AlertTrigger, *, major: bool) -> str:
    if alert.indicator.endswith("_liquidations"):
        asset = alert.indicator.split("_")[0].upper()
        return f"{asset} Liquidations (1H)" if major else f"{asset} LIQUIDATIONS"
    return _display_name(alert)


def _headline_for_alert(alert: AlertTrigger, *, major: bool, emoji: str) -> str:
    name = _headline_name(alert, major=major)
    body = f"MAJOR MOVE: {name}" if major else name
    return f"{emoji}{body}".strip()


def _context_line(alert: AlertTrigger, history: MoveHistory) -> str | None:
    up = _direction_up(alert)
    if history.is_all_time_high:
        return "New all-time high."
    if history.days_since_larger_move and history.days_since_larger_move >= 14:
        word = "spike" if up else "drop"
        return f"Largest {word} in {history.days_since_larger_move} days."
    if history.is_largest_ytd and history.ytd_move_count >= 5:
        return "Largest move of the year."
    if alert.alert_tier == "emergency":
        return "(historic spike)"
    return None


def _takeaway_line(alert: AlertTrigger) -> str:
    if alert.indicator in TAKEAWAY_HINTS:
        return TAKEAWAY_HINTS[alert.indicator]
    ctx = CONTEXT_HINTS.get(alert.indicator, "Watch follow-through.")
    part = ctx.split("—")[0].split(" - ")[0].strip()
    if len(part) > 72:
        part = part[:69].rstrip() + "..."
    return part if part.endswith(".") else f"{part}."


def _pick(options: tuple[str, ...], alert: AlertTrigger) -> str:
    key = sum(ord(c) for c in alert.indicator + str(alert.value))
    return options[key % len(options)]


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


def _tier_label(tier: str) -> str:
    return {"normal": "notable", "major": "major", "emergency": "historic"}[tier]


def _direction_up(alert: AlertTrigger) -> bool:
    if alert.prev_value is None:
        return True
    return alert.value > alert.prev_value


def _move_verb(alert: AlertTrigger, *, strong: bool) -> str:
    up = _direction_up(alert)
    if strong:
        return _pick(UP_STRONG if up else DOWN_STRONG, alert)
    return _pick(UP_MILD if up else DOWN_MILD, alert)


def _cross_verb(alert: AlertTrigger, direction: str) -> str:
    if alert.indicator == "yield_curve":
        return _pick(FLIP_VERBS, alert)
    return _pick(CROSS_ABOVE if direction == "above" else CROSS_BELOW, alert)


def _cross_direction(alert: AlertTrigger) -> str | None:
    for reason in alert.reasons:
        if "crossed above" in reason:
            return "above"
        if "crossed below" in reason:
            return "below"
    return None


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
    if not standout and not is_emergency:
        return ""
    if is_emergency or alert.alert_tier == "emergency":
        return "🚨 "
    if history.is_all_time_high:
        return "🚨 "
    if alert.score >= float(posting_cfg.get("emergency_threshold", 90)):
        return "🚨 "
    if alert.standalone_major and alert.alert_tier in ("major", "emergency"):
        return "🚨 "
    if any(r in ("crosses_above", "crosses_below") for r in alert.rule_types):
        if alert.indicator in ("vix", "yield_curve", "cpi_yoy", "fed_funds"):
            return "⚠️ "
    return ""


def _template_ath(alert: AlertTrigger, history: MoveHistory, posting_cfg: dict[str, Any], *, is_emergency: bool) -> str:
    emoji = _emoji_for_post(alert, history, posting_cfg, standout=True, is_emergency=is_emergency)
    headline = _headline_for_alert(alert, major=False, emoji=emoji)
    data_lines = [_format_value(alert)]
    context = "New all-time high."
    if history.prior_record is not None:
        prior = _format_value(AlertTrigger(
            alert.indicator, alert.name, history.prior_record, None, [], [], [], "", False, alert.timestamp,
        ))
        context = f"New ATH — prior record {prior}."
    return _assemble_tweet(
        headline=headline,
        data_lines=data_lines,
        context=context,
        takeaway=_takeaway_line(alert),
    )


def _template_cross(
    alert: AlertTrigger,
    history: MoveHistory,
    posting_cfg: dict[str, Any],
    *,
    is_emergency: bool,
) -> str:
    direction = _cross_direction(alert)
    if not direction:
        return _template_major_move(alert, history, posting_cfg, is_emergency=is_emergency)

    standout = _is_standout(alert, history, posting_cfg, is_emergency=is_emergency)
    emoji = _emoji_for_post(alert, history, posting_cfg, standout=standout, is_emergency=is_emergency)
    headline = _headline_for_alert(alert, major=standout, emoji=emoji)
    data_lines = [_format_value(alert)]

    if alert.indicator == "yield_curve":
        context = "Uninverted." if direction == "above" else "Inverted."
    else:
        bound = next((r.split()[-1] for r in alert.reasons if "crossed" in r), None)
        verb = _cross_verb(alert, direction)
        context = f"{verb} {bound}." if bound else "Key level break."

    cross_ctx = CROSS_CONTEXT.get(alert.indicator, {}).get(direction)
    takeaway = cross_ctx or _takeaway_line(alert)
    return _assemble_tweet(
        headline=headline,
        data_lines=data_lines,
        context=context,
        takeaway=takeaway,
    )


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


def _template_major_move(
    alert: AlertTrigger,
    history: MoveHistory,
    posting_cfg: dict[str, Any],
    *,
    is_emergency: bool,
) -> str:
    standout = _is_standout(alert, history, posting_cfg, is_emergency=is_emergency)
    major = is_emergency or alert.alert_tier == "emergency" or (
        standout and (alert.standalone_major or alert.score >= float(posting_cfg.get("high_single_threshold", 85)))
    )
    emoji = _emoji_for_post(alert, history, posting_cfg, standout=standout, is_emergency=is_emergency)
    headline = _headline_for_alert(alert, major=major, emoji=emoji)
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
    is_move_rule = any(r in ("percent_change", "absolute_change") for r in alert.rule_types)
    is_cross_rule = any(r in ("crosses_above", "crosses_below") for r in alert.rule_types)

    if is_emergency or alert.alert_tier == "emergency":
        return _template_major_move(alert, history, posting_cfg, is_emergency=True)

    if history.is_all_time_high and not is_move_rule:
        return _template_ath(alert, history, posting_cfg, is_emergency=is_emergency)

    if is_cross_rule and not is_move_rule:
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
    theme = theme or (alerts[0].themes[0] if alerts[0].themes else "markets")
    headline = THEME_HEADLINES.get(theme, "Market move")

    top = max(alerts, key=lambda x: x.score)
    top_hist = histories.get(top.indicator, MoveHistory())
    standout = is_emergency or any(
        _is_standout(a, histories.get(a.indicator, MoveHistory()), posting_cfg, is_emergency=is_emergency)
        for a in alerts
    )
    emoji = ""
    if standout and (is_emergency or top.alert_tier == "emergency"):
        emoji = "🚨 "
    elif standout and theme in ("risk_off",):
        emoji = "⚠️ "

    data_lines: list[str] = []
    for a in sorted(alerts, key=lambda x: x.score, reverse=True)[:4]:
        hist = histories.get(a.indicator, MoveHistory())
        short = _headline_name(a, major=False)
        val = _format_value(a)
        if a.prev_value is not None and a.alert_unit == "percent":
            pct = abs(hist.pct_change or a.magnitude_pct)
            if pct > 0:
                data_lines.append(f"{short} {val} ({_format_pct_line(pct, up=_direction_up(a))})")
                continue
        data_lines.append(f"{short} {val}")

    implications = {
        "risk_on": "Risk appetite improving across assets.",
        "risk_off": "Defensive tone building.",
        "crypto": "Crypto momentum building.",
        "inflation_pressure": "Inflation signals stacking.",
        "easing_conditions": "Easing tone building.",
        "tightening_conditions": "Tightening pressure rising.",
        "housing": "Housing indicators diverging.",
    }
    return _assemble_tweet(
        headline=f"{emoji}{headline}".strip(),
        data_lines=data_lines,
        context=_context_line(top, top_hist) or implications.get(theme),
        takeaway="Follow-through matters across the cluster.",
    )