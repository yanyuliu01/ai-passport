package com.yanyuliu.pockethub;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/** Phone presentation identities; never changes a task's protocol identity or owner. */
final class AppPresentation {
    private AppPresentation() { }

    static String role(String name) {
        String value = name == null ? "" : name.trim().toLowerCase(Locale.ROOT);
        if (value.isEmpty() || value.equals("ds") || value.equals("dsh")
                || value.equals("deepseek") || value.equals("deep") || value.equals("xiaoyou")) return "ds";
        if (value.equals("cc") || value.equals("claude")) return "claude";
        if (value.equals("codex")) return "codex";
        return value;
    }

    static String name(String role) {
        switch (role(role)) {
            case "ds": return "DS";
            case "codex": return "Codex";
            case "claude": return "Claude";
            case "assistant": return "助手";
            default: return role == null ? "助手" : role;
        }
    }

    static String author(Card card, Card.Entry entry) {
        if (entry.role.equals("you")) return "you";
        // Runtime task entries are DS's relay replies. Desktop entries are the observed agent.
        return card.monitoringOnly ? owner(card) : "ds";
    }

    static String owner(Card card) {
        return card.monitoringOnly && card.agent.isEmpty() ? "assistant" : role(card.agent);
    }

    static int tint(String name) {
        switch (role(name)) {
            case "ds": return 0xffe8f1ff;
            case "codex": return 0xfff0edf9;
            case "claude": return 0xfffff0e5;
            default: return 0xffedf2f7;
        }
    }

    static String stateLabel(Card card, boolean connected) {
        switch (DsState.state(card, connected)) {
            case "working": case "talking": return "执行中";
            case "waiting": return "待确认";
            case "queued": return "排队";
            case "unknown": return "状态未知";
            case "idle": return "本轮结束";
            case "done": return "已完成";
            case "failed": return "未成功";
            case "cancelled": return "已停止";
            default: return card.stateLabel();
        }
    }

    /** Work DS handed to a helper; desktop sessions are observed, not dispatched. */
    static boolean dispatched(Card card) {
        return !card.monitoringOnly && !card.agent.isEmpty();
    }

    /** The chat holds only DS's own conversation and its hand-off receipts. */
    static List<Card> chatCards(List<Card> cards) {
        List<Card> result = new ArrayList<>();
        for (Card card : cards) if (!card.monitoringOnly) result.add(card);
        return result;
    }

    /**
     * Sidebar tasks: pinned first, then newest start first. State never decides the
     * position, so a task finishing or asking for approval does not move under the finger.
     */
    static List<Card> sidebarTasks(List<Card> cards, java.util.Set<String> pinned, java.util.Set<String> archived) {
        List<Card> result = new ArrayList<>();
        for (Card card : cards) if (dispatched(card) && !archived.contains(card.id)) result.add(card);
        result.sort(Comparator.comparingInt((Card card) -> pinned.contains(card.id) ? 0 : 1)
                .thenComparing(Comparator.comparingDouble((Card card) -> card.createdAt).reversed())
                .thenComparing(Comparator.comparingLong(Card::order).reversed()));
        return result;
    }

    /** One status word; an ended task has none and is shown as muted text instead. */
    static String stateWord(Card card, boolean connected) {
        switch (DsState.state(card, connected)) {
            case "working": case "talking": return "进行中";
            case "queued": return "排队";
            case "waiting": return card.monitoringOnly ? "等电脑确认" : "等你确认";
            case "unknown": return card.outstanding() ? "状态未知" : "";
            default: return "";
        }
    }

    static final class Helper {
        final String role;
        /** Newest first, same rule as the sidebar. */
        final List<Card> dispatched = new ArrayList<>();
        final List<Card> desktop = new ArrayList<>();
        Helper(String role) { this.role = role; }

        /** The task it is doing now (waiting for approval counts); null when idle. */
        Card current(boolean connected) {
            for (Card card : dispatched) {
                String state = DsState.state(card, connected);
                if (state.equals("working") || state.equals("talking") || state.equals("waiting")) return card;
            }
            return null;
        }

        boolean desktopRunning(boolean connected) {
            for (Card card : desktop) if (DsState.state(card, connected).equals("working")) return true;
            return false;
        }

        String status(boolean connected) {
            Card now = current(connected);
            if (now != null) return DsState.state(now, connected).equals("waiting") ? "等你确认" : "工作中";
            return desktopRunning(connected) ? "电脑上在跑" : "空闲";
        }
    }

    /**
     * Helpers in a fixed order (Codex, Claude, then by name), so the sidebar never reshuffles.
     * Helpers the Runtime offers appear even before they have done anything.
     */
    static List<Helper> helpers(List<Card> cards, List<String> offered) {
        Map<String, Helper> byRole = new LinkedHashMap<>();
        for (String name : offered) {
            String role = role(name);
            if (!role.equals("ds")) byRole.computeIfAbsent(role, Helper::new);
        }
        List<Card> newest = new ArrayList<>(cards);
        newest.sort(Comparator.comparingDouble((Card card) -> card.createdAt).reversed()
                .thenComparing(Comparator.comparingLong(Card::order).reversed()));
        for (Card card : newest) {
            String role = owner(card);
            if (role.equals("ds") || (!card.monitoringOnly && card.agent.isEmpty())) continue;
            Helper helper = byRole.computeIfAbsent(role, Helper::new);
            (card.monitoringOnly ? helper.desktop : helper.dispatched).add(card);
        }
        List<Helper> result = new ArrayList<>(byRole.values());
        result.sort(Comparator.comparingInt((Helper helper) -> helper.role.equals("codex") ? 0
                : helper.role.equals("claude") ? 1 : 2).thenComparing(helper -> helper.role));
        return result;
    }

    static String draftKey(String runtime, String task) {
        return (runtime == null ? "" : runtime) + "\n" + (task == null ? "" : task);
    }

    static String draftKey(String runtime, String task, String companion) {
        return draftKey(runtime, task) + (task == null ? "\n" + role(companion) : "");
    }
}
