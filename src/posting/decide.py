from __future__ import annotations

from typing import Any

from src.posting.grouping import best_story_theme, group_related_alerts, group_score
from src.posting.models import AlertTrigger, TweetDecision


def decide_tweet_type(
    triggered_alerts: list[AlertTrigger],
    posting_cfg: dict[str, Any],
) -> TweetDecision | None:
    if not triggered_alerts:
        return None

    high_single = float(posting_cfg.get("high_single_threshold", 85))
    multi_threshold = float(posting_cfg.get("multi_threshold", 120))
    min_cluster_size = int(posting_cfg.get("min_cluster_size", 3))
    emergency_threshold = float(posting_cfg.get("emergency_threshold", 90))

    top_alert = max(triggered_alerts, key=lambda x: x.score)

    # Rule 1: Very high single score → standalone tweet
    if top_alert.score >= high_single or top_alert.standalone_major:
        is_emergency = (
            top_alert.alert_tier == "emergency"
            or top_alert.score >= emergency_threshold
        )
        return TweetDecision(
            tweet_type="single",
            alerts=[top_alert],
            score=top_alert.score,
            is_emergency=is_emergency,
        )

    # Rule 2–4: Group related alerts
    groups = group_related_alerts(triggered_alerts, posting_cfg)
    if groups:
        best_group = groups[0]
        g_score = group_score(best_group, posting_cfg)
        theme = best_story_theme(best_group)

        if len(best_group) >= min_cluster_size and g_score >= multi_threshold:
            return TweetDecision(
                tweet_type="multi",
                alerts=best_group,
                score=g_score,
                is_emergency=False,
                theme=theme,
            )

        # Allow 2-alert multi if score is strong and coherent
        if len(best_group) >= 2 and g_score >= multi_threshold * 0.85:
            return TweetDecision(
                tweet_type="multi",
                alerts=best_group,
                score=g_score,
                is_emergency=False,
                theme=theme,
            )

    # Fallback: highest-scoring single
    return TweetDecision(
        tweet_type="single",
        alerts=[top_alert],
        score=top_alert.score,
        is_emergency=top_alert.alert_tier == "emergency" or top_alert.score >= emergency_threshold,
    )