// Bridge to the native (Capacitor) app: background GPS that keeps running with the screen off, the real
// mock-location flag, and fingerprint / face unlock. In a normal browser every function is a harmless no-op.
// See /mobile/README.md for how to build the Android/iOS app.
import { post, queue } from "./api.js";

const cap = () => window.Capacitor;
export const isNative = () => !!cap()?.isNativePlatform?.();
const plugin = n => cap()?.Plugins?.[n];

let watcherId = null;

/** Starts OS-level background tracking. Returns true if the native plugin took over. */
export async function startNativeTracking(onFix) {
  const bg = plugin("BackgroundGeolocation");
  if (!isNative() || !bg) return false;
  if (watcherId != null) return true;
  watcherId = await bg.addWatcher({
    backgroundMessage: "Fieldwise is recording your route while you're checked in.", backgroundTitle: "Tracking on duty", requestPermissions: true, stale: false, distanceFilter: 25,
  }, (loc, err) => {
    if (err) { if (err.code === "NOT_AUTHORIZED") bg.openSettings?.(); return; }
    const ping = { ts: new Date(loc.time || Date.now()).toISOString(), lat: loc.latitude, lng: loc.longitude, accuracy: loc.accuracy, battery: null, network: navigator.onLine ? "online" : "offline", gps_on: true, mock: !!loc.simulated };
    plugin("Device")?.getBatteryInfo?.().then(b => { ping.battery = Math.round((b.batteryLevel ?? 0) * 100); }).catch(() => {}).finally(() => {
      (navigator.onLine ? post("/tracking/pings", [ping]).catch(() => queue.addPing(ping)) : Promise.resolve(queue.addPing(ping))); onFix?.(ping); });
  });
  return true;
}
export async function stopNativeTracking() { const bg = plugin("BackgroundGeolocation"); if (bg && watcherId != null) { await bg.removeWatcher({ id: watcherId }); watcherId = null; } }

/** Fingerprint / face unlock via the phone's own biometric prompt. Resolves true when the person is verified. */
export async function verifyBiometric(reason = "Confirm it's you to mark attendance") {
  const nb = plugin("NativeBiometric");
  if (!isNative() || !nb) return false;
  try { const a = await nb.isAvailable(); if (!a.isAvailable) return false; await nb.verifyIdentity({ reason, title: "Fieldwise", useFallback: true, maxAttempts: 3 }); return true; } catch { return false; }
}
export const biometricAvailable = async () => { const nb = plugin("NativeBiometric"); if (!isNative() || !nb) return false; try { return (await nb.isAvailable()).isAvailable; } catch { return false; } };
