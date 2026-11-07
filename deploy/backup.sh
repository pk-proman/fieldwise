#!/bin/sh
# Daily PostgreSQL dump (custom format) with rotation. Used by the compose "backup" service; also fine from cron:
#   PGHOST=... PGUSER=... PGPASSWORD=... PGDATABASE=... sh deploy/backup.sh
set -eu
DIR="${BACKUP_DIR:-/backups}"; KEEP="${KEEP_DAYS:-14}"
mkdir -p "$DIR"
F="$DIR/fieldwise-$(date +%Y%m%d-%H%M%S).dump"
pg_dump -Fc -f "$F"
[ -s "$F" ] || { echo "backup is empty" >&2; exit 1; }
find "$DIR" -name 'fieldwise-*.dump' -mtime +"$KEEP" -delete
echo "backup ok: $F ($(du -h "$F" | cut -f1))"
# Uploaded files (receipts, photos) live in the 'uploads' volume: back that up too (e.g. `tar czf uploads.tgz /data/uploads`).
