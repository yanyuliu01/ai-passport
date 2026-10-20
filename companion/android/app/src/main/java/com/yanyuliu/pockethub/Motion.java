package com.yanyuliu.pockethub;

import android.animation.ValueAnimator;
import android.view.View;
import android.view.animation.DecelerateInterpolator;
import android.view.animation.OvershootInterpolator;

/**
 * App 里共用的几种动效。时长统一在这里：小变化 150ms，进页面 220ms。
 * 系统里关掉了动画（开发者选项或无障碍「移除动画」）时一律直接到位。
 */
final class Motion {
    static final int QUICK = 150, PAGE = 220;
    /** 进一层（打开任务、帮手、设置子页）、退一层、同层切换。 */
    static final int FORWARD = 1, BACK = -1, SAME = 0;

    private Motion() { }

    static boolean enabled() {
        return ValueAnimator.areAnimatorsEnabled();
    }

    /** 换页：往里走从右边推进来，往回走从左边，同层只淡入。 */
    static void enter(View view, int direction) {
        view.animate().cancel();
        if (!enabled()) { view.setAlpha(1f); view.setTranslationX(0f); return; }
        float shift = view.getResources().getDisplayMetrics().density * 32;
        view.setAlpha(0f);
        view.setTranslationX(direction * shift);
        view.animate().alpha(1f).translationX(0f).setDuration(direction == SAME ? QUICK : PAGE)
                .setInterpolator(new DecelerateInterpolator(1.6f)).start();
    }

    /** 新出现的一条消息：从下面一点点浮上来。 */
    static void appear(View view) { appear(view, 0); }

    /** 同上，等 delay 毫秒再浮上来（一组气泡一条接一条地出）。 */
    static void appear(View view, long delay) {
        if (!enabled()) return;
        float rise = view.getResources().getDisplayMetrics().density * 10;
        view.setAlpha(0f);
        view.setTranslationY(rise);
        view.animate().alpha(1f).translationY(0f).setDuration(PAGE).setStartDelay(delay)
                .setInterpolator(new DecelerateInterpolator(1.6f)).start();
    }

    /**
     * 聊天气泡弹出来（像 iMessage / 微信）：从尖角那一侧由小放大、稍微回弹一下。
     * fromEnd 是你发的（尖角在右下），否则是她发的（尖角在左上）。delay 用来一条接一条地出。
     */
    static void pop(View bubble, boolean fromEnd, long delay) {
        if (!enabled()) return;
        bubble.animate().cancel();
        bubble.setAlpha(0f); bubble.setScaleX(.72f); bubble.setScaleY(.72f);
        bubble.setTranslationY(bubble.getResources().getDisplayMetrics().density * 6);
        Runnable pivot = () -> {
            bubble.setPivotX(fromEnd ? bubble.getWidth() : 0f);
            bubble.setPivotY(fromEnd ? bubble.getHeight() : 0f);
        };
        if (bubble.getWidth() > 0) pivot.run();
        else bubble.addOnLayoutChangeListener(new View.OnLayoutChangeListener() {
            @Override public void onLayoutChange(View view, int l, int t, int r, int b, int ol, int ot, int or, int ob) {
                view.removeOnLayoutChangeListener(this); pivot.run();
            }
        });
        bubble.animate().alpha(1f).scaleX(1f).scaleY(1f).translationY(0f).setStartDelay(delay).setDuration(280)
                .setInterpolator(new OvershootInterpolator(1.4f)).start();
    }

    /** 按钮换了样子（发送、停止、等待）：先缩一下再弹回来，让人看到它变了。 */
    static void swap(View view, Runnable change) {
        view.animate().cancel();
        if (!enabled()) { change.run(); view.setScaleX(1f); view.setScaleY(1f); return; }
        view.animate().scaleX(.6f).scaleY(.6f).alpha(.4f).setDuration(80).withEndAction(() -> {
            change.run();
            view.animate().scaleX(1f).scaleY(1f).alpha(1f).setDuration(QUICK)
                    .setInterpolator(new DecelerateInterpolator(1.6f)).start();
        }).start();
    }
}
