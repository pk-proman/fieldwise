// Tracking setup wizard + health watch (Android app only). Phones from many brands stop background apps unless a handful of settings are right,
// so this walks each person through them, shows live green/red status, warns when something breaks later, and reports it to the manager.
import { $, esc, modal } from "./ui.js";
import { post } from "./api.js";
import { plugin, isNative } from "./native.js";
import * as tracker from "./tracker.js";

const LS = { disclosure: "fw-trk-disclosure", autostart: "fw-trk-autostart", prompted: "fw-trk-prompted" };
const get = k => { try { return localStorage.getItem(k); } catch { return null; } };
const put = (k, v) => { try { localStorage.setItem(k, v); } catch {} };
const H = () => plugin("FieldwiseHealth");
const AUTOSTART_BRANDS = ["xiaomi", "redmi", "poco", "oppo", "realme", "oneplus", "vivo", "iqoo", "huawei", "honor", "samsung"];
const BRAND_HELP = {
  xiaomi: "Turn Fieldwise ON in the Autostart list. Then open Recent apps, long-press the Fieldwise card and tap the lock icon.",
  oppo: "Turn on Allow auto-launch / Allow background activity for Fieldwise. Then lock the app in Recent apps.",
  vivo: "Allow Fieldwise to start in the background (Background power consumption: High / No restrictions).",
  samsung: "Open Battery, then Background usage limits. Make sure Fieldwise is NOT in Sleeping apps or Deep sleeping apps.",
  huawei: "Turn Manage automatically OFF for Fieldwise and allow Auto-launch, Secondary launch and Run in background.",
  oneplus: "Allow Fieldwise to Auto-launch and set Battery to Don't optimise.",
};
const brandKey = m => /xiaomi|redmi|poco/.test(m) ? "xiaomi" : /oppo|realme/.test(m) ? "oppo" : /vivo|iqoo/.test(m) ? "vivo" : /huawei|honor/.test(m) ? "huawei" : m;
export const needsAutostart = s => AUTOSTART_BRANDS.includes(s?.manufacturer || "");

let appVersion = "", launchReason = "", launchChecked = false;

/** Raw phone status merged with what only the person can confirm (autostart) and whether they have seen the tracking notice. */
export async function status() {
  const h = H(); if (!h) return null;
  try {
    const s = await h.status();
    s.autostartConfirmed = !needsAutostart(s) || get(LS.autostart) === "1";
    s.disclosureAccepted = !!get(LS.disclosure);
    return s;
  } catch { return null; }
}

export function problems(s) {
  if (!s) return [];
  const out = [];
  if (!s.locationServices) out.push("Phone location is switched off");
  if (s.location !== "always") out.push(s.location === "denied" ? "Location permission is denied" : "Location is not set to ‘Allow all the time’");
  if (!s.notifications) out.push("Notifications are blocked");
  if (!s.batteryUnrestricted) out.push("Battery optimisation is on for Fieldwise");
  if (!s.autostartConfirmed) out.push("Autostart not confirmed");
  return out;
}

async function appInfo() { if (appVersion) return appVersion; try { appVersion = (await plugin("App")?.getInfo())?.version || ""; } catch {} return appVersion; }

export async function reportHealth(s) {
  s = s || await status(); if (!s) return;
  try {
    await post("/tracking/health", { location: s.location, location_services: s.locationServices, notifications: s.notifications, battery_unrestricted: s.batteryUnrestricted,
      autostart_confirmed: s.autostartConfirmed, disclosure_accepted: s.disclosureAccepted, manufacturer: s.manufacturer, model: s.model, android: s.android,
      app_version: await appInfo(), tracking_active: !!tracker.state.active, last_ping_age_min: s.lastPingAgeMin, stop_reason: launchReason });
  } catch { /* offline: next time */ }
}

const STEPS = s => [
  { id: "loc_on", ok: s.locationServices, title: "Phone location is ON", why: "The phone's GPS switch must be on for your route to be recorded.",
    btn: "Open location settings", act: () => H().openSettings({ target: "location" }) },
  { id: "loc", ok: s.location === "always", title: "Location: Allow all the time", why: "Without ‘all the time’, Android stops recording when the screen is off or the app is closed.",
    btn: s.location === "denied" ? "Allow location" : "Allow all the time", act: () => H().requestLocation(), alt: "Open app settings", altAct: () => H().openSettings({ target: "app" }),
    help: "In the app settings choose Permissions, Location, then Allow all the time (and keep Use precise location on)." },
  { id: "notif", ok: s.notifications, title: "Notifications allowed", why: "Shows the ‘Tracking on duty’ notice while you are checked in and warns you if tracking stops.",
    btn: "Allow notifications", act: async () => { await H().requestNotifications(); setTimeout(async () => { const n = await status(); if (n && !n.notifications) H().openSettings({ target: "notifications" }); }, 900); } },
  { id: "batt", ok: s.batteryUnrestricted, title: "Battery: Unrestricted", why: "Battery savers kill background apps. Fieldwise uses little battery; this keeps it running all day.",
    btn: "Open battery settings", act: () => H().openSettings({ target: "battery" }), help: "Find Fieldwise in the list and choose Don't optimise / Unrestricted. If you cannot find it: Settings, Apps, Fieldwise, Battery, Unrestricted." },
  ...(needsAutostart(s) ? [{ id: "auto", ok: s.autostartConfirmed, title: "Allow Autostart / background run", why: `Your ${cap(s.manufacturer)} phone has an extra switch that other phones do not have.`,
    btn: "Open the setting", act: () => H().openSettings({ target: "autostart" }), help: BRAND_HELP[brandKey(s.manufacturer)] || BRAND_HELP.oppo,
    confirm: "I have done this", confirmAct: () => put(LS.autostart, "1") }] : []),
];
const cap = x => x ? x[0].toUpperCase() + x.slice(1) : "";

/** The tracking notice (also Google Play's required "prominent disclosure"): shown before any location permission is requested. */
function disclosure(onAccept) {
  const m = modal({ title: "How tracking works", body: `<p><b>Fieldwise records your location, even when the app is closed or the screen is off,</b> from the moment you check in until you check out.</p>
    <ul><li>Your route is used for attendance, visit verification and travel distance.</li><li>Your manager and administrators can see your live position and route while you are checked in.</li><li>Nothing is recorded after you check out.</li></ul>
    <p class="small muted">Your company's privacy notice explains how long this data is kept and who can see it.</p>`,
    actions: [{ label: "Not now", close: true }, { label: "I understand", primary: true, onClick: () => { put(LS.disclosure, new Date().toISOString()); onAccept(); } }] });
  return m;
}

export async function openWizard() {
  if (!H()) return;
  if (!get(LS.disclosure)) return disclosure(() => openWizard());
  const root = document.createElement("div");
  const m = modal({ title: "Set up tracking on this phone", wide: true, body: root, actions: [{ label: "Close", close: true }] });
  let timer, live = true;
  const paint = async () => {
    const s = await status(); if (!s || !live) return;
    const st = STEPS(s), done = st.filter(x => x.ok).length, all = done === st.length;
    root.innerHTML = `<p class="small muted" style="margin-top:0">${all ? "" : "A few settings keep tracking alive all day. "}${done} of ${st.length} done. This screen updates as you change settings.</p>
      ${all ? `<div class="chip ok" style="display:block;padding:10px 12px;border-radius:8px;margin-bottom:10px"><b>All set.</b> Tracking will keep running when the screen is off.</div>` : ""}
      ${st.map(x => `<div class="card" style="padding:12px;margin-bottom:8px;border-left:4px solid ${x.ok ? "var(--ok)" : "var(--danger)"}">
        <div class="row" style="gap:8px"><b>${x.ok ? "✓" : "✗"} ${esc(x.title)}</b></div>
        ${x.ok ? "" : `<div class="small muted" style="margin:4px 0 8px">${esc(x.why)}</div>${x.help ? `<div class="small" style="margin-bottom:8px">${esc(x.help)}</div>` : ""}
        <div class="row" style="gap:8px;flex-wrap:wrap"><button class="btn primary sm" data-a="${x.id}">${esc(x.btn)}</button>${x.alt ? `<button class="btn sm" data-alt="${x.id}">${esc(x.alt)}</button>` : ""}${x.confirm ? `<button class="btn sm" data-c="${x.id}">${esc(x.confirm)}</button>` : ""}</div>`}</div>`).join("")}
      <p class="small muted">${esc(cap(s.manufacturer))} ${esc(s.model)} · Android ${s.android}</p>`;
    root.querySelectorAll("[data-a]").forEach(b => b.onclick = () => st.find(x => x.id === b.dataset.a).act());
    root.querySelectorAll("[data-alt]").forEach(b => b.onclick = () => st.find(x => x.id === b.dataset.alt).altAct());
    root.querySelectorAll("[data-c]").forEach(b => b.onclick = () => { st.find(x => x.id === b.dataset.c).confirmAct(); paint(); });
    reportHealth(s);
  };
  await paint(); timer = setInterval(paint, 2000);
  const ob = new MutationObserver(() => { if (!document.body.contains(m.root)) { live = false; clearInterval(timer); ob.disconnect(); refreshBanner(); } }); ob.observe(document.body, { childList: true });
}

/* ---- banner shown whenever something would stop tracking ---- */
export async function refreshBanner() {
  const s = await status(); const old = $("#trkbar");
  const p = problems(s);
  if (!s || !p.length) { old?.remove(); return; }
  const bar = old || document.createElement("div"); bar.id = "trkbar"; bar.setAttribute("role", "alert");
  bar.style.cssText = "background:#fff7e0;color:#4a3b00;border-bottom:1px solid #f0d58a;padding:8px 14px;font-size:14px;display:flex;gap:10px;align-items:center;flex-wrap:wrap";
  bar.innerHTML = `<span style="flex:1;min-width:200px"><b>Tracking may stop on this phone.</b> ${esc(p[0])}${p.length > 1 ? ` (+${p.length - 1} more)` : ""}</span><button class="btn sm primary" id="trkfix">Fix now</button>`;
  if (!old) { const main = $(".main"); main?.insertBefore(bar, main.firstChild); }
  $("#trkfix", bar).onclick = () => openWizard();
}

/** Start once after sign-in. Native app only. */
let watching = false;
export async function startHealthWatch() {
  if (!isNative() || !H() || watching) return;
  watching = true;
  if (!launchChecked) {   // was the person on duty when the app opened, with no recent location? Then the phone killed it (or rebooted).
    launchChecked = true; const s = await status();
    if (s?.onDuty && s.lastPingAgeMin != null && s.lastPingAgeMin > 12) launchReason = !s.locationServices ? "gps_off" : "app_stopped";
  }
  const s0 = await status(); reportHealth(s0); refreshBanner();
  if (problems(s0).length && get(LS.prompted) !== new Date().toDateString()) { put(LS.prompted, new Date().toDateString()); setTimeout(openWizard, 800); }   // once a day until fixed
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { tracker.ensure(); refreshBanner(); reportHealth(); } });
  setInterval(() => { if (tracker.state.active) { reportHealth(); refreshBanner(); } }, 30 * 60000);
  setTimeout(() => { launchReason = ""; }, 60 * 60000);   // a stop reason is only relevant for the first hour of the session
}
