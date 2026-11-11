// Settings > Expense rules: how the app turns each person's tracked route into expense claims.
import { get, put } from "./api.js";
import { $, $$, esc, toast, fail } from "./ui.js";

const FIELDS = [["rate_per_km", "Rate per km"], ["min_km", "Minimum km for a claim"], ["daily_cap", "Daily fuel cap (0 = none)"], ["allowance_daily", "Daily allowance"], ["allowance_outstation", "Outstation allowance"], ["outstation_km", "Outstation if furthest point is over (km)"]];
const VEHICLES = [["bike", "Two-wheeler"], ["car", "Car"], ["public", "Public transport"], ["other", "Other vehicle"]];

export async function exprules(pane, S) {
  const [pol, desg] = await Promise.all([get("/expenses/policy"), get("/designations")]);
  const cell = (k, v, g, id) => `<input data-k="${k}" data-g="${g}" data-d="${id}" type="number" min="0" step="any" value="${v ?? ""}" style="width:100%;min-width:70px" aria-label="${k}">`;
  const cols = [["default", "all", "Everyone"], ...["employee", "manager", "admin"].map(r => ["by_role", r, "Role: " + r]), ...VEHICLES.map(([v, l]) => ["by_vehicle", v, "Vehicle: " + l]),
    ...desg.map(d => ["by_designation", d.id, "Designation: " + d.name])];
  const val = (g, id, k) => g === "default" ? pol.default[k] : pol[g]?.[id]?.[k];
  pane.innerHTML = `<div class="card"><h2>Expense rules</h2>
    <p class="muted small">Each day, after check-out, the app measures the person's tracked route and applies these rules. Start in <b>Shadow</b>: amounts are calculated and shown to managers, but no claim is created and nobody is paid from them. When the numbers match what people actually travelled, switch to <b>On</b>.</p>
    <label class="f"><span>Mode</span><select id="xp-mode"><option value="off">Off: people claim by hand</option><option value="shadow">Shadow: calculate and show only</option><option value="on">On: create claims automatically for approval</option></select></label>
    <label class="f"><span><input type="checkbox" id="xp-est"> Also pay for stretches where the phone was silent (estimated distance)</span></label>
    <div class="tablewrap"><table class="stack"><thead><tr><th>Rule</th>${cols.map(c => `<th>${esc(c[2])}</th>`).join("")}</tr></thead><tbody>
      ${FIELDS.map(([k, l]) => `<tr><td>${l}</td>${cols.map(([g, id]) => `<td>${cell(k, val(g, id, k), g, id)}</td>`).join("")}</tr>`).join("")}
    </tbody></table></div>
    <p class="muted small">Fill only the cells you need. The most specific wins: Everyone, then role, then designation, then vehicle. Set each person's vehicle on their user record. Claims go through the usual approval levels. A fuel claim made by hand for a day that already has an automatic one is blocked, so nobody is paid twice.</p>
    <button class="btn primary" id="xp-save" type="button">Save rules</button></div>`;
  $("#xp-mode", pane).value = pol.mode; $("#xp-est", pane).checked = pol.include_estimated;
  $("#xp-save", pane).onclick = async () => {
    const body = { mode: $("#xp-mode", pane).value, include_estimated: $("#xp-est", pane).checked, default: {}, by_designation: {}, by_role: {}, by_vehicle: {} };
    $$("input[data-k]", pane).forEach(i => { if (i.value === "") return; const g = i.dataset.g; if (g === "default") body.default[i.dataset.k] = +i.value; else (body[g][i.dataset.d] ||= {})[i.dataset.k] = +i.value; });
    try { await put("/expenses/policy", body); toast("Expense rules saved."); } catch (e) { fail(e); }
  };
}
