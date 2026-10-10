package com.yanyuliu.pockethub;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Calendar;
import java.util.Collections;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TimeZone;

/**
 * Runtime 报的用量：每个订阅账号的两个额度窗口各剩多少，每个帮手用哪个模型、平时哪一档、
 * 此刻在做几件事。纯 Java，不依赖安卓。
 *
 * 内容来自 /v1/feed 里的 usage，不可变；变了就换一个新的对象。
 */
final class Usage {
    /** 一个订阅账号（几个帮手可以共用一个）。 */
    static final class Account {
        final String id;
        /** ok、warn、out、unknown。 */
        final String state;
        /** 5 小时窗口和本周窗口各剩百分之几（不知道是 -1）、什么时候重置（Unix 秒，不知道是 0）。 */
        final int left5h;
        final long reset5h;
        final int left7d;
        final long reset7d;
        /** 这些数是什么时候取到的（Runtime 的钟，Unix 秒）；没取到过是 0。 */
        final double updatedAt;
        final List<String> agents;
        final String note;

        Account(String id, String state, int left5h, long reset5h, int left7d, long reset7d,
                double updatedAt, List<String> agents, String note) {
            this.id = id;
            this.state = state;
            this.left5h = left5h;
            this.reset5h = reset5h;
            this.left7d = left7d;
            this.reset7d = reset7d;
            this.updatedAt = updatedAt;
            this.agents = Collections.unmodifiableList(agents);
            this.note = note;
        }
    }

    /** 一个帮手。 */
    static final class Agent {
        final String name;
        /** 实际用的模型：正在做的那一轮的，否则上一次的，否则配置里写的；都没有是空串。 */
        final String model;
        /** 平时的档位；不能选档的帮手是空串。 */
        final String effort;
        /** 派活时可以选的档。 */
        final List<String> efforts;
        /** 它算在哪个订阅账号上；按量计费的是空串。 */
        final String account;
        final int running;

        Agent(String name, String model, String effort, List<String> efforts, String account,
              int running) {
            this.name = name;
            this.model = model;
            this.effort = effort;
            this.efforts = Collections.unmodifiableList(efforts);
            this.account = account;
            this.running = running;
        }
    }

    /** Runtime 给这份用量编的号：带着它去等下一次变化。 */
    final long rev;
    /** Runtime 给出这份用量时它的钟（Unix 秒）。 */
    final double at;
    final List<Account> accounts;
    final List<Agent> agents;

    private Usage(long rev, double at, List<Account> accounts, List<Agent> agents) {
        this.rev = rev;
        this.at = at;
        this.accounts = Collections.unmodifiableList(accounts);
        this.agents = Collections.unmodifiableList(agents);
    }

    private static int percent(Map<String, Object> window) {
        Object left = window == null ? null : window.get("left");
        if (!(left instanceof Double)) {
            return -1;
        }
        double value = (Double) left;
        return value >= 0 && value <= 100 ? (int) Math.round(value) : -1;
    }

    private static List<String> strings(Map<String, Object> raw, String key) {
        List<String> out = new ArrayList<>();
        for (Object item : Json.list(raw, key)) {
            if (item instanceof String && !((String) item).isEmpty()) {
                out.add((String) item);
            }
        }
        return out;
    }

    /** 旧版 Runtime 的 feed 里没有 usage：返回 null。 */
    static Usage from(Object value) {
        if (!(value instanceof Map)) {
            return null;
        }
        Map<String, Object> raw = Json.object(value);
        List<Account> accounts = new ArrayList<>();
        for (Object item : Json.list(raw, "accounts")) {
            Map<String, Object> account = Json.object(item);
            String id = Json.string(account, "id");
            if (id.isEmpty()) {
                continue;
            }
            int left5h = -1;
            int left7d = -1;
            long reset5h = 0;
            long reset7d = 0;
            for (Object each : Json.list(account, "windows")) {
                Map<String, Object> window = Json.object(each);
                long reset = Math.max(0L, (long) Json.number(window, "resets_at"));
                if ("5h".equals(Json.string(window, "kind"))) {
                    left5h = percent(window);
                    reset5h = reset;
                } else if ("7d".equals(Json.string(window, "kind"))) {
                    left7d = percent(window);
                    reset7d = reset;
                }
            }
            String state = Json.string(account, "state");
            accounts.add(new Account(id, state.isEmpty() ? "unknown" : state, left5h, reset5h,
                    left7d, reset7d, Json.number(account, "updated_at"),
                    strings(account, "agents"), Json.string(account, "note")));
        }
        List<Agent> agents = new ArrayList<>();
        for (Object item : Json.list(raw, "agents")) {
            Map<String, Object> agent = Json.object(item);
            String name = Json.string(agent, "name");
            if (name.isEmpty()) {
                continue;
            }
            String now = Json.string(agent, "now");
            agents.add(new Agent(name, now.isEmpty() ? Json.string(agent, "model") : now,
                    Json.string(agent, "effort"), strings(agent, "efforts"),
                    Json.string(agent, "account"), (int) Json.number(agent, "running")));
        }
        return new Usage((long) Json.number(raw, "rev"), Json.number(raw, "at"), accounts, agents);
    }

    Agent agent(String name) {
        for (Agent agent : agents) {
            if (agent.name.equals(name)) {
                return agent;
            }
        }
        return null;
    }

    /** 没有账号、也没有哪个帮手报了模型：没什么可看的。 */
    boolean empty() {
        if (!accounts.isEmpty()) {
            return false;
        }
        for (Agent agent : agents) {
            if (!agent.model.isEmpty()) {
                return false;
            }
        }
        return true;
    }

    /**
     * 模型名写短一点，好放进设备的一行：去掉开头的 “claude-” 和结尾的日期（-20251001），
     * 还是太长就截断。
     */
    static String shortModel(String model, int maxBytes) {
        String name = model == null ? "" : model.trim();
        if (name.startsWith("claude-")) {
            name = name.substring("claude-".length());
        }
        int dash = name.lastIndexOf('-');
        if (dash > 0 && name.length() - dash - 1 == 8) {
            boolean digits = true;
            for (int index = dash + 1; index < name.length(); index++) {
                digits &= name.charAt(index) >= '0' && name.charAt(index) <= '9';
            }
            if (digits) {
                name = name.substring(0, dash);
            }
        }
        return BuddyProtocol.clip(name, maxBytes);
    }

    private BuddyProtocol.UsageWho who(Agent agent) {
        return new BuddyProtocol.UsageWho(agent.name,
                shortModel(agent.model, BuddyProtocol.MODEL_MAX), agent.effort, agent.running);
    }

    /**
     * 设备第四屏的内容：每个订阅账号一条（跑在它上面的帮手，最多两个），然后是按量计费、
     * 报了模型的帮手，各一条。extraSeconds 是这份用量到手之后又过了多久；小于 0 表示不带
     * “多久之前的”（用来比较内容变没变）。
     */
    List<BuddyProtocol.UsageEntry> deviceEntries(long extraSeconds) {
        List<BuddyProtocol.UsageEntry> out = new ArrayList<>();
        for (Account account : accounts) {
            List<BuddyProtocol.UsageWho> helpers = new ArrayList<>();
            for (String name : account.agents) {
                Agent agent = agent(name);
                if (agent != null) {
                    helpers.add(who(agent));
                }
            }
            long age = -1;
            if (extraSeconds >= 0 && account.updatedAt > 0 && at >= account.updatedAt) {
                age = (long) (at - account.updatedAt) + extraSeconds;
            }
            out.add(new BuddyProtocol.UsageEntry(account.state, account.left5h, account.reset5h,
                    account.left7d, account.reset7d, age, helpers));
        }
        for (Agent agent : agents) {
            if (agent.account.isEmpty() && !agent.model.isEmpty()) {
                List<BuddyProtocol.UsageWho> helpers = new ArrayList<>();
                helpers.add(who(agent));
                out.add(new BuddyProtocol.UsageEntry("na", -1, 0, -1, 0, -1, helpers));
            }
        }
        return out;
    }

    /** “14:20 重置” / “周三 09:00 重置”；不知道或者已经过了是空串。 */
    static String resetText(long at, long nowSeconds, TimeZone zone) {
        if (at <= 0 || at <= nowSeconds) {
            return "";
        }
        Calendar when = Calendar.getInstance(zone, Locale.US);
        when.setTimeInMillis(at * 1000L);
        Calendar now = Calendar.getInstance(zone, Locale.US);
        now.setTimeInMillis(nowSeconds * 1000L);
        SimpleDateFormat clock = new SimpleDateFormat("HH:mm", Locale.US);
        clock.setTimeZone(zone);
        String time = clock.format(new Date(at * 1000L));
        boolean today = when.get(Calendar.YEAR) == now.get(Calendar.YEAR)
                && when.get(Calendar.DAY_OF_YEAR) == now.get(Calendar.DAY_OF_YEAR);
        if (today) {
            return time + " 重置";
        }
        String[] days = {"周日", "周一", "周二", "周三", "周四", "周五", "周六"};
        return days[when.get(Calendar.DAY_OF_WEEK) - 1] + " " + time + " 重置";
    }

    private static String stateText(String state) {
        switch (state) {
            case "ok":
                return "正常";
            case "warn":
                return "快用完了";
            case "out":
                return "用完了";
            default:
                return "还不知道";
        }
    }

    private static void window(StringBuilder out, String label, int left, long reset,
                               long nowSeconds, TimeZone zone) {
        String when = resetText(reset, nowSeconds, zone);
        if (left < 0 && when.isEmpty()) {
            return;
        }
        out.append('\n').append(label);
        out.append(left < 0 ? "：还不知道剩多少" : " 剩 " + left + "%");
        if (!when.isEmpty()) {
            out.append("，").append(when);
        }
    }

    private static String tier(Agent agent) {
        StringBuilder out = new StringBuilder(agent.name);
        out.append("（").append(agent.model.isEmpty() ? "还没跑过" : agent.model);
        if (!agent.effort.isEmpty()) {
            out.append(" · ").append(Card.effortLabel(agent.effort));
        }
        out.append('）');
        if (agent.running > 0) {
            out.append(" 正在做 ").append(agent.running).append(" 件");
        }
        return out.toString();
    }

    /**
     * 给人看的几段，一个账号一段：谁跑在它上面、用哪个模型和哪一档，两个窗口各剩多少、
     * 什么时候重置，这些数是多久之前的。按量计费的帮手排在后面，各占一段。
     */
    List<String> describe(long nowSeconds, long extraSeconds, TimeZone zone) {
        List<String> out = new ArrayList<>();
        for (Account account : accounts) {
            StringBuilder text = new StringBuilder();
            for (String name : account.agents) {
                Agent agent = agent(name);
                if (agent != null) {
                    text.append(text.length() == 0 ? "" : "\n").append(tier(agent));
                }
            }
            if (text.length() == 0) {
                text.append(account.id);
            }
            text.append(" — ").append(stateText(account.state));
            window(text, "5 小时", account.left5h, account.reset5h, nowSeconds, zone);
            window(text, "本周", account.left7d, account.reset7d, nowSeconds, zone);
            if (account.updatedAt > 0 && at >= account.updatedAt) {
                long minutes = ((long) (at - account.updatedAt) + Math.max(0L, extraSeconds)) / 60L;
                text.append('\n').append(minutes <= 0 ? "刚刚更新" : minutes + " 分钟前更新");
            }
            if (!account.note.isEmpty()) {
                text.append('\n').append(account.note);
            }
            out.add(text.toString());
        }
        for (Agent agent : agents) {
            if (agent.account.isEmpty() && !agent.model.isEmpty()) {
                out.add(tier(agent) + " — 按量计费");
            }
        }
        return out;
    }
}
