package com.yanyuliu.pockethub;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;

/** Phone presentation identities; never changes a task's protocol identity or owner. */
final class AppPresentation {
    private AppPresentation() { }

    static String role(String name) {
        String value = name == null ? "" : name.trim().toLowerCase(Locale.ROOT);
        if (value.isEmpty() || value.equals("ds") || value.equals("dsh")
                || value.equals("deepseek") || value.equals("deep") || value.equals("xiaoyou") || value.equals("xiaoyu")) return "ds";
        if (value.equals("cc") || value.equals("claude")) return "claude";
        if (value.equals("codex")) return "codex";
        return value;
    }

    static String name(String role) {
        switch (role(role)) {
            case "ds": return "小鱼";
            case "codex": return "Codex";
            case "claude": return "Claude";
            case "assistant": return "助手";
            default: return role == null ? "助手" : role;
        }
    }

    static String author(Card card, Card.Entry entry) {
        if (entry.role.equals("you")) return "you";
        // Runtime task entries are Xiaoyu's relay replies. Desktop entries are the observed agent.
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
        if (card.presentation != null && connected) return card.presentation.label();
        switch (XiaoyuState.state(card, connected)) {
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

    /** Work Xiaoyu handed to a helper; desktop sessions are observed, not dispatched. */
    static boolean dispatched(Card card) {
        return !card.monitoringOnly && !card.agent.isEmpty();
    }

    /** The chat holds only Xiaoyu's own conversation and its hand-off receipts. */
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

    static final class TaskSection {
        final String title;
        final List<Card> cards = new ArrayList<>();
        TaskSection(String title) { this.title = title; }
    }

    /** Creation time is stable across completion, approvals and subsequent task runs. */
    static List<TaskSection> taskSections(List<Card> cards, java.util.Set<String> pinned,
                                          java.util.Set<String> archived, long now, ZoneId zone) {
        Map<String, TaskSection> sections = new LinkedHashMap<>();
        for (Card card : sidebarTasks(cards, pinned, archived)) {
            String label = pinned.contains(card.id) ? "置顶" : taskDay(card, now, zone);
            sections.computeIfAbsent(label, TaskSection::new).cards.add(card);
        }
        return new ArrayList<>(sections.values());
    }

    static String taskDay(Card card, long now, ZoneId zone) {
        if (!Double.isFinite(card.createdAt) || card.createdAt <= 0) return "时间未知";
        LocalDate day = Instant.ofEpochSecond((long) card.createdAt).atZone(zone).toLocalDate();
        LocalDate today = Instant.ofEpochMilli(now).atZone(zone).toLocalDate();
        return day.equals(today) ? "今天" : day.equals(today.minusDays(1)) ? "昨天" : day.toString();
    }

    static String taskTime(Card card, boolean pinned, ZoneId zone) {
        if (!Double.isFinite(card.createdAt) || card.createdAt <= 0) return "时间未知";
        return DateTimeFormatter.ofPattern(pinned ? "yyyy-MM-dd HH:mm" : "HH:mm", Locale.ROOT)
                .format(Instant.ofEpochSecond((long) card.createdAt).atZone(zone));
    }

    /** One status word; an ended task has none and is shown as muted text instead. */
    static String stateWord(Card card, boolean connected) {
        if (card.presentation != null && connected)
            return card.presentation.value("group").equals("history") ? "" : card.presentation.label();
        if (connected && card.resultReady && card.active()) return "总结中";
        switch (XiaoyuState.state(card, connected)) {
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
                String state = XiaoyuState.state(card, connected);
                if (state.equals("working") || state.equals("talking") || state.equals("waiting")) return card;
            }
            return null;
        }

        boolean desktopRunning(boolean connected) {
            for (Card card : desktop) if (XiaoyuState.state(card, connected).equals("working")) return true;
            return false;
        }

        /**
         * One answer for every view of this helper (home chip, sidebar, helper page):
         * waiting, working (work Xiaoyu handed over), desktop (a session on the computer is running) or idle.
         */
        String mode(boolean connected) {
            Card now = current(connected);
            if (now != null) return XiaoyuState.state(now, connected).equals("waiting") ? "waiting" : "working";
            return desktopRunning(connected) ? "desktop" : "idle";
        }

        String status(boolean connected) {
            switch (mode(connected)) {
                case "waiting": return "等你确认";
                case "working": return "在做";
                case "desktop": return "电脑上在跑";
                default: return "空闲";
            }
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

    /** Only helpers offered by the selected Runtime belong in its sidebar. */
    static List<Helper> localHelpers(List<Card> cards, List<String> offered) {
        Map<String,Helper> local = new LinkedHashMap<>();
        for(String name:offered) if(!name.contains("@")&&!role(name).equals("ds"))local.put(name,new Helper(name));
        List<Card> newest=new ArrayList<>(cards);newest.sort(Comparator.comparingDouble((Card card)->card.createdAt).reversed());
        for(Card card:newest){
            Helper helper=local.get(card.agent);
            if(helper==null){
                List<Helper> aliases=new ArrayList<>();for(Helper candidate:local.values())if(role(candidate.role).equals(owner(card)))aliases.add(candidate);
                if(aliases.size()==1)helper=aliases.get(0);
            }
            if(helper!=null)(card.monitoringOnly?helper.desktop:helper.dispatched).add(card);
        }
        List<Helper> result=new ArrayList<>(local.values());
        result.sort(Comparator.comparingInt((Helper helper)->role(helper.role).equals("codex")?0:role(helper.role).equals("claude")?1:2).thenComparing(helper->helper.role));
        return result;
    }

    /** 输入框右边那个按钮的四种样子：没字时安静、有字能发、在回话时等或停、点了停在等它停下。 */
    static final String COMPOSER_IDLE = "idle", COMPOSER_READY = "ready", COMPOSER_WAIT = "wait",
            COMPOSER_STOP = "stop", COMPOSER_STOPPING = "stopping";

    /**
     * hasText：框里有字；replying：小鱼还在回上一句；stoppable：有一件能停的事在做（小鱼交办出去的、
     * 不是电脑上同步来的）；stopping：已经叫它停了，还没停下。
     * 有字就能发：补充要求不被正在做的事挡住，她回话途中也能插话（Runtime 0.18 起会打断那一句）。
     */
    static String composerState(boolean hasText, boolean replying, boolean stoppable, boolean stopping) {
        if (stopping && (replying || stoppable)) return COMPOSER_STOPPING;
        // 有字就能发：她还在回上一句时也能插话，Runtime 会停下没说完的那句先听你的。
        if (hasText) return COMPOSER_READY;
        if (stoppable) return COMPOSER_STOP;
        if (replying) return COMPOSER_WAIT;
        return COMPOSER_IDLE;
    }

    /** 小鱼的一条回复拆成的气泡：她单独写一行 --- 的地方另起一条（几条由她定）；段落留在同一个气泡里。 */
    static List<String> bubbles(String text) {
        return Markdown.messages(text);
    }

    /**
     * 像聊天软件那样一条一条整条发出来：还在写的时候只给已经写完的那几条（后面跟着 --- 的），
     * 正在写的那条先藏在“正在输入”后面；说完了（done）才给最后一条。
     */
    static List<String> settledBubbles(String text, boolean done) {
        if (done || text == null) return bubbles(text);
        return bubbles(text.substring(0, Markdown.lastBreak(text)));
    }

    /**
     * The only connection words the owner sees: connected, cannot connect (retrying on its own) or not paired.
     * A failed helper request is reported where it happened, never here.
     */
    static String connection(boolean paired, boolean connected) {
        return !paired ? "需要配对" : connected ? "已连接" : "连不上，正在重试";
    }

    /** A side chat nobody has written in yet is not kept in the list, except the one open now. */
    static boolean listedSideChat(Map<String, Object> item, String open) {
        String id = Json.string(item, "id");
        if (!Json.string(item, "kind").equals("side") || Json.flag(item, "archived") || Json.flag(item,"deleted")) return false;
        return !Json.string(item, "title_source").equals("pending") || id.equals(open);
    }

    static String draftKey(String runtime, String task) {
        return (runtime == null ? "" : runtime) + "\n" + (task == null ? "" : task);
    }

    static String draftKey(String runtime, String task, String companion) {
        return draftKey(runtime, task) + (task == null ? "\n" + role(companion) : "");
    }
}
