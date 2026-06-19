from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from src.alerts import check_alert
from src.config import ROOT, indicator_settings, load_config
from src.db import connect, record_alert, save_reading
from src.fetch import FetchError, fetch_indicator
from src.twitter_client import post_tweet


def run(only: str | None = None) -> int:
    load_dotenv(ROOT / ".env")
    cfg = load_config()
    conn = connect()
    keys = [only] if only else list(cfg["indicators"].keys())
    errors = 0

    for key in keys:
        if key not in cfg["indicators"]:
            print(f"Unknown indicator: {key}", file=sys.stderr)
            errors += 1
            continue
        settings = indicator_settings(cfg, key)
        try:
            value, observed_at = fetch_indicator(settings)
        except FetchError as e:
            print(f"[{key}] fetch failed: {e}", file=sys.stderr)
            errors += 1
            continue

        should_alert, message = check_alert(conn, settings, value)
        save_reading(conn, key, value, observed_at)
        print(f"[{key}] {settings['name']}: {value:g} ({observed_at})")

        if should_alert and message:
            post_tweet(message)
            record_alert(conn, key, value)

    conn.close()
    return 1 if errors else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch indicators and tweet on threshold crossings")
    parser.add_argument("--indicator", help="Run a single indicator key from config.yaml")
    args = parser.parse_args()
    raise SystemExit(run(args.indicator))


if __name__ == "__main__":
    main()