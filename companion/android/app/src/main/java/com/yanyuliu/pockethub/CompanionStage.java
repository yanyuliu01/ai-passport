package com.yanyuliu.pockethub;

import android.animation.Animator;
import android.animation.AnimatorListenerAdapter;
import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.drawable.GradientDrawable;
import android.provider.Settings;
import android.view.Gravity;
import android.view.View;
import android.view.animation.LinearInterpolator;
import android.view.animation.OvershootInterpolator;
import android.widget.FrameLayout;
import android.widget.LinearLayout;

/**
 * 首页上的小幽：每种动作对应一个真实状态，不放纯装饰的动画。
 * idle 轻轻呼吸；listening 侧头、身后光圈外扩；thinking 上下浮、旁边三个点；talking 说话时轻弹；
 * working 帮手在做时专注；happy 刚做完。系统关了动画或偏好里关了呼吸时只换表情不动。
 */
final class CompanionStage extends FrameLayout {
    private final AvatarView avatar;
    private final View ringA, ringB;
    private final LinearLayout dots;
    private final float density;
    private String state = "";
    private ValueAnimator loop, rings, blink;

    CompanionStage(Context context, int sizeDp) {
        super(context);
        density = getResources().getDisplayMetrics().density;
        int size = (int) (sizeDp * density);
        View halo = circle(0xffe9f1ff, 0);
        ringA = circle(0, 0xff9cc0f5); ringB = circle(0, 0xff9cc0f5);
        for (View view : new View[] {ringA, ringB, halo}) {
            addView(view, new LayoutParams(size, size, Gravity.CENTER));
        }
        ringA.setAlpha(0); ringB.setAlpha(0);
        avatar = new AvatarView(context, "ds", "happy");
        int inner = (int) (size * .86f);
        addView(avatar, new LayoutParams(inner, inner, Gravity.CENTER));
        avatar.setPivotY(inner * .9f); avatar.setPivotX(inner / 2f);
        dots = new LinearLayout(context); dots.setGravity(Gravity.CENTER);
        int pad = (int) (8 * density);
        dots.setPadding(pad, pad / 2 + 2, pad, pad / 2 + 2);
        GradientDrawable bubble = new GradientDrawable(); bubble.setColor(0xffffffff); bubble.setCornerRadius(14 * density);
        dots.setBackground(bubble);
        for (int i = 0; i < 3; i++) {
            View dot = circle(0xff2f6fe0, 0);
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams((int) (6 * density), (int) (6 * density));
            params.setMargins((int) (2 * density), 0, (int) (2 * density), 0);
            dots.addView(dot, params);
        }
        LayoutParams dotParams = new LayoutParams(-2, -2, Gravity.TOP | Gravity.END);
        addView(dots, dotParams);
        dots.setVisibility(GONE);
        setClipChildren(false);
        setMinimumWidth((int) (size * 1.3f)); setMinimumHeight((int) (size * 1.15f));
    }

    private View circle(int fill, int stroke) {
        View view = new View(getContext());
        GradientDrawable shape = new GradientDrawable(); shape.setShape(GradientDrawable.OVAL);
        shape.setColor(fill); if (stroke != 0) shape.setStroke((int) (3 * density), stroke);
        view.setBackground(shape);
        return view;
    }

    void set(String role, String mood, String next) {
        avatar.set(role, mood);
        if (next.equals(state)) return;
        state = next;
        restart();
    }

    private boolean moving() {
        return getContext().getSharedPreferences("ds_ui", Context.MODE_PRIVATE).getBoolean("animate", true)
                && Settings.Global.getFloat(getContext().getContentResolver(), Settings.Global.ANIMATOR_DURATION_SCALE, 1f) > 0;
    }

    private void restart() {
        stopLoops();
        avatar.setRotation(0); avatar.setTranslationY(0); avatar.setScaleX(1); avatar.setScaleY(1);
        dots.setVisibility(state.equals("thinking") ? VISIBLE : GONE);
        if (!moving() || !isAttachedToWindow()) return;
        long period;
        switch (state) {
            case "listening": period = 1200; break;
            case "thinking": period = 800; break;
            case "talking": period = 360; break;
            case "working": period = 2400; break;
            default: period = 3200;
        }
        loop = ValueAnimator.ofFloat(0f, 1f); loop.setDuration(period);
        loop.setRepeatCount(ValueAnimator.INFINITE); loop.setInterpolator(new LinearInterpolator());
        loop.addUpdateListener(animation -> {
            float wave = (float) Math.sin(2 * Math.PI * (float) animation.getAnimatedValue());
            switch (state) {
                case "listening":
                    avatar.setRotation(4 * wave); avatar.setTranslationY(-3 * density * Math.abs(wave)); break;
                case "thinking":
                    avatar.setTranslationY(-6 * density * (wave + 1) / 2); break;
                case "talking":
                    avatar.setScaleX(1 + .04f * (wave + 1) / 2); avatar.setScaleY(1 - .03f * (wave + 1) / 2); break;
                default:
                    float breath = 1 + .035f * (wave + 1) / 2; avatar.setScaleX(breath); avatar.setScaleY(breath);
            }
        });
        loop.start();
        if (state.equals("listening")) {
            rings = ValueAnimator.ofFloat(0f, 1f); rings.setDuration(1400);
            rings.setRepeatCount(ValueAnimator.INFINITE); rings.setInterpolator(new LinearInterpolator());
            rings.addUpdateListener(animation -> {
                float t = (float) animation.getAnimatedValue();
                ring(ringA, t); ring(ringB, (t + .5f) % 1f);
            });
            rings.start();
        }
        if (state.equals("thinking")) {
            blink = ValueAnimator.ofFloat(0f, 1f); blink.setDuration(1000);
            blink.setRepeatCount(ValueAnimator.INFINITE); blink.setInterpolator(new LinearInterpolator());
            blink.addUpdateListener(animation -> {
                float t = (float) animation.getAnimatedValue();
                for (int i = 0; i < dots.getChildCount(); i++) {
                    float phase = (t - i * .2f + 1f) % 1f;
                    dots.getChildAt(i).setAlpha(phase < .4f ? .2f + 2f * phase : 1f - (phase - .4f) * 1.3f);
                }
            });
            blink.start();
        }
    }

    private void ring(View view, float t) {
        view.setScaleX(1 + .9f * t); view.setScaleY(1 + .9f * t); view.setAlpha(.5f * (1 - t));
    }

    private void stopLoops() {
        for (ValueAnimator animator : new ValueAnimator[] {loop, rings, blink}) if (animator != null) animator.cancel();
        loop = rings = blink = null;
        ringA.setAlpha(0); ringB.setAlpha(0);
    }

    /** 被摸一下或事情做完：蹦一下。 */
    void hop() {
        if (!moving()) return;
        if (loop != null) loop.pause();
        avatar.animate().cancel();
        avatar.animate().translationY(-26 * density).scaleX(1.05f).scaleY(.95f).setDuration(180)
                .setListener(new AnimatorListenerAdapter() {
                    @Override public void onAnimationEnd(Animator animation) {
                        avatar.animate().translationY(0).scaleX(1).scaleY(1).setDuration(380)
                                .setInterpolator(new OvershootInterpolator(2.2f)).setListener(new AnimatorListenerAdapter() {
                                    @Override public void onAnimationEnd(Animator done) { if (loop != null) loop.resume(); }
                                }).start();
                    }
                }).start();
    }

    /** 信从这里飞出去的起点（窗口坐标）。 */
    void center(int[] out) {
        avatar.getLocationInWindow(out);
        out[0] += avatar.getWidth() / 2; out[1] += avatar.getHeight() / 2;
    }

    @Override protected void onAttachedToWindow() { super.onAttachedToWindow(); restart(); }
    @Override protected void onDetachedFromWindow() { stopLoops(); super.onDetachedFromWindow(); }
    @Override protected void onVisibilityChanged(View changed, int visibility) {
        super.onVisibilityChanged(changed, visibility);
        if (ringA == null) return;
        if (isShown()) restart(); else stopLoops();
    }
}
