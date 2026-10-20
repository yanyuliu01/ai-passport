package com.yanyuliu.pockethub;

import android.animation.ValueAnimator;
import android.view.View;
import android.view.animation.DecelerateInterpolator;

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
    static void appear(View view) {
        if (!enabled()) return;
        float rise = view.getResources().getDisplayMetrics().density * 10;
        view.setAlpha(0f);
        view.setTranslationY(rise);
        view.animate().alpha(1f).translationY(0f).setDuration(PAGE)
                .setInterpolator(new DecelerateInterpolator(1.6f)).start();
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
