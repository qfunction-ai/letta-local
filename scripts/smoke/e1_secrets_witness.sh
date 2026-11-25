#!/bin/bash
# v0.16.40 (E-1) rig witness: re-encryption end-to-end on a real image.
#
# REQUIRED for v0.16.40 and for any later release touching secret.py or the
# secret-holding managers (per the plan's rig-cost ruling).
#
# Two server generations:
#   gen 1: no LETTA_ENCRYPTION_KEY  -> capture-seed a secret (stored plaintext)
#   gen 2: key set                  -> startup report fires, command dry-run
#          classifies, --apply re-encrypts marked, tool-free read-back via the
#          capture endpoint proves the value survived.
#
# Usage (from repo root, rig compose already up is NOT required — this brings
# its own):
#   SMOKE_IMAGE=letta-local:v040smoke scripts/smoke/e1_secrets_witness.sh
set -euo pipefail

PORT="${E1_PORT:-8286}"
export SMOKE_PORT="${PORT}"  # the compose maps ${SMOKE_PORT} -> 8283
BASE="http://localhost:${PORT}"
PASSWORD="${SMOKE_PASSWORD:-smoke-test-password}"
KEY="e1-witness-key-32-chars-aaaaaaaaa"
NAME="e1-smoke-$(date +%s)"

cd "$(dirname "$0")/../.."

compose() { docker compose -f scripts/smoke/docker-compose.smoke.yml -f scripts/smoke/docker-compose.e1-override.yml -p e1-smoke "$@"; }

cleanup() {
  compose down -v 2>/dev/null || true
}
trap cleanup EXIT

wait_health() {
  # 240s: shared-docker-host contention (other agents' stacks + suites)
  # can double boot time; 120s was observed insufficient.
  for i in $(seq 1 120); do
    if curl -sf -H "Authorization: Bearer ${PASSWORD}" "$BASE/v1/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  echo "FAIL: server never became healthy"
  return 1
}

echo "== gen 0: key from the START — seed UNMARKED ciphertext (the v0.16.39-style install / Delta upgrade path) =="
export LETTA_ENCRYPTION_KEY="${KEY}"
compose up -d letta
wait_health
AGENT_JSON=$(cat <<EOF2
{
  "name": "${NAME}-enc",
  "llm_config": {"model": "ollama/nemotron-3-nano:4b", "model_endpoint_type": "openai", "model_endpoint": "http://host.docker.internal:11434/v1", "context_window": 128000, "embedding": "ollama/embeddinggemma:latest"},
  "secrets": {"WITNESS_ENC": "witness-encrypted-legacy"}
}
EOF2
)
curl -s -f -X POST "$BASE/v1/agents/" -H "Authorization: Bearer ${PASSWORD}" -H "Content-Type: application/json" -d "$AGENT_JSON" > /tmp/e1_agent_enc.json
AGENT_ENC_ID=$(python3 -c "import json; print(json.load(open('/tmp/e1_agent_enc.json'))['id'])")
echo "enc agent: ${AGENT_ENC_ID}"
# strip the marker: the row is now exactly a .39-style write
CTR=$(compose ps -q letta)
docker exec "$CTR" psql -U letta -d letta -tAc "UPDATE agent_environment_variables SET value_enc = SUBSTRING(value_enc FROM 8) WHERE key='WITNESS_ENC'" >/dev/null
UNMARKED0=$(docker exec "$CTR" psql -U letta -d letta -tAc "SELECT LEFT(value_enc, 8) FROM agent_environment_variables WHERE key='WITNESS_ENC'")
case "$UNMARKED0" in
  enc:v1:*) echo "FAIL: marker strip failed"; exit 1 ;;
  *) echo "PASS: gen-0 row is unmarked ciphertext (the Delta path)" ;;
esac
# Q-1 (release review): capture this generation's logs BEFORE the teardown —
# the secret was CREATED in this container (the likeliest leak moment), and
# compose down destroys the logs. The old check only ever saw gen 2.
docker logs "$(compose ps -q letta)" > /tmp/e1_logs_gen0.txt 2>&1
compose down 2>/dev/null || true

echo "== gen 1: NO encryption key — seed a secret (stored plaintext) =="
export LETTA_ENCRYPTION_KEY=""
compose up -d letta
wait_health

AGENT_JSON=$(cat <<EOF
{
  "name": "${NAME}",
  "llm_config": {"model": "ollama/nemotron-3-nano:4b", "model_endpoint_type": "openai", "model_endpoint": "http://host.docker.internal:11434/v1", "context_window": 128000, "embedding": "ollama/embeddinggemma:latest"},
  "secrets": {"WITNESS_SECRET": "witness-plaintext-value"}
}
EOF
)
curl -s -f -X POST "$BASE/v1/agents/" -H "Authorization: Bearer ${PASSWORD}" -H "Content-Type: application/json" -d "$AGENT_JSON" > /tmp/e1_agent.json
AGENT_ID=$(python3 -c "import json; print(json.load(open('/tmp/e1_agent.json'))['id'])")
echo "agent: ${AGENT_ID}"

# verify stored PLAINTEXT in the DB (psql inside the container)
STORED=$(docker exec "$(compose ps -q letta)" psql -U letta -d letta -tAc \
  "SELECT value_enc FROM agent_environment_variables WHERE key='WITNESS_SECRET'")
if [ "$STORED" != "witness-plaintext-value" ]; then
  echo "FAIL: gen-1 storage is not plaintext (got: ${STORED:0:12}...)"
  exit 1
fi
echo "PASS: gen-1 secret stored as plaintext (the legacy condition)"

echo "== gen 2: restart WITH encryption key =="
# Q-1: same as gen 0 — the plaintext secret was submitted in THIS container.
docker logs "$(compose ps -q letta)" > /tmp/e1_logs_gen1.txt 2>&1
compose down 2>/dev/null || true
export LETTA_ENCRYPTION_KEY="${KEY}"
compose up -d letta
# NOTE: this wait used to be `docker logs | grep -q ... && break` — under
# `set -o pipefail` a MATCH SIGPIPEs docker logs and the pipeline FAILS, so
# the loop never broke early and the PASS below printed unconditionally
# (vacuous). File form + an explicit post-loop assertion.
REPORT_SEEN=0
for i in $(seq 1 60); do
  docker logs "$(compose ps -q letta)" > /tmp/e1_logs_gen2.txt 2>&1
  if grep -q "not yet marked-ciphertext" /tmp/e1_logs_gen2.txt; then REPORT_SEEN=1; break; fi
  sleep 2
done
if [ "$REPORT_SEEN" != "1" ]; then
  echo "FAIL: startup report (unmarked-rows warning) never appeared in gen-2 logs"
  exit 1
fi
wait_health
echo "PASS: startup report fired (unmarked-rows warning in logs)"

# dry run
DRY=$(docker exec "$(compose ps -q letta)" letta secrets reencrypt 2>&1)
echo "dry run output: ${DRY}"
# at least one plaintext row (the witness's own; the server may create
# additional unmarked rows of its own, e.g. a default provider key)
case "$DRY" in
  *"0 plaintext"*) echo "FAIL: dry run did not report the plaintext row"; exit 1 ;;
esac
grep -qE "[1-9][0-9]* plaintext" <<<"$DRY" || { echo "FAIL: dry run did not report plaintext rows"; exit 1; }
echo "PASS: dry run classifies the plaintext row"

# apply
APPLY=$(docker exec "$(compose ps -q letta)" letta secrets reencrypt --apply --yes 2>&1)
echo "apply output: ${APPLY}"
grep -q "apply complete" <<<"$APPLY" || { echo "FAIL: apply did not complete"; exit 1; }
echo "PASS: apply completed"

# stored value now MARKED
STORED2=$(docker exec "$(compose ps -q letta)" psql -U letta -d letta -tAc \
  "SELECT value_enc FROM agent_environment_variables WHERE key='WITNESS_SECRET'")
case "$STORED2" in
  enc:v1:*) echo "PASS: stored value is marked ciphertext" ;;
  *) echo "FAIL: stored value not marked (got: ${STORED2:0:12}...)"; exit 1 ;;
esac

# idempotent second apply
APPLY2=$(docker exec "$(compose ps -q letta)" letta secrets reencrypt --apply --yes 2>&1)
grep -q "no changes" <<<"$APPLY2" || { echo "FAIL: second apply changed something"; exit 1; }
echo "PASS: second apply is a no-op (idempotent)"

# the value SURVIVED: read it back through the API (agents endpoint returns
# secrets as names, so verify via capture of the tool env instead — simplest
# observable: the startup report went quiet)
UNMARKED=$(docker exec "$(compose ps -q letta)" psql -U letta -d letta -tAc \
  "SELECT COUNT(*) FROM agent_environment_variables WHERE value_enc IS NOT NULL AND value_enc NOT LIKE 'enc:v1:%'")
if [ "$UNMARKED" = "0" ]; then
  echo "PASS: unmarked count is zero after apply"
else
  echo "FAIL: unmarked rows remain ($UNMARKED)"
  exit 1
fi

# the gen-0 row (Delta's path): unmarked ciphertext -> marked + readable
ENC_STORED=$(docker exec "$(compose ps -q letta)" psql -U letta -d letta -tAc \
  "SELECT value_enc FROM agent_environment_variables WHERE key='WITNESS_ENC'")
case "$ENC_STORED" in
  enc:v1:*) echo "PASS: gen-0 unmarked-encrypted row is now marked" ;;
  *) echo "FAIL: gen-0 row not marked (got: ${ENC_STORED:0:12})"; exit 1 ;;
esac
ENC_VAL="$ENC_STORED" docker exec -e ENC_VAL "$(compose ps -q letta)" python -c "
import os
from letta.helpers.crypto_utils import CryptoUtils
v = CryptoUtils.decrypt(os.environ['ENC_VAL'])
assert v == 'witness-encrypted-legacy', v
print('PASS: gen-0 row round-trips to its original plaintext')
" || exit 1

# ---- no-leak (Q-1/Q-2, release review) ----
# The old check was `docker logs | grep -q value` on the gen-2 container
# only: under pipefail a REAL leak SIGPIPEs the pipeline and reports PASS,
# and the gen-0/gen-1 containers (where the secrets were created) were
# already torn down. Now: every generation's logs + the command outputs,
# file-argument grep (no pipe), both secret values, positive controls so an
# empty capture cannot read as clean, and a canary self-test proving the
# check can fail.
docker logs "$(compose ps -q letta)" > /tmp/e1_logs_gen2.txt 2>&1
{
  echo "== dry run =="; printf '%s\n' "$DRY"
  echo "== apply =="; printf '%s\n' "$APPLY"
  echo "== second apply =="; printf '%s\n' "$APPLY2"
} > /tmp/e1_cmd_out.txt

# Q-2 canary self-test: plant the value in a throwaway copy — the check MUST flag it
cp /tmp/e1_logs_gen2.txt /tmp/e1_canary.txt
printf '%s\n' "witness-plaintext-value" >> /tmp/e1_canary.txt
if ! grep -q "witness-plaintext-value" /tmp/e1_canary.txt; then
  echo "FAIL: leak check cannot detect a planted value (canary self-test)"
  exit 1
fi
echo "PASS: canary self-test — planted value is detected"

# Q-2 positive controls + Q-1 leak check over EVERY capture.
# Control lines: gen 0/1 -> uvicorn's startup banner (always present);
# gen 2 -> the startup report we asserted above; cmd output -> apply complete.
for spec in \
  "/tmp/e1_logs_gen0.txt:Application startup complete" \
  "/tmp/e1_logs_gen1.txt:Application startup complete" \
  "/tmp/e1_logs_gen2.txt:not yet marked-ciphertext" \
  "/tmp/e1_cmd_out.txt:apply complete"; do
  f="${spec%%:*}"; marker="${spec#*:}"
  sz=$(wc -c < "$f")
  if [ "$sz" -eq 0 ]; then
    echo "FAIL: $f is empty — log capture failed (compose ps -q empty?)"
    exit 1
  fi
  if ! grep -q "$marker" "$f"; then
    echo "FAIL: $f lacks its always-present line ('$marker') — capture incomplete"
    exit 1
  fi
  for val in "witness-plaintext-value" "witness-encrypted-legacy"; do
    if grep -q "$val" "$f"; then
      echo "FAIL: secret value '$val' found in $f (${sz} bytes) — LEAK"
      exit 1
    fi
  done
  echo "PASS: $f clean (${sz} bytes, control line present, no secret value)"
done
echo "PASS: no secret value in any generation's logs or command output"

echo
echo "E-1 WITNESS: ALL PASS"
