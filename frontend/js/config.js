// Where the API lives. In the browser/PWA it is the same origin. In the native app the web files are bundled
// inside the app, so the person enters their company's Fieldwise address once on the sign-in screen.
const K = "fw_server";
export const isBundled = () => location.protocol === "capacitor:" || location.protocol === "ionic:" || !!window.Capacitor?.isNativePlatform?.();
export const serverBase = () => { if (!isBundled()) return ""; try { return (localStorage.getItem(K) || "").replace(/\/$/, ""); } catch { return ""; } };
export const setServer = u => { let v = String(u || "").trim(); if (v && !/^https?:\/\//i.test(v)) v = "https://" + v; try { localStorage.setItem(K, v.replace(/\/$/, "")); } catch {} };
export const wsBase = () => { const b = serverBase(); const u = b ? new URL(b) : location; return (u.protocol === "https:" ? "wss://" : "ws://") + u.host; };
