# Response to v01640-hardening-9e4c21f7-REVIEW.md

**From:** the LettaLocal agent
**Date:** 2026-11-25
**Commits:** e804ac33d (V-1/V-2/V-3 + should-fixes), 60723273e/3f185463c/88314b0a2 (evidence trail)

## V-1, V-2, V-3: all fixed, all with witnesses

**V-1 (reencrypt --apply round-trip).** Verified in source exactly as reviewed — the confirmation block reused loop-leftover `col`/`row_id`/`value`, covered only the last row per table, compared the decrypted plaintext against the stored CIPHERTEXT for unmarked-encrypted rows, and ran after the commit. Your Delta-state analysis is right: my rig witness passed only because its rows were legacy plaintext. Fix (rewritten `_apply`):
- `expected_plain` computed at collection: the stored value for plaintext/ambiguous rows, `decrypt(value)` for unmarked-encrypted.
- Pre-commit round-trip proof per row, INSIDE the table transaction — a failure rolls the table back.
- Post-commit re-read of EVERY rewritten row against its own expected plaintext.

Witnesses: (a) unit — three rows (plaintext / unmarked-encrypted / plaintext) in one table; all marked, all round-trip, exit 0; idempotent second apply. The unmarked-encrypted MIDDLE row is the case the old code broke on. (b) rig — gen 0 added to the E-1 witness: key-from-start, the stored value's marker stripped via psql → exactly a v0.16.39-style write (Delta's state); dry run classifies `1 unmarked-encrypted | 1 plaintext`; `--apply --yes` completes; the gen-0 row is marked AND round-trips to its original plaintext. Extended witness: ALL PASS, E1 EXIT 0.

**V-2 (pinned IP breaks HTTPS).** Confirmed live before fixing — a pinned-IP URL with a Host header failed TLS with `certificate verify failed: IP address mismatch` against a self-signed cert for the original name. Your mechanism is exactly right: SNI and cert verification follow the URL host. Fix: `extensions={"sni_hostname": <original hostname>}` on every request including each redirect hop. Witnesses (local TLS server, self-signed for `img.test`, CA trusted via `SSL_CERT_FILE`, `getaddrinfo` stub resolving `img.test` → the server): the pinned fetch succeeds AND verifies; the same server presenting a certificate for a different name fails — SNI is honest, not just permissive.

**V-3 (cap didn't stream).** Verified: `client.get()` buffers the full body; the `aiter_bytes()` loop only iterated a complete buffer. Fix: ALL requests (redirect probes AND the final) go `stream=True`; Content-Length precheck rejects before any read; the body is counted chunk-by-chunk with abort past the cap. Witness: an endless HTTP/1.1 chunked server aborts at the cap — the server observed ~11.4 MB sent of a would-be 64 MB (bound = cap + chunked-framing/socket-buffer slack; my first bound of cap+1MB was too naive and the test itself corrected it). The declared-oversize case fails with `content-length` in the error before the body exists.

## Should-fixes: all done

- **Missing Content-Type now rejected** (`refusing response with no content type`), with its own witness.
- **README U-7 one-paragraph rationale** added in the Known-security-holes section: Host checking is advisory on wildcard binds *because* U-6 forces secure mode in containers — a rebinding page has no password.
- **B-5 design doc** gained §1.5: R-7's principle stated as "no key + non-local endpoint = don't send", with the widening (derive from the U-5 address validators) recorded for the design review.

## Python 3.11 ipaddress parity (your request)

Verified in-container on the shipped 3.11: loopback / RFC1918 / link-local / ULA / unspecified all rejected; the three IPv6-embedded forms — `::ffff:10.0.0.5` (IPv4-mapped private), `2002:0a00:5::` (6to4 of a private), `64:ff9b::8.8.8.8` (NAT64) — all REJECTED, matching your 3.13 findings. Multicast/reserved are rejected explicitly (not via `is_global`), so the classification doesn't depend on the stdlib version's taxonomy. Full table recorded in the evidence doc.

## External dependency (C-2 matrix)

`plans/V016_40_DELTA_EXTRAS_REQUEST.md` is with you for relay to Delta. The trim ships the rig-proven set; adding an extra back before the tag is a one-line Dockerfile+workflow change. Per your note, we'll fold in Delta's answer when it arrives, before tagging.

## Suite / rig status on the V-fix tree

- Suite: running (matched-env FAILED-set protocol vs the .39 baseline; the pre-V-fix tree was 64 failed / 1509 passed with **zero new failures vs the .39 baseline**).
- Rig: re-running on the V-fix image (the pre-V-fix image was 22/22, RIG_TARGET=0.16.40).
- U-5 unit witnesses: 25 passing on 3.12 (20 original + 5 new V-2/V-3/content-type); classification parity proven on 3.11.
- V-1 unit witnesses: 2 passing.
- E-1 extended rig witness (gen 0/1/2): ALL PASS.

Both results get recorded in the evidence doc as they land; the tag sequence waits for them.

## One note on the shared-host point

Fair word, received. The rig runs today overlapped Delta's live-stack work because .40 was mid-flight when its stack came up — the health-wait widening was to keep the witness honest under real contention, not to paper over it. Going forward: the release-gate rig runs (the ones whose timing matters) run alone on the host, per the queue.
