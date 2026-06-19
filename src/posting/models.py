from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class AlertTrigger:
    indicator: str
    name: str
    value: float
    prev_value: float | None
    reasons: list[str]
    rule_types: list[str]
    themes: list[str]
    category: str
    is_macro: bool
    timestamp: datetime
    score: float = 0.0
    db_id: int | None = None
    magnitude_pct: float = 0.0
    standalone_major: bool = False


@dataclass
class TweetDecision:
    tweet_type: str  # "single" | "multi"
    alerts: list[AlertTrigger]
    score: float = 0.0
    is_emergency: bool = False
    theme: str | None = None


# Known coherent story patterns for multi-tweets
STORY_PATTERNS: dict[str, set[str]] = {
    "risk_on": {"risk_on", "crypto", "equities"},
    "risk_off": {"risk_off", "equities"},
    "inflation_pressure": {"inflation_pressure", "tightening_conditions"},
    "easing_conditions": {"easing_conditions", "risk_on"},
    "housing_stress": {"housing", "easing_conditions"},
    "crypto_rally": {"crypto", "risk_on"},
}

# Category groupings for coherence bonus
CATEGORY_GROUPS: dict[str, set[str]] = {
    "crypto": {"btc", "eth", "sol", "fear_greed"},
    "rates_fx": {"dxy", "treasury_10y", "fed_funds", "yield_curve", "move"},
    "macro_data": {"cpi_yoy", "unemployment", "pmi_manufacturing", "ism_services", "jobless_claims", "consumer_sentiment"},
    "equities_vol": {"sp500", "nasdaq100", "vix", "hy_spread"},
    "housing": {"case_shiller", "mortgage_30y"},
    "commodities": {"gold", "silver", "oil"},
}