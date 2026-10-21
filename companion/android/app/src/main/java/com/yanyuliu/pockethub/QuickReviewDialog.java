package com.yanyuliu.pockethub;

import android.app.Activity;
import android.app.AlertDialog;
import android.graphics.Color;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.text.InputType;
import android.widget.AdapterView;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Spinner;

import java.text.DateFormat;
import java.util.Date;
import java.util.Map;

/** Review remains available while chat or helpers are busy, and always targets its original computer. */
final class QuickReviewDialog {
    private final Activity activity;
    private final RuntimeClient.Target target;
    private final AlertDialog dialog;
    private final LinearLayout rows;
    private final TextView status;
    private final Button batch, mode;
    private final Button applyDuration;
    private final LinearLayout durationControls;
    private final Spinner duration;
    private final EditText minutes;
    private static final int[] DURATIONS = {0, 900, 3600, 28800, 86400, 604800};
    private boolean durationInitialized;
    private final Runnable individual;
    private final boolean legacyFallback;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final long openedAt = android.os.SystemClock.elapsedRealtime();
    private QuickReview snapshot;
    private boolean busy;
    private String rowsKey = "";
    private final Runnable poll = () -> load(null, null);

    static void show(Activity activity, Runnable individual) {
        show(activity, individual, false);
    }

    static void show(Activity activity, Runnable individual, boolean legacyFallback) {
        RuntimeClient.Target target = RuntimeClient.selected(activity);
        if (target == null) {
            new AlertDialog.Builder(activity).setMessage("先连接一台电脑，再设置快速审核。")
                    .setPositiveButton("知道了", null).show();
            return;
        }
        new QuickReviewDialog(activity, target, individual, legacyFallback).load(null, null);
    }

    private QuickReviewDialog(Activity activity, RuntimeClient.Target target, Runnable individual, boolean legacyFallback) {
        this.activity = activity; this.target = target;
        this.individual = individual; this.legacyFallback = legacyFallback;
        LinearLayout page = new LinearLayout(activity); page.setOrientation(LinearLayout.VERTICAL);
        int pad = (int) (16 * activity.getResources().getDisplayMetrics().density);
        page.setPadding(pad, pad, pad, pad);
        status = new TextView(activity); page.addView(status);
        TextView scope = new TextView(activity);
        scope.setText("仅允许当前列表这一批工具操作；新来的请求仍需确认，不会永久放行，也不改变自动通过设置。\n持续或定时放行请使用下方“只读工具自动通过”。它仅用于这台电脑当前主对话派出的帮手，"
                + "只覆盖明确的文件读取、目录和文本检索；可持续开启或自定时长，随时关闭。命令、修改、删除、付费及联网外发"
                + "仍需你确认，可在下方批量处理。独立桌面会话、其他电脑和卡住后是否继续仍需单独处理。"
                + "这是你的预授权，系统不会逐项判断风险；支持时长设置的 Runtime 会保存设置，重启后仍按原期限生效。");
        scope.setPadding(0, pad / 2, 0, pad / 2); page.addView(scope);
        TextView updates = new TextView(activity);
        updates.setText("页面每 3 秒自动更新，无需反复点击。\n“更新状态”只拉取待确认数量、最近记录和自动通过期限，不会开启或延长自动通过。");
        updates.setPadding(0, 0, 0, pad / 2); page.addView(updates);
        batch = new Button(activity); batch.setText("正在读取待审核操作…"); page.addView(batch);
        batch.setTooltipText("仅允许当前列表中的操作；新请求仍需确认，不改变自动通过设置。");
        durationControls = new LinearLayout(activity); durationControls.setOrientation(LinearLayout.VERTICAL);
        TextView durationLabel = new TextView(activity); durationLabel.setText("自动通过的有效时间");
        durationControls.addView(durationLabel);
        duration = new Spinner(activity);
        ArrayAdapter<String> choices = new ArrayAdapter<>(activity, android.R.layout.simple_spinner_item,
                new String[]{"持续开启，直到手动关闭", "15 分钟", "1 小时", "8 小时", "24 小时", "7 天", "自定义分钟数"});
        choices.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        duration.setAdapter(choices); durationControls.addView(duration);
        minutes = new EditText(activity); minutes.setInputType(InputType.TYPE_CLASS_NUMBER);
        minutes.setSingleLine(true); minutes.setHint("1–525600 分钟");
        durationControls.addView(minutes); minutes.setVisibility(View.GONE);
        duration.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            public void onItemSelected(AdapterView<?> parent, View view, int position, long id) {
                minutes.setVisibility(position == DURATIONS.length ? View.VISIBLE : View.GONE);
            }
            public void onNothingSelected(AdapterView<?> parent) { }
        });
        page.addView(durationControls); durationControls.setVisibility(View.GONE);
        mode = new Button(activity); mode.setText("正在读取审核模式…"); page.addView(mode);
        applyDuration = new Button(activity); applyDuration.setText("按所选时长重新设置自动通过");
        page.addView(applyDuration); applyDuration.setVisibility(View.GONE);
        ScrollView scroll = new ScrollView(activity);
        rows = new LinearLayout(activity); rows.setOrientation(LinearLayout.VERTICAL);
        scroll.addView(rows); page.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
        dialog = new AlertDialog.Builder(activity).setTitle("快速审核 · " + target.name)
                .setView(page).setNegativeButton("关闭", null)
                .setNeutralButton("逐条确认", (d, which) -> individual.run())
                .setPositiveButton("更新状态", null).create();
        dialog.setOnDismissListener(d -> {
            handler.removeCallbacks(poll);
            long elapsed = android.os.SystemClock.elapsedRealtime() - openedAt;
            new Thread(() -> UsageEvents.screen("quick_review", elapsed), "review-usage").start();
        });
        dialog.show();
        dialog.getWindow().setLayout(-1, (int) (activity.getResources().getDisplayMetrics().heightPixels * 0.88));
        dialog.getButton(AlertDialog.BUTTON_POSITIVE).setTooltipText("重新读取电脑上的最新状态，不开启、不延长自动通过。");
        dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v -> load(null, null));
        batch.setOnClickListener(v -> {
            if (snapshot != null && !busy) load("batch", snapshot.batchBody());
        });
        mode.setOnClickListener(v -> {
            if (snapshot != null && !busy) {
                if (snapshot.automatic()) load("mode", snapshot.modeBody(false)); else enableMode();
            }
        });
        applyDuration.setOnClickListener(v -> enableMode());
    }

    private void enableMode() {
        if (snapshot == null || busy) return;
        try {
            int seconds = !snapshot.durationConfigSupported() ? 3600
                    : duration.getSelectedItemPosition() == DURATIONS.length
                    ? QuickReview.customSeconds(minutes.getText().toString())
                    : DURATIONS[duration.getSelectedItemPosition()];
            load("mode", snapshot.modeBody(true, seconds));
        } catch (IllegalArgumentException | IllegalStateException error) {
            minutes.setError(error.getMessage());
        }
    }

    private boolean current() {
        RuntimeClient.Target selected = RuntimeClient.selected(activity);
        return !activity.isFinishing() && !activity.isDestroyed() && dialog.isShowing()
                && selected != null && selected.url.equals(target.url) && selected.token.equals(target.token);
    }

    private void load(String operation, String body) {
        handler.removeCallbacks(poll);
        if (!current()) { dialog.dismiss(); return; }
        if (busy) return;
        busy = true; batch.setEnabled(false); mode.setEnabled(false);
        applyDuration.setEnabled(false); duration.setEnabled(false); minutes.setEnabled(false);
        dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(false);
        RuntimeClient.review(target, operation, body, (data, problem) -> {
            busy = false;
            if (!current()) { dialog.dismiss(); return; }
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(true);
            if (problem != null) {
                if (legacyFallback && operation == null && RuntimeClient.REVIEW_UNAVAILABLE.equals(problem)) {
                    dialog.dismiss(); individual.run(); return;
                }
                snapshot = null;
                status.setText(problem + "\n自动更新已暂停，请点“更新状态”核实结果并恢复更新；也可逐条确认。自动通过仍以电脑上的设置为准。");
                return;
            }
            snapshot = new QuickReview(data);
            render(operation);
            handler.postDelayed(poll, 3000);
        });
    }

    private void render(String operation) {
        String message = snapshot.automatic() ? (snapshot.readOnlyModeSupported()
                ? snapshot.persistent() ? "只读工具自动通过持续开启，直到手动关闭"
                : "只读工具自动通过已开启，到 " + expiryTime(Json.number(snapshot.data, "expires_at"))
                : "自动通过范围尚未核实，请先关闭并升级 Runtime")
                : "当前为手动审核";
        if (!snapshot.durationConfigSupported()) message += "\n此 Runtime 仅支持临时一小时模式，升级后可设置时长或持续开启。";
        if ("batch".equals(operation)) {
            int allowed = 0, other = 0;
            for (Object raw : Json.list(snapshot.data, "results")) {
                if ("allowed".equals(Json.string(Json.object(raw), "status"))) allowed++; else other++;
            }
            message = "已允许 " + allowed + " 项，已变化或未允许 " + other + " 项。本次仅处理刚才列表中的操作，新请求仍需确认。\n" + message;
        }
        String error = Json.string(snapshot.data, "error");
        status.setText(message + (error.isEmpty() ? "" : "\n" + error));
        status.setTextColor(snapshot.automatic() ? 0xff8a5413 : Color.DKGRAY);
        batch.setText(snapshot.pending.isEmpty() ? "当前没有待确认操作"
                : "仅允许当前 " + snapshot.pending.size() + " 项");
        batch.setEnabled(!snapshot.pending.isEmpty());
        mode.setText(snapshot.automatic() ? "关闭自动通过，恢复逐条确认" : snapshot.readOnlyModeSupported()
                ? snapshot.durationConfigSupported() ? "按所选时长开启只读工具自动通过"
                : "开启一小时只读工具自动通过" : "升级 Runtime 后可开启只读自动通过");
        mode.setEnabled(snapshot.automatic() || snapshot.readOnlyModeSupported());
        durationControls.setVisibility(snapshot.durationConfigSupported() ? View.VISIBLE : View.GONE);
        duration.setEnabled(true); minutes.setEnabled(true);
        if (!durationInitialized && snapshot.durationConfigSupported()) {
            durationInitialized = true;
            int seconds = snapshot.automatic() ? (int) Json.number(snapshot.data, "duration_seconds") : 0;
            int selected = DURATIONS.length;
            for (int i = 0; i < DURATIONS.length; i++) if (DURATIONS[i] == seconds) selected = i;
            duration.setSelection(selected);
            if (selected == DURATIONS.length) minutes.setText(String.valueOf(seconds / 60));
        }
        applyDuration.setVisibility(snapshot.automatic() && snapshot.durationConfigSupported() ? View.VISIBLE : View.GONE);
        applyDuration.setEnabled(snapshot.readOnlyModeSupported());
        String key = snapshot.pending.toString() + Json.list(snapshot.data, "audit").toString();
        if (rowsKey.equals(key)) return;
        rowsKey = key;
        rows.removeAllViews();
        if (snapshot.pending.isEmpty()) add("目前没有可批量回答的工具操作。", false);
        for (Object raw : snapshot.pending) {
            Map<String, Object> item = Json.object(raw);
            String project = Json.string(item, "project");
            add(Json.string(item, "agent") + " · " + Json.string(item, "tool")
                    + "\n来源任务：" + Json.string(item, "card")
                    + (project.isEmpty() ? "" : " · 项目：" + project)
                    + ("desktop".equals(Json.string(item, "source")) ? " · 独立桌面会话" : ""), false);
            String detail = Json.string(item, "detail");
            TextView preview = add(detail.length() > 500 ? detail.substring(0, 500) + "…\n点击查看完整操作" : detail, true);
            preview.setOnClickListener(v -> {
                ScrollView full = new ScrollView(activity);
                TextView text = new TextView(activity); text.setText(detail); text.setTextIsSelectable(true);
                text.setPadding(24, 24, 24, 24); full.addView(text);
                new AlertDialog.Builder(activity).setTitle(Json.string(item, "tool"))
                        .setView(full).setPositiveButton("返回审核", null).show();
            });
        }
        if (Json.number(snapshot.data, "pending_total") > snapshot.pending.size())
            add("当前仅列出前 100 项，本次只允许这批；处理后会自动更新剩余操作。", false);
        add("最近审核记录（本电脑，最多 50 条）", false);
        java.util.List<Object> audit = Json.list(snapshot.data, "audit");
        for (int i = audit.size() - 1; i >= 0; i--) {
            Map<String, Object> event = Json.object(audit.get(i));
            String action = Json.string(event, "action");
            String label = switch (action) {
                case "auto_allow" -> "开启自动通过";
                case "manual" -> "恢复手动";
                case "allowed" -> "已允许";
                case "requested" -> "已提交审核决定";
                case "expired" -> "自动通过已到期";
                case "restored" -> "重启后恢复自动通过设置";
                case "rejected" -> "审核操作未生效";
                case "storage_failed" -> "保存失败，已恢复手动";
                default -> "请求已变化或无法允许";
            };
            add(time(Json.number(event, "at")) + " · " + label
                    + ("duration_changed".equals(Json.string(event, "change")) ? "（调整时长）" : "")
                    + (("auto_allow".equals(action) || "restored".equals(action)) && event.containsKey("seconds")
                    ? Json.number(event, "seconds") == 0 ? " · 持续开启"
                    : " · " + (long) (Json.number(event, "seconds") / 60) + " 分钟" : "")
                    + ("auto_allow".equals(Json.string(event, "by")) ? "（自动）" : "")
                    + " · " + Json.string(event, "agent") + " " + Json.string(event, "card"), false);
        }
    }

    private TextView add(String text, boolean detail) {
        TextView view = new TextView(activity); view.setText(text); view.setTextIsSelectable(true);
        view.setPadding(0, 12, 0, 12);
        if (detail) view.setTypeface(android.graphics.Typeface.MONOSPACE);
        rows.addView(view);
        return view;
    }

    private static String time(double seconds) {
        return DateFormat.getTimeInstance(DateFormat.SHORT).format(new Date((long) (seconds * 1000)));
    }

    private static String expiryTime(double seconds) {
        return DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT)
                .format(new Date((long) (seconds * 1000)));
    }
}
