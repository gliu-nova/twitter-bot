"""Bridge twitter-bot to market-memory Etherscan on-chain ingestion.

Each bot poll can:
  1. Ingest watched addresses via market_memory.etherscan
  2. Convert new whale transfers into AlertTrigger rows for the posting engine

Requires market-memory with the etherscan package (local editable install or
a release that includes it) and ETHERSCAN_API_KEY in the environment.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import ROOT
from src.posting.models import AlertTrigger

WHALE_INDICATOR = "eth_whale"
WHALE_REASON_PREFIX = "whale_"
GAS_INDICATOR = "eth_gas"
VOLUME_INDICATOR = "eth_onchain_volume"
VOLUME_REASON_PREFIX = "volume_"

# Prefer bot-local watchlist; fall back to sibling market-memory checkout
_DEFAULT_MM = (ROOT.parent / "market-memory").resolve()
_DEFAULT_DB = _DEFAULT_MM / "data" / "etherscan.db"
_DEFAULT_WATCHLIST_LOCAL = ROOT / "data" / "etherscan_watchlist.yaml"
_DEFAULT_WATCHLIST_MM = _DEFAULT_MM / "data" / "watchlist.yaml"


def _default_watchlist() -> Path:
    if _DEFAULT_WATCHLIST_LOCAL.is_file():
        return _DEFAULT_WATCHLIST_LOCAL
    return _DEFAULT_WATCHLIST_MM


def etherscan_enabled(cfg: dict[str, Any]) -> bool:
    esc = _etherscan_cfg(cfg)
    if "enabled" in esc:
        return bool(esc["enabled"])
    # Auto-enable when API key is present
    return bool(os.environ.get("ETHERSCAN_API_KEY", "").strip())


def _etherscan_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    # Prefer top-level `etherscan:`; fall back to market_memory.etherscan
    if isinstance(cfg.get("etherscan"), dict):
        return cfg["etherscan"]
    mm = cfg.get("market_memory") or {}
    nested = mm.get("etherscan")
    return nested if isinstance(nested, dict) else {}


def _resolve_path(raw: str | Path | None, default: Path) -> Path:
    if not raw:
        return default
    path = Path(raw)
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    return path


def _load_mm_config(esc: dict[str, Any]):
    from market_memory.etherscan import load_etherscan_config

    db_path = _resolve_path(esc.get("db_path") or os.environ.get("ETHERSCAN_DB_PATH"), _DEFAULT_DB)
    chain = esc.get("chain") or esc.get("chain_id") or os.environ.get("ETHERSCAN_CHAIN", "ethereum")
    watchlist = esc.get("watchlist_path") or os.environ.get("ETHERSCAN_WATCHLIST_PATH")
    threshold = esc.get("whale_threshold_eth") or esc.get("large_transfer_eth")
    rate = esc.get("rate_limit_delay")

    return load_etherscan_config(
        db_path=db_path,
        chain_id=chain,
        large_transfer_eth=float(threshold) if threshold is not None else None,
        rate_limit_delay=float(rate) if rate is not None else None,
        watchlist_path=watchlist,
        whale_alerts=True,
    )


def maybe_ingest_etherscan(cfg: dict[str, Any]) -> dict[str, Any] | None:
    """Run Etherscan ingest for configured addresses. Returns a small report or None."""
    if not etherscan_enabled(cfg):
        return None
    esc = _etherscan_cfg(cfg)
    if not esc.get("ingest_on_poll", True):
        return {"skipped": True, "reason": "ingest_on_poll=false"}

    try:
        from market_memory.etherscan import run_ingest, run_ingest_entries
        from market_memory.etherscan.watchlist import load_watchlist, merge_cli_addresses
    except ImportError as exc:
        print(f"[etherscan] market_memory.etherscan not available: {exc}")
        return {"skipped": True, "reason": f"import_error:{exc}"}

    try:
        mm_cfg = _load_mm_config(esc)
        mm_cfg.whale_alerts_enabled = True
    except ValueError as exc:
        print(f"[etherscan] config error: {exc}")
        return {"skipped": True, "reason": str(exc)}

    mode = str(esc.get("mode") or "recent")
    addresses = list(esc.get("addresses") or [])
    wl_path = esc.get("watchlist_path") or os.environ.get("ETHERSCAN_WATCHLIST_PATH")
    if not wl_path:
        default_wl = _default_watchlist()
        if default_wl.is_file():
            wl_path = str(default_wl)

    watchlist = None
    if wl_path:
        try:
            watchlist = load_watchlist(
                _resolve_path(wl_path, _default_watchlist()),
                default_chain=mm_cfg.chain_id,
            )
        except Exception as exc:
            print(f"[etherscan] watchlist load failed: {exc}")
            return {"skipped": True, "reason": f"watchlist:{exc}"}

    entries = merge_cli_addresses(
        [str(a) for a in addresses],
        watchlist,
        chain_id=mm_cfg.chain_id,
        chain_name=mm_cfg.chain_name,
    )

    report: dict[str, Any] = {
        "skipped": False,
        "mode": mode,
        "entries": len(entries),
        "db_path": str(mm_cfg.db_path),
        "results": [],
        "whales_new": 0,
    }

    try:
        if not entries:
            # Gas-only snapshot keeps DB warm when no addresses configured
            result = run_ingest(
                address=None,
                mode="gas",
                include_gas=True,
                whale_alerts=False,
                config=mm_cfg,
            )
            report["results"].append(result.to_dict())
        else:
            results = run_ingest_entries(
                entries,
                mode=mode,
                include_gas=True,
                whale_alerts=True,
                config=mm_cfg,
            )
            for r in results:
                report["results"].append(r.to_dict())
                report["whales_new"] += len(r.whale_alerts)
    except Exception as exc:
        print(f"[etherscan] ingest failed: {exc}")
        report["error"] = str(exc)
        return report

    return report


def _tier_for_eth(value_eth: float, esc: dict[str, Any], settings: dict[str, Any]) -> str:
    emergency = float(esc.get("emergency_eth") or settings.get("emergency_alert") or 2000)
    major = float(esc.get("major_eth") or settings.get("major_alert") or 500)
    if value_eth >= emergency:
        return "emergency"
    if value_eth >= major:
        return "major"
    return "normal"


def _short_addr(addr: str | None, n: int = 4) -> str:
    if not addr:
        return "?"
    if len(addr) < 12:
        return addr
    return f"{addr[: n + 2]}…{addr[-n:]}"


def whale_dict_to_alert(
    raw: dict[str, Any],
    *,
    cfg: dict[str, Any],
    source: str = "etherscan",
) -> AlertTrigger:
    """Convert a market_memory whale alert dict into an AlertTrigger."""
    from src.config import indicator_settings

    esc = _etherscan_cfg(cfg)
    settings = indicator_settings(cfg, WHALE_INDICATOR)
    value = float(raw.get("value_eth") or 0)
    tier = _tier_for_eth(value, esc, settings)
    chain_name = str(raw.get("chain_name") or "ethereum")
    label = raw.get("label")
    watched = raw.get("watched_address")
    tx_hash = str(raw.get("tx_hash") or "")
    from_a = str(raw.get("from_address") or "")
    to_a = str(raw.get("to_address") or "")
    url = str(raw.get("explorer_url") or "")
    ts = raw.get("time_stamp")
    if ts is not None:
        timestamp = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    else:
        timestamp = datetime.now(timezone.utc)

    reasons = [
        f"whale transfer >= threshold on {chain_name}",
        f"{WHALE_REASON_PREFIX}tx:{tx_hash}",
        f"{WHALE_REASON_PREFIX}from:{from_a}",
        f"{WHALE_REASON_PREFIX}to:{to_a}",
        f"{WHALE_REASON_PREFIX}chain:{chain_name}",
        f"{WHALE_REASON_PREFIX}url:{url}",
    ]
    if label:
        reasons.append(f"{WHALE_REASON_PREFIX}label:{label}")
    if watched:
        reasons.append(f"{WHALE_REASON_PREFIX}watched:{watched}")

    name = settings.get("name") or "ETH whale transfer"
    if label:
        name = f"{name} ({label})"

    reasons = [r for r in reasons if not r.startswith(f"{WHALE_REASON_PREFIX}source:")]
    reasons.append(f"{WHALE_REASON_PREFIX}source:{source}")

    return AlertTrigger(
        indicator=WHALE_INDICATOR,
        name=name,
        value=value,
        prev_value=None,
        reasons=reasons,
        rule_types=["whale_transfer"],
        themes=list(settings.get("themes") or ["crypto", "risk_on"]),
        category=str(settings.get("category") or "crypto"),
        is_macro=False,
        timestamp=timestamp,
        magnitude_pct=0.0,
        magnitude_abs=value,
        alert_unit="absolute",
        alert_tier=tier,
        standalone_major=bool(settings.get("standalone_major", True)),
    )


def parse_whale_meta(alert: AlertTrigger) -> dict[str, str]:
    """Extract whale_* reason tokens into a dict."""
    meta: dict[str, str] = {}
    for reason in alert.reasons:
        if not reason.startswith(WHALE_REASON_PREFIX):
            continue
        body = reason[len(WHALE_REASON_PREFIX) :]
        key, _, val = body.partition(":")
        if key:
            meta[key] = val
    return meta


def collect_whale_alerts(cfg: dict[str, Any]) -> list[AlertTrigger]:
    """Load newly fired (or unalerted) whales from the SQLite store as triggers.

    Prefer alerts produced during the latest ingest (already marked). Also scans
    for any large transfers not yet recorded when `rescan_db` is true.
    """
    if not etherscan_enabled(cfg):
        return []
    esc = _etherscan_cfg(cfg)
    if not esc.get("post_whales", True):
        return []

    try:
        from market_memory.etherscan import EtherscanDB, check_whale_alerts
    except ImportError as exc:
        print(f"[etherscan] cannot collect whales: {exc}")
        return []

    try:
        mm_cfg = _load_mm_config(esc)
    except ValueError as exc:
        print(f"[etherscan] config error: {exc}")
        return []

    if not Path(mm_cfg.db_path).is_file():
        print(f"[etherscan] DB missing at {mm_cfg.db_path} — run ingest first")
        return []

    threshold = float(esc.get("whale_threshold_eth") or mm_cfg.large_transfer_eth)
    max_n = int(esc.get("max_whales_per_run") or 5)
    alerts: list[AlertTrigger] = []

    with EtherscanDB(mm_cfg.db_path) as db:
        # New whales are recorded during ingest; rescan only if requested
        if esc.get("rescan_db", False):
            fresh = check_whale_alerts(
                db,
                threshold_eth=threshold,
                only_unalerted=True,
                mark_alerted=True,
                limit=max_n * 5,
            )
            for w in fresh[:max_n]:
                alerts.append(whale_dict_to_alert(w.to_dict(), cfg=cfg))
        else:
            # Pull recently recorded whale_alerts (last few hours) that we can post
            # Idempotency for *posting* is handled by bot alert_log / queue cooldowns.
            rows = db.fetch_whale_alerts(limit=max_n * 3)
            for row in rows:
                raw = {
                    "tx_hash": row["tx_hash"],
                    "chain_id": row["chain_id"],
                    "chain_name": "ethereum",  # refined below if possible
                    "value_eth": row["value_eth"],
                    "from_address": row["from_address"],
                    "to_address": row["to_address"],
                    "watched_address": row["watched_address"],
                    "label": row["label"],
                    "time_stamp": row["time_stamp"],
                    "explorer_url": row["explorer_url"] or "",
                }
                try:
                    from market_memory.etherscan.chains import resolve_chain

                    raw["chain_name"] = resolve_chain(int(row["chain_id"])).name
                except Exception:
                    pass
                alerts.append(whale_dict_to_alert(raw, cfg=cfg))
                if len(alerts) >= max_n:
                    break

    return alerts[:max_n]


def _fast_gas_from_ingest(ingest_report: dict[str, Any] | None) -> float | None:
    if not ingest_report:
        return None
    for r in ingest_report.get("results") or []:
        gas = r.get("gas_oracle") or {}
        for key in ("FastGasPrice", "fast_gas_price"):
            raw = gas.get(key)
            if raw is not None and raw != "":
                try:
                    return float(raw)
                except (TypeError, ValueError):
                    continue
    return None


def _gas_from_db(mm_cfg: Any) -> float | None:
    try:
        from market_memory.etherscan import EtherscanDB
    except ImportError:
        return None
    if not Path(mm_cfg.db_path).is_file():
        return None
    with EtherscanDB(mm_cfg.db_path) as db:
        row = db.fetch_latest_gas(chain_id=mm_cfg.chain_id)
        if row and row["fast_gas_price"] is not None:
            return float(row["fast_gas_price"])
    return None


def gas_reading_to_alert(
    value: float,
    prev: float | None,
    *,
    cfg: dict[str, Any],
    chain_name: str,
) -> AlertTrigger | None:
    """Return gas spike alert when move crosses configured thresholds."""
    from src.alerts import _detect_tier, _pct_change
    from src.config import indicator_settings

    esc = _etherscan_cfg(cfg)
    settings = indicator_settings(cfg, GAS_INDICATOR)
    if prev is None:
        return None

    pct = abs(_pct_change(prev, value))
    if pct == float("inf"):
        pct = 100.0

    min_pct = float(settings.get("normal_alert") or esc.get("gas_spike_pct") or 25)
    crosses = settings.get("crosses_above_gwei") or esc.get("gas_crosses_above_gwei")
    fired = pct >= min_pct
    rule_types: list[str] = []
    reasons: list[str] = []

    if fired:
        direction = "up" if value > prev else "down"
        rule_types.append("percent_change")
        reasons.append(f"fast gas {direction} {pct:.1f}% on {chain_name}")
    if crosses is not None and prev < float(crosses) <= value:
        fired = True
        rule_types.append("crosses_above")
        reasons.append(f"fast gas crossed {float(crosses):.0f} gwei on {chain_name}")

    if not fired:
        return None

    tier = _detect_tier(settings, prev, value)
    return AlertTrigger(
        indicator=GAS_INDICATOR,
        name=settings.get("name") or "Ethereum gas (fast)",
        value=value,
        prev_value=prev,
        reasons=reasons,
        rule_types=rule_types or ["percent_change"],
        themes=list(settings.get("themes") or ["crypto"]),
        category=str(settings.get("category") or "crypto"),
        is_macro=False,
        timestamp=datetime.now(timezone.utc),
        magnitude_pct=pct,
        magnitude_abs=abs(value - prev),
        alert_unit="percent",
        alert_tier=tier,
        standalone_major=bool(settings.get("standalone_major", False)),
    )


def volume_spike_to_alert(
    spike: dict[str, Any],
    *,
    cfg: dict[str, Any],
    address: str | None = None,
    label: str | None = None,
) -> AlertTrigger:
    from src.config import indicator_settings

    settings = indicator_settings(cfg, VOLUME_INDICATOR)
    zscore = float(spike.get("zscore") or 0)
    chain_name = str(spike.get("chain_name") or "ethereum")
    bucket = int(spike.get("bucket_start") or 0)
    volume = float(spike.get("volume_eth") or 0)
    tx_count = int(spike.get("tx_count") or 0)

    reasons = [
        f"hourly ETH volume z-score {zscore:.1f} on {chain_name}",
        f"{VOLUME_REASON_PREFIX}bucket:{bucket}",
        f"{VOLUME_REASON_PREFIX}zscore:{zscore:.2f}",
        f"{VOLUME_REASON_PREFIX}volume_eth:{volume:.4f}",
        f"{VOLUME_REASON_PREFIX}tx_count:{tx_count}",
    ]
    if address:
        reasons.append(f"{VOLUME_REASON_PREFIX}address:{address.lower()}")
    if label:
        reasons.append(f"{VOLUME_REASON_PREFIX}label:{label}")

    tier = "normal"
    if zscore >= float(settings.get("emergency_alert") or 4.0):
        tier = "emergency"
    elif zscore >= float(settings.get("major_alert") or 3.0):
        tier = "major"

    name = settings.get("name") or "ETH on-chain volume spike"
    if label:
        name = f"{name} ({label})"

    ts = datetime.fromtimestamp(bucket, tz=timezone.utc) if bucket else datetime.now(timezone.utc)
    return AlertTrigger(
        indicator=VOLUME_INDICATOR,
        name=name,
        value=zscore,
        prev_value=float(spike.get("mean_volume") or 0) or None,
        reasons=reasons,
        rule_types=["volume_spike"],
        themes=list(settings.get("themes") or ["crypto", "risk_on"]),
        category=str(settings.get("category") or "crypto"),
        is_macro=False,
        timestamp=ts,
        magnitude_pct=0.0,
        magnitude_abs=zscore,
        alert_unit="absolute",
        alert_tier=tier,
        standalone_major=bool(settings.get("standalone_major", False)),
    )


def collect_volume_spike_alerts(cfg: dict[str, Any]) -> list[AlertTrigger]:
    """Detect statistical volume outliers for watched addresses."""
    if not etherscan_enabled(cfg):
        return []
    esc = _etherscan_cfg(cfg)
    if not esc.get("post_volume_spikes", True):
        return []

    try:
        from market_memory.etherscan import EtherscanDB, detect_volume_spikes
        from market_memory.etherscan.watchlist import load_watchlist, merge_cli_addresses
    except ImportError as exc:
        print(f"[etherscan] volume spikes unavailable: {exc}")
        return []

    try:
        mm_cfg = _load_mm_config(esc)
    except ValueError as exc:
        print(f"[etherscan] config error: {exc}")
        return []

    if not Path(mm_cfg.db_path).is_file():
        return []

    z_thresh = float(esc.get("volume_spike_zscore") or mm_cfg.volume_spike_zscore)
    max_n = int(esc.get("max_volume_spikes_per_run") or 2)
    since_hours = int(esc.get("volume_spike_since_hours") or 48)
    since_ts = int(datetime.now(timezone.utc).timestamp()) - since_hours * 3600

    wl_path = esc.get("watchlist_path") or os.environ.get("ETHERSCAN_WATCHLIST_PATH")
    if not wl_path:
        default_wl = _default_watchlist()
        if default_wl.is_file():
            wl_path = str(default_wl)

    addresses: list[tuple[str, str | None]] = [("", None)]
    if wl_path:
        try:
            wl = load_watchlist(
                _resolve_path(wl_path, _default_watchlist()),
                default_chain=mm_cfg.chain_id,
            )
            entries = merge_cli_addresses([], wl, chain_id=mm_cfg.chain_id, chain_name=mm_cfg.chain_name)
            if entries:
                addresses = [(e.address, e.label) for e in entries]
        except Exception as exc:
            print(f"[etherscan] volume watchlist failed: {exc}")

    alerts: list[AlertTrigger] = []
    with EtherscanDB(mm_cfg.db_path) as db:
        for addr, label in addresses:
            spikes = detect_volume_spikes(
                db,
                address=addr or None,
                since_ts=since_ts,
                chain_id=mm_cfg.chain_id,
                zscore_threshold=z_thresh,
            )
            for spike in spikes[:max_n]:
                alerts.append(
                    volume_spike_to_alert(
                        {**spike.to_dict(), "chain_name": mm_cfg.chain_name},
                        cfg=cfg,
                        address=addr or None,
                        label=label,
                    )
                )
                if len(alerts) >= max_n:
                    return alerts[:max_n]
    return alerts[:max_n]


def _pending_volume_buckets(conn: Any) -> set[str]:
    rows = conn.execute(
        """
        SELECT reasons FROM pending_alerts
        WHERE indicator = ? AND processed = 0
        """,
        (VOLUME_INDICATOR,),
    ).fetchall()
    found: set[str] = set()
    for row in rows:
        for reason in (row["reasons"] or "").split("|"):
            if reason.startswith(f"{VOLUME_REASON_PREFIX}bucket:"):
                found.add(reason.split(":", 1)[1])
    return found


def _pending_whale_txs(conn: Any) -> set[str]:
    """Tx hashes already sitting in pending_alerts for eth_whale."""
    rows = conn.execute(
        """
        SELECT reasons FROM pending_alerts
        WHERE indicator = ? AND processed = 0
        """,
        (WHALE_INDICATOR,),
    ).fetchall()
    found: set[str] = set()
    for row in rows:
        reasons = (row["reasons"] or "").split("|")
        for reason in reasons:
            if reason.startswith(f"{WHALE_REASON_PREFIX}tx:"):
                found.add(reason.split(":", 1)[1])
    return found


def process_etherscan_for_bot(
    conn: Any,
    cfg: dict[str, Any],
    *,
    enqueue_fn: Any | None = None,
) -> dict[str, Any]:
    """Ingest on-chain data and enqueue whale alerts. Returns summary report."""
    from src.posting import enqueue_alert

    enqueue = enqueue_fn or enqueue_alert
    report: dict[str, Any] = {"enabled": etherscan_enabled(cfg)}
    if not report["enabled"]:
        report["skipped"] = True
        return report

    ingest_report = maybe_ingest_etherscan(cfg)
    report["ingest"] = ingest_report

    # Prefer whales returned on this ingest pass (true "new" set)
    new_from_ingest: list[AlertTrigger] = []
    if ingest_report and not ingest_report.get("skipped"):
        for r in ingest_report.get("results") or []:
            for raw in r.get("whale_alerts") or []:
                new_from_ingest.append(whale_dict_to_alert(raw, cfg=cfg))

    esc = _etherscan_cfg(cfg)
    max_n = int(esc.get("max_whales_per_run") or 5)
    candidates = new_from_ingest[:max_n]
    if not candidates and esc.get("rescan_db", False):
        candidates = collect_whale_alerts(cfg)

    already = _pending_whale_txs(conn)
    queued = 0
    for alert in candidates:
        meta = parse_whale_meta(alert)
        tx = meta.get("tx") or ""
        if tx and tx in already:
            continue
        enqueue(
            conn,
            cfg,
            alert,
            queue_reason=f"on-chain whale transfer {alert.value:.2f} ETH ({tx[:18]}…)",
        )
        if tx:
            already.add(tx)
        queued += 1

    report["whales_queued"] = queued
    report["whales_seen"] = len(candidates)

    gas_queued = 0
    if esc.get("post_gas", True):
        try:
            mm_cfg = _load_mm_config(esc)
        except ValueError:
            mm_cfg = None
        gas_val = _fast_gas_from_ingest(ingest_report)
        if gas_val is None and mm_cfg is not None:
            gas_val = _gas_from_db(mm_cfg)
        if gas_val is not None:
            from src.db import last_reading, save_reading

            prev_row = last_reading(conn, GAS_INDICATOR)
            prev = float(prev_row["value"]) if prev_row else None
            chain_name = mm_cfg.chain_name if mm_cfg else "ethereum"
            gas_alert = gas_reading_to_alert(gas_val, prev, cfg=cfg, chain_name=chain_name)
            save_reading(conn, GAS_INDICATOR, gas_val, datetime.now(timezone.utc).isoformat())
            if gas_alert:
                enqueue(
                    conn,
                    cfg,
                    gas_alert,
                    queue_reason=f"ETH fast gas {gas_val:.1f} gwei ({gas_alert.alert_tier})",
                )
                gas_queued = 1
    report["gas_queued"] = gas_queued

    volume_queued = 0
    if esc.get("post_volume_spikes", True):
        already_buckets = _pending_volume_buckets(conn)
        for alert in collect_volume_spike_alerts(cfg):
            bucket = ""
            for reason in alert.reasons:
                if reason.startswith(f"{VOLUME_REASON_PREFIX}bucket:"):
                    bucket = reason.split(":", 1)[1]
                    break
            if bucket and bucket in already_buckets:
                continue
            enqueue(
                conn,
                cfg,
                alert,
                queue_reason=f"on-chain volume z={alert.value:.1f}",
            )
            if bucket:
                already_buckets.add(bucket)
            volume_queued += 1
    report["volume_spikes_queued"] = volume_queued

    if queued or gas_queued or volume_queued:
        print(
            f"[etherscan] queued whales={queued} gas={gas_queued} "
            f"volume_spikes={volume_queued}"
        )
    elif ingest_report and not ingest_report.get("skipped"):
        print(
            f"[etherscan] ingest ok entries={ingest_report.get('entries')} "
            f"new_whales={ingest_report.get('whales_new', 0)} (none queued)"
        )
    return report
