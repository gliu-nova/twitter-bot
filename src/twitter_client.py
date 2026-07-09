from __future__ import annotations

import os
import time
from pathlib import Path

import tweepy

TWITTER_RETRIES = 2


def post_tweet(text: str, *, media_path: str | Path | None = None) -> None:
    if os.getenv("DRY_RUN", "1") == "1":
        print(f"[DRY RUN] Would tweet:\n{text}\n")
        if media_path:
            print(f"[DRY RUN] With media: {media_path}")
        return

    api_key = os.environ["TWITTER_API_KEY"]
    api_secret = os.environ["TWITTER_API_SECRET"]
    access_token = os.environ["TWITTER_ACCESS_TOKEN"]
    access_secret = os.environ["TWITTER_ACCESS_TOKEN_SECRET"]

    client = tweepy.Client(
        consumer_key=api_key,
        consumer_secret=api_secret,
        access_token=access_token,
        access_token_secret=access_secret,
    )

    media_ids: list[int] | None = None
    if media_path:
        path = Path(media_path)
        if path.exists():
            auth = tweepy.OAuth1UserHandler(api_key, api_secret, access_token, access_secret)
            api = tweepy.API(auth)
            uploaded = api.media_upload(filename=str(path))
            media_ids = [uploaded.media_id]

    last_err: Exception | None = None
    for attempt in range(TWITTER_RETRIES + 1):
        try:
            client.create_tweet(text=text[:280], media_ids=media_ids)
            print("Tweet posted." + (f" (media: {media_path})" if media_path else ""))
            return
        except tweepy.TweepyException as e:
            last_err = e
            transient = _is_transient_twitter_error(e)
            if attempt < TWITTER_RETRIES and transient:
                time.sleep(1.0 * (attempt + 1))
                continue
            print(f"Tweet failed: {e}", file=__import__("sys").stderr)
            raise
    if last_err:
        raise last_err


def _is_transient_twitter_error(exc: Exception) -> bool:
    status = getattr(exc, "response", None)
    code = getattr(status, "status_code", None) if status is not None else None
    if code in (429, 500, 502, 503, 504):
        return True
    msg = str(exc).lower()
    return "rate limit" in msg or "over capacity" in msg or "timeout" in msg
