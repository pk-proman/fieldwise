# Field team: import, hierarchy, phone activation, routes and automatic expenses (app 1.3.0)

## 1. Deploy
```bash
./deploy/deploy.sh root@187.127.182.29 --domain fieldwise.promaninfi.com --email prashant.c.kumar@gmail.com
./mobile/scripts/build-apk.sh release && ./mobile/release/publish-apk.sh
```
Two Alembic migrations run on deploy: `c3e9a6b1d742` (phone activation tables) and `d4f1b8c27a96` (daily route summaries, `users.vehicle`, `expense_claims.source`).

## 2. What changed
| Area | Behaviour |
|---|---|
| Wording | The "Sales team" settings tab is now "Field team". Rename it per client with the `team_label` setting. |
| Team import / export | Settings > Users & hierarchy > Import / Export (CSV). Import has a "Check file" preview, matches people by email or employee code, resolves `reporting_manager` (email or employee code), rejects loops, respects the seat limit. New people get no password: give them an app PIN. |
| Hierarchy | One shared function decides who is under whom (`Ctx.subtree_ids`). A disabled manager's direct reports move up one level automatically. A disabled person cannot be chosen as manager. |
| Phone activation | Users & hierarchy > open a person > **App PIN**. The PIN is 6 digits, single use, valid 48 hours, tied to the registered mobile number, locked after 5 wrong tries. The person enters company code + mobile + PIN once. Activating on a new phone, or after a reinstall, needs a new PIN and removes the old phone. |
| App scope | PIN scope **Field only** (default): the phone gets Check-in, Visits, Tasks, Beat plan, Leads, Follow-ups, Expenses, Leaves, Announcements, and the server refuses admin or team calls from that phone. **Field + manager** adds the person's normal tools. |
| No daily sign-in | The phone keeps a long-lived token, renewed whenever the app opens. The administrator can remove the phone (App PIN dialog) at any time. Web sign-in (email, password, MFA) is unchanged and employees may use it too. |
| Mobile number changed | The person taps "My mobile number changed" in the app; administrators get a notification and issue a new PIN. |
| Routes | Distance ignores GPS drift, impossible jumps and poor-accuracy or mock points. Silent stretches (phone off, no GPS) are shown as **estimated** and not counted unless the workspace chooses to. A daily summary (distance, stops, route line, quality score) is kept about 13 months; raw points follow the retention setting. |
| Route history | New **Route history** page: month view, drill down Me > direct reports > their team, per-person day list, map of each day, CSV export. |
| Automatic expenses | Settings > **Expense rules**: rate per km, minimum km, daily cap, daily and outstation allowance, set for everyone and overridden by role, designation and vehicle (most specific wins). Modes: **Off**, **Shadow** (calculated and shown, no claims, nobody paid), **On** (claims created for approval after check-out). A hand-made fuel claim is blocked on a day that already has an automatic one. |

## 3. Recommended rollout for expenses
1. Set the rules and switch to **Shadow** for the first month.
2. For a few people, compare the shown km and amount with the odometer or a map app.
3. Switch to **On** only when they agree. Managers can still reject or correct any claim.

## 4. Honest limits
* **Not tested on a real phone yet:** background tracking, the activation screen inside the Android app, and the long-lived token across a reboot. Run `deploy/ANDROID_DEVICE_TEST.md` first.
* The PIN proves the person was given the PIN for that number. It does not prove they hold that SIM (an SMS or WhatsApp OTP can be added later). IMEI cannot be read by apps on modern Android, so the phone is bound by an install key generated at activation.
* Distance is cleaned GPS, not road-snapped. Road snapping needs a routing service and is not built.
* Vehicle types are the four fixed choices bike, car, public, other.
* Dashboards with hierarchy drill-down (points 4 and 5) are not built yet; Route history is the first screen with that drill-down.

## 5. iPhone (6 users planned)
Apple does not allow installing an app from a link the way Android does. Use the web app (Add to Home Screen) for check-in, visits and expenses: it cannot record routes in the background. For route tracking on iPhone, use TestFlight for a pilot (needs an Apple Developer account, US$99/year; builds expire every 90 days) and later unlisted App Store distribution.
