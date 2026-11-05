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

    @Test public void firstVisibleBubbleIgnoresHiddenEmptyAndFailedRows() {
        TurnTiming.sent("k", 1000);
        assertTrue(TurnTiming.watching("k"));
        TurnTiming.visibleBubble("k", 1200, false, true, true);  // queued GONE bubble
        TurnTiming.visibleBubble("k", 1300, true, false, true);  // no assistant body
        TurnTiming.visibleBubble("k", 1400, true, true, false);  // error is not a reply
        assertTrue(TurnTiming.watching("k"));
        assertEquals(List.of("send key=k t=1000"), lines);
        TurnTiming.visibleBubble("k", 2500, true, true, true);
        TurnTiming.visibleBubble("k", 3000, true, true, true);
        assertFalse(TurnTiming.watching("k"));
        assertEquals(List.of("send key=k t=1000", "bubble key=k t=2500 elapsed_ms=1500"), lines);
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

    @Test public void stagesUseOneOriginalClockAndDoNotLogUnknownOrDuplicateTurns() {
        TurnTiming.stage("missing", TurnTiming.Stage.RELAY_READY);
        TurnTiming.sent("k1", 1000);
        now = 1100; TurnTiming.stage("k1", TurnTiming.Stage.INTENT_START);
        now = 1250; TurnTiming.stage("k1", TurnTiming.Stage.INTENT_READY);
        now = 1400; TurnTiming.stage("k1", TurnTiming.Stage.INTENT_READY);
        now = 1500; TurnTiming.stage("k1", TurnTiming.Stage.RELAY_QUEUED);
        TurnTiming.firstBubble("k1", 2000);
        now = 2100; TurnTiming.stage("k1", TurnTiming.Stage.RELAY_RESPONSE);
        assertEquals(List.of("send key=k1 t=1000",
                "phase key=k1 name=intent_start t=1100 elapsed_ms=100",
                "phase key=k1 name=intent_ready t=1250 elapsed_ms=250",
                "phase key=k1 name=relay_queued t=1500 elapsed_ms=500",
                "bubble key=k1 t=2000 elapsed_ms=1000"), lines);
    }
}
