from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "indicators.db"


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS readings (
            indicator TEXT NOT NULL,
            value REAL NOT NULL,
            observed_at TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            PRIMARY KEY (indicator, recorded_at)
        );
        CREATE TABLE IF NOT EXISTS alert_log (
            indicator TEXT PRIMARY KEY,
            last_value REAL NOT NULL,
            last_alert_at TEXT
        );
    """)
    return conn


def last_reading(conn: sqlite3.Connection, indicator: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT value, observed_at, recorded_at FROM readings WHERE indicator = ? ORDER BY recorded_at DESC LIMIT 1",
        (indicator,),
    ).fetchone()


def hours_since_last_fetch(conn: sqlite3.Connection, indicator: str) -> float | None:
    row = last_reading(conn, indicator)
    if not row:
        return None
    recorded = datetime.fromisoformat(row["recorded_at"])
    return (datetime.now(timezone.utc) - recorded).total_seconds() / 3600


def save_reading(conn: sqlite3.Connection, indicator: str, value: float, observed_at: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO readings (indicator, value, observed_at, recorded_at) VALUES (?, ?, ?, ?)",
        (indicator, value, observed_at, now),
    )
    conn.commit()


def last_alert(conn: sqlite3.Connection, indicator: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT last_value, last_alert_at FROM alert_log WHERE indicator = ?",
        (indicator,),
    ).fetchone()


def record_alert(conn: sqlite3.Connection, indicator: str, value: float) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO alert_log (indicator, last_value, last_alert_at) VALUES (?, ?, ?)
           ON CONFLICT(indicator) DO UPDATE SET last_value = excluded.last_value, last_alert_at = excluded.last_alert_at""",
        (indicator, value, now),
    )
    conn.commit()