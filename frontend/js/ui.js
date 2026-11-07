// Tiny UI toolkit: escaping, toasts, modal, drawer, forms, tables, maps.
export const $ = (s, r = document) => r.querySelector(s);
export const $$ = (s, r = document) => [...r.querySelectorAll(s)];
export const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const money = (n, cur = "INR") => { try { return new Intl.NumberFormat(cur === "INR" ? "en-IN" : "en-US", { style: "currency", currency: cur, maximumFractionDigits: 0 }).format(n || 0); } catch { return cur + " " + (n || 0); } };
export const fmtDT = s => s ? new Date(s).toLocaleString([], { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "—";
export const fmtT = s => s ? new Date(s).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—";
export const fmtD = s => s ? new Date(s.length === 10 ? s + "T00:00" : s).toLocaleDateString([], { day: "2-digit", month: "short", year: "numeric" }) : "—";
export const today = () => new Date(Date.now() - new Date().getTimezoneOffset() * 6e4).toISOString().slice(0, 10);
export const debounce = (f, ms = 300) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => f(...a), ms); }; };

export function toast(msg, kind = "ok") {
  const el = document.createElement("div"); el.className = "toast" + (kind === "err" ? " err" : ""); el.textContent = msg;
  $("#toasts").appendChild(el); setTimeout(() => el.remove(), kind === "err" ? 6000 : 3200);
}
export const fail = e => { console.error(e); toast(e?.message || "Something went wrong.", "err"); };

export const icon = n => ({
  home: '<path d="M3 11l9-8 9 8M5 10v10h14V10"/>', clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>', map: '<path d="M9 4L3 6v14l6-2 6 2 6-2V4l-6 2-6-2zM9 4v14M15 6v14"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2 20c0-3.5 3-6 7-6s7 2.500 7 6M17 5a3.500 3.500 0 010 7M22 20c0-2.500-1.500-4.500-4-5.500"/>',
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4"/>', building: '<path d="M4 21V5l8-2v18M12 9h8v12M7 9h2M7 13h2M7 17h2M15 13h2M15 17h2"/>',
  check: '<path d="M4 12l5 5L20 6"/>', route: '<circle cx="6" cy="19" r="2"/><circle cx="18" cy="5" r="2"/><path d="M8 19h7a3 3 0 000-6H9a3 3 0 010-6h7"/>',
  cal: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>', wallet: '<path d="M3 7h16a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V7zm0 0l13-3v3M17 14h2"/>',
  cog: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2"/>', box: '<path d="M21 8l-9-5-9 5v8l9 5 9-5V8zM3 8l9 5 9-5M12 13v8"/>',
  bell: '<path d="M6 17V11a6 6 0 1112 0v6l2 2H4l2-2zM10 21h4"/>', pin: '<path d="M12 21s7-6 7-12a7 7 0 10-14 0c0 6 7 12 7 12z"/><circle cx="12" cy="9" r="2.500"/>',
  contact: '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/>', handshake: '<path d="M3 12l5-5 4 3 4-3 5 5-8 8-9-8z"/>',
}[n] || "") ;
export const svgIcon = n => `<svg viewBox="0 0 24 24" aria-hidden="true">${icon(n)}</svg>`;

export function modal({ title, body, actions = [], wide = false, onMount }) {
  const root = document.createElement("div"); root.className = "scrim";
  root.innerHTML = `<div class="modal ${wide ? "wide" : ""}" role="dialog" aria-modal="true" aria-label="${esc(title)}"><header><h2>${esc(title)}</h2><button class="btn sm icon right" aria-label="Close" data-x>✕</button></header><div class="body"></div><footer></footer></div>`;
  const b = $(".body", root); typeof body === "string" ? (b.innerHTML = body) : b.appendChild(body);
  const f = $("footer", root); if (!actions.length) f.remove();
  const close = () => { root.remove(); document.removeEventListener("keydown", esc_); prev?.focus?.(); };
  const prev = document.activeElement;
  const esc_ = e => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", esc_);
  actions.forEach(a => {
    const btn = document.createElement("button"); btn.className = "btn " + (a.primary ? "primary" : a.danger ? "danger" : ""); btn.textContent = a.label;
    btn.onclick = async () => { if (a.close) return close(); btn.disabled = true; try { const r = await a.onClick?.(root, close); if (r !== false) close(); } catch (e) { fail(e); } finally { btn.disabled = false; } };
    f.appendChild(btn);
  });
  $("[data-x]", root).onclick = close; root.addEventListener("mousedown", e => { if (e.target === root) close(); });
  document.body.appendChild(root); onMount?.(root, close);
  (root.querySelector("input,select,textarea,button.primary") || root.querySelector("button"))?.focus();
  return { root, close };
}
export function confirmBox(title, text, label = "Confirm", danger = false) {
  return new Promise(res => { let done = false; const m = modal({ title, body: `<p>${esc(text)}</p>`, actions: [{ label: "Cancel", close: true }, { label, primary: !danger, danger, onClick: () => { done = true; res(true); } }] });
    new MutationObserver(() => { if (!document.body.contains(m.root) && !done) res(false); }).observe(document.body, { childList: true }); });
}
export function drawer({ title, body, onMount }) {
  const scrim = document.createElement("div"); scrim.className = "scrim"; scrim.style.justifyContent = "flex-end"; scrim.style.padding = "0";
  scrim.innerHTML = `<aside class="drawer" role="dialog" aria-label="${esc(title)}"><header><h2>${esc(title)}</h2><button class="btn sm icon right" aria-label="Close" data-x>✕</button></header><div class="body"></div></aside>`;
  const b = $(".body", scrim); typeof body === "string" ? (b.innerHTML = body) : b.appendChild(body);
  const close = () => scrim.remove(); $("[data-x]", scrim).onclick = close; scrim.addEventListener("mousedown", e => { if (e.target === scrim) close(); });
  document.body.appendChild(scrim); onMount?.(scrim, close); return { root: scrim, body: b, close };
}

// field = {k,label,type:text|number|date|datetime|select|textarea|checkbox|tel|email|password,options:[v | [v,label]],req,hint,full,ph}
export function fieldsHTML(fields, v = {}) {
  return `<div class="formgrid">` + fields.map(f => {
    const val = v[f.k] ?? f.def ?? "";
    let input;
    if (f.type === "select") input = `<select name="${f.k}" ${f.req ? "required" : ""}><option value="">${f.ph || "Select…"}</option>${(f.options || []).map(o => { const [ov, ol] = Array.isArray(o) ? o : [o, o]; return `<option value="${esc(ov)}" ${String(val) === String(ov) ? "selected" : ""}>${esc(ol)}</option>`; }).join("")}</select>`;
    else if (f.type === "textarea") input = `<textarea name="${f.k}" ${f.req ? "required" : ""} placeholder="${esc(f.ph || "")}">${esc(val)}</textarea>`;
    else if (f.type === "checkbox") return `<label class="f ${f.full ? "full" : ""}"><span><input type="checkbox" name="${f.k}" ${val ? "checked" : ""}> ${esc(f.label)}</span></label>`;
    else input = `<input name="${f.k}" type="${f.type === "datetime" ? "datetime-local" : f.type || "text"}" value="${esc(f.type === "datetime" && val ? String(val).slice(0, 16) : val)}" ${f.req ? "required" : ""} ${f.type === "number" ? 'step="any"' : ""} placeholder="${esc(f.ph || "")}" autocomplete="off">`;
    return `<label class="f ${f.full ? "full" : ""}"><span>${esc(f.label)}${f.req ? " *" : ""}</span>${input}${f.hint ? `<span class="hint">${esc(f.hint)}</span>` : ""}</label>`;
  }).join("") + `</div>`;
}
export function readFields(root, fields) {
  const out = {};
  fields.forEach(f => {
    const el = root.querySelector(`[name="${f.k}"]`); if (!el) return;
    if (f.type === "checkbox") out[f.k] = el.checked;
    else if (f.type === "number") out[f.k] = el.value === "" ? null : Number(el.value);
    else if (f.type === "datetime") out[f.k] = el.value ? new Date(el.value).toISOString().slice(0, 19) : null;
    else out[f.k] = el.value === "" && (f.type === "select" || f.nullable) ? null : el.value.trim ? el.value.trim() : el.value;
  });
  return out;
}
export function validate(root, fields) {
  for (const f of fields) { if (!f.req) continue; const el = root.querySelector(`[name="${f.k}"]`); if (el && !el.value.trim()) { el.focus(); throw new Error(`${f.label} is required.`); } }
}

export function table(cols, rows, { onRow, empty = "Nothing here yet.", sel = false } = {}) {
  if (!rows.length) return `<div class="empty"><h3>${esc(empty)}</h3></div>`;
  return `<div class="tablewrap"><table class="stack"><thead><tr>${sel ? '<th style="width:32px"><input type="checkbox" data-all aria-label="Select all"></th>' : ""}${cols.map(c => `<th>${esc(c.h)}</th>`).join("")}</tr></thead><tbody>` +
    rows.map((r, i) => `<tr class="${onRow ? "click" : ""}" data-i="${i}">${sel ? `<td><input type="checkbox" data-sel="${r.id}" aria-label="Select row"></td>` : ""}${cols.map(c => `<td data-l="${esc(c.h)}">${c.f(r)}</td>`).join("")}</tr>`).join("") + `</tbody></table></div>`;
}
export function wireTable(root, rows, onRow) {
  $$("tbody tr[data-i]", root).forEach(tr => tr.addEventListener("click", e => { if (e.target.closest("input,button,a,select")) return; onRow?.(rows[+tr.dataset.i]); }));
  const all = $("[data-all]", root); if (all) all.onchange = () => $$("[data-sel]", root).forEach(c => c.checked = all.checked);
}
export const selected = root => $$("[data-sel]:checked", root).map(c => +c.dataset.sel);
export const chip = (t, kind = "") => `<span class="chip ${kind}">${esc(t)}</span>`;
export const statusKind = s => ({ approved: "ok", paid: "ok", present: "ok", done: "ok", won: "ok", live: "ok", pending: "warn", late: "warn", half_day: "warn", proposal: "info", qualified: "info", in_progress: "info", rejected: "bad", lost: "bad", cancelled: "bad", urgent: "bad", high: "warn", offline: "bad", idle: "warn" }[s] || "");
export const skeleton = (n = 4) => Array.from({ length: n }, () => '<div class="skeleton"></div>').join("");
export function download(name, text, type = "text/csv") { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000); }

// ---- maps ----
const TILES = {
  streets: ["https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", "© OpenStreetMap"],
  light: ["https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", "© OpenStreetMap © CARTO"],
  dark: ["https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", "© OpenStreetMap © CARTO"],
  satellite: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", "Imagery © Esri"],
};
export function makeMap(el, style = "streets", center = [20.5, 78.9], zoom = 5) {
  if (!window.L) { el.innerHTML = '<div class="empty">Map could not load (offline?).</div>'; return null; }
  const map = L.map(el, { zoomControl: true }).setView(center, zoom);
  const [url, attr] = TILES[style] || TILES.streets; L.tileLayer(url, { attribution: attr, maxZoom: 19, subdomains: "abc" }).addTo(map);
  setTimeout(() => map.invalidateSize(), 150); return map;
}
export const pinIcon = (color = "#0f766e", label = "") => L.divIcon({ className: "", iconSize: [28, 34], iconAnchor: [14, 32], html: `<svg width="28" height="34" viewBox="0 0 28 34"><path d="M14 33S2 21 2 13a12 12 0 1124 0c0 8-12 20-12 20z" fill="${color}" stroke="#fff" stroke-width="2"/><text x="14" y="18" text-anchor="middle" font-size="11" font-weight="700" fill="#fff" font-family="sans-serif">${esc(label)}</text></svg>` });
export function here() {
  return new Promise((res, rej) => navigator.geolocation ? navigator.geolocation.getCurrentPosition(p => res({ lat: p.coords.latitude, lng: p.coords.longitude, accuracy: p.coords.accuracy }),
    e => rej(new Error(e.code === 1 ? "Location permission is blocked. Allow location for this site in your browser settings." : "Could not get your location. Turn on GPS and try again.")), { enableHighAccuracy: true, timeout: 15000, maximumAge: 10000 }) : rej(new Error("This device does not support location.")));
}
export async function captureSelfie() {
  if (!navigator.mediaDevices?.getUserMedia) throw new Error("Camera is not available on this device.");
  const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user", width: 640 } }).catch(() => { throw new Error("Camera permission is blocked. Allow camera access to use face scan."); });
  return new Promise((resolve, reject) => {
    const body = document.createElement("div"); body.innerHTML = `<video playsinline autoplay muted style="width:100%;border-radius:10px;background:#000"></video><p class="muted small">Look straight at the camera in good light.</p>`;
    const v = $("video", body); v.srcObject = stream; let done = false;
    const stop = () => stream.getTracks().forEach(t => t.stop());
    const m = modal({ title: "Face scan", body, actions: [{ label: "Cancel", close: true }, { label: "Capture", primary: true, onClick: () => { const c = document.createElement("canvas"); c.width = v.videoWidth || 640; c.height = v.videoHeight || 480; c.getContext("2d").drawImage(v, 0, 0); done = true; stop(); resolve(c.toDataURL("image/jpeg", .85)); } }] });
    new MutationObserver(() => { if (!document.body.contains(m.root)) { stop(); if (!done) reject(new Error("Face scan cancelled.")); } }).observe(document.body, { childList: true });
  });
}
export const fileToDataURL = f => new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(f); });
