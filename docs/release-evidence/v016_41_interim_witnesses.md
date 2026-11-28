# v0.16.41 interim witnesses (accumulated during implementation)

Branch: `letta/v01640-hardening-9e4c21f7`. Each witness ran on the dev image
built from the branch at the cited stage. Full transcripts live in the
session logs; the load-bearing lines are recorded here.

## S-2 uid split — F-5 spike (pre-implementation)

- Rig on the spike image: **25/27**. The one real failure (4.95) pinned to
  `/root` mode 700 — the sandbox uid cannot traverse `/root/.letta` at all.
- In-container proof of the fix hypothesis: LETTA_DIR under `/data/letta`
  (755) → `import letta` as uid 1000 OK; numpy import + staging write OK.
- Results doc: `plans/F5_SPIKE_RESULTS.md`.

## Seccomp socket-family filter (R-2 condition 2)

- LIVE in the container: TCP **ALLOWED**; AF_UNIX / SOCK_DGRAM / SOCK_RAW /
  AF_NETLINK all **DENIED**.
- Two bugs caught by the live run: SCMP_CMP op values (EQ=4, not 0), and
  SOCK_CLOEXEC flag bits defeating plain-EQ type compares (masked-eq with
  SOCK_TYPE_MASK=0xF is the correct form — plain EQ let SOCK_DGRAM|CLOEXEC
  through).

## io_uring block (G-1)

- Under the filter: `io_uring_setup` → EPERM(13, our ERRNO action).
- Without any filter: the linuxkit kernel itself blocks it (EPERM 1 — Docker
  Desktop's runtime restriction). On io_uring-capable hosts the positive
  control shows a real fd. Both readings recorded.

## G-4 traversal probe — canary ran as ROOT (X-1)

- A 700 dir owned by root, probed as uid `nobody` through the REAL setpriv
  path: **raised the named "unreachable by the sandbox uid" error**.
- Positive control: traversable dir, no raise, setpriv executing
  (`shutil.which('setpriv')` verified True).

## PG convergence on pre-.41 volumes (V-1/G-2/X-3)

Witness (old volume: password `letta`, trust-everywhere hba → .41 boot):
```
Pre-v0.16.41 Postgres volume detected: converging auth to scram + generated password
  ALTER USER applied
  pg_hba.conf converged to scram-sha-256 (reloaded)
Using internal PostgreSQL at: localhost:5432 (scram-sha-256, generated password)
old password rejected (good)
rows via generated pw: 1
```
Marker `.letta_v041_scram` present in PGDATA (idempotent).

**X-3 restart branch (forced):** broken reload (`SELECT 1/0` → exit 1) →
the real `gosu postgres pg_ctl restart -w -t 60` ran (checkpoint + shutdown
logs; server back up) → old password still rejected, rows=1,
`listen_addresses` carried over via the command line
(`127.0.0.1 | command line` — postmaster.opts reuse works).

**X-3 exit branch (forced):** `pg_ctl -D <wrong dir> restart` → exit 1 —
the same condition startup.sh turns into the named error + `exit 1`.

## Y-2 resolution: listen_addresses applies from the FIRST upgraded boot

`SELECT setting, source FROM pg_settings WHERE name='listen_addresses'` on
the first upgraded boot: **`127.0.0.1 | command line`**. The `-c` overrides
postgresql.conf on every boot — the "first boot keeps `*`" caveat from the
X-3 commit message was WRONG and is dropped. Loopback-only applies from
boot one, fresh and upgraded volumes alike.

## W-2 fresh-volume double boot

- Boot 1 (fresh): 0 convergence lines. Boot 2 (same volume): **0
  convergence/warning lines** — the initdb-time marker keeps healthy
  restarts silent (previously every restart warned about a legacy password
  that never existed).

## Z-2: all three X-3 branches THROUGH THE REAL startup.sh

Witness script: `scripts/witness/v041_pg_upgrade.sh` (MODE=happy|restart|exit).
The branch modes build derived images with witness-only seds (never committed
as test knobs).

- **happy** (unmodified startup.sh): convergence lines from the script,
  old password rejected, rows=1, `127.0.0.1 | command line`, server up.
- **restart** (reload query swapped for `SELECT 1/0`): the script's own
  `pg_reload_conf() failed — restarting postgres to load the new hba (fail
  closed)` line fires; afterwards old password rejected, rows=1, server up,
  **23 postgres LOG lines present (Z-1: the un-redirected restart keeps
  post-restart logs flowing to the container)**.
- **exit** (reload broken AND restart pointed at a wrong -D — BOTH breaks
  required; a broken restart alone never runs because the normal reload
  succeeds and the restart branch is never entered): the named ERROR lines
  print, container exits code 1, zero `Application startup complete`.

Witness-script engineering notes (all caught by runs, all fixed in the
script): the server-verdict check must run before the EXIT trap's cleanup
(a post-cleanup check sees a ghost container and reports DOWN); stale
containers from prior runs block docker run (pre-clean added); a sed
pattern that goes stale when the target line changes silently no-ops
(the exit mode gained a took-guard — grep for the broken marker in the
built image); unbound-variable heredocs when the sed body needs a literal
$VAR (quoted heredoc); the exit-code inspect races container removal
(capture at the moment of the exited-state poll).

## V-1 witness script (reproducible)

`scripts/witness/v041_pg_upgrade.sh` — seeds a .40.1-era volume (trust hba
+ password `letta`), boots the .41 startup path, asserts convergence,
migration with the generated password, the seeded row surviving, the old
password rejected. Runnable from the repo (the Z-2 modes above).


## I12-2: witness-script canaries (rule 2)

Both canaries run through the same witness script (the CANARY env var selects
them; the took-guards are bypassed by design because the canary images are
unbroken in the relevant way):

- **Restart-mode canary** (CANARY=restart — the UNMODIFIED image, no
  `SELECT 1/0` swap; the reload succeeds so the restart branch never runs):
  ```
  CHECK FAILED: restart branch line MISSING (stale sed would boot the normal path)
  CHECK FAILED: no postgres READY line after the restart marker (Z-1 broken?)
  WITNESS MODE=restart: FAIL (2 checks failed)
  ```
  Exactly the two restart-mode asserts, failing for the right reason.

- **Exit-mode canary** (CANARY=exit — the container exits early for an
  UNRELATED reason: entry replaced with `echo 'unrelated early exit' && exit 7`):
  ```
  CHECK FAILED: the named ERROR line is MISSING (an unrelated crash must not pass)
  CHECK FAILED: refuse line missing
  X-3 EXIT BRANCH: FAIL (2 checks)
  ```
  The named-ERROR requirement works: an unrelated crash cannot pass the exit
  verdict.

Real modes re-verified after the canary edits: restart PASS, exit PASS,
happy PASS (the canary paths don't disturb the genuine paths).

## I12-5: known issues surfaced by the hosted CI runs (product findings)

Recorded for tracking; each has a proposed release:

1. **Postgres autogenerate drift** — the models and migrations disagree
   (`alembic check` reports new upgrade operations: index/unique-constraint
   differences on blocks_tags, provider_trace_metadata, tool_call_policies).
   This is the migration-agreement gap the .42 plan's B1 item (H-4's
   first-diff-allowlist pattern) exists to close. **Proposed: v0.16.42**
   (data safety, with the B1 check and the A1 upgrade fixture).
2. **SQLite migration env imports asyncpg** — the alembic env imports
   asyncpg unconditionally, but the sqlite extra doesn't provide it; SQLite
   users running migrations hit ModuleNotFoundError. A packaging defect for
   the sqlite path. **Proposed: v0.16.41.1 or v0.16.42** (with the R2A-5
   SQLite decision in .42 — either the import gets conditional or SQLite
   migration support is declared unsupported).
3. **~189 lint findings on main** — surfaced when core-lint first actually
   ran on hosted runners (it had queued forever on nonexistent self-hosted
   runners). A dedicated cleanup PR (ruff check+format over the tree), after
   which lint returns as a required check in tests.yml. **Proposed: its own
   cleanup PR, then re-enable as required check.**


## I26-2: agent_files + memfs on a .40.1 install (upgrade question)

**Facts (Verified on the published ghcr.io/qfunction-ai/letta-local:0.16.40.1 image):**
- .40.1 code paths: `os.path.expanduser("~/.letta/agent_files")` and
  `os.path.expanduser("~/.letta/memfs")` (tool_sandbox/base.py:146/:706,
  memory_repo/memfs_client_base.py:33, storage/local.py:42). With
  HOME=/root those resolve to /root/.letta/agent_files and
  /root/.letta/memfs.
- Volume-mounted deployments (.40.1 smoke rig, and any compose following
  it): the volume was mounted AT /root/.letta — so agent_files and memfs
  WERE on the volume.

**Conclusion: no pre-existing data loss** for volume-mounted .40.1
deployments. (Non-volume deployments lose ALL state on recreate —
pre-existing, unrelated to .41.)

**Upgrade path for .41:** the paths now resolve under /data/letta. The
one-time migration is the operator's volume-mount change, which the
startup banner documents with exact commands (docker compose down; cp -a
from the old volume to the new; re-up). Nothing is auto-copied: /data/letta
and /root/.letta on an upgraded host are distinct mounts, and silently
copying credential-adjacent state across mounts is a bigger risk than an
explicit operator step. The consumer notice must call this out.

**Recording:** this section is the evidence-doc record. The consumer
notice for .41 gains a "volume mount path changed" entry pointing at the
startup banner's commands.


## I12-6 CLOSED: rig check 27 full output (the evidence run)

**Rig commit:** 23044201c (scripts/smoke/smoke_test.sh)
**Image:** letta-local:v041-dev (local image ID sha256:346e825b69189330546f7a1cc1562b1d392af37dcbbe0d1df3dd2de8a5f8cb53), built from the branch head
**Result:** smoke: 33 passed, 0 failed (rig_exit=0)

Verbatim check 27 block:

```
== check 27: datastore hardening (R-2 conditions 1-4) ==
--- tool create: HTTP 200 ---
--- probe result (tool_return + stdout + stderr + status) ---
R27:27.1-pg=DENIED(13)
R27:27.1-redis=DENIED(13)
R27:27.1-net-443=CONNECTED
R27:27.1-net-8080=DENIED(13)
R27:27.2-pg_password=DENIED
R27:27.2-redis.conf=DENIED
R27:27.2-proc1-environ=DENIED
R27:27.3-af-unix=DENIED(13)
R27:27.3-udp=DENIED(13)
R27:27.3-raw=DENIED(13)
R27:27.4-io-uring=ret=-1 errno=13
--- end probe result ---
  PASS  27.1 ports selective: datastore+8080 denied, 443 connected
  PASS  27.2 credentials unreadable from sandbox
  PASS  27.3 socket families denied
  PASS  27.4 io_uring_setup denied (errno=13 — seccomp filter enforced)
--- positive control (outside sandbox) ---
PC:TCP=OK
PC:UNIX=OK
PC:UDP=OK
PC:RAW=DENIED(93)
PC:io-uring=ret=-1 errno=1
  PASS  positive control: TCP allowed outside sandbox
  PASS  positive control: sibling probes ran outside sandbox
```

Also in the run: 4.95 PASS (second consecutive). I31-3 correction applied:
Landlock per-port rules enforce 27.1 on linuxkit (fixed struct works);
"ENV-LIMITED" dropped. The I29-1 pre-fix EINVAL: Inferred (no saved line).
