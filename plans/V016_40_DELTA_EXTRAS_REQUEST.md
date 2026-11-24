# Request to Delta: confirm the extras your stack needs (v0.16.40 C-2)

**From:** LettaLocal2 (via Ryan) · **Date:** 2026-11-24 · **Channel:** usual file-based handoff

The v0.16.40 plan (REV 2, approved) trims the container from `--all-extras` to the extras consumers actually use — the dependency baseline (353 advisories) shrinks with it, and an extra that fails at runtime fails when an optional import is first reached, so we need this right before the trim, not after.

**The container currently installs all 13 extras.** Below are the drop candidates. For each, confirm whether Delta's code or tools import anything from it (directly or via a server feature you rely on). Answer per line: **KEEP** (with what uses it) or **DROP**.

| Extra | Contents | Drop rationale |
|---|---|---|
| `desktop` | langchain, langchain-community, wikipedia, locust, magika, tiktoken, aiosqlite, sqlite-vec, pgvector, websockets, fastapi, uvicorn, async-lru | The upstream desktop-app bundle. langchain-community is where much of the advisory noise (nltk etc.) enters. Delta runs its own React+FastAPI frontend, not this. |
| `external-tools` | langchain, langchain-community, wikipedia, exa-py, turbopuffer | **The one to check carefully**: if Delta agents use the built-in `web_search` tool, does it import exa-py at runtime? And does anything you run use turbopuffer (archival/vector search)? |
| `bedrock` | boto3, aioboto3 | AWS Bedrock provider. You run local Ollama / OpenAI-compatible endpoints. |
| `modal` | modal | The v2 remote sandbox (Modal). Sandbox execution is Landlock-local since v0.16.30. |
| `cloud-tool-sandbox` | e2b-code-interpreter | E2B, the `run_code` builtin's remote executor. If nothing invokes `run_code`, this goes. |
| `pinecone` | pinecone | Vector store backend. You use PG/pgvector. |
| `experimental` | uvloop, granian | Alternative servers; the container runs uvicorn. |
| `profiling` | ddtrace | Datadog tracing. |

**Presumed KEEP (no action needed unless you object):** `postgres` (PG backend), `server` (uvicorn/fastapi/websockets — the container's own server), `sqlite` (local dev), `redis` (if you use background streaming; otherwise say DROP), `dev` (never in the container image).

Also confirm: does Delta's stack import anything from the `letta` package that lives only in an extra's module path (e.g. `letta.services.file_processor.*` variants)? If unsure, say so — the trim ships with an import smoke test that walks the mounted routers and tool executors, but that covers the server's surface, not your client code's.

Reply as a file in your usual handoff location; Ryan relays. The trim lands in v0.16.40 only after your confirmation, and you'll run your suite against the trimmed image before it's adopted.
