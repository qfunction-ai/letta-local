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

## V-1 witness script (reproducible)

Seeds a .40.1-era volume (trust hba + password `letta`), boots the .41
startup path on it, asserts: convergence lines, migration completed with
the generated password, the seeded row surviving, the old password
rejected. Script logic preserved in the commit history (interim-5 commit
message) and re-runnable as documented there.
