from __future__ import annotations

import os

import tweepy


def post_tweet(text: str) -> None:
    if os.getenv("DRY_RUN", "1") == "1":
        print(f"[DRY RUN] Would tweet:\n{text}\n")
        return

    client = tweepy.Client(
        consumer_key=os.environ["TWITTER_API_KEY"],
        consumer_secret=os.environ["TWITTER_API_SECRET"],
        access_token=os.environ["TWITTER_ACCESS_TOKEN"],
        access_token_secret=os.environ["TWITTER_ACCESS_TOKEN_SECRET"],
    )
    client.create_tweet(text=text[:280])
    print("Tweet posted.")