# Plan: v0.16.40.1 hotfix — Host guard (A-1) + sandbox argument injection (S-1)

**From:** the LettaLocal agent
**Date:** 2026-11-26
**Re:** `letta-local-independent-review-findings.md` (Claude for Ryan, 2026-11-26)
**Status:** DRAFT — awaiting go-ahead. Short plan, per the findings doc.

## Verification (done before this plan, per the findings doc's rule)

Every Part-1 finding traced in source at `9a50433d`:

- **A-1 CONFIRMED.** `letta/server/startup.sh:8` sets `HOST="${HOST:-0.0.0.0}"` as a shell variable and never exports it; the `exec letta server` child doesn't see it; `app.py:901` reads `os.getenv("HOST", "localhost")` → falls back to `"localhost"` while actually binding 0.0.0.0. The Host guard then treats a container-wide bind as loopback and 403s every request that arrives by service name (`Host: letta:8283`). The Dockerfile has no `ENV HOST` (only LETTA_* and PATH).
- **S-1 CONFIRMED, three vectors:**
  1. **Raw name interpolation.** `tool_sandbox/base.py:400-419` iterates `self.args` — the MODEL-SUPPLIED arguments dict — and pastes each key into the generated script as raw code twice (`f"{name} = __letta_coerced_args.get({name!r}, {name})"` and the bare `{name}` inside `__letta_raw_args`). A crafted key is code execution in the sandbox.
  2. **Value serialization.** `tool_parser_helper.py:63-75` (`convert_param_to_str_value`): the `string` branch correctly uses `repr()`, but the `array` branch is literally `pass` with the validation commented out, and `object` plus the fallback fall through to `return str(raw_value)` — raw `str()` of model text into generated code.
  3. **`allow_unsafe_eval=True`** at `base.py:412` → `ast_parsers.py:80-82`: `resolve_type` falls back to `eval(annotation, ...)` on the tool's annotation strings with the function's own globals. Not directly model-call text, but tool-author text — and in this fork tool source is model-influenced (agents propose custom tools; the v0.16.39 P-1 finding). The coercion block is `try/except pass`-wrapped, so removing it degrades gracefully.
- **A-2 spot-checked** (smoke_test.sh ~791): the verdict counts only CRED, DBSET, and leaks with "PASSWORD" in the name — NOCRED, /proc reads, and the password file are collected but never enter `attack_succeeded`. Confirmed; .41 scope.
- **S-3 spot-checked** (settings.py:62-75): `sandbox_type` silently falls through to `SandboxType.LOCAL` when Landlock is unavailable. Confirmed; .41 scope.

## Fix 1 — A-1: Host guard (container 403s every service-name request)

1. `startup.sh`: export HOST (`export HOST="${HOST:-0.0.0.0}"`).
2. Dockerfile: `ENV HOST=0.0.0.0` in the final stage, so a custom entrypoint can't reintroduce the fallback.
3. **New rig check 25**: an authenticated request with `Host: letta:8283` (the compose service name) must return **200**; a request with a foreign Host must still be rejected per the U-7 design. **Unfixed-first positive control**: run the check against the published v0.16.40 image and record the 403 before implementing.
4. Consumer notice correction: "SDK clients that send neither are unaffected" is wrong — every HTTP/1.1 client sends Host. Reword the Origin/Host bullet.

## Fix 2 — S-1: sandbox argument injection

1. **Name validation, before any script is generated:** an argument name is accepted only if it is a valid Python identifier, not a keyword, and is a property in the tool's JSON schema. Anything else → the tool call is rejected with a named error (not silently dropped).
2. **Value serialization:** every branch of `convert_param_to_str_value` serializes the JSON-decoded value with `repr()` — delete the `pass` in the `array` branch, fix `object` and the fallback `return str(raw_value)`. `repr()` of a decoded JSON value is a valid Python literal for all six schema types.
3. **Remove `allow_unsafe_eval=True`** from the generated coercion block (graceful degradation via the existing try/except; coercion falls back to raw args).
4. **Tests:** the reviewer's two demos plus argument-name and argument-value payloads, as regression tests that must FAIL on v0.16.40 and PASS on .40.1 (RED-verified before the fix). Plus a rig check 26: a crafted argument sent through the real tool-call path is rejected end-to-end.

## Release process

- Version: `0.16.40.1` in pyproject (four-segment patch scheme, first use — the rig's RIG_TARGET gate reads pyproject and needs to accept it).
- Full rig alone on the Docker host (now 26 checks), tag `v0.16.40.1`, CI, published-digest re-run, evidence doc `docs/release-evidence/v0.16.40.1.md`.
- Consumer-notice addendum lands NOW (docs-only, separate commit, prescribed by the findings doc): don't adopt 0.16.40; wait for .40.1; workaround `HOST=0.0.0.0` in the letta service env.
- No schema/migration changes; no API changes; no extras changes.

## Out of scope (recorded for the .41 revision)

Everything in the findings doc's Part 2 (S-2 through S-10, A-2 through A-10, vLLM N-1) folds into the revised v0.16.41 plan, together with the H-1..H-5 items from the .41 plan review — **which has not arrived in the workspace** (neither have the attached Adversarial/Security review files or vllm NOTES). Ryan: please forward them; the .41 revision will fold Part 2 in either way, but H-1..H-5 can't be incorporated blind.

The chain note: .40.1 breaks link 1 (S-1). Links 2 and 3 (S-2 internal datastore trust, COPY TO PROGRAM) are .41 — the release check for .41 covers the S-2 rig evidence specifically, per the findings doc.
