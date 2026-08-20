#!/usr/bin/env bash
# LettaLocal release-candidate smoke test.
#
# Spins up the candidate image on an isolated port (default 8284) with a
# disposable SQLite volume, runs the checks below, and tears everything
# down (trap). Non-zero exit if any check fails.
#
# Checks are limited to paths that have actually broken in past releases:
#   1. Server healthy
#   2. Create agent
#   3. Non-streaming message                    (needs Ollama)
#   4. Streaming message, no UnboundLocalError  (needs Ollama)
#   5. capture -> reset-messages -> GET count   (no LLM needed)
#   6. Policy PUT: valid contains-rule + invalid regex -> 400 w/ rule name
#   7. Policy evaluate denies denied tool
#   8. Delete agent
#
# Usage:
#   docker build -t letta-local:smoke .
#   SMOKE_IMAGE=letta-local:smoke scripts/smoke/smoke_test.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.smoke.yml"

SMOKE_IMAGE="${SMOKE_IMAGE:-letta-local:smoke}"
SMOKE_PORT="${SMOKE_PORT:-8284}"
SMOKE_MODEL="${SMOKE_MODEL:-ollama/nemotron-3-nano:4b}"
SMOKE_EMBEDDING="${SMOKE_EMBEDDING:-ollama/embeddinggemma:latest}"
BASE="http://localhost:${SMOKE_PORT}"

PASS=0
FAIL=0
AGENT_ID=""

cleanup() {
  if [ -n "${AGENT_ID}" ]; then
    curl -s -f -X DELETE "${BASE}/v1/agents/${AGENT_ID}" >/dev/null 2>&1 || true
  fi
  SMOKE_IMAGE="${SMOKE_IMAGE}" SMOKE_PORT="${SMOKE_PORT}" \
    docker compose -f "${COMPOSE_FILE}" down -v >/dev/null 2>&1 || true
}
trap cleanup EXIT

ok()  { echo "  PASS  $1"; PASS=$((PASS + 1)); }
bad() { echo "  FAIL  $1"; FAIL=$((FAIL + 1)); }

# Count messages from GET /messages (handles bare list and {messages: [...]} shapes)
json_count() {
  python3 -c '
import sys, json
d = json.load(sys.stdin)
msgs = d if isinstance(d, list) else d.get("messages", [])
print(len(msgs))'
}

echo "== LettaLocal smoke test =="
echo "   image: ${SMOKE_IMAGE}"
echo "   port:  ${SMOKE_PORT}"
echo "   model: ${SMOKE_MODEL}"
echo

# Checks 3-4 need Ollama on the host; everything else runs without an LLM.
OLLAMA_UP=1
if curl -s -f --max-time 3 http://localhost:11434/api/tags >/dev/null 2>&1; then
  OLLAMA_UP=0
fi

echo "== starting container =="
if ! SMOKE_IMAGE="${SMOKE_IMAGE}" SMOKE_PORT="${SMOKE_PORT}" \
     docker compose -f "${COMPOSE_FILE}" up -d >/dev/null; then
  echo "compose up failed" >&2
  exit 1
fi

READY=1
for _ in $(seq 1 60); do
  if curl -s -f "${BASE}/v1/models" >/dev/null 2>&1; then READY=0; break; fi
  sleep 2
done
if [ "${READY}" -eq 0 ]; then
  ok "1. server healthy"
else
  bad "1. server healthy (no response on ${BASE} after 120s)"
  echo "aborting" >&2
  exit 1
fi

# ---------------------------------------------------------------- check 2
echo "== check 2: create agent =="
AGENT_ID="$(curl -s -f -X POST "${BASE}/v1/agents/" \
  -H "Content-Type: application/json" \
  -d "{\"model\": \"${SMOKE_MODEL}\", \"embedding\": \"${SMOKE_EMBEDDING}\", \"model_settings\": {\"provider_type\": \"ollama\", \"temperature\": 0.0}, \"memory_blocks\": [{\"label\": \"persona\", \"value\": \"You are a helpful assistant.\"}, {\"label\": \"human\", \"value\": \"A student.\"}]}" \
  | python3 -c 'import sys, json; print(json.load(sys.stdin)["id"])' 2>/dev/null || true)"
if [ -n "${AGENT_ID}" ]; then
  ok "2. create agent (${AGENT_ID})"
else
  bad "2. create agent"
  if [ "${OLLAMA_UP}" -eq 0 ]; then
    echo "       agent creation failed WITH Ollama up — investigate before release"
  else
    echo "       degraded: no Ollama and agent creation failed; cannot continue"
  fi
  echo
  echo "== smoke: ${PASS} passed, ${FAIL} failed =="
  exit 1
fi

# ---------------------------------------------------------------- checks 3-4
if [ "${OLLAMA_UP}" -eq 0 ]; then
  echo "== check 3: non-streaming message =="
  R3="$(curl -s -f --max-time 120 -X POST "${BASE}/v1/agents/${AGENT_ID}/messages" \
    -H "Content-Type: application/json" \
    -d '{"messages": [{"role": "user", "content": "Say the word apple"}], "stream": false}' || true)"
  if echo "${R3}" | grep -q '"assistant_message"'; then
    ok "3. non-streaming message"
  else
    bad "3. non-streaming message (no assistant_message in response)"
  fi

  echo "== check 4: streaming message =="
  R4="$(curl -s -N --max-time 120 -X POST "${BASE}/v1/agents/${AGENT_ID}/messages" \
    -H "Content-Type: application/json" \
    -d '{"messages": [{"role": "user", "content": "Say the word banana"}], "streaming": true, "stream_tokens": false}' || true)"
  if echo "${R4}" | grep -q "UnboundLocalError\|stopped with unknown error"; then
    bad "4. streaming message (error surfaced in stream)"
  elif echo "${R4}" | grep -q "^data:"; then
    ok "4. streaming message"
  else
    bad "4. streaming message (no data chunks)"
  fi
else
  echo "== checks 3-4 skipped (no Ollama on localhost:11434) =="
fi

# ---------------------------------------------------------------- check 5
echo "== check 5: capture -> reset-messages -> GET count =="
R5C="$(curl -s -o /dev/null -w "%{http_code}" -X POST "${BASE}/v1/agents/${AGENT_ID}/messages/capture" \
  -H "Content-Type: application/json" \
  -d '{"provider": "smoke", "model": "smoke", "request_messages": [{"role": "user", "content": "seed user message"}], "response_dict": {"content": "seed assistant message"}}')"
N_BEFORE="$(curl -s -f "${BASE}/v1/agents/${AGENT_ID}/messages?limit=100" | json_count 2>/dev/null || echo -1)"
R5R="$(curl -s -o /dev/null -w "%{http_code}" -X PATCH "${BASE}/v1/agents/${AGENT_ID}/reset-messages" \
  -H "Content-Type: application/json" \
  -d '{"add_default_initial_messages": false}')"
N_AFTER="$(curl -s -f "${BASE}/v1/agents/${AGENT_ID}/messages?limit=100" | json_count 2>/dev/null || echo -1)"
if [ "${R5C}" = "200" ] && [ "${R5R}" = "200" ] && [ "${N_BEFORE}" -ge 3 ] && [ "${N_AFTER}" -eq 1 ]; then
  ok "5. reset-messages (${N_BEFORE} -> ${N_AFTER} messages)"
else
  bad "5. reset-messages (capture=${R5C} reset=${R5R} count ${N_BEFORE} -> ${N_AFTER}, expected >=3 -> 1)"
fi

# ---------------------------------------------------------------- check 6
echo "== check 6: policy PUT (valid contains-rule, invalid regex) =="
R6A="$(curl -s -o /dev/null -w "%{http_code}" -X PUT "${BASE}/v1/agents/${AGENT_ID}/policy" \
  -H "Content-Type: application/json" \
  -d '{"denied_tools": ["execute_code"], "rules": [{"name": "block-sensitive-file", "condition": {"field": "tool_args.path", "operator": "contains", "value": "confidential"}, "action": "deny", "priority": 90}]}')"
if [ "${R6A}" = "200" ]; then
  ok "6a. policy PUT with contains operator"
else
  bad "6a. policy PUT with contains operator (http=${R6A})"
fi

ERRFILE="$(mktemp)"
R6B="$(curl -s -o "${ERRFILE}" -w "%{http_code}" -X PUT "${BASE}/v1/agents/${AGENT_ID}/policy" \
  -H "Content-Type: application/json" \
  -d '{"rules": [{"name": "block-sensitive-file", "condition": {"field": "tool_args.path", "operator": "matches", "value": "*confidential*"}, "action": "deny", "priority": 90}]}')"
if [ "${R6B}" = "400" ] && grep -q "block-sensitive-file" "${ERRFILE}"; then
  ok "6b. invalid regex -> 400 naming the rule"
else
  bad "6b. invalid regex (http=${R6B}, body: $(head -c 200 "${ERRFILE}"))"
fi
rm -f "${ERRFILE}"

# ---------------------------------------------------------------- check 7
echo "== check 7: policy/evaluate denies execute_code =="
R7="$(curl -s -X POST "${BASE}/v1/agents/${AGENT_ID}/policy/evaluate" \
  -H "Content-Type: application/json" \
  -d '{"tool_name": "execute_code", "tool_args": {"code": "print(1)"}}')"
ALLOWED="$(echo "${R7}" | python3 -c 'import sys, json; print(str(json.load(sys.stdin).get("allowed")).lower())' 2>/dev/null || true)"
if [ "${ALLOWED}" = "false" ]; then
  ok "7. policy/evaluate denies execute_code"
else
  bad "7. policy/evaluate (allowed=${ALLOWED}, body: ${R7:0:200})"
fi

# ---------------------------------------------------------------- check 8
echo "== check 8: delete agent =="
R8="$(curl -s -o /dev/null -w "%{http_code}" -X DELETE "${BASE}/v1/agents/${AGENT_ID}")"
if [ "${R8}" = "200" ] || [ "${R8}" = "204" ]; then
  ok "8. delete agent"
  AGENT_ID=""
else
  bad "8. delete agent (http=${R8})"
fi

echo
echo "== smoke: ${PASS} passed, ${FAIL} failed =="
if [ "${FAIL}" -eq 0 ]; then exit 0; else exit 1; fi
