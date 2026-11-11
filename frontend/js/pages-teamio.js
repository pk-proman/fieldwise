// Team import / export (CSV) for Settings > Users & hierarchy.
import { get, api } from "./api.js";
import { $, esc, toast, fail, modal, download } from "./ui.js";

export function teamIO(root, reload) {
  $("#tio-export", root)?.addEventListener("click", async () => { try { download("team.csv", await get("/users/export")); } catch (e) { fail(e); } });
  $("#tio-import", root)?.addEventListener("click", () => {
    let file = null;
    modal({ title: "Import team from CSV", wide: true,
      body: `<p class="muted small">One row per person. Needs <b>name</b> and <b>email</b>; the other columns are optional. People already in the app are matched by email or employee code and updated. <b>reporting_manager</b> is the manager's email or employee code. New people get no password: give each an app PIN afterwards.</p>
        <div class="row" style="margin:8px 0"><button class="btn sm" id="tio-tpl" type="button">Download template</button><input type="file" id="tio-file" accept=".csv,text/csv" aria-label="CSV file"></div><div id="tio-out" class="small"></div>`,
      actions: [{ label: "Close", close: true }, { label: "Check file", onClick: async el => run(el, false) }, { label: "Import", primary: true, onClick: async el => run(el, true) }],
      onMount: el => { $("#tio-tpl", el).onclick = async () => { try { download("team-import-template.csv", await get("/users/import-template")); } catch (e) { fail(e); } }; } });
    async function run(el, commit) {
      file = $("#tio-file", el)?.files?.[0];
      if (!file) { toast("Choose a CSV file first."); return false; }
      const fd = new FormData(); fd.append("file", file); fd.append("commit", commit ? "true" : "false");
      try {
        const r = await api("POST", "/users/import", fd);
        const errs = r.errors.map(e => `<li>Row ${e.row} (${esc(e.email || "")}): ${esc(e.error)}</li>`).join("");
        $("#tio-out", el).innerHTML = `<div class="card" style="padding:10px"><b>${commit ? "Done" : "Preview"}:</b> ${r.created} to create, ${r.updated} to update, ${r.skipped} skipped.${errs ? `<ul style="margin:6px 0 0 18px">${errs}</ul>` : ""}${commit ? `<p class="muted">${esc(r.note)}</p>` : ""}</div>`;
        if (commit) { toast("Team imported."); reload(); return false; }
      } catch (e) { fail(e); }
      return false;
    }
  });
}
