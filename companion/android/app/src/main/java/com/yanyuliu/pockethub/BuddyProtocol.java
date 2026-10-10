package com.yanyuliu.pockethub;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * 小幽固件说的蓝牙协议（Hardware Buddy 线协议）：一行一个 UTF-8 JSON 对象，以 \n 结尾。
 * 这个类只做字符串和字节处理，不依赖 Android，可以在电脑上直接跑测试。
 *
 * 各字段的字节上限与固件 main/buddy_types.h 一致；超出的在这里按字符边界截断，
 * 这样固件不会因为请求编号过长而拒收整条消息。
 */
public final class BuddyProtocol {
    public static final int MESSAGE_MAX = 159;
    public static final int ENTRY_MAX = 95;
    public static final int ENTRY_COUNT = 4;
    public static final int PROMPT_ID_MAX = 95;
    public static final int TOOL_MAX = 47;
    public static final int HINT_MAX = 319;
    public static final int TURN_TEXT_MAX = 3000;
    public static final int LINE_MAX = 4096;
    // “chat”和“helpers”两条消息里各字段的上限（固件的缓冲区大小减去结尾的 0）。
    public static final int REPLY_MAX = 959;
    public static final int AGENT_MAX = 23;
    public static final int STAGE_MAX = 159;
    public static final int HELPER_COUNT = 4;
    public static final int HELPER_ABOUT_MAX = 63;
    // “card”和“tasks”两条消息：卡的编号超过上限的固件不收，这里也不发。
    public static final int CARD_ID_MAX = 11;
    public static final int CARD_KEPT = 8;
    public static final int TASK_COUNT = 4;
    public static final int TASK_TITLE_MAX = 47;
    public static final int TASK_LINE_MAX = 63;
    /** 做完的事在设备第三屏最多留这么多件，一条消息里最多带这么多件。 */
    public static final int PAST_COUNT = 5;
    public static final int PAST_CHUNK = 5;
    public static final int DAY_MAX = 11;
    /** 用量：最多几条（一个账号一条），每条最多几个帮手，模型名最多多少字节。 */
    public static final int USAGE_COUNT = 4;
    public static final int USAGE_WHO = 2;
    public static final int MODEL_MAX = 19;

    private BuddyProtocol() {
    }

    /** 需要用户在设备上回答“可以 / 不行”的一条请求。 */
    public static final class Prompt {
        public final String id;
        public final String tool;
        public final String hint;

        public Prompt(String id, String tool, String hint) {
            this.id = id;
            this.tool = tool;
            this.hint = hint;
        }
    }

    /** 设备发回来的决定。 */
    public static final class Decision {
        public final String id;
        public final boolean allow;

        Decision(String id, boolean allow) {
            this.id = id;
            this.allow = allow;
        }
    }

    public static String heartbeat(int total, int running, int waiting, String message,
                                   List<String> entries, long tokens, long tokensToday,
                                   Prompt prompt) {
        StringBuilder out = new StringBuilder(512);
        out.append("{\"total\":").append(Math.max(0, total));
        out.append(",\"running\":").append(Math.max(0, running));
        out.append(",\"waiting\":").append(Math.max(0, waiting));
        out.append(",\"msg\":");
        quote(out, clip(message, MESSAGE_MAX));
        out.append(",\"entries\":[");
        int count = entries == null ? 0 : Math.min(entries.size(), ENTRY_COUNT);
        for (int index = 0; index < count; index++) {
            if (index > 0) {
                out.append(',');
            }
            quote(out, clip(entries.get(index), ENTRY_MAX));
        }
        out.append("],\"tokens\":").append(Math.max(0L, tokens));
        out.append(",\"tokens_today\":").append(Math.max(0L, tokensToday));
        if (prompt != null && prompt.id != null && !prompt.id.isEmpty()
                && utf8Length(prompt.id) <= PROMPT_ID_MAX) {
            out.append(",\"prompt\":{\"id\":");
            quote(out, prompt.id);
            out.append(",\"tool\":");
            quote(out, clip(prompt.tool, TOOL_MAX));
            out.append(",\"hint\":");
            quote(out, clip(prompt.hint, HINT_MAX));
            out.append('}');
        }
        out.append("}\n");
        return out.toString();
    }

    /** 一条完整消息的正文，显示在设备的“最新回复”页。 */
    public static String turn(String text) {
        StringBuilder out = new StringBuilder(256);
        out.append("{\"evt\":\"turn\",\"role\":\"assistant\",\"content\":[{\"type\":\"text\",\"text\":");
        quote(out, clip(text, TURN_TEXT_MAX));
        out.append("}]}\n");
        return out.toString();
    }

    public static String time(long epochSeconds, int offsetSeconds) {
        return "{\"time\":[" + epochSeconds + "," + offsetSeconds + "]}\n";
    }

    /** 连上后告诉设备：这头是小幽中枢，能接收语音。旧固件不认识这一行，会回一个错误应答。 */
    public static String hubHello() {
        return "{\"cmd\":\"hub\",\"voice\":true}\n";
    }

    /**
     * 和小幽的对话现在走到哪一步了。phase 是 idle、thinking、helper、done、failed 之一；
     * said 是我说的话，reply 是小幽的话，agent 是正在替她干活的帮手，stage 是她转交时说的那句，
     * mood 是 idle、busy、ask、happy、oops 之一。只有连接时声明了 chat 的固件认识这一行。
     */
    public static String chat(String phase, String said, String reply, String agent,
                              String stage, String mood) {
        return chat(phase, said, reply, agent, stage, mood, null, -1);
    }

    /**
     * 同上，给认识卡的固件：card 是这句话归到的那张卡（没有就不带），doing 是后台还在做的
     * 事的件数（小于 0 表示不带）。
     */
    public static String chat(String phase, String said, String reply, String agent,
                              String stage, String mood, String card, int doing) {
        return chat(phase, said, reply, agent, stage, mood, card, doing, null);
    }

    /** 固件认识的档位名；别的不发。 */
    public static boolean effort(String effort) {
        return "minimal".equals(effort) || "low".equals(effort) || "medium".equals(effort)
                || "high".equals(effort) || "xhigh".equals(effort) || "max".equals(effort);
    }

    private static void appendEffort(StringBuilder out, String effort) {
        if (effort(effort)) {
            out.append(",\"eff\":\"").append(effort).append('"');
        }
    }

    /** 同上，再带上帮手干这件事用的档位（eff）；只发给声明了 usage 的固件。 */
    public static String chat(String phase, String said, String reply, String agent,
                              String stage, String mood, String card, int doing, String effort) {
        StringBuilder out = new StringBuilder(256);
        out.append("{\"cmd\":\"chat\",\"phase\":");
        quote(out, phase == null ? "idle" : phase);
        out.append(",\"said\":");
        quote(out, clip(said, MESSAGE_MAX));
        out.append(",\"reply\":");
        quote(out, clip(reply, REPLY_MAX));
        out.append(",\"agent\":");
        quote(out, clip(agent, AGENT_MAX));
        out.append(",\"stage\":");
        quote(out, clip(stage, STAGE_MAX));
        out.append(",\"mood\":");
        quote(out, mood == null ? "idle" : mood);
        if (cardId(card)) {
            out.append(",\"card\":");
            quote(out, card);
        }
        if (doing >= 0) {
            out.append(",\"doing\":").append(doing);
        }
        appendEffort(out, effort);
        out.append("}\n");
        return out.toString();
    }

    /** 固件收得下的卡编号：不为空、不超过上限。更长的截断了就成了另一张卡，所以不发。 */
    public static boolean cardId(String id) {
        return id != null && !id.isEmpty() && utf8Length(id) <= CARD_ID_MAX;
    }

    /**
     * 一件事的卡：at 是开始的“时:分”，state 是 working、waiting、done、failed、cancelled、
     * talking 之一，said 是这件事的第一句话，reply 是小幽最新的话，edits 是补充或改过几次。
     * 编号太长时返回 null（不发）。
     */
    public static String card(String id, String at, String state, String agent, int edits,
                              String said, String reply) {
        return card(id, at, state, agent, edits, said, reply, null);
    }

    /** 同上，再带上这件事用的档位；只发给声明了 usage 的固件。 */
    public static String card(String id, String at, String state, String agent, int edits,
                              String said, String reply, String effort) {
        if (!cardId(id)) {
            return null;
        }
        StringBuilder out = new StringBuilder(256);
        out.append("{\"cmd\":\"card\",\"id\":");
        quote(out, id);
        out.append(",\"at\":");
        quote(out, clip(at, 5));
        out.append(",\"state\":");
        quote(out, state == null ? "done" : state);
        out.append(",\"agent\":");
        quote(out, clip(agent, AGENT_MAX));
        out.append(",\"edits\":").append(Math.max(0, Math.min(edits, 99)));
        out.append(",\"said\":");
        quote(out, clip(said, MESSAGE_MAX));
        out.append(",\"reply\":");
        quote(out, clip(reply, REPLY_MAX));
        appendEffort(out, effort);
        out.append("}\n");
        return out.toString();
    }

    /** 让设备忘掉所有的卡；接着把最近的几张重新发一遍。 */
    public static String cardClear() {
        return "{\"cmd\":\"card\",\"clear\":true}\n";
    }

    /** 交给帮手的一件事，设备第三屏上的一行。 */
    public static final class Task {
        public final String id;
        public final String agent;
        public final String title;
        /** working、waiting、queued；做完的是 done、failed、cancelled（只发给分开显示的固件）。 */
        public final String state;
        public final long seconds;
        /** 最近两步：p1 在前，p2 是最新的；只有一步时放在 p1。 */
        public final String p1;
        public final String p2;
        /** 这件事用的档位；没有是 null。 */
        public final String effort;

        public Task(String id, String agent, String title, String state, long seconds,
                    String p1, String p2) {
            this(id, agent, title, state, seconds, p1, p2, null);
        }

        public Task(String id, String agent, String title, String state, long seconds,
                    String p1, String p2, String effort) {
            this.effort = effort;
            this.id = id;
            this.agent = agent;
            this.title = title;
            this.state = state;
            this.seconds = seconds;
            this.p1 = p1;
            this.p2 = p2;
        }
    }

    /** 正在做的事，最多带 TASK_COUNT 件；编号太长的跳过。空表表示没有在做的事。 */
    public static String tasks(List<Task> tasks) {
        StringBuilder out = new StringBuilder(256);
        out.append("{\"cmd\":\"tasks\",\"list\":[");
        int count = 0;
        if (tasks != null) {
            for (Task task : tasks) {
                if (count >= TASK_COUNT) {
                    break;
                }
                if (task == null || !cardId(task.id)) {
                    continue;
                }
                if (count++ > 0) {
                    out.append(',');
                }
                out.append("{\"id\":");
                quote(out, task.id);
                out.append(",\"agent\":");
                quote(out, clip(task.agent, AGENT_MAX));
                out.append(",\"title\":");
                quote(out, clip(task.title, TASK_TITLE_MAX));
                out.append(",\"state\":");
                quote(out, task.state == null ? "working" : task.state);
                out.append(",\"secs\":").append(Math.max(0L, task.seconds));
                out.append(",\"p1\":");
                quote(out, clip(task.p1, TASK_LINE_MAX));
                out.append(",\"p2\":");
                quote(out, clip(task.p2, TASK_LINE_MAX));
                appendEffort(out, task.effort);
                out.append('}');
            }
        }
        out.append("]}\n");
        return out.toString();
    }

    /** 做完的一件事，设备第三屏下半张单子上的一行：没有进展，多一个“哪天做完的”。 */
    public static final class Past {
        public final String id;
        public final String agent;
        public final String title;
        /** done、failed、cancelled。 */
        public final String state;
        /** 今天、昨天、10-08：设备原样显示，同一天的排在一个小标题下面。 */
        public final String day;
        public final String effort;

        public Past(String id, String agent, String title, String state, String day,
                    String effort) {
            this.id = id;
            this.agent = agent;
            this.title = title;
            this.state = state;
            this.day = day;
            this.effort = effort;
        }
    }

    /**
     * 做完的事分几条发：每条最多 PAST_CHUNK 件，带着它们从单子的第几件起（at）、单子一共
     * 多长（n）。设备没有多余的内存，一条消息不能随历史变长。单子最多 PAST_COUNT 件，编号
     * 太长的不算在里面；空单子也发一条，让设备把旧的清掉。只发给声明了 past 的固件。
     */
    public static List<String> past(List<Past> items) {
        List<Past> kept = new ArrayList<>();
        if (items != null) {
            for (Past item : items) {
                if (item != null && cardId(item.id) && kept.size() < PAST_COUNT) {
                    kept.add(item);
                }
            }
        }
        List<String> lines = new ArrayList<>();
        int at = 0;
        do {
            StringBuilder out = new StringBuilder(256);
            out.append("{\"cmd\":\"past\",\"at\":").append(at).append(",\"n\":")
                    .append(kept.size()).append(",\"list\":[");
            for (int index = at; index < kept.size() && index < at + PAST_CHUNK; index++) {
                Past item = kept.get(index);
                if (index > at) {
                    out.append(',');
                }
                out.append("{\"id\":");
                quote(out, item.id);
                out.append(",\"agent\":");
                quote(out, clip(item.agent, AGENT_MAX));
                out.append(",\"title\":");
                quote(out, clip(item.title, TASK_TITLE_MAX));
                out.append(",\"state\":");
                quote(out, "failed".equals(item.state) || "cancelled".equals(item.state)
                        ? item.state : "done");
                out.append(",\"day\":");
                quote(out, clip(item.day, DAY_MAX));
                appendEffort(out, item.effort);
                out.append('}');
            }
            out.append("]}\n");
            lines.add(out.toString());
            at += PAST_CHUNK;
        } while (at < kept.size());
        return lines;
    }

    /** 设备想要的那张卡（它单子上有这件事、手里没有它的卡）；这一行不是这个意思时返回 null。 */
    public static String parseWant(String line) {
        Map<String, String> fields = parseFlatObject(line);
        if (fields == null || !"want".equals(fields.get("evt"))) {
            return null;
        }
        String card = fields.get("card");
        return cardId(card) ? card : null;
    }

    /** 用量屏上的一个帮手：名字、模型、平时的档位、此刻在做几件事。 */
    public static final class UsageWho {
        public final String name;
        public final String model;
        public final String effort;
        public final int running;

        public UsageWho(String name, String model, String effort, int running) {
            this.name = name;
            this.model = model;
            this.effort = effort;
            this.running = running;
        }
    }

    /**
     * 用量屏上的一条：一个订阅账号还剩多少，和跑在它上面的帮手；或者一个按量计费的帮手
     * （state 是 na，没有额度）。left 是剩下的百分比，不知道是 -1；reset 是重置的时间
     * （Unix 秒），不知道是 0；age 是这些数是多少秒之前的，不知道是 -1。
     */
    public static final class UsageEntry {
        public final String state;
        public final int left5h;
        public final long reset5h;
        public final int left7d;
        public final long reset7d;
        public final long age;
        public final List<UsageWho> who;

        public UsageEntry(String state, int left5h, long reset5h, int left7d, long reset7d,
                          long age, List<UsageWho> who) {
            this.state = state;
            this.left5h = left5h;
            this.reset5h = reset5h;
            this.left7d = left7d;
            this.reset7d = reset7d;
            this.age = age;
            this.who = who;
        }
    }

    /** 第四屏的内容。最多 USAGE_COUNT 条，每条最多 USAGE_WHO 个帮手；没有帮手的条不发。 */
    public static String usage(List<UsageEntry> entries) {
        StringBuilder out = new StringBuilder(256);
        out.append("{\"cmd\":\"usage\",\"list\":[");
        int count = 0;
        if (entries != null) {
            for (UsageEntry entry : entries) {
                if (count >= USAGE_COUNT) {
                    break;
                }
                if (entry == null || entry.who == null) {
                    continue;
                }
                StringBuilder who = new StringBuilder();
                int helpers = 0;
                for (UsageWho one : entry.who) {
                    if (one == null || one.name == null || one.name.isEmpty()
                            || helpers >= USAGE_WHO) {
                        continue;
                    }
                    if (helpers++ > 0) {
                        who.append(',');
                    }
                    who.append("{\"n\":");
                    quote(who, clip(one.name, AGENT_MAX));
                    if (one.model != null && !one.model.isEmpty()) {
                        who.append(",\"m\":");
                        quote(who, clip(one.model, MODEL_MAX));
                    }
                    appendEffort(who, one.effort);
                    if (one.running > 0) {
                        who.append(",\"run\":").append(Math.min(one.running, 255));
                    }
                    who.append('}');
                }
                if (helpers == 0) {
                    continue;
                }
                if (count++ > 0) {
                    out.append(',');
                }
                String state = entry.state;
                out.append("{\"st\":\"").append("ok".equals(state) || "warn".equals(state)
                        || "out".equals(state) || "na".equals(state) ? state : "unknown").append('"');
                if (entry.left5h >= 0 && entry.left5h <= 100) {
                    out.append(",\"w5\":").append(entry.left5h);
                }
                if (entry.reset5h > 0) {
                    out.append(",\"r5\":").append(entry.reset5h);
                }
                if (entry.left7d >= 0 && entry.left7d <= 100) {
                    out.append(",\"w7\":").append(entry.left7d);
                }
                if (entry.reset7d > 0) {
                    out.append(",\"r7\":").append(entry.reset7d);
                }
                if (entry.age >= 0) {
                    out.append(",\"age\":").append(entry.age);
                }
                out.append(",\"who\":[").append(who).append("]}");
            }
        }
        out.append("]}\n");
        return out.toString();
    }

    /** 小屏幕上先看结论：简报在前，完整回复跟在后面；两者一样或者回复以简报开头时只留回复。 */
    public static String chatReply(String brief, String reply) {
        String shortText = brief == null ? "" : brief.trim();
        String fullText = reply == null ? "" : reply.trim();
        if (shortText.isEmpty() || fullText.startsWith(shortText)) {
            return fullText;
        }
        if (fullText.isEmpty()) {
            return shortText;
        }
        return shortText + "\n\n" + fullText;
    }

    /** 小幽能找的帮手：每项是 {名字, 一句说明}，最多带 HELPER_COUNT 个，没有名字的跳过。 */
    public static String helpers(List<String[]> helpers) {
        StringBuilder out = new StringBuilder(256);
        out.append("{\"cmd\":\"helpers\",\"list\":[");
        int count = 0;
        if (helpers != null) {
            for (String[] helper : helpers) {
                if (count >= HELPER_COUNT) {
                    break;
                }
                String name = helper == null || helper.length == 0 ? "" : clip(helper[0], AGENT_MAX);
                if (name.isEmpty()) {
                    continue;
                }
                if (count++ > 0) {
                    out.append(',');
                }
                out.append("{\"name\":");
                quote(out, name);
                out.append(",\"about\":");
                quote(out, clip(helper.length > 1 ? helper[1] : "", HELPER_ABOUT_MAX));
                out.append('}');
            }
        }
        out.append("]}\n");
        return out.toString();
    }

    /** 设备发来的语音控制行里的 state（start / end / cancel）；不是语音控制行返回 null。 */
    public static String parseVoiceState(String line) {
        Map<String, String> fields = parseFlatObject(line);
        if (fields == null || !"voice".equals(fields.get("cmd"))) {
            return null;
        }
        String state = fields.get("state");
        return "start".equals(state) || "end".equals(state) || "cancel".equals(state)
                ? state : null;
    }

    /**
     * 设备的“录音开始”那一行里带的卡：按下时屏幕上的那件事，这句话是对它说的。
     * 没带、或者这一行不是录音开始时返回 null。
     */
    public static String parseVoiceCard(String line) {
        Map<String, String> fields = parseFlatObject(line);
        if (fields == null || !"voice".equals(fields.get("cmd"))
                || !"start".equals(fields.get("state"))) {
            return null;
        }
        String card = fields.get("card");
        return cardId(card) ? card : null;
    }

    /** “录音开始”那一行说这句话是在那件事里面说的（设备第三屏选中的那件）：一定归到它。 */
    public static boolean parseVoicePin(String line) {
        Map<String, String> fields = parseFlatObject(line);
        return parseVoiceCard(line) != null && "true".equals(fields.get("pin"));
    }

    /** 设备声明它有第四屏：认识 usage，也认识 chat、card、tasks 里的 eff。 */
    public static boolean hubAckHasUsage(String line) {
        Map<String, String> fields = parseFlatObject(line);
        return hubAckHasCards(line) && "true".equals(fields.get("usage"));
    }

    /** 设备声明它把做完的事另列一张单子（past），手里没有的卡会来要（want）。 */
    public static boolean hubAckHasPast(String line) {
        Map<String, String> fields = parseFlatObject(line);
        return hubAckHasThreads(line) && "true".equals(fields.get("past"));
    }

    /** 设备声明它把对话和任务分开显示：任务单子里可以带做完的事。 */
    public static boolean hubAckHasThreads(String line) {
        Map<String, String> fields = parseFlatObject(line);
        return hubAckHasCards(line) && "true".equals(fields.get("threads"));
    }

    /** 设备对 hubHello 的应答里有没有声明它认识 card 和 tasks 这两条消息。 */
    public static boolean hubAckHasCards(String line) {
        Map<String, String> fields = parseFlatObject(line);
        return fields != null && "hub".equals(fields.get("ack")) && "true".equals(fields.get("ok"))
                && "true".equals(fields.get("cards"));
    }

    /** 设备对 hubHello 的应答：true 能说话，false 是旧固件，null 表示这一行不是应答。 */
    public static Boolean parseHubAck(String line) {
        Map<String, String> fields = parseFlatObject(line);
        if (fields == null || !"hub".equals(fields.get("ack"))) {
            return null;
        }
        return "true".equals(fields.get("ok"));
    }

    /** 设备对 hubHello 的应答里有没有声明它认识 chat 和 helpers 这两条消息。 */
    public static boolean hubAckHasChat(String line) {
        Map<String, String> fields = parseFlatObject(line);
        return fields != null && "hub".equals(fields.get("ack")) && "true".equals(fields.get("ok"))
                && "true".equals(fields.get("chat"));
    }

    /**
     * 取出一层对象里某个键下面的对象数组，每个对象按 parseFlatObject 解析。
     * 用来读 Runtime 的 /v1/agents。格式不对或者没有这个键时返回空表。
     */
    public static List<Map<String, String>> parseObjectArray(String text, String key) {
        List<Map<String, String>> items = new ArrayList<>();
        if (text == null || key == null) {
            return items;
        }
        int[] at = {0};
        skipSpace(text, at);
        if (at[0] >= text.length() || text.charAt(at[0]) != '{') {
            return items;
        }
        at[0]++;
        while (at[0] < text.length()) {
            skipSpace(text, at);
            String name = readString(text, at);
            if (name == null) {
                return items;
            }
            skipSpace(text, at);
            if (at[0] >= text.length() || text.charAt(at[0]) != ':') {
                return items;
            }
            at[0]++;
            skipSpace(text, at);
            if (name.equals(key) && at[0] < text.length() && text.charAt(at[0]) == '[') {
                at[0]++;
                while (at[0] < text.length()) {
                    skipSpace(text, at);
                    if (at[0] < text.length() && text.charAt(at[0]) == ']') {
                        return items;
                    }
                    int start = at[0];
                    if (!skipValue(text, at) || at[0] <= start) {
                        return items;
                    }
                    Map<String, String> item = parseFlatObject(text.substring(start, at[0]));
                    if (item != null) {
                        items.add(item);
                    }
                    if (at[0] < text.length() && text.charAt(at[0]) == ',') {
                        at[0]++;
                    }
                }
                return items;
            }
            if (at[0] < text.length() && text.charAt(at[0]) == '"') {
                if (readString(text, at) == null) {
                    return items;
                }
            } else if (!skipValue(text, at)) {
                return items;
            }
            skipSpace(text, at);
            if (at[0] >= text.length() || text.charAt(at[0]) != ',') {
                return items;
            }
            at[0]++;
        }
        return items;
    }

    /** 解析设备发来的一行；不是权限决定就返回 null。 */
    public static Decision parseDecision(String line) {
        Map<String, String> fields = parseFlatObject(line);
        if (fields == null || !"permission".equals(fields.get("cmd"))) {
            return null;
        }
        String id = fields.get("id");
        String decision = fields.get("decision");
        if (id == null || id.isEmpty() || decision == null) {
            return null;
        }
        if ("once".equals(decision) || "always".equals(decision)) {
            return new Decision(id, true);
        }
        if ("deny".equals(decision)) {
            return new Decision(id, false);
        }
        return null;
    }

    // ---- 经蓝牙换固件（固件的 main/pocket_update_core.h）----

    /** 数据帧的第一个字节。0xFE 不会出现在 UTF-8 里，设备靠它把数据帧和文本行分开。 */
    public static final int FW_MAGIC = 0xFE;
    public static final int FW_HEADER_BYTES = 5;
    /** 一次写入最多带这么多字节（含帧头）。 */
    public static final int FW_FRAME_MAX = 244;

    /** {"cmd":"fw","op":…}：info、end、abort、confirm、rollback 这几个不带别的参数。 */
    public static String fwOp(String op) {
        StringBuilder out = new StringBuilder("{\"cmd\":\"fw\",\"op\":");
        quote(out, op);
        return out.append("}\n").toString();
    }

    /** 开始传一份镜像：多大、SHA-256 是什么（64 位十六进制）。 */
    public static String fwBegin(int size, String sha256) {
        StringBuilder out = new StringBuilder("{\"cmd\":\"fw\",\"op\":\"begin\",\"size\":");
        out.append(Math.max(0, size)).append(",\"sha256\":");
        quote(out, sha256);
        return out.append("}\n").toString();
    }

    /** 一帧固件数据：[0xFE][偏移，4 字节小端][image 里从 offset 开始的 length 字节]。 */
    public static byte[] fwFrame(byte[] image, int offset, int length) {
        byte[] frame = new byte[FW_HEADER_BYTES + length];
        frame[0] = (byte) FW_MAGIC;
        frame[1] = (byte) offset;
        frame[2] = (byte) (offset >> 8);
        frame[3] = (byte) (offset >> 16);
        frame[4] = (byte) (offset >> 24);
        System.arraycopy(image, offset, frame, FW_HEADER_BYTES, length);
        return frame;
    }

    /** 设备关于换固件说的一行：对某个 op 的应答，或者传输中的进度。 */
    public static final class FwLine {
        /** true 是应答（"ack"），false 是进度（"evt"）。 */
        public final boolean ack;
        /** 应答的是哪个 op；旧固件不认识 fw，应答里没有 op，这里是空串。 */
        public final String op;
        public final boolean ok;
        public final String error;
        private final Map<String, String> fields;

        FwLine(boolean ack, Map<String, String> fields) {
            this.ack = ack;
            this.fields = fields;
            String name = fields.get("op");
            this.op = name == null ? "" : name;
            this.ok = !ack || "true".equals(fields.get("ok"));
            String reason = fields.get("error");
            this.error = reason == null ? "" : reason;
        }

        public String text(String key) {
            String value = fields.get(key);
            return value == null ? "" : value;
        }

        /** 非负整数；没有这一项或者写得不对时返回 fallback。 */
        public int number(String key, int fallback) {
            String value = fields.get(key);
            if (value == null || value.isEmpty() || value.length() > 10) {
                return fallback;
            }
            long total = 0;
            for (int index = 0; index < value.length(); index++) {
                char digit = value.charAt(index);
                if (digit < '0' || digit > '9') {
                    return fallback;
                }
                total = total * 10 + (digit - '0');
            }
            return total > Integer.MAX_VALUE ? fallback : (int) total;
        }

        public boolean flag(String key) {
            return "true".equals(fields.get(key));
        }
    }

    /** 这一行是不是设备关于换固件说的；不是返回 null。 */
    public static FwLine parseFw(String line) {
        Map<String, String> fields = parseFlatObject(line);
        if (fields == null) {
            return null;
        }
        if ("fw".equals(fields.get("ack"))) {
            return new FwLine(true, fields);
        }
        return "fw".equals(fields.get("evt")) ? new FwLine(false, fields) : null;
    }

    /** 把一行切成不超过 size 字节的若干段，按顺序写入蓝牙特征。 */
    public static List<byte[]> chunk(String line, int size) {
        byte[] bytes = line.getBytes(StandardCharsets.UTF_8);
        List<byte[]> parts = new ArrayList<>();
        int step = Math.max(1, size);
        for (int offset = 0; offset < bytes.length; offset += step) {
            int length = Math.min(step, bytes.length - offset);
            byte[] part = new byte[length];
            System.arraycopy(bytes, offset, part, 0, length);
            parts.add(part);
        }
        return parts;
    }

    /** 把蓝牙通知里零散到达的字节拼回一行一行的文本。 */
    public static final class LineAssembler {
        private final ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        private boolean discarding;

        public List<String> feed(byte[] data) {
            List<String> lines = new ArrayList<>();
            if (data == null) {
                return lines;
            }
            for (byte value : data) {
                if (value == '\n') {
                    if (!discarding && buffer.size() > 0) {
                        lines.add(new String(buffer.toByteArray(), StandardCharsets.UTF_8));
                    }
                    buffer.reset();
                    discarding = false;
                } else if (!discarding) {
                    if (buffer.size() >= LINE_MAX) {
                        // 超长的一行整行丢弃，直到下一个换行。
                        buffer.reset();
                        discarding = true;
                    } else {
                        buffer.write(value);
                    }
                }
            }
            return lines;
        }

        public void reset() {
            buffer.reset();
            discarding = false;
        }
    }

    public static int utf8Length(String text) {
        return text == null ? 0 : text.getBytes(StandardCharsets.UTF_8).length;
    }

    /** 截到不超过 maxBytes 个 UTF-8 字节，不切断字符；换行和制表符换成空格以外的保留。 */
    public static String clip(String text, int maxBytes) {
        if (text == null) {
            return "";
        }
        StringBuilder out = new StringBuilder();
        int used = 0;
        for (int index = 0; index < text.length(); ) {
            int codePoint = text.codePointAt(index);
            int width = codePoint < 0x80 ? 1 : codePoint < 0x800 ? 2 : codePoint < 0x10000 ? 3 : 4;
            if (used + width > maxBytes) {
                break;
            }
            // 落单的代理项不是合法文本，固件会因此拒收整行，直接跳过。
            if (codePoint >= 0xD800 && codePoint <= 0xDFFF) {
                index += Character.charCount(codePoint);
                continue;
            }
            out.appendCodePoint(codePoint);
            used += width;
            index += Character.charCount(codePoint);
        }
        return out.toString();
    }

    static void quote(StringBuilder out, String text) {
        out.append('"');
        for (int index = 0; index < text.length(); index++) {
            char value = text.charAt(index);
            switch (value) {
                case '"':
                    out.append("\\\"");
                    break;
                case '\\':
                    out.append("\\\\");
                    break;
                case '\n':
                    out.append("\\n");
                    break;
                case '\r':
                    out.append("\\r");
                    break;
                case '\t':
                    out.append("\\t");
                    break;
                default:
                    if (value == 0) {
                        // 固件拒收带 \u0000 的行。
                        out.append(' ');
                    } else if (value < 0x20) {
                        out.append(String.format("\\u%04x", (int) value));
                    } else {
                        out.append(value);
                    }
            }
        }
        out.append('"');
    }

    /**
     * 只认一层的 JSON 对象。字符串原样取出；数字和 true / false 取出它们写在 JSON 里的样子
     * （"3"、"true"）；null、嵌套的对象和数组跳过，所以值为 null 的键查不到。格式不对返回 null。
     */
    static Map<String, String> parseFlatObject(String line) {
        if (line == null) {
            return null;
        }
        Map<String, String> fields = new HashMap<>();
        int[] at = {0};
        skipSpace(line, at);
        if (at[0] >= line.length() || line.charAt(at[0]) != '{') {
            return null;
        }
        at[0]++;
        skipSpace(line, at);
        if (at[0] < line.length() && line.charAt(at[0]) == '}') {
            return fields;
        }
        while (at[0] < line.length()) {
            skipSpace(line, at);
            String key = readString(line, at);
            if (key == null) {
                return null;
            }
            skipSpace(line, at);
            if (at[0] >= line.length() || line.charAt(at[0]) != ':') {
                return null;
            }
            at[0]++;
            skipSpace(line, at);
            if (at[0] < line.length() && line.charAt(at[0]) == '"') {
                String value = readString(line, at);
                if (value == null) {
                    return null;
                }
                fields.put(key, value);
            } else {
                int start = at[0];
                if (!skipValue(line, at)) {
                    return null;
                }
                String raw = line.substring(start, at[0]).trim();
                if (isNumberOrBoolean(raw)) {
                    fields.put(key, raw);
                }
            }
            skipSpace(line, at);
            if (at[0] >= line.length()) {
                return null;
            }
            char next = line.charAt(at[0]++);
            if (next == '}') {
                return fields;
            }
            if (next != ',') {
                return null;
            }
        }
        return null;
    }

    private static boolean isNumberOrBoolean(String raw) {
        if (raw.equals("true") || raw.equals("false")) {
            return true;
        }
        if (raw.isEmpty() || raw.length() > 24) {
            return false;
        }
        for (int index = 0; index < raw.length(); index++) {
            char value = raw.charAt(index);
            boolean digit = value >= '0' && value <= '9';
            if (!digit && value != '-' && value != '+' && value != '.' && value != 'e' && value != 'E') {
                return false;
            }
        }
        char first = raw.charAt(0);
        return first == '-' || (first >= '0' && first <= '9');
    }

    private static void skipSpace(String line, int[] at) {
        while (at[0] < line.length() && Character.isWhitespace(line.charAt(at[0]))) {
            at[0]++;
        }
    }

    private static String readString(String line, int[] at) {
        if (at[0] >= line.length() || line.charAt(at[0]) != '"') {
            return null;
        }
        StringBuilder out = new StringBuilder();
        at[0]++;
        while (at[0] < line.length()) {
            char value = line.charAt(at[0]++);
            if (value == '"') {
                return out.toString();
            }
            if (value != '\\') {
                out.append(value);
                continue;
            }
            if (at[0] >= line.length()) {
                return null;
            }
            char escaped = line.charAt(at[0]++);
            switch (escaped) {
                case 'n':
                    out.append('\n');
                    break;
                case 't':
                    out.append('\t');
                    break;
                case 'r':
                    out.append('\r');
                    break;
                case 'b':
                    out.append('\b');
                    break;
                case 'f':
                    out.append('\f');
                    break;
                case 'u':
                    if (at[0] + 4 > line.length()) {
                        return null;
                    }
                    try {
                        out.append((char) Integer.parseInt(line.substring(at[0], at[0] + 4), 16));
                    } catch (NumberFormatException error) {
                        return null;
                    }
                    at[0] += 4;
                    break;
                default:
                    out.append(escaped);
            }
        }
        return null;
    }

    /** 跳过一个非字符串的值（数字、true/false/null、嵌套的对象或数组）。 */
    private static boolean skipValue(String line, int[] at) {
        int depth = 0;
        boolean inString = false;
        while (at[0] < line.length()) {
            char value = line.charAt(at[0]);
            if (inString) {
                if (value == '\\') {
                    at[0]++;
                } else if (value == '"') {
                    inString = false;
                }
            } else if (value == '"') {
                inString = true;
            } else if (value == '{' || value == '[') {
                depth++;
            } else if (value == '}' || value == ']') {
                if (depth == 0) {
                    return true;
                }
                depth--;
            } else if (value == ',' && depth == 0) {
                return true;
            }
            at[0]++;
        }
        return false;
    }
}
