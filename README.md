# Letta Local

A fork of [Letta](https://github.com/letta-ai/letta) optimized for **local model inference**.

The upstream Letta project is built for cloud models (OpenAI, Anthropic, etc.). This fork makes Letta's stateful agent framework work with local inference servers — Ollama, vLLM, SGLang, LocalAI, llama.cpp, and MLX — without requiring a cloud API key.

## What this fork does differently

Upstream assumes models support native OpenAI-style tool calling. Most local models don't. This fork adds:

- **Auto-detection of tool calling capability.** A runtime probe checks whether the model supports native tool calling. If it does (e.g., Qwen2.5 on vLLM), native mode is used. If it doesn't (e.g., phi3:mini on Ollama), tools are embedded in the system prompt and parsed from text output.
- **Prompt-based tool calling.** For models that don't support the `tools` API parameter, tool schemas are injected into the system prompt as text, and the model's text output is parsed as a JSON tool call.
- **Tool call repair pipeline.** Local models produce malformed JSON — code fences, extra text, missing brackets. The repair pipeline handles it.
- **ModelConstraints schema.** Declarative capability degradation: `tool_calling_mode`, `json_repair_level`, `tool_call_retry_count`, `disable_structured_output`. Auto-applied for local providers.
- **6 new provider types.** Ollama, vLLM, SGLang, LocalAI, llama.cpp, and BitNet — with model discovery via each server's API.

## Supported providers

| Provider | Status | Model discovery | Tool calling |
|----------|--------|-----------------|--------------|
| Ollama | Working | /api/tags + /api/show | Auto (native or prompt) |
| vLLM | Working | /v1/models | Auto (native with --enable-auto-tool-choice) |
| SGLang | Code ready, untested | /v1/models | Auto (generic probe) |
| LocalAI | Code ready, untested | /v1/models | Auto (generic probe) |
| llama.cpp | Code ready, untested | /v1/models | Auto (generic probe) |
| MLX | Code ready, untested | /v1/models | Auto (generic probe) |
| BitNet | Stub | — | — |

## Quick start

### Prerequisites

- Python 3.11+
- PostgreSQL 16 with pgvector extension
- At least one local inference server (Ollama, vLLM, etc.)

### 1. Install

```bash
cd letta-local
pip install -e .
```

### 2. Set up PostgreSQL

```bash
docker run -d --name letta-postgres -p 5433:5432 \
  -e POSTGRES_USER=letta -e POSTGRES_PASSWORD=letta -e POSTGRES_DB=letta \
  pgvector/pgvector:pg16
```

Run Alembic migrations:

```bash
cd letta
alembic upgrade head
```

### 3. Start a local inference server

Ollama:
```bash
ollama pull phi3:mini
ollama serve
```

vLLM (with native tool calling):
```bash
vllm serve Qwen/Qwen2.5-1.5B-Instruct --port 9000 \
  --enable-auto-tool-choice --tool-call-parser hermes
```

### 4. Start the Letta server

```bash
LETTA_PG_URI="postgresql+asyncpg://letta:letta@localhost:5433/letta" \
python -c "from letta.server.rest_api.app import start_server; start_server(port=8383)"
```

### 5. Create an agent

```python
from letta_client import Letta

client = Letta(base_url="http://localhost:8383")

# Create an agent using a local model
agent = client.agents.create(
    name="my-local-agent",
    model="ollama/phi3:mini",  # or "vllm/Qwen/Qwen2.5-1.5B-Instruct"
    embedding="letta/letta-free",
)

# Send a message
response = client.agents.messages.create(
    agent_id=agent.id,
    messages=[{"role": "user", "content": "Hello!"}],
)
```

The auto-detection probe runs on the first request. If the model supports native tool calling, it uses that. Otherwise, it falls back to prompt-based tool calling automatically.

## How tool calling works

### Native mode (models that support it)

The standard OpenAI `tools` parameter is used. The model returns structured `tool_calls` in the response. Works with Qwen2.5, Gemma 4, Mistral, etc. on vLLM (with `--enable-auto-tool-choice`).

### Prompt mode (models that don't support it)

1. Tool schemas are converted to text and injected into the system prompt.
2. The model generates text containing a JSON tool call.
3. The repair pipeline handles malformed output (code fences, extra text, etc.).
4. The parsed tool call is executed and the result is returned to the model.

### Auto mode (default for local providers)

On first request, the probe sends a test request with one tool:
- **Ollama**: Queries `/api/show` for the model's `capabilities` array. If `"tools"` is present, native mode. Zero inference cost.
- **Other providers**: Sends a minimal test request. If the response contains native `tool_calls`, native mode. Otherwise, prompt mode.

Results are cached in-memory. The probe only runs once per model per process.

## Configuration

### ModelConstraints

Set on agent creation or via `model_settings`:

```python
model_settings = {
    "constraints": {
        "tool_calling_mode": "auto",     # "native", "prompt", or "auto"
        "tool_call_retry_count": 3,      # retries on malformed tool calls
        "disable_structured_output": True,
        "json_repair_level": "aggressive",  # "none", "minimal", "aggressive"
    }
}
```

### Explicit mode

If you know your model doesn't support native tool calling, force prompt mode:

```python
model_settings = {
    "constraints": {
        "tool_calling_mode": "prompt",
    }
}
```

## Running tests

```bash
# Unit tests (108 tests, no servers needed)
pytest tests/test_tool_capability_probe.py tests/test_model_constraints.py \
       tests/test_tool_call_repair.py tests/test_prompt_tool_calling.py \
       tests/test_ollama_capability_filter.py tests/test_local_model_providers.py

# Integration tests (requires live servers)
RUN_LOCAL_INTEGRATION_TESTS=1 \
LETTA_SERVER_URL=http://localhost:8383 \
VLLM_SERVER_URL=http://localhost:9000 \
pytest tests/integration_test_local_model_agent.py -v
```

## Known issues

- **vLLM on macOS Metal GPU**: OOMs under sustained load. Use `--gpu-memory-utilization 0.7` to reduce KV cache allocation.
- **Non-deterministic tool calling**: Some models (Gemma 4 on vLLM) produce native tool calls sometimes and text other times. The probe tries twice; the agent loop's retry mechanism handles occasional failures.
- **vLLM requires flags**: Native tool calling on vLLM needs `--enable-auto-tool-choice --tool-call-parser hermes`. Without these, all models fall back to prompt mode (which still works).

## Differences from upstream

| Area | Upstream | This fork |
|------|----------|-----------|
| Target | Cloud models (OpenAI, Anthropic) | Local inference servers |
| Tool calling | Assumes native support | Auto-detects, falls back to prompt |
| Provider types | Cloud-focused | 6 additional local providers |
| Model constraints | Not implemented | Full schema with auto-apply |
| Repair pipeline | None | Handles malformed JSON from local models |
| Model settings | OpenAI-centric | OllamaModelSettings, VLLMModelSettings |

## Upstream sync

This fork tracks upstream and merges periodically. The upstream base is tagged at `upstream-base-113153571`. To merge upstream changes:

```bash
git remote add upstream https://github.com/letta-ai/letta.git
git fetch upstream
git merge upstream/main
# Resolve conflicts, run tests
pytest tests/test_tool_capability_probe.py tests/test_model_constraints.py \
       tests/test_tool_call_repair.py tests/test_prompt_tool_calling.py
```

## License

Same as upstream Letta — see [LICENSE](LICENSE).
