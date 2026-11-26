#!/bin/sh
set -e  # Exit on any error

# DNS-over-TCP is now handled by RES_OPTIONS=use-vc in the compose
# environment, which glibc reads at resolver init. No need to write
# to /etc/resolv.conf at runtime.

HOST="${HOST:-0.0.0.0}"
export HOST  # v0.16.40.1 (A-1): without the export, app.py reads os.getenv("HOST") -> None -> "localhost" -> the U-7 guard treats a 0.0.0.0 bind as loopback and 403s every service-name request
PORT="${PORT:-8283}"

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
    redis-server --daemonize yes --bind 0.0.0.0

    # Wait for Redis to be ready
    wait_for_redis

    # Set default Redis host for internal redis
    export LETTA_REDIS_HOST="localhost"
    echo "Using internal Redis at: $LETTA_REDIS_HOST"
fi

# Check if we're configured for external Postgres
if [ -n "$LETTA_PG_URI" ]; then
    echo "External Postgres configuration detected, using env var LETTA_PG_URI"
else
    echo "No external Postgres configuration detected, starting internal PostgreSQL..."
    # Start PostgreSQL using the base image's entrypoint script
    /usr/local/bin/docker-entrypoint.sh postgres &

    # Wait for PostgreSQL to be ready
    wait_for_postgres

    # Set default connection URI for internal postgres
    export LETTA_PG_URI="postgresql://${POSTGRES_USER:-letta}:${POSTGRES_PASSWORD:-letta}@localhost:5432/${POSTGRES_DB:-letta}"
    echo "Using internal PostgreSQL at: $LETTA_PG_URI"
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
