import { get, post, patch, del, put, qs, api } from "./api.js";
import { $, $$, esc, toast, fail, money, fmtDT, fmtD, today, debounce, table, wireTable, selected, chip, statusKind, modal, drawer, confirmBox, fieldsHTML, readFields, validate, makeMap, pinIcon, here, download, fileToDataURL, skeleton } from "./ui.js";
import { haversine } from "./geo.js";
import { importModal } from "./kit.js";
import { fillForm } from "./pages-field.js";
import { fileUrl } from "./api.js";

import { STAGES as LEAD_STAGES, tempChip, moveStage, qualifyModal, relatedPanel } from "./pages-sales.js";
const cap = s => s ? s[0].toUpperCase() + s.slice(1).replace(/_/g, " ") : "";
const label = (S, k, d) => S.tenant.settings?.page_headers?.[k] || d;

export async function ensureTesseract() {
  if (window.Tesseract) return;
  await new Promise((res, rej) => { const s = document.createElement("script"); s.src = "https://cdn.jsdelivr.net/npm/tesseract.js@5.1.1/dist/tesseract.min.js"; s.onload = res; s.onerror = () => rej(new Error("Card scanner could not load. Check your connection.")); document.head.appendChild(s); });
}
export async function scanCard(file, fill) {
  toast("Reading card…"); await ensureTesseract();
  const { data } = await Tesseract.recognize(file, "eng");
  const r = await post("/crm/ocr/parse-card", { text: data.text });
  fill(r); toast("Card scanned. Please review the details.");
}

// ------------------------------------------------------------------ generic CRM entity page
function entityConfig(S, users, customers, products = [], bt = [], cat = [], grp = [], units = []) {
  const owner = { k: "owner_id", label: "Owner", type: "select", options: users.map(u => [u.id, u.name]) };
  return {
    leads: { title: "Leads", one: "lead", cl: "lead", scan: true, kanban: true,
      fields: [{ k: "name", label: "Contact name", req: true }, { k: "company", label: "Company" }, { k: "phone", label: "Phone", type: "tel" }, { k: "email", label: "Email", type: "email" },
        { k: "product_id", label: "Product", type: "select", req: true, options: products.map(p => [p.id, p.code + " · " + p.name]), hint: "From the product master", ph: "Select product…" }, { k: "status", label: "Stage", type: "select", options: [["new", "New"], ["contacted", "Contacted"]], def: "new", createOnly: true },
        { k: "value", label: "Deal value", type: "number" }, { k: "expected_close", label: "Expected close", type: "date" }, { k: "source", label: "Source", type: "select", options: ["Referral", "Website", "Exhibition", "Cold call", "Social", "Other"] },
        { k: "city", label: "City" }, owner, { k: "notes", label: "Notes", type: "textarea", full: true }],
      cols: [{ h: "Lead", f: r => `<strong>${esc(r.name)}</strong><div class="small muted">${esc(r.company)}</div>` }, { h: "Stage", f: r => chip(cap(r.status), statusKind(r.status)) }, { h: "Product", f: r => esc(r.product_name || "—") }, { h: "Value", f: r => money(r.value, S.tenant.settings.currency) },
        { h: "Score", f: r => tempChip(r) }, { h: "Next action", f: r => `<span class="small ${r.next_action?.startsWith("Overdue") ? "" : "muted"}" style="${r.next_action?.startsWith("Overdue") ? "color:var(--danger)" : ""}">${esc(r.next_action || "")}</span>` }, { h: "Owner", f: r => esc(users.find(u => u.id === r.owner_id)?.name || "—") }], filters: [{ k: "status", label: "Stage", options: LEAD_STAGES }] },
    customers: { title: "Customers", one: "customer", cl: "customer", scan: true,
      fields: [{ k: "name", label: "Customer name", req: true }, { k: "industry", label: "Industry" }, { k: "phone", label: "Phone", type: "tel", hint: "Used to send the visit OTP" }, { k: "email", label: "Email", type: "email" }, { k: "city", label: "City" }, { k: "country", label: "Country", def: "India" },
        { k: "business_type", label: "Business type", type: "select", options: bt }, { k: "parent_id", label: "Parent company", type: "select", int: true, options: customers.map(c => [c.id, c.name]) }, { k: "gstin", label: "GSTIN / Tax ID" }, { k: "credit_limit", label: "Credit limit (0 = none)", type: "number" }, { k: "credit_days", label: "Credit days", type: "number", def: 30 }, owner, { k: "address", label: "Address", full: true }, { k: "lat", label: "Latitude", type: "number" }, { k: "lng", label: "Longitude", type: "number", hint: "Needed for geo-fenced tasks and routes" }],
      cols: [{ h: "Customer", f: r => `<strong>${esc(r.name)}</strong><div class="small muted">${esc(r.industry)}${r.parent_id ? " · sub-company" : ""}</div>` }, { h: "City", f: r => esc(r.city) || "—" }, { h: "Phone", f: r => esc(r.phone) || "—" }, { h: "Contacts", f: r => r.contacts_count ?? 0 }, { h: "Leads", f: r => r.leads_count ?? 0 }, { h: "Orders", f: r => r.orders_count ?? 0 }, { h: "Map", f: r => r.lat != null ? chip("pinned", "ok") : chip("no location", "warn") }, { h: "Owner", f: r => esc(users.find(u => u.id === r.owner_id)?.name || "—") }], filters: [] },
    contacts: { title: "Contacts", one: "contact", cl: "contact", scan: true,
      fields: [{ k: "name", label: "Name", req: true }, { k: "designation", label: "Designation" }, { k: "phone", label: "Phone", type: "tel" }, { k: "email", label: "Email", type: "email" }, { k: "customer_id", label: "Customer", type: "select", options: customers.map(c => [c.id, c.name]) }, owner],
      cols: [{ h: "Name", f: r => `<strong>${esc(r.name)}</strong><div class="small muted">${esc(r.designation)}</div>` }, { h: "Customer", f: r => esc(customers.find(c => c.id === r.customer_id)?.name || "—") }, { h: "Phone", f: r => esc(r.phone) || "—" }, { h: "Email", f: r => esc(r.email) || "—" }], filters: [] },
    products: { title: "Products", one: "product", cl: null, readonly: S.user.role !== "admin" && S.user.role !== "owner",
      fields: [{ k: "code", label: "Item code", req: true }, { k: "name", label: "Product name", req: true }, { k: "group", label: "Group", type: "select", options: grp }, { k: "category", label: "Category", type: "select", options: cat }, { k: "kind", label: "Type", type: "select", options: [["product", "Product"], ["part", "Part / item"]], def: "product" }, { k: "parent_id", label: "Belongs to product (for parts)", type: "select", int: true, options: products.filter(p => p.kind !== "part").map(p => [p.id, p.name]) },
        { k: "hsn", label: "HSN / SAC code" }, { k: "gst_pct", label: "GST %", type: "number", def: 18 }, { k: "price", label: "Selling price", type: "number" }, { k: "uom", label: "Unit", type: "select", options: units, def: "Nos" }, { k: "moq", label: "Min. order quantity", type: "number" }, { k: "amc_charge", label: "AMC charge", type: "number" }, { k: "video_url", label: "Video link (YouTube)", full: true }, { k: "description", label: "Description", type: "textarea", full: true }, { k: "disabled", label: "Disabled (hide from new leads and quotes)", type: "checkbox" }],
      cols: [{ h: "Code", f: r => esc(r.code) }, { h: "Name", f: r => `<strong>${esc(r.name)}</strong>${r.kind === "part" ? ' <span class="chip">part</span>' : ""}${r.disabled ? ' <span class="chip warn">disabled</span>' : ""}` }, { h: "Group", f: r => esc(r.group) || "—" }, { h: "HSN", f: r => esc(r.hsn) || "—" }, { h: "GST", f: r => (r.gst_pct ?? 0) + "%" }, { h: "Price", f: r => money(r.price, S.tenant.settings.currency) }, { h: "Media", f: r => `${(r.photos || []).length ? "📷" + r.photos.length + " " : ""}${r.brochure ? "📄" : ""}` || "—" }, { h: "ERP", f: r => r.erp_name ? chip("synced", "ok") : "—" }], filters: [] },
  };
}

async function entityPage(root, S, entity) {
  const [bt, cat, grp, units] = await Promise.all(["business_type", "product_category", "product_group", "unit"].map(k => get("/masters?kind=" + k).then(r => r.map(m => m.name))));
  const [users, custs, cfs, prods] = await Promise.all([get("/users?all=1").catch(() => get("/users")), entity === "contacts" || entity === "customers" ? get("/crm/customers?limit=500").then(r => r.items) : Promise.resolve([]), get("/custom-fields"), entity === "leads" || entity === "customers" ? get("/crm/products?limit=500").then(r => r.items) : Promise.resolve([])]);
  const cfg = entityConfig(S, users, custs, prods, bt, cat, grp, units)[entity];
  const myCF = cfs.filter(f => f.entity === cfg.cl);
  const cfFields = myCF.map(f => ({ k: "cf_" + f.key, label: f.label, type: f.type === "select" ? "select" : f.type === "number" ? "number" : f.type === "date" ? "date" : "text", options: f.options, req: f.required, _key: f.key }));
  const editFields = [...cfg.fields.filter(f => !f.createOnly), ...cfFields], allFields = [...cfg.fields, ...cfFields];
  let view = localStorage.getItem("fw_view_" + entity) || "table", rows = [], total = 0, savedViews = [];
  root.innerHTML = `<div class="page-h"><h1>${esc(label(S, entity, cfg.title))}</h1><span class="muted" id="cnt"></span><div class="right row">
      ${cfg.kanban ? `<div class="tabs" style="margin:0;border:0"><button data-v="table" class="${view === "table" ? "on" : ""}">Table</button><button data-v="board" class="${view === "board" ? "on" : ""}">Board</button></div>` : ""}
      <button class="btn sm" id="exp">Export CSV</button>${["customers", "products"].includes(entity) && !cfg.readonly ? '<button class="btn sm" id="imp">Bulk upload</button>' : ""}${cfg.readonly ? "" : `<button class="btn primary" id="new">+ New ${cfg.one}</button>`}</div></div>
    <div class="row" style="margin-bottom:12px"><input id="q" type="search" placeholder="Search ${cfg.title.toLowerCase()}…" class="grow" style="max-width:320px" aria-label="Search">
      ${cfg.filters.map(f => `<select data-f="${f.k}" style="width:auto" aria-label="${f.label}"><option value="">All ${f.label.toLowerCase()}s</option>${f.options.map(o => `<option value="${o}">${cap(o)}</option>`).join("")}</select>`).join("")}
      ${cfg.cl ? `<select id="views" style="width:auto" aria-label="Saved views"><option value="">Saved views</option></select><button class="btn sm" id="saveview">Save view</button>` : ""}</div>
    <div id="bulk" class="row" style="display:none;margin-bottom:10px"><strong id="bn"></strong><select id="bown" style="width:auto"><option value="">Reassign owner…</option>${users.map(u => `<option value="${u.id}">${esc(u.name)}</option>`).join("")}</select><button class="btn sm danger" id="bdel">Delete selected</button></div>
    <div id="list">${skeleton(5)}</div>`;
  const params = () => { const p = { q: $("#q").value }; $$("[data-f]", root).forEach(s => p[s.dataset.f] = s.value); return p; };
  const load = async () => {
    const r = await get("/crm/" + entity + qs({ ...params(), limit: 200 })); rows = r.items; total = r.total; $("#cnt").textContent = total + " record" + (total === 1 ? "" : "s"); paint();
  };
  const emptyMsg = () => `<div class="empty"><h3>No ${cfg.title.toLowerCase()} yet</h3><p>${cfg.readonly ? "Products sync from ERPNext." : `Add your first ${cfg.one}${cfg.scan ? " or scan a business card" : ""}.`}</p></div>`;
  const paint = () => {
    $("#bulk").style.display = "none";
    if (!rows.length) return $("#list").innerHTML = ($("#q").value ? `<div class="empty"><h3>No matches</h3><p>Try a different search or clear the filters.</p></div>` : emptyMsg());
    if (cfg.kanban && view === "board") {
      $("#list").innerHTML = `<div class="kanban">${LEAD_STAGES.map(s => { const c = rows.filter(r => r.status === s); return `<div class="col" data-s="${s}"><h3>${cap(s)} <span class="chip">${c.length}</span></h3>${c.map(r => `<div class="kcard" draggable="true" data-id="${r.id}"><strong>${esc(r.name)}</strong><div class="small muted">${esc(r.company)}</div><div class="row small" style="justify-content:space-between"><span>${money(r.value, S.tenant.settings.currency)}</span>${tempChip(r)}</div></div>`).join("")}</div>`; }).join("")}</div>`;
      $$(".kcard").forEach(k => { k.ondragstart = e => e.dataTransfer.setData("id", k.dataset.id); k.onclick = () => open(rows.find(r => r.id == k.dataset.id)); });
      $$(".col").forEach(c => { c.ondragover = e => e.preventDefault(); c.ondrop = e => { e.preventDefault(); const r = rows.find(x => x.id == e.dataTransfer.getData("id")); if (r) moveStage(r, c.dataset.s, load); }; });
    } else {
      $("#list").innerHTML = table(cfg.cols, rows, { onRow: true, sel: !cfg.readonly }); wireTable($("#list"), rows, open);
      $$("[data-sel],[data-all]", root).forEach(c => c.addEventListener("change", () => { const n = selected(root).length; $("#bulk").style.display = n ? "flex" : "none"; $("#bn").textContent = n + " selected"; }));
    }
  };
  const form = (rec = {}, edit = false) => {
    const v = { ...rec }; cfFields.forEach(f => v[f.k] = rec.custom?.[f._key] ?? "");
    return fieldsHTML(edit ? editFields : allFields, v);
  };
  const collect = (el, edit = false) => { const fl = edit ? editFields : allFields; validate(el, fl); const d = readFields(el, fl); if (d.product_id) d.product_id = +d.product_id; const custom = {}; cfFields.forEach(f => { custom[f._key] = d[f.k]; delete d[f.k]; }); d.custom = custom; if (d.owner_id) d.owner_id = +d.owner_id; if (d.customer_id) d.customer_id = +d.customer_id; return d; };
  function create() {
    const body = document.createElement("div");
    body.innerHTML = (cfg.scan ? `<div class="row" style="margin-bottom:12px"><button class="btn" id="scan" type="button">📇 Scan business card</button><input type="file" id="cardf" accept="image/*" capture="environment" hidden><span class="muted small">Auto-fills name, company, phone and email.</span></div>` : "") + form({ owner_id: S.user.id }) +
      (entity === "customers" ? `<button class="btn sm" type="button" id="loc">📍 Use my current location</button>` : "");
    modal({ title: "New " + cfg.one, body, wide: true, actions: [{ label: "Cancel", close: true }, { label: "Create", primary: true, onClick: async el => { await post("/crm/" + entity, collect(el)); toast(cfg.one + " created." + (entity === "leads" ? " A first follow-up was scheduled." : "")); load(); } }],
      onMount: el => {
        const cf = $("#cardf", el); if (cf) { $("#scan", el).onclick = () => cf.click(); cf.onchange = () => cf.files[0] && scanCard(cf.files[0], r => { const map = { name: r.name, company: r.company, phone: r.phone, email: r.email, designation: r.designation }; Object.entries(map).forEach(([k, v]) => { const i = $(`[name="${k}"]`, el); if (i && v && !i.value) i.value = v; }); if (entity === "customers" && r.company) $(`[name="name"]`, el).value = r.company; }).catch(fail); }
        const loc = $("#loc", el); if (loc) loc.onclick = () => here().then(p => { $('[name="lat"]', el).value = p.lat.toFixed(6); $('[name="lng"]', el).value = p.lng.toFixed(6); }).catch(fail);
      } });
  }
  async function open(rec) {
    const dr = drawer({ title: rec.name || rec.title, body: `<div id="dbody">${skeleton(4)}</div>` });
    const extra = entity === "leads" ? `<button class="btn" id="conv" ${rec.converted_customer_id || rec.status === "lost" ? "disabled" : ""}>${rec.converted_customer_id ? "Converted" : "Won: convert to customer"}</button><button class="btn" id="share">Share…</button>` : "";
    const comm2 = r => [r.phone ? `<a class="btn sm" href="tel:${esc(r.phone)}">📞 Call</a>` : "", r.phone ? `<a class="btn sm" target="_blank" rel="noopener" href="https://wa.me/${esc(String(r.phone).replace(/\D/g, ""))}">💬 WhatsApp</a>` : "", r.email ? `<a class="btn sm" href="mailto:${esc(r.email)}">✉️ Email</a>` : ""].join(" ");
    const tl = cfg.cl ? await get(`/crm/${entity}/${rec.id}/timeline`) : [];
    const b = $("#dbody", dr.root);
    const stepper = entity === "leads" ? `<div class="card" style="padding:12px;margin-bottom:14px"><div class="row" style="gap:4px;margin-bottom:8px">${LEAD_STAGES.filter(x => x !== "lost").map(st => `<button class="btn sm ${rec.status === st ? "primary" : ""}" data-st="${st}" ${rec.status === "lost" ? "disabled" : ""}>${cap(st)}</button>`).join("")}<button class="btn sm danger right" data-st="lost" ${rec.status === "lost" || rec.status === "won" ? "disabled" : ""}>Lost</button></div>
      <div class="row small">${tempChip(rec)}<span class="muted">${esc(rec.next_action || "")}</span></div>${rec.status === "lost" ? `<div class="small" style="color:var(--danger);margin-top:6px">Lost: ${esc(rec.lost_reason)} <button class="btn sm" data-st="new">Reopen</button></div>` : ""}
      <div class="row" style="margin-top:8px"><button class="btn sm" id="qual" ${["won", "lost"].includes(rec.status) ? "disabled" : ""}>${rec.qualified_at ? "✓ Qualified " + (rec.qualification?.total ?? "") + "/8 · edit" : "Qualify lead"}</button>${comm2(rec)}</div></div>` : "";
    b.innerHTML = stepper + form(rec, true) + (cfg.readonly ? "" : `<div class="row" style="margin:6px 0 16px"><button class="btn primary" id="save">Save changes</button>${extra}<button class="btn danger right" id="del">Delete</button></div>`) +
      (cfg.cl ? `<h3>Timeline</h3><div class="row" style="margin:8px 0"><input id="note" placeholder="Add a note…" class="grow"><button class="btn sm" id="addn">Add</button></div><div class="timeline">${tl.map(t => `<div><div>${esc(t.text)}</div><div class="small muted">${esc(t.user_name || "System")} · ${fmtDT(t.created_at)}</div></div>`).join("") || '<div class="muted">No activity yet.</div>'}</div>` : "");
    if (entity === "products") {
      const adm = ["admin", "owner"].includes(S.user.role); const media = document.createElement("div"); media.style.marginTop = "14px";
      media.innerHTML = `<h3>Photos &amp; brochure</h3><div class="row" style="margin:8px 0">${(rec.photos || []).map((p, i) => `<div style="position:relative"><img src="${fileUrl(p)}" alt="Product photo ${i + 1}" style="width:92px;height:72px;object-fit:cover;border-radius:8px;border:1px solid var(--border)">${adm ? `<button class="btn sm" data-dp="${i}" style="position:absolute;top:2px;right:2px;padding:0 6px;min-height:22px">✕</button>` : ""}</div>`).join("") || '<span class="muted small">No photos</span>'}</div>
        ${rec.brochure ? `<a href="${fileUrl(rec.brochure)}" target="_blank" rel="noopener">📄 View brochure</a>` : '<span class="muted small">No brochure</span>'}${adm ? `<div class="row" style="margin-top:8px"><label class="btn sm">+ Photo<input type="file" id="pph" accept="image/*" hidden></label><label class="btn sm">${rec.brochure ? "Replace" : "+"} brochure (PDF)<input type="file" id="pbr" accept="application/pdf" hidden></label></div>` : ""}`;
      b.appendChild(media);
      const up = async (kind, f) => { try { const { fileToDataURL } = await import("./ui.js"); await post(`/crm/products/${rec.id}/files`, { kind, data: await fileToDataURL(f) }); toast("Uploaded."); dr.close(); load(); } catch (e) { fail(e); } };
      $("#pph", media)?.addEventListener("change", e => e.target.files[0] && up("photo", e.target.files[0])); $("#pbr", media)?.addEventListener("change", e => e.target.files[0] && up("brochure", e.target.files[0]));
      $$("[data-dp]", media).forEach(x => x.onclick = async () => { await del(`/crm/products/${rec.id}/photos/${x.dataset.dp}`); dr.close(); load(); });
    }
    if (cfg.readonly) $$("input,select,textarea", b).forEach(i => i.disabled = true);
    if (entity === "leads" || entity === "customers") {
      const host = document.createElement("div"); host.id = "rel"; b.appendChild(host);
      const leadsAll = entity === "leads" ? rows : (await get("/crm/leads?limit=500")).items;
      const custAll = custs.length ? custs : (await get("/crm/customers?limit=500")).items;
      relatedPanel(host, entity, rec, { S, users, products: prods, customers: custAll, leads: leadsAll, reload: () => load() }).catch(fail);
    }
    $$("[data-st]", b).forEach(x => x.onclick = () => moveStage(rec, x.dataset.st, () => { dr.close(); load(); }));
    $("#qual", b)?.addEventListener("click", () => qualifyModal(rec, S, () => { dr.close(); load(); }));
    $("#save", b)?.addEventListener("click", async () => { try { await patch(`/crm/${entity}/${rec.id}`, collect(b, true)); toast("Saved."); dr.close(); load(); } catch (e) { fail(e); } });
    $("#del", b)?.addEventListener("click", async () => { if (await confirmBox(`Delete ${cfg.one}?`, "It moves to the recycle bin and can be restored for 90 days.", "Delete", true)) { await del(`/crm/${entity}/${rec.id}`); toast("Moved to recycle bin."); dr.close(); load(); } });
    $("#addn", b)?.addEventListener("click", async () => { const t = $("#note", b).value.trim(); if (!t) return; await post(`/crm/${entity}/${rec.id}/timeline`, { text: t }); dr.close(); open(rec); });
    $("#conv", b)?.addEventListener("click", async () => { try { await post(`/crm/leads/${rec.id}/convert`); toast("Converted to customer."); dr.close(); load(); } catch (e) { fail(e); } });
    $("#share", b)?.addEventListener("click", () => modal({ title: "Share lead", body: `<p class="muted">Choose who else can see and work on this lead.</p>` + users.filter(u => u.id !== S.user.id).map(u => `<label class="f" style="margin-bottom:6px"><span><input type="checkbox" value="${u.id}" ${rec.shared_with?.includes(u.id) ? "checked" : ""}> ${esc(u.name)}</span></label>`).join(""),
      actions: [{ label: "Cancel", close: true }, { label: "Share", primary: true, onClick: async el => { await post(`/crm/leads/${rec.id}/share`, { user_ids: $$("input:checked", el).map(i => +i.value) }); toast("Sharing updated."); } }] }));
  }
  const reload = debounce(() => load().catch(fail), 250);
  $("#q").oninput = reload; $$("[data-f]", root).forEach(s => s.onchange = reload);
  $$("[data-v]", root).forEach(b => b.onclick = () => { view = b.dataset.v; localStorage.setItem("fw_view_" + entity, view); $$("[data-v]", root).forEach(x => x.classList.toggle("on", x === b)); paint(); });
  $("#new")?.addEventListener("click", create);
  $("#imp")?.addEventListener("click", () => importModal(entity, cfg.title.toLowerCase(), load));
  $("#exp").onclick = async () => download(entity + ".csv", await get("/crm/" + entity + qs({ ...params(), fmt: "csv" })));
  $("#bdel").onclick = async () => { const ids = selected(root); if (await confirmBox(`Delete ${ids.length} record(s)?`, "They move to the recycle bin for 90 days.", "Delete", true)) { await post(`/crm/${entity}/bulk`, { ids, action: "delete" }); load(); } };
  $("#bown").onchange = async e => { if (!e.target.value) return; await post(`/crm/${entity}/bulk`, { ids: selected(root), action: "update", changes: { owner_id: +e.target.value } }); toast("Owner updated."); load(); };
  if (cfg.cl) {
    const loadViews = async () => { savedViews = await get("/views" + qs({ entity })); $("#views").innerHTML = `<option value="">Saved views</option>` + savedViews.map(v => `<option value="${v.id}">${esc(v.name)}</option>`).join(""); };
    $("#views").onchange = e => { const v = savedViews.find(x => x.id == e.target.value); $("#q").value = v?.filters.q || ""; $$("[data-f]", root).forEach(s => s.value = v?.filters[s.dataset.f] || ""); load(); };
    $("#saveview").onclick = () => modal({ title: "Save current filters as a view", body: `<label class="f"><span>View name</span><input id="vn" placeholder="e.g. Hot leads in Mumbai"></label>${["manager", "admin", "owner"].includes(S.user.role) ? '<label class="f"><span><input type="checkbox" id="vs"> Share with my team</span></label>' : ""}`,
      actions: [{ label: "Cancel", close: true }, { label: "Save view", primary: true, onClick: async el => { const name = $("#vn", el).value.trim(); if (!name) throw new Error("Give the view a name."); await post("/views", { entity, name, filters: params(), shared: $("#vs", el)?.checked }); loadViews(); toast("View saved."); } }] });
    loadViews();
  }
  await load();
}
export const leads = (r, S) => entityPage(r, S, "leads");
export const customers = (r, S) => entityPage(r, S, "customers");
export const contacts = (r, S) => entityPage(r, S, "contacts");
export const products = (r, S) => entityPage(r, S, "products");

// ------------------------------------------------------------------ Tasks
export async function tasks(root, S) {
  const [users, custs] = await Promise.all([get("/users"), get("/crm/customers?limit=500").then(r => r.items)]);
  const statuses = S.tenant.settings.task_statuses || ["open", "in_progress", "done"];
  let rows = [];
  root.innerHTML = `<div class="page-h"><h1>${esc(label(S, "tasks", "Tasks"))}</h1><div class="right row"><button class="btn sm" id="bulk">Bulk upload CSV</button><button class="btn primary" id="new">+ New task</button></div></div>
    <div class="row" style="margin-bottom:12px"><input id="q" type="search" placeholder="Search tasks…" style="max-width:280px" aria-label="Search"><select id="fs" style="width:auto"><option value="">Any status</option>${statuses.map(s => `<option>${s}</option>`).join("")}</select>
    <select id="fa" style="width:auto"><option value="">Everyone</option>${users.map(u => `<option value="${u.id}">${esc(u.name)}</option>`).join("")}</select><label class="row small"><input type="checkbox" id="od"> Overdue only</label></div><div id="list">${skeleton(5)}</div>`;
  const fields = [{ k: "title", label: "Task", req: true, full: true }, { k: "description", label: "Details", type: "textarea", full: true }, { k: "assignee_id", label: "Assign to", type: "select", options: users.map(u => [u.id, u.name]), def: S.user.id },
    { k: "customer_id", label: "Customer", type: "select", options: custs.map(c => [c.id, c.name]), hint: "Location is picked up from the customer" }, { k: "priority", label: "Priority", type: "select", options: ["low", "medium", "high", "urgent"], def: "medium" },
    { k: "due_at", label: "Due", type: "datetime" }, { k: "repeat", label: "Repeat", type: "select", options: [["daily", "Daily"], ["weekly", "Weekly"], ["monthly", "Monthly"]], ph: "Does not repeat" }, { k: "geofence_m", label: "Geo-fence radius (m)", type: "number", hint: "Task can only be completed within this distance" }, { k: "status", label: "Status", type: "select", options: statuses, def: "open" }];
  const load = async () => {
    rows = await get("/tasks" + qs({ q: $("#q").value, status: $("#fs").value, assignee_id: $("#fa").value, overdue: $("#od").checked ? 1 : "" }));
    $("#list").innerHTML = table([{ h: "Task", f: r => `<strong>${esc(r.title)}</strong>${r.repeat ? ` <span class="chip">↻ ${r.repeat}</span>` : ""}${r.lat != null && r.geofence_m ? ` <span class="chip info">📍 ${r.geofence_m} m</span>` : ""}` }, { h: "Assignee", f: r => esc(r.assignee_name || "—") },
      { h: "Priority", f: r => chip(r.priority, statusKind(r.priority)) }, { h: "Due", f: r => r.due_at ? `<span style="${new Date(r.due_at) < new Date() && r.status !== "done" ? "color:var(--danger)" : ""}">${fmtDT(r.due_at)}</span>` : "—" }, { h: "Status", f: r => chip(cap(r.status), statusKind(r.status)) },
      { h: "", f: r => r.status === "done" ? "" : `<span class="nowrap">${r.assignee_id === S.user.id ? `${!["en_route", "checked_in"].includes(r.status) ? `<button class="btn sm" data-pg="${r.id}:en_route">On my way</button> ` : ""}${r.status !== "checked_in" ? `<button class="btn sm" data-pg="${r.id}:checked_in">Check in</button> ` : ""}<button class="btn sm" data-pg="${r.id}:rescheduled">Reschedule</button> ` : ""}<button class="btn sm primary" data-done="${r.id}">✓ Complete</button>${["manager", "admin", "owner"].includes(S.user.role) && r.lat != null ? ` <button class="btn sm" data-ds="${r.id}">Nearest…</button>` : ""}</span>` }], rows, { onRow: true, empty: "No tasks match. Create one to get started." });
    wireTable($("#list"), rows, edit);
    $$("[data-pg]", root).forEach(b => b.onclick = async () => { const [id, step] = b.dataset.pg.split(":");
      if (step === "rescheduled") return modal({ title: "Reschedule task", body: `<label class="f"><span>New date &amp; time</span><input type="datetime-local" id="rd"></label><label class="f"><span>Reason</span><input id="rr"></label>`, actions: [{ label: "Cancel", close: true }, { label: "Reschedule", primary: true, onClick: async el => { const v = $("#rd", el).value; await post(`/field/tasks/${id}/progress`, { step, due_at: v ? new Date(v).toISOString().slice(0, 19) : null, reason: $("#rr", el).value }); toast("Rescheduled."); load(); } }] });
      try { const body = { step }; if (step === "checked_in") { const p = await here(); body.lat = p.lat; body.lng = p.lng; } await post(`/field/tasks/${id}/progress`, body); toast(step === "en_route" ? "Marked: on your way." : "Checked in."); load(); } catch (e) { fail(e); } });
    $$("[data-ds]", root).forEach(b => b.onclick = async () => { try { const r = await get(`/field/dispatch/${b.dataset.ds}`); modal({ title: "Nearest available for: " + r.task, wide: true, body: r.suggestions.length ? table([{ h: "Executive", f: x => `<strong>${esc(x.name)}</strong>${x.on_duty ? "" : " <span class='chip warn'>not checked in</span>"}` }, { h: "Distance", f: x => x.distance_km + " km" }, { h: "ETA", f: x => x.eta_min + " min" }, { h: "Open tasks", f: x => x.open_tasks }, { h: "Last seen", f: x => x.last_seen_min + " min ago" }, { h: "", f: x => `<button class="btn sm primary" data-as="${x.user_id}">Assign</button>` }], r.suggestions) : "<p>No one has shared a location recently.</p>",
      onMount: el => $$("[data-as]", el).forEach(a => a.onclick = async () => { await patch("/tasks/" + b.dataset.ds, { assignee_id: +a.dataset.as }); toast("Assigned."); el.remove(); load(); }), actions: [{ label: "Close", close: true }] }); } catch (e) { fail(e); } });
    $$("[data-done]", root).forEach(b => b.onclick = async () => { b.disabled = true; try { const t = rows.find(r => r.id == b.dataset.done); const body = { status: "done" }; if (t.lat != null && t.geofence_m) { const p = await here(); body.lat = p.lat; body.lng = p.lng; } await patch("/tasks/" + t.id, body); toast("Task completed."); load(); } catch (e) { fail(e); b.disabled = false; } });
  };
  const save = (id) => async el => { validate(el, fields); const d = readFields(el, fields); ["assignee_id", "customer_id"].forEach(k => d[k] = d[k] ? +d[k] : null); if (!d.geofence_m) delete d.geofence_m; if (el.querySelector("#pinme")?.checked) { const p = await here(); d.lat = p.lat; d.lng = p.lng; d.geofence_m = d.geofence_m || S.tenant.settings.geofence_default_m || 150; } id ? await patch("/tasks/" + id, d) : await post("/tasks", d); toast("Task saved."); load(); };
  const pin = `<label class="f"><span><input type="checkbox" id="pinme"> Pin to my current location (geo-fence)</span></label>`;
  const edit = r => modal({ title: "Edit task", wide: true, body: fieldsHTML(fields, r) + pin, actions: [{ label: "Delete", danger: true, onClick: async () => { await del("/tasks/" + r.id); load(); } }, { label: "Cancel", close: true }, { label: "Save", primary: true, onClick: save(r.id) }] });
  $("#new").onclick = () => modal({ title: "New task", wide: true, body: fieldsHTML(fields, {}) + pin, actions: [{ label: "Cancel", close: true }, { label: "Create", primary: true, onClick: save() }] });
  $("#bulk").onclick = () => modal({ title: "Bulk upload tasks", body: `<p>Upload a CSV with columns:<br><code>title, description, assignee_email, priority, due, customer, repeat</code></p><p class="small muted">Dates as YYYY-MM-DD or YYYY-MM-DD HH:MM. Rows with problems are skipped and listed.</p><div class="row"><button class="btn sm" id="tpl">Download template</button></div><input type="file" accept=".csv" id="bf" style="margin-top:12px">`,
    onMount: el => $("#tpl", el).onclick = () => download("tasks-template.csv", "title,description,assignee_email,priority,due,customer,repeat\nFollow up quote,Call about pricing,rahul@proman.demo,high,2026-12-01 10:00,Tata Projects Ltd,\n"),
    actions: [{ label: "Cancel", close: true }, { label: "Upload", primary: true, onClick: async el => { const f = $("#bf", el).files[0]; if (!f) throw new Error("Choose a CSV file."); const fd = new FormData(); fd.append("file", f); const r = await api("POST", "/tasks/bulk-upload", fd); toast(`${r.created} task(s) created.` + (r.errors.length ? ` ${r.errors.length} skipped.` : "")); if (r.errors.length) alert(r.errors.join("\n")); load(); } }] });
  ["q"].forEach(i => $("#" + i).oninput = debounce(load, 250)); ["fs", "fa", "od"].forEach(i => $("#" + i).onchange = load);
  await load();
}

// ------------------------------------------------------------------ Visits (OTP-verified customer visits + MOM)
export async function visits(root, S) {
  const custs = (await get("/crm/customers?limit=500")).items;
  root.innerHTML = `<div class="page-h"><h1>${esc(label(S, "visits", "Customer visits"))}</h1><div class="right"><button class="btn primary" id="start">+ Start visit</button></div></div><div id="active"></div><div id="list">${skeleton(4)}</div>`;
  const loadActive = async () => {
    const v = await get("/visits/active"); const el = $("#active");
    if (!v) return el.innerHTML = "";
    el.innerHTML = `<div class="card" style="margin-bottom:16px;border-color:var(--accent)"><div class="row"><h2>Visit in progress — ${esc(v.customer_name || "")}</h2>${chip(v.otp_verified ? "OTP verified" : "Waiting for customer OTP", v.otp_verified ? "ok" : "warn")}<span class="muted small right">started ${fmtDT(v.started_at)}</span></div>
      ${v.otp_verified || !S.tenant.settings.require_visit_otp ? `<div id="momf"><label class="f"><span>Minutes of meeting</span><textarea id="mom" placeholder="Key points, decisions, next steps"></textarea></label>
        <label class="f"><span>Email MOM to</span><input id="mto" placeholder="comma-separated emails (customer and manager are added automatically)" value="${esc(S.tenant.settings.mom_recipients_default || "")}"></label>
        <div id="vforms"></div><div class="row"><input type="file" id="mph" accept="image/*" capture="environment" style="max-width:260px">${S.tenant.settings.require_visit_photo ? '<span class="small" style="color:var(--warn)">Photo required</span>' : ""}<button class="btn primary" id="end">End visit &amp; send MOM</button></div></div>`
      : `<div class="row" style="margin-top:8px"><input id="otp" inputmode="numeric" maxlength="6" placeholder="6-digit code" style="max-width:180px" aria-label="Customer OTP"><button class="btn primary" id="vo">Verify</button><span class="muted small">Ask the customer for the code sent to their phone.</span></div>`}</div>`;
    if ($("#vforms")) { const [fs, rs] = await Promise.all([get("/forms?applies_to=visit"), get("/forms/responses?visit_id=" + v.id)]); const done = new Set(rs.map(r => r.template_id));
      $("#vforms").innerHTML = fs.length ? `<h3 style="margin:6px 0">Forms</h3>` + fs.map(f => `<div class="row" style="padding:4px 0"><span>${done.has(f.id) ? "✅" : f.mandatory ? "⚠️" : "📝"}</span><span class="grow">${esc(f.name)}${f.mandatory && !done.has(f.id) ? ' <span class="chip warn">required</span>' : ""}</span>${done.has(f.id) ? '<span class="chip ok">filled</span>' : `<button class="btn sm" data-ff="${f.id}">Fill in</button>`}</div>`).join("") : "";
      $$("[data-ff]", $("#vforms")).forEach(b => b.onclick = () => fillForm(fs.find(f => f.id == b.dataset.ff), { visit_id: v.id, customer_id: v.customer_id }, loadActive)); }
    $("#vo")?.addEventListener("click", async () => { try { await post(`/visits/${v.id}/verify-otp`, { otp: $("#otp").value }); toast("Visit verified."); loadActive(); } catch (e) { fail(e); } });
    $("#end")?.addEventListener("click", async e => { e.target.disabled = true; try { const f = $("#mph").files[0]; const p = await here().catch(() => ({})); await post(`/visits/${v.id}/end`, { mom: $("#mom").value, email_to: $("#mto").value, photo: f ? await fileToDataURL(f) : null, lat: p.lat, lng: p.lng }); toast("Visit ended. MOM sent."); loadActive(); loadList(); } catch (er) { fail(er); e.target.disabled = false; } });
  };
  const loadList = async () => { const rows = await get("/visits"); $("#list").innerHTML = `<h2 style="margin-bottom:8px">History</h2>` + table([{ h: "Customer", f: r => `<strong>${esc(r.customer_name || "—")}</strong>` }, { h: "By", f: r => esc(r.user_name) }, { h: "Started", f: r => fmtDT(r.started_at) }, { h: "Duration", f: r => r.ended_at ? Math.round((new Date(r.ended_at) - new Date(r.started_at)) / 6e4) + " min" : chip("open", "warn") }, { h: "OTP", f: r => r.otp_verified ? chip("verified", "ok") : "—" }, { h: "MOM", f: r => `<span class="small">${esc((r.mom || "").slice(0, 80))}</span>` }], rows, { empty: "No visits recorded yet." }); };
  $("#start").onclick = () => modal({ title: "Start customer visit", body: `<label class="f"><span>Customer</span><select id="vc"><option value="">Select…</option>${custs.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join("")}</select></label><p class="muted small">We'll check you're at the customer's location, then text a one-time code to the customer's phone.</p>`,
    actions: [{ label: "Cancel", close: true }, { label: "Send OTP", primary: true, onClick: async el => { const id = +$("#vc", el).value; if (!id) throw new Error("Choose a customer."); const p = await here().catch(() => ({})); const r = await post("/visits/start", { customer_id: id, lat: p.lat, lng: p.lng }); toast(r.otp_required ? `OTP sent to ${r.sent_to}` + (r.dev_otp ? ` (demo mode — code ${r.dev_otp})` : "") : "Visit started."); if (r.dev_otp) alert("No SMS gateway is configured, so here is the code for testing: " + r.dev_otp); loadActive(); loadList(); } }] });
  await Promise.all([loadActive(), loadList()]);
}

// ------------------------------------------------------------------ Routes
export async function routes(root, S) {
  const users = await get("/users"), custs = (await get("/crm/customers?limit=500")).items;
  root.innerHTML = `<div class="page-h"><h1>${esc(label(S, "routes", "Routes"))}</h1></div><div class="tabs"><button class="on" data-t="plan">Plan a route</button><button data-t="past">Past routes</button></div><div id="pane"></div>`;
  let map, cleanup = () => {};
  const tab = t => { cleanup(); map?.remove(); $$(".tabs button").forEach(b => b.classList.toggle("on", b.dataset.t === t)); (t === "plan" ? plan : past)(); };
  $$(".tabs button").forEach(b => b.onclick = () => tab(b.dataset.t));
  function plan() {
    const geo = custs.filter(c => c.lat != null);
    $("#pane").innerHTML = `<div class="grid" style="grid-template-columns:minmax(260px,1fr) minmax(0,2fr)"><div class="card"><h2>Stops</h2>${geo.length ? `<div style="max-height:340px;overflow:auto">${geo.map(c => `<label class="f" style="margin-bottom:6px"><span><input type="checkbox" value="${c.id}"> ${esc(c.name)} <span class="muted small">${esc(c.city)}</span></span></label>`).join("")}</div>` : `<div class="empty"><h3>No pinned customers</h3>Add latitude/longitude to customers first.</div>`}
      <div class="row" style="margin-top:10px"><button class="btn primary" id="opt">Optimise route</button></div><div id="sum" style="margin-top:12px"></div></div><div class="card" style="padding:8px"><div id="map" class="map tall"></div></div></div>`;
    map = makeMap($("#map"), S.tenant.settings.map_style); let layer = L.layerGroup().addTo(map), ordered = [], watch = null, idx = 0;
    const next = () => ordered[idx];
    $("#opt").onclick = async () => {
      const ids = $$("input:checked", $("#pane")).map(i => +i.value); if (ids.length < 1) return toast("Select at least one stop.", "err");
      try { const p = await here(); const r = await post("/routes/optimize", { start_lat: p.lat, start_lng: p.lng, customer_ids: ids }); ordered = r.stops; idx = 0; layer.clearLayers();
        const line = [[p.lat, p.lng], ...ordered.map(s => [s.lat, s.lng])]; L.polyline(line, { color: "#0f766e", weight: 4, dashArray: "6 6" }).addTo(layer); L.marker([p.lat, p.lng], { icon: pinIcon("#175cd3", "★") }).addTo(layer);
        ordered.forEach((s, i) => L.marker([s.lat, s.lng], { icon: pinIcon("#0f766e", i + 1) }).addTo(layer).bindPopup(esc(s.name))); map.fitBounds(line, { padding: [40, 40] });
        $("#sum").innerHTML = `<div class="small"><b>${r.distance_km} km</b> · ~${r.total_minutes} min incl. stops${r.saved_km ? ` · saves ${r.saved_km} km vs. your order` : ""}</div><ol style="padding-left:18px">${ordered.map(s => `<li>${esc(s.name)}</li>`).join("")}</ol>
          <div class="row"><button class="btn primary" id="play">▶ Start navigation</button><a class="btn" id="gm" target="_blank" rel="noopener">Open in Maps</a></div><div id="nav" class="small" style="margin-top:8px"></div>`;
        const setGm = () => { const s = next(); const g = $("#gm"); if (g && s) g.href = `https://www.google.com/maps/dir/?api=1&destination=${s.lat},${s.lng}&travelmode=driving`; }; setGm();
        $("#play").onclick = e => { const b = e.currentTarget; if (watch != null) { navigator.geolocation.clearWatch(watch); watch = null; b.textContent = "▶ Resume navigation"; return; } b.textContent = "⏸ Pause";
          watch = navigator.geolocation.watchPosition(pos => { const s = next(); if (!s) { $("#nav").textContent = "Route complete 🎉"; navigator.geolocation.clearWatch(watch); watch = null; b.textContent = "▶ Start navigation"; return; } const d = haversine(pos.coords.latitude, pos.coords.longitude, s.lat, s.lng); if (d < 100) { toast(`Arrived at ${s.name}`); idx++; setGm(); } $("#nav").innerHTML = next() ? `Next: <b>${esc(next().name)}</b> · ${(haversine(pos.coords.latitude, pos.coords.longitude, next().lat, next().lng) / 1000).toFixed(1)} km away (${idx + 1}/${ordered.length})` : "Route complete 🎉"; }, () => { $("#nav").textContent = "Waiting for GPS…"; }, { enableHighAccuracy: true }); };
        cleanup = () => watch != null && navigator.geolocation.clearWatch(watch);
      } catch (e) { fail(e); }
    };
  }
  function past() {
    $("#pane").innerHTML = `<div class="row" style="margin-bottom:12px"><select id="pu" style="width:auto">${users.map(u => `<option value="${u.id}" ${u.id === S.user.id ? "selected" : ""}>${esc(u.name)}</option>`).join("")}</select><input type="date" id="pd" value="${today()}" style="width:auto"><span id="ps" class="muted small"></span></div><div class="card" style="padding:8px"><div id="map" class="map tall"></div></div>`;
    map = makeMap($("#map"), S.tenant.settings.map_style); let layer = L.layerGroup().addTo(map);
    const load = async () => { try { const r = await get("/routes/past" + qs({ user_id: $("#pu").value, day: $("#pd").value })); layer.clearLayers();
      $("#ps").textContent = `${r.stats.distance_km} km · ${r.stats.points} GPS points · ${r.visits.length} visit(s)`;
      if (r.points.length) { const l = L.polyline(r.points, { color: "#175cd3", weight: 4 }).addTo(layer); map.fitBounds(l.getBounds(), { padding: [40, 40] }); L.circleMarker(r.points[0], { radius: 7, color: "#067647", fillOpacity: 1 }).addTo(layer).bindPopup("Start"); L.circleMarker(r.points.at(-1), { radius: 7, color: "#b42318", fillOpacity: 1 }).addTo(layer).bindPopup("Last point"); } else toast("No tracking data for that day.");
      r.visits.forEach((v, i) => v.lat != null && L.marker([v.lat, v.lng], { icon: pinIcon("#0f766e", i + 1) }).addTo(layer).bindPopup(`${esc(v.customer || "Visit")}<br>${fmtDT(v.started_at)}`)); } catch (e) { fail(e); } };
    $("#pu").onchange = $("#pd").onchange = load; load();
  }
  plan();
  return () => { cleanup(); map?.remove(); };
}
