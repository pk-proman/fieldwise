"""Turns a phone's health report into plain-language problems. Shared by the manager view and the 'GPS is off' alert, so the alert can say WHY."""

# Brands whose battery managers kill background apps unless the user allows Autostart.
AUTOSTART_BRANDS = {"xiaomi", "redmi", "poco", "oppo", "realme", "oneplus", "vivo", "iqoo", "huawei", "honor", "samsung"}
STOP_REASONS = {
    "app_stopped": "The app was stopped by the phone while on duty (battery manager)",
    "rebooted": "The phone restarted while on duty",
    "gps_off": "Phone location was switched off",
}


def problems(d: dict | None) -> list[dict]:
    d = d or {}
    out = []

    def add(code, text, fix):
        out.append({"code": code, "text": text, "fix": fix})
    if d.get("location_services") is False:
        add("gps_off", "Phone location is switched off", "Switch location on in the phone's quick settings")
    loc = d.get("location")
    if loc in ("denied", "while_in_use"):
        add("location", "Location is not set to 'Allow all the time'" if loc == "while_in_use" else "Location permission is denied", "Settings, Apps, Fieldwise, Location: Allow all the time")
    if d.get("notifications") is False:
        add("notifications", "Notifications are blocked", "Allow notifications so the 'tracking on' notice and alerts can show")
    if d.get("battery_unrestricted") is False:
        add("battery", "Battery optimisation is on for Fieldwise", "Settings, Apps, Fieldwise, Battery: Unrestricted")
    if (d.get("manufacturer") or "").lower() in AUTOSTART_BRANDS and not d.get("autostart_confirmed"):
        add("autostart", f"Autostart / background permission not confirmed ({d.get('manufacturer')})", "Open the setup guide in the app and follow the brand steps")
    if d.get("stop_reason") in STOP_REASONS:
        add("stopped", STOP_REASONS[d["stop_reason"]], "Reopen the app; fix the battery items above")
    return out


def likely_reason(d: dict | None) -> str:
    p = problems(d)
    return p[0]["text"] if p else ""
