package com.yanyuliu.pockethub;

import android.Manifest;
import android.app.Activity;
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
import android.widget.ScrollView;
import android.widget.TextView;

import java.util.ArrayList;
import java.util.List;

/**
 * 唯一的界面：三步设置（蓝牙权限、通知读取、连接设备）、来源开关、测试按钮和运行日志。
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
        chatView = text("", 15, false);
        chatView.setTextIsSelectable(true);
        root.addView(chatView);
        chatInput = input("想跟小幽说什么");
        chatInput.setSingleLine(false);
        chatInput.setMaxLines(4);
        root.addView(chatInput);
        root.addView(button("发送", view -> sendChat()));

        root.addView(heading("Runtime"));
        runtimeStatus = text("", 13, false);
        root.addView(runtimeStatus);
        pairingInput = input("粘贴连接串：http://地址:端口#令牌");
        root.addView(pairingInput);
        root.addView(button("保存连接串", view -> savePairing()));

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
        StringBuilder talk = new StringBuilder();
        for (String line : store.chatLines()) {
            talk.append(line).append("\n\n");
        }
        if (store.busy()) {
            talk.append("小幽正在想…");
        }
        chatView.setText(talk.length() == 0 ? "（还没有聊过）" : talk.toString().trim());
        String url = RuntimeClient.savedUrl(this);
        runtimeStatus.setText(url == null ? "还没有设置。在电脑上运行 python3 -m xiaoyou_runtime --pair"
                + " 得到连接串。" : "已设置：" + url);

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
        HubStore.get().log("已保存 Runtime 连接串");
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
