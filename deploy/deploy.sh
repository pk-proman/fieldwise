#!/usr/bin/env bash
# Deploy Fieldwise to a VPS that already runs nginx (e.g. the shared Hostinger server). Run from your Mac:
#
#   ./deploy/deploy.sh root@187.127.182.29 --domain fieldwise.promaninfi.com [--port 8021] [--email you@example.com] [--dir /opt/fieldwise] [--dry-run]
#
# Before the first run: create a DNS A record for the domain pointing to the server (Cloudflare: DNS only / grey cloud).
# What it does: copies the code (never your local database), creates /opt/fieldwise/.env ONCE with random secrets,
# builds and starts the containers (app + PostgreSQL + nightly backup), adds ONE nginx site for the domain, gets a
# Let's Encrypt certificate. Other sites on the server are never touched. Re-running updates the code only; the
# database and the .env on the server are kept.
set -euo pipefail
TARGET=""; DOMAIN=""; PORT="8021"; EMAIL=""; DIR="/opt/fieldwise"; DRY=0
while [ $# -gt 0 ]; do case "$1" in
  --domain) DOMAIN="$2"; shift 2;; --port) PORT="$2"; shift 2;; --email) EMAIL="$2"; shift 2;;
  --dir) DIR="$2"; shift 2;; --dry-run) DRY=1; shift;; -*) echo "unknown option $1"; exit 1;; *) TARGET="$1"; shift;; esac; done
[ -n "$TARGET" ] && [ -n "$DOMAIN" ] || { echo "usage: $0 user@host --domain fieldwise.example.com [--port 8021] [--email you@x.com] [--dir /opt/fieldwise] [--dry-run]"; exit 1; }
HERE="$(cd "$(dirname "$0")/.." && pwd)"
SSHO=(-o StrictHostKeyChecking=accept-new)
run()  { echo "+ $*"; [ "$DRY" = 1 ] || "$@"; }
rssh() { echo "+ ssh $TARGET $*"; [ "$DRY" = 1 ] || ssh "${SSHO[@]}" "$TARGET" "$@"; }
rbash() { local body; body="$(cat)"; echo "+ ssh $TARGET bash -s -- $*"; [ "$DRY" = 1 ] || ssh "${SSHO[@]}" "$TARGET" bash -s -- "$@" <<<"$body"; }

echo "Deploying Fieldwise to $TARGET:$DIR  (domain $DOMAIN, local port $PORT)"
read -r -p "Continue? [y/N] " a; [ "$a" = y ] || exit 1

rssh "command -v docker >/dev/null || (curl -fsSL https://get.docker.com | sh)"
rssh "mkdir -p $DIR"
run rsync -az --delete \
  --exclude .git --exclude .env --exclude '*.db' --exclude '*.db-shm' --exclude '*.db-wal' --exclude backend/uploads --exclude backend/tests \
  --exclude __pycache__ --exclude .venv --exclude node_modules --exclude mobile --exclude .DS_Store "$HERE/" "$TARGET:$DIR/"

rbash "$DIR" "$PORT" "$DOMAIN" <<'REMOTE'
set -e; DIR="$1"; PORT="$2"; DOMAIN="$3"; cd "$DIR"
if [ ! -f .env ]; then
  { echo "ENV=production"; echo "SECRET_KEY=$(openssl rand -hex 32)"; echo "POSTGRES_PASSWORD=$(openssl rand -hex 24)"; } > .env; chmod 600 .env; echo "created .env with new random secrets"
fi
setenv() { if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi; }
if ! grep -q '^HOST_PORT=' .env; then   # first deploy: the port must be free, otherwise stop before anything is started
  if ss -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]$PORT$" || docker ps --format '{{.Ports}}' 2>/dev/null | grep -q ":$PORT->"; then
    echo "ERROR: port $PORT is already in use on this server. Re-run with --port <free port>. Ports in use:"; ss -ltn | awk 'NR>1{print $4}' | sed 's/.*://' | sort -n | uniq | tr '\n' ' '; echo; exit 1
  fi
  setenv HOST_PORT "$PORT"; echo "port $PORT is free - reserved for Fieldwise"
fi
setenv PUBLIC_URL "https://$DOMAIN"
grep -q '^CORS_ORIGINS=' .env || setenv CORS_ORIGINS "capacitor://localhost,https://localhost,http://localhost"
echo "HOST_PORT=$(grep '^HOST_PORT=' .env | cut -d= -f2)"
REMOTE

rssh "cd $DIR && docker compose up -d --build"

rbash "$DIR" "$DOMAIN" "${EMAIL:-}" <<'REMOTE'
set -e; DIR="$1"; DOMAIN="$2"; EMAIL="$3"; P=$(grep '^HOST_PORT=' "$DIR/.env" | cut -d= -f2); f="/etc/nginx/sites-available/$DOMAIN"
if [ -f "$f" ]; then echo "nginx site $f already exists - leaving it unchanged"; else
cat > "$f" <<NGX
server {
    listen 80;
    server_name $DOMAIN;
    client_max_body_size 12m;
    gzip on; gzip_types application/json text/css application/javascript image/svg+xml;
    location /api/ws/ {
        proxy_pass http://127.0.0.1:$P;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 3600s;
    }
    location / {
        proxy_pass http://127.0.0.1:$P;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
    }
}
NGX
ln -sf "$f" "/etc/nginx/sites-enabled/$DOMAIN"; fi
nginx -t && systemctl reload nginx
command -v certbot >/dev/null || { apt-get update -y && apt-get install -y certbot python3-certbot-nginx; }
if [ -n "$EMAIL" ]; then CB="--email $EMAIL"; else CB="--register-unsafely-without-email"; fi
certbot --nginx -d "$DOMAIN" --redirect --non-interactive --agree-tos $CB
nginx -t && systemctl reload nginx && echo "nginx + certificate ready for $DOMAIN"
REMOTE

rssh "cd $DIR && docker compose ps && sleep 8 && curl -fsS http://127.0.0.1:\$(grep '^HOST_PORT=' .env | cut -d= -f2)/api/ready && echo"
echo "Done. Open https://$DOMAIN  and create the first workspace (the first sign-up becomes its owner)."
echo "Next: Settings -> SMS/SMTP, then follow deploy/PILOT_CHECKLIST.md."
