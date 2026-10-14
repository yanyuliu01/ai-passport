package com.yanyuliu.pockethub;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

/** Presentation state is separate from the wire state and its queued/stale flags. */
final class DsState {
    static String state(Card card, boolean connected) {
        if ((!connected && card.outstanding()) || card.stale || card.state.equals("unknown")) return "unknown";
        if (card.state.equals("working") && card.queued) return "queued";
        if (card.monitoringOnly && card.state.equals("done")) return "idle";
        return card.state;
    }

    static String label(Card card, boolean connected) {
        switch (state(card, connected)) {
            case "unknown": return "状态未知";
            case "queued": return "排队";
            case "idle": return "本轮结束";
            default: return card.stateLabel();
        }
    }

    static int priority(Card card) {
        if (card.state.equals("waiting") && !card.stale) return 0;
        if (card.active() && !card.queued && !card.stale) return 1;
        if (card.queued && !card.stale) return 2;
        return 3;
    }

    static List<Card> roster(List<Card> cards) {
        List<Card> result = new ArrayList<>();
        for (Card card : cards) if (CardViews.isTask(card) && card.outstanding()) result.add(card);
        result.sort(Comparator.comparingInt(DsState::priority)
                .thenComparingDouble(card -> card.startedAt > 0 ? card.startedAt : card.createdAt)
                .thenComparing(card -> card.id));
        return result;
    }

    static String pose(Card card, boolean connected) {
        switch (state(card, connected)) {
            case "waiting": return "ask";
            case "unknown": return "think";
            case "queued": case "idle": case "cancelled": return "idle";
            case "done": return "happy";
            case "failed": return "oops";
            case "talking": return "think";
            default:
                String step = card.progress.isEmpty() ? "" : card.progress.get(card.progress.size() - 1).toLowerCase(java.util.Locale.ROOT);
                if (step.isEmpty()) return "think";
                if (step.matches(".*\\b(test|pytest|ctest|validate|lint|check|verify)\\b.*")) return "read";
                if (step.matches("^(read|glob|grep|ls|find|rg|cat|head|tail|websearch|webfetch|search|view)\\b.*")) return "read";
                return "type";
        }
    }

    static int color(String agent) {
        String name = agent.toLowerCase(java.util.Locale.ROOT);
        if (name.equals("claude") || name.equals("cc")) return 0xffe08a63;
        if (name.equals("codex")) return 0xff6fd3b0;
        if (name.equals("dsh") || name.equals("deepseek") || name.equals("deep") || name.equals("ds")) return 0xff4f7bff;
        if (name.isEmpty()) return 0xff7cc8f2;
        int hash = 0x811c9dc5;
        for (byte value : name.getBytes(java.nio.charset.StandardCharsets.UTF_8)) hash = (hash ^ (value & 255)) * 0x01000193;
        return new int[] {0xfff2a7c8, 0xffc9b458, 0xffb9a7ff, 0xffe6e6e6}[(hash & 0x7fffffff) % 4];
    }
}
