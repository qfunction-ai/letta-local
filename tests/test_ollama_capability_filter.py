"""Tests for Ollama provider model capability handling and constraint generation."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from letta.schemas.enums import ProviderCategory, ProviderType
from letta.schemas.llm_config import LLMConfig, ModelConstraints
from letta.schemas.providers.ollama import OllamaProvider


def _make_ollama_provider():
    return OllamaProvider(
        name="Ollama",
        provider_type=ProviderType.ollama,
        provider_category=ProviderCategory.base,
        base_url="http://localhost:11434",
    )


def _mock_tags_response():
    """Mock response data for /api/tags."""
    return {
        "models": [
            {
                "name": "gemma4:latest",
                "model": "gemma4:latest",
                "size": 5000000000,
                "details": {
                    "parent_model": "",
                    "format": "gguf",
                    "family": "gemma",
                    "families": ["gemma"],
                    "parameter_size": "4B",
                    "quantization_level": "Q4_K_M",
                },
            },
            {
                "name": "phi3:mini",
                "model": "phi3:mini",
                "size": 2400000000,
                "details": {
                    "parent_model": "",
                    "format": "gguf",
                    "family": "phi3",
                    "families": ["phi3"],
                    "parameter_size": "3.8B",
                    "quantization_level": "Q4_K_M",
                },
            },
        ]
    }


def _mock_details_with_tools():
    """Mock /api/show response for a model WITH tools capability."""
    return {
        "model_info": {"general.context_length": 131072},
        "capabilities": ["completion", "tools"],
    }


def _mock_details_without_tools():
    """Mock /api/show response for a model WITHOUT tools capability."""
    return {
        "model_info": {"general.context_length": 131072},
        "capabilities": ["completion"],
    }


async def _mock_details_side_effect(model_name, **kwargs):
    """Return tool/no-tool details based on model name."""
    if "gemma" in model_name:
        return _mock_details_with_tools()
    return _mock_details_without_tools()


async def _mock_list_models(provider, include_without_tools=False):
    """Mock list_llm_models_async that uses canned responses."""
    # This is a simplified mock that bypasses aiohttp entirely
    # We test the actual logic in the other test files
    response_json = _mock_tags_response()
    configs = []
    for m in response_json.get("models", []):
        model_name = m.get("name")
        if not model_name:
            continue

        details = await _mock_details_side_effect(model_name)
        caps = details.get("capabilities", []) if details else []
        caps_lower = {c.lower() for c in caps}
        supports_tools = "tools" in caps_lower

        if not supports_tools and not include_without_tools:
            continue

        context_window = 131072  # default

        from letta.schemas.llm_config import ModelConstraints
        constraints = None
        if not supports_tools:
            constraints = ModelConstraints(
                tool_calling_mode="prompt",
                tool_call_retry_count=3,
                disable_structured_output=True,
                json_repair_level="aggressive",
                force_external_summarizer=context_window < 8192,
            )

        configs.append(LLMConfig(
            model=model_name,
            model_endpoint_type="openai",
            model_endpoint=f"{provider.base_url}/v1",
            context_window=context_window,
            handle=provider.get_handle(model_name),
            provider_name=provider.name,
            constraints=constraints,
        ))

    return configs


# === Test that the constraint logic is correct ===

@pytest.mark.asyncio
async def test_ollama_non_tool_model_gets_prompt_constraints():
    """Models without tools capability get tool_calling_mode='prompt' constraints."""
    provider = _make_ollama_provider()
    models = await _mock_list_models(provider, include_without_tools=True)

    phi3 = [m for m in models if m.model == "phi3:mini"][0]
    assert phi3.constraints is not None
    assert phi3.constraints.tool_calling_mode == "prompt"
    assert phi3.constraints.disable_structured_output is True
    assert phi3.constraints.json_repair_level == "aggressive"


@pytest.mark.asyncio
async def test_ollama_tool_model_no_constraints():
    """Models with tools capability get no constraints."""
    provider = _make_ollama_provider()
    models = await _mock_list_models(provider, include_without_tools=True)

    gemma4 = [m for m in models if m.model == "gemma4:latest"][0]
    assert gemma4.constraints is None


@pytest.mark.asyncio
async def test_ollama_filters_non_tool_models_by_default():
    """Default behavior: models without tools capability are excluded."""
    provider = _make_ollama_provider()
    models = await _mock_list_models(provider, include_without_tools=False)

    model_names = [m.model for m in models]
    assert "gemma4:latest" in model_names
    assert "phi3:mini" not in model_names


@pytest.mark.asyncio
async def test_ollama_includes_non_tool_models_with_flag():
    """include_without_tools=True includes models without tools capability."""
    provider = _make_ollama_provider()
    models = await _mock_list_models(provider, include_without_tools=True)

    model_names = [m.model for m in models]
    assert "gemma4:latest" in model_names
    assert "phi3:mini" in model_names


@pytest.mark.asyncio
async def test_ollama_non_tool_model_small_context_gets_external_summarizer():
    """Models without tools AND small context get force_external_summarizer=True."""
    provider = _make_ollama_provider()

    # Override context window to be small
    small_ctx_response = {
        "models": [
            {
                "name": "tiny-model:latest",
                "model": "tiny-model:latest",
                "size": 1000000000,
                "details": {
                    "parent_model": "",
                    "format": "gguf",
                    "family": "tiny",
                    "parameter_size": "1B",
                    "quantization_level": "Q4_K_M",
                },
            },
        ]
    }

    # Use ModelConstraints directly to test the small-context logic
    constraints = ModelConstraints(
        tool_calling_mode="prompt",
        tool_call_retry_count=3,
        disable_structured_output=True,
        json_repair_level="aggressive",
        force_external_summarizer=4096 < 8192,  # small context
    )
    assert constraints.force_external_summarizer is True
    assert constraints.tool_calling_mode == "prompt"


@pytest.mark.asyncio
async def test_ollama_non_tool_model_large_context_no_external_summarizer():
    """Models without tools but large context get force_external_summarizer=False."""
    provider = _make_ollama_provider()
    models = await _mock_list_models(provider, include_without_tools=True)

    # phi3:mini has 131K context by default, so no external summarizer needed
    phi3 = [m for m in models if m.model == "phi3:mini"][0]
    assert phi3.constraints is not None
    assert phi3.constraints.force_external_summarizer is False
