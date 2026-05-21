"""
BitNet provider (placeholder).

BitNet b1.58 models use 1.58-bit ternary weights {-1, 0, +1}, enabling
CPU-only inference with dramatic memory and energy savings. However, the
current bitnet.cpp inference framework does not support tool calling or
a built-in OpenAI-compatible API server, which are required for Letta's
agent loop.

This provider is a placeholder that will be activated when upstream support
lands. Track progress:
  - bitnet.cpp API server: community FastAPI wrapper exists (hamzasgd/BitNet)
    but lacks /v1/models and tools support
  - bitnet.cpp tool calling: not supported, would use prompt-based mode
  - Ollama BitNet support: BLOCKED — GGUF uses unsupported quantization type 36.
    Ollama maintainers confirmed "BitNet is not a supported architecture" and
    rejected PR #11218 adding a BitNet runner
  - vLLM 1-bit support: PR #17588 (BitBLAS backend) is stale/closed, needs rebase.
    When merged, vLLM + BitBLAS would serve BitNet models with OpenAI API

For now, use the openai_compatible provider type to point at any
OpenAI-compatible server that serves BitNet models. The recommended setup:
  1. Run bitnet.cpp + community FastAPI wrapper (hamzasgd/BitNet)
  2. Point openai_compatible provider at the wrapper
  3. Use prompt-based tool calling (tool_calling_mode="prompt")
"""

from typing import Literal

from pydantic import Field

from letta.errors import LettaInvalidArgumentError
from letta.log import get_logger
from letta.schemas.enums import ProviderCategory, ProviderType
from letta.schemas.llm_config import LLMConfig
from letta.schemas.providers.base import Provider

logger = get_logger(__name__)


class BitNetProvider(Provider):
    """BitNet 1.58-bit model provider.

    Placeholder — bitnet.cpp does not yet expose an OpenAI-compatible API
    with tool calling support. Use openai_compatible provider type instead
    when serving BitNet models through vLLM or another OpenAI-compatible
    server that supports 1-bit architectures.
    """

    provider_type: Literal[ProviderType.bitnet] = Field(
        ProviderType.bitnet, description="The type of the provider."
    )
    provider_category: ProviderCategory = Field(
        ProviderCategory.base, description="The category of the provider (base or byok)."
    )
    base_url: str = Field(
        ..., description="Base URL for the BitNet server (when available)."
    )
    api_key: str | None = Field(None, description="API key (optional).")

    async def list_llm_models_async(self) -> list[LLMConfig]:
        logger.warning(
            "BitNet provider is not yet functional. bitnet.cpp does not expose "
            "an OpenAI-compatible API with tool calling. Use the 'openai_compatible' "
            "provider type instead when serving BitNet models through vLLM or "
            "another compatible server. "
            "Track: https://github.com/microsoft/BitNet/issues/257"
        )
        return []
