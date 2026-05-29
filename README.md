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
- **Landlock + seccomp-BPF sandbox.** Kernel-level filesystem and network isolation for tool execution subprocesses. Works inside Docker Desktop containers with zero extra flags, on bare Linux, no external dependencies. Replaces the Docker sandbox backend (which required root-equivalent Docker socket access).
- **Agent OS-compatible policy engine.** Argument-level rules, rate limiting, regex matching, YAML loading. Schema matches Microsoft's Agent Governance Toolkit so policy files are interchangeable. No AGT dependency.
- **Unified audit logging.** Every security event (tool execution, denial, approval request, canary detection, secret detection, message sent) is logged with agent ID, actor, step/run IDs, and matched rule. Fork-only module — no upstream equivalent.
- **Canary exfiltration detection.** A random canary value is injected into the agent's memory. Before every tool call, the arguments are checked for the canary. If found, the tool call is blocked — that's a prompt exfiltration attack. Post-LLM output filtering also redacts canary tokens from assistant messages before they reach the user.
- **Secret scanning.** Entropy + regex detection in tool arguments. Shannon entropy ≥4.5 bits/char in strings of 20+ chars is the primary signal. Regex patterns (AWS keys, GitHub tokens, PEM keys, Slack tokens, Stripe keys) are confirmatory. Routed through the policy engine via `CONTAINS_SECRET` operator. Default: audit (log + warn in tool result, don't block). Configurable to deny or require approval.
- **Tool calling mode override.** `tool_calling_mode: "native" | "prompt"` on LLMConfig overrides auto-detection entirely. For models unreliable at native tool calling under the real system prompt (e.g., reasoning models on Ollama), set `tool_calling_mode="prompt"` and skip the probe.
- **Ollama reasoning model auto-detection.** The Ollama provider detects the `thinking` capability via `/api/show` and sets `enable_reasoner=True`. The V1 agent path now honors provider-detected reasoning capability instead of overriding it to `False`. Thinking mode (`chat_template_args: {enable_thinking: true}`) is sent in the LLM request for all `enable_reasoner` models.
- **Prompt-based send_message fallback.** When prompt-based tool calling can't parse a tool call from the model's text, the fallback now checks if `send_message` is in the agent's tool list. Custom agents without `send_message` get the text as an assistant message instead of a crash loop.
- **Sandbox staging directory.** Sandbox tools that return large data (e.g., 2.5MB query results) can write to `LETTA_STAGING_DIR` instead of returning everything in the tool result. The runtime validates staged files (1MB per-file, 50MB per-agent, path safety) and moves them to the agent's persistent file directory. The sandbox can only write to `.staging/` — not directly to agent files.
- **grep_files.** Searches the agent's file workspace recursively. Supports regex pattern, include filter (regex on filename), context lines, and pagination (20 matches per page). Skips `.staging/` and binary files.

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

## Landlock sandbox

Kernel-level isolation for tool execution subprocesses. No Docker socket, no external dependencies, no C compilation. Works inside Docker Desktop containers (kernel 6.12.76-linuxkit, Landlock ABI v6) and on bare Linux 5.13+.

**How it works:** A wrapper script (`letta/bin/letta_landlock_wrapper.py`) is launched as a separate process via `asyncio.create_subprocess_exec(close_fds=True)`. The wrapper applies Landlock filesystem/network restrictions and a seccomp-BPF syscall filter, then execs the tool script. Restrictions are irreversible once applied.

**Security properties:**
- Filesystem: read/write/execute only on explicitly allowed paths
- Network: TCP connect/bind opt-in via `allow_tcp_connect`/`allow_tcp_bind` (Landlock ABI v4+)
- IPC: abstract Unix socket and signal scoping (Landlock ABI v6)
- Syscalls: seccomp blocks fork/clone/clone3/vfork/ptrace/mount/chroot/etc.
- `/proc` access: denied except `/proc/self/`
- FD isolation: `close_fds=True` prevents access to parent's DB connections and HTTP sockets

**Sandbox type selection hierarchy:** E2B → Modal → Landlock → Local. Landlock is auto-detected at runtime via `tool_settings.landlock_available` (queries the kernel ABI version). If Landlock is not available, falls back to LOCAL with a loud warning.

**Network access for tools:**
```python
# Tools that need network access can declare it via metadata:
tool.metadata_["requires_network"] = True
# The executor auto-promotes this to allow_tcp_connect=True on the Landlock config
```

DNS over TCP is forced via `options use-vc` in `/etc/resolv.conf` (added in the Dockerfile) because Landlock blocks UDP.

**Configuration:**
```python
from letta.schemas.sandbox_config import LandlockSandboxConfig

config = LandlockSandboxConfig(
    allowed_read_paths=["/usr", "/lib", "/lib64", "/etc"],
    allowed_write_paths=["/path/to/tool_exec_dir"],
    allowed_execute_paths=["/usr/bin", "/usr/local/bin"],
    allow_tcp_connect=False,  # opt-in
    allow_tcp_bind=False,     # opt-in
    block_fork=True,          # prevent fork bombs
    timeout=180,
)
```

**Docker sandbox removed.** The Docker sandbox backend (which required Docker socket access — a root-equivalent privilege) has been removed from this fork. Landlock replaces it with kernel-level isolation that doesn't require any special host access. Existing agents with `sandbox_type='docker'` are migrated to `sandbox_type='local'` via Alembic; runtime auto-detection routes to Landlock when the kernel supports it.

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

# Security tests (196 tests, no servers needed)
pytest tests/test_audit_logger.py tests/test_canary_checker.py \
       tests/test_policy_engine.py tests/test_policy_checker.py \
       tests/test_agent_step_recorder.py tests/test_constraint_relaxation.py \
       tests/test_tool_capability_probe.py tests/test_ollama_capability_filter.py

# Landlock sandbox tests (28 tests, Linux with Landlock ABI >= 1 required)
pytest tests/test_landlock_sandbox.py tests/test_landlock_ctypes.py

# Integration tests (requires live servers)
RUN_LOCAL_INTEGRATION_TESTS=1 \
LETTA_SERVER_URL=http://localhost:8383 \
VLLM_SERVER_URL=http://localhost:9000 \
pytest tests/integration_test_local_model_agent.py -v
```

## Known issues

- **vLLM on macOS Metal GPU**: OOMs under sustained load. Use `--gpu-memory-utilization 0.7` to reduce KV cache allocation. The token budget enforcer (default `context_window_ratio=0.7`) helps prevent this by stopping the agent before it exceeds the GPU's real capacity.
- **Non-deterministic tool calling**: Some models (Gemma 4 on vLLM) produce native tool calls sometimes and text other times. The probe tries twice; the agent loop's retry mechanism handles occasional failures. For models that are unreliable at native tool calling, set `tool_calling_mode="prompt"` to skip the probe.
- **vLLM requires flags**: Native tool calling on vLLM needs `--enable-auto-tool-choice --tool-call-parser hermes`. Without these, all models fall back to prompt mode (which still works).
- **Correction factor placeholders**: `TOKEN_ESTIMATE_CORRECTION` values for specific model families (qwen, llama, etc.) are placeholders (`None`). They fall back to `DEFAULT_TOKEN_CORRECTION = 2.5` until a benchmark script measures real ratios. Live calibration makes this less critical — the first API response provides the real ratio.
- **vLLM reasoning models**: vLLM doesn't expose model capability metadata like Ollama's `/api/show`. Reasoning models served via vLLM need `reasoning=True` set explicitly on agent creation. The `enable_reasoner` flag is not auto-detected for vLLM.

## Differences from upstream

| Area | Upstream | This fork |
|------|----------|-----------|
| Target | Cloud models (OpenAI, Anthropic) | Local inference servers |
| Tool calling | Assumes native support | Auto-detects, falls back to prompt, mode override |
| Provider types | Cloud-focused | 6 additional local providers |
| Reasoning models | Hardcoded lists | Provider-detected (Ollama thinking capability) |
| Model constraints | Not implemented | Full schema with auto-apply |
| Repair pipeline | None | Handles malformed JSON from local models |
| Model settings | OpenAI-centric | OllamaModelSettings, VLLMModelSettings |
| Token estimation | bytes/4 (inaccurate for subword tokenizers) | Model-family correction + live calibration |
| Token budget | No enforcement | Per-step, per-run, context-window ratio |
| Error loops | Retry until max steps | Circuit breaker with force-compact |
| Sandbox | LOCAL (host subprocess), E2B, Modal | + LANDLOCK (kernel-level isolation, no Docker socket) |
| Policy engine | Two-list (denied_tools, approval_required_tools) | Agent OS-compatible rules + rate limiting + YAML + secret scanning |
| Audit logging | None | Unified audit trail for all security events |
| Exfiltration detection | None | Canary injection + output filtering |
| Secret scanning | None | Entropy + regex via policy engine |
| File tools | grep_files is a stub | grep_files implemented, sandbox staging dir for large output |

## Upstream sync

This fork tracks upstream and merges periodically. The upstream base is tagged at `upstream-base-113153571`. To merge upstream changes:

```bash
git remote add upstream https://github.com/letta-ai/letta.git
git fetch upstream
git merge upstream/main
# Resolve conflicts, run tests
pytest tests/test_tool_capability_probe.py tests/test_model_constraints.py \
       tests/test_tool_call_repair.py tests/test_prompt_tool_calling.py \
       tests/test_local_model_hardening.py tests/test_landlock_sandbox.py \
       tests/test_policy_engine.py tests/test_audit_logger.py \
       tests/test_canary_checker.py tests/test_policy_checker.py
```

## License

Same as upstream Letta — see [LICENSE](LICENSE).
