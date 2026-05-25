"""Tests for local model reliability hardening: token_correction, token_budget, circuit_breaker."""

import pytest

from letta.agents.circuit_breaker import AgentCircuitBreaker
from letta.agents.token_budget import TokenBudget, TokenBudgetDecision
from letta.local_llm.token_correction import (
    DEFAULT_TOKEN_CORRECTION,
    LiveTokenCalibration,
    get_token_correction,
)


# ---------------------------------------------------------------------------
# token_correction.py
# ---------------------------------------------------------------------------


class TestGetTokenCorrection:
    """Test the static correction factor lookup."""

    def test_none_model_returns_default(self):
        assert get_token_correction(None) == DEFAULT_TOKEN_CORRECTION

    def test_empty_string_returns_default(self):
        assert get_token_correction("") == DEFAULT_TOKEN_CORRECTION

    def test_unknown_model_returns_default(self):
        assert get_token_correction("some-random-model") == DEFAULT_TOKEN_CORRECTION

    def test_qwen_prefix_returns_default_when_placeholder(self):
        # qwen is in the table but value is None — should return default
        assert get_token_correction("qwen2.5-7b-instruct") == DEFAULT_TOKEN_CORRECTION

    def test_llama_prefix_returns_default_when_placeholder(self):
        assert get_token_correction("llama-3-8b") == DEFAULT_TOKEN_CORRECTION

    def test_default_is_conservative(self):
        # 2.5x is conservative enough to prevent context overflow
        assert DEFAULT_TOKEN_CORRECTION >= 2.0


class TestLiveTokenCalibration:
    """Test the live calibration cache."""

    def test_no_data_returns_static(self):
        lc = LiveTokenCalibration()
        # Without any update, should fall back to static table
        assert lc.get_correction("qwen2.5-7b") == DEFAULT_TOKEN_CORRECTION

    def test_update_creates_live_ratio(self):
        lc = LiveTokenCalibration()
        # Server reports 1800 tokens, bytes/4 estimated 1000
        lc.update("qwen2.5-7b", server_prompt_tokens=1800, bytes4_estimate=1000)
        assert lc.get_correction("qwen2.5-7b") == 1.8

    def test_different_model_separate_ratios(self):
        lc = LiveTokenCalibration()
        lc.update("qwen2.5-7b", 1800, 1000)
        lc.update("llama-3-8b", 1500, 1000)
        assert lc.get_correction("qwen2.5-7b") == 1.8
        assert lc.get_correction("llama-3-8b") == 1.5

    def test_unmeasured_model_returns_static(self):
        lc = LiveTokenCalibration()
        lc.update("qwen2.5-7b", 1800, 1000)
        # Different model not in cache — falls back to static
        assert lc.get_correction("phi-3-mini") == DEFAULT_TOKEN_CORRECTION

    def test_zero_bytes4_estimate_ignored(self):
        lc = LiveTokenCalibration()
        lc.update("qwen2.5-7b", 1800, 0)
        # Should not store a ratio (division by zero guard)
        assert lc.get_correction("qwen2.5-7b") == DEFAULT_TOKEN_CORRECTION

    def test_zero_server_tokens_ignored(self):
        lc = LiveTokenCalibration()
        lc.update("qwen2.5-7b", 0, 1000)
        assert lc.get_correction("qwen2.5-7b") == DEFAULT_TOKEN_CORRECTION

    def test_reset_clears_cache(self):
        lc = LiveTokenCalibration()
        lc.update("qwen2.5-7b", 1800, 1000)
        assert lc.get_correction("qwen2.5-7b") == 1.8
        lc.reset()
        assert lc.get_correction("qwen2.5-7b") == DEFAULT_TOKEN_CORRECTION

    def test_get_cached_ratios_returns_copy(self):
        lc = LiveTokenCalibration()
        lc.update("qwen2.5-7b", 1800, 1000)
        ratios = lc.get_cached_ratios()
        assert "qwen2.5-7b" in ratios
        ratios["qwen2.5-7b"] = 999  # modifying copy should not affect cache
        assert lc.get_correction("qwen2.5-7b") == 1.8


# ---------------------------------------------------------------------------
# token_budget.py
# ---------------------------------------------------------------------------


class TestTokenBudget:
    """Test the token budget enforcement."""

    def test_no_limits_never_exceeded(self):
        tb = TokenBudget()
        d = tb.check(step_tokens=999999, total_run_tokens=999999)
        assert not d.exceeded

    def test_step_budget_exceeded(self):
        tb = TokenBudget(max_step_tokens=1000)
        d = tb.check(step_tokens=1500, total_run_tokens=1500)
        assert d.exceeded
        assert d.budget_type == "step"
        assert "max_step_tokens=1000" in d.reason

    def test_step_budget_not_exceeded(self):
        tb = TokenBudget(max_step_tokens=1000)
        d = tb.check(step_tokens=800, total_run_tokens=800)
        assert not d.exceeded

    def test_step_budget_exact_not_exceeded(self):
        tb = TokenBudget(max_step_tokens=1000)
        d = tb.check(step_tokens=1000, total_run_tokens=1000)
        assert not d.exceeded  # not strictly greater

    def test_run_budget_exceeded(self):
        tb = TokenBudget(max_run_tokens=5000)
        d = tb.check(step_tokens=100, total_run_tokens=6000)
        assert d.exceeded
        assert d.budget_type == "run"
        assert "max_run_tokens=5000" in d.reason

    def test_run_budget_not_exceeded(self):
        tb = TokenBudget(max_run_tokens=5000)
        d = tb.check(step_tokens=100, total_run_tokens=4000)
        assert not d.exceeded

    def test_context_window_exceeded(self):
        tb = TokenBudget(context_window_limit=8192, context_window_ratio=0.7)
        # 70% of 8192 = 5734
        d = tb.check(step_tokens=100, total_run_tokens=6000)
        assert d.exceeded
        assert d.budget_type == "context_window"
        assert "context_window=8192" in d.reason

    def test_context_window_not_exceeded(self):
        tb = TokenBudget(context_window_limit=8192, context_window_ratio=0.7)
        d = tb.check(step_tokens=100, total_run_tokens=5000)
        assert not d.exceeded

    def test_context_window_ratio_calculation(self):
        tb = TokenBudget(context_window_limit=10000, context_window_ratio=0.7)
        # effective_limit = 7000
        d = tb.check(step_tokens=100, total_run_tokens=6999)
        assert not d.exceeded
        d = tb.check(step_tokens=100, total_run_tokens=7001)
        assert d.exceeded

    def test_step_check_takes_precedence(self):
        # Step budget is checked first
        tb = TokenBudget(max_step_tokens=100, max_run_tokens=100000)
        d = tb.check(step_tokens=200, total_run_tokens=200)
        assert d.exceeded
        assert d.budget_type == "step"

    def test_run_check_takes_precedence_over_context(self):
        tb = TokenBudget(max_run_tokens=500, context_window_limit=100000)
        d = tb.check(step_tokens=100, total_run_tokens=600)
        assert d.exceeded
        assert d.budget_type == "run"

    def test_run_tokens_used_property(self):
        tb = TokenBudget()
        tb.check(step_tokens=100, total_run_tokens=5000)
        assert tb.run_tokens_used == 5000

    def test_default_context_window_ratio_is_0_7(self):
        tb = TokenBudget(context_window_limit=8192)
        assert tb.context_window_ratio == 0.7


class TestTokenBudgetDecision:
    """Test the TokenBudgetDecision dataclass."""

    def test_not_exceeded(self):
        d = TokenBudgetDecision(exceeded=False, reason="", budget_type="")
        assert not d.exceeded

    def test_exceeded_with_reason(self):
        d = TokenBudgetDecision(exceeded=True, reason="over limit", budget_type="run")
        assert d.exceeded
        assert d.reason == "over limit"
        assert d.budget_type == "run"


# ---------------------------------------------------------------------------
# circuit_breaker.py
# ---------------------------------------------------------------------------


class TestAgentCircuitBreaker:
    """Test the circuit breaker for step loop error handling."""

    def test_first_error_returns_none(self):
        cb = AgentCircuitBreaker()
        assert cb.record_error("llm_api_error") is None

    def test_second_error_returns_none(self):
        cb = AgentCircuitBreaker()
        cb.record_error("llm_api_error")
        assert cb.record_error("llm_api_error") is None

    def test_third_error_triggers_auto_compact(self):
        cb = AgentCircuitBreaker()
        cb.record_error("llm_api_error")
        cb.record_error("llm_api_error")
        assert cb.record_error("llm_api_error") == "auto_compact"

    def test_success_resets_counters(self):
        cb = AgentCircuitBreaker()
        cb.record_error("llm_api_error")
        cb.record_error("llm_api_error")
        cb.record_success()
        # After reset, we need 3 more errors
        assert cb.record_error("llm_api_error") is None
        assert cb.record_error("llm_api_error") is None
        assert cb.record_error("llm_api_error") == "auto_compact"

    def test_context_overflow_triggers_at_2(self):
        cb = AgentCircuitBreaker()
        assert cb.record_error("context_window_overflow") is None
        assert cb.record_error("context_window_overflow") == "auto_compact"

    def test_different_error_types_tracked_separately(self):
        cb = AgentCircuitBreaker()
        cb.record_error("llm_api_error")
        cb.record_error("context_window_overflow")
        # Only 1 of each — neither should trigger
        counts = cb.get_counts()
        assert counts["llm_api_error"] == 1
        assert counts["context_window_overflow"] == 1

    def test_success_resets_all_categories(self):
        cb = AgentCircuitBreaker()
        cb.record_error("llm_api_error")
        cb.record_error("context_window_overflow")
        cb.record_success()
        assert cb.get_counts() == {}

    def test_is_open_after_trigger(self):
        cb = AgentCircuitBreaker()
        assert not cb.is_open
        cb.record_error("llm_api_error")
        cb.record_error("llm_api_error")
        cb.record_error("llm_api_error")
        assert cb.is_open

    def test_is_open_cleared_after_success(self):
        cb = AgentCircuitBreaker()
        cb.record_error("llm_api_error")
        cb.record_error("llm_api_error")
        cb.record_error("llm_api_error")
        assert cb.is_open
        cb.record_success()
        assert not cb.is_open

    def test_last_action(self):
        cb = AgentCircuitBreaker()
        assert cb.last_action is None
        cb.record_error("llm_api_error")
        assert cb.last_action is None
        cb.record_error("llm_api_error")
        cb.record_error("llm_api_error")
        assert cb.last_action == "auto_compact"

    def test_reset_clears_everything(self):
        cb = AgentCircuitBreaker()
        cb.record_error("llm_api_error")
        cb.record_error("llm_api_error")
        cb.record_error("llm_api_error")
        cb.reset()
        assert cb.get_counts() == {}
        assert not cb.is_open
        assert cb.last_action is None

    def test_custom_thresholds(self):
        cb = AgentCircuitBreaker(thresholds={"llm_api_error": 5})
        for _ in range(4):
            assert cb.record_error("llm_api_error") is None
        assert cb.record_error("llm_api_error") == "auto_compact"

    def test_unknown_error_type_uses_default_threshold(self):
        cb = AgentCircuitBreaker()
        # Unknown type defaults to threshold of 3
        for _ in range(2):
            assert cb.record_error("some_new_error") is None
        assert cb.record_error("some_new_error") == "auto_compact"

    def test_get_counts_returns_copy(self):
        cb = AgentCircuitBreaker()
        cb.record_error("llm_api_error")
        counts = cb.get_counts()
        counts["llm_api_error"] = 999
        # Original should not be affected
        assert cb.get_counts()["llm_api_error"] == 1
