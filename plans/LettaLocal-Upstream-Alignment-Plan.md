# Plan: LettaLocal follow-ups from the current-Letta comparison

**For:** the LettaLocal agent
**From:** Ryan Smith, drafted by Claude (architect / independent reviewer)
**Date:** 2026-11-24
**Source:** `LettaLocal-vs-Current-Letta-Comparison.md`

## What changed upstream (context)

Letta retired the V1 Python server, which is LettaLocal's lineage:
- In August 2026, `letta-ai/letta` became a landing page, and the server code moved to an `archive` branch.
- Upstream won't accept security reports for it.
- Letta's active product is now **Letta Code** (`letta-ai/letta-code`). It's a TypeScript agent harness with no V1 REST API.

What this means for LettaLocal:
- There's nothing to merge, and no migration path for consumers.
- LettaLocal already has upstream's last two security fixes: JSON for sandbox→server results, and `file://` image URLs blocked.

The work below is documentation accuracy, one new security finding, and hardening ideas taken from Letta Code.

**Rules that still hold:**
- Nabla stays pinned to 0.16.38 through filming.
- Release runs set `RIG_TARGET`.
- B-5 waits for its design review.
- Handoffs go through files, relayed by Ryan.

---

## Part 1: docs. Ride along with the v0.16.39 Part B fixes (R-1 to R-4)

### U-1. Correct the upstream-status statement

The README's line 4 says "The upstream Letta project is no longer maintained — there are no upstream merges". That's now inaccurate. Replace it with:

> An independently maintained fork of Letta's V1 API server. Letta retired the V1 server in August 2026 and moved its active development to Letta Code, a different architecture. QFunction maintains this V1 lineage for self-hosted, local-model use, including all of its security fixes. There are no upstream merges.

- Sweep the repo for the same claim and fix every instance, including the README's opening lines and SECURITY.md:
  ```
  grep -rniE "no longer maintained|not maintained|unmaintained|upstream.*(dead|abandoned)"
  ```
- **Witness:** extend the B-13 doc-drift guard test with a check that the phrase "upstream Letta project is no longer maintained" doesn't come back.

### U-2. SECURITY.md: say where reports go, and why

Add one sentence: Letta doesn't accept security reports for the retired V1 server, so all LettaLocal reports go to QFunction through GitHub private vulnerability reporting.

Don't link to or quote upstream's AGENTS.md.

### U-3. Record the lineage in the docs

Add a short "Lineage" note to the README or `docs/`:
- the code comes from the Letta V1 server (archived upstream; last upstream version 0.16.8);
- LettaLocal's history was re-rooted, so it shares no git history with upstream;
- LettaLocal's version numbers (0.16.9 and later) are QFunction's own.

This stops anyone, the agents included, from assuming upstream 0.16.x tags correspond to LettaLocal releases.

### U-4. NOTICE

Confirm NOTICE credits the original Letta authors under Apache-2.0. If any code from Letta Code is ever reused (Part 3), add its attribution then.

**Not in this plan:** naming is out of scope. Don't rename anything.

---

## Part 2: security. Add to the v0.16.40 plan

### U-5 (L-10). SSRF in the server-side image fetch

**Location:** `letta/helpers/message_helper.py`, `_fetch_image_from_url`.

**Problem:** it fetches any http or https URL with `follow_redirects=True`. Nothing blocks loopback, private addresses (RFC 1918), link-local addresses, or the cloud metadata address 169.254.169.254. Any authenticated caller can put such a URL in an image message, and the response is fed to the model.

**Fix:**
1. Resolve the hostname before connecting. Reject loopback, private, link-local, unique-local (IPv6) and multicast addresses, plus `0.0.0.0` and `::`. Connect to the IP address you validated, not the name, so DNS rebinding between check and connect fails.
2. Turn off automatic redirects. Follow at most three manually, re-running the address check on each hop.
3. Require an `image/*` content type, and cap the response size (suggested: 10 MB, read in streamed chunks and aborted past the cap).
4. Add an operator escape hatch, `LETTA_IMAGE_FETCH_ALLOW_PRIVATE=true`, for lab setups that serve images internally. It defaults to off and logs a startup WARNING when on.

**Witnesses** (RED on the current tree):
- Loopback URLs are rejected: `http://127.0.0.1:8283/v1/health` and `http://[::1]/`.
- `http://169.254.169.254/latest/meta-data/` is rejected.
- A redirect from an allowed public host to `127.0.0.1` is rejected. Use a local test server that returns a 302.
- A non-image content type is rejected.
- An oversize body is aborted.
- A normal public image URL still works.

**Docs:** add L-10 to the README's "Known security holes" until it ships, then mark it closed with the version.

### U-6. Insecure mode binds to loopback only

**Problem:** `LETTA_SERVER_SECURE=false` disables authentication, but the server can still bind to a non-loopback address such as `0.0.0.0`. That puts an unauthenticated API on the network.

**Fix:** in insecure mode, refuse to start if the bind host isn't loopback (`127.0.0.1`, `::1`, `localhost`). The error should name the fix: "set `LETTA_SERVER_PASSWORD`, or bind to 127.0.0.1".

**Container case:** Docker port publishing needs a `0.0.0.0` bind inside the container. Handle that explicitly:
- either secure mode is required in the container image, or
- add a separate, loud opt-in, `LETTA_ALLOW_INSECURE_NETWORK_BIND=true`, that logs a WARNING every start.

Pick one and document it.

**Witnesses:**
- Insecure mode with host `0.0.0.0` refuses to start.
- Insecure mode on `127.0.0.1` starts.
- Secure mode on `0.0.0.0` starts.
- The rig still passes.

### U-7. Audit Origin and CORS handling

**Problem:** a localhost API is reachable by browser-based attacks (DNS rebinding, cross-site requests). Letta Code rejects any request that carries an `Origin` header. LettaLocal's CORS list had the upstream origin removed in B-2, but the rest of the config hasn't been audited.

**Audit:**
- Report the effective CORS origins, and whether credentials are allowed.
- Report what happens to a request with an unexpected `Origin` header and a valid password.
- Report what happens to a request whose `Host` header names a non-local domain (the DNS-rebinding signature).

**Fix if the audit finds gaps:**
- Reject requests with an `Origin` that isn't on the configured allowlist.
- Add an optional `Host` header allowlist, defaulting to localhost names plus the configured bind host.

**Witness:** rejected requests for a foreign Origin and a foreign Host, and allowed requests for neither.

Write the audit result into the v0.16.40 evidence doc, even if nothing needs changing.

### U-8. Telemetry audit (backs a marketing claim)

LettaLocal appears to send nothing off the machine by default:
- Sentry runs only when the operator sets `SENTRY_DSN`.
- OTEL runs only when an endpoint is configured.
- B-2 removed the upstream domains.

That was a code read, not an exhaustive audit. Do the audit:

1. **Static audit:** grep for every outbound HTTP client construction and hard-coded URL, and classify each as operator-configured, model/provider traffic, or default phone-home.
2. **Live witness:** add a rig check. Start the server with no provider keys and no telemetry settings, run the quick-start flow against a local Ollama, and capture outbound connections:
   - use `tcpdump` or `ss` in the container, or a deny-all egress network with logging;
   - the only allowed destinations are Postgres, the local inference endpoint, and the embedding endpoint.
3. **Result:** record it in the evidence doc. If it's clean, Ryan can add "sends no telemetry by default" to the FAQ, citing the rig check.

### U-9. B-5 design input (fold into the design doc, don't implement separately)

Add two points to `V016_39_SCOPED_TOOL_CREDENTIALS.md` before it goes to review:

- **Store scoped tool tokens as hashes (SHA-256), not plaintext,** and compare in constant time. Letta Code does this for its capability tokens.
- **State plainly that the `LOCAL` executor isn't a security boundary.** It's the fallback when Landlock is unavailable. Under it, tools run as the server user and can read the password file and `/proc/<ppid>/environ`.
  - B-5's guarantees apply only under Landlock (or E2B/Modal).
  - Recommend that secure mode log a startup WARNING when the selected executor is `LOCAL`.

---

## Part 3: design only. After the Nabla freeze (mid-January), with the openworker spec

### U-10. An OpenAI-compatible "agent as model" facade (design doc only)

Letta Code serves `/v1/models`, `/v1/chat/completions` and `/v1/responses`, with each agent exposed as a model. A facade like that on LettaLocal would let any OpenAI client drive a stateful LettaLocal agent through the existing password auth. That includes aisuite, which openworker uses.

**The design doc should cover:**
- **Mapping:**
  - the model name resolves to an agent ID or name;
  - the messages array becomes a send to that agent;
  - streaming maps to SSE chunks.
- **Conversation identity:** a header or the `user` field. Decide what happens when a client resends the full history; the agent already holds its own state.
- **Tool calls:** surface or suppress the agent's internal tools, and decide how approvals appear to an OpenAI client.
- **Auth:** the same password middleware. No separate key.
- **Scope:** read the Letta Code implementation (`src/websocket/app-server-openai*.ts`, Apache-2.0) for edge cases such as idempotency keys and conversation memory. Don't port the TypeScript.

**Deliverable:** `plans/OPENAI_COMPAT_FACADE_DESIGN.md`, for review. No code until it's approved and the openworker spec is written.

---

## Explicitly out of scope

- **No migration to Letta Code, and no re-platforming.** Nabla and Delta rely on the V1 blocks, passages and messages APIs, and Letta Code doesn't have them.
- **No changes driven by upstream's AGENTS.md.** It states Letta's position on the archive and has no bearing on how LettaLocal is built.
- **Don't copy Letta Code's telemetry.** "No default phone-home" is a differentiator.

---

## Order and tracking

| Item | When | Blocks |
|---|---|---|
| U-1 to U-4 (docs) | With the Part B fixes, before the v0.16.39 tag | Nothing |
| U-9 (B-5 input) | Before the B-5 design review | B-5 review |
| U-5 to U-8 | Add to the v0.16.40 plan as REV 2, next to C-1/C-2 and R-5 to R-8 | Nothing current |
| U-10 | After the Nabla freeze, with the openworker spec | — |

Send back two things:
1. The Part B fix commits, with U-1 to U-4 included.
2. v0.16.40 plan REV 2, with U-5 to U-8 and the R-items merged in.

Ryan will relay both to me for review.
