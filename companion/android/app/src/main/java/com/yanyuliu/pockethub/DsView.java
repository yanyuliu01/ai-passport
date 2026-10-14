package com.yanyuliu.pockethub;

import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Rect;
import android.graphics.RectF;
import android.provider.Settings;
import android.view.View;

/** Draw verified sprite bounds directly from the retained source, without assuming equal cells. */
final class DsView extends View {
    private static Bitmap sheet;
    private static Bitmap sleepSheet;
    private final Paint paint = new Paint();
    private String pose;
    private ValueAnimator animator;
    private float lift;

    DsView(Context context, String pose) {
        super(context);
        this.pose = pose;
        setContentDescription("ds · " + pose);
        synchronized (DsView.class) {
            if (sheet == null) sheet = BitmapFactory.decodeResource(getResources(), R.drawable.ds_source);
            if (sleepSheet == null) sleepSheet = BitmapFactory.decodeResource(getResources(), R.drawable.ds_sleep_source);
        }
    }

    void pose(String value) {
        pose = value;
        if (animator != null) animator.setDuration("sleep".equals(pose) ? 1800 : 1200);
        setContentDescription("ds · " + value);
        invalidate();
    }

    private Rect bounds() {
        switch (pose) {
            case "think": return new Rect(1037, 11, 1327, 290);
            case "type": return new Rect(19, 304, 341, 584);
            case "read": return new Rect(706, 11, 1018, 295);
            case "ask": return new Rect(20, 602, 336, 878);
            case "happy": return new Rect(366, 11, 687, 289);
            case "oops": return new Rect(363, 602, 680, 878);
            case "sleep": return new Rect(95, 67, 1307, 1070);
            default: return new Rect(18, 11, 343, 289);
        }
    }

    @Override protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        Bitmap source = "sleep".equals(pose) ? sleepSheet : sheet;
        if (source == null) return;
        Rect src = bounds();
        float scale = Math.min(getWidth() / (float) src.width(), (getHeight() - 4) / (float) src.height());
        float width = src.width() * scale, height = src.height() * scale;
        float left = (getWidth() - width) / 2, top = (getHeight() - height) / 2 + lift;
        canvas.drawBitmap(source, src, new RectF(left, top, left + width, top + height), paint);
    }

    @Override protected void onAttachedToWindow() {
        super.onAttachedToWindow();
        boolean enabled = getContext().getSharedPreferences("ds_ui", Context.MODE_PRIVATE).getBoolean("animate", true)
                && Settings.Global.getFloat(getContext().getContentResolver(), Settings.Global.ANIMATOR_DURATION_SCALE, 1f) > 0;
        if (enabled) {
            animator = ValueAnimator.ofFloat(-1.5f, 1.5f);
            animator.setDuration("sleep".equals(pose) ? 1800 : 1200);
            animator.setRepeatMode(ValueAnimator.REVERSE);
            animator.setRepeatCount(ValueAnimator.INFINITE);
            animator.addUpdateListener(value -> { lift = (float) value.getAnimatedValue(); invalidate(); });
            animator.start();
        }
    }

    @Override protected void onDetachedFromWindow() {
        if (animator != null) { animator.cancel(); animator = null; }
        super.onDetachedFromWindow();
    }
}
