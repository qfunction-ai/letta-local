# Review response: reencrypt-secrets-plan.md (E-1)

**From:** the LettaLocal agent
**Date:** 2026-11-25
**Re:** `reencrypt-secrets-plan.md` (Ryan/Claude, 2026-11-25), reviewed against main at `5ccbd09d3`

## Verification — all five load-bearing claims confirmed, zero refutations

1. **Plaintext fallback (Verified, `schemas/secret.py:35-66`)**: `from_plaintext` with no key stores the raw value in `encrypted_value` (the `_enc` column) with a WARNING. Exactly as described.
2. **The reuse no-op (Verified, `services/agent_manager.py:952-996`)**: with a key later configured, `get_plaintext_async` on a legacy plaintext row falls back to returning the stored plaintext → it equals the incoming value → the key lands outside `to_encrypt` → the row is rewritten with `value_enc = existing_env_vars[k].value_enc` — the same plaintext. Re-sending identical secrets is a no-op for precisely the rows that need re-encryption. Delta's Q-8 re-send approach is dead on arrival for those rows, as stated.
3. **The ambiguous-read hazard (Verified, `schemas/secret.py:215-266` + `helpers/crypto_utils.py:304-320`)**: key configured + decrypt fails + `is_encrypted()` true (valid base64, decoded ≥ 45 bytes, no `PLAINTEXT_PREFIXES` match) → `logger.error` + **raise**. A 64-char random base64 token qualifies. The hazard is real in code, not just inference.
4. **Table inventory (Verified, `letta/orm/`)**: all five tables and every listed column exist — `AgentEnvironmentVariable.value_enc` (sandbox_config.py:64), `SandboxEnvironmentVariable.value_enc`, `Provider.api_key_enc`+`access_key_enc`, `MCPServer.token_enc`+`custom_headers_enc`, `MCPOAuth` ×4.
5. **agent_manager is the only reuse path (Verified by grep)**: `to_encrypt` / reuse-existing-encrypted appears in `agent_manager.py` alone; `sandbox_config_manager` and `provider_manager` call `from_plaintext_async` on every write. The `was_decrypted_with_key()` helper placement is right — it fixes the next manager to copy the pattern, not just this one.

## Additional findings from verification (fold into the plan)

**F-A. `update_agent` doesn't just no-op on ambiguous rows — it crashes.** The reuse comparison at agent_manager.py:962-968 calls `get_plaintext_async()` with no try/except. For an ambiguous legacy row with a key configured, that call RAISES — so the operator can't even re-send the secret through the API to fix it; the update 500s before reaching the reuse logic. Part 1's write-path fix must therefore handle TWO cases, not one:
- decrypt failed → fell back to plaintext → treat as changed, re-encrypt from the incoming value (the plan's case);
- decrypt failed → **raised** (ambiguous) → also treat as changed, re-encrypt from the incoming value.
Both reduce to the same rule: a value counts as "unchanged" only if it decrypted with the key. The helper should record *why* the plaintext came back (decrypted / fell-back / raised) so the manager never has to re-derive it.

**F-B. Startup report cost.** Counting plaintext/ambiguous rows across five tables at every startup is five COUNT queries with per-row decrypt attempts (PBKDF2 per ambiguous candidate — the docstring warns 100-500ms each). On a large DB that's real startup latency. Gate it: run the scan only when a key is configured (as planned) AND bound the work — either a configurable row cap per table with a "scan truncated" note, or cache the result and re-scan only on config change. Report-only must not become a boot tax.

**F-C. The command surface already exists.** `pyproject.toml` exposes `letta = "letta.main:app"` (typer). `letta secrets reencrypt` fits the existing CLI — no new entry point, and it runs inside the container as `docker exec <ctr> letta secrets reencrypt`. Prefer that over a `scripts/` file: scripts/ is operator-facing on the host, the CLI is operator-facing in the container, and this command needs the server's env (the key) — the container is where the key lives.

**F-D. Rig witness mechanics.** Witness 5 (no-key → store → restart with key → command → tool reads secret) needs a compose-level restart mid-rig and a `docker compose exec` for the command. Both are new rig mechanics — the compose file needs the key as a settable env var and the check needs two server generations. Doable, but budget rig work for it; it's the most expensive witness in the list.

## Design question 4 — recommendation: DO THE MARKER NOW

`enc:v1:` prefix, as part of E-1, not deferred. Reasoning:

- **The ambiguity class is a treadmill without it.** The reencrypt command exists because ciphertext isn't self-identifying. If it writes markerless ciphertext, the first key rotation recreates the same five-table ambiguity mess it just cleaned up. With the marker, every row the command (and every future write) produces is exactly identifiable, and the ambiguous class can only shrink — it becomes a one-time cleanup instead of a recurring one.
- **The read-path change is strictly additive.** `get_plaintext_async` already branches on decrypt-failure; a prefix check in front (starts with `enc:v1:` → definitely ciphertext, decrypt, no heuristic fallback) is one more branch, and it makes the "encrypted" classification in the dry run exact for all new rows — the idempotency and `--apply` safety properties stop depending on a base64 heuristic.
- **Migration risk is small and one-sided.** The prefix is applied at write time only. Legacy rows keep the existing read path unchanged. Nothing that reads `_enc` columns through the API sees a format change — `Secret` serializes plaintext to consumers, never the stored form. The only external surface is direct DB reads (backups, manual SQL), and a self-describing prefix helps exactly those readers.
- **Scope guard**: `is_encrypted()` keeps its heuristic for legacy values; the marker does not change its behavior for anything already stored. No migration needed — rows convert as they're rewritten.

## Placement: include E-1 in v0.16.40

The .41-alongside-B-5 placement was for review economy, not dependency — E-1 (storage encryption) and B-5 (credential handout scoping) touch different layers, and B-5's design review is still pending while E-1 is fully specified now. Delta's Phase 1 needs E-1 (the D-3 two-step workaround is the cost of waiting), and .40 is already the security release.

Proposed sequencing inside .40, after the P-3 batch: **C-1/C-2 → U-5/U-6 → R-7 → R-6/R-5/R-8 → E-1 → U-7/U-8**. E-1 lands as its own commit series (write-path helper + marker, command, startup report, witnesses) with the five witnesses RED-verified on v0.16.39 first, per the plan. B-5 stays gated on its design review regardless.

One release-size note for the verdict call: .40 was already carrying C-1 (four upgrades), C-2, the U-5/U-6 security pair, and R-5..R-8. E-1 adds roughly a command + a helper + a report — additive, well-witnessed, no consumer breakage (the write-path change is invisible unless a key is configured). I judge it fits; if you want .40 lean, the cut line is between R-8 and E-1, and E-1 slides to .41 unchanged.
