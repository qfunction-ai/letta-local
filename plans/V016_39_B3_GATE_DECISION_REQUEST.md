# B-3 gate decision request — dependency scan will fail on first push

**To:** Ryan. **From:** LettaLocal2. **Date:** 2026-11-24. **Blocking:** v0.16.39 push/tag.

## The problem

Plan REV 3, B-3 acceptance: "the workflow exists and **runs green on the current lockfile** (scan result recorded in the release evidence doc)." The scan policy as approved: fixable vulnerability → build fails; unfixable → report only.

**The empirical scan result makes that witness unsatisfiable.** pip-audit against `uv export --frozen --no-dev` (858 pins, all-extras — matches the container's `uv sync --all-extras`):

- **38 packages affected, 629 unique advisories** (pip-audit: 537 vulns; OSV querybatch cross-check agrees within ~15%).
- Severity sample (n=40): 15 HIGH, 13 MODERATE, 12 unspecified. Sampled advisories verified real, not feed noise (e.g. GHSA-3rp8-rxj8-mvjw: aiohttp WebSocket `max_msg_size` integer overflow; GHSA-qccp-gfcp-xxvc: urllib3 sensitive-header forwarding across origins).
- Worst offenders: nltk 3.9.1, pypdf 6.4.1, pillow 10.4.0, aiohttp 3.12.15 — all have fix versions available.
- I initially suspected advisory spam inflating the counts (the 2025-26 bulk-advisory wave is real and some entries ARE marginal — e.g. "SSRF to RFC 6598 shared-address space"). The data says otherwise: the counts are inflated by *volume of marginal advisories*, but a solid HIGH-severity core is real and fixable.

**Consequence: with fail-on-fixable, the dependency-scan job goes red on the first push to main and stays red until the lockfile is upgraded.** Every subsequent push (including the .39 tag push, which fires the other two workflows) shares a red check suite with it.

## Options

1. **Dependency-upgrade pass before tagging .39.** `uv lock --upgrade` + full suite + rig + evidence. Highest fidelity to the plan's witness (green on current lockfile), but it's a large dependency churn inside a release that was scoped as cleanup — new failure surface (aiohttp 3.12→3.13+, pillow 10→11, starlette/fastapi bumps) right before Nabla's filming window. Needs its own review round per protocol.
2. **Ship the scan report-only; flip the gate after an upgrade pass.** `continue-on-error: true` on the audit step now, record the 629-advisory result in the evidence doc, add a tracked item to flip the gate in .40 after the upgrade. Witness "runs green" is satisfied honestly (the job runs and reports), the gate arrives when it's actionable.
3. **Ship as approved (fail-on-fixable), accept red main.** The scan does its job loudly. Cost: red check suite on main and every PR until the upgrade lands, which trains everyone to ignore the red — worse than no gate.

## Recommendation

**Option 2.** A gate that's red on day one is a gate nobody trusts; a recorded 629-advisory result with a scheduled upgrade is honest and actionable. The upgrade pass (option 1's substance) becomes the first item of v0.16.40 with its own review round — dependency churn deserves its own plan, not a rider on a cleanup release.

If you want option 1, say so and I'll draft the upgrade plan first; .39 holds until it lands.
