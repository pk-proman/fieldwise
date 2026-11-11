// Phone activation: a field member enters workspace + mobile number + the PIN their administrator gave them, once.
// After that the phone stays signed in (no daily login) until the administrator revokes it.
import { post, session } from "./api.js";
import { $, esc, toast, fail } from "./ui.js";
import { plugin } from "./native.js";
import { isBundled, serverBase, setServer, DEFAULT_SERVER } from "./config.js";
import { LOGO_FULL } from "./brand.js";

const K = { secret: "fw_dev_secret", on: "fw_dev_on" };
const ls = { get: k => { try { return localStorage.getItem(k); } catch { return null; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch {} }, del: k => { try { localStorage.removeItem(k); } catch {} } };

export const isActivated = () => ls.get(K.on) === "1" && !!ls.get(K.secret);
export const forgetDevice = () => { ls.del(K.on); ls.del(K.secret); };
const newSecret = () => Array.from(crypto.getRandomValues(new Uint8Array(32)), b => b.toString(16).padStart(2, "0")).join("");

async function deviceInfo() {
  let model = "", label = navigator.userAgent.slice(0, 120);
  try { const i = await plugin("Device")?.getInfo?.(); if (i) { model = `${i.manufacturer || ""} ${i.model || ""}`.trim(); label = `${model} · Android ${i.osVersion || ""}`; } } catch {}
  return { model, label };
}

/** Called on every start: proves this is still the activated install and extends the token. Offline = keep going with the stored token. */
export async function renewDevice() {
  if (!isActivated() || !session.token) return true;
  try { const r = await post("/auth/device/renew", { device_secret: ls.get(K.secret) }); session.set(r.token, JSON.parse(localStorage.getItem("fw_user") || "null"), session.workspace); return true; }
  catch (e) { if (e.status === 0) return true; if (e.status === 401) { forgetDevice(); session.clear(); return false; } return true; }
}

export function activationScreen(msg, { onDone, onEmailSignIn }) {
  const app = document.getElementById("app");
  app.innerHTML = `<div class="auth"><form class="card" id="actf" novalidate><div class="authlogo">${LOGO_FULL}</div><h1>Activate this phone</h1>
    <p class="muted" style="margin:4px 0 16px">Enter the details your administrator sent you. You only do this once on each phone.</p>
    ${msg ? `<div class="chip bad" style="display:block;margin-bottom:12px;padding:8px 10px;border-radius:8px">${esc(msg)}</div>` : ""}
    <label class="f"><span>Company code</span><input name="workspace" required autocapitalize="none" autocomplete="off" value="${esc(session.workspace)}"></label>
    <label class="f"><span>Your mobile number</span><input name="phone" type="tel" inputmode="tel" required autocomplete="tel"></label>
    <label class="f"><span>PIN</span><input name="pin" inputmode="numeric" maxlength="12" required autocomplete="one-time-code"></label>
    <button class="btn primary big" style="width:100%" id="go">Activate</button>
    <details style="margin-top:16px"><summary class="small muted" style="cursor:pointer">Server: ${esc(serverBase().replace(/^https?:\/\//, ""))} &middot; change</summary><label class="f"><span>Server address</span><input name="server" value="${serverBase() === DEFAULT_SERVER ? "" : esc(serverBase())}" placeholder="${DEFAULT_SERVER}"></label></details>
    <p class="small muted" style="margin-top:14px;text-align:center">Manager or administrator? <a href="#" id="em">Sign in with email</a></p></form></div>`;
  $("#em").onclick = e => { e.preventDefault(); onEmailSignIn(); };
  $("#actf").onsubmit = async e => {
    e.preventDefault(); const f = Object.fromEntries(new FormData(e.target)); const b = $("#go"); b.disabled = true;
    if (isBundled() && f.server) setServer(f.server);
    try {
      const secret = newSecret(), info = await deviceInfo();
      const r = await post("/auth/activate", { workspace: f.workspace.trim().toLowerCase(), phone: f.phone.trim(), pin: f.pin.trim(), device_secret: secret, label: info.label, model: info.model, app_version: "" });
      ls.set(K.secret, secret); ls.set(K.on, "1"); session.set(r.token, r.user, r.workspace);
      toast("Phone activated."); onDone();
    } catch (err) { fail(err); b.disabled = false; }
  };
}
