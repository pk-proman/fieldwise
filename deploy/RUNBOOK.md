# Fieldwise: launch & operations runbook

## 1. First deployment (single server, Docker)
1. A Linux server with Docker, a DNS name (e.g. `fieldwise.yourcompany.com`) and a TLS certificate (Let's Encrypt). **HTTPS is mandatory**: browsers only allow GPS and camera on HTTPS, and the mobile app needs it.
2. `cp .env.example .env` and fill in `SECRET_KEY`, `POSTGRES_PASSWORD`, `PUBLIC_URL`. The app **refuses to start** with `ENV=production` and a missing/short `SECRET_KEY`.
3. `docker compose up -d --build`. Migrations run automatically (`alembic upgrade head`). The app listens on `127.0.0.1:8000`.
4. Install `deploy/nginx.conf` (edit the domain and certificate paths). It adds TLS, request-rate limits and the WebSocket upgrade.
5. Create the first workspace at `https://your-domain/` (Create a workspace). Then in Settings: company profile, branches, SMTP, SMS gateway (needed for visit OTPs: **in production the OTP is never shown on screen**), privacy notice.
6. Check: `https://your-domain/api/ready` returns `{"status":"ready"}`. Point an uptime monitor at it.

## 2. Backups (do this before real data goes in)
- The compose `backup` service writes a `pg_dump` daily to the `backups` volume and keeps 14 days. **Copy those off the server** (object storage / another machine).
- Uploaded files (receipts, photos, PO scans) are in the `uploads` volume: back it up too (`docker run --rm -v fieldwise_uploads:/d -v $PWD:/o alpine tar czf /o/uploads.tgz -C /d .`).
- **Test a restore** into a scratch database (`deploy/restore.sh`) before launch and then every quarter.

## 3. Upgrades
`git pull && docker compose up -d --build`. Take a backup first. Schema changes ship as Alembic revisions; after changing a model run `alembic revision --autogenerate -m "what changed"` and review it.

## 4. Scaling limits (read before adding servers or workers)
- Run **one app process**. Live-location streaming (WebSocket hub) and the in-app rate limiter live in memory. A second instance would not see the first one's live pings.
- The background scheduler is protected by a database lock, so extra instances won't double-run jobs, but live-map pushes and per-IP limits would still be per instance.
- Rough capacity, measured on one laptop-class machine with PostgreSQL (44 users, 8,000 leads, 150,000 GPS points): list/search pages 50–500 ms, dashboards under 1 s, the 31-day all-staff productivity report about 2.6 s. Beyond a few hundred field staff: add Redis for the live hub, move rate limits to the proxy, and consider partitioning `location_pings`.
- GPS breadcrumbs are deleted after `PING_RETENTION_DAYS` (default 180, per-workspace in Settings).

## 5. Security notes
Verified by automated tests (run `pytest`; they also run on PostgreSQL): tenant isolation sweep over every endpoint, cross-workspace id rejection, upload type whitelist, SSRF guard, rate limits, MFA, password policy and single-use reset links, consent gate, export/erase/delete, audit trail, production refusing weak secrets and never showing OTPs. `bandit` and `pip-audit` report no medium/high findings and no known-vulnerable dependencies.
**Not covered, and recommended before a public launch:** an independent penetration test; a Web Application Firewall / DDoS protection in front; CAPTCHA or email verification on public sign-up (currently only 5 sign-ups/hour/IP); central log shipping and alerting; secret rotation procedure; dependency updates every month.
- `BIOMETRIC_PROVIDER=mock` accepts any selfie: selfies are stored as photo proof (labelled "selfie", not "face"). Configure `rekognition` for real matching.
- WhatsApp / SMS / telephony / lead-portal integrations are only as good as the gateway and payload mapping: test each with real data.
- ERPNext on a private network needs `ALLOW_PRIVATE_URLS=1`. Loopback and cloud-metadata addresses stay blocked.

## 6. People & privacy
- Staff must accept the location-tracking notice before their first check-in (Settings → Security & privacy). Changing the notice version asks again. Acceptance is audited.
- Data-subject requests: *export* (owner: Settings → Security → Download everything), *erase a person* (Users → edit → Erase personal data: removes identity and location history, keeps business records with an anonymous author), *delete workspace* (owner, typed confirmation + password).
- Tracking happens only between check-in and check-out. Tell staff what is recorded and who can see it; get local legal advice on consent, notice and retention (India DPDP Act, GCC rules).

## 7. When something breaks
- Every API response has `X-Request-ID`; the same id is in the server log line. Ask users for it.
- `docker compose logs app` (access log: method, path, status, ms; slow/5xx lines are warnings).
- Database down → `/api/ready` returns 503. Restore from the latest dump (section 2).
- Suspected key leak: rotate `SECRET_KEY` (signs everyone out; encrypted secrets saved in Settings must then be re-entered), rotate lead-intake keys (Settings → Lead sources), and review Settings → Security → Audit trail.
