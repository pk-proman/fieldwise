package com.promaninfi.fieldwise;

import android.Manifest;
import android.app.Activity;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.location.LocationManager;
import android.net.Uri;
import android.os.Build;
import android.os.PowerManager;
import android.provider.Settings;
import androidx.core.app.ActivityCompat;
import androidx.core.app.NotificationManagerCompat;
import androidx.core.content.ContextCompat;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

/** Everything the setup wizard and the watchdog need from Android: permission and battery state, deep links to the right settings page, and the duty flag. */
@CapacitorPlugin(name = "FieldwiseHealth")
public class FieldwiseHealthPlugin extends Plugin {

    // Each phone brand hides its "let this app start itself / run in background" switch somewhere different. Try them in turn; fall back to the app's own settings page.
    private static final String[][] AUTOSTART = {
        {"com.miui.securitycenter", "com.miui.permcenter.autostart.AutoStartManagementActivity"},
        {"com.coloros.safecenter", "com.coloros.safecenter.permission.startup.StartupAppListActivity"},
        {"com.coloros.safecenter", "com.coloros.safecenter.startupapp.StartupAppListActivity"},
        {"com.oppo.safe", "com.oppo.safe.permission.startup.StartupAppListActivity"},
        {"com.coloros.oppoguardelf", "com.coloros.powermanager.fuelgaue.PowerUsageModelActivity"},
        {"com.vivo.permissionmanager", "com.vivo.permissionmanager.activity.BgStartUpManagerActivity"},
        {"com.iqoo.secure", "com.iqoo.secure.ui.phoneoptimize.AddWhiteListActivity"},
        {"com.iqoo.secure", "com.iqoo.secure.ui.phoneoptimize.BgStartUpManager"},
        {"com.huawei.systemmanager", "com.huawei.systemmanager.startupmgr.ui.StartupNormalAppListActivity"},
        {"com.huawei.systemmanager", "com.huawei.systemmanager.optimize.process.ProtectActivity"},
        {"com.samsung.android.lool", "com.samsung.android.sm.battery.ui.BatteryActivity"},
        {"com.oneplus.security", "com.oneplus.security.chainlaunch.view.ChainLaunchAppListActivity"},
        {"com.asus.mobilemanager", "com.asus.mobilemanager.autostart.AutoStartActivity"},
    };

    private Context ctx() { return getContext(); }

    @PluginMethod
    public void status(PluginCall call) {
        Context c = ctx();
        boolean fine = granted(Manifest.permission.ACCESS_FINE_LOCATION) || granted(Manifest.permission.ACCESS_COARSE_LOCATION);
        boolean bg = Build.VERSION.SDK_INT < 29 ? fine : granted(Manifest.permission.ACCESS_BACKGROUND_LOCATION);
        boolean notif = Build.VERSION.SDK_INT >= 33 ? granted(Manifest.permission.POST_NOTIFICATIONS) : NotificationManagerCompat.from(c).areNotificationsEnabled();
        notif = notif && NotificationManagerCompat.from(c).areNotificationsEnabled();
        PowerManager pm = (PowerManager) c.getSystemService(Context.POWER_SERVICE);
        LocationManager lm = (LocationManager) c.getSystemService(Context.LOCATION_SERVICE);
        long last = HealthPrefs.lastPing(c);
        JSObject o = new JSObject();
        o.put("location", !fine ? "denied" : (bg ? "always" : "while_in_use"));
        o.put("locationServices", lm != null && lm.isLocationEnabled());
        o.put("notifications", notif);
        o.put("batteryUnrestricted", pm != null && pm.isIgnoringBatteryOptimizations(c.getPackageName()));
        o.put("manufacturer", Build.MANUFACTURER == null ? "" : Build.MANUFACTURER.toLowerCase());
        o.put("model", Build.MODEL == null ? "" : Build.MODEL);
        o.put("android", Build.VERSION.SDK_INT);
        o.put("onDuty", HealthPrefs.onDuty(c));
        o.put("lastPingAgeMin", last > 0 ? (System.currentTimeMillis() - last) / 60000.0 : JSObject.NULL);
        call.resolve(o);
    }

    @PluginMethod
    public void setDuty(PluginCall call) {
        boolean on = Boolean.TRUE.equals(call.getBoolean("on", false));
        boolean was = HealthPrefs.onDuty(ctx());
        if (on && was) { call.resolve(); return; }          // already on duty (e.g. app reopened): keep the original start time
        HealthPrefs.setDuty(ctx(), on);
        if (on) TrackingWatchdogWorker.start(ctx()); else TrackingWatchdogWorker.stop(ctx());
        call.resolve();
    }

    @PluginMethod
    public void markPing(PluginCall call) { HealthPrefs.markPing(ctx()); TrackingAlerts.clear(ctx()); call.resolve(); }

    /** First call asks for location; once that is granted, the next call sends the person to "Allow all the time" (Android 11+ shows the settings page, not a dialog). */
    @PluginMethod
    public void requestLocation(PluginCall call) {
        Activity a = getActivity();
        if (a == null) { call.reject("No activity"); return; }
        boolean fine = granted(Manifest.permission.ACCESS_FINE_LOCATION);
        if (!fine) ActivityCompat.requestPermissions(a, new String[]{Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION}, 9101);
        else if (Build.VERSION.SDK_INT >= 29 && !granted(Manifest.permission.ACCESS_BACKGROUND_LOCATION))
            ActivityCompat.requestPermissions(a, new String[]{Manifest.permission.ACCESS_BACKGROUND_LOCATION}, 9102);
        call.resolve();
    }

    @PluginMethod
    public void requestNotifications(PluginCall call) {
        Activity a = getActivity();
        if (a != null && Build.VERSION.SDK_INT >= 33 && !granted(Manifest.permission.POST_NOTIFICATIONS))
            ActivityCompat.requestPermissions(a, new String[]{Manifest.permission.POST_NOTIFICATIONS}, 9103);
        call.resolve();
    }

    /** target: app | battery | location | notifications | autostart. Always ends on some settings page, never fails silently. */
    @PluginMethod
    public void openSettings(PluginCall call) {
        String t = call.getString("target", "app");
        Context c = ctx();
        String opened = t;
        boolean ok = false;
        switch (t) {
            case "battery": ok = go(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)); break;
            case "location": ok = go(new Intent(Settings.ACTION_LOCATION_SOURCE_SETTINGS)); break;
            case "notifications": ok = go(new Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, c.getPackageName())); break;
            case "autostart":
                for (String[] a : AUTOSTART) { if (go(new Intent().setComponent(new ComponentName(a[0], a[1])))) { ok = true; break; } }
                break;
            default: break;
        }
        if (!ok) { go(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:" + c.getPackageName()))); opened = "app"; }
        JSObject o = new JSObject(); o.put("opened", opened); call.resolve(o);
    }

    private boolean go(Intent i) {
        try { i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK); ctx().startActivity(i); return true; } catch (Exception e) { return false; }
    }

    private boolean granted(String p) { return ContextCompat.checkSelfPermission(ctx(), p) == PackageManager.PERMISSION_GRANTED; }
}
