package com.yanyuliu.pockethub;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ComponentName;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Typeface;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.text.InputType;
import android.view.View;
import android.view.Gravity;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.RadioButton;
import android.widget.ScrollView;
import android.widget.TextView;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * 唯一的界面，分三页：
 *   对话  和小幽的日常对话连着显示；交给帮手的事在这里只留一行指路的。
 *   任务  交给帮手的事各有各的页面：打开一件，在里面说的话只归到它，不混进对话。
 *   设置  由哪台电脑回答、设备固件、三步设置、来源开关、测试按钮和运行日志。
 * 等你点头的操作在哪一页都显示在最上面。界面全部用代码搭，不依赖任何第三方库。
 */
public class MainActivity extends Activity implements HubStore.Listener {
    private static final int REQUEST_PERMISSIONS = 1;

    private TextView status;
    private TextView logView;
    private LinearLayout sourceList;
    private EditText packageInput;
    private EditText labelInput;
    private int testCount;
    private TextView chatView;
    private EditText chatInput;
    private EditText pairingInput;
    private TextView runtimeStatus;
    private LinearLayout runtimeList;
    private String runtimeKey = null;
    private static final int TAB_CHAT = 0;
    private static final int TAB_TASKS = 1;
    private static final int TAB_SETTINGS = 2;
    private static final int CARDS_SHOWN = 30;
    private int tab = TAB_CHAT;
    /** 任务页里打开的那件事；null 是任务列表。 */
    private String openTask = null;
    /** 输入框上面那一行点一下要去的任务；没有就是 null。 */
    private String nowJump = null;
    private Button[] tabButtons;
    private ScrollView scroll;
    private LinearLayout body;
    private LinearLayout askList;
    private LinearLayout chatPane;
    private LinearLayout chatList;
    private LinearLayout taskPane;
    private LinearLayout settingsPane;
    private LinearLayout composer;
    private String askKey = null;
    private String chatKey = null;
    private String taskKey = null;
    private TextView firmwareStatus;
    private LinearLayout firmwareList;
    /** 上次从电脑问到的版本清单，以及画列表时用的记号（清单或记号变了才重画）。 */
    private List<Map<String, String>> firmwareVersions = new ArrayList<>();
    private String firmwareKey = null;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        int pad = dp(16);
        LinearLayout page = column();
        page.setPadding(pad, dp(8), pad, dp(8));

        LinearLayout tabs = new LinearLayout(this);
        tabs.setOrientation(LinearLayout.HORIZONTAL);
        tabButtons = new Button[] {
                button("对话", view -> show(TAB_CHAT)),
                button("任务", view -> show(TAB_TASKS)),
                button("设置", view -> show(TAB_SETTINGS)),
        };
        for (Button choice : tabButtons) {
            tabs.addView(half(choice));
        }
        page.addView(tabs);
        askList = column();
        page.addView(askList);

        chatPane = column();
        chatList = column();
        chatPane.addView(chatList);
        taskPane = column();
        settingsPane = column();
        LinearLayout root = settingsPane;

        root.addView(text("小幽中枢", 24, true));
        root.addView(text("和小幽聊天，并把她的回复和各个来源的通知转给随身设备。", 14, false));
        status = text("", 15, false);
        status.setPadding(0, dp(12), 0, dp(8));
        root.addView(status);

        root.addView(heading("由哪台电脑回答"));
        runtimeList = new LinearLayout(this);
        runtimeList.setOrientation(LinearLayout.VERTICAL);
        root.addView(runtimeList);
        runtimeStatus = text("", 13, false);
        root.addView(runtimeStatus);
        pairingInput = input("粘贴连接串：http://地址:端口#令牌");
        root.addView(pairingInput);
        root.addView(button("添加这台电脑", view -> savePairing()));

        root.addView(heading("设备固件"));
        firmwareStatus = text("", 14, false);
        root.addView(firmwareStatus);
        firmwareList = new LinearLayout(this);
        firmwareList.setOrientation(LinearLayout.VERTICAL);
        root.addView(firmwareList);
        root.addView(button("看看电脑上留着哪些版本", view -> fetchFirmware()));
        root.addView(button("不换了（取消还没开始的）", view ->
                RuntimeClient.chooseFirmware(this, null, "")));

        root.addView(heading("设置"));
        root.addView(button("① 授权蓝牙", view -> requestBluetooth()));
        root.addView(button("② 开启通知读取", view -> startActivity(
                new Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))));
        root.addView(button("③ 连接小幽", view -> connect()));
        root.addView(button("断开", view -> {
            rememberConnect(false);
            LinkService.stop(this);
        }));

        root.addView(heading("来源"));
        sourceList = new LinearLayout(this);
        sourceList.setOrientation(LinearLayout.VERTICAL);
        root.addView(sourceList);
        packageInput = input("要添加的 App 包名，例如 com.example.app");
        labelInput = input("显示名称，例如 Gemini");
        root.addView(packageInput);
        root.addView(labelInput);
        root.addView(button("添加来源", view -> addSource()));

        root.addView(heading("测试"));
        root.addView(button("发一条测试消息", view -> HubStore.get().post(
                "test-" + (++testCount), "测试", "第 " + testCount + " 条",
                "这是从手机发给小幽的测试消息", null, null)));
        root.addView(button("清掉测试消息", view -> {
            for (int index = 1; index <= testCount; index++) {
                HubStore.get().dismiss("test-" + index);
            }
        }));

        root.addView(heading("日志"));
        logView = text("", 12, false);
        logView.setTypeface(Typeface.MONOSPACE);
        root.addView(logView);

        body = column();
        body.addView(chatPane);
        body.addView(taskPane);
        body.addView(settingsPane);
        scroll = new ScrollView(this);
        scroll.addView(body);
        page.addView(scroll, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1));

        // 输入框固定在最下面：在对话页是跟小幽说，在打开的任务里是接着那件事说。
        composer = column();
        chatView = text("", 14, false);
        chatView.setPadding(0, dp(6), 0, 0);
        chatView.setOnClickListener(view -> {
            if (nowJump != null) {
                open(nowJump);
            }
        });
        composer.addView(chatView);
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.BOTTOM);
        chatInput = input("想跟小幽说什么");
        chatInput.setSingleLine(false);
        chatInput.setMaxLines(4);
        chatInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE);
        row.addView(half(chatInput));
        Button send = button("发送", view -> sendChat());
        send.setLayoutParams(new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        row.addView(send);
        composer.addView(row);
        page.addView(composer);
        setContentView(page);
        getWindow().setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);
        applyTab();

        RuntimeClient.restoreChat(this);
        RuntimeClient.watch(this);
        // 上次是连着的：App 被系统关掉再打开后自己连回去，不用再点一次。
        if (hasBluetoothPermission()
                && getSharedPreferences("link", MODE_PRIVATE).getBoolean("connect", false)) {
            LinkService.start(this);
        }
    }

    private void rememberConnect(boolean connect) {
        getSharedPreferences("link", MODE_PRIVATE).edit().putBoolean("connect", connect).apply();
    }

    @Override
    protected void onResume() {
        super.onResume();
        HubStore.get().addListener(this);
        refreshSources();
        onHubChanged();
    }

    @Override
    protected void onPause() {
        HubStore.get().removeListener(this);
        super.onPause();
    }

    @Override
    public void onHubChanged() {
        HubStore store = HubStore.get();
        status.setText("蓝牙权限：" + (hasBluetoothPermission() ? "已授权" : "未授权")
                + "\n通知读取：" + (listenerEnabled() ? "已开启" : "未开启")
                + "\n设备连接：" + store.linkState()
                + "\n未处理的消息：" + store.waiting() + " 条");
        List<Card> cards = store.cards();
        if (openTask != null && CardViews.find(cards, openTask) == null) {
            // 打开的那件事没了（换了一台电脑，或者它那边的记录换了）：回到列表。
            openTask = null;
            applyTab();
        }
        int doing = CardViews.doing(cards);
        tabButtons[TAB_TASKS].setText(doing > 0 ? "任务 · 在做 " + doing : "任务");
        boolean atBottom = scroll.getScrollY() + scroll.getHeight() >= body.getHeight() - dp(48);
        refreshAsks(store.approvals());
        boolean grew = refreshChat(cards, store) && tab == TAB_CHAT;
        grew |= refreshTasks(cards) && tab == TAB_TASKS && openTask != null;
        if (grew && atBottom) {
            scrollDown();
        }

        // 输入框上面那一行：这句话现在怎么样了。
        StringBuilder talk = new StringBuilder();
        HubStore.Turn turn = store.turn();
        nowJump = null;
        if (store.busy()) {
            if (!cards.isEmpty()) {
                talk.append("我：").append(CardViews.oneLine(store.busyText(), 80)).append("\n");
            }
            if (!turn.stage.isEmpty()) {
                talk.append("小幽：").append(turn.stage).append("\n");
            }
            talk.append(turn.agent.isEmpty() ? "小幽正在想…" : "小幽在等 " + turn.agent + " 做完…");
        } else if (!cards.isEmpty() && turn.phase.equals("failed") && turn.card.isEmpty()) {
            // 没能变成卡的那一句（连不上、没收到声音）：原因写在这里。
            talk.append("（没成功）").append(turn.reply);
        } else if (tab == TAB_CHAT) {
            // 最新的话归到了一件任务上：对话页里看不到内容，给一条过去的路。
            Card filed = CardViews.find(cards, turn.card);
            if (filed != null && CardViews.isTask(filed)) {
                nowJump = filed.id;
                talk.append("↪ 最新的在任务 ").append(filed.id).append("「").append(filed.title)
                        .append("」里（").append(filed.stateLabel()).append("），点这里看");
            }
        }
        chatView.setText(talk.toString().trim());
        chatView.setVisibility(talk.length() == 0 ? View.GONE : View.VISIBLE);
        refreshRuntimes();
        firmwareStatus.setText(store.firmwareLine());
        refreshFirmware();

        StringBuilder lines = new StringBuilder();
        for (String line : store.logLines()) {
            lines.append(line).append('\n');
        }
        logView.setText(lines.length() == 0 ? "（还没有记录）" : lines.toString());
    }

    private void sendChat() {
        String message = chatInput.getText().toString().trim();
        if (message.isEmpty() || HubStore.get().busy()) {
            return;
        }
        chatInput.setText("");
        if (tab == TAB_TASKS && openTask != null) {
            RuntimeClient.send(this, message, openTask);
        } else {
            RuntimeClient.send(this, message);
        }
        scrollDown();
    }

    // ---- 三页 ----

    private void show(int next) {
        if (next == TAB_TASKS && tab == TAB_TASKS) {
            openTask = null;  // 已经在任务页：再点一下回到列表
        }
        tab = next;
        applyTab();
        onHubChanged();
        scrollDown();
    }

    /** 打开一件任务。 */
    private void open(String card) {
        openTask = card;
        tab = TAB_TASKS;
        applyTab();
        onHubChanged();
        scrollDown();
    }

    private void applyTab() {
        chatPane.setVisibility(tab == TAB_CHAT ? View.VISIBLE : View.GONE);
        taskPane.setVisibility(tab == TAB_TASKS ? View.VISIBLE : View.GONE);
        settingsPane.setVisibility(tab == TAB_SETTINGS ? View.VISIBLE : View.GONE);
        for (int index = 0; index < tabButtons.length; index++) {
            tabButtons[index].setEnabled(index != tab || (tab == TAB_TASKS && openTask != null));
            tabButtons[index].setTypeface(index == tab ? Typeface.DEFAULT_BOLD : Typeface.DEFAULT);
        }
        boolean inTask = tab == TAB_TASKS && openTask != null;
        composer.setVisibility(tab == TAB_CHAT || inTask ? View.VISIBLE : View.GONE);
        chatInput.setHint(inTask ? "接着 " + openTask + " 这件事说（只归到它）" : "想跟小幽说什么");
    }

    private void scrollDown() {
        scroll.post(() -> scroll.scrollTo(0, body.getHeight()));
    }

    @Override
    public void onBackPressed() {
        if (tab == TAB_TASKS && openTask != null) {
            show(TAB_TASKS);
        } else if (tab != TAB_CHAT) {
            show(TAB_CHAT);
        } else {
            super.onBackPressed();
        }
    }

    private LinearLayout column() {
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        return layout;
    }

    private void savePairing() {
        String problem = RuntimeClient.savePairing(this, pairingInput.getText().toString());
        if (problem != null) {
            HubStore.get().log(problem);
            runtimeStatus.setText(problem);
            return;
        }
        // 令牌不留在输入框里。
        pairingInput.setText("");
        HubStore.get().log("已保存连接串");
    }

    private void connect() {
        if (!hasBluetoothPermission()) {
            requestBluetooth();
            return;
        }
        rememberConnect(true);
        LinkService.start(this);
    }

    private void addSource() {
        String packageName = packageInput.getText().toString().trim();
        if (packageName.isEmpty()) {
            return;
        }
        Sources.put(this, packageName, labelInput.getText().toString(), true);
        packageInput.setText("");
        labelInput.setText("");
        refreshSources();
        HubStore.get().log("已添加来源 " + packageName);
    }

    /**
     * 等你点头的操作：在哪一页都在最上面，谁要做什么原样显示。
     * 内容没变时不重建，免得打断点击和选中的文字。
     */
    private void refreshAsks(List<Card.Approval> approvals) {
        StringBuilder key = new StringBuilder();
        for (Card.Approval approval : approvals) {
            key.append(approval.id).append('@').append(approval.createdAt).append('|');
        }
        if (key.toString().equals(askKey)) {
            return;
        }
        askKey = key.toString();
        askList.removeAllViews();
        for (Card.Approval approval : approvals) {
            TextView ask = text("等你点头（" + approval.card + "）\n" + approval.tool + "\n"
                    + approval.detail, 15, true);
            ask.setTextIsSelectable(true);
            ask.setPadding(0, dp(8), 0, 0);
            askList.addView(ask);
            LinearLayout row = new LinearLayout(this);
            row.setOrientation(LinearLayout.HORIZONTAL);
            row.addView(half(button("可以", view -> answerApproval(approval.id, true))));
            row.addView(half(button("不行", view -> answerApproval(approval.id, false))));
            row.addView(half(button("看这件事", view -> open(approval.card))));
            askList.addView(row);
        }
    }

    /**
     * 对话页：小幽自己答的连着显示，旧的在前；交给帮手的事只留一行，点开去它自己的页面。
     * 重建了返回 true。
     */
    private boolean refreshChat(List<Card> cards, HubStore store) {
        int first = Math.max(0, cards.size() - CARDS_SHOWN);
        List<String> lines = cards.isEmpty() ? store.chatLines() : new ArrayList<>();
        StringBuilder key = new StringBuilder();
        for (int index = first; index < cards.size(); index++) {
            key.append(cards.get(index).id).append('#').append(cards.get(index).seq).append('|');
        }
        key.append(lines.size()).append(lines.isEmpty() ? "" : lines.get(lines.size() - 1));
        if (key.toString().equals(chatKey)) {
            return false;
        }
        chatKey = key.toString();
        chatList.removeAllViews();
        if (cards.isEmpty()) {
            // 旧版 Runtime 没有卡：照以前的样子一句一句列出来。
            StringBuilder talk = new StringBuilder();
            for (String line : lines) {
                talk.append(line).append("\n\n");
            }
            TextView plain = text(talk.length() == 0 ? "（还没有聊过）" : talk.toString().trim(),
                    15, false);
            plain.setTextIsSelectable(true);
            chatList.addView(plain);
            return true;
        }
        for (int index = first; index < cards.size(); index++) {
            Card card = cards.get(index);
            if (CardViews.isTask(card)) {
                Button pointer = button(CardViews.pointer(card), view -> open(card.id));
                pointer.setGravity(Gravity.START | Gravity.CENTER_VERTICAL);
                chatList.addView(pointer);
            } else {
                TextView content = text(CardViews.thread(card), 15, false);
                content.setTextIsSelectable(true);
                content.setPadding(0, dp(8), 0, dp(8));
                chatList.addView(content);
            }
        }
        return true;
    }

    /**
     * 任务页：没打开哪件时是列表（在做的在前），打开了是那一件的全部内容。重建了返回 true。
     */
    private boolean refreshTasks(List<Card> cards) {
        List<Card> tasks = CardViews.tasks(cards, CARDS_SHOWN);
        Card opened = CardViews.find(cards, openTask);
        StringBuilder key = new StringBuilder(opened == null ? "" : opened.id).append('>');
        if (opened != null) {
            key.append(opened.seq);
        } else {
            for (Card card : tasks) {
                key.append(card.id).append('#').append(card.seq).append('|');
            }
        }
        if (key.toString().equals(taskKey)) {
            return false;
        }
        taskKey = key.toString();
        taskPane.removeAllViews();
        if (opened == null) {
            taskPane.addView(text(tasks.isEmpty() ? "（还没有交给帮手的事）"
                    : "点开一件看它的全部经过，在里面说的话只归到它。", 13, false));
            for (Card card : tasks) {
                Button item = button(CardViews.listItem(card), view -> open(card.id));
                item.setGravity(Gravity.START | Gravity.CENTER_VERTICAL);
                taskPane.addView(item);
            }
            return true;
        }
        taskPane.addView(button("← 所有任务", view -> show(TAB_TASKS)));
        String when = opened.createdAt > 0 ? " · " + new SimpleDateFormat("MM-dd HH:mm", Locale.US)
                .format(new Date((long) (opened.createdAt * 1000))) : "";
        TextView title = text(CardViews.status(opened) + when + "\n" + opened.title, 14, true);
        title.setPadding(0, dp(8), 0, dp(4));
        taskPane.addView(title);
        StringBuilder words = new StringBuilder(CardViews.thread(opened));
        if (opened.active()) {
            // 帮手最近的几步：工具名和命令原样显示。
            for (String step : opened.progress) {
                words.append(words.length() == 0 ? "" : "\n").append("› ").append(step);
            }
        }
        TextView content = text(words.toString(), 15, false);
        content.setTextIsSelectable(true);
        taskPane.addView(content);
        if (opened.active()) {
            taskPane.addView(button("取消这件事", view -> RuntimeClient.cancel(this, opened.id)));
        }
        return true;
    }

    private void answerApproval(String id, boolean allow) {
        // 设备上可能已经答过了：只有这里先拿到的那一方把回答交回 Runtime。
        String approval = HubStore.get().takeApproval(id);
        if (approval != null) {
            RuntimeClient.approve(this, approval, allow);
        }
    }

    private View half(View view) {
        view.setLayoutParams(new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        return view;
    }

    /** 登记过的 Runtime：点一下切换，长按删除。列表没变时不重建，免得打断点击。 */
    private void refreshRuntimes() {
        List<RuntimeClient.Target> targets = RuntimeClient.targets(this);
        RuntimeClient.Target current = RuntimeClient.selected(this);
        StringBuilder key = new StringBuilder(current == null ? "" : current.url);
        for (RuntimeClient.Target target : targets) {
            key.append('|').append(target.name).append('@').append(target.url);
        }
        if (key.toString().equals(runtimeKey)) {
            return;
        }
        runtimeKey = key.toString();
        runtimeList.removeAllViews();
        for (RuntimeClient.Target target : targets) {
            RadioButton choice = new RadioButton(this);
            choice.setText(target.name + "  (" + target.url + ")");
            choice.setChecked(current != null && current.url.equals(target.url));
            choice.setOnClickListener(view -> {
                RuntimeClient.select(this, target.url);
                HubStore.get().log("现在由 " + target.name + " 上的小幽回答");
            });
            choice.setOnLongClickListener(view -> {
                RuntimeClient.remove(this, target.url);
                HubStore.get().log("已删除 Runtime " + target.name);
                return true;
            });
            runtimeList.addView(choice);
        }
        runtimeStatus.setText(targets.isEmpty()
                ? "还没有登记电脑。在电脑上运行 python3 -m xiaoyou_runtime --pair 得到连接串。"
                : "点一下切换由哪台电脑回答，长按删除。换电脑时最近的对话会带过去。");
    }

    /** 问现在用的那台电脑：仓库里有哪些固件。 */
    private void fetchFirmware() {
        RuntimeClient.fetchFirmwareVersions(this, (versions, problem) -> runOnUiThread(() -> {
            if (problem != null) {
                HubStore.get().log("固件：" + problem);
            }
            firmwareVersions = versions;
            firmwareKey = null;
            refreshFirmware();
        }));
    }

    /** 电脑上留着的每一版一个按钮：点一下把设备换成这一版。清单和记号都没变时不重画。 */
    private void refreshFirmware() {
        HubStore store = HubStore.get();
        String running = store.firmwareBuild();
        String wanted = store.firmwareTarget();
        String key = running + "|" + wanted + "|" + firmwareVersions.size()
                + (firmwareVersions.isEmpty() ? "" : "|" + firmwareVersions.get(0).get("id"));
        if (key.equals(firmwareKey)) {
            return;
        }
        firmwareKey = key;
        firmwareList.removeAllViews();
        for (Map<String, String> version : firmwareVersions) {
            final String id = version.get("id");
            String seq = version.get("seq");
            if (id == null || seq == null) {
                continue;
            }
            final String label = "第 " + seq + " 版";
            String note = version.get("note");
            String name = version.get("version");
            boolean onDevice = !running.isEmpty() && running.equals(version.get("build"));
            boolean updatable = "true".equals(version.get("updatable"));
            StringBuilder line = new StringBuilder(label);
            if (name != null && !name.isEmpty()) {
                line.append("  ").append(name);
            }
            if (note != null && !note.isEmpty()) {
                line.append("  ").append(note);
            }
            if (onDevice) {
                line.append("  ● 设备上现在是这一版");
            } else if (id.equals(wanted)) {
                line.append("  → 正要换成这一版");
            } else if (!updatable) {
                line.append("  （装上后只能插线换，这里不提供）");
            }
            Button choice = button(line.toString(), view -> new AlertDialog.Builder(this)
                    .setMessage("把设备换成" + label + "？手机连着设备时会自己开始，大约一两分钟，"
                            + "期间设备屏幕显示进度。")
                    .setPositiveButton("换", (dialog, which) ->
                            RuntimeClient.chooseFirmware(this, id, label))
                    .setNegativeButton("算了", null)
                    .show());
            choice.setEnabled(updatable && !onDevice);
            firmwareList.addView(choice);
        }
        if (firmwareVersions.isEmpty()) {
            firmwareList.addView(text("（还没问过，或者电脑上还没有固件）", 12, false));
        }
    }

    private void refreshSources() {
        sourceList.removeAllViews();
        for (Sources.Source source : Sources.all(this)) {
            CheckBox box = new CheckBox(this);
            box.setText(source.label + "  (" + source.packageName + ")"
                    + (installed(source.packageName) ? "" : "  未安装"));
            box.setChecked(source.enabled);
            box.setOnCheckedChangeListener((view, checked) ->
                    Sources.put(this, source.packageName, source.label, checked));
            box.setOnLongClickListener(view -> {
                Sources.remove(this, source.packageName);
                refreshSources();
                return true;
            });
            sourceList.addView(box);
        }
        TextView hint = text("长按一项可以删除。", 12, false);
        sourceList.addView(hint);
    }

    private boolean installed(String packageName) {
        try {
            getPackageManager().getPackageInfo(packageName, 0);
            return true;
        } catch (PackageManager.NameNotFoundException error) {
            return false;
        }
    }

    private List<String> neededPermissions() {
        List<String> needed = new ArrayList<>();
        if (Build.VERSION.SDK_INT >= 31) {
            needed.add(Manifest.permission.BLUETOOTH_SCAN);
            needed.add(Manifest.permission.BLUETOOTH_CONNECT);
        } else {
            needed.add(Manifest.permission.ACCESS_FINE_LOCATION);
        }
        if (Build.VERSION.SDK_INT >= 33) {
            needed.add(Manifest.permission.POST_NOTIFICATIONS);
        }
        return needed;
    }

    private boolean hasBluetoothPermission() {
        for (String permission : neededPermissions()) {
            if (Manifest.permission.POST_NOTIFICATIONS.equals(permission)) {
                continue;
            }
            if (checkSelfPermission(permission) != PackageManager.PERMISSION_GRANTED) {
                return false;
            }
        }
        return true;
    }

    private void requestBluetooth() {
        requestPermissions(neededPermissions().toArray(new String[0]), REQUEST_PERMISSIONS);
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions,
                                           int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        onHubChanged();
    }

    private boolean listenerEnabled() {
        String enabled = Settings.Secure.getString(getContentResolver(),
                "enabled_notification_listeners");
        String mine = new ComponentName(this, NotifyListener.class).flattenToString();
        return enabled != null && enabled.contains(mine);
    }

    private int dp(int value) {
        return (int) (value * getResources().getDisplayMetrics().density);
    }

    private TextView text(String value, int size, boolean bold) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(size);
        if (bold) {
            view.setTypeface(Typeface.DEFAULT_BOLD);
        }
        return view;
    }

    private TextView heading(String value) {
        TextView view = text(value, 17, true);
        view.setPadding(0, dp(20), 0, dp(6));
        return view;
    }

    private Button button(String label, View.OnClickListener action) {
        Button button = new Button(this);
        button.setText(label);
        button.setAllCaps(false);
        button.setOnClickListener(action);
        button.setLayoutParams(new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return button;
    }

    private EditText input(String hint) {
        EditText view = new EditText(this);
        view.setHint(hint);
        view.setSingleLine(true);
        view.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        return view;
    }
}
