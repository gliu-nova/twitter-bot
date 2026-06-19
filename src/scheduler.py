from __future__ import annotations

from typing import Any

import sqlite3

from src.db import minutes_since_last_fetch
from src.market_hours import is_us_equity_session

CRYPTO_KEYS = {"btc", "eth", "sol", "fear_greed"}
US_EQUITY_KEYS = {"sp500", "nasdaq100", "vix", "gold", "silver", "dxy", "oil"}
RATES_FX_KEYS = {"treasury_10y", "yield_curve", "fed_funds", "move", "hy_spread"}
HOUSING_KEYS = {"case_shiller", "mortgage_30y"}
MACRO_KEYS = {
    "cpi_yoy",
    "unemployment",
    "jobless_claims",
    "pmi_manufacturing",
    "ism_services",
    "consumer_sentiment",
    "m2",
}


def poll_schedule(settings: dict[str, Any]) -> str:
    key = settings["key"]
    if key in CRYPTO_KEYS:
        return "crypto_24_7"
    if key in RATES_FX_KEYS:
        return "rates_fx"
    if key in HOUSING_KEYS:
        return "housing"
    if key in MACRO_KEYS:
        return "macro"
    if key in US_EQUITY_KEYS:
        return "us_equity"
    return (settings.get("quality") or {}).get("schedule") or "macro"


def fetch_interval_minutes(settings: dict[str, Any], cfg: dict[str, Any]) -> float:
    sched_cfg = cfg.get("scheduler") or {}
    schedule = poll_schedule(settings)

    if schedule == "crypto_24_7":
        return float(sched_cfg.get("crypto_minutes", 10))

    if schedule == "us_equity":
        if is_us_equity_session():
            return float(sched_cfg.get("market_minutes", 10))
        return float(sched_cfg.get("market_off_hours_minutes", 60))

    if schedule == "rates_fx":
        return float(sched_cfg.get("rates_minutes", 30))

    if schedule == "housing":
        return float(sched_cfg.get("housing_hours", 24)) * 60

    # macro
    return float(sched_cfg.get("macro_hours", 6)) * 60


def is_fetch_due(
    conn: sqlite3.Connection,
    settings: dict[str, Any],
    cfg: dict[str, Any],
    *,
    force: bool = False,
) -> bool:
    if force:
        return True
    interval = fetch_interval_minutes(settings, cfg)
    elapsed = minutes_since_last_fetch(conn, settings["key"])
    if elapsed is None:
        return True
    return elapsed >= interval


def interval_label(settings: dict[str, Any], cfg: dict[str, Any]) -> str:
    mins = fetch_interval_minutes(settings, cfg)
    schedule = poll_schedule(settings)
    if mins >= 60:
        hours = mins / 60
        return f"{hours:g}h ({schedule})"
    return f"{mins:g}m ({schedule})"