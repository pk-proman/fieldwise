// Bridge to the native (Capacitor) app: background GPS that keeps running with the screen off, the real
// mock-location flag, and fingerprint / face unlock. In a normal browser every function is a harmless no-op.
// See /mobile/README.md for how to build the Android/iOS app.
import { post, queue } from "./api.js";

const cap = () => window.Capacitor;
export const isNative = () => !!cap()?.isNativePlatform?.();
// Capacitor only fills Capacitor.Plugins when registerPlugin(name) is called, and this app has no bundler to do that for us,
// so register on first use (only when the native side really has the plugin).
export const plugin = n => { const c = cap(); if (!c?.isNativePlatform?.() || !c.PluginHeaders?.some(h => h.name === n)) return undefined; return c.Plugins?.[n] || c.registerPlugin(n); };

let watcherId = null, hb = null, lastLoc = null, lastFixAt = 0, realFixAt = 0, onFixCb = null;

const sendPing = ping => {
  plugin("FieldwiseHealth")?.markPing?.().catch?.(() => {});    // native watchdog: "the pipeline is alive"
  plugin("Device")?.getBatteryInfo?.().then(b => { ping.battery = Math.round((b.batteryLevel ?? 0) * 100); }).catch(() => {}).finally(() => {
    (navigator.onLine ? post("/tracking/pings", [ping]).catch(() => queue.addPing(ping)) : Promise.resolve(queue.addPing(ping))); onFixCb?.(ping); });
};

// The OS only reports a fix when the phone has moved ~25 m, so someone sitting in a meeting would look "silent" for hours.
// Every ~2 minutes without a fix, repeat the last position (or report GPS off) so the server and the watchdog can tell "still" from "dead".
async function heartbeat() {
  if (Date.now() - lastFixAt < 110000) return;
  let s = null; try { s = await plugin("FieldwiseHealth")?.status(); } catch {}
  const gpsOn = !s || (s.locationServices && s.location !== "denied");
  if (gpsOn && !lastLoc) return;
  sendPing({ ts: new Date().toISOString(), lat: gpsOn ? lastLoc.latitude : null, lng: gpsOn ? lastLoc.longitude : null, accuracy: gpsOn ? lastLoc.accuracy : null, battery: null,
    network: navigator.onLine ? "online" : "offline", gps_on: gpsOn, mock: false });
  lastFixAt = Date.now() - 20000;   // do not repeat on every tick
}

/** Starts OS-level background tracking. Returns true if the native plugin took over. */
export async function startNativeTracking(onFix) {
  const bg = plugin("BackgroundGeolocation");
  if (!isNative() || !bg) return false;
  if (watcherId != null) return true;
  onFixCb = onFix; lastFixAt = realFixAt = Date.now();
  plugin("FieldwiseHealth")?.setDuty?.({ on: true }).catch?.(() => {});
  watcherId = await bg.addWatcher({
    backgroundMessage: "Fieldwise is recording your route while you're checked in.", backgroundTitle: "Tracking on duty", requestPermissions: !!(() => { try { return localStorage.getItem("fw-trk-disclosure"); } catch { return null; } })(), stale: false, distanceFilter: 25,
  }, (loc, err) => {
    if (err) { if (err.code === "NOT_AUTHORIZED") bg.openSettings?.(); return; }
    lastLoc = loc; lastFixAt = realFixAt = Date.now();
    sendPing({ ts: new Date(loc.time || Date.now()).toISOString(), lat: loc.latitude, lng: loc.longitude, accuracy: loc.accuracy, battery: null, network: navigator.onLine ? "online" : "offline", gps_on: true, mock: !!loc.simulated });
  });
  clearInterval(hb); hb = setInterval(heartbeat, 60000);
  return true;
}
export async function stopNativeTracking() {
  clearInterval(hb); hb = null; lastLoc = null;
  plugin("FieldwiseHealth")?.setDuty?.({ on: false }).catch?.(() => {});
  const bg = plugin("BackgroundGeolocation"); if (bg && watcherId != null) { try { await bg.removeWatcher({ id: watcherId }); } catch {} watcherId = null; }
}
/** Self-restart: drop the watcher and start a fresh one (used when no fix has arrived for a while after the app returns to the foreground). */
export async function restartNativeTracking() {
  const cb = onFixCb, bg = plugin("BackgroundGeolocation"); if (!bg) return false;
  if (watcherId != null) { try { await bg.removeWatcher({ id: watcherId }); } catch {} watcherId = null; }
  return startNativeTracking(cb);
}
export const lastNativeFixAt = () => realFixAt;   // real GPS fixes only (heartbeats do not count)

/** Fingerprint / face unlock via the phone's own biometric prompt. Resolves true when the person is verified. */
export async function verifyBiometric(reason = "Confirm it's you to mark attendance") {
  const nb = plugin("NativeBiometric");
  if (!isNative() || !nb) return false;
  try { const a = await nb.isAvailable(); if (!a.isAvailable) return false; await nb.verifyIdentity({ reason, title: "Fieldwise", useFallback: true, maxAttempts: 3 }); return true; } catch { return false; }
}
export const biometricAvailable = async () => { const nb = plugin("NativeBiometric"); if (!isNative() || !nb) return false; try { return (await nb.isAvailable()).isAvailable; } catch { return false; } };
