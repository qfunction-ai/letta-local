#!/usr/bin/env bash
# Stage 1 (config) and stage 3 (isolation) checks for the vLLM reference deployment — DRAFT, 2026-11-26, untested.
# Follows the LettaLocal rig rules from v0.16.40:
#   - no `cmd | grep -q` (pipefail turns an early grep exit into a false result); values are captured, then compared;
#   - every negative check has a positive control, so a dead probe can't pass;
#   - a canary self-test proves the comparison function fails when it should.
#
# Usage (from the LettaLocal repo root, stack already up):
#   COMPOSE="docker compose -f scripts/smoke/docker-compose.smoke.yml -f docker-compose.vllm.yml" \
#   VLLM_SECRETS_DIR=/path/to/secrets VLLM_SERVED_NAME=gemma-4-26b bash check_vllm_isolation.sh
set -euo pipefail

: "${COMPOSE:?set COMPOSE to the docker compose command with both files}"
: "${VLLM_SECRETS_DIR:?}"
: "${VLLM_SERVED_NAME:?}"
PROXY_KEY=$(cat "$VLLM_SECRETS_DIR/proxy_key")
PROXY=http://vllm-proxy:8080

PASS=0; FAIL=0
pass() { echo "PASS: $*"; PASS=$((PASS + 1)); }
fail() { echo "FAIL: $*"; FAIL=$((FAIL + 1)); }

# --- probes (run inside the letta container, which has python) --------------------------------------------------

# http_status METHOD URL [AUTH_HEADER_VALUE] -> prints the HTTP status, or "ERR:<reason>" if no response
http_status() {
  $COMPOSE exec -T letta python3 - "$1" "$2" "${3:-}" <<'PY'
import sys, urllib.request, urllib.error
method, url, auth = sys.argv[1], sys.argv[2], sys.argv[3]
data = b"{}" if method == "POST" else None
req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
if auth:
    req.add_header("Authorization", auth)
try:
    print(urllib.request.urlopen(req, timeout=10).status)
except urllib.error.HTTPError as e:
    print(e.code)
except Exception as e:
    print("ERR:" + type(e).__name__)
PY
}

# tcp_connect SERVICE HOST PORT -> prints OK or ERR:<reason>
tcp_connect() {
  $COMPOSE exec -T "$1" python3 - "$2" "$3" <<'PY'
import sys, socket
try:
    socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=5).close()
    print("OK")
except Exception as e:
    print("ERR:" + type(e).__name__)
PY
}

# expect LABEL ACTUAL EXPECTED  (exact string match)
expect() {
  if [ "$2" = "$3" ]; then pass "$1 ($2)"; else fail "$1: expected $3, got $2"; fi
}
# expect_err LABEL ACTUAL  (must be a failure to connect, not a response)
expect_err() {
  case "$2" in ERR:*) pass "$1 ($2)";; *) fail "$1: expected a connection failure, got $2";; esac
}

# --- canary self-test: the comparison functions must be able to fail -------------------------------------------
before_fail=$FAIL
expect "canary (must FAIL)" "200" "403" >/dev/null
expect_err "canary (must FAIL)" "OK" >/dev/null
if [ $((FAIL - before_fail)) -eq 2 ]; then
  FAIL=$before_fail; echo "PASS: canary self-test (both comparisons detected a mismatch)"; PASS=$((PASS + 1))
else
  echo "FAIL: canary self-test — the comparison functions cannot fail; aborting"; exit 1
fi

# --- positive controls ------------------------------------------------------------------------------------------
expect "control: letta reaches the proxy over TCP" "$(tcp_connect letta vllm-proxy 8080)" "OK"
expect "control: authorized GET /v1/models through the proxy" "$(http_status GET "$PROXY/v1/models" "Bearer $PROXY_KEY")" "200"
expect "control: vLLM answers on its own loopback" "$(tcp_connect vllm 127.0.0.1 8000)" "OK"

# --- stage 3: isolation ------------------------------------------------------------------------------------------
expect_err "letta cannot reach vLLM directly (vllm:8000)" "$(tcp_connect letta vllm 8000)"
expect_err "vLLM has no internet egress (1.1.1.1:443)" "$(tcp_connect vllm 1.1.1.1 443)"

expect "no key -> 401" "$(http_status GET "$PROXY/v1/models")" "401"
expect "wrong key -> 401" "$(http_status GET "$PROXY/v1/models" "Bearer wrong-key-0000000000000000000000000000")" "401"
expect "POST /v1/models -> 403" "$(http_status POST "$PROXY/v1/models" "Bearer $PROXY_KEY")" "403"
expect "GET /v1/chat/completions -> 403" "$(http_status GET "$PROXY/v1/chat/completions" "Bearer $PROXY_KEY")" "403"

for path in /invocations /pause /resume /pooling /classify /score /rerank /tokenize /detokenize /metrics \
            /version /health /collective_rpc /server_info /v1/completions /v1/embeddings /v1/responses \
            /v1/load_lora_adapter /inference/v1/generate /start_profile; do
  for method in GET POST; do
    expect "$method $path -> 403" "$(http_status "$method" "$PROXY$path" "Bearer $PROXY_KEY")" "403"
  done
done

# A model outside the pinned set must not be served.
status=$($COMPOSE exec -T letta python3 - "$PROXY_KEY" <<'PY'
import json, sys, urllib.request, urllib.error
body = json.dumps({"model": "not-a-pinned-model", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1}).encode()
req = urllib.request.Request("http://vllm-proxy:8080/v1/chat/completions", data=body, method="POST",
                             headers={"Content-Type": "application/json", "Authorization": "Bearer " + sys.argv[1]})
try:
    print(urllib.request.urlopen(req, timeout=30).status)
except urllib.error.HTTPError as e:
    print(e.code)
except Exception as e:
    print("ERR:" + type(e).__name__)
PY
)
case "$status" in 4??) pass "unpinned model rejected ($status)";; *) fail "unpinned model: expected 4xx, got $status";; esac

# /v1/models must list exactly the served name.
models=$($COMPOSE exec -T letta python3 - "$PROXY_KEY" <<'PY'
import json, sys, urllib.request
req = urllib.request.Request("http://vllm-proxy:8080/v1/models", headers={"Authorization": "Bearer " + sys.argv[1]})
print(",".join(sorted(m["id"] for m in json.load(urllib.request.urlopen(req, timeout=10))["data"])))
PY
)
expect "served models are exactly the pinned name" "$models" "$VLLM_SERVED_NAME"

# --- stage 1: effective configuration ----------------------------------------------------------------------------
vllm_cfg=$($COMPOSE exec -T vllm python3 - <<'PY'
import os
cmd = open("/proc/1/cmdline", "rb").read().replace(b"\0", b" ").decode()
env = os.environ
checks = {
    "no_trust_remote_code": "--trust-remote-code" not in cmd and "trust_remote_code" not in cmd,
    "hf_offline": env.get("HF_HUB_OFFLINE") == "1",
    "usage_stats_off": env.get("VLLM_NO_USAGE_STATS") == "1" and env.get("DO_NOT_TRACK") == "1",
    "dev_mode_off": env.get("VLLM_SERVER_DEV_MODE", "0") == "0",
    "runtime_lora_off": env.get("VLLM_ALLOW_RUNTIME_LORA_UPDATING", "False").lower() in ("false", "0", ""),
    "no_profiler": "--profiler-config" not in cmd,
    "no_lora": "--enable-lora" not in cmd,
    "no_python_O": not os.environ.get("PYTHONOPTIMIZE"),
    "not_root": os.getuid() != 0,
}
print(" ".join(f"{k}={'ok' if v else 'BAD'}" for k, v in checks.items()))
PY
)
for item in $vllm_cfg; do
  case "$item" in *=ok) pass "vllm config: ${item%=ok}";; *) fail "vllm config: $item";; esac
done

# Container hardening, read from docker inspect.
for svc in vllm vllm-proxy; do
  cid=$($COMPOSE ps -q "$svc")
  ro=$(docker inspect -f '{{.HostConfig.ReadonlyRootfs}}' "$cid")
  user=$(docker inspect -f '{{.Config.User}}' "$cid")
  capdrop=$(docker inspect -f '{{json .HostConfig.CapDrop}}' "$cid")
  secopt=$(docker inspect -f '{{json .HostConfig.SecurityOpt}}' "$cid")
  expect "$svc read-only rootfs" "$ro" "true"
  case "$user" in ""|0|0:*|root|root:*) fail "$svc runs as root ($user)";; *) pass "$svc non-root ($user)";; esac
  case "$capdrop" in *'"ALL"'*) pass "$svc drops all capabilities";; *) fail "$svc cap_drop: $capdrop";; esac
  case "$secopt" in *no-new-privileges*) pass "$svc no-new-privileges";; *) fail "$svc security_opt: $secopt";; esac
done

# TODO (needs the LettaLocal tool-run API): from inside the Landlock tool sandbox,
#   (a) a TCP connect to vllm:8000 must fail;
#   (b) GET http://vllm-proxy:8080/v1/models without a key must return 401;
#   (c) the proxy key must not be readable (env, /proc, files) — ER-3.4.
# The sandbox shares the letta container's network namespace, so (b) and (c) are what stop it, not the network.

echo
echo "RESULT: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
