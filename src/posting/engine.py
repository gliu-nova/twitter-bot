from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

import sqlite3

from src.config import indicator_settings
from src.market_hours import is_us_equity_session, posting_schedule, vix_off_hours_immediate
from src.db import (
    fetch_pending_alerts,
    hours_since_indicator_post,
    insert_pending_alert,
    last_alert,
    mark_alerts_processed,
    posts_today,
    recent_tweet_categories,
    record_alert,
    record_post,
)
from src.posting.charts import (
    chart_for_decision,
    chart_latest_value_label,
    chart_title_for_alert,
)
from src.posting.compose import (
    compose_multi_tweet,
    compose_single_tweet,
    hydrate_liq_from_reasons,
    liq_breakdown_tokens,
    should_attach_chart,
    validate_post_before_send,
)
from src.posting.history import build_move_history
from src.posting.decide import decide_tweet_type
from src.posting.grouping import filter_stale_alerts
from src.posting.models import AlertTrigger
from src.posting.scoring import apply_session_score_adjustments, calculate_score
from src.twitter_client import post_tweet

ET = ZoneInfo("America/New_York")


def _row_to_alert(row: sqlite3.Row, cfg: dict[str, Any]) -> AlertTrigger:
    key = row["indicator"]
    settings = indicator_settings(cfg, key)
    alert = AlertTrigger(
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
        standalone_major=bool(settings.get("standalone_major")),
        alert_tier=str(row["alert_tier"]) if row["alert_tier"] else "normal",
        alert_unit=str(settings.get("alert_unit", "percent")),
    )
    hydrate_liq_from_reasons(alert)
    return alert


def _should_flush_macro(now: datetime | None = None) -> bool:
    now = now or datetime.now(ET)
    return now.time() >= time(16, 15)


def _buffer_age_minutes(
    alerts: list[AlertTrigger],
    now: datetime | None = None,
) -> float:
    now = now or datetime.now(timezone.utc)
    oldest = min(a.timestamp for a in alerts)
    return (now - oldest).total_seconds() / 60


def _should_flush_session_market(
    alerts: list[AlertTrigger],
    posting_cfg: dict[str, Any],
    now: datetime | None = None,
    *,
    now_et: datetime | None = None,
) -> bool:
    """US equity-session indicators: Mon–Fri 9:30–16:00 ET (+ VIX major/emergency off-hours)."""
    if not alerts:
        return False
    now = now or datetime.now(timezone.utc)
    now_et = now_et or datetime.now(ET)

    if any(vix_off_hours_immediate(a) for a in alerts):
        return True

    if not is_us_equity_session(now_et):
        return False

    buffer_min = float(posting_cfg.get("market_buffer_minutes", 30))
    return _buffer_age_minutes(alerts, now) >= buffer_min


def _should_flush_anytime_market(
    alerts: list[AlertTrigger],
    posting_cfg: dict[str, Any],
    now: datetime | None = None,
) -> bool:
    """Crypto / 24-7 indicators: flush after buffer window any time of day."""
    if not alerts:
        return False
    now = now or datetime.now(timezone.utc)
    buffer_min = float(posting_cfg.get("market_buffer_minutes", 30))
    return _buffer_age_minutes(alerts, now) >= buffer_min


def _partition_market_alerts(
    alerts: list[AlertTrigger],
    cfg: dict[str, Any],
) -> tuple[list[AlertTrigger], list[AlertTrigger]]:
    session: list[AlertTrigger] = []
    anytime: list[AlertTrigger] = []
    for alert in alerts:
        settings = indicator_settings(cfg, alert.indicator)
        if posting_schedule(settings) == "anytime":
            anytime.append(alert)
        else:
            session.append(alert)
    return session, anytime


def _emergency_escalation_allows_repost(
    conn: sqlite3.Connection,
    alert: AlertTrigger,
    settings: dict[str, Any],
) -> bool:
    """Allow a same-day repost only when emergency tier and size clearly escalated."""
    if alert.alert_tier != "emergency":
        return False
    row = last_alert(conn, alert.indicator)
    if not row or row["last_value"] is None:
        return False
    mult = float(settings.get("emergency_escalation_multiplier", 2.0))
    return alert.value >= float(row["last_value"]) * mult


def _in_cooldown(
    conn: sqlite3.Connection,
    alert: AlertTrigger,
    posting_cfg: dict[str, Any],
    cfg: dict[str, Any],
    *,
    is_emergency: bool,
) -> bool:
    hours = hours_since_indicator_post(conn, alert.indicator)
    if hours is None:
        return False
    settings = indicator_settings(cfg, alert.indicator)
    indicator_cd = float(settings.get("cooldown_hours", 24))
    global_cd = float(posting_cfg.get("indicator_cooldown_hours", 36))
    # Emergency bypasses daily cap and the global 36h cap, but still honors
    # per-indicator cooldown_hours (24h for liquidations).
    min_hours = indicator_cd if is_emergency else max(indicator_cd, global_cd)
    if hours >= min_hours:
        return False
    if is_emergency and _emergency_escalation_allows_repost(conn, alert, settings):
        return False
    return True


def _daily_cap_reached(conn: sqlite3.Connection, posting_cfg: dict[str, Any]) -> bool:
    cap = int(posting_cfg.get("daily_post_cap", 8))
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


def _alert_trigger_summary(alert: AlertTrigger) -> str:
    """Compact one-line description of what triggered the alert."""
    triggers = "; ".join(alert.reasons) if alert.reasons else "trigger details unavailable"
    return (
        f"{alert.indicator} ({alert.name}) tier={alert.alert_tier} "
        f"score={alert.score:.0f} — {triggers}"
    )


def _buffer_wait_reason(
    alert: AlertTrigger,
    batch: str,
    posting_cfg: dict[str, Any],
    *,
    now_utc: datetime,
    now_et: datetime,
) -> str:
    """Explain why a buffered alert is not flushing yet."""
    buffer_min = float(posting_cfg.get("market_buffer_minutes", 30))
    age_min = (now_utc - alert.timestamp).total_seconds() / 60

    if batch == "macro":
        return f"macro batch flushes at 16:15 ET (queued {age_min:.0f}m ago)"

    if batch == "anytime":
        if age_min < buffer_min:
            remaining = buffer_min - age_min
            return (
                f"24/7 buffer: {age_min:.0f}/{buffer_min:.0f} min elapsed "
                f"({remaining:.0f}m remaining)"
            )
        return "24/7 buffer elapsed — should flush on next tick"

    # US equity session indicators
    if vix_off_hours_immediate(alert):
        return "VIX major/emergency — eligible for immediate off-hours flush"
    if not is_us_equity_session(now_et):
        return "outside US equity session (9:30–16:00 ET Mon–Fri)"
    if age_min < buffer_min:
        remaining = buffer_min - age_min
        return (
            f"session buffer: {age_min:.0f}/{buffer_min:.0f} min elapsed "
            f"({remaining:.0f}m remaining)"
        )
    return "session buffer elapsed — should flush on next tick"


def _log_buffered_alerts(
    session_market: list[AlertTrigger],
    anytime_market: list[AlertTrigger],
    macro_alerts: list[AlertTrigger],
    posting_cfg: dict[str, Any],
    *,
    now_utc: datetime,
    now_et: datetime,
) -> None:
    pending = len(session_market) + len(anytime_market) + len(macro_alerts)
    if not pending:
        return

    print(
        f"[posting] {pending} alert(s) buffered — waiting for flush window "
        f"(session={len(session_market)}, anytime={len(anytime_market)}, macro={len(macro_alerts)}):"
    )
    for alert in session_market:
        wait = _buffer_wait_reason(
            alert, "session", posting_cfg, now_utc=now_utc, now_et=now_et
        )
        print(f"  • {_alert_trigger_summary(alert)}")
        print(f"    why buffered: {wait}")
    for alert in anytime_market:
        wait = _buffer_wait_reason(
            alert, "anytime", posting_cfg, now_utc=now_utc, now_et=now_et
        )
        print(f"  • {_alert_trigger_summary(alert)}")
        print(f"    why buffered: {wait}")
    for alert in macro_alerts:
        wait = _buffer_wait_reason(
            alert, "macro", posting_cfg, now_utc=now_utc, now_et=now_et
        )
        print(f"  • {_alert_trigger_summary(alert)}")
        print(f"    why buffered: {wait}")


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
    *,
    queue_reason: str = "threshold crossed",
) -> int:
    settings = indicator_settings(cfg, alert.indicator)
    posting_cfg = cfg.get("posting") or {}
    score = calculate_score(alert, settings, posting_cfg)
    if score is None:
        print(f"[queue] {alert.indicator} rejected: stale alert")
        return 0
    alert.score = score
    reasons = list(alert.reasons)
    for token in liq_breakdown_tokens(alert):
        if token not in reasons:
            reasons.append(token)

    alert_id = insert_pending_alert(
        conn,
        indicator=alert.indicator,
        value=alert.value,
        prev_value=alert.prev_value,
        reasons=reasons,
        rule_types=alert.rule_types,
        themes=alert.themes,
        category=alert.category,
        score=alert.score,
        is_macro=alert.is_macro,
        triggered_at=alert.timestamp.isoformat(),
        alert_tier=alert.alert_tier,
    )
    alert.db_id = alert_id
    print(f"[queue] queued for posting decision — {_alert_trigger_summary(alert)}")
    print(f"[queue]   why queued: {queue_reason}")
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
    session_market, anytime_market = _partition_market_alerts(market_alerts, cfg)
    in_session = is_us_equity_session(now_et)

    batches: list[tuple[str, list[AlertTrigger]]] = []

    if session_market and (
        force or _should_flush_session_market(session_market, posting_cfg, now_utc, now_et=now_et)
    ):
        batches.append(("session", session_market))

    if macro_alerts and (force or _should_flush_macro(now_et)):
        batches.append(("macro", macro_alerts))

    if anytime_market and (force or _should_flush_anytime_market(anytime_market, posting_cfg, now_utc)):
        batches.append(("anytime", anytime_market))

    if not batches:
        _log_buffered_alerts(
            session_market,
            anytime_market,
            macro_alerts,
            posting_cfg,
            now_utc=now_utc,
            now_et=now_et,
        )
        return 0

    posted = 0
    for batch_name, raw_alerts in batches:
        alerts = filter_stale_alerts(raw_alerts, posting_cfg, now_utc)
        if not alerts:
            mark_alerts_processed(conn, [a.db_id for a in raw_alerts if a.db_id])
            continue

        if batch_name in ("session", "anytime"):
            apply_session_score_adjustments(alerts, cfg, posting_cfg, in_session=in_session)

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
            if not _in_cooldown(conn, a, posting_cfg, cfg, is_emergency=decision.is_emergency)
        ]
        if not filtered:
            print("[posting] all alerts in cooldown — skipping (keeping in queue)")
            continue
        decision.alerts = filtered

        # Daily cap (emergency bypasses)
        if not decision.is_emergency and _daily_cap_reached(conn, posting_cfg):
            print(f"[posting] daily cap ({posting_cfg.get('daily_post_cap', 8)}) reached — skipping")
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
            posting_cfg=posting_cfg,
        )
        primary = decision.alerts[0]
        chart_title = chart_title_for_alert(primary) if chart_path else None
        chart_latest = chart_latest_value_label(primary, primary.value) if chart_path else None
        validation = validate_post_before_send(
            text,
            primary,
            chart_title=chart_title,
            chart_latest_value=chart_latest,
        )
        if not validation.ok:
            chart_issues = [i for i in validation.issues if "chart" in i.lower()]
            text_issues = [i for i in validation.issues if i not in chart_issues]
            if chart_path and chart_issues:
                print(f"[posting] chart validation failed — text-only: {chart_issues}")
                chart_path = None
            if text_issues:
                print(f"[posting] post validation warnings: {text_issues}")
        if chart_path:
            print(f"[posting] chart: {chart_path}")
        elif decision.tweet_type == "single":
            alert = decision.alerts[0]
            history = build_move_history(conn, alert)
            if not should_attach_chart(alert, history, posting_cfg, is_emergency=decision.is_emergency):
                print(f"[posting] text-only — no chart ({alert.indicator})")

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