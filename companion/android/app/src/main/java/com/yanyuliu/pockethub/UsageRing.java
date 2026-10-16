package com.yanyuliu.pockethub;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.RectF;
import android.view.View;

/**
 * A helper's remaining quota drawn as a ring around its portrait: full and green when fresh,
 * shrinking clockwise as the quota is spent, turning amber and then red near the end.
 * An unknown quota draws only the faint track.
 */
final class UsageRing extends View {
    private final Paint track = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint arc = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF bounds = new RectF();
    private int left = -1;

    UsageRing(Context context) {
        super(context);
        setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
        track.setStyle(Paint.Style.STROKE); track.setColor(0x1f33465e);
        arc.setStyle(Paint.Style.STROKE); arc.setStrokeCap(Paint.Cap.ROUND);
    }

    /** Percent left, 0–100, or -1 when unknown. */
    void set(int percent) {
        int next = percent < 0 ? -1 : Math.min(100, percent);
        if (next == left) return;
        left = next; invalidate();
    }

    int left() { return left; }

    static int color(int percent) {
        return percent > 40 ? 0xff2f9a5b : percent > 15 ? 0xffd88a1c : 0xffd2453c;
    }

    @Override protected void onDraw(Canvas canvas) {
        float stroke = getResources().getDisplayMetrics().density * 2.5f;
        track.setStrokeWidth(stroke); arc.setStrokeWidth(stroke);
        float inset = stroke / 2f + 1f;
        bounds.set(inset, inset, getWidth() - inset, getHeight() - inset);
        canvas.drawOval(bounds, track);
        if (left <= 0) return;
        arc.setColor(color(left));
        canvas.drawArc(bounds, -90f, 360f * left / 100f, false, arc);
    }
}
