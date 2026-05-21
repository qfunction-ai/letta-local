"""Unit tests for the tool capability probe and auto-mode resolution."""

import json
from unittest.mock import MagicMock, patch

import pytest

from letta.llm_api.tool_capability_probe import ToolCapabilityCache, resolve_tool_calling_mode
from letta.schemas.llm_config import LLMConfig, ModelConstraints


# -------------------------------------------------------
# Fixtures
# -------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_cache():
    """Reset the singleton cache before each test."""
    ToolCapabilityCache.reset()
    yield
    ToolCapabilityCache.reset()


def _make_config(
    model="test-model",
    endpoint_type="ollama",
    endpoint="http://localhost:11434/v1",
    tool_calling_mode="auto",
):
    """Create an LLMConfig with the given tool_calling_mode."""
    return LLMConfig(
        model=model,
        model_endpoint_type=endpoint_type,
        model_endpoint=endpoint,
        context_window=8192,
        handle=f"{endpoint_type}/{model}",
        constraints=ModelConstraints(
            tool_calling_mode=tool_calling_mode,
        ) if tool_calling_mode else None,
    )


# -------------------------------------------------------
# ToolCapabilityCache tests
# -------------------------------------------------------

class TestToolCapabilityCache:

    def test_cache_returns_none_for_uncached_model(self):
        cache = ToolCapabilityCache()
        config = _make_config()
        assert cache.get_cached(config) is None

    def test_set_and_get_cached(self):
        cache = ToolCapabilityCache()
        config = _make_config()
        cache.set_cached(config, True)
        assert cache.get_cached(config) is True

    def test_cache_key_differentiates_models(self):
        cache = ToolCapabilityCache()
        config_a = _make_config(model="model-a")
        config_b = _make_config(model="model-b")
        cache.set_cached(config_a, True)
        cache.set_cached(config_b, False)
        assert cache.get_cached(config_a) is True
        assert cache.get_cached(config_b) is False

    def test_probe_uses_cache_on_second_call(self):
        """After the first probe, subsequent calls should use the cache."""
        cache = ToolCapabilityCache()
        config = _make_config()

        # First probe: mock the Ollama show endpoint
        with patch("letta.llm_api.tool_capability_probe.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"capabilities": ["completion", "tools"]}
            mock_post.return_value = mock_resp
            result1 = cache.probe(config)

        assert result1 is True
        # The probe should have been called once
        assert mock_post.call_count == 1

        # Second probe: should use cache, no HTTP call
        with patch("letta.llm_api.tool_capability_probe.requests.post") as mock_post2:
            result2 = cache.probe(config)
            mock_post2.assert_not_called()

        assert result2 is True

    def test_probe_ollama_with_tools_capability(self):
        """Ollama model with "tools" in capabilities should return True."""
        cache = ToolCapabilityCache()
        config = _make_config(endpoint_type="ollama")

        with patch("letta.llm_api.tool_capability_probe.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"capabilities": ["completion", "tools"]}
            mock_post.return_value = mock_resp
            result = cache.probe(config)

        assert result is True
        # Should have called /api/show
        mock_post.assert_called_once()
        call_url = mock_post.call_args[0][0]
        assert call_url == "http://localhost:11434/api/show"

    def test_probe_ollama_without_tools_capability(self):
        """Ollama model without "tools" in capabilities should return False."""
        cache = ToolCapabilityCache()
        config = _make_config(endpoint_type="ollama", model="phi3:mini")

        with patch("letta.llm_api.tool_capability_probe.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"capabilities": ["completion"]}
            mock_post.return_value = mock_resp
            result = cache.probe(config)

        assert result is False

    def test_probe_ollama_empty_capabilities(self):
        """Ollama model with empty capabilities should return False."""
        cache = ToolCapabilityCache()
        config = _make_config(endpoint_type="ollama")

        with patch("letta.llm_api.tool_capability_probe.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"capabilities": []}
            mock_post.return_value = mock_resp
            result = cache.probe(config)

        assert result is False

    def test_probe_ollama_http_error(self):
        """Ollama probe on HTTP error should return False (safe fallback)."""
        cache = ToolCapabilityCache()
        config = _make_config(endpoint_type="ollama")

        with patch("letta.llm_api.tool_capability_probe.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 500
            mock_post.return_value = mock_resp
            result = cache.probe(config)

        assert result is False

    def test_probe_ollama_connection_error(self):
        """Ollama probe on connection error should return False."""
        cache = ToolCapabilityCache()
        config = _make_config(endpoint_type="ollama")

        import requests as req
        with patch("letta.llm_api.tool_capability_probe.requests.post") as mock_post:
            mock_post.side_effect = req.ConnectionError("Connection refused")
            result = cache.probe(config)

        assert result is False

    def test_probe_generic_with_native_tool_calls(self):
        """Generic provider returning native tool_calls should return True."""
        cache = ToolCapabilityCache()
        config = _make_config(endpoint_type="vllm", endpoint="http://localhost:8000/v1")

        with patch("letta.llm_api.tool_capability_probe.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "echo",
                                        "arguments": '{"text": "test"}',
                                    }
                                }
                            ]
                        }
                    }
                ]
            }
            mock_post.return_value = mock_resp
            result = cache.probe(config)

        assert result is True

    def test_probe_generic_without_tool_calls(self):
        """Generic provider returning text-only should return False."""
        cache = ToolCapabilityCache()
        config = _make_config(endpoint_type="vllm", endpoint="http://localhost:8000/v1")

        with patch("letta.llm_api.tool_capability_probe.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "choices": [
                    {
                        "message": {
                            "content": '{"function": "echo", "params": {"text": "test"}}',
                        }
                    }
                ]
            }
            mock_post.return_value = mock_resp
            result = cache.probe(config)

        assert result is False

    def test_probe_generic_400_tools_not_supported(self):
        """Generic provider returning 400 with 'tools' error should return False."""
        cache = ToolCapabilityCache()
        config = _make_config(endpoint_type="localai", endpoint="http://localhost:8080/v1")

        with patch("letta.llm_api.tool_capability_probe.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 400
            mock_resp.text = '{"error": "model does not support tools"}'
            mock_post.return_value = mock_resp
            result = cache.probe(config)

        assert result is False

    def test_singleton_instance(self):
        """instance() should return the same object each time."""
        a = ToolCapabilityCache.instance()
        b = ToolCapabilityCache.instance()
        assert a is b


# -------------------------------------------------------
# resolve_tool_calling_mode tests
# -------------------------------------------------------

class TestResolveToolCallingMode:

    def test_native_mode_passes_through(self):
        config = _make_config(tool_calling_mode="native")
        assert resolve_tool_calling_mode(config) == "native"

    def test_prompt_mode_passes_through(self):
        config = _make_config(tool_calling_mode="prompt")
        assert resolve_tool_calling_mode(config) == "prompt"

    def test_no_constraints_defaults_to_native(self):
        config = _make_config(tool_calling_mode=None)
        config.constraints = None
        assert resolve_tool_calling_mode(config) == "native"

    def test_auto_mode_resolves_to_prompt_for_non_tool_model(self):
        """Auto mode with a model that doesn't support tools -> prompt."""
        cache = ToolCapabilityCache.instance()
        config = _make_config(tool_calling_mode="auto", endpoint_type="ollama")

        with patch("letta.llm_api.tool_capability_probe.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"capabilities": ["completion"]}
            mock_post.return_value = mock_resp
            result = resolve_tool_calling_mode(config)

        assert result == "prompt"

    def test_auto_mode_resolves_to_native_for_tool_model(self):
        """Auto mode with a model that supports tools -> native."""
        cache = ToolCapabilityCache.instance()
        config = _make_config(tool_calling_mode="auto", endpoint_type="ollama")

        with patch("letta.llm_api.tool_capability_probe.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"capabilities": ["completion", "tools"]}
            mock_post.return_value = mock_resp
            result = resolve_tool_calling_mode(config)

        assert result == "native"

    def test_auto_mode_uses_cached_result(self):
        """After first probe, the resolved mode should be cached."""
        cache = ToolCapabilityCache.instance()
        config = _make_config(tool_calling_mode="auto", endpoint_type="ollama")

        # First call: probe
        with patch("letta.llm_api.tool_capability_probe.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"capabilities": ["completion", "tools"]}
            mock_post.return_value = mock_resp
            result1 = resolve_tool_calling_mode(config)
            assert mock_post.call_count == 1

        # Second call: cached
        with patch("letta.llm_api.tool_capability_probe.requests.post") as mock_post2:
            result2 = resolve_tool_calling_mode(config)
            mock_post2.assert_not_called()

        assert result1 == "native"
        assert result2 == "native"

    def test_auto_mode_with_preseeded_cache(self):
        """If the cache is pre-seeded, no probe should happen."""
        cache = ToolCapabilityCache.instance()
        config = _make_config(tool_calling_mode="auto", endpoint_type="ollama")
        cache.set_cached(config, True)

        with patch("letta.llm_api.tool_capability_probe.requests.post") as mock_post:
            result = resolve_tool_calling_mode(config)
            mock_post.assert_not_called()

        assert result == "native"


# -------------------------------------------------------
# apply_default_constraints integration
# -------------------------------------------------------

class TestAutoApplyConstraints:

    def test_ollama_gets_auto_mode(self):
        """Ollama provider should get tool_calling_mode='auto' by default."""
        config = LLMConfig(
            model="test-model",
            model_endpoint_type="ollama",
            model_endpoint="http://localhost:11434/v1",
            context_window=131072,
            handle="ollama/test-model",
        )
        # apply_default_constraints is a model_validator, so it runs on construction
        assert config.constraints is not None
        assert config.constraints.tool_calling_mode == "auto"

    def test_vllm_gets_auto_mode(self):
        """vLLM provider should get tool_calling_mode='auto' by default."""
        config = LLMConfig(
            model="test-model",
            model_endpoint_type="vllm",
            model_endpoint="http://localhost:8000/v1",
            context_window=131072,
            handle="vllm/test-model",
        )
        assert config.constraints is not None
        assert config.constraints.tool_calling_mode == "auto"

    def test_openai_stays_native(self):
        """OpenAI provider should NOT get auto-apply (no constraints)."""
        config = LLMConfig(
            model="gpt-4",
            model_endpoint_type="openai",
            model_endpoint="https://api.openai.com/v1",
            context_window=128000,
            handle="openai/gpt-4",
        )
        # OpenAI should not get auto-applied constraints
        assert config.constraints is None

    def test_small_context_gets_aggressive_constraints(self):
        """Very small context should still get degraded constraints."""
        config = LLMConfig(
            model="tiny-model",
            model_endpoint_type="openai",
            model_endpoint="https://api.openai.com/v1",
            context_window=4096,
            handle="openai/tiny-model",
        )
        assert config.constraints is not None
        assert config.constraints.force_external_summarizer is True
        assert config.constraints.json_repair_level == "aggressive"

    def test_explicit_constraints_not_overridden(self):
        """If constraints are already set, auto-apply should not override."""
        config = LLMConfig(
            model="test-model",
            model_endpoint_type="ollama",
            model_endpoint="http://localhost:11434/v1",
            context_window=131072,
            handle="ollama/test-model",
            constraints=ModelConstraints(
                tool_calling_mode="prompt",
            ),
        )
        assert config.constraints.tool_calling_mode == "prompt"
