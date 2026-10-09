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
import android.view.ViewGroup;
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
 * 唯一的界面：和小幽的对话（一件事一张卡，等你点头的操作在最上面）、由哪台电脑回答、
 * 设备固件、三步设置（蓝牙权限、通知读取、连接设备）、来源开关、测试按钮和运行日志。
 * 界面全部用代码搭，不依赖任何第三方库。
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
    private LinearLayout thingList;
    private String thingKey = null;
    private static final int CARDS_SHOWN = 12;
    private TextView firmwareStatus;
    private LinearLayout firmwareList;
    /** 上次从电脑问到的版本清单，以及画列表时用的记号（清单或记号变了才重画）。 */
    private List<Map<String, String>> firmwareVersions = new ArrayList<>();
    private String firmwareKey = null;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        int pad = dp(16);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(pad, pad, pad, pad);

        root.addView(text("小幽中枢", 24, true));
        root.addView(text("和小幽聊天，并把她的回复和各个来源的通知转给随身设备。", 14, false));
        status = text("", 15, false);
        status.setPadding(0, dp(12), 0, dp(8));
        root.addView(status);

        root.addView(heading("和小幽聊天"));
        thingList = new LinearLayout(this);
        thingList.setOrientation(LinearLayout.VERTICAL);
        root.addView(thingList);
        chatView = text("", 15, false);
        chatView.setTextIsSelectable(true);
        root.addView(chatView);
        chatInput = input("想跟小幽说什么");
        chatInput.setSingleLine(false);
        chatInput.setMaxLines(4);
        root.addView(chatInput);
        root.addView(button("发送", view -> sendChat()));

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

        ScrollView scroll = new ScrollView(this);
        scroll.addView(root);
        setContentView(scroll);

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
        refreshThings(cards, store.approvals());
        StringBuilder talk = new StringBuilder();
        HubStore.Turn turn = store.turn();
        if (cards.isEmpty()) {
            // 旧版 Runtime 没有卡：照以前的样子一句一句列出来。
            for (String line : store.chatLines()) {
                talk.append(line).append("\n\n");
            }
        } else if (!store.busy() && turn.phase.equals("failed") && turn.card.isEmpty()) {
            // 没能变成卡的那一句（连不上、没收到声音）：原因写在这里。
            talk.append("（没成功）").append(turn.reply);
        }
        if (store.busy()) {
            if (!cards.isEmpty()) {
                talk.append("我：").append(store.busyText()).append("\n");
            }
            if (!turn.stage.isEmpty()) {
                talk.append("小幽：").append(turn.stage).append("\n");
            }
            talk.append(turn.agent.isEmpty() ? "小幽正在想…" : "小幽在等 " + turn.agent + " 做完…");
        }
        chatView.setText(talk.length() == 0 ? (cards.isEmpty() ? "（还没有聊过）" : "")
                : talk.toString().trim());
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
        RuntimeClient.send(this, message);
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
     * 等你点头的操作（在最上面，谁要做什么原样显示）和最近的卡，旧的在前。
     * 内容没变时不重建，免得打断点击和选中的文字。
     */
    private void refreshThings(List<Card> cards, List<Card.Approval> approvals) {
        int first = Math.max(0, cards.size() - CARDS_SHOWN);
        StringBuilder key = new StringBuilder();
        for (Card.Approval approval : approvals) {
            key.append(approval.id).append('@').append(approval.createdAt).append('|');
        }
        for (int index = first; index < cards.size(); index++) {
            key.append(cards.get(index).id).append('#').append(cards.get(index).seq).append('|');
        }
        if (key.toString().equals(thingKey)) {
            return;
        }
        thingKey = key.toString();
        thingList.removeAllViews();
        for (Card.Approval approval : approvals) {
            TextView ask = text("等你点头（" + approval.card + "）\n" + approval.tool + "\n"
                    + approval.detail, 15, true);
            ask.setTextIsSelectable(true);
            ask.setPadding(0, dp(8), 0, 0);
            thingList.addView(ask);
            LinearLayout row = new LinearLayout(this);
            row.setOrientation(LinearLayout.HORIZONTAL);
            row.addView(half(button("可以", view -> answerApproval(approval.id, true))));
            row.addView(half(button("不行", view -> answerApproval(approval.id, false))));
            thingList.addView(row);
        }
        SimpleDateFormat clock = new SimpleDateFormat("HH:mm", Locale.US);
        for (int index = first; index < cards.size(); index++) {
            Card card = cards.get(index);
            StringBuilder head = new StringBuilder(card.id);
            if (card.createdAt > 0) {
                head.append(" · ").append(clock.format(new Date((long) (card.createdAt * 1000))));
            }
            if (!card.agent.isEmpty()) {
                head.append(" · ").append(card.agent);
            }
            head.append(" · ").append(card.stateLabel());
            if (card.edits > 0) {
                head.append("（改过 ").append(card.edits).append(" 次）");
            }
            TextView title = text(head + "\n" + card.title, 13, true);
            title.setPadding(0, dp(12), 0, dp(2));
            thingList.addView(title);
            StringBuilder body = new StringBuilder();
            for (Card.Entry entry : card.entries) {
                body.append(entry.role.equals("you") ? "我：" : "小幽：").append(entry.text)
                        .append("\n");
            }
            if (card.active()) {
                // 帮手最近的几步：工具名和命令原样显示。
                for (String step : card.progress) {
                    body.append("› ").append(step).append("\n");
                }
            }
            TextView content = text(body.toString().trim(), 15, false);
            content.setTextIsSelectable(true);
            thingList.addView(content);
            if (card.active()) {
                thingList.addView(button("取消这件事", view -> RuntimeClient.cancel(this, card.id)));
            }
        }
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
