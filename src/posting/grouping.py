from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from src.posting.models import STORY_PATTERNS, AlertTrigger, CATEGORY_GROUPS


def _alert_themes(alert: AlertTrigger) -> set[str]:
    return set(alert.themes)


def _time_spread_hours(alerts: list[AlertTrigger]) -> float:
    if len(alerts) < 2:
        return 0.0
    times = [a.timestamp for a in alerts]
    return (max(times) - min(times)).total_seconds() / 3600


def _coherence_bonus(alerts: list[AlertTrigger]) -> float:
    if len(alerts) < 2:
        return 0.0

    indicators = {a.indicator for a in alerts}
    bonus = 0.0

    # Same category group
    for _group_name, members in CATEGORY_GROUPS.items():
        overlap = indicators & members
        if len(overlap) >= 2:
            bonus = max(bonus, 20.0)

    # Thematic story match
    all_themes: set[str] = set()
    for a in alerts:
        all_themes |= _alert_themes(a)

    for _story, required in STORY_PATTERNS.items():
        if required.issubset(all_themes) or len(required & all_themes) >= 2:
            bonus = max(bonus, 25.0)

    # Shared theme tag
    theme_sets = [_alert_themes(a) for a in alerts]
    common = set.intersection(*theme_sets) if theme_sets else set()
    if common:
        bonus = max(bonus, 15.0)

    return bonus


def _time_spread_penalty(alerts: list[AlertTrigger], posting_cfg: dict[str, Any]) -> float:
    spread = _time_spread_hours(alerts)
    max_hours = float(posting_cfg.get("group_time_window_hours", 8))
    if spread <= max_hours / 2:
        return 0.0
    if spread <= max_hours:
        return 5.0
    return 15.0


def group_score(alerts: list[AlertTrigger], posting_cfg: dict[str, Any]) -> float:
    if not alerts:
        return 0.0
    base = sum(a.score for a in alerts)
    bonus = _coherence_bonus(alerts)
    penalty = _time_spread_penalty(alerts, posting_cfg)
    return round(base + bonus - penalty, 1)


def _within_window(alerts: list[AlertTrigger], hours: float) -> bool:
    if len(alerts) < 2:
        return True
    spread = _time_spread_hours(alerts)
    return spread <= hours


def _group_by_theme(alerts: list[AlertTrigger]) -> list[list[AlertTrigger]]:
    theme_buckets: dict[str, list[AlertTrigger]] = {}
    for alert in alerts:
        for theme in alert.themes:
            theme_buckets.setdefault(theme, []).append(alert)
    return [bucket for bucket in theme_buckets.values() if len(bucket) >= 2]


def _group_by_category(alerts: list[AlertTrigger]) -> list[list[AlertTrigger]]:
    buckets: dict[str, list[AlertTrigger]] = {}
    for alert in alerts:
        buckets.setdefault(alert.category, []).append(alert)
    return [bucket for bucket in buckets.values() if len(bucket) >= 2]


def _dedupe_alerts(alerts: list[AlertTrigger]) -> list[AlertTrigger]:
    seen: set[str] = set()
    out: list[AlertTrigger] = []
    for a in sorted(alerts, key=lambda x: x.score, reverse=True):
        if a.indicator in seen:
            continue
        seen.add(a.indicator)
        out.append(a)
    return out


def group_related_alerts(
    alerts: list[AlertTrigger],
    posting_cfg: dict[str, Any],
) -> list[list[AlertTrigger]]:
    """Return candidate alert groups sorted by group_score descending."""
    if not alerts:
        return []

    window_hours = float(posting_cfg.get("group_time_window_hours", 8))
    alerts = _dedupe_alerts(alerts)
    candidates: list[list[AlertTrigger]] = []

    for bucket_fn in (_group_by_theme, _group_by_category):
        for bucket in bucket_fn(alerts):
            if len(bucket) >= 2 and _within_window(bucket, window_hours):
                candidates.append(_dedupe_alerts(bucket))

    # Deduplicate candidate groups by frozenset of indicators
    seen_groups: set[frozenset[str]] = set()
    unique: list[list[AlertTrigger]] = []
    for group in candidates:
        key = frozenset(a.indicator for a in group)
        if key in seen_groups:
            continue
        seen_groups.add(key)
        unique.append(group)

    unique.sort(key=lambda g: group_score(g, posting_cfg), reverse=True)
    return unique


def best_story_theme(alerts: list[AlertTrigger]) -> str | None:
    all_themes: set[str] = set()
    for a in alerts:
        all_themes |= _alert_themes(a)

    best: tuple[str, int] | None = None
    for story, required in STORY_PATTERNS.items():
        overlap = len(required & all_themes)
        if overlap >= 2 and (best is None or overlap > best[1]):
            best = (story, overlap)

    if best:
        return best[0]

    # Fallback: most common theme tag
    counts: dict[str, int] = {}
    for a in alerts:
        for t in a.themes:
            counts[t] = counts.get(t, 0) + 1
    if counts:
        return max(counts, key=counts.get)
    return None


def filter_stale_alerts(
    alerts: list[AlertTrigger],
    posting_cfg: dict[str, Any],
    now: datetime | None = None,
) -> list[AlertTrigger]:
    now = now or datetime.now(timezone.utc)
    max_age = timedelta(hours=float(posting_cfg.get("alert_max_age_hours", 12)))
    return [a for a in alerts if now - a.timestamp <= max_age]