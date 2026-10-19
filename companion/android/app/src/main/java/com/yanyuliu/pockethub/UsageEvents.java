package com.yanyuliu.pockethub;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * Usage events for the runtime's auto-iteration pipeline (docs/development/auto-iteration):
 * which screen, how long, errors shown, crashes, and repeated taps on one spot. Never message
 * text, typed input or screenshots. Events are appended to a small local file and sent in
 * batches to POST /v1/app/events; a batch is removed only after the runtime accepted it, so a
 * crash or a lost connection keeps them for the next try.
 */
final class UsageEvents {
    static final int MAX_STORED = 500;
    static final int BATCH = 50;
    private static final Object LOCK = new Object();
    private static File file;
    private static String version = "";
    private static final String SESSION = UUID.randomUUID().toString().replace("-", "").substring(0, 16);
    private static volatile boolean sending;
    private static boolean crashHandlerInstalled;

    private UsageEvents() {}

    static void init(File folder, String appVersion) {
        synchronized (LOCK) { file = new File(folder, "usage-events.jsonl"); version = appVersion == null ? "" : appVersion; }
    }

    /** Records a crash before the process dies, then hands it to the previous handler. */
    static synchronized void installCrashHandler() {
        if (crashHandlerInstalled) return;  // the activity can be created again
        crashHandlerInstalled = true;
        Thread.UncaughtExceptionHandler previous = Thread.getDefaultUncaughtExceptionHandler();
        Thread.setDefaultUncaughtExceptionHandler((thread, error) -> {
            try { record("crash", crashFields(thread.getName(), error)); } catch (Throwable ignored) { }
            if (previous != null) previous.uncaughtException(thread, error);
        });
    }

    static Map<String, Object> crashFields(String thread, Throwable error) {
        Map<String, Object> fields = new LinkedHashMap<>();
        String message = error.getMessage();
        fields.put("exception", error.getClass().getName() + (message == null ? "" : ": " + message));
        List<String> frames = new ArrayList<>();
        for (StackTraceElement frame : error.getStackTrace()) {
            if (frames.size() >= 8) break;
            frames.add(frame.toString());
        }
        fields.put("frames", frames);
        fields.put("thread", thread);
        return fields;
    }

    static void screen(String name, long millis) {
        Map<String, Object> fields = new LinkedHashMap<>();
        fields.put("name", name); fields.put("ms", millis);
        record("screen", fields);
    }

    static void error(String screen, String message) {
        Map<String, Object> fields = new LinkedHashMap<>();
        fields.put("screen", screen); fields.put("message", message);
        record("error", fields);
    }

    static void rageTap(String screen, String region, int count) {
        Map<String, Object> fields = new LinkedHashMap<>();
        fields.put("screen", screen); fields.put("region", region); fields.put("count", count);
        record("rage_tap", fields);
    }

    static void record(String type, Map<String, Object> fields) {
        String line = line(UUID.randomUUID().toString().replace("-", ""), type, System.currentTimeMillis(),
                version, SESSION, fields);
        synchronized (LOCK) {
            if (file == null) return;
            try {
                List<String> lines = read(file);
                lines.add(line);
                if (lines.size() > MAX_STORED) lines = new ArrayList<>(lines.subList(lines.size() - MAX_STORED, lines.size()));
                write(file, lines);
            } catch (IOException ignored) {
                // Usage events are best effort; never disturb the app for them.
            }
        }
    }

    static boolean pending() {
        synchronized (LOCK) { return file != null && file.length() > 0; }
    }

    /** Sends one batch in the background; only one send runs at a time. */
    static void flush(RuntimeClient.Target target) {
        if (target == null || sending || !pending()) return;
        sending = true;
        new Thread(() -> {
            try {
                List<String> batch;
                synchronized (LOCK) { batch = first(read(file), BATCH); }
                if (batch.isEmpty()) return;
                RuntimeClient.postAppEvents(target, body(batch));
                synchronized (LOCK) {
                    List<String> lines = read(file);
                    write(file, dropSent(lines, batch));
                }
            } catch (IOException ignored) {
                // Offline or an old runtime without /v1/app/events: keep them and try later.
            } finally {
                sending = false;
            }
        }, "usage-events").start();
    }

    // ---- pure helpers (unit-tested) ----

    static String line(String id, String type, long at, String version, String session, Map<String, Object> fields) {
        Map<String, Object> event = new LinkedHashMap<>();
        event.put("id", id); event.put("type", type); event.put("at", at);
        event.put("app_version", version); event.put("session", session);
        event.putAll(fields);
        return json(event);
    }

    static String body(List<String> lines) {
        return "{\"events\":[" + String.join(",", lines) + "]}";
    }

    static List<String> first(List<String> lines, int count) {
        return new ArrayList<>(lines.subList(0, Math.min(count, lines.size())));
    }

    /** Removes the sent lines from the front; lines recorded meanwhile stay. */
    static List<String> dropSent(List<String> lines, List<String> sent) {
        int prefix = 0;
        while (prefix < sent.size() && prefix < lines.size() && lines.get(prefix).equals(sent.get(prefix))) prefix++;
        if (prefix == sent.size()) return new ArrayList<>(lines.subList(prefix, lines.size()));
        // The file was trimmed meanwhile (over MAX_STORED): drop whatever sent lines are still there.
        List<String> rest = new ArrayList<>(lines);
        rest.removeAll(sent);
        return rest;
    }

    static String json(Object value) {
        if (value == null) return "null";
        if (value instanceof Number || value instanceof Boolean) return value.toString();
        if (value instanceof Map) {
            StringBuilder out = new StringBuilder("{");
            for (Map.Entry<?, ?> entry : ((Map<?, ?>) value).entrySet()) {
                if (out.length() > 1) out.append(',');
                out.append(json(String.valueOf(entry.getKey()))).append(':').append(json(entry.getValue()));
            }
            return out.append('}').toString();
        }
        if (value instanceof List) {
            StringBuilder out = new StringBuilder("[");
            for (Object item : (List<?>) value) {
                if (out.length() > 1) out.append(',');
                out.append(json(item));
            }
            return out.append(']').toString();
        }
        String text = value.toString();
        StringBuilder out = new StringBuilder("\"");
        for (int index = 0; index < text.length(); index++) {
            char c = text.charAt(index);
            switch (c) {
                case '"': out.append("\\\""); break;
                case '\\': out.append("\\\\"); break;
                case '\n': out.append("\\n"); break;
                case '\r': out.append("\\r"); break;
                case '\t': out.append("\\t"); break;
                default:
                    if (c < 0x20) out.append(String.format("\\u%04x", (int) c)); else out.append(c);
            }
        }
        return out.append('"').toString();
    }

    private static List<String> read(File source) throws IOException {
        if (source == null || !source.exists()) return new ArrayList<>();
        List<String> lines = new ArrayList<>();
        for (String line : new String(Files.readAllBytes(source.toPath()), StandardCharsets.UTF_8).split("\n")) {
            if (!line.trim().isEmpty()) lines.add(line);
        }
        return lines;
    }

    private static void write(File target, List<String> lines) throws IOException {
        File temporary = new File(target.getPath() + ".tmp");
        try (FileOutputStream out = new FileOutputStream(temporary)) {
            out.write((lines.isEmpty() ? "" : String.join("\n", lines) + "\n").getBytes(StandardCharsets.UTF_8));
            out.getFD().sync();
        }
        if (!temporary.renameTo(target)) throw new IOException("rename failed");
    }

    /** Several quick taps on the same spot: the owner probably expected something to happen. */
    static final class RageTap {
        static final int TAPS = 4;
        static final long WINDOW_MS = 2000;
        private final float radius;
        private final List<double[]> taps = new ArrayList<>();

        RageTap(float radiusPixels) { radius = radiusPixels; }

        /** Returns the tap count when this tap completes a burst, otherwise 0. */
        int tap(float x, float y, long at) {
            // Keep every recent tap; a tap elsewhere in between does not break a burst here.
            List<double[]> recent = new ArrayList<>(), near = new ArrayList<>();
            for (double[] tap : taps) {
                if (at - (long) tap[2] > WINDOW_MS) continue;
                recent.add(tap);
                if (Math.hypot(tap[0] - x, tap[1] - y) <= radius) near.add(tap);
            }
            double[] current = {x, y, at};
            recent.add(current); near.add(current);
            taps.clear(); taps.addAll(recent);
            if (near.size() < TAPS) return 0;
            taps.removeAll(near);
            return near.size();
        }

        /** A coarse 3 x 6 grid cell, "column,row", so the runtime can group without exact positions. */
        static String region(float x, float y, int width, int height) {
            int column = Math.max(0, Math.min(2, (int) (x * 3 / Math.max(1, width))));
            int row = Math.max(0, Math.min(5, (int) (y * 6 / Math.max(1, height))));
            return column + "," + row;
        }
    }
}
