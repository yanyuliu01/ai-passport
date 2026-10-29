package com.yanyuliu.pockethub;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** A persistent conversation timeline independent of card lifetime and execution state. */
final class ChatHistory {
    static final class Message {
        String id, card, scope, role, text, status, kind;
        String clientId;
        String conversation = "default";
        List<Attachment> attachments = new ArrayList<>();
        Presentation presentation;
        double at;
        boolean synced;
        Message(String id, String card, String scope, String role, String text, String status,
                String kind, double at, boolean synced) {
            this.id = id; this.card = card; this.scope = scope; this.role = role; this.text = text;
            this.status = status; this.kind = kind; this.at = at; this.synced = synced;
            this.clientId = id.endsWith(":you") ? id.substring(0, id.length() - 4)
                    : id.endsWith(":reply") ? id.substring(0, id.length() - 6) : "";
        }
    }

    private final Map<String, Message> messages = new LinkedHashMap<>();

    private final java.util.Set<String> erased = new java.util.HashSet<>();
    boolean eraseConversation(String id) {
        boolean changed = erased.add(id);
        return messages.values().removeIf(message -> message.conversation.equals(id)) || changed;
    }

    void begin(String id, String card, String text, double at) {
        String target = card == null ? "" : card;
        String scope = target.isEmpty() ? "main" : "task";
        put(new Message(id + ":you", target, scope, "you", text, "sending", "user", at, false));
        put(new Message(id + ":reply", target, scope, "xiaoyou", "", "thinking", "reply", at, false));
    }

    void scope(String id, String conversation, boolean main) {
        for (String suffix : new String[]{":you", ":reply"}) {
            Message m = messages.get(id + suffix);
            if (m != null) { m.conversation = conversation; if (main) m.scope = "main"; if(erased.contains(conversation))messages.remove(id+suffix); }
        }
    }

    void images(String id, List<Attachment> items) {
        Message message = messages.get(id + ":you");
        if (message != null && items != null && !items.isEmpty())
            message.attachments = java.util.Collections.unmodifiableList(new java.util.ArrayList<>(items));
    }

    private void put(Message message) { if(!erased.contains(message.conversation)) messages.put(message.id, message); }

    void importTurn(ChatTurn turn) {
        begin(turn.id, null, turn.text, turn.at);
        finish(turn.id, null, turn.reply, false, null);
        messages.get(turn.id + ":you").kind = "legacy";
        messages.get(turn.id + ":reply").kind = "legacy";
    }

    String retry(String id) {
        Message message = messages.get(id);
        if (message == null || message.clientId.isEmpty()) return null;
        String client = message.clientId;
        String base = id.endsWith(":you") ? id.substring(0, id.length() - 4) : id;
        messages.remove(base + ":you"); messages.remove(base + ":reply");
        return client;
    }

    void accepted(String local, String remote) {
        for (String role : new String[]{"you", "reply"}) {
            Message message = messages.remove(local + ":" + role);
            if (message == null) continue;
            message.id = remote + ":" + role;
            message.status = role.equals("you") ? "sent" : "thinking";
            put(message);
        }
    }

    void heard(String id, String text) {
        Message message = messages.get(id + ":you");
        if (message != null) message.text = text;
    }

    void streaming(String id, String text) {
        Message message = messages.get(id + ":reply");
        if (message != null && !(message.synced && message.status.equals("done"))) { message.text = text; message.status = text.isEmpty() ? "thinking" : "streaming"; }
    }

    void presentation(String id, Presentation value) {
        Message message = messages.get(id + ":reply");
        if (message == null || value == null) return;
        if (message.presentation != null && message.presentation.complete() && !value.complete()) return;
        message.presentation = value;
        if (value.complete()) TurnTiming.received(message.clientId);
    }

    void finish(String id, String card, String text, boolean failed, Card snapshot) {
        Message user = messages.get(id + ":you"), reply = messages.get(id + ":reply");
        if (user != null) { user.status = failed ? "failed" : "done"; user.card = card == null ? user.card : card; }
        if (reply != null) { reply.text = text; reply.status = failed ? "failed" : "done"; reply.card = card == null ? reply.card : card; }
        if (reply != null && !failed) TurnTiming.received(reply.clientId);
        if (snapshot == null || failed) return;
        // Runtimes that number card entries use the message ids themselves: ingest matches by id.
        for (Card.Entry entry : snapshot.entries)
            if (!entry.id.isEmpty() && (entry.id.equals(id + ":you") || entry.id.equals(id + ":reply"))) return;
        // Older runtimes: the message response identifies the card; bind its returned pair by text.
        boolean boundReply = false;
        for (int index = snapshot.entries.size() - 1; index >= 0; index--) {
            Card.Entry entry = snapshot.entries.get(index);
            if (!boundReply && reply != null && entry.role.equals("xiaoyou") && entry.text.equals(text)) {
                bind(reply, snapshot, entry, index); boundReply = true;
            } else if (boundReply && user != null && entry.role.equals("you") && entry.text.equals(user.text)) {
                bind(user, snapshot, entry, index); break;
            }
        }
    }

    private void bind(Message message, Card card, Card.Entry entry, int index) {
        messages.remove(message.id);
        messages.remove(legacyKey(card, entry, index));
        message.id = key(card, entry, index); message.synced = true;
        // Keep the local bubble anchored at send time, even if server entries arrive much later.
        put(message);
    }

    private static String legacyKey(Card card, Card.Entry entry, int index) {
        return card.id + ":entry:" + (entry.at > 0 ? Double.toString(entry.at) : "index" + index)
                + ":" + entry.role;
    }

    private static String key(Card card, Card.Entry entry, int index) {
        return entry.id.isEmpty() ? legacyKey(card, entry, index) : entry.id;
    }

    /** Whether the card can change the stored timeline; desktop activity updates never do. */
    boolean ingest(Card card) {
        if (card.monitoringOnly) return false;
        // Older phone caches lack a card id. Match complete pairs, time and one-to-one ownership.
        for (int index = 0; index + 1 < card.entries.size(); index++) {
            Card.Entry user = card.entries.get(index), reply = card.entries.get(index + 1);
            if (!user.role.equals("you") || !reply.role.equals("xiaoyou") || user.at <= 0) continue;
            Message best = null;
            for (Message candidate : messages.values()) {
                if (!candidate.kind.equals("legacy") || !candidate.role.equals("you") || !candidate.card.isEmpty()
                        || !candidate.text.equals(user.text) || Math.abs(candidate.at - user.at) > 120) continue;
                Message answer = messages.get(candidate.id.substring(0, candidate.id.length() - 4) + ":reply");
                if (answer != null && answer.text.equals(reply.text)
                        && (best == null || Math.abs(candidate.at - user.at) < Math.abs(best.at - user.at))) best = candidate;
            }
            if (best != null) {
                Message answer = messages.get(best.id.substring(0, best.id.length() - 4) + ":reply");
                best.card = card.id; answer.card = card.id;
                bind(best, card, user, index); bind(answer, card, reply, index + 1);
            }
        }
        for (int index = 0; index < card.entries.size(); index++) {
            Card.Entry entry = card.entries.get(index);
            if (entry.kind.equals("worker_result") && (entry.presentation == null || !entry.presentation.inChat())) continue;
            String id = key(card, entry, index);
            Message known = messages.get(id);
            if (known == null && !entry.id.isEmpty()) known = messages.remove(legacyKey(card, entry, index));
            String scope = !entry.scope.isEmpty() ? entry.scope : known != null ? known.scope : "main";
            String kind = entry.kind;
            boolean last = index == card.entries.size() - 1;
            if (kind.isEmpty() && last && AppPresentation.dispatched(card) && !card.active()
                    && !entry.role.equals("you")) kind = "summary";
            String text = entry.kind.isEmpty() && kind.equals("summary") && !card.brief.isEmpty() && entry.text.length() > 800
                    ? card.brief : entry.text;
            if (known == null && entry.id.isEmpty()) {
                // Compatibility: bind an already acknowledged local pair only within its returned card.
                for (Message candidate : new ArrayList<>(messages.values())) {
                    if (!candidate.synced && candidate.status.equals("done") && candidate.card.equals(card.id)
                            && candidate.role.equals(entry.role) && candidate.text.equals(entry.text)) {
                        known = candidate; messages.remove(candidate.id); scope = candidate.scope; break;
                    }
                }
            }
            Message next = new Message(id, card.id, scope, entry.role, text, "done", kind,
                    known != null ? known.at : entry.at > 0 ? entry.at : card.createdAt + index * .001, true);
            next.conversation = card.conversation;
            next.attachments = entry.attachments;
            next.presentation = entry.presentation != null ? entry.presentation : known == null ? null : known.presentation;
            if (known != null) next.clientId = known.clientId;
            put(next);
        }
        String partialId = card.id + ":summary-live";
        if (!card.summaryPartial.isEmpty() && card.active() && !liveSummary(card.id)) {
            Message previous = messages.get(partialId);
            Message summary = new Message(partialId, card.id, "main", "xiaoyou", card.summaryPartial, "streaming",
                    "summary", previous == null ? System.currentTimeMillis() / 1000.0 : previous.at, true);
            summary.conversation = card.conversation; put(summary);
        } else messages.remove(partialId);
        return true;
    }

    /**
     * A message another client sent (or Xiaoyu's reply still being written) that is not on a card
     * yet. Local sends keep their own bubble; anything already final is left alone.
     */
    boolean pending(SyncCache.Pending item) {
        Message known = messages.get(item.id);
        if (known != null && ((!known.synced && item.presentation == null)
                || known.status.equals("done") && (item.presentation == null || !item.presentation.complete()))) return false;
        boolean you = item.role.equals("you");
        String status = you ? "sent" : item.text.isEmpty() ? "thinking" : "streaming";
        if (item.presentation != null && item.presentation.complete())
            status = item.presentation.value("status").equals("failed") ? "failed" : "done";
        if (known != null && known.text.equals(item.text) && known.status.equals(status)
                && known.attachments.size() == item.attachments.size()
                && (item.presentation == null ? known.presentation == null : known.presentation != null && item.presentation.json().equals(known.presentation.json()))
                && (item.attachments.isEmpty() || known.attachments.get(0).id.equals(item.attachments.get(0).id))) return false;
        Message next = new Message(item.id, item.task, item.scope, item.role, item.text, status,
                you ? "user" : "reply", known != null ? known.at : item.at, true);
        next.conversation = item.conversation;
        next.attachments = item.attachments;
        next.presentation = item.presentation;
        // A local send keeps its client key (its chat row); another client's message has none.
        next.clientId = known != null ? known.clientId : "";
        put(next);
        if (!you && status.equals("done")) TurnTiming.received(next.clientId);
        if (summary(next)) messages.remove(next.card + ":summary-live");
        return true;
    }

    /**
     * A background result being retold: newer Runtimes send it as its own message (id
     * run-summary:reply, the same id it gets on the card when final) while the card still carries
     * summary_partial. Drawing both shows the same words twice, each with its own receipt, so the
     * card's copy is only used when there is no such message (older Runtimes).
     */
    private boolean liveSummary(String card) {
        for (Message message : messages.values())
            if (summary(message) && message.card.equals(card) && !message.status.equals("done")) return true;
        return false;
    }

    private static boolean summary(Message message) {
        return message.synced && !message.role.equals("you") && !message.card.isEmpty()
                && message.id.endsWith("-summary:reply");
    }

    /**
     * Finished question/answer pairs of the main conversation, oldest first: what is carried to
     * another computer so Xiaoyu there can pick up the thread. Ids are the request ids, so a
     * computer that already has a turn skips it.
     */
    List<ChatTurn> turns() {
        List<ChatTurn> result = new ArrayList<>();
        for (Message user : messages.values()) {
            if (!user.role.equals("you") || !user.scope.equals("main") || !user.id.endsWith(":you")
                    || !user.status.equals("done") || user.text.isEmpty()
                    || user.text.equals(RuntimeClient.VOICE_PLACEHOLDER)) continue;
            if (!user.conversation.equals("default")) continue;
            String base = user.id.substring(0, user.id.length() - 4);
            Message reply = messages.get(base + ":reply");
            if (reply == null || !reply.status.equals("done") || reply.text.isEmpty()) continue;
            result.add(new ChatTurn(base, user.text, reply.text, (long) Math.max(user.at, reply.at)));
        }
        result.sort(Comparator.comparingLong((ChatTurn turn) -> turn.at));
        return result;
    }

    /** Whether a synced message is final enough to store; reply fragments still being written are not. */
    static boolean settled(SyncCache.Pending item) {
        if (item.role.equals("you")) return true;
        if (item.presentation != null) return item.presentation.complete();
        return item.status.equals("final") || item.status.equals("failed") || item.status.equals("cancelled");
    }

    /**
     * An independent copy for storing off the lock. Messages are copied field by field; attachment
     * lists and presentations are replaced, never changed in place, so they are shared.
     */
    ChatHistory copy() {
        ChatHistory result = new ChatHistory();
        for (Message message : messages.values()) {
            Message copy = new Message(message.id, message.card, message.scope, message.role, message.text,
                    message.status, message.kind, message.at, message.synced);
            copy.clientId = message.clientId; copy.conversation = message.conversation;
            copy.attachments = message.attachments; copy.presentation = message.presentation;
            result.put(copy);
        }
        return result;
    }

    /** Adds the other history's messages this one does not have (same id = same message). */
    boolean absorb(ChatHistory other) {
        boolean added = false;
        for(String id:other.erased) { if(!erased.contains(id))added=true;eraseConversation(id); }
        for (Message message : other.messages.values())
            if (!messages.containsKey(message.id)) { put(message); added = true; }
        return added;
    }

    void detachTasks() {
        for (Message message : messages.values()) message.card = "";
    }

    List<Message> main() { return main("default"); }
    List<Message> main(String conversation) {
        List<Message> result = select("main", null, false);
        result.removeIf(m -> !conversation.equals(m.conversation) || m.presentation != null && !m.presentation.inChat());
        return result;
    }
    List<Message> pending(String card) { return select("task", card, true); }

    private List<Message> select(String scope, String card, boolean unsynced) {
        List<Message> result = new ArrayList<>();
        for (Message message : messages.values()) {
            if (!erased.contains(message.conversation) && message.scope.equals(scope) && (card == null || message.card.equals(card))
                    && (!unsynced || !message.synced)) {
                Message copy = new Message(message.id, message.card, message.scope, message.role, message.text,
                        message.status, message.kind, message.at, message.synced);
                copy.conversation = message.conversation;
                copy.clientId = message.clientId; copy.attachments = message.attachments; result.add(copy);
                copy.presentation = message.presentation;
            }
        }
        result.sort((a, b) -> {
            int order = Presentation.compareOrder(Presentation.order(a.presentation, a.at, a.role, a.id),
                    Presentation.order(b.presentation, b.at, b.role, b.id));
            return order != 0 ? order : a.id.compareTo(b.id);
        });
        return result;
    }

    String json() {
        StringBuilder out = new StringBuilder("{\"messages\":[");
        boolean first = true;
        for (Message message : messages.values()) {
            if (!first) out.append(','); first = false;
            out.append('{');
            String[] names = {"id", "card", "scope", "role", "text", "status", "kind"};
            String[] values = {message.id, message.card, message.scope, message.role, message.text, message.status, message.kind};
            for (int i = 0; i < names.length; i++) {
                if (i > 0) out.append(','); BuddyProtocol.quote(out, names[i]); out.append(':'); BuddyProtocol.quote(out, values[i]);
            }
            out.append(",\"attachments\":[");
            for (int i = 0; i < message.attachments.size(); i++) {
                if (i > 0) out.append(','); out.append(message.attachments.get(i).json());
            }
            out.append(']');
            out.append(",\"presentation\":").append(message.presentation == null ? "null" : message.presentation.json());
            out.append(",\"conversation\":"); BuddyProtocol.quote(out, message.conversation);
            out.append(",\"client_id\":"); BuddyProtocol.quote(out, message.clientId);
            out.append(",\"at\":").append(message.at).append(",\"synced\":").append(message.synced).append('}');
        }
        out.append("],\"erased\":[");boolean comma=false;for(String id:erased){if(comma)out.append(',');comma=true;BuddyProtocol.quote(out,id);}return out.append("]}").toString();
    }

    static ChatHistory restore(String json) {
        ChatHistory history = new ChatHistory();
        Map<String, Object> raw = Json.parseObject(json);
        if (raw == null) return history;
        for(Object value:Json.list(raw,"erased"))if(value instanceof String)history.erased.add((String)value);
        for (Object value : Json.list(raw, "messages")) {
            Map<String, Object> item = Json.object(value);
            String id = Json.string(item, "id");
            if (id.isEmpty()) continue;
            String status = Json.string(item, "status");
            if (status.equals("sending") || status.equals("thinking") || status.equals("streaming")) status = "interrupted";
            Message message = new Message(id, Json.string(item, "card"), Json.string(item, "scope"),
                    Json.string(item, "role"), Json.string(item, "text"), status, Json.string(item, "kind"),
                    Json.number(item, "at"), Json.flag(item, "synced"));
            if (!Json.string(item, "client_id").isEmpty()) message.clientId = Json.string(item, "client_id");
            message.conversation = Json.string(item, "conversation").isEmpty() ? "default" : Json.string(item, "conversation");
            message.attachments = Attachment.parse(item);
            message.presentation = Presentation.from(item.get("presentation"));
            history.put(message);
        }
        return history;
    }
}
