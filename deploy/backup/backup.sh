#!/usr/bin/env bash
# Backup, restore and scheduler for the on-prem stack.
#
#   backup.sh run                 the schedule (the container's default):
#                                   every 15 min  copy new originals (encrypted, incremental)
#                                   daily 01:30   full backup, then prune; monthly restore test
#   backup.sh backup              one full backup now: database dump + base backup + new files
#   backup.sh files               copy new originals only (what the 15-minute job does)
#   backup.sh verify              restore self-test of the latest backup (a temporary database
#                                 and file checksums); never touches the live data
#   backup.sh restore [DIR]       disaster recovery into an EMPTY stack from the database dump
#   backup.sh restore-files [DIR] put the originals back into an empty object store
#   backup.sh pitr DIR "TIME"     point-in-time recovery: unpack the base backup into an empty
#                                 data directory (/pgdata) and arrange WAL replay up to TIME
#   backup.sh prune               remove old backups and the WAL no kept backup needs
#
# Layout of $BACKUP_DIR (copy it, and $WAL_DIR, somewhere off this host):
#   files/<bucket>/<key>.enc      every original ever seen, AES-256 encrypted, content-addressed
#   <timestamp>/db.dump.enc       pg_dump (custom format), encrypted
#   <timestamp>/base.tar.gz.enc   base backup for point-in-time recovery, encrypted
#   <timestamp>/manifest.json     what the backup holds and its checksums
# $WAL_DIR/<segment>.enc          archived write-ahead log, encrypted by PostgreSQL itself
#
# Recovery point: the database to within minutes (WAL archived at least every 5 minutes),
# the originals to within 15 minutes.
#
# Environment: PGHOST PGUSER PGPASSWORD PGDATABASE, S3_ENDPOINT S3_ACCESS_KEY S3_SECRET_KEY,
# BACKUP_PASSPHRASE (required), BACKUP_DIR, WAL_DIR, BACKUP_KEEP (default 14),
# BACKUP_HOUR (default 01), FILES_INTERVAL (default 900 seconds)
set -euo pipefail

: "${BACKUP_PASSPHRASE:?set BACKUP_PASSPHRASE in .env (keep a copy somewhere safe)}"
: "${PGHOST:?}" "${PGUSER:?}" "${PGPASSWORD:?}" "${PGDATABASE:=ansurance}"
: "${S3_ENDPOINT:?}" "${S3_ACCESS_KEY:?}" "${S3_SECRET_KEY:?}"
BACKUP_DIR=${BACKUP_DIR:-/backups}
WAL_DIR=${WAL_DIR:-/wal}
BACKUP_KEEP=${BACKUP_KEEP:-14}
BUCKETS=${BACKUP_BUCKETS:-"originals audit-archive"}
export MC_CONFIG_DIR=/tmp/mc-backup

log() { printf '%s backup: %s\n' "$(date -u +%FT%TZ)" "$*"; }
enc() { openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:BACKUP_PASSPHRASE "$@"; }
dec() { openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_PASSPHRASE "$@"; }
psqlq() { psql -qAtX -v ON_ERROR_STOP=1 "$@"; }
mcs() { mc alias set src "$S3_ENDPOINT" "$S3_ACCESS_KEY" "$S3_SECRET_KEY" >/dev/null; }

set_info() { # key value
  psqlq -d "$PGDATABASE" -c "INSERT INTO platform_info (key, value) VALUES ('$1', '$2')
    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()" >/dev/null
}

latest_dir() { ls -1d "$BACKUP_DIR"/20* 2>/dev/null | sort | tail -1; }

# Copy every original that is not backed up yet. Content-addressed objects never change, so
# this is incremental by construction. Sets FILES_NEW, FILES_TOTAL, FILES_BYTES.
sync_files() {
  mkdir -p "$BACKUP_DIR/files"
  mcs
  FILES_NEW=0 FILES_TOTAL=0 FILES_BYTES=0
  local b key size target
  for b in $BUCKETS; do
    mkdir -p "$BACKUP_DIR/files/$b"
    while IFS=$'\t' read -r key size; do
      [ -n "$key" ] || continue
      FILES_TOTAL=$((FILES_TOTAL + 1))
      FILES_BYTES=$((FILES_BYTES + size))
      target="$BACKUP_DIR/files/$b/$key.enc"
      if [ ! -f "$target" ]; then
        mkdir -p "$(dirname "$target")"
        mc cat "src/$b/$key" | enc -out "$target.tmp"
        mv "$target.tmp" "$target"
        FILES_NEW=$((FILES_NEW + 1))
      fi
    done < <(mc ls --recursive --json "src/$b" 2>/dev/null | jq -r 'select(.type=="file") | [.key, .size] | @tsv')
  done
  set_info backup.last_files_sync "$(date -u +%FT%TZ)"
}

do_files() {
  sync_files
  log "files: $FILES_TOTAL in the store, $FILES_NEW new copied"
}

do_backup() {
  mkdir -p "$BACKUP_DIR/files"
  local ts dir
  ts=$(date -u +%Y%m%dT%H%M%SZ)
  dir="$BACKUP_DIR/$ts.partial"
  mkdir -p "$dir"

  log "dumping the database"
  # One snapshot for both the dump and the row counts in the manifest, so they always agree
  # even while the system is in use.
  local snap counts alembic
  coproc PSQL { psql -qAtX -v ON_ERROR_STOP=1 -d "$PGDATABASE"; }
  echo "BEGIN ISOLATION LEVEL REPEATABLE READ; SELECT pg_export_snapshot();" >&"${PSQL[1]}"
  read -r -t 30 -u "${PSQL[0]}" snap || { log "could not open a snapshot"; return 1; }
  pg_dump -Fc --snapshot="$snap" -d "$PGDATABASE" | enc -out "$dir/db.dump.enc"
  echo "SELECT json_build_object(
      'client', (SELECT count(*) FROM client), 'document', (SELECT count(*) FROM document),
      'blob', (SELECT count(*) FROM blob), 'audit_entry', (SELECT count(*) FROM audit_entry),
      'user_account', (SELECT count(*) FROM user_account),
      'process_instance', (SELECT count(*) FROM process_instance))::text;" >&"${PSQL[1]}"
  read -r -t 30 -u "${PSQL[0]}" counts
  echo "SELECT version_num FROM alembic_version;" >&"${PSQL[1]}"
  read -r -t 30 -u "${PSQL[0]}" alembic
  echo "COMMIT;" >&"${PSQL[1]}"
  echo '\q' >&"${PSQL[1]}"
  wait "$PSQL_PID" 2>/dev/null || true
  local dump_sha
  dump_sha=$(sha256sum "$dir/db.dump.enc" | cut -d' ' -f1)

  log "taking a base backup (for point-in-time recovery)"
  local base_sha=""
  if pg_basebackup -D - -Ft -X none -c fast 2>/tmp/basebackup.err | gzip | enc -out "$dir/base.tar.gz.enc" \
     && [ "${PIPESTATUS[0]}" = 0 ]; then
    base_sha=$(sha256sum "$dir/base.tar.gz.enc" | cut -d' ' -f1)
  else
    log "WARNING: base backup failed ($(head -c 200 /tmp/basebackup.err)); point-in-time recovery is not available from this backup"
    rm -f "$dir/base.tar.gz.enc"
  fi

  sync_files
  log "files: $FILES_TOTAL in the store, $FILES_NEW new copied"

  jq -n --arg ts "$ts" --arg sha "$dump_sha" --arg base "$base_sha" --arg alembic "$alembic" \
        --argjson counts "$counts" --argjson files "$FILES_TOTAL" --argjson bytes "$FILES_BYTES" \
        '{format: 2, created_at: $ts, dump: "db.dump.enc", dump_sha256: $sha,
          base: (if $base == "" then null else "base.tar.gz.enc" end),
          base_sha256: (if $base == "" then null else $base end),
          alembic_version: $alembic, row_counts: $counts, files: $files, file_bytes: $bytes}' \
        > "$dir/manifest.json"
  mv "$dir" "$BACKUP_DIR/$ts"
  set_info backup.last_success "$(date -u +%FT%TZ)"
  log "backup $ts complete"
  echo "$BACKUP_DIR/$ts"
}

do_verify() {
  local dir
  dir=$(latest_dir)
  [ -n "$dir" ] || { log "no backup to verify"; set_info backup.last_restore_test "$(date -u +%FT%TZ)|FAILED: no backup"; return 1; }
  log "verifying $dir"
  local tmpdb="restore_check_$$" ok=1 report=""
  local want_sha got_sha
  want_sha=$(jq -r .dump_sha256 "$dir/manifest.json")
  got_sha=$(sha256sum "$dir/db.dump.enc" | cut -d' ' -f1)
  [ "$want_sha" = "$got_sha" ] || { report="dump checksum mismatch"; ok=0; }

  if [ $ok = 1 ]; then
    psqlq -d postgres -c "CREATE DATABASE $tmpdb" >/dev/null
    if dec -in "$dir/db.dump.enc" | pg_restore -d "$tmpdb" --no-owner --exit-on-error 2>/tmp/restore.err; then
      local t want have
      for t in client document blob audit_entry user_account process_instance; do
        want=$(jq -r ".row_counts.$t" "$dir/manifest.json")
        have=$(psqlq -d "$tmpdb" -c "SELECT count(*) FROM $t")
        # the manifest and the dump come from one snapshot: they must match exactly
        if [ "$want" != "$have" ]; then report="$report $t:$have!=$want"; ok=0; fi
      done
      local ver
      ver=$(psqlq -d "$tmpdb" -c "SELECT version_num FROM alembic_version")
      [ "$ver" = "$(jq -r .alembic_version "$dir/manifest.json")" ] || { report="$report schema version"; ok=0; }
    else
      report="pg_restore failed: $(head -c 200 /tmp/restore.err)"; ok=0
    fi
    psqlq -d postgres -c "DROP DATABASE IF EXISTS $tmpdb" >/dev/null
  fi

  # the base backup (if any) must be readable: a gzip tar that contains a backup_label
  local base
  base=$(jq -r '.base // empty' "$dir/manifest.json")
  if [ -n "$base" ]; then
    if [ "$(sha256sum "$dir/$base" | cut -d' ' -f1)" != "$(jq -r .base_sha256 "$dir/manifest.json")" ] \
       || [ "$(dec -in "$dir/$base" | tar -tzf - 2>/dev/null | grep -c '^backup_label$')" != "1" ]; then
      report="$report base backup unreadable"; ok=0
    fi
  fi

  # files: every content-addressed copy must decrypt to bytes that hash to the name it has
  local checked=0 bad=0 f key
  while IFS= read -r f; do
    key=${f#"$BACKUP_DIR"/files/*/}
    case "$key" in sha256/*/*.enc) ;; *) continue ;; esac
    want=$(basename "$key" .enc)
    have=$(dec -in "$f" | sha256sum | cut -d' ' -f1)
    checked=$((checked + 1))
    [ "$want" = "$have" ] || bad=$((bad + 1))
  done < <(find "$BACKUP_DIR/files" -name '*.enc' -type f | sort | head -"${VERIFY_MAX_FILES:-2000}")
  [ "$bad" = 0 ] || { report="$report $bad of $checked files do not match their checksum"; ok=0; }

  if [ $ok = 1 ]; then
    log "restore test OK ($checked files checked)"
    set_info backup.last_restore_test "$(date -u +%FT%TZ)|ok"
  else
    log "restore test FAILED:$report"
    set_info backup.last_restore_test "$(date -u +%FT%TZ)|FAILED:$(echo "$report" | tr -d "'" | head -c 150)"
    return 1
  fi
}

restore_files() {
  local dir=${1:-$(latest_dir)}
  local files_dir
  files_dir="$(dirname "$dir")/files"
  mcs
  local n=0 f rel b key have
  while IFS= read -r f; do
    rel=${f#"$files_dir"/}
    b=${rel%%/*}
    key=${rel#*/}
    key=${key%.enc}
    case "$key" in
      sha256/*/*)
        have=$(dec -in "$f" | sha256sum | cut -d' ' -f1)
        [ "$have" = "$(basename "$key")" ] || { log "corrupt backup file $rel"; return 1; } ;;
    esac
    dec -in "$f" | mc pipe "src/$b/$key" >/dev/null
    n=$((n + 1))
  done < <(find "$files_dir" -name '*.enc' -type f | sort)
  log "restored $n files"
}

do_restore() {
  local dir=${1:-$(latest_dir)}
  [ -f "$dir/manifest.json" ] || { log "no manifest in $dir"; return 1; }
  local want got
  want=$(jq -r .dump_sha256 "$dir/manifest.json")
  got=$(sha256sum "$dir/db.dump.enc" | cut -d' ' -f1)
  [ "$want" = "$got" ] || { log "the dump does not match its checksum: refusing to restore"; return 1; }
  local existing
  existing=$(psqlq -d "$PGDATABASE" -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='client'")
  if [ "$existing" != "0" ] && [ "${RESTORE_FORCE:-0}" != "1" ]; then
    log "the target database already has data; set RESTORE_FORCE=1 to overwrite it"
    return 1
  fi
  log "restoring the database from $dir"
  dec -in "$dir/db.dump.enc" | pg_restore -d "$PGDATABASE" --clean --if-exists --no-owner --exit-on-error
  restore_files "$dir"
  log "restore complete: start the stack and run the verification (scripts/restore.sh does)"
}

# Point-in-time recovery. Runs with the data directory volume mounted at /pgdata (must be
# empty) and, for the WAL, the archive at $WAL_DIR (copied from $WAL_SRC when given).
do_pitr() {
  local dir=$1 target=$2
  [ -f "$dir/manifest.json" ] && [ -f "$dir/base.tar.gz.enc" ] || { log "$dir has no base backup"; return 1; }
  [ -z "$(ls -A /pgdata 2>/dev/null)" ] || { log "/pgdata is not empty: refusing to overwrite it"; return 1; }
  [ "$(sha256sum "$dir/base.tar.gz.enc" | cut -d' ' -f1)" = "$(jq -r .base_sha256 "$dir/manifest.json")" ] \
    || { log "the base backup does not match its checksum"; return 1; }
  if [ -n "${WAL_SRC:-}" ]; then
    mkdir -p "$WAL_DIR"
    cp -a "$WAL_SRC"/. "$WAL_DIR"/
  fi
  # PostgreSQL (uid postgres) reads the archive during recovery
  chown -R postgres:postgres "$WAL_DIR" 2>/dev/null || true
  chmod -R u+rwX "$WAL_DIR" 2>/dev/null || true
  log "unpacking the base backup"
  dec -in "$dir/base.tar.gz.enc" | tar -xzf - -C /pgdata
  # PostgreSQL itself decrypts each archived segment when it needs it
  cat >> /pgdata/postgresql.auto.conf <<CONF
restore_command = 'openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_PASSPHRASE -in $WAL_DIR/%f.enc -out %p'
recovery_target_time = '$target'
recovery_target_action = 'promote'
recovery_target_inclusive = true
CONF
  touch /pgdata/recovery.signal
  chown -R postgres:postgres /pgdata
  chmod 700 /pgdata
  log "ready: starting PostgreSQL will replay the log up to $target and then accept connections"
}

do_prune() {
  local n d oldest
  n=$(ls -1d "$BACKUP_DIR"/20* 2>/dev/null | wc -l)
  if [ "$n" -gt "$BACKUP_KEEP" ]; then
    ls -1d "$BACKUP_DIR"/20* | sort | head -n $((n - BACKUP_KEEP)) | while read -r d; do
      log "pruning $d"
      rm -r -- "${d:?}"
    done
  fi
  # WAL older than the oldest kept backup (less a day of margin) can never be needed
  oldest=$(ls -1d "$BACKUP_DIR"/20* 2>/dev/null | sort | head -1 | xargs -r basename)
  if [ -n "$oldest" ] && [ -d "$WAL_DIR" ]; then
    local cutoff
    cutoff=$(date -u -d "${oldest:0:4}-${oldest:4:2}-${oldest:6:2} ${oldest:9:2}:${oldest:11:2}:${oldest:13:2} UTC - 1 day" +%s)
    find "$WAL_DIR" -name '*.enc' -type f ! -newermt "@$cutoff" -delete
  fi
}

seconds_until() { # next HH:30 UTC
  local now target
  now=$(date -u +%s)
  target=$(date -u -d "$(date -u +%F) ${BACKUP_HOUR:-01}:30:00" +%s)
  [ "$target" -gt "$now" ] || target=$((target + 86400))
  echo $((target - now))
}

case "${1:-run}" in
  backup) do_backup ;;
  files) do_files ;;
  verify) do_verify ;;
  restore) shift; do_restore "${1:-}" ;;
  restore-files) shift; restore_files "${1:-}" ;;
  pitr) shift; do_pitr "${1:?backup dir}" "${2:?target time}" ;;
  prune) do_prune ;;
  run)
    log "scheduler: files every ${FILES_INTERVAL:-900}s, full backup daily at ${BACKUP_HOUR:-01}:30 UTC, restore test on the 1st"
    [ -n "$(latest_dir)" ] || do_backup >/dev/null || log "first backup failed"
    next_full=$(( $(date -u +%s) + $(seconds_until) ))
    while true; do
      sleep "${FILES_INTERVAL:-900}"
      if [ "$(date -u +%s)" -ge "$next_full" ]; then
        do_backup >/dev/null || log "BACKUP FAILED"
        [ "$(date -u +%d)" = "01" ] && { do_verify || true; }
        do_prune
        next_full=$(( $(date -u +%s) + $(seconds_until) ))
      else
        do_files || log "FILE SYNC FAILED"
      fi
    done ;;
  *) echo "usage: $0 run|backup|files|verify|restore|restore-files|pitr|prune" >&2; exit 2 ;;
esac
