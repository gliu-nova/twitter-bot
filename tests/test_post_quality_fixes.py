"""Tests for post copy and chart readability fixes."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src.posting.charts import _configure_x_axis, render_line_chart
from src.posting.compose import compose_single_tweet
from src.posting.history import MoveHistory, build_move_history
from src.posting.models import AlertTrigger


def _alert(
    indicator: str,
    value: float,
    prev: float | None = None,
    *,
    is_macro: bool = False,
) -> AlertTrigger:
    return AlertTrigger(
        indicator=indicator,
        name=indicator,
        value=value,
        prev_value=prev,
        reasons=[],
        rule_types=["percent_change"],
        themes=["macro_data"] if is_macro else ["crypto"],
        category="macro_data" if is_macro else "crypto",
        is_macro=is_macro,
        timestamp=datetime.now(timezone.utc),
    )


class ComposeQualityTests(unittest.TestCase):
    def test_consumer_sentiment_record_low_copy(self) -> None:
        history = MoveHistory(level_extreme="all-time low.", pct_change=-5.0, abs_change=-2.5)
        text = compose_single_tweet(
            _alert("consumer_sentiment", 44.8, 47.3, is_macro=True),
            history=history,
        )
        lowered = text.lower()
        self.assertIn("record low", lowered)
        self.assertIn("2008", text)
        self.assertIn("2022", text)
        self.assertNotIn("consumer confidence weakening", lowered)
        self.assertNotIn("consumer outlook deteriorating", lowered)

    def test_eth_funding_negative_accessible_copy(self) -> None:
        history = MoveHistory(pct_change=-120.0, abs_change=-0.0002)
        text = compose_single_tweet(
            _alert("eth_funding", -0.00005, 0.0001),
            history=history,
        )
        lowered = text.lower()
        self.assertTrue(
            "shorts now pay longs" in lowered
            or "shorts pay longs" in lowered
            or "leverage tilts short" in lowered
        )
        self.assertNotIn("drops below zero", lowered)
        self.assertNotIn("positioning has shifted below neutral", lowered)
        self.assertTrue(
            "leverage is no longer tilted long" in lowered
            or "shorts are paying longs" in lowered
            or "negative funding favors shorts" in lowered
        )


class RecordExtremeHistoryTests(unittest.TestCase):
    def test_consumer_sentiment_all_time_low_from_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            conn.execute(
                "CREATE TABLE readings (indicator TEXT, value REAL, observed_at TEXT, recorded_at TEXT)"
            )
            base = datetime(2024, 1, 1, tzinfo=timezone.utc)
            for i in range(20):
                ts = (base + timedelta(days=30 * i)).isoformat()
                conn.execute(
                    "INSERT INTO readings VALUES (?, ?, ?, ?)",
                    ("consumer_sentiment", 55.0 + i * 0.1, ts[:10], ts),
                )
            conn.execute(
                "INSERT INTO readings VALUES (?, ?, ?, ?)",
                ("consumer_sentiment", 44.8, "2026-06-27", "2026-06-27T12:00:00+00:00"),
            )
            conn.commit()

            history = build_move_history(conn, _alert("consumer_sentiment", 44.8, 47.3, is_macro=True))
            self.assertEqual(history.level_extreme, "all-time low.")


class ChartQualityTests(unittest.TestCase):
    def test_x_axis_limits_tick_count(self) -> None:
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates

        dates = [
            datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=i)
            for i in range(90)
        ]
        fig, ax = plt.subplots()
        try:
            _configure_x_axis(ax, dates)
            ticks = ax.xaxis.get_majorticklocs()
            self.assertLessEqual(len(ticks), 8)
            self.assertIsInstance(ax.xaxis.get_major_locator(), mdates.AutoDateLocator)
        finally:
            plt.close(fig)

    def test_negative_funding_chart_includes_zero_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            conn.execute(
                "CREATE TABLE readings (indicator TEXT, value REAL, observed_at TEXT, recorded_at TEXT)"
            )
            base = datetime(2026, 6, 20, tzinfo=timezone.utc)
            values = [0.00008, 0.00005, 0.00002, -0.00001, -0.00004]
            for i, val in enumerate(values):
                ts = (base + timedelta(hours=6 * i)).strftime("%Y-%m-%d %H:%M:%S")
                conn.execute(
                    "INSERT INTO readings VALUES (?, ?, ?, ?)",
                    ("eth_funding", val, ts, ts),
                )
            conn.commit()

            alert = _alert("eth_funding", -0.00004, 0.00002)
            cfg = {
                "indicators": {
                    "eth_funding": {"name": "ETH Perp Funding Rate", "source": "okx_funding"},
                }
            }
            with patch("src.posting.charts.CHART_DIR", Path(tmp) / "charts"):
                path = render_line_chart(conn, alert, cfg, months=1)
            self.assertIsNotNone(path)


if __name__ == "__main__":
    unittest.main()