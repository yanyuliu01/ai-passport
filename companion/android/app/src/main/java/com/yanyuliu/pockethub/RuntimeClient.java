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
    private static final int POLL_SECONDS = 50;
    private static final long GIVE_UP_MS = 15 * 60 * 1000L;
    private static final ExecutorService WORKER = Executors.newSingleThreadExecutor();
    /** 回答授权、取消一件事：不排在正在等结果的那句话后面。 */
    private static final ExecutorService QUICK = Executors.newCachedThreadPool();
    private static final int FEED_WAIT_SECONDS = 50;
    private static Thread watcher;
    /** 正在等卡变化的那条连接：换 Runtime 时断开它，不用等它自己超时。 */
    private static volatile HttpURLConnection feedConnection;
    /** 界面上查固件、选版本用的线程：聊天那一条可能正等着小幽回话，不能排在它后面。 */
    private static final ExecutorService FIRMWARE_WORKER = Executors.newSingleThreadExecutor();

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

    static synchronized void select(Context context, String url) {
        store(context, targets(context), url);
        // 手里的卡是上一台的：马上清掉，不等盯着卡的那一路发现。
        HubStore.get().resetCards();
        HubStore.get().helpersChanged();
        HttpURLConnection waiting = feedConnection;
        if (waiting != null) {
            waiting.disconnect();
        }
    }

    // ---- 卡：一直等着 Runtime 说哪些事变了 ----

    /** 开始盯着现在用的那台 Runtime 的卡；已经在盯就什么都不做。进程在它就在。 */
    static synchronized void watch(Context context) {
        if (watcher != null) {
            return;
        }
        final Context app = context.getApplicationContext();
        watcher = new Thread(() -> watchLoop(app), "xiaoyou-feed");
        watcher.setDaemon(true);
        watcher.start();
    }

    private static void watchLoop(Context app) {
        HubStore store = HubStore.get();
        String watched = null;
        String complained = null;
        int failures = 0;
        while (true) {
            Target target = selected(app);
            if (target == null) {
                watched = null;
                pause(3);
                continue;
            }
            if (!target.url.equals(watched)) {
                watched = target.url;
                store.resetCards();
            }
            try {
                fetchFeed(target, FEED_WAIT_SECONDS, true);
                failures = 0;
                complained = null;
            } catch (IOException | RuntimeException error) {
                Target now = selected(app);
                if (now != null && !now.url.equals(watched)) {
                    continue;  // 是换 Runtime 时自己断开的
                }
                String detail = String.valueOf(error.getMessage());
                boolean old = detail.contains(" 404");
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
        String body = requestText("GET", target.url + "/v1/feed?after=" + after + "&wait=" + wait,
                target.token, null, null, wait + 15, interruptible);
        Map<String, Object> feed = Json.parseObject(body);
        if (feed == null) {
            throw new IOException("Runtime 返回的内容看不懂");
        }
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
        List<Card.Approval> approvals = new ArrayList<>();
        for (Object item : Json.list(feed, "approvals")) {
            Card.Approval approval = Card.Approval.from(Json.object(item));
            if (approval != null) {
                approvals.add(approval);
            }
        }
        store.applyFeed(seq, cards, approvals);
    }

    /** 回答 Runtime 上的一个授权。送不到时这个授权会重新拿出来问。 */
    static void approve(Context context, String approval, boolean allow) {
        final Target target = selected(context.getApplicationContext());
        final HubStore store = HubStore.get();
        if (target == null) {
            store.approvalFailed(approval, "还没有设置 Runtime 的连接串");
            return;
        }
        store.log((allow ? "回答：可以" : "回答：不行") + "（" + approval + "）");
        QUICK.execute(() -> {
            try {
                requestText("POST", target.url + "/v1/approvals/" + approval, target.token,
                        ("{\"decision\":\"" + (allow ? "allow" : "deny") + "\"}")
                                .getBytes(StandardCharsets.UTF_8),
                        "application/json; charset=utf-8", 15, false);
            } catch (IOException | RuntimeException error) {
                String detail = String.valueOf(error.getMessage());
                if (detail.contains(" 404") || detail.contains(" 409")) {
                    // 那件事已经结束，或者别处已经答过了：不用再问。
                    store.log("这个授权已经不用回答了：" + detail);
                } else {
                    store.approvalFailed(approval, detail);
                }
            }
        });
    }

    /** 取消一件事。结果从卡的变化里看。 */
    static void cancel(Context context, String card) {
        final Target target = selected(context.getApplicationContext());
        final HubStore store = HubStore.get();
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
        WORKER.execute(() -> {
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

    static synchronized void remove(Context context, String url) {
        List<Target> list = targets(context);
        Target current = selected(context);
        for (int index = list.size() - 1; index >= 0; index--) {
            if (list.get(index).url.equals(url)) {
                list.remove(index);
            }
        }
        store(context, list, current == null ? null : current.url);
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
        return null;
    }

    // ---- 最近的对话：存在手机上，换 Runtime 时带过去 ----

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

    private static synchronized void remember(Context context, ChatTurn turn) {
        List<ChatTurn> list = turns(context);
        list.add(turn);
        while (list.size() > TURNS_KEPT) {
            list.remove(0);
        }
        SharedPreferences.Editor editor = prefs(context).edit();
        editor.putInt("turn_count", list.size());
        for (int index = 0; index < list.size(); index++) {
            ChatTurn kept = list.get(index);
            editor.putString("turn_id_" + index, kept.id)
                    .putString("turn_text_" + index, kept.text)
                    .putString("turn_reply_" + index, kept.reply)
                    .putLong("turn_at_" + index, kept.at);
        }
        editor.apply();
    }

    /** App 刚启动时把存着的对话放回聊天框。 */
    static void restoreChat(Context context) {
        HubStore.get().restoreChat(turns(context));
    }

    static final String VOICE_PLACEHOLDER = "（语音）";

    /** 发一句话。结果通过 HubStore 的聊天状态反映出来，这里不返回。 */
    static void send(Context context, String text) {
        submit(context, text, null, null);
    }

    /**
     * 发一段录音（16 位单声道 WAV）。Runtime 先识别成文字，再像打字一样回答。
     * card 是说这句话时设备屏幕上的那件事（没有就是 null）：这句话是对它说的。
     */
    static void sendVoice(Context context, byte[] wav, String card) {
        submit(context, VOICE_PLACEHOLDER, wav, card);
    }

    private static void submit(Context context, String text, byte[] wav, String card) {
        final Context app = context.getApplicationContext();
        final Target target = selected(app);
        final HubStore store = HubStore.get();
        final String url = target == null ? null : target.url;
        final String token = target == null ? null : target.token;
        if (target == null) {
            store.chatFailed(text, "还没有设置 Runtime 的连接串");
            return;
        }
        store.chatAsked(text);
        final String clientId = UUID.randomUUID().toString().replace("-", "");
        WORKER.execute(() -> {
            try {
                // 先把最近的对话带过去：这台 Runtime 已经知道的它会自己跳过。
                List<ChatTurn> history = turns(app);
                if (!history.isEmpty()) {
                    try {
                        request("POST", url + "/v1/conversations/default/history", token,
                                ChatTurn.historyJson(history).getBytes(StandardCharsets.UTF_8),
                                "application/json; charset=utf-8", 15);
                    } catch (IOException error) {
                        // 旧版 Runtime 没有这个接口：照常发消息，只是它接不上别处的话。
                        store.log("没能把之前的对话带给 " + target.name + "：" + error.getMessage());
                    }
                }
                Map<String, String> message;
                if (wav != null) {
                    message = request("POST", url + "/v1/voice?client_id=" + clientId
                                    + (BuddyProtocol.cardId(card) ? "&card=" + card : ""), token,
                            wav, "audio/wav", 30);
                } else {
                    StringBuilder body = new StringBuilder("{\"text\":");
                    BuddyProtocol.quote(body, text);
                    body.append(",\"client_id\":\"").append(clientId).append("\"}");
                    message = request("POST", url + "/v1/messages", token,
                            body.toString().getBytes(StandardCharsets.UTF_8),
                            "application/json; charset=utf-8", 20);
                }
                String id = message.get("id");
                if (id == null) {
                    throw new IOException("Runtime 没有返回消息编号");
                }
                long deadline = System.currentTimeMillis() + GIVE_UP_MS;
                boolean heard = wav == null;
                while (true) {
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
                        if (filed != null) {
                            // 这句话归到了一张卡：先把卡取来，第一屏才不会先闪一下“说完了”
                            // 再变成“交给帮手了”。取不到也没关系，盯着卡的那一路会补上。
                            try {
                                fetchFeed(target, 0, false);
                            } catch (IOException | RuntimeException ignored) {
                                // 见上：另一路会补上。
                            }
                        }
                        store.chatAnswered(brief == null ? "" : brief, reply == null ? "" : reply,
                                message.get("mood"), filed);
                        String said = recognized != null && !recognized.isEmpty() ? recognized : text;
                        if (reply != null && !reply.isEmpty()) {
                            remember(app, new ChatTurn(id, said, reply,
                                    System.currentTimeMillis() / 1000L));
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
                    store.chatProgress(message.get("helper"), message.get("stage"));
                    // 带上这条记录的版本号去等：记录一有变化 Runtime 就返回。旧版 Runtime
                    // 没有版本号，语音还没识别出来时改用短轮询，好让“我说了什么”尽快显示出来。
                    String rev = message.get("rev");
                    int wait = heard || rev != null ? POLL_SECONDS : 2;
                    message = request("GET", url + "/v1/messages/" + id + "?wait=" + wait
                            + (rev == null ? "" : "&rev=" + rev), token, null, null, wait + 15);
                }
            } catch (IOException | RuntimeException error) {
                String detail = error.getMessage();
                store.chatFailed(null, "连不上 Runtime：" + (detail == null ? error.toString() : detail));
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
        HttpURLConnection connection = (HttpURLConnection) new URL(address).openConnection();
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
            if (code >= 400) {
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
        try (InputStream in = stream) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buffer = new byte[4096];
            int count;
            while ((count = in.read(buffer)) > 0) {
                out.write(buffer, 0, count);
                if (out.size() > 512 * 1024) {
                    throw new IOException("Runtime 返回的内容太长");
                }
            }
            return new String(out.toByteArray(), StandardCharsets.UTF_8);
        }
    }
}
