from __future__ import annotations

from typing import Any

from src.posting.history import MoveHistory, rarity_phrase
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


def _emoji(alert: AlertTrigger, *, standout: bool) -> str:
    if not standout:
        return ""
    up = _direction_up(alert)
    if alert.alert_tier == "emergency":
        return "🔥 " if up else "⚠️ "
    if any(r in ("crosses_above", "crosses_below") for r in alert.rule_types):
        return "⚠️ "
    return "📈 " if up else "📉 "


def _emergency_prefix(
    alert: AlertTrigger,
    posting_cfg: dict[str, Any],
    *,
    standout: bool,
    force_caps: bool = False,
) -> str:
    if force_caps or alert.alert_tier == "emergency":
        name = _display_name(alert).upper()
        verb = _move_verb(alert, strong=True).upper()
        return f"MAJOR MOVE: {name} {verb} "
    if not standout:
        return ""
    if alert.score >= float(posting_cfg.get("emergency_threshold", 90)):
        name = _display_name(alert).upper()
        verb = _move_verb(alert, strong=True).upper()
        return f"MAJOR MOVE: {name} {verb} "
    if alert.indicator == "yield_curve" and _cross_direction(alert):
        return "BREAKING: Yield curve "
    return ""


def _template_ath(alert: AlertTrigger, history: MoveHistory, posting_cfg: dict[str, Any], *, is_emergency: bool) -> str:
    name = _display_name(alert)
    val = _format_value(alert)
    prefix = "🚨 "
    body = f"{name} reached a new all-time high above {val}."
    if history.prior_record is not None:
        prior = _format_value(AlertTrigger(
            alert.indicator, alert.name, history.prior_record, None, [], [], [], "", False, alert.timestamp,
        ))
        body += f" Previous record: {prior}."
    ctx = CONTEXT_HINTS.get(alert.indicator, "ATH breaks often attract momentum — watch follow-through.")
    return f"{prefix}{body} {ctx}"[:280]


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

    name = _display_name(alert)
    val = _format_value(alert)
    standout = _is_standout(alert, history, posting_cfg, is_emergency=is_emergency)
    prefix = _emergency_prefix(alert, posting_cfg, standout=standout) or "🚨 "
    verb = _cross_verb(alert, direction)

    if alert.indicator == "yield_curve":
        if direction == "above":
            opener = f"{prefix}uninverted — now {val}."
        else:
            opener = f"{prefix}inverted — now {val}."
    else:
        bound = next((r.split()[-1] for r in alert.reasons if "crossed" in r), val)
        opener = f"{prefix}{name} {verb} {bound} — now {val}."

    ctx = CROSS_CONTEXT.get(alert.indicator, {}).get(direction)
    if not ctx:
        ctx = CONTEXT_HINTS.get(alert.indicator, "Level break — watching follow-through.")
    return f"{opener} {ctx}"[:280]


def _template_macro_release(
    alert: AlertTrigger,
    history: MoveHistory,
    posting_cfg: dict[str, Any],
    *,
    is_emergency: bool,
) -> str:
    name = _display_name(alert)
    val = _format_value(alert)
    standout = _is_standout(alert, history, posting_cfg, is_emergency=is_emergency)
    prefix = _emergency_prefix(alert, posting_cfg, standout=standout) or "🚨 "
    verb = "rose to" if _direction_up(alert) else "fell to"
    if alert.prev_value is not None and history.abs_change:
        opener = f"{prefix}{name} {verb} {val}."
    else:
        opener = f"{prefix}{name} at {val}."

    rarity = rarity_phrase(history, direction_up=_direction_up(alert))
    ctx = CONTEXT_HINTS.get(alert.indicator, "Macro data shift — markets repricing.")
    parts = [opener]
    if rarity:
        parts.append(rarity)
    parts.append(ctx)
    return " ".join(parts)[:280]


def _template_major_move(
    alert: AlertTrigger,
    history: MoveHistory,
    posting_cfg: dict[str, Any],
    *,
    is_emergency: bool,
) -> str:
    name = _display_name(alert)
    val = _format_value(alert)
    tier = _tier_label(alert.alert_tier)
    strong = alert.alert_tier in ("major", "emergency")
    verb = _move_verb(alert, strong=strong)
    standout = _is_standout(alert, history, posting_cfg, is_emergency=is_emergency)
    force_caps = is_emergency or alert.alert_tier == "emergency"
    prefix = _emergency_prefix(alert, posting_cfg, standout=standout, force_caps=force_caps)
    emoji = "" if prefix else _emoji(alert, standout=standout and not force_caps)

    if force_caps and prefix.endswith(" "):
        # Caps prefix already includes name+verb; shorten opener
        if alert.alert_unit == "percent" and alert.prev_value is not None:
            pct = abs(history.pct_change or alert.magnitude_pct)
            opener = f"{prefix}{pct:.1f}% to {val} (historic move)."
        elif alert.alert_unit == "absolute" and alert.prev_value is not None:
            bps = abs(history.abs_change) * 100
            move_txt = f"{bps:.0f} bps" if bps >= 1 else f"{abs(history.abs_change):.2f} pp"
            opener = f"{prefix}{move_txt} to {val} (historic move)."
        else:
            opener = f"{prefix}to {val} (historic move)."
        rarity = rarity_phrase(history, direction_up=_direction_up(alert))
        ctx = CONTEXT_HINTS.get(alert.indicator, "Watching for follow-through across markets.")
        parts = [opener]
        if rarity:
            parts.append(rarity)
        parts.append(ctx)
        return " ".join(parts)[:280]

    if alert.alert_unit == "absolute" and alert.prev_value is not None:
        bps = abs(history.abs_change) * 100
        move_txt = f"{bps:.0f} bps" if bps >= 1 else f"{abs(history.abs_change):.2f} pp"
        opener = f"{prefix}{emoji}{name} {verb} {move_txt} to {val} ({tier} move)."
    elif alert.alert_unit == "percent" and alert.prev_value is not None:
        pct = abs(history.pct_change or alert.magnitude_pct)
        opener = f"{prefix}{emoji}{name} {verb} {pct:.1f}% today to {val} ({tier} move)."
    elif alert.prev_value is not None:
        opener = f"{prefix}{emoji}{name} {verb} to {val} ({tier} move)."
    else:
        opener = f"{prefix}{emoji}{name} at {val} ({tier} move)."

    rarity = rarity_phrase(history, direction_up=_direction_up(alert))
    ctx = CONTEXT_HINTS.get(alert.indicator, "Watching for follow-through across markets.")
    parts = [opener]
    if rarity:
        parts.append(rarity)
    parts.append(ctx)
    return " ".join(parts)[:280]


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

    standout = is_emergency or any(
        _is_standout(a, histories.get(a.indicator, MoveHistory()), posting_cfg, is_emergency=is_emergency)
        for a in alerts
    )
    prefix = "🔥 " if standout and theme in ("crypto", "risk_on") else ("⚠️ " if standout else "")

    parts: list[str] = []
    used_verbs: set[str] = set()
    for a in sorted(alerts, key=lambda x: x.score, reverse=True)[:4]:
        hist = histories.get(a.indicator, MoveHistory())
        strong = a.alert_tier in ("major", "emergency")
        verb = _move_verb(a, strong=strong)
        while verb in used_verbs and len(used_verbs) < 4:
            verb = _move_verb(a, strong=not strong)
        used_verbs.add(verb.split()[0])
        val = _format_value(a)
        short = _display_name(a)
        parts.append(f"{short} {verb} ({val})")

    body = ", ".join(parts)
    implications = {
        "risk_on": "Breadth improving — risk appetite may be rotating back into growth assets.",
        "risk_off": "Defensive tone strengthening — could pressure equities and crypto.",
        "crypto": "Crypto momentum building — watching BTC dominance and equity correlation.",
        "inflation_pressure": "Inflation signals stacking — yields and the dollar may react.",
        "easing_conditions": "Easing tone building — liquidity-sensitive assets on watch.",
        "tightening_conditions": "Tightening pressure rising — multiples and crypto vulnerable.",
        "housing": "Housing indicators diverging — affordability vs demand in focus.",
    }
    context = implications.get(theme, "Cross-asset move with a coherent story — follow-through matters.")

    # Add rarity if top alert has it
    top = max(alerts, key=lambda x: x.score)
    top_hist = histories.get(top.indicator, MoveHistory())
    rarity = rarity_phrase(top_hist, direction_up=_direction_up(top))
    text = f"{prefix}{headline}: {body}. {context}"
    if rarity and len(text) + len(rarity) < 270:
        text = f"{prefix}{headline}: {body}. {rarity} {context}"

    return text[:280]