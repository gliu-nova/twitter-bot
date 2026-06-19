from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    with path.open() as f:
        return yaml.safe_load(f)


def _normalize_rules(ind: dict[str, Any]) -> list[dict[str, Any]]:
    if ind.get("rules"):
        return ind["rules"]
    rules: list[dict[str, Any]] = []
    if ind.get("threshold_percent") is not None:
        rules.append({"type": "percent_change", "threshold": ind["threshold_percent"]})
    if ind.get("threshold_low") is not None:
        rules.append({"type": "crosses_below", "value": ind["threshold_low"]})
    if ind.get("threshold_high") is not None:
        rules.append({"type": "crosses_above", "value": ind["threshold_high"]})
    return rules


def _source_fetch_defaults(source: str) -> dict[str, Any]:
    if source == "coingecko":
        return {"fetch_interval_hours": 24}
    return {}


def _source_quality_defaults(source: str) -> dict[str, Any]:
    if source in ("coingecko", "fear_greed"):
        return {"schedule": "crypto_24_7", "max_stale_hours": 36}
    if source == "yahoo":
        return {"schedule": "us_equity", "max_stale_hours": 48}
    return {"schedule": "macro", "max_stale_hours": 720}


def indicator_settings(cfg: dict[str, Any], key: str) -> dict[str, Any]:
    defaults = {k: v for k, v in cfg.get("defaults", {}).items() if k != "rules"}
    ind = cfg["indicators"][key]
    merged = {**defaults, **ind, "key": key}
    merged["rules"] = _normalize_rules(merged)
    dq = cfg.get("defaults", {}).get("quality") or {}
    iq = ind.get("quality") or {}
    merged["quality"] = {**_source_quality_defaults(merged["source"]), **dq, **iq}
    merged.update({k: v for k, v in _source_fetch_defaults(merged["source"]).items() if k not in ind})
    return merged