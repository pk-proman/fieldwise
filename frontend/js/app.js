import { LOGO_FULL, LOGO_COMPACT } from "./brand.js";
import { get, post, session, setAuthLostHandler, queue } from "./api.js";
import { $, $$, esc, toast, fail, svgIcon, fmtDT, skeleton, modal, debounce } from "./ui.js";
import * as tracker from "./tracker.js";
import { isBundled, serverBase, setServer } from "./config.js";
import * as core from "./pages-core.js";
import * as crm from "./pages-crm.js";
import * as hr from "./pages-hr.js";
import * as admin from "./pages-admin.js";
import * as sales from "./pages-sales.js";
import * as ord from "./pages-orders.js";
import * as bt from "./pages-beats.js";
import * as ld from "./pages-leads.js";
import * as c2 from "./pages-crm2.js";
import * as ops from "./pages-ops.js";
import * as fld from "./pages-field.js";

export const S = { user: null, tenant: null, cleanup: null };
const ROLE = { employee: 1, manager: 2, admin: 3, owner: 4 };
const ROUTES = [
  { p: "dashboard", t: "Dashboard", i: "home", f: core.dashboard },
  { p: "attendance", t: "Attendance", i: "clock", f: core.attendance },
  { p: "team", t: "Live team", i: "map", f: core.team, min: "manager" },
  { sec: "Sales" },
  { p: "leads", t: "Leads", i: "target", f: ld.leads },
  { p: "followups", t: "Follow-ups", i: "bell", f: sales.followups },
  { p: "orders", t: "Orders", i: "box", f: ord.orders },
  { p: "invoices", t: "Invoices", i: "wallet", f: c2.invoices },
  { p: "collections", t: "Collections", i: "wallet", f: ord.collections },
  { p: "customers", t: "Customers", i: "building", f: crm.customers },
  { p: "contacts", t: "Contacts", i: "contact", f: crm.contacts },
  { p: "notes", t: "Notes", i: "contact", f: c2.notes },
  { p: "renewals", t: "Renewals", i: "cal", f: c2.renewals },
  { p: "events", t: "Trade fairs", i: "pin", f: c2.events },
  { p: "calls", t: "Call logs", i: "clock", f: c2.calls },
  { p: "dialer", t: "Auto dialer", i: "bell", f: c2.dialer },
  { p: "products", t: "Products", i: "box", f: crm.products },
  { sec: "Field work" },
  { p: "tasks", t: "Tasks", i: "check", f: crm.tasks },
  { p: "beats", t: "Beat plan", i: "route", f: bt.beats },
  { p: "meetings", t: "Meetings", i: "handshake", f: sales.meetings },
  { p: "visits", t: "Visits", i: "pin", f: crm.visits },
  { p: "routes", t: "Routes", i: "route", f: crm.routes },
  { p: "workhours", t: "Work hours", i: "clock", f: fld.workhours },
  { sec: "People" },
  { p: "roster", t: "Roster", i: "cal", f: fld.roster },
  { p: "leaves", t: "Leaves", i: "cal", f: hr.leaves },
  { p: "expenses", t: "Expenses", i: "wallet", f: hr.expenses },
  { p: "reports", t: "Reports", i: "target", f: sales.reports },
  { p: "incentives", t: "Incentives", i: "wallet", f: c2.incentives },
  { sec: "Purchase & support", min: "manager" },
  { p: "vendors", t: "Vendors", i: "building", f: ops.vendors, min: "manager" },
  { p: "purchase", t: "Purchase orders", i: "box", f: ops.purchase, min: "manager" },
  { p: "dealers", t: "Dealers", i: "handshake", f: ops.dealers, min: "manager" },
  { p: "announcements", t: "Announcements", i: "bell", f: fld.announcements },
  { p: "helpdesk", t: "Helpdesk", i: "bell", f: ops.helpdesk },
  { sec: "Admin", min: "admin" },
  { p: "settings", t: "Settings", i: "cog", f: admin.settings, min: "admin" },
  { p: "recycle", t: "Recycle bin", i: "box", f: admin.recycle, min: "manager" },
];
const MOBILE = ["dashboard", "attendance", "tasks", "leads", "more"];
const allowed = r => !r.min || ROLE[S.user.role] >= ROLE[r.min];

setAuthLostHandler(msg => { tracker.stop(); S.user = null; authScreen("login", msg); });

// ---- PWA: service worker, "update available" bar, install button -------------------------------
let installEvt = null;
function pwaBar(text, btn, onClick) {
  document.getElementById("pwa-bar")?.remove();
  const b = document.createElement("div"); b.id = "pwa-bar";
  b.style.cssText = "position:fixed;left:12px;right:12px;bottom:calc(12px + env(safe-area-inset-bottom));z-index:9999;background:#134e4a;color:#fff;padding:10px 12px;border-radius:12px;display:flex;gap:10px;align-items:center;box-shadow:0 6px 24px rgba(0,0,0,.25);font-size:14px";
  b.innerHTML = `<span style="flex:1"></span>${btn ? `<button class="btn primary" style="padding:6px 12px">${btn}</button>` : ""}<button class="btn" aria-label="Dismiss" style="padding:6px 10px">✕</button>`;
  b.firstChild.textContent = text;
  const [go, x] = btn ? [b.children[1], b.children[2]] : [null, b.children[1]];
  if (go) go.onclick = () => { onClick(); b.remove(); };
  x.onclick = () => b.remove();
  document.body.appendChild(b);
}
function setupPwa() {
  const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone;
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").then(reg => {
      const offer = w => pwaBar("A new version of Fieldwise is ready.", "Update", () => w.postMessage("skip-waiting"));
      if (reg.waiting && navigator.serviceWorker.controller) offer(reg.waiting);
      reg.addEventListener("updatefound", () => { const w = reg.installing; w?.addEventListener("statechange", () => { if (w.state === "installed" && navigator.serviceWorker.controller) offer(w); }); });
      setInterval(() => reg.update().catch(() => {}), 60 * 60 * 1000);
    }).catch(() => {});
    let reloaded = false;
    navigator.serviceWorker.addEventListener("controllerchange", () => { if (!reloaded && sessionStorage.getItem("fw-upd")) { reloaded = true; location.reload(); } });
    document.addEventListener("click", e => { if (e.target.closest?.("#pwa-bar button.primary")) sessionStorage.setItem("fw-upd", "1"); }, true);
  }
  if (standalone) return;
  window.addEventListener("beforeinstallprompt", e => {   // Android / desktop Chrome and Edge
    e.preventDefault(); installEvt = e;
    if (!sessionStorage.getItem("fw-inst-x")) setTimeout(() => pwaBar("Install Fieldwise on this device for quicker access.", "Install", async () => { installEvt.prompt(); await installEvt.userChoice; installEvt = null; }), 4000);
  });
  window.addEventListener("appinstalled", () => { installEvt = null; document.getElementById("pwa-bar")?.remove(); });
  if (/iphone|ipad/i.test(navigator.userAgent) && !localStorage.getItem("fw-ios-hint")) {   // iOS has no install prompt
    setTimeout(() => { pwaBar("To install: tap the Share icon, then “Add to Home Screen”.", null); localStorage.setItem("fw-ios-hint", "1"); }, 4000);
  }
}

async function boot() {
  if (isBundled()) {  // files are inside the app; links to server documents (PDFs) need the server address
    const fix = u => typeof u === "string" && u.startsWith("/api/") ? serverBase() + u : u; const wo = window.open; window.open = (u, ...r) => wo.call(window, fix(u), ...r);
    document.addEventListener("click", e => { const a = e.target.closest?.('a[href^="/api/"]'); if (a) a.href = serverBase() + a.getAttribute("href"); }, true);
  } else { setupPwa(); }
  if (location.hash.startsWith("#/reset")) return resetScreen();
  if (!session.token) return authScreen("login");
  try { const me = await get("/auth/me"); S.user = me.user; S.tenant = me.tenant; shell(); route(); if (me.consent_required) askConsent(me.tracking_policy); }
  catch (e) { if (e.status === 0) { document.body.insertAdjacentHTML("afterbegin", ""); S.user = JSON.parse(localStorage.getItem("fw_user") || "null"); if (S.user) { S.tenant = { name: "Offline", settings: {} }; shell(); route(); return; } } authScreen("login", e.message); }
}

export function askConsent(policy) {
  if ($("#consent-modal")) return;
  const m = modal({ title: "Before you start: location tracking notice", body: `<div id="consent-modal"><p style="white-space:pre-wrap">${esc(policy)}</p><p class="small muted">You can read this again later in Settings. Your acceptance is recorded with the date.</p></div>`, wide: true,
    actions: [{ label: "Decline and sign out", danger: true, onClick: async () => { try { await post("/auth/logout"); } catch {} tracker.stop(); session.clear(); S.user = null; authScreen("login"); } }, { label: "I understand and accept", primary: true, onClick: async () => { await post("/auth/consent"); toast("Thank you. You can now check in."); } }] });
  $("[data-x]", m.root)?.remove();  // can't be dismissed without choosing
}

function resetScreen() {
  const token = decodeURIComponent((location.hash.split("token=")[1] || "").split("&")[0]);
  $("#app").innerHTML = `<div class="auth"><form class="card" id="rf" novalidate><h1>Choose a new password</h1><p class="muted" style="margin:4px 0 16px">At least 8 characters, with letters and numbers.</p>
    <label class="f"><span>New password</span><input name="p1" type="password" required minlength="8" autocomplete="new-password"></label><label class="f"><span>Repeat it</span><input name="p2" type="password" required autocomplete="new-password"></label><button class="btn primary" style="width:100%" id="go">Save password</button></form></div>`;
  $("#rf").onsubmit = async e => { e.preventDefault(); const f = Object.fromEntries(new FormData(e.target)); if (f.p1 !== f.p2) return toast("The two passwords don't match.", "err"); $("#go").disabled = true;
    try { await post("/auth/reset", { token, new_password: f.p1 }); toast("Password changed. Please sign in."); location.hash = ""; authScreen("login"); } catch (err) { fail(err); $("#go").disabled = false; } };
}

function forgotModal() {
  modal({ title: "Reset your password", body: `<p class="muted">We'll email a link that works for 60 minutes.</p><label class="f"><span>Workspace</span><input id="fw" value="${esc(session.workspace)}"></label><label class="f"><span>Email</span><input id="fe" type="email"></label>`,
    actions: [{ label: "Cancel", close: true }, { label: "Send reset link", primary: true, onClick: async el => { await post("/auth/forgot", { workspace: $("#fw", el).value, email: $("#fe", el).value }); toast("If that account exists, a reset link is on its way."); } }] });
}

function authScreen(mode = "login", msg = "") {
  const signup = mode === "signup";
  $("#app").innerHTML = `<div class="auth"><form class="card" id="authf" novalidate>
    <div class="authlogo">${LOGO_FULL}</div>
    <h1>${signup ? "Create your workspace" : "Sign in"}</h1><p class="muted" style="margin:4px 0 16px">${signup ? "14-day trial · up to 10 users · no card needed" : "Field force, CRM and ERPNext in one place."}</p>
    ${msg ? `<div class="chip bad" style="display:block;margin-bottom:12px;padding:8px 10px;border-radius:8px">${esc(msg)}</div>` : ""}
    ${signup ? `<label class="f"><span>Company name</span><input name="company" required autocomplete="organization"></label><label class="f"><span>Your name</span><input name="name" required autocomplete="name"></label>
      <label class="f"><span>Country</span><select name="country"><option>India</option><option>UAE</option><option>Saudi Arabia</option><option>Qatar</option><option>Oman</option><option>Kuwait</option><option>Bahrain</option></select></label>`
      : `${isBundled() ? `<label class="f"><span>Company server</span><input name="server" required inputmode="url" autocapitalize="none" placeholder="fieldwise.yourcompany.com" value="${esc(serverBase())}"><span class="hint">Ask your administrator for this address. You only enter it once.</span></label>` : ""}<label class="f"><span>Workspace</span><input name="workspace" required autocomplete="organization" placeholder="e.g. proman" value="${esc(session.workspace)}"><span class="hint">The short name you chose at sign-up.</span></label>`}
    <label class="f"><span>Email</span><input name="email" type="email" required autocomplete="username"></label>
    <label class="f"><span>Password</span><input name="password" type="password" required minlength="8" autocomplete="${signup ? "new-password" : "current-password"}"></label>
    <button class="btn primary" style="width:100%" id="go">${signup ? "Create workspace" : "Sign in"}</button>
    ${isBundled() && !signup ? '<span id="sw"></span>' : `<p class="small muted" style="margin-top:14px;text-align:center">${signup ? 'Have a workspace? <a href="#" id="sw">Sign in</a>' : 'New company? <a href="#" id="sw">Create a workspace</a>'}</p>`}
    ${signup ? "" : `<p class="small" style="margin:-4px 0 12px"><a href="#" id="fg">Forgot your password?</a></p>`}
    ${signup ? "" : `<label class="f" id="otpw" style="display:none;margin-top:10px"><span>Authenticator code</span><input name="otp" inputmode="numeric" maxlength="6" autocomplete="one-time-code" placeholder="6-digit code"></label>`}
    ${signup ? "" : `<p class="small muted" style="text-align:center">Signing in here signs you out on any other device.</p>`}</form></div>`;
  $("#fg")?.addEventListener("click", e => { e.preventDefault(); forgotModal(); });
  $("#sw").onclick = e => { e.preventDefault(); authScreen(signup ? "login" : "signup"); };
  $("#authf").onsubmit = async e => {
    e.preventDefault(); const f = Object.fromEntries(new FormData(e.target)); const b = $("#go"); b.disabled = true;
    if (isBundled()) { if (f.server) setServer(f.server); delete f.server; if (!serverBase()) { toast("Enter your company's Fieldwise address.", "err"); b.disabled = false; return; } }
    try {
      const r = signup ? await post("/auth/signup", f) : await post("/auth/login", { ...f, device: navigator.userAgent.slice(0, 120) });
      session.set(r.token, r.user, r.workspace); await boot(); if (r.must_enroll_mfa) enrollMfa(true);
    } catch (err) { if (err.status === 428) { const w = $("#otpw"); w.style.display = "block"; $("input", w).focus(); toast("Enter the code from your authenticator app."); } else fail(err); b.disabled = false; }
  };
}

function shell() {
  const nav = ROUTES.filter(r => r.sec ? (!r.min || ROLE[S.user.role] >= ROLE[r.min]) : allowed(r));
  $("#app").innerHTML = `<div class="shell"><aside class="side"><div class="brand sidelogo" aria-label="Fieldwise">${LOGO_COMPACT}</div>
    <nav class="nav" aria-label="Main">${nav.map(r => r.sec ? `<div class="sec">${r.sec}</div>` : `<a href="#/${r.p}" data-p="${r.p}">${svgIcon(r.i)}<span>${esc(r.t)}</span></a>`).join("")}</nav></aside>
    <div class="main"><header class="top"><span class="org">${esc(S.tenant.name)}</span><span class="net" id="net">● Online</span><span id="qn" class="small muted"></span>
      <div style="position:relative;flex:1;max-width:380px"><input id="gs" type="search" placeholder="Search leads, customers, mobile, email…" aria-label="Global search" autocomplete="off" style="min-height:34px"><div id="gsr" class="card" style="display:none;position:absolute;left:0;right:0;top:40px;max-height:60vh;overflow:auto;padding:6px;z-index:60"></div></div>
      <button class="btn icon right" id="bell" aria-label="Notifications">${svgIcon("bell").replace("<svg", '<svg style="width:18px;height:18px;stroke:currentColor;fill:none;stroke-width:1.8"')}</button><span id="nb"></span>
      <button class="btn sm" id="me" aria-haspopup="menu">${esc(S.user.name.split(" ")[0])} ▾</button></header>
      <main class="content" id="view" tabindex="-1"></main></div></div>
    <nav class="mobilebar" aria-label="Primary">${MOBILE.map(p => p === "more" ? `<a href="#/more" data-p="more"><svg viewBox="0 0 24 24"><circle cx="5" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="19" cy="12" r="1.5"/></svg>More</a>` : (r => `<a href="#/${p}" data-p="${p}">${svgIcon(r.i)}${esc(r.t)}</a>`)(ROUTES.find(x => x.p === p))).join("")}</nav>`;
  $("#me").onclick = userMenu; $("#bell").onclick = showNotifs;
  const gs = $("#gs"), gr = $("#gsr"); const route_ = { leads: "leads", customers: "customers", contacts: "contacts", quotations: "leads", orders: "orders" };
  const doSearch = debounce(async () => { const q = gs.value.trim(); if (q.length < 2) { gr.style.display = "none"; return; } try { const r = await get("/search?q=" + encodeURIComponent(q)); const groups = Object.entries(r).filter(([, v]) => v.length);
    gr.innerHTML = groups.length ? groups.map(([k, v]) => `<div class="small muted" style="padding:6px 8px;text-transform:uppercase;letter-spacing:.05em">${k}</div>` + v.map(x => `<a href="#/${route_[k]}" data-go style="display:block;padding:6px 8px;border-radius:6px;color:inherit"><b>${esc(x.title)}</b> <span class="small muted">${esc(x.sub || "")}${x.status ? " · " + esc(x.status) : ""}</span></a>`).join("")).join("") : `<div class="empty" style="padding:14px">No matches for “${esc(q)}”.</div>`; gr.style.display = "block"; } catch {} }, 250);
  gs.oninput = doSearch; gs.onkeydown = e => { if (e.key === "Escape") { gr.style.display = "none"; gs.blur(); } }; document.addEventListener("click", e => { if (!e.target.closest("#gsr,#gs")) gr.style.display = "none"; else if (e.target.closest("[data-go]")) setTimeout(() => { gr.style.display = "none"; gs.value = ""; }, 0); });
  const net = () => { const n = $("#net"); if (!n) return; n.className = "net" + (navigator.onLine ? "" : " off"); n.textContent = navigator.onLine ? "● Online" : "● Offline — saving locally"; const q = queue.size(); $("#qn").textContent = q ? `${q} item${q > 1 ? "s" : ""} waiting to sync` : ""; };
  window.addEventListener("online", net); window.addEventListener("offline", net); setInterval(net, 5000); net();
  pollNotifs(); setInterval(pollNotifs, 60000);
  get("/attendance/today").then(r => { if (r.attendance?.check_in_at && !r.attendance.check_out_at) tracker.start(); }).catch(() => {});
}

async function pollNotifs() { try { const n = await get("/notifications"); $("#nb").innerHTML = n.unread ? `<span class="badge">${n.unread}</span>` : ""; } catch {} }
async function showNotifs() {
  const old = $("#pop"); if (old) return old.remove();
  const pop = document.createElement("div"); pop.id = "pop"; pop.className = "card pop"; pop.style.padding = "0"; pop.innerHTML = `<div style="padding:12px">${skeleton(3)}</div>`; document.body.appendChild(pop);
  const close = e => { if (!pop.contains(e.target) && e.target.id !== "bell") { pop.remove(); document.removeEventListener("mousedown", close); } }; setTimeout(() => document.addEventListener("mousedown", close));
  try {
    const n = await get("/notifications");
    pop.innerHTML = `<div class="row" style="padding:10px 12px;border-bottom:1px solid var(--border)"><h3>Notifications</h3><button class="btn sm right" id="mr">Mark all read</button></div>` +
      (n.items.length ? n.items.map(i => `<div class="notif ${i.read ? "" : "unread"}"><strong>${esc(i.title)}</strong><div class="small">${esc(i.body)}</div><div class="small muted">${fmtDT(i.created_at)}</div></div>`).join("") : `<div class="empty"><h3>You're all caught up</h3>Reminders and approvals show up here.</div>`);
    const mr = $("#mr", pop); if (mr) mr.onclick = async () => { await post("/notifications/read"); pollNotifs(); pop.remove(); };
  } catch (e) { pop.remove(); fail(e); }
}
function userMenu() {
  const old = $("#pop"); if (old) return old.remove();
  const pop = document.createElement("div"); pop.id = "pop"; pop.className = "card pop"; pop.style.width = "240px";
  pop.innerHTML = `<div style="margin-bottom:8px"><strong>${esc(S.user.name)}</strong><div class="small muted">${esc(S.user.email)} · ${esc(S.user.role)}</div></div>
   <button class="btn" style="width:100%;margin-bottom:6px" id="th">Toggle dark / light</button><button class="btn" style="width:100%;margin-bottom:6px" id="fe">Enrol face for check-in</button><button class="btn" style="width:100%;margin-bottom:6px" id="mf">${S.user.mfa_enabled ? "Authenticator: on" : "Turn on authenticator (MFA)"}</button><button class="btn danger" style="width:100%" id="lo">Sign out</button>`;
  document.body.appendChild(pop);
  const close = e => { if (!pop.contains(e.target) && e.target.id !== "me") { pop.remove(); document.removeEventListener("mousedown", close); } }; setTimeout(() => document.addEventListener("mousedown", close));
  $("#th", pop).onclick = () => { const r = document.documentElement, dark = r.dataset.theme === "dark" || (!r.dataset.theme && matchMedia("(prefers-color-scheme:dark)").matches); r.dataset.theme = dark ? "light" : "dark"; };
  $("#lo", pop).onclick = async () => { try { await post("/auth/logout"); } catch {} tracker.stop(); session.clear(); S.user = null; authScreen("login"); };
  $("#mf", pop).onclick = () => { pop.remove(); enrollMfa(false); };
  $("#fe", pop).onclick = async () => { pop.remove(); try { const { captureSelfie } = await import("./ui.js"); const img = await captureSelfie(); await post("/auth/face-enroll", { image: img }); toast("Face enrolled."); } catch (e) { fail(e); } };
}

async function enrollMfa(forced) {
  if (S.user.mfa_enabled && !forced) return toast("Authenticator is already on for your account.");
  try { const r = await post("/auth/mfa/setup"); const body = document.createElement("div");
    body.innerHTML = `<p>${forced ? "<b>Your administrator requires an authenticator app.</b> " : ""}Open Google Authenticator, Microsoft Authenticator or similar, add an account using this key, then enter the 6-digit code it shows.</p><div class="card" style="padding:10px;word-break:break-all"><div class="small muted">Setup key</div><code style="font-size:15px">${esc(r.secret)}</code><div class="small" style="margin-top:6px"><a href="${esc(r.uri)}">Open in authenticator app</a></div></div><label class="f" style="margin-top:12px"><span>Code from the app</span><input id="mc" inputmode="numeric" maxlength="6"></label>`;
    modal({ title: "Set up authenticator", body, actions: [{ label: forced ? "Later" : "Cancel", close: true }, { label: "Turn on", primary: true, onClick: async () => { await post("/auth/mfa/enable", { code: $("#mc", body).value }); S.user.mfa_enabled = true; toast("Authenticator is on. You'll be asked for a code when you sign in."); } }] }); } catch (e) { fail(e); } }

function moreScreen(root) {
  root.innerHTML = `<div class="page-h"><h1>More</h1></div><div class="card"><div class="nav">${ROUTES.filter(r => r.p && allowed(r) && !MOBILE.includes(r.p)).map(r => `<a href="#/${r.p}">${svgIcon(r.i)}<span>${esc(r.t)}</span></a>`).join("")}</div></div>`;
}

export function route() {
  const p = (location.hash.replace(/^#\//, "") || "dashboard").split("?")[0].split("/")[0];
  const root = $("#view"); if (!root || !S.user) return;
  S.cleanup?.(); S.cleanup = null;
  $$(".nav a,.mobilebar a").forEach(a => a.classList.toggle("on", a.dataset.p === p));
  if (p === "more") return moreScreen(root);
  const r = ROUTES.find(x => x.p === p && allowed(x)) || ROUTES[0];
  document.title = `${r.t} · Fieldwise`;
  root.innerHTML = skeleton(5);
  Promise.resolve(r.f(root, S)).then(c => { if (typeof c === "function") S.cleanup = c; }).catch(e => { root.innerHTML = `<div class="empty"><h3>Couldn't load this page</h3><p>${esc(e.message)}</p><button class="btn" data-reload>Try again</button></div>`; });
  root.focus({ preventScroll: true });
  root.addEventListener("click", e => { if (e.target.closest("[data-reload]")) location.reload(); });
}
window.addEventListener("hashchange", route);
boot();
