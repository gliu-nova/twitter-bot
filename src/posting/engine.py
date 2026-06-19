from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

import sqlite3

from src.config import indicator_settings
from src.db import (
    fetch_pending_alerts,
    hours_since_indicator_post,
    insert_pending_alert,
    mark_alerts_processed,
    posts_today,
    recent_tweet_categories,
    record_alert,
    record_post,
)
from src.posting.charts import chart_for_decision
from src.posting.compose import compose_multi_tweet, compose_single_tweet
from src.posting.history import build_move_history
from src.posting.decide import decide_tweet_type
from src.posting.grouping import filter_stale_alerts
from src.posting.models import AlertTrigger
from src.posting.scoring import calculate_score
from src.twitter_client import post_tweet

ET = ZoneInfo("America/New_York")


def _row_to_alert(row: sqlite3.Row, cfg: dict[str, Any]) -> AlertTrigger:
    key = row["indicator"]
    settings = indicator_settings(cfg, key)
    return AlertTrigger(
        indicator=key,
        name=settings.get("name", key),
        value=float(row["value"]),
        prev_value=float(row["prev_value"]) if row["prev_value"] is not None else None,
        reasons=row["reasons"].split("|") if row["reasons"] else [],
        rule_types=row["rule_types"].split("|") if row["rule_types"] else [],
        themes=row["themes"].split("|") if row["themes"] else [],
        category=row["category"],
        is_macro=bool(row["is_macro"]),
        timestamp=datetime.fromisoformat(row["triggered_at"]),
        score=float(row["score"]),
        db_id=int(row["id"]),
    )


def _should_flush_macro(now: datetime | None = None) -> bool:
    now = now or datetime.now(ET)
    return now.time() >= time(16, 15)


def _should_flush_market(
    alerts: list[AlertTrigger],
    posting_cfg: dict[str, Any],
    now: datetime | None = None,
) -> bool:
    if not alerts:
        return False
    now = now or datetime.now(timezone.utc)
    buffer_min = float(posting_cfg.get("market_buffer_minutes", 30))
    oldest = min(a.timestamp for a in alerts)
    age_minutes = (now - oldest).total_seconds() / 60
    return age_minutes >= buffer_min


def _in_cooldown(
    conn: sqlite3.Connection,
    alert: AlertTrigger,
    posting_cfg: dict[str, Any],
    *,
    is_emergency: bool,
) -> bool:
    if is_emergency:
        return False
    hours = hours_since_indicator_post(conn, alert.indicator)
    if hours is None:
        return False
    min_hours = float(posting_cfg.get("indicator_cooldown_hours", 36))
    return hours < min_hours


def _daily_cap_reached(conn: sqlite3.Connection, posting_cfg: dict[str, Any]) -> bool:
    cap = int(posting_cfg.get("daily_post_cap", 2))
    return posts_today(conn) >= cap


def _primary_category(alert: AlertTrigger) -> str:
    if "crypto" in alert.themes:
        return "crypto"
    if alert.category in ("macro_data", "rates_fx", "housing", "commodities"):
        return "macro"
    return alert.category or "other"


def _diversity_penalty(
    conn: sqlite3.Connection,
    decision_alerts: list[AlertTrigger],
    posting_cfg: dict[str, Any],
) -> float:
    """Penalize posting another crypto tweet if last 2 were crypto."""
    recent = recent_tweet_categories(conn, limit=int(posting_cfg.get("diversity_lookback", 3)))
    candidate_cat = _primary_category(decision_alerts[0])
    if candidate_cat != "crypto":
        return 0.0
    crypto_streak = 0
    for cat in recent:
        if cat == "crypto":
            crypto_streak += 1
        else:
            break
    max_crypto_streak = int(posting_cfg.get("max_crypto_streak", 2))
    if crypto_streak >= max_crypto_streak:
        return 50.0
    return 0.0


def _prefer_macro_alternative(
    decisions: list[Any],
    all_alerts: list[AlertTrigger],
    conn: sqlite3.Connection,
    posting_cfg: dict[str, Any],
) -> Any | None:
    """If top decision is crypto-blocked, try highest macro alternative."""
    recent = recent_tweet_categories(conn, limit=int(posting_cfg.get("diversity_lookback", 3)))
    crypto_streak = sum(1 for c in recent[:2] if c == "crypto")
    if crypto_streak < int(posting_cfg.get("max_crypto_streak", 2)):
        return None

    macro_alerts = [a for a in all_alerts if _primary_category(a) == "macro"]
    if not macro_alerts:
        return None

    from src.posting.decide import decide_tweet_type

    return decide_tweet_type(macro_alerts, posting_cfg)


def enqueue_alert(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    alert: AlertTrigger,
) -> int:
    settings = indicator_settings(cfg, alert.indicator)
    posting_cfg = cfg.get("posting") or {}
    score = calculate_score(alert, settings, posting_cfg)
    if score is None:
        print(f"[queue] {alert.indicator} rejected: stale alert")
        return 0
    alert.score = score

    alert_id = insert_pending_alert(
        conn,
        indicator=alert.indicator,
        value=alert.value,
        prev_value=alert.prev_value,
        reasons=alert.reasons,
        rule_types=alert.rule_types,
        themes=alert.themes,
        category=alert.category,
        score=alert.score,
        is_macro=alert.is_macro,
        triggered_at=alert.timestamp.isoformat(),
    )
    alert.db_id = alert_id
    print(
        f"[queue] {alert.indicator} tier={alert.alert_tier} score={alert.score} "
        f"themes={','.join(alert.themes)}"
    )
    return alert_id


def process_posting_queue(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    *,
    force: bool = False,
) -> int:
    """Decide and post from buffered alerts. Returns number of tweets posted."""
    posting_cfg = cfg.get("posting") or {}
    now_et = datetime.now(ET)
    now_utc = datetime.now(timezone.utc)

    market_rows = fetch_pending_alerts(conn, include_macro=False)
    macro_rows = fetch_pending_alerts(conn, include_macro=True)
    macro_only = [r for r in macro_rows if r["is_macro"]]

    market_alerts = [_row_to_alert(r, cfg) for r in market_rows]
    macro_alerts = [_row_to_alert(r, cfg) for r in macro_only]

    batches: list[tuple[str, list[AlertTrigger]]] = []

    if market_alerts and (force or _should_flush_market(market_alerts, posting_cfg, now_utc)):
        batches.append(("market", market_alerts))

    if macro_alerts and (force or _should_flush_macro(now_et)):
        batches.append(("macro", macro_alerts))

    if not batches:
        pending = len(market_rows) + len(macro_only)
        if pending:
            print(f"[posting] {pending} alert(s) buffered — waiting for flush window")
        return 0

    posted = 0
    for _batch_name, raw_alerts in batches:
        alerts = filter_stale_alerts(raw_alerts, posting_cfg, now_utc)
        if not alerts:
            mark_alerts_processed(conn, [a.db_id for a in raw_alerts if a.db_id])
            continue

        decision = decide_tweet_type(alerts, posting_cfg)
        if not decision:
            continue

        # Diversity: prefer macro if crypto streak too long
        penalty = _diversity_penalty(conn, decision.alerts, posting_cfg)
        if penalty >= 50:
            alt = _prefer_macro_alternative([decision], alerts, conn, posting_cfg)
            if alt:
                print("[posting] diversity: preferring macro over crypto streak")
                decision = alt

        # Cooldown filter (keep emergency)
        filtered = [
            a
            for a in decision.alerts
            if not _in_cooldown(conn, a, posting_cfg, is_emergency=decision.is_emergency)
        ]
        if not filtered:
            print("[posting] all alerts in cooldown — skipping (keeping in queue)")
            continue
        decision.alerts = filtered

        # Daily cap (emergency bypasses)
        if not decision.is_emergency and _daily_cap_reached(conn, posting_cfg):
            print(f"[posting] daily cap ({posting_cfg.get('daily_post_cap', 2)}) reached — skipping")
            continue

        if decision.tweet_type == "multi":
            histories = {a.indicator: build_move_history(conn, a) for a in decision.alerts}
            text = compose_multi_tweet(
                decision.alerts,
                decision.theme,
                histories=histories,
                posting_cfg=posting_cfg,
                is_emergency=decision.is_emergency,
            )
        else:
            alert = decision.alerts[0]
            history = build_move_history(conn, alert)
            text = compose_single_tweet(
                alert,
                history=history,
                posting_cfg=posting_cfg,
                is_emergency=decision.is_emergency,
            )

        chart_path = chart_for_decision(
            conn,
            cfg,
            tweet_type=decision.tweet_type,
            alerts=decision.alerts,
            theme=decision.theme,
            is_emergency=decision.is_emergency,
        )
        if chart_path:
            print(f"[posting] chart: {chart_path}")

        print(f"[posting] {decision.tweet_type} score={decision.score} emergency={decision.is_emergency}")
        post_tweet(text, media_path=chart_path)

        alert_ids = [a.db_id for a in decision.alerts if a.db_id]
        indicators = [a.indicator for a in decision.alerts]
        primary_cat = _primary_category(decision.alerts[0])
        themes = list({t for a in decision.alerts for t in a.themes})

        record_post(
            conn,
            tweet_type=decision.tweet_type,
            text=text,
            alert_ids=alert_ids,
            indicators=indicators,
            is_emergency=decision.is_emergency,
            score=decision.score,
            primary_category=primary_cat,
            themes=themes,
        )
        for a in decision.alerts:
            record_alert(conn, a.indicator, a.value)

        # Mark entire batch processed (including unselected alerts from same window)
        mark_alerts_processed(conn, [a.db_id for a in raw_alerts if a.db_id])
        posted += 1

        if not decision.is_emergency and _daily_cap_reached(conn, posting_cfg):
            print("[posting] daily cap reached after post — stopping")
            break

    return posted