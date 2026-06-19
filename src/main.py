from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from src.alerts import check_alert
from src.config import ROOT, indicator_settings, load_config
from src.db import connect, hours_since_last_fetch, last_reading, record_alert, save_reading
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


def _use_cached(conn, key: str, interval: float) -> tuple[float, str] | None:
    elapsed = hours_since_last_fetch(conn, key)
    if elapsed is None or elapsed >= interval:
        return None
    row = last_reading(conn, key)
    if not row:
        return None
    return float(row["value"]), row["observed_at"]


def run(only: str | None = None, *, health_only: bool = False) -> int:
    load_dotenv(ROOT / ".env")
    cfg = load_config()

    health = check_api_health()
    print("API health:", ", ".join(f"{k}={v}" for k, v in health.items()))
    if health_only:
        ok = all(
            v == "ok" or (k == "coingecko" and "rate_limited" in v)
            for k, v in health.items()
        )
        return 0 if ok else 1

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
        interval = settings.get("fetch_interval_hours")
        cached = _use_cached(conn, key, interval) if interval else None

        if cached:
            value, observed_at = cached
            print(f"[{key}] {settings['name']}: {value:g} ({observed_at}) [cached, fetch every {interval:g}h]")
            continue

        health_msg = health.get(src_health, "")
        api_ok = health_msg == "ok" or (
            src_health == "coingecko" and "rate_limited" in health_msg
        )
        if not api_ok and settings["source"] == "coingecko" and last_reading(conn, key):
            row = last_reading(conn, key)
            value, observed_at = float(row["value"]), row["observed_at"]
            print(f"[{key}] {settings['name']}: {value:g} ({observed_at}) [cached, API: {health.get(src_health)}]")
            continue

        if not api_ok:
            print(f"[{key}] skipped: API unhealthy ({src_health})", file=sys.stderr)
            errors += 1
            continue

        try:
            value, observed_at = fetch_indicator(settings)
        except FetchError as e:
            if settings["source"] == "coingecko" and last_reading(conn, key):
                row = last_reading(conn, key)
                value, observed_at = float(row["value"]), row["observed_at"]
                print(f"[{key}] {settings['name']}: {value:g} ({observed_at}) [cached after fetch error]")
                continue
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