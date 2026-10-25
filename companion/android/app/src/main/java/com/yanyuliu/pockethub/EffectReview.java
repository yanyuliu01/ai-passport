package com.yanyuliu.pockethub;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * One request whose operations ended with an unknown result (Runtime request_effect_review):
 * GET /v1/messages/<id>/effects, then the owner says for each operation that did not finish
 * successfully whether it took effect. This only records the owner's word; the Runtime never
 * repeats, retries or rolls back an operation because of it.
 */
final class EffectReview {
    static final String APPLIED = "applied", NOT_APPLIED = "not_applied";

    static final class Item {
        final String id, name, state, detail;
        Item(String id, String name, String state, String detail) {
            this.id = id; this.name = name; this.state = state; this.detail = detail;
        }
        /** An operation never claimed (pending) cannot have run, so it can only be confirmed as not applied. */
        boolean canApply() { return !state.equals("pending"); }
    }

    final String messageId, operationId, status, runtimeId;
    /** Opaque: sent back exactly as received. */
    final Object revision;
    final List<Item> items = new ArrayList<>();
    final Map<String, String> recorded = new LinkedHashMap<>();

    private EffectReview(String messageId, String operationId, String status, Object revision, String runtimeId) {
        this.messageId = messageId; this.operationId = operationId; this.status = status; this.revision = revision;
        this.runtimeId = runtimeId;
    }

    static EffectReview from(Map<String, Object> view) {
        if (view == null) return null;
        String status = Json.string(view, "status");
        if (!status.equals("needs_review") && !status.equals("recorded") && !status.equals("reviewed")) return null;
        EffectReview review = new EffectReview(Json.string(view, "message_id"), Json.string(view, "operation_id"),
                status, view.get("revision"), Json.string(view, "runtime_id"));
        for (Object value : Json.list(view, "effects")) {
            Map<String, Object> effect = Json.object(value);
            String state = Json.string(effect, "state");
            if (state.equals("succeeded") || Json.string(effect, "id").isEmpty()) continue;
            Object args = effect.get("args");
            review.items.add(new Item(Json.string(effect, "id"), Json.string(effect, "name"), state,
                    args == null ? "" : CardViews.oneLine(UsageEvents.json(args), 160)));
        }
        Map<String, Object> record = Json.object(view.get("review"));
        Map<String, Object> outcomes = record == null ? null : Json.object(record.get("outcomes"));
        if (outcomes != null) for (Map.Entry<String, Object> entry : outcomes.entrySet())
            if (entry.getValue() instanceof String) review.recorded.put(entry.getKey(), (String) entry.getValue());
        return review;
    }

    /** null when every listed operation has a valid answer; otherwise why the owner cannot submit yet. */
    String missing(Map<String, String> outcomes) {
        for (Item item : items) {
            String value = outcomes.get(item.id);
            if (value == null) return "每一项都要选：已生效还是未生效";
            if (value.equals(APPLIED) && !item.canApply()) return "还没开始的操作只能确认为未生效";
            if (!value.equals(APPLIED) && !value.equals(NOT_APPLIED)) return "选项无效";
        }
        return items.isEmpty() ? "没有需要核对的操作" : null;
    }

    /** The exact POST body; kept unchanged for retries after a lost receipt or a 503. */
    String body(String reviewId, Map<String, String> outcomes) {
        String problem = missing(outcomes);
        if (problem != null) throw new IllegalArgumentException(problem);
        Map<String, Object> chosen = new LinkedHashMap<>();
        for (Item item : items) chosen.put(item.id, outcomes.get(item.id));
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("operation_id", operationId); body.put("revision", revision);
        body.put("review_id", reviewId); body.put("outcomes", chosen);
        return UsageEvents.json(body);
    }

    /** The request id behind a chat message id ("<id>:you" / "<id>:reply"); empty when it is not one. */
    static String requestId(String messageId) {
        if (messageId == null) return "";
        if (messageId.endsWith(":you")) return messageId.substring(0, messageId.length() - 4);
        if (messageId.endsWith(":reply")) return messageId.substring(0, messageId.length() - 6);
        return "";
    }

    static String label(String status) {
        switch (status) {
            case "reviewed": return "已核对，说明已回到原对话";
            case "recorded": return "核对已保存，说明还没发出，可以重试";
            default: return "这次操作结果不明，请你确认";
        }
    }
}
