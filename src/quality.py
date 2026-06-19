from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import Any

import requests

from src.fetch import FRED_BASE, FEAR_GREED_BASE, COINGECKO_BASE, FetchError, fetch_indicator
from src.market_hours import market_hours_note


class QualityError(Exception):
    pass


def validate_value(value: float, name: str) -> None:
    if not isinstance(value, (int, float)):
        raise QualityError(f"{name}: expected number, got {type(value).__name__}")
    if math.isnan(value) or math.isinf(value):
        raise QualityError(f"{name}: invalid value {value}")


def _parse_observed(observed_at: str) -> datetime:
    if observed_at.isdigit():
        return datetime.fromtimestamp(int(observed_at), tz=timezone.utc)
    if "T" in observed_at:
        return datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    return datetime.strptime(observed_at[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)


def check_freshness(observed_at: str, max_stale_hours: float, name: str) -> None:
    observed = _parse_observed(observed_at)
    # Date-only timestamps (no time) — treat as valid through end of that UTC day
    if len(observed_at.strip()) == 10 and "T" not in observed_at and not observed_at.isdigit():
        observed = observed.replace(hour=23, minute=59, second=59)
    age_hours = (datetime.now(timezone.utc) - observed).total_seconds() / 3600
    if age_hours > max_stale_hours:
        raise QualityError(
            f"{name}: data stale ({age_hours:.1f}h old, limit {max_stale_hours}h, as of {observed_at})"
        )


def cross_verify(
    primary: float,
    verify_cfg: dict[str, Any],
    tolerance_pct: float,
    name: str,
) -> None:
    fetch_cfg = {k: v for k, v in verify_cfg.items() if k != "tolerance_pct"}
    secondary, _ = fetch_indicator(fetch_cfg)
    validate_value(secondary, f"{name} verify")
    if secondary == 0:
        if primary != 0:
            raise QualityError(f"{name}: cross-verify mismatch {primary:g} vs 0")
        return
    diff_pct = abs((primary - secondary) / secondary) * 100
    if diff_pct > tolerance_pct:
        raise QualityError(
            f"{name}: cross-verify failed {primary:g} vs {secondary:g} ({diff_pct:.2f}% > {tolerance_pct}%)"
        )


def check_schedule(schedule: str, alerting: bool) -> str | None:
    """Return skip reason if alerts should be suppressed for schedule context."""
    note = market_hours_note(schedule)
    if note and alerting:
        return note
    return None


def run_quality_checks(
    settings: dict[str, Any],
    value: float,
    observed_at: str,
    *,
    for_alert: bool,
) -> str | None:
    """Validate reading. Returns skip-alert reason, or raises QualityError."""
    name = settings["name"]
    q = settings.get("quality") or {}

    validate_value(value, name)
    check_freshness(observed_at, float(q.get("max_stale_hours", 72)), name)

    verify = q.get("verify")
    if verify:
        cross_verify(value, verify, float(verify.get("tolerance_pct", 1.0)), name)

    schedule = q.get("schedule", "macro")
    return check_schedule(schedule, for_alert)


def check_api_health() -> dict[str, str]:
    """Ping data providers; returns source -> ok|error message."""
    results: dict[str, str] = {}

    key = os.getenv("FRED_API_KEY", "").strip()
    if not key:
        results["fred"] = "missing FRED_API_KEY"
    else:
        try:
            r = requests.get(
                FRED_BASE,
                params={"series_id": "DFF", "api_key": key, "file_type": "json", "limit": 1},
                timeout=15,
            )
            r.raise_for_status()
            results["fred"] = "ok"
        except Exception as e:
            results["fred"] = str(e)

    try:
        from src.fetch import _yahoo_latest
        _yahoo_latest("^GSPC")
        results["yahoo"] = "ok"
    except Exception as e:
        results["yahoo"] = str(e)

    try:
        r = requests.get(COINGECKO_BASE, params={"ids": "bitcoin", "vs_currencies": "usd"}, timeout=15)
        r.raise_for_status()
        results["coingecko"] = "ok"
    except Exception as e:
        results["coingecko"] = str(e)

    try:
        r = requests.get(FEAR_GREED_BASE, params={"limit": 1}, timeout=15)
        r.raise_for_status()
        results["fear_greed"] = "ok"
    except Exception as e:
        results["fear_greed"] = str(e)

    return results