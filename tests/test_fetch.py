"""Tests for fetch helpers."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from src.fetch import FetchError, _yahoo_latest


class YahooLatestTests(unittest.TestCase):
    @patch("src.fetch.yf.Ticker")
    def test_uses_last_valid_close_when_latest_row_is_nan(self, mock_ticker_cls: MagicMock) -> None:
        hist = pd.DataFrame(
            {
                "Close": [100.0, float("nan")],
            },
            index=pd.to_datetime(["2026-06-25", "2026-06-26"]),
        )
        mock_ticker_cls.return_value.history.return_value = hist

        value, observed = _yahoo_latest("QQQ")

        self.assertEqual(value, 100.0)
        self.assertEqual(observed, "2026-06-25")

    @patch("src.fetch.yf.Ticker")
    def test_raises_when_all_closes_are_nan(self, mock_ticker_cls: MagicMock) -> None:
        hist = pd.DataFrame(
            {"Close": [float("nan"), float("nan")]},
            index=pd.to_datetime(["2026-06-25", "2026-06-26"]),
        )
        mock_ticker_cls.return_value.history.return_value = hist

        with self.assertRaises(FetchError):
            _yahoo_latest("QQQ")


if __name__ == "__main__":
    unittest.main()
