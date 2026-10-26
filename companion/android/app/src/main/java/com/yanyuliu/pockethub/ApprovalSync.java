package com.yanyuliu.pockethub;

import java.io.IOException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** An approval delta is not a replacement of the whole pending list. */
final class ApprovalSync {
    private String epoch = "", cursor = "0";
    private final Map<String, Map<String, Object>> pending = new LinkedHashMap<>();

    String path() throws IOException {
        return "/v1/sync?conversation=default&all_conversations=1&states=unavailable&after="
                + java.net.URLEncoder.encode(cursor, "UTF-8") + "&epoch="
                + java.net.URLEncoder.encode(epoch, "UTF-8") + "&wait=20";
    }

    List<Card.Approval> apply(Map<String, Object> page, String url, String name, double now) throws IOException {
        String next = Json.string(page, "epoch");
        if (next.isEmpty() || !(page.get("approvals") instanceof List)) throw new IOException("Invalid approval sync");
        boolean fresh = !epoch.equals(next) || Json.flag(page, "reset") || page.containsKey("snapshot");
        double applied;
        try { applied = Double.parseDouble(cursor); } catch (NumberFormatException opaque) { applied = -1; }
        if (!fresh && page.get("seq") instanceof Number && Json.number(page, "seq") < applied)
            throw new IOException("Stale approval sync page");
        if (fresh) pending.clear();
        List<Map<String, Object>> updates = new ArrayList<>();
        for (Object value : Json.list(page, "approvals")) updates.add(Json.object(value));
        updates.sort(java.util.Comparator.comparingDouble(item -> Json.number(item, "seq")));
        for (Map<String, Object> item : updates) {
            if (!fresh && Json.number(item, "seq") <= applied) continue;
            String id = Json.string(item, "id"), status = Json.string(item, "status");
            if (id.isEmpty()) continue;
            Map<String, Object> old = pending.get(id);
            if (old != null && Json.number(old, "seq") > Json.number(item, "seq")) continue;
            if (status.isEmpty() || status.equals("pending")) pending.put(id, item);
            else pending.remove(id);
        }
        List<Card.Approval> result = new ArrayList<>();
        for (String id : new ArrayList<>(pending.keySet())) {
            Card.Approval approval = Card.Approval.from(pending.get(id));
            if (approval == null || approval.expiresAt > 0 && approval.expiresAt <= now) pending.remove(id);
            else result.add(approval.origin(url, name));
        }
        epoch = next;
        Object seq = page.get("seq");
        cursor = seq instanceof Number ? Long.toString(((Number) seq).longValue()) : seq instanceof String ? (String) seq : "";
        if (cursor.isEmpty()) throw new IOException("Missing approval sync cursor");
        return result;
    }
}
