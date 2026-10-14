package com.yanyuliu.pockethub;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;

/** Keep desktop approval monitoring alive when no BLE device is connected. */
public final class RuntimeWatchService extends Service {
    static void start(Context context) {
        if (RuntimeClient.selected(context) != null) context.startForegroundService(new Intent(context, RuntimeWatchService.class));
    }
    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (RuntimeClient.selected(this) == null) { stopSelf(); return START_NOT_STICKY; }
        NotificationManager manager = getSystemService(NotificationManager.class);
        manager.createNotificationChannel(new NotificationChannel("runtime", "电脑连接与审批同步", NotificationManager.IMPORTANCE_LOW));
        PendingIntent open = PendingIntent.getActivity(this, 2, new Intent(this, MainActivity.class), PendingIntent.FLAG_IMMUTABLE);
        Notification notification = new Notification.Builder(this, "runtime").setSmallIcon(android.R.drawable.stat_notify_sync)
                .setContentTitle("ds 中枢").setContentText("正在同步电脑任务与审批").setContentIntent(open).setOngoing(true).build();
        if (Build.VERSION.SDK_INT >= 34) startForeground(2, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
        else startForeground(2, notification);
        RuntimeClient.watch(this);
        return START_STICKY;
    }
    @Override public IBinder onBind(Intent intent) { return null; }
}
