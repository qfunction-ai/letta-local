# Start with pgvector base for builder
FROM pgvector/pgvector:0.8.1-pg15 AS builder
# Install Python and required packages
RUN apt-get update && apt-get install -y \
    python3 \
    python3-venv \
    python3-full \
    build-essential \
    libpq-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

ARG LETTA_ENVIRONMENT=DEV
ENV LETTA_ENVIRONMENT=${LETTA_ENVIRONMENT} \
    UV_NO_PROGRESS=1 \
    UV_PYTHON_PREFERENCE=system \
    UV_CACHE_DIR=/tmp/uv_cache

# Set for other builds
ARG LETTA_VERSION
ENV LETTA_VERSION=${LETTA_VERSION}

WORKDIR /app

# Create and activate virtual environment
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Now install uv and uvx in the virtual environment
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/


# Copy dependency files first (cached unless pyproject.toml or uv.lock change)
COPY pyproject.toml uv.lock README.md ./

# Install dependencies before copying source — source changes don't invalidate the dep cache
RUN uv sync --frozen --no-dev --all-extras --python 3.11

# Copy the rest of the application code
COPY . .

# Runtime stage
FROM pgvector/pgvector:0.8.1-pg15 AS runtime

# Overridable Node.js version with --build-arg NODE_VERSION
ARG NODE_VERSION=22

# Allow overriding the OpenTelemetry Collector version and let Docker inject TARGETARCH during build
ARG OTEL_VERSION=0.96.0
ARG TARGETARCH

RUN apt-get update && \
    apt-get install -y curl python3 python3-venv libpq-dev redis-server && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x | bash - && \
    apt-get install -y nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    case "${TARGETARCH:-amd64}" in \
      arm64|aarch64) OTEL_ARCH=arm64 ;; \
      amd64|x86_64|x64) OTEL_ARCH=amd64 ;; \
      *) OTEL_ARCH=amd64 ;; \
    esac; \
    OTEL_FILENAME="otelcol-contrib_${OTEL_VERSION}_linux_${OTEL_ARCH}.tar.gz"; \
    echo "Downloading https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v${OTEL_VERSION}/${OTEL_FILENAME}"; \
    curl -L "https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v${OTEL_VERSION}/${OTEL_FILENAME}" -o /tmp/otel-collector.tar.gz && \
    tar xzf /tmp/otel-collector.tar.gz -C /usr/local/bin && \
    rm /tmp/otel-collector.tar.gz && \
    mkdir -p /etc/otel

# Add OpenTelemetry Collector configs
COPY otel/otel-collector-config-file.yaml /etc/otel/config-file.yaml
COPY otel/otel-collector-config-clickhouse.yaml /etc/otel/config-clickhouse.yaml
COPY otel/otel-collector-config-signoz.yaml /etc/otel/config-signoz.yaml

ARG LETTA_ENVIRONMENT=DEV
ENV LETTA_ENVIRONMENT=${LETTA_ENVIRONMENT} \
    VIRTUAL_ENV="/app/.venv" \
    PATH="/app/.venv/bin:$PATH" \
    POSTGRES_USER=letta \
    POSTGRES_PASSWORD=letta \
    POSTGRES_DB=letta

ARG LETTA_VERSION
ENV LETTA_VERSION=${LETTA_VERSION}

WORKDIR /app

# Copy virtual environment and app from builder
COPY --from=builder /app .

# Force DNS over TCP at runtime (Landlock sandbox blocks UDP; glibc resolver needs TCP)
# Handled by RES_OPTIONS=use-vc in the compose environment — no /etc/resolv.conf write needed.

# Create non-root user for running the Letta server.
# When using external Postgres/Redis (LETTA_PG_URI + LETTA_REDIS_HOST),
# set user: "1000:1000" and HOME=/home/letta in docker-compose.yml.
# When using internal Postgres/Redis, the container must run as root
# (the pgvector entrypoint uses gosu to drop to postgres).
# The user is created here so the image supports both modes.
RUN groupadd --gid 1000 letta && \
    useradd --uid 1000 --gid letta --shell /bin/bash --create-home letta && \
    mkdir -p /home/letta/.letta /data/logs && \
    chown -R letta:letta /home/letta/.letta /data/logs

# Copy initialization SQL if it exists
COPY init.sql /docker-entrypoint-initdb.d/

EXPOSE 8283 5432 6379 4317 4318

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["./letta/server/startup.sh"]
