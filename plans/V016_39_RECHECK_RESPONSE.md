# v0.16.39 Part B Re-Check — Fix Response

Commit: `027d8b795` on `letta/v01639-cleanup-7d3e91b4`. Diff for review:
`f40e15899..027d8b795`.

## T-1 — growth check crashes on the fast-forward push

Fixed in `.github/workflows/dependency-scan.yml`: `git cat-file -e
"$GITHUB_EVENT_BEFORE:security/dependency-baseline.txt"` guards the `git
show`; on the creation push (before-SHA predates the baseline file, e.g.
`7b88fa26`) the growth check is skipped with a logged reason and the gate
still runs. Comment in the workflow states the bash -e / exit-128 mechanism.

Local witness, exact step logic under `bash -e`:
- `GITHUB_EVENT_BEFORE=7b88fa26` → "creation push, growth check skipped", exit 0
- `GITHUB_EVENT_BEFORE=c830cb830` → growth check armed against the 353-entry baseline, exit 0
- scratch baseline +1 uncommented ID vs `c830cb830` → gate exits 1 with
  `::error::Baseline grew with UNCOMMENTED entry CVE-9999-00002`

## T-2 — build_pip_subprocess_env() temp-dir leak

Fixed per option (b): `_get_pip_home()` in `tool_execution_helper.py` — one
process-lifetime home per server process, `mkdtemp` (0700 by construction,
asserted), with a `.cache` dir so pip reuses downloaded wheels across
installs. `build_pip_subprocess_env()` sets `HOME` from it. Thread-safe via
lock; never the server user's home; never per-call.

Witness (in `tests/test_v039_b12_pip_env.py`): `_prepare_venv` driven twice
with the poisoned env — `letta-pip-home-*` count grows by at most one
(observed: exactly one), mode 0700, cache dir present. B-12 suite 7/7.

## Nit — venv pip self-upgrade not wheel-only

`create_venv_for_local_sandbox`'s `pip install --upgrade pip` now carries
`--only-binary=:all:`. Every pip path in the tree is now wheel-only.

## T-3 — 68 generic baseline comments replaced

Classification method: core export (`uv export --no-dev`, 251 pins) vs the
all-extras export, then per-extra exports for membership. Result: **all 68
added IDs belong to extras-only packages; zero core packages** — the
"extras-only" claim holds for every entry:

| Package | IDs | Extras |
|---|---|---|
| tornado | 18 | dev (ipykernel/jupyter-client) |
| virtualenv | 20 | dev (pre-commit) |
| filelock | 4 | dev (virtualenv→pre-commit) |
| pytest | 1 | dev (direct) |
| langchain-core | 6 | external-tools, desktop |
| langsmith | 5 | external-tools, desktop |
| langchain | 2 | external-tools, desktop |
| langchain-text-splitters | 1 | external-tools, desktop |
| flask | 1 | desktop |
| msgpack | 2 | desktop |
| python-engineio | 2 | desktop |
| python-socketio | 2 | desktop |
| granian | 2 | experimental |
| h2 / hpack | 1+1 | modal |
| pg8000 | 1 | postgres |

Each of the 68 lines now carries a per-package comment naming the package,
version, pulling extras, and the revisit point (v0.16.40 C-2 extras trim).
The four dev-extra entries note the container installs them because
`--all-extras` includes the dev extra while `--no-dev` drops only the dev
GROUP — flagged in the comments as a C-2 decision item. Gate re-verified
green after the rewrite (parse + growth + audit).

## U-1..U-4 — upstream-alignment doc fixes

Plan authored: `plans/LettaLocal-Upstream-Alignment-Plan.md`. Implemented in
the same commit:
- U-1: README Contributing now describes the actual pipeline (plan-and-review,
  fast-forward to main, annotated tags, CI on tags; branch protection
  deliberately unused — would deadlock plan review).
- U-2: CONTRIBUTING.md no longer points PRs at letta-ai/letta or the
  upstream Discord; fork/clone URLs corrected; contact is GitHub issues +
  private vulnerability reporting.
- U-3: differences table carries the deprecation note (upstream unmaintained
  since 2026-08-05; table is the historical divergence point).
- U-4: License section states Apache 2.0 directly.

## Verification

- B-12 suite 7/7; B-3 gate tests + B-13 doc guards green (22/22 combined);
  full v039 witness set 64 passed (6 pre-existing baseline collection
  errors unchanged).
- Workflow YAML parses; step logic re-witnessed under bash -e (above).
- Smoke rig: see rig log appended below after the run against the image
  built at `027d8b795`.

## Rig result

Smoke rig 22/22 PASS against the image built at `027d8b795`
(`/tmp/v039_rig_recheck.log`): L-3 tripwire CLOSED (full attack chain
blocked), quick-start witness green, all checks pass with RIG_TARGET=0.16.39.
