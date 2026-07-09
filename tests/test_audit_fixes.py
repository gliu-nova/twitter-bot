"""Tests for audit fixes: escalation, score gate, cooldown tokens, liquidations, etc."""

from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from src.alerts import check_alert, emergency_escalation_allows
from src.crypto_metrics import _okx_liquidations
from src.db import hours_since_indicator_post, prune_old_readings, record_post
from src.posting.context_explain import classify_post_skip
from src.posting.decide import decide_tweet_type
from src.posting.models import AlertTrigger
from src.twitter_client import _is_transient_twitter_error, post_tweet


def _alert(
    indicator: str,
    *,
    score: float,
    tier: str = "normal",
    standalone: bool = False,
    themes: list[str] | None = None,
    value: float = 100.0,
) -> AlertTrigger:
    return AlertTrigger(
        indicator=indicator,
        name=indicator,
        value=value,
        prev_value=90.0,
        reasons=["test"],
        rule_types=["percent_change"],
        themes=themes or ["crypto"],
        category="crypto",
        is_macro=False,
        timestamp=datetime.now(timezone.utc),
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


class EscalationTests(unittest.TestCase):
    def test_up_move_escalates(self) -> None:
        self.assertTrue(
            emergency_escalation_allows(
                tier="emergency", value=200.0, last_value=100.0, multiplier=2.0
            )
        )

    def test_down_move_escalates(self) -> None:
        self.assertTrue(
            emergency_escalation_allows(
                tier="emergency", value=50.0, last_value=100.0, multiplier=2.0
            )
        )

    def test_insufficient_move_blocked(self) -> None:
        self.assertFalse(
            emergency_escalation_allows(
                tier="emergency", value=150.0, last_value=100.0, multiplier=2.0
            )
        )

    def test_non_emergency_blocked(self) -> None:
        self.assertFalse(
            emergency_escalation_allows(
                tier="major", value=200.0, last_value=100.0, multiplier=2.0
            )
        )

    def test_check_alert_allows_crash_through_cooldown(self) -> None:
        conn = _memory_db()
        now = datetime.now(timezone.utc)
        conn.execute(
            "INSERT INTO readings (indicator, value, observed_at, recorded_at) VALUES (?,?,?,?)",
            ("btc", 100.0, "2026-07-01", now.isoformat()),
        )
        conn.execute(
            "INSERT INTO alert_log (indicator, last_value, last_alert_at) VALUES (?,?,?)",
            ("btc", 100.0, (now - timedelta(hours=1)).isoformat()),
        )
        conn.commit()
        settings = {
            "key": "btc",
            "name": "Bitcoin",
            "cooldown_hours": 24,
            "emergency_escalation_multiplier": 2.0,
            "normal_alert": 5,
            "major_alert": 8,
            "emergency_alert": 12,
            "alert_unit": "percent",
            "rules": [{"type": "percent_change", "threshold": 5}],
            "themes": ["crypto"],
            "category": "crypto",
            "quality": {},
        }
        ok, alert = check_alert(conn, settings, 50.0)
        self.assertTrue(ok)
        self.assertIsNotNone(alert)
        assert alert is not None
        self.assertEqual(alert.alert_tier, "emergency")
        conn.close()


class DecideScoreGateTests(unittest.TestCase):
    def test_below_threshold_returns_none(self) -> None:
        decision = decide_tweet_type(
            [_alert("btc", score=40)],
            {"high_single_threshold": 85, "multi_threshold": 120},
        )
        self.assertIsNone(decision)

    def test_high_score_posts_single(self) -> None:
        decision = decide_tweet_type(
            [_alert("btc", score=90)],
            {"high_single_threshold": 85, "multi_threshold": 120, "emergency_threshold": 90},
        )
        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision.tweet_type, "single")

    def test_standalone_major_posts_below_score(self) -> None:
        decision = decide_tweet_type(
            [_alert("cpi_yoy", score=40, standalone=True)],
            {"high_single_threshold": 85, "multi_threshold": 120},
        )
        self.assertIsNotNone(decision)

    def test_classify_below_threshold_gate(self) -> None:
        primary, secondary = classify_post_skip(
            gate="below_threshold", score=40, post_threshold=85
        )
        self.assertEqual(primary, "below_threshold")
        self.assertIsNone(secondary)


class CooldownTokenTests(unittest.TestCase):
    def test_btc_does_not_match_btc_funding(self) -> None:
        conn = _memory_db()
        record_post(
            conn,
            tweet_type="single",
            text="funding",
            alert_ids=[1],
            indicators=["btc_funding"],
            is_emergency=False,
            score=90,
            primary_category="crypto",
            themes=["crypto"],
        )
        hours = hours_since_indicator_post(conn, "btc")
        self.assertIsNone(hours)
        hours_funding = hours_since_indicator_post(conn, "btc_funding")
        self.assertIsNotNone(hours_funding)
        conn.close()

    def test_exact_token_matches(self) -> None:
        conn = _memory_db()
        record_post(
            conn,
            tweet_type="multi",
            text="multi",
            alert_ids=[1, 2],
            indicators=["btc", "eth"],
            is_emergency=False,
            score=100,
            primary_category="crypto",
            themes=["crypto"],
        )
        self.assertIsNotNone(hours_since_indicator_post(conn, "btc"))
        self.assertIsNotNone(hours_since_indicator_post(conn, "eth"))
        self.assertIsNone(hours_since_indicator_post(conn, "sol"))
        conn.close()


class PruneReadingsTests(unittest.TestCase):
    def test_prune_deletes_old_rows(self) -> None:
        conn = _memory_db()
        old = (datetime.now(timezone.utc) - timedelta(days=500)).isoformat()
        recent = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO readings (indicator, value, observed_at, recorded_at) VALUES (?,?,?,?)",
            ("btc", 1.0, old[:10], old),
        )
        conn.execute(
            "INSERT INTO readings (indicator, value, observed_at, recorded_at) VALUES (?,?,?,?)",
            ("btc", 2.0, recent[:10], recent),
        )
        conn.commit()
        deleted = prune_old_readings(conn, keep_days=400)
        self.assertEqual(deleted, 1)
        n = conn.execute("SELECT COUNT(*) AS n FROM readings").fetchone()["n"]
        self.assertEqual(n, 1)
        conn.close()


class OkxLiquidationPaginationTests(unittest.TestCase):
    def test_sums_across_pages(self) -> None:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        def _bucket(ord_id: str, ts: int, sz: float, side: str) -> dict:
            return {
                "ordId": ord_id,
                "details": [
                    {"time": ts, "sz": str(sz), "bkPx": "100", "posSide": side},
                ],
            }

        page1 = {
            "code": "0",
            "data": [_bucket(f"id{i}", now_ms - i * 1000, 10.0, "long") for i in range(100)],
        }
        page2 = {
            "code": "0",
            "data": [
                _bucket("id100", now_ms - 200_000, 5.0, "short"),
            ],
        }
        responses = [page1, page2]

        class FakeResp:
            def __init__(self, body: dict) -> None:
                self._body = body

            def json(self) -> dict:
                return self._body

        def fake_get(*_args, **kwargs):
            body = responses.pop(0)
            return FakeResp(body)

        with patch("src.crypto_metrics._http_get", side_effect=fake_get):
            total, long_usd, short_usd, _ts = _okx_liquidations("BTC-USDT", window_minutes=60)

        # page1: 100 * 10 * 100 = 100_000 long; page2: 5 * 100 = 500 short
        self.assertAlmostEqual(long_usd, 100_000.0)
        self.assertAlmostEqual(short_usd, 500.0)
        self.assertAlmostEqual(total, 100_500.0)


class TwitterRetryTests(unittest.TestCase):
    def test_transient_detection(self) -> None:
        class Exc(Exception):
            pass

        e = Exc("rate limit exceeded")
        self.assertTrue(_is_transient_twitter_error(e))
        self.assertFalse(_is_transient_twitter_error(Exc("forbidden")))

    @patch.dict(
        "os.environ",
        {
            "DRY_RUN": "0",
            "TWITTER_API_KEY": "k",
            "TWITTER_API_SECRET": "s",
            "TWITTER_ACCESS_TOKEN": "t",
            "TWITTER_ACCESS_TOKEN_SECRET": "ts",
        },
    )
    @patch("src.twitter_client.time.sleep")
    @patch("src.twitter_client.tweepy.Client")
    def test_retries_then_succeeds(self, mock_client_cls: MagicMock, _sleep: MagicMock) -> None:
        import tweepy

        client = mock_client_cls.return_value
        client.create_tweet.side_effect = [
            tweepy.TweepyException("rate limit"),
            None,
        ]
        post_tweet("hello")
        self.assertEqual(client.create_tweet.call_count, 2)


class EngineBatchMarkTests(unittest.TestCase):
    def test_only_posted_alerts_marked_processed(self) -> None:
        """Sibling alerts in the same flush window stay queued after a post."""
        from src.db import insert_pending_alert, mark_alerts_processed

        conn = _memory_db()
        id_a = insert_pending_alert(
            conn,
            indicator="btc",
            value=100,
            prev_value=90,
            reasons=["a"],
            rule_types=["percent_change"],
            themes=["crypto"],
            category="crypto",
            score=95,
            is_macro=False,
            triggered_at=datetime.now(timezone.utc).isoformat(),
            alert_tier="emergency",
        )
        id_b = insert_pending_alert(
            conn,
            indicator="vix",
            value=40,
            prev_value=20,
            reasons=["b"],
            rule_types=["percent_change"],
            themes=["risk_off"],
            category="equities_vol",
            score=70,
            is_macro=False,
            triggered_at=datetime.now(timezone.utc).isoformat(),
            alert_tier="normal",
        )
        # Simulate engine marking only the posted decision alerts
        mark_alerts_processed(conn, [id_a])
        pending = conn.execute(
            "SELECT id, indicator FROM pending_alerts WHERE processed = 0"
        ).fetchall()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["id"], id_b)
        self.assertEqual(pending[0]["indicator"], "vix")
        conn.close()


if __name__ == "__main__":
    unittest.main()
