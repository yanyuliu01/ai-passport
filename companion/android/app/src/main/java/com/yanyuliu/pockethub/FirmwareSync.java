package com.yanyuliu.pockethub;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Handler;
import android.os.Looper;

import java.io.IOException;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * 让设备上的固件和 Runtime 那台电脑说的“该是哪一版”一致。
 *
 * 电脑上留着每一版固件，并且记着要推给设备的那一版；设备只连着手机。所以这里做三件事：
 * 连上设备时问它现在是哪一版并转告电脑；盯着电脑上“要推的那一版”；两边不一样时把镜像
 * 取来、经蓝牙传给设备（具体怎么传在 FirmwarePush），设备重启后再去“认可”新固件。
 * 要的那一版正好还在设备的另一个槽位里时不用传，让设备直接切回去。
 *
 * 除了标明的两处后台线程，所有方法都在主线程调用，状态也只在主线程读写。
 */
final class FirmwareSync {
    /** 设备此刻在不在对讲：正在说话时不开始传固件。 */
    interface Host {
        boolean recording();
    }

    private static final String PREFS = "firmware";
    private static final String KEY_EXPECT_BUILD = "expect_build";
    private static final String KEY_EXPECT_ID = "expect_id";
    private static final long TICK_MS = 250;
    private static final long PROGRESS_REPORT_MS = 3000;
    private static final long RETRY_MS = 3000;
    private static final int POLL_SECONDS = 50;

    private final Context context;
    private final BleLink link;
    private final Host host;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private Thread poller;
    private volatile boolean running;

    // 设备：这一次连接里它自己说的。
    private boolean ready;
    /** null 还不知道；false 是旧固件，不认识 fw。 */
    private Boolean supported;
    private String build = "";
    private String prev = "";
    private String state = "";
    private String version = "";
    private int maxSize;
    private boolean infoRequested;
    /** 这一次连接里说过几次“认可”：设备一直不认就不再说了，免得来回空转。 */
    private int confirmTries;

    // Runtime：该推哪一版。
    private String targetId = "";
    private String targetBuild = "";
    private String targetSha = "";
    private int targetSize;
    private int targetSeq;
    /** 只在轮询线程里读写：上次从哪台 Runtime 拿到的第几版清单。 */
    private int rev = -1;
    private String revUrl = "";

    // 正在进行的一次传输。
    private FirmwarePush push;
    private boolean downloading;
    private byte[] cachedImage;
    private String cachedId = "";
    /** 让设备直接切回去被拒绝过的那一版：对它只能老老实实传。 */
    private String rollbackRefused = "";
    private long progressReportedMs;
    private int progressShown = -1;
    /** 这一次传输里已经把数据来源接到蓝牙上了。 */
    private boolean sourceAttached;

    FirmwareSync(Context context, BleLink link, Host host) {
        this.context = context.getApplicationContext();
        this.link = link;
        this.host = host;
    }

    void start() {
        if (running) {
            return;
        }
        running = true;
        poller = new Thread(this::pollLoop, "firmware-target");
        poller.setDaemon(true);
        poller.start();
    }

    void stop() {
        running = false;
        if (poller != null) {
            poller.interrupt();
        }
        worker.shutdownNow();
        main.removeCallbacksAndMessages(null);
    }

    // ---- 设备连上、断开 ----

    /** 蓝牙就绪、已经打过招呼：问设备现在是哪一版。 */
    void onReady() {
        ready = true;
        supported = null;
        build = "";
        prev = "";
        state = "";
        push = null;
        sourceAttached = false;
        confirmTries = 0;
        infoRequested = true;
        link.send(BuddyProtocol.fwOp("info"));
    }

    void onClosed() {
        ready = false;
        supported = null;
        infoRequested = false;
        main.removeCallbacks(tick);
        main.removeCallbacks(considerLater);
        if (push != null && push.active()) {
            // 设备会把收了一半的留几分钟：重新连上后用同一份镜像再 begin，它会说从哪儿接着发。
            HubStore.get().setFirmware("连接断了，传到 " + push.percent() + "%；连上后接着传", true);
        }
        push = null;
    }

    /** 设备发来的一行；是关于固件的就处理掉并返回 true。 */
    boolean onLine(String line) {
        BuddyProtocol.FwLine reply = BuddyProtocol.parseFw(line);
        if (reply == null) {
            return false;
        }
        if (!reply.ack) {
            feedPush(reply);
            return true;
        }
        if (reply.op.isEmpty()) {
            // 旧固件对不认识的命令的应答里没有 op。
            if (!reply.ok && infoRequested) {
                infoRequested = false;
                supported = false;
                HubStore.get().setFirmware("设备上的固件不支持经蓝牙更新：要先插线刷一次新固件", true);
                report("unsupported", "", reply.error);
            }
            feedPush(reply);
            return true;
        }
        switch (reply.op) {
            case "info":
                onInfo(reply);
                break;
            case "confirm":
                if (!reply.ok) {
                    HubStore.get().log("固件：设备没能认可新固件（" + reply.error + "）");
                }
                // 再问一次，拿到认可之后的状态。
                link.send(BuddyProtocol.fwOp("info"));
                break;
            case "rollback":
                if (reply.ok) {
                    HubStore.get().setFirmware("设备正在切回另一个槽位里的那一版，重启后回来", true);
                } else {
                    expect("", "");
                    rollbackRefused = targetId;
                    HubStore.get().log("固件：设备不肯直接切回去（" + reply.error + "），改成重新传");
                    consider();
                }
                break;
            case "begin":
            case "end":
                feedPush(reply);
                break;
            default:
                break;
        }
        return true;
    }

    private void onInfo(BuddyProtocol.FwLine reply) {
        infoRequested = false;
        if (!reply.ok) {
            return;
        }
        supported = true;
        build = reply.text("build");
        prev = reply.text("prev");
        state = reply.text("state");
        version = reply.text("ver");
        maxSize = reply.number("max", 0);
        HubStore.get().setFirmwareVersions(build, targetId);
        if ("pending".equals(state) && confirmTries < 2) {
            // 新固件第一次启动：能连上、能说话，就算它过关。不说这一句，它三分钟后会自己退回上一版。
            ++confirmTries;
            link.send(BuddyProtocol.fwOp("confirm"));
            return;
        }
        SharedPreferences prefs = prefs();
        String expectedBuild = prefs.getString(KEY_EXPECT_BUILD, "");
        String expectedId = prefs.getString(KEY_EXPECT_ID, "");
        if (expectedBuild.isEmpty()) {
            HubStore.get().setFirmware("设备固件 " + describe(), false);
            report("connected", "", "");
        } else if (expectedBuild.equals(build)) {
            expect("", "");
            HubStore.get().setFirmware("新固件装好了：" + describe(), true);
            report("installed", expectedId, "");
        } else {
            expect("", "");
            HubStore.get().setFirmware("新固件没装成，设备还是 " + describe(), true);
            report("failed", expectedId, "设备重启后还是原来那一版（新固件没启动成功，或者没等到认可被退回）");
        }
        consider();
    }

    private String describe() {
        return (version.isEmpty() ? "" : version + " ") + "(" + build + ")";
    }

    // ---- Runtime 说该推哪一版 ----

    /** 轮询线程。 */
    private void pollLoop() {
        while (running) {
            RuntimeClient.Target runtime = RuntimeClient.selected(context);
            long pause = 0;
            if (runtime == null) {
                pause = 5000;
            } else {
                if (!runtime.url.equals(revUrl)) {
                    revUrl = runtime.url;
                    rev = -1;
                }
                try {
                    Map<String, String> fields =
                            RuntimeClient.firmwareTarget(runtime, rev, POLL_SECONDS);
                    rev = number(fields.get("rev"), -1);
                    main.post(() -> onTarget(fields));
                    if (rev < 0) {
                        pause = 5000;
                    }
                } catch (IOException | RuntimeException error) {
                    // 旧版 Runtime 没有这个接口，或者暂时连不上：过一阵再问。
                    rev = -1;
                    pause = 20000;
                }
            }
            try {
                Thread.sleep(pause);
            } catch (InterruptedException interrupted) {
                return;
            }
        }
    }

    private static int number(String text, int fallback) {
        if (text == null || text.isEmpty() || text.length() > 9) {
            return fallback;
        }
        for (int index = 0; index < text.length(); index++) {
            if (text.charAt(index) < '0' || text.charAt(index) > '9') {
                return fallback;
            }
        }
        return Integer.parseInt(text);
    }

    private void onTarget(Map<String, String> fields) {
        String id = fields.get("id");
        String wanted = id == null ? "" : id;
        if (!wanted.equals(targetId)) {
            rollbackRefused = "";
        }
        targetId = wanted;
        targetBuild = text(fields, "build");
        targetSha = text(fields, "sha256");
        targetSize = number(fields.get("size"), 0);
        targetSeq = number(fields.get("seq"), 0);
        HubStore.get().setFirmwareVersions(build, targetId);
        if (push != null && push.active() && !cachedId.equals(targetId)) {
            // 电脑那边改主意了：这一次不传了。
            link.send(BuddyProtocol.fwOp("abort"));
            endPush("换了要推的版本，这一次停下");
        }
        consider();
    }

    private static String text(Map<String, String> fields, String key) {
        String value = fields.get(key);
        return value == null ? "" : value;
    }

    private final Runnable considerLater = this::consider;

    /** 看看现在要不要动手：设备连着、认识 fw、电脑要的那一版和它跑的不一样、手头没有别的事。 */
    private void consider() {
        main.removeCallbacks(considerLater);
        if (!ready || !Boolean.TRUE.equals(supported) || push != null || downloading
                || targetId.isEmpty() || targetBuild.isEmpty() || targetBuild.equals(build)
                || "pending".equals(state) || !prefs().getString(KEY_EXPECT_BUILD, "").isEmpty()) {
            return;
        }
        if (host.recording()) {
            main.postDelayed(considerLater, RETRY_MS);
            return;
        }
        if (maxSize > 0 && targetSize > maxSize) {
            HubStore.get().setFirmware("第 " + targetSeq + " 版比设备的槽位还大，传不了", true);
            report("failed", targetId, "镜像比设备的槽位大");
            return;
        }
        if (!prev.isEmpty() && targetBuild.equals(prev) && !targetId.equals(rollbackRefused)) {
            // 要的就是设备另一个槽位里的那一版：不用传，让它切回去。
            expect(targetBuild, targetId);
            HubStore.get().setFirmware("第 " + targetSeq + " 版还在设备里，让它直接切回去", true);
            link.send(BuddyProtocol.fwOp("rollback"));
            return;
        }
        if (cachedImage != null && cachedId.equals(targetId)) {
            startPush();
            return;
        }
        download();
    }

    private void download() {
        final RuntimeClient.Target runtime = RuntimeClient.selected(context);
        if (runtime == null || worker.isShutdown()) {
            return;
        }
        final String id = targetId;
        final String sha = targetSha;
        final int size = targetSize;
        final int seq = targetSeq;
        downloading = true;
        HubStore.get().setFirmware("正在从 " + runtime.name + " 取第 " + seq + " 版固件…", true);
        worker.execute(() -> {
            byte[] image = null;
            String problem = null;
            try {
                image = RuntimeClient.firmwareImage(runtime, id);
                if (image.length != size || !sha256(image).equals(sha)) {
                    image = null;
                    problem = "取到的镜像和清单里的 SHA-256 对不上";
                }
            } catch (IOException | RuntimeException error) {
                problem = "没取到镜像：" + error.getMessage();
            }
            final byte[] fetched = image;
            final String failure = problem;
            main.post(() -> {
                downloading = false;
                if (fetched == null) {
                    HubStore.get().setFirmware("第 " + seq + " 版" + failure, true);
                    main.postDelayed(considerLater, 10 * RETRY_MS);
                    return;
                }
                cachedImage = fetched;
                cachedId = id;
                consider();
            });
        });
    }

    /** 后台线程里也用。 */
    static String sha256(byte[] data) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(data);
            StringBuilder out = new StringBuilder(64);
            for (byte value : digest) {
                out.append(Character.forDigit((value >> 4) & 0xF, 16));
                out.append(Character.forDigit(value & 0xF, 16));
            }
            return out.toString();
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException(error);
        }
    }

    // ---- 传 ----

    private void startPush() {
        long now = System.currentTimeMillis();
        push = new FirmwarePush(cachedImage, targetSha, now);
        sourceAttached = false;
        progressShown = -1;
        progressReportedMs = 0;
        link.setFast(true);
        link.send(push.beginLine());
        HubStore.get().setFirmware("开始把第 " + targetSeq + " 版传给设备（"
                + cachedImage.length / 1024 + " KB）", true);
        main.removeCallbacks(tick);
        main.postDelayed(tick, TICK_MS);
    }

    /** 蓝牙空下来时来要下一帧。窗口满了或者发完了就返回 null，等设备报了进度再接着要。 */
    private final BleLink.FrameSource frames = () -> {
        FirmwarePush current = push;
        return current == null ? null : current.nextFrame(System.currentTimeMillis());
    };

    private void feedPush(BuddyProtocol.FwLine reply) {
        if (push == null) {
            return;
        }
        push.onLine(reply, link.writePayload() - BuddyProtocol.FW_HEADER_BYTES,
                System.currentTimeMillis());
        advance();
    }

    /** 传输往前走一步：该发数据就接上数据来源，该说 end 就说，完了或者败了就收场。 */
    private void advance() {
        FirmwarePush current = push;
        if (current == null) {
            return;
        }
        long now = System.currentTimeMillis();
        String line = current.takeLine(now);
        if (line != null) {
            link.send(line);
        }
        switch (current.state()) {
            case SENDING:
                if (sourceAttached) {
                    link.kick();
                } else {
                    sourceAttached = true;
                    link.setFrameSource(frames);
                }
                break;
            case DONE:
                // 设备核对通过，约一秒后重启；重新连上后在 onInfo 里看它是不是真的换上了。
                expect(targetBuild, cachedId);
                endPush("第 " + targetSeq + " 版传完了，设备正在重启");
                return;
            case FAILED:
                onPushFailed(current.error());
                return;
            default:
                break;
        }
        int percent = current.percent();
        if (percent != progressShown) {
            progressShown = percent;
            HubStore.get().setFirmware("正在把第 " + targetSeq + " 版传给设备：" + percent + "%", false);
        }
        if (now - progressReportedMs >= PROGRESS_REPORT_MS && current.state() != FirmwarePush.State.BEGINNING) {
            progressReportedMs = now;
            reportProgress(cachedId, current.done(), current.size());
        }
    }

    private void onPushFailed(String error) {
        if ("unconfirmed".equals(error)) {
            // 设备上的新固件还在等认可：补上这一句，再问一次它的情况，之后会重新来过。
            endPush("设备上的固件还没被认可，先认可再传");
            link.send(BuddyProtocol.fwOp("confirm"));
            return;
        }
        if ("restarting".equals(error)) {
            endPush("设备正要重启，等它回来");
            return;
        }
        link.send(BuddyProtocol.fwOp("abort"));
        String id = cachedId;
        int seq = targetSeq;
        endPush("第 " + seq + " 版没传成：" + explain(error));
        report("failed", id, explain(error));
    }

    private void endPush(String line) {
        push = null;
        sourceAttached = false;
        main.removeCallbacks(tick);
        link.setFrameSource(null);
        link.setFast(false);
        HubStore.get().setFirmware(line, true);
    }

    private final Runnable tick = new Runnable() {
        @Override
        public void run() {
            FirmwarePush current = push;
            if (current == null) {
                return;
            }
            current.onTick(System.currentTimeMillis());
            advance();
            if (push != null) {
                main.postDelayed(this, TICK_MS);
            }
        }
    };

    /** 设备或者这一头给的原因，换成看得懂的话。 */
    static String explain(String error) {
        switch (error == null ? "" : error) {
            case "size":
                return "设备说镜像的大小不对";
            case "sha256":
                return "传到设备上的内容核对不上";
            case "image":
                return "设备不认这份镜像";
            case "flash":
                return "设备写闪存失败";
            case "memory":
                return "设备内存不够";
            case "no slot":
                return "设备的分区里没有第二个槽位（要先插线刷一次新固件）";
            case "link":
                return "蓝牙一次能带的数据太少";
            case "link too lossy":
            case "stalled":
                return "蓝牙连接太差，传不动";
            case "no answer":
                return "设备没有回应";
            case "unknown command":
            case "unsupported":
                return "设备上的固件不支持经蓝牙更新";
            default:
                return error == null || error.isEmpty() ? "原因不明" : error;
        }
    }

    // ---- 转告 Runtime ----

    private void report(String event, String id, String detail) {
        StringBuilder body = new StringBuilder("{\"event\":");
        BuddyProtocol.quote(body, event);
        body.append(",\"build\":");
        BuddyProtocol.quote(body, build);
        body.append(",\"prev\":");
        BuddyProtocol.quote(body, prev);
        body.append(",\"state\":");
        BuddyProtocol.quote(body, state);
        body.append(",\"ver\":");
        BuddyProtocol.quote(body, version);
        body.append(",\"max\":").append(Math.max(0, maxSize));
        body.append(",\"id\":");
        BuddyProtocol.quote(body, id);
        body.append(",\"detail\":");
        BuddyProtocol.quote(body, BuddyProtocol.clip(detail, 180));
        body.append('}');
        post(body.toString(), true);
    }

    private void reportProgress(String id, int sent, int size) {
        StringBuilder body = new StringBuilder("{\"event\":\"progress\",\"build\":");
        BuddyProtocol.quote(body, build);
        body.append(",\"state\":");
        BuddyProtocol.quote(body, state);
        body.append(",\"id\":");
        BuddyProtocol.quote(body, id);
        body.append(",\"sent\":").append(sent).append(",\"size\":").append(size).append('}');
        post(body.toString(), false);
    }

    private void post(String json, boolean useAnswer) {
        final RuntimeClient.Target runtime = RuntimeClient.selected(context);
        if (runtime == null || worker.isShutdown()) {
            return;
        }
        worker.execute(() -> {
            try {
                Map<String, String> answer = RuntimeClient.firmwareReport(runtime, json);
                if (useAnswer) {
                    main.post(() -> onTarget(answer));
                }
            } catch (IOException | RuntimeException error) {
                // 旧版 Runtime 没有这个接口，或者暂时连不上；下次连上设备时还会再报。
            }
        });
    }

    // ---- 跨过设备重启要记住的事 ----

    private SharedPreferences prefs() {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    /** 记下“设备重启后应该是这一版”；两个空串表示不等了。 */
    private void expect(String expectedBuild, String id) {
        prefs().edit().putString(KEY_EXPECT_BUILD, expectedBuild).putString(KEY_EXPECT_ID, id)
                .apply();
    }
}
