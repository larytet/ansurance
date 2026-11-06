#!/usr/bin/env bash
# Upgrade the on-prem stack to the code in this checkout: check first, back up, build,
# start, verify, and roll back automatically if the new version does not come up healthy.
#
#   git pull && scripts/upgrade.sh            (or: make upgrade)
#
# What it does, in order:
#   1. pre-flight: the running stack must be healthy (otherwise an upgrade would hide an
#      existing problem)
#   2. takes a backup (database dump, base backup, originals) and notes the schema version
#   3. tags the running images :rollback
#   4. builds and starts the new version (migrations run when the API starts)
#   5. health gate: the smoke test. If anything fails, the :rollback images are started again
#
# The database schema only moves forward. If a failed upgrade had already applied a
# migration, rolling the code back is not enough: the script says so and prints the exact
# command that restores the pre-upgrade backup into a fresh stack.
#
# Test hook: UPGRADE_SIMULATE_FAILURE=1 forces the health gate to fail.
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT=${COMPOSE_PROJECT_NAME:-ansurance}
ENVFILE=${ENV_FILE:-deploy/compose/.env}
COMPOSE=(docker compose -p "$PROJECT" -f deploy/compose/docker-compose.yml --env-file "$ENVFILE")
say() { printf '\n== %s\n' "$*"; }

schema_version() {
  "${COMPOSE[@]}" exec -T postgres psql -U ansurance -d ansurance -qAtX \
    -c "SELECT version_num FROM alembic_version" 2>/dev/null || echo unknown
}

images() { # service names that have a locally built image
  local s
  for s in $("${COMPOSE[@]}" config --services); do
    docker image inspect "${PROJECT}-${s}" >/dev/null 2>&1 && echo "$s"
  done
}

if [ "${SKIP_PREFLIGHT:-0}" != "1" ]; then
  say "1/5 pre-flight: is the running stack healthy?"
  ./scripts/smoke.sh || { echo "The stack is not healthy now; fix that before upgrading." >&2; exit 1; }
fi

say "2/5 backing up before the upgrade"
BEFORE_SCHEMA=$(schema_version)
BACKUP_OUT=$("${COMPOSE[@]}" run --rm --no-deps backup backup | tail -1)
BACKUP_STAMP=$(basename "$BACKUP_OUT")
echo "backup: $BACKUP_STAMP (schema $BEFORE_SCHEMA)"

say "3/5 keeping the running images for a rollback"
for s in $(images); do
  docker tag "${PROJECT}-${s}:latest" "${PROJECT}-${s}:rollback"
  echo "tagged ${PROJECT}-${s}:rollback"
done

rollback() {
  say "ROLLBACK: starting the previous images"
  for s in $(images); do
    if docker image inspect "${PROJECT}-${s}:rollback" >/dev/null 2>&1; then
      docker tag "${PROJECT}-${s}:rollback" "${PROJECT}-${s}:latest"
    fi
  done
  "${COMPOSE[@]}" up -d --no-build --force-recreate --wait || true
  local after
  after=$(schema_version)
  if [ "$after" != "$BEFORE_SCHEMA" ]; then
    cat >&2 <<MSG

The failed upgrade had already changed the database schema ($BEFORE_SCHEMA -> $after), and the
previous code does not know that schema. Restore the pre-upgrade backup into a fresh stack:

    scripts/restore.sh <backup location>/$BACKUP_STAMP <new-project-name>

or fix forward with the new version. Your data is safe in the backup $BACKUP_STAMP.
MSG
  else
    echo "The schema did not change, so the previous version is running again with all its data."
  fi
}

say "4/5 building and starting the new version"
export BUILD_ID=${BUILD_ID:-$(date -u +%Y%m%dT%H%M%S)}
if ! "${COMPOSE[@]}" up -d --build --wait; then
  echo "The new version did not become healthy." >&2
  rollback
  exit 1
fi

say "5/5 health gate"
if [ "${UPGRADE_SIMULATE_FAILURE:-0}" = "1" ] || ! ./scripts/smoke.sh; then
  echo "The health gate failed${UPGRADE_SIMULATE_FAILURE:+ (simulated)}." >&2
  rollback
  exit 1
fi

AFTER_SCHEMA=$(schema_version)
say "upgraded: schema $BEFORE_SCHEMA -> $AFTER_SCHEMA, build $BUILD_ID, backup $BACKUP_STAMP"
echo "To go back: scripts/upgrade.sh from the previous checkout, or restore backup $BACKUP_STAMP."
