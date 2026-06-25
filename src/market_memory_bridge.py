"""Bridge twitter-bot alerts to the local market-memory DuckDB store."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from market_memory import EventDB
from market_memory.models import EventCreate, SimilarityQuery

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = (ROOT.parent / "market-memory" / "data").resolve()

INDICATOR_MEMORY_MAP: dict[str, dict[str, Any]] = {
    "btc_liquidations": {
        "event_type": "market_surge",
        "asset": "BTC",
        "indicator_type": "liquidations",
        "direction": "spike",
    },
    "eth_liquidations": {
        "event_type": "market_surge",
        "asset": "ETH",
        "indicator_type": "liquidations",
        "direction": "spike",
    },
    "sol_liquidations": {
        "event_type": "market_surge",
        "asset": "SOL",
        "indicator_type": "liquidations",
        "direction": "spike",
    },
    "btc_funding": {"event_type": "market_surge", "asset": "BTC", "indicator_type": "funding"},
    "eth_funding": {"event_type": "market_surge", "asset": "ETH", "indicator_type": "funding"},
    "sol_funding": {"event_type": "market_surge", "asset": "SOL", "indicator_type": "funding"},
    "btc_basis": {"event_type": "market_surge", "asset": "BTC", "indicator_type": "basis"},
    "eth_basis": {"event_type": "market_surge", "asset": "ETH", "indicator_type": "basis"},
    "sol_basis": {"event_type": "market_surge", "asset": "SOL", "indicator_type": "basis"},
    "fed_funds": {"event_type": "fed_announcement"},
}


def _resolve_data_dir(cfg: dict[str, Any]) -> Path:
    mm_cfg = cfg.get("market_memory") or {}
    raw = mm_cfg.get("data_dir") or os.environ.get("MARKET_MEMORY_DATA_DIR")
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = (ROOT / path).resolve()
        return path
    return DEFAULT_DATA_DIR


def memory_enabled(cfg: dict[str, Any]) -> bool:
    mm_cfg = cfg.get("market_memory") or {}
    if "enabled" in mm_cfg:
        return bool(mm_cfg["enabled"])
    return _resolve_data_dir(cfg).exists()


def _funding_direction(alert_value: float) -> str:
    if alert_value < -0.00001:
        return "extreme"
    if alert_value > 0.0003:
        return "extreme"
    return "reset"


def _basis_direction(alert_value: float, prev_value: float | None) -> str:
    if alert_value < 0:
        return "negative"
    if prev_value is not None and alert_value < prev_value:
        return "negative"
    return "positive"


def build_similarity_query(alert: Any, cfg: dict[str, Any]) -> SimilarityQuery | None:
    mapping = INDICATOR_MEMORY_MAP.get(alert.indicator)
    if not mapping:
        return None
    mm_cfg = cfg.get("market_memory") or {}
    since_raw = mm_cfg.get("since", "2021-01-01")
    since = datetime.fromisoformat(str(since_raw)).replace(tzinfo=timezone.utc)
    direction = mapping.get("direction")
    if alert.indicator.endswith("_funding"):
        direction = _funding_direction(float(alert.value))
    elif alert.indicator.endswith("_basis"):
        direction = _basis_direction(float(alert.value), alert.prev_value)
    return SimilarityQuery(
        event_type=mapping["event_type"],
        asset=mapping.get("asset"),
        indicator_type=mapping.get("indicator_type"),
        direction=direction,
        since=since,
    )


def memory_context_line(alert: Any, cfg: dict[str, Any]) -> str | None:
    """Return a tweet context line from market-memory when it adds real signal."""
    if not memory_enabled(cfg):
        return None
    query = build_similarity_query(alert, cfg)
    if not query:
        return None
    mm_cfg = cfg.get("market_memory") or {}
    min_occurrences = int(mm_cfg.get("min_occurrences", 3))
    db = EventDB(data_dir=str(_resolve_data_dir(cfg)))
    try:
        ctx = db.tweet_context(query, current_value=float(alert.value))
    finally:
        db.close()
    if ctx.occurrences < min_occurrences:
        return None
    if ctx.percentile is not None:
        if not (ctx.percentile >= 90 or ctx.percentile <= 10 or ctx.occurrences >= 8):
            return None
    return ctx.tweet_context


def maybe_sync_market_memory(cfg: dict[str, Any]) -> dict[str, Any] | None:
    """Run incremental market-memory sync on poll cadence when enabled."""
    if not memory_enabled(cfg):
        return None
    mm_cfg = cfg.get("market_memory") or {}
    if not mm_cfg.get("sync_on_poll", True):
        return None
    interval = int(mm_cfg.get("sync_interval_minutes", 60))
    since_raw = mm_cfg.get("since", "2021-01-01")
    data_dir = str(_resolve_data_dir(cfg))
    try:
        from datetime import datetime as dt

        from market_memory.sync import sync_database

        return sync_database(
            data_dir=data_dir,
            since=dt.fromisoformat(str(since_raw)).replace(tzinfo=timezone.utc),
            interval_minutes=interval,
            seed_verified_liquidations=bool(mm_cfg.get("seed_verified_liquidations", False)),
        )
    except Exception as exc:
        print(f"[market-memory] sync skipped: {exc}")
        return None


def record_posted_alert(alert: Any, cfg: dict[str, Any]) -> None:
    """Persist a posted alert as a market-memory event for future context."""
    if not memory_enabled(cfg):
        return
    mapping = INDICATOR_MEMORY_MAP.get(alert.indicator)
    if not mapping:
        return
    ts = alert.timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    direction = mapping.get("direction")
    metadata: dict[str, Any] = {"source_indicator": alert.indicator, "alert_tier": alert.alert_tier}
    if alert.indicator.endswith("_funding"):
        direction = _funding_direction(float(alert.value))
    elif alert.indicator.endswith("_basis"):
        direction = _basis_direction(float(alert.value), alert.prev_value)
    elif alert.indicator.endswith("_liquidations"):
        if alert.liq_long_usd is not None:
            metadata["long_usd"] = alert.liq_long_usd
        if alert.liq_short_usd is not None:
            metadata["short_usd"] = alert.liq_short_usd
    pct = None
    if alert.prev_value not in (None, 0):
        pct = (alert.value - alert.prev_value) / abs(alert.prev_value) * 100
    event = EventCreate(
        id=f"bot-{alert.indicator}-{int(ts.timestamp())}",
        timestamp=ts,
        event_type=mapping["event_type"],
        asset=mapping.get("asset"),
        indicator_type=mapping.get("indicator_type"),
        timeframe="1h" if alert.indicator.endswith("_liquidations") else None,
        value=float(alert.value),
        percent_change=pct,
        direction=direction,
        source="twitter-bot",
        tags=["crypto"] if mapping.get("asset") else ["macro"],
        metadata=metadata,
    )
    db = EventDB(data_dir=str(_resolve_data_dir(cfg)))
    try:
        db.ingest_events([event])
    finally:
        db.close()