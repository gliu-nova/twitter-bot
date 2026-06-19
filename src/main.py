from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from src.alerts import check_alert
from src.config import ROOT, indicator_settings, load_config
from src.db import connect, record_alert, save_reading
from src.fetch import FetchError, fetch_indicator
from src.quality import QualityError, check_api_health, run_quality_checks
from src.twitter_client import post_tweet


def _source_for_health(source: str) -> str:
    if source in ("fred", "fred_cpi_yoy"):
        return "fred"
    if source == "yahoo":
        return "yahoo"
    if source == "coingecko":
        return "coingecko"
    if source == "fear_greed":
        return "fear_greed"
    return source


def run(only: str | None = None, *, health_only: bool = False) -> int:
    load_dotenv(ROOT / ".env")
    cfg = load_config()

    health = check_api_health()
    print("API health:", ", ".join(f"{k}={v}" for k, v in health.items()))
    if health_only:
        return 0 if all(v == "ok" for v in health.values()) else 1

    conn = connect()
    keys = [only] if only else list(cfg["indicators"].keys())
    errors = 0
    skipped_alerts = 0

    for key in keys:
        if key not in cfg["indicators"]:
            print(f"Unknown indicator: {key}", file=sys.stderr)
            errors += 1
            continue
        settings = indicator_settings(cfg, key)
        src_health = _source_for_health(settings["source"])
        if health.get(src_health) != "ok":
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

        should_alert, message = check_alert(conn, settings, value)
        save_reading(conn, key, value, observed_at)

        if not should_alert or not message:
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

        post_tweet(message)
        record_alert(conn, key, value)

    conn.close()
    if skipped_alerts:
        print(f"Alerts suppressed: {skipped_alerts}")
    return 1 if errors else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch indicators and tweet on threshold crossings")
    parser.add_argument("--indicator", help="Run a single indicator key from config.yaml")
    parser.add_argument("--health", action="store_true", help="Run API health checks only")
    args = parser.parse_args()
    raise SystemExit(run(args.indicator, health_only=args.health))


if __name__ == "__main__":
    main()