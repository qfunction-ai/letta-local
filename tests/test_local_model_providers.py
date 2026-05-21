"""Tests for local model provider classes: openai_compatible, localai, llamacpp,
llamafile, mlx, bitnet — and the cast_to_subtype routing."""

import pytest

from letta.schemas.enums import ProviderType, ProviderCategory
from letta.schemas.providers.base import Provider
from letta.schemas.providers.openai_compatible import OpenAICompatibleProvider
from letta.schemas.providers.localai import LocalAIProvider
from letta.schemas.providers.llamacpp import LlamaCppProvider
from letta.schemas.providers.llamafile import LlamafileProvider
from letta.schemas.providers.mlx import MLXProvider
from letta.schemas.providers.bitnet import BitNetProvider


# === ProviderType enum ===

@pytest.mark.parametrize("ptype", ["localai", "llamacpp", "llamafile", "mlx", "openai_compatible", "bitnet"])
def test_new_provider_type_values_exist(ptype):
    """All 6 new ProviderType enum values are defined."""
    assert hasattr(ProviderType, ptype), f"ProviderType.{ptype} missing"


def test_all_new_provider_types_are_unique():
    """No duplicate values in the new ProviderType entries."""
    new_types = [
        ProviderType.localai,
        ProviderType.llamacpp,
        ProviderType.llamafile,
        ProviderType.mlx,
        ProviderType.openai_compatible,
        ProviderType.bitnet,
    ]
    values = [t.value for t in new_types]
    assert len(values) == len(set(values)), "Duplicate ProviderType values"


# === cast_to_subtype routing ===

@pytest.mark.parametrize("ptype,expected_cls", [
    (ProviderType.openai_compatible, OpenAICompatibleProvider),
    (ProviderType.localai, LocalAIProvider),
    (ProviderType.llamacpp, LlamaCppProvider),
    (ProviderType.llamafile, LlamafileProvider),
    (ProviderType.mlx, MLXProvider),
    (ProviderType.bitnet, BitNetProvider),
])
def test_cast_to_subtype(ptype, expected_cls):
    """cast_to_subtype returns the correct provider subclass."""
    p = Provider(
        name="test",
        provider_type=ptype,
        provider_category=ProviderCategory.base,
        base_url="http://localhost:8080",
    )
    sub = p.cast_to_subtype()
    assert isinstance(sub, expected_cls), f"Expected {expected_cls.__name__}, got {type(sub).__name__}"


# === Provider instantiation ===

def test_openai_compatible_provider_creation():
    """OpenAICompatibleProvider can be instantiated with base_url."""
    provider = OpenAICompatibleProvider(
        name="test",
        provider_type=ProviderType.openai_compatible,
        provider_category=ProviderCategory.base,
        base_url="http://localhost:9000",
    )
    assert provider.name == "test"
    assert provider.base_url == "http://localhost:9000"


def test_localai_provider_inherits_openai_compatible():
    """LocalAIProvider inherits from OpenAICompatibleProvider."""
    assert issubclass(LocalAIProvider, OpenAICompatibleProvider)


def test_llamacpp_provider_inherits_openai_compatible():
    """LlamaCppProvider inherits from OpenAICompatibleProvider."""
    assert issubclass(LlamaCppProvider, OpenAICompatibleProvider)


def test_llamafile_provider_inherits_openai_compatible():
    """LlamafileProvider inherits from OpenAICompatibleProvider."""
    assert issubclass(LlamafileProvider, OpenAICompatibleProvider)


def test_mlx_provider_inherits_openai_compatible():
    """MLXProvider inherits from OpenAICompatibleProvider."""
    assert issubclass(MLXProvider, OpenAICompatibleProvider)


# === BitNet placeholder ===

@pytest.mark.asyncio
async def test_bitnet_list_models_returns_empty():
    """BitNetProvider.list_llm_models_async() returns empty list (placeholder)."""
    provider = BitNetProvider(
        name="test",
        provider_type=ProviderType.bitnet,
        provider_category=ProviderCategory.base,
        base_url="http://localhost:8080",
    )
    models = await provider.list_llm_models_async()
    assert models == []


# === Provider imports ===

def test_all_new_providers_importable_from_providers_package():
    """All new provider classes are importable from letta.schemas.providers."""
    from letta.schemas.providers import (
        OpenAICompatibleProvider,
        LocalAIProvider,
        LlamaCppProvider,
        LlamafileProvider,
        MLXProvider,
        BitNetProvider,
    )
    # Just verify they're importable
    assert OpenAICompatibleProvider is not None
    assert LocalAIProvider is not None
    assert LlamaCppProvider is not None
    assert LlamafileProvider is not None
    assert MLXProvider is not None
    assert BitNetProvider is not None
