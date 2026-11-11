// Sideloaded Android apps get no automatic updates from Google Play, so the app asks the server whether a newer build exists.
// Newer: a dismissible prompt (once a day per version). Older than the server's minimum: a blocking prompt.
import { serverBase } from "./config.js";
import { modal, esc } from "./ui.js";
import { plugin } from "./native.js";

const cap = () => window.Capacitor;
let busy = false, shown = null;

async function installedBuild() {
  const app = plugin("App");
  if (!app?.getInfo) return null;
  try { const i = await app.getInfo(); return { code: parseInt(i.build, 10) || 0, name: i.version }; } catch { return null; }
}

export async function checkForUpdate() {
  if (busy || cap()?.getPlatform?.() !== "android") return;   // iPhone updates come from the App Store / TestFlight
  busy = true;
  try {
    const me = await installedBuild(); if (!me) return;
    const r = await fetch(serverBase() + "/api/app/version", { cache: "no-store" }); if (!r.ok) return;
    const v = await r.json(); if (!v.available || v.versionCode <= me.code) return;
    const forced = me.code < (v.minVersionCode || 0);
    if (shown === v.versionCode) return;
    if (!forced) { try { if (localStorage.getItem("fw-upd-" + v.versionCode) === new Date().toDateString()) return; } catch {} }
    shown = v.versionCode;
    const notes = String(v.releaseNotes || "").split("\n").filter(x => x.trim()).map(x => `<li>${esc(x)}</li>`).join("");
    const body = `<p>${forced ? "This version of Fieldwise is no longer supported. Please update to continue." : "A new version of Fieldwise is available."}</p>
      <p class="small muted">You have ${esc(me.name)} &middot; new version ${esc(v.versionName)} (${(v.size / 1048576).toFixed(1)} MB)</p>${notes ? `<ul>${notes}</ul>` : ""}
      <p class="small muted">Android will ask you to confirm the install. Your sign-in and data are kept.</p>`;
    const m = modal({ title: forced ? "Update required" : "Update available", body,
      actions: [...(forced ? [] : [{ label: "Later", close: true }]), { label: "Download update", primary: true, onClick: () => { window.open(v.apkUrl, "_system"); return !!forced === false; } }] });
    if (forced) {   // cannot be dismissed: no close button, taps outside ignored, and if Esc still removes it, it comes straight back
      m.root.querySelector("[data-x]")?.remove(); m.root.addEventListener("mousedown", e => e.stopPropagation(), true);
      const ob = new MutationObserver(() => { if (!document.body.contains(m.root)) { ob.disconnect(); shown = null; setTimeout(checkForUpdate, 0); } }); ob.observe(document.body, { childList: true });
    }
    else try { localStorage.setItem("fw-upd-" + v.versionCode, new Date().toDateString()); } catch {}
  } catch { /* offline: try again next time the app is opened */ } finally { busy = false; }
}
