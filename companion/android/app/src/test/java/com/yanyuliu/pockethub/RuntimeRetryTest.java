package com.yanyuliu.pockethub;

import org.junit.Test;
import static org.junit.Assert.*;
import java.io.IOException;
import java.net.SocketException;
import java.net.SocketTimeoutException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;

public class RuntimeRetryTest {
    @Test public void lostSubmitResponseRetriesSameClientIdWithoutDuplicatingWork() throws Exception {
        byte[] body = "{\"text\":\"你好\",\"client_id\":\"same-id\"}".getBytes(StandardCharsets.UTF_8);
        AtomicInteger attempts = new AtomicInteger(), work = new AtomicInteger();
        java.util.Set<String> ids = new java.util.HashSet<>(); List<Long> pauses = new ArrayList<>();
        String result = RuntimeRetry.run(RuntimeRetry.safe("POST", "https://runtime.test/v1/messages", body), () -> {
            String id = BuddyProtocol.parseFlatObject(new String(body, StandardCharsets.UTF_8)).get("client_id");
            if (ids.add(id)) work.incrementAndGet();
            if (attempts.incrementAndGet() == 1) throw new SocketException("Software caused connection abort");
            return id;
        }, pauses::add);
        assertEquals("same-id", result); assertEquals(2, attempts.get()); assertEquals(1, work.get());
        assertEquals(java.util.Arrays.asList(500L), pauses);
    }
    @Test public void reconnectsWhileWaitingForExistingReply() throws Exception {
        AtomicInteger attempts = new AtomicInteger();
        String result = RuntimeRetry.run(RuntimeRetry.safe("GET", "https://runtime.test/v1/messages/m1", null), () -> {
            if (attempts.incrementAndGet() < 3) throw new SocketTimeoutException(); return "done";
        }, delay -> { });
        assertEquals("done", result); assertEquals(3, attempts.get());
    }
    @Test public void persistentDisconnectStopsAfterThreeAttempts() throws Exception {
        AtomicInteger attempts = new AtomicInteger();
        try {
            RuntimeRetry.run(true, () -> { attempts.incrementAndGet(); throw new SocketException(); }, delay -> { });
            fail();
        } catch (SocketException expected) { assertEquals(3, attempts.get()); }
    }
    @Test public void serverErrorsAreNotRetried() throws Exception {
        AtomicInteger attempts = new AtomicInteger();
        try {
            RuntimeRetry.run(true, () -> { attempts.incrementAndGet(); throw new IOException("令牌不对（401）"); }, delay -> fail());
            fail();
        } catch (IOException expected) { assertEquals(1, attempts.get()); }
    }
    @Test public void mutationsRequireAnExplicitDeduplicationContract() {
        assertFalse(RuntimeRetry.safe("POST", "https://runtime.test/v1/messages", "{\"text\":\"hello\"}".getBytes(StandardCharsets.UTF_8)));
        assertFalse(RuntimeRetry.safe("POST", "https://runtime.test/v1/approvals/a1", new byte[0]));
        assertFalse(RuntimeRetry.safe("POST", "https://runtime.test/v1/voice?client_id=", new byte[1]));
        assertTrue(RuntimeRetry.safe("POST", "https://runtime.test/v1/voice?client_id=abc", new byte[1]));
    }
}
