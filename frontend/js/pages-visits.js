// Customer visits: check in at the customer (location recorded, no OTP), capture the customer, check out with the customer's details and a summary.
// A new customer becomes a lead automatically; a known customer's record is updated; the visit is recorded either way.
import { get, post } from "./api.js";
import { $, $$, esc, toast, fail, fmtDT, table, chip, modal, here, fileToDataURL, skeleton } from "./ui.js";
import { fillForm } from "./pages-field.js";

const label = (S, k, d) => S.tenant.settings?.page_headers?.[k] || d;
const hhmm = s => s ? new Date(s).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";

export async function visits(root, S) {
  const [custs, prods] = await Promise.all([get("/crm/customers?limit=500").then(r => r.items), get("/crm/products?limit=500").then(r => r.items.filter(p => p.code !== "UNSPEC"))]);
  const prodOpts = sel => `<option value="">Select a product…</option>${prods.map(p => `<option value="${p.id}" ${sel == p.id ? "selected" : ""}>${esc(p.name)}</option>`).join("")}`;
  root.innerHTML = `<div class="page-h"><h1>${esc(label(S, "visits", "Customer visits"))}</h1><div class="right"><button class="btn primary" id="start">+ Check in at a customer</button></div></div><div id="active"></div><div id="list">${skeleton(4)}</div>`;

  const field = (id, lbl, val = "", type = "text", ro = false) => `<label class="f"><span>${lbl}</span><input id="${id}" type="${type}" value="${esc(val)}" ${ro ? "readonly" : ""}></label>`;
  const readCustomer = el => ({ name: $("#c-name", el)?.value || "", phone: $("#c-phone", el)?.value || "", email: $("#c-email", el)?.value || "", company: $("#c-company", el)?.value || "",
    city: $("#c-city", el)?.value || "", address: $("#c-address", el)?.value || "", product_id: +($("#c-prod", el)?.value || 0) || null, notes: "" });

  const loadActive = async () => {
    const v = await get("/visits/active"), el = $("#active");
    if (!v) return el.innerHTML = "";
    const known = !!v.customer_id, c = known ? (v.customer || {}) : (v.prospect || {});
    el.innerHTML = `<div class="card" style="margin-bottom:16px;border-color:var(--accent)">
      <div class="row"><h2>${esc(v.customer_name || "Customer")}</h2>${chip("ACTIVE since " + hhmm(v.started_at), "ok")}${known ? "" : chip("New customer: becomes a lead", "info")}</div>
      <div class="muted small" style="margin:2px 0 10px">Checked in at your current location${v.start_dist_m != null ? ` · ${Math.round(v.start_dist_m)} m from the saved customer location` : ""}.</div>
      <h3 style="margin:6px 0">Customer details</h3><div class="grid g2">${field("c-name", "Name", c.name || v.customer_name || "", "text", known)}${field("c-phone", "Mobile number", c.phone || "", "tel")}
        ${field("c-email", "Email", c.email || "", "email")}${field("c-company", "Company", c.company || "")}${field("c-city", "City", c.city || "")}${field("c-address", "Address", c.address || "")}</div>
      <label class="f"><span>Product of interest ${known ? "" : "(required)"}</span><select id="c-prod">${prodOpts((v.prospect || {}).product_id)}</select></label>
      <div class="row" style="margin-bottom:8px"><button class="btn sm" id="c-save" type="button">Save details</button><span class="muted small">You can fill the rest now and complete it before you check out.</span></div>
      <label class="f"><span>Visit summary (required)</span><textarea id="mom" placeholder="What was discussed, decisions, next steps"></textarea></label>
      <label class="f"><span>Email the summary to</span><input id="mto" placeholder="comma-separated emails (customer and manager are added automatically)" value="${esc(S.tenant.settings.mom_recipients_default || "")}"></label>
      <div id="vforms"></div>
      <div class="row"><input type="file" id="mph" accept="image/*" capture="environment" style="max-width:260px">${S.tenant.settings.require_visit_photo ? '<span class="small" style="color:var(--warn)">Photo required</span>' : ""}
        <button class="btn danger right" id="end" style="min-width:160px">Check out</button></div></div>`;
    const [fs, rs] = await Promise.all([get("/forms?applies_to=visit"), get("/forms/responses?visit_id=" + v.id)]); const done = new Set(rs.map(r => r.template_id));
    $("#vforms").innerHTML = fs.length ? `<h3 style="margin:6px 0">Forms</h3>` + fs.map(f => `<div class="row" style="padding:4px 0"><span>${done.has(f.id) ? "✅" : f.mandatory ? "⚠️" : "📝"}</span><span class="grow">${esc(f.name)}${f.mandatory && !done.has(f.id) ? ' <span class="small muted">(required)</span>' : ""}</span><button class="btn sm" data-ff="${f.id}">${done.has(f.id) ? "Edit" : "Fill"}</button></div>`).join("") : "";
    $$("[data-ff]", $("#vforms")).forEach(b => b.onclick = () => fillForm(fs.find(f => f.id == b.dataset.ff), { visit_id: v.id, customer_id: v.customer_id }, loadActive));
    $("#c-save").onclick = async () => { try { await post(`/visits/${v.id}/customer`, readCustomer(el)); toast("Details saved."); } catch (e) { fail(e); } };
    $("#end").onclick = async e => {
      e.target.disabled = true;
      try {
        const f = $("#mph").files[0], p = await here().catch(() => ({}));
        const r = await post(`/visits/${v.id}/end`, { mom: $("#mom").value, customer: readCustomer(el), email_to: $("#mto").value, photo: f ? await fileToDataURL(f) : null, lat: p.lat, lng: p.lng });
        toast("Checked out. " + (r.outcome || "Visit recorded."));
        await Promise.all([loadActive(), loadList()]);
      } catch (er) { fail(er); e.target.disabled = false; }
    };
  };

  const loadList = async () => {
    const rows = await get("/visits");
    $("#list").innerHTML = `<h2 style="margin-bottom:8px">History</h2>` + table([
      { h: "Customer", f: r => `<strong>${esc(r.customer_name || "—")}</strong>${r.is_new_customer ? " " + chip("New", "info") : ""}` }, { h: "By", f: r => esc(r.user_name) },
      { h: "In", f: r => fmtDT(r.started_at) }, { h: "Duration", f: r => r.ended_at ? Math.round((new Date(r.ended_at) - new Date(r.started_at)) / 60000) + " min" : chip("Active", "ok") },
      { h: "Outcome", f: r => esc(r.outcome || "") }, { h: "Summary", f: r => `<span class="small">${esc((r.mom || "").slice(0, 90))}</span>` }], rows, { empty: "No visits yet." });
  };

  $("#start").onclick = () => {
    const m = modal({ title: "Check in at a customer", body: `<p class="muted small">Check in once you reach the customer. Your location is recorded and the visit becomes active.</p>
      <div class="tabs" style="margin-bottom:10px"><button class="on" data-m="old" type="button">Existing customer</button><button data-m="new" type="button">New customer</button></div>
      <div id="m-old"><label class="f"><span>Search</span><input id="vs" placeholder="Type to find a customer"></label><label class="f"><span>Customer</span><select id="vc" size="5" style="height:auto">${custs.map(c => `<option value="${c.id}">${esc(c.name)}${c.city ? " · " + esc(c.city) : ""}</option>`).join("")}</select></label></div>
      <div id="m-new" style="display:none">${field("n-name", "Customer / company name (required)")}${field("n-phone", "Mobile number", "", "tel")}${field("n-city", "City")}<label class="f"><span>Product of interest</span><select id="n-prod">${prodOpts()}</select></label>
        <p class="muted small">Name is enough to start. Add the rest during the visit; mobile number and product are needed before you check out.</p></div>`,
      actions: [{ label: "Cancel", close: true }, { label: "Check in here", primary: true, onClick: async el => {
        const isNew = $("#m-new", el).style.display !== "none", body = {};
        if (isNew) { body.customer = { name: $("#n-name", el).value, phone: $("#n-phone", el).value, city: $("#n-city", el).value, product_id: +($("#n-prod", el).value || 0) || null }; if (!body.customer.name.trim()) throw new Error("Type the customer's name."); }
        else { const id = +$("#vc", el).value; if (!id) throw new Error("Choose a customer."); body.customer_id = id; }
        const p = await here().catch(() => { throw new Error("Turn on location to check in at the customer."); });
        const r = await post("/visits/start", { ...body, lat: p.lat, lng: p.lng });
        toast("Checked in. Visit is active." + (r.distance_m != null ? ` You are ${r.distance_m} m from the saved location.` : "")); await Promise.all([loadActive(), loadList()]);
      } }] });
    $$(".tabs button", m.root).forEach(b => b.onclick = () => { $$(".tabs button", m.root).forEach(x => x.classList.toggle("on", x === b)); $("#m-old", m.root).style.display = b.dataset.m === "old" ? "" : "none"; $("#m-new", m.root).style.display = b.dataset.m === "new" ? "" : "none"; });
    $("#vs", m.root).oninput = e => { const q = e.target.value.toLowerCase(); $$("#vc option", m.root).forEach(o => o.hidden = !o.textContent.toLowerCase().includes(q)); };
  };
  await Promise.all([loadActive(), loadList()]);
}
