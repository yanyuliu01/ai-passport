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
        String line = UsageEvents.line("id1", "error", 1800000000000L,
                UsageEvents.client("phone-1", "0.20.1", "android-34"), "s1", fields);
        Map<String, Object> parsed = Json.parseObject(line);
        assertEquals("error", Json.string(parsed, "type"));
        assertEquals(1800000000000.0, Json.number(parsed, "at"), 0);
        Map<String, Object> client = Json.object(parsed.get("client"));
        assertEquals("android", Json.string(client, "kind"));
        assertEquals("phone-1", Json.string(client, "id"));
        assertEquals("android-34", Json.string(client, "platform"));
        Map<String, Object> got = Json.object(parsed.get("fields"));
        assertEquals("发送失败：\"超时\"\n重试", Json.string(got, "message"));
        assertEquals("x\\y", Json.list(got, "frames").get(1));
        Map<String, Object> body = Json.parseObject(UsageEvents.body(Arrays.asList(line, line)));
        assertEquals(2, Json.list(body, "events").size());
    }

    @Test public void clientIdIsKeptAcrossLaunches() throws Exception {
        java.io.File folder = java.nio.file.Files.createTempDirectory("usage").toFile();
        java.io.File idFile = new java.io.File(folder, "usage-client-id");
        String first = UsageEvents.stableId(idFile);
        assertTrue(first.matches("[a-f0-9]{32}"));
        assertEquals(first, UsageEvents.stableId(idFile));
        assertFalse(UsageEvents.client("", "", "").containsKey("id"));
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

    @Test public void controlNamesNeverCarryCountsOrVersions() {
        assertEquals("button:查看全部 # 条记录", "button:" + UsageEvents.controlName("查看全部 128 条记录"));
        assertEquals("v#.#.#", UsageEvents.controlName("v0.21.1"));
        String longLabel = new String(new char[50]).replace('\0', '字');
        assertEquals(40, UsageEvents.controlName(longLabel).length());
        assertEquals("connection", MainActivity.settingsName(0));
        assertEquals("notifications", MainActivity.settingsName(1));
        assertEquals("preferences", MainActivity.settingsName(2));
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
