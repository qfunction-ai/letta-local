# v0.16.40 C-2 extras trim: Delta confirmation request

**From:** the LettaLocal agent (via Ryan)
**Date:** 2026-11-25
**Re:** container extras trim — which optional dependency groups does Delta actually import?

## What's changing

The v0.16.39 container installs ALL optional extras (`uv sync --all-extras`), which is why the dependency scan carries 342 pins and a large baseline of advisories that exist only in extras no QFunction product uses. v0.16.40 trims the container to only the extras a consumer needs. **The dev extra is already decided: it drops.** (43 advisory IDs of noise — pytest, virtualenv, tornado, filelock — test tooling in a production image.)

## The candidates (drop unless you use them)

| Extra | Notable packages | Notes |
|---|---|---|
| `desktop` | pyzmq, ipykernel, tornado, matplotlib, jupyter chain | the biggest advisory-noise block (8 IDs in the R-2 additions) |
| `external-tools` | flask, gevent, locust, python-socketio | needs careful checking: web_search/exa-py may live here |
| `experimental` | — | 1 ID |
| `modal` | modal, grpclib, synchronicity | 2 IDs |
| `postgres` | asyncpg, pg8000 | **likely KEEP — the rig and consumers run PG** |
| `dev` | pytest chain, black, ruff, pre-commit | already dropped, listed for completeness |

## What Delta should answer (file handoff through Ryan, as usual)

For each extra above, KEEP or DROP, with the reason if non-obvious. Specifically:
1. Do Delta's agents or tools import anything from `desktop` (zmq/ipykernel/matplotlib)?
2. Does anything in Delta's toolchain go through `external-tools` — in particular does the `web_search` tool (exa-py) or anything flask-based run in the container Delta uses?
3. Any need for `modal` (sandboxed executors) or `experimental`?

Default for no answer by implementation time: the trim takes only what the rig proves (postgres stays, dev drops), and everything else drops from the CONTAINER only — consumers can still `pip install letta[extra]` themselves; the image is what changes.

## Why this matters to Delta

The trimmed image is what Delta re-pins onto. If Delta needs an extra we dropped, the import fails at runtime, not at install (the v0.16.40 plan's RUNTIME witness covers our router/executor import surface — it cannot see Delta's own tool code). A one-line answer per extra avoids a broken re-pin.
