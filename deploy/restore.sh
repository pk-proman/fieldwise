#!/bin/sh
# Restore a dump into an EMPTY database:  sh deploy/restore.sh /backups/fieldwise-20261107-020000.dump
# Try it on a scratch database regularly: a backup you have never restored is not a backup.
set -eu
[ $# -eq 1 ] || { echo "usage: restore.sh <dump file>" >&2; exit 2; }
pg_restore --no-owner --clean --if-exists -d "${PGDATABASE:-fieldwise}" "$1"
echo "restored $1"
