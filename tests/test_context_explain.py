"""Tests for tweet context selection explain/debug mode."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from src.market_memory_bridge import MemoryContextResult
from src.posting.compose import compose_single_tweet, explain_context_selection
from src.posting.context_explain import (
    SkippedPostCandidate,
    explain_context_enabled,
    explain_skipped_top_n,
    finalize_context_explain_logs,
    select_top_skipped_candidates,
)
from src.posting.history import MoveHistory
from src.posting.models import AlertTrigger


def _alert(
    indicator: str,
    value: float,
    prev: float | None = None,
    *,
    score: float = 0.0,
) -> AlertTrigger:
    return AlertTrigger(
        indicator=indicator,
        name=indicator,
        value=value,
        prev_value=prev,
        reasons=[],
        rule_types=["percent_change"],
        themes=["crypto"],
        category="crypto",
        is_macro=False,
        timestamp=datetime.now(timezone.utc),
        score=score,
    )


class ExplainContextEnabledTests(unittest.TestCase):
    def test_disabled_by_default(self) -> None:
        self.assertFalse(explain_context_enabled())
        self.assertFalse(explain_context_enabled(posting_cfg={}, app_cfg={"posting": {}}))

    def test_enabled_via_config(self) -> None:
        self.assertTrue(explain_context_enabled(app_cfg={"posting": {"explain_context": True}}))

    def test_env_override(self) -> None:
        with patch.dict("os.environ", {"EXPLAIN_CONTEXT": "1"}):
            self.assertTrue(explain_context_enabled())


class ContextSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = {
            "market_memory": {"enabled": True, "data_dir": "data/market_memory", "min_occurrences": 3},
        }

    def test_memory_skipped_when_sqlite_rarity_exists(self) -> None:
        history = MoveHistory(days_since_larger_move=14, pct_change=5.0)
        decision = explain_context_selection(
            _alert("btc_funding", 0.0005, 0.0001),
            history,
            app_cfg=self.cfg,
        )
        self.assertEqual(decision.selected_source, "sqlite_rarity")
        self.assertTrue(decision.sqlite_produced_line)
        self.assertTrue(decision.memory_eligible)
        self.assertFalse(decision.memory_queried)
        self.assertEqual(decision.memory_skip_reason, "sqlite_rarity_selected")

    @patch("src.market_memory_bridge.memory_context_decision")
    def test_memory_queried_and_selected(self, mock_memory: unittest.mock.MagicMock) -> None:
        mock_memory.return_value = MemoryContextResult(
            line="Similar BTC funding spikes since 2021: 8 occurrences.",
            eligible=True,
            queried=True,
            occurrences=8,
            percentile=92.0,
        )
        history = MoveHistory(pct_change=1.0)
        decision = explain_context_selection(
            _alert("btc_funding", 0.0001, 0.00009),
            history,
            app_cfg=self.cfg,
        )
        mock_memory.assert_called_once()
        self.assertEqual(decision.selected_source, "market_memory")
        self.assertTrue(decision.memory_queried)
        self.assertEqual(decision.memory_occurrences, 8)
        self.assertIn("occurrences", decision.selected_line or "")

    @patch("src.market_memory_bridge.memory_context_decision")
    def test_memory_rejected_below_thresholds(self, mock_memory: unittest.mock.MagicMock) -> None:
        mock_memory.return_value = MemoryContextResult(
            eligible=True,
            queried=True,
            occurrences=2,
            percentile=55.0,
            reject_reason="occurrences_below_min:2<3",
        )
        history = MoveHistory(pct_change=1.0)
        decision = explain_context_selection(
            _alert("btc_funding", 0.0001, 0.00009),
            history,
            app_cfg=self.cfg,
        )
        self.assertTrue(decision.memory_queried)
        self.assertEqual(decision.memory_reject_reason, "occurrences_below_min:2<3")
        self.assertEqual(decision.memory_occurrences, 2)
        self.assertEqual(decision.selected_source, "template_fallback")
        self.assertIsNotNone(decision.selected_line)

    @patch("src.market_memory_bridge.memory_context_decision")
    def test_fallback_when_no_sqlite_or_memory(self, mock_memory: unittest.mock.MagicMock) -> None:
        mock_memory.return_value = MemoryContextResult(
            eligible=True,
            queried=True,
            occurrences=1,
            reject_reason="occurrences_below_min:1<3",
        )
        history = MoveHistory(pct_change=2.0)
        alert = _alert("btc_exchange_spread", 3.2, 28.5)
        decision = explain_context_selection(alert, history, app_cfg=self.cfg)
        self.assertEqual(decision.selected_source, "template_fallback")
        self.assertFalse(decision.sqlite_produced_line)
        self.assertIsNotNone(decision.selected_line)


class SkippedExplainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = {
            "posting": {
                "explain_context": True,
                "explain_skipped_top_n": 1,
            },
            "market_memory": {"enabled": False},
        }

    def test_explain_skipped_top_n_defaults_and_env(self) -> None:
        self.assertEqual(explain_skipped_top_n(app_cfg={"posting": {}}), 1)
        self.assertEqual(
            explain_skipped_top_n(app_cfg={"posting": {"explain_skipped_top_n": 3}}),
            3,
        )
        with patch.dict("os.environ", {"EXPLAIN_SKIPPED_TOP_N": "2"}):
            self.assertEqual(explain_skipped_top_n(app_cfg={"posting": {"explain_skipped_top_n": 3}}), 2)

    def test_select_top_skipped_candidates_respects_limit_and_dedupes(self) -> None:
        candidates = [
            SkippedPostCandidate(_alert("btc", 1, score=90), "daily_cap", 90),
            SkippedPostCandidate(_alert("eth", 2, score=80), "cooldown", 80),
            SkippedPostCandidate(_alert("btc", 3, score=70), "buffered", 70),
        ]
        picked = select_top_skipped_candidates(candidates, 2)
        self.assertEqual(len(picked), 2)
        self.assertEqual(picked[0].alert.indicator, "btc")
        self.assertEqual(picked[1].alert.indicator, "eth")

    @patch("src.posting.context_explain.log_skipped_context_explanations")
    def test_posted_run_does_not_log_skipped(self, mock_log_skipped: unittest.mock.MagicMock) -> None:
        finalize_context_explain_logs(
            posted=1,
            skipped_candidates=[
                SkippedPostCandidate(_alert("btc", 1, score=90), "daily_cap", 90),
            ],
            conn=unittest.mock.MagicMock(),
            cfg=self.cfg,
        )
        mock_log_skipped.assert_not_called()

    @patch("src.posting.compose.explain_context_selection")
    @patch("src.posting.history.build_move_history")
    def test_no_post_run_logs_top_skipped_by_default(
        self,
        mock_history: unittest.mock.MagicMock,
        mock_explain: unittest.mock.MagicMock,
    ) -> None:
        mock_history.return_value = MoveHistory()
        mock_explain.return_value = explain_context_selection(
            _alert("btc_funding", 0.0001, 0.00009),
            MoveHistory(),
            app_cfg=self.cfg,
        )
        candidates = [
            SkippedPostCandidate(_alert("eth", 1, score=70), "cooldown", 70),
            SkippedPostCandidate(_alert("btc_funding", 0.0001, score=90), "daily_cap", 90),
        ]
        with patch("builtins.print") as mock_print:
            finalize_context_explain_logs(
                posted=0,
                skipped_candidates=candidates,
                conn=unittest.mock.MagicMock(),
                cfg=self.cfg,
            )
        skipped_lines = [
            str(call.args[0]) for call in mock_print.call_args_list
            if str(call.args[0]).startswith("[context-explain-skipped]")
        ]
        self.assertEqual(len(skipped_lines), 1)
        self.assertIn("btc_funding", skipped_lines[0])
        self.assertIn("daily_cap", skipped_lines[0])
        mock_explain.assert_called_once()

    @patch("src.posting.compose.explain_context_selection")
    @patch("src.posting.history.build_move_history")
    def test_explain_skipped_top_n_controls_count(
        self,
        mock_history: unittest.mock.MagicMock,
        mock_explain: unittest.mock.MagicMock,
    ) -> None:
        mock_history.return_value = MoveHistory()
        mock_explain.side_effect = lambda alert, history, app_cfg=None: explain_context_selection(
            alert, history, app_cfg=app_cfg,
        )
        cfg = {**self.cfg, "posting": {**self.cfg["posting"], "explain_skipped_top_n": 2}}
        candidates = [
            SkippedPostCandidate(_alert("btc", 1, score=90), "daily_cap", 90),
            SkippedPostCandidate(_alert("eth", 2, score=80), "cooldown", 80),
            SkippedPostCandidate(_alert("sol", 3, score=70), "buffered", 70),
        ]
        with patch("builtins.print") as mock_print:
            finalize_context_explain_logs(
                posted=0,
                skipped_candidates=candidates,
                conn=unittest.mock.MagicMock(),
                cfg=cfg,
            )
        skipped_lines = [
            str(call.args[0]) for call in mock_print.call_args_list
            if str(call.args[0]).startswith("[context-explain-skipped]")
        ]
        self.assertEqual(len(skipped_lines), 2)
        self.assertEqual(mock_explain.call_count, 2)

    @patch("src.posting.compose.explain_context_selection")
    @patch("src.posting.history.build_move_history")
    def test_explain_skipped_top_n_zero_disables_skipped_logs(
        self,
        mock_history: unittest.mock.MagicMock,
        mock_explain: unittest.mock.MagicMock,
    ) -> None:
        cfg = {**self.cfg, "posting": {**self.cfg["posting"], "explain_skipped_top_n": 0}}
        with patch("builtins.print") as mock_print:
            finalize_context_explain_logs(
                posted=0,
                skipped_candidates=[
                    SkippedPostCandidate(_alert("btc", 1, score=90), "daily_cap", 90),
                ],
                conn=unittest.mock.MagicMock(),
                cfg=cfg,
            )
        skipped_lines = [
            str(call.args[0]) for call in mock_print.call_args_list
            if "context-explain-skipped" in str(call.args[0])
        ]
        self.assertEqual(skipped_lines, [])
        mock_explain.assert_not_called()

    @patch("src.posting.compose.explain_context_selection")
    @patch("src.posting.history.build_move_history")
    def test_skipped_logs_require_explain_context_enabled(
        self,
        mock_history: unittest.mock.MagicMock,
        mock_explain: unittest.mock.MagicMock,
    ) -> None:
        cfg = {**self.cfg, "posting": {**self.cfg["posting"], "explain_context": False}}
        with patch("builtins.print") as mock_print:
            finalize_context_explain_logs(
                posted=0,
                skipped_candidates=[
                    SkippedPostCandidate(_alert("btc", 1, score=90), "daily_cap", 90),
                ],
                conn=unittest.mock.MagicMock(),
                cfg=cfg,
            )
        skipped_lines = [
            str(call.args[0]) for call in mock_print.call_args_list
            if "context-explain-skipped" in str(call.args[0])
        ]
        self.assertEqual(skipped_lines, [])
        mock_explain.assert_not_called()

    @patch("builtins.print")
    def test_posted_tweet_logs_only_posted_explanation(self, mock_print: unittest.mock.MagicMock) -> None:
        alert = _alert("btc_funding", 0.0005, 0.0001, score=90)
        history = MoveHistory(days_since_larger_move=14, pct_change=5.0)
        cfg = {**self.cfg, "market_memory": {"enabled": True, "data_dir": "data/market_memory"}}
        compose_single_tweet(
            alert,
            history=history,
            posting_cfg=cfg["posting"],
            app_cfg=cfg,
        )
        posted_lines = [
            str(call.args[0]) for call in mock_print.call_args_list
            if str(call.args[0]).startswith("[context-explain]")
        ]
        skipped_lines = [
            str(call.args[0]) for call in mock_print.call_args_list
            if str(call.args[0]).startswith("[context-explain-skipped]")
        ]
        self.assertEqual(len(posted_lines), 1)
        self.assertIn('"outcome":"posted"', posted_lines[0])
        self.assertEqual(skipped_lines, [])


if __name__ == "__main__":
    unittest.main()