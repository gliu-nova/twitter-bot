from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import requests
import yfinance as yf

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
COINGECKO_BASE = "https://api.coingecko.com/api/v3/simple/price"


class FetchError(Exception):
    pass


def _fred_api_key() -> str:
    key = os.getenv("FRED_API_KEY", "").strip()
    if not key:
        raise FetchError("FRED_API_KEY is not set")
    return key


def _fred_latest(series_id: str) -> tuple[float, str]:
    resp = requests.get(
        FRED_BASE,
        params={
            "series_id": series_id,
            "api_key": _fred_api_key(),
            "file_type": "json",
            "sort_order": "desc",
            "limit": 5,
        },
        timeout=30,
    )
    resp.raise_for_status()
    observations = resp.json().get("observations", [])
    for obs in observations:
        raw = obs.get("value")
        if raw in (None, ".", ""):
            continue
        return float(raw), obs["date"]
    raise FetchError(f"No recent FRED data for {series_id}")


def _fred_cpi_yoy(series_id: str) -> tuple[float, str]:
    resp = requests.get(
        FRED_BASE,
        params={
            "series_id": series_id,
            "api_key": _fred_api_key(),
            "file_type": "json",
            "sort_order": "desc",
            "limit": 24,
        },
        timeout=30,
    )
    resp.raise_for_status()
    observations = [
        (obs["date"], float(obs["value"]))
        for obs in resp.json().get("observations", [])
        if obs.get("value") not in (None, ".", "")
    ]
    if len(observations) < 13:
        raise FetchError("Not enough CPI history for YoY calculation")
    latest_date, latest = observations[0]
    year_ago = next((v for d, v in observations if d <= _shift_year(latest_date)), None)
    if year_ago is None:
        _, year_ago = observations[12]
    yoy = ((latest / year_ago) - 1) * 100
    return yoy, latest_date


def _shift_year(date_str: str) -> str:
    y, m, d = map(int, date_str.split("-"))
    return f"{y - 1}-{m:02d}-{d:02d}"


def _yahoo_latest(symbol: str) -> tuple[float, str]:
    ticker = yf.Ticker(symbol)
    hist = ticker.history(period="5d")
    if hist.empty:
        raise FetchError(f"No Yahoo data for {symbol}")
    row = hist.iloc[-1]
    observed = hist.index[-1].strftime("%Y-%m-%d")
    return float(row["Close"]), observed


def _coingecko_latest(coin_id: str) -> tuple[float, str]:
    resp = requests.get(
        COINGECKO_BASE,
        params={"ids": coin_id, "vs_currencies": "usd"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if coin_id not in data or "usd" not in data[coin_id]:
        raise FetchError(f"No CoinGecko price for {coin_id}")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return float(data[coin_id]["usd"]), today


def fetch_indicator(settings: dict[str, Any]) -> tuple[float, str]:
    source = settings["source"]
    if source == "yahoo":
        return _yahoo_latest(settings["symbol"])
    if source == "fred":
        return _fred_latest(settings["series"])
    if source == "fred_cpi_yoy":
        return _fred_cpi_yoy(settings["series"])
    if source == "coingecko":
        return _coingecko_latest(settings["coin_id"])
    raise FetchError(f"Unknown source: {source}")