// The vendor's console (super-admins only): provision client workspaces, see their setup progress, suspend, change plan and seats,
// open a client as its administrator (audited), and manage the vendor's own administrators.
import { get, post, patch } from "./api.js";
import { $, $$, esc, toast, fail, modal, drawer, table, wireTable, chip, fmtDT, fmtD, skeleton } from "./ui.js";

const PLANS = ["trial", "starter", "growth", "enterprise"];
const copy = async (text, btn) => { try { await navigator.clipboard.writeText(text); btn.textContent = "Copied"; } catch { const t = document.createElement("textarea"); t.value = text; document.body.appendChild(t); t.select(); document.execCommand("copy"); t.remove(); btn.textContent = "Copied"; } };

function inviteBox(d) {
  return `<div class="card" style="margin-top:10px"><b>Invitation link for ${esc(d.admin?.email || "the administrator")}</b>
    <div class="small muted">${d.emailed ? "It was also emailed. " : "Email is not set up on this server, so send it yourself. "}Valid for 7 days. It lets them choose their own password.</div>
    <input readonly value="${esc(d.invite_link)}" style="width:100%;margin-top:6px" onclick="this.select()"><div class="row" style="margin-top:6px;gap:8px"><button class="btn sm" data-copy>Copy link</button><span class="small">Workspace code: <b>${esc(d.slug)}</b></span></div></div>`;
}

export async function clients(root, S) {
  root.innerHTML = `<div class="page-h"><h1>Clients</h1><span class="muted" id="cnt"></span><div class="right row"><button class="btn primary" id="new">+ New client</button></div></div><div id="list">${skeleton(4)}</div>`;
  const load = async () => {
    const rows = await get("/platform/tenants");
    $("#cnt").textContent = `${rows.length} client${rows.length === 1 ? "" : "s"} · ${rows.filter(r => r.active).length} active`;
    $("#list").innerHTML = table([
      { h: "Client", f: r => `<strong>${esc(r.name)}</strong><div class="small muted">code ${esc(r.slug)}</div>` },
      { h: "Administrator", f: r => r.admin ? `${esc(r.admin.name)}<div class="small muted">${esc(r.admin.email)}</div>` : "—" },
      { h: "Plan", f: r => `${esc(r.plan)}<div class="small muted">${r.users} of ${r.seat_limit} seats</div>` },
      { h: "Setup", f: r => r.setup_complete ? chip("Complete", "ok") : chip(`${r.setup_progress}% done`, "warn") },
      { h: "Status", f: r => r.active ? chip("Active", "ok") : chip("Suspended", "bad") },
      { h: "Last active", f: r => r.last_seen ? fmtDT(r.last_seen) : "never" },
    ], rows, { onRow: true, empty: "No clients yet. Add the first one." });
    wireTable($("#list"), rows, r => detail(r.id));
  };
  $("#new").onclick = () => {
    const body = document.createElement("div");
    body.innerHTML = `<div class="formgrid"><label class="f"><span>Company name *</span><input id="nc"></label><label class="f"><span>Workspace code (optional)</span><input id="ns" placeholder="auto from the name"></label>
      <label class="f"><span>Administrator name *</span><input id="nn"></label><label class="f"><span>Administrator email *</span><input id="ne" type="email"></label>
      <label class="f"><span>Country</span><input id="nk" value="India"></label><label class="f"><span>Plan</span><select id="np">${PLANS.map(p => `<option>${p}</option>`).join("")}</select></label>
      <label class="f"><span>User seats</span><input id="nl" type="number" min="1" value="10"></label><label class="f"><span>Start from another client's configuration (optional)</span><input id="nf" type="file" accept="application/json"></label></div>
      <label class="f"><span>Internal notes</span><textarea id="nt" rows="2"></textarea></label><p class="small muted">The administrator receives a link to choose a password, then must complete the setup wizard before their team can sign in.</p>`;
    modal({ title: "New client", body, wide: true, actions: [{ label: "Cancel", close: true }, { label: "Create client", primary: true, onClick: async (el, close) => {
      let config = null; const f = $("#nf", body).files[0]; if (f) { try { config = JSON.parse(await f.text()); } catch { throw new Error("That file is not valid JSON."); } }
      const d = await post("/platform/tenants", { company: $("#nc", body).value, slug: $("#ns", body).value, admin_name: $("#nn", body).value, admin_email: $("#ne", body).value, country: $("#nk", body).value, plan: $("#np", body).value, seat_limit: +$("#nl", body).value || 10, notes: $("#nt", body).value, config });
      close(); const m = modal({ title: "Client created", body: `<p><b>${esc(d.name)}</b> is ready.</p>${inviteBox(d)}`, actions: [{ label: "Done", primary: true, close: true }] }); $("[data-copy]", m.root).onclick = e => copy(d.invite_link, e.target); load(); return false; } }] });
  };
  async function detail(id) {
    const d = await get("/platform/tenants/" + id);
    const dr = drawer({ title: d.name, body: `<div id="dd"></div>` });
    const paint = d => {
      $("#dd", dr.root).innerHTML = `<div class="row" style="gap:6px;flex-wrap:wrap">${d.active ? chip("Active", "ok") : chip("Suspended", "bad")} ${d.setup_complete ? chip("Setup complete", "ok") : chip("Setup " + d.setup_progress + "%", "warn")} ${chip(d.plan, "info")} ${d.erp_connected ? chip("ERP connected", "ok") : ""}</div>
        <p class="small muted">Workspace code <b>${esc(d.slug)}</b> · created ${fmtD(d.created_at)} · administrator ${esc(d.admin?.name || "—")} (${esc(d.admin?.email || "")})</p>
        <h3>Setup progress</h3>${d.setup.steps.map(s => `<div class="row small" style="padding:3px 0"><span style="width:20px">${s.done ? "✅" : s.required ? "⬜" : "➖"}</span>${esc(s.title)}<span class="muted" style="margin-left:6px">${esc(s.detail || "")}</span></div>`).join("")}
        <h3>People</h3><p class="small">${Object.entries(d.users_by_role).map(([r, n]) => `${n} ${esc(r)}`).join(" · ") || "none"} · seats ${d.users} of ${d.seat_limit}</p>
        <h3>Subscription</h3><div class="formgrid"><label class="f"><span>Plan</span><select id="dp">${PLANS.map(p => `<option ${p === d.plan ? "selected" : ""}>${p}</option>`).join("")}</select></label><label class="f"><span>Seats</span><input id="dl" type="number" min="1" value="${d.seat_limit}"></label></div>
        <label class="f"><span>Internal notes</span><textarea id="dn" rows="2">${esc(d.notes || "")}</textarea></label>
        <div class="row" style="gap:8px;flex-wrap:wrap;margin-top:8px"><button class="btn primary" id="ds">Save</button><button class="btn" id="dopen">Open as administrator</button><button class="btn" id="dinv">New invite link</button><button class="btn" id="dexp">Export configuration</button>
          <button class="btn ${d.active ? "danger" : ""}" id="dact">${d.active ? "Suspend" : "Reactivate"}</button></div><div id="dx"></div>
        <h3>Recent platform activity</h3>${d.platform_log.map(l => `<div class="small"><span class="muted">${fmtDT(l.at)}</span> ${esc(l.action.replace("platform.", ""))} ${esc(l.detail)}</div>`).join("") || '<p class="small muted">None.</p>'}`;
      $("#ds", dr.root).onclick = async () => { try { paint(await patch("/platform/tenants/" + id, { plan: $("#dp", dr.root).value, seat_limit: +$("#dl", dr.root).value, notes: $("#dn", dr.root).value })); toast("Saved."); load(); } catch (e) { fail(e); } };
      $("#dact", dr.root).onclick = async () => { if (d.active && !confirm("Suspend this client? Nobody in their company will be able to sign in.")) return; try { paint(await patch("/platform/tenants/" + id, { active: !d.active })); load(); } catch (e) { fail(e); } };
      $("#dinv", dr.root).onclick = async () => { try { const r = await post(`/platform/tenants/${id}/invite`); $("#dx", dr.root).innerHTML = inviteBox({ ...r, slug: d.slug, admin: d.admin }); $("[data-copy]", dr.root).onclick = e => copy(r.invite_link, e.target); } catch (e) { fail(e); } };
      $("#dexp", dr.root).onclick = async () => { try { const c = await get(`/platform/tenants/${id}/config`); const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([JSON.stringify(c, null, 1)], { type: "application/json" })); a.download = `${d.slug}-configuration.json`; a.click(); } catch (e) { fail(e); } };
      $("#dopen", dr.root).onclick = async () => {
        try { const r = await post(`/platform/tenants/${id}/open`); localStorage.setItem("fw_platform_session", JSON.stringify({ token: localStorage.getItem("fw_token"), user: localStorage.getItem("fw_user"), ws: localStorage.getItem("fw_ws") }));
          localStorage.setItem("fw_token", r.token); localStorage.setItem("fw_user", JSON.stringify(r.user)); localStorage.setItem("fw_ws", r.workspace); location.hash = "#/"; location.reload(); } catch (e) { fail(e); }
      };
    };
    paint(d);
  }
  await load();
}

export async function admins(root, S) {
  root.innerHTML = `<div class="page-h"><h1>Platform administrators</h1><div class="right"><button class="btn primary" id="new">+ Add administrator</button></div></div><p class="muted" style="margin-top:-6px">People at your company who can create and manage client workspaces. They cannot see a client's business data except by opening it as that client's administrator, which is logged.</p><div id="list">${skeleton(3)}</div>`;
  const load = async () => { const rows = await get("/platform/admins"); $("#list").innerHTML = table([{ h: "Name", f: r => `<strong>${esc(r.name)}</strong>` }, { h: "Email", f: r => esc(r.email) }, { h: "Last active", f: r => r.last_seen ? fmtDT(r.last_seen) : "never" }], rows); };
  $("#new").onclick = () => modal({ title: "Add platform administrator", body: `<label class="f"><span>Name</span><input id="an"></label><label class="f"><span>Email</span><input id="ae" type="email"></label>`,
    actions: [{ label: "Cancel", close: true }, { label: "Add", primary: true, onClick: async (el, close) => { const r = await post("/platform/admins", { name: $("#an", el).value, email: $("#ae", el).value }); close();
      const m = modal({ title: "Administrator added", body: `<p>Send them this link so they can choose a password. Their workspace code is <b>_platform</b>.</p><input readonly value="${esc(r.invite_link)}" style="width:100%" onclick="this.select()"><div style="margin-top:6px"><button class="btn sm" data-copy>Copy link</button></div>`, actions: [{ label: "Done", primary: true, close: true }] });
      $("[data-copy]", m.root).onclick = e => copy(r.invite_link, e.target); load(); return false; } }] });
  await load();
}
