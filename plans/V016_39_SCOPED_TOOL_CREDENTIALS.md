# Scoped Tool Credentials — Design Round (B-5, with RC-7 and B-12 Part C)

**Status: DRAFT — design only, no implementation in v0.16.39. Goes to review before any code.**
**Date:** 2026-11-24
**Scope:** three problems that are really one problem — scoping what tools are allowed to cause:
1. **B-5** — scoped tool credentials (agent-aware/multi-agent tools 401 under secure mode)
2. **RC-7** — approval run-scoping (approval responses bind to tool_call_ids but not to runs)
3. **B-12 Part C** — sandboxed dependency installs (wheel-only was the v0.16.39 stopgap)

---

## 1. Problem statements (all verified against shipped code)

### 1.1 B-5: the 401 chain

Under secure mode (on by default since v0.16.38), agent-aware tools fail with 401:

- The multi-agent client (`letta/functions/function_sets/multi_agent.py:24`) builds its client from the tool environment, defaulting to `http://localhost:8283`.
- The v0.16.38 L-1 allowlist (`base.py` env gathering) carries **no credential** — by design.
- The operator workaround (pass the server password as `LETTA_API_KEY`) is blocked by the scrub (`server_credentials.py`) — also by design.
- The auth middleware accepts only the server password.

So agent-to-agent messaging is dead under secure mode. Correct default, missing capability.

### 1.2 RC-7: approval responses are temporally scoped only by accident

`_extract_approval_actions` (V3) and `_approval_response_matches_request` (V2) match on `tool_call_ids`. The only temporal scope is `_maybe_get_approval_messages` reading the last two messages. A crafted or replayed approval response can approve a tool call in a different run. Recorded as a FINDING in the v0.16.38 plan; never fixed.

### 1.3 B-12 Part C: pip installs still run in the server process

v0.16.39 narrowed the env and made installs wheel-only, but `install_pip_requirements_for_sandbox` still executes `pip` in the server process (outside Landlock/seccomp). A malicious wheel is still arbitrary code as the server user — just with a smaller environment and no sdist build step.

---

### 1.4 The LOCAL executor is not a security boundary (v0.16.40 R-6/U-9 input)

Under the LOCAL executor (Landlock unavailable — non-Landlock hosts, or the
fallback path), tools run as the SERVER USER with no filesystem restriction:
they can read `${LETTA_DIR}/server_password` (mode 0600, but same user) and
`/proc/<ppid>/environ` (the server's own environment). B-5's guarantees
(credential injection, non-persistability, scope enforcement) are API-layer
controls and hold regardless of executor — but confidentiality of the SERVER
password does not. This design must state plainly:

- B-5 protects SCOPED tool credentials' scope and injection; it does not make
  the LOCAL executor safe to run untrusted tool code.
- Recommendation shipped with v0.16.40 (U-9): secure mode logs a startup
  WARNING when the selected tool executor is LOCAL.
- Operators running untrusted tools should use Landlock-capable hosts
  (Docker Desktop with kernel Landlock support) or E2B/Modal.

## 2. Design: scoped tool credentials (B-5)

### 2.1 Credential shape

A **ToolCredential** is a short-lived, scoped token minted by the server, injected into the tool's environment at spawn time:

```
ToolCredential:
  token: server-minted random (secrets.token_urlsafe(32))
  agent_id: the agent whose tools receive it
  run_id: optional — mint per-run when track_agent_run is on
  scopes: frozenset — see 2.2
  endpoints: frozenset of URL prefixes, default {http://localhost:8283/v1}
  expires_at: now + 15 min (refreshed per tool-call spawn)
  revoked: bool
```

Storage: in-memory registry keyed by token hash (never store the token itself), plus a DB row for revocation across restarts. NOT a JWT — nothing to parse, nothing for the tool to tamper with; the server looks it up.

### 2.2 Scopes

The middleware authorizes by scope, not by identity:

- `messages:send` — send messages to the owning agent (or, if explicitly granted, other agents in a shared group).
- `messages:read` — read the owning agent's messages (multi-agent search).
- **Never granted:** anything under `/v1/sandbox_configs`, `/v1/tools`, `/v1/policies`, `/v1/agents` (create/modify/delete), `/v1/blocks`, `/v1/sources`. A tool credential can never rewrite its own sandbox, policy, or tools — that is the L-3 escape re-opened if we get this wrong.

### 2.3 Injection and non-persistability

- Injected at spawn time into the per-exec temp env as `LETTA_TOOL_TOKEN` (the L-1 allowlist gains exactly one var, carrying the token and nothing else).
- The API write path rejects `LETTA_TOOL_TOKEN` as a user-supplied env var value (extend `reject_sensitive_env_vars`) so a tool cannot re-inject its token into a NEW agent's environment.
- Tools cannot read the server password: unchanged from v0.16.38.
- Token lifetime = tool-call lifetime (minted at spawn, revoked at completion or expiry, whichever first). A wedged tool's credential dies in 15 minutes regardless.

### 2.4 Middleware changes

`validate_server_password` gains a second acceptance path: `Authorization: Bearer <tool-token>` → look up registry → check scope against the route → check endpoint allowlist is irrelevant (middleware is local). Route-level scope map is a static dict in the middleware — one place, easy to audit.

### 2.5 Rig (check 20 gains a step)

Check 20's attack tool gains a valid tool credential (minted for it) and retries the sandbox-config PATCH with it → must 403 (scope, not auth, is the barrier). Check 20 must still pass throughout — the L-3 tripwire stays CLOSED.

## 3. Design: approval run-scoping (RC-7)

- Approval responses carry `run_id` (the run that requested approval).
- `_extract_approval_actions` / `_approval_response_matches_request` gain: the response's run_id must equal the current run's id, else the approval is ignored (logged as a security event: APPROVAL_RUN_MISMATCH).
- Stored approval records bind (tool_call_id, run_id) — the DB record already carries run_id via the run relationship.
- Backward compatibility: responses without run_id (older clients) are scoped by the existing last-two-messages gate and a new WARNING — Delta/Epsilon/Nabla clients all go through the SDK, which will populate run_id.

## 4. Design: sandboxed installs (B-12 Part C)

Two layers, in order of preference:

1. **Install-in-sandbox**: run `pip install` inside a Landlock sandbox with network access (unlike tool exec) but no read access to server files. The venv is per-sandbox; the server process never executes package code. Requires a Landlock profile variant allowing outbound 443 — a new, explicit profile, never the tool-exec profile.
2. **Package allowlist** (optional, per sandbox config): operator-maintained list of permitted package names; anything else 400s at tool create/modify time. Complements, does not replace, layer 1.

Sequencing note: layer 1 depends on L-8 (network rules behind auth) landing first, or the install sandbox's network grant becomes the new hole. This is why B-12 shipped wheel-only as the stopgap.

## 5. Open questions for the review round

1. Per-run vs per-agent credential granularity (per-run is tighter; per-agent survives the run-abort flow — recommend per-run with run_id in the token).
2. Should `messages:send` allow cross-agent sends within a group by default, or require explicit opt-in per target agent? (Epsilon's teaching scenarios want group sends; Delta's multi-agent orchestrator wants them too.)
3. Token registry: in-memory + DB-revocation row, or pure DB? (Multi-worker deployments need the DB row regardless.)
4. RC-7: ignore-and-log vs hard-fail on run_id mismatch. (Recommend ignore-and-log — a mismatched approval should not kill the run, just not approve anything.)
5. B-12 Part C layer 1: is the Landlock-with-network profile acceptable before L-8 lands, or strictly gated on L-8? (Recommend strictly gated.)

## 6. What this design deliberately does NOT do

- No credential for the tool to escalate to full server auth. There is no scope for "admin".
- No persistence of tool credentials in message history, agent state, or logs (token hash only in audit events).
- No change to the v0.16.38 password middleware semantics for human/SDK clients.
