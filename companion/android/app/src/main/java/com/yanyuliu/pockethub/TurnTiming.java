package com.yanyuliu.pockethub;

import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import java.util.function.Consumer;
import java.util.function.LongSupplier;

/**
 * Send-to-first-complete-bubble timing, on the phone's own uptime clock (docs: cross-stack
 * reliability, 3-second first response). Lines carry the random client key and times only,
 * never message text. A retry keeps the original start; only the first bubble of a turn counts.
 * "final" marks when the complete reply reached the phone, so delivery and drawing can be told apart.
 */
final class TurnTiming {
    static final String TAG = "XiaoyuTiming";
    private static final int MAX_OPEN = 64;
    private static final Map<String, Long> started = new LinkedHashMap<>();
    private static final Set<String> arrived = new HashSet<>();
    /** Where lines go; MainActivity connects it to logcat. Unit tests read it directly. */
    static volatile Consumer<String> sink = line -> {};
    /** The phone's uptime clock in milliseconds; MainActivity connects SystemClock. */
    static volatile LongSupplier clock = () -> 0;

    private TurnTiming() {}

    static synchronized void sent(String key, long uptimeMs) {
        if (key == null || key.isEmpty() || started.containsKey(key)) return;
        if (started.size() >= MAX_OPEN) arrived.remove(forget(started.keySet().iterator().next()));
        started.put(key, uptimeMs);
        sink.accept("send key=" + key + " t=" + uptimeMs);
    }

    /** The complete reply for this turn reached the phone (stream, polling or sync). */
    static synchronized void received(String key) {
        Long start = key == null ? null : started.get(key);
        if (start == null || !arrived.add(key)) return;
        long now = clock.getAsLong();
        sink.accept("final key=" + key + " t=" + now + " elapsed_ms=" + (now - start));
    }

    static synchronized void firstBubble(String key, long uptimeMs) {
        Long start = key == null ? null : started.remove(key);
        if (start == null) return;
        arrived.remove(key);
        sink.accept("bubble key=" + key + " t=" + uptimeMs + " elapsed_ms=" + (uptimeMs - start));
    }

    private static String forget(String key) { started.remove(key); return key; }

    static synchronized void reset() { started.clear(); arrived.clear(); }
}
