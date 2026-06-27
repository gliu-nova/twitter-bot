#!/usr/bin/env python3
"""One-shot market-memory historical backfill (all series indicators)."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from src.config import load_config
from src.market_memory_bridge import _resolve_data_dir, memory_enabled


def main() -> int:
    load_dotenv(ROOT / ".env")
    cfg = load_config()
    if not memory_enabled(cfg):
        print("market_memory disabled or data_dir missing", file=sys.stderr)
        return 1

    mm_cfg = cfg.get("market_memory") or {}
    since_raw = mm_cfg.get("since", "2021-01-01")
    data_dir = str(_resolve_data_dir(cfg))

    from market_memory.sync import sync_database

    report = sync_database(
        data_dir=data_dir,
        since=datetime.fromisoformat(str(since_raw)).replace(tzinfo=timezone.utc),
        interval_minutes=0,
        seed_verified_liquidations=bool(mm_cfg.get("seed_verified_liquidations", False)),
    )
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())