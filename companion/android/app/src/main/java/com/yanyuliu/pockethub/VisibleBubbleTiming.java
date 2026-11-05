package com.yanyuliu.pockethub;

import android.graphics.Rect;
import android.os.SystemClock;
import android.view.View;
import android.view.ViewGroup;
import android.view.ViewParent;
import android.view.ViewTreeObserver;
import android.widget.TextView;

/** Times a real visible frame, not creation of a hidden or still-animating bubble. */
final class VisibleBubbleTiming implements ViewTreeObserver.OnPreDrawListener, View.OnAttachStateChangeListener {
    static final class Message {
        final String id, clientId;
        boolean eligible;
        Message(String id, String clientId) { this.id = id; this.clientId = clientId; }
    }

    private final ViewGroup group;
    private final Message message;
    private boolean observing, posted;

    private VisibleBubbleTiming(ViewGroup group, Message message) { this.group = group; this.message = message; }

    static void watch(ViewGroup group, Message message) {
        if (!TurnTiming.watching(message.clientId)) return;
        VisibleBubbleTiming tracker = new VisibleBubbleTiming(group, message);
        group.addOnAttachStateChangeListener(tracker);
        if (group.isAttachedToWindow()) tracker.onViewAttachedToWindow(group);
    }

    @Override public void onViewAttachedToWindow(View view) {
        if (!observing) { group.getViewTreeObserver().addOnPreDrawListener(this); observing = true; }
    }

    @Override public void onViewDetachedFromWindow(View view) { stopObserving(); posted = false; }

    private void stopObserving() {
        if (observing && group.getViewTreeObserver().isAlive())
            group.getViewTreeObserver().removeOnPreDrawListener(this);
        observing = false;
    }

    @Override public boolean onPreDraw() {
        if (!TurnTiming.watching(message.clientId)) {
            stopObserving(); group.removeOnAttachStateChangeListener(this);
        } else if (!posted && message.eligible && hasVisibleText()) {
            // post runs after this traversal has drawn. Recheck in case the row
            // was hidden, replaced or retracted before the callback ran.
            posted = true;
            group.post(() -> {
                posted = false;
                if (message.eligible && hasVisibleText()) {
                    TurnTiming.visibleBubble(message.clientId, SystemClock.uptimeMillis(), true, true, true);
                    stopObserving(); group.removeOnAttachStateChangeListener(this);
                }
            });
        }
        return true;
    }

    private boolean hasVisibleText() {
        if (!group.isAttachedToWindow() || !group.isShown()) return false;
        for (int i = 0; i < group.getChildCount(); i++) {
            View child = group.getChildAt(i);
            if (!(child instanceof ViewGroup) || ((ViewGroup) child).getChildCount() == 0) continue;
            View words = ((ViewGroup) child).getChildAt(0);
            if (!(words instanceof TextView) || ((TextView) words).getText().toString().trim().isEmpty()
                    || !words.isShown() || !words.getGlobalVisibleRect(new Rect())) continue;
            boolean opaque = true;
            for (View view = words; view != null; ) {
                if (view.getAlpha() <= 0f) { opaque = false; break; }
                ViewParent parent = view.getParent();
                view = parent instanceof View ? (View) parent : null;
            }
            if (opaque) return true;
        }
        return false;
    }
}
