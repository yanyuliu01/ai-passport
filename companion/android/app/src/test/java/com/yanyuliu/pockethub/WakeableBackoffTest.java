package com.yanyuliu.pockethub;

import org.junit.Test;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import static org.junit.Assert.*;

public class WakeableBackoffTest {
    @Test public void notificationBeforeWaitIsNotLost() throws Exception {
        WakeableBackoff backoff = new WakeableBackoff();
        long observed = backoff.generation();
        backoff.wake();
        ExecutorService worker = Executors.newSingleThreadExecutor();
        try {
            Future<?> done = worker.submit(() -> {
                try { backoff.await(observed, 10000); }
                catch (InterruptedException error) { Thread.currentThread().interrupt(); }
            });
            done.get(1, TimeUnit.SECONDS);
        } finally { worker.shutdownNow(); }
    }

    @Test public void routeOrNewWorkWakesActiveRetry() throws Exception {
        WakeableBackoff backoff = new WakeableBackoff();
        long observed = backoff.generation();
        CountDownLatch entered = new CountDownLatch(1);
        ExecutorService worker = Executors.newSingleThreadExecutor();
        try {
            Future<?> done = worker.submit(() -> {
                synchronized (backoff) {
                    entered.countDown();
                    try { backoff.await(observed, 10000); }
                    catch (InterruptedException error) { Thread.currentThread().interrupt(); }
                }
            });
            assertTrue(entered.await(1, TimeUnit.SECONDS));
            backoff.wake();
            done.get(1, TimeUnit.SECONDS);
        } finally { worker.shutdownNow(); }
    }

    @Test public void continuingFailureRetainsBoundedDelay() throws Exception {
        WakeableBackoff backoff = new WakeableBackoff();
        long start = System.nanoTime();
        backoff.await(backoff.generation(), 150);
        assertTrue("Retry should not become a busy loop", System.nanoTime() - start >= TimeUnit.MILLISECONDS.toNanos(120));
    }
}
