"""Bridge twitter-bot alerts to the local market-memory DuckDB store."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from market_memory import EventDB
from market_memory.indicators import memory_query_for_indicator
from market_memory.models import EventCreate, SimilarityQuery

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = (ROOT.parent / "market-memory" / "data").resolve()

INDICATOR_MEMORY_MAP: dict[str, dict[str, Any]] = {
    key: mapping
    for key in (
        "btc_liquidations", "eth_liquidations", "sol_liquidations",
        "btc_funding", "eth_funding", "sol_funding",
        "btc_basis", "eth_basis", "sol_basis",
        "btc_exchange_spread", "eth_exchange_spread", "sol_exchange_spread",
        "fed_funds",
        "sp500", "nasdaq100", "vix", "dxy", "gold", "silver", "move",
        "btc", "eth", "sol",
        "oil", "hy_spread", "treasury_10y", "yield_curve",
        "jobless_claims", "unemployment", "cpi_yoy", "m2",
        "mortgage_30y", "consumer_sentiment", "case_shiller",
        "pmi_manufacturing", "ism_services",
        "fear_greed",
    )
    if (mapping := memory_query_for_indicator(key)) is not None
}


@dataclass
class MemoryContextResult:
    line: str | None = None
    eligible: bool = False
    queried: bool = False
    skip_reason: str | None = None
    reject_reason: str | None = None
    occurrences: int | None = None
    percentile: float | None = None


def _resolve_data_dir(cfg: dict[str, Any]) -> Path:
    mm_cfg = cfg.get("market_memory") or {}
    raw = os.environ.get("MARKET_MEMORY_DATA_DIR") or mm_cfg.get("data_dir")
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


def _price_direction(alert_value: float, prev_value: float | None) -> str | None:
    if prev_value is None:
        return None
    if alert_value > prev_value:
        return "up"
    if alert_value < prev_value:
        return "down"
    return None


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
    elif alert.indicator.endswith("_exchange_spread"):
        direction = "wide"
    elif alert.indicator in ("btc", "eth", "sol", "sp500", "nasdaq100", "gold", "silver", "oil"):
        direction = _price_direction(float(alert.value), alert.prev_value)
    return SimilarityQuery(
        event_type=mapping["event_type"],
        asset=mapping.get("asset"),
        indicator_type=mapping.get("indicator_type"),
        direction=direction,
        since=since,
    )


def memory_context_eligible(alert: Any, cfg: dict[str, Any] | None) -> bool:
    if not cfg or not memory_enabled(cfg):
        return False
    return alert.indicator in INDICATOR_MEMORY_MAP


def memory_context_decision(alert: Any, cfg: dict[str, Any] | None) -> MemoryContextResult:
    """Resolve market-memory context with structured eligibility/query outcome."""
    result = MemoryContextResult()
    if not cfg:
        result.skip_reason = "no_compose_cfg"
        return result
    if not memory_enabled(cfg):
        result.skip_reason = "memory_disabled"
        return result
    mapping = INDICATOR_MEMORY_MAP.get(alert.indicator)
    if not mapping:
        result.skip_reason = "indicator_not_mapped"
        return result
    result.eligible = True
    query = build_similarity_query(alert, cfg)
    if not query:
        result.skip_reason = "query_build_failed"
        return result
    mm_cfg = cfg.get("market_memory") or {}
    min_occurrences = int(mm_cfg.get("min_occurrences", 3))
    result.queried = True
    db = EventDB(data_dir=str(_resolve_data_dir(cfg)))
    try:
        ctx = db.tweet_context(query, current_value=float(alert.value))
    except Exception as exc:
        result.reject_reason = f"query_error:{type(exc).__name__}"
        return result
    finally:
        db.close()
    result.occurrences = ctx.occurrences
    result.percentile = ctx.percentile
    if ctx.occurrences < min_occurrences:
        result.reject_reason = f"occurrences_below_min:{ctx.occurrences}<{min_occurrences}"
        return result
    if ctx.percentile is not None:
        if not (ctx.percentile >= 90 or ctx.percentile <= 10 or ctx.occurrences >= 8):
            result.reject_reason = f"percentile_not_extreme:pct={ctx.percentile}"
            return result
    result.line = ctx.tweet_context
    return result


def memory_context_line(alert: Any, cfg: dict[str, Any]) -> str | None:
    """Return a tweet context line from market-memory when it adds real signal."""
    return memory_context_decision(alert, cfg).line


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


def _event_tags(indicator: str, mapping: dict[str, Any]) -> list[str]:
    if mapping.get("asset"):
        return ["crypto"]
    if indicator in ("sp500", "nasdaq100", "vix"):
        return ["equities"]
    if indicator in ("gold", "silver", "oil"):
        return ["commodities"]
    if indicator == "fear_greed":
        return ["crypto", "sentiment"]
    return ["macro"]


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
    elif alert.indicator.endswith("_exchange_spread"):
        direction = "wide"
    elif alert.indicator.endswith("_liquidations"):
        direction = "spike"
        if alert.liq_long_usd is not None:
            metadata["long_usd"] = alert.liq_long_usd
        if alert.liq_short_usd is not None:
            metadata["short_usd"] = alert.liq_short_usd
    elif alert.indicator in ("btc", "eth", "sol", "sp500", "nasdaq100", "gold", "silver", "oil"):
        direction = _price_direction(float(alert.value), alert.prev_value)
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
        tags=_event_tags(alert.indicator, mapping),
        metadata=metadata,
    )
    db = EventDB(data_dir=str(_resolve_data_dir(cfg)))
    try:
        db.ingest_events([event])
    finally:
        db.close()