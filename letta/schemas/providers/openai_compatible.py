"""
Generic OpenAI-compatible provider.

Points at any server that exposes /v1/chat/completions and /v1/models.
Works with LocalAI, llama.cpp server (llama-server), llamafile, MLX-LM server,
and any other OpenAI-compatible inference engine without requiring a dedicated
provider type.

Usage:
    curl -X POST http://localhost:8283/v1/providers \\
      -H "Content-Type: application/json" \\
      -d '{
        "name": "My Local Server",
        "provider_type": "openai_compatible",
        "api_key": "placeholder",
        "base_url": "http://localhost:8080/v1"
      }'
"""

from typing import Literal, Optional

from pydantic import Field

from letta.constants import DEFAULT_CONTEXT_WINDOW
from letta.log import get_logger
from letta.schemas.embedding_config import EmbeddingConfig
from letta.schemas.enums import ProviderCategory, ProviderType
from letta.schemas.llm_config import LLMConfig
from letta.schemas.providers.base import Provider

logger = get_logger(__name__)


class OpenAICompatibleProvider(Provider):
    """Generic provider for any OpenAI-compatible /v1 endpoint.

    Auto-discovers models from /v1/models. Works with LocalAI, llama.cpp
    server, llamafile, MLX-LM server, and any engine that exposes the
    standard OpenAI chat completions API.
    """

    provider_type: Literal[ProviderType.openai_compatible] = Field(
        ProviderType.openai_compatible, description="The type of the provider."
    )
    provider_category: ProviderCategory = Field(
        ProviderCategory.base, description="The category of the provider (base or byok)."
    )
    base_url: str = Field(..., description="Base URL for the OpenAI-compatible API (e.g., http://localhost:8080/v1).")
    api_key: str | None = Field(None, description="API key (optional for local servers).")
    default_context_window: int = Field(
        DEFAULT_CONTEXT_WINDOW,
        description="Default context window when the server does not report max_model_len.",
    )
    handle_base: str | None = Field(
        None,
        description="Custom handle base name for model handles (e.g., 'localai' instead of provider name).",
    )

    async def list_llm_models_async(self) -> list[LLMConfig]:
        from letta.llm_api.openai import openai_get_model_list_async

        base_url = self.base_url.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url = base_url + "/v1"

        try:
            response = await openai_get_model_list_async(base_url, api_key=self.api_key)
        except Exception as e:
            logger.error(f"Failed to list models from {base_url}: {e}")
            return []

        data = response.get("data", response) if isinstance(response, dict) else response
        if not isinstance(data, list):
            logger.warning(f"Unexpected /v1/models response format from {base_url}: {type(data)}")
            return []

        configs = []
        for model in data:
            if not isinstance(model, dict) or "id" not in model:
                continue

            model_name = model["id"]

            # Context window: use max_model_len if reported, else default
            context_window = model.get("max_model_len")
            if context_window is not None:
                try:
                    context_window = int(context_window)
                except (ValueError, TypeError):
                    context_window = self.default_context_window
            else:
                context_window = self.default_context_window

            configs.append(
                LLMConfig(
                    model=model_name,
                    model_endpoint_type="openai",
                    model_endpoint=base_url,
                    context_window=context_window,
                    handle=self.get_handle(model_name, base_name=self.handle_base)
                    if self.handle_base
                    else self.get_handle(model_name),
                    max_tokens=self.get_default_max_output_tokens(model_name),
                    provider_name=self.name,
                    provider_category=self.provider_category,
                )
            )

        if not configs:
            logger.warning(f"No models discovered at {base_url}/v1/models. The server may not be running or may not support the /v1/models endpoint.")

        return configs

    async def list_embedding_models_async(self) -> list[EmbeddingConfig]:
        # Most local inference servers do not serve embedding models.
        # Users who need local embeddings should configure a dedicated
        # embedding provider (e.g., Ollama with an embedding model).
        return []
