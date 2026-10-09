package com.yanyuliu.pockethub;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TimeZone;

/**
 * 前台服务：保持和小幽的蓝牙连接，把中枢的状态翻译成设备协议发过去，
 * 把设备上的回答交回中枢。应用界面关掉后它继续运行。
 */
public class LinkService extends Service implements BleLink.Listener, HubStore.Listener {
    static final String ACTION_STOP = "com.yanyuliu.pockethub.STOP";
    private static final String CHANNEL = "link";
    private static final int NOTIFICATION_ID = 1;
    private static final long KEEPALIVE_MS = 10000;

    private final Handler main = new Handler(Looper.getMainLooper());
    private BleLink link;
    private String lastTurnKey = "";
    private String lastHeartbeat = "";
    private String lastChat = "";
    private String lastHelpers = "";
    /** 设备声明了它认识 chat 和 helpers：对话按新的方式发，不再塞在心跳和 turn 里。 */
    private boolean deviceChat;
    /** 设备声明了它认识 card 和 tasks：三屏的固件。 */
    private boolean deviceCards;
    private long helpersSeen = -1;
    private int keepalives;
    /** 设备正在说话时不为 null。 */
    private VoiceRecording recording;
    /** 这段录音是对哪件事说的（按下时设备屏幕上的那张卡）；没有就是 null。 */
    private String recordingCard;
    /** 已经发给设备的卡：编号 → 发过去的那一行。内容没变就不重发。 */
    private final Map<String, String> sentCards = new HashMap<>();
    private long cardsEpochSent = -1;
    private String lastTasks = "";
    /** 让设备上的固件和电脑上指定的那一版保持一致。 */
    private FirmwareSync firmware;

    static void start(Context context) {
        Intent intent = new Intent(context, LinkService.class);
        if (Build.VERSION.SDK_INT >= 26) {
            context.startForegroundService(intent);
        } else {
            context.startService(intent);
        }
    }

    static void stop(Context context) {
        context.startService(new Intent(context, LinkService.class).setAction(ACTION_STOP));
    }

    @Override
    public void onCreate() {
        super.onCreate();
        link = new BleLink(this, this);
        firmware = new FirmwareSync(this, link, () -> recording != null);
        firmware.start();
        HubStore.get().addListener(this);
        RuntimeClient.watch(this);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            stopSelf();
            return START_NOT_STICKY;
        }
        Notification notification = buildNotification();
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(NOTIFICATION_ID, notification,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE);
        } else {
            startForeground(NOTIFICATION_ID, notification);
        }
        link.start();
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        main.removeCallbacksAndMessages(null);
        HubStore.get().removeListener(this);
        firmware.stop();
        link.stop();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private Notification buildNotification() {
        NotificationManager manager =
                (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        if (Build.VERSION.SDK_INT >= 26 && manager != null) {
            NotificationChannel channel = new NotificationChannel(CHANNEL,
                    getString(R.string.channel_link), NotificationManager.IMPORTANCE_LOW);
            manager.createNotificationChannel(channel);
        }
        PendingIntent open = PendingIntent.getActivity(this, 0,
                new Intent(this, MainActivity.class), PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder builder = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(this, CHANNEL) : new Notification.Builder(this);
        return builder.setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
                .setContentTitle(getString(R.string.app_name))
                .setContentText(getString(R.string.notification_running))
                .setContentIntent(open)
                .setOngoing(true)
                .build();
    }

    // ---- BleLink.Listener ----

    @Override
    public void onReady() {
        TimeZone zone = TimeZone.getDefault();
        long now = System.currentTimeMillis();
        link.send(BuddyProtocol.time(now / 1000L, zone.getOffset(now) / 1000));
        link.send(BuddyProtocol.hubHello());
        recording = null;
        lastHeartbeat = "";
        lastTurnKey = "";
        lastChat = "";
        lastHelpers = "";
        deviceChat = false;
        deviceCards = false;
        sentCards.clear();
        cardsEpochSent = -1;
        lastTasks = "";
        helpersSeen = -1;
        pushState();
        firmware.onReady();
        main.removeCallbacks(keepalive);
        main.postDelayed(keepalive, KEEPALIVE_MS);
    }

    @Override
    public void onLine(String line) {
        if (firmware.onLine(line)) {
            return;
        }
        BuddyProtocol.Decision decision = BuddyProtocol.parseDecision(line);
        if (decision != null) {
            // 帮手在 Runtime 上等着的授权交回 Runtime；别的是通知上的按钮。
            String approval = HubStore.get().takeApproval(decision.id);
            if (approval != null) {
                RuntimeClient.approve(this, approval, decision.allow);
            } else {
                HubStore.get().answer(decision.id, decision.allow);
            }
            return;
        }
        String voice = BuddyProtocol.parseVoiceState(line);
        if (voice != null) {
            onVoiceState(voice, BuddyProtocol.parseVoiceCard(line));
            return;
        }
        Boolean hub = BuddyProtocol.parseHubAck(line);
        if (hub != null) {
            deviceChat = BuddyProtocol.hubAckHasChat(line);
            deviceCards = deviceChat && BuddyProtocol.hubAckHasCards(line);
            HubStore.get().log(!hub ? "设备固件较旧，不支持按住说话"
                    : (deviceCards ? "设备支持按住说话，三屏：形象、对话、任务"
                            : (deviceChat ? "设备支持按住说话（固件是上一版的对话首页，没有卡）"
                                    : "设备支持按住说话（固件还是旧界面）")));
            if (deviceChat) {
                // 换成新的发法：心跳要重发一次（里面不再带对话），对话单独发。
                lastHeartbeat = "";
                pushState();
            }
        }
    }

    @Override
    public void onVoiceFrame(byte[] frame) {
        if (recording != null) {
            recording.add(frame);
        }
    }

    private void onVoiceState(String state, String card) {
        if ("start".equals(state)) {
            recordingCard = card;
            recording = new VoiceRecording();
            link.setFast(true);
            return;
        }
        VoiceRecording finished = recording;
        recording = null;
        link.setFast(false);
        if (finished == null || "cancel".equals(state)) {
            return;
        }
        HubStore store = HubStore.get();
        store.log("收到语音 " + finished.millis() / 100 / 10.0 + " 秒，" + finished.frames()
                + " 帧" + (finished.lostFrames() > 0 ? "，丢了 " + finished.lostFrames() + " 帧" : ""));
        if (finished.frames() == 0) {
            store.chatFailed(RuntimeClient.VOICE_PLACEHOLDER, "没有收到声音，再说一次吧");
        } else {
            // 上一句还没答完也照发：Runtime 那边按顺序一条一条处理。
            RuntimeClient.sendVoice(this, finished.toWav(), recordingCard);
        }
    }

    @Override
    public void onClosed() {
        main.removeCallbacks(keepalive);
        recording = null;
        deviceChat = false;
        deviceCards = false;
        firmware.onClosed();
    }

    /** 问一次小幽有哪些帮手，告诉设备。清单没变就不重发。 */
    private void refreshHelpers() {
        RuntimeClient.fetchAgents(this, agents -> main.post(() -> {
            if (link == null || !link.isReady() || !deviceChat) {
                return;
            }
            String line = BuddyProtocol.helpers(agents);
            if (!line.equals(lastHelpers)) {
                lastHelpers = line;
                link.send(line);
            }
        }));
    }

    // ---- HubStore.Listener ----

    @Override
    public void onHubChanged() {
        pushState();
    }

    // 设备 30 秒收不到心跳就认为连接失效，所以即使没有变化也定时重发。
    private final Runnable keepalive = new Runnable() {
        @Override
        public void run() {
            lastHeartbeat = "";
            if (!lastTasks.isEmpty()) {
                lastTasks = "\u0000";  // 有事在做：重发一次任务单子，秒数跟上
            }
            // 对话那一行比心跳长得多，不用每次都重发；每半分钟补发一次，万一设备那边
            // 因为心跳超时清掉了画面，也能恢复。
            if (++keepalives % 3 == 0) {
                lastChat = "";
            }
            pushState();
            main.postDelayed(this, KEEPALIVE_MS);
        }
    };

    /** 把卡和正在做的事告诉设备：第二屏和第三屏。只发变了的。 */
    private void pushCards(HubStore store) {
        if (cardsEpochSent != store.cardsEpoch()) {
            // 刚连上，或者手里的卡整个换了：让设备先忘掉旧的。
            cardsEpochSent = store.cardsEpoch();
            sentCards.clear();
            link.send(BuddyProtocol.cardClear());
        }
        List<Card> all = store.cards();
        // 设备上只放最近的几张；还在做的不管多早都带上。
        List<Card> shown = new ArrayList<>();
        int room = BuddyProtocol.CARD_KEPT;
        for (int index = all.size() - 1; index >= 0; index--) {
            Card card = all.get(index);
            if (card.active() || room > 0) {
                shown.add(0, card);
                room--;
            }
        }
        SimpleDateFormat clock = new SimpleDateFormat("HH:mm", Locale.US);
        long now = System.currentTimeMillis() / 1000L;
        List<BuddyProtocol.Task> tasks = new ArrayList<>();
        StringBuilder tasksKey = new StringBuilder();
        Map<String, String> kept = new HashMap<>();
        for (Card card : shown) {
            // 小屏幕上先看结论：简报在前，完整的话跟在后面。
            String line = BuddyProtocol.card(card.id,
                    card.createdAt > 0 ? clock.format(new Date((long) (card.createdAt * 1000))) : "",
                    card.state, card.agent, card.edits, card.said(),
                    BuddyProtocol.chatReply(card.active() ? "" : card.brief, card.lastSay()));
            if (line == null) {
                continue;
            }
            if (!line.equals(sentCards.get(card.id))) {
                link.send(line);
            }
            kept.put(card.id, line);
            if (card.active() && !card.agent.isEmpty()) {
                int steps = card.progress.size();
                double since = card.startedAt > 0 ? card.startedAt : card.createdAt;
                BuddyProtocol.Task task = new BuddyProtocol.Task(card.id, card.agent, card.title,
                        card.state.equals("waiting") ? "waiting"
                                : (card.queued ? "queued" : "working"),
                        since > 0 ? Math.max(0L, now - (long) since) : 0L,
                        steps >= 2 ? card.progress.get(steps - 2)
                                : (steps == 1 ? card.progress.get(0) : ""),
                        steps >= 2 ? card.progress.get(steps - 1) : "");
                tasks.add(task);
                tasksKey.append(task.id).append('\u0001').append(task.agent).append('\u0001')
                        .append(task.title).append('\u0001').append(task.state).append('\u0001')
                        .append(task.p1).append('\u0001').append(task.p2).append('\u0002');
            }
        }
        sentCards.clear();
        sentCards.putAll(kept);
        // 设备只放得下几件：多了就留最近开始的。
        while (tasks.size() > BuddyProtocol.TASK_COUNT) {
            tasks.remove(0);
        }
        // 做了多久由设备自己接着数，所以只在别的内容变了时才发；心跳那一拍会把它清空重发，
        // 顺便把秒数对齐。
        if (!tasksKey.toString().equals(lastTasks)) {
            lastTasks = tasksKey.toString();
            link.send(BuddyProtocol.tasks(tasks));
        }
    }

    private void pushState() {
        if (link == null || !link.isReady()) {
            return;
        }
        HubStore store = HubStore.get();
        // 新界面的设备：对话有自己的屏，通知页只放别的来源的通知。
        List<HubStore.Event> recent = deviceChat
                ? store.recentNotices(BuddyProtocol.ENTRY_COUNT)
                : store.recent(BuddyProtocol.ENTRY_COUNT);
        List<String> entries = new ArrayList<>();
        for (HubStore.Event event : recent) {
            entries.add(event.entry());
        }
        HubStore.Ask ask = store.currentAsk();
        BuddyProtocol.Prompt prompt = ask == null ? null
                : new BuddyProtocol.Prompt(ask.id, ask.source, ask.text);
        boolean busy = store.busy();
        // 旧界面的设备靠心跳里的这行字显示“在想：……”和最新一条消息；新界面不需要。
        String message = deviceChat ? ""
                : (busy ? "在想：" + store.busyText()
                        : (recent.isEmpty() ? "" : recent.get(0).summary()));
        String heartbeat = BuddyProtocol.heartbeat(Sources.enabledCount(this), busy ? 1 : 0,
                store.waiting(),
                message, entries, 0, store.todayCount(), prompt);
        if (!heartbeat.equals(lastHeartbeat)) {
            lastHeartbeat = heartbeat;
            link.send(heartbeat);
        }
        if (deviceChat) {
            HubStore.Turn turn = store.turn();
            String chat = deviceCards
                    ? BuddyProtocol.chat(turn.phase, turn.said, turn.reply, turn.agent,
                            turn.stage, turn.mood, turn.card, turn.doing)
                    : BuddyProtocol.chat(turn.phase, turn.said, turn.reply, turn.agent,
                            turn.stage, turn.mood);
            if (!chat.equals(lastChat)) {
                lastChat = chat;
                link.send(chat);
            }
            if (deviceCards) {
                pushCards(store);
            }
            if (helpersSeen != store.helpersVersion()) {
                helpersSeen = store.helpersVersion();
                refreshHelpers();
            }
            return;
        }
        if (!recent.isEmpty()) {
            // 最新一条消息的全文，显示在设备的“最新回复”页；同一条只发一次。
            HubStore.Event latest = recent.get(0);
            String turnKey = latest.key + "@" + latest.time;
            if (!turnKey.equals(lastTurnKey)) {
                lastTurnKey = turnKey;
                link.send(BuddyProtocol.turn(latest.full()));
            }
        }
    }
}
