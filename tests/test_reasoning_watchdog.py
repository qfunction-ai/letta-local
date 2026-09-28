"""Tests for the reasoning-generation watchdog (v0.16.35).

Pins: trip on consecutive identical reasoning deltas; no-trip on healthy
varied streams at 3x incident scale; off-default byte-identical; state
reset across process() calls (instance reuse); multi-token-delta
counting contract; and the WRONG-TWIN guard (detection must live in
class SimpleOpenAIStreamingInterface, not its copy-paste siblings).
"""
import re
import time
from pathlib import Path
from types import SimpleNamespace

from openai.types.chat import ChatCompletionChunk
from openai.types.chat.chat_completion_chunk import Choice, ChoiceDelta

from letta.interfaces.openai_streaming_interface import SimpleOpenAIStreamingInterface
from letta.schemas.letta_message import ReasoningMessage


class _FakeAsyncStream:
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


def _rchunk(reasoning):
    return ChatCompletionChunk(
        id="chatcmpl-test",
        created=int(time.time()),
        model="test-model",
        object="chat.completion.chunk",
        choices=[Choice(index=0, delta=ChoiceDelta(content=None, reasoning_content=reasoning), finish_reason=None)],
    )


def _varied(n):
    words = ["searching", "the", "archive", "for", "earnings", "data", "now", "considering", "next", "step"]
    return [_rchunk(words[i % len(words)]) for i in range(n)]


async def _run(chunks, watchdog=False):
    iface = SimpleOpenAIStreamingInterface(model="test-model", reasoning_watchdog=watchdog)
    yielded = []
    async for msg in iface.process(_FakeAsyncStream(chunks)):
        yielded.append(msg)
    return iface, yielded


class TestTrip:
    async def test_collapse_trips_and_terminates(self):
        """Incident-2 shape: varied prefix then 40 identical deltas ->
        TERMINATES EARLY (yielded << total chunks; no crash). Trip state is
        reset in process()'s finally by design — the observable is the
        terminated yield count plus the reset (post-state clean)."""
        chunks = _varied(10) + [_rchunk("arch") for _ in range(40)]
        iface, yielded = await _run(chunks, watchdog=True)
        reasoning = [m for m in yielded if isinstance(m, ReasoningMessage)]
        # Structural: yielded = varied + (limit - 1). Deltas 1..23 of the
        # run yield; the 24th trips and returns un-yielded; the loop breaks
        # on the next chunk. 10 + 23 = 33; full consumption would be 50.
        assert len(reasoning) == 10 + 24 - 1, f"got {len(reasoning)}"
        # post-state: reset by the finally (the v0.16.27 lesson)
        assert iface._watchdog_tripped is False
        assert iface._watchdog_run_len == 0 and iface._watchdog_run_token is None

    async def test_word_granular_deltas_trip(self):
        """Multi-token deltas count the same — the consecutive-identical-DELTA
        contract regardless of delta size."""
        chunks = _varied(5) + [_rchunk("archive_archive") for _ in range(30)]
        iface, yielded = await _run(chunks, watchdog=True)
        reasoning = [m for m in yielded if isinstance(m, ReasoningMessage)]
        assert len(reasoning) == 5 + 24 - 1, f"got {len(reasoning)}"


class TestHealthy:
    async def test_varied_no_trip_at_3x_scale(self):
        chunks = _varied(300)  # 3x incident scale, varied vocabulary
        iface, yielded = await _run(chunks, watchdog=True)
        assert iface._watchdog_tripped is False
        assert len([m for m in yielded if isinstance(m, ReasoningMessage)]) == 300


class TestOffDefault:
    async def test_off_consumes_everything(self):
        """Byte-identical today behavior: degenerate stream, watchdog off."""
        chunks = _varied(5) + [_rchunk("arch") for _ in range(60)]
        iface, yielded = await _run(chunks, watchdog=False)
        assert iface._watchdog_tripped is False
        assert len([m for m in yielded if isinstance(m, ReasoningMessage)]) == 65


class TestReset:
    async def test_state_resets_across_process_calls(self):
        """v0.16.27 instance-reuse lesson: a tripped stream resets in the
        finally; a SECOND process() run on the same instance behaves clean
        (full consumption of a healthy stream, fresh detection state)."""
        chunks = _varied(5) + [_rchunk("arch") for _ in range(30)]
        iface, yielded1 = await _run(chunks, watchdog=True)
        assert len([m for m in yielded1 if isinstance(m, ReasoningMessage)]) == 5 + 24 - 1  # tripped
        # second run: healthy stream, full consumption (no stale trip)
        yielded2 = []
        async for msg in iface.process(_FakeAsyncStream(_varied(20))):
            yielded2.append(msg)
        assert len([m for m in yielded2 if isinstance(m, ReasoningMessage)]) == 20
        # and a fresh collapse on the reused instance still trips (state live)
        chunks2 = _varied(3) + [_rchunk("x") for _ in range(30)]
        yielded3 = []
        async for msg in iface.process(_FakeAsyncStream(chunks2)):
            yielded3.append(msg)
        assert len([m for m in yielded3 if isinstance(m, ReasoningMessage)]) == 3 + 24 - 1  # tripped again


class TestSourceGuards:
    def test_detection_inside_simple_class(self):
        """WRONG-TWIN GUARD (Epsilon review finding 1): the detection code
        must live in class SimpleOpenAIStreamingInterface — the file has
        three copy-paste process loops; landing in a sibling passes
        synthetic tests while production stays unprotected."""
        src = Path("letta/interfaces/openai_streaming_interface.py").read_text()
        classes = [(m.start(), m.group(1)) for m in re.finditer(r"^class (\w+)", src, re.M)]
        det = src.find("self._watchdog_run_len >= _WATCHDOG_RUN_LIMIT")
        trip = src.find("if self._watchdog_tripped:")
        assert det != -1 and trip != -1
        for pos, name in [(det, "detection"), (trip, "trip-break")]:
            owner = None
            for i, (cpos, cname) in enumerate(classes):
                nxt = classes[i + 1][0] if i + 1 < len(classes) else len(src)
                if cpos <= pos < nxt:
                    owner = cname
            assert owner == "SimpleOpenAIStreamingInterface", (
                f"{name} lives in {owner}, not SimpleOpenAIStreamingInterface"
            )

    def test_config_chain(self):
        sec = Path("letta/security/agent_security.py").read_text()
        assert 'metadata.get("reasoning_watchdog", "off") == "on"' in sec
        assert "agent.reasoning_watchdog = False" in sec  # default init
        v3 = Path("letta/agents/letta_agent_v3.py").read_text()
        assert "llm_adapter.reasoning_watchdog = getattr(self" in v3
        ada = Path("letta/adapters/simple_llm_stream_adapter.py").read_text()
        assert "reasoning_watchdog=self._reasoning_watchdog_enabled()" in ada
        # reset in finally
        iface = Path("letta/interfaces/openai_streaming_interface.py").read_text()
        assert iface.count("self._watchdog_tripped = False") == 2  # ctor + finally-reset

    def test_limit_constant_and_doc(self):
        iface = Path("letta/interfaces/openai_streaming_interface.py").read_text()
        assert "_WATCHDOG_RUN_LIMIT = 24" in iface
        llm = Path("letta/schemas/llm_config.py").read_text()
        assert "wedged-generation protection" in llm  # Part B doc note
