package com.yanyuliu.pockethub;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.Executor;
import java.util.function.Consumer;

/** One cache writer, with at most one pending checkpoint per source. An older
 * in-flight checkpoint always finishes before its replacement. A crash can
 * replay from the previous atomic checkpoint without skipping applied records.
 */
final class SyncCacheWriter {
    private final Executor executor;
    private final int limit;
    private final Consumer<RuntimeException> failed;
    private final Map<String, Runnable> pending = new LinkedHashMap<>();
    private boolean running;

    SyncCacheWriter(Executor executor, int limit, Consumer<RuntimeException> failed) {
        this.executor = executor; this.limit = limit; this.failed = failed;
    }

    synchronized boolean offer(String source, Runnable checkpoint) {
        if (!pending.containsKey(source) && pending.size() >= limit) return false;
        pending.put(source, checkpoint);
        if (!running) {
            running = true;
            try { executor.execute(this::drain); }
            catch (RuntimeException error) { running = false; failed.accept(error); return false; }
        }
        return true;
    }

    private void drain() {
        while (true) {
            Runnable checkpoint;
            synchronized (this) {
                if (pending.isEmpty()) { running = false; return; }
                String source = pending.keySet().iterator().next();
                checkpoint = pending.remove(source);
            }
            try { checkpoint.run(); }
            catch (RuntimeException error) { failed.accept(error); }
        }
    }
}
