#!/usr/bin/env bash
# Run the black-box conformance checks against the local compose stack.
# Creates a throw-away manager account with the CLI, then runs scripts/conformance.py.
set -euo pipefail
cd "$(dirname "$0")/.."
COMPOSE=(docker compose -f deploy/compose/docker-compose.yml --env-file deploy/compose/.env)
PW="a-conformance-password-1"
EMAIL="conf-mgr-$(date +%s)@conf.il"
OUT=$("${COMPOSE[@]}" exec -T api python -m app.cli create-user \
  --email "$EMAIL" --name Conformance --role manager --password "$PW")
SECRET=$(printf '%s\n' "$OUT" | sed -n 's/.*secret=\([A-Z2-7]*\).*/\1/p' | head -1)
[ -n "$SECRET" ] || { echo "could not read the TOTP secret" >&2; exit 1; }
ANS_BASE_URL=https://127.0.0.1:8443 ANS_VERIFY_TLS=0 \
ANS_CONF_EMAIL="$EMAIL" ANS_CONF_PASSWORD="$PW" ANS_CONF_TOTP_SECRET="$SECRET" \
  uv run --project backend python scripts/conformance.py
