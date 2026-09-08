"""Bridge Cross-Asset-Signal-Engine to market-memory Blockscout on-chain ingestion.

Each bot poll can:
  1. Ingest watched addresses via market_memory.blockscout
  2. Convert whale transfers and high-EV trader scores into AlertTrigger rows

Requires market-memory with the blockscout package and BLOCKSCOUT_API_KEY.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import ROOT
from src.etherscan_bridge import (
    WHALE_INDICATOR,
    WHALE_REASON_PREFIX,
    parse_whale_meta,
    whale_dict_to_alert,
)
from src.posting.models import AlertTrigger

TRADER_INDICATOR = "eth_trader"
TRADER_REASON_PREFIX = "trader_"

_DEFAULT_MM = (ROOT.parent / "market-memory").resolve()
_DEFAULT_DB = _DEFAULT_MM / "data" / "blockscout.db"
_DEFAULT_WATCHLIST_LOCAL = ROOT / "data" / "etherscan_watchlist.yaml"
_DEFAULT_WATCHLIST_MM = _DEFAULT_MM / "data" / "watchlist.yaml"

# Blockscout explorer front-ends (strip /api/v2 from API base)
_EXPLORER_BASES: dict[str, str] = {
    "ethereum": "https://eth.blockscout.com",
    "eth": "https://eth.blockscout.com",
    "mainnet": "https://eth.blockscout.com",
    "base": "https://base.blockscout.com",
    "optimism": "https://optimism.blockscout.com",
    "op": "https://optimism.blockscout.com",
    "arbitrum": "https://arbitrum.blockscout.com",
    "polygon": "https://polygon.blockscout.com",
    "gnosis": "https://gnosis.blockscout.com",
}


def _default_watchlist() -> Path:
    if _DEFAULT_WATCHLIST_LOCAL.is_file():
        return _DEFAULT_WATCHLIST_LOCAL
    return _DEFAULT_WATCHLIST_MM


def blockscout_enabled(cfg: dict[str, Any]) -> bool:
    bsc = _blockscout_cfg(cfg)
    if "enabled" in bsc:
        return bool(bsc["enabled"])
    return bool(os.environ.get("BLOCKSCOUT_API_KEY", "").strip())


def _blockscout_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    if isinstance(cfg.get("blockscout"), dict):
        return cfg["blockscout"]
    mm = cfg.get("market_memory") or {}
    nested = mm.get("blockscout")
    return nested if isinstance(nested, dict) else {}


def _resolve_path(raw: str | Path | None, default: Path) -> Path:
    if not raw:
        return default
    path = Path(raw)
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    return path


def _explorer_tx_url(instance: str, tx_hash: str) -> str:
    base = _EXPLORER_BASES.get(instance.lower().strip(), _EXPLORER_BASES["ethereum"])
    return f"{base}/tx/{tx_hash}"


def _load_mm_config(bsc: dict[str, Any]):
    from market_memory.blockscout import load_blockscout_config

    db_path = _resolve_path(
        bsc.get("db_path") or os.environ.get("BLOCKSCOUT_DB_PATH"),
        _DEFAULT_DB,
    )
    instance = bsc.get("instance") or os.environ.get("BLOCKSCOUT_INSTANCE", "ethereum")
    watchlist = bsc.get("watchlist_path") or os.environ.get("BLOCKSCOUT_WATCHLIST_PATH")
    threshold = bsc.get("whale_threshold_eth") or bsc.get("large_transfer_eth")
    rate = bsc.get("rate_limit_delay")
    min_score = bsc.get("trader_min_score") or bsc.get("high_ev_min_score")

    return load_blockscout_config(
        db_path=db_path,
        instance=instance,
        large_transfer_eth=float(threshold) if threshold is not None else None,
        rate_limit_delay=float(rate) if rate is not None else None,
        watchlist_path=watchlist,
        api_key=os.environ.get("BLOCKSCOUT_API_KEY"),
    )


def maybe_ingest_blockscout(cfg: dict[str, Any]) -> dict[str, Any] | None:
    """Run Blockscout ingest for configured addresses. Returns a small report or None."""
    if not blockscout_enabled(cfg):
        return None
    bsc = _blockscout_cfg(cfg)
    if not bsc.get("ingest_on_poll", True):
        return {"skipped": True, "reason": "ingest_on_poll=false"}

    try:
        from market_memory.blockscout import run_ingest, run_ingest_entries
        from market_memory.blockscout.pipeline import WatchTarget
        from market_memory.etherscan.watchlist import load_watchlist, merge_cli_addresses
    except ImportError as exc:
        print(f"[blockscout] market_memory.blockscout not available: {exc}")
        return {"skipped": True, "reason": f"import_error:{exc}"}

    try:
        mm_cfg = _load_mm_config(bsc)
    except ValueError as exc:
        print(f"[blockscout] config error: {exc}")
        return {"skipped": True, "reason": str(exc)}

    mode = str(bsc.get("mode") or "account")
    addresses = list(bsc.get("addresses") or [])
    wl_path = bsc.get("watchlist_path") or os.environ.get("BLOCKSCOUT_WATCHLIST_PATH")
    if not wl_path:
        default_wl = _default_watchlist()
        if default_wl.is_file():
            wl_path = str(default_wl)

    watchlist = None
    if wl_path:
        try:
            watchlist = load_watchlist(
                _resolve_path(wl_path, _default_watchlist()),
                default_chain=mm_cfg.instance,
            )
        except Exception as exc:
            print(f"[blockscout] watchlist load failed: {exc}")
            return {"skipped": True, "reason": f"watchlist:{exc}"}

    merged = merge_cli_addresses(
        [str(a) for a in addresses],
        watchlist,
        chain_id=mm_cfg.chain_id,
        chain_name=mm_cfg.instance,
    )

    report: dict[str, Any] = {
        "skipped": False,
        "mode": mode,
        "instance": mm_cfg.instance,
        "entries": len(merged),
        "db_path": str(mm_cfg.db_path),
        "results": [],
        "whales_new": 0,
        "traders_seen": 0,
    }

    try:
        if not merged:
            result = run_ingest(
                mode="stats",
                include_stats=True,
                whale_alerts=False,
                score_trader=False,
                config=mm_cfg,
            )
            report["results"].append(result.to_dict())
        else:
            default_role = str(bsc.get("default_role") or "monitor")
            entries = [
                WatchTarget(address=e.address, label=e.label, role=default_role)
                for e in merged
            ]
            results = run_ingest_entries(
                entries,
                mode=mode,
                include_stats=True,
                config=mm_cfg,
            )
            for r in results:
                report["results"].append(r.to_dict())
                report["whales_new"] += len(r.whales or [])
                if r.trader_score is not None:
                    report["traders_seen"] += 1
    except Exception as exc:
        print(f"[blockscout] ingest failed: {exc}")
        report["error"] = str(exc)
        return report

    return report


def blockscout_whale_to_alert(
    raw: dict[str, Any],
    *,
    cfg: dict[str, Any],
    instance: str,
) -> AlertTrigger:
    """Normalize Blockscout whale dict into the shared eth_whale AlertTrigger."""
    tx_hash = str(raw.get("tx_hash") or "")
    chain_name = instance or "ethereum"
    ts_raw = raw.get("timestamp") or raw.get("time_stamp")
    time_stamp: int | None = None
    if ts_raw is not None:
        if isinstance(ts_raw, (int, float)):
            time_stamp = int(ts_raw)
        else:
            try:
                time_stamp = int(datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00")).timestamp())
            except (TypeError, ValueError):
                time_stamp = None

    normalized = {
        "tx_hash": tx_hash,
        "chain_id": raw.get("chain_id", 1),
        "chain_name": chain_name,
        "value_eth": raw.get("value_eth"),
        "from_address": raw.get("from_address"),
        "to_address": raw.get("to_address"),
        "watched_address": raw.get("watched_address"),
        "label": raw.get("label"),
        "time_stamp": time_stamp,
        "explorer_url": raw.get("explorer_url") or _explorer_tx_url(chain_name, tx_hash),
    }
    return whale_dict_to_alert(normalized, cfg=cfg, source="blockscout")


def trader_result_to_alert(
    result: dict[str, Any],
    *,
    cfg: dict[str, Any],
) -> AlertTrigger | None:
    """Build eth_trader alert from an ingest result with trader_score."""
    from src.config import indicator_settings

    score = result.get("trader_score")
    if score is None:
        return None
    bsc = _blockscout_cfg(cfg)
    min_score = float(bsc.get("trader_min_score") or bsc.get("high_ev_min_score") or 75)
    if float(score) < min_score:
        return None

    settings = indicator_settings(cfg, TRADER_INDICATOR)
    address = str(result.get("address") or "")
    label = result.get("label")
    instance = str(result.get("instance") or "ethereum")
    chain_id = int(result.get("chain_id") or 1)

    reasons = [
        f"high-EV trader score >= {min_score:.0f} on {instance}",
        f"{TRADER_REASON_PREFIX}address:{address}",
        f"{TRADER_REASON_PREFIX}chain:{instance}",
        f"{TRADER_REASON_PREFIX}chain_id:{chain_id}",
        f"{TRADER_REASON_PREFIX}tx_count:{result.get('txs_inserted', 0)}",
    ]
    if label:
        reasons.append(f"{TRADER_REASON_PREFIX}label:{label}")

    name = settings.get("name") or "High-EV on-chain trader"
    if label:
        name = f"{name} ({label})"

    tier = "normal"
    major_at = float(settings.get("major_alert") or 85)
    emergency_at = float(settings.get("emergency_alert") or 92)
    if float(score) >= emergency_at:
        tier = "emergency"
    elif float(score) >= major_at:
        tier = "major"

    return AlertTrigger(
        indicator=TRADER_INDICATOR,
        name=name,
        value=float(score),
        prev_value=None,
        reasons=reasons,
        rule_types=["trader_score"],
        themes=list(settings.get("themes") or ["crypto", "risk_on"]),
        category=str(settings.get("category") or "crypto"),
        is_macro=False,
        timestamp=datetime.now(timezone.utc),
        magnitude_pct=0.0,
        magnitude_abs=float(score),
        alert_unit="absolute",
        alert_tier=tier,
        standalone_major=bool(settings.get("standalone_major", False)),
    )


def parse_trader_meta(alert: AlertTrigger) -> dict[str, str]:
    meta: dict[str, str] = {}
    for reason in alert.reasons:
        if not reason.startswith(TRADER_REASON_PREFIX):
            continue
        body = reason[len(TRADER_REASON_PREFIX) :]
        key, _, val = body.partition(":")
        if key:
            meta[key] = val
    return meta


def _pending_trader_addresses(conn: Any) -> set[str]:
    rows = conn.execute(
        """
        SELECT reasons FROM pending_alerts
        WHERE indicator = ? AND processed = 0
        """,
        (TRADER_INDICATOR,),
    ).fetchall()
    found: set[str] = set()
    for row in rows:
        for reason in (row["reasons"] or "").split("|"):
            if reason.startswith(f"{TRADER_REASON_PREFIX}address:"):
                found.add(reason.split(":", 1)[1].lower())
    return found


def _pending_whale_txs(conn: Any) -> set[str]:
    rows = conn.execute(
        """
        SELECT reasons FROM pending_alerts
        WHERE indicator = ? AND processed = 0
        """,
        (WHALE_INDICATOR,),
    ).fetchall()
    found: set[str] = set()
    for row in rows:
        for reason in (row["reasons"] or "").split("|"):
            if reason.startswith(f"{WHALE_REASON_PREFIX}tx:"):
                found.add(reason.split(":", 1)[1])
    return found


def process_blockscout_for_bot(
    conn: Any,
    cfg: dict[str, Any],
    *,
    enqueue_fn: Any | None = None,
) -> dict[str, Any]:
    """Ingest Blockscout data and enqueue whale / trader alerts."""
    from src.posting import enqueue_alert

    enqueue = enqueue_fn or enqueue_alert
    report: dict[str, Any] = {"enabled": blockscout_enabled(cfg)}
    if not report["enabled"]:
        report["skipped"] = True
        return report

    ingest_report = maybe_ingest_blockscout(cfg)
    report["ingest"] = ingest_report

    bsc = _blockscout_cfg(cfg)
    max_whales = int(bsc.get("max_whales_per_run") or 3)
    max_traders = int(bsc.get("max_traders_per_run") or 2)
    instance = str((ingest_report or {}).get("instance") or bsc.get("instance") or "ethereum")

    whale_candidates: list[AlertTrigger] = []
    trader_candidates: list[AlertTrigger] = []

    if ingest_report and not ingest_report.get("skipped"):
        for r in ingest_report.get("results") or []:
            if bsc.get("post_whales", True):
                for raw in r.get("whales") or []:
                    whale_candidates.append(
                        blockscout_whale_to_alert(raw, cfg=cfg, instance=instance)
                    )
            if bsc.get("post_traders", True):
                trader_alert = trader_result_to_alert(r, cfg=cfg)
                if trader_alert:
                    trader_candidates.append(trader_alert)

    already_tx = _pending_whale_txs(conn)
    whales_queued = 0
    for alert in whale_candidates[:max_whales]:
        meta = parse_whale_meta(alert)
        tx = meta.get("tx") or ""
        if tx and tx in already_tx:
            continue
        enqueue(
            conn,
            cfg,
            alert,
            queue_reason=f"blockscout whale {alert.value:.2f} ETH ({tx[:18]}…)" if tx else "blockscout whale",
        )
        if tx:
            already_tx.add(tx)
        whales_queued += 1

    already_traders = _pending_trader_addresses(conn)
    traders_queued = 0
    for alert in trader_candidates[:max_traders]:
        meta = parse_trader_meta(alert)
        addr = (meta.get("address") or "").lower()
        if addr and addr in already_traders:
            continue
        enqueue(
            conn,
            cfg,
            alert,
            queue_reason=f"blockscout trader score {alert.value:.1f} ({addr[:12]}…)" if addr else "blockscout trader",
        )
        if addr:
            already_traders.add(addr)
        traders_queued += 1

    report["whales_queued"] = whales_queued
    report["whales_seen"] = len(whale_candidates)
    report["traders_queued"] = traders_queued
    report["traders_seen"] = len(trader_candidates)

    if whales_queued or traders_queued:
        print(
            f"[blockscout] queued whales={whales_queued} traders={traders_queued}"
        )
    elif ingest_report and not ingest_report.get("skipped"):
        print(
            f"[blockscout] ingest ok entries={ingest_report.get('entries')} "
            f"new_whales={ingest_report.get('whales_new', 0)} "
            f"traders={ingest_report.get('traders_seen', 0)} (none queued)"
        )
    return report