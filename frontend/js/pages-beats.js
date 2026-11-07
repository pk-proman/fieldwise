// Beat plans: recurring ordered visit schedules, today's beat for reps, adherence for managers.
import { get, post, patch, del, qs } from "./api.js";
import { $, $$, esc, toast, fail, money, fmtD, today, table, chip, statusKind, modal, confirmBox, makeMap, pinIcon, here, skeleton } from "./ui.js";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const cap = s => s ? s[0].toUpperCase() + s.slice(1).replace(/_/g, " ") : "";
const kind = s => ({ visited: "ok", in_progress: "info", planned: "", skipped: "warn", missed: "bad" }[s] || "");
const isMgr = S => ["manager", "admin", "owner"].includes(S.user.role);

export async function beats(root, S) {
  const mgr = isMgr(S);
  root.innerHTML = `<div class="page-h"><h1>Beat plan</h1></div><div class="tabs"><button data-t="day" class="on">Today's beat</button>${mgr ? '<button data-t="plans">Beat plans</button><button data-t="adh">Adherence & coverage</button>' : ""}</div><div id="pane">${skeleton(4)}</div>`;
  const pane = $("#pane"); let cleanup = () => {};
  const tab = async t => { cleanup(); cleanup = () => {}; $$(".tabs button").forEach(b => b.classList.toggle("on", b.dataset.t === t)); pane.innerHTML = skeleton(4); try { await ({ day, plans, adh })[t](); } catch (e) { fail(e); } };
  $$(".tabs button").forEach(b => b.onclick = () => tab(b.dataset.t));

  async function day() {
    const users = mgr ? await get("/users") : [];
    let uid = S.user.id, d = today();
    pane.innerHTML = `<div class="row" style="margin-bottom:12px"><input type="date" id="bd" value="${d}" style="width:auto" aria-label="Day">${mgr ? `<select id="bu" style="width:auto">${users.map(u => `<option value="${u.id}" ${u.id === uid ? "selected" : ""}>${esc(u.name)}</option>`).join("")}</select>` : ""}<span id="prog" class="muted"></span></div>
      <div class="grid" style="grid-template-columns:minmax(0,1fr) minmax(0,1fr)"><div id="stops"></div><div class="card" style="padding:8px"><div id="map" class="map tall"></div></div></div>`;
    const map = makeMap($("#map"), S.tenant.settings.map_style); const layer = map ? L.layerGroup().addTo(map) : null; cleanup = () => map?.remove();
    const load = async () => {
      const p = await get("/beats/day" + qs({ day: $("#bd").value, user_id: mgr ? $("#bu").value : "" })); const own = p.user_id === S.user.id, isToday = p.day === today();
      $("#prog").innerHTML = p.planned ? `<b>${p.visited}</b> of ${p.planned} visited${p.skipped ? ` · ${p.skipped} skipped` : ""} · ${p.adherence}% adherence` : "";
      $("#stops").innerHTML = p.planned ? (`<div style="height:8px;background:var(--surface-2);border-radius:5px;margin-bottom:12px"><div style="width:${p.adherence || 0}%;height:100%;background:var(--accent);border-radius:5px"></div></div>`) + p.stops.map((s, i) => `<div class="card" style="margin-bottom:10px;padding:12px"><div class="row"><span class="chip info" style="font-weight:700">${i + 1}</span><strong class="grow">${esc(s.customer.name)}</strong>${chip(cap(s.status), kind(s.status))}</div>
        <div class="small muted" style="margin:4px 0">${esc(s.beat_name)} · ${esc([s.customer.address, s.customer.city].filter(Boolean).join(", ")) || "no address"}${s.outstanding ? ` · <span style="color:var(--danger)">${money(s.outstanding, S.tenant.settings.currency)} outstanding</span>` : ""}</div>${s.skip_reason ? `<div class="small" style="color:var(--warn)">Skipped: ${esc(s.skip_reason)}</div>` : ""}
        <div class="row">${s.customer.lat != null ? `<a class="btn sm" target="_blank" rel="noopener" href="https://www.google.com/maps/dir/?api=1&destination=${s.customer.lat},${s.customer.lng}&travelmode=driving">🧭 Navigate</a>` : ""}${s.customer.phone ? `<a class="btn sm" href="tel:${esc(s.customer.phone)}">📞</a>` : ""}
        ${own && isToday && ["planned", "missed"].includes(s.status) ? `<button class="btn sm primary" data-st="${s.id}">Start visit</button><button class="btn sm" data-sk="${s.id}">Skip</button>` : ""}${own && s.status === "in_progress" ? `<a class="btn sm primary" href="#/visits">Continue visit →</a>` : ""}</div></div>`).join("")
        : `<div class="empty"><h3>No beat planned for this day</h3><p>${mgr ? "Create a beat plan in the Beat plans tab." : "Your manager hasn't scheduled a beat for this day."}</p></div>`;
      if (map && layer) { layer.clearLayers(); const pts = p.stops.filter(s => s.customer.lat != null); pts.forEach((s, i) => L.marker([s.customer.lat, s.customer.lng], { icon: pinIcon({ visited: "#067647", skipped: "#b54708", missed: "#b42318", in_progress: "#175cd3" }[s.status] || "#0f766e", p.stops.indexOf(s) + 1) }).addTo(layer).bindPopup(esc(s.customer.name)));
        if (pts.length > 1) L.polyline(pts.map(s => [s.customer.lat, s.customer.lng]), { color: "#0f766e", weight: 3, dashArray: "6 6" }).addTo(layer); if (pts.length) map.fitBounds(pts.map(s => [s.customer.lat, s.customer.lng]), { padding: [40, 40], maxZoom: 15 }); }
      $$("[data-st]", $("#stops")).forEach(b => b.onclick = async () => { b.disabled = true; try { const g = await here().catch(() => ({})); const r = await post(`/beats/visits/${b.dataset.st}/start`, { lat: g.lat, lng: g.lng });
        toast(r.otp_required ? `OTP sent to ${r.sent_to}.` + (r.dev_otp ? ` (demo code ${r.dev_otp})` : "") + " Enter it under Visits." : "Visit started."); if (r.dev_otp) alert("No SMS gateway is configured. Test code: " + r.dev_otp); location.hash = "#/visits"; } catch (e) { fail(e); b.disabled = false; } });
      $$("[data-sk]", $("#stops")).forEach(b => b.onclick = () => modal({ title: "Skip this stop", body: `<label class="f"><span>Reason *</span><select id="sr">${["Shop closed", "Owner not available", "Customer asked to reschedule", "Route/time constraint", "Vehicle issue", "Other"].map(x => `<option>${x}</option>`).join("")}</select></label><label class="f"><span>Details</span><input id="sd"></label>`,
        actions: [{ label: "Cancel", close: true }, { label: "Skip stop", primary: true, onClick: async el => { await post(`/beats/visits/${b.dataset.sk}/skip`, { reason: $("#sr", el).value + ($("#sd", el).value ? ": " + $("#sd", el).value : "") }); toast("Stop skipped. Your manager was notified."); load(); } }] }));
    };
    $("#bd").onchange = load; $("#bu")?.addEventListener("change", load); await load();
  }

  async function plans() {
    const [list, users, custs] = await Promise.all([get("/beats"), get("/users"), get("/crm/customers?limit=500").then(r => r.items)]);
    pane.innerHTML = `<div class="row" style="margin-bottom:12px"><span class="muted">A beat is a recurring route: customers, in order, on set weekdays. Visits are generated 14 days ahead; holidays and approved leave are skipped.</span><button class="btn primary right" id="nb">+ New beat</button></div>` +
      table([{ h: "Beat", f: r => `<strong>${esc(r.name)}</strong><div class="small muted">${esc(r.territory)}</div>` }, { h: "Executive", f: r => esc(r.user_name) }, { h: "Schedule", f: r => r.days.map(d => chip(DAYS[d])).join(" ") + (r.every_weeks > 1 ? `<div class="small muted">every ${r.every_weeks} weeks</div>` : "") }, { h: "Customers", f: r => `${r.customers.length}<div class="small muted">${esc(r.customers.slice(0, 3).map(c => c.name).join(", "))}${r.customers.length > 3 ? "…" : ""}</div>` }, { h: "Status", f: r => r.active ? chip("Active", "ok") : chip("Paused", "warn") }], list, { onRow: true, empty: "No beats yet. Create one for each field executive." });
    const rows = list; $$("tbody tr[data-i]", pane).forEach(tr => tr.onclick = () => form(rows[+tr.dataset.i]));
    $("#nb").onclick = () => form(null);
    function form(b) {
      const sel = [...(b?.customer_ids || [])], body = document.createElement("div");
      body.innerHTML = `<div class="formgrid"><label class="f"><span>Beat name *</span><input id="bn" value="${esc(b?.name || "")}" placeholder="e.g. Andheri - Monday"></label><label class="f"><span>Field executive *</span><select id="bx">${users.filter(u => u.active !== false).map(u => `<option value="${u.id}" ${(b?.user_id || S.user.id) === u.id ? "selected" : ""}>${esc(u.name)}</option>`).join("")}</select></label>
        <label class="f"><span>Repeats every</span><select id="bw">${[1, 2, 3, 4].map(n => `<option value="${n}" ${b?.every_weeks === n ? "selected" : ""}>${n === 1 ? "week" : n + " weeks"}</option>`).join("")}</select></label><label class="f"><span>Starts on</span><input type="date" id="bs" value="${b?.start_date || today()}"></label>
        <label class="f"><span>Territory</span><input id="bt" value="${esc(b?.territory || "")}"></label><label class="f"><span><input type="checkbox" id="ba" ${b?.active === false ? "" : "checked"}> Active</span></label></div>
        <div class="f"><span style="font-weight:550;font-size:13px">Visit days *</span><div class="row">${DAYS.map((d, i) => `<label><input type="checkbox" name="dy" value="${i}" ${(b?.days || []).includes(i) ? "checked" : ""}> ${d}</label>`).join("")}</div></div>
        <div class="grid g2"><div><div class="row"><strong>Add customers</strong></div><input id="cq" type="search" placeholder="Search…" style="margin:6px 0"><div id="cl" style="max-height:240px;overflow:auto"></div></div>
        <div><div class="row"><strong>Visit order</strong><button class="btn sm right" id="op" type="button">Optimise order</button></div><div id="so" style="max-height:270px;overflow:auto;margin-top:6px"></div><div id="os" class="small muted"></div></div></div>`;
      const nm = id => custs.find(c => c.id === id)?.name || "?";
      const paint = () => {
        const q = $("#cq", body).value.toLowerCase(); $("#cl", body).innerHTML = custs.filter(c => !sel.includes(c.id) && c.name.toLowerCase().includes(q)).slice(0, 60).map(c => `<div class="row small" style="padding:4px 0;border-bottom:1px solid var(--border)"><span class="grow">${esc(c.name)} <span class="muted">${esc(c.city)}</span>${c.lat == null ? ' <span class="chip warn">no map</span>' : ""}</span><button class="btn sm" type="button" data-add="${c.id}">Add</button></div>`).join("") || '<div class="muted small">No more customers.</div>';
        $("#so", body).innerHTML = sel.map((id, i) => `<div class="row small" style="padding:4px 0;border-bottom:1px solid var(--border)"><span class="chip info">${i + 1}</span><span class="grow">${esc(nm(id))}</span><button class="btn sm" type="button" data-up="${i}" ${i ? "" : "disabled"}>↑</button><button class="btn sm" type="button" data-dn="${i}" ${i < sel.length - 1 ? "" : "disabled"}>↓</button><button class="btn sm" type="button" data-rm="${i}">✕</button></div>`).join("") || '<div class="muted small">Add customers from the left.</div>';
      };
      body.addEventListener("click", e => { const t = e.target; if (t.dataset.add) sel.push(+t.dataset.add); else if (t.dataset.rm) sel.splice(+t.dataset.rm, 1); else if (t.dataset.up) { const i = +t.dataset.up; [sel[i - 1], sel[i]] = [sel[i], sel[i - 1]]; } else if (t.dataset.dn) { const i = +t.dataset.dn; [sel[i + 1], sel[i]] = [sel[i], sel[i + 1]]; } else return; paint(); });
      $("#cq", body).oninput = paint; paint();
      $("#op", body).onclick = async () => { try { const r = await post("/beats/optimize", { customer_ids: sel, user_id: +$("#bx", body).value }); sel.splice(0, sel.length, ...r.customer_ids); paint(); $("#os", body).textContent = `Optimised: about ${r.distance_km} km${r.unmapped ? ` (${r.unmapped} customer(s) without a map location kept at the end)` : ""}.`; } catch (e) { fail(e); } };
      modal({ title: b ? "Edit beat" : "New beat plan", body, wide: true, actions: [...(b ? [{ label: "Delete", danger: true, onClick: async () => { if (!(await confirmBox("Delete this beat?", "Planned future visits are removed. History is kept.", "Delete", true))) return false; await del("/beats/" + b.id); toast("Beat deleted."); plans(); } }] : []), { label: "Cancel", close: true }, { label: "Save beat", primary: true, onClick: async () => {
        const days = $$("[name=dy]:checked", body).map(i => +i.value); if (!$("#bn", body).value.trim()) throw new Error("Give the beat a name."); if (!days.length) throw new Error("Choose at least one visit day."); if (!sel.length) throw new Error("Add at least one customer.");
        const payload = { name: $("#bn", body).value, user_id: +$("#bx", body).value, days, every_weeks: +$("#bw", body).value, start_date: $("#bs", body).value || null, customer_ids: sel, territory: $("#bt", body).value, active: $("#ba", body).checked };
        b ? await patch("/beats/" + b.id, payload) : await post("/beats", payload); toast("Beat saved. Visits scheduled for the next 14 days."); plans(); } }] });
    }
  }

  async function adh() {
    const r = await get("/beats/adherence"), t = r.total;
    const pc = v => v == null ? "—" : `<span style="color:${v >= 80 ? "var(--ok)" : v >= 50 ? "var(--warn)" : "var(--danger)"};font-weight:600">${v}%</span>`;
    pane.innerHTML = `<div class="muted small" style="margin-bottom:10px">${fmtD(r.from)} → ${fmtD(r.to)} · adherence = visited ÷ (visited + skipped + missed)</div><div class="grid g4 kpis" style="margin-bottom:14px">${[["Adherence", pc(t.adherence)], ["Visited", t.visited], ["Skipped", t.skipped], ["Missed", t.missed]].map(([l, v]) => `<div class="card kpi"><div class="v">${v}</div><div class="l">${l}</div></div>`).join("")}</div>
      <div class="grid g2"><div><h2 style="margin-bottom:8px">By executive</h2>${table([{ h: "Executive", f: x => esc(x.name) }, { h: "Visited", f: x => x.visited }, { h: "Skipped", f: x => x.skipped }, { h: "Missed", f: x => x.missed }, { h: "Adherence", f: x => pc(x.adherence) }], r.by_user, { empty: "No beat data yet." })}<h2 style="margin:16px 0 8px">By beat</h2>${table([{ h: "Beat", f: x => esc(x.name) }, { h: "Visited", f: x => x.visited }, { h: "Missed", f: x => x.missed }, { h: "Adherence", f: x => pc(x.adherence) }], r.by_beat, { empty: "—" })}</div>
      <div><h2 style="margin-bottom:8px">Coverage: days since last beat visit</h2>${table([{ h: "Customer", f: x => esc(x.name) }, { h: "Last visited", f: x => x.last_visited ? fmtD(x.last_visited) : "never" }, { h: "Days", f: x => x.days_since == null ? chip("never", "bad") : x.days_since > 14 ? chip(x.days_since + " d", "bad") : x.days_since > 7 ? chip(x.days_since + " d", "warn") : x.days_since + " d" }], r.coverage, { empty: "No customers on a beat." })}</div></div>`;
  }
  await day(); return () => cleanup();
}
