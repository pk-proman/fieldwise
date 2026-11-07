// Small shared builders: generic CRUD list page, CSV import dialog.
import { get, post, patch, del, api, session } from "./api.js";
import { $, $$, esc, toast, fail, table, wireTable, modal, confirmBox, fieldsHTML, readFields, validate, skeleton, download } from "./ui.js";

export const isMgr = S => ["manager", "admin", "owner"].includes(S.user.role);
export const isAdmin = S => ["admin", "owner"].includes(S.user.role);
export const cap = s => s ? s[0].toUpperCase() + s.slice(1).replace(/_/g, " ") : "";
export const tok = () => encodeURIComponent(session.token || "");

/** Generic list + create/edit/delete page. cfg: {title, hint, list, create, update(id), remove(id), fields(rec), cols, write(S), empty, rowActions(r), after(root, rows, reload), toPayload(d, rec)} */
export async function crudPage(root, S, cfg) {
  let rows = [];
  root.innerHTML = `<div class="page-h"><h1>${esc(cfg.title)}</h1>${cfg.hint ? `<span class="muted small">${esc(cfg.hint)}</span>` : ""}<div class="right row">${cfg.toolbar || ""}${cfg.create && cfg.write(S) ? `<button class="btn primary" id="kit-new">+ ${esc(cfg.newLabel || "New")}</button>` : ""}</div></div><div id="kit-list">${skeleton(4)}</div>`;
  const reload = async () => {
    rows = await get(typeof cfg.list === "function" ? cfg.list() : cfg.list); if (cfg.filter) rows = rows.filter(cfg.filter);
    $("#kit-list").innerHTML = table([...cfg.cols, ...(cfg.rowActions ? [{ h: "", f: r => `<span class="nowrap">${cfg.rowActions(r)}</span>` }] : [])], rows, { onRow: !!cfg.update && cfg.write(S), empty: cfg.empty || "Nothing here yet." });
    wireTable($("#kit-list"), rows, r => edit(r)); cfg.after?.(root, rows, reload);
  };
  const form = rec => {
    const f = cfg.fields(rec || null, rows);
    modal({ title: rec ? "Edit" : (cfg.newLabel || "New"), wide: cfg.wide, body: fieldsHTML(f, rec || {}), onMount: el => cfg.onForm?.(el, rec),
      actions: [...(rec && cfg.remove && cfg.write(S) ? [{ label: "Delete", danger: true, onClick: async () => { if (!(await confirmBox("Delete?", "This can't be undone.", "Delete", true))) return false; await del(cfg.remove(rec.id)); toast("Deleted."); reload(); } }] : []),
        { label: "Cancel", close: true }, { label: "Save", primary: true, onClick: async el => { validate(el, f); let d = readFields(el, f); f.forEach(x => { if (x.int && d[x.k]) d[x.k] = +d[x.k]; if (x.list && typeof d[x.k] === "string") d[x.k] = d[x.k].split(",").map(s => s.trim()).filter(Boolean); }); if (cfg.toPayload) d = cfg.toPayload(d, rec);
          rec ? await patch(cfg.update(rec.id), d) : await post(cfg.create, d); toast("Saved."); reload(); } }] });
  };
  const edit = r => form(r);
  $("#kit-new")?.addEventListener("click", () => form(null));
  await reload(); return reload;
}

/** CSV import dialog with validate-first (dry run). */
export function importModal(entity, label, onDone, extra = "") {
  const body = document.createElement("div");
  body.innerHTML = `<p>Upload a CSV with a header row. Columns are matched by name.</p><div class="row" style="margin-bottom:10px"><button class="btn sm" id="tpl" type="button">Download template</button></div>${extra}<input type="file" id="cf" accept=".csv,text/csv"><div id="res" style="margin-top:12px"></div>`;
  $("#tpl", body).onclick = async () => download(`${entity}-template.csv`, await get(`/import/${entity}/template`));
  let validated = false;
  const run = async (dry, el) => { const f = $("#cf", body).files[0]; if (!f) throw new Error("Choose a CSV file."); const fd = new FormData(); fd.append("file", f); const dup = $("#dup", body)?.value || "repeat";
    const r = await api("POST", `/import/${entity}?dry_run=${dry}&on_duplicate=${dup}`, fd);
    $("#res", body).innerHTML = `<div class="card" style="padding:10px"><b>${dry ? "Check result" : "Imported"}</b><div class="small">${r.created} new${r.repeat ? ` · ${r.repeat} repeat enquiries` : ""}${r.updated ? ` · ${r.updated} updated` : ""} · ${r.skipped} skipped</div><div class="small muted">Columns used: ${esc(r.mapped.join(", "))}</div>${r.errors.length ? `<ul class="small" style="color:var(--danger);padding-left:16px">${r.errors.slice(0, 10).map(e => `<li>${esc(e)}</li>`).join("")}</ul>` : ""}</div>`; return r; };
  modal({ title: "Import " + label, body, wide: true, actions: [{ label: "Close", close: true }, { label: "Check file", onClick: async () => { await run(true); validated = true; return false; } }, { label: "Import now", primary: true, onClick: async () => { if (!validated && !(await confirmBox("Import without checking?", "We recommend running 'Check file' first.", "Import anyway"))) return false; const r = await run(false); toast(`Imported ${r.created + (r.updated || 0)} record(s).`); onDone?.(); return false; } }] });
}
