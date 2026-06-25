"""Structured explain/debug for tweet context source selection."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from src.posting.models import AlertTrigger

ContextSource = Literal["sqlite_rarity", "market_memory", "template_fallback", "none"]
ExplainOutcome = Literal["posted", "skipped"]
PostSkipGate = Literal["buffered", "stale", "cooldown", "daily_cap"]
PrimarySkipReason = Literal["below_threshold", "buffered", "stale", "cooldown", "daily_cap"]
SecondarySkipReason = Literal["below_threshold", "cooldown", "daily_cap", "stale", "buffered"]


@dataclass
class ContextDecision:
    indicator: str
    asset: str
    alert_value: float
    candidates_considered: list[str] = field(default_factory=list)
    sqlite_produced_line: bool = False
    sqlite_branch: str | None = None
    memory_eligible: bool = False
    memory_queried: bool = False
    memory_skip_reason: str | None = None
    memory_reject_reason: str | None = None
    memory_occurrences: int | None = None
    memory_percentile: float | None = None
    selected_source: ContextSource = "none"
    selected_line: str | None = None

    @classmethod
    def for_alert(cls, alert: AlertTrigger) -> ContextDecision:
        asset = alert.indicator.split("_")[0].upper()
        return cls(
            indicator=alert.indicator,
            asset=asset,
            alert_value=float(alert.value),
        )

    def note_candidate(self, name: str) -> None:
        if name not in self.candidates_considered:
            self.candidates_considered.append(name)

    def select(
        self,
        source: ContextSource,
        line: str | None,
        *,
        sqlite_branch: str | None = None,
    ) -> None:
        self.selected_source = source
        self.selected_line = line
        if source == "sqlite_rarity":
            self.sqlite_produced_line = True
            self.sqlite_branch = sqlite_branch

    def apply_memory_fields(
        self,
        *,
        eligible: bool,
        queried: bool,
        skip_reason: str | None = None,
        reject_reason: str | None = None,
        occurrences: int | None = None,
        percentile: float | None = None,
    ) -> None:
        self.memory_eligible = eligible
        self.memory_queried = queried
        if skip_reason is not None:
            self.memory_skip_reason = skip_reason
        if reject_reason is not None:
            self.memory_reject_reason = reject_reason
        if occurrences is not None:
            self.memory_occurrences = occurrences
        if percentile is not None:
            self.memory_percentile = percentile

    def to_log_dict(self, *, outcome: ExplainOutcome | None = None) -> dict[str, Any]:
        raw = asdict(self)
        if outcome is not None:
            raw["outcome"] = outcome
        return {key: value for key, value in raw.items() if value not in (None, "", [], False) or key in (
            "indicator", "asset", "alert_value", "selected_source", "outcome",
        )}

    def to_log_line(self, *, outcome: ExplainOutcome | None = None) -> str:
        return json.dumps(self.to_log_dict(outcome=outcome), separators=(",", ":"), ensure_ascii=True)


def classify_post_skip(
    *,
    gate: PostSkipGate,
    score: float,
    post_threshold: float | None,
) -> tuple[PrimarySkipReason, SecondarySkipReason | None]:
    """Map posting-engine gate + score to primary/secondary skip reasons."""
    below_threshold = post_threshold is not None and score < post_threshold

    if gate == "buffered":
        return "buffered", "below_threshold" if below_threshold else None
    if gate == "stale":
        return "stale", "below_threshold" if below_threshold else None
    if gate == "cooldown":
        if below_threshold:
            return "below_threshold", "cooldown"
        return "cooldown", None
    if gate == "daily_cap":
        if below_threshold:
            return "below_threshold", "daily_cap"
        return "daily_cap", None
    raise ValueError(f"unknown post skip gate: {gate}")


@dataclass
class SkippedPostCandidate:
    alert: AlertTrigger
    primary_skip_reason: PrimarySkipReason
    score: float
    post_threshold: float | None = None
    secondary_skip_reason: SecondarySkipReason | None = None
    skip_detail: str | None = None


# Alias for callers that prefer the explain naming.
ContextExplain = ContextDecision


def explain_context_enabled(
    posting_cfg: dict[str, Any] | None = None,
    app_cfg: dict[str, Any] | None = None,
) -> bool:
    env = os.environ.get("EXPLAIN_CONTEXT", "").strip().lower()
    if env in ("1", "true", "yes"):
        return True
    if posting_cfg and "explain_context" in posting_cfg:
        return bool(posting_cfg["explain_context"])
    posting = (app_cfg or {}).get("posting") or {}
    return bool(posting.get("explain_context", False))


def explain_skipped_top_n(
    posting_cfg: dict[str, Any] | None = None,
    app_cfg: dict[str, Any] | None = None,
) -> int:
    env = os.environ.get("EXPLAIN_SKIPPED_TOP_N")
    if env is not None and env.strip() != "":
        return int(env)
    if posting_cfg and "explain_skipped_top_n" in posting_cfg:
        return int(posting_cfg["explain_skipped_top_n"])
    posting = (app_cfg or {}).get("posting") or {}
    return int(posting.get("explain_skipped_top_n", 1))


def select_top_skipped_candidates(
    candidates: list[SkippedPostCandidate],
    limit: int,
) -> list[SkippedPostCandidate]:
    if limit <= 0 or not candidates:
        return []
    ordered = sorted(candidates, key=lambda item: item.score, reverse=True)
    picked: list[SkippedPostCandidate] = []
    seen: set[str] = set()
    for candidate in ordered:
        key = candidate.alert.indicator
        if key in seen:
            continue
        seen.add(key)
        picked.append(candidate)
        if len(picked) >= limit:
            break
    return picked


def log_context_decision(decision: ContextDecision | None) -> None:
    if decision is None:
        return
    print(f"[context-explain] {decision.to_log_line(outcome='posted')}")


def log_skipped_context_explanations(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    candidates: list[SkippedPostCandidate],
) -> None:
    posting_cfg = cfg.get("posting") or {}
    if not explain_context_enabled(posting_cfg, cfg):
        return
    limit = explain_skipped_top_n(posting_cfg, cfg)
    selected = select_top_skipped_candidates(candidates, limit)
    if not selected:
        return

    from src.posting.compose import explain_context_selection
    from src.posting.history import build_move_history

    for candidate in selected:
        history = build_move_history(conn, candidate.alert)
        context = explain_context_selection(candidate.alert, history, app_cfg=cfg)
        payload = context.to_log_dict(outcome="skipped")
        payload["score"] = candidate.score
        payload["primary_skip_reason"] = candidate.primary_skip_reason
        if candidate.secondary_skip_reason is not None:
            payload["secondary_skip_reason"] = candidate.secondary_skip_reason
        if candidate.skip_detail is not None:
            payload["skip_detail"] = candidate.skip_detail
        if candidate.post_threshold is not None:
            payload["post_threshold"] = candidate.post_threshold
        print(
            f"[context-explain-skipped] "
            f"{json.dumps(payload, separators=(',', ':'), ensure_ascii=True)}"
        )


def finalize_context_explain_logs(
    *,
    posted: int,
    skipped_candidates: list[SkippedPostCandidate],
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
) -> None:
    if posted > 0:
        return
    log_skipped_context_explanations(conn, cfg, skipped_candidates)