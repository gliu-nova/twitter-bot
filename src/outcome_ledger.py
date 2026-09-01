"""Outcome ledger: what happened after each fired alert.

Records 4h / 24h / 5d moves on the same series and whether a related
series confirmed the move (e.g. VIX up after SPX down). Writes a simple
monthly HTML page and a JSON summary for the public dashboard.
"""

from __future__ import annotations

import html
import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import sqlite3

from src.db import ROOT
from src.posting.event_score import CONFIRMATION_PEERS, PEER_LABELS
from src.posting.models import AlertTrigger

PAGE_PATH = ROOT / "data" / "outcome_ledger.html"
JSON_PATH = ROOT / "data" / "outcome_ledger.json"

HORIZONS: dict[str, tuple[timedelta, timedelta, timedelta]] = {
    "4h": (timedelta(hours=4), timedelta(minutes=90), timedelta(hours=2)),
    "24h": (timedelta(hours=24), timedelta(hours=4), timedelta(hours=6)),
    "5d": (timedelta(days=5), timedelta(hours=18), timedelta(hours=36)),
}
FLAT_PCT = 0.15
CONFIRM_LOOKAHEAD_DEFAULT = timedelta(hours=24)
CONFIRM_LOOKAHEAD_MACRO = timedelta(days=5)


@dataclass
class MonthStats:
    month: str
    alerts_fired: int = 0
    scored: int = 0
    resolved: int = 0
    resolved_pct: float | None = None
    false_alarms: int = 0
    false_alarm_pct: float | None = None
    confirmed: int = 0
    pending: int = 0
    median_hours_to_confirm: float | None = None
    false_alarm_by_indicator: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "month": self.month,
            "alerts_fired": self.alerts_fired,
            "scored": self.scored,
            "resolved": self.resolved,
            "resolved_pct": self.resolved_pct,
            "false_alarms": self.false_alarms,
            "false_alarm_pct": self.false_alarm_pct,
            "confirmed": self.confirmed,
            "pending": self.pending,
            "median_hours_to_confirm": self.median_hours_to_confirm,
            "false_alarm_by_indicator": self.false_alarm_by_indicator,
        }


def expected_direction(alert: AlertTrigger) -> int:
    for reason in alert.reasons:
        lowered = reason.lower()
        if "crossed above" in lowered:
            return 1
        if "crossed below" in lowered:
            return -1
    if "crosses_above" in alert.rule_types:
        return 1
    if "crosses_below" in alert.rule_types:
        return -1
    if alert.prev_value is None:
        return 1
    if alert.value > alert.prev_value:
        return 1
    if alert.value < alert.prev_value:
        return -1
    return 1


def primary_horizon(is_macro: bool) -> str:
    return "5d" if is_macro else "24h"


def register_fired_alert(conn: sqlite3.Connection, alert: AlertTrigger) -> int | None:
    """Insert a ledger row for a newly queued alert. Idempotent."""
    fired_at = alert.timestamp
    if fired_at.tzinfo is None:
        fired_at = fired_at.replace(tzinfo=timezone.utc)
    conn.execute(
        """INSERT OR IGNORE INTO outcome_ledger
           (pending_alert_id, indicator, fired_at, value, prev_value,
            expected_direction, alert_tier, is_macro)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            alert.db_id,
            alert.indicator,
            fired_at.isoformat(),
            float(alert.value),
            alert.prev_value,
            expected_direction(alert),
            alert.alert_tier or "normal",
            1 if alert.is_macro else 0,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT last_insert_rowid() AS id").fetchone()
    return int(row["id"]) if row else None


def mark_ledger_posted(conn: sqlite3.Connection, pending_ids: list[int]) -> None:
    ids = [i for i in pending_ids if i is not None]
    if not ids:
        return
    placeholders = ",".join("?" * len(ids))
    conn.execute(
        f"UPDATE outcome_ledger SET posted = 1 WHERE pending_alert_id IN ({placeholders})",
        ids,
    )
    conn.commit()


def process_outcome_ledger(
    conn: sqlite3.Connection,
    cfg: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
    page_path: Path | None = None,
) -> MonthStats:
    """Backfill, resolve elapsed horizons, write the public page."""
    now = now or datetime.now(timezone.utc)
    backfill_from_pending(conn)
    filled = resolve_open_rows(conn, cfg or {}, now=now)
    stats = month_stats(conn, now=now)
    dest = page_path or PAGE_PATH
    write_outcome_page(stats, dest)
    json_dest = dest.with_suffix(".json") if page_path is not None else JSON_PATH
    json_dest.parent.mkdir(parents=True, exist_ok=True)
    json_dest.write_text(json.dumps(stats.to_dict(), indent=2), encoding="utf-8")
    print(
        f"[outcome-ledger] {stats.month}: fired={stats.alerts_fired} "
        f"resolved={_fmt_pct(stats.resolved_pct)} "
        f"false_alarm={_fmt_pct(stats.false_alarm_pct)} "
        f"median_ttc={_fmt_hours(stats.median_hours_to_confirm)} "
        f"horizons_filled={filled}"
    )
    print(f"[outcome-ledger] page: {dest}")
    return stats


def backfill_from_pending(conn: sqlite3.Connection) -> int:
    """Register historical pending_alerts that predate the ledger."""
    rows = conn.execute(
        """SELECT id, indicator, value, prev_value, triggered_at, alert_tier, is_macro, reasons, rule_types
           FROM pending_alerts
           WHERE NOT EXISTS (
               SELECT 1 FROM outcome_ledger ol WHERE ol.pending_alert_id = pending_alerts.id
           )"""
    ).fetchall()
    added = 0
    for row in rows:
        prev = float(row["prev_value"]) if row["prev_value"] is not None else None
        value = float(row["value"])
        reasons = str(row["reasons"] or "").split("|")
        rule_types = str(row["rule_types"] or "").split("|")
        direction = 1
        if any("crossed below" in r.lower() for r in reasons) or "crosses_below" in rule_types:
            direction = -1
        elif any("crossed above" in r.lower() for r in reasons) or "crosses_above" in rule_types:
            direction = 1
        elif prev is not None and value < prev:
            direction = -1
        conn.execute(
            """INSERT OR IGNORE INTO outcome_ledger
               (pending_alert_id, indicator, fired_at, value, prev_value,
                expected_direction, alert_tier, is_macro)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(row["id"]),
                row["indicator"],
                row["triggered_at"],
                value,
                prev,
                direction,
                row["alert_tier"] or "normal",
                int(row["is_macro"] or 0),
            ),
        )
        added += 1
    if added:
        conn.commit()
    return added


def resolve_open_rows(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    *,
    now: datetime,
) -> int:
    rows = conn.execute(
        """SELECT * FROM outcome_ledger
           WHERE move_4h_pct IS NULL
              OR move_24h_pct IS NULL
              OR move_5d_pct IS NULL
              OR confirmed = 0"""
    ).fetchall()
    filled = 0
    for row in rows:
        filled += _resolve_row(conn, dict(row), cfg, now)
    if filled:
        conn.commit()
    return filled


def month_stats(conn: sqlite3.Connection, *, now: datetime | None = None) -> MonthStats:
    now = now or datetime.now(timezone.utc)
    month = now.strftime("%Y-%m")
    start = f"{month}-01"
    if now.month == 12:
        end = f"{now.year + 1}-01-01"
    else:
        end = f"{now.year}-{now.month + 1:02d}-01"
    rows = conn.execute(
        """SELECT * FROM outcome_ledger
           WHERE fired_at >= ? AND fired_at < ?
           ORDER BY fired_at""",
        (start, end),
    ).fetchall()

    stats = MonthStats(month=month, alerts_fired=len(rows))
    confirm_hours: list[float] = []
    by_ind: dict[str, list[int]] = {}

    for row in rows:
        is_macro = bool(row["is_macro"])
        horizon = primary_horizon(is_macro)
        resolved_flag = row[f"resolved_{horizon}"]
        if row["hours_to_confirm"] is not None:
            confirm_hours.append(float(row["hours_to_confirm"]))
        if int(row["confirmed"] or 0):
            stats.confirmed += 1
        if resolved_flag is None:
            stats.pending += 1
            continue
        stats.scored += 1
        hit = int(resolved_flag) == 1
        if hit:
            stats.resolved += 1
        else:
            stats.false_alarms += 1
        by_ind.setdefault(row["indicator"], []).append(0 if hit else 1)

    if stats.scored:
        stats.resolved_pct = round(100.0 * stats.resolved / stats.scored, 1)
        stats.false_alarm_pct = round(100.0 * stats.false_alarms / stats.scored, 1)
    if confirm_hours:
        stats.median_hours_to_confirm = round(statistics.median(confirm_hours), 1)

    breakdown: list[dict[str, Any]] = []
    for indicator, flags in sorted(by_ind.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        n = len(flags)
        fa = sum(flags)
        breakdown.append(
            {
                "indicator": indicator,
                "label": PEER_LABELS.get(indicator, indicator.replace("_", " ")),
                "scored": n,
                "false_alarms": fa,
                "false_alarm_pct": round(100.0 * fa / n, 1) if n else None,
            }
        )
    stats.false_alarm_by_indicator = breakdown
    return stats


def write_outcome_page(stats: MonthStats, path: Path | None = None) -> Path:
    dest = path or PAGE_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_render_html(stats), encoding="utf-8")
    return dest


def _resolve_row(
    conn: sqlite3.Connection,
    row: dict[str, Any],
    cfg: dict[str, Any],
    now: datetime,
) -> int:
    fired = _parse_ts(str(row["fired_at"]))
    if fired is None:
        return 0
    indicator = str(row["indicator"])
    start_value = float(row["value"])
    expected = int(row["expected_direction"])
    filled = 0

    updates: dict[str, Any] = {}
    for name, (offset, before, after) in HORIZONS.items():
        if row[f"move_{name}_pct"] is not None:
            continue
        target = fired + offset
        if now < target - before:
            continue
        later = _reading_near(conn, indicator, target, before, after)
        if later is None:
            continue
        move = _pct_move(start_value, later)
        if move is None:
            continue
        updates[f"move_{name}_pct"] = move
        updates[f"resolved_{name}"] = 1 if _in_expected_direction(move, expected) else 0
        filled += 1

    if not int(row["confirmed"] or 0):
        confirm = _first_confirmation(conn, row, fired, expected, cfg, now)
        if confirm is None and cfg:
            confirm = _memory_confirmation(cfg, indicator, fired, expected, bool(row["is_macro"]))
        if confirm is not None:
            peer, confirmed_at = confirm
            hours = (confirmed_at - fired).total_seconds() / 3600
            updates["confirmed"] = 1
            updates["confirmed_by"] = peer
            updates["confirmed_at"] = confirmed_at.isoformat()
            updates["hours_to_confirm"] = round(hours, 2)
            filled += 1

    if not updates:
        return 0
    assignments = ", ".join(f"{col} = ?" for col in updates)
    conn.execute(
        f"UPDATE outcome_ledger SET {assignments} WHERE id = ?",
        [*updates.values(), row["id"]],
    )
    return filled


def _in_expected_direction(move_pct: float, expected: int) -> bool:
    if abs(move_pct) < FLAT_PCT:
        return False
    return (move_pct > 0 and expected > 0) or (move_pct < 0 and expected < 0)


def _pct_move(start: float, end: float) -> float | None:
    if start == 0:
        return None
    return (end - start) / abs(start) * 100


def _parse_ts(raw: str) -> datetime | None:
    text = str(raw).strip()
    try:
        if "T" in text:
            ts = datetime.fromisoformat(text.replace("Z", "+00:00"))
        elif len(text) >= 16 and text[10] == " ":
            ts = datetime.strptime(text[:16], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        else:
            # Date-only FRED/Yahoo prints: treat as 16:00 UTC (after the US session).
            ts = datetime.strptime(text[:10], "%Y-%m-%d").replace(
                hour=16, tzinfo=timezone.utc,
            )
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    except ValueError:
        return None


def _reading_near(
    conn: sqlite3.Connection,
    indicator: str,
    target: datetime,
    before: timedelta,
    after: timedelta,
) -> float | None:
    lo = (target - before).date().isoformat()
    hi = (target + after + timedelta(days=1)).isoformat()
    rows = conn.execute(
        """SELECT value, observed_at FROM readings
           WHERE indicator = ? AND observed_at >= ? AND observed_at < ?""",
        (indicator, lo, hi),
    ).fetchall()
    best: tuple[float, float] | None = None
    window_lo = target - before
    window_hi = target + after
    for row in rows:
        ts = _parse_ts(str(row["observed_at"]))
        if ts is None or ts < window_lo or ts > window_hi:
            continue
        delta = abs((ts - target).total_seconds())
        if best is None or delta < best[0]:
            best = (delta, float(row["value"]))
    return best[1] if best else None


def _reading_at_or_before(
    conn: sqlite3.Connection,
    indicator: str,
    ts: datetime,
) -> float | None:
    row = conn.execute(
        """SELECT value FROM readings
           WHERE indicator = ? AND observed_at <= ?
           ORDER BY observed_at DESC LIMIT 1""",
        (indicator, ts.isoformat()),
    ).fetchone()
    return float(row["value"]) if row else None


def _first_confirmation(
    conn: sqlite3.Connection,
    row: dict[str, Any],
    fired: datetime,
    expected: int,
    cfg: dict[str, Any],
    now: datetime,
) -> tuple[str, datetime] | None:
    peers = CONFIRMATION_PEERS.get(str(row["indicator"])) or []
    if not peers:
        return None
    lookahead = CONFIRM_LOOKAHEAD_MACRO if row["is_macro"] else CONFIRM_LOOKAHEAD_DEFAULT
    deadline = min(now, fired + lookahead)
    if deadline <= fired:
        return None

    from src.config import indicator_settings

    best: tuple[datetime, str] | None = None
    for peer, sign in peers:
        required = expected * sign
        baseline = _reading_at_or_before(conn, peer, fired)
        if baseline is None:
            continue
        later_rows = conn.execute(
            """SELECT value, observed_at FROM readings
               WHERE indicator = ? AND observed_at > ? AND observed_at <= ?
               ORDER BY observed_at ASC""",
            (peer, fired.isoformat(), deadline.isoformat()),
        ).fetchall()
        threshold = 0.0
        try:
            settings = indicator_settings(cfg, peer) if cfg else {}
            threshold = float(settings.get("normal_alert") or 0) * 0.5
        except Exception:
            threshold = 0.0
        unit = "percent"
        try:
            unit = str((indicator_settings(cfg, peer) if cfg else {}).get("alert_unit") or "percent")
        except Exception:
            unit = "percent"
        for later in later_rows:
            ts = _parse_ts(str(later["observed_at"]))
            if ts is None:
                continue
            end = float(later["value"])
            if unit == "absolute":
                move = end - baseline
                floor = max(threshold, 1e-9)
            else:
                if baseline == 0:
                    continue
                move = (end - baseline) / abs(baseline) * 100
                floor = max(threshold, FLAT_PCT)
            if required > 0 and move < floor:
                continue
            if required < 0 and move > -floor:
                continue
            if best is None or ts < best[0]:
                best = (ts, peer)
            break
    if best is None:
        return None
    return best[1], best[0]


def _memory_confirmation(
    cfg: dict[str, Any],
    indicator: str,
    fired: datetime,
    expected: int,
    is_macro: bool,
) -> tuple[str, datetime] | None:
    peers = CONFIRMATION_PEERS.get(indicator) or []
    if not peers:
        return None
    lookahead = CONFIRM_LOOKAHEAD_MACRO if is_macro else CONFIRM_LOOKAHEAD_DEFAULT
    try:
        from src.market_memory_bridge import first_confirming_peer_event

        return first_confirming_peer_event(
            cfg,
            peers=[(peer, expected * sign) for peer, sign in peers],
            since=fired,
            until=fired + lookahead,
        )
    except Exception:
        return None


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.0f}%" if value == int(value) else f"{value:.1f}%"


def _fmt_hours(value: float | None) -> str:
    if value is None:
        return "—"
    if value < 1:
        return f"{int(round(value * 60))}m"
    if value < 48:
        return f"{value:.1f}h".replace(".0h", "h")
    return f"{value / 24:.1f}d"


def _render_html(stats: MonthStats) -> str:
    month_label = datetime.strptime(stats.month + "-01", "%Y-%m-%d").strftime("%B %Y")
    rows = []
    for item in stats.false_alarm_by_indicator:
        pct = _fmt_pct(item.get("false_alarm_pct"))
        rows.append(
            "<tr>"
            f"<th scope='row'>{html.escape(str(item['label']))}</th>"
            f"<td class='num'>{html.escape(pct)}</td>"
            f"<td class='meta'>{int(item['false_alarms'])}/{int(item['scored'])}</td>"
            "</tr>"
        )
    table_body = "\n".join(rows) if rows else (
        "<tr><td colspan='3' class='meta'>No resolved alerts this month yet.</td></tr>"
    )
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    scored_note = (
        f"{stats.scored} with a completed 24h window (5d for macro)"
        if stats.scored
        else "none scored yet — waiting on 24h / 5d follow-through"
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Outcome ledger — {html.escape(month_label)}</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #0f1419;
      --card: #1a2330;
      --ink: #e7ecf3;
      --muted: #8b9bb0;
      --line: #2a3545;
      --good: #3dd68c;
      --bad: #ff6b6b;
      --accent: #7ab8ff;
    }}
    @media (prefers-color-scheme: light) {{
      :root {{
        --bg: #f6f7f9;
        --card: #fff;
        --ink: #1b2430;
        --muted: #5b6b7c;
        --line: #e2e7ee;
        --good: #0f7b4c;
        --bad: #c0392b;
        --accent: #1d4ed8;
      }}
    }}
    body {{
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, sans-serif;
      background: var(--bg);
      color: var(--ink);
      line-height: 1.45;
    }}
    main {{
      max-width: 42rem;
      margin: 0 auto;
      padding: 2.5rem 1.25rem 4rem;
    }}
    h1 {{ font-size: 1.6rem; margin: 0 0 0.35rem; }}
    .sub {{ color: var(--muted); margin: 0 0 2rem; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(10.5rem, 1fr));
      gap: 0.85rem;
      margin-bottom: 2rem;
    }}
    .stat {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 1rem 1.1rem;
    }}
    .stat dt {{
      margin: 0;
      font-size: 0.72rem;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .stat dd {{
      margin: 0.35rem 0 0;
      font-size: 1.55rem;
      font-variant-numeric: tabular-nums;
      font-weight: 650;
    }}
    h2 {{ font-size: 1.05rem; margin: 0 0 0.75rem; }}
    table {{ width: 100%; border-collapse: collapse; background: var(--card); border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }}
    th, td {{ text-align: left; padding: 0.65rem 0.85rem; border-bottom: 1px solid var(--line); }}
    th {{ font-weight: 600; }}
    .num {{ font-variant-numeric: tabular-nums; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .meta {{ color: var(--muted); font-size: 0.9rem; }}
    footer {{ margin-top: 2rem; color: var(--muted); font-size: 0.85rem; }}
  </style>
</head>
<body>
  <main>
    <h1>Outcome ledger</h1>
    <p class="sub">{html.escape(month_label)} · {html.escape(scored_note)}</p>
    <dl class="grid">
      <div class="stat"><dt>Alerts fired this month</dt><dd>{stats.alerts_fired}</dd></div>
      <div class="stat"><dt>% resolved in expected direction</dt><dd>{html.escape(_fmt_pct(stats.resolved_pct))}</dd></div>
      <div class="stat"><dt>False-alarm rate</dt><dd>{html.escape(_fmt_pct(stats.false_alarm_pct))}</dd></div>
      <div class="stat"><dt>Median time-to-confirmation</dt><dd>{html.escape(_fmt_hours(stats.median_hours_to_confirm))}</dd></div>
    </dl>
    <h2>False-alarm rate by indicator</h2>
    <table>
      <thead>
        <tr><th>Indicator</th><th>False-alarm rate</th><th>False / scored</th></tr>
      </thead>
      <tbody>
        {table_body}
      </tbody>
    </table>
    <footer>
      Same-series follow-through at 24h (5d for macro). Confirmation is a related series
      moving the expected way (e.g. VIX up after SPX down). Generated {html.escape(generated)}.
    </footer>
  </main>
</body>
</html>
"""
