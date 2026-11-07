// API client, session, and offline write-queue.
import { serverBase } from "./config.js";
const K = { token: "fw_token", user: "fw_user", ws: "fw_ws", q: "fw_queue" };
export const session = {
  get token() { try { return localStorage.getItem(K.token); } catch { return null; } },
  set(token, user, ws) { try { localStorage.setItem(K.token, token); localStorage.setItem(K.user, JSON.stringify(user)); ws && localStorage.setItem(K.ws, ws); } catch {} },
  clear() { try { localStorage.removeItem(K.token); localStorage.removeItem(K.user); } catch {} },
  get workspace() { try { return localStorage.getItem(K.ws) || ""; } catch { return ""; } },
};
let onAuthLost = () => {};
export const setAuthLostHandler = f => { onAuthLost = f; };

export class ApiError extends Error { constructor(msg, status) { super(msg); this.status = status; } }

export async function api(method, path, body, opts = {}) {
  const headers = {};
  if (session.token) headers.Authorization = "Bearer " + session.token;
  let payload;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  let res;
  try { res = await fetch(serverBase() + "/api" + path, { method, headers, body: payload }); }
  catch { throw new ApiError("You appear to be offline. Check your connection and try again.", 0); }
  if (opts.raw) return res;
  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("json") ? await res.json().catch(() => null) : await res.text();
  if (!res.ok) {
    if (res.status === 401 && session.token && !path.startsWith("/auth/login")) { session.clear(); onAuthLost(data?.detail); }
    const d = data?.detail;
    throw new ApiError(typeof d === "string" ? d : Array.isArray(d) ? d.map(x => x.msg).join("; ") : "Request failed (" + res.status + ").", res.status);
  }
  return data;
}
export const get = (p) => api("GET", p), post = (p, b) => api("POST", p, b ?? {}), patch = (p, b) => api("PATCH", p, b), put = (p, b) => api("PUT", p, b), del = (p) => api("DELETE", p);
export const qs = o => { const p = new URLSearchParams(); Object.entries(o).forEach(([k, v]) => v !== "" && v != null && p.set(k, v)); const s = p.toString(); return s ? "?" + s : ""; };
export const fileUrl = path => path ? `${serverBase()}/api/files/${path}?t=${encodeURIComponent(session.token || "")}` : "";

// ---------- offline queue (punches + GPS breadcrumbs) ----------
const load = () => { try { return JSON.parse(localStorage.getItem(K.q) || '{"punches":[],"pings":[]}'); } catch { return { punches: [], pings: [] }; } };
const save = q => { try { localStorage.setItem(K.q, JSON.stringify(q)); } catch {} };
export const queue = {
  addPing(p) { const q = load(); q.pings.push(p); if (q.pings.length > 5000) q.pings.splice(0, q.pings.length - 5000); save(q); },
  addPunch(kind, body) { const q = load(); q.punches.push({ kind, body, queued_at: new Date().toISOString() }); save(q); },
  size() { const q = load(); return q.punches.length + q.pings.length; },
  async flush() {
    if (!navigator.onLine || !session.token) return { punches: 0, pings: 0 };
    const q = load(); let sp = 0, sg = 0;
    while (q.punches.length) {
      const p = q.punches[0];
      try { await post(`/attendance/${p.kind}`, p.body); } catch (e) { if (e.status === 0) break; /* server rejected: drop so it can't block the queue */ }
      q.punches.shift(); sp++; save(q);
    }
    while (q.pings.length) {
      const batch = q.pings.slice(0, 500);
      try { await post("/tracking/pings", batch); } catch (e) { if (e.status === 0 || e.status >= 500) break; }
      q.pings.splice(0, batch.length); sg += batch.length; save(q);
    }
    return { punches: sp, pings: sg };
  },
};
