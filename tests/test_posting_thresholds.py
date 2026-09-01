"""Tests for posting-engine threshold loosening (score, age, diversity, off-hours)."""

from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src.config import indicator_settings, load_config
from src.db import insert_pending_alert, record_post
from src.market_hours import off_hours_equity_alert_action
from src.posting.context_explain import classify_post_skip
from src.posting.decide import decide_tweet_type
from src.posting.engine import (
    _crypto_streak_at_cap,
    _prefer_non_crypto_alternative,
    process_posting_queue,
)
from src.posting.grouping import filter_stale_alerts
from src.posting.models import AlertTrigger
from src.posting.scoring import calculate_score, is_fresh


HEADLINE_STANDALONE = (
    "nasdaq100",
    "qqq",
    "gold",
    "oil",
    "jobless_claims",
    "unemployment",
    "consumer_sentiment",
)


def _alert(
    indicator: str,
    *,
    score: float,
    tier: str = "normal",
    standalone: bool = False,
    themes: list[str] | None = None,
    category: str = "crypto",
    hours_ago: float = 0.0,
) -> AlertTrigger:
    return AlertTrigger(
        indicator=indicator,
        name=indicator,
        value=100.0,
        prev_value=90.0,
        reasons=["test"],
        rule_types=["percent_change"],
        themes=themes or ["crypto"],
        category=category,
        is_macro=False,
        timestamp=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
        score=score,
        alert_tier=tier,
        standalone_major=standalone,
    )


def _memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE readings (
            indicator TEXT NOT NULL,
            value REAL NOT NULL,
            observed_at TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            liq_long_usd REAL,
            liq_short_usd REAL,
            aux_value REAL,
            PRIMARY KEY (indicator, recorded_at)
        );
        CREATE TABLE alert_log (
            indicator TEXT PRIMARY KEY,
            last_value REAL NOT NULL,
            last_alert_at TEXT
        );
        CREATE TABLE post_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            posted_at TEXT NOT NULL,
            tweet_type TEXT NOT NULL,
            text TEXT NOT NULL,
            alert_ids TEXT NOT NULL,
            indicators TEXT NOT NULL,
            is_emergency INTEGER NOT NULL DEFAULT 0,
            score REAL NOT NULL
        );
        CREATE TABLE tweet_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            posted_at TEXT NOT NULL,
            primary_category TEXT NOT NULL,
            themes TEXT NOT NULL
        );
        CREATE TABLE pending_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            indicator TEXT NOT NULL,
            value REAL NOT NULL,
            prev_value REAL,
            reasons TEXT NOT NULL,
            rule_types TEXT NOT NULL,
            themes TEXT NOT NULL,
            category TEXT NOT NULL,
            score REAL NOT NULL,
            is_macro INTEGER NOT NULL DEFAULT 0,
            triggered_at TEXT NOT NULL,
            processed INTEGER NOT NULL DEFAULT 0,
            alert_tier TEXT NOT NULL DEFAULT 'normal'
        );
        """
    )
    return conn


def _queue_crypto_posts(conn: sqlite3.Connection, n: int = 2) -> None:
    for i, indicator in enumerate(("eth_liquidations", "sol_liquidations")[:n]):
        record_post(
            conn,
            tweet_type="single",
            text=f"crypto {i}",
            alert_ids=[i + 1],
            indicators=[indicator],
            is_emergency=False,
            score=90,
            primary_category="crypto",
            themes=["crypto"],
        )


class ConfigThresholdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cfg = load_config()
        cls.posting = cls.cfg["posting"]

    def test_posting_engine_thresholds(self) -> None:
        self.assertEqual(self.posting["high_single_threshold"], 75)
        self.assertEqual(self.posting["alert_max_age_hours"], 36)
        self.assertEqual(self.posting["max_crypto_streak"], 2)

    def test_headline_indicators_are_standalone_major(self) -> None:
        for key in HEADLINE_STANDALONE:
            settings = indicator_settings(self.cfg, key)
            self.assertTrue(settings.get("standalone_major"), key)

    def test_sp500_fred_verify_tolerance(self) -> None:
        settings = indicator_settings(self.cfg, "sp500")
        self.assertEqual(settings["quality"]["verify"]["tolerance_pct"], 3.0)

    def test_loosened_fire_rules(self) -> None:
        nasdaq = indicator_settings(self.cfg, "nasdaq100")
        self.assertEqual(nasdaq["normal_alert"], 2.5)
        self.assertEqual(nasdaq["major_alert"], 3.5)
        self.assertEqual(nasdaq["emergency_alert"], 5.5)

        oil = indicator_settings(self.cfg, "oil")
        self.assertEqual(oil["normal_alert"], 4)

        jobless = indicator_settings(self.cfg, "jobless_claims")
        self.assertEqual(jobless["normal_alert"], 6)

        qqq = indicator_settings(self.cfg, "qqq")
        self.assertEqual(qqq["move_percentile"], 95)

        dark = indicator_settings(self.cfg, "dark_pool_spy")
        self.assertEqual(dark["pct_percentile"], 95)


class ScoreGateTests(unittest.TestCase):
    def test_config_threshold_posts_76(self) -> None:
        posting = load_config()["posting"]
        decision = decide_tweet_type(
            [_alert("nasdaq100", score=76, themes=["equities"], category="equities_vol")],
            posting,
        )
        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision.tweet_type, "single")

    def test_config_threshold_blocks_74(self) -> None:
        posting = load_config()["posting"]
        decision = decide_tweet_type(
            [_alert("dxy", score=74, themes=["tightening_conditions"], category="rates_fx")],
            posting,
        )
        self.assertIsNone(decision)

    def test_standalone_major_still_posts_below_threshold(self) -> None:
        posting = load_config()["posting"]
        decision = decide_tweet_type(
            [_alert("gold", score=40, standalone=True, themes=["risk_off"], category="commodities")],
            posting,
        )
        self.assertIsNotNone(decision)


class FreshnessTests(unittest.TestCase):
    def test_twenty_hour_alert_survives_36h_window(self) -> None:
        posting = load_config()["posting"]
        alert = _alert("treasury_10y", score=83.5, hours_ago=20)
        self.assertTrue(is_fresh(alert, posting))
        self.assertFalse(is_fresh(alert, {"alert_max_age_hours": 12}))

        settings = indicator_settings(load_config(), "treasury_10y")
        self.assertIsNotNone(calculate_score(alert, settings, posting))

    def test_filter_stale_keeps_20h_alerts(self) -> None:
        posting = load_config()["posting"]
        fresh = _alert("m2", score=31, hours_ago=20)
        stale = _alert("m2", score=31, hours_ago=40)
        kept = filter_stale_alerts([fresh, stale], posting)
        self.assertEqual([a.score for a in kept], [31])


class OffHoursEquityTests(unittest.TestCase):
    def test_session_indicators_queue_off_hours(self) -> None:
        skip = "outside US equity session (9:30–16:00 ET Mon–Fri)"
        alert = _alert("sp500", score=85, themes=["equities"], category="equities_vol")
        for key in ("sp500", "nasdaq100", "qqq", "gold", "dxy", "dark_pool_spy", "crypto_etf_ibit"):
            settings = {"key": key, "quality": {"schedule": "us_equity"}}
            self.assertEqual(
                off_hours_equity_alert_action(settings, alert, skip),
                "queue",
                key,
            )

    def test_vix_major_still_posts_off_hours(self) -> None:
        skip = "outside US equity session (9:30–16:00 ET Mon–Fri)"
        settings = {"key": "vix", "quality": {"schedule": "us_equity"}}
        major = _alert("vix", score=90, tier="major", themes=["risk_off"], category="equities_vol")
        major.indicator = "vix"
        self.assertEqual(off_hours_equity_alert_action(settings, major, skip), "post")

        normal = _alert("vix", score=70, tier="normal", themes=["risk_off"], category="equities_vol")
        normal.indicator = "vix"
        self.assertEqual(off_hours_equity_alert_action(settings, normal, skip), "queue")

    def test_crypto_and_no_skip_still_post(self) -> None:
        alert = _alert("btc", score=80)
        crypto = {"key": "btc", "quality": {"schedule": "crypto_24_7"}}
        self.assertEqual(
            off_hours_equity_alert_action(crypto, alert, "outside US equity session"),
            "post",
        )
        equity = {"key": "sp500", "quality": {"schedule": "us_equity"}}
        self.assertEqual(off_hours_equity_alert_action(equity, alert, None), "post")


class DiversityTests(unittest.TestCase):
    def test_classify_diversity_gate(self) -> None:
        primary, secondary = classify_post_skip(
            gate="diversity", score=80, post_threshold=75
        )
        self.assertEqual(primary, "diversity")
        self.assertIsNone(secondary)

    def test_streak_cap_and_non_crypto_alternative(self) -> None:
        conn = _memory_db()
        posting = {"max_crypto_streak": 2, "diversity_lookback": 3, "high_single_threshold": 75}
        self.assertFalse(_crypto_streak_at_cap(conn, posting))
        _queue_crypto_posts(conn, 2)
        self.assertTrue(_crypto_streak_at_cap(conn, posting))

        crypto = _alert("btc", score=80)
        gold = _alert("gold", score=80, standalone=True, themes=["risk_off"], category="commodities")
        alt = _prefer_non_crypto_alternative([crypto, gold], posting)
        self.assertIsNotNone(alt)
        assert alt is not None
        self.assertEqual(alt.alerts[0].indicator, "gold")

        self.assertIsNone(_prefer_non_crypto_alternative([crypto], posting))
        conn.close()

    @patch("src.posting.engine.chart_for_decision", return_value=None)
    @patch("src.posting.engine.post_tweet")
    def test_engine_skips_non_emergency_crypto_on_streak(
        self,
        mock_tweet: unittest.mock.MagicMock,
        _chart: unittest.mock.MagicMock,
    ) -> None:
        conn = _memory_db()
        cfg = load_config()
        _queue_crypto_posts(conn, 2)
        insert_pending_alert(
            conn,
            indicator="btc",
            value=70000,
            prev_value=65000,
            reasons=["moved 7.7% up (limit ±5%)"],
            rule_types=["percent_change"],
            themes=["crypto", "risk_on"],
            category="crypto",
            score=80,
            is_macro=False,
            triggered_at=datetime.now(timezone.utc).isoformat(),
            alert_tier="normal",
        )
        posted = process_posting_queue(conn, cfg, force=True)
        self.assertEqual(posted, 0)
        mock_tweet.assert_not_called()
        pending = conn.execute(
            "SELECT processed FROM pending_alerts WHERE indicator = 'btc'"
        ).fetchone()
        self.assertEqual(pending["processed"], 0)
        conn.close()

    @patch("src.posting.engine.chart_for_decision", return_value=None)
    @patch("src.posting.engine.post_tweet")
    def test_engine_prefers_non_crypto_alternative_in_same_batch(
        self,
        mock_tweet: unittest.mock.MagicMock,
        _chart: unittest.mock.MagicMock,
    ) -> None:
        conn = _memory_db()
        cfg = load_config()
        _queue_crypto_posts(conn, 2)
        now = datetime.now(timezone.utc).isoformat()
        insert_pending_alert(
            conn,
            indicator="crypto_etf_ibit",
            value=80_000_000,
            prev_value=40_000_000,
            reasons=["volume 80.0M (2.00x 30d avg)"],
            rule_types=["unusual_volume"],
            themes=["risk_on", "crypto"],
            category="crypto",
            score=82,
            is_macro=False,
            triggered_at=now,
            alert_tier="normal",
        )
        insert_pending_alert(
            conn,
            indicator="sp500",
            value=7400,
            prev_value=7200,
            reasons=["moved 2.8% up (limit ±2%)"],
            rule_types=["percent_change"],
            themes=["risk_on", "equities"],
            category="equities_vol",
            score=80,
            is_macro=False,
            triggered_at=now,
            alert_tier="normal",
        )
        posted = process_posting_queue(conn, cfg, force=True)
        self.assertEqual(posted, 1)
        mock_tweet.assert_called_once()
        row = conn.execute(
            "SELECT indicators FROM post_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(row["indicators"], "sp500")
        ibit = conn.execute(
            "SELECT processed FROM pending_alerts WHERE indicator = 'crypto_etf_ibit'"
        ).fetchone()
        self.assertEqual(ibit["processed"], 0)
        conn.close()

    @patch("src.posting.engine.chart_for_decision", return_value=None)
    @patch("src.posting.engine.post_tweet")
    def test_engine_still_posts_emergency_crypto_on_streak(
        self,
        mock_tweet: unittest.mock.MagicMock,
        _chart: unittest.mock.MagicMock,
    ) -> None:
        conn = _memory_db()
        cfg = load_config()
        _queue_crypto_posts(conn, 2)
        insert_pending_alert(
            conn,
            indicator="btc_liquidations",
            value=170_000_000,
            prev_value=1_000_000,
            reasons=["1H liquidations $170.2M > dynamic threshold $25.0M"],
            rule_types=["liquidation_spike"],
            themes=["crypto", "risk_off"],
            category="crypto",
            score=92,
            is_macro=False,
            triggered_at=datetime.now(timezone.utc).isoformat(),
            alert_tier="emergency",
        )
        posted = process_posting_queue(conn, cfg, force=True)
        self.assertEqual(posted, 1)
        mock_tweet.assert_called_once()
        conn.close()


if __name__ == "__main__":
    unittest.main()
