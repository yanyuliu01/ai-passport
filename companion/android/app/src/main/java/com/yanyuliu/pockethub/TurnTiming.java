package com.yanyuliu.pockethub;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.function.Consumer;

/**
 * Send-to-first-complete-bubble timing, on the phone's own uptime clock (docs: cross-stack
 * reliability, 3-second first response). One line per turn: the random client key and times only,
 * never message text. A retry keeps the original start; only the first bubble of a turn counts.
 */
final class TurnTiming {
    static final String TAG = "XiaoyouTiming";
    private static final int MAX_OPEN = 64;
    private static final Map<String, Long> started = new LinkedHashMap<>();
    /** Where lines go; MainActivity connects it to logcat. Unit tests read it directly. */
    static volatile Consumer<String> sink = line -> {};

    private TurnTiming() {}

    static synchronized void sent(String key, long uptimeMs) {
        if (key == null || key.isEmpty() || started.containsKey(key)) return;
        if (started.size() >= MAX_OPEN) started.remove(started.keySet().iterator().next());
        started.put(key, uptimeMs);
        sink.accept("send key=" + key + " t=" + uptimeMs);
    }

    static synchronized void firstBubble(String key, long uptimeMs) {
        Long start = key == null ? null : started.remove(key);
        if (start == null) return;
        sink.accept("bubble key=" + key + " t=" + uptimeMs + " elapsed_ms=" + (uptimeMs - start));
    }

    static synchronized void reset() { started.clear(); }
}
