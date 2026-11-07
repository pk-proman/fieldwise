// Sales workflow UI: lead pipeline helpers, quotations, follow-ups, meetings, analytics.
import { get, post, patch, put, del, qs, fileUrl } from "./api.js";
import { orderModal, collectModal } from "./pages-orders.js";
import { tok } from "./kit.js";
import { $, $$, esc, toast, fail, money, fmtDT, fmtD, today, table, wireTable, chip, statusKind, modal, fieldsHTML, readFields, validate, here, fileToDataURL, skeleton, download } from "./ui.js";

export const STAGES = ["new", "contacted", "qualified", "proposal", "negotiation", "won", "lost"];
export const cap = s => s ? s[0].toUpperCase() + s.slice(1).replace(/_/g, " ") : "";
export const OUTCOMES = [["order_placed", "Order placed"], ["quotation_requested", "Quotation requested"], ["follow_up_required", "Follow-up required"], ["negotiation", "In negotiation"], ["not_interested", "Not interested"], ["no_answer", "No answer"], ["rescheduled", "Rescheduled"]];
const KINDS = [["call", "Call"], ["visit", "Visit"], ["email", "Email"], ["whatsapp", "WhatsApp"], ["meeting", "Meeting"]];
const tempChip = r => r.score != null ? `<span class="chip ${r.score_band === "hot" ? "bad" : r.score_band === "warm" ? "warn" : ""}" title="Lead score ${r.score}/100 (${r.score_band})">${r.score_band === "hot" ? "🔥 " : ""}${r.score}</span>` : "";
export { tempChip };
const nowLocal = (addH = 24) => { const d = new Date(Date.now() + addH * 36e5); d.setMinutes(0, 0, 0); return new Date(d - d.getTimezoneOffset() * 6e4).toISOString().slice(0, 16); };
const comm = r => [r.phone ? `<a class="btn sm" href="tel:${esc(r.phone)}" title="Call">📞</a>` : "", r.phone ? `<a class="btn sm" target="_blank" rel="noopener" href="https://wa.me/${esc(String(r.phone).replace(/\D/g, ""))}" title="WhatsApp">💬</a>` : "", r.email ? `<a class="btn sm" href="mailto:${esc(r.email)}" title="Email">✉️</a>` : ""].join(" ");

// ------------------------------------------------------------- lost / qualify / stage
export function askLostReason(done) {
  modal({ title: "Mark lead as lost", body: `<label class="f"><span>Why was it lost? *</span><select id="lr"><option value="">Select…</option>${["Price too high", "Chose a competitor", "No budget", "No response", "Requirement changed", "Not a fit", "Other"].map(x => `<option>${x}</option>`).join("")}</select></label><label class="f"><span>Details</span><input id="ld" placeholder="Optional"></label>`,
    actions: [{ label: "Cancel", close: true }, { label: "Mark lost", danger: true, onClick: async el => { const r = $("#lr", el).value; if (!r) throw new Error("Choose a reason."); await done(r + ($("#ld", el).value ? ": " + $("#ld", el).value : "")); } }] });
}
export async function moveStage(rec, stage, reload) {
  if (stage === rec.status) return;
  if (stage === "lost") return askLostReason(async reason => { await patch(`/crm/leads/${rec.id}`, { status: "lost", lost_reason: reason }); toast("Lead marked lost."); reload(); });
  try { await patch(`/crm/leads/${rec.id}`, { status: stage }); toast("Moved to " + cap(stage) + "."); reload(); } catch (e) { fail(e); }
}
export function qualifyModal(rec, S, done) {
  const q = rec.qualification || {};
  const row = (k, label, hint) => `<div style="margin-bottom:12px"><strong>${label}</strong> <span class="muted small">${hint}</span><div class="row" style="margin-top:4px">${[[0, "No"], [1, "Partly"], [2, "Yes"]].map(([v, l]) => `<label><input type="radio" name="${k}" value="${v}" ${(q[k] ?? "") === v ? "checked" : ""}> ${l}</label>`).join("")}</div></div>`;
  modal({ title: "Qualify lead: " + rec.name, body: `<p class="muted small">Answer honestly. A lead qualifies at ${S.tenant.settings.qualify_min_score ?? 5} of 8 points.</p>` +
    row("budget", "Budget", "Does the customer have a budget for this?") + row("authority", "Authority", "Are we talking to the decision maker?") + row("need", "Need", "Is there a clear, urgent requirement?") + row("timeline", "Timeline", "Do they plan to buy within 3 months?") +
    `<label class="f"><span>Expected close date</span><input type="date" id="qc" value="${rec.expected_close || ""}"></label><label class="f"><span>Notes</span><textarea id="qn">${esc(q.notes || "")}</textarea></label>`,
    actions: [{ label: "Cancel", close: true }, { label: "Save qualification", primary: true, onClick: async el => { const b = {}; for (const k of ["budget", "authority", "need", "timeline"]) { const c = $(`input[name=${k}]:checked`, el); if (!c) throw new Error("Answer all four questions."); b[k] = +c.value; } b.notes = $("#qn", el).value; b.expected_close = $("#qc", el).value || null;
      const r = await post(`/crm/leads/${rec.id}/qualify`, b); toast(r.qualified ? `Qualified (${r.score}/8). You can now send a quotation.` : `Score ${r.score}/8: needs ${r.needed} to qualify. Keep nurturing.`); done(); } }] });
}

// ------------------------------------------------------------- quotation
export async function quoteModal(target, products, S, done) {
  const cur = S.tenant.settings.currency || "INR", limit = S.tenant.settings.discount_approval_pct ?? 10; const qs0 = await get("/quote-settings").catch(() => ({ terms: "", charges: [], validity_days: 15, tax_pct: 18 }));
  const body = document.createElement("div");
  body.innerHTML = `<table class="stack" style="margin-bottom:8px"><thead><tr><th>Product</th><th style="width:70px">Qty</th><th style="width:100px">Rate</th><th style="width:70px">Disc %</th><th style="width:100px">Amount</th><th></th></tr></thead><tbody id="lines"></tbody></table>
    <button class="btn sm" id="addl" type="button">+ Add product</button><div class="formgrid" style="margin-top:12px"><label class="f"><span>Valid till</span><input type="date" id="vt" value="${new Date(Date.now() + (qs0.validity_days || 15) * 864e5).toISOString().slice(0, 10)}"></label><label class="f"><span>Tax %</span><input type="number" id="tx" value="${qs0.tax_pct ?? 18}"></label></div>
    <h3 style="margin:6px 0">Additional charges</h3><div id="chg"></div><div class="row"><button class="btn sm" id="achg" type="button">+ Add charge</button></div>
    <label class="f" style="margin-top:8px"><span>Terms &amp; conditions</span><textarea id="tc" style="min-height:110px">${esc(qs0.terms || "")}</textarea></label><label class="f"><span>Notes for the customer</span><textarea id="nt"></textarea></label><div id="tot" style="text-align:right;font-weight:600"></div><div id="warn" class="small" style="color:var(--warn);text-align:right"></div>`;
  const addLine = (pid = target.product_id || "") => {
    const tr = document.createElement("tr"); const p0 = products.find(p => p.id == pid);
    tr.innerHTML = `<td data-l="Product"><select class="pp">${products.map(p => `<option value="${p.id}" ${p.id == pid ? "selected" : ""}>${esc(p.code)} · ${esc(p.name)}</option>`).join("")}</select></td><td data-l="Qty"><input class="qq" type="number" min="1" value="1"></td><td data-l="Rate"><input class="rr" type="number" value="${p0?.price ?? products[0]?.price ?? 0}"></td><td data-l="Disc %"><input class="dd" type="number" min="0" max="100" value="0"></td><td class="am mono" data-l="Amount"></td><td><button class="btn sm" type="button" data-rm>✕</button></td>`;
    $("#lines", body).appendChild(tr);
  };
  const chg = (c = {}) => { const d = document.createElement("div"); d.className = "row"; d.style.marginBottom = "6px"; d.innerHTML = `<input class="cgn" placeholder="e.g. Freight" value="${esc(c.name || "")}" style="width:160px"><input class="cga" type="number" placeholder="Amount" value="${c.amount ?? ""}" style="width:120px"><button class="btn sm" type="button" data-rc>✕</button>`; $("#chg", body).appendChild(d); };
  (qs0.charges || []).filter(c => c.type === "fixed" && c.value).forEach(c => chg({ name: c.name, amount: c.value })); $("#achg", body).onclick = () => chg();
  body.addEventListener("click", e => { if (e.target.matches("[data-rc]")) { e.target.closest(".row").remove(); calc(); } });
  const calc = () => { let sub = 0, mx = 0; $$("#lines tr", body).forEach(tr => { const q = +$(".qq", tr).value || 0, r = +$(".rr", tr).value || 0, d = +$(".dd", tr).value || 0; const a = q * r * (1 - d / 100); sub += a; mx = Math.max(mx, d); $(".am", tr).textContent = money(a, cur); });
    const tax = +$("#tx", body).value || 0, ch = $$("#chg .cga", body).reduce((a, i) => a + (+i.value || 0), 0); $("#tot", body).textContent = `Subtotal ${money(sub, cur)}${ch ? " + charges " + money(ch, cur) : ""} · Total incl. tax ${money((sub + ch) * (1 + tax / 100), cur)}`; $("#warn", body).textContent = mx > limit ? `Discount above ${limit}% needs manager approval before it goes to ERPNext.` : ""; };
  body.addEventListener("input", calc);
  body.addEventListener("change", e => { if (e.target.classList.contains("pp")) { const tr = e.target.closest("tr"); $(".rr", tr).value = products.find(p => p.id == e.target.value)?.price ?? 0; calc(); } });
  body.addEventListener("click", e => { if (e.target.matches("[data-rm]") && $$("#lines tr", body).length > 1) { e.target.closest("tr").remove(); calc(); } });
  $("#addl", body).onclick = () => { addLine(products[0]?.id); calc(); }; addLine(); calc();
  modal({ title: "New quotation", body, wide: true, actions: [{ label: "Cancel", close: true }, { label: "Create quotation", primary: true, onClick: async () => {
    const items = $$("#lines tr", body).map(tr => ({ product_id: +$(".pp", tr).value, qty: +$(".qq", tr).value, rate: +$(".rr", tr).value, discount_pct: +$(".dd", tr).value || 0 }));
    const q = await post("/quotations", { ...target.key, items, valid_till: $("#vt", body).value || null, tax_pct: +$("#tx", body).value || 0, notes: $("#nt", body).value, terms: $("#tc", body).value, charges: $$("#chg .row", body).map(r => ({ name: $(".cgn", r).value.trim(), amount: +$(".cga", r).value || 0 })).filter(c => c.name) });
    if (q.status === "pending_approval") toast(`${q.number} saved. Waiting for manager approval.`); else if (q.erp_name) toast(`${q.number} created in ERPNext as ${q.erp_name}.`); else toast(q.erp_error ? `${q.number} saved, but ERPNext failed: ${q.erp_error}` : `${q.number} saved (ERPNext integration is off).`, q.erp_error ? "err" : "ok"); done(); } }] });
}

// ------------------------------------------------------------- follow-up modal
export function followupModal(target, users, S, done, rec = null) {
  const f = [{ k: "kind", label: "Type", type: "select", options: KINDS, def: rec?.kind || "call" }, { k: "due_at", label: "When", type: "datetime", req: true, def: rec?.due_at || nowLocal() }, { k: "owner_id", label: "Owner", type: "select", options: users.map(u => [u.id, u.name]), def: rec?.owner_id || S.user.id },
    { k: "repeat", label: "Repeat (recurring visit plan)", type: "select", options: [["daily", "Daily"], ["weekly", "Weekly"], ["monthly", "Monthly"]], ph: "Does not repeat" }, { k: "notes", label: "What to discuss", type: "textarea", full: true, def: rec?.notes }];
  modal({ title: rec ? "Reschedule follow-up" : "Schedule follow-up", body: fieldsHTML(f), actions: [{ label: "Cancel", close: true }, { label: "Save", primary: true, onClick: async el => { validate(el, f); const d = readFields(el, f); d.owner_id = +d.owner_id;
    if (rec) await patch("/followups/" + rec.id, { due_at: d.due_at, notes: d.notes, kind: d.kind }); else await post("/followups", { ...target, ...d, repeat: d.repeat || "" }); toast("Follow-up saved."); done(); } }] });
}
export function completeFollowup(fu, done) {
  const f = [{ k: "outcome", label: "Outcome", type: "select", req: true, options: OUTCOMES }, { k: "notes", label: "Notes", type: "textarea", full: true }, { k: "next_at", label: "Schedule next follow-up", type: "datetime" }, { k: "next_kind", label: "Next type", type: "select", options: KINDS, def: "call" }];
  modal({ title: "Complete follow-up: " + (fu.customer_name || fu.lead_name || ""), body: fieldsHTML(f), actions: [{ label: "Cancel", close: true }, { label: "Mark done", primary: true, onClick: async el => { validate(el, f); const d = readFields(el, f); await post(`/followups/${fu.id}/complete`, { outcome: d.outcome, notes: d.notes, next_at: d.next_at, next_kind: d.next_kind || "call" }); toast("Follow-up completed."); done(); } }] });
}

// ------------------------------------------------------------- meeting modals
export function meetingModal(target, customers, leads, users, S, done) {
  const f = [{ k: "title", label: "Meeting title", req: true, full: true }, { k: "customer_id", label: "Customer", type: "select", options: customers.map(c => [c.id, c.name]), def: target.customer_id }, { k: "lead_id", label: "Lead", type: "select", options: leads.map(c => [c.id, c.name]), def: target.lead_id },
    { k: "scheduled_at", label: "When", type: "datetime", req: true, def: nowLocal(24) }, { k: "duration_min", label: "Duration (min)", type: "number", def: 45 }, { k: "mode", label: "Mode", type: "select", options: [["in_person", "In person"], ["call", "Phone call"], ["video", "Video call"]], def: "in_person" },
    { k: "owner_id", label: "Owner", type: "select", options: users.map(u => [u.id, u.name]), def: S.user.id }, { k: "location", label: "Location / link", full: true }, { k: "attendees", label: "Customer attendees", full: true }, { k: "agenda", label: "Agenda", type: "textarea", full: true }];
  modal({ title: "Schedule meeting", wide: true, body: fieldsHTML(f), actions: [{ label: "Cancel", close: true }, { label: "Schedule", primary: true, onClick: async el => { validate(el, f); const d = readFields(el, f); ["customer_id", "lead_id", "owner_id"].forEach(k => d[k] = d[k] ? +d[k] : null); if (!d.customer_id && !d.lead_id) throw new Error("Pick a customer or a lead."); d.duration_min = d.duration_min || 45; await post("/meetings", d); toast("Meeting scheduled."); done(); } }] });
}
function signaturePad(host) {
  host.innerHTML = `<canvas width="480" height="140" style="width:100%;border:1px dashed var(--border);border-radius:8px;background:#fff;touch-action:none"></canvas><button type="button" class="btn sm" data-clear>Clear</button>`;
  const c = $("canvas", host), g = c.getContext("2d"); g.lineWidth = 2.2; g.lineCap = "round"; g.strokeStyle = "#101828"; let on = false, dirty = false;
  const pos = e => { const r = c.getBoundingClientRect(); return [(e.clientX - r.left) * c.width / r.width, (e.clientY - r.top) * c.height / r.height]; };
  c.onpointerdown = e => { on = true; dirty = true; g.beginPath(); g.moveTo(...pos(e)); c.setPointerCapture(e.pointerId); }; c.onpointermove = e => { if (on) { g.lineTo(...pos(e)); g.stroke(); } }; c.onpointerup = () => on = false;
  $("[data-clear]", host).onclick = () => { g.clearRect(0, 0, c.width, c.height); dirty = false; };
  return () => dirty ? c.toDataURL("image/png") : null;
}
export function completeMeeting(m, users, S, done) {
  const body = document.createElement("div");
  body.innerHTML = `<label class="f"><span>Outcome *</span><select id="oc"><option value="">Select…</option>${OUTCOMES.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select></label><label class="f"><span>Minutes of meeting *</span><textarea id="mom" placeholder="Discussion, decisions, commitments"></textarea></label>
    <h3>Action items</h3><div id="ai"></div><button class="btn sm" id="aai" type="button" style="margin:6px 0 12px">+ Add action item</button>
    <div class="formgrid"><label class="f"><span>Next follow-up</span><input type="datetime-local" id="nf"></label><label class="f"><span>Email MOM to</span><input id="em" placeholder="extra emails, comma-separated" value="${esc(S.tenant.settings.mom_recipients_default || "")}"></label></div>
    <label class="f"><span>Photo (proof of meeting)</span><input type="file" id="ph" accept="image/*" capture="environment"></label><div class="f"><span style="font-weight:550;font-size:13px">Customer signature</span><div id="sig"></div></div>`;
  const getSig = signaturePad($("#sig", body));
  $("#aai", body).onclick = () => { const d = document.createElement("div"); d.className = "row"; d.style.marginBottom = "6px"; d.innerHTML = `<input class="grow ait" placeholder="Action item"><select class="aia" style="width:auto">${users.map(u => `<option value="${u.id}" ${u.id === S.user.id ? "selected" : ""}>${esc(u.name)}</option>`).join("")}</select><input type="date" class="aid" style="width:auto">`; $("#ai", body).appendChild(d); };
  $("#aai", body).click();
  modal({ title: "Complete meeting: " + m.title, body, wide: true, actions: [{ label: "Cancel", close: true }, { label: "Complete & send MOM", primary: true, onClick: async () => {
    if (!$("#oc", body).value) throw new Error("Choose the outcome."); if ($("#mom", body).value.trim().length < 3) throw new Error("Write the minutes of meeting.");
    const p = await here().catch(() => ({})); const ph = $("#ph", body).files[0];
    const items = $$("#ai > div", body).map(d => ({ title: $(".ait", d).value.trim(), assignee_id: +$(".aia", d).value, due_at: $(".aid", d).value ? new Date($(".aid", d).value + "T17:00").toISOString().slice(0, 19) : null })).filter(i => i.title);
    const r = await post(`/meetings/${m.id}/complete`, { outcome: $("#oc", body).value, mom: $("#mom", body).value, signature: getSig(), photo: ph ? await fileToDataURL(ph) : null, action_items: items, next_followup_at: $("#nf", body).value ? new Date($("#nf", body).value).toISOString().slice(0, 19) : null, email_to: $("#em", body).value, lat: p.lat, lng: p.lng });
    toast(`Meeting completed. ${r.tasks_created} task(s) created.`); done(); } }] });
}

// ------------------------------------------------------------- related panel inside lead/customer drawer
export async function relatedPanel(host, entity, rec, ctx) {
  const { S, users, products, customers, leads, reload } = ctx, cur = S.tenant.settings.currency || "INR";
  const key = entity === "leads" ? { lead_id: rec.id } : { customer_id: rec.id };
  const r = await get(`/crm/${entity}/${rec.id}/related`), isMgr = ["manager", "admin", "owner"].includes(S.user.role);
  const refresh = async () => { host.innerHTML = ""; await relatedPanel(host, entity, rec, ctx); };
  const qBlock = `<div class="row" style="margin:18px 0 6px"><h3>Quotations</h3><button class="btn sm right" id="nq">+ Quotation</button></div>` + (r.quotations.length ? r.quotations.map(q => `<div class="card" style="padding:10px;margin-bottom:8px"><div class="row"><strong>${esc(q.number)}</strong>${q.erp_name ? chip("ERPNext " + q.erp_name, "ok") : q.status === "pending_approval" ? chip("awaiting approval", "warn") : chip("not in ERPNext", "warn")}${chip(cap(q.status), statusKind(q.status))}<strong class="right mono">${money(q.total, cur)}</strong></div>
      <div class="small muted">${q.items.map(i => `${esc(i.name)} × ${i.qty}`).join(", ")} · valid till ${fmtD(q.valid_till)}${q.erp_status ? " · ERP status " + esc(q.erp_status) : ""}</div>${q.erp_error ? `<div class="small" style="color:var(--danger)">ERPNext: ${esc(q.erp_error)}</div>` : ""}
      <div class="row" style="margin-top:6px">${q.status !== "pending_approval" ? `<a class="btn sm" target="_blank" rel="noopener" href="/api/quotations/${q.id}/pdf-view?t=${tok()}">📄 PDF</a><button class="btn sm" data-qm="${q.id}">✉ Send</button>` : ""}${q.status === "pending_approval" && isMgr ? `<button class="btn sm primary" data-qa="${q.id}">Approve discount</button>` : ""}${!q.erp_name && q.status !== "pending_approval" ? `<button class="btn sm" data-qp="${q.id}">Push to ERPNext</button>` : ""}${q.erp_name ? `<button class="btn sm" data-qr="${q.id}">Refresh from ERPNext</button>` : ""}
      ${["sent", "accepted"].includes(q.status) && q.status !== "pending_approval" ? `<button class="btn sm primary" data-qo="${q.id}">Create order</button>` : ""}${["draft"].includes(q.status) ? `<button class="btn sm" data-qs="${q.id}:sent">Mark sent</button>` : ""}${["draft", "sent"].includes(q.status) ? `<button class="btn sm" data-qs="${q.id}:accepted">Customer accepted</button><button class="btn sm danger" data-qs="${q.id}:rejected">Rejected</button>` : ""}</div></div>`).join("") : `<div class="muted small">No quotations yet.${entity === "leads" && !rec.qualified_at ? " Qualify the lead first." : ""}</div>`);
  const fBlock = `<div class="row" style="margin:18px 0 6px"><h3>Follow-ups</h3><button class="btn sm right" id="nfu">+ Follow-up</button></div>` + (r.followups.length ? r.followups.map(f => `<div class="row" style="padding:6px 0;border-bottom:1px solid var(--border)"><span>${{ call: "📞", visit: "📍", email: "✉️", whatsapp: "💬", meeting: "🤝" }[f.kind]}</span><div class="grow"><div class="small">${esc(f.notes) || cap(f.kind)}</div><div class="small ${f.status === "pending" && new Date(f.due_at) < new Date() ? "" : "muted"}" style="${f.status === "pending" && new Date(f.due_at) < new Date() ? "color:var(--danger)" : ""}">${fmtDT(f.due_at)} · ${esc(f.owner_name)}${f.outcome ? " · " + cap(f.outcome) : ""}</div></div>${f.status === "pending" ? `<button class="btn sm" data-fd="${f.id}">Done</button>` : chip(cap(f.status), statusKind(f.status))}</div>`).join("") : `<div class="muted small">Nothing scheduled.</div>`);
  const mBlock = `<div class="row" style="margin:18px 0 6px"><h3>Meetings</h3><button class="btn sm right" id="nm">+ Meeting</button></div>` + (r.meetings.length ? r.meetings.map(m => `<div class="row" style="padding:6px 0;border-bottom:1px solid var(--border)"><div class="grow"><strong class="small">${esc(m.title)}</strong><div class="small muted">${fmtDT(m.scheduled_at)} · ${cap(m.mode)}${m.outcome ? " · " + cap(m.outcome) : ""}${m.task_count ? ` · ${m.task_count} task(s)` : ""}</div></div>${chip(cap(m.status), statusKind(m.status))}</div>`).join("") : `<div class="muted small">No meetings.</div>`);
  let oBlock = "";
  if (entity === "customers") {
    const [od, ob] = await Promise.all([get("/orders" + qs({ scope: "team", customer_id: rec.id })), get("/collections/outstanding" + qs({ customer_id: rec.id }))]);
    const c = ob.customers[0];
    oBlock = `<div class="row" style="margin:18px 0 6px"><h3>Orders &amp; collections</h3><button class="btn sm right" id="no">+ Order</button><button class="btn sm" id="nc">Collect</button></div>
      <div class="small" style="margin-bottom:6px">Outstanding <b>${money(c?.outstanding || 0, cur)}</b>${c?.overdue ? ` · <span style="color:var(--danger)">${money(c.overdue, cur)} overdue</span>` : ""}${rec.credit_limit ? ` · credit limit ${money(rec.credit_limit, cur)}` : ""}</div>` +
      (od.length ? od.slice(0, 8).map(o => `<div class="row small" style="padding:5px 0;border-bottom:1px solid var(--border)"><b>${esc(o.number)}</b><span class="grow muted">${fmtD(o.created_at)}${o.erp_name ? " · ERP " + esc(o.erp_name) : ""}</span><span class="mono">${money(o.total, cur)}</span>${chip(cap(o.status))}</div>`).join("") : `<div class="muted small">No orders yet.</div>`);
  }
  const tBlock = `<h3 style="margin:18px 0 6px">Related tasks</h3>` + (r.tasks.length ? r.tasks.map(t => `<div class="row small" style="padding:5px 0;border-bottom:1px solid var(--border)"><span class="grow">${esc(t.title)}</span><span class="muted">${t.due_at ? fmtD(t.due_at) : ""}</span>${chip(cap(t.status), statusKind(t.status))}</div>`).join("") : `<div class="muted small">No tasks linked.</div>`);
  host.innerHTML = qBlock + oBlock + fBlock + mBlock + tBlock;
  const done = async () => { await refresh(); reload?.(); };
  $("#nq", host).onclick = () => { if (!products.length) return toast("Add products to the product master first.", "err"); if (entity === "leads" && !rec.qualified_at) return toast("Qualify the lead first.", "err"); quoteModal({ key, product_id: rec.product_id }, products, S, done); };
  const allCusts = () => customers.length ? customers : [];
  $("#no", host)?.addEventListener("click", () => orderModal(S, { customers: allCusts(), products, customer_id: rec.id }, refresh));
  $("#nc", host)?.addEventListener("click", () => collectModal(S, { customers: allCusts(), customer_id: rec.id }, refresh));
  $$("[data-qo]", host).forEach(b => b.onclick = () => orderModal(S, { customers: allCusts(), products, customer_id: rec.customer_id || rec.converted_customer_id || null, quotation: r.quotations.find(q => q.id == b.dataset.qo) }, done));
  $("#nfu", host).onclick = () => followupModal(key, users, S, refresh);
  $("#nm", host).onclick = () => meetingModal(key, customers, leads, users, S, refresh);
  $$("[data-fd]", host).forEach(b => b.onclick = () => completeFollowup(r.followups.find(f => f.id == b.dataset.fd), refresh));
  const act = (sel, fn) => $$(sel, host).forEach(b => b.onclick = async () => { b.disabled = true; try { await fn(b); await done(); } catch (e) { fail(e); b.disabled = false; } });
  $$("[data-qm]", host).forEach(b => b.onclick = () => sendQuoteModal(r.quotations.find(q => q.id == b.dataset.qm), rec, S, done));
  act("[data-qa]", b => post(`/quotations/${b.dataset.qa}/approve`)); act("[data-qp]", async b => { const q = await post(`/quotations/${b.dataset.qp}/push`); toast("Created in ERPNext: " + q.erp_name); });
  act("[data-qr]", async b => { const q = await post(`/quotations/${b.dataset.qr}/refresh`); toast("ERPNext status: " + q.erp_status); }); act("[data-qs]", b => { const [id, st] = b.dataset.qs.split(":"); return post(`/quotations/${id}/status`, { status: st }); });
}

// ============================================================ Follow-ups page
export async function followups(root, S) {
  const [users, leads, custs] = await Promise.all([get("/users"), get("/crm/leads?limit=500").then(r => r.items), get("/crm/customers?limit=500").then(r => r.items)]);
  const isMgr = ["manager", "admin", "owner"].includes(S.user.role); let tab = "today", scope = isMgr ? "team" : "mine", rows = [];
  root.innerHTML = `<div class="page-h"><h1>Follow-ups</h1><div class="right row">${isMgr ? `<select id="sc" style="width:auto"><option value="team">My team</option><option value="mine">My follow-ups</option></select>` : ""}<button class="btn primary" id="new">+ Follow-up</button></div></div><div id="kp" class="grid g4 kpis" style="margin-bottom:12px"></div><div class="tabs">${[["overdue", "Overdue"], ["today", "Today"], ["upcoming", "Upcoming"], ["done", "Done"]].map(([k, l]) => `<button data-t="${k}">${l}</button>`).join("")}</div><div id="list">${skeleton(4)}</div>`;
  const sod = new Date(); sod.setHours(0, 0, 0, 0); const eod = new Date(sod.getTime() + 864e5);
  const load = async () => {
    const [open, done] = await Promise.all([get("/followups" + qs({ scope, bucket: "open" })), get("/followups" + qs({ scope, bucket: "done" }))]);
    rows = { overdue: open.filter(f => new Date(f.due_at) < new Date()), today: open.filter(f => new Date(f.due_at) >= new Date() && new Date(f.due_at) < eod), upcoming: open.filter(f => new Date(f.due_at) >= eod), done: done.slice(0, 100) };
    $("#kp").innerHTML = [["overdue", "Overdue"], ["today", "Due today"], ["upcoming", "Upcoming"]].map(([k, l]) => `<div class="card kpi"><div class="v" style="${k === "overdue" && rows.overdue.length ? "color:var(--danger)" : ""}">${rows[k].length}</div><div class="l">${l}</div></div>`).join("");
    paint();
  };
  const paint = () => {
    $$(".tabs button").forEach(b => b.classList.toggle("on", b.dataset.t === tab));
    const list = rows[tab];
    $("#list").innerHTML = table([{ h: "", f: r => ({ call: "📞", visit: "📍", email: "✉️", whatsapp: "💬", meeting: "🤝" }[r.kind]) }, { h: "Who", f: r => `<strong>${esc(r.customer_name || r.lead_name || "—")}</strong><div class="small muted">${r.lead_id ? "Lead" : "Customer"}${r.repeat ? " · ↻ " + r.repeat : ""}</div>` }, { h: "Notes", f: r => esc(r.notes) || "—" },
      { h: tab === "done" ? "Done" : "Due", f: r => tab === "done" ? fmtDT(r.completed_at) + `<div class="small muted">${cap(r.outcome)}</div>` : `<span style="${r.overdue ? "color:var(--danger)" : ""}">${fmtDT(r.due_at)}</span>` }, ...(scope === "team" ? [{ h: "Owner", f: r => esc(r.owner_name) }] : []),
      { h: "", f: r => tab === "done" ? "" : `<span class="nowrap">${comm(r)} <button class="btn sm primary" data-d="${r.id}">Done</button> <button class="btn sm" data-r="${r.id}">Reschedule</button></span>` }], list, { empty: tab === "overdue" ? "Nothing overdue. Nice work." : "Nothing here." });
    $$("[data-d]", root).forEach(b => b.onclick = () => completeFollowup(list.find(f => f.id == b.dataset.d), load));
    $$("[data-r]", root).forEach(b => b.onclick = () => followupModal({}, users, S, load, list.find(f => f.id == b.dataset.r)));
  };
  $$(".tabs button").forEach(b => b.onclick = () => { tab = b.dataset.t; paint(); });
  $("#sc")?.addEventListener("change", e => { scope = e.target.value; load(); });
  $("#new").onclick = () => modal({ title: "New follow-up for…", body: `<label class="f"><span>Lead or customer</span><select id="tg"><optgroup label="Leads">${leads.filter(l => !["won", "lost"].includes(l.status)).map(l => `<option value="l${l.id}">${esc(l.name)} · ${esc(l.company)}</option>`).join("")}</optgroup><optgroup label="Customers">${custs.map(c => `<option value="c${c.id}">${esc(c.name)}</option>`).join("")}</optgroup></select></label>`,
    actions: [{ label: "Cancel", close: true }, { label: "Next", primary: true, onClick: el => { const v = $("#tg", el).value; if (!v) throw new Error("Choose who this is for."); setTimeout(() => followupModal(v[0] === "l" ? { lead_id: +v.slice(1) } : { customer_id: +v.slice(1) }, users, S, load), 50); } }] });
  await load(); if (rows.overdue.length) { tab = "overdue"; paint(); }
}

// ============================================================ Meetings page
export async function meetings(root, S) {
  const [users, leads, custs] = await Promise.all([get("/users"), get("/crm/leads?limit=500").then(r => r.items), get("/crm/customers?limit=500").then(r => r.items)]);
  const isMgr = ["manager", "admin", "owner"].includes(S.user.role); let tab = "upcoming", scope = isMgr ? "team" : "mine", all = [];
  root.innerHTML = `<div class="page-h"><h1>Customer meetings</h1><div class="right row">${isMgr ? `<select id="sc" style="width:auto"><option value="team">My team</option><option value="mine">My meetings</option></select>` : ""}<button class="btn primary" id="new">+ Schedule meeting</button></div></div><div class="tabs">${[["upcoming", "Upcoming"], ["progress", "In progress"], ["done", "Completed"], ["cancelled", "Cancelled"]].map(([k, l]) => `<button data-t="${k}">${l}</button>`).join("")}</div><div id="list">${skeleton(4)}</div>`;
  const load = async () => { all = await get("/meetings" + qs({ scope })); paint(); };
  const paint = () => {
    $$(".tabs button").forEach(b => b.classList.toggle("on", b.dataset.t === tab));
    const f = { upcoming: m => m.status === "scheduled", progress: m => m.status === "in_progress", done: m => m.status === "completed", cancelled: m => m.status === "cancelled" }[tab], rows = all.filter(f).sort((a, b) => tab === "upcoming" ? new Date(a.scheduled_at) - new Date(b.scheduled_at) : new Date(b.scheduled_at) - new Date(a.scheduled_at));
    $("#list").innerHTML = table([{ h: "Meeting", f: r => `<strong>${esc(r.title)}</strong><div class="small muted">${esc(r.customer_name || r.lead_name || "")} · ${cap(r.mode)}</div>` }, { h: "When", f: r => fmtDT(r.scheduled_at) + `<div class="small muted">${r.duration_min} min</div>` }, ...(scope === "team" ? [{ h: "Owner", f: r => esc(r.owner_name) }] : []),
      { h: "Outcome", f: r => r.outcome ? chip(cap(r.outcome), r.outcome === "order_placed" ? "ok" : r.outcome === "not_interested" ? "bad" : "info") : "—" }, { h: "Tasks", f: r => r.task_count || "—" },
      { h: "", f: r => r.status === "scheduled" ? `<button class="btn sm primary" data-s="${r.id}">Check in</button> <button class="btn sm" data-c="${r.id}">Complete</button> <button class="btn sm" data-x="${r.id}">Cancel</button>` : r.status === "in_progress" ? `<button class="btn sm primary" data-c="${r.id}">Complete</button>` : r.signature ? `<a class="small" target="_blank" rel="noopener" href="${fileUrl(r.signature)}">Signature</a>` : "" }], rows, { empty: "No meetings here. Schedule one with a customer or lead." });
    $$("[data-s]", root).forEach(b => b.onclick = async () => { b.disabled = true; try { const p = await here().catch(() => ({})); await post(`/meetings/${b.dataset.s}/start`, { lat: p.lat, lng: p.lng }); toast("Checked in. Meeting started."); tab = "progress"; load(); } catch (e) { fail(e); b.disabled = false; } });
    $$("[data-c]", root).forEach(b => b.onclick = () => completeMeeting(all.find(m => m.id == b.dataset.c), users, S, load));
    $$("[data-x]", root).forEach(b => b.onclick = async () => { if (confirm("Cancel this meeting?")) { await patch("/meetings/" + b.dataset.x, { status: "cancelled" }); load(); } });
  };
  $$(".tabs button").forEach(b => b.onclick = () => { tab = b.dataset.t; paint(); });
  $("#sc")?.addEventListener("change", e => { scope = e.target.value; load(); });
  $("#new").onclick = () => meetingModal({}, custs, leads, users, S, load);
  await load();
}

// ============================================================ Reports
export async function reports(root, S) {
  const isMgr = ["manager", "admin", "owner"].includes(S.user.role), cur = S.tenant.settings.currency || "INR";
  root.innerHTML = `<div class="page-h"><h1>Reports &amp; forecast</h1></div><div class="tabs">${[["forecast", "Sales forecast"], ["funnel", "Funnel & win rate"], ["dcr", "Daily call report"], ["targets", "Targets"]].map(([k, l], i) => `<button data-t="${k}" class="${i ? "" : "on"}">${l}</button>`).join("")}</div><div id="pane">${skeleton(4)}</div>`;
  const pane = $("#pane"); const bar = (v, max, c = "var(--accent)") => `<div style="height:14px;background:var(--surface-2);border-radius:5px;min-width:80px"><div style="width:${max ? Math.max(2, v / max * 100) : 0}%;height:100%;background:${c};border-radius:5px"></div></div>`;
  const tabs = {
    async forecast() {
      const f = await get("/reports/forecast"); const mx = Math.max(1, ...f.by_month.map(m => m.value));
      pane.innerHTML = `<div class="grid g4 kpis" style="margin-bottom:14px">${[["Open pipeline", money(f.open_value, cur), f.open_count + " leads"], ["Weighted forecast", money(f.weighted, cur), "value × stage probability"], ["Commit (negotiation)", money(f.commit, cur), "most likely to close"], ["Won this month", money(f.won_this_month, cur), ""]].map(([l, v, s]) => `<div class="card kpi"><div class="v" style="font-size:22px">${v}</div><div class="l">${l}<br>${s}</div></div>`).join("")}</div>
      <div class="grid g2"><div class="card"><h2>By expected close month</h2>${f.by_month.length ? f.by_month.map(m => `<div class="row" style="margin:6px 0"><span style="width:90px" class="small">${m.key}</span><div class="grow">${bar(m.value, mx, "var(--border)")}<div style="margin-top:-14px">${bar(m.weighted, mx)}</div></div><span class="small mono" style="width:130px;text-align:right">${money(m.weighted, cur)} / ${money(m.value, cur)}</span></div>`).join("") + `<div class="small muted">Dark = weighted, light = total. Set expected close dates when qualifying leads.</div>` : '<div class="empty">No open leads.</div>'}</div>
      <div class="card"><h2>By stage</h2>${f.by_stage.map(s => `<div class="row small" style="padding:5px 0;border-bottom:1px solid var(--border)"><span style="width:100px">${cap(s.key)}</span><span class="muted">${f.probabilities[s.key] ?? 0}%</span><span class="grow"></span><span>${s.count} leads</span><span class="mono" style="width:120px;text-align:right">${money(s.weighted, cur)}</span></div>`).join("")}<h2 style="margin-top:16px">By owner</h2>${f.by_owner.map(s => `<div class="row small" style="padding:5px 0;border-bottom:1px solid var(--border)"><span class="grow">${esc(s.key)}</span><span>${s.count}</span><span class="mono" style="width:120px;text-align:right">${money(s.weighted, cur)}</span></div>`).join("")}</div></div>`;
    },
    async funnel() {
      const f = await get("/reports/funnel?days=90"), mx = Math.max(1, f.stages[0].count);
      pane.innerHTML = `<div class="grid g4 kpis" style="margin-bottom:14px"><div class="card kpi"><div class="v">${f.total}</div><div class="l">Leads created (90 days)</div></div><div class="card kpi"><div class="v">${f.win_rate ?? "—"}${f.win_rate != null ? "%" : ""}</div><div class="l">Win rate (won ÷ closed)</div></div><div class="card kpi"><div class="v">${f.avg_days_to_win ?? "—"}</div><div class="l">Avg days to win</div></div><div class="card kpi"><div class="v" style="${f.stale ? "color:var(--warn)" : ""}">${f.stale}</div><div class="l">Stale leads (14+ days in stage)</div></div></div>
      <div class="grid g2"><div class="card"><h2>Conversion funnel</h2>${f.stages.map((s, i) => `<div class="row" style="margin:7px 0"><span style="width:92px" class="small">${cap(s.stage)}</span><div class="grow">${bar(s.count, mx)}</div><span class="small mono" style="width:90px;text-align:right">${s.count}${i ? ` · ${f.stages[i - 1].count ? Math.round(s.count / f.stages[i - 1].count * 100) : 0}%` : ""}</span></div>`).join("")}</div>
      <div class="card"><h2>Why we lose</h2>${f.lost_reasons.length ? f.lost_reasons.map(r => `<div class="row small" style="padding:5px 0;border-bottom:1px solid var(--border)"><span class="grow">${esc(r.reason)}</span><b>${r.count}</b></div>`).join("") : '<div class="muted">No lost leads yet.</div>'}<h2 style="margin-top:16px">Lead sources</h2>${f.by_source.map(r => `<div class="row small" style="padding:5px 0;border-bottom:1px solid var(--border)"><span class="grow">${esc(r.source)}</span><span>${r.leads} leads</span><b>${r.won} won</b></div>`).join("")}</div></div>`;
    },
    async dcr() {
      pane.innerHTML = `<div class="row" style="margin-bottom:12px"><input type="date" id="dd" value="${today()}" style="width:auto"><button class="btn sm" id="dx">Export CSV</button></div><div id="dl">${skeleton(3)}</div>`; let rows = [];
      const load = async () => { rows = await get("/reports/dcr" + qs({ day: $("#dd").value })); $("#dl").innerHTML = table([{ h: "Executive", f: r => `<strong>${esc(r.name)}</strong>` }, { h: "Attendance", f: r => chip(cap(r.status), statusKind(r.status)) }, { h: "Km", f: r => r.km }, { h: "Visits", f: r => r.visits }, { h: "Meetings", f: r => r.meetings }, { h: "Calls (productive)", f: r => `${r.calls_done} (${r.productive_calls})` }, { h: "New leads", f: r => r.new_leads }, { h: "Quotes", f: r => `${r.quotations} · ${money(r.quoted_value, cur)}` }, { h: "Orders", f: r => `${r.orders_booked} · ${money(r.order_value, cur)}` }, { h: "Collected", f: r => money(r.collected, cur) }, { h: "Tasks done", f: r => r.tasks_done }, { h: "Overdue f/u", f: r => r.overdue_followups ? `<span style="color:var(--danger)">${r.overdue_followups}</span>` : 0 }, { h: "Expenses", f: r => money(r.expenses, cur) }], rows, { empty: "No data for this day." }); };
      $("#dd").onchange = load; $("#dx").onclick = () => download("dcr-" + $("#dd").value + ".csv", ["name,day,status,km,visits,meetings,calls,productive_calls,new_leads,quotations,orders,tasks_done,overdue_followups,expenses,orders_booked,collected", ...rows.map(r => [r.name, r.day, r.status, r.km, r.visits, r.meetings, r.calls_done, r.productive_calls, r.new_leads, r.quotations, r.orders, r.tasks_done, r.overdue_followups, r.expenses].join(","))].join("\n")); await load();
      pane.insertAdjacentHTML("beforeend", `<p class="small muted" style="margin-top:10px">A summary is also sent to managers after 19:30 each day (turn off in Settings).</p>`);
    },
    async targets() {
      const t = await get("/reports/targets"); const pct = (a, b) => b ? Math.round(a / b * 100) : null;
      pane.innerHTML = `<div class="row" style="margin-bottom:10px"><h2>${t.month}</h2></div>` + table([{ h: "Executive", f: r => `<strong>${esc(r.name)}</strong>` }, { h: "Revenue won", f: r => { const p = pct(r.revenue, r.target_revenue); return `${money(r.revenue, cur)} / ${r.target_revenue ? money(r.target_revenue, cur) : "—"}${p != null ? bar(Math.min(p, 100), 100, p >= 100 ? "var(--ok)" : "var(--accent)") + `<span class="small muted">${p}%</span>` : ""}`; } },
        { h: "Visits & meetings", f: r => `${r.visits} / ${r.target_visits || "—"}` }, { h: "New leads", f: r => `${r.new_leads} / ${r.target_leads || "—"}` }, ...(isMgr ? [{ h: "", f: r => `<button class="btn sm" data-t="${r.user_id}">Set target</button>` }] : [])], t.rows, { empty: "No team members yet." });
      $$("[data-t]", pane).forEach(b => b.onclick = () => { const r = t.rows.find(x => x.user_id == b.dataset.t), f = [{ k: "revenue", label: "Revenue target", type: "number", def: r.target_revenue }, { k: "visits", label: "Visits + meetings", type: "number", def: r.target_visits }, { k: "new_leads", label: "New leads", type: "number", def: r.target_leads }];
        modal({ title: `Target for ${r.name} (${t.month})`, body: fieldsHTML(f), actions: [{ label: "Cancel", close: true }, { label: "Save", primary: true, onClick: async el => { const d = readFields(el, f); await put("/reports/targets", { user_id: r.user_id, month: t.month, revenue: d.revenue || 0, visits: d.visits || 0, new_leads: d.new_leads || 0 }); toast("Target saved."); tabs.targets(); } }] }); });
    } };
  $$(".tabs button").forEach(b => b.onclick = async () => { $$(".tabs button").forEach(x => x.classList.toggle("on", x === b)); pane.innerHTML = skeleton(4); try { await tabs[b.dataset.t](); } catch (e) { fail(e); } });
  await tabs.forecast();
}


// ------------------------------------------------------------- send quotation (email / WhatsApp / custom PDF)
export async function sendQuoteModal(q, rec, S, done) {
  const tpls = await get("/templates").catch(() => []);
  const body = document.createElement("div");
  body.innerHTML = `<div class="tabs" style="margin-bottom:10px"><button data-ch="email" class="on">Email</button><button data-ch="whatsapp">WhatsApp</button><button data-ch="custom">Use my own PDF</button></div>
    <div id="sp-email"><label class="f"><span>To</span><input id="se" value="${esc(rec?.email || "")}" placeholder="customer@company.com"></label><label class="f"><span>Subject</span><input id="ss"></label></div>
    <div id="sp-whatsapp" style="display:none"><label class="f"><span>Mobile</span><input id="sw" value="${esc(rec?.phone || "")}"></label></div>
    <div id="sp-custom" style="display:none"><p class="muted small">Upload your own quotation PDF. It replaces the generated one everywhere it's sent.</p><input type="file" id="sc" accept="application/pdf"></div>
    <div id="sp-msg"><label class="f"><span>Message</span><textarea id="sm" style="min-height:140px"></textarea></label><div class="small muted">The message uses your saved template. A secure link to the PDF is added (valid 30 days). Email also attaches the PDF.</div></div>`;
  let ch = "email"; const fill = () => { const t = tpls.find(x => x.key === "quotation_send" && x.channel === ch); const m = $("#sm", body), s = $("#ss", body); if (t) { m.value = t.body; if (s) s.value = t.subject; } };
  const setCh = c => { ch = c; $$(".tabs button", body).forEach(b => b.classList.toggle("on", b.dataset.ch === c)); ["email", "whatsapp", "custom"].forEach(k => $("#sp-" + k, body).style.display = k === c ? "block" : "none"); $("#sp-msg", body).style.display = c === "custom" ? "none" : "block"; if (c !== "custom") fill(); };
  $$(".tabs button", body).forEach(b => b.onclick = () => setCh(b.dataset.ch)); fill();
  modal({ title: `Send ${q.number}`, body, wide: true, actions: [{ label: "Close", close: true }, { label: "Send", primary: true, onClick: async () => {
    if (ch === "custom") { const f = $("#sc", body).files[0]; if (!f) throw new Error("Choose a PDF."); const { fileToDataURL } = await import("./ui.js"); await post(`/quotations/${q.id}/custom-pdf`, { data: await fileToDataURL(f) }); toast("Your PDF will be used from now on."); done?.(); return; }
    const payload = { channel: ch, to: ch === "email" ? $("#se", body).value : $("#sw", body).value, subject: $("#ss", body)?.value || "", message: $("#sm", body).value, base_url: location.origin };
    const r = await post(`/quotations/${q.id}/send`, payload);
    if (ch === "email") { if (!r.sent) throw new Error(r.detail); toast(r.detail); done?.(); return; }
    if (r.sent) { toast("Sent on WhatsApp."); done?.(); return; }
    window.open(r.wa_link, "_blank", "noopener"); await post(`/quotations/${q.id}/send`, { ...payload, mark_sent: true }).catch(() => {}); toast("WhatsApp opened with the message. Press send there."); done?.(); } }] });
}
