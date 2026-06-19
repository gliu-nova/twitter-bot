from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from src.alerts import check_alert
from src.config import ROOT, indicator_settings, load_config
from src.db import connect, hours_since_last_fetch, last_reading, save_reading
from src.fetch import FetchError, fetch_indicator
from src.posting import enqueue_alert, process_posting_queue
from src.quality import QualityError, check_api_health, run_quality_checks


def _source_for_health(source: str) -> str:
    if source in ("fred", "fred_cpi_yoy"):
        return "fred"
    if source == "yahoo":
        return "yahoo"
    if source in ("binance", "kraken"):
        return source
    if source == "coingecko":
        return "coingecko"
    if source == "fear_greed":
        return "fear_greed"
    return source


def _use_cached(conn, key: str, interval: float) -> tuple[float, str] | None:
    elapsed = hours_since_last_fetch(conn, key)
    if elapsed is None or elapsed >= interval:
        return None
    row = last_reading(conn, key)
    if not row:
        return None
    return float(row["value"]), row["observed_at"]


def run(only: str | None = None, *, health_only: bool = False, force_post: bool = False) -> int:
    load_dotenv(ROOT / ".env")
    cfg = load_config()

    health = check_api_health()
    print("API health:", ", ".join(f"{k}={v}" for k, v in health.items()))
    if health_only:
        ok = all(v == "ok" for k, v in health.items())
        return 0 if ok else 1

    conn = connect()
    keys = [only] if only else list(cfg["indicators"].keys())
    errors = 0
    skipped_alerts = 0
    queued = 0

    for key in keys:
        if key not in cfg["indicators"]:
            print(f"Unknown indicator: {key}", file=sys.stderr)
            errors += 1
            continue
        settings = indicator_settings(cfg, key)
        src_health = _source_for_health(settings["source"])
        interval = settings.get("fetch_interval_hours")
        cached = _use_cached(conn, key, interval) if interval else None

        if cached:
            value, observed_at = cached
            print(f"[{key}] {settings['name']}: {value:g} ({observed_at}) [cached, fetch every {interval:g}h]")
            continue

        health_msg = health.get(src_health, "")
        api_ok = health_msg == "ok"
        if not api_ok:
            print(f"[{key}] skipped: API unhealthy ({src_health})", file=sys.stderr)
            errors += 1
            continue

        try:
            value, observed_at = fetch_indicator(settings)
        except FetchError as e:
            print(f"[{key}] fetch failed: {e}", file=sys.stderr)
            errors += 1
            continue

        try:
            run_quality_checks(settings, value, observed_at, for_alert=False)
        except QualityError as e:
            print(f"[{key}] quality rejected: {e}", file=sys.stderr)
            errors += 1
            continue

        print(f"[{key}] {settings['name']}: {value:g} ({observed_at})")

        should_alert, alert = check_alert(conn, settings, value)
        save_reading(conn, key, value, observed_at)

        if not should_alert or not alert:
            continue

        try:
            skip = run_quality_checks(settings, value, observed_at, for_alert=True)
            if skip:
                print(f"[{key}] alert suppressed: {skip}")
                skipped_alerts += 1
                continue
        except QualityError as e:
            print(f"[{key}] alert blocked by quality: {e}", file=sys.stderr)
            skipped_alerts += 1
            continue

        enqueue_alert(conn, cfg, alert)
        queued += 1

    if queued:
        print(f"Queued {queued} alert(s) for posting decision")
    posted = process_posting_queue(conn, cfg, force=force_post)
    if posted:
        print(f"Posted {posted} tweet(s)")

    conn.close()
    if skipped_alerts:
        print(f"Alerts suppressed: {skipped_alerts}")
    return 1 if errors else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch indicators and tweet on threshold crossings")
    parser.add_argument("--indicator", help="Run a single indicator key from config.yaml")
    parser.add_argument("--health", action="store_true", help="Run API health checks only")
    parser.add_argument(
        "--force-post",
        action="store_true",
        help="Flush posting queue immediately (bypass buffer window)",
    )
    args = parser.parse_args()
    raise SystemExit(run(args.indicator, health_only=args.health, force_post=args.force_post))


if __name__ == "__main__":
    main()