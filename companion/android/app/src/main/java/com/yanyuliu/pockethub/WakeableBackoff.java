package com.yanyuliu.pockethub;

import java.util.concurrent.TimeUnit;

/** A bounded retry delay that can be interrupted by new work or a route change. */
final class WakeableBackoff {
    private volatile long generation;

    long generation() { return generation; }

    synchronized void wake() {
        generation++;
        notifyAll();
    }

    synchronized void await(long observed, long millis) throws InterruptedException {
        long remaining = TimeUnit.MILLISECONDS.toNanos(millis);
        long deadline = System.nanoTime() + remaining;
        while (generation == observed && remaining > 0) {
            TimeUnit.NANOSECONDS.timedWait(this, remaining);
            remaining = deadline - System.nanoTime();
        }
    }
}
