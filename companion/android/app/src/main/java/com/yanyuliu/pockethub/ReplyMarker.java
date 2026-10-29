package com.yanyuliu.pockethub;

import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.view.View;
import android.view.animation.LinearInterpolator;

/**
 * 回复末尾的小标记，告诉你小鱼现在在干什么：
 * 在打字（像 iMessage / 微信那样一个白气泡里三个点依次跳）、说完了（一个淡淡的星芒）。
 * 她的话不再一个字一个字地出，写完一条才整条弹出来，所以想和写都是“正在输入”。
 * 只画在最后一条回复下面；状态换了才重新起动画。
 */
final class ReplyMarker extends View {
    static final String HIDDEN = "hidden", TYPING = "typing", RESTING = "resting";

    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final android.graphics.Path tail = new android.graphics.Path();
    private final android.graphics.RectF box = new android.graphics.RectF();
    private final int color;
    private String state = HIDDEN;
    private ValueAnimator clock;
    private float phase;

    ReplyMarker(Context context, int color) {
        super(context);
        this.color = color;
        paint.setStrokeCap(Paint.Cap.ROUND);
        setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
    }

    String state() { return state; }

    /** 换状态；同一个状态反复调用不会重启动画。 */
    void state(String next) {
        if (next.equals(state)) return;
        String before = state;
        state = next;
        setVisibility(next.equals(HIDDEN) ? GONE : VISIBLE);
        if (clock != null) { clock.cancel(); clock = null; }
        phase = 0;
        if (next.equals(TYPING) && Motion.enabled()) {
            clock = ValueAnimator.ofFloat(0f, 1f);
            clock.setDuration(1300);
            clock.setRepeatCount(ValueAnimator.INFINITE);
            clock.setInterpolator(new LinearInterpolator());
            clock.addUpdateListener(animation -> { phase = (float) animation.getAnimatedValue(); invalidate(); });
            if (isAttachedToWindow()) clock.start();
            // 刚开始打字：气泡从左上角弹出来。
            if (before.equals(HIDDEN) || before.equals(RESTING)) Motion.pop(this, false, 0);
        }
        invalidate();
    }

    @Override protected void onAttachedToWindow() {
        super.onAttachedToWindow();
        if (clock != null && !clock.isStarted()) clock.start();
    }

    @Override protected void onDetachedFromWindow() {
        if (clock != null) clock.cancel();
        super.onDetachedFromWindow();
    }

    private float dp(float value) { return value * getResources().getDisplayMetrics().density; }

    @Override protected void onDraw(Canvas canvas) {
        float w = getWidth(), h = getHeight();
        if (state.equals(TYPING)) {
            // 和她的气泡一样的白底、左上角是小尖角；里面三个灰点依次往上跳、变深。
            float big = h / 2f, small = h * 0.17f;
            paint.setStyle(Paint.Style.FILL);
            paint.setColor(0xffffffff);
            box.set(0, 0, w, h);
            tail.reset();
            tail.addRoundRect(box, new float[] {small, small, big, big, big, big, big, big},
                    android.graphics.Path.Direction.CW);
            canvas.drawPath(tail, paint);
            float r = h * 0.09f, gap = r * 3.2f, cx = w / 2f, cy = h / 2f;
            for (int dot = 0; dot < 3; dot++) {
                float t = phase - dot * 0.16f;
                t -= (float) Math.floor(t);
                // 每个点在一圈里前 40% 的时间跳一下，其余时间歇着。
                float lift = t < 0.4f ? (float) Math.sin(t / 0.4f * Math.PI) : 0f;
                paint.setColor(0xff9aa6b6);
                paint.setAlpha((int) (120 + 135 * lift));
                canvas.drawCircle(cx + (dot - 1) * gap, cy - lift * h * 0.12f, r, paint);
            }
        } else if (state.equals(RESTING)) {
            // 说完了：一个静止、变淡的八道光星芒，画在左边一个正方形里。
            float size = Math.min(w, h) * 0.6f;
            float cx = size / 2f + dp(2), cy = h / 2f;
            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeWidth(size * 0.09f);
            paint.setColor(color);
            paint.setAlpha(120);
            for (int ray = 0; ray < 8; ray++) {
                double angle = ray * Math.PI / 4;
                float outer = size * 0.38f, inner = size * 0.08f;
                canvas.drawLine(cx + (float) Math.cos(angle) * inner, cy + (float) Math.sin(angle) * inner,
                        cx + (float) Math.cos(angle) * outer, cy + (float) Math.sin(angle) * outer, paint);
            }
        }
    }
}
