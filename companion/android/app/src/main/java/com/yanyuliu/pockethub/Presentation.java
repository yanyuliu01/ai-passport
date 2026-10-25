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
    /** Final text the owner cut into: only part of the reply was said. */
    boolean interrupted() { return complete() && Json.flag(raw, "interrupted"); }
    /** The status line under a message (same rule as desktop): unfinished, cut short, failed or stopped. */
    String statusLine() {
        if (!supported) return label();
        String status = value("status");
        return !complete() || interrupted() || status.equals("failed") || status.equals("cancelled") ? label() : "";
    }
    /** The Gateway's plain text for one-line places (home card, device screen, copy); null when not given. */
    String plain() { return supported && raw.get("plain") instanceof String ? (String) raw.get("plain") : null; }
    /** Plain text, or the legacy local clean-up for records without a projection. */
    static String plain(Presentation view, String text) {
        String value = view == null ? null : view.plain();
        return value != null ? value : Markdown.plain(text);
    }
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
        return compareOrder(order(a, 0, "", ""), order(b, 0, "", ""));
    }
    /** One typed tuple per record, including legacy/local rows. Pair-dependent fallback is not transitive. */
    static List<Object> order(Presentation view, double at, String role, String id) {
        List<Object> value = view == null ? java.util.Collections.emptyList() : Json.list(view.raw, "order");
        if (view != null && view.supported && value.size() == 4
                && value.get(0) instanceof Number && Double.isFinite(((Number)value.get(0)).doubleValue())
                && value.get(1) instanceof String && value.get(2) instanceof Number
                && Double.isFinite(((Number)value.get(2)).doubleValue()) && value.get(3) instanceof String) return value;
        return java.util.Arrays.asList(Double.isFinite(at) ? at : 0, "", role.equals("you") ? 0 : 1, id);
    }
    static int compareOrder(List<Object> left, List<Object> right) {
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
