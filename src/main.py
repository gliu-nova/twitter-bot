from __future__ import annotations

import argparse
import sys

import requests
from dotenv import load_dotenv

from src.alerts import check_alert
from src.config import ROOT, indicator_settings, load_config
from src.db import connect, has_pending_alert, last_reading, save_reading
from src.market_hours import off_hours_equity_alert_action
from src.fetch import FetchError, fetch_indicator
from src.posting import enqueue_alert, process_posting_queue
from src.quality import QualityError, check_api_health, run_quality_checks
from src.scheduler import interval_label, is_fetch_due
from src.validate import run_validate


def _skip_reason_from_error(exc: Exception) -> str:
    msg = str(exc)
    if "cross-verify" in msg:
        return "cross-verify mismatch"
    if "stale" in msg:
        return "stale data"
    if isinstance(exc, FetchError):
        return "fetch error"
    if isinstance(exc, requests.RequestException):
        return "fetch timeout" if isinstance(exc, requests.Timeout) else "fetch error"
    return "quality check"


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
    if source in ("okx_funding", "okx_basis", "okx_liquidations"):
        return "okx"
    if source in ("hyperliquid_funding", "hyperliquid_basis"):
        return "hyperliquid"
    if source == "exchange_spread":
        return "kraken"
    return source


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
    skipped_indicators: list[tuple[str, str, str]] = []
    skipped_alerts = 0
    queued_keys: list[str] = []
    skipped_fetch = 0

    for key in keys:
        if key not in cfg["indicators"]:
            detail = "unknown indicator key in config"
            print(f"[{key}] skipped ({detail})", file=sys.stderr)
            skipped_indicators.append((key, "config error", detail))
            continue
        settings = indicator_settings(cfg, key)
        force_fetch = only is not None

        if not is_fetch_due(conn, settings, cfg, force=force_fetch):
            skipped_fetch += 1
            continue

        src_health = _source_for_health(settings["source"])
        health_msg = health.get(src_health, "")
        api_ok = health_msg == "ok"
        if not api_ok:
            detail = f"API unhealthy ({src_health}: {health_msg})"
            print(f"[{key}] skipped ({detail})", file=sys.stderr)
            skipped_indicators.append((key, "API unhealthy", detail))
            continue

        liq_long_usd: float | None = None
        liq_short_usd: float | None = None
        try:
            if settings.get("source") == "okx_liquidations":
                from src.crypto_metrics import fetch_liquidation_metric

                value, liq_long_usd, liq_short_usd, observed_at = fetch_liquidation_metric(settings)
            else:
                value, observed_at = fetch_indicator(settings)
        except (FetchError, requests.RequestException) as e:
            reason = _skip_reason_from_error(e)
            print(f"[{key}] skipped ({reason}): {e}", file=sys.stderr)
            skipped_indicators.append((key, reason, str(e)))
            continue

        try:
            run_quality_checks(settings, value, observed_at, for_alert=False)
        except QualityError as e:
            reason = _skip_reason_from_error(e)
            print(f"[{key}] skipped ({reason}): {e}", file=sys.stderr)
            skipped_indicators.append((key, reason, str(e)))
            continue
        except FetchError as e:
            reason = _skip_reason_from_error(e)
            print(f"[{key}] skipped ({reason}): {e}", file=sys.stderr)
            skipped_indicators.append((key, reason, str(e)))
            continue

        label = interval_label(settings, cfg)
        print(f"[{key}] {settings['name']}: {value:g} ({observed_at}) [poll every {label}]")

        if settings.get("source") == "okx_liquidations":
            from src.liquidations import check_liquidation_alert

            should_alert, alert = check_liquidation_alert(
                conn, settings, value, liq_long_usd or 0.0, liq_short_usd or 0.0
            )
            save_reading(
                conn,
                key,
                value,
                observed_at,
                liq_long_usd=liq_long_usd,
                liq_short_usd=liq_short_usd,
            )
        else:
            should_alert, alert = check_alert(conn, settings, value)
            save_reading(conn, key, value, observed_at)

        if not should_alert or not alert:
            continue

        if liq_long_usd is not None and liq_short_usd is not None:
            alert.liq_long_usd = liq_long_usd
            alert.liq_short_usd = liq_short_usd

        try:
            skip = run_quality_checks(settings, value, observed_at, for_alert=True)
            if skip:
                action = off_hours_equity_alert_action(settings, alert, skip)
                if action == "drop":
                    print(f"[{key}] alert suppressed: {skip}")
                    skipped_alerts += 1
                    continue
                if action == "queue":
                    if has_pending_alert(conn, key):
                        print(f"[{key}] off-hours alert deferred (already queued)")
                    else:
                        enqueue_alert(
                            conn,
                            cfg,
                            alert,
                            queue_reason="off-hours — deferred until US equity session (9:30–16:00 ET Mon–Fri)",
                        )
                        queued_keys.append(key)
                    continue
                print(f"[{key}] off-hours {alert.alert_tier} move — posting allowed")
        except QualityError as e:
            print(f"[{key}] alert blocked by quality: {e}", file=sys.stderr)
            skipped_alerts += 1
            continue

        enqueue_alert(
            conn,
            cfg,
            alert,
            queue_reason="threshold rule(s) fired — awaiting posting engine scoring and flush window",
        )
        queued_keys.append(key)

    if skipped_fetch and not only:
        print(f"Skipped {skipped_fetch} indicator(s) not due for fetch")

    if queued_keys:
        names = ", ".join(queued_keys)
        print(
            f"Queued {len(queued_keys)} alert(s) for posting decision: {names} "
            "(see [queue] lines above for trigger detail)"
        )
    posted = process_posting_queue(conn, cfg, force=force_post)
    if posted:
        print(f"Posted {posted} tweet(s)")

    conn.close()
    if skipped_alerts:
        print(f"Alerts suppressed: {skipped_alerts}")
    if skipped_indicators:
        print(
            f"Note: {len(skipped_indicators)} indicator(s) skipped this run "
            "(non-fatal — other indicators and posting still ran):"
        )
        for key, reason, detail in skipped_indicators:
            print(f"  • {key}: {reason} — {detail}")
    # Partial indicator failures should not fail the scheduler (exit 0).
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch indicators and tweet on threshold crossings")
    parser.add_argument("--indicator", help="Run a single indicator key from config.yaml")
    parser.add_argument("--health", action="store_true", help="Run API health checks only")
    parser.add_argument(
        "--force-post",
        action="store_true",
        help="Flush posting queue immediately (bypass buffer window)",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Smoke test: secrets, API health, Twitter credentials",
    )
    parser.add_argument(
        "--test-post",
        action="store_true",
        help="Post one test tweet to verify live posting (uses DRY_RUN from .env)",
    )
    args = parser.parse_args()
    if args.validate:
        load_dotenv(ROOT / ".env")
        raise SystemExit(run_validate())
    if args.test_post:
        load_dotenv(ROOT / ".env")
        from datetime import datetime, timezone

        from src.twitter_client import post_tweet

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        post_tweet(f"Market bot connectivity test — automated posting check ({ts})."[:280])
        raise SystemExit(0)
    raise SystemExit(run(args.indicator, health_only=args.health, force_post=args.force_post))


if __name__ == "__main__":
    main()