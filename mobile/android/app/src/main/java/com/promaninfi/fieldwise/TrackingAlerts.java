package com.promaninfi.fieldwise;

import android.Manifest;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;
import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;
import androidx.core.content.ContextCompat;

/** The "tracking stopped, tap to resume" notification. A background app cannot restart location tracking itself on Android 12+, so the person taps. */
final class TrackingAlerts {
    private static final String CHANNEL = "fw_tracking_alerts";
    private static final int ID = 7101;

    static void show(Context c, String title, String text) {
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(c, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return;
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel ch = new NotificationChannel(CHANNEL, "Tracking alerts", NotificationManager.IMPORTANCE_HIGH);
            ch.setDescription("Tells you when your route stopped recording");
            c.getSystemService(NotificationManager.class).createNotificationChannel(ch);
        }
        Intent open = c.getPackageManager().getLaunchIntentForPackage(c.getPackageName());
        if (open == null) return;
        open.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent pi = PendingIntent.getActivity(c, 0, open, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        NotificationCompat.Builder b = new NotificationCompat.Builder(c, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_menu_mylocation).setContentTitle(title).setContentText(text)
            .setStyle(new NotificationCompat.BigTextStyle().bigText(text)).setContentIntent(pi).setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_HIGH).setCategory(NotificationCompat.CATEGORY_STATUS);
        try { NotificationManagerCompat.from(c).notify(ID, b.build()); } catch (SecurityException ignored) { }
    }

    static void clear(Context c) { NotificationManagerCompat.from(c).cancel(ID); }
}
