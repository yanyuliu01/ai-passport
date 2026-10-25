package com.yanyuliu.pockethub;

import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import java.util.ArrayList;
import java.util.List;
import static org.junit.Assert.*;

public class TurnTimingTest {
    private final List<String> lines = new ArrayList<>();

    private long now;

    @Before public void capture() { TurnTiming.reset(); TurnTiming.sink = lines::add; TurnTiming.clock = () -> now; }
    @After public void release() { TurnTiming.reset(); TurnTiming.sink = line -> {}; TurnTiming.clock = () -> 0; }

    @Test public void arrivalIsReportedOnceBetweenSendAndBubble() {
        TurnTiming.sent("k1", 1000);
        now = 1800; TurnTiming.received("k1");
        now = 1900; TurnTiming.received("k1");  // Stream and sync may both deliver it.
        TurnTiming.firstBubble("k1", 2100);
        assertEquals(List.of("send key=k1 t=1000", "final key=k1 t=1800 elapsed_ms=800",
                "bubble key=k1 t=2100 elapsed_ms=1100"), lines);
    }

    @Test public void aSyncDeliveredReplyReportsArrivalUnderTheSendKey() {
        ChatHistory history = new ChatHistory(); history.begin("k2", null, "hello", 10);
        TurnTiming.sent("k2", 100);
        history.accepted("k2", "server");
        SyncCache.Pending reply = new SyncCache.Pending("server:reply", "", "main", "xiaoyou", "OK", "final", 11);
        reply.presentation = Presentation.restore("{\"version\":1,\"complete\":true,\"status\":\"final\",\"placement\":\"chat\","
                + "\"blocks\":[{\"text\":\"OK\",\"complete\":true}]}");
        now = 700; history.pending(reply);
        assertEquals("final key=k2 t=700 elapsed_ms=600", lines.get(lines.size() - 1));
    }

    @Test public void firstBubbleReportsElapsedOnceWithKeyAndTimesOnly() {
        TurnTiming.sent("k1", 1000);
        TurnTiming.firstBubble("k1", 2350);
        TurnTiming.firstBubble("k1", 2900);  // Later bubbles of the same reply do not count.
        assertEquals(List.of("send key=k1 t=1000", "bubble key=k1 t=2350 elapsed_ms=1350"), lines);
    }

    @Test public void retryKeepsTheOriginalStart() {
        TurnTiming.sent("k1", 1000);
        TurnTiming.sent("k1", 5000);
        TurnTiming.firstBubble("k1", 6000);
        assertEquals("bubble key=k1 t=6000 elapsed_ms=5000", lines.get(lines.size() - 1));
    }

    @Test public void bubblesWithoutAStartAreIgnored() {
        TurnTiming.firstBubble("restored-history", 10);
        TurnTiming.firstBubble(null, 10);
        TurnTiming.sent("", 10);
        TurnTiming.sent(null, 10);
        assertTrue(lines.isEmpty());
    }

    @Test public void openTurnsAreBounded() {
        for (int i = 0; i < 70; i++) TurnTiming.sent("k" + i, i);
        lines.clear();
        TurnTiming.firstBubble("k0", 100);  // The oldest turns were dropped.
        TurnTiming.firstBubble("k69", 100);
        assertEquals(List.of("bubble key=k69 t=100 elapsed_ms=31"), lines);
    }
}
