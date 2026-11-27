#!/bin/bash
# v0.16.41 PG upgrade witness (V-1/G-2/X-3/Z-2): a pre-.41 Postgres volume
# (password 'letta', trust-everywhere hba) booted through the REAL .41
# startup.sh must converge auth, keep data, reject the old password — and
# both failure branches must fail closed THROUGH THE SCRIPT.
#
# Usage:
#   docker build -t letta-local:v041-dev .
#   bash scripts/witness/v041_pg_upgrade.sh          # the happy path
#   MODE=restart bash scripts/witness/v041_pg_upgrade.sh   # X-3 restart branch (broken reload)
#   MODE=exit bash scripts/witness/v041_pg_upgrade.sh      # X-3 exit branch (failed restart)
set -uo pipefail
MODE="${MODE:-happy}"
IMG="${WITNESS_IMAGE:-letta-local:v041-dev}"
NET=w041net
docker network create $NET 2>/dev/null || true
cleanup() {
  docker rm -f w-old w-new >/dev/null 2>&1 || true
  docker network rm $NET >/dev/null 2>&1 || true
  docker volume rm w041-pgdata w041-letta-data w041-pgdata-x >/dev/null 2>&1 || true
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

# Optional: modify the startup script INSIDE a derived image for the branch
# modes (witness-only edits; never committed as test knobs).
BOOT_ARGS=""
if [ "$MODE" = "restart" ]; then
  echo "=== MODE=restart: swapping the reload query for SELECT 1/0 (broken reload) ==="
  IMG=w041-brokenreload
  docker build -t $IMG -f - . <<DOCKERFILE
FROM letta-local:v041-dev
RUN sed -i "s/SELECT pg_reload_conf();/SELECT 1\/0;/" /app/letta/server/startup.sh
DOCKERFILE
elif [ "$MODE" = "exit" ]; then
  echo "=== MODE=exit: pointing the restart at a wrong -D (failed restart) ==="
  IMG=w041-brokenrestart
  # NOTE: the sed target tracks the CURRENT restart line (Z-1 removed the
  # >/dev/null redirect — the first version of this sed matched the old
  # form, silently no-opped, and the mode booted happily).
  # NOTE: the exit branch requires BOTH breaks — the broken RELOAD (to
  # enter the restart path) AND the broken RESTART (to fail it). A broken
  # restart alone never runs: the normal reload succeeds and the restart
  # branch is never entered (hit on the first standalone-restart-sed run:
  # the sed applied, the server booted happily).
  docker build -t $IMG -f - . <<'DOCKERFILE'
FROM letta-local:v041-dev
RUN sed -i "s/SELECT pg_reload_conf();/SELECT 1\/0;/" /app/letta/server/startup.sh && \
    sed -i 's|pg_ctl -D "$PGDATA_DIR" restart|pg_ctl -D /var/lib/postgresql/data_WRONG restart|' /app/letta/server/startup.sh
DOCKERFILE
fi

echo "=== PHASE 2: boot the .41 image through the REAL startup.sh ==="
docker run -d --name w-new --network $NET \
  -e POSTGRES_USER=letta -e POSTGRES_DB=letta \
  -e LETTA_SERVER_SECURE=true -e LETTA_SERVER_PASSWORD=witnesspw \
  -v w041-pgdata:/var/lib/postgresql/data \
  -v w041-letta-data:/data/letta \
  $IMG >/dev/null

if [ "$MODE" = "exit" ]; then
  # Guard: the sed must have taken (a no-op sed silently boots happy —
  # hit on the first exit-mode run when the pattern went stale).
  docker run --rm $IMG sh -c 'grep -q "data_WRONG" /app/letta/server/startup.sh'     || { echo "SED DID NOT APPLY (stale pattern — the image boots happy)"; exit 1; }
  # The container must EXIT non-zero with the named ERROR, server never up
  for i in $(seq 1 120); do
    STATE=$(docker inspect -f '{{.State.Status}}' w-new 2>/dev/null)
    [ "$STATE" = "exited" ] && break
    # capture the exit code the MOMENT we see exited (a later inspect may
    # race with removal)
    [ "$STATE" = "exited" ] && EXIT_CODE=$(docker inspect -f '{{.State.ExitCode}}' w-new 2>/dev/null)
    sleep 1
  done
  EXIT_CODE=${EXIT_CODE:-$(docker inspect -f '{{.State.ExitCode}}' w-new 2>/dev/null)}
  echo "container state: $(docker inspect -f '{{.State.Status}}' w-new)"
  echo "container exit code: ${EXIT_CODE}"
  docker logs w-new 2>&1 | grep -E "ERROR: postgres restart|refusing to start|trust auth" | head -4
  docker logs w-new 2>&1 | grep -c "Application startup complete" | xargs echo "server started (must be 0):"
  if [ "${EXIT_CODE}" != "0" ] && ! docker logs w-new 2>&1 | grep -q "Application startup complete"; then
    echo "X-3 EXIT BRANCH: PASS (named error, non-zero exit, no server)"
  else
    echo "X-3 EXIT BRANCH: FAIL"
    exit 1
  fi
  exit 0
fi

# happy + restart modes: wait for migration
for i in $(seq 1 120); do
  docker logs w-new 2>&1 | grep -q "Database migration completed" && break
  STATE=$(docker inspect -f '{{.State.Status}}' w-new 2>/dev/null)
  [ "$STATE" = "exited" ] && { echo "UNEXPECTED EXIT: $(docker inspect -f '{{.State.ExitCode}}' w-new)"; docker logs w-new 2>&1 | tail -8; exit 1; }
  sleep 1
done
echo "--- convergence lines (through startup.sh) ---"
docker logs w-new 2>&1 | grep -E "Pre-v0.16.41|ALTER USER|converged|reloaded|restart|restarting" | head -8
echo "--- Z-1: post-restart postgres logs present? ---"
docker logs w-new 2>&1 | grep -cE "checkpoint|database system is ready|LOG:" | xargs echo "postgres log lines:"
GENPW=$(docker exec w-new cat /data/letta/pg_password 2>/dev/null || echo "")
if [ -n "$GENPW" ]; then
  docker exec w-new sh -c "PGPASSWORD='letta' psql -h 127.0.0.1 -U letta -d letta -tAc 'SELECT 1'" >/dev/null 2>&1 \
    && echo "old password WORKS (BAD)" || echo "old password rejected (good)"
  docker exec w-new sh -c "PGPASSWORD='${GENPW}' psql -h 127.0.0.1 -U letta -d letta -tAc 'SELECT count(*) FROM witness'" 2>/dev/null | tr -d ' ' | xargs echo "rows via generated pw:"
  docker exec w-new sh -c "PGPASSWORD='${GENPW}' psql -h 127.0.0.1 -U letta -d letta -tAc \"SELECT setting, source FROM pg_settings WHERE name='listen_addresses';\"" 2>/dev/null
fi
# Capture the server-state verdict BEFORE any exit path (the trap removes
# the container; checks after cleanup see a ghost and report DOWN wrongly —
# caught on the first run of this script).
SERVER_UP=$(docker logs w-new 2>&1 | grep -c "Application startup complete")
[ "${SERVER_UP}" -ge 1 ] && echo "server up (good)" || echo "SERVER DOWN (BAD for happy/restart modes)"
