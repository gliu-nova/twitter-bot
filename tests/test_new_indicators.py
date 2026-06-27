"""Tests for newly added ETF, treasury, and dark-pool indicators."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.config import indicator_settings, load_config
from src.market_memory_bridge import INDICATOR_MEMORY_MAP
from src.scheduler import RATES_FX_KEYS, US_EQUITY_KEYS, poll_schedule


NEW_KEYS = (
    "treasury_2y",
    "qqq",
    "bond_etf_agg",
    "bond_etf_bnd",
    "crypto_etf_ibit",
    "crypto_etf_fbtc",
    "dark_pool_spy",
)


class NewIndicatorConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cfg = load_config()

    def test_all_keys_present_in_config(self) -> None:
        for key in NEW_KEYS:
            self.assertIn(key, self.cfg["indicators"], key)
            self.assertIn(key, self.cfg["posting"]["indicator_themes"], key)

    def test_scheduler_groups(self) -> None:
        self.assertIn("treasury_2y", RATES_FX_KEYS)
        for key in NEW_KEYS:
            if key == "treasury_2y":
                continue
            self.assertIn(key, US_EQUITY_KEYS, key)

    def test_poll_schedules(self) -> None:
        self.assertEqual(poll_schedule(indicator_settings(self.cfg, "treasury_2y")), "rates_fx")
        self.assertEqual(poll_schedule(indicator_settings(self.cfg, "qqq")), "us_equity")
        self.assertEqual(poll_schedule(indicator_settings(self.cfg, "dark_pool_spy")), "us_equity")

    def test_market_memory_mappings(self) -> None:
        for key in NEW_KEYS:
            self.assertIn(key, INDICATOR_MEMORY_MAP, key)

    def test_custom_alert_modes(self) -> None:
        for key in ("qqq", "dark_pool_spy", "crypto_etf_ibit"):
            settings = indicator_settings(self.cfg, key)
            self.assertEqual(settings.get("alert_mode"), "custom")
            self.assertEqual(settings["rules"], [])


class NewIndicatorFetchTests(unittest.TestCase):
    @patch("src.fetch._fred_latest", return_value=(3.95, "2026-06-25"))
    def test_fetch_treasury_2y(self, _mock: unittest.mock.MagicMock) -> None:
        from src.fetch import fetch_indicator

        cfg = load_config()
        value, observed = fetch_indicator(indicator_settings(cfg, "treasury_2y"))
        self.assertAlmostEqual(value, 3.95)
        self.assertEqual(observed, "2026-06-25")

    @patch("src.fetch._yahoo_latest", return_value=(520.1, "2026-06-25"))
    def test_fetch_qqq(self, _mock: unittest.mock.MagicMock) -> None:
        from src.fetch import fetch_indicator

        cfg = load_config()
        value, observed = fetch_indicator(indicator_settings(cfg, "qqq"))
        self.assertAlmostEqual(value, 520.1)
        self.assertEqual(observed, "2026-06-25")

    @patch("src.finra_dark_pool.fetch_finra_dark_pool_both", return_value=(17.5, 52.4, "2026-06-25"))
    def test_fetch_dark_pool_spy(self, _mock: unittest.mock.MagicMock) -> None:
        from src.fetch import fetch_indicator

        cfg = load_config()
        value, observed = fetch_indicator(indicator_settings(cfg, "dark_pool_spy"))
        self.assertAlmostEqual(value, 17.5)
        self.assertEqual(observed, "2026-06-25")

    @patch("src.etf_activity.fetch_etf_activity")
    def test_fetch_ibit_activity(self, mock_snap: unittest.mock.MagicMock) -> None:
        from src.etf_activity import EtfActivitySnapshot
        from src.fetch import fetch_indicator

        mock_snap.return_value = EtfActivitySnapshot(
            symbol="IBIT",
            price=35.0,
            volume=50_000_000,
            avg_volume_30d=40_000_000,
            net_assets_usd=58e9,
            flow_usd=None,
            options_volume=100_000,
            options_pcr=1.2,
            volume_percentile=80.0,
            observed_at="2026-06-25",
        )
        cfg = load_config()
        value, observed = fetch_indicator(indicator_settings(cfg, "crypto_etf_ibit"))
        self.assertAlmostEqual(value, 50_000_000)
        self.assertEqual(observed, "2026-06-25")


if __name__ == "__main__":
    unittest.main()