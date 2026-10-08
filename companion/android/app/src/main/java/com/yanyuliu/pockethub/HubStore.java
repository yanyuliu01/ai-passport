package com.yanyuliu.pockethub;

import android.app.PendingIntent;
import android.os.Handler;
import android.os.Looper;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * 中枢的内存状态：最近的消息、还没处理的通知、当前要问用户的那一条，以及运行日志。
 * 通知监听、蓝牙连接和界面都只跟它打交道，彼此不直接依赖。
 * 这里的内容只在内存里，进程结束就没了，不写入存储。
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

    /** 带“允许 / 拒绝”按钮的通知：设备上的回答会触发对应的按钮。 */
    public static final class Ask {
        public final String id;
        public final String source;
        public final String text;
        final PendingIntent allow;
        final PendingIntent deny;

        Ask(String id, String source, String text, PendingIntent allow, PendingIntent deny) {
            this.id = id;
            this.source = source;
            this.text = text;
            this.allow = allow;
            this.deny = deny;
        }
    }

    public interface Listener {
        void onHubChanged();
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
    private boolean busy;
    private String busyText = "";
    private long sequence;

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

    /** 我说了一句话，小幽开始想。 */
    public void chatAsked(String text) {
        synchronized (this) {
            busy = true;
            busyText = text;
            appendChat("我：" + text);
        }
        notifyChanged();
    }

    /** 小幽答完了：brief 给小屏幕，reply 是完整回复。 */
    public void chatAnswered(String brief, String reply) {
        synchronized (this) {
            busy = false;
            String shown = reply.isEmpty() ? brief : reply;
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
            busy = false;
            if (asked != null) {
                appendChat("我：" + asked);
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
        return busy;
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

    public synchronized int waiting() {
        return active.size();
    }

    /** 当前要拿去问用户的那一条（最早的一条）；没有则为 null。 */
    public synchronized Ask currentAsk() {
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
