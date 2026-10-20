package com.yanyuliu.pockethub;

import java.util.List;
import java.util.Map;

/** Immutable server snapshot: a batch can only allow the operations shown in that snapshot. */
final class QuickReview {
    static final int MAX_SECONDS = 365 * 86400;
    final Map<String, Object> data;
    final String epoch, conversation;
    final long revision;
    final List<Object> pending;

    QuickReview(Map<String, Object> data) {
        this.data = data;
        epoch = Json.string(data, "epoch");
        conversation = Json.string(data, "conversation");
        revision = (long) Json.number(data, "revision");
        pending = Json.list(data, "pending");
        if (epoch.isEmpty() || conversation.isEmpty()) throw new IllegalArgumentException("审核状态不完整");
    }

    boolean automatic() { return "auto_allow".equals(Json.string(data, "mode")); }
    boolean persistent() { return automatic() && Json.flag(data, "persistent"); }

    boolean durationConfigSupported() {
        Map<String, Object> options = Json.object(data.get("duration_options"));
        return Json.flag(options, "persistent") && Json.flag(options, "survives_restart")
                && Json.number(options, "min_seconds") == 60
                && Json.number(options, "max_seconds") >= MAX_SECONDS;
    }

    static int customSeconds(String minutes) {
        try {
            if (!minutes.trim().matches("[0-9]+")) throw new NumberFormatException();
            long value = Long.parseLong(minutes.trim());
            if (value < 1 || value > MAX_SECONDS / 60) throw new NumberFormatException();
            return (int) value * 60;
        } catch (NumberFormatException error) {
            throw new IllegalArgumentException("请输入 1 到 525600 分钟，或选择持续开启");
        }
    }

    boolean readOnlyModeSupported() {
        List<Object> tools = Json.list(data, "automatic_tools");
        if (tools.isEmpty()) return false;
        for (Object tool : tools) {
            if (!("Read".equals(tool) || "Glob".equals(tool) || "Grep".equals(tool) || "LS".equals(tool)))
                return false;
        }
        return true;
    }

    String modeBody(boolean enable) {
        return modeBody(enable, 3600);
    }

    String modeBody(boolean enable, int seconds) {
        if (enable && !readOnlyModeSupported()) throw new IllegalStateException("Runtime 尚未声明只读自动审核范围");
        if (enable && seconds != 0 && (seconds < 60 || seconds > MAX_SECONDS))
            throw new IllegalArgumentException("自动通过时长无效");
        if (enable && !durationConfigSupported() && seconds != 3600)
            throw new IllegalStateException("请升级 Runtime 后配置时长或持续开启");
        return base().append(",\"revision\":").append(revision)
                .append(",\"mode\":\"").append(enable ? "auto_allow" : "manual")
                .append("\",\"seconds\":").append(seconds).append('}').toString();
    }

    String batchBody() {
        StringBuilder out = base().append(",\"items\":[");
        for (int i = 0; i < pending.size(); i++) {
            Map<String, Object> item = Json.object(pending.get(i));
            if (i > 0) out.append(',');
            out.append("{\"id\":"); BuddyProtocol.quote(out, Json.string(item, "id"));
            out.append(",\"key\":"); BuddyProtocol.quote(out, Json.string(item, "key"));
            out.append('}');
        }
        return out.append("]}").toString();
    }

    private StringBuilder base() {
        StringBuilder out = new StringBuilder("{\"epoch\":"); BuddyProtocol.quote(out, epoch);
        out.append(",\"conversation\":"); BuddyProtocol.quote(out, conversation);
        return out;
    }
}
