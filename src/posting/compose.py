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
    short = _headline_name(alert, major=False)
    val = _format_value(alert)
    lines = [f"{short} {val}"]
    if alert.prev_value is not None and alert.alert_unit == "percent":
        pct = abs(history.pct_change or alert.magnitude_pct)
        if pct > 0:
            lines.append(_format_pct_line(pct, up=_direction_up(alert)))
    elif alert.prev_value is not None and alert.alert_unit == "absolute":
        bps = abs(history.abs_change) * 100
        if bps >= 0.01:
            sign = "+" if _direction_up(alert) else "-"
            move = f"{bps:.0f} bps" if bps >= 1 else f"{abs(history.abs_change):.2f} pp"
            lines.append(f"{sign}{move}")
    return lines


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