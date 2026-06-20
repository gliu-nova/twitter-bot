from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import tweepy

from src.config import ROOT
from src.quality import check_api_health

LAST_TWITTER_VALIDATE = ROOT / "data" / ".last_twitter_get_me"

REQUIRED_SECRETS = (
    "FRED_API_KEY",
    "TWITTER_API_KEY",
    "TWITTER_API_SECRET",
    "TWITTER_ACCESS_TOKEN",
    "TWITTER_ACCESS_TOKEN_SECRET",
)


def check_secrets() -> list[str]:
    missing = [k for k in REQUIRED_SECRETS if not os.getenv(k, "").strip()]
    return missing


def verify_twitter() -> str:
    client = tweepy.Client(
        consumer_key=os.environ["TWITTER_API_KEY"],
        consumer_secret=os.environ["TWITTER_API_SECRET"],
        access_token=os.environ["TWITTER_ACCESS_TOKEN"],
        access_token_secret=os.environ["TWITTER_ACCESS_TOKEN_SECRET"],
    )
    me = client.get_me(user_fields=["username"])
    if not me or not me.data:
        raise RuntimeError("Twitter get_me returned no data")
    LAST_TWITTER_VALIDATE.parent.mkdir(parents=True, exist_ok=True)
    LAST_TWITTER_VALIDATE.write_text(datetime.now(timezone.utc).isoformat())
    return str(me.data.username)


def _twitter_check_allowed() -> tuple[bool, str]:
    if os.getenv("CHECK_TWITTER", "1") == "0":
        return False, "CHECK_TWITTER=0"

    min_hours = float(os.getenv("TWITTER_VALIDATE_MIN_HOURS", "24"))
    if min_hours <= 0 or not LAST_TWITTER_VALIDATE.exists():
        return True, ""

    try:
        last = datetime.fromisoformat(LAST_TWITTER_VALIDATE.read_text().strip())
    except ValueError:
        return True, ""

    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    hours = (datetime.now(timezone.utc) - last).total_seconds() / 3600
    if hours < min_hours:
        return False, f"rate-limited (last get_me {hours:.1f}h ago, min interval {min_hours}h)"
    return True, ""


def run_validate(*, check_twitter: bool = True) -> int:
    print("=== Smoke validation ===")

    missing = check_secrets()
    if missing:
        print("FAIL: missing secrets:", ", ".join(missing))
        return 1
    print("OK: all required secrets present")

    health = check_api_health()
    print("API health:", ", ".join(f"{k}={v}" for k, v in health.items()))
    bad = [k for k, v in health.items() if v != "ok"]
    if bad:
        print("FAIL: unhealthy APIs:", ", ".join(bad))
        return 1
    print("OK: all data APIs healthy")

    if check_twitter:
        allowed, reason = _twitter_check_allowed()
        if not allowed:
            print(f"SKIP: Twitter get_me ({reason})")
        else:
            try:
                username = verify_twitter()
                print(f"OK: Twitter credentials valid (@{username})")
            except Exception as e:
                print(f"FAIL: Twitter credential check: {e}")
                return 1

    dry = os.getenv("DRY_RUN", "1")
    print(f"OK: DRY_RUN={dry}")
    print("=== Validation passed ===")
    return 0