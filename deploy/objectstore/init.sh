#!/usr/bin/env bash
# Idempotent object store setup: buckets (originals with Object Lock and default
# retention), and an application user that can read and write but never delete.
# Needs: ENDPOINT, MINIO_ROOT_USER, MINIO_ROOT_PASSWORD, APP_USER, APP_PASSWORD
# Optional: LOCK_DAYS (default 2555, about 7 years)
set -euo pipefail
: "${ENDPOINT:?}" "${MINIO_ROOT_USER:?}" "${MINIO_ROOT_PASSWORD:?}" "${APP_USER:?}" "${APP_PASSWORD:?}"
LOCK_DAYS=${LOCK_DAYS:-2555}
export MC_CONFIG_DIR=/tmp/mc
mc alias set s "$ENDPOINT" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null

mc mb --ignore-existing --with-lock s/originals
mc retention set --default GOVERNANCE "${LOCK_DAYS}d" s/originals
mc mb --ignore-existing --with-lock s/audit-archive
mc retention set --default GOVERNANCE "${LOCK_DAYS}d" s/audit-archive
mc mb --ignore-existing s/derived
mc mb --ignore-existing s/exports

mc admin policy create s ansurance-app /opt/objectstore/app-policy.json 2>/dev/null \
  || mc admin policy create s ansurance-app /opt/objectstore/app-policy.json --force 2>/dev/null || true
mc admin user add s "$APP_USER" "$APP_PASSWORD" 2>/dev/null || true
mc admin policy attach s ansurance-app --user "$APP_USER" 2>/dev/null || true
echo "objectstore initialised"
