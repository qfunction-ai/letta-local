"""
MLX-LM provider.

Apple's MLX framework for Apple Silicon. MLX-LM provides a Python server
with OpenAI-compatible /v1/chat/completions endpoint. Optimized for unified
memory architecture on M1+ chips.

Usage:
    # Start MLX-LM server:
    python -m mlx_lm.server --model mlx-community/phi-4-mini --port 8080

    curl -X POST http://localhost:8283/v1/providers \\
      -H "Content-Type: application/json" \\
      -d '{
        "name": "MLX",
        "provider_type": "mlx",
        "api_key": "placeholder",
        "base_url": "http://localhost:8080"
      }'
"""

from typing import Literal

from pydantic import Field

from letta.schemas.enums import ProviderCategory, ProviderType
from letta.schemas.providers.openai_compatible import OpenAICompatibleProvider


class MLXProvider(OpenAICompatibleProvider):
    """MLX-LM provider — Apple Silicon optimized inference via MLX framework."""

    provider_type: Literal[ProviderType.mlx] = Field(
        ProviderType.mlx, description="The type of the provider."
    )
    provider_category: ProviderCategory = Field(
        ProviderCategory.base, description="The category of the provider (base or byok)."
    )
    base_url: str = Field(
        ..., description="Base URL for the MLX-LM server (e.g., http://localhost:8080)."
    )
