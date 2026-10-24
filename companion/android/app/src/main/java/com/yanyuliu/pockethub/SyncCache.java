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
        String conversation = "default";
        final double at;
        List<Attachment> attachments = new ArrayList<>();
        Presentation presentation;
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
    private int presentationVersion;
    private final Map<String, Map<String, Object>> tasks = new HashMap<>();
    private final Map<String, Map<String, Object>> messages = new HashMap<>();

    SyncCache(String conversation) { this.conversation = conversation; }

    String cursor() { return cursor; }
    String epoch() { return epoch; }
    void requirePresentation() { if (presentationVersion != 1) cursor = "0"; }

    Result apply(Map<String, Object> page) {
        String next = Json.string(page, "epoch");
        boolean replaced = !epoch.isEmpty() && !next.isEmpty() && !next.equals(epoch);
        if (replaced) {
            // Another set of records: ids like c1 may now mean something else.
            tasks.clear(); messages.clear();
        }
        if (!next.isEmpty()) epoch = next;
        presentationVersion = (int) Json.number(Json.object(page.get("presentation_policy")), "version");
        Set<String> touched = new LinkedHashSet<>();
        for (Object item : Json.list(page, "messages")) {
            Map<String, Object> message = Json.object(item);
            String id = Json.string(message, "id");
            if (id.isEmpty()) continue;
            Map<String, Object> known = messages.get(id);
            // Newer revisions win; an older or repeated one changes nothing.
            if (known != null && Json.number(known, "rev") > Json.number(message, "rev")) continue;
            if (known != null && terminal(known) && !terminal(message)) continue;
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

    /**
     * The applied records and the cursor that covers them, as one document: saving them together is what
     * lets a restart continue from the cursor without skipping records it never applied.
     */
    String save() {
        Map<String, Object> root = new HashMap<>();
        root.put("version", 1); root.put("conversation", conversation); root.put("epoch", epoch);
        root.put("cursor", cursor); root.put("presentation", presentationVersion);
        root.put("tasks", new ArrayList<>(tasks.values())); root.put("messages", new ArrayList<>(messages.values()));
        return UsageEvents.json(root);
    }

    /** A saved document back, or null when it is unreadable or from another format; then sync starts over. */
    static SyncCache restore(String json, String conversation) {
        Map<String, Object> root = json == null ? null : Json.parseObject(json);
        if (root == null || Json.number(root, "version") != 1 || !conversation.equals(Json.string(root, "conversation"))) return null;
        String cursor = Json.string(root, "cursor"), epoch = Json.string(root, "epoch");
        if (cursor.isEmpty() || epoch.isEmpty()) return null;
        SyncCache cache = new SyncCache(conversation);
        cache.cursor = cursor; cache.epoch = epoch;
        cache.presentationVersion = (int) Json.number(root, "presentation");
        for (Object item : Json.list(root, "tasks")) {
            Map<String, Object> task = Json.object(item);
            if (!Json.string(task, "id").isEmpty()) cache.tasks.put(Json.string(task, "id"), task);
        }
        for (Object item : Json.list(root, "messages")) {
            Map<String, Object> message = Json.object(item);
            if (!Json.string(message, "id").isEmpty()) cache.messages.put(Json.string(message, "id"), message);
        }
        return cache;
    }

    /** Everything held, as if it had just arrived: what a restored cache hands to the screens. */
    Result everything() {
        List<Card> cards = new ArrayList<>();
        for (String id : tasks.keySet()) {
            Card card = card(id);
            if (card != null) cards.add(card);
        }
        long seq = 0;
        try { seq = Long.parseLong(cursor); } catch (NumberFormatException opaque) { /* opaque cursors carry no number */ }
        return new Result(false, seq, false, cards, pending(), null);
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

    private static boolean terminal(Map<String, Object> message) {
        String status = Json.string(message, "status");
        return status.equals("final") || status.equals("failed") || status.equals("cancelled");
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
        if (message.get("presentation") instanceof Map) entry.put("presentation", message.get("presentation"));
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
            if (!conversation.equals("*") && !conversation.equals(Json.string(message, "conversation"))) continue;
            String task = Json.string(message, "task");
            if (final_(message) && !task.isEmpty() && tasks.containsKey(task)) continue;
            String status = Json.string(message, "status");
            if ((status.equals("failed") || status.equals("cancelled")) && !message.containsKey("presentation")) continue;
            String text = Json.string(message, "text");
            if (text.isEmpty()) text = Json.string(message, "partial");
            String scope = Json.string(message, "scope");
            Pending pending = new Pending(Json.string(message, "id"), task,
                    scope.isEmpty() ? "main" : scope, role(message), text, status,
                    Json.number(message, "created_at"));
            pending.conversation = Json.string(message, "conversation");
            pending.presentation = Presentation.from(message.get("presentation"));
            pending.attachments = Attachment.parse(message); result.add(pending);
        }
        result.sort(java.util.Comparator.comparingDouble((Pending item) -> item.at));
        return result;
    }
}
