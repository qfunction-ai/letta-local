# vLLM reference deployment — draft notes

**Date:** 2026-11-26
**For:** the LettaLocal agent's v0.16.42 vLLM stage (relay via Ryan), with the "vLLM Testing Process" doc
**Status: untested draft.** It was written with no GPU and no Docker daemon. The verifier script was exercised locally; nothing else was run.

Labels: **Verified** = I ran it. **Read** = traced in code (LettaLocal `9a50433d`) or vLLM docs. **Inferred** = reasoning, not run.

## Files

| File | What it is | Tested |
|---|---|---|
| `docker-compose.vllm.yml` | Overlay on `scripts/smoke/docker-compose.smoke.yml`: vLLM on an internal-only network, an nginx proxy as the only bridge, Docker secrets for the two keys, container hardening, GPU reservation | No |
| `proxy/vllm.conf.template` | nginx site: allowlists `GET /v1/models` and `POST /v1/chat/completions`, checks the client key, swaps in the upstream key, everything else 403; rate and connection limits; body-size cap | No |
| `proxy/entrypoint.sh` | Renders the template from the secrets into tmpfs and refuses weak, mismatched or unsafe keys | `sh -n` only |
| `fetch_models.py` | ER-8.3 weights fetch and verify: `record` proposes a lock file for review, `verify` requires the exact file set and every SHA-256 to match | **Verified**: pass, hash-mismatch, extra-file and missing-file cases all behave (exit 0 / 1 / 1 / 1) |
| `check_vllm_isolation.sh` | Stage-1 config and stage-3 isolation checks, following the v0.16.40 rig rules: no pipe-into-`grep -q`, positive controls, canary self-test | `bash -n`; canary logic **Verified** in isolation |

## Findings while drafting (for the LettaLocal agent)

### N-1 — LettaLocal can't send a vLLM API key from configuration, and may send the OpenAI key instead (fix in .42; Read)

- The env-configured vLLM provider is built without any API key (`letta/server/server.py` around lines 278–293), and `ModelSettings` has no `vllm_api_key` (`letta/settings.py` lines 250–252).
- At request time, `OpenAIClient._prepare_client_kwargs` (`letta/llm_api/openai_client.py` around lines 176–206) uses a BYOK key if there is one; otherwise **`model_settings.openai_api_key` or `OPENAI_API_KEY`**; otherwise `"DUMMY_API_KEY"`.

Consequences:
1. With `VLLM_API_BASE`, a protected vLLM (ER-8.4: key required) gets `DUMMY_API_KEY` and returns 401.
2. If an operator has also set `OPENAI_API_KEY` for another reason, **the real OpenAI key is sent as the bearer token to the vLLM endpoint**, and likewise to any other OpenAI-compatible base provider that goes through this client. For a local vLLM that's low impact. For an endpoint on another host, it discloses the credential.

**Fix:**
- add `vllm_api_key`, held as a `Secret` and passed to `VLLMProvider`;
- only fall back to `OPENAI_API_KEY` when the endpoint actually is OpenAI;
- add a unit test that a non-OpenAI endpoint never receives the OpenAI key.

**Interim:** the overlay doesn't set `VLLM_API_BASE`. Register vLLM as a **BYOK provider** instead (below): `get_byok_overrides` then supplies the stored key, encrypted at rest. The overlay also forces `OPENAI_API_KEY` empty.

### N-2 — No embeddings through the vLLM provider (Read; corrects the testing-process doc)

`VLLMProvider.list_embedding_models_async` returns `[]` (`letta/schemas/providers/vllm.py` lines 55–58). So "EmbeddingGemma via vLLM" (stage 2.4 of the doc) won't work as written.

Options:
- (a) keep embeddings on another local server (Ollama today) and test them in the existing rig, not the vLLM stage;
- (b) add embedding support to the provider in .42, which needs a second vLLM instance or a pooling-capable one, plus `/v1/embeddings` in the proxy allowlist.

I'd take (a) for .42. **I'll correct stage 2.4 in the doc to match whichever Ryan picks.**

### N-3 — The sandbox shares the server's network position (Read + Inferred; shapes stage 3)

Tool code runs in a Landlock sandbox **inside the letta container**, so it has the same network namespace. Delta's tools call `http://backend:8000` from the sandbox, so the sandbox has network access (Read, Delta compose and tool templates).

Network rules therefore can't tell the server from tool code:
- The **direct** path to vLLM is still closed, because the letta container isn't on the `inference` network.
- The **proxy**, though, is reachable from tool code. What stops tool code is the proxy key: it must never be readable from the sandbox (env, `/proc`, files).

That's the TODO at the end of `check_vllm_isolation.sh`. It needs the tool-run API to execute a probe from inside the sandbox.

### N-4 — Things to pin or confirm at first run

| Item | Where | Note |
|---|---|---|
| `nginxinc/nginx-unprivileged` digest | compose | Runs as uid 101, listens on 8080 |
| `vllm/vllm-openai` digest | compose | ≥ the recorded floor; the auth-bypass CVE was fixed in 0.22.0 |
| Tool-call parser per model | `VLLM_TOOL_PARSER` | Gemma 4: `gemma4` per NVIDIA's parser table (upstream vLLM name). Nemotron 3 Nano: confirm against the pinned vLLM version. NVIDIA's table lists `nemotron_nano` as a Dynamo-only alias for `qwen3_coder`. |
| `--limit-mm-per-prompt` syntax | compose | The JSON form is version-dependent; confirm with `vllm serve --help` on the pinned image |
| Writable paths under a read-only rootfs | compose | vLLM and torch caches are redirected to `/tmp` (tmpfs). If startup still writes elsewhere, add a tmpfs mount; don't drop `read_only` |
| `shm_size`, `mem_limit` | compose | Sized for one L4 and a 26B MoE at 4-bit; adjust from the first run |
| Key format | entrypoint | `openssl rand -hex 32`, two different keys |

## Setup sketch (first run)

1. `python3 fetch_models.py record --weights-dir /srv/weights --model <name>` → review the lock-file diff (trainer, repo, revision, license, files) → commit it.
2. On every run: `python3 fetch_models.py verify --weights-dir /srv/weights`. Stop on any FAIL.
3. Create the two secrets: `openssl rand -hex 32 > proxy_key; openssl rand -hex 32 > upstream_key; chmod 600 *`.
4. Start the stack: `docker compose -f scripts/smoke/docker-compose.smoke.yml -f docker-compose.vllm.yml up -d`, with `VLLM_MODEL_DIR`, `VLLM_SERVED_NAME`, `VLLM_TOOL_PARSER`, `VLLM_WEIGHTS_DIR` and `VLLM_SECRETS_DIR` set.
5. Register the BYOK provider on LettaLocal: `POST /v1/providers/` with `{"name": "vllm-local", "provider_type": "vllm", "base_url": "http://vllm-proxy:8080/v1", "api_key": "<proxy_key>"}` and the server password header. Keep "vllm" in the name, because LettaLocal switches tool choice to `auto` when the model handle contains "vllm" (Read, `openai_client.py` around line 156). **Inferred:** that the BYOK model handle carries the provider name; confirm on the first run.
6. Run `check_vllm_isolation.sh`, then the stage-2 functional checks.
