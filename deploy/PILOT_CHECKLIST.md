# Fieldwise pilot checklist (Proman)

**Goal of the pilot:** prove that a small real team can run a normal working week in Fieldwise (attendance, visits, leads, quotations, orders, collections) with ERPNext staying correct, and find what breaks *before* everyone is moved over from Groweon.
**Suggested size and length:** 1 manager + 6 field staff (4 India, 2 Middle East) + 1 admin, for **3 weeks**. Keep Groweon running for everyone else. Do **not** retire Groweon during the pilot.

Legend: ☐ to do · **(blocker)** must be done before pilot users start · *(you)* needs a decision or access only you have

---
## A. Decisions before anything else  *(you)*
- ☐ **(blocker)** Choose pilot people and a pilot manager. Tell them the pilot has a start/end date and that they may be asked for feedback daily.
- ☐ **(blocker)** Choose the hosting: server location (India for data-residency comfort), who owns the server and domain, who is on call. Domain e.g. `fieldwise.promaninfi.com`.
- ☐ **(blocker)** Legal/HR sign-off on location tracking: written notice to the pilot staff, working hours when tracking applies, who can see the data, how long it is kept (default 180 days). Get advice for India (DPDP Act) and for the Middle East country or countries involved.
- ☐ Decide what "success" means (section H) and who decides go/no-go.
- ☐ Decide the ERPNext approach: pilot against a **staging/copy** of ERPNext first (strongly recommended), or production from day one.

## B. Infrastructure & security (admin / IT)
- ☐ **(blocker)** Server with Docker, DNS, TLS certificate; `deploy/nginx.conf` installed; `https://…/api/ready` shows ready.
- ☐ **(blocker)** `.env` filled: `SECRET_KEY` (random, 32+ chars), `POSTGRES_PASSWORD`, `PUBLIC_URL`. The app refuses to start otherwise.
- ☐ **(blocker)** Backups running **and a restore tested** on a scratch database. Copies leave the server (second location).
- ☐ Uptime monitor on `/api/ready`; someone receives the alert.
- ☐ Owner account created with a strong password **and authenticator (MFA) switched on**; a second admin exists in case the owner is away.
- ☐ Settings → Security: MFA mode (optional/required), session length, tracking-notice text and version reviewed by HR/legal.
- ☐ Remove or never create the demo workspace (`proman` / `Demo@12345`): the seed data is for demos only.
- ☐ Ask an independent party for a penetration test **before public sign-up is opened**. For a closed pilot (invited users only) this is recommended, not blocking.

## C. Providers & integrations (test each with a real message/call)
- ☐ **(blocker)** **SMS gateway** configured and tested: visit OTPs are the control that proves a visit happened. In production the OTP is never shown on screen, so without SMS visits can't start. (Alternatively switch visit OTP off for the pilot.)
- ☐ **(blocker)** **Email (SMTP)** configured: password resets, quotations, MOMs, scheduled reports. Send a test.
- ☐ WhatsApp gateway (optional): send a test quotation link. Without it, the app opens WhatsApp with the text ready.
- ☐ Telephony (optional): click-to-call and call-log webhook tested with one agent; confirm a missed call from a new number creates a lead.
- ☐ **Lead portals** (IndiaMART, JustDial, TradeIndia…): for each one you use, paste a **real sample payload** into Settings → Lead sources → *Test a payload*, then send one live test enquiry and confirm: lead created, right source, product mapped (or "Unspecified"), right owner, no duplicate on a second send. Watch "Recent leads received" daily.
- ☐ **ERPNext (staging first):** API key/secret created for a dedicated ERP user with only the needed permissions. Run, in this order, checking the result in ERPNext each time: *Test connection* → Employees → Products → Leads & customers → one Quotation → one Sales Order with PO attached (it must arrive as a **Draft**) → one Sales Invoice (draft) → one Payment Entry → attendance (Employee Checkin). Check that item codes, customer names, mode-of-payment names and expense-claim types match your ERPNext setup (see section K).
- ☐ AI "Analyse lead": leave **off** for the pilot unless the data-sharing is approved; it sends lead details to the provider you choose.
- ☐ Face check: the built-in provider accepts any selfie (stored as "selfie" proof only). Decide: selfie-as-photo is fine for the pilot, or configure AWS Rekognition first.

## D. Workspace set-up (admin, ~1 day)
- ☐ Company profile (name, address, GSTIN, PAN, bank details, logo) – these print on quotations and invoices.
- ☐ Branches with correct map position and geofence radius (150–300 m is typical; test at the real gate).
- ☐ Users, roles and **reporting lines** (managers only see their own team). Departments, designations, shifts and weekly offs. Holidays for India and the Middle East. Leave types.
- ☐ Lead stages, sources, regions, regret reasons; qualification threshold; discount-approval %; first-follow-up and auto-regret days; lead assignment (round-robin/pool/rules).
- ☐ Quotation terms and conditions, additional charges, number series (so numbers continue from your current series), tax %.
- ☐ Products (74 in Groweon): import, then check GST %, HSN, units. Credit limits and credit days on key customers.
- ☐ Expense categories, fuel rate and cap, daily-allowance rates and approval levels, forms/checklists you want on visits.
- ☐ Territories drawn for each pilot rep; roster for the first week.

## E. Data migration from Groweon (do a rehearsal first)
- ☐ Export from Groweon (leads, companies, contacts, products, open orders/invoices outstanding). *(you: needs your Groweon access)*
- ☐ **Rehearse on a throw-away workspace:** import products → customers → leads (use *Check file* first). Review the error list; fix the CSV; re-run until clean.
- ☐ Decide the cut-over rule: **only open/active leads and customers with activity in the last 12–18 months** (Groweon shows 1,265 active leads; old/closed history can stay in Groweon).
- ☐ Duplicates: leads import merges same mobile/email as repeat enquiries; review the "repeat" count after import.
- ☐ Outstanding invoices: come from ERPNext (sync), not from Groweon. Compare the totals with Finance.
- ☐ Pilot users' own leads only (assign owners by email column) so the pilot isn't mixed with the rest of the company.

## F. Devices & training (1–2 hours per group)
- ☐ Install the Android app (`mobile/dist/Fieldwise-debug.apk`) or the web app (add to home screen) on **each pilot phone**. iPhones: web app until the iOS app is built. Enter the company server address once.
- ☐ On each phone: location = **Allow all the time**; battery saver **off** for Fieldwise (Xiaomi/Oppo/Vivo/Samsung "sleeping apps" kill tracking); notifications on.
- ☐ Each person accepts the tracking notice, enrols face (if used) and sets up the authenticator app if MFA is on.
- ☐ Walk through one real day: check in at a branch → live tracking → visit with OTP → MOM → quotation → check out → claim fuel/DA.
- ☐ Show managers: Live team, Work hours, Roster, approvals (leave, expenses, discounts, credit), Daily call report.
- ☐ One-page "what to do if…" for staff: no signal, phone died, forgot to check out, wrong OTP, can't sign in.
- ☐ A single support channel (WhatsApp group/email) and a named person who answers within the working day.

## G. Pilot operation
**Every day (pilot manager, 10 min):** Work hours → Alerts & anomalies (gaps, fake GPS, no check-out); Follow-ups overdue; any rejected/failed sync in Settings → ERPNext → recent activity; Settings → Lead sources → recent leads received.
**Twice a week (admin):** ERPNext sync log has no red rows; invoices/outstanding match ERPNext; backups are present; server disk space.
**Weekly (all pilot users, 15 min):** feedback call. Log every issue with: who, what, what they expected, the **X-Request-ID** if shown, screenshot.
- ☐ Week 1: shadow with Groweon in parallel for the pilot users' leads (enter in both for the first days if you want a safety net, then stop).
- ☐ Week 2: first month-end style cycle: expenses approved and posted to ERPNext, collections verified, daily call reports reviewed.
- ☐ Week 3: stress the edges: offline day, new joiner, leave + roster swap, trade-fair lead capture, renewal reminders, a discount needing approval, a credit-limit exception.

## H. Success measures (set targets before you start)
| Area | Measure | Suggested target |
|---|---|---|
| Adoption | pilot staff who check in on working days | ≥ 90% |
| Tracking | check-ins with a usable route (no gap > 30 min) | ≥ 85% |
| Visits | visits logged with OTP vs. meetings the team says happened | ≥ 90% |
| Leads | portal enquiries that arrive in Fieldwise within 5 min | ≥ 99% (and zero silent losses: compare with the portal's own count) |
| ERPNext | quotations/orders/payments in ERPNext matching Fieldwise | 100% (every mismatch investigated) |
| Reliability | app unavailable during working hours | < 1 hour in total |
| Speed | screens feel instant on mobile data | no one reports waiting > 5 s regularly |
| Satisfaction | "I'd rather use this than Groweon" | majority of pilot users |
| Battery | complaints of battery drain | none beyond what staff accept |

## I. Go / no-go for wider rollout (all must be true)
- ☐ No open data-loss, wrong-money or wrong-person-sees-data issues.
- ☐ ERPNext reconciliation clean for two consecutive weeks.
- ☐ Restore of a backup tested again during the pilot.
- ☐ Targets in H met (or each miss has an agreed fix and date).
- ☐ HR/legal sign-off applies to all staff, not just the pilot group.
- ☐ Penetration test done and critical/high findings fixed.
- ☐ Support person/rota in place for the larger group; capacity checked (one app process: fine for a few hundred users; see RUNBOOK §4).
- ☐ A dated plan for retiring Groweon (read-only first), with the final export saved.

## J. Rollback plan (agree it now)
- Pilot users go back to Groweon the same day: it was never switched off. Export their Fieldwise leads (Leads → Export) so nothing created during the pilot is lost.
- ERPNext documents created from Fieldwise are normal ERPNext documents (drafts for orders and invoices): they can be cancelled in ERPNext. Decide who reviews them.
- Data deletion (if the pilot is cancelled): Settings → Security → Download everything, then Delete workspace.

## K. Known limits to tell the pilot group (so they aren't surprised)
- Tracking works on the **Android app** with the screen off. On the **web app / iPhone web**, tracking only runs while the app is open.
- The iPhone app has not been built yet. The Android file is a **debug build** (not from the Play Store); phones ask permission to install it.
- The face check does not verify identity yet (photo proof only). Fingerprint/face unlock on Android is checked by the phone itself.
- Maps use free OpenStreetMap tiles: fine for a pilot, replace with a paid tile provider before many users.
- ERPNext: mappings follow standard v14/15 fields; **custom fields, naming series, item codes, account heads and mode-of-payment names differ by site**: that is what the staging run is for. Daily/hourly sync, not instant, for most things.
- One app process: very large teams or several servers need extra work (RUNBOOK §4).
- Things not built: marketing campaigns/journeys, omni-channel inbox, customer self-service portal, payroll.

## L. Risk register (review weekly)
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Staff phones kill background tracking | High | Medium | Battery exemptions in training; check "No signal" in Work hours daily |
| Portal payload differs from our mapping → leads dropped | Medium | High | Real-sample test; compare daily with portal counts; "Unspecified" product still creates the lead |
| ERPNext mapping mismatch (items, accounts, taxes) | Medium | High | Staging first; reconcile weekly; Quotation/Order drafts only |
| Staff resist tracking / privacy concern | Medium | High | Clear notice, limited hours, explain who sees what, listen to concerns early |
| Server or database failure | Low | High | Tested backups, monitoring, restore drill |
| Fake GPS / buddy punching | Medium | Medium | Native app flags fake location; anomalies report; selfie proof |
| Credentials/keys leaked | Low | High | MFA for admins, key rotation steps in RUNBOOK §7, audit trail review |
| Pilot results aren't representative | Medium | Medium | Include India and Middle East, a manager, and a mix of tenure |

## M. Owner table (fill in)
| Area | Name | Backup |
|---|---|---|
| Pilot sponsor / go-no-go | | |
| Pilot manager (daily review) | | |
| Admin (settings, users) | | |
| IT (server, backups, TLS) | | |
| ERPNext / Finance reconciliation | | |
| HR / legal (tracking notice) | | |
| Support contact for pilot users | | |
