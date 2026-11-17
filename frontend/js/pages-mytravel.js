// Expense tracker: for any period, day by day, the distance travelled, the visits made and the expense the rules expect. Each day can be adjusted
// (with a reason) and other expenses added (food, stay, taxi...). Managers can pick a person from their team and correct a pending amount.
import { get, post, api, fileUrl } from "./api.js";
import { $, $$, esc, toast, fail, money, fmtD, today, table, wireTable, chip, statusKind, modal, fieldsHTML, readFields, validate, confirmBox, here, makeMap } from "./ui.js";

const iso = d => d.toISOString().slice(0, 10);
const local = d => iso(new Date(d.getTime() - d.getTimezoneOffset() * 6e4));
const qual = q => q == null ? "—" : chip(q + "%", q >= 75 ? "ok" : q >= 50 ? "warn" : "bad");
const CAT = { fuel: "Travel (fuel)", da: "Daily allowance", food: "Food", lodging: "Accommodation", taxi: "Taxi / cab", travel: "Bus, train or other travel", toll: "Toll", parking: "Parking", other: "Other" };
const EXTRA = ["food", "lodging", "taxi", "travel", "toll", "parking", "other"];
const cap = s => s ? s[0].toUpperCase() + s.slice(1) : "";
const num = v => { const n = parseFloat(v); return Number.isFinite(n) ? n : 0; };
const r2 = n => Math.round(n * 100) / 100;

export async function mytravel(root, S) {
  const mgr = ["manager", "admin", "owner"].includes(S.user.role) && S.appScope !== "field";
  const users = mgr ? await get("/users").catch(() => []) : [];
  const now = new Date(), first = new Date(now.getFullYear(), now.getMonth(), 1), prevFirst = new Date(now.getFullYear(), now.getMonth() - 1, 1), prevLast = new Date(now.getFullYear(), now.getMonth(), 0);
  let start = local(first), end = local(now), uid = S.user.id, r = null;
  root.innerHTML = `<div class="page-h"><h1>Expense tracker</h1><div class="right row"><button class="btn sm" id="mt-all" style="display:none"></button><button class="btn primary" id="mt-add">+ Add expense</button></div></div>
    <div class="card" style="padding:10px 12px;margin-bottom:12px"><div class="row" style="gap:8px;flex-wrap:wrap">
      ${mgr ? `<select id="mt-u" aria-label="Person" style="width:auto">${users.map(u => `<option value="${u.id}" ${u.id === uid ? "selected" : ""}>${esc(u.id === S.user.id ? "Me" : u.name)}</option>`).join("")}</select>` : ""}
      <select id="mt-p" aria-label="Period" style="width:auto"><option value="cur">This month</option><option value="prev">Last month</option><option value="custom">Choose dates…</option></select>
      <span id="mt-c" style="display:none" class="row"><input type="date" id="mt-s" aria-label="From"> <input type="date" id="mt-e" aria-label="To"> <button class="btn sm" id="mt-go" type="button">Show</button></span></div></div>
    <div id="mt-body"></div>`;
  const body = $("#mt-body", root);
  const setP = v => {
    $("#mt-c", root).style.display = v === "custom" ? "flex" : "none";
    if (v === "cur") { start = local(first); end = local(now); load(); }
    if (v === "prev") { start = local(prevFirst); end = local(prevLast); load(); }
  };
  $("#mt-p", root).onchange = e => setP(e.target.value);
  $("#mt-go", root).onclick = () => { start = $("#mt-s", root).value; end = $("#mt-e", root).value; if (start && end) load(); };
  $("#mt-u", root)?.addEventListener("change", e => { uid = +e.target.value; load(); });
  $("#mt-add", root).onclick = () => {
    const f = [{ k: "day", label: "Date", type: "date", req: true, def: today() }];
    modal({ title: "Add an expense", body: fieldsHTML(f) + '<p class="small muted">Next you can adjust travel for that day and add food, accommodation and other expenses.</p>', actions: [{ label: "Cancel", close: true }, { label: "Continue", primary: true, onClick: el => { validate(el, f); const d = readFields(el, f).day; if (d > today()) throw new Error("Choose today or an earlier date."); setTimeout(() => openDay(d), 0); } }] });
  };

  const stCell = d => {
    const its = d.items.filter(i => ["fuel", "da"].includes(i.category)), all = d.items;
    if (!all.length) return d.calc && d.calc.total > 0 ? chip("Not claimed", "warn") : "—";
    const st = all.some(i => i.status === "pending") ? "pending" : all.every(i => i.status === "paid") ? "paid" : "approved";
    return chip(cap(st), statusKind(st)) + (its.length && its.some(i => i.adjust_note) ? ' <span class="chip" title="Changed from the calculated amount">adjusted</span>' : "");
  };

  async function load() {
    body.innerHTML = `<div class="muted">Loading…</div>`;
    try {
      r = await get(`/expenses/period?start=${start}&end=${end}&user_id=${uid}`);
      const cur = r.currency, t = r.totals, mine = r.me;
      $("#mt-add", root).style.display = mine ? "" : "none";
      const all = $("#mt-all", root);
      all.style.display = mine && t.unclaimed_days ? "" : "none";
      all.textContent = `Claim the expected amount for ${t.unclaimed_days} day${t.unclaimed_days === 1 ? "" : "s"}`;
      all.onclick = claimAll;
      body.innerHTML = `<div class="grid g4 kpis" style="margin-bottom:12px">
        <div class="card kpi"><div class="v">${t.km}</div><div class="l">km tracked${t.est_km ? ` · +${t.est_km} estimated` : ""} · ${t.visits} visits</div></div>
        <div class="card kpi"><div class="v">${money(t.calculated, cur)}</div><div class="l">expected: travel at ${money(r.rate, cur)}/km + allowance</div></div>
        <div class="card kpi"><div class="v">${money(t.other, cur)}</div><div class="l">other expenses (food, stay, taxi…)</div></div>
        <div class="card kpi"><div class="v">${money(t.claimed, cur)}</div><div class="l">total claimed</div></div></div>
        <div class="tip" style="margin-bottom:12px">${mine ? "Open a day to <b>adjust the travel amount</b> (with a reason) and <b>add food, accommodation and other expenses</b>, then claim it." : "Open a day to see the claim. Pending amounts can be adjusted before you approve them in Expenses."} ${r.mode === "off" ? "Expected amounts use the workspace rates; nothing is claimed until it is claimed here." : ""}</div>
        <div id="mt-days"></div>`;
      const rows = r.days.map(d => ({ ...d, id: d.day }));
      const cl = d => d.items.filter(i => ["fuel", "da"].includes(i.category)).reduce((a, i) => a + i.amount, 0);
      $("#mt-days", body).innerHTML = table([{ h: "Date", f: d => fmtD(d.day) }, { h: "Km", f: d => d.km ? d.km : "—" }, { h: "Visits", f: d => d.visits.length || "—" },
        { h: "Expected", f: d => d.calc ? money(d.calc.total, cur) : "—" }, { h: "Travel + allowance", f: d => cl(d) ? money(cl(d), cur) : "—" }, { h: "Other", f: d => d.other ? money(d.other, cur) : "—" },
        { h: "Total", f: d => d.claimed ? `<strong class="mono">${money(d.claimed, cur)}</strong>` : "—" }, { h: "Status", f: stCell }, { h: "Route", f: d => qual(d.quality) }], rows, { onRow: true, empty: "No travel, visits or expenses in this period." });
      wireTable($("#mt-days", body), rows, d => openDay(d.day));
    } catch (e) { body.innerHTML = ""; fail(e); }
  }

  async function claimAll() {
    const days = r.days.filter(d => d.calc && d.calc.total > 0 && !d.items.some(i => ["fuel", "da"].includes(i.category)));
    const sum = days.reduce((a, d) => a + d.calc.total, 0);
    if (!days.length || !(await confirmBox("Claim the expected amounts", `Claim ${money(sum, r.currency)} for ${days.length} day(s), exactly as calculated from your tracked routes? You can still adjust any day afterwards while it is pending.`, "Claim"))) return;
    let ok = 0, bad = 0;
    for (const d of days) { try { await post("/expenses/day-travel", { day: d.day }); ok++; } catch { bad++; } }
    toast(`${ok} day(s) claimed${bad ? `, ${bad} could not be claimed` : ""}.`, bad ? "err" : "ok");
    load();
  }

  // ---- one day: adjust travel, add other expenses
  async function openDay(day) {
    let p; try { p = await get(`/expenses/period?start=${day}&end=${day}&user_id=${uid}`); } catch (e) { return fail(e); }
    const d = p.days.find(x => x.day === day) || { day, km: 0, est_km: 0, visits: [], calc: null, items: [], other: 0, claimed: 0, quality: null };
    const cur = p.currency, mine = p.me, calc = d.calc || { km: 0, fuel: 0, allowance: 0, allowance_kind: "", notes: [], total: 0 };
    const fuel = d.items.find(i => i.category === "fuel"), da = d.items.find(i => i.category === "da");
    const locked = [fuel, da].some(i => i && (i.status !== "pending" || i.level > 0)) || !mine;
    const others = d.items.filter(i => !["fuel", "da"].includes(i.category));
    let route = null; try { const x = await get(`/tracking/history/day?user_id=${uid}&day=${day}`); if (x.found) route = x; } catch { /* no route that day */ }
    const km0 = fuel?.distance_km ?? calc.km, amt0 = fuel?.amount ?? calc.fuel, da0 = da?.amount ?? calc.allowance;
    const itemRow = i => `<div class="row" style="gap:8px;padding:6px 0;border-bottom:1px solid var(--border);align-items:flex-start"><div class="grow"><strong>${esc(CAT[i.category] || cap(i.category))}</strong> <span class="mono">${money(i.amount, cur)}</span> ${chip(cap(i.status), statusKind(i.status))}${i.description ? `<div class="small muted">${esc(i.description)}</div>` : ""}${i.adjust_note ? `<div class="small" style="color:var(--warn)">Changed${i.calc_amount != null ? ` from ${money(i.calc_amount, cur)}` : ""}: ${esc(i.adjust_note)}</div>` : ""}</div>${i.receipt ? `<a class="small" href="${fileUrl(i.receipt)}" target="_blank" rel="noopener">Receipt</a>` : ""}${mine && i.status === "pending" && !["fuel", "da"].includes(i.category) ? `<button class="btn sm" data-w="${i.id}">Withdraw</button>` : ""}${!mine && i.status === "pending" ? `<button class="btn sm" data-adj="${i.id}">Adjust</button>` : ""}</div>`;
    const m = modal({ title: `${p.user.name} · ${fmtD(day)}`, wide: true, body: `
      ${route ? '<div id="dm-map" class="map" style="height:220px;margin-bottom:8px"></div>' : ""}
      <p class="small muted" style="margin:0 0 10px">${d.km ? `<b>${d.km} km</b> tracked` : "No route was tracked"}${d.est_km ? ` · ${d.est_km} km estimated (phone silent, not counted)` : ""} · ${d.visits.length} visit${d.visits.length === 1 ? "" : "s"}${d.visits.length ? ": " + d.visits.map(v => esc(v.customer || "Customer")).join(", ") : ""}${calc.notes.length ? " · " + esc(calc.notes.join("; ")) : ""}</p>
      <h3 style="margin:6px 0">Travel and allowance ${fuel || da ? chip(cap((fuel || da).status), statusKind((fuel || da).status)) : ""}</h3>
      <div class="grid g3" style="gap:8px">
        <label class="f"><span>Km to claim</span><input type="number" step="0.1" min="0" id="tk" value="${km0}" ${locked ? "disabled" : ""}><span class="hint">Tracked: ${calc.km} km</span></label>
        <label class="f"><span>Travel amount (${esc(cur)})</span><input type="number" step="0.01" min="0" id="ta" value="${amt0}" ${locked ? "disabled" : ""}><span class="hint">Rules: ${money(calc.fuel, cur)} (${money(p.rate, cur)}/km${p.cap ? `, capped at ${money(p.cap, cur)}` : ""})</span></label>
        <label class="f"><span>Allowance (${esc(cur)})</span><input type="number" step="0.01" min="0" id="tda" value="${da0}" ${locked ? "disabled" : ""}><span class="hint">Rules: ${money(calc.allowance, cur)}${calc.allowance_kind ? " " + esc(calc.allowance_kind) : ""}</span></label></div>
      ${locked ? "" : `<label class="f"><span>Reason for any change <span class="muted">(needed if you change the calculated numbers)</span></span><input id="tr" maxlength="250" value="${esc(fuel?.adjust_note || da?.adjust_note || "")}" placeholder="e.g. detour to the depot, GPS was off for an hour"></label>
      <div class="row" style="margin-top:6px"><span class="grow small muted" id="tsum"></span><button class="btn primary" id="tsave">${fuel || da ? "Update claim" : "Claim travel and allowance"}</button></div>`}
      <h3 style="margin:14px 0 4px">Other expenses</h3>
      ${others.length ? others.map(itemRow).join("") : '<p class="muted small" style="margin:0 0 6px">None added for this day.</p>'}
      ${mine ? `<div class="card" style="padding:10px;margin-top:8px"><div class="grid g3" style="gap:8px">
        <label class="f"><span>Type</span><select id="xc">${EXTRA.map(c => `<option value="${c}">${esc(CAT[c])}</option>`).join("")}</select></label>
        <label class="f"><span>Amount (${esc(cur)})</span><input type="number" step="0.01" min="0" id="xa"></label>
        <label class="f"><span>Receipt (optional)</span><input type="file" id="xr" accept="image/*,application/pdf"></label></div>
        <label class="f"><span>Details</span><input id="xd" maxlength="300" placeholder="e.g. hotel in Pune, 1 night"></label>
        <div class="row"><span class="grow small muted">Sent to your manager for approval.</span><button class="btn" id="xadd">Add expense</button></div></div>` : ""}
      ${d.items.filter(i => ["fuel", "da"].includes(i.category)).length ? `<h3 style="margin:14px 0 4px">Claims for travel and allowance</h3>${d.items.filter(i => ["fuel", "da"].includes(i.category)).map(itemRow).join("")}` : ""}
      <div class="row" style="margin-top:12px;font-size:16px"><strong class="grow">Total for the day</strong><strong class="mono">${money(d.claimed, cur)}</strong></div>`,
      actions: [{ label: "Close", close: true }] });
    const reopen = async () => { m.close(); await load(); openDay(day); };
    const sum = () => { const el = $("#tsum", m.root); if (el) el.textContent = `Travel + allowance: ${money(r2(num($("#ta", m.root).value) + num($("#tda", m.root).value)), cur)}`; };
    if (!locked) {
      let touched = false;
      $("#tk", m.root).oninput = e => { if (!touched) { const v = num(e.target.value) * p.rate; $("#ta", m.root).value = r2(p.cap ? Math.min(v, p.cap) : v); } sum(); };
      $("#ta", m.root).oninput = () => { touched = true; sum(); }; $("#tda", m.root).oninput = sum; sum();
      $("#tsave", m.root).onclick = async ev => {
        const km = num($("#tk", m.root).value), amt = num($("#ta", m.root).value), al = num($("#tda", m.root).value), why = $("#tr", m.root).value.trim();
        const payload = { day, reason: why }; if (Math.abs(km - km0) > 0.004 || fuel) payload.km = km; if (touched || fuel || Math.abs(amt - amt0) > 0.004) payload.amount = amt; if (Math.abs(al - da0) > 0.004 || da) payload.allowance = al;
        ev.target.disabled = true;
        try { await post("/expenses/day-travel", payload); toast("Travel and allowance saved."); await reopen(); } catch (e) { ev.target.disabled = false; fail(e); }
      };
    }
    $("#xadd", m.root)?.addEventListener("click", async ev => {
      const amount = num($("#xa", m.root).value); if (amount <= 0) return toast("Enter an amount.", "err");
      const fd = new FormData(); fd.append("day", day); fd.append("category", $("#xc", m.root).value); fd.append("amount", amount); fd.append("description", $("#xd", m.root).value);
      const rf = $("#xr", m.root).files[0]; if (rf) { fd.append("receipt", rf); const pos = await here().catch(() => null); if (pos) { fd.append("lat", pos.lat); fd.append("lng", pos.lng); } }
      ev.target.disabled = true;
      try { await api("POST", "/expenses", fd); toast("Expense added."); await reopen(); } catch (e) { ev.target.disabled = false; fail(e); }
    });
    $$("[data-w]", m.root).forEach(b => b.onclick = async () => { try { await post(`/expenses/${b.dataset.w}/cancel`); toast("Withdrawn."); await reopen(); } catch (e) { fail(e); } });
    $$("[data-adj]", m.root).forEach(b => b.onclick = () => {
      const it = d.items.find(i => i.id === +b.dataset.adj), f = [{ k: "amount", label: `New amount (${cur})`, type: "number", req: true, def: it.amount }, { k: "note", label: "Reason (the employee sees this)", req: true, full: true }];
      modal({ title: `Adjust ${CAT[it.category] || it.category}`, body: `<p class="small muted">Was ${money(it.calc_amount ?? it.amount, cur)} as calculated or claimed. The original is kept on record.</p>` + fieldsHTML(f),
        actions: [{ label: "Cancel", close: true }, { label: "Save", primary: true, onClick: async el => { validate(el, f); const v = readFields(el, f); await post(`/expenses/${it.id}/adjust`, { amount: +v.amount, note: v.note }); toast("Amount changed."); setTimeout(reopen, 0); } }] });
    });
    if (route) {
      const map = makeMap($("#dm-map", m.root), S.tenant.settings.map_style, route.polyline?.[0] || [20.5, 78.9], 14);
      if (map && route.polyline?.length) {
        const line = L.polyline(route.polyline, { color: "#0f766e", weight: 4 }).addTo(map);
        L.circleMarker(route.polyline[0], { radius: 7, color: "#fff", weight: 2, fillColor: "#16a34a", fillOpacity: 1 }).addTo(map).bindPopup("Start");
        L.circleMarker(route.polyline[route.polyline.length - 1], { radius: 7, color: "#fff", weight: 2, fillColor: "#dc2626", fillOpacity: 1 }).addTo(map).bindPopup("End");
        (route.stops || []).forEach(s => L.circleMarker([s.lat, s.lng], { radius: 6, color: "#fff", weight: 2, fillColor: "#f59e0b", fillOpacity: 1 }).addTo(map).bindPopup(`Stop · ${s.min} min`));
        map.fitBounds(line.getBounds(), { padding: [24, 24] });
        setTimeout(() => map.invalidateSize(), 150);
      }
    }
  }
  load();
}
