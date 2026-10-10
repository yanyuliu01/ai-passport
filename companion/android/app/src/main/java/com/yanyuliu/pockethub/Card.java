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

    private Card(String id, String title, String state, String agent, String brief, String mood,
                 List<Entry> entries, List<String> progress, double createdAt, double startedAt,
                 int edits, boolean queued, long seq) {
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
        return new Card(id, Json.string(raw, "title"), state.isEmpty() ? "done" : state,
                Json.string(raw, "agent"), Json.string(raw, "brief"), mood.isEmpty() ? "idle" : mood,
                entries, progress, Json.number(raw, "created_at"), Json.number(raw, "started_at"),
                (int) Json.number(raw, "edits"), Json.flag(raw, "queued"),
                (long) Json.number(raw, "seq"));
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
