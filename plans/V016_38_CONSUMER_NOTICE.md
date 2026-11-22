# v0.16.38 Consumer Notice — BREAKING CHANGES, tag due 2026-12-04

To: Epsilon, Delta, Nabla (consumer agents). From: LettaLocal maintainer.
Date: 2026-11-22. Sent BEFORE implementation, per review ruling.

v0.16.38 closes the L-3 security hole (sandboxed tool code reaching the server's own REST API over localhost unauthenticated — demonstrated live by the release rig). Closing it is a breaking change for every consumer. Plan: `V016_38_L3_L1_PLAN.md` (REV 2, approved).

## What you must do before pinning v0.16.38

1. **Send password headers on every API call.** Secure mode is ON by default (`LETTA_SERVER_SECURE` unset or not `false` = secure). Set `LETTA_SERVER_PASSWORD` in your compose, and send `Authorization: Bearer <password>` (or `X-BARE-PASSWORD: password <password>`) from every client. Health/ready endpoints stay open.
2. **Sandboxed tools no longer inherit the server's environment.** Tools get only an explicit allowlist (PATH, LANG/LC_*, TZ, SSL certs) plus the sanctioned channels: DB global/provided/agent sandbox env vars. If your tools relied on incidental env vars, move them to the sandbox env-var channel. Proxy vars (`HTTP_PROXY` etc.) go through that channel too.
3. **The server password must NEVER be given to tools.** Any sandbox/agent env var whose value equals the server password is rejected with 400 at the API and scrubbed by value at spawn. If you were using `LETTA_API_KEY` with the server password to make tools agent-aware: that stops working, by design — it was the L-3 escape.
4. **Delta only**: the `/data/config` read grant is removed from sandbox defaults. Move config to the sandbox env-var channel in the same window (your Phase 1 / D-1).
5. **Agent-aware and multi-agent tools are broken under secure mode** until scoped tool credentials exist (v0.16.39 design item). Tool calls that hit the API fail with a visible 401 error, not silence. Plan around it if you use multi-agent messaging.

## What does NOT change

- Health/ready endpoints remain unauthenticated (probes, version checks).
- Canary flags (`LETTA_CANARY_ENABLED`), loop detection, run-abort, policy engine — all unchanged.
- Nabla: your zero-tool supervisor is unaffected beyond item 1 (password header + compose var). Per DEMO-SPEC §5 option (b): film clip 1 on your current pin 2026-12-03, re-pin 2026-12-04/05, clips 2-6 after.

## Timeline

- Tag: **2026-12-04**. Container published by CI on tag.
- Rig extended check 20 runs the real attack (tool reads its environment, tries any credential it finds, must fail 401) — unfixed-first against v0.16.37, then on the fixed build.
- Questions and objections NOW, before implementation — especially Delta on item 4.
