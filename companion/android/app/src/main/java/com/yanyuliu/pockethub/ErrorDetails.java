package com.yanyuliu.pockethub;

import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;

import java.text.DateFormat;
import java.util.Date;

/**
 * 出错时的“详情”：一句人话写在界面上，原始报错放在这里，能选中、能一键复制给帮手或开发者看。
 */
final class ErrorDetails {
    private ErrorDetails() { }

    static void show(Activity activity, String title, String detail) {
        if (activity.isFinishing() || detail == null || detail.isEmpty()) return;
        String when = DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.MEDIUM).format(new Date());
        String text = detail + "\n\n时间：" + when;
        Sheet.centered(activity, title).message(text).action("复制", true, () -> {
            ClipboardManager clipboard = (ClipboardManager) activity.getSystemService(Context.CLIPBOARD_SERVICE);
            if (clipboard != null) clipboard.setPrimaryClip(ClipData.newPlainText("报错", text));
        }).show();
    }
}
