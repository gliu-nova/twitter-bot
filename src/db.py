from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
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
        CREATE TABLE IF NOT EXISTS pending_alerts (
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
            processed INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS post_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            posted_at TEXT NOT NULL,
            tweet_type TEXT NOT NULL,
            text TEXT NOT NULL,
            alert_ids TEXT NOT NULL,
            indicators TEXT NOT NULL,
            is_emergency INTEGER NOT NULL DEFAULT 0,
            score REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tweet_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            posted_at TEXT NOT NULL,
            primary_category TEXT NOT NULL,
            themes TEXT NOT NULL
        );
    """)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(pending_alerts)")}
    if "alert_tier" not in cols:
        conn.execute(
            "ALTER TABLE pending_alerts ADD COLUMN alert_tier TEXT NOT NULL DEFAULT 'normal'"
        )
        conn.commit()
    reading_cols = {row[1] for row in conn.execute("PRAGMA table_info(readings)")}
    for col in ("liq_long_usd", "liq_short_usd", "aux_value"):
        if col not in reading_cols:
            conn.execute(f"ALTER TABLE readings ADD COLUMN {col} REAL")
    conn.commit()
    return conn


def last_reading(conn: sqlite3.Connection, indicator: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT value, observed_at, recorded_at FROM readings WHERE indicator = ? ORDER BY recorded_at DESC LIMIT 1",
        (indicator,),
    ).fetchone()


def hours_since_last_fetch(conn: sqlite3.Connection, indicator: str) -> float | None:
    mins = minutes_since_last_fetch(conn, indicator)
    if mins is None:
        return None
    return mins / 60


def minutes_since_last_fetch(conn: sqlite3.Connection, indicator: str) -> float | None:
    row = last_reading(conn, indicator)
    if not row:
        return None
    recorded = datetime.fromisoformat(row["recorded_at"])
    return (datetime.now(timezone.utc) - recorded).total_seconds() / 60


def save_reading(
    conn: sqlite3.Connection,
    indicator: str,
    value: float,
    observed_at: str,
    *,
    liq_long_usd: float | None = None,
    liq_short_usd: float | None = None,
    aux_value: float | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO readings
           (indicator, value, observed_at, recorded_at, liq_long_usd, liq_short_usd, aux_value)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (indicator, value, observed_at, now, liq_long_usd, liq_short_usd, aux_value),
    )
    conn.commit()


def liquidation_readings_since(
    conn: sqlite3.Connection,
    indicator: str,
    *,
    days: int = 30,
) -> list[tuple[str, float, float | None, float | None]]:
    """All liquidation readings in the past N days (total, long, short)."""
    from datetime import timedelta

    rows = conn.execute(
        """SELECT value, observed_at, liq_long_usd, liq_short_usd FROM readings
           WHERE indicator = ? ORDER BY observed_at ASC""",
        (indicator,),
    ).fetchall()
    if not rows:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out: list[tuple[str, float, float | None, float | None]] = []
    for row in rows:
        ts = str(row["observed_at"])
        try:
            if "T" in ts:
                observed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            elif len(ts) >= 16 and ts[10] == " ":
                observed = datetime.strptime(ts[:16], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            else:
                observed = datetime.strptime(ts[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if observed >= cutoff:
            out.append(
                (
                    ts,
                    float(row["value"]),
                    float(row["liq_long_usd"]) if row["liq_long_usd"] is not None else None,
                    float(row["liq_short_usd"]) if row["liq_short_usd"] is not None else None,
                )
            )
    return out


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


def has_pending_alert(conn: sqlite3.Connection, indicator: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM pending_alerts WHERE indicator = ? AND processed = 0 LIMIT 1",
        (indicator,),
    ).fetchone()
    return row is not None


def insert_pending_alert(
    conn: sqlite3.Connection,
    *,
    indicator: str,
    value: float,
    prev_value: float | None,
    reasons: list[str],
    rule_types: list[str],
    themes: list[str],
    category: str,
    score: float,
    is_macro: bool,
    triggered_at: str,
    alert_tier: str = "normal",
) -> int:
    cur = conn.execute(
        """INSERT INTO pending_alerts
           (indicator, value, prev_value, reasons, rule_types, themes, category, score, is_macro, triggered_at, alert_tier)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            indicator,
            value,
            prev_value,
            "|".join(reasons),
            "|".join(rule_types),
            "|".join(themes),
            category,
            score,
            1 if is_macro else 0,
            triggered_at,
            alert_tier,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def fetch_pending_alerts(conn: sqlite3.Connection, *, include_macro: bool) -> list[sqlite3.Row]:
    if include_macro:
        return conn.execute(
            "SELECT * FROM pending_alerts WHERE processed = 0 ORDER BY triggered_at",
        ).fetchall()
    return conn.execute(
        "SELECT * FROM pending_alerts WHERE processed = 0 AND is_macro = 0 ORDER BY triggered_at",
    ).fetchall()


def mark_alerts_processed(conn: sqlite3.Connection, alert_ids: list[int]) -> None:
    if not alert_ids:
        return
    placeholders = ",".join("?" * len(alert_ids))
    conn.execute(
        f"UPDATE pending_alerts SET processed = 1 WHERE id IN ({placeholders})",
        alert_ids,
    )
    conn.commit()


def posts_today(conn: sqlite3.Connection) -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM post_log WHERE posted_at LIKE ? AND is_emergency = 0",
        (f"{today}%",),
    ).fetchone()
    return int(row["n"]) if row else 0


def emergency_posts_last_24h(conn: sqlite3.Connection) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM post_log WHERE posted_at >= ? AND is_emergency = 1",
        (cutoff,),
    ).fetchone()
    return int(row["n"]) if row else 0


def record_post(
    conn: sqlite3.Connection,
    *,
    tweet_type: str,
    text: str,
    alert_ids: list[int],
    indicators: list[str],
    is_emergency: bool,
    score: float,
    primary_category: str,
    themes: list[str],
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO post_log (posted_at, tweet_type, text, alert_ids, indicators, is_emergency, score)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            now,
            tweet_type,
            text,
            ",".join(str(i) for i in alert_ids),
            ",".join(indicators),
            1 if is_emergency else 0,
            score,
        ),
    )
    conn.execute(
        "INSERT INTO tweet_history (posted_at, primary_category, themes) VALUES (?, ?, ?)",
        (now, primary_category, ",".join(themes)),
    )
    conn.commit()


def recent_tweet_categories(conn: sqlite3.Connection, limit: int = 5) -> list[str]:
    rows = conn.execute(
        "SELECT primary_category FROM tweet_history ORDER BY posted_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [r["primary_category"] for r in rows]


def readings_since(
    conn: sqlite3.Connection,
    indicator: str,
    *,
    months: int = 6,
) -> list[tuple[str, float]]:
    """All readings in the past N months (full poll resolution)."""
    from datetime import timedelta

    rows = conn.execute(
        """SELECT value, observed_at FROM readings
           WHERE indicator = ? ORDER BY observed_at ASC""",
        (indicator,),
    ).fetchall()
    if not rows:
        return []

    cutoff = datetime.now(timezone.utc).date() - timedelta(days=months * 31)
    out: list[tuple[str, float]] = []
    for row in rows:
        ts = str(row["observed_at"])
        day = ts[:10]
        try:
            day_date = datetime.fromisoformat(day).date()
        except ValueError:
            continue
        if day_date >= cutoff:
            out.append((ts, float(row["value"])))
    return out


def daily_readings_since(
    conn: sqlite3.Connection,
    indicator: str,
    *,
    months: int = 6,
) -> list[tuple[str, float]]:
    """Last reading per calendar day for the past N months."""
    rows = conn.execute(
        """SELECT value, observed_at FROM readings
           WHERE indicator = ? ORDER BY observed_at ASC""",
        (indicator,),
    ).fetchall()
    if not rows:
        return []

    cutoff = datetime.now(timezone.utc).date()
    # approximate month window
    from datetime import timedelta

    start = cutoff - timedelta(days=months * 31)
    by_day: dict[str, float] = {}
    for row in rows:
        day = str(row["observed_at"])[:10]
        try:
            day_date = datetime.fromisoformat(day).date()
        except ValueError:
            continue
        if day_date >= start:
            by_day[day] = float(row["value"])
    return sorted(by_day.items())


def hours_since_indicator_post(conn: sqlite3.Connection, indicator: str) -> float | None:
    row = conn.execute(
        "SELECT posted_at FROM post_log WHERE indicators LIKE ? ORDER BY posted_at DESC LIMIT 1",
        (f"%{indicator}%",),
    ).fetchone()
    if not row:
        alert_row = conn.execute(
            "SELECT last_alert_at FROM alert_log WHERE indicator = ?",
            (indicator,),
        ).fetchone()
        if not alert_row or not alert_row["last_alert_at"]:
            return None
        posted = datetime.fromisoformat(alert_row["last_alert_at"])
    else:
        posted = datetime.fromisoformat(row["posted_at"])
    return (datetime.now(timezone.utc) - posted).total_seconds() / 3600