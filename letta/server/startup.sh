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
        echo "Then re-up. Continuing with the OLD path this boot: agents and data load,"
        echo "BUT TOOL EXECUTION IS DISABLED (the sandbox uid cannot traverse /root)."
        echo "Every tool call returns a named refusal until the volume is moved."
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

    # v0.16.41 (interim-5 V-1): record whether the DATABASE predates this
    # boot BEFORE starting Postgres — initdb runs only when PGDATA is
    # absent, so PG_EXISTED=1 means the db user's password and pg_hba.conf
    # are whatever the volume was created with. (The previous gate — the
    # LETTA_DIR password file — was always written a few lines above, so
    # the convergence never ran. The marker below lives IN PGDATA, so it
    # also handles a pre-.41 database volume paired with a fresh LETTA_DIR,
    # which is exactly the old-mount migration case.)
    PGDATA_DIR="${PGDATA:-/var/lib/postgresql/data}"
    PG_EXISTED=0
    if [ -f "$PGDATA_DIR/PG_VERSION" ]; then
        PG_EXISTED=1
    fi

    # Start PostgreSQL using the base image's entrypoint script.
    # v0.16.41 (interim-4 G-3.1): loopback only — the image default is '*'.
    # listen_addresses is not a secret; the command line is fine (U-3).
    /usr/local/bin/docker-entrypoint.sh postgres -c listen_addresses=127.0.0.1 &

    # Wait for PostgreSQL to be ready
    wait_for_postgres

    # v0.16.41 (interim-6 W-2): a FRESH volume just initdb'd under .41 —
    # the auth is already scram + the generated password. Write the marker
    # NOW, or every later boot of this healthy install would take the
    # convergence branch and warn about a legacy password that never
    # existed (misleading operators to set LEGACY_PG_PASSWORD).
    if [ "${PG_EXISTED}" = "0" ]; then
        touch "$PGDATA_DIR/.letta_v041_scram"
    fi

    # v0.16.41 (interim-4 G-2, interim-5 V-1): on a pre-.41 DATABASE, initdb
    # never re-runs — converge auth to the .41 posture: ALTER USER to the
    # generated password (statement via STDIN — V-2: never argv; PGPASSWORD
    # env carries the old one), and scram-sha-256 in pg_hba. The marker
    # (.letta_v041_scram) lives in PGDATA so the database's own state
    # decides, and idempotent re-runs skip.
    if [ "${PG_EXISTED}" = "1" ] && [ ! -f "$PGDATA_DIR/.letta_v041_scram" ]; then
        echo "Pre-v0.16.41 Postgres volume detected: converging auth to scram + generated password"
        PGPASSWORD="${LEGACY_PG_PASSWORD:-letta}" psql -h localhost -U "${POSTGRES_USER:-letta}" -d postgres >/dev/null 2>&1 <<'PSQLPROBE'
SELECT 1;
PSQLPROBE
        if [ $? -eq 0 ]; then
            # V-2: the ALTER statement goes through stdin — the new password
            # never appears in /proc/<pid>/cmdline.
            PGPASSWORD="${LEGACY_PG_PASSWORD:-letta}" psql -h localhost -U "${POSTGRES_USER:-letta}" -d postgres >/dev/null 2>&1 <<PSQLALTER
ALTER USER ${POSTGRES_USER:-letta} WITH PASSWORD '${POSTGRES_PASSWORD}';
PSQLALTER
            if [ $? -eq 0 ]; then
                echo "  ALTER USER applied"
                # Rewrite pg_hba.conf: scram for local + host lines (the
                # method column only — comments mentioning 'trust' are left
                # alone).
                HBA="$PGDATA_DIR/pg_hba.conf"
                if [ -f "$HBA" ]; then
                    sed -i -E 's/^(local[[:space:]]+all[[:space:]]+all[[:space:]]+)(peer|trust|md5|password|scram-sha-256)\b/\1scram-sha-256/' "$HBA"
                    sed -i -E 's/^(host[[:space:]]+all[[:space:]]+all[[:space:]]+[^[:space:]]+[[:space:]]+)(trust|md5|password|scram-sha-256)\b/\1scram-sha-256/' "$HBA"
                    # Residual 'trust' anywhere on a RULE line (non-comment) is
                    # the real risk — force scram on lines that look like rules.
                    sed -i -E '/^[[:space:]]*(local|host)/ s/\btrust\b/scram-sha-256/g' "$HBA"
                    # Reload via SQL (pg_reload_conf) — pg_ctl as root
                    # cannot touch the postgres-owned data dir; the SQL
                    # path runs as the connected user and actually takes
                    # effect. Verified by the V-1 witness: without an
                    # effective reload the running server kept the OLD
                    # (trust) hba in memory and the old password still
                    # authenticated.
                    # X-3 (interim-7): a failed reload means the RUNNING
                    # postgres still honors the old TRUST hba — every local
                    # connection gets in without a password for the whole
                    # boot, defeating the SCRAM change. FAIL CLOSED:
                    # restart postgres so the new hba is loaded from disk,
                    # then re-wait. A failed restart aborts the boot with a
                    # named error (better than running on trust auth).
                    _reloaded=0
                    if PGPASSWORD="${POSTGRES_PASSWORD}" psql -h localhost -U "${POSTGRES_USER:-letta}" -d postgres -tAc "SELECT pg_reload_conf();" >/dev/null 2>&1; then
                        echo "  pg_hba.conf converged to scram-sha-256 (reloaded)"
                        _reloaded=1
                    else
                        echo "  pg_reload_conf() failed — restarting postgres to load the new hba (fail closed)"
                        # Z-1 (interim-9): NO output redirect — without -l the
                        # restarted postmaster inherits pg_ctl's stdout/stderr;
                        # redirecting them to /dev/null would discard every
                        # Postgres log line for the rest of the boot.
                        if gosu postgres pg_ctl -D "$PGDATA_DIR" restart -w -t 60; then
                            wait_for_postgres
                            _reloaded=1
                        else
                            echo "  ERROR: postgres restart after hba convergence failed."
                            echo "  The running server still honors the OLD (trust) pg_hba. Refusing to start"
                            echo "  the Letta server on trust auth (S-2). Fix pg_hba.conf manually and re-run."
                            exit 1
                        fi
                    fi
                fi
                # Marker IN the database dir: the db's own auth state now
                # matches .41 AND the running server loaded it. Idempotent
                # for every later boot. (X-3: only after _reloaded=1.)
                if [ "${_reloaded:-0}" = "1" ]; then
                    touch "$PGDATA_DIR/.letta_v041_scram"
                fi
                # Keep the LETTA_DIR password file in sync (a fresh LETTA_DIR
                # paired with an old db volume needs it for later boots).
                if [ ! -f "${LETTA_DIR:-/data/letta}/pg_password" ]; then
                    mkdir -p "${LETTA_DIR:-/data/letta}"
                    printf '%s' "$POSTGRES_PASSWORD" > "${LETTA_DIR:-/data/letta}/pg_password"
                    chmod 600 "${LETTA_DIR:-/data/letta}/pg_password"
                fi
            else
                echo "  WARNING: could not ALTER USER (old password not 'letta'?)."
                echo "  Set LEGACY_PG_PASSWORD to the volume's actual password and re-run."
                echo "  The server will try the generated password and may fail to connect."
            fi
        else
            echo "  WARNING: cannot connect with the legacy default password."
            echo "  Set LEGACY_PG_PASSWORD to the volume's actual password and re-run."
        fi
    fi

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
