#!/usr/bin/env bash
# Loads the ERPNext product catalogue and sales team (backend/seed/*.json, made on your Mac) into a workspace on the live server.
#   ./deploy/load-seed.sh root@187.127.182.29                 -> lists the workspaces on the server
#   ./deploy/load-seed.sh root@187.127.182.29 --tenant proman  -> loads products + sales team into that workspace
# Safe to re-run (rows are matched on their ERPNext id). The files hold employee emails: they are copied over SSH, used, then deleted on the server.
# Alternative without this script: Settings -> ERPNext -> enter the API key/secret, item group "Products", then "Sync now" on Products and Sales team.
set -euo pipefail
TARGET="${1:-}"; shift || true; TENANT=""; DIR="/opt/fieldwise"
while [ $# -gt 0 ]; do case "$1" in --tenant) TENANT="$2"; shift 2;; --dir) DIR="$2"; shift 2;; *) echo "unknown option $1"; exit 1;; esac; done
[ -n "$TARGET" ] || { echo "usage: $0 user@host [--tenant slug] [--dir /opt/fieldwise]"; exit 1; }
HERE="$(cd "$(dirname "$0")/.." && pwd)"; SEED="$HERE/backend/seed"
for f in pispl_products.json pispl_sales_team.json; do [ -f "$SEED/$f" ] || { echo "missing $SEED/$f"; exit 1; }; done
SSHO=(-o StrictHostKeyChecking=accept-new)
if [ -z "$TENANT" ]; then
  echo "Workspaces on the server (use one of these with --tenant):"
  ssh "${SSHO[@]}" "$TARGET" "cd $DIR && docker compose exec -T db psql -U fieldwise -d fieldwise -tAc 'select slug from tenants order by id'"
  exit 0
fi
ssh "${SSHO[@]}" "$TARGET" "mkdir -p /tmp/fw-seed && chmod 700 /tmp/fw-seed"
scp "${SSHO[@]}" "$SEED/pispl_products.json" "$SEED/pispl_sales_team.json" "$TARGET:/tmp/fw-seed/"
ssh "${SSHO[@]}" "$TARGET" "set -e; cd $DIR; APP=\$(docker compose ps -q app)
  trap 'docker compose exec -T -u root app rm -rf /tmp/fw-seed >/dev/null 2>&1 || true; rm -rf /tmp/fw-seed' EXIT
  docker cp /tmp/fw-seed \$APP:/tmp/fw-seed
  docker compose exec -T -u root app env PYTHONPATH=/srv/backend python scripts/load_seed.py --tenant '$TENANT' --products /tmp/fw-seed/pispl_products.json --sales-team /tmp/fw-seed/pispl_sales_team.json"
echo "Done. In Fieldwise: Settings -> Sales team, and Leads -> New lead -> Product."
