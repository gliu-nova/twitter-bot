"""Outcome ledger: follow-through, confirmation, monthly page."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.outcome_ledger import (
    MonthStats,
    expected_direction,
    process_outcome_ledger,
    register_fired_alert,
    write_outcome_page,
)
from src.posting.models import AlertTrigger


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE readings (
            indicator TEXT NOT NULL,
            value REAL NOT NULL,
            observed_at TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            PRIMARY KEY (indicator, recorded_at)
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
        CREATE TABLE outcome_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pending_alert_id INTEGER UNIQUE,
            indicator TEXT NOT NULL,
            fired_at TEXT NOT NULL,
            value REAL NOT NULL,
            prev_value REAL,
            expected_direction INTEGER NOT NULL,
            alert_tier TEXT NOT NULL DEFAULT 'normal',
            is_macro INTEGER NOT NULL DEFAULT 0,
            posted INTEGER NOT NULL DEFAULT 0,
            move_4h_pct REAL,
            move_24h_pct REAL,
            move_5d_pct REAL,
            resolved_4h INTEGER,
            resolved_24h INTEGER,
            resolved_5d INTEGER,
            confirmed INTEGER NOT NULL DEFAULT 0,
            confirmed_by TEXT,
            confirmed_at TEXT,
            hours_to_confirm REAL,
            UNIQUE(indicator, fired_at)
        );
        """
    )
    return conn


def _alert(
    indicator: str,
    value: float,
    prev: float,
    *,
    fired: datetime,
    is_macro: bool = False,
    db_id: int = 1,
    reasons: list[str] | None = None,
) -> AlertTrigger:
    return AlertTrigger(
        indicator=indicator,
        name=indicator,
        value=value,
        prev_value=prev,
        reasons=reasons or ["moved"],
        rule_types=["percent_change"],
        themes=["equities"],
        category="equities_vol",
        is_macro=is_macro,
        timestamp=fired,
        alert_tier="normal",
        db_id=db_id,
    )


def _reading(conn: sqlite3.Connection, indicator: str, value: float, ts: datetime) -> None:
    conn.execute(
        "INSERT INTO readings (indicator, value, observed_at, recorded_at) VALUES (?, ?, ?, ?)",
        (indicator, value, ts.isoformat(), ts.isoformat()),
    )
    conn.commit()


CFG = {
    "defaults": {},
    "indicators": {
        "sp500": {"name": "S&P 500", "source": "yahoo", "normal_alert": 2},
        "vix": {"name": "VIX", "source": "yahoo", "normal_alert": 5},
        "btc": {"name": "Bitcoin", "source": "yahoo", "normal_alert": 5},
    },
    "posting": {"indicator_themes": {}},
    "market_memory": {"enabled": False},
}


class ExpectedDirectionTests(unittest.TestCase):
    def test_down_move_and_cross_below(self) -> None:
        fired = datetime(2026, 9, 1, tzinfo=timezone.utc)
        self.assertEqual(expected_direction(_alert("sp500", 5600, 5800, fired=fired)), -1)
        up = _alert("vix", 31, 18, fired=fired, reasons=["crossed above 30"])
        up.rule_types = ["crosses_above"]
        self.assertEqual(expected_direction(up), 1)


class ResolveAndStatsTests(unittest.TestCase):
    def test_24h_follow_through_is_resolved(self) -> None:
        conn = _conn()
        fired = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
        alert = _alert("sp500", 5600, 5800, fired=fired)
        register_fired_alert(conn, alert)
        _reading(conn, "sp500", 5600, fired)
        _reading(conn, "sp500", 5400, fired + timedelta(hours=24))

        stats = process_outcome_ledger(
            conn, CFG, now=fired + timedelta(hours=30), page_path=Path(tempfile.mkdtemp()) / "out.html",
        )
        row = conn.execute("SELECT * FROM outcome_ledger").fetchone()
        self.assertLess(row["move_24h_pct"], 0)
        self.assertEqual(row["resolved_24h"], 1)
        self.assertEqual(stats.alerts_fired, 1)
        self.assertEqual(stats.resolved_pct, 100.0)
        self.assertEqual(stats.false_alarm_pct, 0.0)
        conn.close()

    def test_opposite_24h_is_false_alarm(self) -> None:
        conn = _conn()
        fired = datetime(2026, 9, 2, 12, tzinfo=timezone.utc)
        alert = _alert("btc", 100, 110, fired=fired)  # down alert
        register_fired_alert(conn, alert)
        _reading(conn, "btc", 100, fired)
        _reading(conn, "btc", 108, fired + timedelta(hours=24))  # bounced

        stats = process_outcome_ledger(
            conn, CFG, now=fired + timedelta(hours=30), page_path=Path(tempfile.mkdtemp()) / "out.html",
        )
        row = conn.execute("SELECT resolved_24h, move_24h_pct FROM outcome_ledger").fetchone()
        self.assertEqual(row["resolved_24h"], 0)
        self.assertGreater(row["move_24h_pct"], 0)
        self.assertEqual(stats.false_alarm_pct, 100.0)
        self.assertEqual(stats.false_alarm_by_indicator[0]["indicator"], "btc")
        conn.close()

    def test_vix_confirms_spx_down_and_records_ttc(self) -> None:
        conn = _conn()
        fired = datetime(2026, 9, 3, 14, tzinfo=timezone.utc)
        alert = _alert("sp500", 5600, 5800, fired=fired)
        register_fired_alert(conn, alert)
        _reading(conn, "sp500", 5600, fired)
        _reading(conn, "vix", 18, fired - timedelta(minutes=10))
        confirm_at = fired + timedelta(hours=3)
        _reading(conn, "vix", 22, confirm_at)  # +22%

        stats = process_outcome_ledger(
            conn, CFG, now=fired + timedelta(hours=6), page_path=Path(tempfile.mkdtemp()) / "out.html",
        )
        row = conn.execute("SELECT * FROM outcome_ledger").fetchone()
        self.assertEqual(row["confirmed"], 1)
        self.assertEqual(row["confirmed_by"], "vix")
        self.assertAlmostEqual(row["hours_to_confirm"], 3.0, places=1)
        self.assertEqual(stats.median_hours_to_confirm, 3.0)
        conn.close()

    def test_pending_until_24h_window(self) -> None:
        conn = _conn()
        fired = datetime(2026, 9, 4, 12, tzinfo=timezone.utc)
        register_fired_alert(conn, _alert("sp500", 5600, 5800, fired=fired))
        _reading(conn, "sp500", 5550, fired + timedelta(hours=4))
        stats = process_outcome_ledger(
            conn, CFG, now=fired + timedelta(hours=5), page_path=Path(tempfile.mkdtemp()) / "out.html",
        )
        row = conn.execute("SELECT resolved_4h, resolved_24h FROM outcome_ledger").fetchone()
        self.assertEqual(row["resolved_4h"], 1)
        self.assertIsNone(row["resolved_24h"])
        self.assertEqual(stats.pending, 1)
        self.assertEqual(stats.scored, 0)
        conn.close()

    def test_macro_scores_on_5d(self) -> None:
        conn = _conn()
        fired = datetime(2026, 9, 1, 16, tzinfo=timezone.utc)
        alert = _alert("jobless_claims", 250, 220, fired=fired, is_macro=True)
        register_fired_alert(conn, alert)
        _reading(conn, "jobless_claims", 250, fired)
        _reading(conn, "jobless_claims", 280, fired + timedelta(days=5))
        stats = process_outcome_ledger(
            conn, CFG, now=fired + timedelta(days=6), page_path=Path(tempfile.mkdtemp()) / "out.html",
        )
        row = conn.execute("SELECT resolved_5d FROM outcome_ledger").fetchone()
        self.assertEqual(row["resolved_5d"], 1)
        self.assertEqual(stats.resolved_pct, 100.0)
        conn.close()

    def test_page_lists_the_four_headline_stats(self) -> None:
        stats = MonthStats(
            month="2026-09",
            alerts_fired=12,
            scored=10,
            resolved=6,
            resolved_pct=60.0,
            false_alarms=4,
            false_alarm_pct=40.0,
            median_hours_to_confirm=3.2,
            false_alarm_by_indicator=[
                {"indicator": "btc", "label": "BTC", "scored": 5, "false_alarms": 2, "false_alarm_pct": 40.0},
            ],
        )
        path = Path(tempfile.mkdtemp()) / "outcome_ledger.html"
        write_outcome_page(stats, path)
        text = path.read_text(encoding="utf-8")
        self.assertIn("Alerts fired this month", text)
        self.assertIn("% resolved in expected direction", text)
        self.assertIn("False-alarm rate", text)
        self.assertIn("Median time-to-confirmation", text)
        self.assertIn("12", text)
        self.assertIn("60%", text)
        self.assertIn("BTC", text)

    def test_backfill_from_pending_alerts(self) -> None:
        conn = _conn()
        fired = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
        conn.execute(
            """INSERT INTO pending_alerts
               (indicator, value, prev_value, reasons, rule_types, themes, category, score, is_macro, triggered_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("gold", 2100, 2000, "moved 5% up", "percent_change", "risk_off", "commodities", 70, 0, fired.isoformat()),
        )
        conn.commit()
        process_outcome_ledger(
            conn, CFG, now=fired + timedelta(hours=1), page_path=Path(tempfile.mkdtemp()) / "out.html",
        )
        row = conn.execute("SELECT indicator, expected_direction FROM outcome_ledger").fetchone()
        self.assertEqual(row["indicator"], "gold")
        self.assertEqual(row["expected_direction"], 1)
        conn.close()


if __name__ == "__main__":
    unittest.main()
