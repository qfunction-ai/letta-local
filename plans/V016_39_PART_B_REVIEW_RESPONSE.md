# Response to Part B review (V016_39_PART_B_REVIEW.md)

**From:** the LettaLocal agent · **Date:** 2026-11-24 · **Branch:** `letta/v01639-cleanup-7d3e91b4` (fixes at `066dad74e`, `e6fedaa1e`, `e99e1eba1`, `8dbce13a0`)

All four blockers fixed on the branch. All four should-fix items and the C-1 correction are in the v0.16.40 plan (REV 3). Evidence section: `docs/release-evidence/v0.16.39.md` ("Part B review response").

## Blockers

**R-1 — fixed.** Both `_prepare_venv` methods now build `build_pip_subprocess_env()` once and route EVERY pip/venv subprocess through it; the raw tool env never reaches pip. `--only-binary=:all:` added to the requirements.txt install AND the pip self-upgrade (found during the witness run: `ensure_pip_is_up_to_date` is also a pip-install path). Witnesses: an end-to-end test drives `_prepare_venv` with a poisoned env — no secret reaches any subprocess, HOME is a fresh temp dir, installs are wheel-only — plus an AST guard on both bodies (raw `env=env` to any subprocess call inside `_prepare_venv` fails the suite). 6/6 B-12 tests green.

**R-2 — fixed, and it went one level deeper than the review stated.** The workflow export now has `--all-extras` + `--no-deps`. But the BASELINE was generated from the same wrong export — the original 353 IDs never covered the extras-only packages (flask, granian, langchain, tornado, virtualenv, ...). Regenerated from the all-extras audit: **353 → 421 IDs**, each of the 68 added entries carrying a justification comment per the growth rule. Gate green on the corrected scope: 0 new fixable, 0 new unfixable, 642 records absorbed. Verified locally with the exact workflow commands (under bash — the first local attempt failed on a zsh artifact: unquoted `$FLAGS` doesn't word-split in zsh; the workflow runs bash and is unaffected).

**R-3 — fixed.** Growth check compares against `github.event.before` on push; all-zero SHA falls back to no growth check; schedule/dispatch skip it explicitly (gate still runs). **The CI-level witness (throwaway bad-addition push) is PENDING by necessity:** the workflow only exists on this branch until main is fast-forwarded. It runs immediately after the fast-forward and before the tag; the result gets recorded in the evidence doc. If you want it observed before approving the tag, the alternative is dispatching the workflow on a scratch branch off this one — say the word and I'll do that instead.

**R-4 — fixed.** Main merged (`066dad74e`); README keeps both the RC-8-mitigated bullet and the disclosure line. B-13 doc-drift guards re-run green (4/4).

## Nits — all fixed

- Gate now reads the ungated audit (`--full-audit`) and reports absorbed records (642) — the gated count was structurally always 0.
- `domain = "letta" + ".com"` replaced with a plain literal + explicit allowlist entry in the domain-sweep guard (`server/server.py`, count 1). The exception lives where the rule lives.
- Startup stored-config check now reports ARCHIVES too (migration f6cd5a1e519d preserves the dead endpoint in archive embedding_config).
- Evidence commit table completed through the review fixes; the 21/21 → 22/22 rig-count inconsistency corrected; witness-1's "537 hits" clarified (local full audit, pre-correction scope).

## Deferred to v0.16.40 (plan REV 3)

R-5 (secret-length floor + PG-password component), R-6 (password-file O_CREAT mode + single writer + LOCAL-executor honesty in B-5), R-7 (embedder no-key fail-closed), R-8 (guard widening to helpers/ + no-env spawn patterns). Your C-1 correction is in: all four upgrade targets are core deps (verified), C-1 doesn't wait on the trim — the REV 2 "whichever survive" wording was wrong and is corrected.

## Verification state

- v039 witness suite: 48 passed (46 + 2 new B-12 hot-path tests).
- Rig on the R-1-fixed image: **22/22 PASS** (image built at `e6fedaa1e`; check 20 full attack chain CLOSED, check 21 quick-start witness green).
- Full-suite FAILED-set diff vs the pre-fix .39 run: **byte-identical** (zero new failures). 115F/1427P/1207E; pass delta +17 fully reconciled (+11 B-3 REV4 gate tests and +4 B-13 doc-drift guards added after the original run, +2 new B-12 hot-path witnesses — all pass).
- CI growth witness: pending fast-forward (see R-3 above).

**Request:** diff-only re-check of `066dad74e..8dbce13a0` (merge resolution, R-1/R-2/R-3 code, baseline regen, guard allowlist, archives check). Tag sequence after your pass: fast-forward main → CI growth witness (bad-addition push, then revert) → tag v0.16.39 → CI watch → published-image rig re-run → digest in evidence.
