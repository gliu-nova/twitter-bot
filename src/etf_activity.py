"""ETF flow/volume/options activity signals (IBIT and similar)."""

from __future__ import annotations

from dataclasses import dataclass

import yfinance as yf

from src.fetch import FetchError
from src.stats import percentile_rank


@dataclass
class EtfActivitySnapshot:
    symbol: str
    price: float
    volume: float
    avg_volume_30d: float
    net_assets_usd: float | None
    flow_usd: float | None
    options_volume: float | None
    options_pcr: float | None
    volume_percentile: float | None
    observed_at: str


def _options_activity(ticker: yf.Ticker) -> tuple[float | None, float | None]:
    try:
        expiries = ticker.options
        if not expiries:
            return None, None
        chain = ticker.option_chain(expiries[0])
        call_vol = float(chain.calls["volume"].fillna(0).sum())
        put_vol = float(chain.puts["volume"].fillna(0).sum())
        total = call_vol + put_vol
        if total <= 0:
            return None, None
        return total, put_vol / max(call_vol, 1.0)
    except Exception:
        return None, None


def fetch_etf_activity(symbol: str) -> EtfActivitySnapshot:
    ticker = yf.Ticker(symbol)
    hist = ticker.history(period="6mo")
    if hist.empty or len(hist) < 5:
        raise FetchError(f"No Yahoo history for {symbol}")

    observed = hist.index[-1].strftime("%Y-%m-%d")
    price = float(hist["Close"].iloc[-1])
    volume = float(hist["Volume"].iloc[-1])
    volumes = [float(v) for v in hist["Volume"].tolist()]
    avg_30 = sum(volumes[-30:]) / min(30, len(volumes))

    info = ticker.info or {}
    net_assets = info.get("totalAssets") or info.get("netAssets")
    net_assets_f = float(net_assets) if net_assets is not None else None

    vol_pctile: float | None = None
    if len(volumes) >= 20:
        vol_pctile = percentile_rank(volume, volumes[:-1])

    opt_vol, opt_pcr = _options_activity(ticker)

    return EtfActivitySnapshot(
        symbol=symbol,
        price=price,
        volume=volume,
        avg_volume_30d=avg_30,
        net_assets_usd=net_assets_f,
        flow_usd=None,
        options_volume=opt_vol,
        options_pcr=opt_pcr,
        volume_percentile=vol_pctile,
        observed_at=observed,
    )