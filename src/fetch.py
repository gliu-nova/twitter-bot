from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import requests
import yfinance as yf

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
COINGECKO_BASE = "https://api.coingecko.com/api/v3/simple/price"
BINANCE_BASE = "https://api.binance.com/api/v3/ticker/price"
KRAKEN_BASE = "https://api.kraken.com/0/public/Ticker"
FEAR_GREED_BASE = "https://api.alternative.me/fng/"


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
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        raise FetchError(f"FRED error for {series_id}: {e}") from e
    body = resp.json()
    if body.get("error_code"):
        raise FetchError(f"FRED error for {series_id}: {body.get('error_message', body)}")
    observations = body.get("observations", [])
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
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        raise FetchError(f"CoinGecko error: {e}") from e
    data = resp.json()
    if coin_id not in data or "usd" not in data[coin_id]:
        raise FetchError(f"No CoinGecko price for {coin_id}")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return float(data[coin_id]["usd"]), today


def _kraken_latest(pair: str) -> tuple[float, str]:
    resp = requests.get(KRAKEN_BASE, params={"pair": pair}, timeout=15)
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        raise FetchError(f"Kraken error: {e}") from e
    result = resp.json().get("result")
    if not result:
        raise FetchError(f"No Kraken data for {pair}")
    ticker = next(iter(result.values()))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    return float(ticker["c"][0]), now


def _binance_latest(symbol: str) -> tuple[float, str]:
    resp = requests.get(BINANCE_BASE, params={"symbol": symbol}, timeout=15)
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        raise FetchError(f"Binance error: {e}") from e
    data = resp.json()
    if "price" not in data:
        raise FetchError(f"No Binance price for {symbol}")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    return float(data["price"]), now


def _fear_greed_latest() -> tuple[float, str]:
    resp = requests.get(FEAR_GREED_BASE, params={"limit": 1}, timeout=30)
    resp.raise_for_status()
    entries = resp.json().get("data", [])
    if not entries:
        raise FetchError("No Crypto Fear & Greed data")
    entry = entries[0]
    ts = entry.get("timestamp")
    observed = (
        datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d")
        if ts else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    )
    return float(entry["value"]), observed


def fetch_chart_history(settings: dict[str, Any], *, months: int = 6) -> list[tuple[str, float]]:
    """Daily history for chart rendering. Falls back to source APIs."""
    source = settings["source"]
    period = f"{months}mo"

    if source == "yahoo":
        ticker = yf.Ticker(settings["symbol"])
        hist = ticker.history(period=period)
        if hist.empty:
            return []
        out: list[tuple[str, float]] = []
        for idx, row in hist.iterrows():
            out.append((idx.strftime("%Y-%m-%d"), float(row["Close"])))
        return out

    if source == "fred":
        limit = max(30, months * 31)
        resp = requests.get(
            FRED_BASE,
            params={
                "series_id": settings["series"],
                "api_key": _fred_api_key(),
                "file_type": "json",
                "sort_order": "desc",
                "limit": limit,
            },
            timeout=30,
        )
        resp.raise_for_status()
        observations = [
            (obs["date"], float(obs["value"]))
            for obs in resp.json().get("observations", [])
            if obs.get("value") not in (None, ".", "")
        ]
        return sorted(observations)

    if source == "fred_cpi_yoy":
        limit = max(24, months * 2 + 14)
        resp = requests.get(
            FRED_BASE,
            params={
                "series_id": settings["series"],
                "api_key": _fred_api_key(),
                "file_type": "json",
                "sort_order": "desc",
                "limit": limit,
            },
            timeout=30,
        )
        resp.raise_for_status()
        raw = sorted(
            (obs["date"], float(obs["value"]))
            for obs in resp.json().get("observations", [])
            if obs.get("value") not in (None, ".", "")
        )
        return [
            (d, ((v / raw[i - 12][1]) - 1) * 100)
            for i, (d, v) in enumerate(raw)
            if i >= 12 and raw[i - 12][1]
        ]

    if source == "fear_greed":
        resp = requests.get(FEAR_GREED_BASE, params={"limit": min(200, months * 31)}, timeout=30)
        resp.raise_for_status()
        entries = resp.json().get("data", [])
        out = []
        for e in reversed(entries):
            ts = e.get("timestamp")
            if not ts:
                continue
            day = datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d")
            out.append((day, float(e["value"])))
        return out

    return []


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
    if source == "fear_greed":
        return _fear_greed_latest()
    if source == "binance":
        return _binance_latest(settings["symbol"])
    if source == "kraken":
        return _kraken_latest(settings["pair"])
    raise FetchError(f"Unknown source: {source}")