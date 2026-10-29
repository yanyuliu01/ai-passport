#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ -e .env ]; then
  echo 'Existing .env retained. Review it before deploying; no credentials replaced.' >&2
  exit 1
fi
relay_domain="${1:-}"
case "$relay_domain" in ''|*[!a-zA-Z0-9.-]*|.*|*.) echo 'Usage: sh setup.sh relay.your-domain.example' >&2; exit 1;; esac
command -v docker >/dev/null || { echo 'Docker with Compose is required on the reachable host.' >&2; exit 1; }
umask 077
relay_token=$(docker run --rm node:22-bookworm-slim node -e 'process.stdout.write(require("crypto").randomBytes(32).toString("base64url"))')
printf 'XIAOYU_RELAY_DOMAIN=%s\nXIAOYU_RELAY_TOKEN=%s\n' "$relay_domain" "$relay_token" > .env
printf 'Configuration created with owner-only permissions. Run: docker compose up -d --build\n'
printf 'Enter wss://%s and the .env access token in desktop Advanced connection settings.\n' "$relay_domain"
