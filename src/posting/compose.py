from __future__ import annotations

from src.posting.models import AlertTrigger

THEME_HEADLINES: dict[str, str] = {
    "risk_on": "Risk-on move",
    "risk_off": "Risk-off shift",
    "crypto": "Crypto rally",
    "inflation_pressure": "Inflation pressure building",
    "disinflation": "Disinflation signal",
    "easing_conditions": "Easing conditions",
    "tightening_conditions": "Tightening conditions",
    "housing": "Housing stress",
    "equities": "Equity move",
}

CONTEXT_HINTS: dict[str, str] = {
    "btc": "Watching for follow-through into broader risk assets",
    "eth": "ETH strength often leads alt rotation — watching breadth",
    "sol": "SOL momentum can signal risk appetite in crypto",
    "sp500": "Equity tone matters for cross-asset flows",
    "nasdaq100": "Tech leadership often sets the risk tone",
    "vix": "Vol spike — watching whether equities absorb or amplify",
    "treasury_10y": "Rates move ripples through equities and crypto",
    "yield_curve": "Curve shape shift — growth vs recession pricing in play",
    "cpi_yoy": "Inflation print — watch real yields and Fed path",
    "dxy": "Dollar strength often pressures risk and commodities",
    "fear_greed": "Sentiment extreme — contrarian or confirmation signal",
    "mortgage_30y": "Mortgage rates feed directly into housing demand",
    "consumer_sentiment": "Consumer tone feeds spending and growth expectations",
    "oil": "Energy move feeds inflation expectations",
    "hy_spread": "Credit spreads are a real-time risk appetite gauge",
}


def _direction_word(alert: AlertTrigger) -> str:
    if alert.prev_value is None:
        return "moved"
    if alert.value > alert.prev_value:
        return "up"
    if alert.value < alert.prev_value:
        return "down"
    return "flat"


def _format_value(alert: AlertTrigger) -> str:
    v = alert.value
    if v >= 1000:
        return f"${v:,.0f}"
    if v >= 100:
        return f"{v:,.1f}"
    if abs(v) < 10:
        return f"{v:.2f}"
    return f"{v:.1f}"


def _primary_reason(alert: AlertTrigger) -> str:
    return alert.reasons[0] if alert.reasons else "threshold crossed"


def compose_single_tweet(alert: AlertTrigger) -> str:
    name = alert.name
    val = _format_value(alert)
    reason = _primary_reason(alert)
    direction = _direction_word(alert)
    hint = CONTEXT_HINTS.get(alert.indicator, "Watching for follow-through across markets")

    if "crossed above" in reason or "crossed below" in reason:
        opener = f"{name} {reason} — now {val}."
    elif direction in ("up", "down"):
        opener = f"{name} {direction} to {val} — {_primary_reason(alert)}."
    else:
        opener = f"{name} alert at {val} — {reason}."

    text = f"{opener} {hint}"
    return text[:280]


def compose_multi_tweet(alerts: list[AlertTrigger], theme: str | None = None) -> str:
    theme = theme or (alerts[0].themes[0] if alerts[0].themes else "markets")
    headline = THEME_HEADLINES.get(theme, "Market move")

    parts: list[str] = []
    for a in sorted(alerts, key=lambda x: x.score, reverse=True)[:4]:
        direction = _direction_word(a)
        val = _format_value(a)
        short = a.name.replace("S&P 500", "SPY").replace("NASDAQ 100", "QQQ")
        if direction in ("up", "down"):
            parts.append(f"{short} {direction} ({val})")
        else:
            parts.append(f"{short} at {val}")

    body = ", ".join(parts)
    hints = {
        "risk_on": "Risk appetite improving — watching whether it holds into the close.",
        "risk_off": "Defensive tone — watching credit and vol for confirmation.",
        "crypto": "Crypto breadth improving — watching BTC dominance and equity correlation.",
        "inflation_pressure": "Inflation signals stacking — watch yields and the dollar.",
        "easing_conditions": "Easing tone building — rates and risk assets worth watching together.",
        "tightening_conditions": "Tightening pressure — liquidity-sensitive assets on watch.",
        "housing": "Housing indicators diverging — affordability vs demand in focus.",
    }
    context = hints.get(theme, "Coherent cross-asset move — watching follow-through.")

    text = f"{headline}: {body}. {context}"
    return text[:280]