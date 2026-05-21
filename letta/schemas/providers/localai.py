"""
LocalAI provider.

LocalAI is a drop-in OpenAI API replacement that runs locally.
Supports 35+ backends (llama.cpp, vLLM, transformers, etc.).
Exposes /v1/chat/completions and /v1/models.

Usage:
    curl -X POST http://localhost:8283/v1/providers \\
      -H "Content-Type: application/json" \\
      -d '{
        "name": "LocalAI",
        "provider_type": "localai",
        "api_key": "local",
        "base_url": "http://localhost:8080"
      }'
"""

from typing import Literal

from pydantic import Field

from letta.schemas.enums import ProviderCategory, ProviderType
from letta.schemas.providers.openai_compatible import OpenAICompatibleProvider


class LocalAIProvider(OpenAICompatibleProvider):
    """LocalAI provider — self-hosted OpenAI-compatible gateway with 35+ backends."""

    provider_type: Literal[ProviderType.localai] = Field(
        ProviderType.localai, description="The type of the provider."
    )
    provider_category: ProviderCategory = Field(
        ProviderCategory.base, description="The category of the provider (base or byok)."
    )
    base_url: str = Field(
        ..., description="Base URL for the LocalAI API (e.g., http://localhost:8080)."
    )
