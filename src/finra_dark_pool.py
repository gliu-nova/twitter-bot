"""FINRA Reg SHO dark-pool proxy for SPY (volume + short %)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from src.fetch import FetchError


def fetch_finra_dark_pool_both(
    symbol: str = "SPY",
    *,
    lookback_days: int = 21,
) -> tuple[float, float, str]:
    """Return latest (volume_millions, pct, date)."""
    from market_memory.sources import fetch_finra_dark_pool_history

    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    with httpx.Client() as client:
        volume_rows, pct_rows = fetch_finra_dark_pool_history(
            client,
            since=since,
            symbol=symbol,
        )
    if not volume_rows or not pct_rows:
        raise FetchError(f"No FINRA dark pool data for {symbol}")
    vol_date, volume = volume_rows[-1]
    pct_date, pct = pct_rows[-1]
    observed = max(vol_date, pct_date)
    return float(volume), float(pct), observed


def fetch_finra_dark_pool_both_history(
    symbol: str = "SPY",
    *,
    since: datetime,
) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
    from market_memory.sources import fetch_finra_dark_pool_history as _fetch_history

    with httpx.Client() as client:
        return _fetch_history(client, since=since, symbol=symbol)


def fetch_finra_dark_pool_latest(
    symbol: str = "SPY",
    *,
    volume: bool,
    lookback_days: int = 21,
) -> tuple[float, str]:
    """Return latest (value, date) from FINRA off-exchange / short-volume proxy."""
    from market_memory.sources import fetch_finra_dark_pool_history

    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    with httpx.Client() as client:
        volume_rows, pct_rows = fetch_finra_dark_pool_history(
            client,
            since=since,
            symbol=symbol,
        )
    rows = volume_rows if volume else pct_rows
    if not rows:
        raise FetchError(f"No FINRA dark pool data for {symbol}")
    observed, value = rows[-1]
    return float(value), observed


def fetch_finra_dark_pool_history(
    symbol: str = "SPY",
    *,
    volume: bool,
    months: int = 6,
) -> list[tuple[str, float]]:
    from market_memory.sources import fetch_finra_dark_pool_history as _fetch_history

    since = datetime.now(timezone.utc) - timedelta(days=max(30, months * 31))
    with httpx.Client() as client:
        volume_rows, pct_rows = _fetch_history(client, since=since, symbol=symbol)
    return volume_rows if volume else pct_rows