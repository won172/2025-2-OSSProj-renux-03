#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  DATABASE_URL="$PRODUCTION_DATABASE_URL" scripts/backup-postgres.sh

Optional env:
  BACKUP_DIR=backups/postgres
  BACKUP_RETENTION_DAYS=14
  BACKUP_PREFIX=dongttok

The script writes a pg_dump custom-format backup plus a sha256 file.
Backups may contain personal data and are ignored by git.
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if ! command -v pg_dump >/dev/null 2>&1; then
  echo "pg_dump is required. Install PostgreSQL client tools first." >&2
  exit 1
fi

if [[ -z "${DATABASE_URL:-}" && -z "${PGDATABASE:-}" ]]; then
  echo "Set DATABASE_URL or PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD." >&2
  exit 1
fi

backup_dir="${BACKUP_DIR:-backups/postgres}"
retention_days="${BACKUP_RETENTION_DAYS:-14}"
prefix="${BACKUP_PREFIX:-dongttok}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
output="${backup_dir}/${prefix}-${timestamp}.dump"

mkdir -p "$backup_dir"
chmod 700 "$backup_dir"

if [[ -n "${DATABASE_URL:-}" ]]; then
  pg_dump --format=custom --no-owner --no-acl --file="$output" "$DATABASE_URL"
else
  pg_dump --format=custom --no-owner --no-acl --file="$output"
fi

if command -v shasum >/dev/null 2>&1; then
  shasum -a 256 "$output" >"${output}.sha256"
elif command -v sha256sum >/dev/null 2>&1; then
  sha256sum "$output" >"${output}.sha256"
else
  echo "Warning: no sha256 tool found; checksum not written." >&2
fi

find "$backup_dir" -name "${prefix}-*.dump" -type f -mtime +"$retention_days" -delete
find "$backup_dir" -name "${prefix}-*.dump.sha256" -type f -mtime +"$retention_days" -delete

echo "Backup written: $output"
echo "Restore test command:"
echo "  pg_restore --clean --if-exists --no-owner --dbname '\$DATABASE_URL' '$output'"
