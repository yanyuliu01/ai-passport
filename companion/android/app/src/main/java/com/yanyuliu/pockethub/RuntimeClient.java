package com.yanyuliu.pockethub;

import android.content.Context;
import android.content.SharedPreferences;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * 和小幽 Runtime 说话：把一句话发过去，等它处理完，把回复交给中枢。
 *
 * Runtime 的地址和令牌用一条“连接串”设置：http://主机:端口#令牌，
 * 由电脑上的 `python3 -m xiaoyou_runtime --pair` 打印出来。
 * 消息一条一条顺序处理，和 Runtime 那边的做法一致。
 */
final class RuntimeClient {
    private static final String PREFS = "runtime";
    private static final String KEY_URL = "url";
    private static final String KEY_TOKEN = "token";
    private static final String KEY_COUNT = "rt_count";
    private static final int TURNS_KEPT = 20;
    static final int MAX_TARGETS = 8;
    private static final int POLL_SECONDS = 50;
    private static final long GIVE_UP_MS = 15 * 60 * 1000L;
    private static final ExecutorService WORKER = Executors.newSingleThreadExecutor();
    private static final Map<String, java.util.Set<String>> SHARED_HISTORY = new java.util.concurrent.ConcurrentHashMap<>();

    /** 已经带给这个地址的对话编号（发送线程和后台带历史的线程都会写）。 */
    private static java.util.Set<String> sharedWith(String url) {
        return SHARED_HISTORY.computeIfAbsent(url, ignored -> java.util.concurrent.ConcurrentHashMap.newKeySet());
    }
    /** 回答授权、取消一件事：不排在正在等结果的那句话后面。 */
    private static final ExecutorService QUICK = Executors.newCachedThreadPool();
    private static final int FEED_WAIT_SECONDS = 50;
    private static Thread watcher;
    /** 正在等卡变化的那条连接：换 Runtime 时断开它，不用等它自己超时。 */
    private static volatile HttpURLConnection feedConnection;
    private static volatile MessageStream activeMessageStream;
    /** 界面上查固件、选版本用的线程：聊天那一条可能正等着小幽回话，不能排在它后面。 */
    private static final ExecutorService FIRMWARE_WORKER = Executors.newSingleThreadExecutor();
    private static final ExecutorService AGENTS_WORKER = Executors.newSingleThreadExecutor();

    private RuntimeClient() {
    }

    /** 一台登记过的 Runtime。 */
    static final class Target {
        final String name;
        final String url;
        final String token;

        Target(String name, String url, String token) {
            this.name = name;
            this.url = url;
            this.token = token;
        }
    }

    private static SharedPreferences prefs(Context context) {
        return context.getApplicationContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    /** 登记过的所有 Runtime。旧版本只存一台，这里一并读出来。 */
    static synchronized List<Target> targets(Context context) {
        SharedPreferences prefs = prefs(context);
        List<Target> list = new ArrayList<>();
        int count = prefs.getInt(KEY_COUNT, -1);
        if (count < 0) {
            String url = prefs.getString(KEY_URL, null);
            String token = prefs.getString(KEY_TOKEN, null);
            if (url != null && token != null) {
                list.add(new Target(hostOf(url), url, token));
            }
            return list;
        }
        for (int index = 0; index < count; index++) {
            String url = prefs.getString("rt_url_" + index, null);
            String token = prefs.getString("rt_token_" + index, null);
            if (url != null && token != null) {
                list.add(new Target(prefs.getString("rt_name_" + index, hostOf(url)), url, token));
            }
        }
        return list;
    }

    private static void store(Context context, List<Target> list, String selected) {
        SharedPreferences.Editor editor = prefs(context).edit();
        editor.putInt(KEY_COUNT, list.size());
        for (int index = 0; index < list.size(); index++) {
            Target target = list.get(index);
            editor.putString("rt_name_" + index, target.name)
                    .putString("rt_url_" + index, target.url)
                    .putString("rt_token_" + index, target.token);
        }
        boolean found = false;
        for (Target target : list) {
            found = found || target.url.equals(selected);
        }
        if (!found) {
            selected = list.isEmpty() ? null : list.get(0).url;
        }
        editor.putString(KEY_URL, selected).remove(KEY_TOKEN).apply();
    }

    private static String hostOf(String url) {
        String host = url.substring(url.indexOf("//") + 2);
        int colon = host.lastIndexOf(':');
        return colon > 0 ? host.substring(0, colon) : host;
    }

    /** 现在用的那台；一台都没有时返回 null。 */
    static synchronized Target selected(Context context) {
        List<Target> list = targets(context);
        String url = prefs(context).getString(KEY_URL, null);
        for (Target target : list) {
            if (target.url.equals(url)) {
                return target;
            }
        }
        return list.isEmpty() ? null : list.get(0);
    }

    // ---- 同一台 Runtime 的几个地址（局域网、Funnel……）按 runtime_id 认成一台 ----

    private static final Map<String, java.util.Set<String>> CAPABILITIES =
            new java.util.concurrent.ConcurrentHashMap<>();

    /** 这个地址上的 Runtime 是谁（/v1/info 的 runtime_id）；还没问到或旧版 Runtime 是空串。 */
    static String runtimeId(Context context, String url) {
        return prefs(context).getString("rid:" + url, "");
    }

    /**
     * 聊天记录、同步缓存用的键：同一台 Runtime 的所有地址共用最早登记的那个地址，
     * 所以在局域网和 Funnel 之间切换，对话和卡都不会清空。认不出是谁时就用地址自己。
     */
    static synchronized String historyKey(Context context, Target target) {
        if (target == null) return "";
        String id = runtimeId(context, target.url);
        if (id.isEmpty()) return target.url;
        for (Target item : targets(context)) if (id.equals(runtimeId(context, item.url))) return item.url;
        return target.url;
    }

    /** 同一台 Runtime 的其他地址（连不上当前这个时换着试）。 */
    static synchronized List<Target> siblings(Context context, Target target) {
        List<Target> result = new ArrayList<>();
        String id = runtimeId(context, target.url);
        if (id.isEmpty()) return result;
        for (Target item : targets(context))
            if (!item.url.equals(target.url) && id.equals(runtimeId(context, item.url))) result.add(item);
        return result;
    }

    /** 问这个地址上的 Runtime 是谁、会什么；旧版 Runtime 没有 /v1/info，当作什么新能力都没有。 */
    private static java.util.Set<String> identify(Context context, Target target) throws IOException {
        java.util.Set<String> abilities = new java.util.HashSet<>();
        try {
            Map<String, Object> info = Json.parseObject(requestText("GET", target.url + "/v1/info",
                    target.token, null, null, 10, true));
            if (info != null) {
                for (Object item : Json.list(info, "capabilities")) if (item instanceof String) abilities.add((String) item);
                String id = Json.string(info, "runtime_id");
                if (!id.isEmpty()) prefs(context).edit().putString("rid:" + target.url, id).apply();
            }
        } catch (IOException error) {
            if (!String.valueOf(error.getMessage()).contains(" 404")) throw error;
        }
        CAPABILITIES.put(target.url, abilities);
        return abilities;
    }

    static synchronized void select(Context context, String url) {
        store(context, targets(context), url);
        Target now = selected(context);
        // 换成另一台电脑：这台的卡收起来、那台的放回去；同一台的另一个地址什么都不动。
        HubStore.get().showComputer(now == null ? "" : historyKey(context, now));
        HubStore.get().selectedRuntime(url, now == null ? url : historyKey(context, now));
        HubStore.get().helpersChanged();
        HttpURLConnection waiting = feedConnection;
        if (waiting != null) {
            waiting.disconnect();
        }
        MessageStream talking = activeMessageStream;
        if (talking != null) talking.close();
    }

    // ---- 卡：一直等着 Runtime 说哪些事变了 ----

    /** 开始盯着现在用的那台 Runtime 的卡；已经在盯就什么都不做。进程在它就在。 */
    static synchronized void watch(Context context) {
        if (watcher != null) {
            return;
        }
        final Context app = context.getApplicationContext();
        ApprovalNotifications.start(app);
        ApprovalMonitor.start(app);
        watcher = new Thread(() -> watchLoop(app), "xiaoyou-feed");
        watcher.setDaemon(true);
        watcher.start();
    }

    private static void watchLoop(Context app) {
        HubStore store = HubStore.get();
        String watched = null;
        java.util.Set<String> abilities = null;
        String complained = null;
        int failures = 0;
        boolean more = false;
        while (true) {
            Target target = selected(app);
            if (target == null) {
                watched = null;
                pause(3);
                continue;
            }
            if (!target.url.equals(watched)) {
                watched = target.url;
                abilities = null;
            }
            try {
                if (abilities == null) {
                    abilities = identify(app, target);
                    String key = historyKey(app, target);
                    // 另一台电脑：摆出它自己的卡和同步进度（没见过就从头取）；同一台不动。
                    store.showComputer(key);
                    store.selectedRuntime(target.url, key);
                    final Target shared = target;
                    QUICK.execute(() -> shareHistory(app, shared));
                }
                // 0.8.0 的 /v1/sync 还不带用量：那一版继续用 feed，首页的额度才不会停在旧数上。
                if (abilities.contains("sync") && abilities.contains("sync_usage")) {
                    more = fetchSync(target, store.syncCache(), more ? 0 : FEED_WAIT_SECONDS, true);
                } else {
                    more = false;
                    fetchFeed(target, FEED_WAIT_SECONDS, true);
                }
                failures = 0;
                complained = null;
            } catch (IOException | RuntimeException error) {
                more = false;
                Target now = selected(app);
                if (now != null && !now.url.equals(watched)) {
                    continue;  // 是换 Runtime 时自己断开的
                }
                String detail = String.valueOf(error.getMessage());
                boolean old = detail.contains(" 404");
                if (failures == 0 && !old) {
                    // 经中继连着时偶尔断一次是常事：先悄悄重连一次，连续失败才算离线。
                    failures++;
                    pause(2);
                    continue;
                }
                List<Target> others = siblings(app, target);
                if (!others.isEmpty() && failures < 1 + others.size()) {
                    // 同一台电脑还有别的地址（比如局域网连不上就走 Funnel）：换一个试，不清空卡。
                    Target next = others.get(0);
                    synchronized (RuntimeClient.class) { store(app, targets(app), next.url); }
                    store.log("连不上 " + target.url + "，改用 " + next.url);
                    failures++;
                    continue;
                }
                store.desktopDisconnected();
                store.runtimeConnected(false);
                if (!detail.equals(complained)) {
                    complained = detail;
                    store.log(old ? target.name + " 上的 Runtime 是旧版，没有卡和授权"
                            : "没取到 " + target.name + " 上的卡：" + detail);
                }
                pause(old ? 60 : Math.min(30, 2 << Math.min(failures++, 4)));
            }
        }
    }

    private static void pause(int seconds) {
        try {
            Thread.sleep(seconds * 1000L);
        } catch (InterruptedException ignored) {
            Thread.currentThread().interrupt();
        }
    }

    /** 取一次“之后变过的卡”并交给中枢；没有变化时最多等 wait 秒。 */
    private static void fetchFeed(Target target, int wait, boolean interruptible)
            throws IOException {
        HubStore store = HubStore.get();
        long after = store.feedSeq();
        long epoch = store.cardsEpoch();
        // 带上手里那份用量的编号：用量变了 Runtime 也会提前返回（0.6.0 起；旧版不认识这个
        // 参数，照常等卡的变化）。
        long usageRev = store.usageRev();
        String body = requestText("GET", target.url + "/v1/feed?after=" + after + "&wait=" + wait
                        + (usageRev >= 0 ? "&usage=" + usageRev : "")
                        // 认得 unavailable（看不到状态的电脑会话、上报）：见 Card.from。
                        + "&states=unavailable",
                target.token, null, null, wait + 15, interruptible);
        Map<String, Object> feed = Json.parseObject(body);
        if (feed == null) {
            throw new IOException("Runtime 返回的内容看不懂");
        }
        // 核对和写入在同一把锁里，见 fetchSync。
        synchronized (store) {
            if (store.cardsEpoch() != epoch) {
                return;  // 等的时候换了 Runtime：这是上一台的回答
            }
            long seq = (long) Json.number(feed, "seq");
            if (seq < after) {
                // Runtime 的序号比手里的小：它换了一份记录。手里的不算数了，从头再取。
                store.resetCards();
                return;
            }
            List<Card> cards = new ArrayList<>();
            for (Object item : Json.list(feed, "cards")) {
                Card card = Card.from(Json.object(item));
                if (card != null) {
                    cards.add(card);
                }
            }
            store.applyFeed(seq, cards, null);
            store.setUsage(Usage.from(feed.get("usage")), epoch);
            store.runtimeConnected(true);
        }
    }

    /**
     * 用 /v1/sync 取一页变化并交给中枢；没有变化时最多等 wait 秒。返回 true 表示还有下一页，
     * 应该马上接着取。游标、epoch 由 cache 管，整页应用完才往前走。
     */
    private static boolean fetchSync(Target target, SyncCache cache, int wait, boolean interruptible)
            throws IOException {
        HubStore store = HubStore.get();
        long epoch = store.cardsEpoch();
        long usageRev = store.usageRev();
        String body = requestText("GET", target.url + "/v1/sync?conversation=default&after="
                        + java.net.URLEncoder.encode(cache.cursor(), "UTF-8") + "&wait=" + wait
                        + (cache.epoch().isEmpty() ? "" : "&epoch=" + java.net.URLEncoder.encode(cache.epoch(), "UTF-8"))
                        + (usageRev >= 0 ? "&usage=" + usageRev : "")
                        // 认得 unavailable（看不到状态的电脑会话、上报）：见 Card.from。
                        + "&states=unavailable",
                target.token, null, null, wait + 15, interruptible);
        Map<String, Object> page = Json.parseObject(body);
        if (page == null) {
            throw new IOException("Runtime 返回的内容看不懂");
        }
        // 核对“还是这台电脑”和写进去在同一把锁里：中间换了电脑，这一页就整页不要，
        // 不会把上一台的卡写到下一台的显示里。
        synchronized (store) {
            if (store.cardsEpoch() != epoch) {
                return false;  // 等的时候换了 Runtime：这是上一台的回答
            }
            SyncCache.Result result = cache.apply(page);
            if (result.replaced) {
                // Runtime 换了一份记录：手里的卡不算数了。聊天记录按编号留着。
                store.resetCards();
                epoch = store.cardsEpoch();
            }
            store.applyFeed(result.seq, result.cards, null);
            store.applyPending(result.pending);
            if (result.usage != null) store.setUsage(Usage.from(result.usage), epoch);
            store.runtimeConnected(true);
            return result.more;
        }
    }

    /**
     * 把这台手机上别处的对话带给这台 Runtime（它已经知道的会自己跳过）。连上时在后台做一次，
     * 不再挡在每次发消息前面。
     */
    private static void shareHistory(Context app, Target target) {
        java.util.Set<String> shared = sharedWith(target.url);
        List<ChatTurn> history = new ArrayList<>();
        for (ChatTurn turn : HubStore.get().recentTurns(turns(app), TURNS_KEPT))
            if (!shared.contains(turn.id)) history.add(turn);
        if (history.isEmpty()) return;
        try {
            request("POST", target.url + "/v1/conversations/default/history", target.token,
                    ChatTurn.historyJson(history).getBytes(StandardCharsets.UTF_8),
                    "application/json; charset=utf-8", 15);
            for (ChatTurn turn : history) shared.add(turn.id);
        } catch (IOException | RuntimeException error) {
            // 旧版 Runtime 没有这个接口：照常聊，只是它接不上别处的话。
            HubStore.get().log("没能把之前的对话带给 " + target.name + "：" + error.getMessage());
        }
    }

    /** 回答 Runtime 上的一个授权。送不到时这个授权会重新拿出来问。 */
    interface ActionCallback { void onResult(String problem); }

    private static void actionResult(ActionCallback callback, String problem) {
        if (callback != null) new android.os.Handler(android.os.Looper.getMainLooper())
                .post(() -> callback.onResult(problem));
    }

    static void approve(Context context, String approval, boolean allow) {
        approve(context, approval, allow, null);
    }

    static void approve(Context context, String approval, boolean allow, ActionCallback callback) {
        final HubStore store = HubStore.get();
        final Card.Approval request = store.approval(approval);
        final Target target = request == null ? null : target(context, request.runtimeUrl);
        if (store.monitoringApproval(approval)) {
            store.log("电脑端授权请在电脑端回答");
            actionResult(callback, "请在电脑端确认这次操作");
            return;
        }
        if (target == null) {
            store.approvalFailed(approval, "还没有设置 Runtime 的连接串");
            actionResult(callback, "来源电脑已移除，请重新连接");
            return;
        }
        store.log((allow ? "回答：可以" : "回答：不行") + "（" + approval + "）");
        QUICK.execute(() -> {
            try {
                requestText("POST", target.url + "/v1/approvals/" + java.net.URLEncoder.encode(request.remoteId, "UTF-8"), target.token,
                        ("{\"decision\":\"" + (allow ? "allow" : "deny") + "\"}")
                                .getBytes(StandardCharsets.UTF_8),
                        "application/json; charset=utf-8", 15, false);
                actionResult(callback, null);
            } catch (IOException | RuntimeException error) {
                String detail = String.valueOf(error.getMessage());
                if (detail.contains(" 404") || detail.contains(" 409")) {
                    // 那件事已经结束，或者别处已经答过了：不用再问。
                    store.log("这个授权已经不用回答了：" + detail);
                } else {
                    store.approvalFailed(approval, detail);
                }
                actionResult(callback, detail.contains(" 404") || detail.contains(" 409")
                        ? "请求已结束或已在其他端处理" : "提交失败：" + detail);
            }
        });
    }

    /** 取消一件事。结果从卡的变化里看。 */
    static void cancel(Context context, String card) {
        final Target target = selected(context.getApplicationContext());
        final HubStore store = HubStore.get();
        if (store.monitoringOnly(card)) {
            store.log("电脑端对话只读同步，请在电脑端中断");
            return;
        }
        if (target == null) {
            return;
        }
        QUICK.execute(() -> {
            try {
                requestText("POST", target.url + "/v1/cards/" + card + "/cancel", target.token,
                        "{}".getBytes(StandardCharsets.UTF_8), "application/json; charset=utf-8",
                        15, false);
                store.log("已取消 " + card);
            } catch (IOException | RuntimeException error) {
                store.log("没能取消 " + card + "：" + error.getMessage());
            }
        });
    }

    /** 收到帮手清单时的回调：每项是 {名字, 一句说明}。 */
    interface AgentsCallback {
        void onAgents(List<String[]> agents);
    }

    /**
     * 问现在用的那台 Runtime：小幽有哪些帮手。默认接话的那个代理就是小幽自己用来说话的，
     * 也列在里面——在设备上它和别的帮手一样，是她能找的人。问不到时不回调。
     */
    static void fetchAgents(Context context, AgentsCallback callback) {
        final Target target = selected(context.getApplicationContext());
        if (target == null) {
            return;
        }
        AGENTS_WORKER.execute(() -> {
            try {
                String body = requestText("GET", target.url + "/v1/agents", target.token, null,
                        null, 8);
                List<String[]> agents = new ArrayList<>();
                for (Map<String, String> item : BuddyProtocol.parseObjectArray(body, "agents")) {
                    String name = item.get("name");
                    if (name != null && !name.isEmpty()) {
                        String about = item.get("description");
                        agents.add(new String[] {name, about == null ? "" : about});
                    }
                }
                callback.onAgents(agents);
            } catch (IOException | RuntimeException error) {
                // 旧版 Runtime 没有这个接口，或者暂时连不上：设备上的帮手页就先空着。
                HubStore.get().log("没问到小幽有哪些帮手：" + error.getMessage());
            }
        });
    }

    interface UsageCallback {
        void onUsage(String problem, boolean applied);
    }

    /** 手动现问额度，不排在对话后面；来源或记录世代变了就丢弃这次结果。 */
    static void fetchUsage(Context context, UsageCallback callback) {
        final Context app = context.getApplicationContext();
        final Target target;
        final long epoch;
        synchronized (RuntimeClient.class) {
            target = selected(app);
            epoch = HubStore.get().cardsEpoch();
        }
        if (target == null) {
            callback.onUsage("还没有登记电脑", false);
            return;
        }
        QUICK.execute(() -> {
            String problem = null;
            Usage next = null;
            try {
                String body = requestText("GET", target.url + "/v1/usage?refresh=1", target.token,
                        null, null, 90);
                next = Usage.from(Json.parseObject(body));
                if (next == null) throw new IOException("Runtime 返回的用量看不懂");
            } catch (IOException | RuntimeException error) {
                problem = String.valueOf(error.getMessage());
            }
            boolean applied = false;
            synchronized (RuntimeClient.class) {
                Target current = selected(app);
                if (current != null && target.url.equals(current.url) && target.token.equals(current.token)
                        && HubStore.get().cardsEpoch() == epoch) {
                    if (next != null) {
                        HubStore.get().setUsage(next, epoch);
                        applied = true;
                    }
                } else problem = null;
            }
            callback.onUsage(problem, applied);
        });
    }

    static synchronized void remove(Context context, String url) {
        List<Target> list = targets(context);
        Target current = selected(context);
        Target removed = target(context, url);
        String removedKey = removed == null ? url : historyKey(context, removed);
        for (int index = list.size() - 1; index >= 0; index--) {
            if (list.get(index).url.equals(url)) {
                list.remove(index);
            }
        }
        store(context, list, current == null ? null : current.url);
        HubStore.get().dropApprovalRuntime(url);
        if (current != null && current.url.equals(url)) {
            Target next = selected(context);
            HubStore.get().showComputer(next == null ? "" : historyKey(context, next));
        }
        // 这台电脑一个地址都不剩了：它收起来的卡也不要了。
        boolean kept = false;
        for (Target item : targets(context)) kept |= historyKey(context, item).equals(removedKey);
        if (!kept) HubStore.get().forgetComputer(removedKey);
        ApprovalMonitor.reconcile(context.getApplicationContext());
        if (list.isEmpty()) context.stopService(new android.content.Intent(context, RuntimeWatchService.class));
    }

    static synchronized Target target(Context context, String url) {
        if (url.isEmpty()) return selected(context);
        for (Target item : targets(context)) if (item.url.equals(url)) return item;
        return null;
    }

    private static synchronized void rename(Context context, String url, String name) {
        List<Target> list = targets(context);
        Target current = selected(context);
        for (int index = 0; index < list.size(); index++) {
            Target target = list.get(index);
            if (target.url.equals(url)) {
                list.set(index, new Target(name, target.url, target.token));
            }
        }
        store(context, list, current == null ? null : current.url);
    }

    /**
     * 用连接串登记一台 Runtime 并切换到它；同一个地址再登记一次只更新令牌。
     * 格式不对时返回说明，成功返回 null。
     */
    static String savePairing(Context context, String pairing) {
        String text = pairing == null ? "" : pairing.trim();
        int hash = text.indexOf('#');
        if (hash <= 0 || hash == text.length() - 1) {
            return "连接串应该像 http://192.168.1.5:8765#令牌";
        }
        String url = text.substring(0, hash);
        while (url.endsWith("/")) {
            url = url.substring(0, url.length() - 1);
        }
        String token = text.substring(hash + 1).trim();
        if (!url.startsWith("http://") && !url.startsWith("https://")) {
            return "地址要以 http:// 或 https:// 开头";
        }
        if (token.length() < 16 || token.contains(" ")) {
            return "令牌不对：应该是至少 16 位、不含空格的一串字符";
        }
        synchronized (RuntimeClient.class) {
            List<Target> list = targets(context);
            boolean registered = false;
            for (Target item : list) if (item.url.equals(url)) registered = true;
            if (list.size() >= MAX_TARGETS && !registered) return "最多登记 8 台电脑，请先移除不再使用的电脑";
            String name = hostOf(url);
            for (int index = list.size() - 1; index >= 0; index--) {
                if (list.get(index).url.equals(url)) {
                    name = list.get(index).name;
                    list.remove(index);
                }
            }
            list.add(new Target(name, url, token));
            store(context, list, url);
        }
        select(context, url);
        // 问一下这台电脑叫什么，列表里好认；问不到就先用地址当名字。
        final String address = url;
        final Context app = context.getApplicationContext();
        WORKER.execute(() -> {
            try {
                String name = request("GET", address + "/healthz", token, null, null, 8).get("name");
                if (name != null && !name.trim().isEmpty()) {
                    rename(app, address, name.trim());
                    HubStore.get().log("已登记 Runtime：" + name.trim());
                }
            } catch (IOException | RuntimeException error) {
                HubStore.get().log("已登记 " + address + "，但现在连不上它");
            }
        });
        ApprovalMonitor.reconcile(app);
        RuntimeWatchService.start(context);
        return null;
    }

    // ---- 最近的对话：存在手机上，换 Runtime 时带过去 ----

    /** 旧版单独存的最近几轮（只读，用于迁移；现在从聊天记录里算，见 HubStore.recentTurns）。 */
    static synchronized List<ChatTurn> turns(Context context) {
        SharedPreferences prefs = prefs(context);
        List<ChatTurn> list = new ArrayList<>();
        int count = prefs.getInt("turn_count", 0);
        for (int index = 0; index < count; index++) {
            String id = prefs.getString("turn_id_" + index, null);
            String text = prefs.getString("turn_text_" + index, null);
            String reply = prefs.getString("turn_reply_" + index, null);
            if (id != null && text != null && reply != null) {
                list.add(new ChatTurn(id, text, reply, prefs.getLong("turn_at_" + index, 0)));
            }
        }
        return list;
    }


    /** App 刚启动时把存着的对话放回聊天框。 */
    static void restoreChat(Context context) {
        Target target = selected(context);
        HubStore.get().initializeHistory(context, historyKey(context, target), turns(context));
    }

    static final String VOICE_PLACEHOLDER = "（语音）";

    /** 发一句话。结果通过 HubStore 的聊天状态反映出来，这里不返回。 */
    static void send(Context context, String text) {
        submit(context, text, null, null, false, null);
    }

    /** 在一件事里面说一句话（card 是它的编号）：这句话一定归到它，不混进主对话。 */
    static void send(Context context, String text, String card) {
        submit(context, text, null, card, true, null);
    }

    /**
     * 发一句话，并说明这件事让帮手用哪一档做（low、medium、high）。card 为 null 是对小幽
     * 说的，否则是在那件事里面说的。
     */
    static void send(Context context, String text, String card, String effort) {
        submit(context, text, null, card, card != null, effort);
    }

    static void send(Context context, String text, String card, String effort, String agent) {
        submit(context, text, null, card, card != null, effort, agent);
    }

    /**
     * 发一段录音（16 位单声道 WAV）。Runtime 先识别成文字，再像打字一样回答。
     * card 是说这句话时设备屏幕上的那件事（没有就是 null）：这句话是对它说的。pin 表示是在
     * 设备第三屏选中那件任务时说的：一定归到它，不混进对话。
     */
    static void sendVoice(Context context, byte[] wav, String card, boolean pin) {
        submit(context, VOICE_PLACEHOLDER, wav, card, pin, null);
    }

    private static void submit(Context context, String text, byte[] wav, String card,
                               boolean pin, String effort) {
        submit(context, text, wav, card, pin, effort, null);
    }

    private static void submit(Context context, String text, byte[] wav, String card,
                               boolean pin, String effort, String agent) {
        submit(context, text, wav, card, pin, effort, agent, null);
    }

    static void retry(Context context, String id, String text, String card) {
        if (HubStore.get().busy()) return;
        String client = HubStore.get().retryMessage(id);
        if (client != null) submit(context, text, null, card, card != null, null, null, client);
    }

    private static void submit(Context context, String text, byte[] wav, String card,
                               boolean pin, String effort, String agent, String retryId) {
        final Context app = context.getApplicationContext();
        final Target target = selected(app);
        final HubStore store = HubStore.get();
        final String url = target == null ? null : target.url;
        final String token = target == null ? null : target.token;
        // 电脑上的会话也能从这里接着聊（Runtime 0.8.1 起）：是否接得上由 Runtime 判断并回答。
        if (target == null) {
            store.chatFailed(text, "还没有设置 Runtime 的连接串");
            return;
        }
        final String clientId = retryId == null ? UUID.randomUUID().toString().replace("-", "") : retryId;
        store.selectedRuntime(url, historyKey(app, target));
        final long epoch = store.cardsEpoch();
        store.chatAsked(text, clientId, pin ? card : null);
        WORKER.execute(() -> {
            MessageStream events = null;
            try {
                // 别处的对话在连上时已经在后台带过去了（shareHistory），这里不再等它。
                Map<String, String> message;
                if (wav != null) {
                    message = request("POST", url + "/v1/voice?client_id=" + clientId
                                    + (BuddyProtocol.cardId(card) ? "&card=" + card + (pin ? "&pin=1" : "")
                                    : ""), token,
                            wav, "audio/wav", 30);
                } else {
                    message = request("POST", url + "/v1/messages", token,
                            CardViews.messageJson(text, clientId, card, effort, agent)
                                    .getBytes(StandardCharsets.UTF_8),
                            "application/json; charset=utf-8", 20);
                }
                String id = message.get("id");
                if (id == null) {
                    throw new IOException("Runtime 没有返回消息编号");
                }
                if (store.cardsEpoch() != epoch) return;
                store.chatAccepted(clientId, id);
                long deadline = System.currentTimeMillis() + GIVE_UP_MS;
                boolean heard = wav == null;
                boolean streamTried = false;
                while (true) {
                    if (store.cardsEpoch() != epoch) return;
                    String status = message.get("status");
                    String recognized = message.get("text");
                    if (!heard && recognized != null && !recognized.isEmpty()) {
                        heard = true;
                        store.chatHeard(VOICE_PLACEHOLDER, recognized);
                    }
                    if ("done".equals(status)) {
                        String reply = message.get("reply");
                        String brief = message.get("brief");
                        String filed = message.get("card");
                        store.chatAnswered(brief == null ? "" : brief, reply == null ? "" : reply,
                                message.get("mood"), filed);
                        if (filed != null) {
                            // Final text is already visible; refresh task details independently.
                            QUICK.execute(() -> {
                                if (store.cardsEpoch() != epoch) return;
                                try { fetchFeed(target, 0, false); }
                                catch (IOException | RuntimeException ignored) { }
                            });
                        }
                        if (!pin && reply != null && !reply.isEmpty()) {
                            // 这一轮记在聊天记录里，带给别的电脑时从那里算（HubStore.recentTurns）；
                            // 这台本来就有，不用再带。
                            sharedWith(url).add(id);
                        }
                        return;
                    }
                    if ("failed".equals(status)) {
                        String error = message.get("error");
                        store.chatFailed(null, error == null ? "Runtime 没有说明原因" : error);
                        return;
                    }
                    if (System.currentTimeMillis() > deadline) {
                        throw new IOException("等了太久还没有结果");
                    }
                    // 小幽把活交给了谁、帮手做完了没有：有变化就告诉中枢，设备上看得见。
                    store.chatProgress(message.get("helper"), "transcribing".equals(status) ? "正在识别语音…" : message.get("stage"));
                    // 回复还在生成：Runtime 给的是到目前为止的整段，对话里边生成边显示。
                    // 旧版 Runtime 没有这一项，就照旧等整段。
                    store.chatStreaming(message.get("partial"));
                    // 带上这条记录的版本号去等：记录一有变化 Runtime 就返回。旧版 Runtime
                    // 没有版本号，语音还没识别出来时改用短轮询，好让“我说了什么”尽快显示出来。
                    String rev = message.get("rev");
                    if (!streamTried && "true".equals(message.get("event_stream"))) {
                        streamTried = true;
                        try {
                            events = MessageStream.open(url, token, id, rev);
                            activeMessageStream = events;
                        } catch (IOException ignored) { /* Existing polling remains available. */ }
                    }
                    Map<String, String> pushed = null;
                    if (events != null) {
                        try {
                            pushed = events.next();
                            if (pushed != null && !id.equals(pushed.get("id"))) throw new IOException("回复消息编号不匹配");
                        } catch (IOException error) { pushed = null; }
                        if (pushed == null) {
                            events.close();
                            if (activeMessageStream == events) activeMessageStream = null;
                            events = null;
                        }
                    }
                    if (store.cardsEpoch() != epoch) return;
                    if (pushed != null) { message = pushed; continue; }
                    int wait = heard || rev != null ? POLL_SECONDS : 2;
                    message = request("GET", url + "/v1/messages/" + id + "?wait=" + wait
                            + (rev == null ? "" : "&rev=" + rev), token, null, null, wait + 15);
                }
            } catch (IOException | RuntimeException error) {
                if (store.cardsEpoch() != epoch) return;
                String detail = error.getMessage();
                store.chatFailed(null, "连不上 Runtime：" + (detail == null ? error.toString() : detail));
            } finally {
                if (events != null) {
                    events.close();
                    if (activeMessageStream == events) activeMessageStream = null;
                }
            }
        });
    }

    // ---- 设备固件：Runtime 那台电脑上留着每一版，手机负责把要的那一版交给设备 ----

    /** 界面要的版本清单；problem 不为 null 时是没问到的原因。在后台线程回调。 */
    interface FirmwareListCallback {
        void onVersions(List<Map<String, String>> versions, String problem);
    }

    static void fetchFirmwareVersions(Context context, FirmwareListCallback callback) {
        final Target target = selected(context.getApplicationContext());
        if (target == null) {
            callback.onVersions(new ArrayList<>(), "还没有登记电脑");
            return;
        }
        FIRMWARE_WORKER.execute(() -> {
            try {
                callback.onVersions(firmwareVersions(target), null);
            } catch (IOException | RuntimeException error) {
                callback.onVersions(new ArrayList<>(), "没问到 " + target.name + " 上有哪些固件："
                        + error.getMessage());
            }
        });
    }

    /** 在界面上选了一版（ref 为 null 是不推了）。结果写进日志；真正动手的是 FirmwareSync。 */
    static void chooseFirmware(Context context, String ref, String label) {
        final Target target = selected(context.getApplicationContext());
        if (target == null) {
            HubStore.get().log("固件：还没有登记电脑");
            return;
        }
        FIRMWARE_WORKER.execute(() -> {
            try {
                Map<String, String> answer = firmwareSetTarget(target, ref, "restore");
                String id = answer.get("id");
                HubStore.get().setFirmwareVersions(HubStore.get().firmwareBuild(), id);
                HubStore.get().log(ref == null ? "固件：不推了"
                        : "固件：已请 " + target.name + " 把设备换成" + label);
            } catch (IOException | RuntimeException error) {
                HubStore.get().log("固件：没办成：" + error.getMessage());
            }
        });
    }

    private static final int FIRMWARE_MAX_BYTES = 8 * 1024 * 1024;

    /** 电脑上发布的 App 更新（一层的对象；没有时 available 是 false）。 */
    /** 电脑上发布的资源包（语音模型等）；旧版 Runtime 没有这个接口时是空的。 */
    static List<AppPacks.Pack> appPacks(Target target) throws IOException {
        try {
            Map<String, Object> raw = Json.parseObject(requestText("GET", target.url + "/v1/app/packs",
                    target.token, null, null, 15));
            return raw == null ? new ArrayList<>() : AppPacks.parse(raw);
        } catch (IOException error) {
            if (String.valueOf(error.getMessage()).contains(" 404")) return new ArrayList<>();
            throw error;
        }
    }

    static Map<String, String> appRelease(Target target) throws IOException {
        return request("GET", target.url + "/v1/app?abi=" + AppUpdate.preferredAbi(android.os.Build.SUPPORTED_ABIS),
                target.token, null, null, 15);
    }

    /**
     * 现在该推给设备的是哪一版（一层的对象；没有时 id 是空串）。rev 不小于 0 时最多等 wait 秒，
     * 清单一有变化就返回。
     */
    static Map<String, String> firmwareTarget(Target target, int rev, int waitSeconds)
            throws IOException {
        String query = rev < 0 ? "" : "?wait=" + waitSeconds + "&rev=" + rev;
        return request("GET", target.url + "/v1/firmware/target" + query, target.token, null, null,
                waitSeconds + 15);
    }

    /** 把设备的情况告诉 Runtime；回来的还是“该推哪一版”。 */
    static Map<String, String> firmwareReport(Target target, String json) throws IOException {
        return request("POST", target.url + "/v1/firmware/device", target.token,
                json.getBytes(StandardCharsets.UTF_8), "application/json; charset=utf-8", 15);
    }

    /** 指定要推给设备的那一版：序号、编号、latest、previous；null 表示不推了。 */
    static Map<String, String> firmwareSetTarget(Target target, String ref, String reason)
            throws IOException {
        StringBuilder body = new StringBuilder("{\"id\":");
        if (ref == null) {
            body.append("null");
        } else {
            BuddyProtocol.quote(body, ref);
            body.append(",\"reason\":");
            BuddyProtocol.quote(body, reason);
        }
        body.append('}');
        return request("POST", target.url + "/v1/firmware/target", target.token,
                body.toString().getBytes(StandardCharsets.UTF_8),
                "application/json; charset=utf-8", 15);
    }

    /** 仓库里的版本，新的在前；每项是 Runtime 清单里的一条（id、seq、version、note、build…）。 */
    static List<Map<String, String>> firmwareVersions(Target target) throws IOException {
        return BuddyProtocol.parseObjectArray(
                requestText("GET", target.url + "/v1/firmware", target.token, null, null, 15),
                "versions");
    }

    /** 一版固件的应用镜像。 */
    static byte[] firmwareImage(Target target, String id) throws IOException {
        HttpURLConnection connection = (HttpURLConnection)
                new URL(target.url + "/v1/firmware/" + id + "/image").openConnection();
        try {
            connection.setConnectTimeout(10000);
            connection.setReadTimeout(60000);
            connection.setRequestProperty("Authorization", "Bearer " + target.token);
            int code = connection.getResponseCode();
            if (code != 200) {
                throw new IOException("Runtime 返回 " + code);
            }
            try (InputStream in = connection.getInputStream()) {
                ByteArrayOutputStream out = new ByteArrayOutputStream(2 * 1024 * 1024);
                byte[] buffer = new byte[16384];
                int count;
                while ((count = in.read(buffer)) > 0) {
                    out.write(buffer, 0, count);
                    if (out.size() > FIRMWARE_MAX_BYTES) {
                        throw new IOException("固件超过 8 MB");
                    }
                }
                return out.toByteArray();
            }
        } finally {
            connection.disconnect();
        }
    }

    private static Map<String, String> request(String method, String address, String token,
                                               byte[] body, String contentType,
                                               int timeoutSeconds)
            throws IOException {
        Map<String, String> fields = BuddyProtocol.parseFlatObject(
                requestText(method, address, token, body, contentType, timeoutSeconds, false));
        if (fields == null) {
            throw new IOException("Runtime 返回的内容看不懂");
        }
        return fields;
    }

    /** 发一个请求，返回响应的正文；状态码不是成功时抛出带说明的异常。 */
    private static String requestText(String method, String address, String token,
                                      byte[] body, String contentType, int timeoutSeconds)
            throws IOException {
        return requestText(method, address, token, body, contentType, timeoutSeconds, false);
    }

    /** feed 为 true 表示这是等卡变化的那条长连接，记下来好让换 Runtime 时能断开它。 */
    private static String requestText(String method, String address, String token,
                                      byte[] body, String contentType, int timeoutSeconds,
                                      boolean feed)
            throws IOException {
        // The watcher owns feed reconnection. A selection change must cancel it immediately.
        return RuntimeRetry.run(!feed && RuntimeRetry.safe(method, address, body),
                () -> requestOnce(method, address, token, body, contentType, timeoutSeconds, feed),
                Thread::sleep);
    }

    private static String requestOnce(String method, String address, String token,
                                      byte[] body, String contentType, int timeoutSeconds,
                                      boolean feed) throws IOException {
        HttpURLConnection connection = (HttpURLConnection) new URL(address).openConnection();
        connection.setInstanceFollowRedirects(false);
        if (feed) {
            feedConnection = connection;
        }
        try {
            connection.setRequestMethod(method);
            connection.setConnectTimeout(10000);
            connection.setReadTimeout(timeoutSeconds * 1000);
            connection.setRequestProperty("Authorization", "Bearer " + token);
            if (body != null) {
                connection.setDoOutput(true);
                connection.setRequestProperty("Content-Type", contentType);
                connection.setFixedLengthStreamingMode(body.length);
                try (OutputStream out = connection.getOutputStream()) {
                    out.write(body);
                }
            }
            int code = connection.getResponseCode();
            InputStream stream = code >= 400 ? connection.getErrorStream()
                    : connection.getInputStream();
            String text = stream == null ? "" : readAll(stream);
            if (code == 401) {
                throw new IOException("令牌不对（401）");
            }
            if (code >= 300) {
                Map<String, String> fields = BuddyProtocol.parseFlatObject(text);
                String error = fields == null ? null : fields.get("error");
                throw new IOException("Runtime 返回 " + code + (error == null ? "" : "：" + error));
            }
            return text;
        } finally {
            if (feed) {
                feedConnection = null;
            }
            connection.disconnect();
        }
    }

    private static String readAll(InputStream stream) throws IOException {
        return Json.readResponse(stream);
    }
}
