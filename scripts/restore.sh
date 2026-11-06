#!/usr/bin/env bash
# Disaster recovery: bring up an EMPTY stack and restore a backup into it.
#
#   scripts/restore.sh <backup-dir> [project]
#       restore the database from the dump and the originals from the backup
#   scripts/restore.sh <backup-dir> [project] --to "2026-11-06 14:30:00+00" --wal <wal-dir>
#       point-in-time recovery: unpack the base backup and replay the archived write-ahead
#       log up to that moment (use it to undo a mistake, or to lose less than a day of data)
#
# <backup-dir> is the timestamped directory inside the backup location; its sibling `files/`
# holds the originals. The project (default: ansurance) must be new: no data in its database.
# The BACKUP_PASSPHRASE that made the backup must be in deploy/compose/.env (or ENV_FILE).
set -euo pipefail
cd "$(dirname "$0")/.."

DIR=${1:?usage: scripts/restore.sh <backup-dir> [project] [--to TIME --wal DIR]}
PROJECT=${2:-ansurance}
shift 2 || true
TARGET="" WAL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --to) TARGET=$2; shift 2 ;;
    --wal) WAL=$2; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
ENVFILE=${ENV_FILE:-deploy/compose/.env}
COMPOSE=(docker compose -p "$PROJECT" -f deploy/compose/docker-compose.yml --env-file "$ENVFILE")
DIR=$(cd "$DIR" && pwd)
PARENT=$(dirname "$DIR")

[ -f "$DIR/manifest.json" ] || { echo "no manifest.json in $DIR" >&2; exit 1; }
[ -d "$PARENT/files" ] || { echo "no files/ next to $DIR" >&2; exit 1; }

if [ -z "$TARGET" ]; then
  echo "1/4 starting an empty database and object store (project $PROJECT)"
  "${COMPOSE[@]}" up -d --wait postgres objectstore
  "${COMPOSE[@]}" up objectstore-init

  echo "2/4 restoring the database and the originals"
  "${COMPOSE[@]}" run --build --rm --no-deps -v "$PARENT:/restore:ro" \
    backup restore "/restore/$(basename "$DIR")"
else
  [ -n "$WAL" ] || { echo "--to needs --wal <directory with the archived log>" >&2; exit 2; }
  WAL=$(cd "$WAL" && pwd)
  echo "1/4 creating the volumes and the object store (project $PROJECT)"
  "${COMPOSE[@]}" up --no-start postgres
  "${COMPOSE[@]}" up -d --wait objectstore
  "${COMPOSE[@]}" up objectstore-init

  echo "2/4 unpacking the base backup and preparing the replay up to $TARGET"
  "${COMPOSE[@]}" run --build --rm --no-deps -u root \
    -v "${PROJECT}_pgdata:/pgdata" -v "$PARENT:/restore:ro" -v "$WAL:/walsrc:ro" \
    -e WAL_SRC=/walsrc \
    backup pitr "/restore/$(basename "$DIR")" "$TARGET"
  "${COMPOSE[@]}" run --build --rm --no-deps -v "$PARENT:/restore:ro" \
    backup restore-files "/restore/$(basename "$DIR")"
  "${COMPOSE[@]}" up -d --wait postgres
fi

echo "3/4 starting the application (migrations run on start)"
"${COMPOSE[@]}" up -d --build --wait

echo "4/4 done. Check:  make smoke  and the manager's audit verification."
echo "Restored from: $DIR${TARGET:+ up to $TARGET}"
jq . "$DIR/manifest.json" 2>/dev/null || cat "$DIR/manifest.json"
