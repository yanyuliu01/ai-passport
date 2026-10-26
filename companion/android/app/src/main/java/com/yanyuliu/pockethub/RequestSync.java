package com.yanyuliu.pockethub;

import java.io.IOException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Pending requests share the existing sync connection; no per-message network worker. */
final class RequestSync {
    private static final class Watch {
        final String id, conversation;
        final long generation;
        Map<String, String> value;
        String problem;
        boolean legacy;
        Watch(String id, String conversation, long generation, Map<String, String> value) {
            this.id = id; this.conversation = conversation; this.generation = generation;
            this.value = new HashMap<>(value);
        }
    }
    private final LinkedHashMap<String, Watch> watching = new LinkedHashMap<>();
    private static String key(String id, long generation) { return generation + "/" + id; }

    static void verifySource(Map<String, Object> page, String runtime, String subject) throws IOException {
        if ((!runtime.isEmpty() && !runtime.equals(Json.string(page, "runtime_id")))
                || (!subject.isEmpty() && !subject.equals(Json.string(page, "subject_id"))))
            throw new IOException("同步来源与已确认的 Runtime 不符，原记录保留");
    }

    synchronized void open(String id, String conversation, long generation, Map<String, String> accepted) {
        watching.put(key(id, generation), new Watch(id, conversation, generation, accepted));
    }

    synchronized void close(String id, long generation) { watching.remove(key(id, generation)); notifyAll(); }

    synchronized List<String> ids(long generation) {
        List<String> ids = new ArrayList<>();
        for (Watch watch : watching.values()) if (watch.generation == generation && ids.size() < 32) ids.add(watch.id);
        return ids;
    }

    synchronized boolean more(long generation) {
        int count = 0;
        for (Watch watch : watching.values()) if (watch.generation == generation && ++count > 32) return true;
        return false;
    }

    synchronized void apply(Map<String, Object> page, List<String> requested, long generation) throws IOException {
        // An older Runtime after a rollback may ignore the optional query.
        if (!(page.get("requests") instanceof List)) {
            for (String id : requested) {
                Watch watch = watching.get(key(id, generation));
                if (watch != null) watch.legacy = true;
            }
            notifyAll(); return;
        }
        for (Object raw : Json.list(page, "requests")) {
            Map<String, Object> value = Json.object(raw);
            String id = Json.string(value, "id"), name = key(id, generation);
            if (!requested.contains(id)) throw new IOException("同步返回了未请求的回复编号");
            Watch watch = watching.get(name);
            if (watch == null) continue;
            if (!watch.conversation.equals(Json.string(value, "conversation"))) throw new IOException("同步回复的对话不匹配");
            Map<String, String> next = Presentation.snapshot(UsageEvents.json(value));
            long rev = revision(next), known = revision(watch.value);
            if (rev < 0) throw new IOException("同步回复缺少有效修订号");
            next.put("rev", Long.toString(rev));
            if (rev > known && !(terminal(watch.value) && !terminal(next))) watch.value = next;
            // Keep deferred and overflow requests first in the next bounded page.
            watching.remove(name); watching.put(name, watch);
        }
        for (Object id : Json.list(page, "requests_missing")) {
            Watch watch = watching.get(key(String.valueOf(id), generation));
            if (watch != null && requested.contains(id)) watch.problem = "已受理请求的状态暂不可读，原编号保留；没有重新提交";
        }
        notifyAll();
    }

    synchronized Map<String, String> next(String id, long generation, String seen, long waitMs) throws IOException {
        Watch watch = watching.get(key(id, generation));
        if (watch == null) return null;
        if (watch.problem != null) throw new IOException(watch.problem);
        if (!watch.legacy && String.valueOf(watch.value.get("rev")).equals(String.valueOf(seen))) {
            try { wait(waitMs); } catch (InterruptedException interrupted) { Thread.currentThread().interrupt(); return null; }
        }
        watch = watching.get(key(id, generation));
        if (watch == null) return null;
        if (watch.problem != null) throw new IOException(watch.problem);
        if (watch.legacy) { Map<String, String> result = new HashMap<>(); result.put("legacy", "true"); return result; }
        return new HashMap<>(watch.value);
    }

    private static long revision(Map<String, String> value) {
        if (value == null || value.get("rev") == null) return -1;
        try { return new java.math.BigDecimal(value.getOrDefault("rev", "0")).longValueExact(); }
        catch (NumberFormatException | ArithmeticException invalid) { return -1; }
    }
    private static boolean terminal(Map<String, String> value) {
        String status = value.get("status");
        return "done".equals(status) || "failed".equals(status) || "cancelled".equals(status);
    }
}
