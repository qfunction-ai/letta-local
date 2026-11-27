#!/bin/sh
set -e  # Exit on any error

# DNS-over-TCP is now handled by RES_OPTIONS=use-vc in the compose
# environment, which glibc reads at resolver init. No need to write
# to /etc/resolv.conf at runtime.

HOST="${HOST:-0.0.0.0}"
export HOST  # v0.16.40.1 (A-1): without the export, app.py reads os.getenv("HOST") -> None -> "localhost" -> the U-7 guard treats a 0.0.0.0 bind as loopback and 403s every service-name request
PORT="${PORT:-8283}"

# v0.16.41 (S-2/F-5): LETTA_DIR lives under /data (volume-backed, mode
# 755 — traversable by the sandbox uid). The old /root/.letta layout made
# /root (mode 700) a hard traversal wall: uid 1000 could not reach the
# exec dirs at all (the F-5 spike's root cause). /root/.letta is
# SUPPORTED ONLY for pre-.41 volumes detected below (loud, with the exact
# compose change); a fresh /root/.letta is refused.
if [ -n "${LETTA_DIR:-}" ]; then
    echo "LETTA_DIR override detected: ${LETTA_DIR}"
else
    if [ -d /root/.letta ] && [ -n "$(ls -A /root/.letta 2>/dev/null | head -1)" ]; then
        echo "WARNING: a populated /root/.letta was found (a pre-v0.16.41 volume mount)."
        echo "v0.16.41 moved LETTA_DIR to /data/letta (the sandbox uid cannot traverse /root)."
        echo "ACTION REQUIRED: change your volume mount from ':/root/.letta' to ':/data/letta'"
        echo "and move your existing data (agents, secrets, config) into it, e.g.:"
        echo "  docker compose down"
        echo "  docker run --rm -v <your-volume>:/from -v <your-new-volume>:/to alpine sh -c 'cp -a /from/. /to/'"
        echo "Then re-up. Continuing with the OLD path so your data keeps working this boot."
        LETTA_DIR_OVERRIDE_BANNER=1
    fi
    if [ "${LETTA_DIR_OVERRIDE_BANNER:-0}" != "1" ]; then
        export LETTA_DIR="/data/letta"
    fi
fi
if [ -n "${LETTA_DIR:-}" ]; then
    mkdir -p "${LETTA_DIR}" 2>/dev/null || true
fi

# v0.16.40 (U-6, review P-2): the CONTAINER requires secure mode. A 0.0.0.0
# bind in a container is what makes the port reachable from outside it, so
# insecure mode in a container is exactly the case U-6 exists to prevent.
# Loopback binds still work (docker run --network host with HOST=127.0.0.1
# for local development). The error names the fix.
if [ "${LETTA_SERVER_SECURE:-}" = "false" ] || [ "${SECURE:-}" = "false" ]; then
    case "$HOST" in
        localhost|127.0.0.1|::1)
            echo "WARNING: insecure mode on loopback ($HOST) — local development only."
            ;;
        *)
            echo "ERROR: insecure mode (LETTA_SERVER_SECURE=false) refuses to bind $HOST in the container."
            echo "Set LETTA_SERVER_PASSWORD, or run with --network host and HOST=127.0.0.1."
            exit 1
            ;;
    esac
fi


# Function to wait for PostgreSQL to be ready
wait_for_postgres() {
    until pg_isready -U "${POSTGRES_USER:-letta}" -h localhost; do
        echo "Waiting for PostgreSQL to be ready..."
        sleep 2
    done
}

# Function to wait for Redis to be ready
wait_for_redis() {
    until redis-cli ping 2>/dev/null | grep -q PONG; do
        echo "Waiting for Redis to be ready..."
        sleep 1
    done
}

# Check if we're configured for external Redis
if [ -n "$LETTA_REDIS_HOST" ]; then
    echo "External Redis configuration detected, using env var LETTA_REDIS_HOST=$LETTA_REDIS_HOST"
else
    echo "No external Redis configuration detected, starting internal Redis..."
    # v0.16.41 (S-2 + U-3): internal Redis binds LOOPBACK with a generated
    # password. The password is written to a 0600 config file — NEVER argv
    # (/proc/<pid>/cmdline is world-readable; a --requirepass argument would
    # hand it to the sandbox uid). It rides the server's environment
    # (LETTA_REDIS_PASSWORD), protected by the sandbox uid split.
    REDIS_PASSWORD="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    REDIS_CONF="${LETTA_DIR:-/data/letta}/redis.conf"
    mkdir -p "$(dirname "$REDIS_CONF")"
    cat > "$REDIS_CONF" <<REDISCONF
bind 127.0.0.1
requirepass ${REDIS_PASSWORD}
REDISCONF
    chmod 600 "$REDIS_CONF"
    redis-server "$REDIS_CONF" --daemonize yes

    # Wait for Redis to be ready (authenticated ping)
    until redis-cli -a "$REDIS_PASSWORD" ping 2>/dev/null | grep -q PONG; do
        echo "Waiting for Redis to be ready..."
        sleep 1
    done

    # Set default Redis host + password for the server (env — the uid
    # split keeps it away from tool code)
    export LETTA_REDIS_HOST="localhost"
    export LETTA_REDIS_PASSWORD="$REDIS_PASSWORD"
    echo "Using internal Redis at: $LETTA_REDIS_HOST (loopback, generated password)"
fi

# Check if we're configured for external Postgres
if [ -n "$LETTA_PG_URI" ]; then
    echo "External Postgres configuration detected, using env var LETTA_PG_URI"
else
    echo "No external Postgres configuration detected, starting internal PostgreSQL..."
    # v0.16.41 (S-2): internal Postgres uses SCRAM auth + a GENERATED
    # password. initdb only honors POSTGRES_PASSWORD on FIRST init, so the
    # generated value is persisted (0600, under LETTA_DIR) and reused on
    # restarted volumes. The password rides the server's ENV
    # (LETTA_PG_URI) — protected from tool code by the sandbox uid split.
    # U-3: never argv (cmdline is world-readable).
    if [ -z "${POSTGRES_PASSWORD:-}" ] || [ "${POSTGRES_PASSWORD:-}" = "letta" ]; then
        if [ -f "${LETTA_DIR:-/data/letta}/pg_password" ]; then
            POSTGRES_PASSWORD="$(cat "${LETTA_DIR:-/data/letta}/pg_password")"
        else
            POSTGRES_PASSWORD="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
            mkdir -p "${LETTA_DIR:-/data/letta}"
            printf '%s' "$POSTGRES_PASSWORD" > "${LETTA_DIR:-/data/letta}/pg_password"
            chmod 600 "${LETTA_DIR:-/data/letta}/pg_password"
        fi
        export POSTGRES_PASSWORD
    fi
    export POSTGRES_HOST_AUTH_METHOD="${POSTGRES_HOST_AUTH_METHOD:-scram-sha-256}"
    export POSTGRES_INITDB_ARGS="${POSTGRES_INITDB_ARGS:---auth-local=scram-sha-256 --auth-host=scram-sha-256}"

    # Start PostgreSQL using the base image's entrypoint script
    /usr/local/bin/docker-entrypoint.sh postgres &

    # Wait for PostgreSQL to be ready
    wait_for_postgres

    # Set default connection URI for internal postgres
    export LETTA_PG_URI="postgresql://${POSTGRES_USER:-letta}:${POSTGRES_PASSWORD}@localhost:5432/${POSTGRES_DB:-letta}"
    echo "Using internal PostgreSQL at: localhost:5432 (scram-sha-256, generated password)"
fi

# Attempt database migration
echo "Attempting to migrate database..."
if ! alembic upgrade head; then
    echo "ERROR: Database migration failed!"
    echo "Please check your database connection and try again."
    echo "If the problem persists, check the logs for more details."
    exit 1
fi
echo "Database migration completed successfully."

# Set permissions for tool execution directory if configured
if [ -n "$LETTA_SANDBOX_MOUNT_PATH" ]; then
    if ! chmod 777 "$LETTA_SANDBOX_MOUNT_PATH"; then
        echo "ERROR: Failed to set permissions for tool execution directory at: $LETTA_SANDBOX_MOUNT_PATH"
        echo "Please check that the directory exists and is accessible"
        exit 1
    fi
fi

# If ADE is enabled, add the --ade flag to the command
CMD="letta server --host $HOST --port $PORT"
if [ "${SECURE:-false}" = "true" ]; then
    CMD="$CMD --secure"
fi

# Start OpenTelemetry Collector in the background
if [ -n "$CLICKHOUSE_ENDPOINT" ] && [ -n "$CLICKHOUSE_PASSWORD" ]; then
    echo "Starting OpenTelemetry Collector with Clickhouse export..."
    CONFIG_FILE="/etc/otel/config-clickhouse.yaml"
elif [ -n "$SIGNOZ_ENDPOINT" ] && [ -n "$SIGNOZ_INGESTION_KEY" ]; then
    echo "Starting OpenTelemetry Collector with Signoz export..."
    CONFIG_FILE="/etc/otel/config-signoz.yaml"
else
    echo "Starting OpenTelemetry Collector with file export only..."
    CONFIG_FILE="/etc/otel/config-file.yaml"
fi

/usr/local/bin/otelcol-contrib --config "$CONFIG_FILE" &
OTEL_PID=$!

# Function to cleanup processes on exit
cleanup() {
    echo "Shutting down..."
    kill $OTEL_PID
    wait $OTEL_PID
}
trap cleanup EXIT

echo "Starting Letta Server at http://$HOST:$PORT..."
echo "Executing: $CMD"
exec $CMD
