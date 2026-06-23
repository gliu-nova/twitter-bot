from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

from src.fetch import FetchError, _coingecko_latest, _kraken_latest

OKX_BASE = "https://www.okx.com/api/v5"
HL_INFO = "https://api.hyperliquid.xyz/info"
COINBASE_BASE = "https://api.coinbase.com/v2/prices"

ASSET_MAP: dict[str, dict[str, str]] = {
    "BTC": {
        "okx_swap": "BTC-USDT-SWAP",
        "okx_uly": "BTC-USDT",
        "okx_index": "BTC-USDT",
        "hl": "BTC",
        "kraken": "XBTUSD",
        "coinbase": "BTC-USD",
        "coingecko": "bitcoin",
    },
    "ETH": {
        "okx_swap": "ETH-USDT-SWAP",
        "okx_uly": "ETH-USDT",
        "okx_index": "ETH-USDT",
        "hl": "ETH",
        "kraken": "ETHUSD",
        "coinbase": "ETH-USD",
        "coingecko": "ethereum",
    },
    "SOL": {
        "okx_swap": "SOL-USDT-SWAP",
        "okx_uly": "SOL-USDT",
        "okx_index": "SOL-USDT",
        "hl": "SOL",
        "kraken": "SOLUSD",
        "coinbase": "SOL-USD",
        "coingecko": "solana",
    },
}


def _now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def _resolve_asset(settings: dict[str, Any]) -> dict[str, str]:
    asset = settings.get("asset", "").upper()
    if asset not in ASSET_MAP:
        raise FetchError(f"Unknown asset: {asset}")
    return ASSET_MAP[asset]


def _basis_bps(mark: float, index: float) -> float:
    if index == 0:
        raise FetchError("index price is zero")
    return (mark - index) / index * 10000


def _okx_funding(inst_id: str) -> tuple[float, str]:
    resp = requests.get(
        f"{OKX_BASE}/public/funding-rate",
        params={"instId": inst_id},
        timeout=15,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") != "0" or not body.get("data"):
        raise FetchError(f"OKX funding error for {inst_id}: {body.get('msg', body)}")
    return float(body["data"][0]["fundingRate"]), _now_ts()


def _okx_basis(inst_id: str, index_inst: str) -> tuple[float, str]:
    mark_resp = requests.get(
        f"{OKX_BASE}/public/mark-price",
        params={"instId": inst_id},
        timeout=15,
    )
    idx_resp = requests.get(
        f"{OKX_BASE}/market/index-tickers",
        params={"instId": index_inst},
        timeout=15,
    )
    mark_resp.raise_for_status()
    idx_resp.raise_for_status()
    mark_body = mark_resp.json()
    idx_body = idx_resp.json()
    if mark_body.get("code") != "0" or not mark_body.get("data"):
        raise FetchError(f"OKX mark error for {inst_id}")
    if idx_body.get("code") != "0" or not idx_body.get("data"):
        raise FetchError(f"OKX index error for {index_inst}")
    mark = float(mark_body["data"][0]["markPx"])
    index = float(idx_body["data"][0]["idxPx"])
    return _basis_bps(mark, index), _now_ts()


def _hl_ctx(asset: str) -> dict[str, Any]:
    resp = requests.post(HL_INFO, json={"type": "metaAndAssetCtxs"}, timeout=15)
    resp.raise_for_status()
    meta, ctxs = resp.json()
    names = [u["name"] for u in meta["universe"]]
    try:
        idx = names.index(asset)
    except ValueError as e:
        raise FetchError(f"Hyperliquid asset not found: {asset}") from e
    return ctxs[idx]


def _hl_funding(asset: str) -> tuple[float, str]:
    ctx = _hl_ctx(asset)
    return float(ctx["funding"]), _now_ts()


def _hl_basis(asset: str) -> tuple[float, str]:
    ctx = _hl_ctx(asset)
    mark = float(ctx["markPx"])
    index = float(ctx["oraclePx"])
    return _basis_bps(mark, index), _now_ts()


def _coinbase_spot(product: str) -> tuple[float, str]:
    resp = requests.get(f"{COINBASE_BASE}/{product}/spot", timeout=15)
    resp.raise_for_status()
    data = resp.json().get("data")
    if not data or "amount" not in data:
        raise FetchError(f"No Coinbase price for {product}")
    return float(data["amount"]), _now_ts()


def _exchange_spread(kraken_pair: str, coinbase_product: str) -> tuple[float, str]:
    kraken_px, _ = _kraken_latest(kraken_pair)
    coinbase_px, _ = _coinbase_spot(coinbase_product)
    mid = (kraken_px + coinbase_px) / 2
    if mid == 0:
        raise FetchError("exchange spread mid is zero")
    spread_bps = abs(kraken_px - coinbase_px) / mid * 10000
    return spread_bps, _now_ts()


def _okx_liquidations(uly: str, *, window_minutes: int) -> tuple[float, float, float, str]:
    resp = requests.get(
        f"{OKX_BASE}/public/liquidation-orders",
        params={"instType": "SWAP", "uly": uly, "state": "filled", "limit": 100},
        timeout=15,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") != "0":
        raise FetchError(f"OKX liquidations error for {uly}: {body.get('msg', body)}")

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    cutoff = now_ms - window_minutes * 60 * 1000
    long_usd = 0.0
    short_usd = 0.0
    for bucket in body.get("data", []):
        for detail in bucket.get("details", []):
            ts = int(detail.get("time") or detail.get("ts") or 0)
            if ts < cutoff:
                continue
            usd = float(detail["sz"]) * float(detail["bkPx"])
            pos = (detail.get("posSide") or "").lower()
            if pos == "long":
                long_usd += usd
            elif pos == "short":
                short_usd += usd
            elif (detail.get("side") or "").lower() == "sell":
                long_usd += usd
            else:
                short_usd += usd
    return long_usd + short_usd, long_usd, short_usd, _now_ts()


def fetch_liquidation_metric(settings: dict[str, Any]) -> tuple[float, float, float, str]:
    asset_cfg = _resolve_asset(settings) if settings.get("asset") else {}
    uly = settings.get("uly") or asset_cfg["okx_uly"]
    window = int(settings.get("liquidation_window_minutes", 60))
    return _okx_liquidations(uly, window_minutes=window)


def fetch_crypto_metric(settings: dict[str, Any]) -> tuple[float, str]:
    source = settings["source"]
    asset_cfg = _resolve_asset(settings) if settings.get("asset") else {}

    if source == "okx_funding":
        inst = settings.get("inst_id") or asset_cfg["okx_swap"]
        return _okx_funding(inst)

    if source == "hyperliquid_funding":
        asset = settings.get("asset", "").upper()
        return _hl_funding(asset)

    if source == "okx_basis":
        inst = settings.get("inst_id") or asset_cfg["okx_swap"]
        index_inst = settings.get("index_inst") or asset_cfg["okx_index"]
        return _okx_basis(inst, index_inst)

    if source == "hyperliquid_basis":
        asset = settings.get("asset", "").upper()
        return _hl_basis(asset)

    if source == "exchange_spread":
        kraken_pair = settings.get("kraken_pair") or asset_cfg["kraken"]
        coinbase_product = settings.get("coinbase_product") or asset_cfg["coinbase"]
        return _exchange_spread(kraken_pair, coinbase_product)

    if source == "okx_liquidations":
        total, _, _, ts = fetch_liquidation_metric(settings)
        return total, ts

    raise FetchError(f"Unknown crypto metric source: {source}")


def verify_exchange_spread_mid(settings: dict[str, Any], tolerance_pct: float, name: str) -> None:
    """Cross-check Kraken/Coinbase mid against CoinGecko."""
    asset_cfg = _resolve_asset(settings)
    kraken_px, _ = _kraken_latest(asset_cfg["kraken"])
    coinbase_px, _ = _coinbase_spot(asset_cfg["coinbase"])
    mid = (kraken_px + coinbase_px) / 2
    cg_px, _ = _coingecko_latest(asset_cfg["coingecko"])
    if mid == 0:
        raise FetchError(f"{name}: spread mid is zero")
    diff_pct = abs((mid - cg_px) / cg_px) * 100
    if diff_pct > tolerance_pct:
        raise FetchError(
            f"{name}: spread mid verify failed {mid:g} vs CoinGecko {cg_px:g} ({diff_pct:.2f}% > {tolerance_pct}%)"
        )