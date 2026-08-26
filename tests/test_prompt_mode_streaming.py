"""Synthetic-stream unit tests for prompt-mode streaming dual-emission fix.

The bug (v0.16.25 and earlier): in prompt-based tool calling, content
deltas that constitute the tool-call JSON were yielded immediately as
AssistantMessage chunks (TTFT) AND parsed at stream end into a tool
call — the client saw raw JSON as the assistant's message while the
tool also executed, and the same text attached to the persisted
assistant Message and rendered as an assistant message.

The fix: prompt-mode content is held back; the stream-end decision (in
process()) and the persist-side decision (get_tool_call_objects) run
the SAME parser on the SAME text, so they cannot diverge. Parsed as a
tool call -> text suppressed everywhere; otherwise -> one complete
AssistantMessage.

These tests drive SimpleOpenAIStreamingInterface.process() with
fabricated ChatCompletionChunk streams. Deterministic — no server, no
model. This is the reproduction the smoke rig cannot provide
(model-dependent routing).
"""
import time
from types import SimpleNamespace

from openai.types.chat import ChatCompletionChunk
from openai.types.chat.chat_completion_chunk import Choice, ChoiceDelta, ChoiceDeltaToolCall, ChoiceDeltaToolCallFunction

from letta.interfaces.openai_streaming_interface import SimpleOpenAIStreamingInterface
from letta.schemas.letta_message import AssistantMessage, ReasoningMessage, ToolCallMessage
from letta.schemas.letta_message_content import ReasoningContent, TextContent
from letta.security.canary_output_filter import StreamingCanaryFilter

TOOL_JSON = '{"function": "archival_memory_search", "params": {"query": "financial data"}}'


class _FakeAsyncStream:
    """AsyncStream stand-in: supports `async with` + async iteration."""

    def __init__(self, chunks):
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for c in self._chunks:
            yield c


def _chunk(content=None, reasoning=None):
    delta = ChoiceDelta(content=content, reasoning_content=reasoning)
    return ChatCompletionChunk(
        id="chatcmpl-test",
        created=int(time.time()),
        model="test-model",
        object="chat.completion.chunk",
        choices=[Choice(index=0, delta=delta, finish_reason=None)],
    )


def _split(text, n=3):
    """Split text into n contiguous pieces."""
    step = max(len(text) // n, 1)
    return [text[i : i + step] for i in range(0, len(text), step)]


async def _run(chunks, tool_calling_mode="prompt", llm_config=None):
    """Drive process(); return yielded messages. Mimics the adapter call
    order afterwards: tool_calls extracted BEFORE content."""
    interface = SimpleOpenAIStreamingInterface(
        model="test-model",
        tool_calling_mode=tool_calling_mode,
        llm_config=llm_config,
    )
    yielded = []
    async for msg in interface.process(_FakeAsyncStream(chunks)):
        yielded.append(msg)
    tool_calls = interface.get_tool_call_objects()
    content = interface.get_content()
    return yielded, tool_calls, content, interface


def _assistants(yielded):
    return [m for m in yielded if isinstance(m, AssistantMessage)]


def _reasonings(yielded):
    return [m for m in yielded if isinstance(m, ReasoningMessage)]


class TestPromptModeSuppression:
    async def test_pure_json_suppressed(self):
        yielded, calls, content, _ = await _run([_chunk(p) for p in _split(TOOL_JSON)])
        assert _assistants(yielded) == [], "tool-call JSON must not be yielded as assistant message"
        assert len(calls) == 1 and calls[0].function.name == "archival_memory_search"
        assert not any(isinstance(c, TextContent) for c in content), "consumed text must not flow via get_content"

    async def test_fenced_json_suppressed(self):
        text = f"```json\n{TOOL_JSON}\n```"
        yielded, calls, content, _ = await _run([_chunk(p) for p in _split(text)])
        assert _assistants(yielded) == []
        assert len(calls) == 1 and calls[0].function.name == "archival_memory_search"
        assert not any(isinstance(c, TextContent) for c in content)

    async def test_prose_plus_json_suppressed(self):
        text = f"Sure, I will search for that.\n{TOOL_JSON}"
        yielded, calls, content, _ = await _run([_chunk(p) for p in _split(text)])
        assert _assistants(yielded) == []
        assert len(calls) == 1 and calls[0].function.name == "archival_memory_search"
        assert not any(isinstance(c, TextContent) for c in content)

    async def test_json_then_trailing_prose_suppressed(self):
        # Parallel-tool shape: second JSON object after the first, plus prose.
        text = TOOL_JSON + '{"function": "execute_code", "params": {}}' + " and then some trailing words"
        yielded, calls, content, _ = await _run([_chunk(p) for p in _split(text, n=5)])
        assert _assistants(yielded) == []
        # First complete object wins
        assert len(calls) == 1 and calls[0].function.name == "archival_memory_search"
        assert not any(isinstance(c, TextContent) for c in content)

    async def test_reasoning_then_json(self):
        chunks = [
            _chunk(reasoning="I should search the archives for this."),
            _chunk(reasoning="Let me form the call."),
            *(_chunk(p) for p in _split(TOOL_JSON)),
        ]
        yielded, calls, content, _ = await _run(chunks)
        # Reasoning streams live
        assert len(_reasonings(yielded)) == 2
        assert _assistants(yielded) == []
        assert len(calls) == 1 and calls[0].function.name == "archival_memory_search"
        parts = [c for c in content if isinstance(c, ReasoningContent)]
        assert parts and "search the archives" in parts[0].reasoning
        assert not any(isinstance(c, TextContent) for c in content)


class TestPromptModeFinalAnswer:
    async def test_prose_emitted_once_at_end(self):
        text = "The financial data shows ACME revenue up 12% quarter over quarter."
        yielded, calls, content, _ = await _run([_chunk(p) for p in _split(text)])
        assistants = _assistants(yielded)
        assert len(assistants) == 1, "final answer must emit exactly ONE AssistantMessage"
        assert assistants[0].content == text
        assert assistants[-1] is yielded[-1] or isinstance(yielded[-1], AssistantMessage), "emission at stream end"
        assert calls == []  # llm_config=None -> no valid_tool_names -> no send_message fallback
        assert any(isinstance(c, TextContent) and c.text == text for c in content)

    async def test_send_message_fallback_keeps_text(self):
        # send_message in valid_tool_names: fallback call carries the text as
        # its argument — the consumed flag must NOT be set (text flows on).
        llm_config = SimpleNamespace(valid_tool_names={"send_message"}, constraints=None)
        text = "Just a plain answer here."
        yielded, calls, content, _ = await _run([_chunk(p) for p in _split(text)], llm_config=llm_config)
        assistants = _assistants(yielded)
        assert len(assistants) == 1 and assistants[0].content == text
        assert len(calls) == 1 and calls[0].function.name == "send_message"
        # flag NOT set: TextContent flows for the fallback's argument semantics
        assert any(isinstance(c, TextContent) for c in content)

    async def test_canary_filter_composition(self):
        # v0.16.25 canary filter consumes assistant_message chunks; under
        # holdback it sees ONE end-of-stream chunk. Redaction must hold on
        # the single-chunk cadence.
        canary = "CANARY-SMOKE-PROBE-2026"
        text = f"Here is the token {canary} that you asked for."
        yielded, calls, content, _ = await _run([_chunk(p) for p in _split(text)])
        assistants = _assistants(yielded)
        assert len(assistants) == 1
        filt = StreamingCanaryFilter(canary)
        emitted, detected = filt.feed(assistants[0].content)
        full = emitted + filt.flush()
        assert detected
        assert canary not in full


class TestNativeModeUnchanged:
    async def test_native_mode_content_streams_immediately(self):
        # tool_calling_mode != "prompt": every content delta yields an
        # AssistantMessage (TTFT preserved). 3 deltas -> 3 assistant chunks.
        pieces = _split("Hello there, this is a native-mode answer.")
        yielded, calls, content, _ = await _run([_chunk(p) for p in pieces], tool_calling_mode=None)
        assistants = _assistants(yielded)
        assert len(assistants) == len(pieces)
        assert "".join(a.content for a in assistants) == "".join(pieces)
        assert calls == []
        assert any(isinstance(c, TextContent) for c in content)

    async def test_native_tool_call_deltas(self):
        tc = ChoiceDeltaToolCall(
            index=0,
            id="call-test-1",
            function=ChoiceDeltaToolCallFunction(name="web_search", arguments='{"query": "x"}'),
        )
        delta = ChoiceDelta(tool_calls=[tc])
        chunk = ChatCompletionChunk(
            id="chatcmpl-test",
            created=int(time.time()),
            model="test-model",
            object="chat.completion.chunk",
            choices=[Choice(index=0, delta=delta, finish_reason=None)],
        )
        yielded, calls, content, _ = await _run([chunk], tool_calling_mode=None)
        assert _assistants(yielded) == []
        assert len(calls) == 1 and calls[0].function.name == "web_search"
        assert any(isinstance(m, ToolCallMessage) for m in yielded)


class TestEdgeCases:
    async def test_empty_stream(self):
        yielded, calls, content, _ = await _run([])
        assert _assistants(yielded) == []
        assert calls == []
        assert content == [] or all(isinstance(c, (ReasoningContent,)) for c in content)

    async def test_reasoning_only_no_assistant(self):
        chunks = [_chunk(reasoning="thinking..."), _chunk(reasoning="more thinking")]
        yielded, calls, content, _ = await _run(chunks)
        assert _assistants(yielded) == [], "reasoning-only stream must yield no AssistantMessage"
        assert calls == []
        assert any(isinstance(c, ReasoningContent) for c in content)
