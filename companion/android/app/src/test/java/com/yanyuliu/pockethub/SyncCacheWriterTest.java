package com.yanyuliu.pockethub;

import org.junit.Test;
import static org.junit.Assert.*;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

public class SyncCacheWriterTest {
    @Test public void queuedCheckpointsCoalescePerSourceAndDoNoWorkOnCaller() {
        List<Runnable> tasks = new ArrayList<>(); List<String> stored = new ArrayList<>();
        SyncCacheWriter writer = new SyncCacheWriter(tasks::add, 2, error -> fail(error.toString()));
        assertTrue(writer.offer("A", () -> stored.add("A-old")));
        assertTrue(writer.offer("B", () -> stored.add("B")));
        assertTrue(writer.offer("A", () -> stored.add("A-new")));
        assertFalse(writer.offer("C", () -> stored.add("C")));
        assertTrue(stored.isEmpty()); assertEquals(1, tasks.size());
        tasks.remove(0).run();
        assertEquals(List.of("A-new", "B"), stored);
        assertTrue(writer.offer("C", () -> stored.add("C")));
        tasks.remove(0).run(); assertEquals(List.of("A-new", "B", "C"), stored);
    }

    @Test public void slowStoreDoesNotBlockOfferingNewerStateOrAnotherSource() throws Exception {
        var executor = Executors.newSingleThreadExecutor();
        CountDownLatch began = new CountDownLatch(1), release = new CountDownLatch(1), done = new CountDownLatch(1);
        List<String> stored = new ArrayList<>();
        SyncCacheWriter writer = new SyncCacheWriter(executor, 2, error -> fail(error.toString()));
        try {
            writer.offer("A", () -> {
                began.countDown();
                try { if (!release.await(5, TimeUnit.SECONDS)) throw new IllegalStateException("blocked"); }
                catch (InterruptedException error) { throw new IllegalStateException(error); }
                stored.add("A-epoch1");
            });
            assertTrue(began.await(5, TimeUnit.SECONDS));
            assertTrue(writer.offer("A", () -> stored.add("A-superseded")));
            assertTrue(writer.offer("A", () -> stored.add("A-epoch2")));
            assertTrue(writer.offer("B", () -> { stored.add("B"); done.countDown(); }));
            // These offers returned while the original store was still blocked.
            release.countDown(); assertTrue(done.await(5, TimeUnit.SECONDS));
            assertEquals(List.of("A-epoch1", "A-epoch2", "B"), stored);
        } finally { release.countDown(); executor.shutdownNow(); }
    }

    @Test public void failedCheckpointDoesNotStrandLaterWork() {
        List<Runnable> tasks = new ArrayList<>(); List<String> stored = new ArrayList<>();
        List<RuntimeException> errors = new ArrayList<>();
        SyncCacheWriter writer = new SyncCacheWriter(tasks::add, 2, errors::add);
        writer.offer("A", () -> { throw new IllegalStateException("disk unavailable"); });
        writer.offer("B", () -> stored.add("B")); tasks.remove(0).run();
        assertEquals(1, errors.size()); assertEquals(List.of("B"), stored);
        writer.offer("A", () -> stored.add("A-recovered")); tasks.remove(0).run();
        assertEquals(List.of("B", "A-recovered"), stored);
    }

    @Test public void rejectedSchedulingCanRetryWithoutLosingAnotherSource() {
        List<Runnable> tasks = new ArrayList<>(); int[] attempts = {0};
        SyncCacheWriter writer = new SyncCacheWriter(task -> {
            if (attempts[0]++ == 0) throw new java.util.concurrent.RejectedExecutionException();
            tasks.add(task);
        }, 2, error -> {});
        List<String> stored = new ArrayList<>();
        assertFalse(writer.offer("A", () -> stored.add("A")));
        assertTrue(writer.offer("B", () -> stored.add("B")));
        tasks.remove(0).run(); assertEquals(List.of("A", "B"), stored);
    }
}
