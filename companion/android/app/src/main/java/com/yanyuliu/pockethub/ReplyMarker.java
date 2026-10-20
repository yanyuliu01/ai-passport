package com.yanyuliu.pockethub;

import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.view.View;
import android.view.animation.LinearInterpolator;

/**
 * 回复末尾的小标记，告诉你小幽现在在干什么：
 * 想（一个点在呼吸）、写（星芒在转）、说完了（星芒停下、变淡）。
 * 只画在最后一条回复下面；状态换了才重新起动画。
 */
final class ReplyMarker extends View {
    static final String HIDDEN = "hidden", THINKING = "thinking", WRITING = "writing", RESTING = "resting";

    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
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
        state = next;
        setVisibility(next.equals(HIDDEN) ? GONE : VISIBLE);
        if (clock != null) { clock.cancel(); clock = null; }
        phase = 0;
        if ((next.equals(THINKING) || next.equals(WRITING)) && Motion.enabled()) {
            clock = ValueAnimator.ofFloat(0f, 1f);
            clock.setDuration(next.equals(THINKING) ? 1200 : 2400);
            clock.setRepeatCount(ValueAnimator.INFINITE);
            clock.setInterpolator(new LinearInterpolator());
            clock.addUpdateListener(animation -> { phase = (float) animation.getAnimatedValue(); invalidate(); });
            if (isAttachedToWindow()) clock.start();
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

    @Override protected void onDraw(Canvas canvas) {
        float size = Math.min(getWidth(), getHeight());
        float cx = getWidth() / 2f, cy = getHeight() / 2f;
        if (state.equals(THINKING)) {
            // 呼吸的点：大小和深浅一起起伏。
            float wave = (float) (0.5 - 0.5 * Math.cos(phase * 2 * Math.PI));
            paint.setStyle(Paint.Style.FILL);
            paint.setColor(color);
            paint.setAlpha((int) (110 + 145 * wave));
            canvas.drawCircle(cx, cy, size * (0.12f + 0.08f * wave), paint);
        } else if (state.equals(WRITING) || state.equals(RESTING)) {
            // 八道光的星芒：写的时候慢慢转、光一长一短；说完了停下变淡。
            boolean live = state.equals(WRITING);
            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeWidth(size * 0.09f);
            paint.setColor(color);
            paint.setAlpha(live ? 255 : 120);
            float turn = live ? phase * 360f : 0f;
            canvas.save();
            canvas.rotate(turn, cx, cy);
            for (int ray = 0; ray < 8; ray++) {
                double angle = ray * Math.PI / 4;
                float pulse = live ? (float) (0.5 + 0.5 * Math.sin(phase * 4 * Math.PI + ray)) : 0.5f;
                float outer = size * (0.32f + 0.12f * pulse), inner = size * 0.08f;
                canvas.drawLine(cx + (float) Math.cos(angle) * inner, cy + (float) Math.sin(angle) * inner,
                        cx + (float) Math.cos(angle) * outer, cy + (float) Math.sin(angle) * outer, paint);
            }
            canvas.restore();
        }
    }
}
