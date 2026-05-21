"""Tests for prompt-based tool calling: format_tools_as_text, request building,
and response conversion in the OpenAI client."""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from letta.agents.helpers import format_tools_as_text
from letta.schemas.enums import AgentType, LLMCallType, MessageRole
from letta.schemas.llm_config import LLMConfig, ModelConstraints
from letta.schemas.letta_message_content import TextContent
from letta.schemas.message import Message as PydanticMessage


# === format_tools_as_text ===

def test_format_tools_basic():
    """Formats a single tool with name, description, and params."""
    tools = [
        {
            "type": "function",
            "function": {
                "name": "send_message",
                "description": "Send a message to the user",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message": {"type": "string", "description": "The message to send"},
                    },
                    "required": ["message"],
                },
            },
        },
    ]
    result = format_tools_as_text(tools)
    assert "send_message" in result
    assert "Send a message to the user" in result
    assert "message:" in result
    assert "Available functions" in result


def test_format_tools_multiple():
    """Formats multiple tools with proper indentation."""
    tools = [
        {
            "type": "function",
            "function": {
                "name": "send_message",
                "description": "Send a message",
                "parameters": {
                    "type": "object",
                    "properties": {"message": {"type": "string", "description": "The message"}},
                    "required": ["message"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "core_memory_replace",
                "description": "Replace memory",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "Memory block label"},
                        "old_content": {"type": "string", "description": "Content to find"},
                        "new_content": {"type": "string", "description": "New content"},
                    },
                    "required": ["label", "old_content", "new_content"],
                },
            },
        },
    ]
    result = format_tools_as_text(tools)
    assert "send_message" in result
    assert "core_memory_replace" in result
    assert "label:" in result
    assert "old_content:" in result


def test_format_tools_empty():
    """Empty tools list returns empty string."""
    result = format_tools_as_text([])
    assert result == ""


def test_format_tools_includes_response_format():
    """Includes JSON response format instructions by default."""
    tools = [
        {
            "type": "function",
            "function": {
                "name": "test",
                "description": "A test function",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]
    result = format_tools_as_text(tools)
    assert '"function"' in result
    assert '"params"' in result


def test_format_tools_required_params():
    """Required params are marked with (required)."""
    tools = [
        {
            "type": "function",
            "function": {
                "name": "test",
                "description": "A test function",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "required_param": {"type": "string", "description": "A required param"},
                        "optional_param": {"type": "string", "description": "An optional param"},
                    },
                    "required": ["required_param"],
                },
            },
        },
    ]
    result = format_tools_as_text(tools)
    assert "(required)" in result
    # optional_param should not have (required)
    lines = result.split("\n")
    required_line = [l for l in lines if "required_param" in l][0]
    optional_line = [l for l in lines if "optional_param" in l][0]
    assert "(required)" in required_line
    assert "(required)" not in optional_line


# === build_request_data with prompt mode ===

def _make_llm_config_prompt_mode():
    return LLMConfig(
        model="test-model",
        model_endpoint_type="openai",
        model_endpoint="http://localhost:11434/v1",
        context_window=131072,
        handle="test/test-model",
        constraints=ModelConstraints(
            tool_calling_mode="prompt",
            tool_call_retry_count=3,
            disable_structured_output=True,
            json_repair_level="aggressive",
        ),
    )


def _make_llm_config_native_mode():
    return LLMConfig(
        model="test-model",
        model_endpoint_type="openai",
        model_endpoint="http://localhost:11434/v1",
        context_window=131072,
        handle="test/test-model",
    )


def _make_tools():
    return [
        {
            "name": "send_message",
            "description": "Send a message",
            "parameters": {
                "type": "object",
                "properties": {"message": {"type": "string", "description": "The message"}},
                "required": ["message"],
            },
        },
    ]


def test_build_request_strips_tools_in_prompt_mode():
    """When tool_calling_mode='prompt', request has no tools field."""
    from letta.llm_api.openai_client import OpenAIClient

    client = OpenAIClient()
    config = _make_llm_config_prompt_mode()
    tools = _make_tools()

    messages = [
        PydanticMessage(
            role=MessageRole.system,
            content=[TextContent(text="You are a helpful assistant.")],
            created_at=datetime.now(timezone.utc),
        ),
        PydanticMessage(
            role=MessageRole.user,
            content=[TextContent(text="Hello!")],
            created_at=datetime.now(timezone.utc),
        ),
    ]

    request_data = client.build_request_data(
        agent_type=AgentType.letta_v1_agent,
        messages=messages,
        llm_config=config,
        tools=tools,
        system="You are a helpful assistant.",
    )

    # Tools should be None (stripped for prompt-based mode)
    assert request_data.get("tools") is None


def test_build_request_embeds_tools_in_system_prompt():
    """When tool_calling_mode='prompt', system message contains tool docs."""
    from letta.llm_api.openai_client import OpenAIClient

    client = OpenAIClient()
    config = _make_llm_config_prompt_mode()
    tools = _make_tools()

    messages = [
        PydanticMessage(
            role=MessageRole.system,
            content=[TextContent(text="You are a helpful assistant.")],
            created_at=datetime.now(timezone.utc),
        ),
        PydanticMessage(
            role=MessageRole.user,
            content=[TextContent(text="Hello!")],
            created_at=datetime.now(timezone.utc),
        ),
    ]

    request_data = client.build_request_data(
        agent_type=AgentType.letta_v1_agent,
        messages=messages,
        llm_config=config,
        tools=tools,
        system="You are a helpful assistant.",
    )

    # System message should contain tool documentation
    sys_msg = request_data["messages"][0]
    sys_content = sys_msg.get("content", "")
    if isinstance(sys_content, list):
        sys_content = " ".join(p.get("text", "") for p in sys_content if isinstance(p, dict))
    assert "Available functions" in sys_content
    assert "send_message" in sys_content


def test_build_request_native_mode_keeps_tools():
    """When no prompt mode, tools are kept in the request normally."""
    from letta.llm_api.openai_client import OpenAIClient

    client = OpenAIClient()
    config = _make_llm_config_native_mode()
    tools = _make_tools()

    messages = [
        PydanticMessage(
            role=MessageRole.system,
            content=[TextContent(text="You are a helpful assistant.")],
            created_at=datetime.now(timezone.utc),
        ),
        PydanticMessage(
            role=MessageRole.user,
            content=[TextContent(text="Hello!")],
            created_at=datetime.now(timezone.utc),
        ),
    ]

    request_data = client.build_request_data(
        agent_type=AgentType.letta_v1_agent,
        messages=messages,
        llm_config=config,
        tools=tools,
        system="You are a helpful assistant.",
    )

    # Tools should be present in native mode
    assert request_data.get("tools") is not None


# === convert_response_to_chat_completion with prompt mode ===

@pytest.mark.asyncio
async def test_convert_response_parses_text_tool_call():
    """When prompt mode, text content with valid JSON tool call becomes a synthetic ToolCall."""
    from letta.llm_api.openai_client import OpenAIClient

    client = OpenAIClient()
    config = _make_llm_config_prompt_mode()

    # Simulate a response where the model outputs a JSON tool call in text
    response_data = {
        "id": "test-id",
        "object": "chat.completion",
        "created": 1234567890,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": '{"function": "send_message", "params": {"message": "Hello!"}}',
                },
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }

    messages = [
        PydanticMessage(
            role=MessageRole.system,
            content=[TextContent(text="You are a helpful assistant.")],
            created_at=datetime.now(timezone.utc),
        ),
    ]

    result = await client.convert_response_to_chat_completion(response_data, messages, config)

    assert result.choices[0].message.tool_calls is not None
    assert len(result.choices[0].message.tool_calls) == 1
    tc = result.choices[0].message.tool_calls[0]
    assert tc.function.name == "send_message"
    args = json.loads(tc.function.arguments)
    assert args["message"] == "Hello!"
    assert result.choices[0].finish_reason == "tool_calls"


@pytest.mark.asyncio
async def test_convert_response_fallback_send_message():
    """When prompt mode but no JSON in text, falls back to send_message."""
    from letta.llm_api.openai_client import OpenAIClient

    client = OpenAIClient()
    config = _make_llm_config_prompt_mode()

    response_data = {
        "id": "test-id",
        "object": "chat.completion",
        "created": 1234567890,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": "Hello! I'm just a friendly assistant chatting with you.",
                },
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }

    messages = [
        PydanticMessage(
            role=MessageRole.system,
            content=[TextContent(text="You are a helpful assistant.")],
            created_at=datetime.now(timezone.utc),
        ),
    ]

    result = await client.convert_response_to_chat_completion(response_data, messages, config)

    assert result.choices[0].message.tool_calls is not None
    tc = result.choices[0].message.tool_calls[0]
    assert tc.function.name == "send_message"
    args = json.loads(tc.function.arguments)
    assert "Hello!" in args["message"]


@pytest.mark.asyncio
async def test_convert_response_preserves_native_tool_calls():
    """When prompt mode but model returns native tool_calls, don't override."""
    from letta.llm_api.openai_client import OpenAIClient

    client = OpenAIClient()
    config = _make_llm_config_prompt_mode()

    response_data = {
        "id": "test-id",
        "object": "chat.completion",
        "created": 1234567890,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-123",
                            "type": "function",
                            "function": {
                                "name": "send_message",
                                "arguments": '{"message": "Hello from native tool call!"}',
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }

    messages = [
        PydanticMessage(
            role=MessageRole.system,
            content=[TextContent(text="You are a helpful assistant.")],
            created_at=datetime.now(timezone.utc),
        ),
    ]

    result = await client.convert_response_to_chat_completion(response_data, messages, config)

    # Should preserve the native tool call, not override it
    assert result.choices[0].message.tool_calls is not None
    assert len(result.choices[0].message.tool_calls) == 1
    assert result.choices[0].message.tool_calls[0].function.name == "send_message"


# === Streaming interface: prompt-based tool calling ===

def test_simple_streaming_prompt_mode_parses_text_tool_call():
    """SimpleOpenAIStreamingInterface with prompt mode: text content → synthetic ToolCall."""
    from letta.interfaces.openai_streaming_interface import SimpleOpenAIStreamingInterface
    from letta.schemas.letta_message import AssistantMessage

    interface = SimpleOpenAIStreamingInterface(
        tool_calling_mode="prompt",
    )

    # Simulate accumulated content_messages from streaming
    interface.content_messages = [
        AssistantMessage(
            id="test-id",
            content='{"function": "send_message", "params": {"message": "Hello!"}}',
            date=datetime.now(timezone.utc).isoformat(),
        ),
    ]

    # No native tool calls accumulated
    assert interface._tool_calls_acc == {}

    tool_calls = interface.get_tool_call_objects()
    assert len(tool_calls) == 1
    assert tool_calls[0].function.name == "send_message"
    args = json.loads(tool_calls[0].function.arguments)
    assert args["message"] == "Hello!"


def test_simple_streaming_prompt_mode_fallback_send_message():
    """SimpleOpenAIStreamingInterface with prompt mode: no JSON → send_message fallback."""
    from letta.interfaces.openai_streaming_interface import SimpleOpenAIStreamingInterface
    from letta.schemas.letta_message import AssistantMessage

    interface = SimpleOpenAIStreamingInterface(
        tool_calling_mode="prompt",
    )

    interface.content_messages = [
        AssistantMessage(
            id="test-id",
            content="Hello! I'm just a friendly assistant chatting with you.",
            date=datetime.now(timezone.utc).isoformat(),
        ),
    ]

    tool_calls = interface.get_tool_call_objects()
    assert len(tool_calls) == 1
    assert tool_calls[0].function.name == "send_message"
    args = json.loads(tool_calls[0].function.arguments)
    assert "Hello!" in args["message"]


def test_simple_streaming_prompt_mode_preserves_native_tool_calls():
    """SimpleOpenAIStreamingInterface with prompt mode: native tool calls take priority."""
    from letta.interfaces.openai_streaming_interface import SimpleOpenAIStreamingInterface

    interface = SimpleOpenAIStreamingInterface(
        tool_calling_mode="prompt",
    )

    # Simulate native tool calls found in stream
    interface._tool_calls_acc = {
        0: {"id_parts": ["call-123"], "name": "send_message", "arguments": '{"message": "from native"}'},
    }
    interface._tool_call_start_order = [0]

    tool_calls = interface.get_tool_call_objects()
    assert len(tool_calls) == 1
    assert tool_calls[0].function.name == "send_message"
    assert tool_calls[0].id == "call-123"


def test_simple_streaming_prompt_mode_empty_content():
    """SimpleOpenAIStreamingInterface with prompt mode: no content → empty list."""
    from letta.interfaces.openai_streaming_interface import SimpleOpenAIStreamingInterface

    interface = SimpleOpenAIStreamingInterface(
        tool_calling_mode="prompt",
    )

    # No content messages, no tool calls
    tool_calls = interface.get_tool_call_objects()
    assert tool_calls == []


def test_simple_streaming_no_prompt_mode_no_tool_calls():
    """SimpleOpenAIStreamingInterface without prompt mode: no tool calls → empty list."""
    from letta.interfaces.openai_streaming_interface import SimpleOpenAIStreamingInterface
    from letta.schemas.letta_message import AssistantMessage

    interface = SimpleOpenAIStreamingInterface(
        tool_calling_mode=None,
    )

    interface.content_messages = [
        AssistantMessage(
            id="test-id",
            content='{"function": "send_message", "params": {"message": "Hello!"}}',
            date=datetime.now(timezone.utc).isoformat(),
        ),
    ]

    # Without prompt mode, text content is NOT parsed as tool calls
    tool_calls = interface.get_tool_call_objects()
    assert tool_calls == []


# === Stream adapter: local provider types accepted ===

def test_stream_adapter_accepts_local_provider_types():
    """SimpleLLMStreamAdapter accepts local inference provider types for streaming."""
    from letta.adapters.simple_llm_stream_adapter import SimpleLLMStreamAdapter
    from letta.llm_api.openai_client import OpenAIClient

    local_types = ["ollama", "vllm", "openai_compatible", "localai", "llamacpp", "llamafile", "mlx"]

    for ptype in local_types:
        config = LLMConfig(
            model="test-model",
            model_endpoint_type=ptype,
            model_endpoint="http://localhost:8080/v1",
            context_window=8192,
            handle="test/test-model",
        )
        adapter = SimpleLLMStreamAdapter(
            llm_client=OpenAIClient(),
            llm_config=config,
            call_type=LLMCallType.agent_step,
        )
        # Should not raise ValueError for these provider types
        assert adapter is not None


# === OpenAIStreamingInterface (non-Simple): prompt-based fallback ===

def test_openai_streaming_prompt_mode_parses_text_tool_call():
    """OpenAIStreamingInterface with prompt mode: content_buffer → synthetic ToolCall."""
    from letta.interfaces.openai_streaming_interface import OpenAIStreamingInterface

    interface = OpenAIStreamingInterface(
        tool_calling_mode="prompt",
    )

    # Simulate accumulated content_buffer from streaming
    interface.content_buffer = [
        '{"function": "send_message", "params": {"message": "Hello!"}}'
    ]

    tool_call = interface.get_tool_call_object()
    assert tool_call.function.name == "send_message"
    args = json.loads(tool_call.function.arguments)
    assert args["message"] == "Hello!"


def test_openai_streaming_prompt_mode_fallback_send_message():
    """OpenAIStreamingInterface with prompt mode: no JSON → send_message fallback."""
    from letta.interfaces.openai_streaming_interface import OpenAIStreamingInterface

    interface = OpenAIStreamingInterface(
        tool_calling_mode="prompt",
    )

    interface.content_buffer = [
        "Hello! I'm just a friendly assistant chatting with you."
    ]

    tool_call = interface.get_tool_call_object()
    assert tool_call.function.name == "send_message"
    args = json.loads(tool_call.function.arguments)
    assert "Hello!" in args["message"]


def test_openai_streaming_prompt_mode_empty_content_raises():
    """OpenAIStreamingInterface with prompt mode: empty content → ValueError."""
    from letta.interfaces.openai_streaming_interface import OpenAIStreamingInterface

    interface = OpenAIStreamingInterface(
        tool_calling_mode="prompt",
    )

    with pytest.raises(ValueError, match="No tool call found"):
        interface.get_tool_call_object()


def test_openai_streaming_no_prompt_mode_no_native_calls_raises():
    """OpenAIStreamingInterface without prompt mode: no native calls → ValueError."""
    from letta.interfaces.openai_streaming_interface import OpenAIStreamingInterface

    interface = OpenAIStreamingInterface(
        tool_calling_mode=None,
    )

    with pytest.raises(ValueError, match="No tool call found"):
        interface.get_tool_call_object()
