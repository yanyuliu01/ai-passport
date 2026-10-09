package com.yanyuliu.pockethub;

import android.app.PendingIntent;
import android.os.Handler;
import android.os.Looper;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * 中枢的内存状态：最近的消息、还没处理的通知、当前要问用户的那一条，以及运行日志。
 * 通知监听、蓝牙连接和界面都只跟它打交道，彼此不直接依赖。
 * 这里的内容只在内存里，进程结束就没了，不写入存储。卡是 Runtime 那边记着的，这里只是
 * 它的一份副本，重新连上后从头再取。
 */
public final class HubStore {
    public static final class Event {
        public final String key;
        public final String source;
        public final String title;
        public final String text;
        public final long time;
        /** 小幽自己说的话带一句简报；别的来源的通知没有，为 null。 */
        public final String brief;

        Event(String key, String source, String title, String text, long time) {
            this(key, source, title, text, time, null);
        }

        Event(String key, String source, String title, String text, long time, String brief) {
            this.brief = brief;
            this.key = key;
            this.source = source;
            this.title = title;
            this.text = text;
            this.time = time;
        }

        /** “来源：标题 正文”，用于首页说明行。 */
        public String summary() {
            if (brief != null) {
                return brief.replace('\n', ' ');
            }
            String body = title.isEmpty() ? text : (text.isEmpty() ? title : title + " " + text);
            return source + "：" + body.replace('\n', ' ');
        }

        public String entry() {
            return new SimpleDateFormat("HH:mm", Locale.US).format(new Date(time)) + " "
                    + summary();
        }

        public String full() {
            if (brief != null) {
                return text;
            }
            StringBuilder out = new StringBuilder(source);
            if (!title.isEmpty()) {
                out.append(" · ").append(title);
            }
            if (!text.isEmpty()) {
                out.append('\n').append(text);
            }
            return out.toString();
        }
    }

    /**
     * 要拿去问主人“可以 / 不行”的一条：带按钮的通知（设备上的回答会触发对应的按钮），或者
     * Runtime 上某个帮手要做的操作（approval 不为 null，回答要交回 Runtime）。
     */
    public static final class Ask {
        /** 发给设备的请求编号。 */
        public final String id;
        public final String source;
        public final String text;
        /** Runtime 那边的授权编号；通知来的是 null。 */
        public final String approval;
        final PendingIntent allow;
        final PendingIntent deny;

        Ask(String id, String source, String text, PendingIntent allow, PendingIntent deny) {
            this(id, source, text, null, allow, deny);
        }

        Ask(String id, String source, String text, String approval, PendingIntent allow,
            PendingIntent deny) {
            this.id = id;
            this.source = source;
            this.text = text;
            this.approval = approval;
            this.allow = allow;
            this.deny = deny;
        }
    }

    public interface Listener {
        void onHubChanged();
    }

    /** 小幽此刻在干什么：设备的第一屏显示的就是它。 */
    public static final class Turn {
        /** idle、thinking、helper、done、failed 之一。 */
        public final String phase;
        public final String said;
        public final String reply;
        /** 正在替小幽干活的帮手；没有就是空串。 */
        public final String agent;
        /** 她转交时说的那句话，或者帮手做完时的一句说明。 */
        public final String stage;
        public final String mood;
        /** 这句话归到的那张卡；还不知道就是空串。 */
        public final String card;
        /** 后台还在做的事的件数。 */
        public final int doing;

        Turn(String phase, String said, String reply, String agent, String stage, String mood) {
            this(phase, said, reply, agent, stage, mood, "", 0);
        }

        Turn(String phase, String said, String reply, String agent, String stage, String mood,
             String card, int doing) {
            this.card = card;
            this.doing = doing;
            this.phase = phase;
            this.said = said;
            this.reply = reply;
            this.agent = agent;
            this.stage = stage;
            this.mood = mood;
        }
    }

    private static final int EVENT_LIMIT = 40;
    private static final int LOG_LIMIT = 60;
    private static final int CHAT_LIMIT = 60;
    private static final String CHAT_SOURCE = "小幽";
    private static final HubStore INSTANCE = new HubStore();

    private final List<Event> events = new ArrayList<>();
    private final Map<String, Event> active = new LinkedHashMap<>();
    private final Map<String, Ask> asks = new LinkedHashMap<>();
    private final List<String> log = new ArrayList<>();
    private final List<Listener> listeners = new CopyOnWriteArrayList<>();
    private final Handler main = new Handler(Looper.getMainLooper());
    private final List<String> chat = new ArrayList<>();
    private String linkState = "未启动";
    /** 已经发给 Runtime、还没等到结果的消息数。 */
    private int pending;
    private String busyText = "";
    private long sequence;
    private Turn turn = new Turn("idle", "", "", "", "", "idle");
    /** 换了一台 Runtime，或者别的原因让“小幽有哪些帮手”需要重新问一次。 */
    private long helpersVersion;
    /** Runtime 上的卡，按开卡的先后。 */
    private final TreeMap<Long, Card> cards = new TreeMap<>();
    private final Map<String, Long> cardOrder = new HashMap<>();
    /** 已经看到的最大序号：带着它去等 Runtime 的下一次变化。 */
    private long feedSeq;
    /** 卡被整个换掉（换了 Runtime、Runtime 换了记录）一次加一：设备上的也要清掉重发。 */
    private long cardsEpoch;
    /** 第一屏跟着的那张卡：我最近说的那句话归到的，或者刚刚有了结果的。 */
    private String turnCard;
    /** Runtime 上还在等回答的授权，早的在前。 */
    private List<Card.Approval> approvals = new ArrayList<>();
    /** 已经回答、Runtime 还没确认收到的授权：不再拿去问。 */
    private final Set<String> answered = new HashSet<>();
    /** 回答没送到的次数：重新问的时候换一个请求编号，设备才会当成新的请求。 */
    private final Map<String, Integer> attempts = new HashMap<>();

    private HubStore() {
    }

    public static HubStore get() {
        return INSTANCE;
    }

    public void addListener(Listener listener) {
        listeners.add(listener);
    }

    public void removeListener(Listener listener) {
        listeners.remove(listener);
    }

    /** 收到一条消息。key 相同且内容相同的重复通知会被忽略。 */
    public void post(String key, String source, String title, String text,
                     PendingIntent allow, PendingIntent deny) {
        synchronized (this) {
            Event previous = active.get(key);
            String cleanTitle = title == null ? "" : title.trim();
            String cleanText = text == null ? "" : text.trim();
            if (previous != null && previous.title.equals(cleanTitle)
                    && previous.text.equals(cleanText)) {
                return;
            }
            Event event = new Event(key, source, cleanTitle, cleanText, System.currentTimeMillis());
            events.add(0, event);
            while (events.size() > EVENT_LIMIT) {
                events.remove(events.size() - 1);
            }
            active.put(key, event);
            if (allow != null && deny != null) {
                // 请求编号必须短于固件的上限，且每次都不同，避免旧回答套到新请求上。
                String id = "n" + (++sequence) + "-" + Integer.toHexString(key.hashCode());
                asks.put(key, new Ask(id, source, event.title.isEmpty() ? event.text
                        : event.title + "\n" + event.text, allow, deny));
            } else {
                asks.remove(key);
            }
            appendLog("收到 " + event.summary());
        }
        notifyChanged();
    }

    /** 通知被划掉或被对应的 App 撤回。 */
    public void dismiss(String key) {
        boolean changed;
        synchronized (this) {
            changed = active.remove(key) != null;
            changed |= asks.remove(key) != null;
        }
        if (changed) {
            notifyChanged();
        }
    }

    // ---- Runtime 上的卡和授权 ----

    public synchronized long feedSeq() {
        return feedSeq;
    }

    public synchronized long cardsEpoch() {
        return cardsEpoch;
    }

    /** 卡，按开卡的先后，旧的在前。 */
    public synchronized List<Card> cards() {
        return new ArrayList<>(cards.values());
    }

    /** 后台还在做的事的件数。 */
    public synchronized int doing() {
        int count = 0;
        for (Card card : cards.values()) {
            if (card.active()) {
                count++;
            }
        }
        return count;
    }

    /** 手里的卡都不算数了（换了一台 Runtime，或者它换了一份记录）：清掉，从头再取。 */
    public void resetCards() {
        synchronized (this) {
            cards.clear();
            cardOrder.clear();
            approvals = new ArrayList<>();
            answered.clear();
            attempts.clear();
            feedSeq = 0;
            turnCard = null;
            ++cardsEpoch;
        }
        notifyChanged();
    }

    /**
     * Runtime 说这些卡变了，以及现在还有哪些授权在等回答。seq 是它现在的序号。
     * 同一张卡只收序号不比手里旧的，所以两路同时取到的结果谁先谁后都一样。
     */
    public void applyFeed(long seq, List<Card> changed, List<Card.Approval> pendingApprovals) {
        boolean dirty = false;
        synchronized (this) {
            for (Card card : changed) {
                Long order = cardOrder.get(card.id);
                Card previous = order == null ? null : cards.get(order);
                if (previous != null && previous.seq > card.seq) {
                    continue;
                }
                if (order == null) {
                    order = card.order();
                    // 编号认不出、或者撞了：排到最后，不覆盖别的卡。
                    while (order <= 0 || cards.containsKey(order)) {
                        order = (cards.isEmpty() ? 0 : cards.lastKey()) + 1;
                    }
                    cardOrder.put(card.id, order);
                }
                cards.put(order, card);
                dirty = true;
                // 后台的一件事有了结果：第一屏说这件事。正在等小幽回话时不打断那一轮。
                if (previous != null && previous.active() && !card.active() && pending == 0) {
                    turnCard = card.id;
                }
            }
            if (seq > feedSeq) {
                feedSeq = seq;
            }
            Set<String> waiting = new HashSet<>();
            for (Card.Approval approval : pendingApprovals) {
                waiting.add(approval.id);
            }
            answered.retainAll(waiting);
            attempts.keySet().retainAll(waiting);
            if (!sameApprovals(approvals, pendingApprovals)) {
                Set<String> known = new HashSet<>();
                for (Card.Approval approval : approvals) {
                    known.add(approval.id + "." + approval.createdAt);
                }
                for (Card.Approval approval : pendingApprovals) {
                    if (!known.contains(approval.id + "." + approval.createdAt)) {
                        appendLog(approval.tool + " 在等你点头");
                    }
                }
                approvals = new ArrayList<>(pendingApprovals);
                dirty = true;
            }
        }
        if (dirty) {
            notifyChanged();
        }
    }

    private static boolean sameApprovals(List<Card.Approval> left, List<Card.Approval> right) {
        if (left.size() != right.size()) {
            return false;
        }
        for (int index = 0; index < left.size(); index++) {
            if (!left.get(index).id.equals(right.get(index).id)
                    || left.get(index).createdAt != right.get(index).createdAt) {
                return false;
            }
        }
        return true;
    }

    /** Runtime 上还在等回答、而且还没答过的授权，早的在前。 */
    public synchronized List<Card.Approval> approvals() {
        List<Card.Approval> open = new ArrayList<>();
        for (Card.Approval approval : approvals) {
            if (!answered.contains(approval.id)) {
                open.add(approval);
            }
        }
        return open;
    }

    private String approvalAskId(Card.Approval approval) {
        Integer attempt = attempts.get(approval.id);
        // Runtime 重启后授权编号从头数，所以带上它出现的时间：旧回答套不到新请求上。
        return approval.id + "." + approval.createdAt + "." + (attempt == null ? 0 : attempt);
    }

    /**
     * 设备（给的是请求编号）或者手机界面（给的是授权编号）回答了一个 Runtime 上的授权：
     * 记下已经答过并返回授权编号，由调用方交回 Runtime。不是授权、或者已经答过时返回 null。
     */
    public String takeApproval(String id) {
        String found = null;
        synchronized (this) {
            for (Card.Approval approval : approvals) {
                if (!answered.contains(approval.id)
                        && (approval.id.equals(id) || approvalAskId(approval).equals(id))) {
                    answered.add(approval.id);
                    found = approval.id;
                    break;
                }
            }
        }
        if (found != null) {
            notifyChanged();
        }
        return found;
    }

    /** 回答没能送到 Runtime：这个授权还在等，重新拿出来问。 */
    public void approvalFailed(String approval, String error) {
        synchronized (this) {
            if (answered.remove(approval)) {
                Integer attempt = attempts.get(approval);
                attempts.put(approval, attempt == null ? 1 : attempt + 1);
            }
            appendLog("回答没送到 Runtime：" + error);
        }
        notifyChanged();
    }

    /** 设备上的回答。返回是否找到了对应的请求并成功触发。 */
    public boolean answer(String id, boolean allow) {
        Ask ask = null;
        String key = null;
        synchronized (this) {
            for (Map.Entry<String, Ask> entry : asks.entrySet()) {
                if (entry.getValue().id.equals(id)) {
                    key = entry.getKey();
                    ask = entry.getValue();
                    break;
                }
            }
            if (ask == null) {
                appendLog("设备回答了一个已经不存在的请求，忽略");
                return false;
            }
            asks.remove(key);
            active.remove(key);
        }
        boolean sent = true;
        try {
            PendingIntent intent = allow ? ask.allow : ask.deny;
            if (intent != null) {
                intent.send();
            }
        } catch (PendingIntent.CanceledException error) {
            sent = false;
        }
        log((allow ? "设备回答：可以" : "设备回答：不行") + "（" + ask.source + "）"
                + (sent ? "" : "，但对应的通知按钮已失效"));
        notifyChanged();
        return sent;
    }

    // ---- 和小幽聊天 ----

    /** App 刚启动、聊天框还空着时，把存着的对话放回来。 */
    public void restoreChat(List<ChatTurn> turns) {
        synchronized (this) {
            if (!chat.isEmpty()) {
                return;
            }
            for (ChatTurn turn : turns) {
                appendChat("我：" + turn.text);
                appendChat(CHAT_SOURCE + "：" + turn.reply);
            }
        }
        notifyChanged();
    }

    /** 我说了一句话，小幽开始想。语音在识别出来之前，说的是什么还不知道。 */
    public void chatAsked(String text) {
        synchronized (this) {
            ++pending;
            busyText = text;
            turnCard = null;
            appendChat("我：" + text);
            turn = new Turn("thinking", RuntimeClient.VOICE_PLACEHOLDER.equals(text) ? "" : text,
                    "", "", "", "busy");
        }
        notifyChanged();
    }

    /**
     * Runtime 说这一轮走到了哪一步：helper 不为空表示小幽把活交给了它，stage 是她当时说的话。
     * 没有变化时不通知，免得白白重发。
     */
    public void chatProgress(String helper, String stage) {
        synchronized (this) {
            if (pending <= 0) {
                return;
            }
            String agent = helper == null ? "" : helper;
            String text = stage == null ? "" : stage;
            String phase = agent.isEmpty() ? "thinking" : "helper";
            if (turn.phase.equals(phase) && turn.agent.equals(agent) && turn.stage.equals(text)) {
                return;
            }
            turn = new Turn(phase, turn.said, "", agent, text, "busy");
        }
        notifyChanged();
    }

    public synchronized Turn turn() {
        int doing = 0;
        for (Card card : cards.values()) {
            if (card.active()) {
                doing++;
            }
        }
        Long order = turnCard == null ? null : cardOrder.get(turnCard);
        Card card = order == null ? null : cards.get(order);
        if (pending > 0 || card == null) {
            return new Turn(turn.phase, turn.said, turn.reply, turn.agent, turn.stage, turn.mood,
                    turn.card, doing);
        }
        // 这一轮已经有卡了：第一屏跟着卡走，帮手做完时它自己就变成结果。
        String say = card.lastSay();
        if (card.active()) {
            if (card.agent.isEmpty()) {
                return new Turn("thinking", card.said(), "", "", "", "busy", card.id, doing);
            }
            return new Turn("helper", card.said(), "", card.agent, card.title,
                    card.state.equals("waiting") ? "ask" : "busy", card.id, doing);
        }
        if (card.state.equals("failed")) {
            return new Turn("failed", card.said(), say.isEmpty() ? card.brief : say, "", "", "oops",
                    card.id, doing);
        }
        if (card.state.equals("cancelled")) {
            return new Turn("done", card.said(), say.isEmpty() ? "取消了" : say, "", "", "idle",
                    card.id, doing);
        }
        return new Turn("done", card.said(), card.brief.isEmpty() ? say : card.brief, "", "",
                card.mood, card.id, doing);
    }

    public synchronized long helpersVersion() {
        return helpersVersion;
    }

    /** 换了一台 Runtime：帮手的清单要重新问。 */
    public void helpersChanged() {
        synchronized (this) {
            ++helpersVersion;
        }
        notifyChanged();
    }

    /** 语音识别出了我刚才说的话：把聊天记录里的占位换成原话。 */
    public void chatHeard(String placeholder, String heard) {
        synchronized (this) {
            for (int position = chat.size() - 1; position >= 0; --position) {
                if (chat.get(position).equals("我：" + placeholder)) {
                    chat.set(position, "我：" + heard);
                    break;
                }
            }
            if (pending > 0) {
                busyText = heard;
                turn = new Turn(turn.phase, heard, turn.reply, turn.agent, turn.stage, turn.mood);
            }
        }
        notifyChanged();
    }

    /** 小幽答完了：brief 给小屏幕，reply 是完整回复，mood 是她此刻的表情。 */
    public void chatAnswered(String brief, String reply, String mood) {
        chatAnswered(brief, reply, mood, null);
    }

    /** 同上；card 是这句话归到的那张卡（旧版 Runtime 没有，为 null）。 */
    public void chatAnswered(String brief, String reply, String mood, String card) {
        synchronized (this) {
            pending = Math.max(0, pending - 1);
            turnCard = card;
            String shown = reply.isEmpty() ? brief : reply;
            turn = new Turn("done", turn.said, BuddyProtocol.chatReply(brief, reply), "", "",
                    mood == null || mood.isEmpty() ? "idle" : mood);
            events.add(0, new Event("chat-" + (++sequence), CHAT_SOURCE, "", shown,
                    System.currentTimeMillis(), brief.isEmpty() ? shown : brief));
            trimEvents();
            appendChat(CHAT_SOURCE + "：" + shown);
        }
        notifyChanged();
    }

    /** 这一轮没成功。asked 不为 null 表示这句话还没来得及记进聊天记录。 */
    public void chatFailed(String asked, String error) {
        synchronized (this) {
            if (asked != null) {
                appendChat("我：" + asked);
                turn = new Turn("failed", RuntimeClient.VOICE_PLACEHOLDER.equals(asked) ? "" : asked,
                        error, "", "", "oops");
                if (pending == 0) {
                    turnCard = null;
                }
            } else {
                pending = Math.max(0, pending - 1);
                turnCard = null;
                turn = new Turn("failed", turn.said, error, "", "", "oops");
            }
            events.add(0, new Event("chat-" + (++sequence), CHAT_SOURCE, "", "没成功：" + error,
                    System.currentTimeMillis(), "没成功：" + error));
            trimEvents();
            appendChat("（没成功）" + error);
            appendLog("聊天失败：" + error);
        }
        notifyChanged();
    }

    public synchronized boolean busy() {
        return pending > 0;
    }

    public synchronized String busyText() {
        return busyText;
    }

    /** 聊天记录，旧的在前。 */
    public synchronized List<String> chatLines() {
        return new ArrayList<>(chat);
    }

    private void appendChat(String line) {
        chat.add(line);
        while (chat.size() > CHAT_LIMIT) {
            chat.remove(0);
        }
    }

    private void trimEvents() {
        while (events.size() > EVENT_LIMIT) {
            events.remove(events.size() - 1);
        }
    }

    public synchronized List<Event> recent(int count) {
        return new ArrayList<>(events.subList(0, Math.min(count, events.size())));
    }

    /** 最近的通知，不含小幽自己说的话：那些在设备首页上，不用在“通知”里再出现一遍。 */
    public synchronized List<Event> recentNotices(int count) {
        List<Event> notices = new ArrayList<>();
        for (Event event : events) {
            if (event.brief == null) {
                notices.add(event);
                if (notices.size() >= count) {
                    break;
                }
            }
        }
        return notices;
    }

    public synchronized int waiting() {
        return active.size();
    }

    /** 当前要拿去问用户的那一条（最早的一条）；没有则为 null。 */
    public synchronized Ask currentAsk() {
        // 帮手停在那里等着，所以它的问题排在通知前面。
        for (Card.Approval approval : approvals) {
            if (!answered.contains(approval.id)) {
                return new Ask(approvalAskId(approval), approval.tool, approval.detail,
                        approval.id, null, null);
            }
        }
        for (Ask ask : asks.values()) {
            return ask;
        }
        return null;
    }

    public synchronized long todayCount() {
        long startOfDay = System.currentTimeMillis() / 86400000L * 86400000L;
        long count = 0;
        for (Event event : events) {
            if (event.time >= startOfDay) {
                count++;
            }
        }
        return count;
    }

    public synchronized String linkState() {
        return linkState;
    }

    public void setLinkState(String state) {
        synchronized (this) {
            if (state.equals(linkState)) {
                return;
            }
            linkState = state;
            appendLog("连接：" + state);
        }
        notifyChanged();
    }

    public void log(String line) {
        synchronized (this) {
            appendLog(line);
        }
        notifyChanged();
    }

    public synchronized List<String> logLines() {
        return new ArrayList<>(log);
    }

    private void appendLog(String line) {
        log.add(0, new SimpleDateFormat("HH:mm:ss", Locale.US).format(new Date()) + " " + line);
        while (log.size() > LOG_LIMIT) {
            log.remove(log.size() - 1);
        }
    }

    private void notifyChanged() {
        main.post(() -> {
            for (Listener listener : listeners) {
                listener.onHubChanged();
            }
        });
    }
}
