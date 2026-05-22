"""Runtime capability probe for local model tool-calling support.

When tool_calling_mode="auto" (the default for local providers), this module
detects whether the model supports native OpenAI-style tool calling. The result
is cached in-memory so the probe only runs once per (endpoint, model) pair per
process lifetime.

Probe strategies:
  - Ollama: Query /api/show for the model's capabilities array. If "tools"
    is present, the model supports native tool calling. Zero inference cost.
  - Generic (vLLM, SGLang, LocalAI, etc.): Send a minimal test request with
    one tool definition. If the response contains a native tool_calls field,
    the model supports it. Otherwise, fall back to prompt-based tool calling.

Both sync and async probe methods are provided. The async versions use httpx
and should be preferred in the agent loop.
"""

import asyncio
import json
import threading
from typing import Optional

import httpx
import requests

from letta.log import get_logger
from letta.schemas.llm_config import LLMConfig

logger = get_logger(__name__)

# Minimal tool definition for the generic probe
_PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "echo",
        "description": "Echo the input text back.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The text to echo back.",
                }
            },
            "required": ["text"],
        },
    },
}


class ToolCapabilityCache:
    """Thread-safe in-memory cache for tool-calling capability detection.

    Key format: "{model_endpoint}::{model}"
    Value: True = native tool calling supported, False = prompt mode needed
    """

    _instance: Optional["ToolCapabilityCache"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._cache: dict[str, bool] = {}
        self._probing: set[str] = set()  # prevent concurrent probes for same key

    @classmethod
    def instance(cls) -> "ToolCapabilityCache":
        """Get the singleton cache instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton (for testing)."""
        with cls._lock:
            cls._instance = None

    def _cache_key(self, llm_config: LLMConfig) -> str:
        return f"{llm_config.model_endpoint}::{llm_config.model}"

    def get_cached(self, llm_config: LLMConfig) -> Optional[bool]:
        """Return cached capability, or None if not yet probed."""
        return self._cache.get(self._cache_key(llm_config))

    def set_cached(self, llm_config: LLMConfig, supports_native: bool) -> None:
        """Manually set the cached capability (for testing or pre-seeding)."""
        self._cache[self._cache_key(llm_config)] = supports_native

    def probe(self, llm_config: LLMConfig) -> bool:
        """Probe whether the model supports native tool calling.

        Returns True if native tool calling is supported, False if prompt-based
        tool calling should be used. Results are cached after the first probe.
        """
        key = self._cache_key(llm_config)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        # Prevent concurrent probes for the same key
        with self._lock:
            if key in self._probing:
                # Another thread is probing — assume prompt mode for safety
                return False
            self._probing.add(key)

        try:
            result = self._do_probe(llm_config)
            self._cache[key] = result
            return result
        finally:
            with self._lock:
                self._probing.discard(key)

    def _do_probe(self, llm_config: LLMConfig) -> bool:
        """Perform the actual probe based on provider type."""
        endpoint_type = (llm_config.model_endpoint_type or "").lower()

        # Ollama: use /api/show to check capabilities (zero inference cost)
        if endpoint_type == "ollama":
            return self._probe_ollama(llm_config)

        # Generic: send a test request with a tool
        return self._probe_generic(llm_config)

    def _probe_ollama(self, llm_config: LLMConfig) -> bool:
        """Query Ollama's /api/show endpoint for model capabilities.

        Ollama (since PR #10066) returns a `capabilities` array. If "tools"
        is in the array, the model supports native tool calling.
        """
        # Derive the native Ollama API URL from the OpenAI-compatible endpoint
        # e.g. http://localhost:11434/v1 -> http://localhost:11434
        base_url = llm_config.model_endpoint or ""
        if base_url.endswith("/v1") or base_url.endswith("/v1/"):
            ollama_api_url = base_url.rstrip("/").removesuffix("/v1")
        else:
            ollama_api_url = base_url.rstrip("/")

        show_url = f"{ollama_api_url}/api/show"
        payload = {"name": llm_config.model, "verbose": False}

        try:
            resp = requests.post(show_url, json=payload, timeout=10)
            if resp.status_code != 200:
                logger.warning(
                    f"Ollama capability probe failed for {llm_config.model}: "
                    f"HTTP {resp.status_code}"
                )
                return False

            data = resp.json()
            capabilities = data.get("capabilities", [])

            if "tools" in capabilities:
                logger.info(
                    f"Ollama model {llm_config.model} supports native tool calling "
                    f"(capabilities: {capabilities})"
                )
                return True
            else:
                logger.info(
                    f"Ollama model {llm_config.model} does NOT support native tool calling "
                    f"(capabilities: {capabilities})"
                )
                return False

        except requests.RequestException as e:
            logger.warning(
                f"Ollama capability probe error for {llm_config.model}: {e}"
            )
            return False

    def _probe_generic(self, llm_config: LLMConfig) -> bool:
        """Send a minimal test request with one tool to check native support.

        If the response contains a tool_calls field with at least one entry,
        the model supports native tool calling. If the response is text-only
        or returns an error about tools, it doesn't.

        Some models are non-deterministic — they may produce native tool calls
        on some requests but not others (e.g., Gemma 4 on vLLM). We run the
        probe up to 2 times. If ANY attempt produces a native tool call, we
        report True. The agent loop's retry mechanism handles occasional
        failures.
        """
        # Try up to 2 times — models like Gemma 4 on vLLM are non-deterministic
        any_success = False
        for attempt in range(2):
            result = self._probe_generic_once(llm_config, attempt=attempt)
            if result:
                any_success = True
                break
            # If the first attempt failed, try once more — the model
            # might be non-deterministic about tool calling format.

        if not any_success:
            logger.info(
                f"Model {llm_config.model} does NOT support native tool calling "
                f"(both probe attempts returned no tool calls)"
            )
        return any_success

    def _probe_generic_once(self, llm_config: LLMConfig, attempt: int = 0) -> bool:
        """Single attempt at the generic probe."""
        endpoint = llm_config.model_endpoint or ""
        url = f"{endpoint.rstrip('/')}/chat/completions"

        payload = {
            "model": llm_config.model,
            "messages": [
                {"role": "user", "content": "Call the echo tool with text 'test'."}
            ],
            "tools": [_PROBE_TOOL],
            "max_tokens": 50,
            "stream": False,
        }

        headers = {"Content-Type": "application/json"}
        if llm_config.model_endpoint:
            # Some providers require an API key even if it's just "ollama"
            pass

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=30)

            # If the provider returns 400 with "does not support tools", it's clear
            if resp.status_code == 400:
                error_msg = resp.text.lower()
                if "does not support tools" in error_msg or "tool" in error_msg:
                    logger.info(
                        f"Provider {endpoint} rejected tools for {llm_config.model}: "
                        f"falling back to prompt-based tool calling"
                    )
                    return False

            if resp.status_code != 200:
                logger.warning(
                    f"Generic capability probe failed for {llm_config.model}: "
                    f"HTTP {resp.status_code}"
                )
                return False

            data = resp.json()
            choices = data.get("choices", [])
            if not choices:
                return False

            message = choices[0].get("message", {})
            tool_calls = message.get("tool_calls", [])

            if tool_calls:
                logger.info(
                    f"Model {llm_config.model} supports native tool calling "
                    f"(probe attempt {attempt+1} returned {len(tool_calls)} tool call(s))"
                )
                return True
            else:
                logger.info(
                    f"Model {llm_config.model} probe attempt {attempt+1}: "
                    f"no native tool calls in response"
                )
                return False

        except requests.RequestException as e:
            logger.warning(
                f"Generic capability probe error for {llm_config.model}: {e}"
            )
            return False

    # -------------------------------------------------------
    # Async probe methods (use httpx, non-blocking)
    # -------------------------------------------------------

    async def probe_async(self, llm_config: LLMConfig) -> bool:
        """Async version of probe(). Uses httpx instead of requests.

        Returns True if native tool calling is supported, False if prompt-based
        tool calling should be used. Results are cached after the first probe.
        """
        key = self._cache_key(llm_config)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        with self._lock:
            if key in self._probing:
                return False
            self._probing.add(key)

        try:
            result = await self._do_probe_async(llm_config)
            self._cache[key] = result
            return result
        finally:
            with self._lock:
                self._probing.discard(key)

    async def _do_probe_async(self, llm_config: LLMConfig) -> bool:
        """Perform the actual async probe based on provider type."""
        endpoint_type = (llm_config.model_endpoint_type or "").lower()

        if endpoint_type == "ollama":
            return await self._probe_ollama_async(llm_config)

        return await self._probe_generic_async(llm_config)

    async def _probe_ollama_async(self, llm_config: LLMConfig) -> bool:
        """Async version of _probe_ollama using httpx."""
        base_url = llm_config.model_endpoint or ""
        if base_url.endswith("/v1") or base_url.endswith("/v1/"):
            ollama_api_url = base_url.rstrip("/").removesuffix("/v1")
        else:
            ollama_api_url = base_url.rstrip("/")

        show_url = f"{ollama_api_url}/api/show"
        payload = {"name": llm_config.model, "verbose": False}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(show_url, json=payload)

            if resp.status_code != 200:
                logger.warning(
                    f"Ollama capability probe failed for {llm_config.model}: "
                    f"HTTP {resp.status_code}"
                )
                return False

            data = resp.json()
            capabilities = data.get("capabilities", [])

            if "tools" in capabilities:
                logger.info(
                    f"Ollama model {llm_config.model} supports native tool calling "
                    f"(capabilities: {capabilities})"
                )
                return True
            else:
                logger.info(
                    f"Ollama model {llm_config.model} does NOT support native tool calling "
                    f"(capabilities: {capabilities})"
                )
                return False

        except (httpx.HTTPError, Exception) as e:
            logger.warning(
                f"Ollama capability probe error for {llm_config.model}: {e}"
            )
            return False

    async def _probe_generic_async(self, llm_config: LLMConfig) -> bool:
        """Async version of _probe_generic using httpx.

        Tries up to 2 times for non-deterministic models.
        """
        any_success = False
        for attempt in range(2):
            result = await self._probe_generic_once_async(llm_config, attempt=attempt)
            if result:
                any_success = True
                break

        if not any_success:
            logger.info(
                f"Model {llm_config.model} does NOT support native tool calling "
                f"(both probe attempts returned no tool calls)"
            )
        return any_success

    async def _probe_generic_once_async(self, llm_config: LLMConfig, attempt: int = 0) -> bool:
        """Single async attempt at the generic probe."""
        endpoint = llm_config.model_endpoint or ""
        url = f"{endpoint.rstrip('/')}/chat/completions"

        payload = {
            "model": llm_config.model,
            "messages": [
                {"role": "user", "content": "Call the echo tool with text 'test'."}
            ],
            "tools": [_PROBE_TOOL],
            "max_tokens": 50,
            "stream": False,
        }

        headers = {"Content-Type": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, json=payload, headers=headers)

            if resp.status_code == 400:
                error_msg = resp.text.lower()
                if "does not support tools" in error_msg or "tool" in error_msg:
                    logger.info(
                        f"Provider {endpoint} rejected tools for {llm_config.model}: "
                        f"falling back to prompt-based tool calling"
                    )
                    return False

            if resp.status_code != 200:
                logger.warning(
                    f"Generic capability probe failed for {llm_config.model}: "
                    f"HTTP {resp.status_code}"
                )
                return False

            data = resp.json()
            choices = data.get("choices", [])
            if not choices:
                return False

            message = choices[0].get("message", {})
            tool_calls = message.get("tool_calls", [])

            if tool_calls:
                logger.info(
                    f"Model {llm_config.model} supports native tool calling "
                    f"(probe attempt {attempt+1} returned {len(tool_calls)} tool call(s))"
                )
                return True
            else:
                logger.info(
                    f"Model {llm_config.model} probe attempt {attempt+1}: "
                    f"no native tool calls in response"
                )
                return False

        except (httpx.HTTPError, Exception) as e:
            logger.warning(
                f"Generic capability probe error for {llm_config.model}: {e}"
            )
            return False


def resolve_tool_calling_mode(llm_config: LLMConfig) -> str:
    """Resolve tool_calling_mode="auto" to "native" or "prompt" (sync).

    If the mode is "native" or "prompt", return it unchanged.
    If the mode is "auto", probe the model's capability and return the result.
    The probe result is cached, so this is O(1) after the first call.

    For async callers, use resolve_tool_calling_mode_async() instead.

    Returns:
        "native" or "prompt"
    """
    # Check pre-resolved value first
    if llm_config.resolved_tool_calling_mode is not None:
        return llm_config.resolved_tool_calling_mode

    if llm_config.constraints is None:
        return "native"  # default: native mode

    mode = llm_config.constraints.tool_calling_mode
    if mode in ("native", "prompt"):
        return mode

    if mode == "auto":
        cache = ToolCapabilityCache.instance()
        supports_native = cache.probe(llm_config)
        resolved = "native" if supports_native else "prompt"
        logger.info(
            f"Resolved tool_calling_mode='auto' to '{resolved}' for "
            f"{llm_config.model} on {llm_config.model_endpoint_type}"
        )
        # Store on config for downstream callers
        llm_config.resolved_tool_calling_mode = resolved
        # Relax defensive constraints now that we know the model's capability
        if llm_config.constraints is not None:
            llm_config.constraints.relax_constraints_after_probe(resolved)
        return resolved

    # Unknown mode — default to native
    logger.warning(f"Unknown tool_calling_mode '{mode}', defaulting to 'native'")
    return "native"


async def resolve_tool_calling_mode_async(llm_config: LLMConfig) -> str:
    """Resolve tool_calling_mode="auto" to "native" or "prompt" (async).

    Preferred over the sync version in async contexts (agent loop, etc.).
    Uses httpx for non-blocking HTTP calls. Stores the resolved mode on
    llm_config.resolved_tool_calling_mode for downstream code to read.

    Returns:
        "native" or "prompt"
    """
    # Check pre-resolved value first
    if llm_config.resolved_tool_calling_mode is not None:
        return llm_config.resolved_tool_calling_mode

    if llm_config.constraints is None:
        return "native"

    mode = llm_config.constraints.tool_calling_mode
    if mode in ("native", "prompt"):
        llm_config.resolved_tool_calling_mode = mode
        return mode

    if mode == "auto":
        cache = ToolCapabilityCache.instance()
        supports_native = await cache.probe_async(llm_config)
        resolved = "native" if supports_native else "prompt"
        logger.info(
            f"Resolved tool_calling_mode='auto' to '{resolved}' for "
            f"{llm_config.model} on {llm_config.model_endpoint_type}"
        )
        llm_config.resolved_tool_calling_mode = resolved
        # Relax defensive constraints now that we know the model's capability
        if llm_config.constraints is not None:
            llm_config.constraints.relax_constraints_after_probe(resolved)
        return resolved

    logger.warning(f"Unknown tool_calling_mode '{mode}', defaulting to 'native'")
    return "native"
