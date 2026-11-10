# Fieldwise: automation and AI-agent opportunities

Already automated today: first follow-up scheduled on every new lead, lead assignment (round-robin / pool / rules), auto-regret of stale leads, repeat-enquiry merge,
missed check-in / silent-tracker / waiting-time alerts, follow-up, task and meeting reminders with escalation, scheduled E-reports, hourly ERPNext sync, renewal reminders,
and (new) the **AI Lead Qualifier**.

Effort: S = 1-3 days, M = about a week, L = 2+ weeks. "Needs" lists anything outside the code (a provider account, data, a decision).

## A. Rules-based automations (no language model, cheap and predictable) — build first
| # | Automation | What it does | Effort | Needs |
|---|---|---|---|---|
| 1 | **Speed-to-lead SLA** | A first-contact clock on every new lead. No call or visit logged in 30 min: nudge the owner. 60 min: alert the manager. 2 h: reassign to the next available rep. Instant "thanks, <rep> will call you" message to the buyer. | S-M | WhatsApp or SMS gateway for the buyer message |
| 2 | **Smarter assignment** | Choose the owner by product group, region and current load. Skip reps on leave or not checked in. Prefer the rep nearest the lead's city (live location already exists). Uses the new sales-team table and its hierarchy. | M | Rules agreed with sales head |
| 3 | **Stalled-deal watch** | Stage-ageing limits per stage. Auto-create a task and add the deal to a weekly "deals at risk" digest for each manager. | S | Agreed ageing limits |
| 4 | **Quotation follow-up ladder** | Quote sent: automatic follow-ups at +2, +5 and +10 days, expiry warning, "revise quote" prompt after a discount request. | S | Templates |
| 5 | **Collections reminders** | ERPNext overdue invoices: staged reminders to the customer (WhatsApp/email) plus a task for the rep. Track promise-to-pay dates. | M | Finance sign-off on wording |
| 6 | **Auto-fill and enrichment** | GSTIN gives legal name, address and state. Pincode gives city and state. Match leads to existing ERPNext customers. | S | GST lookup provider |
| 7 | **"Plan my day"** | One tap proposes today's visits from overdue follow-ups, hot leads and overdue collections near the rep, ordered by the route optimiser we already have. | M | none |
| 8 | **Expense auto-checks** | Compare claimed fuel/travel with tracked kilometres, auto-approve within policy, flag outliers. | S | Policy limits |
| 9 | **Attendance exceptions** | Forgot check-out: close at shift end using tracked evidence. Late-arrival and regularisation requests approved by rule. | S | HR rules |

## B. AI agents (language model with guardrails) — build after A1-A4
| # | Agent | What it does | Effort | Guardrail |
|---|---|---|---|---|
| 1 | **Lead concierge** | Replies on WhatsApp/email to a portal enquiry within a minute, asks the missing questions the qualifier already lists (capacity, timeline, location, product), updates the lead, and hands over to the rep once it is qualified. | M-L | Fixed question set; never quotes prices; hands off on any doubt |
| 2 | **Quotation drafter** | Reads the enquiry, the catalogue and similar past quotes, then drafts the quotation (items, quantities, terms). A human approves before it goes to ERPNext. | M | Human approval required |
| 3 | **Visit MOM agent** | A short voice note or text becomes a structured minutes-of-meeting, follow-up tasks and a suggested stage change. | M | Rep confirms before saving |
| 4 | **Manager copilot** | A morning briefing per rep and manager (what needs attention today) and a chat to ask "who is behind target, which big deals are at risk". Reads CRM data only. | M | Read-only; respects who may see what |
| 5 | **Collections assistant** | Drafts and times reminder messages and records promises made. | M | Finance approves templates |
| 6 | **Data-quality agent** | Finds duplicates, fixes company names, links leads to ERPNext customers, spots suspicious attendance patterns. | M | Suggests, a person applies |

## C. Make the AI Lead Qualifier trustworthy (do this during the pilot)
1. **Outcome tracking:** compare each verdict with what happened (won / lost / no contact) and show precision on the AI settings page.
2. **False-reject queue:** a list of "Rejected" leads for a person to review, with one click to overturn. Overturns feed back into the thresholds.
3. **Calibrate with real data:** after about 200 real leads, adjust the weights, the 45-point line and the source scores from the numbers.
4. **Keep auto-regret off** until precision is proven above roughly 95% for rejects.

## Suggested order for the pilot
1. A1 speed-to-lead SLA, A3 stalled-deal watch, A4 quotation follow-ups and C1-C2 (outcome tracking and review queue).
2. A2 smarter assignment and A7 "Plan my day".
3. B1 lead concierge, B3 MOM agent and B4 manager copilot, once the message gateways are proven in the pilot.

## Caveats
- Everything involving WhatsApp, SMS and telephony needs a real provider tested with real messages (still untested; see PILOT_CHECKLIST).
- Any agent that writes to the customer or to ERPNext should start in "suggest only" mode and be switched to automatic per rule once its accuracy is measured.
- Language-model agents send lead text to the provider you choose. Keep them opt-in per workspace, as the existing AI settings already are.
