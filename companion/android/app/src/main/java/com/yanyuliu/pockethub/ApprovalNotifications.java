package com.yanyuliu.pockethub;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;

/** Notify each approval, including desktop requests that must be answered on the computer. */
final class ApprovalNotifications implements HubStore.Listener {
    private static ApprovalNotifications instance;
    private final Context context;
    private final Map<String, Integer> shown = new HashMap<>();
    private int nextId = 1000;
    private static final String CHANNEL = "approvals";

    static synchronized void start(Context context) {
        if (instance != null) return;
        instance = new ApprovalNotifications(context.getApplicationContext());
        HubStore.get().addListener(instance);
    }
    private ApprovalNotifications(Context context) {
        this.context = context;
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        if (manager != null) manager.createNotificationChannel(new NotificationChannel(CHANNEL,
                "审批与电脑端确认", NotificationManager.IMPORTANCE_HIGH));
    }
    @Override public synchronized void onHubChanged() {
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        if (manager == null) return;
        Set<String> pending = new HashSet<>();
        for (Card.Approval approval : HubStore.get().approvals()) {
            String key = approval.id;
            pending.add(key);
            if (shown.containsKey(key)) continue;
            int id = nextId++;
            boolean actionable = !HubStore.get().monitoringApproval(approval.id);
            Intent intent = new Intent(context, MainActivity.class).putExtra("approval", approval.id)
                    .setData(android.net.Uri.parse("ds://approval/" + approval.id))
                    .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
            PendingIntent open = PendingIntent.getActivity(context, id, intent, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
            String title = (actionable ? "等你确认 · " : "请在电脑端确认 · ") + approval.runtimeName + " · " + approval.agent;
            Notification notification = new Notification.Builder(context, CHANNEL)
                    .setSmallIcon(android.R.drawable.ic_dialog_alert).setContentTitle(title)
                    .setContentText(approval.tool + " · " + approval.detail)
                    .setStyle(new Notification.BigTextStyle().bigText(approval.tool + "\n" + approval.detail))
                    .setContentIntent(open).setAutoCancel(true).setCategory(Notification.CATEGORY_EVENT).build();
            try { manager.notify(id, notification); shown.put(key, id); }
            catch (SecurityException denied) { /* The settings UI explains the OS permission. */ }
        }
        for (String key : new HashSet<>(shown.keySet())) if (!pending.contains(key)) manager.cancel(shown.remove(key));
    }
}
