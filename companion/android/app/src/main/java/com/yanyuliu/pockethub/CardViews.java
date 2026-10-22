package com.yanyuliu.pockethub;

import java.util.ArrayList;
import java.util.Calendar;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.TimeZone;

/**
 * 卡在手机上怎么分、怎么写成字。纯 Java，不依赖安卓。
 *
 * 卡分两种：小幽自己直接答的是“对话”，交给过帮手的是“任务”。对话那一页把对话连着
 * 显示，任务只留一行指路的；任务各有各的页面，在里面说的话只归到它自己。
 */
final class CardViews {
    static final int PREVIEW_CHARS = 60;

    private CardViews() {
    }

    /** 交给过帮手的事是任务；小幽自己答的（包括没答成的）是对话。 */
    static boolean isTask(Card card) {
        return card.monitoringOnly || !card.agent.isEmpty();
    }

    /** 任务：还在做的在前，然后是结束的；各自新的在前。最多 limit 件。 */
    static List<Card> tasks(List<Card> cards, int limit) {
        List<Card> out = new ArrayList<>();
        for (int pass = 0; pass < 3; pass++) {
            for (int index = cards.size() - 1; index >= 0; index--) {
                Card card = cards.get(index);
                int group = card.active() ? 0 : (card.outstanding() ? 1 : 2);
                if (isTask(card) && group == pass && out.size() < limit) {
                    out.add(card);
                }
            }
        }
        return out;
    }

    /** 还在做的任务有几件。 */
    static int doing(List<Card> cards) {
        int count = 0;
        for (Card card : cards) {
            if (isTask(card) && card.active()) {
                count++;
            }
        }
        return count;
    }

    static Card find(List<Card> cards, String id) {
        if (id != null) {
            for (Card card : cards) {
                if (card.id.equals(id)) {
                    return card;
                }
            }
        }
        return null;
    }

    /** 一张卡里可见的话：Runtime 是我/小幽，电脑观察是你/Codex 或 Claude。 */
    static String thread(Card card) {
        StringBuilder out = new StringBuilder();
        for (Card.Entry entry : card.entries) {
            if (out.length() > 0) {
                out.append("\n\n");
            }
            out.append(entry.role.equals("you") ? (card.monitoringOnly ? "你：" : "我：")
                    : assistantLabel(card) + "：").append(entry.text);
        }
        return out.toString();
    }

    static String assistantLabel(Card card) {
        if (!card.monitoringOnly) {
            return "ds";
        }
        if (card.agent.equalsIgnoreCase("codex")) {
            return "Codex";
        }
        if (card.agent.equalsIgnoreCase("claude")) {
            return "Claude";
        }
        return card.agent.isEmpty() ? "助手" : card.agent;
    }

    /** 最近片段与完整历史分清楚；内容缺失不会被写成已经采集完了。 */
    static String contentNotice(Card card) {
        if (!card.monitoringOnly) {
            return "";
        }
        if (card.entries.isEmpty()) {
            return "尚未采集到可见对话内容。";
        }
        String notice = card.contentScope.equals("recent") ? "显示最近可见的对话片段。" : "";
        if (card.contentTruncated) {
            notice += "较早或较长内容已省略，请在电脑端查看完整对话。";
        }
        return notice;
    }

    /** 只在 Runtime 明确给了来源时说明原题或回退，旧版不假称已拿到侧栏标题。 */
    static String titleNotice(Card card) {
        if (!card.monitoringOnly) {
            return "";
        }
        switch (card.titleSource) {
            case "owner_alias":
                return "各端共用的显示名称；原生原名保留在来源详情。";
            case "upstream":
            case "desktop":
                return card.titleTruncated ? "标题取自电脑侧栏，较长部分已省略。" : "标题与电脑侧栏一致。";
            case "request":
                return "电脑端标题暂未获取，按首条可读需求命名。";
            case "fallback":
                return "来源未提供可读标题，会话编号可在详情查看。";
            default:
                return "";
        }
    }

    /** “c3 · codex 高档 · 在做（改过 1 次）”。 */
    static String status(Card card) {
        StringBuilder out = new StringBuilder(card.monitoringOnly ? "电脑" : card.id);
        if (!card.agent.isEmpty()) {
            out.append(" · ").append(card.agent);
            if (!card.effort.isEmpty()) {
                out.append(' ').append(Card.effortLabel(card.effort));
            }
        }
        if (card.monitoringOnly && !card.project.isEmpty()) {
            out.append(" · ").append(card.project);
        }
        out.append(" · ").append(card.stateLabel());
        if (card.edits > 0) {
            out.append("（改过 ").append(card.edits).append(" 次）");
        }
        return out.toString();
    }

    /** 对话那一页里给任务留的一行：只说有这么件事、现在怎么样，内容在任务里看。 */
    static String pointer(Card card) {
        return "↪ 任务 " + status(card) + "\n" + card.title;
    }

    /** 任务列表里的一项：状态、标题、最新的一句。 */
    static String listItem(Card card) {
        String latest = card.outstanding() && !card.progress.isEmpty()
                ? "› " + card.progress.get(card.progress.size() - 1)
                : (card.brief.isEmpty() ? card.lastSay() : card.brief);
        latest = oneLine(latest, PREVIEW_CHARS);
        return status(card) + "\n" + card.title + (latest.isEmpty() ? "" : "\n" + latest);
    }

    static String oneLine(String text, int limit) {
        String flat = text.replace('\n', ' ').trim();
        if (flat.length() <= limit) {
            return flat;
        }
        // 不把一个代理对（表情等）切成两半。
        int end = Character.isHighSurrogate(flat.charAt(limit - 1)) ? limit - 1 : limit;
        return flat.substring(0, end) + "…";
    }

    // ---- 设备（三屏的固件）：第二屏只翻对话，第三屏是任务 ----

    /**
     * 设备第三屏的单子：还在做的任务（按开始的先后，多了留最近的），ended 为 true 时后面
     * 接着最近做完的（新的在前），一共最多 limit 件。
     */
    static List<Card> deviceTasks(List<Card> cards, int limit, boolean ended) {
        List<Card> out = new ArrayList<>();
        for (Card card : cards) {
            if (isTask(card) && card.active()) {
                out.add(card);
            }
        }
        // Select after sorting: an older approval must not disappear behind newer work.
        out.sort(Comparator.comparingInt((Card card) -> card.state.equals("waiting") ? 0 : card.queued ? 2 : 1)
                .thenComparingDouble(card -> card.startedAt > 0 ? card.startedAt : card.createdAt)
                .thenComparing(card -> card.id));
        if (out.size() > limit) out = new ArrayList<>(out.subList(0, limit));
        // 失联对话仍在当前列表，但不能把已知正在运行的任务挤出去。
        for (int index = cards.size() - 1; index >= 0 && out.size() < limit; index--) {
            Card card = cards.get(index);
            if (isTask(card) && card.outstanding() && !card.active()) {
                out.add(card);
            }
        }
        for (int index = cards.size() - 1; ended && index >= 0 && out.size() < limit; index--) {
            Card card = cards.get(index);
            if (isTask(card) && !card.outstanding()) {
                out.add(card);
            }
        }
        return out;
    }

    /** 一件做完的任务算哪个时候做完的：Runtime 记了就用它记的，旧版没记就用开卡的时间。 */
    static double endedAt(Card card) {
        return card.finishedAt > 0 ? card.finishedAt
                : (card.startedAt > 0 ? card.startedAt : card.createdAt);
    }

    /**
     * 设备第三屏下半张单子：做完的任务，最近做完的在前，最多 limit 件。只给把做完的事
     * 另列一张单子的固件（past）；这时上半张单子（deviceTasks，ended 为 false）只有在做的。
     */
    static List<Card> devicePast(List<Card> cards, int limit) {
        List<Card> out = new ArrayList<>();
        for (Card card : cards) {
            if (isTask(card) && !card.outstanding()) {
                out.add(card);
            }
        }
        // 做完的时间一样（或者都没记）时，后开的卡在前。
        Collections.sort(out, new Comparator<Card>() {
            @Override
            public int compare(Card left, Card right) {
                int byTime = Double.compare(endedAt(right), endedAt(left));
                return byTime != 0 ? byTime : Long.compare(right.order(), left.order());
            }
        });
        while (out.size() > limit) {
            out.remove(out.size() - 1);
        }
        return out;
    }

    /** 哪天做完的，写给设备分组用：今天、昨天，更早的写月和日（10-08）；不知道是空串。 */
    static String dayLabel(double endedAt, long nowSeconds, TimeZone zone) {
        if (endedAt <= 0) {
            return "";
        }
        Calendar then = Calendar.getInstance(zone, Locale.US);
        then.setTimeInMillis((long) (endedAt * 1000));
        Calendar now = Calendar.getInstance(zone, Locale.US);
        now.setTimeInMillis(nowSeconds * 1000L);
        if (sameDay(then, now)) {
            return "今天";
        }
        now.add(Calendar.DAY_OF_YEAR, -1);
        if (sameDay(then, now)) {
            return "昨天";
        }
        return String.format(Locale.US, "%02d-%02d", then.get(Calendar.MONTH) + 1,
                then.get(Calendar.DAY_OF_MONTH));
    }

    private static boolean sameDay(Calendar left, Calendar right) {
        return left.get(Calendar.YEAR) == right.get(Calendar.YEAR)
                && left.get(Calendar.DAY_OF_YEAR) == right.get(Calendar.DAY_OF_YEAR);
    }

    /** 下半张单子上的一行。 */
    static BuddyProtocol.Past devicePastItem(Card card, long nowSeconds, TimeZone zone) {
        return devicePastItem(card, nowSeconds, zone, true);
    }

    static BuddyProtocol.Past devicePastItem(Card card, long nowSeconds, TimeZone zone,
                                            boolean desktop) {
        return new BuddyProtocol.Past(card.id, card.agent, deviceTitle(card), deviceState(card, desktop),
                dayLabel(endedAt(card), nowSeconds, zone), card.effort);
    }

    /** 单子上的一行。做完的事：状态是它怎么结束的，p1 是结论（设备上没有它的卡时用）。 */
    static BuddyProtocol.Task deviceTask(Card card, long nowSeconds) {
        return deviceTask(card, nowSeconds, true);
    }

    static BuddyProtocol.Task deviceTask(Card card, long nowSeconds, boolean desktop) {
        if (card.monitoringOnly && card.state.equals("unknown")) {
            int steps = card.progress.size();
            return new BuddyProtocol.Task(card.id, card.agent, deviceTitle(card),
                    deviceState(card, desktop), 0L,
                    steps >= 2 ? card.progress.get(steps - 2)
                            : (steps == 1 ? card.progress.get(0) : card.stateLabel()),
                    steps >= 2 ? card.progress.get(steps - 1) : "", card.effort);
        }
        if (!card.active()) {
            String state = card.monitoringOnly && card.state.equals("done")
                    ? deviceState(card, desktop)
                    : (card.state.equals("failed") || card.state.equals("cancelled")
                            ? card.state : "done");
            return new BuddyProtocol.Task(card.id, card.agent, deviceTitle(card), state, 0L,
                    oneLine(card.brief.isEmpty() ? card.lastSay() : card.brief, PREVIEW_CHARS), "",
                    card.effort);
        }
        int steps = card.progress.size();
        double since = card.startedAt > 0 ? card.startedAt : card.createdAt;
        return new BuddyProtocol.Task(card.id, card.agent, deviceTitle(card),
                card.state.equals("waiting") ? "waiting" : (card.queued ? "queued" : "working"),
                since > 0 ? Math.max(0L, nowSeconds - (long) since) : 0L,
                steps >= 2 ? card.progress.get(steps - 2) : (steps == 1 ? card.progress.get(0) : ""),
                steps >= 2 ? card.progress.get(steps - 1) : "", card.effort);
    }

    /** 设备上一件任务的来回最多这么多字节、每句最多这么多字：设备的地方是所有卡合用的。 */
    static final int THREAD_BYTES = 480;
    static final int THREAD_ENTRY_CHARS = 90;

    /**
     * 一件任务在设备上的来回：第一句之后的每一句，“你：”“ds：”各占一段，旧的在前；
     * 放不下时丢掉旧的，留最新的。做完的事最后一句用简报（给小屏幕写的）。
     */
    static String deviceThread(Card card) {
        List<String> lines = new ArrayList<>();
        boolean first = true;
        for (int index = 0; index < card.entries.size(); index++) {
            Card.Entry entry = card.entries.get(index);
            boolean mine = entry.role.equals("you");
            if (mine && first) {
                first = false;  // 第一句设备单独显示
                continue;
            }
            boolean last = index == card.entries.size() - 1;
            String text = !mine && last && !card.active() && !card.brief.isEmpty()
                    ? card.brief : entry.text;
            // 设备小屏只认纯文字：手机上画的加粗、列表、分条记号去掉（Markdown.plain）。
            lines.add((mine ? "你：" : "ds：") + oneLine(mine ? text : Markdown.plain(text), THREAD_ENTRY_CHARS));
        }
        StringBuilder out = new StringBuilder();
        for (int index = lines.size() - 1; index >= 0; index--) {
            String line = lines.get(index);
            int size = line.getBytes(java.nio.charset.StandardCharsets.UTF_8).length
                    + (out.length() == 0 ? 0 : 2);
            if (out.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8).length + size
                    > THREAD_BYTES) {
                break;
            }
            out.insert(0, out.length() == 0 ? line : line + "\n\n");
        }
        return out.toString();
    }

    /**
     * 发给设备的那张卡上的话。对话：简报在前，完整的话跟在后面。任务在分开显示的固件
     * （threads）上有自己的页面，给的是它最近的来回。
     */
    static String deviceReply(Card card, boolean threads) {
        if (card.monitoringOnly) {
            return desktopReply(card);
        }
        if (threads && isTask(card)) {
            return deviceThread(card);
        }
        return BuddyProtocol.chatReply(card.active() ? "" : card.brief, Markdown.plain(card.lastSay()));
    }

    /** 小屏只放最近一轮可见来回，分别留空间给需求和回复，始终带上实际状态。 */
    private static String desktopReply(Card card) {
        String prefix = card.stateLabel() + "\n只读 · 电脑端操作";
        int request = -1;
        int reply = -1;
        for (int index = 0; index < card.entries.size(); index++) {
            if (card.entries.get(index).role.equals("you")) {
                request = index;
                reply = -1;
            } else {
                reply = index;
            }
        }
        List<String> labels = new ArrayList<>();
        List<String> snippets = new ArrayList<>();
        if (request >= 0) {
            labels.add("你：");
            snippets.add(card.entries.get(request).text);
        }
        if (reply >= 0) {
            labels.add(assistantLabel(card) + "：");
            snippets.add(card.entries.get(reply).text);
        }
        if (snippets.isEmpty()) {
            String latest = !card.progress.isEmpty()
                    ? card.progress.get(card.progress.size() - 1) : card.brief;
            if (!latest.isEmpty()) {
                labels.add("最新：");
                snippets.add(latest);
            }
        }
        if (snippets.isEmpty()) {
            return prefix;
        }
        int available = THREAD_BYTES - BuddyProtocol.utf8Length(prefix);
        for (String label : labels) {
            available -= BuddyProtocol.utf8Length(label) + 2; // 每段前空一行
        }
        int budget = Math.max(0, available / snippets.size());
        StringBuilder out = new StringBuilder(prefix);
        for (int index = 0; index < snippets.size(); index++) {
            String flat = snippets.get(index).replace('\n', ' ').trim();
            String clipped = BuddyProtocol.clip(flat, budget);
            if (!clipped.equals(flat) && budget >= 3) {
                clipped = BuddyProtocol.clip(flat, budget - 3) + "…";
            }
            out.append("\n\n").append(labels.get(index)).append(clipped);
        }
        return BuddyProtocol.clip(out.toString(), THREAD_BYTES);
    }

    /** 设备任务、历史单子的 47 字节优先留给侧栏标题；来源、项目、状态在各自栏里显示。 */
    static String deviceTitle(Card card) {
        if (!card.monitoringOnly || !card.title.isEmpty()) {
            return card.title;
        }
        return (card.project.isEmpty() ? "" : card.project + " · ") + assistantLabel(card) + " 对话";
    }

    /** 卡的需求栏优先放实际需求；旧 Runtime 无内容时仍显示电脑来源和项目。 */
    static String deviceSaid(Card card) {
        if (card.monitoringOnly) {
            for (Card.Entry entry : card.entries) {
                if (entry.role.equals("you")) {
                    return card.said();
                }
            }
            return deviceTitle(card);
        }
        return card.said();
    }

    /** 电脑本轮结束与 Runtime 任务完成在设备上也分别显示。 */
    static String deviceState(Card card) {
        return deviceState(card, true);
    }

    static String deviceState(Card card, boolean desktop) {
        if (card.monitoringOnly && card.state.equals("unknown")) {
            return desktop ? "unknown" : "cancelled";
        }
        return card.monitoringOnly && card.state.equals("done")
                ? (desktop ? "idle" : "done") : card.state;
    }

    /** 发一句话的请求体。card 不为 null 表示是在那件事里面说的：一定归到它。 */
    static String messageJson(String text, String clientId, String card) {
        return messageJson(text, clientId, card, null);
    }

    /** 同上；effort 不为空表示这句话让帮手用这一档做（Runtime 0.6.0 起认识）。 */
    static String messageJson(String text, String clientId, String card, String effort) {
        return messageJson(text, clientId, card, effort, null);
    }

    static String messageJson(String text, String clientId, String card, String effort, String agent) {
        StringBuilder body = new StringBuilder("{\"text\":");
        BuddyProtocol.quote(body, text);
        body.append(",\"client_id\":\"").append(clientId).append('"');
        if (BuddyProtocol.cardId(card)) {
            body.append(",\"card\":\"").append(card).append("\",\"pin\":true");
        }
        if (BuddyProtocol.effort(effort)) {
            body.append(",\"effort\":\"").append(effort).append('"');
        }
        if (agent != null && !agent.isEmpty() && !BuddyProtocol.cardId(card)) {
            body.append(",\"agent\":");
            BuddyProtocol.quote(body, agent);
        }
        return body.append('}').toString();
    }
}
