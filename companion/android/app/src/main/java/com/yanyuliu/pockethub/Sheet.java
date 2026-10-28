package com.yanyuliu.pockethub;

import android.app.Activity;
import android.app.Dialog;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.ColorDrawable;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.content.res.ColorStateList;
import android.text.TextUtils;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.VelocityTracker;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;
import android.view.animation.AccelerateInterpolator;
import android.view.animation.DecelerateInterpolator;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

/**
 * 从底部滑上来的面板，代替系统对话框：顶上一条把手和标题、右上角关闭。
 * 往下拖过三分之一或者快速往下一甩就关；点外面、按返回键也关，关的时候滑下去。
 * 选项（option）点了先关面板再做事。只放列表、说明和详情；危险操作的确认仍用系统对话框。
 */
final class Sheet extends Dialog {
    private static final int INK = 0xff1f3149, HINT = 0xff6b7a8f, LINE = 0xffeef1f6, DANGER = 0xffb3402f;

    private final Activity activity;
    private final LinearLayout panel;
    private final LinearLayout body;
    private final FrameLayout holder;
    private boolean closing;
    private boolean floating;

    /** full：占满大半个屏幕、内容自己管滚动（用于审批这类详情页）；否则按内容高度，太长时滚动。 */
    Sheet(Activity activity, String title, boolean full) {
        super(activity);
        this.activity = activity;
        requestWindowFeature(Window.FEATURE_NO_TITLE);
        panel = new LinearLayout(activity); panel.setOrientation(LinearLayout.VERTICAL);
        GradientDrawable shape = new GradientDrawable(); shape.setColor(0xfff6f8fb);
        float radius = dp(24); shape.setCornerRadii(new float[] {radius, radius, radius, radius, 0, 0, 0, 0});
        panel.setBackground(shape); panel.setClickable(true);

        LinearLayout top = new LinearLayout(activity); top.setOrientation(LinearLayout.VERTICAL);
        View handle = new View(activity);
        GradientDrawable bar = new GradientDrawable(); bar.setColor(0xffd5dde8); bar.setCornerRadius(dp(2));
        handle.setBackground(bar);
        LinearLayout.LayoutParams handleParams = new LinearLayout.LayoutParams(dp(36), dp(4));
        handleParams.gravity = Gravity.CENTER_HORIZONTAL; handleParams.setMargins(0, dp(8), 0, dp(4));
        top.addView(handle, handleParams);
        LinearLayout heading = new LinearLayout(activity); heading.setGravity(Gravity.CENTER_VERTICAL);
        heading.setPadding(dp(20), dp(4), dp(8), dp(6));
        TextView name = new TextView(activity); name.setText(title == null ? "" : title);
        name.setTextSize(17); name.setTypeface(Typeface.DEFAULT_BOLD); name.setTextColor(INK);
        name.setMaxLines(2); name.setEllipsize(TextUtils.TruncateAt.END); name.setIncludeFontPadding(false);
        heading.addView(name, new LinearLayout.LayoutParams(0, -2, 1));
        LinearLayout close = new LinearLayout(activity); close.setGravity(Gravity.CENTER);
        close.setBackground(round(0xffeef1f6, dp(18))); close.setContentDescription("关闭");
        close.setOnClickListener(view -> close());
        NavIconView cross = new NavIconView(activity, "close"); cross.color(INK);
        close.addView(cross, new LinearLayout.LayoutParams(dp(18), dp(18)));
        heading.addView(close, new LinearLayout.LayoutParams(dp(36), dp(36)));
        top.addView(heading);
        panel.addView(top);
        draggable(top);

        body = new LinearLayout(activity); body.setOrientation(LinearLayout.VERTICAL);
        int screen = activity.getResources().getDisplayMetrics().heightPixels;
        if (full) {
            body.setPadding(dp(20), 0, dp(20), dp(16));
            panel.addView(body, new LinearLayout.LayoutParams(-1, 0, 1));
        } else {
            body.setPadding(dp(12), 0, dp(12), dp(20));
            ScrollView scroll = new ScrollView(activity) {
                @Override protected void onMeasure(int width, int height) {
                    super.onMeasure(width, MeasureSpec.makeMeasureSpec(screen * 7 / 10, MeasureSpec.AT_MOST));
                }
            };
            scroll.setVerticalScrollBarEnabled(false); scroll.addView(body);
            panel.addView(scroll, new LinearLayout.LayoutParams(-1, -2));
        }

        holder = new FrameLayout(activity);
        holder.setOnClickListener(view -> close());
        FrameLayout.LayoutParams panelParams = new FrameLayout.LayoutParams(-1, full ? screen * 92 / 100 : -2, Gravity.BOTTOM);
        holder.addView(panel, panelParams);
        setContentView(holder);
        Window window = getWindow();
        if (window != null) {
            window.setBackgroundDrawable(new ColorDrawable(Color.TRANSPARENT));
            window.setLayout(-1, -1);
            window.setGravity(Gravity.BOTTOM);
            window.addFlags(WindowManager.LayoutParams.FLAG_DIM_BEHIND);
            window.setDimAmount(.32f);
            window.setWindowAnimations(0);
        }
        setOnShowListener(dialog -> slideIn());
    }

    static Sheet centered(Activity activity,String title) {
        Sheet sheet=new Sheet(activity,title,false);sheet.floating=true;
        sheet.panel.setBackground(sheet.round(0xfff6f8fb,sheet.dp(20)));
        LinearLayout top=(LinearLayout)sheet.panel.getChildAt(0);top.getChildAt(0).setVisibility(View.GONE);top.setOnTouchListener(null);top.setPadding(0,sheet.dp(12),0,0);
        FrameLayout.LayoutParams params=(FrameLayout.LayoutParams)sheet.panel.getLayoutParams();
        params.width=Math.min(sheet.dp(420),activity.getResources().getDisplayMetrics().widthPixels-sheet.dp(32));params.gravity=Gravity.CENTER;
        sheet.panel.setLayoutParams(params);return sheet;
    }

    /** 往面板里放一段内容。 */
    Sheet add(View view) { body.addView(view); return this; }

    /** 一段说明文字。 */
    Sheet message(String text) {
        TextView words = new TextView(activity); words.setText(text); words.setTextSize(14);
        words.setTextColor(INK); words.setLineSpacing(dp(3), 1f); words.setTextIsSelectable(true);
        words.setPadding(dp(8), dp(4), dp(8), dp(12));
        return add(words);
    }

    /** 一行选项：点了关掉面板再做事。detail 是第二行小字，可为 null。 */
    Sheet option(String label, String detail, boolean danger, Runnable action) {
        LinearLayout row = new LinearLayout(activity); row.setOrientation(LinearLayout.VERTICAL);
        row.setGravity(Gravity.CENTER_VERTICAL); row.setMinimumHeight(dp(52));
        row.setPadding(dp(14), dp(10), dp(14), dp(10));
        row.setBackground(new RippleDrawable(ColorStateList.valueOf(0x14000000), round(Color.WHITE, dp(14)), round(Color.WHITE, dp(14))));
        TextView title = new TextView(activity); title.setText(label); title.setTextSize(15);
        title.setTextColor(danger ? DANGER : INK); title.setIncludeFontPadding(false);
        row.addView(title);
        if (detail != null && !detail.isEmpty()) {
            TextView second = new TextView(activity); second.setText(detail); second.setTextSize(12);
            second.setTextColor(HINT); second.setMaxLines(2); second.setEllipsize(TextUtils.TruncateAt.END);
            second.setPadding(0, dp(4), 0, 0); row.addView(second);
        }
        row.setFocusable(true);
        row.setOnClickListener(view -> { close(); if (action != null) action.run(); });
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
        params.setMargins(0, 0, 0, dp(6));
        body.addView(row, params);
        return this;
    }

    /** 底部一排按钮（例如「刷新用量」）。 */
    Sheet action(String label, boolean primary, Runnable action) {
        TextView button = new TextView(activity); button.setText(label); button.setTextSize(15);
        button.setGravity(Gravity.CENTER); button.setIncludeFontPadding(false);
        button.setTextColor(primary ? Color.WHITE : INK);
        int fill = primary ? 0xff2f6fe0 : Color.WHITE;
        button.setBackground(new RippleDrawable(ColorStateList.valueOf(0x22000000), round(fill, dp(14)), round(fill, dp(14))));
        button.setOnClickListener(view -> { close(); if (action != null) action.run(); });
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, dp(48));
        params.setMargins(0, dp(6), 0, 0);
        body.addView(button, params);
        return this;
    }

    @Override public void onBackPressed() { close(); }

    /** 滑下去再关。 */
    void close() {
        if (closing) return;
        closing = true;
        if (floating || !Motion.enabled() || !isShowing()) { dismissNow(); return; }
        panel.animate().cancel();
        panel.animate().translationY(panel.getHeight()).setDuration(200)
                .setInterpolator(new AccelerateInterpolator(1.4f)).withEndAction(this::dismissNow).start();
        fadeDim(0f, 200);
    }

    private void dismissNow() {
        try { super.dismiss(); } catch (IllegalArgumentException detached) { /* 窗口已经没了 */ }
    }

    /** 程序里直接关（比如提交完成）也走滑下去。 */
    @Override public void dismiss() { close(); }

    private void slideIn() {
        if (floating || !Motion.enabled()) return;
        panel.setTranslationY(activity.getResources().getDisplayMetrics().heightPixels);
        panel.post(() -> {
            panel.setTranslationY(panel.getHeight());
            panel.animate().translationY(0).setDuration(300).setInterpolator(new DecelerateInterpolator(2f)).start();
            fadeDim(.32f, 300);
        });
    }

    private void fadeDim(float to, int duration) {
        Window window = getWindow();
        if (window == null) return;
        float from = window.getAttributes().dimAmount;
        android.animation.ValueAnimator dim = android.animation.ValueAnimator.ofFloat(from, to);
        dim.setDuration(duration);
        dim.addUpdateListener(animation -> {
            Window current = getWindow();
            if (current != null) current.setDimAmount((float) animation.getAnimatedValue());
        });
        dim.start();
    }

    /** 拖把手和标题：跟着手往下走，松手时拖过三分之一或甩得快就关，否则弹回去。 */
    private void draggable(View grip) {
        final float[] start = new float[1];
        final VelocityTracker[] tracker = new VelocityTracker[1];
        grip.setOnTouchListener((view, event) -> {
            switch (event.getActionMasked()) {
                case MotionEvent.ACTION_DOWN:
                    start[0] = event.getRawY();
                    tracker[0] = VelocityTracker.obtain(); tracker[0].addMovement(event);
                    panel.animate().cancel();
                    return true;
                case MotionEvent.ACTION_MOVE:
                    if (tracker[0] != null) tracker[0].addMovement(event);
                    panel.setTranslationY(Math.max(0, event.getRawY() - start[0]));
                    return true;
                case MotionEvent.ACTION_UP:
                case MotionEvent.ACTION_CANCEL:
                    float speed = 0;
                    if (tracker[0] != null) {
                        tracker[0].addMovement(event); tracker[0].computeCurrentVelocity(1000);
                        speed = tracker[0].getYVelocity(); tracker[0].recycle(); tracker[0] = null;
                    }
                    if (panel.getTranslationY() > panel.getHeight() / 3f || speed > dp(900)) close();
                    else panel.animate().translationY(0).setDuration(180).setInterpolator(new DecelerateInterpolator()).start();
                    return true;
                default:
                    return false;
            }
        });
    }

    private GradientDrawable round(int color, float radius) {
        GradientDrawable shape = new GradientDrawable(); shape.setColor(color); shape.setCornerRadius(radius); return shape;
    }

    private int dp(float value) {
        return Math.round(value * activity.getResources().getDisplayMetrics().density);
    }
}
