#!/bin/bash
# v0.16.41 PG upgrade witness (V-1/G-2/X-3/Z-2 + AA-1/AA-2): a pre-.41
# Postgres volume (password 'letta', trust-everywhere hba) booted through
# the REAL .41 startup.sh must converge auth, keep data, reject the old
# password — and both failure branches must fail closed THROUGH THE SCRIPT.
#
# Every check asserts and accumulates FAIL; the script exits non-zero on any
# failure. docker logs are CAPTURED then grepped (a pipe into grep -q under
# pipefail can SIGPIPE the writer and invert a match — AA-1).
#
# Usage:
#   docker build -t letta-local:v041-dev .
#   bash scripts/witness/v041_pg_upgrade.sh               # happy path
#   MODE=restart bash scripts/witness/v041_pg_upgrade.sh  # X-3 restart branch
#   MODE=exit bash scripts/witness/v041_pg_upgrade.sh     # X-3 exit branch
set -uo pipefail
MODE="${MODE:-happy}"
CANARY="${CANARY:-}"
IMG="${WITNESS_IMAGE:-letta-local:v041-dev}"
NET=w041net
FAIL=0

# I12-2 canaries: prove the asserts can fail.
#   CANARY=restart: the restart-mode checks run against an UNMODIFIED image
#   (the reload is NOT broken) — the restart-branch-line assert must FAIL.
#   CANARY=exit: the container exits early for an UNRELATED reason (entry
#   replaced) — the named-ERROR assert must FAIL (an unrelated crash must
#   not pass the exit-mode verdict).
if [ "$CANARY" = "restart" ]; then
  MODE=restart
  # no derived image: run the unmodified dev image with restart-mode checks
  IMG=w041-canary-restart
  docker build -t $IMG -f - . <<'DOCKERFILE'
FROM letta-local:v041-dev
DOCKERFILE
elif [ "$CANARY" = "exit" ]; then
  MODE=exit
  # exit early with a message that has NO named ERROR line (unrelated crash)
  IMG=w041-canary-exit
  docker build -t $IMG -f - . <<'DOCKERFILE'
FROM letta-local:v041-dev
ENTRYPOINT ["sh", "-c", "echo 'unrelated early exit (canary)' && exit 7"]
DOCKERFILE
fi

w_fail() { echo "  CHECK FAILED: $1"; FAIL=$((FAIL + 1)); }
w_pass() { echo "  PASS: $1"; }

docker network create $NET 2>/dev/null || true
cleanup() {
  docker rm -f w-old w-new >/dev/null 2>&1 || true
  docker network rm $NET >/dev/null 2>&1 || true
  docker volume rm w041-pgdata w041-letta-data >/dev/null 2>&1 || true
  # AA-2 nit: remove the derived witness images too (leaving them was
  # cluttering the host with multi-GB layers).
  docker image rm w041-brokenreload w041-brokenrestart w041-canary-restart w041-canary-exit >/dev/null 2>&1 || true
}
trap cleanup EXIT
# Pre-clean stale containers from prior runs (a leftover w-new blocks the
# docker run with a name conflict — hit on the first MODE=restart run).
docker rm -f w-old w-new >/dev/null 2>&1 || true
docker volume rm w041-pgdata w041-letta-data >/dev/null 2>&1 || true

echo "=== PHASE 1: seed a pre-.41 volume (trust hba, password 'letta') ==="
docker run -d --name w-old --network $NET \
  -e POSTGRES_USER=letta -e POSTGRES_PASSWORD=letta -e POSTGRES_DB=letta \
  -v w041-pgdata:/var/lib/postgresql/data \
  ghcr.io/qfunction-ai/letta-local:0.16.40.1 postgres >/dev/null
until docker exec w-old pg_isready -U letta -h localhost >/dev/null 2>&1; do sleep 1; done
docker exec w-old psql -U letta -d letta -c "CREATE TABLE witness (id int, note text); INSERT INTO witness VALUES (1, 'pre-upgrade');" >/dev/null
echo "seeded: rows=$(docker exec w-old psql -U letta -d letta -tAc 'SELECT count(*) FROM witness' | tr -d ' ')"
docker rm -f w-old >/dev/null

# Branch modes build derived images with witness-only seds (never committed
# as test knobs). Each gains a TOOK guard — a stale sed pattern silently
# no-ops and the mode boots the normal path (the exit-mode lesson).
if [ "$MODE" = "restart" ] && [ "$CANARY" != "restart" ]; then
  echo "=== MODE=restart: swapping the reload query for SELECT 1/0 (broken reload) ==="
  IMG=w041-brokenreload
  docker build -t $IMG -f - . <<'DOCKERFILE'
FROM letta-local:v041-dev
RUN sed -i "s/SELECT pg_reload_conf();/SELECT 1\/0;/" /app/letta/server/startup.sh
DOCKERFILE
  docker run --rm $IMG sh -c 'grep -q "SELECT 1/0" /app/letta/server/startup.sh' \
    || { echo "SED DID NOT APPLY (stale pattern)"; exit 1; }
  w_pass "took-guard: the broken-reload sed is in the image"
elif [ "$MODE" = "exit" ] && [ "$CANARY" != "exit" ]; then
  echo "=== MODE=exit: broken reload AND broken restart (the composition is REQUIRED) ==="
  IMG=w041-brokenrestart
  docker build -t $IMG -f - . <<'DOCKERFILE'
FROM letta-local:v041-dev
RUN sed -i "s/SELECT pg_reload_conf();/SELECT 1\/0;/" /app/letta/server/startup.sh && \
    sed -i 's|pg_ctl -D "$PGDATA_DIR" restart|pg_ctl -D /var/lib/postgresql/data_WRONG restart|' /app/letta/server/startup.sh
DOCKERFILE
  docker run --rm $IMG sh -c 'grep -q "data_WRONG" /app/letta/server/startup.sh' \
    || { echo "SED DID NOT APPLY (stale pattern)"; exit 1; }
  w_pass "took-guard: both seds are in the image"
fi

echo "=== PHASE 2: boot the .41 image through the REAL startup.sh ==="
docker run -d --name w-new --network $NET \
  -e POSTGRES_USER=letta -e POSTGRES_DB=letta \
  -e LETTA_SERVER_SECURE=true -e LETTA_SERVER_PASSWORD=witnesspw \
  -v w041-pgdata:/var/lib/postgresql/data \
  -v w041-letta-data:/data/letta \
  $IMG >/dev/null

if [ "$MODE" = "exit" ]; then
  # The container must EXIT non-zero with the NAMED ERROR, server never up.
  # (AA-2: requiring the named line means an unrelated crash does NOT pass.)
  EXIT_CODE=""
  for i in $(seq 1 120); do
    STATE=$(docker inspect -f '{{.State.Status}}' w-new 2>/dev/null)
    if [ "$STATE" = "exited" ]; then
      EXIT_CODE=$(docker inspect -f '{{.State.ExitCode}}' w-new 2>/dev/null)
      break
    fi
    sleep 1
  done
  LOGS=$(docker logs w-new 2>&1)
  echo "container state: $(docker inspect -f '{{.State.Status}}' w-new 2>/dev/null)"
  echo "container exit code: ${EXIT_CODE:-unknown}"
  grep -q "ERROR: postgres restart after hba convergence failed" <<< "$LOGS" \
    && w_pass "the named ERROR line printed" \
    || w_fail "the named ERROR line is MISSING (an unrelated crash must not pass)"
  grep -q "Refusing to start" <<< "$LOGS" \
    && w_pass "the refuse-to-start line printed" || w_fail "refuse line missing"
  STARTUPS=$(grep -c "Application startup complete" <<< "$LOGS")
  [ "$STARTUPS" = "0" ] && w_pass "server never started" || w_fail "server started ($STARTUPS)"
  [ -n "$EXIT_CODE" ] && [ "$EXIT_CODE" != "0" ] \
    && w_pass "non-zero exit code" || w_fail "exit code is 0 or unknown"
  if [ "$FAIL" -eq 0 ]; then
    echo "X-3 EXIT BRANCH: PASS"
    exit 0
  else
    echo "X-3 EXIT BRANCH: FAIL (${FAIL} checks)"
    exit 1
  fi
fi

# happy + restart modes: wait for migration (AA-1: capture, no pipe-grep).
# NOTE: the server takes ~20s MORE after migration completes before
# 'Application startup complete' — the checks below must not race it, so
# the server gets its own wait loop right before its check.
for i in $(seq 1 120); do
  LOGS=$(docker logs w-new 2>&1)
  grep -q "Database migration completed" <<< "$LOGS" && break
  STATE=$(docker inspect -f '{{.State.Status}}' w-new 2>/dev/null)
  [ "$STATE" = "exited" ] && { echo "UNEXPECTED EXIT: $(docker inspect -f '{{.State.ExitCode}}' w-new)"; echo "$LOGS" | tail -8; exit 1; }
  sleep 1
done
echo "--- convergence lines (through startup.sh) ---"
grep -E "Pre-v0.16.41|ALTER USER|converged|reloaded|restart|restarting" <<< "$LOGS" | head -8

if [ "$MODE" = "restart" ]; then
  # AA-2: prove the restart branch RAN (not just that the boot succeeded).
  grep -q "pg_reload_conf() failed — restarting postgres" <<< "$LOGS" \
    && w_pass "the restart branch line printed" \
    || w_fail "restart branch line MISSING (stale sed would boot the normal path)"
fi

# AA-2 #3: isolate POST-RESTART postgres logs (the first postmaster also
# emits LOG: lines — counting all of them proves nothing about the redirect).
POST_RESTART_READY=$(awk '/restarting postgres to load the new hba/{seen=1} seen && /database system is ready to accept connections/{print; exit}' <<< "$LOGS")
if [ "$MODE" = "restart" ]; then
  [ -n "$POST_RESTART_READY" ] \
    && w_pass "postgres READY line AFTER the restart marker (Z-1: post-restart logs flow)" \
    || w_fail "no postgres READY line after the restart marker (Z-1 broken?)"
else
  w_pass "postgres log lines present: $(grep -c "LOG:" <<< "$LOGS")"
fi

# NOTE (AA-2 nit): PGPASSWORD on the docker exec command line is acceptable
# ONLY for this throwaway witness volume — never copy this pattern into the
# rig or product code (U-3: /proc/<pid>/cmdline is world-readable).
GENPW=$(docker exec w-new cat /data/letta/pg_password 2>/dev/null || echo "")
if [ -z "$GENPW" ]; then
  w_fail "generated password file missing"
else
  w_pass "generated password file present"
  if docker exec w-new sh -c "PGPASSWORD='letta' psql -h 127.0.0.1 -U letta -d letta -tAc 'SELECT 1'" >/dev/null 2>&1; then
    w_fail "OLD PASSWORD STILL WORKS"
  else
    w_pass "old password rejected"
  fi
  ROWS=$(docker exec w-new sh -c "PGPASSWORD='${GENPW}' psql -h 127.0.0.1 -U letta -d letta -tAc 'SELECT count(*) FROM witness'" 2>/dev/null | tr -d ' ')
  [ "$ROWS" = "1" ] && w_pass "data survived (rows=1)" || w_fail "rows=${ROWS:-none} (expected 1)"
  LA=$(docker exec w-new sh -c "PGPASSWORD='${GENPW}' psql -h 127.0.0.1 -U letta -d letta -tAc \"SELECT setting, source FROM pg_settings WHERE name='listen_addresses';\"" 2>/dev/null)
  grep -q "127.0.0.1|command line" <<< "$LA" \
    && w_pass "listen_addresses: 127.0.0.1 (command line)" \
    || w_fail "listen_addresses wrong: ${LA:-none}"
fi

# The server's own wait: startup completes AFTER migration (+~20s). Without
# this the check races the boot and reports DOWN on a healthy container —
# caught by the restructured script's first run (the old script printed the
# same race as a line and ignored it).
for i in $(seq 1 90); do
  LOGS_NOW=$(docker logs w-new 2>&1)
  grep -q "Application startup complete" <<< "$LOGS_NOW" && break
  STATE=$(docker inspect -f '{{.State.Status}}' w-new 2>/dev/null)
  [ "$STATE" = "exited" ] && break
  sleep 1
done
STARTUPS=$(docker logs w-new 2>&1 | grep -c "Application startup complete")
[ "$STARTUPS" -ge 1 ] && w_pass "server up" || w_fail "SERVER DOWN"
# (AA-1 form throughout: capture, then herestring. The wait loop's own grep
# is also capture-form; grep -c for the count has no early-exit problem.)

if [ "$FAIL" -eq 0 ]; then
  echo "WITNESS MODE=${MODE}: PASS"
  exit 0
else
  echo "WITNESS MODE=${MODE}: FAIL (${FAIL} checks failed)"
  exit 1
fi
