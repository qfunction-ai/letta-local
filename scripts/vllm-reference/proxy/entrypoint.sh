#!/bin/sh
# vLLM proxy entrypoint — DRAFT. Renders the nginx site config from the two secrets into tmpfs, then starts nginx.
# Keys are read from Docker secrets, never from the environment (env is visible in `docker inspect`).
set -eu

PROXY_KEY_FILE=/run/secrets/vllm_proxy_key
UPSTREAM_KEY_FILE=/run/secrets/vllm_upstream_key

for f in "$PROXY_KEY_FILE" "$UPSTREAM_KEY_FILE"; do
  if [ ! -s "$f" ]; then
    echo "FATAL: secret $f missing or empty" >&2
    exit 1
  fi
done

PROXY_KEY=$(cat "$PROXY_KEY_FILE")
UPSTREAM_KEY=$(cat "$UPSTREAM_KEY_FILE")

# Refuse keys that could break out of the nginx string literal or are too weak.
case "$PROXY_KEY$UPSTREAM_KEY" in
  *[!A-Za-z0-9_-]*) echo "FATAL: keys must be [A-Za-z0-9_-] only (generate with: openssl rand -hex 32)" >&2; exit 1 ;;
esac
if [ ${#PROXY_KEY} -lt 32 ] || [ ${#UPSTREAM_KEY} -lt 32 ]; then
  echo "FATAL: keys must be at least 32 characters" >&2
  exit 1
fi
if [ "$PROXY_KEY" = "$UPSTREAM_KEY" ]; then
  echo "FATAL: proxy and upstream keys must differ" >&2
  exit 1
fi

umask 077
sed -e "s|__PROXY_KEY__|$PROXY_KEY|" -e "s|__UPSTREAM_KEY__|$UPSTREAM_KEY|" \
  /proxy/vllm.conf.template > /etc/nginx/conf.d/vllm.conf

# The image's default site would also listen; remove it if the image ships one.
rm -f /etc/nginx/conf.d/default.conf

nginx -t
exec nginx -g 'daemon off;'
