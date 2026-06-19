from __future__ import annotations

from typing import Any

from src.posting.models import AlertTrigger


def _pct_change(old: float | None, new: float) -> float:
    if old is None or old == 0:
        return 0.0
    return abs((new - old) / abs(old)) * 100


def _magnitude_score(alert: AlertTrigger, settings: dict[str, Any], posting_cfg: dict[str, Any]) -> float:
    pct = alert.magnitude_pct or _pct_change(alert.prev_value, alert.value)
    cap = float(posting_cfg.get("magnitude_cap_pct", 25))
    normalized = min(pct / cap, 1.0) * 100

    # Level-cross rules (VIX>30, yield curve) get a floor
    if any(r in ("crosses_above", "crosses_below") for r in alert.rule_types):
        normalized = max(normalized, 70.0)
    if alert.standalone_major:
        normalized = max(normalized, 85.0)
    return min(normalized, 100.0)


def _rarity_score(settings: dict[str, Any]) -> float:
    return float(settings.get("rarity", 50))


def _audience_score(settings: dict[str, Any]) -> float:
    return float(settings.get("audience_relevance", 50))


def _freshness_score(alert: AlertTrigger, posting_cfg: dict[str, Any]) -> float:
    """Alerts are fresh when just triggered; decay handled at queue level."""
    return float(posting_cfg.get("freshness_default", 90))


def calculate_score(
    alert: AlertTrigger,
    settings: dict[str, Any],
    posting_cfg: dict[str, Any],
) -> float:
    weights = posting_cfg.get("score_weights") or {
        "magnitude": 0.40,
        "rarity": 0.30,
        "audience": 0.20,
        "freshness": 0.10,
    }
    magnitude = _magnitude_score(alert, settings, posting_cfg)
    rarity = _rarity_score(settings)
    audience = _audience_score(settings)
    freshness = _freshness_score(alert, posting_cfg)

    score = (
        magnitude * weights["magnitude"]
        + rarity * weights["rarity"]
        + audience * weights["audience"]
        + freshness * weights["freshness"]
    )
    return round(score, 1)