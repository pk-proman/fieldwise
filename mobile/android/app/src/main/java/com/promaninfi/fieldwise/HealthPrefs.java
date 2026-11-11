package com.promaninfi.fieldwise;

import android.content.Context;
import android.content.SharedPreferences;

/** What the native side remembers about the duty session, so the watchdog and boot receiver work even when the web view is dead. */
final class HealthPrefs {
    private static final String NAME = "fw_health";
    static final long STALE_MS = 12 * 60 * 1000L;        // no location handled for 12 minutes while on duty = tracking has stopped
    static final long REALERT_MS = 30 * 60 * 1000L;      // do not nag more often than this
    static final long MAX_DUTY_MS = 16 * 60 * 60 * 1000L; // forgot to check out: stop nagging after 16 h

    static SharedPreferences of(Context c) { return c.getSharedPreferences(NAME, Context.MODE_PRIVATE); }
    static boolean onDuty(Context c) { return of(c).getBoolean("on_duty", false); }
    static long lastPing(Context c) { return of(c).getLong("last_ping", 0); }
    static long dutySince(Context c) { return of(c).getLong("duty_since", 0); }
    static void setDuty(Context c, boolean on) {
        long now = System.currentTimeMillis();
        SharedPreferences.Editor e = of(c).edit().putBoolean("on_duty", on);
        if (on) e.putLong("duty_since", now).putLong("last_ping", now).putLong("last_alert", 0);
        e.apply();
    }
    static void markPing(Context c) { of(c).edit().putLong("last_ping", System.currentTimeMillis()).apply(); }
}
