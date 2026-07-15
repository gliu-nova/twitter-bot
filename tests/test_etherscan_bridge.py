"""Tests for market-memory Etherscan → twitter-bot whale bridge."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.etherscan_bridge import (
    WHALE_INDICATOR,
    parse_whale_meta,
    process_etherscan_for_bot,
    whale_dict_to_alert,
)
from src.posting.compose import compose_single_tweet, should_attach_chart
from src.posting.history import MoveHistory
from src.posting.scoring import calculate_score


class TestWhaleAlertConversion(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = {
            "defaults": {},
            "posting": {
                "score_weights": {"magnitude": 0.45, "rarity": 0.30, "audience": 0.25},
                "alert_max_age_hours": 48,
                "high_single_threshold": 85,
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
                    "themes": ["crypto", "risk_on"],
                    "category": "crypto",
                    "rarity": 90,
                    "audience_relevance": 90,
                    "quality": {"schedule": "crypto_24_7"},
                }
            },
            "etherscan": {
                "enabled": True,
                "whale_threshold_eth": 100,
                "major_eth": 500,
                "emergency_eth": 2000,
                "max_whales_per_run": 3,
            },
        }

    def test_whale_dict_to_alert(self) -> None:
        raw = {
            "tx_hash": "0xabc",
            "chain_id": 1,
            "chain_name": "ethereum",
            "value_eth": 250.5,
            "from_address": "0xfromaddr000000000000000000000000000001",
            "to_address": "0xtoaddr00000000000000000000000000000002",
            "watched_address": "0xfromaddr000000000000000000000000000001",
            "label": "vitalik",
            "time_stamp": 1700000000,
            "explorer_url": "https://etherscan.io/tx/0xabc",
            "threshold_eth": 100.0,
        }
        alert = whale_dict_to_alert(raw, cfg=self.cfg)
        self.assertEqual(alert.indicator, WHALE_INDICATOR)
        self.assertEqual(alert.value, 250.5)
        self.assertEqual(alert.alert_tier, "normal")
        self.assertTrue(alert.standalone_major)
        meta = parse_whale_meta(alert)
        self.assertEqual(meta["tx"], "0xabc")
        self.assertEqual(meta["label"], "vitalik")
        self.assertEqual(meta["chain"], "ethereum")

    def test_tier_major_and_emergency(self) -> None:
        major = whale_dict_to_alert(
            {
                "tx_hash": "0x1",
                "value_eth": 600,
                "chain_name": "ethereum",
                "from_address": "0xa",
                "to_address": "0xb",
                "time_stamp": 1700000000,
            },
            cfg=self.cfg,
        )
        self.assertEqual(major.alert_tier, "major")
        emergency = whale_dict_to_alert(
            {
                "tx_hash": "0x2",
                "value_eth": 5000,
                "chain_name": "ethereum",
                "from_address": "0xa",
                "to_address": "0xb",
                "time_stamp": 1700000000,
            },
            cfg=self.cfg,
        )
        self.assertEqual(emergency.alert_tier, "emergency")

    def test_compose_whale_tweet(self) -> None:
        raw = {
            "tx_hash": "0xdeadbeef",
            "chain_name": "ethereum",
            "value_eth": 150.0,
            "from_address": "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
            "to_address": "0x1111111111111111111111111111111111111111",
            "label": "vitalik",
            "explorer_url": "https://etherscan.io/tx/0xdeadbeef",
            "time_stamp": 1700000000,
        }
        alert = whale_dict_to_alert(raw, cfg=self.cfg)
        text = compose_single_tweet(alert, history=MoveHistory(), posting_cfg=self.cfg["posting"])
        self.assertIn("150", text)
        self.assertIn("ETH", text)
        self.assertIn("etherscan.io", text)
        self.assertFalse(
            should_attach_chart(alert, MoveHistory(), self.cfg["posting"], is_emergency=False)
        )

    def test_score_posts_as_standalone(self) -> None:
        from datetime import datetime, timezone

        from src.config import indicator_settings

        alert = whale_dict_to_alert(
            {
                "tx_hash": "0xabc",
                "value_eth": 150,
                "chain_name": "ethereum",
                "from_address": "0xa",
                "to_address": "0xb",
                "time_stamp": int(datetime.now(timezone.utc).timestamp()),
            },
            cfg=self.cfg,
        )
        settings = indicator_settings(self.cfg, WHALE_INDICATOR)
        score = calculate_score(alert, settings, self.cfg["posting"])
        self.assertIsNotNone(score)
        assert score is not None
        self.assertGreaterEqual(score, 85.0)


class TestProcessEtherscanForBot(unittest.TestCase):
    def test_queues_whales_from_ingest(self) -> None:
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
                }
            },
            "etherscan": {"enabled": True, "max_whales_per_run": 2},
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
            "entries": 1,
            "whales_new": 1,
            "results": [
                {
                    "whale_alerts": [
                        {
                            "tx_hash": "0xnewwhale",
                            "chain_id": 1,
                            "chain_name": "ethereum",
                            "value_eth": 200.0,
                            "from_address": "0xaaa",
                            "to_address": "0xbbb",
                            "label": "test",
                            "time_stamp": 1700000000,
                            "explorer_url": "https://etherscan.io/tx/0xnewwhale",
                        }
                    ]
                }
            ],
        }

        queued: list = []

        def fake_enqueue(conn, cfg, alert, *, queue_reason=""):
            queued.append(alert)
            return 1

        with patch("src.etherscan_bridge.maybe_ingest_etherscan", return_value=ingest_report):
            report = process_etherscan_for_bot(conn, cfg, enqueue_fn=fake_enqueue)

        self.assertEqual(report["whales_queued"], 1)
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0].value, 200.0)

    def test_disabled_skips(self) -> None:
        conn = MagicMock()
        report = process_etherscan_for_bot(conn, {"etherscan": {"enabled": False}})
        self.assertTrue(report.get("skipped"))


if __name__ == "__main__":
    unittest.main()
