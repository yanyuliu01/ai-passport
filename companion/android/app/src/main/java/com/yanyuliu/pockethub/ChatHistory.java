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

    void begin(String id, String card, String text, double at) {
        String target = card == null ? "" : card;
        String scope = target.isEmpty() ? "main" : "task";
        put(new Message(id + ":you", target, scope, "you", text, "sending", "user", at, false));
        put(new Message(id + ":reply", target, scope, "xiaoyou", "", "thinking", "reply", at, false));
    }

    private void put(Message message) { messages.put(message.id, message); }

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

    void finish(String id, String card, String text, boolean failed, Card snapshot) {
        Message user = messages.get(id + ":you"), reply = messages.get(id + ":reply");
        if (user != null) { user.status = failed ? "failed" : "done"; user.card = card == null ? user.card : card; }
        if (reply != null) { reply.text = text; reply.status = failed ? "failed" : "done"; reply.card = card == null ? reply.card : card; }
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

    void ingest(Card card) {
        if (card.monitoringOnly) return;
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
            if (entry.kind.equals("worker_result")) continue;
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
            if (known != null) next.clientId = known.clientId;
            put(next);
        }
        String partialId = card.id + ":summary-live";
        if (!card.summaryPartial.isEmpty() && card.active()) {
            Message previous = messages.get(partialId);
            put(new Message(partialId, card.id, "main", "xiaoyou", card.summaryPartial, "streaming",
                    "summary", previous == null ? System.currentTimeMillis() / 1000.0 : previous.at, true));
        } else messages.remove(partialId);
    }

    /**
     * A message another client sent (or Xiaoyou's reply still being written) that is not on a card
     * yet. Local sends keep their own bubble; anything already final is left alone.
     */
    boolean pending(SyncCache.Pending item) {
        Message known = messages.get(item.id);
        if (known != null && (!known.synced || known.status.equals("done"))) return false;
        boolean you = item.role.equals("you");
        String status = you ? "sent" : item.text.isEmpty() ? "thinking" : "streaming";
        if (known != null && known.text.equals(item.text) && known.status.equals(status)) return false;
        Message next = new Message(item.id, item.task, item.scope, item.role, item.text, status,
                you ? "user" : "reply", known != null ? known.at : item.at, true);
        next.clientId = "";
        put(next);
        return true;
    }

    /** Adds the other history's messages this one does not have (same id = same message). */
    boolean absorb(ChatHistory other) {
        boolean added = false;
        for (Message message : other.messages.values())
            if (!messages.containsKey(message.id)) { put(message); added = true; }
        return added;
    }

    List<Message> main() { return select("main", null, false); }
    List<Message> pending(String card) { return select("task", card, true); }

    private List<Message> select(String scope, String card, boolean unsynced) {
        List<Message> result = new ArrayList<>();
        for (Message message : messages.values()) {
            if (message.scope.equals(scope) && (card == null || message.card.equals(card))
                    && (!unsynced || !message.synced)) {
                Message copy = new Message(message.id, message.card, message.scope, message.role, message.text,
                        message.status, message.kind, message.at, message.synced);
                copy.clientId = message.clientId; result.add(copy);
            }
        }
        result.sort(Comparator.comparingDouble((Message message) -> message.at)
                .thenComparingInt(message -> message.role.equals("you") ? 0 : 1));
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
            out.append(",\"client_id\":"); BuddyProtocol.quote(out, message.clientId);
            out.append(",\"at\":").append(message.at).append(",\"synced\":").append(message.synced).append('}');
        }
        return out.append("]}").toString();
    }

    static ChatHistory restore(String json) {
        ChatHistory history = new ChatHistory();
        Map<String, Object> raw = Json.parseObject(json);
        if (raw == null) return history;
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
            history.put(message);
        }
        return history;
    }
}
