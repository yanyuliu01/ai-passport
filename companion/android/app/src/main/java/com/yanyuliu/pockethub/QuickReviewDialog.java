package com.yanyuliu.pockethub;

import android.app.Activity;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.text.TextUtils;
import android.view.Gravity;
import android.view.View;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.text.DateFormat;
import java.util.Date;
import java.util.Map;

/**
 * 快速审核，一张底部面板：最上一句现在是什么模式；中间是等你确认的操作和一个“全部允许”；
 * 下面一个开关管“只读工具自动通过”（开的时候选时长）；最近记录收起来。每 3 秒自己更新。
 * 审核随时能做，不受聊天或帮手忙不忙影响，并且始终发给打开时的那台电脑。
 */
final class QuickReviewDialog {
    private static final int INK = 0xff1f3149, HINT = 0xff6b7a8f, BLUE = 0xff2f6fe0, AMBER = 0xff8a5413, DANGER = 0xffb3402f;
    private static final int[] DURATIONS = {0, 900, 3600, 28800, 86400, 604800};
    private static final String[] DURATION_LABELS = {"一直开着", "15 分钟", "1 小时", "8 小时", "24 小时", "7 天"};

    private final Activity activity;
    private final RuntimeClient.Target target;
    private final Sheet sheet;
    private final TextView status, pendingHeading, modeHint, historyToggle;
    private final LinearLayout pendingRows, durations, history;
    private final TextView batch, modeSwitch, details;
    private String lastProblem;
    private final EditText minutes;
    private final Runnable individual;
    private final boolean legacyFallback;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final long openedAt = android.os.SystemClock.elapsedRealtime();
    private QuickReview snapshot;
    private boolean busy, choosing, historyOpen, closed;
    private String rowsKey = "";
    private final Runnable poll = () -> load(null, null);

    static void show(Activity activity, Runnable individual) {
        show(activity, individual, false);
    }

    static void show(Activity activity, Runnable individual, boolean legacyFallback) {
        RuntimeClient.Target target = RuntimeClient.selected(activity);
        if (target == null) {
            Sheet.centered(activity, "快速审核").message("先连接一台电脑，再设置快速审核。").show();
            return;
        }
        new QuickReviewDialog(activity, target, individual, legacyFallback).load(null, null);
    }

    private QuickReviewDialog(Activity activity, RuntimeClient.Target target, Runnable individual, boolean legacyFallback) {
        this.activity = activity; this.target = target;
        this.individual = individual; this.legacyFallback = legacyFallback;
        sheet = new Sheet(activity, "快速审核", false);
        LinearLayout page = column(); page.setPadding(dp(4), 0, dp(4), 0);

        status = text("正在读取…", 14, INK); status.setPadding(dp(4), 0, dp(4), dp(4)); page.addView(status);
        // 出错时多一个“详细信息”：原始报错在那里看，界面上只说一句人话。
        details = text("详细信息 ›", 13, BLUE); details.setPadding(dp(4), dp(2), dp(4), dp(10)); details.setVisibility(View.GONE);
        details.setOnClickListener(v -> ErrorDetails.show(activity, "快速审核的报错", "电脑：" + target.name + "\n" + lastProblem));
        page.addView(details);

        pendingHeading = text("等你确认", 12, HINT); pendingHeading.setPadding(dp(4), 0, dp(4), dp(6)); page.addView(pendingHeading);
        pendingRows = column(); page.addView(pendingRows);
        batch = button("", true); batch.setVisibility(View.GONE);
        page.addView(batch, buttonParams());

        TextView autoHeading = text("只读工具自动通过", 12, HINT); autoHeading.setPadding(dp(4), dp(18), dp(4), dp(6)); page.addView(autoHeading);
        LinearLayout modeRow = new LinearLayout(activity); modeRow.setGravity(Gravity.CENTER_VERTICAL);
        modeRow.setPadding(dp(14), dp(12), dp(14), dp(12)); modeRow.setBackground(round(Color.WHITE));
        LinearLayout modeWords = column();
        modeWords.addView(text("读文件、搜索时不用问你", 15, INK));
        modeHint = text("改文件、跑命令、联网还是要你确认", 12, HINT); modeHint.setPadding(0, dp(3), 0, 0); modeWords.addView(modeHint);
        modeRow.addView(modeWords, new LinearLayout.LayoutParams(0, -2, 1));
        modeSwitch = text("", 14, BLUE); modeSwitch.setPadding(dp(12), dp(6), 0, dp(6)); modeRow.addView(modeSwitch);
        page.addView(modeRow);
        durations = column(); durations.setVisibility(View.GONE); durations.setPadding(0, dp(6), 0, 0);
        for (int i = 0; i < DURATIONS.length; i++) {
            int seconds = DURATIONS[i];
            TextView choice = button(DURATION_LABELS[i], false);
            choice.setOnClickListener(v -> enable(seconds)); durations.addView(choice, buttonParams());
        }
        LinearLayout custom = new LinearLayout(activity); custom.setGravity(Gravity.CENTER_VERTICAL);
        minutes = new EditText(activity); minutes.setInputType(InputType.TYPE_CLASS_NUMBER); minutes.setSingleLine(true);
        minutes.setHint("自定义分钟数"); minutes.setTextSize(14);
        custom.addView(minutes, new LinearLayout.LayoutParams(0, -2, 1));
        TextView ok = button("开启", false); ok.setPadding(dp(18), 0, dp(18), 0);
        ok.setOnClickListener(v -> {
            try { enable(QuickReview.customSeconds(minutes.getText().toString())); }
            catch (IllegalArgumentException error) { minutes.setError(error.getMessage()); }
        });
        custom.addView(ok, new LinearLayout.LayoutParams(-2, dp(44)));
        durations.addView(custom);
        page.addView(durations);

        historyToggle = text("› 最近记录", 13, HINT); historyToggle.setPadding(dp(4), dp(18), dp(4), dp(6));
        historyToggle.setOnClickListener(v -> { historyOpen = !historyOpen; rowsKey = ""; if (snapshot != null) render(null); });
        page.addView(historyToggle);
        history = column(); page.addView(history);

        TextView one = text("逐条确认 ›", 13, BLUE); one.setPadding(dp(4), dp(14), dp(4), dp(4));
        one.setOnClickListener(v -> { sheet.dismiss(); individual.run(); });
        page.addView(one);

        ScrollView scroll = new ScrollView(activity); scroll.addView(page);
        sheet.add(scroll);
        sheet.setOnDismissListener(d -> {
            closed = true; handler.removeCallbacks(poll);
            long elapsed = android.os.SystemClock.elapsedRealtime() - openedAt;
            new Thread(() -> UsageEvents.screen("quick_review", elapsed), "review-usage").start();
        });
        batch.setOnClickListener(v -> { if (snapshot != null && !busy) load("batch", snapshot.batchBody()); });
        modeSwitch.setOnClickListener(v -> {
            if (snapshot == null || busy) return;
            if (snapshot.automatic()) { load("mode", snapshot.modeBody(false)); return; }
            if (!snapshot.readOnlyModeSupported()) return;
            if (!snapshot.durationConfigSupported()) { enable(3600); return; }
            choosing = !choosing; durations.setVisibility(choosing ? View.VISIBLE : View.GONE); render(null);
        });
        sheet.show();
    }

    private void enable(int seconds) {
        if (snapshot == null || busy) return;
        try { choosing = false; durations.setVisibility(View.GONE); load("mode", snapshot.modeBody(true, seconds)); }
        catch (IllegalArgumentException | IllegalStateException error) { status.setText(error.getMessage()); }
    }

    private boolean current() {
        RuntimeClient.Target selected = RuntimeClient.selected(activity);
        return !closed && !activity.isFinishing() && !activity.isDestroyed()
                && selected != null && selected.url.equals(target.url) && selected.token.equals(target.token);
    }

    private void load(String operation, String body) {
        handler.removeCallbacks(poll);
        if (!current()) { if (!closed) sheet.dismiss(); return; }
        if (busy) return;
        busy = true; batch.setEnabled(false); modeSwitch.setEnabled(false);
        RuntimeClient.review(target, operation, body, (data, problem) -> {
            busy = false;
            if (!current()) return;
            batch.setEnabled(true); modeSwitch.setEnabled(true);
            lastProblem = problem; details.setVisibility(problem == null ? View.GONE : View.VISIBLE);
            if (problem != null) {
                if (legacyFallback && operation == null && RuntimeClient.REVIEW_UNAVAILABLE.equals(problem)) {
                    sheet.dismiss(); individual.run(); return;
                }
                // 409：列表在你点的时候变了，什么都没做；重新读一遍就好，不必让你看到状态码。
                if (problem.contains("409") && operation != null) {
                    status.setText("列表刚变了，刚才那一下没有生效，已重新读取。"); status.setTextColor(AMBER);
                    handler.postDelayed(poll, 800); return;
                }
                if (problem.contains("409")) {
                    // 连读都被拒：这台电脑现在不接受从手机做快速审核（比如经中继连着），重试也一样，不再循环。
                    status.setText("这台电脑现在不接受手机上的快速审核（多半是电脑上的小鱼版本旧了），可以先点下面“逐条确认”。"); status.setTextColor(AMBER);
                    modeSwitch.setText(""); batch.setVisibility(View.GONE); pendingHeading.setVisibility(View.GONE); return;
                }
                status.setText("没读到电脑上的审核状态：" + problem.replaceFirst("^远端返回 \\d+：", "") + "。过一会儿会再试。");
                status.setTextColor(DANGER);
                handler.postDelayed(poll, 5000);
                return;
            }
            snapshot = new QuickReview(data);
            render(operation);
            handler.postDelayed(poll, 3000);
        });
    }

    private void render(String operation) {
        String message = snapshot.automatic() ? (snapshot.readOnlyModeSupported()
                ? snapshot.persistent() ? "只读工具自动通过：一直开着"
                : "只读工具自动通过：开到 " + time(Json.number(snapshot.data, "expires_at"))
                : "自动通过的范围没核实，建议先关掉")
                : snapshot.pending.isEmpty() ? "没有等你确认的操作" : snapshot.pending.size() + " 项操作在等你确认";
        if ("batch".equals(operation)) {
            int allowed = 0, other = 0;
            for (Object raw : Json.list(snapshot.data, "results")) {
                if ("allowed".equals(Json.string(Json.object(raw), "status"))) allowed++; else other++;
            }
            message = "已允许 " + allowed + " 项" + (other > 0 ? "，" + other + " 项已经变了没有允许" : "") + "。";
        }
        String error = Json.string(snapshot.data, "error");
        status.setText(message + (error.isEmpty() ? "" : "\n" + error));
        status.setTextColor(snapshot.automatic() ? AMBER : INK);
        modeSwitch.setText(snapshot.automatic() ? "关闭" : snapshot.readOnlyModeSupported() ? (choosing ? "收起" : "开启 ›") : "需要升级");
        modeSwitch.setTextColor(snapshot.automatic() ? DANGER : snapshot.readOnlyModeSupported() ? BLUE : HINT);
        modeHint.setText(snapshot.automatic() && !snapshot.persistent()
                ? "到时自动关；改文件、跑命令、联网还是要你确认" : "改文件、跑命令、联网还是要你确认");
        batch.setVisibility(snapshot.pending.isEmpty() ? View.GONE : View.VISIBLE);
        batch.setText("全部允许这 " + snapshot.pending.size() + " 项");
        pendingHeading.setVisibility(snapshot.pending.isEmpty() ? View.GONE : View.VISIBLE);
        String key = snapshot.pending.toString() + historyOpen + Json.list(snapshot.data, "audit").toString();
        if (rowsKey.equals(key)) return;
        rowsKey = key;
        pendingRows.removeAllViews();
        for (Object raw : snapshot.pending) {
            Map<String, Object> item = Json.object(raw);
            String detail = Json.string(item, "detail");
            LinearLayout row = column(); row.setPadding(dp(14), dp(10), dp(14), dp(10)); row.setBackground(round(Color.WHITE));
            String agent = AppPresentation.name(AppPresentation.role(Json.string(item, "agent")));
            row.addView(text(agent + " 想用 " + Json.string(item, "tool"), 14, INK));
            TextView preview = text(CardViews.oneLine(detail, 120), 12, HINT);
            preview.setTypeface(Typeface.MONOSPACE); preview.setMaxLines(2); preview.setEllipsize(TextUtils.TruncateAt.END);
            preview.setPadding(0, dp(4), 0, 0); row.addView(preview);
            row.setOnClickListener(v -> Sheet.centered(activity, Json.string(item, "tool")).message(detail).show());
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2); params.setMargins(0, 0, 0, dp(6));
            pendingRows.addView(row, params);
        }
        if (Json.number(snapshot.data, "pending_total") > snapshot.pending.size()) {
            TextView more = text("还有更多，处理完这批会接着列出。", 12, HINT); more.setPadding(dp(4), 0, dp(4), dp(4));
            pendingRows.addView(more);
        }
        java.util.List<Object> audit = Json.list(snapshot.data, "audit");
        historyToggle.setText((historyOpen ? "⌃ " : "› ") + "最近记录" + (audit.isEmpty() ? "" : " " + audit.size()));
        history.removeAllViews();
        if (!historyOpen) return;
        for (int i = audit.size() - 1; i >= 0; i--) {
            Map<String, Object> event = Json.object(audit.get(i));
            String label = switch (Json.string(event, "action")) {
                case "auto_allow" -> "开启自动通过";
                case "manual" -> "恢复手动";
                case "allowed" -> "已允许";
                case "requested" -> "已提交";
                case "expired" -> "自动通过到期";
                case "restored" -> "重启后恢复自动通过";
                case "rejected" -> "没有生效";
                case "storage_failed" -> "保存失败，已恢复手动";
                default -> "请求变了，没有允许";
            };
            String agent = Json.string(event, "agent");
            TextView line = text(time(Json.number(event, "at")) + "  " + label
                    + (agent.isEmpty() ? "" : " · " + AppPresentation.name(AppPresentation.role(agent))), 12, HINT);
            line.setPadding(dp(4), dp(4), dp(4), dp(4)); history.addView(line);
        }
    }

    private TextView text(String value, int size, int color) {
        TextView view = new TextView(activity); view.setText(value); view.setTextSize(size); view.setTextColor(color);
        view.setLineSpacing(dp(2), 1f); return view;
    }

    private TextView button(String label, boolean primary) {
        TextView view = text(label, 15, primary ? Color.WHITE : INK); view.setGravity(Gravity.CENTER);
        int fill = primary ? BLUE : Color.WHITE;
        view.setBackground(new RippleDrawable(ColorStateList.valueOf(0x22000000), round(fill), round(fill)));
        return view;
    }

    private LinearLayout.LayoutParams buttonParams() {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, dp(46)); params.setMargins(0, dp(4), 0, dp(2));
        return params;
    }

    private LinearLayout column() { LinearLayout view = new LinearLayout(activity); view.setOrientation(LinearLayout.VERTICAL); return view; }
    private GradientDrawable round(int color) { GradientDrawable shape = new GradientDrawable(); shape.setColor(color); shape.setCornerRadius(dp(14)); return shape; }
    private int dp(float value) { return Math.round(value * activity.getResources().getDisplayMetrics().density); }

    private static String time(double seconds) {
        return DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT).format(new Date((long) (seconds * 1000)));
    }
}
