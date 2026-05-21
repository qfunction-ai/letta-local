"""
Llamafile provider.

Llamafile packages LLM runtime + model weights into a single executable.
Built on llama.cpp + Cosmopolitan Libc. Exposes OpenAI-compatible
/v1/chat/completions on port 8080.

Usage:
    # Start a llamafile:
    ./model.llamafile

    curl -X POST http://localhost:8283/v1/providers \\
      -H "Content-Type: application/json" \\
      -d '{
        "name": "Llamafile",
        "provider_type": "llamafile",
        "api_key": "placeholder",
        "base_url": "http://localhost:8080"
      }'
"""

from typing import Literal

from pydantic import Field

from letta.schemas.enums import ProviderCategory, ProviderType
from letta.schemas.providers.openai_compatible import OpenAICompatibleProvider


class LlamafileProvider(OpenAICompatibleProvider):
    """Llamafile provider — single-file distributable LLM with OpenAI-compatible API."""

    provider_type: Literal[ProviderType.llamafile] = Field(
        ProviderType.llamafile, description="The type of the provider."
    )
    provider_category: ProviderCategory = Field(
        ProviderCategory.base, description="The category of the provider (base or byok)."
    )
    base_url: str = Field(
        ..., description="Base URL for the Llamafile server (e.g., http://localhost:8080)."
    )
