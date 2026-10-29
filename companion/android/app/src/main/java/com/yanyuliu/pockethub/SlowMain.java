package com.yanyuliu.pockethub;

import java.util.function.Consumer;
import java.util.function.LongSupplier;

/**
 * Names main-thread messages that run long enough to drop frames. Off unless the debug property
 * debug.xiaoyu.slowmain is 1 (set over adb), so normal use pays nothing. Lines carry the handler
 * and callback class names Looper prints and the duration, never message content.
 */
final class SlowMain {
    static final long THRESHOLD_MS = 100;
    private final LongSupplier clock;
    private final Consumer<String> sink;
    private long began;
    private String what = "";

    SlowMain(LongSupplier clock, Consumer<String> sink) { this.clock = clock; this.sink = sink; }

    /** Looper's Printer: ">>>>> Dispatching to <handler> <callback>: <what>" then "<<<<< Finished to ...". */
    void println(String line) {
        if (line.startsWith(">>>>> Dispatching to ")) {
            began = clock.getAsLong();
            what = target(line.substring(">>>>> Dispatching to ".length()));
        } else if (line.startsWith("<<<<< Finished to ") && began > 0) {
            long spent = clock.getAsLong() - began;
            began = 0;
            if (spent >= THRESHOLD_MS) sink.accept("slow_main ms=" + spent + " at=" + what);
        }
    }

    /** Keep "Handler (class) {id} callback-class"; drop the trailing ": what" detail. */
    static String target(String rest) {
        int colon = rest.lastIndexOf(": ");
        String value = colon < 0 ? rest : rest.substring(0, colon);
        return value.replaceAll("\\{[0-9a-f]+\\}", "").replaceAll("@[0-9a-f]+", "").replaceAll("\\s+", " ").trim();
    }

    static void installIfEnabled() {
        String enabled = "";
        try {
            enabled = String.valueOf(Class.forName("android.os.SystemProperties")
                    .getMethod("get", String.class, String.class).invoke(null, "debug.xiaoyu.slowmain", ""));
        } catch (ReflectiveOperationException | RuntimeException ignored) { }
        if (!enabled.equals("1")) return;
        SlowMain watch = new SlowMain(android.os.SystemClock::uptimeMillis,
                line -> android.util.Log.i(TurnTiming.TAG, line));
        android.os.Looper.getMainLooper().setMessageLogging(watch::println);
    }
}
