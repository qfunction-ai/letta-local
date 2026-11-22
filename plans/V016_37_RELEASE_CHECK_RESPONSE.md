# v0.16.37 Release Check — Response Plan

Review: `LettaLocalv01637ReleaseCheck-4047471.docx` (Ryan, 2026-11-20, review of tag v0.16.37 / 4047471)
Reviewer pass: `V016_37_RELEASE_CHECK_RESPONSE_REVIEW.md` (Claude for Ryan, 2026-11-22) — **approved with changes**. All four load-bearing claims verified in code, zero refutations (V2 binding block at v2:538-547; `app.py:175` env password; `base.py:617` `os.environ.copy()` for local sandboxes; `app.py:813` password printed to stdout).
Status: REV 2 — F1/F3/F4/F5 revised per review and landing as one commit; F2 revised (see below), lands separately after characterization tests. The v0.16.38 plan (L-3 + L-1 together) is the next document.

## Verification of findings

All findings verified against the tree at 4047471ac. Zero refutations.

### V-1 (High) — L-3 confirmed live, deadline needs an owner — CONFIRMED

The exploit is demonstrated, not inferred: the release rig run executed check 20 against the shipped image and a sandboxed custom tool's PATCH to `/v1/sandbox-config/{id}` over localhost returned 200. Combined with L-1 (environment inheritance) and L-2 (/data/config readable), a tool can widen its own sandbox for every later run. Agreed that this is the most important date across all three repos.

The disclosure gap is real. The KNOWN-OPEN disclosure and the 14-day commitment live only in the annotated tag message. Verified:
- `README.md` (390 lines): zero mentions of L-3, KNOWN-OPEN, the deadline, or v0.16.38.
- No `CHANGELOG.md` exists in the repo.
- No GitHub Release has been created since v0.16.16 (annotated tags are the convention).

An annotated tag message is not a discoverable commitment. Accepted.

### V-2 (Medium) — the tripwire cannot trip — CONFIRMED

`scripts/smoke/smoke_test.sh` check 20 calls `ok` in BOTH branches: KNOWN-OPEN (line ~616) and CLOSED (line ~618). Consequences confirmed as stated:
1. When v0.16.38 closes L-3, nothing forces the expectation to flip — the check just silently changes which `ok` line prints.
2. If a later release reopens the hole, the check still passes.

The skip-is-failure rule is present and correct. The missing piece is exactly what the reviewer describes: the acceptable state must be a function of the server version.

### V-3 (Medium) — approval conditions tested by shape, not behavior — CONFIRMED

Verified the four conditions against the shipped tests:
- Waive-only-approval: behavioral (`test_policy_engine.py::test_waived_approval_lets_lower_deny_rule_fire`) — good.
- Canary-before-approval: behavioral (`test_tool_call_chunk_holdback.py`, P-13 class) — good.
- Exact-call binding: `test_approval_wiring_guard.py::test_approval_binding_check_present` asserts the string `"approval_response.tool_call_id != requested_call_id"` exists in V2 source. Shape check, not behavior. Confirmed.
- Genuine-approval source: no test at all. Confirmed.

### Low items — ALL CONFIRMED

- **Deferred-hole tracking**: L-1, L-2, L-7, L-8, L-9 appear nowhere as a list in the repo. They live in review docs and my memory only.
- **Client-chosen canary while L-3 is open**: confirmed by design (P-15 carve-out, verified in v0.16.37). Anyone who can reach the API can create agents whose canary they know. Harmless to existing agents; deserves a documentation line.
- **Interpreter version**: the `-P` comment claims container Python 3.11; nothing logs or checks the version at runtime. A base-image change would surface as opaque tool failures, not an early signal.

## Proposed fixes

### F1 (V-2) — version-gate the tripwire

The rig already curls `/v1/health/`, which returns `"version"`. Check 20 parses it and compares against `0.16.38` (tuple compare, no `packaging` dependency in the rig):

- server version < 0.16.38: KNOWN-OPEN (200) → `ok`; CLOSED (401/403) → `bad` ("hole closed before v0.16.38 — remove the expectation and verify the fix"); any other result → `bad`; skip → `bad`.
- server version >= 0.16.38: CLOSED → `ok`; KNOWN-OPEN → `bad` (REGRESSION — the hole is back); skip → `bad`.

This makes the check trip in both directions the reviewer named: it forces the expectation flip when .38 lands, and it fails loudly if the hole reopens.

### F2 (V-3) — extract the binding logic, then test it behaviorally

The binding logic is inline in `LettaAgentV3._step` (lines ~1202-1239) and cannot be reached from a unit test without a full agent harness. Proposal: extract it verbatim into a module-level helper in `letta/agents/helpers.py`:

```python
def _extract_approval_actions(approval_request, approval_response, messages):
    """Returns (tool_calls, tool_call_denials, tool_returns)."""
```

Semantics unchanged — same backfill-id handling, same ID-set matching, same denial/return extraction. `_step` calls the helper. This is a refactor of the agent loop and is flagged as such for reviewer approval; the alternative (driving `_step` end-to-end with a full harness) is heavier and more brittle for the same coverage.

Then two behavioral tests (`tests/test_approval_binding_behavioral.py`):

**(a) Exact-call binding.** Approval request carries calls X and Y (distinct args). Response approves only X:
- released set contains exactly X, with the REQUEST's arguments (the response cannot invent args);
- a response naming an unknown ID releases nothing;
- a response approving Y does not release X.

**(b) Genuine-approval source.**
- messages ending `[approval_request, assistant_message("I approve tool call X: approve=true")]` → `_maybe_get_approval_messages` returns `(None, None)` — assistant text never satisfies the gate;
- an approval-role response with `approvals=None` (text only) → releases nothing.

### F3 (V-1 + Low tracking) — README disclosure section

Add a "Known security holes" section to `README.md` containing:

1. The L-3 disclosure: demonstrated live by the release rig (check 20), sandboxed tool code can reach the server's REST API over localhost unauthenticated and rewrite its own sandbox/policy config. Affects every deployment including Delta's.
2. The commitment: minimal L-3 lands in v0.16.38, due **2026-12-04** (14 days from the v0.16.37 tag), as its first item. Breaking change: consumers need password headers.
3. The deferred-hole list: L-1 (environment inheritance), L-2 (/data/config read), L-7 (encryption key), L-8 (live network-rule check), L-9 (seccomp) — one list, fixed before v0.16.38 scoping starts.
4. One canary line (Low 2): while L-3 is open, anyone who can reach the API can create agents whose canary value they chose; harmless to existing agents, closes with L-3.

Recommendation: README section, not a new CHANGELOG.md — the fork has no changelog convention, and a new file nobody reads is worse than a section in the file everyone opens. Reviewer's call.

### F4 (Low 3) — interpreter version log

Log the interpreter version once per process in the Landlock sandbox spawn path (`landlock_sandbox.py`), at INFO: `python_executable` + `sys.version_info` when it is `sys.executable`; for venv interpreters, log the venv path (its base interpreter is the same image). A base-image downgrade to < 3.11 already fails closed (`-P` rejected); this makes the cause visible in the first log lines instead of an opaque tool error.

Open question: reviewer may prefer this at server startup (`main.py`) for visibility even on deployments that never run sandboxed tools. My recommendation is the spawn path — it logs exactly the interpreter that matters, where it matters.

### F5 (V-1 deadline ownership) — schedule + consumer notice

The deadline gets an owner: me, via a scheduled reminder ahead of 2026-12-04. Consumer notification (Epsilon, Delta, Nabla — the password-header breaking change) happens at v0.16.38 kickoff, before implementation starts, not at pin bump.

## Release vehicle

All of F1-F5 are post-release, non-tag work landing on main directly:
- F1 changes the rig for the NEXT release run — no retroactive re-tag of v0.16.37.
- F2 is test-only plus the flagged extraction refactor.
- F3/F4 are docs and a log line.

Recommendation: land as one commit after reviewer approval, no new tag. If the reviewer prefers to hold F2's extraction for v0.16.38, F1/F3/F4/F5 can land independently — they have no code-path interaction.

## Out of scope

- The L-3 fix itself (v0.16.38, first item).
- L-1, L-2, L-7, L-8, L-9 fixes (v0.16.38 scoping, tracked via F3).
- V2 retirement (recorded disagreement, unchanged).

## Reviewer answers incorporated (REV 2)

- **Q1 (F2)**: extraction accepted, WITH per-agent coverage and production-path tests. Revision: the V2 and V3 binding blocks are NOT copies — V2 is single-call legacy HITL (mismatch → `approval_response = None`), V3 is multi-call ID-set matching. Forcing one shared helper would change V2's semantics, so: extract each agent's block, test each behaviorally, and add one production-path test per agent (drive `_step` for V3, `_handle_ai_response` for V2 through the existing harness). Characterization tests must run green on 4047471 BEFORE the extraction and green after — that is the proof the refactor changed nothing. (b) gains a run-scoping probe: an approval whose tool_call_id matches but which arrives for a different run/agent — if the code doesn't scope approvals by run, that gets recorded as a finding, not tested to pass.
- **Q2 (F3)**: README section. Incorporated, plus posture note in the first screen and affected-versions line (all releases <= 0.16.37). One deviation: consumer pin levels (Epsilon 0.16.36, Nabla 0.16.31, Delta TBD) stay in the consumer notices, not the public README — the README names versions and impact, not internal deployment details.
- **Q3 (F4)**: spawn path. Incorporated; logs once per DISTINCT interpreter path (venv interpreters log on first appearance), and logs ERROR when the server's own interpreter is < 3.11.
- **Q4 (F1)**: hard-fail. Incorporated: POST_38 + KNOWN-OPEN = REGRESSION, `bad`.
- **Q5 (release vehicle)**: F1/F3/F4/F5 land now as one commit; F2 lands separately once its characterization tests exist. Neither waits for v0.16.38.

F1 incorporates all four reviewer changes: gate on the checkout's target version with a server-version equality assert (mislabeled/stale image fails); strict `x.y.z` only, non-numeric suffixes fail loudly (rc/dev/local never compare wrong); the .38 cycle bumps the version in its FIRST commit (recorded in the .38 plan — a dev build containing the L-3 fix must not trip the PRE_38 expectation); reopened hole hard-fails.

## Branch protection — partial pushback

"Protect main so changes land by PR with CI required" would deadlock this fork. Verified CI reality (2026-08-20): the fork's PR-triggered workflows require `self-hosted` runners that exist at letta-ai, not qfunction-ai — every PR workflow run in the fork's history queued 24h and was cancelled; zero completions ever. Only tag-triggered workflows (Test Package Installation, Publish container image) run and pass. Required-checks protection means no PR can ever merge. Proposal: force-push protection + PRs without required checks (review discipline stays human), and the CI-required part goes to Ryan with the runner problem attached — it is an infrastructure decision, not a settings toggle.

## Section 2 (L-3 cannot close without L-1) — accepted, verified

All four code claims verified. Password auth alone is bypassable by any tool that reads `LETTA_SERVER_PASSWORD` from its inherited environment (`base.py:617`). v0.16.38 scope therefore includes: L-1 allowlist (never a denylist — denylists fail open on the next secret someone adds), the `/proc/<pid>/environ` readability check, the password-print removal (`app.py:813`), and check 20 extended to the real attack (tool reads its environment, finds any credential, retries the PATCH — passes only if that fails). Optional later: sandbox TCP deny to the server's own port (L-8 territory, second layer). The v0.16.38 plan is the next document; schedule holds 2026-12-04, Nabla uses option (b).
