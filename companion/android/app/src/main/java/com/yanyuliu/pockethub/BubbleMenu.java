package com.yanyuliu.pockethub;

import android.app.Activity;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.drawable.GradientDrawable;
import android.view.Gravity;
import android.view.View;
import android.widget.LinearLayout;
import android.widget.PopupWindow;
import android.widget.TextView;

/**
 * 长按聊天气泡出的菜单，照微信：深灰圆角条，图标在上、字在下，一个小三角指向那条气泡；
 * 气泡上方放得下就放上方，否则放下方。它不抢焦点，键盘开着时长按也不会把键盘收起来；
 * 点别处或按返回就关。
 */
final class BubbleMenu {
    private static BubbleMenu open;
    private static final int FILL = 0xff4c4c4c;
    private final Activity activity;
    private final LinearLayout items;
    private PopupWindow popup;

    BubbleMenu(Activity activity) {
        this.activity = activity;
        items = new LinearLayout(activity);
        items.setPadding(dp(6), dp(4), dp(6), dp(4));
        GradientDrawable shape = new GradientDrawable(); shape.setColor(FILL); shape.setCornerRadius(dp(8));
        items.setBackground(shape);
    }

    BubbleMenu item(String icon, String label, Runnable action) {
        LinearLayout cell = new LinearLayout(activity); cell.setOrientation(LinearLayout.VERTICAL);
        cell.setGravity(Gravity.CENTER); cell.setPadding(dp(12), dp(8), dp(12), dp(6));
        NavIconView glyph = new NavIconView(activity, icon); glyph.color(Color.WHITE);
        cell.addView(glyph, new LinearLayout.LayoutParams(dp(22), dp(22)));
        TextView name = new TextView(activity); name.setText(label); name.setTextSize(12); name.setTextColor(Color.WHITE);
        name.setPadding(0, dp(4), 0, 0); name.setIncludeFontPadding(false); cell.addView(name);
        cell.setContentDescription(label); cell.setFocusable(true);
        cell.setOnClickListener(v -> { dismissOpen(); action.run(); });
        items.addView(cell, new LinearLayout.LayoutParams(dp(56), -2));
        return this;
    }

    void show(View anchor) {
        if (activity.isFinishing() || !anchor.isAttachedToWindow()) return;
        dismissOpen(); open = this;
        int[] at = new int[2]; anchor.getLocationInWindow(at);
        items.measure(View.MeasureSpec.UNSPECIFIED, View.MeasureSpec.UNSPECIFIED);
        int width = items.getMeasuredWidth(), height = items.getMeasuredHeight(), arrow = dp(7);
        // 上方要留出顶栏的高度，放不下就翻到气泡下面。
        boolean above = at[1] - height - arrow > dp(96);
        Tail tail = new Tail(above);
        LinearLayout box = new LinearLayout(activity); box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER_HORIZONTAL);
        if (!above) box.addView(tail, new LinearLayout.LayoutParams(dp(14), arrow));
        box.addView(items);
        if (above) box.addView(tail, new LinearLayout.LayoutParams(dp(14), arrow));
        popup = new PopupWindow(box, width, height + arrow, false);
        popup.setOutsideTouchable(true); popup.setTouchable(true); popup.setAnimationStyle(0);
        popup.setBackgroundDrawable(new android.graphics.drawable.ColorDrawable(Color.TRANSPARENT));
        popup.setOnDismissListener(() -> { anchor.setForeground(null); if (open == this) open = null; });
        anchor.setForeground(round(0x22000000));
        int screen = activity.getResources().getDisplayMetrics().widthPixels;
        int x = Math.max(dp(8), Math.min(screen - width - dp(8), at[0] + anchor.getWidth() / 2 - width / 2));
        int y = above ? at[1] - height - arrow : at[1] + anchor.getHeight();
        popup.showAtLocation(anchor, Gravity.TOP | Gravity.START, x, y);
        // 小三角对准气泡中间，菜单被屏幕边挡住挪开时也一样。
        tail.setTranslationX(at[0] + anchor.getWidth() / 2f - x - width / 2f);
        View.OnAttachStateChangeListener gone = new View.OnAttachStateChangeListener() {
            @Override public void onViewAttachedToWindow(View view) { }
            @Override public void onViewDetachedFromWindow(View view) { dismissOpen(); }
        };
        anchor.addOnAttachStateChangeListener(gone);
    }

    /** 有菜单开着就关掉并返回 true（给返回键用）。 */
    static boolean dismissOpen() {
        if (open == null || open.popup == null || !open.popup.isShowing()) { open = null; return false; }
        open.popup.dismiss(); open = null; return true;
    }

    private final class Tail extends View {
        private final boolean down;
        private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        Tail(boolean down) { super(activity); this.down = down; paint.setColor(FILL); }
        @Override protected void onDraw(Canvas canvas) {
            Path path = new Path(); float w = getWidth(), h = getHeight();
            if (down) { path.moveTo(0, 0); path.lineTo(w, 0); path.lineTo(w / 2, h); }
            else { path.moveTo(0, h); path.lineTo(w, h); path.lineTo(w / 2, 0); }
            path.close(); canvas.drawPath(path, paint);
        }
    }

    private GradientDrawable round(int color) { GradientDrawable shape = new GradientDrawable(); shape.setColor(color); shape.setCornerRadius(dp(14)); return shape; }
    private int dp(float value) { return Math.round(value * activity.getResources().getDisplayMetrics().density); }
}
