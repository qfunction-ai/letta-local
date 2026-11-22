# Review: v0.16.37 Release Check — Response Plan

**Reviewer:** Claude (architect / independent reviewer), for Ryan Smith
**Date:** 2026-11-22
**Plan reviewed:** `V016_37_RELEASE_CHECK_RESPONSE.md` (draft, written against 4047471)
**Tree checked:** letta-local `4047471` (v0.16.37). That is still the latest commit on main.
**How each finding was established:**

- **Read:** traced in the code at 4047471.
- **Inferred:** reasoned from the code, not yet confirmed.

## Verdict

**Approved with changes.** F1, F3, F4 and F5 are sound and need only small changes. F2 needs one significant change before it can land.

Separately, this plan is not the v0.16.38 plan. It explicitly scopes out L-3 and L-1, L-2, L-7, L-8 and L-9. The v0.16.38 implementation plan doesn't exist yet, and it's now on Nabla's critical path: filming starts 2026-12-03, and Nabla must be on v0.16.38 first. That plan needs to come next. It also has to handle the design problem in section 2, which this plan's wording ("minimal L-3 … consumers need password headers") walks straight into.

## 1. Changes to F1–F5

### F1 (version-gated tripwire): approve, with four changes

1. **Gate on the release target, not only on what the server reports.** The rig knows which tag it is testing. Check 20 should assert `health.version == target_version` and then apply the gate on that version. If it relies on the server's self-reported version alone, a mislabeled or stale image passes under the wrong expectation.
2. **Define how pre-release versions compare.** `0.16.38rc1`, `0.16.38.dev0` and `0.16.38+local` must compare as at least 0.16.38 when the fix is in the build. A plain tuple compare on split integers breaks on these. Either specify the parsing rule, or forbid non-numeric suffixes in the rig and fail on them.
3. **Bump the version first in the v0.16.38 cycle.** Under the proposed gate, a development build that still reports 0.16.37 but already contains the L-3 fix fails with "hole closed before v0.16.38". That's correct behavior, but it will block the very work that closes L-3. So the first commit of the cycle bumps the version to 0.16.38 (or 0.16.38.dev0, per item 2).
4. **Answer to the plan's question 4: hard-fail.** A reopened hole must stop a release. There is no warn-and-continue path.

### F2 (behavioral approval tests): change required

**The extraction targets the wrong agent, or only half the problem.** V-3's shape-only test, `test_approval_binding_check_present`, checks **V2's** source. The binding check lives at `letta/agents/letta_agent_v2.py:543` *(Read)*. F2 extracts the logic from **V3's** `_step`. V2 is still a live code path (the V2 retirement disagreement is recorded as unchanged), so both agents need behavioral coverage of exact-call binding and genuine-approval source. Either:

- extract one shared helper that **both** V2 and V3 call, and test it once; or
- extract and test each one.

A shared helper is better, because two copies of the binding logic are how the two agents drift apart.

**The extraction needs a check that the helper is the code that runs.** Behavioral tests on an extracted helper prove the helper is correct. They don't prove `_step` still uses it. That's the gap this project has hit repeatedly: tests have to exercise production entry points. Add, per agent:

- one test that drives the real handler (`_step` for V3, `_handle_ai_response` for V2) through the existing harness used by `test_tool_call_chunk_holdback.py`. At minimum it covers approve-one-of-two and an approval naming an unknown ID;
- the plan's helper tests (a) and (b), unchanged.

The characterization test should run green on 4047471 **before** the extraction, and green after it. That's the proof the refactor didn't change behavior.

Add to (b): an approval whose `tool_call_id` matches but which arrives for a different run or agent releases nothing. If the code doesn't scope approvals by run, record that as a finding rather than writing the test to pass.

**Answer to the plan's question 1:** accept the extraction, with the two production-path tests above.

### F3 (README disclosure): approve, with three changes

1. **Put it near the top of the README.** The first screen should carry a short posture note: "Production-ready for trusted tools. Known open: sandboxed code can reach the server API (L-3), fix due in v0.16.38 on 2026-12-04." It links to the full section. The README is the page skeptical readers, and the AI systems now citing LettaLocal, actually read first.
2. **Name the affected versions and consumers.** All releases up to and including 0.16.37 are affected. The consumers are pinned to Epsilon 0.16.36, Nabla 0.16.31 and Delta (version to be confirmed). Downstream readers need to know they're affected, not only that "Delta" is.
3. **Answer to the plan's question 2:** a README section, not a new CHANGELOG.md. I agree with the plan's reasoning.

### F4 (interpreter version log): approve

Answer to the plan's question 3: log it in the spawn path, as proposed. Log once per distinct interpreter path, not once per process, so a venv interpreter is also logged when it first appears.

### F5 (deadline ownership): approve, with one change

A reminder the implementing agent schedules for itself isn't ownership. **Ryan owns the 2026-12-04 date; the agent executes.** Keep the consumer notice at kickoff, before implementation. Since the password-header change breaks Nabla's re-pin, the notice must go out early enough for Nabla's re-pin work (DEMO-SPEC §5) to be planned around it.

### Release vehicle

Answer to the plan's question 5: land F1, F3, F4 and F5 now, as one commit. Land F2 separately, once its characterization tests exist, because it refactors security-critical agent-loop code. Neither should wait for v0.16.38.

LettaLocal has no private dev repo. Until it gets one, protect `main` so changes land by PR with CI required, rather than by direct push.

## 2. The v0.16.38 design problem: L-3 can't be closed without L-1

**The problem.** *(Read.)* The plan describes minimal L-3 as "consumers need password headers", meaning `LETTA_SERVER_SECURE` with `LETTA_SERVER_PASSWORD`.

- The server reads that password from the process environment: `letta/server/rest_api/app.py:175`, `random_password = os.getenv("LETTA_SERVER_PASSWORD") or generate_password()`.
- Local sandboxes inherit that environment: `letta/services/tool_sandbox/base.py:617`, `env = os.environ.copy() if is_local else {}`.

So tool code running in the sandbox can read the password from `os.environ` and then call the API with it. **Turning on password auth alone does not close L-3.** The tripwire check would go green while the escape still works for any tool that reads its own environment.

**Required for v0.16.38:**

1. **Fix L-1 in the same release, at least for secrets.** Local sandbox environments are built from an **allowlist** of the variables tools need (PATH, LANG, the sandbox's own variables), not from a copy of `os.environ` with items removed. At minimum, `LETTA_SERVER_PASSWORD`, `LETTA_PG_URI`, `LETTA_ENCRYPTION_KEY` and provider API keys never reach the tool process.
   - *Reasoning:* a denylist fails open on the next secret someone adds. An allowlist fails closed.
2. **Check that `/proc/<server pid>/environ` and the server's command line are unreadable from the sandbox.** Otherwise the password leaks around the allowlist. *(Inferred: Landlock's read rules should already block `/proc` paths outside the grant, but nobody has tested it.)*
3. **Stop printing the password to stdout.** `app.py:813` prints `Using secure mode with password: …`. Container logs are commonly shipped to a SIEM. Print that secure mode is on, never the value.
4. **Extend check 20 to the real attack.** The sandboxed tool reads its environment, finds any credential, and retries the PATCH with it. Check 20 passes only if that also fails. This is the test that proves L-3 is closed, not just that a header is now required.
5. **Optional, later:** deny sandbox TCP connect to the server's own port. That's L-8 territory. It's a second layer, not a replacement for 1–4.

**The consequence for the schedule.** L-1 moves from "deferred" into v0.16.38's required scope. That makes the 2026-12-04 date tighter, and it weakens option (a) in DEMO-SPEC §5 (pulling v0.16.38 forward to about 2026-11-30). I'd hold 2026-12-04 and use option (b) on the Nabla side.

## 3. What happens next

1. The LettaLocal agent revises this plan with the F1 and F2 changes and lands F1, F3, F4 and F5.
2. The LettaLocal agent writes the **v0.16.38 plan**: L-3 together with L-1 (allowlist), items 2–4 of section 2, and the scoping decision for L-2, L-7, L-8 and L-9.
3. I review the v0.16.38 plan before implementation starts.
