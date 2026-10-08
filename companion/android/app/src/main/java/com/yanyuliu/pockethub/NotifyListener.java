package com.yanyuliu.pockethub;

import android.app.Notification;
import android.app.PendingIntent;
import android.os.Bundle;
import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;

import java.util.Locale;

/**
 * 读取系统通知，把登记过的来源（Claude、Codex……）的通知交给中枢。
 * 需要用户在系统设置里给本应用开启“通知使用权”。只读取登记来源的通知，其他一律忽略。
 */
public class NotifyListener extends NotificationListenerService {
    private static final String[] ALLOW_WORDS = {
            "allow", "approve", "accept", "yes", "confirm", "允许", "批准", "同意", "确认", "可以",
    };
    private static final String[] DENY_WORDS = {
            "deny", "reject", "decline", "no", "not", "don", "never", "cancel", "拒绝", "不允许", "取消", "不行",
    };

    @Override
    public void onListenerConnected() {
        HubStore.get().log("通知读取已就绪");
        StatusBarNotification[] current;
        try {
            current = getActiveNotifications();
        } catch (RuntimeException error) {
            return;
        }
        if (current != null) {
            for (StatusBarNotification notification : current) {
                onNotificationPosted(notification);
            }
        }
    }

    @Override
    public void onNotificationPosted(StatusBarNotification sbn) {
        if (sbn == null || sbn.isOngoing()) {
            return;
        }
        Sources.Source source = Sources.match(this, sbn.getPackageName());
        Notification notification = sbn.getNotification();
        if (source == null || notification == null
                || (notification.flags & Notification.FLAG_GROUP_SUMMARY) != 0) {
            return;
        }
        Bundle extras = notification.extras;
        String title = text(extras, Notification.EXTRA_TITLE);
        String big = text(extras, Notification.EXTRA_BIG_TEXT);
        String body = big.isEmpty() ? text(extras, Notification.EXTRA_TEXT) : big;
        if (title.isEmpty() && body.isEmpty()) {
            return;
        }
        PendingIntent allow = null;
        PendingIntent deny = null;
        if (notification.actions != null) {
            for (Notification.Action action : notification.actions) {
                // 需要输入文字的按钮（比如“回复”）在设备上答不了，跳过。
                if (action == null || action.title == null || action.actionIntent == null
                        || (action.getRemoteInputs() != null
                        && action.getRemoteInputs().length > 0)) {
                    continue;
                }
                String label = action.title.toString().toLowerCase(Locale.ROOT).trim();
                if (deny == null && matches(label, DENY_WORDS)) {
                    deny = action.actionIntent;
                } else if (allow == null && matches(label, ALLOW_WORDS)) {
                    allow = action.actionIntent;
                }
            }
        }
        HubStore.get().post(sbn.getKey(), source.label, title, body, allow, deny);
    }

    @Override
    public void onNotificationRemoved(StatusBarNotification sbn) {
        if (sbn != null) {
            HubStore.get().dismiss(sbn.getKey());
        }
    }

    private static String text(Bundle extras, String key) {
        if (extras == null) {
            return "";
        }
        CharSequence value = extras.getCharSequence(key);
        return value == null ? "" : value.toString();
    }

    private static boolean matches(String label, String[] words) {
        for (String word : words) {
            // 英文按整词比较，避免 "no" 命中 "notify" 这类词；"don't allow" 会拆出 "don"，
            // 而拒绝词先于允许词判断，所以不会被当成允许。
            if (word.charAt(0) < 0x80) {
                for (String part : label.split("[^a-z]+")) {
                    if (part.equals(word)) {
                        return true;
                    }
                }
            } else if (label.contains(word)) {
                return true;
            }
        }
        return false;
    }
}
