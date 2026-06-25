#!/usr/bin/env python3
"""Generate sample posts for crypto indicator families (tone review)."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.posting.compose import compose_single_tweet, validate_post_before_send
from src.posting.history import MoveHistory
from src.posting.models import AlertTrigger


def _alert(
    indicator: str,
    name: str,
    value: float,
    prev: float | None,
    *,
    tier: str = "normal",
    reasons: list[str] | None = None,
    long_usd: float | None = None,
    short_usd: float | None = None,
    history: MoveHistory | None = None,
    label: str,
) -> tuple[str, MoveHistory, str]:
    alert = AlertTrigger(
        indicator=indicator,
        name=name,
        value=value,
        prev_value=prev,
        reasons=reasons or [],
        rule_types=["percent_change"],
        themes=["crypto"],
        category="crypto",
        is_macro=False,
        timestamp=datetime.now(timezone.utc),
        alert_tier=tier,
        alert_unit="absolute" if indicator.endswith(("_basis", "_exchange_spread")) else "percent",
        liq_long_usd=long_usd,
        liq_short_usd=short_usd,
    )
    hist = history or MoveHistory(
        pct_change=abs((value - prev) / prev * 100) if prev else 0,
        abs_change=(value - prev) if prev is not None else 0,
    )
    text = compose_single_tweet(alert, history=hist, posting_cfg={})
    validation = validate_post_before_send(text, alert)
    status = "OK" if validation.ok else f"WARN: {', '.join(validation.issues)}"
    return label, hist, f"{text}\n\n[{status}]"


def main() -> None:
    families: dict[str, list[tuple]] = {
        "exchange_spread": [
            ("btc_exchange_spread", "BTC Exchange Spread", 3.2, 28.5, {}, "Sharp compression"),
            ("eth_exchange_spread", "ETH Exchange Spread", 41.0, 18.2, {}, "Gap widening"),
            ("btc_exchange_spread", "BTC Exchange Spread", 8.1, 22.0, {"days_since_larger_move": 21}, "Largest shrink in 21d"),
            ("sol_exchange_spread", "SOL Exchange Spread", 15.4, 14.9, {}, "Small drift"),
            ("btc_exchange_spread", "BTC Exchange Spread", 2.1, 35.0, {"days_since_larger_move": 45}, "Emergency-tier collapse"),
        ],
        "funding": [
            ("btc_funding", "BTC Funding", -0.00012, 0.00004, {"reasons": ["below 0"]}, "Turned negative"),
            ("eth_funding", "ETH Funding", 0.00038, 0.00012, {}, "Funding surge"),
            ("sol_funding", "SOL Funding", 0.00001, 0.00019, {}, "Reset to neutral"),
            ("btc_funding", "BTC Funding", -0.00028, -0.00005, {"days_since_larger_move": 14}, "Largest move in 14d"),
            ("eth_funding", "ETH Funding", 0.00052, 0.00021, {"alert_tier": "emergency"}, "Emergency crowded long"),
        ],
        "liquidation": [
            ("btc_liquidations", "BTC Liquidations", 461_800_000, 20_300_000, {"long_usd": 380_000_000, "short_usd": 82_000_000, "reasons": ["flush_type:long"]}, "Long flush"),
            ("eth_liquidations", "ETH Liquidations", 128_000_000, 9_500_000, {"long_usd": 18_000_000, "short_usd": 110_000_000, "reasons": ["flush_type:short"]}, "Short flush"),
            ("sol_liquidations", "SOL Liquidations", 74_000_000, 31_000_000, {"long_usd": 38_000_000, "short_usd": 36_000_000, "reasons": ["flush_type:mixed"]}, "Mixed flush"),
            ("btc_liquidations", "BTC Liquidations", 220_000_000, 48_000_000, {"days_since_larger_move": 90, "long_usd": 195_000_000, "short_usd": 25_000_000, "reasons": ["flush_type:long"]}, "Largest spike in 90d"),
            ("eth_liquidations", "ETH Liquidations", 510_000_000, 85_000_000, {"alert_tier": "emergency", "long_usd": 430_000_000, "short_usd": 80_000_000, "reasons": ["flush_type:long"]}, "Emergency long flush"),
        ],
        "basis": [
            ("btc_basis", "BTC Basis", -4.5, 6.2, {}, "Futures below spot"),
            ("eth_basis", "ETH Basis", 18.3, 7.1, {}, "Premium widening"),
            ("sol_basis", "SOL Basis", 5.2, 14.8, {}, "Premium narrowing"),
            ("btc_basis", "BTC Basis", 32.0, 18.0, {"days_since_larger_move": 30}, "Largest gap move in 30d"),
            ("eth_basis", "ETH Basis", -8.2, 2.1, {"alert_tier": "emergency"}, "Emergency inversion"),
        ],
        "open_interest": [
            ("btc_open_interest", "BTC Open Interest", 28_400_000_000, 26_100_000_000, {}, "OI rising"),
            ("eth_open_interest", "ETH Open Interest", 9_800_000_000, 11_200_000_000, {}, "OI falling"),
            ("sol_open_interest", "SOL Open Interest", 1_950_000_000, 1_720_000_000, {"days_since_larger_move": 18}, "Largest move in 18d"),
            ("btc_open_interest", "BTC Open Interest", 29_100_000_000, 27_800_000_000, {"is_all_time_high": True}, "New OI high"),
            ("eth_open_interest", "ETH Open Interest", 7_400_000_000, 10_600_000_000, {"alert_tier": "major"}, "Major deleveraging"),
        ],
    }

    for family, scenarios in families.items():
        print("=" * 72)
        print(family.upper().replace("_", " "))
        print("=" * 72)
        for i, row in enumerate(scenarios, 1):
            indicator, name, value, prev, extras, label = row
            history_kwargs = {k: v for k, v in extras.items() if k not in ("reasons", "long_usd", "short_usd", "alert_tier")}
            hist = MoveHistory(**history_kwargs) if history_kwargs else MoveHistory(
                pct_change=abs((value - prev) / prev * 100) if prev else 0,
                abs_change=(value - prev) if prev is not None else 0,
            )
            _, _, block = _alert(
                indicator,
                name,
                value,
                prev,
                tier=extras.get("alert_tier", "normal"),
                reasons=extras.get("reasons"),
                long_usd=extras.get("long_usd"),
                short_usd=extras.get("short_usd"),
                history=hist,
                label=label,
            )
            print(f"\n--- Sample {i}: {label} ---\n")
            print(block)
            print()


if __name__ == "__main__":
    main()