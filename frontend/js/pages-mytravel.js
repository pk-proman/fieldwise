// My travel: for any period, the distance travelled, the visits made, the day-wise routes and what the expense rules work out. Managers can pick a person from their team.
import { get } from "./api.js";
import { $, $$, esc, fail, money, fmtD, table, wireTable, chip, modal, makeMap } from "./ui.js";

const iso = d => d.toISOString().slice(0, 10);
const qual = q => q == null ? "—" : chip(q + "%", q >= 75 ? "ok" : q >= 50 ? "warn" : "bad");

export async function mytravel(root, S) {
  const mgr = ["manager", "admin", "owner"].includes(S.user.role) && S.appScope !== "field";
  const users = mgr ? await get("/users").catch(() => []) : [];
  const now = new Date(), first = new Date(now.getFullYear(), now.getMonth(), 1), prevFirst = new Date(now.getFullYear(), now.getMonth() - 1, 1), prevLast = new Date(now.getFullYear(), now.getMonth(), 0);
  let start = iso(new Date(first.getTime() - first.getTimezoneOffset() * 6e4)), end = iso(new Date(now.getTime() - now.getTimezoneOffset() * 6e4)), uid = S.user.id;
  root.innerHTML = `<div class="page-h"><h1>${uid === S.user.id ? "My travel" : "Travel"}</h1></div>
    <div class="card" style="padding:10px 12px;margin-bottom:12px"><div class="row" style="gap:8px;flex-wrap:wrap">
      ${mgr ? `<select id="mt-u" aria-label="Person" style="width:auto">${users.map(u => `<option value="${u.id}" ${u.id === uid ? "selected" : ""}>${esc(u.id === S.user.id ? "Me" : u.name)}</option>`).join("")}</select>` : ""}
      <select id="mt-p" aria-label="Period" style="width:auto"><option value="cur">This month</option><option value="prev">Last month</option><option value="custom">Choose dates…</option></select>
      <span id="mt-c" style="display:none" class="row"><input type="date" id="mt-s" aria-label="From"> <input type="date" id="mt-e" aria-label="To"> <button class="btn sm" id="mt-go" type="button">Show</button></span></div></div>
    <div id="mt-body"></div>`;
  const body = $("#mt-body", root);
  const setP = v => {
    $("#mt-c", root).style.display = v === "custom" ? "flex" : "none";
    if (v === "cur") { start = iso(new Date(first.getTime() - first.getTimezoneOffset() * 6e4)); end = iso(new Date(now.getTime() - now.getTimezoneOffset() * 6e4)); load(); }
    if (v === "prev") { start = iso(new Date(prevFirst.getTime() - prevFirst.getTimezoneOffset() * 6e4)); end = iso(new Date(prevLast.getTime() - prevLast.getTimezoneOffset() * 6e4)); load(); }
  };
  $("#mt-p", root).onchange = e => setP(e.target.value);
  $("#mt-go", root).onclick = () => { start = $("#mt-s", root).value; end = $("#mt-e", root).value; if (start && end) load(); };
  $("#mt-u", root)?.addEventListener("change", e => { uid = +e.target.value; load(); });

  async function load() {
    body.innerHTML = `<div class="muted">Loading…</div>`;
    try {
      const r = await get(`/expenses/period?start=${start}&end=${end}&user_id=${uid}`), cur = r.currency, t = r.totals;
      const shadow = r.mode === "shadow", off = r.mode === "off";
      body.innerHTML = `<div class="grid g4 kpis" style="margin-bottom:12px">
        <div class="card kpi"><div class="v">${t.km}</div><div class="l">km tracked${t.est_km ? ` · +${t.est_km} estimated` : ""}</div></div>
        <div class="card kpi"><div class="v">${t.visits}</div><div class="l">customer visits · ${t.days} days</div></div>
        <div class="card kpi"><div class="v">${off ? "—" : money(t.calculated, cur)}</div><div class="l">${off ? "automatic calculation is off" : shadow ? "calculated by the rules (preview)" : "calculated by the rules"}</div></div>
        <div class="card kpi"><div class="v">${money(t.claimed, cur)}</div><div class="l">already claimed</div></div></div>
        ${shadow ? '<div class="tip" style="margin-bottom:12px">Preview only: nothing is paid from the calculated amount yet. Claim your expenses in <a href="#/expenses">Expenses</a>.</div>' : ""}
        <div id="mt-days"></div>`;
      const rows = r.days.map(d => ({ ...d, id: d.day }));
      $("#mt-days", body).innerHTML = table([{ h: "Date", f: d => fmtD(d.day) }, { h: "Travelled", f: d => d.km ? d.km + " km" : "—" }, { h: "Visits", f: d => d.visits.length || "—" },
        { h: "Route quality", f: d => qual(d.quality) }, { h: "Rules give", f: d => d.calc ? money(d.calc.total, cur) : "—" }, { h: "Claimed", f: d => d.claimed ? money(d.claimed, cur) : "—" }], rows, { onRow: true, empty: "No travel or visits in this period." });
      wireTable($("#mt-days", body), rows, d => dayModal(r, d));
    } catch (e) { body.innerHTML = ""; fail(e); }
  }

  async function dayModal(r, d) {
    let route = null; try { const x = await get(`/tracking/history/day?user_id=${uid}&day=${d.day}`); if (x.found) route = x; } catch { /* no route that day */ }
    const m = modal({ title: `${r.user.name} · ${fmtD(d.day)}`, wide: true, body: `${route ? '<div id="mt-map" class="map" style="height:300px"></div>' : '<p class="muted">No route was recorded this day.</p>'}
      <p class="small muted" style="margin:8px 0">${d.km} km tracked${d.est_km ? ` · ${d.est_km} km estimated (phone silent, not counted)` : ""}${d.calc ? ` · rules: ${money(d.calc.fuel, r.currency)} travel${d.calc.allowance ? " + " + money(d.calc.allowance, r.currency) + " " + d.calc.allowance_kind + " allowance" : ""}` : ""}${d.calc?.notes?.length ? " · " + esc(d.calc.notes.join("; ")) : ""}</p>
      <h3 style="margin:8px 0 4px">Visits (${d.visits.length})</h3>${d.visits.length ? d.visits.map(v => `<div class="l" style="padding:6px 0;border-bottom:1px solid var(--border)"><strong>${esc(v.customer || "Customer")}</strong> <span class="small muted">· ${v.minutes} min ${v.done ? "" : "· still active"}</span>${v.outcome ? `<div class="small muted">${esc(v.outcome)}</div>` : ""}</div>`).join("") : '<p class="muted small">No visits.</p>'}`,
      actions: [{ label: "Close", close: true }] });
    if (!route) return;
    const map = makeMap($("#mt-map", m.root), S.tenant.settings.map_style, route.polyline?.[0] || [20.5, 78.9], 14);
    if (!map || !route.polyline?.length) return;
    const line = L.polyline(route.polyline, { color: "#0f766e", weight: 4 }).addTo(map);
    L.circleMarker(route.polyline[0], { radius: 7, color: "#fff", weight: 2, fillColor: "#16a34a", fillOpacity: 1 }).addTo(map).bindPopup("Start");
    L.circleMarker(route.polyline[route.polyline.length - 1], { radius: 7, color: "#fff", weight: 2, fillColor: "#dc2626", fillOpacity: 1 }).addTo(map).bindPopup("End");
    (route.stops || []).forEach(s => L.circleMarker([s.lat, s.lng], { radius: 6, color: "#fff", weight: 2, fillColor: "#f59e0b", fillOpacity: 1 }).addTo(map).bindPopup(`Stop · ${s.min} min`));
    map.fitBounds(line.getBounds(), { padding: [24, 24] });
    setTimeout(() => map.invalidateSize(), 150);
  }
  load();
}
