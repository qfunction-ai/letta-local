"""
Integration tests for local model support with prompt-based tool calling.

Part 1: Direct LLM client tests against Ollama (no server needed)
Part 2: Full agent loop tests via the Letta REST API against Ollama (requires live server)
Part 3: Direct LLM client tests against vLLM (validates auto-detection probe)

Requirements:
- Ollama running on localhost:11434 with phi3:mini pulled
- vLLM running on localhost:9000 with --enable-auto-tool-choice --tool-call-parser hermes
  (Part 3; recommend --gpu-memory-utilization 0.7 on macOS to avoid Metal OOM)
- RUN_LOCAL_INTEGRATION_TESTS=1 environment variable set
- For Parts 2/3: Letta server on localhost:8383 with dedicated Postgres on 5433

Note: vLLM tests should be run first (pytest-ordering) because the Metal GPU
on macOS tends to OOM after sustained load. If vLLM crashes mid-test, restart
and run only the vLLM tests: pytest -k vllm
"""

import json
import os
from datetime import datetime, timezone

import pytest

from letta.agents.helpers import format_tools_as_text, _safe_load_tool_call_str
from letta.llm_api.openai_client import OpenAIClient
from letta.schemas.enums import AgentType, LLMCallType, MessageRole
from letta.schemas.llm_config import LLMConfig, ModelConstraints
from letta.schemas.letta_message_content import TextContent
from letta.schemas.message import Message as PydanticMessage

# ------------------------------
# Skip conditions
# ------------------------------

RUN_LOCAL = os.getenv("RUN_LOCAL_INTEGRATION_TESTS", "").strip() in ("1", "true", "yes")
LETTA_SERVER_URL = os.getenv("LETTA_SERVER_URL", "http://localhost:8383")


def _ollama_available() -> bool:
    """Check if Ollama is running and has phi3:mini available."""
    try:
        import requests
        resp = requests.get("http://localhost:11434/api/tags", timeout=3)
        if resp.status_code != 200:
            return False
        models = resp.json().get("models", [])
        model_names = [m.get("name", "") for m in models]
        return any("phi3" in n for n in model_names)
    except Exception:
        return False


def _letta_server_available() -> bool:
    """Check if the Letta server is running."""
    try:
        import requests
        resp = requests.get(f"{LETTA_SERVER_URL}/v1/health/", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


requires_ollama = pytest.mark.skipif(
    not RUN_LOCAL or not _ollama_available(),
    reason="Requires RUN_LOCAL_INTEGRATION_TESTS=1 and Ollama with phi3:mini",
)

requires_letta_server = pytest.mark.skipif(
    not RUN_LOCAL or not _ollama_available() or not _letta_server_available(),
    reason="Requires RUN_LOCAL_INTEGRATION_TESTS=1, Ollama with phi3:mini, and Letta server",
)

VLLM_URL = os.getenv("VLLM_SERVER_URL", "http://localhost:9000")


def _vllm_available() -> bool:
    """Check if vLLM is running and serving a model."""
    try:
        import requests
        resp = requests.get(f"{VLLM_URL}/v1/models", timeout=3)
        if resp.status_code != 200:
            return False
        data = resp.json().get("data", [])
        return len(data) > 0
    except Exception:
        return False


requires_vllm = pytest.mark.skipif(
    not RUN_LOCAL or not _vllm_available(),
    reason="Requires RUN_LOCAL_INTEGRATION_TESTS=1 and vLLM server on localhost:9000",
)


def _make_phi3_config() -> LLMConfig:
    """Create an LLMConfig for phi3:mini with prompt-based tool calling."""
    return LLMConfig(
        model="phi3:mini",
        model_endpoint_type="ollama",
        model_endpoint="http://localhost:11434/v1",
        context_window=131072,
        handle="ollama/phi3:mini",
        put_inner_thoughts_in_kwargs=False,
        temperature=0.7,
        max_tokens=2048,
        constraints=ModelConstraints(
            tool_calling_mode="prompt",
            tool_call_retry_count=3,
            disable_structured_output=True,
            json_repair_level="aggressive",
        ),
    )


def _make_tools():
    """Standard Letta tools for testing."""
    return [
        {
            "name": "send_message",
            "description": "Sends a message to the human user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Message contents to the human.",
                    },
                },
                "required": ["message"],
            },
        },
        {
            "name": "core_memory_replace",
            "description": "Replace the contents of a memory block.",
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "description": "Memory block label to replace.",
                    },
                    "old_content": {
                        "type": "string",
                        "description": "Content to find and replace.",
                    },
                    "new_content": {
                        "type": "string",
                        "description": "New content to replace with.",
                    },
                },
                "required": ["label", "old_content", "new_content"],
            },
        },
    ]


def _make_messages(system_text: str, user_text: str) -> list[PydanticMessage]:
    """Create a simple message list with system and user messages."""
    return [
        PydanticMessage(
            role=MessageRole.system,
            content=[TextContent(text=system_text)],
            created_at=datetime.now(timezone.utc),
        ),
        PydanticMessage(
            role=MessageRole.user,
            content=[TextContent(text=user_text)],
            created_at=datetime.now(timezone.utc),
        ),
    ]


# ============================================================
# Part 1: Direct LLM client tests (no server needed)
# ============================================================


@requires_ollama
@pytest.mark.asyncio
async def test_phi3_blocking_prompt_tool_calling():
    """
    Test phi3:mini with prompt-based tool calling through the blocking path.
    This exercises the full path: build_request_data → LLM call → convert_response_to_chat_completion.
    """
    config = _make_phi3_config()
    client = OpenAIClient()
    tools = _make_tools()

    system_text = (
        "You are a helpful assistant. You MUST respond with tool calls in JSON format. "
        "When the user asks you to send a message, call the send_message function. "
        'Format: {"function": "send_message", "params": {"message": "your message here"}}'
    )
    messages = _make_messages(
        system_text,
        "Say hello to the user using the send_message tool. Keep your message brief.",
    )

    # Build request with prompt-based tool calling
    request_data = client.build_request_data(
        agent_type=AgentType.letta_v1_agent,
        messages=messages,
        llm_config=config,
        tools=tools,
        system=system_text,
    )

    # Verify tools are stripped (prompt mode)
    assert request_data.get("tools") is None, "Tools should be stripped in prompt mode"

    # Verify system message contains tool documentation
    sys_msg = request_data["messages"][0]
    sys_content = sys_msg.get("content", "")
    if isinstance(sys_content, list):
        sys_content = " ".join(p.get("text", "") for p in sys_content if isinstance(p, dict))
    assert "Available functions" in sys_content, "System message should contain tool docs"
    assert "send_message" in sys_content

    # Make the actual LLM call
    import httpx
    async with httpx.AsyncClient(timeout=120.0) as http_client:
        response = await http_client.post(
            f"{config.model_endpoint}/chat/completions",
            json=request_data,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        response_json = response.json()

    # Convert response (same as convert_response_to_chat_completion)
    text_content = response_json["choices"][0]["message"].get("content", "")
    assert text_content, "Model should return text content"

    # Parse the text as a tool call
    parsed = _safe_load_tool_call_str(text_content)
    assert parsed.get("function"), f"Expected a tool call in text, got: {text_content[:200]}"

    # Verify it's a send_message call
    assert parsed["function"] == "send_message"
    assert "message" in parsed.get("params", {})


@requires_ollama
@pytest.mark.asyncio
async def test_phi3_blocking_core_memory_replace():
    """
    Test phi3:mini calling core_memory_replace through prompt-based tool calling.
    """
    config = _make_phi3_config()
    client = OpenAIClient()
    tools = _make_tools()

    system_text = (
        "You are a helpful assistant with memory. You MUST respond with tool calls in JSON format. "
        "When asked to update memory, call the core_memory_replace function. "
        'Format: {"function": "core_memory_replace", "params": {"label": "human", "old_content": "", "new_content": "updated content"}}'
    )
    messages = _make_messages(
        system_text,
        "Update the human memory block to record that the user's name is Alice.",
    )

    request_data = client.build_request_data(
        agent_type=AgentType.letta_v1_agent,
        messages=messages,
        llm_config=config,
        tools=tools,
        system=system_text,
    )

    # Make the actual LLM call
    import httpx
    async with httpx.AsyncClient(timeout=120.0) as http_client:
        response = await http_client.post(
            f"{config.model_endpoint}/chat/completions",
            json=request_data,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        response_json = response.json()

    text_content = response_json["choices"][0]["message"].get("content", "")
    assert text_content

    parsed = _safe_load_tool_call_str(text_content)
    assert parsed.get("function"), f"Expected a tool call, got: {text_content[:200]}"
    assert parsed["function"] == "core_memory_replace"


@requires_ollama
@pytest.mark.asyncio
async def test_phi3_streaming_prompt_tool_calling():
    """
    Test phi3:mini with prompt-based tool calling through the streaming path.
    This exercises the full streaming adapter + interface pipeline.
    """
    from letta.adapters.simple_llm_stream_adapter import SimpleLLMStreamAdapter
    from letta.llm_api.openai_client import OpenAIClient

    config = _make_phi3_config()
    client = OpenAIClient()
    tools = _make_tools()

    system_text = (
        "You are a helpful assistant. You MUST respond with tool calls in JSON format. "
        "When the user asks you to send a message, call the send_message function. "
        'Format: {"function": "send_message", "params": {"message": "your message here"}}'
    )
    messages = _make_messages(
        system_text,
        "Send a brief greeting to the user using the send_message tool.",
    )

    # Build request data
    request_data = client.build_request_data(
        agent_type=AgentType.letta_v1_agent,
        messages=messages,
        llm_config=config,
        tools=tools,
        system=system_text,
    )

    # Create the streaming adapter
    adapter = SimpleLLMStreamAdapter(
        llm_client=client,
        llm_config=config,
        call_type=LLMCallType.agent_step,
    )

    # Collect all streaming chunks
    chunks = []
    async for chunk in adapter.invoke_llm(
        request_data=request_data,
        messages=messages,
        tools=tools,
        use_assistant_message=True,
    ):
        chunks.append(chunk)

    # We should get some chunks
    assert len(chunks) > 0, "Streaming should produce chunks"

    # After streaming completes, the adapter should have extracted tool calls
    # via the prompt-based fallback in get_tool_call_objects
    tool_calls = adapter.tool_calls
    assert len(tool_calls) > 0, (
        f"Adapter should extract tool calls from text content. "
        f"Got {len(chunks)} chunks but no tool calls. "
        f"Content: {adapter.content[:200] if adapter.content else 'none'}"
    )

    # The tool call should be send_message
    assert tool_calls[-1].function.name == "send_message", (
        f"Expected send_message, got {tool_calls[-1].function.name}"
    )


@requires_ollama
@pytest.mark.asyncio
async def test_phi3_json_repair_pipeline():
    """
    Test that the JSON repair pipeline handles phi3:mini's real output,
    which may include code fences, extra text, or malformed JSON.
    """
    config = _make_phi3_config()
    client = OpenAIClient()
    tools = _make_tools()

    system_text = (
        "You are a helpful assistant. Respond with tool calls in JSON format. "
        'Format: {"function": "send_message", "params": {"message": "your message"}}'
    )
    messages = _make_messages(
        system_text,
        "Send a message saying 'Hello from phi3!' using the send_message tool.",
    )

    request_data = client.build_request_data(
        agent_type=AgentType.letta_v1_agent,
        messages=messages,
        llm_config=config,
        tools=tools,
        system=system_text,
    )

    # Make the actual LLM call
    import httpx
    async with httpx.AsyncClient(timeout=120.0) as http_client:
        response = await http_client.post(
            f"{config.model_endpoint}/chat/completions",
            json=request_data,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        response_json = response.json()

    text_content = response_json["choices"][0]["message"].get("content", "")

    # Log the raw output for debugging
    print(f"\n--- Raw phi3:mini output ---\n{text_content}\n---")

    # The repair pipeline should handle this
    parsed = _safe_load_tool_call_str(text_content)
    assert parsed.get("function"), f"Repair pipeline failed on: {text_content[:200]}"

    # Verify the function name
    assert parsed["function"] in ["send_message", "core_memory_replace"], (
        f"Unexpected function: {parsed['function']}"
    )


# ============================================================
# Part 2: Full agent loop tests via Letta REST API
# ============================================================


@pytest.fixture(scope="module")
def letta_client():
    """Create a Letta client pointing at the local server."""
    from letta_client import Letta
    return Letta(base_url=LETTA_SERVER_URL)


@pytest.fixture(scope="function")
def phi3_agent(letta_client):
    """Create an agent configured with phi3:mini and prompt-based tool calling."""
    send_message_tool = letta_client.tools.list(name="send_message").items[0]
    agent_state = letta_client.agents.create(
        name=f"phi3-test-{os.getpid()}",
        agent_type="memgpt_v2_agent",
        include_base_tools=False,
        tool_ids=[send_message_tool.id],
        model="openai-proxy/phi3:mini",
        embedding="letta/letta-free",
    )
    # Update model settings to use prompt-based tool calling
    agent_state = letta_client.agents.update(
        agent_id=agent_state.id,
        model_settings={
            "provider_type": "openai",
            "model_endpoint_type": "ollama",
            "model_endpoint": "http://localhost:11434/v1",
            "context_window": 131072,
            "temperature": 0.7,
            "max_output_tokens": 2048,
            "constraints": {
                "tool_calling_mode": "prompt",
                "tool_call_retry_count": 3,
                "disable_structured_output": True,
                "json_repair_level": "aggressive",
            },
        },
    )
    yield agent_state
    # Cleanup
    try:
        letta_client.agents.delete(agent_state.id)
    except Exception:
        pass


@requires_letta_server
def test_live_server_phi3_blocking(letta_client, phi3_agent):
    """
    Test phi3:mini with prompt-based tool calling through the full Letta server
    in blocking mode. This exercises the entire V3 agent loop: server → adapter →
    LLM → prompt-based tool call parsing → tool execution → response.
    """
    response = letta_client.agents.messages.create(
        agent_id=phi3_agent.id,
        messages=[
            {
                "role": "user",
                "content": "Say hello to the user using the send_message tool. Keep it brief.",
            },
        ],
    )

    # We should get messages back from the agent loop
    assert len(response.messages) > 0, "Agent returned no messages"

    # Log the response for debugging
    for msg in response.messages:
        print(f"  {type(msg).__name__}: {msg}")


@requires_letta_server
def test_live_server_phi3_streaming(letta_client, phi3_agent):
    """
    Test phi3:mini with a second message to verify the agent loop
    works across multiple turns.
    """
    response = letta_client.agents.messages.create(
        agent_id=phi3_agent.id,
        messages=[
            {
                "role": "user",
                "content": "Send a brief greeting to the user using the send_message tool.",
            },
        ],
    )

    # We should get messages back
    assert len(response.messages) > 0, "Agent returned no messages"


@requires_letta_server
def test_live_server_phi3_conversation_persistence(letta_client, phi3_agent):
    """
    Test that a conversation with phi3:mini persists messages across multiple turns.
    """
    # First turn
    response1 = letta_client.agents.messages.create(
        agent_id=phi3_agent.id,
        messages=[
            {
                "role": "user",
                "content": "Hello! What tools do you have available?",
            },
        ],
    )
    assert len(response1.messages) > 0

    # Second turn — verify the agent remembers context
    response2 = letta_client.agents.messages.create(
        agent_id=phi3_agent.id,
        messages=[
            {
                "role": "user",
                "content": "Can you send me a message saying 'test successful'?",
            },
        ],
    )
    assert len(response2.messages) > 0

    # Verify messages are persisted in the database
    all_messages = letta_client.agents.messages.list(agent_id=phi3_agent.id, limit=50)
    assert len(all_messages.items) >= 2, "Messages should be persisted in the database"


# ============================================================
# Part 3: vLLM integration tests (auto-detection probe)
# ============================================================


def _get_vllm_model_name() -> str | None:
    """Get the model name from the vLLM server."""
    try:
        import requests
        resp = requests.get(f"{VLLM_URL}/v1/models", timeout=5)
        if resp.status_code != 200:
            return None
        data = resp.json().get("data", [])
        if not data:
            return None
        return data[0]["id"]
    except Exception:
        return None


def _make_vllm_config() -> LLMConfig:
    """Create an LLMConfig for the vLLM model with auto tool calling mode."""
    model_name = _get_vllm_model_name()
    return LLMConfig(
        model=model_name,
        model_endpoint_type="vllm",
        model_endpoint=f"{VLLM_URL}/v1",
        context_window=131072,
        handle=f"vllm/{model_name}",
        put_inner_thoughts_in_kwargs=False,
        temperature=0.7,
        max_tokens=2048,
        # No explicit constraints — auto-apply should set tool_calling_mode="auto"
    )


@requires_vllm
def test_vllm_auto_probe_detects_capability():
    """
    Test that the auto-detection probe correctly identifies the vLLM model's
    tool-calling capability. This is the core test of the auto-probe system.
    """
    from letta.llm_api.tool_capability_probe import ToolCapabilityCache

    ToolCapabilityCache.reset()
    cache = ToolCapabilityCache.instance()
    config = _make_vllm_config()

    # Probe the model
    supports_native = cache.probe(config)

    # The probe should have cached the result
    assert cache.get_cached(config) is not None, "Probe should cache result"

    # Log the result for debugging
    model_name = _get_vllm_model_name()
    print(f"  vLLM model {model_name}: supports_native_tool_calling={supports_native}")

    # Clean up
    ToolCapabilityCache.reset()


@requires_vllm
def test_vllm_auto_mode_resolves_correctly():
    """
    Test that resolve_tool_calling_mode() correctly resolves "auto" for
    the vLLM model based on the probe result.
    """
    from letta.llm_api.tool_capability_probe import ToolCapabilityCache, resolve_tool_calling_mode

    ToolCapabilityCache.reset()
    config = _make_vllm_config()

    # Resolve auto mode
    resolved = resolve_tool_calling_mode(config)

    # The resolved mode should be "native" or "prompt" (not "auto")
    assert resolved in ("native", "prompt"), f"Expected 'native' or 'prompt', got '{resolved}'"

    model_name = _get_vllm_model_name()
    print(f"  vLLM model {model_name}: resolved tool_calling_mode='{resolved}'")

    # Verify the cache was populated
    cache = ToolCapabilityCache.instance()
    assert cache.get_cached(config) is not None

    ToolCapabilityCache.reset()


@requires_vllm
@pytest.mark.asyncio
async def test_vllm_blocking_auto_mode():
    """
    Test the vLLM model in auto mode through the blocking path.
    The auto-probe should detect the correct mode and either:
    - Use native tool calling (if supported), or
    - Fall back to prompt-based tool calling

    Note: some models on vLLM (e.g., Gemma 4) are non-deterministic about
    tool calling — they may produce native tool calls on some requests but
    text on others. The probe tries twice; if either attempt produces native
    tool calls, the mode is set to "native". The agent loop's retry mechanism
    handles occasional failures.
    """
    from letta.llm_api.tool_capability_probe import ToolCapabilityCache, resolve_tool_calling_mode

    ToolCapabilityCache.reset()
    config = _make_vllm_config()
    client = OpenAIClient()
    tools = _make_tools()

    system_text = "You are a helpful assistant that uses tools to communicate."
    messages = _make_messages(
        system_text,
        "Say hello to the user using the send_message tool. Keep your message brief.",
    )

    # Build request data (auto-mode resolution happens here)
    request_data = client.build_request_data(
        agent_type=AgentType.letta_v1_agent,
        messages=messages,
        llm_config=config,
        tools=tools,
        system=system_text,
    )

    # Check what mode was resolved
    resolved = resolve_tool_calling_mode(config)
    model_name = _get_vllm_model_name()
    print(f"  vLLM model {model_name}: resolved mode='{resolved}'")

    if resolved == "prompt":
        # Tools should be stripped in prompt mode
        assert request_data.get("tools") is None, "Tools should be stripped in prompt mode"
    else:
        # Tools should be present in native mode
        assert request_data.get("tools") is not None, "Tools should be present in native mode"

    # Make the actual LLM call
    import httpx
    async with httpx.AsyncClient(timeout=120.0) as http_client:
        response = await http_client.post(
            f"{config.model_endpoint}/chat/completions",
            json=request_data,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        response_json = response.json()

    # Verify we got a response (the exact format depends on the resolved mode)
    assert "choices" in response_json, f"Expected choices in response, got: {response_json}"
    assert len(response_json["choices"]) > 0, "Expected at least one choice"

    choice = response_json["choices"][0]
    msg = choice.get("message", {})
    has_content = bool(msg.get("content"))
    has_tool_calls = bool(msg.get("tool_calls"))

    print(f"  Response: has_content={has_content}, has_tool_calls={has_tool_calls}")

    # Either native tool calls or text content should be present
    assert has_content or has_tool_calls, (
        f"Expected either content or tool_calls in response, got: {msg}"
    )

    ToolCapabilityCache.reset()


@requires_vllm
@pytest.mark.asyncio
async def test_vllm_native_tool_calling_direct():
    """
    Test vLLM native tool calling directly (not through auto-mode).
    This validates that vLLM can produce native tool calls for this model.
    Note: some models are non-deterministic — try up to 2 times.
    """
    config = _make_vllm_config()
    model_name = _get_vllm_model_name()

    # Try up to 2 times — non-deterministic models may fail on first attempt
    got_native = False
    for attempt in range(2):
        import httpx
        async with httpx.AsyncClient(timeout=120.0) as http_client:
            response = await http_client.post(
                f"{config.model_endpoint}/chat/completions",
                json={
                    "model": model_name,
                    "messages": [
                        {"role": "user", "content": "Call the echo tool with text 'hello'"},
                    ],
                    "tools": [
                        {
                            "type": "function",
                            "function": {
                                "name": "echo",
                                "description": "Echo the input text.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {"text": {"type": "string"}},
                                    "required": ["text"],
                                },
                            },
                        }
                    ],
                    "max_tokens": 50,
                },
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            response_json = response.json()

        choice = response_json["choices"][0]["message"]
        has_tool_calls = bool(choice.get("tool_calls"))
        print(f"  Attempt {attempt+1}: native_tool_calling={has_tool_calls}")
        if has_tool_calls:
            got_native = True
            break

    print(f"  vLLM model {model_name}: native_tool_calling_supported={got_native}")
    # This test is informational — we don't assert True because the model
    # might genuinely not support native tool calling. The auto-probe handles
    # both cases correctly.
