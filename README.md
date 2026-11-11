# Fieldwise — multi-tenant Field-Force + CRM + ERPNext SaaS

A multi-tenant field-force and CRM product. Each client company gets its own isolated workspace, created by the vendor's super-admin; the client's administrator completes a mandatory setup wizard before the team can use it. See docs/PRODUCT_SETUP_SPEC.md (what every client must configure) and deploy/PLATFORM_RUNBOOK.md (running it as a product).

**Run locally**
```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m app.seed                      # optional demo workspace "proman"
.venv/bin/uvicorn app.main:app --port 8021        # open http://localhost:8021
.venv/bin/python -m pytest tests -q               # 8 integration tests
```
Demo logins (a demo workspace named `proman`, password `Demo@12345`): admin@ / manager@ / rahul@ / sneha@ / imran@ `proman.demo`.

## What's built (spec → implementation)
| Spec | Where |
|---|---|
| Multi-tenant SaaS, plans/seat limits | `tenant_id` on every table, scoped in every query; self-serve signup; `seat_limit` |
| JWT auth, RBAC, **single-device lock** | `routers/auth.py`, `deps.py` — new login rotates `session_id`, old token → 401 |
| Attendance, geo-fence, branches, mock-GPS block, offline punches | `routers/attendance.py`, `geo.py` (haversine), `frontend/js/tracker.js` + offline queue in `api.js` |
| Live GPS, distance, battery, GPS-off/internet-off time, WebSocket live map | `routers/tracking.py`, `hub.py`, Live team page |
| Leaves (types, holidays, balances, approval) / Expenses (receipt watermark, bulk approve, GPS fuel calc) | `routers/leaves.py`, `expenses.py` |
| CRM: leads/customers/contacts, convert, timeline, sharing, tags, custom fields, saved views, CSV export, **90-day recycle bin** | `routers/crm.py`, `admin.py`, scheduler purge |
| Business-card OCR | Tesseract.js in browser + server-side field parser |
| Tasks: repeat, bulk CSV upload, geo-fenced completion, custom status flow | `routers/tasks.py` |
| Route curation: optimisation (nearest-neighbour + 2-opt), play/pause navigation, past routes | `geo.optimize_route`, Routes page |
| Visit **OTP verification**, MOM email | `routers/visits.py`, `notify.py` |
| Notifications (check-in/out, GPS off, task due, waiting time) | `scheduler.py` (60 s loop) |
| ERPNext integration (employees, CRM both ways, items, attendance→Employee Checkin, expense→Expense Claim, webhook) | `integrations/erpnext.py`, `routers/erp.py`, Settings → ERPNext |
| PWA (installable, offline shell) | `manifest.webmanifest`, `sw.js` |

## CRM pipeline (v1.1)
Lead → **mandatory Product (from product master)** → qualify (BANT 0–8, threshold configurable) → **quotation** (created in ERPNext, ERPNext number fetched and shown; discount above threshold needs manager approval) → negotiation → won (auto-converts to customer) / lost (reason required). Stage gates are enforced by the API. Also: auto first follow-up, follow-up management (today/overdue/upcoming, outcomes, recurring visit plans), customer meetings (geo check-in, MOM, signature, photo, action items become linked tasks, next follow-up), related-records panel, rule-based lead score + next-best-action, forecast (weighted by stage), funnel/win-rate/lost reasons, daily call report (+ 7:30 pm digest), monthly targets vs achievement.

## CRM completion (v1.2)
**Leads:** mandatory product master, mobile-or-email required with duplicate detection (repeat-enquiry counter instead of duplicates), Hot/Warm/Cold + star, several products, observers, call/meeting attempt counters, configurable stages (rules: qualification / quotation), regret reasons + auto-regret, round-robin and common-lead-pool assignment (rules by source/region/product), 6 working tabs, filters, CSV import with dry-run.
**Lead intake:** webhooks for IndiaMART, JustDial, TradeIndia, Sulekha, Exporters India, Pharmahopers, Magicbricks, Shopify, Facebook Lead Ads, website forms and generic JSON (`/api/intake/<workspace>/<source>?key=…`), idempotent by portal lead id, mapping tester and log in Settings → Lead sources.
**Quotations:** PDF generation (company profile, GST, bank, T&C, additional charges, amount in words), custom PDF upload, send by email (workspace SMTP, PDF attached) or WhatsApp (gateway or wa.me), signed 30-day public link, number series, discount approval, ERPNext quotation number.
**Orders & invoices:** customer-PO scan → draft ERPNext Sales Order, proforma and tax invoices (PDF, numbering, payment terms, TDS), draft ERPNext Sales Invoice, new/repeat sale, PO-register columns.
**Telephony & dialer:** call logs (provider webhook, missed call → lead), click-to-call gateway, auto-dialer campaigns (auto/manual assignment, dispositions, call-backs, lead creation).
**Also:** renewals/re-order reminders, trade-fair events with ROI, notes with replies + unread, global search, product master (HSN, GST, photos, brochure, parts, MOQ, AMC, bulk import), parent companies, departments/designations, incentive plans + payout report, MFA (TOTP), session expiry, failed-login report, vendors / vendor POs / dealers, helpdesk, bring-your-own-key AI lead analysis, per-workspace SMTP/WhatsApp/SMS/call gateways (secrets encrypted), message templates, dashboard with month-on-month and user/manager views.

## Field-force optimisation (v1.3)
- **Work hours & productivity:** each day's GPS trail is turned into stops (8+ min), time on site / travelling / idle / no-signal, overtime against the person's shift, a productive-% score, and a team productivity table (31-day range, CSV export).
- **Geofence events:** server-side arrival and departure at customer sites (with time on site and a nudge to start the visit), plus **territory polygons** with an alert to the manager when someone leaves during duty. Arrivals can notify managers too.
- **Anomalies:** signal gaps, impossible GPS jumps, fake-location points, GPS-off patterns, no check-out, checked-in-but-no-tracking.
- **Shifts & roster:** shifts with grace/break/weekly-off, per-person assignment, per-day roster overrides, swap requests (colleague accepts → manager approves). Lateness and overtime follow the shift.
- **Visit forms & proof of visit:** form builder (text, number, choice, tick, rating, photo, signature, date); a form can be *required* before a visit can end; optional mandatory geo-tagged photo. Responses export to CSV (use it for stock/shelf audits and surveys).
- **Daily allowance:** metro / other / outstation tiers from attendance and distance from base; **multi-level expense approval** by amount (no one approves two levels).
- **Dispatch:** nearest available executive for a task (distance, ETA, workload, on-duty) and a task pipeline: On my way → Check in → Complete / Reschedule.
- **Announcements** with read receipts; **scheduled e-reports** by email (attendance, expenses, DCR, productivity, beats); receipt OCR on expense claims; battery-aware GPS sampling.
- **Native app scaffold** in `/mobile` (Capacitor): background GPS, real mock-location flag, fingerprint/face unlock. **Not built or tested** (see `mobile/README.md`).

## Launch hardening
Security review, fixes and a deployment runbook: see **`deploy/RUNBOOK.md`**. Highlights: strict CSP and security headers, rate limits, SSRF guard, cross-workspace id validation, upload whitelist, encrypted integration secrets, password policy + reset by email, tracking-consent gate, data export / person erasure / workspace deletion, retention, audit trail, production mode (refuses weak secret, hides API docs, never reveals OTPs), Alembic migrations, Postgres-tested, health/readiness, access log with request ids, Docker/compose/nginx/backup files.

## Honest gaps / decisions
- **Portal payload formats** (IndiaMART, JustDial, TradeIndia…) are mapped from their public field names and a flexible alias table; they have **not been run against live feeds**. Use Settings → Lead sources → "Test a payload" with a real sample and check the log.
- **WhatsApp / SMS / telephony** need your provider account; the app calls any HTTPS gateway you configure (templated body). No provider is pre-integrated.
- **AI** sends lead details to the provider you choose (opt-in); without a key it gives rule-based advice.
- Not built: campaigns/journeys (marketing automation), omni-channel inbox, customer self-service portal.
- **Map tiles**: public OpenStreetMap tiles require a Referer and are for light use only. Fixed the Referrer-Policy that caused the 403; for production traffic use a paid/own tile provider.
- Not built yet (from the Salesforce/Lystloc review): campaigns, journey orchestration, omni-channel inbox, customer self-service portal, sales orders & invoicing, payment collection, shift rosters, beat/territory entry-exit alerts, dynamic checklist forms, stock audit, scheduled PDF e-reports, in-app chat. Lead score is rule-based, not a trained AI model.
- **Fingerprint**: a browser/PWA can't give the server a verifiable fingerprint. The API accepts `device_biometric=true` from a Capacitor build (native biometric prompt); the web UI offers face scan. 
- **Face recognition**: `BIOMETRIC_PROVIDER=mock` accepts any selfie (dev only). Set `rekognition` for real matching.
- **Mock-location**: browsers can't read the Android mock flag; the web tracker flags impossible GPS jumps. A Capacitor wrapper should pass the native `isMock` flag in pings.
- **Background tracking** while the phone is locked needs the native wrapper (background-geolocation plugin); a pure PWA tracks only while open.
- **Database**: SQLite by default; set `DATABASE_URL=postgresql+psycopg://…` for PostgreSQL. Distances use haversine in Python — PostGIS isn't required yet; add it when you need spatial queries at scale. Redis isn't needed for the single-process deployment (session lock is a DB column; live map uses an in-memory hub) — swap the hub for Redis pub/sub when running several instances.
- **Route optimiser** is a heuristic with a 1.3× road factor, not live-traffic routing.
- **SMS/Email**: with no gateway configured the visit OTP is shown on screen (demo mode) and MOM emails are only logged.
- ERPNext mappings were written against standard v14/15 doctypes and tested with a mock server, **not a live ERPNext** — run "Test connection" and a sync on a staging site first.
- Billing/plan enforcement beyond seat limits, a super-admin console and schema migrations (Alembic) are not built yet.

## Deploy
`docker compose up -d --build` (Postgres+PostGIS + app) behind HTTPS (geolocation/camera need HTTPS). Set `SECRET_KEY`.
