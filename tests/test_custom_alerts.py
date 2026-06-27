"""Tests for percentile / multi-metric custom alert checkers."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src.config import indicator_settings, load_config
from src.posting.compose import hydrate_aux_from_reasons
from src.custom_alerts import (
    check_dark_pool_alert,
    check_etf_activity_alert,
    check_qqq_alert,
)
from src.db import connect, save_reading
from src.etf_activity import EtfActivitySnapshot
from src.posting.models import AlertTrigger


class CustomAlertTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = connect()
        self.cfg = load_config()

    def tearDown(self) -> None:
        self.conn.close()

    def _seed_daily(self, indicator: str, closes: list[float], start: str = "2026-01-02") -> None:
        day = datetime.fromisoformat(start)
        for price in closes:
            save_reading(
                self.conn,
                indicator,
                price,
                day.strftime("%Y-%m-%d"),
            )
            day += timedelta(days=1)

    def test_qqq_fires_on_fixed_threshold(self) -> None:
        self._seed_daily("qqq", [100.0, 100.0])
        settings = indicator_settings(self.cfg, "qqq")
        ok, alert = check_qqq_alert(self.conn, settings, 103.0)
        self.assertTrue(ok)
        self.assertIsNotNone(alert)
        assert alert is not None
        self.assertTrue(any("2.5%" in r or "3.0%" in r for r in alert.reasons))

    def test_qqq_fires_on_percentile_move(self) -> None:
        base = [100.0]
        for _ in range(80):
            base.append(base[-1] * 1.001)
        self._seed_daily("qqq", base)
        settings = indicator_settings(self.cfg, "qqq")
        ok, alert = check_qqq_alert(self.conn, settings, base[-1] * 1.04)
        self.assertTrue(ok)
        self.assertIsNotNone(alert)
        assert alert is not None
        self.assertTrue(any("pct daily" in r for r in alert.reasons))

    @patch("src.custom_alerts._dark_pool_history")
    def test_dark_pool_combines_volume_and_pct(self, mock_hist: unittest.mock.MagicMock) -> None:
        vol_hist = [10.0 + (i % 5) for i in range(120)]
        pct_hist = [45.0 + (i % 3) for i in range(120)]
        mock_hist.return_value = (vol_hist, pct_hist)

        settings = indicator_settings(self.cfg, "dark_pool_spy")
        ok, alert = check_dark_pool_alert(self.conn, settings, volume=25.0, pct=62.0)
        self.assertTrue(ok)
        self.assertIsNotNone(alert)
        assert alert is not None
        self.assertEqual(alert.value, 25.0)
        self.assertEqual(alert.aux_value, 62.0)
        self.assertTrue(any("volume" in r.lower() for r in alert.reasons))
        self.assertTrue(any("dark pool %" in r.lower() for r in alert.reasons))

    def test_etf_activity_unusual_volume(self) -> None:
        save_reading(self.conn, "crypto_etf_ibit", 30_000_000, "2026-06-24", aux_value=50e9)
        snap = EtfActivitySnapshot(
            symbol="IBIT",
            price=35.0,
            volume=75_000_000,
            avg_volume_30d=40_000_000,
            net_assets_usd=58.5e9,
            flow_usd=None,
            options_volume=200_000,
            options_pcr=2.8,
            volume_percentile=96.0,
            observed_at="2026-06-25",
        )
        settings = indicator_settings(self.cfg, "crypto_etf_ibit")
        ok, alert = check_etf_activity_alert(self.conn, settings, snap)
        self.assertTrue(ok)
        self.assertIsNotNone(alert)
        assert alert is not None
        self.assertTrue(any("volume" in r.lower() for r in alert.reasons))
        self.assertTrue(any("flow" in r.lower() for r in alert.reasons))
        self.assertTrue(any("options" in r.lower() for r in alert.reasons))

    def test_hydrate_aux_from_reasons(self) -> None:
        alert = AlertTrigger(
            indicator="dark_pool_spy",
            name="Dark Pool",
            value=18.0,
            prev_value=15.0,
            reasons=["aux_value:52.4", "flow_usd:125000000", "options_vol:200000", "options_pcr:2.75"],
            rule_types=["volume_percentile_95"],
            themes=["equities"],
            category="equities_vol",
            is_macro=False,
            timestamp=datetime.now(timezone.utc),
        )
        hydrate_aux_from_reasons(alert)
        self.assertEqual(alert.aux_value, 52.4)
        self.assertEqual(alert.flow_usd, 125_000_000)
        self.assertEqual(alert.options_volume, 200_000)
        self.assertAlmostEqual(alert.options_pcr, 2.75)


if __name__ == "__main__":
    unittest.main()