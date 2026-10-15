"""Tests for the max_tokens wire fix + slot insurance (v0.16.36).

The bug (live A/B, step-1 findings): max_completion_tokens — the only
cap field ChatCompletionRequest carries — is silently ignored by Ollama
0.40.4 on both thinking and non-thinking models; legacy max_tokens is
what binds. Our client sent only the ignored param, making
llm_config.max_tokens a no-op against Ollama (including the v0.16.35
Part-B claim and the would-be insurance default — caught by Epsilon's
gate 1b pre-ship).

These pin: the both-never matrix (local/proxy/cloud x cap-set/unset),
the insurance default + explicit-wins, and the one-definition helper.
"""
from pathlib import Path

from letta.schemas.llm_config import LLMConfig


def _config(endpoint_type, endpoint, max_tokens="unset"):
    kwargs = dict(
        model="mistral:7b",
        model_endpoint_type=endpoint_type,
        model_endpoint=endpoint,
        context_window=8192,
    )
    if max_tokens != "unset":
        kwargs["max_tokens"] = max_tokens
    return LLMConfig(**kwargs)


class TestHelper:
    def test_local_provider_type(self):
        assert _config("ollama", "http://localhost:11434").is_local_inference_endpoint is True

    def test_local_proxy_openai_type(self):
        assert _config("openai", "http://localhost:11434/v1").is_local_inference_endpoint is True

    def test_cloud_openai(self):
        assert _config("openai", "https://api.openai.com/v1").is_local_inference_endpoint is False

    def test_cloud_openai_no_endpoint(self):
        # empty endpoint = the SDK default (cloud); NOT local
        assert _config("openai", "").is_local_inference_endpoint is False

    def test_one_definition_two_consumers(self):
        """The v0.16.20 copy-divergence lesson, pinned: the helper is THE
        definition; apply_default_constraints and openai_client both use it;
        no literal local-set duplication in the client."""
        cfg = Path("letta/schemas/llm_config.py").read_text()
        client = Path("letta/llm_api/openai_client.py").read_text()
        assert cfg.count("def is_local_inference_endpoint") == 1
        assert "is_local_inference_endpoint" in client  # the consumer
        # no duplicated local-set literal in the client
        assert '"ollama", "vllm"' not in client


class TestWireFix:
    def _build_request_data(self, llm_config):
        """Drive the builder's post-dump region via the real function."""
        import letta.llm_api.openai_client as oc

        # build_request_data needs messages list of Message objects; use minimal dicts via
        # the same path the client uses — call with simple chat messages
        from letta.schemas.message import Message

        from letta.schemas.enums import AgentType

        msgs = [Message(role="user", content=[{"type": "text", "text": "hi"}])]
        client = oc.OpenAIClient()
        data = client.build_request_data(
            messages=msgs,
            llm_config=llm_config,
            agent_type=AgentType.letta_v1_agent,
            tools=None,
            force_tool_call=None,
        )
        return data

    def test_local_sends_legacy_only(self):
        cfg = _config("ollama", "http://localhost:11434", max_tokens=500)
        data = self._build_request_data(cfg)
        assert data.get("max_tokens") == 500
        assert "max_completion_tokens" not in data  # never both

    def test_local_proxy_sends_legacy_only(self):
        cfg = _config("openai", "http://localhost:11434/v1", max_tokens=500)
        data = self._build_request_data(cfg)
        assert data.get("max_tokens") == 500
        assert "max_completion_tokens" not in data

    def test_cloud_sends_modern_only(self):
        cfg = _config("openai", "https://api.openai.com/v1", max_tokens=500)
        data = self._build_request_data(cfg)
        assert data.get("max_completion_tokens") == 500
        assert "max_tokens" not in data  # never legacy to OpenAI

    def test_local_none_becomes_insurance_default_on_wire(self):
        """Post-insurance (v0.16.36), a local config with max_tokens unset
        gets the 16384 default at VALIDATION time — so the wire sees the
        legacy key with the insured value, never nothing. The None-flows-
        through case now exists only on cloud endpoints (no insurance)."""
        cfg = _config("ollama", "http://localhost:11434", max_tokens=None)
        assert cfg.max_tokens == 16384  # insurance fired at validation
        data = self._build_request_data(cfg)
        assert data.get("max_tokens") == 16384
        assert "max_completion_tokens" not in data

    def test_cloud_none_sends_no_cap(self):
        """Cloud + max_tokens unset: the builder constructs
        ChatCompletionRequest with max_completion_tokens=None explicitly,
        so exclude_unset keeps the key with a None value (schema-shape
        artifact, pre-existing). The assertion that matters: no LEGACY
        key (never both) and the modern value is None (no cap)."""
        cfg = _config("openai", "https://api.openai.com/v1", max_tokens=None)
        assert cfg.max_tokens is None  # no insurance on cloud
        data = self._build_request_data(cfg)
        assert "max_tokens" not in data  # legacy never sent to cloud
        assert data.get("max_completion_tokens") is None  # explicit None = no cap


class TestSlotInsurance:
    def test_local_default_16384(self):
        cfg = _config("ollama", "http://localhost:11434")
        # apply_default_constraints runs as a model validator on construction
        assert cfg.max_tokens == 16384

    def test_explicit_wins(self):
        cfg = _config("ollama", "http://localhost:11434", max_tokens=4096)
        assert cfg.max_tokens == 4096

    def test_cloud_no_default(self):
        cfg = _config("openai", "https://api.openai.com/v1")
        assert cfg.max_tokens is None

    def test_local_proxy_default(self):
        cfg = _config("openai", "http://localhost:11434/v1")
        assert cfg.max_tokens == 16384


class TestSourceGuards:
    def test_wire_fix_in_builder(self):
        client = Path("letta/llm_api/openai_client.py").read_text()
        assert "is_local_inference_endpoint" in client
        assert 'request_data.pop("max_completion_tokens", None)' in client  # never both
        # the doc correction
        cfg = Path("letta/schemas/llm_config.py").read_text()
        assert "LEGACY" in cfg and "silently ignored by Ollama 0.40.4" in cfg
