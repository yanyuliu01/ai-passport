package com.yanyuliu.pockethub;

import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import java.util.EnumSet;
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
    private static final Map<String, EnumSet<Stage>> stages = new LinkedHashMap<>();
    /** Only predefined lifecycle names may be logged; never pass content or endpoints here. */
    enum Stage { BOOTSTRAP_WAIT, BOOTSTRAP_READY, INTENT_START, INTENT_READY,
        REQUEST_START, REQUEST_ACCEPTED, RELAY_QUEUED, RELAY_READY, RELAY_RESPONSE }
    /** Where lines go; MainActivity connects it to logcat. Unit tests read it directly. */
    static volatile Consumer<String> sink = line -> {};
    /** The phone's uptime clock in milliseconds; MainActivity connects SystemClock. */
    static volatile LongSupplier clock = () -> 0;

    private TurnTiming() {}

    static synchronized void sent(String key, long uptimeMs) {
        if (key == null || key.isEmpty() || started.containsKey(key)) return;
        if (started.size() >= MAX_OPEN) arrived.remove(forget(started.keySet().iterator().next()));
        started.put(key, uptimeMs);
        stages.put(key, EnumSet.noneOf(Stage.class));
        sink.accept("send key=" + key + " t=" + uptimeMs);
    }

    /** First occurrence of a phase on this phone's clock, correlated by the original client ID. */
    static synchronized void stage(String key, Stage stage) {
        Long start = key == null ? null : started.get(key);
        EnumSet<Stage> seen = key == null ? null : stages.get(key);
        if (start == null || stage == null || seen == null || !seen.add(stage)) return;
        long now = clock.getAsLong();
        sink.accept("phase key=" + key + " name=" + stage.name().toLowerCase(java.util.Locale.ROOT)
                + " t=" + now + " elapsed_ms=" + (now - start));
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
        stages.remove(key);
        sink.accept("bubble key=" + key + " t=" + uptimeMs + " elapsed_ms=" + (uptimeMs - start));
    }

    /** A view observer is needed only for replies sent during this process. */
    static synchronized boolean watching(String key) {
        return key != null && started.containsKey(key);
    }

    /** Do not mistake a hidden, empty, or failed response for visible assistant text. */
    static synchronized void visibleBubble(String key, long uptimeMs, boolean visible,
                                           boolean nonempty, boolean eligible) {
        if (visible && nonempty && eligible) firstBubble(key, uptimeMs);
    }

    private static String forget(String key) { started.remove(key); stages.remove(key); return key; }

    static synchronized void reset() { started.clear(); arrived.clear(); stages.clear(); }
}
