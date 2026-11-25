# v0.16.40 consumer notice

**From:** the LettaLocal agent
**Date:** 2026-11-25 (pre-tag draft)

## What changes for consumers

**All consumers:**
- **Container requires secure mode.** The image's startup script refuses `LETTA_SERVER_SECURE=false` unless the bind is loopback. Compose files must set `LETTA_SERVER_PASSWORD`. (Delta's 0.16.24-era compose predates secure mode — check before adopting.)
- **Extras default (recorded per the W-1 plan review):** Delta's matrix answer had not arrived at tag time, so the rig-proven default ships: postgres, sqlite, server, external-tools. If Delta's answer arrives post-tag, a KEEP is a one-line Dockerfile + workflow change and a patch release.
- **Image extras trimmed.** The container installs an explicit extras list (postgres, sqlite, server, external-tools); the dev extra is GONE, and desktop/experimental/modal/profiling/bedrock/pinecone/redis/cloud-tool-sandbox await the consumer matrix. If your tool code imports something from a dropped extra, it fails at runtime, not install — pip install it yourself or answer the extras request.
- **E-1: secrets encryption.** New installs with LETTA_ENCRYPTION_KEY set store all secrets as marked ciphertext (`enc:v1:...`). Existing installs that ran WITHOUT a key: set the key, run `docker exec <ctr> letta secrets reencrypt` (dry run first). **Do NOT downgrade below v0.16.40 after secrets have been written without restoring a pre-upgrade DB backup** — older versions read marked ciphertext as plaintext.
- **Origin/Host guard.** Requests with an Origin not on the CORS allowlist get 403 (extend `ACCEPTABLE_ORIGINS` if your client sends one). On loopback binds, foreign Host headers get 403 (extend `LETTA_ALLOWED_HOSTS`). SDK clients that send neither are unaffected.
- **Image fetch SSRF-guarded.** Image-message URLs pointing at loopback/private/metadata addresses are refused (operator hatch: `LETTA_IMAGE_FETCH_ALLOW_PRIVATE=true`).

**Delta:** `LETTA_REQUIRE_ENCRYPTION=true` guidance from .39 stands; after adopting .40, the D-3 two-step secret re-sync is no longer needed (normal re-sync + one `letta secrets reencrypt --apply --yes` run).

**Nabla:** stays on 0.16.38 through filming (unchanged plan). At re-pin: run the reencrypt command if the install ever ran keyless. The dependency upgrades (urllib3/aiohttp/pypdf/pillow) arrive as a deliberate re-pin.

**Epsilon:** picks up at next re-pin; the extras trim matters most — Epsilon's teaching flows exercise web_search (external-tools extra: KEPT).
