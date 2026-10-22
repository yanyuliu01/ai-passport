package com.yanyuliu.pockethub;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/** Renders the Gateway's decisions; contains no grouping or status inference. */
final class Presentation {
    final Map<String, Object> raw;
    final boolean supported;
    private Presentation(Map<String, Object> raw) {
        this.raw = raw;
        supported = raw.get("version") instanceof Number && ((Number) raw.get("version")).intValue() == 1
                && ((Number) raw.get("version")).doubleValue() == 1;
    }
    static Presentation from(Object value) {
        return value instanceof Map ? new Presentation(Json.object(value)) : null;
    }
    static Presentation restore(String json) { return from(Json.parseObject(json)); }
    String json() { return UsageEvents.json(raw); }
    String value(String key) { return Json.string(raw, key); }
    String label() { return supported ? value("status_label") : "显示规则版本不兼容"; }
    String author() { return supported ? Json.string(Json.object(raw.get("author")), "label") : ""; }
    boolean inChat() { return !supported || value("placement").equals("chat"); }
    boolean complete() { return supported && Json.flag(raw, "complete"); }
    List<String> blocks() {
        List<String> result = new ArrayList<>();
        if (!supported) return result;
        for (Object value : Json.list(raw, "blocks")) {
            Map<String, Object> block = Json.object(value);
            if (Json.flag(block, "complete")) result.add(Json.string(block, "text"));
        }
        return result;
    }
    List<Object> runs(int index) {
        int visible = 0;
        for (Object value : Json.list(raw,"blocks")) {
            Map<String,Object> block = Json.object(value);
            if (Json.flag(block,"complete") && visible++ == index) return Json.list(block,"runs");
        }
        return java.util.Collections.emptyList();
    }
    static int compare(Presentation a, Presentation b) {
        List<Object> left = Json.list(a.raw, "order"), right = Json.list(b.raw, "order");
        for (int i = 0; i < Math.min(left.size(), right.size()); i++) {
            Object x = left.get(i), y = right.get(i);
            int result = x instanceof Number && y instanceof Number
                    ? Double.compare(((Number) x).doubleValue(), ((Number) y).doubleValue())
                    : String.valueOf(x).compareTo(String.valueOf(y));
            if (result != 0) return result;
        }
        return Integer.compare(left.size(), right.size());
    }
    /** Preserve the nested display contract alongside the legacy flat response fields. */
    static Map<String, String> snapshot(String json) {
        Map<String, String> fields = BuddyProtocol.parseFlatObject(json);
        Map<String, Object> object = Json.parseObject(json);
        if (fields != null && object != null && object.get("presentation") instanceof Map)
            fields.put("presentation_json", UsageEvents.json(object.get("presentation")));
        return fields;
    }
}
