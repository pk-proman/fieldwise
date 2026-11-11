// Route history: a month of every person's travel, drilled down the reporting hierarchy (me > my direct reports > their team).
import { get } from "./api.js";
import { $, $$, esc, fail, fmtD, fmtDT, table, wireTable, chip, modal, makeMap, download } from "./ui.js";

const qual = q => q == null ? "—" : chip(q + "%", q >= 75 ? "ok" : q >= 50 ? "warn" : "bad");
const thisMonth = () => new Date().toISOString().slice(0, 7);
const hhmm = s => s ? new Date(s).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";

export async function routehist(root, S) {
  let month = thisMonth(), rootId = null, personId = null;
  const prevMonth = () => { const d = new Date(); d.setDate(1); d.setMonth(d.getMonth() - 1); return d.toISOString().slice(0, 7); };
  root.innerHTML = `<div class="page-h"><h1>Route history</h1><div class="right row" style="gap:6px"><select id="rh-m" aria-label="Month"><option value="${thisMonth()}">This month</option><option value="${prevMonth()}">Last month</option></select>
    <input id="rh-mi" type="month" aria-label="Pick a month" style="width:auto"><button class="btn" id="rh-csv" type="button">Export CSV</button></div></div>
    <p class="muted small" style="margin:-6px 0 12px">Distance is cleaned GPS: drift, jumps and stretches where the phone was silent are not counted as travelled. Silent stretches show separately as “estimated”.</p>
    <div id="rh-bc" class="small" style="margin-bottom:8px"></div><div id="rh-body"></div>`;
  const body = $("#rh-body", root), bc = $("#rh-bc", root);
  const setMonth = m => { if (!m) return; month = m; render(); };
  $("#rh-m", root).onchange = e => { $("#rh-mi", root).value = ""; setMonth(e.target.value); };
  $("#rh-mi", root).onchange = e => setMonth(e.target.value);
  $("#rh-csv", root).onclick = async () => { try { download(`routes-${month}.csv`, await get(`/tracking/history/export?month=${month}${rootId ? "&manager_id=" + rootId : ""}`)); } catch (e) { fail(e); } };

  async function render() { try { personId ? await person() : await team(); } catch (e) { fail(e); } }

  async function team() {
    const r = await get(`/tracking/history/team?month=${month}${rootId ? "&manager_id=" + rootId : ""}`);
    const crumbs = [`<a href="#" data-r="">${esc(S.user.name.split(" ")[0])} (me)</a>`, ...r.path.map(p => `<a href="#" data-r="${p.user_id}">${esc(p.name)}</a>`)];
    bc.innerHTML = crumbs.join(" › ");
    $$("[data-r]", bc).forEach(a => a.onclick = e => { e.preventDefault(); rootId = a.dataset.r ? +a.dataset.r : null; render(); });
    body.innerHTML = table([
      { h: "Person", f: x => `<strong>${esc(x.name)}</strong>${x.self ? ' <span class="chip">own days</span>' : x.people > 1 ? ` <span class="small muted">· team of ${x.people}</span>` : ""}` },
      { h: "Days", f: x => x.days }, { h: "Tracked km", f: x => x.distance_km }, { h: "Estimated km", f: x => x.est_km ? `<span class="muted">${x.est_km}</span>` : "—" },
      { h: "Quality", f: x => qual(x.quality) },
      { h: "", f: x => `<span style="white-space:nowrap">${x.has_team ? `<button class="btn sm" data-team="${x.user_id}" type="button">Open team ›</button> ` : ""}<button class="btn sm" data-days="${x.user_id}" type="button">${x.self || !x.has_team ? "Days" : "Own days"}</button></span>` },
    ], r.rows, { empty: "No tracked travel for this month." });
    $$("[data-team]", body).forEach(b => b.onclick = () => { rootId = +b.dataset.team; render(); });
    $$("[data-days]", body).forEach(b => b.onclick = () => { personId = +b.dataset.days; render(); });
  }

  async function person() {
    const r = await get(`/tracking/history/person?user_id=${personId}&month=${month}`);
    bc.innerHTML = `<a href="#" id="rh-back">‹ Back to team</a> · <strong>${esc(r.user.name)}</strong> · ${r.totals.days} days · ${r.totals.distance_km} km tracked${r.totals.est_km ? ` · ${r.totals.est_km} km estimated` : ""}`;
    $("#rh-back", bc).onclick = e => { e.preventDefault(); personId = null; render(); };
    const rows = r.days.map(d => ({ ...d, id: d.day }));
    body.innerHTML = table([{ h: "Date", f: d => fmtD(d.day) }, { h: "Time on road", f: d => `${hhmm(d.first_at)}–${hhmm(d.last_at)}` }, { h: "Tracked km", f: d => d.distance_km },
      { h: "Est. km", f: d => d.est_km ? `<span class="muted">${d.est_km}</span>` : "—" }, { h: "Moving", f: d => d.moving_min + " min" }, { h: "Stops", f: d => d.stops_n },
      { h: "Quality", f: d => qual(d.quality) }, { h: "Expense (rules)", f: d => d.expense ? `${d.expense.total}${d.expense.mode === "shadow" ? ' <span class="chip">shadow</span>' : ""}` : "—" }], rows, { onRow: true, empty: "No tracked days." });
    wireTable(body, rows, d => dayModal(r.user, d));
  }

  async function dayModal(user, d) {
    const r = await get(`/tracking/history/day?user_id=${user.id}&day=${d.day}`);
    if (!r.found) return fail(new Error("No route recorded for that day."));
    const m = modal({ title: `${user.name} · ${fmtD(d.day)}`, wide: true, body: `<div id="rh-map" class="map" style="height:360px"></div>
      <p class="small muted" style="margin-top:8px">${r.distance_km} km tracked${r.est_km ? ` · ${r.est_km} km estimated across silent gaps (not counted)` : ""} · ${r.stops_n || 0} stops · tracking quality ${r.quality}%${r.expense?.notes?.length ? " · " + esc(r.expense.notes.join("; ")) : ""}</p>`, actions: [{ label: "Close", close: true }] });
    const map = makeMap($("#rh-map", m.root), S.tenant.settings.map_style, r.polyline?.[0] || [20.5, 78.9], 14);
    if (!map || !r.polyline?.length) return;
    const line = L.polyline(r.polyline, { color: "#0f766e", weight: 4 }).addTo(map);
    L.circleMarker(r.polyline[0], { radius: 7, color: "#fff", weight: 2, fillColor: "#16a34a", fillOpacity: 1 }).addTo(map).bindPopup("Start");
    L.circleMarker(r.polyline[r.polyline.length - 1], { radius: 7, color: "#fff", weight: 2, fillColor: "#dc2626", fillOpacity: 1 }).addTo(map).bindPopup("End");
    (r.stops || []).forEach(s => L.circleMarker([s.lat, s.lng], { radius: 6, color: "#fff", weight: 2, fillColor: "#f59e0b", fillOpacity: 1 }).addTo(map).bindPopup(`Stop · ${s.min} min`));
    map.fitBounds(line.getBounds(), { padding: [24, 24] });
    setTimeout(() => map.invalidateSize(), 150);
  }
  render();
}
