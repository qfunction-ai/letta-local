"""
llama.cpp server provider.

llama.cpp ships llama-server, a lightweight HTTP server with OpenAI-compatible
/v1/chat/completions and /v1/models endpoints. Supports GGUF quantized models,
tool calling, and GBNF grammars for constrained generation.

Usage:
    # Start llama-server:
    ./llama-server -m model.gguf --port 8080

    curl -X POST http://localhost:8283/v1/providers \\
      -H "Content-Type: application/json" \\
      -d '{
        "name": "llama.cpp",
        "provider_type": "llamacpp",
        "api_key": "placeholder",
        "base_url": "http://localhost:8080"
      }'
"""

from typing import Literal

from pydantic import Field

from letta.schemas.enums import ProviderCategory, ProviderType
from letta.schemas.providers.openai_compatible import OpenAICompatibleProvider


class LlamaCppProvider(OpenAICompatibleProvider):
    """llama.cpp server provider — lightweight OpenAI-compatible server for GGUF models."""

    provider_type: Literal[ProviderType.llamacpp] = Field(
        ProviderType.llamacpp, description="The type of the provider."
    )
    provider_category: ProviderCategory = Field(
        ProviderCategory.base, description="The category of the provider (base or byok)."
    )
    base_url: str = Field(
        ..., description="Base URL for the llama.cpp server (e.g., http://localhost:8080)."
    )
