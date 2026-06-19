from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    with path.open() as f:
        return yaml.safe_load(f)


def indicator_settings(cfg: dict[str, Any], key: str) -> dict[str, Any]:
    defaults = cfg.get("defaults", {})
    ind = cfg["indicators"][key]
    merged = {**defaults, **ind}
    merged["key"] = key
    return merged