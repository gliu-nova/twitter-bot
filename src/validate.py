from __future__ import annotations

import os
import sys

import tweepy

from src.quality import check_api_health

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
    return str(me.data.username)


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