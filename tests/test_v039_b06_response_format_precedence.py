"""B-6 witnesses: explicit response_format overrides disable_structured_output.

RED on 05a7d0af5: both request builders gave constraints.disable_structured_output
absolute precedence, so an explicitly-set response_format was silently dropped
for local models (Nabla FR-2, zero-tool agent).
"""
import pytest

from letta.schemas.enums import AgentType
from letta.schemas.llm_config import LLMConfig
from letta.schemas.message import Message as PydanticMessage
from letta.llm_api.openai_client import OpenAIClient


def _client(monkeypatch):
    client = OpenAIClient.__new__(OpenAIClient)  # skip __init__ (no network)
    client.actor = None
    # avoid the live capability probe (no endpoint in unit tests)
    import letta.llm_api.tool_capability_probe as probe
    monkeypatch.setattr(probe, "resolve_tool_calling_mode", lambda cfg: "native")
    return client


def _config_with_conflict():
    return LLMConfig(
        model="test-model",
        model_endpoint_type="openai",
        model_endpoint="http://localhost:8080/v1",
        context_window=8192,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "out", "schema": {"type": "object"}},
        },
        constraints={"disable_structured_output": True},
    )


def _messages():
    return [PydanticMessage(role="user", content=[{"type": "text", "text": "hi"}])]


def test_chat_completions_builder_keeps_explicit_response_format(monkeypatch):
    data = _client(monkeypatch).build_request_data(
        agent_type=AgentType.letta_v1_agent,
        messages=_messages(),
        llm_config=_config_with_conflict(),
        tools=[],
    )
    rf = data.response_format if hasattr(data, "response_format") else data.get("response_format")
    assert rf is not None, "explicit response_format clobbered by disable_structured_output"


def test_responses_builder_keeps_explicit_response_format(monkeypatch):
    data = _client(monkeypatch).build_request_data_responses(
        agent_type=AgentType.letta_v1_agent,
        messages=_messages(),
        llm_config=_config_with_conflict(),
        tools=[],
    )
    fmt = (data.get("text") or {}).get("format")
    assert fmt is not None, "explicit response_format clobbered by disable_structured_output (responses path)"
