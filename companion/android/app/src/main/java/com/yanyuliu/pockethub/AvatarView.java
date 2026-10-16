package com.yanyuliu.pockethub;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Rect;
import android.graphics.RectF;
import android.view.View;
import android.util.AttributeSet;
import android.animation.ValueAnimator;
import android.provider.Settings;
import java.util.HashMap;
import java.util.Map;

/** Normalized, transparent 256px portraits cut from the retained source atlases. */
final class AvatarView extends View {
    private static final Map<Integer, Bitmap> CACHE = new HashMap<>();
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.FILTER_BITMAP_FLAG);
    private String role;
    private String mood;
    private final Rect source = new Rect(0, 0, 256, 256);
    private final RectF destination = new RectF();
    private ValueAnimator animator;
    private float lift;

    public AvatarView(Context context) { this(context, "ds", "idle"); }
    public AvatarView(Context context, AttributeSet attributes) { this(context, "ds", "idle"); }

    AvatarView(Context context, String role, String mood) {
        super(context);
        set(role, mood);
        setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
    }

    void set(String role, String mood) {
        this.role = AppPresentation.role(role);
        this.mood = mood == null ? "idle" : mood;
        setContentDescription(AppPresentation.name(role));
        invalidate();
    }

    void pose(String mood) { set(role, mood); }

    void refreshAnimation() {
        boolean animate = isAttachedToWindow() && isShown() && getWindowVisibility() == VISIBLE
                && getContext().getSharedPreferences("ds_ui", Context.MODE_PRIVATE).getBoolean("animate", true)
                && Settings.Global.getFloat(getContext().getContentResolver(), Settings.Global.ANIMATOR_DURATION_SCALE, 1f) > 0;
        if (!animate) {
            if (animator != null) { animator.cancel(); animator = null; }
            lift = 0; invalidate(); return;
        }
        if (animator != null) return;
        animator = ValueAnimator.ofFloat(-1f, 1f);
        animator.setDuration(1800); animator.setRepeatMode(ValueAnimator.REVERSE);
        animator.setRepeatCount(ValueAnimator.INFINITE);
        animator.addUpdateListener(value -> { lift = (float)value.getAnimatedValue() * getResources().getDisplayMetrics().density; invalidate(); });
        animator.start();
    }

    @Override protected void onAttachedToWindow() { super.onAttachedToWindow(); refreshAnimation(); }
    @Override protected void onDetachedFromWindow() {
        if (animator != null) { animator.cancel(); animator = null; }
        super.onDetachedFromWindow();
    }
    @Override protected void onVisibilityChanged(View changed, int visibility) {
        super.onVisibilityChanged(changed, visibility); refreshAnimation();
    }
    @Override protected void onWindowVisibilityChanged(int visibility) {
        super.onWindowVisibilityChanged(visibility); refreshAnimation();
    }

    private int resource() {
        boolean happy = mood.equals("happy");
        boolean focused = mood.equals("type") || mood.equals("read") || mood.equals("focus");
        boolean thinking = mood.equals("think") || mood.equals("ask");
        switch (role) {
            case "codex": return happy ? R.drawable.portrait_codex_happy
                    : focused ? R.drawable.portrait_codex_focus
                    : thinking ? R.drawable.portrait_codex_think : R.drawable.portrait_codex_idle;
            case "claude": return happy ? R.drawable.portrait_claude_happy
                    : focused ? R.drawable.portrait_claude_focus
                    : thinking ? R.drawable.portrait_claude_think : R.drawable.portrait_claude_idle;
            case "ds": return happy ? R.drawable.portrait_ds_happy
                    : focused ? R.drawable.portrait_ds_focus
                    : thinking ? R.drawable.portrait_ds_think : R.drawable.portrait_ds_idle;
            default: return 0;
        }
    }

    @Override protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        int resource = resource();
        if (resource == 0) {
            paint.setColor(0xffdce6f2);
            float cx = getWidth() / 2f, cy = getHeight() / 2f;
            canvas.drawCircle(cx, cy, Math.min(cx, cy) * .85f, paint);
            paint.setColor(0xff6584a7);
            paint.setTextAlign(Paint.Align.CENTER);
            paint.setTextSize(Math.min(getWidth(), getHeight()) * .28f);
            String name = AppPresentation.name(role);
            int end = name.offsetByCodePoints(0, Math.min(2, name.codePointCount(0, name.length())));
            canvas.drawText(name.substring(0, end), cx,
                    cy - (paint.ascent() + paint.descent()) / 2, paint);
            return;
        }
        Bitmap bitmap;
        synchronized (CACHE) {
            bitmap = CACHE.get(resource);
            if (bitmap == null) {
                bitmap = BitmapFactory.decodeResource(getResources(), resource);
                if (bitmap != null) CACHE.put(resource, bitmap);
            }
        }
        if (bitmap == null) return;
        float size = Math.min(getWidth(), getHeight());
        float left = (getWidth() - size) / 2, top = (getHeight() - size) / 2 + lift;
        paint.setFilterBitmap(!role.equals("ds") && !role.equals("codex"));
        source.set(0, 0, bitmap.getWidth(), bitmap.getHeight());
        destination.set(left, top, left + size, top + size);
        canvas.drawBitmap(bitmap, source, destination, paint);
    }
}
