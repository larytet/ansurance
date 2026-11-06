#!/usr/bin/env bash
# T-M0-7, T-M0-8: the running stack answers, serves the web app, sets security headers,
# and publishes only the proxy port.
set -euo pipefail
cd "$(dirname "$0")/.."
ENV=deploy/compose/.env
BIND=$(grep -E '^BIND_ADDR=' "$ENV" | cut -d= -f2); BIND=${BIND:-127.0.0.1}
PORT=$(grep -E '^HTTP_PORT=' "$ENV" | cut -d= -f2); PORT=${PORT:-8080}
BASE="http://${BIND}:${PORT}"
fail() { echo "SMOKE FAIL: $*" >&2; exit 1; }

health=$(curl -fsS "$BASE/api/health") || fail "health"
echo "$health" | grep -q '"status":"ok"' || fail "health body: $health"
curl -fsS "$BASE/api/ready" | grep -q '"status":"ready"' || fail "ready"
curl -fsS "$BASE/" | grep -qi '<title>ansurance</title>' || fail "web app not served"

hdrs=$(curl -fsS -D - -o /dev/null "$BASE/api/health")
for h in 'x-content-type-options: nosniff' 'x-frame-options: DENY' 'content-security-policy' 'x-request-id'; do
  echo "$hdrs" | grep -qi "$h" || fail "missing header $h"
done
echo "$hdrs" | grep -qi '^server:' && fail "Server header must be removed"

# only the proxy publishes a host port
published=$(docker compose -f deploy/compose/docker-compose.yml --env-file "$ENV" ps --format '{{.Service}}|{{.Publishers}}' \
  | grep -E '\{[^ ]* [0-9]+ [1-9][0-9]* ' | cut -d'|' -f1 | sort -u | tr '\n' ' ')
[ "$published" = "proxy " ] || fail "published services: '$published' (expected only proxy)"

unhealthy=$(docker compose -f deploy/compose/docker-compose.yml --env-file "$ENV" ps --format '{{.Service}} {{.Health}}' | awk '$2=="unhealthy"')
[ -z "$unhealthy" ] || fail "unhealthy: $unhealthy"
echo "SMOKE OK ($BASE)"
