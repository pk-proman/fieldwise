package com.promaninfi.fieldwise;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** After a restart or an app update the location service is gone and Android 12+ will not let us restart it from the background: ask the person to open the app. */
public class BootReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context c, Intent i) {
        if (!HealthPrefs.onDuty(c)) return;
        boolean update = Intent.ACTION_MY_PACKAGE_REPLACED.equals(i.getAction());
        TrackingAlerts.show(c, update ? "Fieldwise was updated" : "Your phone restarted", "Open Fieldwise to resume tracking your route.");
        TrackingWatchdogWorker.start(c);
    }
}
