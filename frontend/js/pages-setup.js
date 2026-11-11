// The mandatory first-run setup wizard (client administrators). Every step saves through the same APIs the Settings pages use,
// so nothing here is special: it is a guided order of things a new company has to decide before using the product.
import { get, post, put } from "./api.js";
import { $, $$, esc, toast, fail, here, modal } from "./ui.js";

const TZ = ["Asia/Kolkata", "Asia/Dubai", "Asia/Riyadh", "Asia/Qatar", "Asia/Muscat", "Asia/Kuwait", "Asia/Bahrain", "Asia/Dhaka", "Asia/Colombo", "Asia/Kathmandu", "Asia/Singapore", "Africa/Cairo", "Europe/London", "UTC"];
const CUR = ["INR", "AED", "SAR", "QAR", "OMR", "KWD", "BHD", "USD", "EUR", "GBP", "SGD", "BDT", "LKR", "NPR", "EGP"];
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const tempPw = () => Array.from(crypto.getRandomValues(new Uint8Array(9)), b => "abcdefghjkmnpqrstuvwxyz23456789"[b % 31]).join("") + "A1!";
const val = (r, n) => r.querySelector(`[name=${n}]`)?.value.trim() ?? "";
const chk = (r, n) => !!r.querySelector(`[name=${n}]`)?.checked;

export async function wizard(root, S) {
  let st = await get("/setup"), cur = st.steps.find(s => !s.done && s.required)?.key || st.steps[0].key;
  const done = () => st.steps.every(s => !s.required || s.done);
  const refresh = async () => { st = await get("/setup"); paintNav(); };
  root.innerHTML = `<div class="page-h"><h1>Set up your workspace</h1></div>
    <p class="muted" style="margin-top:-6px;max-width:70ch" id="intro"></p>
    <div class="grid" style="grid-template-columns:minmax(200px,260px) minmax(0,1fr);align-items:start"><div class="card" id="swn" style="padding:10px"></div><div class="card" id="swp"></div></div>`;
  $("#intro").textContent = st.complete ? "Setup is complete. You can revisit any step to change it." : "Finish the required steps below. Until you do, only administrators can use the workspace.";
  const paintNav = () => {
    $("#swn").innerHTML = `<div class="small muted" style="padding:4px 8px 8px">${st.progress}% done</div>` + st.steps.map(s => `<button class="btn" data-k="${s.key}" style="width:100%;justify-content:flex-start;margin-bottom:4px;${s.key === cur ? "border-color:var(--accent);background:var(--accent-soft)" : ""}">
      <span style="width:18px">${s.done ? "✓" : s.required ? "●" : "○"}</span><span style="flex:1;text-align:left">${esc(s.title)}</span>${s.required ? "" : '<span class="small muted">optional</span>'}</button>`).join("") +
      `<button class="btn ${done() ? "primary" : ""}" data-k="review" style="width:100%;margin-top:8px">${st.complete ? "Review" : "Review and finish"}</button>`;
    $$("[data-k]", $("#swn")).forEach(b => b.onclick = () => { cur = b.dataset.k; open(); });
  };
  const next = async () => { await refresh(); const keys = st.steps.map(s => s.key); const i = keys.indexOf(cur); cur = i + 1 < keys.length ? keys[i + 1] : "review"; open(); };
  const mark = async (key, d = true) => post(`/setup/step/${key}`, { done: d });
  const head = (t, p) => `<h2 style="margin-top:0">${esc(t)}</h2><p class="muted" style="margin-top:-4px">${esc(p)}</p>`;
  const foot = (extra = "") => `<div class="row" style="margin-top:16px;gap:8px"><button class="btn primary" id="sv">Save and continue</button>${extra}</div>`;
  const steps = {
    async company(p) {
      const c = await get("/company"), s = await get("/settings");
      p.innerHTML = head("Company profile", st.steps.find(x => x.key === "company").help) + `<div class="formgrid">
        <label class="f"><span>Company name *</span><input name="name" value="${esc(c.name || S.tenant.name)}"></label><label class="f"><span>Country *</span><input name="country" value="${esc(c.country || "")}" placeholder="India"></label>
        <label class="f"><span>Address</span><input name="address" value="${esc(c.address || "")}"></label><label class="f"><span>City</span><input name="city" value="${esc(c.city || "")}"></label>
        <label class="f"><span>State / region</span><input name="state" value="${esc(c.state || "")}"></label><label class="f"><span>Postal code</span><input name="pin" value="${esc(c.pin || "")}"></label>
        <label class="f"><span>Tax ID (GSTIN / VAT / TRN)</span><input name="gstin" value="${esc(c.gstin || "")}"></label><label class="f"><span>Phone</span><input name="phone" value="${esc(c.phone || "")}"></label>
        <label class="f"><span>Company email</span><input name="email" value="${esc(c.email || "")}"></label><label class="f"><span>Website</span><input name="website" value="${esc(c.website || "")}"></label>
        <label class="f"><span>Currency *</span><select name="currency">${CUR.map(x => `<option ${x === (s.currency || "INR") ? "selected" : ""}>${x}</option>`).join("")}</select></label>
        <label class="f"><span>Time zone *</span><select name="timezone">${TZ.map(x => `<option ${x === (s.timezone || "Asia/Kolkata") ? "selected" : ""}>${x}</option>`).join("")}</select></label>
        <label class="f"><span>Logo (light background)</span><input type="file" name="logo" accept="image/*"></label><label class="f"><span>Logo for dark mode (optional)</span><input type="file" name="logo_dark" accept="image/*"></label></div>` + foot();
      $("#sv", p).onclick = async () => {
        if (!val(p, "name") || !val(p, "country")) return toast("Company name and country are required.", "err");
        const body = {}; for (const k of ["name", "country", "address", "city", "state", "pin", "gstin", "phone", "email", "website"]) body[k] = val(p, k);
        await put("/company", body); await put("/settings", { currency: val(p, "currency"), timezone: val(p, "timezone") });
        for (const k of ["logo", "logo_dark"]) { const f = p.querySelector(`[name=${k}]`).files[0]; if (f) { const data = await new Promise(r => { const fr = new FileReader(); fr.onload = () => r(fr.result); fr.readAsDataURL(f); }); await post("/company/files", { kind: k, data }); } }
        await mark("company"); const me = await get("/auth/me"); S.tenant = me.tenant; window.dispatchEvent(new CustomEvent("fw-company", { detail: await get("/company") })); toast("Company profile saved."); next();
      };
    },
    async modules(p) {
      const m = { ...st.modules };
      p.innerHTML = head("Modules", st.steps.find(x => x.key === "modules").help) + st.module_catalog.map(x => `<label class="card row" style="padding:10px;margin-bottom:8px;gap:10px;cursor:pointer"><input type="checkbox" name="m_${x.key}" ${m[x.key] ? "checked" : ""}><span><b>${esc(x.title)}</b><span class="small muted" style="display:block">${esc(x.about)}</span></span></label>`).join("") + foot();
      $("#sv", p).onclick = async () => {
        const mods = {}; st.module_catalog.forEach(x => mods[x.key] = chk(p, "m_" + x.key));
        await put("/setup/modules", { modules: mods }); const me = await get("/auth/me"); S.tenant = me.tenant; toast("Modules saved."); next();
      };
    },
    async work_rules(p) {
      const s = await get("/settings"), off = new Set(s.weekly_off ?? [6]);
      p.innerHTML = head("Work rules", st.steps.find(x => x.key === "work_rules").help) + `<div class="formgrid">
        <label class="f"><span>Work starts</span><input type="time" name="work_start" value="${esc(s.work_start || "09:30")}"></label><label class="f"><span>Work ends</span><input type="time" name="work_end" value="${esc(s.work_end || "18:30")}"></label>
        <label class="f"><span>Late after (minutes grace)</span><input type="number" name="late_grace_min" min="0" value="${s.late_grace_min ?? 15}"></label><label class="f"><span>Alert when GPS is silent for (minutes)</span><input type="number" name="gps_off_alert_min" min="5" value="${s.gps_off_alert_min ?? 15}"></label></div>
        <div class="f"><span style="font-weight:550;font-size:13px">Weekly off</span><div class="row">${DAYS.map((d, i) => `<label><input type="checkbox" name="off${i}" ${off.has(i) ? "checked" : ""}> ${d}</label>`).join("")}</div></div>
        <div style="display:grid;gap:6px;margin-top:10px">${[["enforce_geofence", "Check-in must be at an office or customer location", true], ["block_mock_location", "Block fake-GPS (mock location) apps", true], ["require_face", "Require face match at check-in", false], ["require_visit_otp", "Customer confirms each visit with a code (OTP)", true], ["require_tracking_consent", "Show the location-tracking notice and ask each person to accept it", true]]
          .map(([k, t, d]) => `<label class="row" style="gap:8px"><input type="checkbox" name="${k}" ${(s[k] ?? d) ? "checked" : ""}> ${esc(t)}</label>`).join("")}</div>` + foot();
      $("#sv", p).onclick = async () => {
        const body = { work_start: val(p, "work_start"), work_end: val(p, "work_end"), late_grace_min: +val(p, "late_grace_min") || 0, gps_off_alert_min: +val(p, "gps_off_alert_min") || 15, weekly_off: DAYS.map((_, i) => i).filter(i => chk(p, "off" + i)) };
        for (const k of ["enforce_geofence", "block_mock_location", "require_face", "require_visit_otp", "require_tracking_consent"]) body[k] = chk(p, k);
        await put("/settings", body); await mark("work_rules"); toast("Work rules saved."); next();
      };
    },
    async branches(p) {
      const list = await get("/branches");
      p.innerHTML = head("Offices and branches", st.steps.find(x => x.key === "branches").help) + `<div id="bl">${list.length ? list.map(b => `<div class="card row" style="padding:8px 12px;margin-bottom:6px"><b>${esc(b.name)}</b><span class="small muted" style="margin-left:8px">${esc(b.address || "")} · ${(+b.lat).toFixed(4)}, ${(+b.lng).toFixed(4)} · ${b.radius_m} m</span></div>`).join("") : '<p class="muted">No office yet.</p>'}</div>
        <h3 style="margin:14px 0 6px">Add an office</h3><div class="formgrid"><label class="f"><span>Name *</span><input name="bname" placeholder="Head office"></label><label class="f"><span>Address</span><input name="baddr"></label>
        <label class="f"><span>Latitude *</span><input name="blat" inputmode="decimal"></label><label class="f"><span>Longitude *</span><input name="blng" inputmode="decimal"></label>
        <label class="f"><span>Check-in radius (metres)</span><input name="brad" type="number" value="150" min="20" max="5000"></label></div>
        <div class="row" style="gap:8px;margin-top:8px"><button class="btn" id="loc" type="button">Use my current location</button><button class="btn primary" id="addb" type="button">Add office</button></div>` +
        foot(`<span class="muted small">Add at least one office to continue.</span>`);
      $("#loc", p).onclick = async () => { try { const h = await here(); p.querySelector("[name=blat]").value = h.lat.toFixed(6); p.querySelector("[name=blng]").value = h.lng.toFixed(6); } catch (e) { fail(e); } };
      $("#addb", p).onclick = async () => { try { await post("/branches", { name: val(p, "bname"), address: val(p, "baddr"), lat: val(p, "blat"), lng: val(p, "blng"), radius_m: +val(p, "brad") || 150 }); toast("Office added."); open(); } catch (e) { fail(e); } };
      $("#sv", p).onclick = async () => { await refresh(); if (!st.steps.find(x => x.key === "branches").done) return toast("Add at least one office first.", "err"); next(); };
    },
    async team(p) {
      p.innerHTML = head("People", st.steps.find(x => x.key === "team").help) + `<p class="small">One person per line: <code>Name, email, role</code> (role: admin, manager or employee). Each person gets a temporary password you can hand over; they can change it after signing in.</p>
        <textarea name="lines" rows="6" style="width:100%" placeholder="Asha Rao, asha@company.com, manager&#10;Ravi Kumar, ravi@company.com, employee"></textarea><div id="out"></div>` + foot(`<button class="btn" id="skip">Skip for now</button>`);
      $("#sv", p).onclick = async () => {
        const rows = val(p, "lines").split("\n").map(l => l.split(",").map(x => x.trim())).filter(r => r[0] || r[1]); const made = [], bad = [];
        for (const [name, email, role = "employee"] of rows) { const password = tempPw(); try { await post("/users", { name, email, role: role.toLowerCase(), password }); made.push({ name, email, password }); } catch (e) { bad.push(`${email || name}: ${e.message}`); } }
        if (made.length) await mark("team");
        $("#out", p).innerHTML = (made.length ? `<div class="card" style="margin-top:10px"><b>Created ${made.length}. Copy these temporary passwords now: they are shown only once.</b><table style="width:100%"><tbody>${made.map(m => `<tr><td>${esc(m.name)}</td><td>${esc(m.email)}</td><td><code>${esc(m.password)}</code></td></tr>`).join("")}</tbody></table></div>` : "") + (bad.length ? `<p class="small" style="color:var(--danger)">${bad.map(esc).join("<br>")}</p>` : "");
        if (!rows.length) next(); else if (!bad.length) { $("#sv", p).textContent = "Continue"; $("#sv", p).onclick = () => next(); }
      };
      $("#skip", p).onclick = async () => { await mark("team"); next(); };
    },
    async catalog(p) {
      const [src, reg] = await Promise.all([get("/masters?kind=lead_source"), get("/masters?kind=region")]);
      p.innerHTML = head("Products and sales masters", st.steps.find(x => x.key === "catalog").help) + `<p class="small">Add extra lead sources or regions (one per line). Defaults are listed so you can see what already exists.</p>
        <div class="formgrid"><label class="f"><span>Lead sources now</span><div class="small muted">${src.map(x => esc(x.name)).join(", ") || "none"}</div><textarea name="src" rows="4" placeholder="Add more, one per line"></textarea></label>
        <label class="f"><span>Regions now</span><div class="small muted">${reg.map(x => esc(x.name)).join(", ") || "none"}</div><textarea name="reg" rows="4" placeholder="Add more, one per line"></textarea></label></div>
        <p class="small">Products: add them under <a href="#/products">Products</a> (CSV import) or sync from your ERP under Settings, Integrations.</p>` + foot(`<button class="btn" id="skip">Skip for now</button>`);
      $("#sv", p).onclick = async () => {
        for (const [kind, name] of [["lead_source", "src"], ["region", "reg"]]) for (const n of val(p, name).split("\n").map(x => x.trim()).filter(Boolean)) { try { await post("/masters", { kind, name: n }); } catch { /* duplicates are fine */ } }
        await mark("catalog"); toast("Saved."); next();
      };
      $("#skip", p).onclick = async () => { await mark("catalog"); next(); };
    },
    async integrations(p) {
      p.innerHTML = head("Integrations", st.steps.find(x => x.key === "integrations").help) + `<ul><li><b>ERPNext</b>: sync customers, products, the sales team and invoices.</li><li><b>WhatsApp / SMS gateway</b> and <b>email sending</b>: reminders and quotations.</li><li><b>Lead capture</b>: website forms and portals.</li></ul>
        <p class="small">These need keys from your providers, so they are set up in <a href="#/settings">Settings</a> whenever you are ready.</p>` + foot();
      $("#sv", p).textContent = "Done for now"; $("#sv", p).onclick = async () => { await mark("integrations"); next(); };
    },
    async review(p) {
      await refresh();
      p.innerHTML = head(st.complete ? "Setup complete" : "Review and finish", st.complete ? "Everyone in your company can now use the workspace." : "Required steps must be done before your team can sign in.") +
        st.steps.map(s => `<div class="row" style="padding:6px 0;border-bottom:1px solid var(--border)"><span style="width:22px">${s.done ? "✅" : s.required ? "⬜" : "➖"}</span><b>${esc(s.title)}</b><span class="small muted" style="margin-left:8px">${esc(s.detail || "")}</span><span class="right small ${s.done ? "" : s.required ? "" : "muted"}">${s.done ? "done" : s.required ? "required" : "optional"}</span></div>`).join("") +
        (st.complete ? "" : `<div class="row" style="margin-top:16px"><button class="btn primary" id="fin" ${done() ? "" : "disabled"}>Finish setup</button></div>`);
      $("#fin", p)?.addEventListener("click", async () => { try { await post("/setup/complete"); toast("Setup complete. Your team can now sign in."); window.dispatchEvent(new Event("fw-setup-done")); } catch (e) { fail(e); } });
    },
  };
  async function open() { paintNav(); const p = $("#swp"); p.innerHTML = '<p class="muted">Loading…</p>'; try { await steps[cur](p); } catch (e) { p.innerHTML = `<p style="color:var(--danger)">${esc(e.message)}</p>`; } }
  paintNav(); await open();
}
