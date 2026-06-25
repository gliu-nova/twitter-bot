"""Push run summary to ops-hub for the unified status dashboard."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import requests


def push_ops_heartbeat(
    *,
    health: dict[str, str],
    sync_report: dict[str, Any] | None,
    posted_tweets: int,
    skipped_indicators: int,
    trigger: str = "unknown",
) -> None:
    base = os.environ.get("OPS_HUB_URL", "").strip().rstrip("/")
    if not base:
        return

    bad_apis = [k for k, v in health.items() if v != "ok"]
    status = "ok" if not bad_apis else "degraded"

    mm_summary: dict[str, Any] = {"enabled": sync_report is not None}
    if sync_report:
        db_stats = sync_report.get("db_stats") or {}
        mm_summary.update(
            {
                "skipped": bool(sync_report.get("skipped")),
                "ingested": sync_report.get("ingested"),
                "liquidations_mode": (sync_report.get("sources") or {}).get("liquidations_mode"),
                "total_events": db_stats.get("total_events"),
                "warnings": sync_report.get("warnings") or [],
            }
        )

    payload = {
        "service_id": "twitter-bot",
        "status": status,
        "reported_at": datetime.now(timezone.utc).isoformat(),
        "summary": (
            f"posted={posted_tweets}, skipped_indicators={skipped_indicators}, "
            f"apis_unhealthy={len(bad_apis)}"
        ),
        "details": {
            "trigger": trigger,
            "api_health": health,
            "posted_tweets": posted_tweets,
            "skipped_indicators": skipped_indicators,
            "market_memory": mm_summary,
        },
        "links": {
            "actions": "https://github.com/gliu-nova/twitter-bot/actions",
        },
    }

    headers = {"content-type": "application/json"}
    token = os.environ.get("OPS_HEARTBEAT_SECRET", "").strip()
    if token:
        headers["authorization"] = f"Bearer {token}"

    try:
        resp = requests.post(f"{base}/heartbeat", json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        print(f"[ops-hub] heartbeat ok ({status})")
    except Exception as exc:
        print(f"[ops-hub] heartbeat skipped: {exc}", file=__import__("sys").stderr)