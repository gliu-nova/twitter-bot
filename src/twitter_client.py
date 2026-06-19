from __future__ import annotations

import os
from pathlib import Path

import tweepy


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

    client.create_tweet(text=text[:280], media_ids=media_ids)
    print("Tweet posted." + (f" (media: {media_path})" if media_path else ""))