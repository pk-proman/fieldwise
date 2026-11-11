# Next four workstreams (planned 2026-11-11)

Starting point found in the code: there is **no translation layer** (about 600 hard-coded English strings across 20 JS files); WhatsApp/SMS go out through a generic gateway webhook (`routers/comms.py`) with **no inbound path and no conversation store**; campaigns are **auto-dialer only**; background tracking exists in the Android app but is **unproven on real phones and not on Play**.

Effort: S = 1-3 days, M = about a week, L = 2+ weeks. Estimates assume one developer and quick decisions.

## Suggested order
| Order | Workstream | Why this slot |
|---|---|---|
| 1 | **D. Reliable tracking** (Phase 1 testing now, Play after) | Biggest competitive gap; long waits (D-U-N-S, Play review) so it starts first and runs in parallel with everything else |
| 2 | **B. WhatsApp inbox** | Highest value for a sales team; also unblocks the Lead concierge agent and collections reminders |
| 3 | **A. Hindi** | Adoption for field staff; independent of the others, can fill waiting time |
| 4 | **C. Campaigns** | Needs B (WhatsApp templates, opt-in, sending engine) and is the lowest value for Proman's sales process |

---

## A. Hindi screens (about 2 weeks, then more languages cheaply)
**Approach:** a lightweight translation layer, no framework change.
1. `t("key", {vars})` helper in `frontend/js/i18n.js`, with `locales/en.json` and `locales/hi.json`; language stored per user (`users.language`) and in the app header switcher; falls back to English per missing key, so partial coverage never breaks a screen.
2. Move strings out in priority order, not all at once: (1) sign-in, home, attendance check-in/out, visits, tasks, expenses, leave (everything field staff touch daily), then (2) leads/CRM, then (3) admin and settings (managers can stay English).
3. Dates, numbers and currency through `Intl` with `hi-IN` (lakh/crore grouping); Devanagari font fallback so text never renders as boxes; check layouts for longer Hindi strings.
4. Server messages: error texts and notification/alert texts (missed check-in, reminders) carry a code the client translates; templates for outgoing SMS/WhatsApp get a per-language variant.
5. Pluggable so Arabic (RTL) and regional languages can follow: the layer must not hard-code LTR; Arabic would add an RTL pass.

**Needs:** a Hindi translator or reviewer who knows field-sales vocabulary (machine draft first, human review of the daily-use screens; wrong terms on attendance screens cause real confusion). A glossary agreed once (check-in, visit, beat, lead, quotation).
**Risks:** string extraction touches every file, so do it screen by screen with a visual check; add a test that every key in `en.json` exists in `hi.json` and that no raw English remains on the priority screens.
**Done when:** a Hindi-language user can complete a full day (check in, visit, expense, check out) without seeing English.

## B. Two-way WhatsApp inbox (3 to 5 weeks)
**Decision first: provider.** Official WhatsApp needs the **WhatsApp Business Platform (Cloud API)**, directly from Meta or through a Business Solution Provider (BSP).
| Option | Notes |
|---|---|
| **Meta Cloud API direct** | No per-message markup beyond Meta's fees, webhooks for inbound and delivery status, template management by API. Needs a verified Meta Business account, a dedicated phone number, and display-name approval. Recommended. |
| **BSP (Gupshup, Interakt, WATI, AiSensy, Twilio)** | Faster start, their dashboard handles templates; extra monthly fee and per-message markup; same API shape through an adapter. |
| **Unofficial gateways (QR-scan style)** | Not recommended: against WhatsApp terms, numbers get banned. |
Meta rules that shape the design: a business can send **free-form messages only within 24 hours of the customer's last message**; outside that window only **pre-approved templates**. Customers must have **opted in**. The inbox must show the window state and force a template when it has closed.

**Build:**
1. Data: `conversations` (per customer number and channel, linked to a lead/contact/customer, owner, status open/snoozed/closed, last inbound time), `messages` (direction, type text/image/document/voice/location, provider id, status sent/delivered/read/failed, media path), `wa_templates` (name, language, category, body, approval status).
2. Inbound webhook with signature verification: store messages, download media into uploads, match the number to a lead/contact (existing `_match`) or create an unassigned conversation, and **auto-create a lead** when an unknown number writes in (feeds the AI qualifier and assignment rules).
3. Outbound: free-form inside the window, template outside; delivery/read receipts; retry and failure reasons (this replaces the fire-and-forget gateway call for WhatsApp).
4. UI: inbox page (conversation list with unread, assigned-to-me / unassigned / all; thread view; reply box with attachments; canned replies; "convert to lead", "log as activity", assign/transfer, internal notes) and a conversation tab on every lead and customer. Realtime through the existing WebSocket hub. Mobile layout first, since reps use phones.
5. Operations: opt-in capture and opt-out ("STOP") handling, rate limits and quality-rating monitoring, per-workspace credentials (encrypted like the ERP keys), and audit log.
6. Same data model leaves room for SMS and email threads in the same inbox later.

**Needs:** a decision on provider and the WhatsApp number (it cannot also be in use on the normal WhatsApp app); Meta Business verification (days to weeks, start now); template wording for the first messages; a privacy-policy line about messaging.
**Risks:** template approvals are slow and sometimes rejected; number quality rating can drop if customers report messages; message costs are per conversation category, so budget it.
**Done when:** a lead's reply on WhatsApp appears in the rep's inbox within seconds, the rep answers from the app, status ticks update, and an unknown number becomes a lead.

## C. Marketing campaigns (3 to 4 weeks, after B)
Today: auto-dialer campaigns only. Add, in this order:
1. **Audience builder:** saved segments from filters we already have (stage, product, source, city, tags, last activity, AI verdict, customer vs lead), with live count and a preview; exclusion of opted-out and recently contacted numbers.
2. **Broadcasts:** one-off WhatsApp (template) and email sends to a segment, scheduled, throttled (Meta tier limits), with per-recipient status (sent/delivered/read/replied/failed) and a campaign report. Replies land in the inbox from B and can create tasks for the owner.
3. **Drip journeys:** simple step lists ("day 0 WhatsApp, day 2 email, day 5 task for the rep"), with exit rules (replied, booked a visit, won/lost). Built on the existing scheduler. A visual canvas is not needed; a list editor is enough.
4. **Compliance guardrails:** consent flag on every contact, unsubscribe link in email, WhatsApp opt-out keyword, quiet hours, TRAI/DLT registration if SMS is used in India (templates and sender IDs must be registered).
5. **Email sending:** a proper provider (SES, SendGrid, or the company SMTP) with domain authentication (SPF/DKIM/DMARC) so mail lands in inboxes; today's SMTP setting is for system mail.

**Priority note:** for an infrastructure-products sales team the value is mostly re-engaging old leads and quote follow-ups. The existing automation roadmap (quotation follow-up ladder, collections reminders) covers more of that than mass marketing; build C1-C2 and keep C3 small unless Proman wants nurture campaigns.
**Needs:** consent data (do old leads have it?), email provider and sending domain, DLT registration if SMS.

## D. Reliable background tracking (the biggest competitive gap)
Two tracks that run in parallel. The app already has a foreground-service tracker; the gap is **proof on real phones** and **Play distribution**.

**D1. Make it dependable (next 2 to 3 weeks, in Phase 1 testing)**
*Status 2026-11-11: items 2, 3 and 6 are built in app 1.1.0 (setup wizard with the tracking notice, watchdog and boot notifications, resume-time restart, stationary heartbeat, phone-health view with likely cause in alerts). Items 1 (device tests), 4 and 5 remain. Not yet run on any phone.*
1. Run `deploy/ANDROID_DEVICE_TEST.md` on Xiaomi, Oppo, Vivo, Samsung and Pixel for full days. Treat the measured gap between pings per brand as the acceptance number.
2. **Setup wizard inside the app** (not just the download page): after first sign-in, step through location "Allow all the time", notification permission, battery set to Unrestricted, and the brand-specific Autostart/lock screens, with a deep link to the right settings page per manufacturer, and a status check that shows green only when each is really set.
3. **Self-healing:** a watchdog (WorkManager job) that restarts the service if the OS killed it, resume after reboot when the user is checked in, and a "tracking stopped unexpectedly" push/alert to the manager (silent-tracker alert already exists server-side; make it fire on a real gap and show the reason: GPS off, no permission, battery restriction, app killed, offline).
4. **Smarter and cheaper sampling:** motion-aware intervals (activity recognition: stationary vs moving), adaptive distance filter, batching uploads, and a battery-level floor; target under about 8 percent per day.
5. **Trust signals:** mock-location flag already sent; add detection of rooted/emulated devices, time-tampering checks and a "location accuracy" quality score on each day's route.
6. **Observability:** per-user, per-device health on the manager dashboard (last ping, app version, battery, permission state, OEM) so support can answer "why is X not showing" in a minute.
7. A fallback path for hard cases: BLE beacon or QR check-in at sites, and fused-provider tuning, as options only if some phone models stay unreliable.

**D2. Google Play (4 to 6 weeks end to end, mostly waiting; start now)**
Same as the earlier plan, in order of lead time:
1. **D-U-N-S number** (applied; chase it) and then Play organization account verification, which can only start once it arrives.
2. Meanwhile prepare: privacy policy page, account-deletion page, Data-safety answers, content rating, store listing assets (feature graphic, screenshots), demo login workspace.
3. **Prominent in-app disclosure screen** before the location prompt (explicit consent text, "Accept / Not now"), plus the **background-location permission declaration** and a short demo video. This is where rejections happen, so it is built and reviewed carefully, and the in-app wizard above doubles as the disclosure flow.
4. Internal test track first, then closed/production with staged rollout.
**Needs:** D-U-N-S, support mailbox, publisher decision (an organization account shows the organization's legal name), a Proman-owned domain page for the privacy policy.

## Decisions (2026-11-11)
1. **WhatsApp: Meta Cloud API direct.** Still open: which phone number is the business line (must not be registered on the normal WhatsApp app), and who owns the Meta Business account.
2. **Hindi: on hold.** Workstream A is paused; nothing to build until reopened.
3. **Campaigns: drip journeys are in scope** (C1 to C5 all planned, after the inbox).
4. **Test phones: available.** Device testing (D1) can start as soon as the APK is deployed and published.

## Rough calendar (one developer, parallel waits)
| Weeks | Work |
|---|---|
| 1-3 | D1 device tests and wizard, start Play prep, Meta Business verification |
| 3-8 | B WhatsApp inbox build and pilot on the live number |
| 6-10 | D2 Play submission and review; C1-C2 broadcasts after B is stable; C3 drip journeys follow |
| 10+ | C4-C5 compliance and email sending hardening; Hindi when reopened |
