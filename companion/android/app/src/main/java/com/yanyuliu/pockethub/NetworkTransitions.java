package com.yanyuliu.pockethub;

import java.util.Objects;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/** Coalesces default-network callbacks without dropping a later route or validation recovery. */
final class NetworkTransitions<T> {
    interface Scheduler { void schedule(Runnable task, long delayMs); void close(); }
    private static final long COALESCE_MS = 250;
    private final Scheduler scheduler;
    private T current;
    private boolean validated, everValidated, closed, pending;
    private long generation, resetGeneration = -1, ticket;
    private final Runnable changed;

    NetworkTransitions(T initial, boolean initiallyValidated, Runnable changed) {
        this(initial, initiallyValidated, changed, new Scheduler() {
            final ScheduledExecutorService executor = Executors.newSingleThreadScheduledExecutor(task -> {
                Thread thread = new Thread(task, "xiaoyu-network-transitions"); thread.setDaemon(true); return thread;
            });
            public void schedule(Runnable task, long delayMs) { executor.schedule(task, delayMs, TimeUnit.MILLISECONDS); }
            public void close() { executor.shutdownNow(); }
        });
    }

    NetworkTransitions(T initial, boolean initiallyValidated, Runnable changed, Scheduler scheduler) {
        current = initial;
        validated = initial != null && initiallyValidated;
        everValidated = validated;
        this.changed = changed;
        this.scheduler = scheduler;
    }

    synchronized void available(T network) {
        if (closed || network == null || Objects.equals(current, network)) return;
        current = network;
        validated = false; everValidated = false; generation++;
        schedule();
    }

    synchronized void capabilities(T network, boolean nowValidated) {
        if (closed || !Objects.equals(current, network)) return;
        boolean recovered = nowValidated && !validated;
        validated = nowValidated;
        if (recovered) {
            // The first validation of a newly available network belongs to the
            // same transition even if it arrives after its coalescing window.
            // A later validated -> unvalidated -> validated cycle is new work.
            if (everValidated || (!pending && resetGeneration != generation)) schedule();
            everValidated = true;
        }
    }

    synchronized void lost(T network) {
        if (closed || network == null || !Objects.equals(current, network)) return;
        current = null;
        validated = false; everValidated = false; generation++;
        schedule();
    }

    private void schedule() {
        // One fixed deadline prevents a busy sequence of callbacks from
        // postponing recovery indefinitely. The task observes the newest route.
        if (pending) return;
        pending = true;
        long expected = ++ticket;
        scheduler.schedule(() -> fire(expected), COALESCE_MS);
    }

    private synchronized void fire(long expected) {
        if (closed || !pending || ticket != expected) return;
        pending = false;
        resetGeneration = generation;
        changed.run();
    }

    synchronized void close() {
        if (closed) return;
        closed = true; current = null; validated = false; pending = false; ticket++;
        scheduler.close();
    }
}
