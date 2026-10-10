package com.yanyuliu.pockethub;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;

/**
 * 一件事（Runtime 的一张卡）：主人提的一个需求，从提出到结束。纯 Java，不依赖安卓。
 *
 * 内容来自 Runtime 的 /v1/feed，不可变；卡变了就换一个新的对象。
 */
final class Card {
    /** 一句话：role 是 you（主人）或 xiaoyou。 */
    static final class Entry {
        final String role;
        final String text;

        Entry(String role, String text) {
            this.role = role;
            this.text = text;
        }
    }

    /** 一个还在等回答的授权：谁要做什么，原样给人看。 */
    static final class Approval {
        final String id;
        final String card;
        final String agent;
        /** “帮手 · 工具”。 */
        final String tool;
        /** 要做的内容原文（命令、文件路径……）。 */
        final String detail;
        final long createdAt;

        Approval(String id, String card, String agent, String tool, String detail, long createdAt) {
            this.id = id;
            this.card = card;
            this.agent = agent;
            this.tool = tool;
            this.detail = detail;
            this.createdAt = createdAt;
        }

        static Approval from(Map<String, Object> raw) {
            String id = Json.string(raw, "id");
            if (id.isEmpty()) {
                return null;
            }
            return new Approval(id, Json.string(raw, "card"), Json.string(raw, "agent"),
                    Json.string(raw, "tool"), Json.string(raw, "detail"),
                    (long) Json.number(raw, "created_at"));
        }
    }

    final String id;
    final String title;
    /** talking、working、waiting、done、failed、cancelled 之一。 */
    final String state;
    /** 谁在做或做的；小幽自己答的是空串。 */
    final String agent;
    final String brief;
    final String mood;
    final List<Entry> entries;
    /** 最近几步，旧的在前。 */
    final List<String> progress;
    /** Unix 秒；没开始做是 0。 */
    final double createdAt;
    final double startedAt;
    final int edits;
    final boolean queued;
    final long seq;
    /** 交给过帮手的事是什么时候结束的（Unix 秒）；还在做、或者 Runtime 没记，是 0。 */
    final double finishedAt;
    /** 这件事让帮手用的档位（low、medium、high……）；没有就是空串。 */
    final String effort;
    /** 这件事实际用的模型；还不知道就是空串。 */
    final String model;
    /** 这件事花了多少：token，和订阅额度两个窗口各少了几个百分点。没记就都是 -1。 */
    final long tokens;
    final int spent5h;
    final int spent7d;

    private Card(String id, String title, String state, String agent, String brief, String mood,
                 List<Entry> entries, List<String> progress, double createdAt, double startedAt,
                 int edits, boolean queued, long seq, double finishedAt, String effort,
                 String model, long tokens, int spent5h, int spent7d) {
        this.finishedAt = finishedAt;
        this.effort = effort;
        this.model = model;
        this.tokens = tokens;
        this.spent5h = spent5h;
        this.spent7d = spent7d;
        this.id = id;
        this.title = title;
        this.state = state;
        this.agent = agent;
        this.brief = brief;
        this.mood = mood;
        this.entries = Collections.unmodifiableList(entries);
        this.progress = Collections.unmodifiableList(progress);
        this.createdAt = createdAt;
        this.startedAt = startedAt;
        this.edits = edits;
        this.queued = queued;
        this.seq = seq;
    }

    /** 没有编号的不是卡，返回 null。 */
    static Card from(Map<String, Object> raw) {
        String id = Json.string(raw, "id");
        if (id.isEmpty()) {
            return null;
        }
        List<Entry> entries = new ArrayList<>();
        for (Object item : Json.list(raw, "entries")) {
            Map<String, Object> entry = Json.object(item);
            String role = Json.string(entry, "role");
            if (role.equals("you") || role.equals("xiaoyou")) {
                entries.add(new Entry(role, Json.string(entry, "text")));
            }
        }
        List<String> progress = new ArrayList<>();
        for (Object line : Json.list(raw, "progress")) {
            if (line instanceof String) {
                progress.add((String) line);
            }
        }
        String state = Json.string(raw, "state");
        String mood = Json.string(raw, "mood");
        Map<String, Object> cost = raw.get("cost") instanceof Map ? Json.object(raw.get("cost"))
                : null;
        return new Card(id, Json.string(raw, "title"), state.isEmpty() ? "done" : state,
                Json.string(raw, "agent"), Json.string(raw, "brief"), mood.isEmpty() ? "idle" : mood,
                entries, progress, Json.number(raw, "created_at"), Json.number(raw, "started_at"),
                (int) Json.number(raw, "edits"), Json.flag(raw, "queued"),
                (long) Json.number(raw, "seq"), Json.number(raw, "finished_at"),
                Json.string(raw, "effort"), Json.string(raw, "model"),
                cost == null ? -1 : (long) (Json.number(cost, "in") + Json.number(cost, "out")
                        + Json.number(cost, "cached")),
                cost != null && cost.get("d5") instanceof Double ? (int) Json.number(cost, "d5") : -1,
                cost != null && cost.get("d7") instanceof Double ? (int) Json.number(cost, "d7") : -1);
    }

    /** 档位给人看的说法：低档、中档、高档……不认识的原样，没有就是空串。 */
    static String effortLabel(String effort) {
        if (effort == null || effort.isEmpty()) {
            return "";
        }
        switch (effort) {
            case "minimal":
                return "最低档";
            case "low":
                return "低档";
            case "medium":
                return "中档";
            case "high":
                return "高档";
            case "xhigh":
                return "特高档";
            case "max":
                return "最高档";
            default:
                return effort;
        }
    }

    /**
     * “codex · gpt-6.1-sol · 高档 · 约用了 5 小时额度的 3%”：谁、用哪个模型、哪一档做的，
     * 花了多少。小幽自己答的、什么都没记的，是空串。
     */
    String spentLine() {
        if (agent.isEmpty()) {
            return "";
        }
        StringBuilder out = new StringBuilder(agent);
        if (!model.isEmpty()) {
            out.append(" · ").append(model);
        }
        if (!effort.isEmpty()) {
            out.append(" · ").append(effortLabel(effort));
        }
        if (spent5h > 0) {
            out.append(" · 约用了 5 小时额度的 ").append(spent5h).append('%');
        } else if (spent7d > 0) {
            out.append(" · 约用了本周额度的 ").append(spent7d).append('%');
        } else if (tokens > 0) {
            out.append(" · ").append(tokens >= 10000 ? (tokens / 1000) + "k" : String.valueOf(tokens))
                    .append(" token");
        }
        return out.length() == agent.length() ? "" : out.toString();
    }

    /** 还没结束：有人在做，或者在等主人点头。 */
    boolean active() {
        return state.equals("working") || state.equals("waiting") || state.equals("talking");
    }

    /** 这件事的第一句话。 */
    String said() {
        for (Entry entry : entries) {
            if (entry.role.equals("you")) {
                return entry.text;
            }
        }
        return title;
    }

    /** 小幽最新的一句话；一句都没有时是空串。 */
    String lastSay() {
        for (int index = entries.size() - 1; index >= 0; index--) {
            if (entries.get(index).role.equals("xiaoyou")) {
                return entries.get(index).text;
            }
        }
        return "";
    }

    /** 卡的编号里的数字（c12 → 12），用来按开卡的先后排；认不出时是 0。 */
    long order() {
        long value = 0;
        for (int index = 0; index < id.length(); index++) {
            char digit = id.charAt(index);
            if (digit >= '0' && digit <= '9') {
                value = value * 10 + (digit - '0');
            }
        }
        return value;
    }

    /** 给人看的状态。 */
    String stateLabel() {
        switch (state) {
            case "working":
                return queued ? "排队" : "在做";
            case "waiting":
                return "等你点头";
            case "failed":
                return "没成";
            case "cancelled":
                return "取消了";
            case "talking":
                return "在说";
            default:
                return "好了";
        }
    }
}
