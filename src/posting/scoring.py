from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from src.posting.models import AlertTrigger


def _pct_change(old: float | None, new: float) -> float:
    if old is None or old == 0:
        return 0.0
    return abs((new - old) / abs(old)) * 100


def is_fresh(alert: AlertTrigger, posting_cfg: dict[str, Any], now: datetime | None = None) -> bool:
    """Hard gate: stale alerts are rejected before scoring."""
    now = now or datetime.now(timezone.utc)
    max_age = timedelta(hours=float(posting_cfg.get("alert_max_age_hours", 12)))
    return now - alert.timestamp <= max_age


def _magnitude_score(alert: AlertTrigger, settings: dict[str, Any], posting_cfg: dict[str, Any]) -> float:
    unit = alert.alert_unit or settings.get("alert_unit", "percent")

    if unit == "absolute":
        move = alert.magnitude_abs or abs(alert.value - (alert.prev_value or alert.value))
        cap = float(
            posting_cfg.get("magnitude_cap_absolute")
            or settings.get("emergency_alert")
            or 1.0
        )
        normalized = min(move / cap, 1.0) * 100
    else:
        pct = alert.magnitude_pct or _pct_change(alert.prev_value, alert.value)
        cap = float(posting_cfg.get("magnitude_cap_pct", 25))
        normalized = min(pct / cap, 1.0) * 100

    if any(r in ("crosses_above", "crosses_below") for r in alert.rule_types):
        normalized = max(normalized, 70.0)
    if alert.standalone_major:
        normalized = max(normalized, 85.0)
    if alert.alert_tier == "emergency":
        normalized = max(normalized, 90.0)
    elif alert.alert_tier == "major":
        normalized = max(normalized, 75.0)

    return min(normalized, 100.0)


def _rarity_score(settings: dict[str, Any]) -> float:
    return float(settings.get("rarity", 50))


def _audience_score(settings: dict[str, Any]) -> float:
    return float(settings.get("audience_relevance", 50))


def calculate_score(
    alert: AlertTrigger,
    settings: dict[str, Any],
    posting_cfg: dict[str, Any],
) -> float | None:
    """Return score if fresh, None if stale (hard reject)."""
    if not is_fresh(alert, posting_cfg):
        return None

    weights = posting_cfg.get("score_weights") or {
        "magnitude": 0.45,
        "rarity": 0.30,
        "audience": 0.25,
    }
    magnitude = _magnitude_score(alert, settings, posting_cfg)
    rarity = _rarity_score(settings)
    audience = _audience_score(settings)

    score = (
        magnitude * weights["magnitude"]
        + rarity * weights["rarity"]
        + audience * weights["audience"]
    )
    return round(score, 1)