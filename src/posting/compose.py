"""Compose accessible X/Twitter alert text for financial market events.

Structure (max ~280 chars):
    - Accessible headline with key number
    - WHAT happened (plain English + data)
    - WHY / rarity (one factual context line; prefer time-based rarity when supported)
    - WHY IT MATTERS (natural → implication; evidence-based, not predictive)

Voice: thoughtful market analyst — varied phrasing, calm on small moves, sharper on rare ones.

Spec reference: prompts/post-enhancements2.md
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.posting.history import MoveHistory
from src.posting.models import AlertTrigger

_COMPOSE_CFG: dict[str, Any] | None = None


def _set_compose_cfg(cfg: dict[str, Any] | None) -> None:
    global _COMPOSE_CFG
    _COMPOSE_CFG = cfg

JARGON_TERMS = (
    "cross-exchange arb gap",
    "venue pricing",
    "arb gap",
    "less easy arb",
    "upside fuel",
    "downside fuel",
    "perp premium",
    "positioning reset underway",
)

GENERIC_IMPLICATION_PHRASES = (
    "market structure shifting",
    "monitor closely",
    "watch follow-through",
    "stay alert",
    "signal:",
    "takeaway:",
)

CONFIDENT_PHRASES = (
    "squeeze risk is building",
    "less forced selling pressure ahead",
    "buyers are in control",
    "bulls are in control",
    "bears are in control",
    "shorts take control",
    "is inevitable",
    "this confirms",
    "this means",
    "will move",
    "price should",
    "likely to",
)

MACRO_INDICATORS = {
    "cpi_yoy", "fed_funds", "unemployment", "jobless_claims",
    "pmi_manufacturing", "ism_services", "consumer_sentiment", "m2",
}

PRICE_INDICATORS = frozenset({
    "btc", "eth", "sol", "gold", "silver", "oil", "sp500", "nasdaq100",
})
RATES_VOL_INDICATORS = frozenset({
    "vix", "move", "hy_spread", "treasury_10y", "fed_funds", "yield_curve", "dxy",
})
HOUSING_INDICATORS = frozenset({"case_shiller", "mortgage_30y"})

RATE_PP_INDICATORS = frozenset({
    "cpi_yoy", "unemployment", "fed_funds", "treasury_10y", "mortgage_30y", "hy_spread", "yield_curve",
})
INDEX_DELTA_INDICATORS = frozenset({
    "consumer_sentiment", "pmi_manufacturing", "ism_services", "move", "vix", "case_shiller",
})
DIFFUSION_INDEX_INDICATORS = frozenset({"pmi_manufacturing", "ism_services"})
DIFFUSION_INDEX_LABELS: dict[str, str] = {
    "pmi_manufacturing": "Mfg diffusion index",
    "ism_services": "Services activity index",
}

SHORT_LABELS: dict[str, str] = {
    "btc": "BTC", "eth": "ETH", "sol": "SOL",
    "sp500": "S&P 500", "nasdaq100": "NASDAQ",
    "gold": "GOLD", "silver": "SILVER", "oil": "OIL",
    "vix": "VIX", "dxy": "DXY", "move": "MOVE",
    "treasury_10y": "10Y YIELD", "yield_curve": "YIELD CURVE",
    "hy_spread": "HY SPREAD", "mortgage_30y": "MORTGAGE RATE",
    "cpi_yoy": "CPI", "unemployment": "UNEMPLOYMENT",
    "jobless_claims": "JOBLESS CLAIMS", "consumer_sentiment": "CONSUMER SENTIMENT",
    "pmi_manufacturing": "MANUFACTURING", "ism_services": "SERVICES",
    "fed_funds": "FED FUNDS", "m2": "M2 MONEY",
    "case_shiller": "HOME PRICES", "fear_greed": "FEAR & GREED",
}

KEY_LEVEL_CROSS_INDICATORS = frozenset({"vix", "yield_curve", "cpi_yoy", "fed_funds"})
LIQ_LONG_PREFIX = "long_liq_usd:"
LIQ_SHORT_PREFIX = "short_liq_usd:"
LIQ_SKEW_THRESHOLD = 0.65

def _variant_index(alert: AlertTrigger, salt: str, count: int) -> int:
    if count <= 1:
        return 0
    bucket = int(abs(alert.value) * 1000) % 97
    key = f"{alert.indicator}:{salt}:{alert.timestamp.date()}:{bucket}"
    return sum(ord(c) for c in key) % count


def _pick_variant(alert: AlertTrigger, salt: str, variants: list[str]) -> str:
    return variants[_variant_index(alert, salt, len(variants))]


def _move_pct(alert: AlertTrigger, history: MoveHistory) -> float:
    if history.pct_change:
        return abs(history.pct_change)
    if alert.prev_value and alert.prev_value != 0:
        return abs((alert.value - alert.prev_value) / alert.prev_value * 100)
    return abs(alert.magnitude_pct or 0)


def _is_large_move(alert: AlertTrigger, history: MoveHistory) -> bool:
    if alert.alert_tier in ("major", "emergency"):
        return True
    if history.days_since_larger_move and history.days_since_larger_move >= 14:
        return True
    if history.is_all_time_high or history.is_largest_ytd:
        return True
    return _move_pct(alert, history) >= 15


def _asset_symbol(alert: AlertTrigger) -> str:
    return alert.indicator.split("_")[0].upper()


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

def _indicator_family(alert: AlertTrigger) -> str:
    ind = alert.indicator
    if ind.endswith("_liquidations"):
        return "liquidation"
    if ind.endswith("_funding"):
        return "funding"
    if ind.endswith("_basis"):
        return "basis"
    if ind.endswith("_exchange_spread"):
        return "exchange_spread"
    if ind.endswith("_open_interest"):
        return "open_interest"
    if ind == "fear_greed":
        return "sentiment"
    if ind in MACRO_INDICATORS or alert.is_macro:
        return "macro"
    if ind in PRICE_INDICATORS:
        return "price"
    if ind in RATES_VOL_INDICATORS:
        return "rates_vol"
    if ind in HOUSING_INDICATORS:
        return "housing"
    return "generic"


def _short_label(alert: AlertTrigger) -> str:
    ind = alert.indicator
    if ind.endswith(("_funding", "_basis", "_exchange_spread", "_liquidations")):
        return ind.split("_")[0].upper()
    return SHORT_LABELS.get(ind, _display_name(alert).upper())


def _move_verb(
    alert: AlertTrigger,
    *,
    strong: bool = False,
    history: MoveHistory | None = None,
) -> str:
    pct = abs((history.pct_change if history else 0) or alert.magnitude_pct or 0)
    major = strong or alert.alert_tier in ("major", "emergency")
    if _direction_up(alert):
        if major or pct >= 5:
            return "SURGE"
        if pct >= 2:
            return "JUMP"
        return "CLIMB"
    if major or pct >= 5:
        return "DROP"
    if pct >= 2:
        return "SLIP"
    return "FALL"


def _absolute_delta_suffix(alert: AlertTrigger, history: MoveHistory) -> str | None:
    if alert.prev_value is None:
        return None
    delta = history.abs_change if history.abs_change else alert.value - alert.prev_value
    if abs(delta) < 1e-12:
        return None
    sign = "+" if delta >= 0 else "-"
    ind = alert.indicator

    if ind in RATE_PP_INDICATORS:
        mag = abs(delta)
        if mag >= 0.01:
            return f"({sign}{mag:.2f} pp vs prior)"
        return f"({sign}{mag:.3f} pp vs prior)"

    if ind in INDEX_DELTA_INDICATORS:
        return f"({sign}{abs(delta):.1f} pts vs prior)"

    if ind == "jobless_claims":
        mag = abs(delta)
        if mag >= 1000:
            k = mag / 1000
            ktxt = f"{k:.0f}k" if k == int(k) else f"{k:.1f}k"
            return f"({sign}{ktxt} vs prior)"
        return f"({sign}{mag:,.0f} vs prior)"

    if ind == "m2":
        pct = abs(history.pct_change or alert.magnitude_pct)
        if pct > 0:
            return f"({_format_pct_line(pct, up=_direction_up(alert))} vs prior)"
        return f"({sign}{abs(delta):.2f} vs prior)"

    mag = abs(delta)
    if mag < 0.1:
        bps = mag * 100
        move = f"{bps:.0f} bps" if bps >= 1 else f"{mag:.2f} pp"
        return f"({sign}{move} vs prior)"
    return f"({sign}{mag:.2f} vs prior)"


def _format_level_extreme(phrase: str) -> str:
    if phrase.endswith("-day high."):
        return f"Highest reading in {phrase.split('-')[0]} days."
    if phrase.endswith("-day low."):
        return f"Lowest reading in {phrase.split('-')[0]} days."
    return phrase


def _format_implication(message: str) -> str:
    msg = message.strip().rstrip(".")
    lowered = msg.lower()
    for prefix in ("signal:", "takeaway:", "→ signal:", "→ takeaway:"):
        if lowered.startswith(prefix):
            msg = msg[len(prefix):].strip()
            lowered = msg.lower()
    return msg


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
        parts.append(f"→ {_format_implication(takeaway)}.")
    return "\n\n".join(parts)[:280]


@dataclass
class PostValidationResult:
    ok: bool
    issues: list[str] = field(default_factory=list)


def _post_implication_line(text: str) -> str | None:
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped.startswith("→"):
            return stripped
    return None


def _value_tokens_in_post(alert: AlertTrigger) -> list[str]:
    tokens = [_format_value(alert)]
    if alert.indicator.endswith("_funding"):
        tokens.append(_annualized_funding(alert.value))
        if alert.prev_value is not None:
            delta = alert.value - alert.prev_value
            tokens.append(f"{delta * 100:+.4f}%")
    if alert.indicator.endswith("_liquidations") and alert.value >= 1_000_000:
        tokens.append(_format_liq_usd(alert.value))
    if alert.prev_value is not None and alert.alert_unit == "percent":
        pct = abs((alert.value - alert.prev_value) / abs(alert.prev_value) * 100) if alert.prev_value else 0
        if pct > 0:
            tokens.append(f"{pct:.1f}%")
    return [t for t in tokens if t]


def validate_post_before_send(
    text: str,
    alert: AlertTrigger,
    *,
    chart_title: str | None = None,
    chart_latest_value: str | None = None,
) -> PostValidationResult:
    """Pre-post checks from prompts/post-enhancements.md."""
    issues: list[str] = []
    lowered = text.lower()

    if re.search(r"\b(signal|takeaway)\s*:", lowered):
        issues.append('generic "Signal:" or "Takeaway:" label found')

    for term in JARGON_TERMS:
        if term in lowered:
            issues.append(f"jargon without plain pairing: {term!r}")

    implication = _post_implication_line(text)
    if not implication:
        issues.append("missing implication line (→)")
    elif any(phrase in implication.lower() for phrase in GENERIC_IMPLICATION_PHRASES):
        issues.append("implication line too generic")
    elif any(phrase in lowered for phrase in CONFIDENT_PHRASES):
        issues.append("overly confident or predictive phrasing")

    if chart_title:
        family = _indicator_family(alert)
        title_lower = chart_title.lower()
        if family == "exchange_spread" and "coinbase" not in title_lower and "kraken" not in title_lower:
            issues.append("chart title does not match exchange-spread narrative")
        if family == "funding" and "funding" not in title_lower:
            issues.append("chart title does not match funding metric")
        if family == "basis" and "futures" not in title_lower and "spot" not in title_lower:
            issues.append("chart title does not match basis metric")
        if family == "liquidation" and "liquidation" not in title_lower:
            issues.append("chart title does not match liquidation metric")
        if family == "open_interest" and "open interest" not in title_lower:
            issues.append("chart title does not match open-interest metric")

    if chart_latest_value:
        formatted = _format_value(alert)
        if formatted not in text and chart_latest_value not in text:
            issues.append("latest chart value not reflected in post text")

    if not any(token in text for token in _value_tokens_in_post(alert)):
        issues.append("key metric value missing from post")

    return PostValidationResult(ok=not issues, issues=issues)


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
    """Return 'long', 'short', or 'mixed' for liquidation flushes."""
    for reason in alert.reasons:
        if reason.startswith("flush_type:"):
            return reason.removeprefix("flush_type:")
    long_usd, short_usd = _liq_breakdown(alert)
    if long_usd is None or short_usd is None:
        return None
    total = long_usd + short_usd
    if total <= 0:
        return None
    long_share = long_usd / total
    if long_share >= LIQ_SKEW_THRESHOLD:
        return "long"
    if short_usd / total >= LIQ_SKEW_THRESHOLD:
        return "short"
    return "mixed"


def _liq_breakdown(alert: AlertTrigger) -> tuple[float | None, float | None]:
    hydrate_liq_from_reasons(alert)
    return alert.liq_long_usd, alert.liq_short_usd


def _crypto_rarity_line(alert: AlertTrigger, history: MoveHistory) -> str | None:
    """One strong time-based context line when historical data supports it."""
    family = _indicator_family(alert)
    asset = _asset_symbol(alert)

    if history.liquidation_rank:
        return history.liquidation_rank.rstrip(".")

    if history.is_all_time_high and family == "open_interest":
        return "Highest open interest on record."

    if history.level_extreme:
        phrase = _format_level_extreme(history.level_extreme).rstrip(".")
        if family == "funding":
            return phrase.replace("Highest reading", "Highest funding").replace("Lowest reading", "Lowest funding")
        if family == "basis":
            return phrase.replace("Highest reading", "Widest futures premium").replace("Lowest reading", "Narrowest futures premium")
        if family == "open_interest":
            return phrase.replace("Highest reading", "Highest open interest").replace("Lowest reading", "Lowest open interest")
        if family == "exchange_spread":
            compressing = alert.prev_value is not None and alert.value < alert.prev_value
            widening = alert.prev_value is not None and alert.value > alert.prev_value
            if compressing and "low" in phrase.lower():
                return phrase.replace("Lowest reading", "Tightest Coinbase-Kraken gap")
            if widening and "high" in phrase.lower():
                return phrase.replace("Highest reading", "Widest Coinbase-Kraken gap")
        return phrase

    days = history.days_since_larger_move
    if not days or days < 7:
        if history.is_largest_ytd and history.ytd_move_count >= 5 and family == "liquidation":
            return "Biggest liquidation spike this year."
        return None

    if family == "liquidation":
        return f"Biggest spike in {days} days."
    if family == "funding":
        if alert.value < -0.00001:
            if days >= 14 and days % 7 == 0:
                w = days // 7
                unit = "week" if w == 1 else "weeks"
                return f"Most negative funding in {w} {unit}."
            return f"Most negative funding in {days} days."
        return f"Biggest funding move in {days} days."
    if family == "basis":
        return f"Largest futures-spot move in {days} days."
    if family == "open_interest":
        return f"Largest open-interest move in {days} days."
    if family == "exchange_spread":
        compressing = alert.prev_value is not None and alert.value < alert.prev_value
        widening = alert.prev_value is not None and alert.value > alert.prev_value
        if compressing:
            return f"Sharpest compression in {days} days."
        if widening:
            return f"Widest Coinbase-Kraken gap in {days} days."
        return f"Largest spread move in {days} days."
    return None


def _crypto_why_line(alert: AlertTrigger, history: MoveHistory) -> str | None:
    """Factual context that adds information beyond the headline."""
    family = _indicator_family(alert)
    if family == "exchange_spread":
        compressing = alert.prev_value is not None and alert.value < alert.prev_value
        widening = alert.prev_value is not None and alert.value > alert.prev_value
        asset = _asset_symbol(alert)
        if compressing:
            return _pick_variant(alert, "spread-why-down", [
                "Coinbase and Kraken moved closer together.",
                f"The two venues are pricing {asset} more similarly than before.",
            ])
        if widening:
            return _pick_variant(alert, "spread-why-up", [
                "Coinbase and Kraken moved farther apart.",
                f"The two venues are quoting {asset} wider apart than before.",
            ])
        return None
    if family == "funding":
        side = _funding_side(alert)
        if side == "short":
            return _pick_variant(alert, "fund-why-neg", [
                "Funding has flipped below zero, with shorts now paying longs.",
                "Shorts are paying longs to keep positions open.",
            ])
        if side == "long":
            return _pick_variant(alert, "fund-why-pos", [
                "Longs are paying elevated rates to hold positions.",
                "Longs continue to pay shorts at a higher rate.",
            ])
        return _pick_variant(alert, "fund-why-neutral", [
            "Funding has settled back near zero.",
            "Carry costs have moved back toward neutral.",
        ])
    if family == "liquidation":
        skew = _liq_skew(alert)
        long_usd, short_usd = _liq_breakdown(alert)
        if skew == "long" and long_usd is not None and short_usd is not None:
            return "Longs made up most of the hourly total."
        if skew == "short" and long_usd is not None and short_usd is not None:
            return "Shorts made up most of the hourly total."
        if skew == "long":
            return "Forced closes hit long positions during the selloff."
        if skew == "short":
            return "Forced closes hit short positions during the rally."
        if skew == "mixed":
            return "Both sides were liquidated in the same hour."
        return "The hourly total ran well above recent readings."
    if family == "basis":
        if alert.value < 0:
            return _pick_variant(alert, "basis-why-neg", [
                "Futures flipped to a discount after trading above spot.",
                "Perps are now priced below the cash market.",
            ])
        if alert.prev_value is not None and alert.value > alert.prev_value:
            return "Futures are trading farther above spot than before."
        if alert.prev_value is not None and alert.value < alert.prev_value:
            return "The premium over spot has come in from prior levels."
        return None
    if family == "open_interest":
        if _direction_up(alert):
            return _pick_variant(alert, "oi-why-up", [
                "Traders are adding leverage.",
                "New positions are outpacing closes.",
            ])
        return _pick_variant(alert, "oi-why-down", [
            "Traders are reducing open positions.",
            "Open positions are being closed faster than they open.",
        ])
    return None


def _has_crypto_rarity(alert: AlertTrigger, history: MoveHistory) -> bool:
    return _crypto_rarity_line(alert, history) is not None


def _memory_context_line(alert: AlertTrigger) -> str | None:
    if _COMPOSE_CFG is None:
        return None
    try:
        from src.market_memory_bridge import memory_context_line

        return memory_context_line(alert, _COMPOSE_CFG)
    except Exception:
        return None


def _crypto_context_line(alert: AlertTrigger, history: MoveHistory) -> str | None:
    rarity = _crypto_rarity_line(alert, history)
    if rarity:
        return rarity
    memory_line = _memory_context_line(alert)
    if memory_line:
        return memory_line
    return _crypto_why_line(alert, history)


def _liquidation_headline(alert: AlertTrigger, history: MoveHistory) -> str:
    asset = _asset_symbol(alert)
    skew = _liq_skew(alert)
    large = _is_large_move(alert, history)
    if skew == "long":
        if large:
            return _pick_variant(alert, "liq-h-long", [
                f"{asset} long liquidations spike",
                f"{asset} long flush",
                f"{asset} long-side liquidations jump",
            ])
        return f"{asset} long liquidations rise"
    if skew == "short":
        if large:
            return _pick_variant(alert, "liq-h-short", [
                f"{asset} short liquidations spike",
                f"{asset} short flush",
                f"{asset} short-side liquidations jump",
            ])
        return f"{asset} short liquidations rise"
    if skew == "mixed":
        return f"{asset} liquidations spike"
    return f"{asset} liquidations rise"


def _data_lines_for_liquidation(alert: AlertTrigger, history: MoveHistory) -> list[str]:
    long_usd, short_usd = _liq_breakdown(alert)
    pct = abs(history.pct_change or alert.magnitude_pct) if alert.prev_value is not None else 0.0
    total_line = f"{_format_liq_usd(alert.value)} liquidations (1H)"
    if pct > 0:
        total_line += f" ({_format_pct_line(pct, up=_direction_up(alert))})"
    lines = [total_line]
    if long_usd is not None and short_usd is not None:
        lines.append(
            f"Longs: {_format_liq_usd(long_usd, precision=0)} | "
            f"Shorts: {_format_liq_usd(short_usd, precision=0)}"
        )
    elif pct > 0:
        lines.append(_format_pct_line(pct, up=_direction_up(alert)))
    return lines


def _liquidation_takeaway(alert: AlertTrigger, history: MoveHistory | None = None) -> str:
    skew = _liq_skew(alert)
    if skew == "long":
        return _pick_variant(alert, "liq-t-long", [
            "Long positioning has been reduced.",
            "A large share of long leverage was cleared.",
            "Forced selling removed crowded long exposure.",
        ])
    if skew == "short":
        return _pick_variant(alert, "liq-t-short", [
            "Short positioning has been reduced.",
            "A large share of short leverage was cleared.",
            "Forced buying removed crowded short exposure.",
        ])
    if skew == "mixed":
        return _pick_variant(alert, "liq-t-mixed", [
            "Leverage was cleared on both sides.",
            "Two-way liquidations may keep volatility elevated.",
        ])
    return "Market leverage was reduced in the move."


FUNDING_PERIODS_PER_YEAR = 1095  # 3 x 8h funding windows per day


def _funding_side(alert: AlertTrigger) -> str:
    """Return 'long', 'short', or 'neutral' based on who pays funding."""
    if alert.value < -0.00001:
        return "short"
    if alert.value > 0.00003:
        return "long"
    for reason in alert.reasons:
        if reason.startswith("below"):
            return "short"
        if reason.startswith("above"):
            return "long"
    return "neutral"


def _funding_headline(alert: AlertTrigger, history: MoveHistory) -> str:
    asset = _asset_symbol(alert)
    side = _funding_side(alert)
    large = _is_large_move(alert, history)
    if side == "short":
        return _pick_variant(alert, "fund-h-neg", [
            f"{asset} funding turns negative",
            f"{asset} funding drops below zero",
            f"{asset} funding flips negative",
        ])
    if side == "long":
        if large:
            return _pick_variant(alert, "fund-h-pos", [
                f"{asset} funding rises",
                f"{asset} funding climbs",
                f"{asset} funding moves higher",
            ])
        return f"{asset} funding turns positive"
    return _pick_variant(alert, "fund-h-neutral", [
        f"{asset} funding resets near zero",
        f"{asset} funding returns to neutral",
    ])


def _annualized_funding(rate: float) -> str:
    return f"{rate * FUNDING_PERIODS_PER_YEAR * 100:+.1f}%"


def _funding_rate_delta(alert: AlertTrigger, history: MoveHistory) -> str | None:
    if alert.prev_value is None:
        return None
    delta = history.abs_change if history.abs_change else alert.value - alert.prev_value
    if abs(delta) < 1e-8:
        return None
    sign = "+" if delta >= 0 else "-"
    return f"{sign}{abs(delta) * 100:.4f}% vs prior"


def _data_lines_for_funding(alert: AlertTrigger, history: MoveHistory) -> list[str]:
    rate_line = f"{_format_value(alert)} per 8h"
    delta = _funding_rate_delta(alert, history)
    if delta:
        rate_line += f" ({delta})"
    return [
        rate_line,
        f"Annualized: {_annualized_funding(alert.value)}",
    ]


def _funding_takeaway(alert: AlertTrigger, history: MoveHistory | None = None) -> str:
    side = _funding_side(alert)
    if side == "short":
        return _pick_variant(alert, "fund-t-neg", [
            "Shorts are becoming the more crowded side.",
            "Positioning has shifted below neutral.",
            "The market is leaning more short.",
        ])
    if side == "long":
        return _pick_variant(alert, "fund-t-pos", [
            "Longs are becoming the more crowded side.",
            "Traders are increasingly positioned long.",
            "Positioning is more one-sided toward longs.",
        ])
    return _pick_variant(alert, "fund-t-neutral", [
        "Positioning looks more balanced.",
        "Carry pressure is less one-sided.",
    ])


def _basis_headline(alert: AlertTrigger, history: MoveHistory) -> str:
    asset = _asset_symbol(alert)
    if alert.value < 0:
        return _pick_variant(alert, "basis-h-neg", [
            f"{asset} futures fall below spot",
            f"{asset} futures trade at a discount",
        ])
    if alert.prev_value is not None and alert.value > alert.prev_value:
        return _pick_variant(alert, "basis-h-up", [
            f"{asset} futures premium widens",
            f"{asset} futures pull above spot",
        ])
    if alert.prev_value is not None and alert.value < alert.prev_value:
        return _pick_variant(alert, "basis-h-down", [
            f"{asset} futures premium narrows",
            f"{asset} futures premium compresses",
        ])
    return f"{asset} futures vs spot moves"


def _data_lines_for_basis(alert: AlertTrigger, history: MoveHistory) -> list[str]:
    line = f"Futures vs spot: {_format_value(alert)}"
    if alert.prev_value is not None:
        delta = history.abs_change if history.abs_change else alert.value - alert.prev_value
        sign = "+" if delta >= 0 else "-"
        line += f" ({sign}{abs(delta):.1f} bps vs prior)"
    return [line]


def _basis_takeaway(alert: AlertTrigger, history: MoveHistory | None = None) -> str:
    history = history or MoveHistory()
    if alert.value < 0:
        return _pick_variant(alert, "basis-t-neg", [
            "Spot is trading at a premium to futures.",
            "Cash prices sit above perps.",
        ])
    if _direction_up(alert):
        if _has_crypto_rarity(alert, history):
            return _pick_variant(alert, "basis-t-up-rare", [
                "Futures demand strengthened relative to spot.",
                "Buyers are paying more to hold futures vs spot.",
            ])
        return _pick_variant(alert, "basis-t-up", [
            "Futures demand strengthened relative to spot.",
            "Buyers are paying a larger premium over spot.",
        ])
    return _pick_variant(alert, "basis-t-down", [
        "Futures demand cooled relative to spot.",
        "Traders are paying less to hold futures vs spot.",
        "The futures premium over spot compressed.",
    ])


def _open_interest_headline(alert: AlertTrigger, history: MoveHistory) -> str:
    asset = _asset_symbol(alert)
    if history.is_all_time_high:
        return _pick_variant(alert, "oi-h-ath", [
            f"{asset} open interest reaches a new high",
            f"{asset} open interest hits a record",
        ])
    if alert.prev_value is not None:
        if _direction_up(alert):
            return _pick_variant(alert, "oi-h-up", [
                f"{asset} open interest rises",
                f"{asset} open interest builds",
            ])
        return _pick_variant(alert, "oi-h-down", [
            f"{asset} open interest falls",
            f"{asset} open interest declines",
        ])
    return f"{asset} open interest moves"


def _data_lines_for_open_interest(alert: AlertTrigger, history: MoveHistory) -> list[str]:
    line = f"Open interest: {_format_value(alert)}"
    if alert.prev_value is not None:
        pct = abs(history.pct_change or alert.magnitude_pct)
        if pct > 0:
            line += f" ({_format_pct_line(pct, up=_direction_up(alert))} vs prior)"
    return [line]


def _open_interest_takeaway(alert: AlertTrigger, history: MoveHistory | None = None) -> str:
    history = history or MoveHistory()
    if _direction_up(alert):
        if history.is_all_time_high:
            return _pick_variant(alert, "oi-t-ath", [
                "More leverage is in the market than at any prior peak.",
                "Total outstanding exposure has never been higher.",
            ])
        return _pick_variant(alert, "oi-t-up", [
            "More leverage is entering the market.",
            "Traders are adding exposure.",
            "Open positions continue to build.",
        ])
    return _pick_variant(alert, "oi-t-down", [
        "Leverage is coming out of the market.",
        "Traders are cutting exposure.",
        "Open positions are declining.",
    ])


def _sentiment_headline(alert: AlertTrigger) -> str:
    if alert.value <= 25:
        return "EXTREME FEAR"
    if alert.value >= 75:
        return "EXTREME GREED"
    return "SENTIMENT SHIFT"


def _data_lines_for_sentiment(alert: AlertTrigger, history: MoveHistory) -> list[str]:
    line = f"Index: {int(round(alert.value))}"
    if alert.reasons:
        line += f" ({alert.reasons[0]})"
    return [line]


def _sentiment_context(alert: AlertTrigger, history: MoveHistory) -> str | None:
    if alert.value <= 25:
        return "Fear & Greed at capitulation levels."
    if alert.value >= 75:
        return "Fear & Greed at euphoria levels."
    if history.days_since_larger_move and history.days_since_larger_move >= 14:
        return f"Largest sentiment shift in {history.days_since_larger_move} days."
    return "Sentiment crossed a key threshold."


def _sentiment_takeaway(alert: AlertTrigger) -> str:
    if alert.value <= 25:
        return "defensive demand rising"
    if alert.value >= 75:
        return "positioning stretched to one side"
    return "risk appetite repricing"


def _macro_headline(alert: AlertTrigger, history: MoveHistory) -> str:
    up = _direction_up(alert)
    ind = alert.indicator
    if ind == "cpi_yoy":
        return "CPI HOT" if up else "CPI COOLING"
    if ind == "jobless_claims":
        return "CLAIMS SPIKE" if up else "CLAIMS DROP"
    if ind == "unemployment":
        return "UNEMPLOYMENT RISE" if up else "UNEMPLOYMENT FALL"
    if ind == "consumer_sentiment":
        return "SENTIMENT WEAK" if not up else "SENTIMENT STRONG"
    if ind == "pmi_manufacturing":
        return "MANUFACTURING WEAK" if not up else "MANUFACTURING STRONG"
    if ind == "ism_services":
        return "SERVICES WEAK" if not up else "SERVICES STRONG"
    if ind == "m2":
        return "M2 EXPANSION" if up else "M2 SLOWDOWN"
    if ind == "fed_funds":
        return "FED RATE RISE" if up else "FED RATE CUT"
    return f"{_short_label(alert)} {_move_verb(alert, strong=alert.alert_tier in ('major', 'emergency'), history=history)}"


def _rates_headline(alert: AlertTrigger, history: MoveHistory) -> str:
    ind = alert.indicator
    cross = _cross_direction(alert)
    if ind == "yield_curve":
        if cross == "below":
            return "YIELD CURVE INVERTS"
        if cross == "above":
            return "YIELD CURVE UNINVERTS"
    if ind == "vix":
        if cross == "above" or alert.value >= 30:
            return "VIX SPIKE"
        if cross == "below":
            return "VIX CALM"
    if ind == "hy_spread":
        if cross == "above" or alert.value >= 5:
            return "CREDIT STRESS"
        return f"HY SPREAD {_move_verb(alert, strong=alert.alert_tier in ('major', 'emergency'), history=history)}"
    if ind == "move":
        return f"BOND VOL {_move_verb(alert, strong=True, history=history)}"
    if ind == "treasury_10y":
        return f"YIELDS {_move_verb(alert, strong=alert.alert_tier in ('major', 'emergency'), history=history)}"
    if ind == "dxy":
        return f"DOLLAR {_move_verb(alert, strong=alert.alert_tier in ('major', 'emergency'), history=history)}"
    if ind == "fed_funds":
        return _macro_headline(alert, history)
    return f"{_short_label(alert)} {_move_verb(alert, strong=alert.alert_tier in ('major', 'emergency'), history=history)}"


def _event_headline(alert: AlertTrigger, history: MoveHistory) -> str:
    if history.is_all_time_high:
        return f"{_short_label(alert)} RECORD HIGH"
    if alert.indicator in MACRO_INDICATORS or alert.is_macro:
        return _macro_headline(alert, history)
    if alert.indicator in RATES_VOL_INDICATORS:
        return _rates_headline(alert, history)
    if alert.indicator in HOUSING_INDICATORS:
        return f"{_short_label(alert)} {_move_verb(alert, strong=alert.alert_tier in ('major', 'emergency'), history=history)}"
    return f"{_short_label(alert)} {_move_verb(alert, strong=alert.alert_tier in ('major', 'emergency'), history=history)}"


def _exchange_spread_headline(alert: AlertTrigger, history: MoveHistory) -> str:
    asset = _asset_symbol(alert)
    if alert.prev_value is not None:
        if alert.value < alert.prev_value:
            return _pick_variant(alert, "spread-h-down", [
                f"{asset} exchange prices converge",
                f"{asset} venue prices align",
                f"{asset} exchange gap compresses",
            ])
        if alert.value > alert.prev_value:
            return _pick_variant(alert, "spread-h-up", [
                f"{asset} exchange prices diverge",
                f"{asset} venue prices drift apart",
                f"{asset} exchange gap widens",
            ])
    return f"{asset} exchange spread moves"


def _data_lines_for_exchange_spread(alert: AlertTrigger, history: MoveHistory) -> list[str]:
    line = f"Price gap: {_format_value(alert)} (Coinbase vs Kraken)"
    if alert.prev_value is not None:
        pct = abs(history.pct_change or alert.magnitude_pct)
        if pct > 0:
            line += f" ({_format_pct_line(pct, up=_direction_up(alert))} vs prior)"
    return [line]


def _exchange_spread_takeaway(alert: AlertTrigger, history: MoveHistory | None = None) -> str:
    history = history or MoveHistory()
    compressing = alert.prev_value is not None and alert.value < alert.prev_value
    widening = alert.prev_value is not None and alert.value > alert.prev_value
    if compressing:
        if _has_crypto_rarity(alert, history):
            return _pick_variant(alert, "spread-t-down-rare", [
                "Less room for cross-exchange arbitrage.",
                "Arbitrage spreads have compressed.",
            ])
        return _pick_variant(alert, "spread-t-down", [
            "Less room for cross-exchange arbitrage.",
            "Pricing is more aligned across exchanges.",
        ])
    if widening:
        if _has_crypto_rarity(alert, history):
            return _pick_variant(alert, "spread-t-up-rare", [
                "Price differences between exchanges are widening.",
                "More room for cross-exchange arbitrage.",
            ])
        return _pick_variant(alert, "spread-t-up", [
            "Price differences between exchanges are widening.",
            "Arbitrage room is widening.",
        ])
    return "Cross-exchange pricing is shifting."


def _data_lines_for_diffusion_index(alert: AlertTrigger, history: MoveHistory) -> list[str]:
    label = DIFFUSION_INDEX_LABELS[alert.indicator]
    line = f"{label}: {alert.value:.1f} (0 = breakeven)"
    if alert.prev_value is not None:
        suffix = _absolute_delta_suffix(alert, history)
        if suffix:
            line += f" {suffix}"
    return [line]


def _data_lines_for_alert(alert: AlertTrigger, history: MoveHistory) -> list[str]:
    if alert.indicator.endswith("_liquidations"):
        return _data_lines_for_liquidation(alert, history)
    if alert.indicator.endswith("_funding"):
        return _data_lines_for_funding(alert, history)
    if alert.indicator.endswith("_basis"):
        return _data_lines_for_basis(alert, history)
    if alert.indicator == "fear_greed":
        return _data_lines_for_sentiment(alert, history)
    if alert.indicator.endswith("_exchange_spread"):
        return _data_lines_for_exchange_spread(alert, history)
    if alert.indicator.endswith("_open_interest"):
        return _data_lines_for_open_interest(alert, history)
    if alert.indicator in DIFFUSION_INDEX_INDICATORS:
        return _data_lines_for_diffusion_index(alert, history)
    lines: list[str] = []
    if (
        alert.indicator in PRICE_INDICATORS
        and alert.alert_unit == "percent"
        and alert.prev_value is not None
    ):
        pct = abs(history.pct_change or alert.magnitude_pct)
        if pct > 0:
            lines.append(_format_pct_line(pct, up=_direction_up(alert)))
    if not lines:
        lines = [_format_value(alert)]
    if alert.alert_unit == "percent" and alert.prev_value is not None and alert.indicator not in PRICE_INDICATORS:
        pct = abs(history.pct_change or alert.magnitude_pct)
        if pct > 0:
            lines[0] = f"{lines[0]} ({_format_pct_line(pct, up=_direction_up(alert))})"
    elif alert.alert_unit == "absolute" and alert.prev_value is not None:
        if alert.indicator.endswith("_basis"):
            delta = history.abs_change
            sign = "+" if _direction_up(alert) else "-"
            lines[0] = f"{lines[0]} ({sign}{abs(delta):.1f} bps vs prior)"
        else:
            suffix = _absolute_delta_suffix(alert, history)
            if suffix:
                lines[0] = f"{lines[0]} {suffix}"
    return lines


def _headline_name(alert: AlertTrigger, *, major: bool, history: MoveHistory | None = None) -> str:
    history = history or MoveHistory()
    if alert.indicator.endswith("_liquidations"):
        return _liquidation_headline(alert, history)
    if alert.indicator.endswith("_funding"):
        return _funding_headline(alert, history)
    if alert.indicator.endswith("_basis"):
        return _basis_headline(alert, history)
    if alert.indicator.endswith("_open_interest"):
        return _open_interest_headline(alert, history)
    if alert.indicator.endswith("_exchange_spread"):
        return _exchange_spread_headline(alert, history)
    if alert.indicator == "fear_greed":
        return _sentiment_headline(alert)
    return _event_headline(alert, history)


def _headline_for_alert(
    alert: AlertTrigger,
    *,
    major: bool,
    emoji: str,
    history: MoveHistory | None = None,
) -> str:
    history = history or MoveHistory()
    if alert.indicator.endswith("_liquidations"):
        return f"{emoji}{_liquidation_headline(alert, history)}".strip()
    if alert.indicator.endswith("_funding"):
        return f"{emoji}{_funding_headline(alert, history)}".strip()
    if alert.indicator.endswith("_basis"):
        return f"{emoji}{_basis_headline(alert, history)}".strip()
    if alert.indicator.endswith("_open_interest"):
        return f"{emoji}{_open_interest_headline(alert, history)}".strip()
    if alert.indicator.endswith("_exchange_spread"):
        return f"{emoji}{_exchange_spread_headline(alert, history)}".strip()
    if alert.indicator == "fear_greed":
        return f"{emoji}{_sentiment_headline(alert)}".strip()
    if history.is_all_time_high:
        return f"{emoji}{_headline_name(alert, major=False, history=history)}".strip()
    name = _headline_name(alert, major=major, history=history)
    body = f"MAJOR MOVE: {name}" if major and alert.alert_tier == "emergency" else name
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
    if history.level_extreme:
        return _format_level_extreme(history.level_extreme)
    cross = _cross_context_line(alert, history)
    if cross:
        return cross
    if history.days_since_larger_move and history.days_since_larger_move >= 14:
        if alert.indicator in MACRO_INDICATORS or alert.is_macro:
            word = "increase" if up else "decline"
            return f"Largest macro {word} in {history.days_since_larger_move} days."
        word = "gain" if up else "decline"
        return f"Largest {word} in {history.days_since_larger_move} days."
    if history.days_since_larger_move and history.days_since_larger_move >= 7:
        if alert.indicator in MACRO_INDICATORS or alert.is_macro:
            word = "increase" if up else "decline"
            return f"Largest macro {word} in {history.days_since_larger_move} days."
        word = "gain" if up else "decline"
        return f"Largest {word} in {history.days_since_larger_move} days."
    if history.is_largest_ytd and history.ytd_move_count >= 5:
        return "Largest move of the year."
    if history.is_largest_ytd and history.ytd_move_count >= 2:
        return "Largest move tracked this year."
    if history.liquidation_rank:
        return history.liquidation_rank
    if alert.alert_tier == "emergency":
        return "Largest move vs recent baseline."
    return _context_fallback(alert, history)


def _macro_context_fallback(alert: AlertTrigger, history: MoveHistory) -> str:
    up = _direction_up(alert)
    ind = alert.indicator
    paired: dict[str, tuple[str, str]] = {
        "unemployment": (
            "Unemployment ticked higher — largest rise in months.",
            "Unemployment eased — labor market firming.",
        ),
        "jobless_claims": (
            "Claims spiked — largest weekly jump in months.",
            "Claims fell — layoff pressure easing.",
        ),
        "cpi_yoy": (
            "CPI reading heated — inflation pressure building.",
            "CPI reading cooled — disinflation signal.",
        ),
        "consumer_sentiment": (
            "Sentiment strengthened — consumer outlook improving.",
            "Sentiment weakened — consumer outlook deteriorating.",
        ),
        "pmi_manufacturing": (
            "Manufacturing expanded — activity above breakeven.",
            "Manufacturing contracted — activity below breakeven.",
        ),
        "ism_services": (
            "Services activity expanded — growth signal improving.",
            "Services activity contracted — growth signal weakening.",
        ),
        "fed_funds": (
            "Fed funds rate moved higher — policy tightening.",
            "Fed funds rate moved lower — policy easing.",
        ),
        "m2": (
            "M2 expanded — liquidity conditions loosening.",
            "M2 slowed — liquidity conditions tightening.",
        ),
    }
    if ind in paired:
        return paired[ind][0 if up else 1]
    if history.days_since_larger_move and history.days_since_larger_move >= 7:
        word = "increase" if up else "decline"
        return f"Largest macro {word} in {history.days_since_larger_move} days."
    return "Macro release stands out vs recent readings."


def _context_fallback(alert: AlertTrigger, history: MoveHistory) -> str | None:
    ind = alert.indicator
    if ind in PRICE_INDICATORS:
        pct = abs(history.pct_change or alert.magnitude_pct)
        if pct >= 3:
            return "Sharp move vs recent sessions."
        return "Notable move vs recent trading range."
    if ind in RATES_VOL_INDICATORS:
        if history.days_since_larger_move and history.days_since_larger_move >= 7:
            return f"Largest rate/vol move in {history.days_since_larger_move} days."
        return "Rate and volatility conditions shifting."
    if ind in MACRO_INDICATORS or alert.is_macro:
        return _macro_context_fallback(alert, history)
    if ind in HOUSING_INDICATORS:
        if history.days_since_larger_move and history.days_since_larger_move >= 7:
            word = "gain" if _direction_up(alert) else "decline"
            return f"Largest housing {word} in {history.days_since_larger_move} days."
        return "Housing market repricing."
    if ind.endswith(("_basis", "_funding", "_exchange_spread", "_open_interest", "_liquidations")):
        return _crypto_context_line(alert, history)
    if ind == "fear_greed":
        return _sentiment_context(alert, history)
    return "Move stands out vs recent baseline."


def _takeaway_for_alert(alert: AlertTrigger, history: MoveHistory | None = None) -> str:
    history = history or MoveHistory()
    ind = alert.indicator
    up = _direction_up(alert)
    if history.is_all_time_high:
        if ind in ("gold", "silver"):
            return "defensive demand remains strong"
        if ind in ("btc", "eth", "sol", "sp500", "nasdaq100"):
            return "improving risk appetite"
        return "momentum building at new highs"
    paired: dict[str, tuple[str, str]] = {
        "btc": ("improving risk appetite across crypto", "selling pressure building across crypto"),
        "eth": ("alt momentum building", "alt weakness spreading"),
        "sol": ("high-beta risk appetite improving", "high-beta risk appetite fading"),
        "sp500": ("improving risk appetite", "fading risk appetite"),
        "nasdaq100": ("growth risk appetite improving", "growth risk appetite fading"),
        "gold": ("defensive demand rising", "defensive demand easing"),
        "silver": ("precious metals demand firming", "precious metals demand fading"),
        "oil": ("inflation pressure rising via energy", "demand concerns rising via energy"),
        "vix": ("volatility expanding — pressure on equity multiples", "volatility contracting — risk appetite may improve"),
        "dxy": ("dollar strength tightening global conditions", "dollar weakness easing global conditions"),
        "treasury_10y": ("borrowing costs rising — rippling through risk assets", "borrowing costs falling — conditions may ease"),
        "hy_spread": ("credit stress building — liquidity may be tightening", "credit stress easing — liquidity may improve"),
        "move": ("bond volatility expanding — rate uncertainty rising", "bond volatility contracting — rate stability improving"),
        "mortgage_30y": ("housing affordability worsening", "housing affordability improving"),
        "case_shiller": ("home price growth accelerating", "home price growth cooling"),
        "cpi_yoy": ("inflation pressure narrative heating up", "inflation pressure narrative cooling"),
        "unemployment": ("labor market softening", "labor market strengthening"),
        "jobless_claims": ("labor data cooling", "labor data firming"),
        "consumer_sentiment": ("consumer confidence strengthening", "consumer confidence weakening"),
        "pmi_manufacturing": ("manufacturing growth expectations improving", "manufacturing growth expectations weakening"),
        "ism_services": ("services growth expectations improving", "services growth expectations weakening"),
        "fed_funds": ("financial conditions tightening", "financial conditions easing"),
        "m2": ("liquidity expanding", "liquidity tightening"),
    }
    if ind in paired:
        return paired[ind][0 if up else 1]
    cross = _cross_direction(alert)
    if ind == "yield_curve":
        if cross == "below":
            return "growth expectations weakening"
        if cross == "above":
            return "growth expectations improving"
        return "growth expectations in flux"
    if ind.endswith("_basis"):
        return _basis_takeaway(alert, history)
    if ind == "fear_greed":
        return _sentiment_takeaway(alert)
    if ind.endswith("_funding"):
        return _funding_takeaway(alert, history)
    if ind.endswith("_exchange_spread"):
        return _exchange_spread_takeaway(alert, history)
    if ind.endswith("_open_interest"):
        return _open_interest_takeaway(alert, history)
    if ind.endswith("_liquidations"):
        return _liquidation_takeaway(alert, history)
    return "conditions shifted vs the recent baseline"


def _takeaway_line(alert: AlertTrigger, history: MoveHistory | None = None) -> str:
    return _takeaway_for_alert(alert, history)


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
    if alert.indicator.endswith("_open_interest"):
        if v >= 1_000_000_000:
            return f"${v / 1_000_000_000:.2f}B"
        if v >= 1_000_000:
            return f"${v / 1_000_000:.1f}M"
        return f"${v:,.0f}"
    if alert.indicator in ("cpi_yoy", "unemployment", "fed_funds", "treasury_10y", "mortgage_30y"):
        return f"{v:.2f}%"
    if alert.indicator == "jobless_claims":
        if v >= 1000:
            k = v / 1000
            ktxt = f"{k:.0f}k" if k == int(k) else f"{k:.1f}k"
            return f"{ktxt} claims"
        return f"{int(v)} claims"
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


def _cross_context_line(alert: AlertTrigger, history: MoveHistory | None = None) -> str | None:
    history = history or MoveHistory()
    direction = _cross_direction(alert)
    if not direction:
        return None
    if alert.indicator == "yield_curve":
        base = "Uninverted." if direction == "above" else "Inverted."
    else:
        bound = next((r.split()[-1] for r in alert.reasons if "crossed" in r), None)
        if bound:
            word = "above" if direction == "above" else "below"
            base = f"Crossed {word} {bound}."
        else:
            base = "Key level break."
    if history.days_since_larger_move and history.days_since_larger_move >= 7:
        return f"{base.rstrip('.')} — largest move in {history.days_since_larger_move} days."
    return base


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
        "headline": _headline_for_alert(alert, major=False, emoji=resolved_emoji, history=history),
        "data_lines": _data_lines_for_alert(alert, history),
        "context": _cross_context_line(alert, history),
        "takeaway": _takeaway_line(alert, history),
    }


def _multi_data_lines_for_alert(alert: AlertTrigger, history: MoveHistory) -> list[str]:
    """Compact labeled data lines — no event headline repeat (multi headline is separate)."""
    ind = alert.indicator
    base = _short_label(alert)
    if ind.endswith("_funding"):
        label = f"{base} funding"
    elif ind.endswith("_basis"):
        label = f"{base} basis"
    elif ind.endswith("_exchange_spread"):
        label = f"{base} spread"
    elif ind.endswith("_open_interest"):
        label = f"{base} OI"
    elif ind.endswith("_liquidations"):
        label = f"{base} liqs"
    else:
        label = base
    data = _data_lines_for_alert(alert, history)
    lines = [f"{label}: {data[0]}"]
    lines.extend(data[1:])
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
    """At most one emoji — only on truly standout moves."""
    if is_emergency or alert.alert_tier == "emergency":
        return "🚨 "
    if history.is_all_time_high:
        return "🔥 "
    if history.days_since_larger_move and history.days_since_larger_move >= 30:
        return "🚨 "
    if alert.indicator.endswith("_liquidations") and alert.alert_tier == "major":
        return "🚨 "
    if alert.indicator == "yield_curve" and _cross_direction(alert) == "below":
        return "⚠️ "
    if alert.indicator == "vix" and (alert.value >= 30 or _cross_direction(alert) == "above"):
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
        return "🔥 "
    return ""


def _template_ath(alert: AlertTrigger, history: MoveHistory, posting_cfg: dict[str, Any], *, is_emergency: bool) -> str:
    emoji = _emoji_for_post(alert, history, posting_cfg, standout=True, is_emergency=is_emergency)
    headline = _headline_for_alert(alert, major=True, emoji=emoji, history=history)
    return _assemble_tweet(
        headline=headline,
        data_lines=_data_lines_for_alert(alert, history),
        context=_context_line(alert, history),
        takeaway=_takeaway_line(alert, history),
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
    headline = _headline_for_alert(alert, major=standout, emoji=emoji, history=history)
    data_lines = _data_lines_for_alert(alert, history) if alert.prev_value is not None else [_format_value(alert)]
    return _assemble_tweet(
        headline=headline,
        data_lines=data_lines,
        context=_context_line(alert, history),
        takeaway=_takeaway_line(alert, history),
    )


def _template_funding(
    alert: AlertTrigger,
    history: MoveHistory,
    posting_cfg: dict[str, Any],
    *,
    is_emergency: bool,
) -> str:
    standout = _is_standout(alert, history, posting_cfg, is_emergency=is_emergency)
    emoji = _emoji_for_post(alert, history, posting_cfg, standout=standout, is_emergency=is_emergency)
    return _assemble_tweet(
        headline=f"{emoji}{_funding_headline(alert, history)}".strip(),
        data_lines=_data_lines_for_funding(alert, history),
        context=_crypto_context_line(alert, history),
        takeaway=_funding_takeaway(alert, history),
    )


def _template_basis(
    alert: AlertTrigger,
    history: MoveHistory,
    posting_cfg: dict[str, Any],
    *,
    is_emergency: bool,
) -> str:
    standout = _is_standout(alert, history, posting_cfg, is_emergency=is_emergency)
    emoji = _emoji_for_post(alert, history, posting_cfg, standout=standout, is_emergency=is_emergency)
    return _assemble_tweet(
        headline=f"{emoji}{_basis_headline(alert, history)}".strip(),
        data_lines=_data_lines_for_basis(alert, history),
        context=_crypto_context_line(alert, history),
        takeaway=_basis_takeaway(alert, history),
    )


def _template_sentiment(
    alert: AlertTrigger,
    history: MoveHistory,
    posting_cfg: dict[str, Any],
    *,
    is_emergency: bool,
) -> str:
    standout = _is_standout(alert, history, posting_cfg, is_emergency=is_emergency)
    emoji = _emoji_for_post(alert, history, posting_cfg, standout=standout, is_emergency=is_emergency)
    return _assemble_tweet(
        headline=f"{emoji}{_sentiment_headline(alert)}".strip(),
        data_lines=_data_lines_for_sentiment(alert, history),
        context=_sentiment_context(alert, history),
        takeaway=_sentiment_takeaway(alert),
    )


def _template_open_interest(
    alert: AlertTrigger,
    history: MoveHistory,
    posting_cfg: dict[str, Any],
    *,
    is_emergency: bool,
) -> str:
    standout = _is_standout(alert, history, posting_cfg, is_emergency=is_emergency)
    emoji = _emoji_for_post(alert, history, posting_cfg, standout=standout, is_emergency=is_emergency)
    return _assemble_tweet(
        headline=f"{emoji}{_open_interest_headline(alert, history)}".strip(),
        data_lines=_data_lines_for_open_interest(alert, history),
        context=_crypto_context_line(alert, history),
        takeaway=_open_interest_takeaway(alert, history),
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
        headline=f"{emoji}{_exchange_spread_headline(alert, history)}".strip(),
        data_lines=_data_lines_for_exchange_spread(alert, history),
        context=_crypto_context_line(alert, history),
        takeaway=_exchange_spread_takeaway(alert, history),
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
        headline=_headline_for_alert(alert, major=False, emoji=emoji, history=history),
        data_lines=_data_lines_for_liquidation(alert, history),
        context=_crypto_context_line(alert, history),
        takeaway=_liquidation_takeaway(alert, history),
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
        alert,
        major=_major_for_headline(alert, history, posting_cfg, is_emergency=is_emergency),
        emoji=emoji,
        history=history,
    )
    data_lines = _data_lines_for_alert(alert, history) if alert.prev_value is not None else [_format_value(alert)]
    return _assemble_tweet(
        headline=headline,
        data_lines=data_lines,
        context=_context_line(alert, history),
        takeaway=_takeaway_line(alert, history),
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

    if alert.indicator.endswith("_funding"):
        return _template_funding(alert, history, posting_cfg, is_emergency=is_emergency)

    if alert.indicator.endswith("_basis"):
        return _template_basis(alert, history, posting_cfg, is_emergency=is_emergency)

    if alert.indicator == "fear_greed":
        return _template_sentiment(alert, history, posting_cfg, is_emergency=is_emergency)

    if alert.indicator.endswith("_exchange_spread"):
        return _template_exchange_spread(alert, history, posting_cfg, is_emergency=is_emergency)

    if alert.indicator.endswith("_open_interest"):
        return _template_open_interest(alert, history, posting_cfg, is_emergency=is_emergency)

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
    app_cfg: dict[str, Any] | None = None,
) -> str:
    history = history or MoveHistory()
    posting_cfg = posting_cfg or {}
    _set_compose_cfg(app_cfg)
    return _pick_single_template(alert, history, posting_cfg, is_emergency=is_emergency)


CHART_ALWAYS_SUFFIXES = ("_liquidations", "_basis", "_exchange_spread", "_funding", "_open_interest")
VOLATILITY_INDICATORS = frozenset({"vix", "move"})


def is_text_only_alert(
    alert: AlertTrigger,
    history: MoveHistory,
    posting_cfg: dict[str, Any],
    *,
    is_emergency: bool,
) -> bool:
    """Text-only posts (~0–20%): simple flips, breaking macro headlines, fast level updates."""
    if is_emergency or alert.alert_tier in ("emergency", "major"):
        return False
    if alert.indicator.endswith(CHART_ALWAYS_SUFFIXES):
        return False
    if alert.indicator in VOLATILITY_INDICATORS:
        return False
    if any(r in ("percent_change", "absolute_change", "liquidation_spike") for r in alert.rule_types):
        return False

    level_rules = frozenset({"above", "below", "crosses_above", "crosses_below"})
    if not alert.rule_types or not all(r in level_rules for r in alert.rule_types):
        return False

    if alert.indicator == "fear_greed":
        return True

    if alert.is_macro or alert.indicator in MACRO_INDICATORS:
        return True

    if alert.indicator in KEY_LEVEL_CROSS_INDICATORS:
        return True

    if all(r in ("above", "below") for r in alert.rule_types):
        return True

    return False


def should_attach_chart(
    alert: AlertTrigger,
    history: MoveHistory,
    posting_cfg: dict[str, Any],
    *,
    is_emergency: bool,
) -> bool:
    """Default: attach a chart (target 80–100% of posts)."""
    return not is_text_only_alert(alert, history, posting_cfg, is_emergency=is_emergency)


def compose_multi_tweet(
    alerts: list[AlertTrigger],
    theme: str | None = None,
    *,
    histories: dict[str, MoveHistory] | None = None,
    posting_cfg: dict[str, Any] | None = None,
    is_emergency: bool = False,
    app_cfg: dict[str, Any] | None = None,
) -> str:
    histories = histories or {}
    posting_cfg = posting_cfg or {}
    _set_compose_cfg(app_cfg)
    if len(alerts) == 1:
        a = alerts[0]
        return compose_single_tweet(
            a,
            history=histories.get(a.indicator, MoveHistory()),
            posting_cfg=posting_cfg,
            is_emergency=is_emergency,
            app_cfg=app_cfg,
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
        "risk_on": "Largest coordinated risk-on move in weeks.",
        "risk_off": "Largest coordinated risk-off move in weeks.",
        "crypto": "Largest coordinated crypto move in weeks.",
        "inflation_pressure": "Inflation signals stacking across releases.",
        "easing_conditions": "Easing signals stacking across releases.",
        "tightening_conditions": "Tightening signals stacking across releases.",
        "housing": "Housing indicators diverging sharply.",
    }
    multi_takeaways = {
        "risk_on": "improving risk appetite across the cluster",
        "risk_off": "defensive demand rising across the cluster",
        "crypto": "crypto positioning shifting across spot and perps",
        "inflation_pressure": "inflation pressure narrative heating up across the cluster",
        "easing_conditions": "financial conditions easing across the cluster",
        "tightening_conditions": "liquidity tightening across the cluster",
        "housing": "housing market repricing across the cluster",
    }
    theme_headline = THEME_HEADLINES.get(theme, "Multi-asset move").upper()
    headline = f"{emoji}{theme_headline}".strip()
    context = implications.get(theme) or _context_line(top, top_hist) or "Cluster stands out vs recent baseline."
    return _assemble_tweet(
        headline=headline,
        data_lines=data_lines,
        context=context,
        takeaway=multi_takeaways.get(theme, "market structure shifting together across assets"),
    )