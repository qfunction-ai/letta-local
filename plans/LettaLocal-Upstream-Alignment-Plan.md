# LettaLocal Upstream Alignment Plan

Status: APPROVED for v0.16.39 fix batch (Part B re-check, 2026-11-24).
Scope: documentation-only items U-1..U-4, riding the v0.16.39 tag so the
release carries corrected upstream-status wording. No code changes.

## Background

The fork (qfunction-ai/letta-local) has been an independent project since
2026-08-05, when upstream letta-ai/letta was declared unmaintained. The
docs still carry upstream-era text that is now false or misleading in three
places (Contributing, the differences table, License) plus one file that
points contributors at the dead upstream repo (CONTRIBUTING.md).

## Items

### U-1 — README Contributing section (false process claim)

Current text claims changes land on `main` "only through pull requests with
required CI checks" and that `main` is protected. False: the fork ships
direct-to-main via fast-forward from work branches, with releases as
annotated tags carrying full release notes; CI (Test Package Installation +
Publish container image) fires on tags. GitHub branch protection with
required review approvals is deliberately NOT used — the plan-and-review
pipeline (written plans, reviewer passes, response docs) is the review
mechanism, and PR-gated merging would deadlock it (recorded in the
v0.16.38 plan review).

Fix: replace the section with the actual pipeline: plan-and-review before
code, work branches fast-forwarded to main, annotated tags for releases,
CI on tags, private vulnerability reporting for security issues (unchanged).

### U-2 — CONTRIBUTING.md (points at dead upstream)

The file is upstream boilerplate: it tells contributors to fork
letta-ai/letta and open PRs there, and links the upstream Discord. Both
are wrong for this repo.

Fix: rewrite the PR section for the fork's model (branch from main, work
with a plan, fast-forward merge by the maintainer) and replace the Contact
section (GitHub issues on qfunction-ai/letta-local; no Discord). The AI
Policy section stays — it is accurate and load-bearing.

### U-3 — README differences table (missing deprecation note)

The "Differences from upstream" table reads as if upstream is a live
comparator. It is not: upstream is unmaintained since 2026-08-05.

Fix: add a one-line note above the table stating upstream is unmaintained
since 2026-08-05 and the table describes the historical divergence point,
not a moving target.

### U-4 — README License section (indirection to a dead project)

"Same as upstream Letta" points readers at a project that no longer
maintains anything, including its license relationship to us.

Fix: state the license directly: Apache 2.0, see LICENSE.

## Verification

Doc-drift guards where a test can pin the claim (U-1 wording, U-4 wording)
are not warranted for prose; the B-13 lesson applies to commands and code,
not sentences. Verification is the reviewer's diff read.
