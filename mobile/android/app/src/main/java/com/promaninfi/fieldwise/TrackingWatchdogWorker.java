package com.promaninfi.fieldwise;

import android.content.Context;
import android.location.LocationManager;
import androidx.annotation.NonNull;
import androidx.work.ExistingPeriodicWorkPolicy;
import androidx.work.PeriodicWorkRequest;
import androidx.work.WorkManager;
import androidx.work.Worker;
import androidx.work.WorkerParameters;
import java.util.concurrent.TimeUnit;

/** Every ~15 minutes while on duty: if no location has been handled recently, tell the person tracking stopped and how to resume. */
public class TrackingWatchdogWorker extends Worker {
    static final String WORK = "fw_tracking_watchdog";

    public TrackingWatchdogWorker(@NonNull Context c, @NonNull WorkerParameters p) { super(c, p); }

    @NonNull @Override public Result doWork() {
        Context c = getApplicationContext();
        if (!HealthPrefs.onDuty(c)) { stop(c); return Result.success(); }
        long now = System.currentTimeMillis();
        if (now - HealthPrefs.dutySince(c) > HealthPrefs.MAX_DUTY_MS) { HealthPrefs.setDuty(c, false); stop(c); return Result.success(); }
        long quiet = now - Math.max(HealthPrefs.lastPing(c), HealthPrefs.dutySince(c));
        long lastAlert = HealthPrefs.of(c).getLong("last_alert", 0);
        if (quiet > HealthPrefs.STALE_MS && now - lastAlert > HealthPrefs.REALERT_MS) {
            LocationManager lm = (LocationManager) c.getSystemService(Context.LOCATION_SERVICE);
            boolean off = lm != null && !lm.isLocationEnabled();
            TrackingAlerts.show(c, off ? "Location is switched off" : "Your route has stopped recording",
                off ? "Switch location back on, then open Fieldwise so your day is tracked." : "Open Fieldwise to resume tracking. If this keeps happening, set battery to Unrestricted for Fieldwise.");
            HealthPrefs.of(c).edit().putLong("last_alert", now).apply();
        }
        return Result.success();
    }

    static void start(Context c) {
        WorkManager.getInstance(c).enqueueUniquePeriodicWork(WORK, ExistingPeriodicWorkPolicy.KEEP,
            new PeriodicWorkRequest.Builder(TrackingWatchdogWorker.class, 15, TimeUnit.MINUTES).build());
    }
    static void stop(Context c) { WorkManager.getInstance(c).cancelUniqueWork(WORK); TrackingAlerts.clear(c); }
}
