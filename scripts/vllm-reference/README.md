# vLLM reference deployment (DRAFT — v0.16.43 A3 stage material)

**Provenance:** extracted verbatim from `vllm-reference.zip` (Ryan, 2026-11-26).
**Status: untested draft** — written with no GPU and no Docker daemon. Only
`fetch_models.py`'s verifier and `check_vllm_isolation.sh`'s canary logic have
been exercised. `sh -n` / `bash -n` / `py_compile` pass on all files.

This is the reference for the v0.16.43 vLLM stage (plan REV 3, A3), against
ER-8.4 (hardened vLLM reference deployment) and ER-8.3 (model provenance —
weights pinned by revision + SHA-256 via `fetch_models.py`). The overlay
assumes it is invoked relative to the repo root:

    docker compose -f scripts/smoke/docker-compose.vllm.yml ...
    (paths inside the files reference scripts/smoke/docker-compose.smoke.yml)

N-1 (the OPENAI_API_KEY fallback disclosure) is fixed in .41; the BYOK
interim documented in NOTES.md step 5 remains the registration path.
N-2: embeddings stay on Ollama — this stage tests chat/tool-calling/streaming.
At first run: replace every `PIN_ME_*` value (digests, keys) and confirm the
N-4 pin list (image digests, VLLM_TOOL_PARSER per model, limit-mm-per-prompt
syntax, writable-paths tmpfs, shm_size).
