// Background-ish GPS tracking while checked in (browser/PWA). Sends a breadcrumb every ~45 s,
// stores it offline when the network is down, and flushes on reconnect. Native (Capacitor) builds
// would use a background-geolocation plugin and a native mock-location flag instead of the heuristic below.
import { queue, post } from "./api.js";
import { haversine } from "./geo.js";
import { isNative, startNativeTracking, stopNativeTracking, restartNativeTracking, lastNativeFixAt } from "./native.js";

let watchId = null, timer = null, last = null, lastSent = 0, gpsOk = true, battery = null, lastCur = null;
export const state = { active: false, lastFix: null, listeners: new Set() };
const emit = () => state.listeners.forEach(f => f(state));

navigator.getBattery?.().then(b => { battery = b; });

function record(p, gps_on = true) {
  const now = Date.now();
  let mock = false;
  if (p && last) { const km = haversine(last.lat, last.lng, p.lat, p.lng) / 1000, h = (now - last.t) / 36e5; if (h > 0 && km / h > 250 && km > 0.5) mock = true; } // teleport => suspicious
  if (p) last = { lat: p.lat, lng: p.lng, t: now };
  const ping = { ts: new Date(now).toISOString(), lat: p?.lat ?? null, lng: p?.lng ?? null, accuracy: p?.accuracy ?? null, battery: battery ? Math.round(battery.level * 100) : null,
    network: navigator.onLine ? "online" : "offline", gps_on, mock };
  if (navigator.onLine) post("/tracking/pings", [ping]).catch(() => queue.addPing(ping)); else queue.addPing(ping);
  lastSent = now; state.lastFix = ping; emit();
}

export function start() {
  if (state.active || !navigator.geolocation) return;
  state.active = true;
  if (isNative()) startNativeTracking(p => { state.lastFix = p; emit(); }).then(took => { if (!took) startBrowserTracking(); }); else startBrowserTracking();
  emit();
}
function startBrowserTracking() {
  let cur = null;
  watchId = navigator.geolocation.watchPosition(p => { gpsOk = true; cur = { lat: p.coords.latitude, lng: p.coords.longitude, accuracy: p.coords.accuracy };
      // adaptive sampling: every ~25 s while moving, every ~2 min when standing still (saves battery and data); slower still on low battery
      const moving = (p.coords.speed ?? 0) > 1.2 || (lastCur && haversine(lastCur.lat, lastCur.lng, cur.lat, cur.lng) > 40); const low = battery && battery.level < 0.15 && !battery.charging;
      if (Date.now() - lastSent > (moving ? (low ? 60000 : 25000) : (low ? 300000 : 110000))) { record(cur); lastCur = cur; } },
    () => { gpsOk = false; }, { enableHighAccuracy: true, maximumAge: 15000 });
  timer = setInterval(() => { if (!gpsOk) record(null, false); else if (cur && Date.now() - lastSent > 150000) { record(cur); lastCur = cur; } }, 30000);
  emit();
}
/** Call when the app returns to the foreground: if the phone's tracking service silently died, start it again. */
let restarting = false;
export function ensure() {
  if (!state.active || !isNative() || restarting || Date.now() - lastNativeFixAt() < 6 * 60000) return;
  restarting = true; restartNativeTracking().catch(() => {}).finally(() => { restarting = false; });
}
export function stop() { stopNativeTracking(); if (watchId != null) navigator.geolocation.clearWatch(watchId); clearInterval(timer); watchId = timer = null; state.active = false; last = null; emit(); }

window.addEventListener("online", () => queue.flush());
setInterval(() => queue.flush(), 30000);
