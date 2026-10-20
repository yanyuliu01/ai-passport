package com.yanyuliu.pockethub;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * One Runtime's state from /v1/sync (see docs/development/unified-message-model): its tasks and
 * messages by id, rebuilt into the cards the screens already draw. A card carries only final
 * messages, as /v1/feed did; messages still queued or being written are handed out separately so
 * other phones can show them while Xiaoyou is still answering.
 *
 * The cursor is kept as the string the Runtime gave and sent back unchanged. A page is applied
 * whole before the cursor moves. Not thread-safe: one sync loop owns it.
 */
final class SyncCache {
    /** A message not on any card yet: queued, being transcribed or streamed. */
    static final class Pending {
        final String id, task, scope, role, text, status;
        final double at;
        Pending(String id, String task, String scope, String role, String text, String status, double at) {
            this.id = id; this.task = task; this.scope = scope; this.role = role;
            this.text = text; this.status = status; this.at = at;
        }
    }

    static final class Result {
        /** The Runtime's records were replaced (another epoch): cards from before are not valid. */
        final boolean replaced;
        final long seq;
        final boolean more;
        final List<Card> cards;
        final List<Pending> pending;
        final Map<String, Object> usage;
        Result(boolean replaced, long seq, boolean more, List<Card> cards, List<Pending> pending,
               Map<String, Object> usage) {
            this.replaced = replaced; this.seq = seq; this.more = more; this.cards = cards;
            this.pending = pending; this.usage = usage;
        }
    }

    private final String conversation;
    private String epoch = "";
    private String cursor = "0";
    private final Map<String, Map<String, Object>> tasks = new HashMap<>();
    private final Map<String, Map<String, Object>> messages = new HashMap<>();

    SyncCache(String conversation) { this.conversation = conversation; }

    String cursor() { return cursor; }
    String epoch() { return epoch; }

    Result apply(Map<String, Object> page) {
        String next = Json.string(page, "epoch");
        boolean replaced = !epoch.isEmpty() && !next.isEmpty() && !next.equals(epoch);
        if (replaced) {
            // Another set of records: ids like c1 may now mean something else.
            tasks.clear(); messages.clear();
        }
        if (!next.isEmpty()) epoch = next;
        Set<String> touched = new LinkedHashSet<>();
        for (Object item : Json.list(page, "messages")) {
            Map<String, Object> message = Json.object(item);
            String id = Json.string(message, "id");
            if (id.isEmpty()) continue;
            Map<String, Object> known = messages.get(id);
            // Newer revisions win; an older or repeated one changes nothing.
            if (known != null && Json.number(known, "rev") > Json.number(message, "rev")) continue;
            messages.put(id, message);
            String task = Json.string(message, "task");
            if (!task.isEmpty()) touched.add(task);
        }
        for (Object item : Json.list(page, "tasks")) {
            Map<String, Object> task = Json.object(item);
            String id = Json.string(task, "id");
            if (id.isEmpty()) continue;
            Map<String, Object> known = tasks.get(id);
            if (known != null && Json.number(known, "seq") > Json.number(task, "seq")) continue;
            tasks.put(id, task);
            touched.add(id);
        }
        trimActivity(touched);
        List<Card> cards = new ArrayList<>();
        for (String id : touched) {
            Card card = card(id);
            if (card != null) cards.add(card);
        }
        String seq = cursorOf(page.get("seq"));
        if (!seq.isEmpty()) cursor = seq;
        Map<String, Object> usage = page.get("usage") instanceof Map ? Json.object(page.get("usage")) : null;
        return new Result(replaced, (long) Json.number(page, "seq"), Json.flag(page, "more"), cards,
                pending(), usage);
    }

    private static String cursorOf(Object value) {
        if (value instanceof Double) return Long.toString(((Double) value).longValue());
        if (value instanceof Number) return Long.toString(((Number) value).longValue());
        return value instanceof String ? (String) value : "";
    }

    private void trimActivity(Set<String> touched) {
        List<Map<String, Object>> activity = new ArrayList<>();
        for (Map<String, Object> message : messages.values())
            if (Json.string(message, "kind").equals("activity")) activity.add(message);
        if (activity.size() <= 2000) return;
        activity.sort(java.util.Comparator.comparingDouble((Map<String, Object> item) -> Json.number(item, "created_at"))
                .thenComparing(item -> Json.string(item, "id")));
        for (int index = 0; index < activity.size() - 2000; index++) {
            Map<String, Object> item = activity.get(index);
            messages.remove(Json.string(item, "id"));
            touched.add(Json.string(item, "task"));
        }
    }

    /** The task as a card: its fields plus its final member messages as entries. */
    Card card(String id) {
        Map<String, Object> task = tasks.get(id);
        if (task == null) return null;
        Map<String, Object> raw = new HashMap<>(task);
        List<Object> entries = new ArrayList<>();
        for (Object member : Json.list(task, "members")) {
            if (!(member instanceof String)) continue;
            Map<String, Object> message = messages.get(member);
            if (message == null || !final_(message)) continue;
            entries.add(entry(message));
        }
        raw.put("entries", entries);
        List<Map<String, Object>> activity = new ArrayList<>();
        long seq = (long) Json.number(task, "seq");
        for (Map<String, Object> message : messages.values()) {
            if (id.equals(Json.string(message, "task")) && Json.string(message, "kind").equals("activity")) {
                Map<String, Object> event = entry(message);
                event.put("kind", Json.string(message, "phase"));
                activity.add(event);
                seq = Math.max(seq, (long) Json.number(message, "seq"));
            }
        }
        activity.sort(java.util.Comparator.comparingDouble((Map<String, Object> event) -> Json.number(event, "at"))
                .thenComparing(event -> Json.string(event, "id")));
        raw.put("activity", activity);
        raw.put("seq", (double) seq);
        return Card.from(raw);
    }

    private static boolean final_(Map<String, Object> message) {
        String status = Json.string(message, "status");
        return status.isEmpty() || status.equals("final");
    }

    static String role(Map<String, Object> message) {
        Object author = message.get("author");
        String kind = author instanceof Map ? Json.string(Json.object(author), "kind") : "";
        return kind.equals("owner") ? "you" : "xiaoyou";
    }

    /** The card entry the Runtime would have put on the card for this message (see message_log.py). */
    private static Map<String, Object> entry(Map<String, Object> message) {
        Map<String, Object> entry = new HashMap<>();
        entry.put("id", Json.string(message, "id"));
        entry.put("role", role(message));
        entry.put("text", Json.string(message, "text"));
        entry.put("at", Json.number(message, "created_at"));
        entry.put("scope", Json.string(message, "scope"));
        String kind = Json.string(message, "kind");
        entry.put("kind", kind.equals("result") ? "worker_result" : kind);
        Object author = message.get("author");
        Map<String, Object> who = author instanceof Map ? Json.object(author) : new HashMap<>();
        String by = Json.string(who, "kind");
        entry.put("author", by.equals("agent") ? Json.string(who, "name") : by.equals("owner") ? "you"
                : by.equals("xiaoyou") ? "xiaoyou" : "");
        if (message.get("attachments") instanceof List) entry.put("attachments", message.get("attachments"));
        return entry;
    }

    /**
     * Messages of this conversation not shown on a card yet, oldest first: still queued or being
     * written, or accepted before the Runtime filed them under a task.
     */
    private List<Pending> pending() {
        List<Pending> result = new ArrayList<>();
        for (Map<String, Object> message : messages.values()) {
            if (Json.string(message, "kind").equals("activity")) continue;
            if (!conversation.equals(Json.string(message, "conversation"))) continue;
            String task = Json.string(message, "task");
            if (final_(message) && !task.isEmpty() && tasks.containsKey(task)) continue;
            String status = Json.string(message, "status");
            if (status.equals("failed") || status.equals("cancelled")) continue;
            String text = Json.string(message, "text");
            if (text.isEmpty()) text = Json.string(message, "partial");
            String scope = Json.string(message, "scope");
            result.add(new Pending(Json.string(message, "id"), task,
                    scope.isEmpty() ? "main" : scope, role(message), text, status,
                    Json.number(message, "created_at")));
        }
        result.sort(java.util.Comparator.comparingDouble((Pending item) -> item.at));
        return result;
    }
}
