import { get, post, qs, api, fileUrl } from "./api.js";
import { $, $$, esc, toast, fail, money, fmtD, fmtDT, today, table, wireTable, selected, chip, statusKind, modal, fieldsHTML, readFields, validate, download, skeleton, here } from "./ui.js";

const cap = s => s ? s[0].toUpperCase() + s.slice(1).replace(/_/g, " ") : "";
const isMgr = S => ["manager", "admin", "owner"].includes(S.user.role);

// ------------------------------------------------------------------ Leaves
export async function leaves(root, S) {
  const [types, hols] = await Promise.all([get("/leaves/types"), get("/leaves/holidays")]);
  root.innerHTML = `<div class="page-h"><h1>Leaves</h1><div class="right"><button class="btn primary" id="apply">+ Apply for leave</button></div></div><div class="tabs"><button class="on" data-t="mine">My leaves</button>${isMgr(S) ? '<button data-t="team">Team approvals</button>' : ""}<button data-t="hol">Holidays</button></div><div id="pane">${skeleton(4)}</div>`;
  const pane = $("#pane");
  const tab = async t => { $$(".tabs button").forEach(b => b.classList.toggle("on", b.dataset.t === t)); pane.innerHTML = skeleton(4); try { await ({ mine, team, hol })[t](); } catch (e) { fail(e); } };
  $$(".tabs button").forEach(b => b.onclick = () => tab(b.dataset.t));
  async function mine() {
    const [bal, rows] = await Promise.all([get("/leaves/balance"), get("/leaves" + qs({ scope: "mine" }))]);
    pane.innerHTML = `<div class="grid g3" style="margin-bottom:16px">${bal.map(b => `<div class="card kpi"><div class="v">${b.balance}<span class="muted" style="font-size:15px"> / ${b.allocated}</span></div><div class="l">${esc(b.name)} left · ${b.used} used or pending</div></div>`).join("")}</div>` +
      table([{ h: "Type", f: r => esc(r.type_name) }, { h: "From", f: r => fmtD(r.from_day) }, { h: "To", f: r => fmtD(r.to_day) }, { h: "Days", f: r => r.days }, { h: "Status", f: r => chip(cap(r.status), statusKind(r.status)) + (r.decision_note ? `<div class="small muted">${esc(r.decision_note)}</div>` : "") }, { h: "", f: r => r.status === "pending" ? `<button class="btn sm" data-c="${r.id}">Withdraw</button>` : "" }], rows, { empty: "You haven't applied for any leave." });
    $$("[data-c]", pane).forEach(b => b.onclick = async () => { await post(`/leaves/${b.dataset.c}/cancel`); toast("Request withdrawn."); mine(); });
  }
  async function team() {
    const rows = await get("/leaves" + qs({ status: "pending" }));
    pane.innerHTML = table([{ h: "Employee", f: r => `<strong>${esc(r.user_name)}</strong>` }, { h: "Type", f: r => esc(r.type_name) }, { h: "Dates", f: r => `${fmtD(r.from_day)} → ${fmtD(r.to_day)} (${r.days} d)` }, { h: "Reason", f: r => esc(r.reason) || "—" },
      { h: "", f: r => `<button class="btn sm primary" data-d="approved" data-id="${r.id}">Approve</button> <button class="btn sm danger" data-d="rejected" data-id="${r.id}">Reject</button>` }], rows, { empty: "No leave requests waiting for you." });
    $$("[data-d]", pane).forEach(b => b.onclick = async () => { let note = ""; if (b.dataset.d === "rejected") { note = prompt("Reason for rejecting (shown to the employee):") ?? null; if (note === null) return; } try { await post(`/leaves/${b.dataset.id}/decide`, { decision: b.dataset.d, note }); toast("Done."); team(); } catch (e) { fail(e); } });
  }
  async function hol() { pane.innerHTML = table([{ h: "Date", f: r => fmtD(r.day) }, { h: "Holiday", f: r => esc(r.name) }, { h: "Applies to", f: r => esc(r.country) }], hols, { empty: "No holidays configured. Admins can add them in Settings." }); }
  $("#apply").onclick = () => { const f = [{ k: "leave_type_id", label: "Leave type", type: "select", options: types.map(t => [t.id, t.name]), req: true }, { k: "from_day", label: "From", type: "date", req: true, def: today() }, { k: "to_day", label: "To", type: "date", req: true, def: today() }, { k: "reason", label: "Reason", type: "textarea", full: true }];
    modal({ title: "Apply for leave", body: fieldsHTML(f), actions: [{ label: "Cancel", close: true }, { label: "Submit request", primary: true, onClick: async el => { validate(el, f); const d = readFields(el, f); d.leave_type_id = +d.leave_type_id; await post("/leaves", d); toast("Request sent to your manager."); tab("mine"); } }] }); };
  await mine();
}

// ------------------------------------------------------------------ Expenses
export async function expenses(root, S) {
  const cur = S.tenant.settings.currency || "INR"; let rows = [], scope = "mine";
  root.innerHTML = `<div class="page-h"><h1>Expenses</h1><div class="right row"><button class="btn sm" id="da">🍽 Claim daily allowance</button><button class="btn sm" id="fuel">⛽ Claim fuel from GPS</button><button class="btn primary" id="new">+ New claim</button></div></div>
    <div id="autobar"></div>
    <div class="tabs"><button class="on" data-s="mine">My claims</button>${isMgr(S) ? '<button data-s="team">Team claims</button>' : ""}</div>
    <div class="row" style="margin-bottom:12px"><select id="fs" style="width:auto"><option value="">Any status</option>${["pending", "approved", "rejected", "paid"].map(s => `<option>${s}</option>`).join("")}</select><select id="fc" style="width:auto"><option value="">Any category</option>${["fuel", "travel", "food", "lodging", "other"].map(s => `<option>${s}</option>`).join("")}</select><button class="btn sm" id="exp">Export CSV</button>
    <span id="bulk" class="row right" style="display:none"><button class="btn sm primary" id="ba">Approve selected</button><button class="btn sm danger" id="br">Reject selected</button></span></div><div id="list">${skeleton(4)}</div>`;
  const load = async () => {
    rows = await get("/expenses" + qs({ scope, status: $("#fs").value, category: $("#fc").value }));
    const total = rows.reduce((a, r) => a + r.amount, 0);
    const cols = [{ h: "Date", f: r => fmtD(r.day) }, ...(scope === "team" ? [{ h: "Employee", f: r => esc(r.user_name) }] : []), { h: "Category", f: r => cap(r.category) + (r.source === "auto" ? " " + chip("Auto", "info") : "") }, { h: "Amount", f: r => `<strong class="mono">${money(r.amount, r.currency)}</strong>` }, { h: "Details", f: r => `${esc(r.description)}${r.distance_km ? ` <span class="chip">${r.distance_km} km</span>` : ""}` },
      { h: "Receipt", f: r => r.receipt ? `<a href="${fileUrl(r.receipt)}" target="_blank" rel="noopener">View</a>` : "—" }, { h: "Status", f: r => chip(cap(r.status), statusKind(r.status)) + (r.levels_required > 1 ? `<div class="small muted">${r.status === "pending" ? `Level ${r.level} of ${r.levels_required} approved` : (r.approvals || []).map(a => esc(a.name)).join(" → ")}</div>` : "") + (r.erp_ref ? `<div class="small muted">ERP ${esc(r.erp_ref)}</div>` : "") },
      { h: "", f: r => scope === "mine" && r.status === "pending" ? `<button class="btn sm" data-w="${r.id}">Withdraw</button>` : scope === "team" && r.status === "approved" ? `<button class="btn sm" data-pay="${r.id}">Mark paid</button>` : "" }];
    $("#list").innerHTML = (rows.length ? `<div class="small muted" style="margin-bottom:6px">${rows.length} claim(s) · ${money(total, cur)}</div>` : "") + table(cols, rows, { sel: scope === "team", empty: "No expense claims here." });
    wireTable($("#list"), rows); $$("[data-sel],[data-all]", root).forEach(c => c.onchange = () => $("#bulk").style.display = selected(root).length ? "flex" : "none");
    $$("[data-w]", root).forEach(b => b.onclick = async () => { await post(`/expenses/${b.dataset.w}/cancel`); load(); });
    $$("[data-pay]", root).forEach(b => b.onclick = () => decide([+b.dataset.pay], "paid"));
  };
  const decide = async (ids, decision) => { let note = ""; if (decision === "rejected") { note = prompt("Reason for rejecting:") ?? null; if (note === null) return; } try { const r = await post("/expenses/decide", { ids, decision, note }); toast(`${r.updated.length} approved` + (r.awaiting_next_level?.length ? `, ${r.awaiting_next_level.length} passed to the next approval level` : "") + (r.skipped.length ? `, ${r.skipped.length} skipped (not in your team or already decided)` : "")); load(); } catch (e) { fail(e); } };
  $("#ba").onclick = () => decide(selected(root), "approved"); $("#br").onclick = () => decide(selected(root), "rejected");
  $$(".tabs button").forEach(b => b.onclick = () => { scope = b.dataset.s; $$(".tabs button").forEach(x => x.classList.toggle("on", x === b)); $("#bulk").style.display = "none"; load(); });
  ["fs", "fc"].forEach(i => $("#" + i).onchange = load);
  $("#exp").onclick = async () => download("expenses.csv", await get("/expenses" + qs({ scope, fmt: "csv", status: $("#fs").value, category: $("#fc").value })));
  $("#fuel").onclick = () => modal({ title: "Claim fuel from GPS distance", body: `<p>Your fuel claim is calculated from the distance tracked on the day: <b>km × ${S.tenant.settings.fuel_rate_per_km} ${cur}/km</b>${S.tenant.settings.fuel_daily_cap ? ` (daily cap ${money(S.tenant.settings.fuel_daily_cap, cur)})` : ""}.</p><label class="f"><span>Day</span><input type="date" id="fd" value="${today()}" max="${today()}"></label><p class="small muted">Available after you check out.</p>`,
    actions: [{ label: "Cancel", close: true }, { label: "Create claim", primary: true, onClick: async el => { const r = await post("/expenses/fuel-auto", { day: $("#fd", el).value }); toast(`Fuel claim created: ${money(r.amount, r.currency)} for ${r.distance_km} km.`); load(); } }] });
  $("#da").onclick = () => modal({ title: "Claim daily allowance", body: `<p>Daily allowance is worked out from your attendance: the rate depends on whether you worked in a metro city, elsewhere, or outstation (far from your base).</p><label class="f"><span>Day</span><input type="date" id="dad" value="${today()}" max="${today()}"></label><p class="small muted">Available after you check out and work the minimum hours.</p>`, actions: [{ label: "Cancel", close: true }, { label: "Create claim", primary: true, onClick: async el => { const r = await post("/expenses/da-auto", { day: $("#dad", el).value }); toast(`Daily allowance: ${money(r.amount, r.currency)}.`); load(); } }] });
  async function ocrHook(el) { const rf = $("#rf", el); rf.addEventListener("change", async () => { const file = rf.files[0]; if (!file || !file.type.startsWith("image/")) return; try { toast("Reading receipt…"); const { ensureTesseract } = await import("./pages-crm.js"); await ensureTesseract(); const { data } = await Tesseract.recognize(file, "eng"); const txt = data.text;
    const tagged = [...txt.matchAll(/(?:total|amount|grand total|net|paid)[^\d]{0,15}([\d,]+\.?\d{0,2})/gi)].map(m => parseFloat(m[1].replace(/,/g, ""))).filter(n => n > 0), all = [...txt.matchAll(/\d{1,3}(?:,\d{3})*\.\d{2}/g)].map(m => parseFloat(m[0].replace(/,/g, ""))); const amt = tagged.length ? Math.max(...tagged) : (all.length ? Math.max(...all) : null);
    const dm = txt.match(/(\d{1,2})[\/\-.](\d{1,2})[\/\-.](\d{2,4})/); if (amt && !$("[name=amount]", el).value) $("[name=amount]", el).value = amt; if (dm) { let [, d, m, y] = dm; y = y.length === 2 ? "20" + y : y; const iso = `${y}-${m.padStart(2, "0")}-${d.padStart(2, "0")}`; if (!isNaN(Date.parse(iso)) && iso <= today()) $("[name=day]", el).value = iso; }
    toast(amt ? `Read amount ${amt} from the receipt. Please check it.` : "Couldn't read an amount: enter it manually.", amt ? "ok" : "err"); } catch (e) { fail(e); } }); }
  $("#new").onclick = () => { const f = [{ k: "day", label: "Date", type: "date", req: true, def: today() }, { k: "category", label: "Category", type: "select", req: true, options: ["travel", "food", "lodging", "fuel", "da", "other"] }, { k: "amount", label: `Amount (${cur})`, type: "number", req: true }, { k: "description", label: "Description", full: true }];
    modal({ title: "New expense claim", onMount: el => ocrHook(el), body: fieldsHTML(f) + `<label class="f"><span>Receipt photo</span><input type="file" id="rf" accept="image/*,application/pdf" capture="environment"><span class="hint">A timestamp and location watermark is added automatically.</span></label>`,
      actions: [{ label: "Cancel", close: true }, { label: "Submit", primary: true, onClick: async el => { validate(el, f); const d = readFields(el, f); const fd = new FormData(); Object.entries(d).forEach(([k, v]) => v != null && fd.append(k, v)); const rf = $("#rf", el).files[0]; if (rf) { fd.append("receipt", rf); const p = await here().catch(() => null); if (p) { fd.append("lat", p.lat); fd.append("lng", p.lng); } } await api("POST", "/expenses", fd); toast("Claim submitted."); load(); } }] }); };
  await load();
  // What the expense rules computed from my route (also in shadow mode, so I can compare with my own records)
  get("/expenses/auto-preview").then(r => { if (!r.found || r.mode === "off") return;
    const c = r.calc, shadow = r.mode === "shadow";
    $("#autobar").innerHTML = `<div class="card" style="padding:10px 12px;margin-bottom:12px"><b>Today's route:</b> ${r.tracked_km} km tracked${r.estimated_km ? ` (+${r.estimated_km} km estimated, not counted)` : ""} · rules give ${money(c.total, cur)}
      (${money(c.fuel, cur)} travel${c.allowance ? ` + ${money(c.allowance, cur)} ${c.allowance_kind} allowance` : ""})${shadow ? ' <span class="chip">Preview only: nothing is paid yet</span>' : " · claim is created automatically after check-out"}${c.notes.length ? `<div class="small muted">${esc(c.notes.join("; "))}</div>` : ""}</div>`; }).catch(() => {});
}
