"""Event scorecard metrics and tweet injection."""

from __future__ import annotations

import sqlite3
import unittest
from datetime import date, datetime, timedelta, timezone

from src.posting.compose import compose_single_tweet
from src.posting.event_score import (
    EventMetrics,
    EventScorecard,
    build_event_scorecard,
    inject_scorecard,
)
from src.posting.history import MoveHistory
from src.posting.models import AlertTrigger
from src.stats import z_score


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE readings (
            indicator TEXT NOT NULL,
            value REAL NOT NULL,
            observed_at TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            PRIMARY KEY (indicator, recorded_at)
        )"""
    )
    return conn


def _seed(conn: sqlite3.Connection, indicator: str, values: list[float], start: str = "2025-01-01") -> None:
    day = date.fromisoformat(start)
    for i, value in enumerate(values):
        d = (day + timedelta(days=i)).isoformat()
        conn.execute(
            "INSERT INTO readings (indicator, value, observed_at, recorded_at) VALUES (?, ?, ?, ?)",
            (indicator, value, d, f"{d}T20:00:00+00:00"),
        )
    conn.commit()


def _alert(
    indicator: str,
    value: float,
    prev: float | None,
    *,
    unit: str = "percent",
    tier: str = "normal",
    reasons: list[str] | None = None,
    rule_types: list[str] | None = None,
) -> AlertTrigger:
    return AlertTrigger(
        indicator=indicator,
        name=indicator,
        value=value,
        prev_value=prev,
        reasons=reasons or ["moved"],
        rule_types=rule_types or ["percent_change"],
        themes=["risk_off"],
        category="equities_vol",
        is_macro=False,
        timestamp=datetime(2025, 3, 2, 15, 0, tzinfo=timezone.utc),
        alert_tier=tier,
        alert_unit=unit,
        score=80,
    )


STUB_CFG = {
    "defaults": {},
    "indicators": {
        "sp500": {"name": "S&P 500", "source": "yahoo", "normal_alert": 2},
        "vix": {"name": "VIX", "source": "yahoo", "normal_alert": 15},
        "move": {"name": "MOVE", "source": "yahoo", "normal_alert": 8},
        "btc": {"name": "Bitcoin", "source": "yahoo", "normal_alert": 5},
        "eth": {"name": "Ethereum", "source": "yahoo", "normal_alert": 6},
        "btc_liquidations": {
            "name": "BTC Liquidations",
            "source": "okx_liquidations",
            "normal_alert": 100,
            "alert_unit": "percent",
        },
    },
    "posting": {"indicator_themes": {}},
}


class ZScoreHelperTests(unittest.TestCase):
    def test_outlier_has_large_z(self) -> None:
        history = [1.0] * 20 + [1.2]
        self.assertIsNone(z_score(1.0, [1.0, 1.0]))
        z = z_score(8.0, history)
        assert z is not None
        self.assertGreater(z, 3)

    def test_empty_history(self) -> None:
        self.assertIsNone(z_score(1.0, []))


class ScorecardMetricTests(unittest.TestCase):
    def test_empty_db_does_not_crash(self) -> None:
        conn = _conn()
        alert = _alert("btc", 70000, 65000)
        card = build_event_scorecard(conn, alert, MoveHistory(pct_change=7.7, abs_change=5000))
        self.assertEqual(card.indicator, "btc")
        self.assertIn(card.severity, ("LOW", "MEDIUM", "HIGH", "EXTREME"))
        self.assertEqual(card.metrics.abs_change, 5000)
        conn.close()

    def test_percentile_zscore_velocity_and_rarity(self) -> None:
        conn = _conn()
        closes = [100.0]
        for _ in range(40):
            closes.append(closes[-1] * 1.004)
        closes.append(closes[-1] * 1.09)
        _seed(conn, "sp500", closes)

        alert = _alert("sp500", closes[-1], closes[-2], rule_types=["percent_change"])
        history = MoveHistory(pct_change=9.0, abs_change=closes[-1] - closes[-2], days_since_larger_move=41)
        card = build_event_scorecard(conn, alert, history, cfg=STUB_CFG)

        assert card.metrics.rolling_percentile is not None
        self.assertGreaterEqual(card.metrics.rolling_percentile, 95)
        assert card.metrics.z_score is not None
        self.assertGreater(card.metrics.z_score, 2)
        assert card.metrics.velocity is not None
        self.assertGreater(card.metrics.velocity, 2)
        self.assertTrue(any("percentile" in r and "daily" in r for r in card.reasons))
        self.assertTrue(any("41 days" in r for r in card.reasons))
        self.assertGreaterEqual(card.score, 55)
        conn.close()

    def test_vix_peer_alert_confirms(self) -> None:
        conn = _conn()
        alert = _alert("sp500", 5600, 5800)
        vix = _alert("vix", 32, 18, unit="percent", reasons=["crossed above 30"], rule_types=["crosses_above"])
        history = MoveHistory(pct_change=-3.4, abs_change=-200, days_since_larger_move=20)
        card = build_event_scorecard(
            conn, alert, history, peer_alerts=[alert, vix], cfg=STUB_CFG,
        )
        self.assertIn("vix", card.metrics.confirmations)
        self.assertIn("confirmed by VIX", card.reasons)
        conn.close()

    def test_unrelated_batch_alert_is_not_confirmation(self) -> None:
        conn = _conn()
        alert = _alert("sp500", 7400, 7200)
        ibit = _alert("crypto_etf_ibit", 80_000_000, 40_000_000)
        ibit.themes = ["crypto"]
        ibit.category = "crypto"
        card = build_event_scorecard(
            conn, alert, MoveHistory(pct_change=2.8, abs_change=200),
            peer_alerts=[alert, ibit], cfg=STUB_CFG,
        )
        self.assertNotIn("crypto_etf_ibit", card.metrics.confirmations)
        self.assertFalse(any("ibit" in r.lower() for r in card.reasons))
        conn.close()

    def test_move_elevated_for_treasury(self) -> None:
        conn = _conn()
        _seed(conn, "move", [90.0, 95.0, 132.0], start="2025-02-28")
        alert = AlertTrigger(
            indicator="treasury_10y",
            name="10Y Treasury Yield",
            value=4.62,
            prev_value=4.40,
            reasons=["moved 22 bps up"],
            rule_types=["absolute_change"],
            themes=["tightening_conditions"],
            category="rates_fx",
            is_macro=False,
            timestamp=datetime(2025, 3, 2, 15, 0, tzinfo=timezone.utc),
            alert_tier="major",
            alert_unit="absolute",
            score=82,
        )
        history = MoveHistory(pct_change=5.0, abs_change=0.22, days_since_larger_move=30)
        card = build_event_scorecard(conn, alert, history, cfg=STUB_CFG)
        self.assertIn("Treasury volatility elevated", card.reasons)
        conn.close()

    def test_persistence_consecutive_down_days(self) -> None:
        conn = _conn()
        series = [100.0]
        for _ in range(20):
            series.append(series[-1] * 1.002)
        for drop in (0.985, 0.98, 0.97, 0.94):
            series.append(series[-1] * drop)
        _seed(conn, "btc", series)
        alert = _alert("btc", series[-1], series[-2], rule_types=["percent_change"])
        alert.themes = ["crypto"]
        alert.category = "crypto"
        history = MoveHistory(pct_change=-6.0, abs_change=series[-1] - series[-2])
        card = build_event_scorecard(conn, alert, history, cfg=STUB_CFG)
        self.assertGreaterEqual(card.metrics.persistence or 0, 3)
        conn.close()

    def test_liquidation_uses_1h_horizon(self) -> None:
        conn = _conn()
        values = [2_000_000.0] * 30 + [80_000_000.0]
        _seed(conn, "btc_liquidations", values)
        alert = _alert(
            "btc_liquidations",
            80_000_000.0,
            2_000_000.0,
            rule_types=["liquidation_spike"],
        )
        alert.themes = ["crypto"]
        alert.category = "crypto"
        history = MoveHistory(pct_change=3900.0, abs_change=78_000_000.0, days_since_larger_move=41)
        card = build_event_scorecard(conn, alert, history, cfg=STUB_CFG)
        self.assertEqual(card.metrics.horizon, "1h")
        self.assertTrue(any("1h" in r for r in card.reasons))
        conn.close()

    def test_thin_history_lowers_confidence(self) -> None:
        conn = _conn()
        _seed(conn, "gold", [2000.0, 2010.0])
        alert = AlertTrigger(
            indicator="gold",
            name="Gold",
            value=2010.0,
            prev_value=2000.0,
            reasons=["moved"],
            rule_types=["percent_change"],
            themes=["risk_off"],
            category="commodities",
            is_macro=False,
            timestamp=datetime.now(timezone.utc),
            alert_unit="percent",
        )
        thin = build_event_scorecard(conn, alert, MoveHistory(pct_change=0.5, abs_change=10))
        self.assertLess(thin.metrics.data_confidence, 55)

        rich_values = [1800.0 + i for i in range(80)]
        _seed(conn, "gold", rich_values, start="2024-01-01")
        rich_alert = AlertTrigger(
            indicator="gold",
            name="Gold",
            value=rich_values[-1],
            prev_value=rich_values[-2],
            reasons=["moved"],
            rule_types=["percent_change"],
            themes=["risk_off"],
            category="commodities",
            is_macro=False,
            timestamp=datetime.now(timezone.utc),
            alert_unit="percent",
        )
        rich = build_event_scorecard(
            conn, rich_alert, MoveHistory(pct_change=0.05, abs_change=1.0),
        )
        self.assertGreater(rich.metrics.data_confidence, thin.metrics.data_confidence)
        conn.close()

    def test_emergency_floor_is_high(self) -> None:
        conn = _conn()
        alert = _alert("sp500", 5000, 4800, tier="emergency")
        card = build_event_scorecard(conn, alert, MoveHistory(pct_change=4.2, abs_change=200))
        self.assertGreaterEqual(card.score, 85)
        self.assertIn(card.severity, ("HIGH", "EXTREME"))
        conn.close()

    def test_weak_metrics_omitted_from_reasons(self) -> None:
        conn = _conn()
        alert = _alert("sp500", 5001, 5000)
        card = build_event_scorecard(conn, alert, MoveHistory(pct_change=0.02, abs_change=1.0))
        self.assertEqual(card.reasons, [])
        conn.close()


class ScorecardFormatTests(unittest.TestCase):
    def test_format_matches_template_shape(self) -> None:
        card = EventScorecard(
            indicator="sp500",
            score=87,
            severity="HIGH",
            reasons=[
                "98th percentile 1h move",
                "largest move in 41 days",
                "confirmed by VIX",
                "Treasury volatility elevated",
            ],
            metrics=EventMetrics(),
        )
        block = card.format_block()
        self.assertEqual(
            block.splitlines()[:4],
            [
                "event_score = 87 / 100",
                "severity = HIGH",
                "",
                "Reasons:",
            ],
        )
        self.assertIn("+ 98th percentile 1h move", block)
        self.assertIn("+ confirmed by VIX", block)

    def test_inject_replaces_context_and_stays_under_280(self) -> None:
        alert = _alert("sp500", 5600, 5800)
        history = MoveHistory(pct_change=-3.4, abs_change=-200, days_since_larger_move=41)
        card = EventScorecard(
            indicator="sp500",
            score=87,
            severity="HIGH",
            reasons=[
                "98th percentile daily move",
                "largest move in 41 days",
                "confirmed by VIX",
                "Treasury volatility elevated",
            ],
            metrics=EventMetrics(rolling_percentile=98, rarity_days=41),
        )
        text = compose_single_tweet(alert, history=history, scorecard=card)
        self.assertIn("event_score = 87 / 100", text)
        self.assertIn("severity = HIGH", text)
        self.assertIn("+ 98th percentile daily move", text)
        self.assertLessEqual(len(text), 280)
        self.assertNotIn("Largest decline in 41 days.", text)

    def test_inject_noop_without_reasons(self) -> None:
        original = "S&P 500 DROP\n\n-2.1%\n\nNotable move vs recent trading range.\n\n→ fading risk appetite."
        card = EventScorecard(
            indicator="sp500",
            score=40,
            severity="LOW",
            reasons=[],
            metrics=EventMetrics(),
        )
        self.assertEqual(inject_scorecard(original, card), original)


if __name__ == "__main__":
    unittest.main()
