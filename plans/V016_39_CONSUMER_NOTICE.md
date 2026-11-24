# v0.16.39 Consumer Notice — Delta, Nabla, Epsilon

**Date:** 2026-11-24. Read this before adopting v0.16.39.

## Breaking / behavioral changes

1. **B-2 — upstream domains removed.** The `https://app.letta.com` CORS origin is gone, the "letta" provider and `letta-free` LLM/embedding presets are gone, and `LLMConfig.default_config("letta")` / `EmbeddingConfig.default_config("letta")` now raise `ValueError` instead of silently returning upstream endpoints. **Action:** run the startup stored-agent check against your DB after upgrading — it reports (never rewrites) agents whose stored configs point at the removed endpoints; recreate those agents with local provider configs. Verified against your configs: none of you use the upstream provider or CORS origin.
2. **B-12 — tool dependency installs are wheel-only.** `pip_requirements` on tools/sandbox configs now install with `--only-binary=:all:`. A package with no wheel for your platform fails with a clear pip error instead of building an sdist. **Action:** if any of your custom tools depend on sdist-only packages, they will fail to install on .39.
3. **B-12 — pip/venv subprocess environment narrowed.** Installs see PATH, LANG/LC_*, TZ, SSL certs, proxy vars, and `PIP_*` only; HOME is a throwaway temp dir. If you relied on env vars reaching pip (e.g. custom index creds via non-PIP_ vars), move them to `PIP_INDEX_URL`-style vars.
4. **B-9 — agent env vars validated.** `POST /v1/agents` and `PATCH /v1/agents/{id}` now 400 when `tool_exec_environment_variables` or `secrets` values contain the server password, the DB URI, or the encryption key (including URL-encoded forms). Same rule the tool/sandbox-config routes already had.
5. **B-8 — generated server password persists across restarts.** If you relied on a NEW password being generated at every start (you shouldn't — set `LETTA_SERVER_PASSWORD` explicitly), the password file `${LETTA_DIR}/server_password` is now reused.
6. **B-4 — new `LETTA_REQUIRE_ENCRYPTION` setting (default off).** Set `LETTA_REQUIRE_ENCRYPTION=true` to make the server refuse to start without `LETTA_ENCRYPTION_KEY`. **Delta: set this in production** — your FAQ says credentials are encrypted in production; this setting is what makes it true. Secure mode without an encryption key now logs a startup WARNING (secrets fall back to plaintext storage).
7. **B-1 — legacy modules deleted.** `letta/agent.py`, `routers/openai/` (never mounted), and `tool_execution_sandbox.py` are gone. No consumer imports them (verified). If you have code importing `letta.agent`, it breaks on import.
8. **B-6 — explicit `response_format` now wins.** An explicitly-set `response_format` overrides the auto-applied `disable_structured_output` constraint on local models. **Nabla (FR-2):** your zero-tool structured-output agent now works without pinning `constraints`.
9. **B-7 — BLAS thread pinning in sandboxed tools.** Sandboxed tool processes get `OPENBLAS_NUM_THREADS=1` and `OMP_NUM_THREADS=1`, mitigating the numpy/OpenBLAS seccomp hang (RC-8). Tools importing numpy/pandas should no longer wedge; report if you still see hangs.

## Not in this release (design went to review)

- **B-5 scoped tool credentials** — agent-aware/multi-agent tools still 401 under secure mode. Design doc: `plans/V016_39_SCOPED_TOOL_CREDENTIALS.md` (also covers RC-7 approval run-scoping and sandboxed installs). No code until the design round passes.

## Operational notes

- The smoke rig now supports `RIG_TARGET=<x.y.z>` to pin the expected server version (check 20 fails on mismatch; unset preserves the unfixed-first protocol).
- A dependency-scan workflow (`dependency-scan.yml`) now runs pip-audit against the lockfile on push + weekly. **Known finding:** the current lockfile carries ~629 advisories across 38 packages (majority with fixes available) — see the release evidence doc. A dependency-upgrade pass is pending Ryan's decision.
