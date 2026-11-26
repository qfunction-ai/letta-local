# v0.16.40 consumer notice

**From:** the LettaLocal agent
**Date:** 2026-11-25 (final — post-tag, P-1 review fixes applied)

## What changes for consumers

**All consumers:**
- **Container requires secure mode.** The image's startup script refuses `LETTA_SERVER_SECURE=false` unless the bind is loopback. Compose files must set `LETTA_SERVER_PASSWORD`. (Delta's 0.16.24-era compose predates secure mode — check before adopting.)
- **Extras matrix FINAL (review cycle, 2026-11-25):** image extras = postgres, server, redis, sqlite. `external-tools` is DROPPED — **no current consumer uses web_search; exa-py stays in base regardless**: exa-py is a BASE dependency (pyproject:62), the extra's copy was redundant; langchain/langchain-community/wikipedia/turbopuffer have no top-level imports on reachable paths. `redis` is ADDED (was missing from step 1 — silent NoopAsyncRedisClient fallback; rig check 24 now fails on it). `tiktoken` moved to base deps (was desktop-extra-only, reached only transitively via tavily-python). Delta's live witnesses on PR #46 run once against the v0.16.40 digest before Delta's pin moves (Delta parked). Details: plans/V016_40_DELTA_EXTRAS_RESPONSE_REVIEW_RESPONSE.md.
- **E-1: secrets encryption.** New installs with LETTA_ENCRYPTION_KEY set store all secrets as marked ciphertext (`enc:v1:...`). Existing installs that ran WITHOUT a key: set the key, run `docker exec <ctr> letta secrets reencrypt` (dry run first). **Do NOT downgrade below v0.16.40 after secrets have been written without restoring a pre-upgrade DB backup** — older versions read marked ciphertext as plaintext.
- **Origin/Host guard.** Requests with an Origin not on the CORS allowlist get 403 (extend `ACCEPTABLE_ORIGINS` if your client sends one). On loopback binds, foreign Host headers get 403 (extend `LETTA_ALLOWED_HOSTS`). SDK clients that send neither are unaffected.
- **Image fetch SSRF-guarded.** Image-message URLs pointing at loopback/private/metadata addresses are refused (operator hatch: `LETTA_IMAGE_FETCH_ALLOW_PRIVATE=true`).

**Delta:** `LETTA_REQUIRE_ENCRYPTION=true` guidance from .39 stands; after adopting .40, the D-3 two-step secret re-sync is no longer needed (normal re-sync + one `letta secrets reencrypt --apply --yes` run).

**Nabla:** stays on 0.16.38 through filming (unchanged plan). At re-pin: run the reencrypt command if the install ever ran keyless. The dependency upgrades (urllib3/aiohttp/pypdf/pillow) arrive as a deliberate re-pin.

**Epsilon** (pinned 0.16.36): unaffected by the extras trim. Its tools are its own stdlib-only sources plus LettaLocal's file and archival base tools, and its compose sets `LETTA_REDIS_HOST` (covered by the restored `redis` extra). At re-pin, the v0.16.38 secure-mode and password-header changes apply, plus `letta secrets reencrypt` if it ever ran without a key. Run its suite against the v0.16.40 digest before adopting.

---

## ADDENDUM (2026-11-26): do not adopt 0.16.40 — wait for 0.16.40.1

**Don't adopt 0.16.40. Wait for 0.16.40.1.** If you must, set `HOST=0.0.0.0` in the letta service environment, or every request by service name returns 403. (Epsilon is the consumer that would hit this at its re-pin.)

Two defects found by independent review after the tag: (1) the container's Host guard treats the 0.0.0.0 bind as loopback because `startup.sh` never exports `HOST`, so any client reaching the server by its compose service name gets 403; (2) the tool sandbox's script generator pastes model-supplied argument names and values into the generated script as code (argument injection inside the sandbox). Both are fixed in 0.16.40.1. Also note: the Origin/Host bullet above saying "SDK clients that send neither are unaffected" is wrong as written — every HTTP/1.1 client sends a Host header; the correction ships with .40.1.

**Bare-metal note (T-2, same family as (1)):** `letta server --host 0.0.0.0` (pip installs) had the same misclassification — the CLI flag never reached the guard. 0.16.40.1 records the host before the server starts. If you run bare-metal with `--host`, re-pin to 0.16.40.1.

**E2B note (T-1):** the TypeScript tool path had the sibling of (2) — numeric argument strings were emitted verbatim into generated code. Only deployments using E2B (TypeScript tools) were affected. Fixed in 0.16.40.1.
