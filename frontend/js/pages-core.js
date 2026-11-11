import { get, post, qs, queue, session, fileUrl } from "./api.js";
import { $, $$, esc, toast, fail, money, fmtT, fmtD, fmtDT, today, table, wireTable, chip, statusKind, makeMap, pinIcon, here, captureSelfie, download, modal, skeleton } from "./ui.js";
import * as tracker from "./tracker.js";
import { wsBase } from "./config.js";
import { biometricAvailable, verifyBiometric } from "./native.js";

const label = (S, k, d) => S.tenant.settings?.labels?.[k] || d;
const header = (S, key, d) => S.tenant.settings?.page_headers?.[key] || d;

// ----------------------------------------------------------------- Dashboard
export async function dashboard(root, S) {
  const d = await get("/reports/dashboard");
  const max = Math.max(1, ...d.trend.map(t => t.present)), pmax = Math.max(1, ...d.pipeline.map(p => p.count));
  const order = ["new", "contacted", "qualified", "proposal", "won", "lost"];
  const pipe = order.map(s => d.pipeline.find(p => p.status === s) || { status: s, count: 0, value: 0 });
  const isMgr = ["manager", "admin", "owner"].includes(S.user.role);
  root.innerHTML = `<div class="page-h"><div><h1>${esc(header(S, "dashboard", "Good " + (new Date().getHours() < 12 ? "morning" : new Date().getHours() < 17 ? "afternoon" : "evening") + ", " + S.user.name.split(" ")[0]))}</h1>
    <div class="muted small">${new Date().toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" })}${isMgr ? " · your team" : ""}</div></div></div>
    <div id="ann"></div><div class="grid g4 kpis" style="margin-bottom:14px">
      <div class="card kpi"><div class="v">${d.present}<span class="muted" style="font-size:15px"> / ${d.headcount}</span></div><div class="l">Present today${d.late ? ` · ${d.late} late` : ""}</div></div>
      <div class="card kpi"><div class="v">${d.checked_in_now}</div><div class="l">In the field now · ${d.km_today} km today</div></div>
      <div class="card kpi"><div class="v">${d.visits_today}</div><div class="l">Customer visits today</div></div>
      <div class="card kpi"><div class="v">${d.overdue_tasks}</div><div class="l">Overdue tasks · ${d.open_tasks} open</div></div></div>
    <div class="grid g2"><div class="card"><h2>Attendance — last 7 days</h2><div class="bars" style="margin-bottom:26px">${d.trend.map(t => `<div style="height:${Math.max(3, t.present / max * 100)}%" title="${t.present} present"><span>${new Date(t.day + "T00:00").toLocaleDateString([], { weekday: "short" })}</span></div>`).join("")}</div></div>
      <div class="card"><h2>Sales pipeline</h2><div class="funnel">${pipe.map(p => `<div><span style="width:78px" class="small muted">${p.status}</span><div class="bar" style="width:${p.count / pmax * 55}%"></div><span class="small mono">${p.count} · ${money(p.value, d.currency)}</span></div>`).join("")}</div></div></div>
    <div id="ov" style="margin-top:14px"></div>
    ${isMgr ? `<div class="grid g3" style="margin-top:14px"><a class="card kpi" href="#/leaves"><div class="v">${d.pending_leaves}</div><div class="l">Leave requests waiting for you</div></a>
      <a class="card kpi" href="#/expenses"><div class="v">${d.pending_expenses}</div><div class="l">Expense claims · ${money(d.pending_expense_amount, d.currency)}</div></a>
      <a class="card kpi" href="#/team"><div class="v">${d.on_leave}</div><div class="l">On leave today · see live map</div></a></div>` : ""}`;
  overviewInto($("#ov"), S).catch(e => { $("#ov").innerHTML = ""; });
  get("/announcements").then(a => { const u = a.filter(x => x.unread).slice(0, 2); if (u.length && $("#ann")) $("#ann").innerHTML = u.map(x => `<div class="card" style="margin-bottom:10px;border-color:var(--accent)"><b>📣 ${esc(x.title)}</b> <span class="small muted">${esc(x.author)}</span><div class="small">${esc((x.body || "").slice(0, 220))}</div><a class="small" href="#/announcements">Read more →</a></div>`).join(""); }).catch(() => {});
}

// ----------------------------------------------------------------- Attendance
export async function attendance(root, S) {
  const st = S.tenant.settings, ci = label(S, "check_in", "Check In"), co = label(S, "check_out", "Check Out");
  const [today_, branches] = await Promise.all([get("/attendance/today"), get("/branches")]);
  let a = today_.attendance;
  root.innerHTML = `<div class="page-h"><h1>${esc(header(S, "attendance", "Attendance"))}</h1></div>
    <div class="grid g2"><div class="card punch"><div id="status"></div><button class="btn primary big" id="punch"></button>
      <label class="f" style="margin:0"><span><input type="checkbox" id="face" ${st.require_face ? "checked disabled" : ""}> Verify with face scan ${st.require_face ? "(required)" : ""}</span></label>
      <div id="gps" class="small muted"></div></div>
      <div class="card"><h2>Where you are</h2><div id="map" class="map" style="height:260px"></div></div></div>
    <div class="row" style="margin:20px 0 10px"><h2>History</h2><button class="btn sm right" id="csv">Export CSV</button></div><div id="hist">${skeleton(3)}</div>`;
  const map = makeMap($("#map"), st.map_style, [branches[0]?.lat || 20.5, branches[0]?.lng || 78.9], branches.length ? 15 : 5);
  if (map) branches.forEach(b => { L.marker([b.lat, b.lng], { icon: pinIcon("#0f766e", "B") }).addTo(map).bindPopup(`<b>${esc(b.name)}</b><br>${b.radius_m} m geo-fence`); L.circle([b.lat, b.lng], { radius: b.radius_m, color: "#0f766e", fillOpacity: .1 }).addTo(map); });
  let me;
  const paint = () => {
    const btn = $("#punch"), out = a?.check_in_at && !a?.check_out_at, done = a?.check_out_at;
    btn.textContent = done ? "Day complete ✓" : out ? co : ci; btn.disabled = !!done || a?.status === "on_leave"; btn.classList.toggle("danger", !!out); btn.classList.toggle("primary", !out);
    $("#status").innerHTML = a?.status === "on_leave" ? `<h2>On approved leave today</h2>` : !a?.check_in_at ? `<h2>You haven't marked attendance yet</h2><div class="muted">Allow location and tap the button when you reach your site.</div>` :
      `<h2>${done ? "Worked " + ((new Date(a.check_out_at) - new Date(a.check_in_at)) / 36e5).toFixed(1) + " h · " + a.total_distance_km + " km" : "Checked in at " + fmtT(a.check_in_at)}</h2>
      <div>${chip(a.status.replace("_", " "), statusKind(a.status))} ${a.check_in_method ? chip(a.check_in_method) : ""} ${a.flagged ? chip("⚑ " + a.flagged, "warn") : ""}</div>`;
  };
  paint();
  const updGps = () => { const f = tracker.state.lastFix; $("#gps").textContent = tracker.state.active ? (f ? `Tracking on · last fix ${fmtT(f.ts)} · ${f.battery ?? "?"}% battery` : "Tracking on · waiting for GPS…") : ""; };
  tracker.state.listeners.add(updGps); updGps();
  here().then(p => { if (!map) return; map.setView([p.lat, p.lng], 16); me = L.circleMarker([p.lat, p.lng], { radius: 8, color: "#fff", weight: 2, fillColor: "#175cd3", fillOpacity: 1 }).addTo(map); }).catch(e => { $("#gps").textContent = e.message; });
  $("#punch").onclick = async e => {
    const btn = e.currentTarget, kind = a?.check_in_at && !a?.check_out_at ? "check-out" : "check-in"; btn.disabled = true;
    try {
      const p = await here().catch(err => { throw err; });
      const body = { lat: p.lat, lng: p.lng, accuracy: p.accuracy, mock: false };
      if (await biometricAvailable()) { if (!(await verifyBiometric())) throw new Error("Fingerprint / face unlock didn't match. Try again."); body.device_biometric = true; }
      if ($("#face").checked) body.selfie = await captureSelfie();
      if (!navigator.onLine) { body.client_ts = new Date().toISOString().slice(0, 19); queue.addPunch(kind, body); toast("Offline — saved and will sync when you reconnect."); if (kind === "check-in") { a = { check_in_at: new Date().toISOString(), status: "present", check_in_method: "offline" }; tracker.start(); } else { a = { ...a, check_out_at: new Date().toISOString(), total_distance_km: 0 }; tracker.stop(); } paint(); return; }
      let r; try { r = await post("/attendance/" + kind, body); }
      catch (err) { if (err.status === 403 && /geo|m from|reason/i.test(err.message) && /override/i.test(err.message)) { const reason = prompt(err.message + "\n\nEnter a reason to request an override:"); if (!reason) throw err; r = await post("/attendance/" + kind, { ...body, override_reason: reason }); } else throw err; }
      a = r; toast(kind === "check-in" ? "Checked in. Have a productive day!" : "Checked out. See you tomorrow."); kind === "check-in" ? tracker.start() : tracker.stop(); paint(); loadHist();
    } catch (err) { if (err.status === 428 && /CONSENT/.test(err.message)) { const { askConsent } = await import("./app.js"); const me = await get("/auth/me"); askConsent(me.tracking_policy); } else fail(err); } finally { paint(); }
  };
  const loadHist = async () => {
    const rows = await get("/attendance" + qs({ user_id: S.user.id }));
    $("#hist").innerHTML = table([{ h: "Date", f: r => fmtD(r.day) }, { h: "In", f: r => fmtT(r.check_in_at) }, { h: "Out", f: r => fmtT(r.check_out_at) }, { h: "Distance", f: r => r.total_distance_km + " km" },
      { h: "Status", f: r => chip(r.status.replace("_", " "), statusKind(r.status)) }, { h: "Flags", f: r => r.flagged ? `<span class="small">${esc(r.flagged)}</span>` : "—" }], rows, { empty: "No attendance yet." });
  };
  $("#csv").onclick = async () => { const r = await get("/attendance" + qs({ fmt: "csv", user_id: S.user.id })); download("attendance.csv", r); };
  loadHist().catch(fail);
  return () => { tracker.state.listeners.delete(updGps); map?.remove(); };
}

// ----------------------------------------------------------------- Phone health (can each person's phone keep tracking alive?)
async function phoneHealth() {
  const body = document.createElement("div"); body.innerHTML = skeleton(4);
  const m = modal({ title: "Phone health", wide: true, body, actions: [{ label: "Close", close: true }] });
  try {
    const rows = await get("/tracking/health");
    const bad = rows.filter(r => r.problems.length).length, none = rows.filter(r => !r.reported).length;
    body.innerHTML = `<p class="small muted" style="margin-top:0">${rows.length} people · <b>${bad}</b> with a problem that can stop tracking · ${none} have not opened the Android app yet. Reports arrive when the app is opened and every 30 minutes while on duty.</p>` +
      (rows.length ? `<div class="tablewrap"><table><thead><tr><th>Person</th><th>Phone</th><th>App</th><th>Last location</th><th>Status</th></tr></thead><tbody>${rows.map(r => `<tr>
        <td><b>${esc(r.name)}</b>${r.on_duty ? ' <span class="chip ok">on duty</span>' : ""}</td><td>${r.device ? esc(r.device) + (r.android ? ` <span class="small muted">Android ${r.android}</span>` : "") : '<span class="muted">—</span>'}</td>
        <td>${esc(r.app_version || "—")}</td><td>${r.last_ping_min == null ? "—" : r.last_ping_min < 1 ? "just now" : r.last_ping_min + " min ago"}</td>
        <td>${!r.reported ? '<span class="chip">no report</span>' : r.problems.length ? r.problems.map(p => `<div><span class="chip bad">${esc(p.text)}</span><div class="small muted">${esc(p.fix)}</div></div>`).join("") : '<span class="chip ok">All good</span>'}</td></tr>`).join("")}</tbody></table></div>` : '<div class="empty">No team members yet.</div>');
  } catch (e) { m.close(); fail(e); }
}

// ----------------------------------------------------------------- Live team
const PALETTE = ["#175cd3", "#d92d20", "#067647", "#b54708", "#7a2fd4", "#0e9384", "#c11574", "#4d5565"];
export async function team(root, S) {
  root.innerHTML = `<div class="page-h"><h1>${esc(header(S, "team", "Live team"))}</h1><span class="chip" id="wsst">connecting…</span><label class="row small right"><input type="checkbox" id="showr" checked> Show routes since last check-in</label>${["manager", "admin", "owner"].includes(S.user.role) ? '<button class="btn sm" id="phh">Phone health</button>' : ""}</div>
    <div class="grid" style="grid-template-columns:minmax(0,2fr) minmax(280px,1fr)"><div class="card" style="padding:8px"><div id="map" class="map tall"></div></div><div class="card"><h2>Team</h2><div id="list" class="live-list">${skeleton(4)}</div><div id="detail" style="margin-top:12px"></div></div></div>`;
  $("#phh") && ($("#phh").onclick = phoneHealth);
  const map = makeMap($("#map"), S.tenant.settings.map_style, [20.5, 78.9], 5), markers = {}, lines = {}, extra = L.layerGroup().addTo(map || L.layerGroup()); let people = {}, routes = {}, selected = null, fitted = false;
  const col = uid => PALETTE[uid % PALETTE.length], stateCol = s => ({ live: "#067647", idle: "#b54708", offline: "#b42318", no_gps: "#175cd3", checked_out: "#98a2b3" }[s] || "#667085");
  const paintList = () => { const ps = Object.values(people).sort((a, b) => a.name.localeCompare(b.name));
    $("#list").innerHTML = ps.length ? ps.map(p => `<div class="live-item" data-u="${p.user_id}" style="${selected === p.user_id ? "background:var(--accent-soft)" : ""}"><span class="dot ${p.state}"></span><div class="grow"><strong>${esc(p.name)}</strong> <span style="display:inline-block;width:14px;height:4px;border-radius:2px;background:${col(p.user_id)};vertical-align:middle"></span>
      <div class="small muted">${p.state === "no_gps" ? `Checked in ${fmtT(p.check_in_at)} · no live GPS yet` : p.state === "checked_out" ? "Checked out" : `${p.minutes_ago < 1 ? "just now" : p.minutes_ago + " min ago"} · 🔋${p.battery ?? "?"}% · ${p.network}${p.gps_on ? "" : " · <b style='color:var(--danger)'>GPS off</b>"}`}${routes[p.user_id] ? ` · ${routes[p.user_id].stats.distance_km} km` : ""}</div></div></div>`).join("") : `<div class="empty"><h3>No one has checked in today</h3>People appear here as soon as they check in.</div>`;
    $$(".live-item").forEach(el => el.onclick = () => select(+el.dataset.u)); };
  const place = p => { if (!map || p.lat == null) return; markers[p.user_id]?.remove(); markers[p.user_id] = L.marker([p.lat, p.lng], { icon: pinIcon(stateCol(p.state), p.name[0]) }).addTo(map).bindPopup(`<b>${esc(p.name)}</b><br>${p.state === "no_gps" ? "Check-in location" : p.minutes_ago + " min ago"}`); };
  const drawRoutes = () => { if (!map) return; Object.values(lines).forEach(l => l.remove()); for (const k in lines) delete lines[k];
    if (!$("#showr").checked) return;
    for (const [uid, r] of Object.entries(routes)) { if (r.points.length > 1) lines[uid] = L.polyline(r.points, { color: col(+uid), weight: selected && selected != uid ? 2 : 4, opacity: selected && selected != uid ? .3 : .85 }).addTo(map); } };
  const load = async () => { const [ls, rs] = await Promise.all([get("/tracking/live"), get("/tracking/live-routes")]); people = Object.fromEntries(ls.map(p => [p.user_id, p])); routes = rs; Object.values(people).forEach(place); drawRoutes(); paintList();
    if (!fitted) { const pts = ls.filter(p => p.lat != null).map(p => [p.lat, p.lng]); if (pts.length && map) { map.fitBounds(pts, { padding: [40, 40], maxZoom: 15 }); fitted = true; } } };
  async function select(uid) {
    selected = uid; extra.clearLayers(); drawRoutes(); paintList(); const p = people[uid], r = routes[uid]; if (r?.points.length > 1 && map) map.fitBounds(r.points, { padding: [40, 40], maxZoom: 16 }); else if (p?.lat != null && map) map.setView([p.lat, p.lng], 15);
    $("#detail").innerHTML = `<h3>${esc(p?.name || "")}</h3><div id="dst" class="small" style="margin:6px 0">${r ? statsHTML(r.stats, `Since check-in at ${fmtT(r.check_in_at)}`) : "No session today."}</div><label class="f" style="margin-bottom:6px"><span>Previous check-ins</span><select id="ses"><option value="">Current session</option></select></label><div id="sdet" class="small"></div><button class="btn sm" id="clr">Clear selection</button>`;
    $("#clr").onclick = () => { selected = null; extra.clearLayers(); $("#detail").innerHTML = ""; drawRoutes(); paintList(); };
    try { const ss = await get("/tracking/sessions" + qs({ user_id: uid })); $("#ses").innerHTML += ss.map(a => `<option value="${a.id}">${fmtD(a.day)} · ${fmtT(a.check_in_at)}–${a.check_out_at ? fmtT(a.check_out_at) + " · " + a.distance_km + " km" : "in progress"}${a.flagged ? " ⚑" : ""}</option>`).join(""); } catch (e) { fail(e); }
    $("#ses").onchange = async e => { extra.clearLayers(); if (!e.target.value) { $("#sdet").innerHTML = ""; drawRoutes(); return select(uid); } await showSession(+e.target.value, uid); };
  }
  const statsHTML = (s, title) => `<b>${title}</b><br>Distance <b>${s.distance_km} km</b> · ${s.points} GPS points<br>GPS off <b>${s.gps_off_min} min</b> · Internet off <b>${s.internet_off_min} min</b><br>Lowest battery <b>${s.min_battery ?? "?"}%</b>${s.mock_points ? ` · <span style="color:var(--danger)">${s.mock_points} suspicious points</span>` : ""}`;
  async function showSession(aid, uid) {
    const d = await get("/tracking/session/" + aid); Object.values(lines).forEach(l => l.setStyle({ opacity: .12 })); extra.clearLayers(); const pts = d.points.map(p => [p.lat, p.lng]), c = col(uid);
    if (pts.length > 1) L.polyline(pts, { color: c, weight: 5 }).addTo(extra).bindTooltip("Route");
    if (d.attendance.in[0] != null) L.circleMarker(d.attendance.in, { radius: 8, color: "#fff", weight: 2, fillColor: "#067647", fillOpacity: 1 }).addTo(extra).bindPopup(`Checked in ${fmtDT(d.attendance.check_in_at)}`);
    if (d.attendance.out) L.circleMarker(d.attendance.out, { radius: 8, color: "#fff", weight: 2, fillColor: "#b42318", fillOpacity: 1 }).addTo(extra).bindPopup(`Checked out ${fmtDT(d.attendance.check_out_at)}`);
    d.visits.forEach((v, i) => v.lat != null && L.marker([v.lat, v.lng], { icon: pinIcon("#0f766e", i + 1) }).addTo(extra).bindPopup(`${esc(v.customer || "Visit")}<br>${fmtT(v.started_at)}${v.ended_at ? "–" + fmtT(v.ended_at) : ""}`));
    const all = [...pts, ...(d.attendance.in[0] != null ? [d.attendance.in] : [])]; if (all.length && map) map.fitBounds(all, { padding: [40, 40], maxZoom: 16 });
    $("#sdet").innerHTML = statsHTML(d.stats, `${fmtDT(d.attendance.check_in_at)} → ${d.attendance.check_out_at ? fmtDT(d.attendance.check_out_at) : "still open"}`) + (d.visits.length ? `<br>${d.visits.length} customer visit(s)` : "") + (d.attendance.flagged ? `<br><span style="color:var(--warn)">⚑ ${esc(d.attendance.flagged)}</span>` : "") + (pts.length ? "" : "<br><i>No GPS breadcrumbs were recorded for this check-in.</i>");
    const an = d.analysis; if (an) { const hm = m => `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`; $("#sdet").insertAdjacentHTML("beforeend", `<div style="margin-top:6px"><b>Time split:</b> on site ${hm(an.on_site_min)} · travelling ${hm(an.travel_min)} · idle ${hm(an.idle_min)} · no signal ${hm(an.gap_min)}${an.overtime_min ? ` · overtime ${hm(an.overtime_min)}` : ""}</div>${an.anomalies.length ? `<div style="color:var(--warn)">⚑ ${an.anomalies.map(a => esc(a.detail)).join("; ")}</div>` : ""}`); an.stops.forEach(s => L.marker([s.lat, s.lng], { icon: pinIcon(s.kind === "customer" ? "#067647" : "#b54708", "•") }).addTo(extra).bindPopup(`${esc(s.place || "Stop")}<br>${s.minutes} min`)); }
  }
  $("#showr").onchange = drawRoutes;
  await load();
  const iv = setInterval(() => load().catch(() => {}), 60000);
  let ws; try { ws = new WebSocket(wsBase() + "/api/ws/live?token=" + encodeURIComponent(session.token));
    ws.onopen = () => { const s = $("#wsst"); if (s) { s.textContent = "● live"; s.className = "chip ok"; } }; ws.onclose = () => { const s = $("#wsst"); if (s) { s.textContent = "reconnect on refresh"; s.className = "chip warn"; } };
    ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.type !== "ping" || m.lat == null) return; people[m.user_id] = { ...(people[m.user_id] || {}), ...m, state: "live", minutes_ago: 0 }; place(people[m.user_id]);
      const r = routes[m.user_id]; if (r) { r.points.push([m.lat, m.lng]); if (lines[m.user_id]) lines[m.user_id].addLatLng([m.lat, m.lng]); else drawRoutes(); } paintList(); }; } catch {}
  return () => { clearInterval(iv); ws?.close(); map?.remove(); };
}


// ----------------------------------------------------------------- dashboard overview (month-on-month, by user / manager)
export async function overviewInto(el, S, params = {}) {
  const isMgr = ["manager", "admin", "owner"].includes(S.user.role);
  const o = await get("/reports/overview" + qs(params)), cur = o.currency, pct = c => c == null ? "" : `<span style="color:${c >= 0 ? "var(--ok)" : "var(--danger)"}">${c >= 0 ? "▲" : "▼"} ${Math.abs(c)}%</span>`;
  const chart = (key, color, fmt) => { const mx = Math.max(1, ...o.series.map(s => s[key])); return `<div class="bars" style="margin-bottom:26px">${o.series.map(s => `<div style="height:${Math.max(3, s[key] / mx * 100)}%;background:${color}" title="${s.label}: ${fmt(s[key])}"><span>${s.label.slice(0, 3)}</span></div>`).join("")}</div>`; };
  const cmp = [["new_leads", "New leads", v => v], ["won_value", "Revenue won", v => money(v, cur)], ["order_value", "Order value", v => money(v, cur)], ["new_customers", "New customers", v => v], ["collections", "Collections", v => money(v, cur)]];
  const tot = Math.max(1, o.opportunity.open + o.opportunity.won + o.opportunity.lost);
  el.innerHTML = `<div class="row" style="margin-bottom:10px"><h2>Performance</h2>${isMgr ? `<div class="right row"><details id="udd" style="position:relative"><summary class="btn sm">User's dashboard ▾</summary><div class="card" style="position:absolute;right:0;z-index:30;min-width:220px;max-height:300px;overflow:auto;padding:8px">${o.users.map(u => `<label class="small" style="display:block;padding:3px 0"><input type="checkbox" class="uu" value="${u.id}" ${o.selected.includes(u.id) ? "checked" : ""}> ${esc(u.name)}</label>`).join("")}<button class="btn sm primary" id="uap" style="margin-top:6px">Apply</button></div></details>
      <select id="mdd" style="width:auto" aria-label="Manager's dashboard"><option value="">Whole team</option>${o.users.filter(u => o.users.some(x => x.manager_id === u.id)).map(u => `<option value="${u.id}" ${params.manager_id == u.id ? "selected" : ""}>${esc(u.name)}'s team</option>`).join("")}</select></div>` : ""}</div>
    <div class="grid g4 kpis" style="grid-template-columns:repeat(5,minmax(0,1fr));margin-bottom:12px">${cmp.map(([k, l, f]) => `<div class="card kpi"><div class="v" style="font-size:19px">${f(o.compare[k].value)}</div><div class="l">${l} this month<br>${pct(o.compare[k].change_pct)} <span class="muted">vs ${f(o.compare[k].prev)} last month</span></div></div>`).join("")}</div>
    <div class="grid g3"><div class="card"><h2>Lead generation</h2>${chart("new_leads", "#f5b942", v => v)}</div><div class="card"><h2>Sales orders</h2>${chart("order_value", "var(--ok)", v => money(v, cur))}</div><div class="card"><h2>Revenue won</h2>${chart("won_value", "var(--info)", v => money(v, cur))}</div></div>
    <div class="grid g2" style="margin-top:14px"><div class="card"><h2>Opportunity</h2>${[["Open", o.opportunity.open, "var(--accent)"], ["Won (converted to business)", o.opportunity.won, "var(--ok)"], ["Lost", o.opportunity.lost, "var(--danger)"]].map(([l, v, c]) => `<div style="margin:8px 0"><div class="row small"><span>${l}</span><b class="right mono">${money(v, cur)}</b></div><div style="height:8px;background:var(--surface-2);border-radius:4px"><div style="height:100%;width:${v / tot * 100}%;background:${c};border-radius:4px"></div></div></div>`).join("")}</div>
      <div class="card"><h2>Lead pipeline</h2>${o.pipeline.map(p => `<div class="row small" style="padding:4px 0;border-bottom:1px solid var(--border)"><span style="width:110px">${esc(p.label)}</span><div class="grow"><div style="height:8px;background:var(--accent);border-radius:4px;width:${p.count / Math.max(1, ...o.pipeline.map(x => x.count)) * 100}%"></div></div><span class="mono">${p.count} · ${money(p.value, cur)}</span></div>`).join("") || '<div class="muted">No leads.</div>'}</div></div>`;
  $("#uap", el)?.addEventListener("click", () => { const ids = $$(".uu:checked", el).map(i => i.value); overviewInto(el, S, { user_ids: ids.join(",") }).catch(fail); });
  $("#mdd", el)?.addEventListener("change", e => overviewInto(el, S, e.target.value ? { manager_id: e.target.value } : {}).catch(fail));
}
