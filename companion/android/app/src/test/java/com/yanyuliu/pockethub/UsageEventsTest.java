package com.yanyuliu.pockethub;

import org.junit.Test;
import static org.junit.Assert.*;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public class UsageEventsTest {
    @Test public void eventLineIsValidJsonWithEscapes() {
        Map<String, Object> fields = new LinkedHashMap<>();
        fields.put("screen", "chat");
        fields.put("message", "发送失败：\"超时\"\n重试");
        fields.put("frames", Arrays.asList("a.b(C.java:1)", "x\\y"));
        String line = UsageEvents.line("id1", "error", 1800000000000L, "0.20.1", "s1", fields);
        Map<String, Object> parsed = Json.parseObject(line);
        assertEquals("error", Json.string(parsed, "type"));
        assertEquals("发送失败：\"超时\"\n重试", Json.string(parsed, "message"));
        assertEquals(1800000000000.0, Json.number(parsed, "at"), 0);
        assertEquals("x\\y", Json.list(parsed, "frames").get(1));
        Map<String, Object> body = Json.parseObject(UsageEvents.body(Arrays.asList(line, line)));
        assertEquals(2, Json.list(body, "events").size());
    }

    @Test public void crashFieldsKeepTheTopEightFrames() {
        Exception error = new IllegalStateException("card gone");
        StackTraceElement[] frames = new StackTraceElement[12];
        for (int i = 0; i < frames.length; i++) frames[i] = new StackTraceElement("C", "m" + i, "C.java", i);
        error.setStackTrace(frames);
        Map<String, Object> fields = UsageEvents.crashFields("main", error);
        assertEquals("java.lang.IllegalStateException: card gone", fields.get("exception"));
        assertEquals(8, ((List<?>) fields.get("frames")).size());
        assertEquals("C.m0(C.java:0)", ((List<?>) fields.get("frames")).get(0));
    }

    @Test public void sentBatchIsDroppedButNewerLinesStay() {
        List<String> lines = new ArrayList<>(Arrays.asList("a", "b", "c", "d"));
        List<String> sent = UsageEvents.first(lines, 2);
        assertEquals(Arrays.asList("a", "b"), sent);
        assertEquals(Arrays.asList("c", "d"), UsageEvents.dropSent(lines, sent));
        // The file was trimmed while sending: whatever sent lines remain are removed.
        assertEquals(Arrays.asList("c", "d"), UsageEvents.dropSent(Arrays.asList("b", "c", "d"), sent));
    }

    @Test public void fourQuickTapsOnOneSpotAreARageTap() {
        UsageEvents.RageTap rage = new UsageEvents.RageTap(50);
        assertEquals(0, rage.tap(100, 100, 1000));
        assertEquals(0, rage.tap(110, 105, 1300));
        assertEquals(0, rage.tap(400, 400, 1400));   // elsewhere: not counted
        assertEquals(0, rage.tap(105, 100, 1600));
        assertEquals(4, rage.tap(100, 110, 1900));
        assertEquals(0, rage.tap(100, 100, 2000));   // starts over after a burst
        UsageEvents.RageTap slow = new UsageEvents.RageTap(50);
        for (int i = 0; i < 6; i++) assertEquals(0, slow.tap(100, 100, 1800000000000L + i * 1000L));
        assertEquals("0,0", UsageEvents.RageTap.region(10, 10, 1080, 2400));
        assertEquals("2,5", UsageEvents.RageTap.region(1079, 2399, 1080, 2400));
    }
}
