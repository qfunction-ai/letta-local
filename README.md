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
- **Token accuracy for local models.** Model-family correction factors (default 2.5x) replace the naive `bytes/4` token estimate. Live calibration caches server-reported `prompt_tokens` after the first call for accurate subsequent estimates.
- **Token budget enforcement.** Per-step, per-run, and context-window ratio (default 0.7) budget checks break the agent out of VRAM OOM death spirals on local hardware.
- **Circuit breaker for error loops.** Tracks consecutive LLM errors (3) and context overflows (2). When threshold is exceeded, force-clears the context window instead of retrying with an even larger prompt.
- **Docker sandbox.** Containerized tool execution with security defaults: network isolation, resource limits, non-root execution, read-only rootfs. No cloud API key required.
- **Agent OS-compatible policy engine.** Argument-level rules, rate limiting, regex matching, YAML loading. Schema matches Microsoft's Agent Governance Toolkit so policy files are interchangeable. No AGT dependency.

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

## Token accuracy

Local models don't all use the same tokenizer. The fork's default `bytes/4` estimate underestimates for most subword tokenizers (GPT-4, BPE, SentencePiece). The `token_correction` module fixes this:

- **Static correction table.** `TOKEN_ESTIMATE_CORRECTION` maps model family prefixes (e.g., `"qwen"`, `"llama"`) to measured ratios. Unmeasured models fall back to `DEFAULT_TOKEN_CORRECTION = 2.5`.
- **Live calibration.** `LiveTokenCalibration` caches server-reported `prompt_tokens` from the first API response. Subsequent estimates use the measured ratio instead of the static table. Cold start uses the static table; warm path uses live data.

The correction factors are wired into:
- Pre-call token estimates in Ollama, vLLM, llama.cpp, and chat_completion_proxy adapters
- Context window estimation in the summarizer
- `LettaUsageStatistics.context_tokens` population from server-reported values

## Token budget enforcement

The `TokenBudget` class enforces three budget layers after each LLM call:

| Budget type | Default | Config key |
|---|---|---|
| Per-step | No limit | `agent.metadata.max_step_tokens` |
| Per-run | No limit | `agent.metadata.max_run_tokens` |
| Context window | 70% of `context_window` | `agent.metadata.context_window_ratio` |

The 0.7 default matches vLLM's `--gpu-memory-utilization 0.7`. When the budget is exceeded, the agent stops with `max_tokens_exceeded` instead of OOMing the GPU.

## Circuit breaker

The `AgentCircuitBreaker` breaks consecutive error death spirals:

| Error type | Threshold | Recovery action |
|---|---|---|
| `llm_api_error` | 3 consecutive | Force-clear context window |
| `context_window_overflow` | 2 consecutive | Force-clear context window |

Without the circuit breaker, a context overflow causes another overflow on retry (larger context), causing another overflow, forever. The breaker detects the spiral and force-clears the context window (memory blocks persist across compactions).

## Docker sandbox

Containerized tool execution for local model users who need isolation between the agent and the host:

```bash
# Build the sandbox image
docker build -t letta-sandbox:latest -f Dockerfile.letta-sandbox .
```

Security defaults:
- `network_mode="none"` — no network access (opt-in via `bridge`)
- `user="1001:1001"` — non-root execution
- `read_only=True` — read-only rootfs with tmpfs `/tmp`
- `cap_drop=["ALL"]` + `no-new-privileges`
- `mem_limit="512m"`, `pids_limit=100`, `cpu_count=1.0`

Container lifecycle: one container per agent run, lazy-created on first tool call, reused across calls, cleaned up on exit. Orphan reaper kills stale containers on startup.

Enable Docker sandbox:
```bash
# Docker is auto-detected. If Docker is available, it's used as the default
# sandbox backend (after E2B if an E2B API key is set).
# To disable:
LETTA_DOCKER_SANDBOX_ENABLED_FIELD=false letta-server
```

## Policy engine

Agent OS-compatible policy engine for fine-grained tool call control. Schema matches Microsoft's Agent Governance Toolkit — policy files are interchangeable. No AGT dependency.

**Features:**
- Argument-level rules: deny `web_search` if `query` contains "internal"
- Rate limiting: per-tool and global call limits
- Regex matching on tool arguments
- YAML policy loading
- Backwards compatible with legacy `denied_tools` / `approval_required_tools` lists

**Example policy (YAML):**

```yaml
version: "1.0"
name: local-model-safety
rules:
  - name: block-destructive-sql
    condition:
      field: tool_name
      operator: eq
      value: database_query
    pattern: "DROP|TRUNCATE"
    action: deny
    priority: 100
    message: "Destructive SQL operations are blocked"

  - name: block-internal-queries
    condition:
      field: tool_args.query
      operator: matches
      value: "internal|confidential|secret"
    action: deny
    priority: 80
    message: "Queries containing internal/confidential/secret are blocked"

  - name: audit-archival
    condition:
      field: tool_name
      operator: eq
      value: archival_memory_insert
    action: audit
    priority: 10
    message: "Archival memory insert logged for audit"

defaults:
  action: allow
  max_tool_calls: 100
```

**Rate limiting:**

```python
from letta.security.policy import ToolCallPolicy, PolicyDefaults

policy = ToolCallPolicy(
    max_calls_per_tool={"web_search": 10, "archival_memory_insert": 5},
    defaults=PolicyDefaults(max_tool_calls=100),  # global limit
)
```

**Dot-path resolution** (Agent OS can't do this):

```yaml
rules:
  - name: block-sensitive-paths
    condition:
      field: tool_args.file_path
      operator: matches
      value: "/etc/|/var/log/|~/.ssh/"
    action: deny
```

## Running tests

```bash
# Unit tests (108 tests, no servers needed)
pytest tests/test_tool_capability_probe.py tests/test_model_constraints.py \
       tests/test_tool_call_repair.py tests/test_prompt_tool_calling.py \
       tests/test_ollama_capability_filter.py tests/test_local_model_providers.py

# Local model hardening tests (43 tests, no servers needed)
pytest tests/test_local_model_hardening.py

# Docker sandbox tests (25 tests, no Docker needed)
pytest tests/test_docker_sandbox.py

# Policy engine tests (73 tests, no servers needed)
pytest tests/test_policy_engine.py

# Integration tests (requires live servers)
RUN_LOCAL_INTEGRATION_TESTS=1 \
LETTA_SERVER_URL=http://localhost:8383 \
VLLM_SERVER_URL=http://localhost:9000 \
pytest tests/integration_test_local_model_agent.py -v
```

## Known issues

- **vLLM on macOS Metal GPU**: OOMs under sustained load. Use `--gpu-memory-utilization 0.7` to reduce KV cache allocation. The token budget enforcer (default `context_window_ratio=0.7`) helps prevent this by stopping the agent before it exceeds the GPU's real capacity.
- **Non-deterministic tool calling**: Some models (Gemma 4 on vLLM) produce native tool calls sometimes and text other times. The probe tries twice; the agent loop's retry mechanism handles occasional failures.
- **vLLM requires flags**: Native tool calling on vLLM needs `--enable-auto-tool-choice --tool-call-parser hermes`. Without these, all models fall back to prompt mode (which still works).
- **Correction factor placeholders**: `TOKEN_ESTIMATE_CORRECTION` values for specific model families (qwen, llama, etc.) are placeholders (`None`). They fall back to `DEFAULT_TOKEN_CORRECTION = 2.5` until a benchmark script measures real ratios. Live calibration makes this less critical — the first API response provides the real ratio.

## Differences from upstream

| Area | Upstream | This fork |
|------|----------|-----------|
| Target | Cloud models (OpenAI, Anthropic) | Local inference servers |
| Tool calling | Assumes native support | Auto-detects, falls back to prompt |
| Provider types | Cloud-focused | 6 additional local providers |
| Model constraints | Not implemented | Full schema with auto-apply |
| Repair pipeline | None | Handles malformed JSON from local models |
| Model settings | OpenAI-centric | OllamaModelSettings, VLLMModelSettings |
| Token estimation | bytes/4 (inaccurate for subword tokenizers) | Model-family correction + live calibration |
| Token budget | No enforcement | Per-step, per-run, context-window ratio |
| Error loops | Retry until max steps | Circuit breaker with force-compact |
| Sandbox | LOCAL (host subprocess), E2B, Modal | + DOCKER (containerized, no API key needed) |
| Policy engine | Two-list (denied_tools, approval_required_tools) | Agent OS-compatible rules + rate limiting + YAML |

## Upstream sync

This fork tracks upstream and merges periodically. The upstream base is tagged at `upstream-base-113153571`. To merge upstream changes:

```bash
git remote add upstream https://github.com/letta-ai/letta.git
git fetch upstream
git merge upstream/main
# Resolve conflicts, run tests
pytest tests/test_tool_capability_probe.py tests/test_model_constraints.py \
       tests/test_tool_call_repair.py tests/test_prompt_tool_calling.py \
       tests/test_local_model_hardening.py tests/test_docker_sandbox.py tests/test_policy_engine.py
```

## License

Same as upstream Letta — see [LICENSE](LICENSE).
