"""Tests for market-memory Blockscout → twitter-bot bridge."""

from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import MagicMock, patch

from src.blockscout_bridge import (
    TRADER_INDICATOR,
    blockscout_whale_to_alert,
    process_blockscout_for_bot,
    trader_result_to_alert,
)
from src.posting.compose import compose_single_tweet
from src.posting.history import MoveHistory


class TestBlockscoutWhaleConversion(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = {
            "defaults": {},
            "posting": {
                "score_weights": {"magnitude": 0.45, "rarity": 0.30, "audience": 0.25},
                "alert_max_age_hours": 48,
            },
            "indicators": {
                "eth_whale": {
                    "name": "ETH whale transfer",
                    "source": "etherscan",
                    "alert_mode": "custom",
                    "alert_unit": "absolute",
                    "normal_alert": 100,
                    "major_alert": 500,
                    "emergency_alert": 2000,
                    "standalone_major": True,
                    "themes": ["crypto"],
                    "category": "crypto",
                    "rarity": 90,
                    "audience_relevance": 90,
                    "quality": {"schedule": "crypto_24_7"},
                },
                "eth_trader": {
                    "name": "High-EV on-chain trader",
                    "source": "blockscout",
                    "alert_mode": "custom",
                    "alert_unit": "absolute",
                    "normal_alert": 75,
                    "major_alert": 85,
                    "emergency_alert": 92,
                    "themes": ["crypto"],
                    "category": "crypto",
                    "rarity": 82,
                    "audience_relevance": 70,
                    "quality": {"schedule": "crypto_24_7"},
                },
            },
            "blockscout": {
                "enabled": True,
                "trader_min_score": 75,
            },
        }

    def test_blockscout_whale_to_alert(self) -> None:
        raw = {
            "tx_hash": "0xbswhale",
            "chain_id": 1,
            "value_eth": 300.0,
            "from_address": "0xaaa",
            "to_address": "0xbbb",
            "watched_address": "0xaaa",
            "label": "whale-wallet",
            "timestamp": "2026-01-15T12:00:00Z",
        }
        alert = blockscout_whale_to_alert(raw, cfg=self.cfg, instance="ethereum")
        self.assertEqual(alert.indicator, "eth_whale")
        self.assertEqual(alert.value, 300.0)
        self.assertTrue(any("source:blockscout" in r for r in alert.reasons))
        self.assertTrue(any("blockscout.com" in r for r in alert.reasons))

    def test_trader_result_to_alert(self) -> None:
        result = {
            "address": "0xtrader00000000000000000000000000000001",
            "chain_id": 1,
            "instance": "ethereum",
            "label": "alpha",
            "trader_score": 82.5,
            "txs_inserted": 40,
        }
        alert = trader_result_to_alert(result, cfg=self.cfg)
        self.assertIsNotNone(alert)
        assert alert is not None
        self.assertEqual(alert.indicator, TRADER_INDICATOR)
        self.assertEqual(alert.value, 82.5)
        self.assertEqual(alert.alert_tier, "normal")

    def test_compose_trader_tweet(self) -> None:
        result = {
            "address": "0xtrader00000000000000000000000000000001",
            "chain_id": 1,
            "instance": "ethereum",
            "label": "alpha",
            "trader_score": 88.0,
            "txs_inserted": 40,
        }
        alert = trader_result_to_alert(result, cfg=self.cfg)
        assert alert is not None
        text = compose_single_tweet(alert, history=MoveHistory(), posting_cfg=self.cfg["posting"])
        self.assertIn("88", text)
        self.assertIn("alpha", text)


class TestProcessBlockscoutForBot(unittest.TestCase):
    def test_queues_whales_and_traders(self) -> None:
        cfg = {
            "defaults": {},
            "posting": {
                "score_weights": {"magnitude": 0.45, "rarity": 0.30, "audience": 0.25},
                "alert_max_age_hours": 48,
            },
            "indicators": {
                "eth_whale": {
                    "name": "ETH whale transfer",
                    "source": "etherscan",
                    "alert_mode": "custom",
                    "alert_unit": "absolute",
                    "normal_alert": 100,
                    "major_alert": 500,
                    "emergency_alert": 2000,
                    "standalone_major": True,
                    "themes": ["crypto"],
                    "category": "crypto",
                    "rarity": 90,
                    "audience_relevance": 90,
                    "quality": {"schedule": "crypto_24_7"},
                },
                "eth_trader": {
                    "name": "High-EV on-chain trader",
                    "source": "blockscout",
                    "alert_mode": "custom",
                    "alert_unit": "absolute",
                    "normal_alert": 75,
                    "major_alert": 85,
                    "emergency_alert": 92,
                    "themes": ["crypto"],
                    "category": "crypto",
                    "rarity": 82,
                    "audience_relevance": 70,
                    "quality": {"schedule": "crypto_24_7"},
                },
            },
            "blockscout": {
                "enabled": True,
                "max_whales_per_run": 2,
                "max_traders_per_run": 1,
                "trader_min_score": 70,
            },
        }
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE pending_alerts (
                id INTEGER PRIMARY KEY,
                indicator TEXT,
                value REAL,
                prev_value REAL,
                reasons TEXT,
                rule_types TEXT,
                themes TEXT,
                category TEXT,
                score REAL,
                is_macro INTEGER,
                triggered_at TEXT,
                alert_tier TEXT,
                processed INTEGER DEFAULT 0
            )
            """
        )
        conn.commit()

        ingest_report = {
            "skipped": False,
            "instance": "ethereum",
            "entries": 1,
            "whales_new": 1,
            "traders_seen": 1,
            "results": [
                {
                    "address": "0xtrader00000000000000000000000000000001",
                    "label": "alpha",
                    "chain_id": 1,
                    "instance": "ethereum",
                    "trader_score": 80.0,
                    "txs_inserted": 25,
                    "whales": [
                        {
                            "tx_hash": "0xbs1",
                            "value_eth": 150.0,
                            "from_address": "0xaaa",
                            "to_address": "0xbbb",
                            "label": "alpha",
                        }
                    ],
                }
            ],
        }

        queued: list = []

        def fake_enqueue(conn, cfg, alert, *, queue_reason=""):
            queued.append(alert)
            return 1

        with patch("src.blockscout_bridge.maybe_ingest_blockscout", return_value=ingest_report):
            report = process_blockscout_for_bot(conn, cfg, enqueue_fn=fake_enqueue)

        self.assertEqual(report["whales_queued"], 1)
        self.assertEqual(report["traders_queued"], 1)
        self.assertEqual(len(queued), 2)
        indicators = {a.indicator for a in queued}
        self.assertEqual(indicators, {"eth_whale", "eth_trader"})

    def test_disabled_skips(self) -> None:
        conn = MagicMock()
        report = process_blockscout_for_bot(conn, {"blockscout": {"enabled": False}})
        self.assertTrue(report.get("skipped"))


if __name__ == "__main__":
    unittest.main()