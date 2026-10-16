package com.yanyuliu.pockethub;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.Dialog;
import android.content.ComponentName;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.res.ColorStateList;
import android.graphics.Typeface;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.net.Uri;
import android.os.Handler;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.text.InputType;
import android.text.TextUtils;
import android.text.TextWatcher;
import android.text.Editable;
import android.view.View;
import android.view.Gravity;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.view.inputmethod.InputMethodManager;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.Toast;
import android.widget.ImageView;
import android.widget.RadioButton;
import android.widget.ScrollView;
import android.widget.TextView;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.io.File;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Set;

/**
 * DS is the main thread: chat with her, she hands work to helpers, you watch it progress.
 * A left sidebar holds every place (chat, tasks, helpers, connection and settings); task order
 * never follows task state, so nothing moves while the owner is reading. Existing BLE,
 * Runtime, readonly desktop, approval routing and attachment flows are retained.
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
    /** One task's detail; openTask names it. */
    private static final int TAB_TASKS = 1;
    private static final int TAB_USAGE = 2;
    private static final int TAB_SETTINGS = 3;
    /** 连接与设置: the home of every utility page. */
    private static final int TAB_MINE = 4;
    private static final int TAB_COMPANIONS = 5;
    /** One helper's page; openAgent names it. */
    private static final int TAB_AGENT = 6;
    /** 首页：小幽本人。打开 App 先到这里。 */
    private static final int TAB_HOME = 7;
    private static final int TAB_DEVICE = 8;
    private static final int INK = 0xff1f3149;
    private static final int MUTED = 0xff5f6f84;
    private static final int HINT = 0xff6b7a8f;
    private static final int BLUE = 0xff2f6fe0;
    private static final int AMBER = 0xffa8671a;
    private static final int GREEN = 0xff2f7a4b;
    private static final int DANGER = 0xffb3402f;
    private static final int LINE = 0xffeef1f6;
    private static final int BACKGROUND = 0xfff6f8fb;
    private int tab = TAB_HOME;
    /** 打开的那件事；null 时任务页回到聊天。 */
    private String openTask = null;
    /** 打开的那位帮手（role）。 */
    private String openAgent = null;
    private boolean agentDesktopOpen;
    /** 输入框上面那一行点一下要去的任务；没有就是 null。 */
    private String nowJump = null;
    private ScrollView scroll;
    private LinearLayout body;
    private LinearLayout approvalBar;
    private TextView approvalText;
    private String pendingApproval;
    private String pendingTask;
    private LinearLayout chatPane;
    private LinearLayout chatList;
    private LinearLayout taskPane;
    private LinearLayout homePane;
    private LinearLayout homeBar;
    private CompanionStage stage;
    private TextView homeSpeech;
    private LinearLayout busyRow;
    private HorizontalScrollView busyScroll;
    private LinearLayout homeActions;
    private View micButton;
    private TextView micHint;
    private long nextUpdateCheck;
    private String updateTarget = "";
    private View devicePill;
    private LinearLayout devicePane;
    private String deviceKey = null;
    private String busyKey = null;
    private final OnDeviceSpeech mic = new OnDeviceSpeech();
    /** 手机语音模型还没下载时只录音，松开后交给电脑识别。 */
    private final PhoneMic plainMic = new PhoneMic();
    private boolean listening;
    /** 松开之后、手机识别的最后结果还没到。 */
    private boolean transcribing;
    /** 手机识别到目前为止听到的字。 */
    private String heard = "";
    private long voiceSession;
    /** 被摸一下时她回的那句，显示到下一次状态变化。 */
    private String pokeLine = null;
    /** 见过的、交给帮手的事：新出现的才飞信。null 表示这台电脑的卡还没认过一遍。 */
    private Set<String> seenDispatched = null;
    private final Set<String> activeDispatched = new HashSet<>();
    /** 帮手刚做完的时刻（role → 毫秒），首页把它的胶囊变绿一会儿。 */
    private final Map<String, Long> doneAt = new HashMap<>();
    /** 帮手刚做完的那件事（role → 卡 id），点绿胶囊直接去看结果。 */
    private final Map<String, String> doneCard = new HashMap<>();
    /** 信还没送到的帮手：胶囊先保持空闲，信落下才开始转。 */
    private final Set<String> letterFor = new HashSet<>();
    /** 小幽话还没说完时先记下要送信给谁，说完再飞。 */
    private String pendingLetter = null;
    /** 首页常驻的帮手胶囊（role → 胶囊），状态变化时原地更新、不重建。 */
    private final Map<String, HelperChip> helperChips = new HashMap<>();
    private FrameLayout frame;
    private LinearLayout agentPane;
    private LinearLayout usagePane;
    private LinearLayout settingsPane;
    private LinearLayout connectionSettingsPane;
    private LinearLayout notificationSettingsPane;
    private LinearLayout preferenceSettingsPane;
    private int settingsSection;
    private LinearLayout minePane;
    private LinearLayout companionPane;
    private LinearLayout header;
    private View moreButton;
    private View sendButton;
    private View scrim;
    private LinearLayout drawer;
    private LinearLayout drawerList;
    private TextView drawerState;
    private boolean drawerOpen;
    private String drawerKey = null;
    private String companionChoice = "ds";
    private String composerContext = "";
    private final Map<String, String> drafts = new HashMap<>();
    private final Map<String, Integer> scrollPositions = new HashMap<>();
    private String lastRuntime = "";
    private LinearLayout composer;
    private String chatKey = null;
    private int chatWindow = 80;
    private final Map<String, TextView> chatWords = new HashMap<>();
    private final Map<String, TextView> chatStatus = new HashMap<>();
    private String taskKey = null;
    private String agentKey = null;
    private String mineKey = null;
    private TextView firmwareStatus;
    private LinearLayout firmwareList;
    private TextView usageView;
    private TextView usageSource;
    private TextView usageRefreshStatus;
    private Button usageRefresh;
    private long usageUiAtMs;
    private String usageRefreshingUrl;
    private long usageRefreshingEpoch;
    private long usageRefreshAttempt;
    /** 上次从电脑问到的版本清单，以及画列表时用的记号（清单或记号变了才重画）。 */
    private List<Map<String, String>> firmwareVersions = new ArrayList<>();
    private String firmwareKey = null;
    private TextView connectionTitle;
    private AvatarView lead;
    private List<String[]> helpers = new ArrayList<>();
    private String helpersTarget = "";
    private File exportFile;
    private Dialog approvalDialog;
    private final Handler uiHandler = new Handler();
    private final Runnable tick = new Runnable() {
        @Override public void run() {
            if (tab == TAB_USAGE && System.currentTimeMillis() - usageUiAtMs >= 15000L)
                refreshUsage(HubStore.get());
            if (drawerOpen) refreshDrawer();
            if (HubStore.get().runtimeConnected()) checkForUpdate();
            uiHandler.postDelayed(this, 1000);
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().setStatusBarColor(BACKGROUND);
        getWindow().setNavigationBarColor(BACKGROUND);
        getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR
                | View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR);
        LinearLayout page = column();
        page.setBackgroundColor(BACKGROUND);
        header = new LinearLayout(this);
        header.setPadding(dp(8), dp(8), dp(8), dp(8));
        header.setGravity(Gravity.CENTER_VERTICAL);
        header.addView(iconAction("menu", "打开侧边栏", view -> setDrawer(true)),
                new LinearLayout.LayoutParams(dp(44), dp(44)));
        lead = new AvatarView(this, "ds", "happy");
        lead.setBackground(surface(0xffe9f1ff, 0xffe9f1ff, 10));
        LinearLayout.LayoutParams leadParams = new LinearLayout.LayoutParams(dp(34), dp(34));
        leadParams.setMargins(dp(4), 0, dp(10), 0);
        header.addView(lead, leadParams);
        connectionTitle = text("小幽", 17, true);
        connectionTitle.setSingleLine(true); connectionTitle.setEllipsize(TextUtils.TruncateAt.END);
        header.addView(connectionTitle, new LinearLayout.LayoutParams(0, -2, 1));
        moreButton = iconAction("more", "这件事的操作", view -> taskActions(CardViews.find(HubStore.get().cards(), openTask)));
        header.addView(moreButton, new LinearLayout.LayoutParams(dp(44), dp(44)));
        devicePill = devicePill();
        LinearLayout.LayoutParams pillParams = new LinearLayout.LayoutParams(-2, dp(34));
        pillParams.setMargins(0, 0, dp(6), 0);
        header.addView(devicePill, pillParams);
        page.addView(header);
        // 只有等你确认的事才会出现在这里：它是唯一需要你马上处理的东西。
        approvalBar = new LinearLayout(this); approvalBar.setGravity(Gravity.CENTER_VERTICAL);
        approvalBar.setPadding(dp(12), 0, dp(12), 0);
        approvalBar.setBackground(pressable(0xfffff4e0, 12));
        View dot = new View(this); dot.setBackground(surface(0xffd9822b, 0xffd9822b, 4));
        approvalBar.addView(dot, new LinearLayout.LayoutParams(dp(8), dp(8)));
        approvalText = text("", 14, false); approvalText.setTextColor(0xff8a5413);
        approvalText.setSingleLine(true); approvalText.setEllipsize(TextUtils.TruncateAt.END);
        approvalText.setPadding(dp(8), 0, dp(8), 0);
        approvalBar.addView(approvalText, new LinearLayout.LayoutParams(0, -2, 1));
        TextView go = text("去确认 ›", 13, true); go.setTextColor(0xff8a5413); approvalBar.addView(go);
        approvalBar.setOnClickListener(view -> showApprovals());
        approvalBar.setFocusable(true); approvalBar.setVisibility(View.GONE);
        LinearLayout.LayoutParams barParams = new LinearLayout.LayoutParams(-1, dp(44));
        barParams.setMargins(dp(16), 0, dp(16), dp(4));
        page.addView(approvalBar, barParams);
        chatPane = column();
        chatList = column(); chatPane.addView(chatList);
        taskPane = column();
        agentPane = column();
        minePane = column();
        companionPane = column();
        usagePane = column();
        usageSource = text("", 14, true); usagePane.addView(usageSource);
        usageRefresh = button("刷新用量", view -> fetchUsage()); usagePane.addView(usageRefresh);
        usageRefreshStatus = text("", 13, false); usagePane.addView(usageRefreshStatus);
        usageView = text("", 14, false); usageView.setTextIsSelectable(true);
        usageView.setPadding(0, dp(12), 0, dp(12)); usagePane.addView(usageView);
        settingsPane = column();
        connectionSettingsPane = column(); notificationSettingsPane = column(); preferenceSettingsPane = column();
        settingsPane.addView(connectionSettingsPane); settingsPane.addView(notificationSettingsPane); settingsPane.addView(preferenceSettingsPane);
        LinearLayout root = connectionSettingsPane;
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

        root.addView(heading("随身设备"));
        root.addView(button("授权蓝牙", view -> requestBluetooth()));
        notificationSettingsPane.addView(button("开启通知读取", view -> startActivity(
                new Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))));
        root.addView(button("连接随身设备", view -> connect()));
        notificationSettingsPane.addView(button("开启审批通知", view -> {
            if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
                requestPermissions(new String[] {Manifest.permission.POST_NOTIFICATIONS}, 2);
            else startActivity(new Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, getPackageName()));
        }));
        CheckBox animation = new CheckBox(this);
        animation.setText("形象轻微呼吸动画");
        animation.setChecked(getSharedPreferences("ds_ui", MODE_PRIVATE).getBoolean("animate", true));
        animation.setOnCheckedChangeListener((view, checked) -> {
            getSharedPreferences("ds_ui", MODE_PRIVATE).edit().putBoolean("animate", checked).apply();
            refreshAvatarAnimations(getWindow().getDecorView());
        });
        preferenceSettingsPane.addView(animation);
        CheckBox computerVoice = new CheckBox(this);
        computerVoice.setText("手机说话交给电脑识别（和设备录音用同一个识别，连着电脑时才生效）");
        computerVoice.setChecked(voiceOnComputer());
        computerVoice.setOnCheckedChangeListener((view, checked) ->
                getSharedPreferences("ds_ui", MODE_PRIVATE).edit().putBoolean("voice_on_computer", checked).apply());
        preferenceSettingsPane.addView(computerVoice);
        root.addView(button("断开", view -> {
            rememberConnect(false);
            LinkService.stop(this);
        }));

        root = notificationSettingsPane;
        root.addView(heading("通知来源"));
        sourceList = new LinearLayout(this);
        sourceList.setOrientation(LinearLayout.VERTICAL);
        root.addView(sourceList);
        packageInput = input("要添加的 App 包名，例如 com.example.app");
        labelInput = input("显示名称，例如 Gemini");
        root.addView(packageInput);
        root.addView(labelInput);
        root.addView(button("添加来源", view -> addSource()));

        root = preferenceSettingsPane;
        root.addView(heading("测试"));
        root.addView(button("发一条测试消息", view -> HubStore.get().post(
                "test-" + (++testCount), "测试", "第 " + testCount + " 条",
                "这是从手机发给ds的测试消息", null, null)));
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
        body.setPadding(dp(16), dp(4), dp(16), dp(18));
        homePane = buildHome(); devicePane = column();
        body.addView(homePane); body.addView(devicePane);
        body.addView(chatPane); body.addView(taskPane); body.addView(agentPane); body.addView(minePane);
        body.addView(companionPane); body.addView(usagePane); body.addView(settingsPane);
        scroll = new ScrollView(this); scroll.setFillViewport(true);
        scroll.setVerticalScrollBarEnabled(false); scroll.addView(body);
        page.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
        composer = column(); composer.setPadding(dp(12), dp(6), dp(12), dp(16));
        composer.setBackgroundColor(BACKGROUND);
        chatView = text("", 12, false); chatView.setPadding(dp(8), dp(2), dp(8), dp(6));
        chatView.setOnClickListener(view -> { if (nowJump != null) open(nowJump); });
        composer.addView(chatView);
        LinearLayout row = new LinearLayout(this); row.setGravity(Gravity.CENTER_VERTICAL);
        // 附件按钮等电脑端能收手机附件后再出现，不放一个点了没用的按钮。
        chatInput = input("跟小幽说…");
        chatInput.setSingleLine(false); chatInput.setMaxLines(4);
        chatInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE);
        chatInput.setTextSize(15); chatInput.setPadding(dp(16), dp(10), dp(16), dp(10));
        chatInput.setTextColor(INK); chatInput.setHintTextColor(HINT);
        chatInput.setMinHeight(dp(44)); chatInput.setMinimumHeight(dp(44));
        chatInput.setIncludeFontPadding(false);
        chatInput.setBackground(surface(Color.WHITE, 0xffe6ebf2, 22));
        row.addView(chatInput, new LinearLayout.LayoutParams(0, -2, 1));
        LinearLayout send = column(); send.setGravity(Gravity.CENTER);
        send.setBackground(pressable(BLUE, 22)); send.setFocusable(true);
        send.setOnClickListener(view -> sendChat());
        NavIconView arrow = new NavIconView(this, "send"); arrow.color(Color.WHITE);
        send.addView(arrow, new LinearLayout.LayoutParams(dp(22), dp(22)));
        sendButton = send;
        LinearLayout.LayoutParams sendParams = new LinearLayout.LayoutParams(dp(44), dp(44));
        sendParams.setMargins(dp(8), 0, 0, 0); row.addView(sendButton, sendParams);
        composer.addView(row); page.addView(composer);
        homeBar = buildHomeBar(); page.addView(homeBar);
        chatInput.addTextChangedListener(new TextWatcher() {
            public void beforeTextChanged(CharSequence value, int start, int count, int after) { }
            public void onTextChanged(CharSequence value, int start, int before, int count) { updateSendState(); }
            public void afterTextChanged(Editable value) { }
        });

        scrim = new View(this); scrim.setBackgroundColor(0x52142032);
        scrim.setOnClickListener(view -> setDrawer(false)); scrim.setVisibility(View.GONE);
        scrim.setContentDescription("关闭侧边栏");
        drawer = column(); drawer.setBackgroundColor(Color.WHITE);
        drawer.setPadding(dp(12), dp(16), dp(12), dp(12)); drawer.setClickable(true);
        drawer.setVisibility(View.GONE);
        ScrollView drawerScroll = new ScrollView(this); drawerScroll.setVerticalScrollBarEnabled(false);
        drawerList = column(); drawerScroll.addView(drawerList);
        drawer.addView(drawerScroll, new LinearLayout.LayoutParams(-1, 0, 1));
        View divider = new View(this); divider.setBackgroundColor(LINE);
        drawer.addView(divider, new LinearLayout.LayoutParams(-1, dp(1)));
        LinearLayout footer = listRow(52, false);
        NavIconView gear = new NavIconView(this, "gear"); gear.color(INK);
        footer.addView(gear, new LinearLayout.LayoutParams(dp(22), dp(22)));
        TextView footerLabel = text("连接与设置", 14, false); footerLabel.setTextColor(INK);
        footerLabel.setPadding(dp(12), 0, dp(8), 0);
        footer.addView(footerLabel, new LinearLayout.LayoutParams(0, -2, 1));
        drawerState = text("", 12, false); footer.addView(drawerState);
        footer.setOnClickListener(view -> { setDrawer(false); show(TAB_MINE); });
        drawer.addView(footer, new LinearLayout.LayoutParams(-1, -2));
        frame = new FrameLayout(this);
        frame.addView(page, new FrameLayout.LayoutParams(-1, -1));
        frame.addView(scrim, new FrameLayout.LayoutParams(-1, -1));
        frame.addView(drawer, new FrameLayout.LayoutParams(dp(312), -1));
        if (savedInstanceState != null) {
            tab = savedInstanceState.getInt("tab", TAB_HOME);
            settingsSection = savedInstanceState.getInt("settingsSection", 0);
            openTask = savedInstanceState.getString("openTask");
            openAgent = savedInstanceState.getString("openAgent");
            Bundle savedDrafts = savedInstanceState.getBundle("drafts");
            if (savedDrafts != null) for (String key : savedDrafts.keySet()) drafts.put(key, savedDrafts.getString(key, ""));
        }
        setContentView(frame);
        getWindow().setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);
        lastRuntime = runtimeUrl();
        companionChoice = getSharedPreferences("ds_ui", MODE_PRIVATE)
                .getString("companion_" + ArtifactDownload.key(lastRuntime), "ds");
        refreshMine(); refreshCompanions(); applyTab();

        RuntimeClient.restoreChat(this);
        RuntimeClient.watch(this);
        RuntimeWatchService.start(this);
        String requestedCard = getIntent().getStringExtra("card");
        if (requestedCard != null) pendingTask = requestedCard;
        pendingApproval = getIntent().getStringExtra("approval");
        // 上次是连着的：App 被系统关掉再打开后自己连回去，不用再点一次。
        if (hasBluetoothPermission()
                && getSharedPreferences("link", MODE_PRIVATE).getBoolean("connect", false)) {
            LinkService.start(this);
        }
    }

    private void rememberConnect(boolean connect) {
        getSharedPreferences("link", MODE_PRIVATE).edit().putBoolean("connect", connect).apply();
    }

    private void toast(String message) { Toast.makeText(this, message, Toast.LENGTH_SHORT).show(); }

    private void refreshAvatarAnimations(View view) {
        if (view instanceof AvatarView) ((AvatarView)view).refreshAnimation();
        if (view instanceof ViewGroup) {
            ViewGroup group = (ViewGroup)view;
            for (int i=0;i<group.getChildCount();i++) refreshAvatarAnimations(group.getChildAt(i));
        }
    }

    private View iconAction(String icon, String label, View.OnClickListener action) {
        LinearLayout container = column(); container.setGravity(Gravity.CENTER);
        container.setContentDescription(label); container.setFocusable(true);
        container.setOnClickListener(action);
        container.setBackground(pressable(Color.TRANSPARENT, 22));
        NavIconView view = new NavIconView(this, icon); view.color(INK);
        container.addView(view, new LinearLayout.LayoutParams(dp(22), dp(22)));
        return container;
    }

    private View emptyState(String title, String message) {
        LinearLayout panel = column(); panel.setGravity(Gravity.CENTER);
        panel.setPadding(dp(20), dp(36), dp(20), dp(36));
        panel.addView(new AvatarView(this, companionChoice, "idle"), new LinearLayout.LayoutParams(dp(130), dp(130)));
        TextView name = text(title, 19, true); name.setPadding(0, dp(16), 0, dp(8));
        name.setGravity(Gravity.CENTER); panel.addView(name);
        TextView words = text(message, 13, false); words.setGravity(Gravity.CENTER); panel.addView(words);
        return panel;
    }

    // ---- Sidebar ----

    private void setDrawer(boolean open) {
        if (open == drawerOpen) return;
        drawerOpen = open;
        float width = dp(312);
        drawer.animate().cancel(); scrim.animate().cancel();
        if (open) {
            InputMethodManager keyboard = (InputMethodManager) getSystemService(INPUT_METHOD_SERVICE);
            if (keyboard != null) keyboard.hideSoftInputFromWindow(chatInput.getWindowToken(), 0);
            drawerKey = null; refreshDrawer();
            drawer.setVisibility(View.VISIBLE); scrim.setVisibility(View.VISIBLE);
            drawer.setTranslationX(-width); scrim.setAlpha(0f);
            drawer.animate().translationX(0).setDuration(200).start();
            scrim.animate().alpha(1f).setDuration(200).start();
        } else {
            drawer.animate().translationX(-width).setDuration(180)
                    .withEndAction(() -> { if (!drawerOpen) drawer.setVisibility(View.GONE); }).start();
            scrim.animate().alpha(0f).setDuration(180)
                    .withEndAction(() -> { if (!drawerOpen) scrim.setVisibility(View.GONE); }).start();
        }
    }

    private String marksKey(String kind) { return kind + "_" + ArtifactDownload.key(runtimeUrl()); }

    /** 置顶、归档只记在这台手机上，按电脑分开；标题和状态仍以电脑上的为准。 */
    private Set<String> marks(String kind) {
        return new HashSet<>(getSharedPreferences("ds_ui", MODE_PRIVATE).getStringSet(marksKey(kind), new HashSet<>()));
    }

    private void mark(String kind, String id, boolean on) {
        Set<String> ids = marks(kind);
        if (on) ids.add(id); else ids.remove(id);
        getSharedPreferences("ds_ui", MODE_PRIVATE).edit().putStringSet(marksKey(kind), ids).apply();
        drawerKey = null; refreshDrawer();
    }

    /** 聊天对象的名字：默认形象就是小幽本人。 */
    private String companionName() {
        return AppPresentation.role(companionChoice).equals("ds") ? "小幽" : AppPresentation.name(companionChoice);
    }

    private List<String> offeredHelpers() {
        List<String> names = new ArrayList<>();
        for (String[] helper : helpers) names.add(helper[0]);
        return names;
    }

    /** 侧边栏：和小幽聊天、任务、帮手，最后是连接与设置。内容没变时不重建。 */
    private void refreshDrawer() {
        HubStore store = HubStore.get();
        boolean connected = store.runtimeConnected();
        boolean device = store.linkState().equals("已连接");
        boolean computer = RuntimeClient.selected(this) != null && connected;
        if (AppUpdate.available != null) {
            drawerState.setText("有新版 App"); drawerState.setTextColor(BLUE);
        } else {
            drawerState.setText(device && computer ? "全部在线" : !computer ? "电脑离线" : "设备未连接");
            drawerState.setTextColor(device && computer ? GREEN : AMBER);
        }
        if (!drawerOpen) return;
        Set<String> pinned = marks("pinned"), archived = marks("archived");
        List<Card> cards = store.cards();
        List<Card> tasks = AppPresentation.sidebarTasks(cards, pinned, archived);
        List<AppPresentation.Helper> roster = AppPresentation.helpers(cards, offeredHelpers());
        StringBuilder key = new StringBuilder().append(tab).append(openTask).append(openAgent).append(connected).append(store.linkState())
                .append(companionChoice).append(pinned).append(archived.size())
                .append(java.time.LocalDate.now()).append(java.time.ZoneId.systemDefault());
        for (Card card : tasks) key.append(card.id).append(':').append(card.state).append(card.resultReady).append(card.queued).append(card.title).append(card.createdAt).append('|');
        for (AppPresentation.Helper helper : roster) key.append(helper.role).append(helper.status(connected)).append('|');
        if (key.toString().equals(drawerKey)) return;
        drawerKey = key.toString();
        drawerList.removeAllViews();

        LinearLayout home = listRow(52, tab == TAB_HOME);
        home.addView(avatar(companionChoice, "happy", 32));
        TextView homeLabel = text(companionName(), 15, true);
        homeLabel.setPadding(dp(12), 0, 0, 0);
        home.addView(homeLabel, new LinearLayout.LayoutParams(0, -2, 1));
        home.setOnClickListener(view -> { setDrawer(false); show(TAB_HOME); });
        drawerList.addView(home);
        LinearLayout chat = listRow(44, tab == TAB_CHAT);
        NavIconView history = new NavIconView(this, "history"); history.color(MUTED);
        LinearLayout.LayoutParams historyParams = new LinearLayout.LayoutParams(dp(20), dp(20));
        historyParams.setMargins(dp(6), 0, dp(6), 0); chat.addView(history, historyParams);
        TextView chatLabel = text("对话记录", 14, false); chatLabel.setTextColor(INK);
        chatLabel.setPadding(dp(10), 0, 0, 0);
        chat.addView(chatLabel, new LinearLayout.LayoutParams(0, -2, 1));
        chat.setOnClickListener(view -> { setDrawer(false); show(TAB_CHAT); });
        drawerList.addView(chat);

        drawerList.addView(drawerHeading("任务"));
        if (tasks.isEmpty()) {
            TextView none = text("在聊天里交给小幽一件事，它会出现在这里。", 12, false);
            none.setTextColor(HINT); none.setPadding(dp(10), dp(4), dp(10), dp(8)); drawerList.addView(none);
        }
        for (AppPresentation.TaskSection section : AppPresentation.taskSections(cards, pinned, archived,
                System.currentTimeMillis(), java.time.ZoneId.systemDefault())) {
            drawerList.addView(drawerHeading(section.title));
        for (Card card : section.cards) {
            LinearLayout row = listRow(48, tab == TAB_TASKS && card.id.equals(openTask));
            row.addView(avatar(AppPresentation.owner(card), "idle", 24));
            String word = AppPresentation.stateWord(card, connected);
            TextView title = text(card.title, 14, false);
            title.setTextColor(word.isEmpty() ? MUTED : INK);
            title.setSingleLine(true); title.setEllipsize(TextUtils.TruncateAt.END);
            LinearLayout words = column(); words.setPadding(dp(10), dp(6), dp(6), dp(6));
            words.addView(title);
            String time = AppPresentation.taskTime(card, pinned.contains(card.id), java.time.ZoneId.systemDefault());
            TextView clock = text(time, 11, false); clock.setTextColor(HINT); words.addView(clock);
            row.addView(words, new LinearLayout.LayoutParams(0, -2, 1));
            if (pinned.contains(card.id)) {
                NavIconView pin = new NavIconView(this, "pin"); pin.color(0xff8796a8);
                pin.setContentDescription("已置顶"); pin.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_YES);
                LinearLayout.LayoutParams pinParams = new LinearLayout.LayoutParams(dp(14), dp(14));
                pinParams.setMargins(0, 0, dp(6), 0); row.addView(pin, pinParams);
            }
            if (!word.isEmpty()) row.addView(stateText(word, 12));
            row.setOnClickListener(view -> { setDrawer(false); open(card.id); });
            row.setOnLongClickListener(view -> { taskActions(card); return true; });
            row.setContentDescription(card.title + "，" + AppPresentation.taskDay(card, System.currentTimeMillis(), java.time.ZoneId.systemDefault()) + " " + time
                    + (word.isEmpty() ? "" : "，" + word) + "，长按整理");
            drawerList.addView(row);
        }
        }
        if (!archived.isEmpty()) {
            LinearLayout row = listRow(40, false);
            TextView label = text("已归档 " + archived.size(), 12, false); label.setTextColor(HINT);
            label.setPadding(dp(34), 0, 0, 0); row.addView(label);
            row.setOnClickListener(view -> showArchived());
            drawerList.addView(row);
        }

        if (!roster.isEmpty()) drawerList.addView(drawerHeading("帮手"));
        for (AppPresentation.Helper helper : roster) {
            LinearLayout row = listRow(48, tab == TAB_AGENT && helper.role.equals(openAgent));
            row.addView(avatar(helper.role, helper.current(connected) != null ? "focus" : "idle", 30));
            TextView name = text(AppPresentation.name(helper.role), 14, false); name.setTextColor(INK);
            name.setPadding(dp(10), 0, dp(6), 0);
            row.addView(name, new LinearLayout.LayoutParams(0, -2, 1));
            String state = helper.status(connected);
            TextView word = stateText(state, 12);
            if (state.equals("空闲")) word.setTextColor(HINT);
            row.addView(word);
            row.setOnClickListener(view -> { setDrawer(false); openAgent(helper.role); });
            row.setContentDescription(AppPresentation.name(helper.role) + "，" + state);
            drawerList.addView(row);
        }

        drawerList.addView(drawerHeading("设备"));
        LinearLayout deviceRow = listRow(48, tab == TAB_DEVICE);
        NavIconView deviceIcon = new NavIconView(this, "device"); deviceIcon.color(INK);
        LinearLayout.LayoutParams deviceParams = new LinearLayout.LayoutParams(dp(22), dp(22));
        deviceParams.setMargins(dp(4), 0, dp(4), 0); deviceRow.addView(deviceIcon, deviceParams);
        TextView deviceLabel = text("随身小幽", 14, false); deviceLabel.setTextColor(INK);
        deviceLabel.setPadding(dp(10), 0, dp(6), 0);
        deviceRow.addView(deviceLabel, new LinearLayout.LayoutParams(0, -2, 1));
        boolean near = device;
        TextView deviceState = text(near ? "在身边" : "未连接", 12, false); deviceState.setTextColor(near ? GREEN : HINT);
        deviceRow.addView(deviceState);
        deviceRow.setOnClickListener(view -> { setDrawer(false); show(TAB_DEVICE); });
        drawerList.addView(deviceRow);
    }

    private TextView drawerHeading(String label) {
        TextView view = text(label, 12, false); view.setTextColor(HINT);
        view.setPadding(dp(10), dp(18), dp(10), dp(6));
        return view;
    }

    /** 置顶 / 归档 / 停止：侧边栏长按和任务页右上角是同一组。不提供重命名，标题以电脑上的为准。 */
    private void taskActions(Card card) {
        if (card == null) return;
        boolean pinned = marks("pinned").contains(card.id);
        boolean stoppable = !card.monitoringOnly && card.active();
        List<String> items = new ArrayList<>();
        items.add(pinned ? "取消置顶" : "置顶");
        items.add("归档");
        if (stoppable) items.add("停止");
        new AlertDialog.Builder(this).setTitle(card.title)
                .setItems(items.toArray(new String[0]), (dialog, index) -> {
                    if (index == 0) mark("pinned", card.id, !pinned);
                    else if (index == 1) {
                        mark("archived", card.id, true); mark("pinned", card.id, false);
                        if (card.id.equals(openTask)) show(TAB_HOME);
                        toast("已归档，可在侧边栏「已归档」找回");
                    } else confirmStop(card);
                }).show();
    }

    private void showArchived() {
        Set<String> archived = marks("archived");
        List<Card> found = new ArrayList<>();
        for (Card card : HubStore.get().cards()) if (archived.contains(card.id)) found.add(card);
        if (found.isEmpty()) {
            getSharedPreferences("ds_ui", MODE_PRIVATE).edit().remove(marksKey("archived")).apply();
            drawerKey = null; refreshDrawer(); return;
        }
        String[] titles = new String[found.size()];
        for (int index = 0; index < titles.length; index++) titles[index] = found.get(index).title;
        new AlertDialog.Builder(this).setTitle("点一件放回任务列表")
                .setItems(titles, (dialog, index) -> mark("archived", found.get(index).id, false))
                .setNegativeButton("关闭", null).show();
    }

    // ---- 连接与设置 ----

    private void refreshMine() {
        if (minePane == null) return;
        HubStore store = HubStore.get();
        RuntimeClient.Target target = RuntimeClient.selected(this);
        boolean device = store.linkState().equals("已连接");
        int running = 0;
        for (Card card : store.cards()) if (card.active() || (card.monitoringOnly && card.state.equals("working"))) running++;
        AppUpdate.Release update = AppUpdate.availableFor(RuntimeClient.selected(this));
        String key = store.linkState() + (target == null ? "" : target.name + target.url) + store.runtimeConnected()
                + running + companionChoice + (update == null ? "" : update.sha256) + updateProgress
                + "|" + packProgress + "|" + System.identityHashCode(AppUpdate.packs);
        if (key.equals(mineKey)) return;
        mineKey = key;
        minePane.removeAllViews();
        minePane.addView(groupHeading("连接"));
        LinearLayout links = group();
        links.addView(groupRow("device", "随身设备", device ? null : store.linkState(),
                device ? "已连接" : "未连接", device ? GREEN : AMBER, view -> openSettings(0), true));
        links.addView(groupRow("computer", target == null ? "电脑" : target.name,
                target == null ? "还没有添加电脑" : running > 0 ? running + " 件事在运行" : "没有在运行的事",
                target == null ? "去添加" : store.runtimeConnected() ? "在线" : "离线",
                target != null && store.runtimeConnected() ? GREEN : AMBER, view -> openSettings(0), false));
        minePane.addView(links);
        minePane.addView(groupHeading("设置"));
        LinearLayout settings = group();
        settings.addView(groupRow(null, "伴侣形象", null, AppPresentation.name(companionChoice) + " ›", HINT,
                view -> { refreshCompanions(); show(TAB_COMPANIONS); }, true));
        settings.addView(groupRow(null, "用量", null, "›", HINT, view -> show(TAB_USAGE), true));
        settings.addView(groupRow(null, "设备固件", null, "›", HINT, view -> openSettings(0), true));
        settings.addView(groupRow(null, "通知", null, "›", HINT, view -> openSettings(1), true));
        settings.addView(groupRow(null, "偏好与运行记录", null, "›", HINT, view -> openSettings(2), true));
        String installed = "";
        try { installed = getPackageManager().getPackageInfo(getPackageName(), 0).versionName; } catch (Exception ignored) { }
        settings.addView(speechPackRow());
        settings.addView(groupRow(null, "App 更新",
                update == null ? "现在是 " + installed : update.label() + (update.note.isEmpty() ? "" : " · " + update.note),
                updateProgress >= 0 ? "下载中 " + updateProgress + "%" : update != null ? "安装 ›" : "检查 ›",
                update != null ? BLUE : HINT, view -> updateApp(), false));
        minePane.addView(settings);
        TextView note = text("FoloToy AI Passport", 11, false); note.setTextColor(HINT);
        note.setGravity(Gravity.CENTER); note.setPadding(0, dp(28), 0, dp(14)); minePane.addView(note);
    }

    /** 下载新版时的进度（0–99）；不在下载时是 -1。 */
    private int updateProgress = -1;
    /** 下载语音模型时的进度（0–99）；不在下载时是 -1。 */
    private int packProgress = -1;

    interface SourceChoice { void picked(boolean external); }

    /**
     * 每次下载前选从哪里下：网上（GitHub，发布时传上去的同一个文件）或者这台电脑。只有电脑能下时
     * （what 为 null）直接从电脑下，不多问。
     */
    private void chooseSource(String what, SourceChoice choice) {
        if (what == null) { choice.picked(false); return; }
        new android.app.AlertDialog.Builder(this)
                .setTitle("从哪里下载" + what + "？")
                .setItems(new String[] {"从网上下载（GitHub）", "从电脑下载"}, (dialog, which) -> choice.picked(which == 0))
                .setNegativeButton("取消", null)
                .show();
    }

    private static String mb(long bytes) {
        return String.format(java.util.Locale.ROOT, "%.1f MB", bytes / 1048576.0);
    }

    /** 语音模型这一行：电脑上发布的语音资源包，和手机上装好的比，只下载变了的文件。 */
    private View speechPackRow() {
        AppPacks.Pack pack = AppPacks.find(AppUpdate.packs, AppPacks.SPEECH);
        boolean ready = AppPacks.ready(this, AppPacks.SPEECH, AppPacks.SPEECH_FILES);
        List<AppPacks.Item> todo = pack == null ? new ArrayList<>() : AppPacks.missing(pack, AppPacks.installed(this, AppPacks.SPEECH));
        String detail = pack == null ? (ready ? "已装好；电脑上没有新的" : "还没下载；先交给电脑识别")
                : todo.isEmpty() ? "已是最新" : (ready ? "有更新，" : "还没下载，") + "要下 " + mb(AppPacks.bytes(todo));
        String end = packProgress >= 0 ? "下载中 " + packProgress + "%" : pack != null && !todo.isEmpty() ? (ready ? "更新 ›" : "下载 ›") : "检查 ›";
        return groupRow(null, "手机语音模型", detail, end, pack != null && !todo.isEmpty() ? BLUE : HINT,
                view -> updateSpeechPack(), true);
    }

    private void updateSpeechPack() {
        RuntimeClient.Target target = RuntimeClient.selected(this);
        if (target == null) { toast("先添加电脑"); return; }
        if (packProgress >= 0) return;
        AppPacks.Pack pack = AppPacks.find(AppUpdate.packs, AppPacks.SPEECH);
        List<AppPacks.Item> todo = pack == null ? null : AppPacks.missing(pack, AppPacks.installed(this, AppPacks.SPEECH));
        if (pack == null || todo.isEmpty()) {
            toast("正在问电脑有没有新的语音模型…");
            AppUpdate.check(this, target, (newer, error) -> {
                if (error != null) toast("没问到：" + error);
                mineKey = null; onHubChanged();
            });
            return;
        }
        chooseSource(pack.hosted() ? "语音模型 " + mb(AppPacks.bytes(todo)) : null, external -> {
            packProgress = 0; mineKey = null; refreshMine();
            AppPacks.update(this, target, pack, external, percent -> {
                packProgress = percent; mineKey = null; refreshMine();
            }, error -> {
                packProgress = -1; mineKey = null; refreshMine();
                toast(error == null ? "手机语音模型已就绪" : "没下载成：" + error);
            });
        });
    }

    /** 没有更新时去问一次；有更新时下载、校验，再交给系统安装（主人要在系统界面点确认）。 */
    private void updateApp() {
        RuntimeClient.Target target = RuntimeClient.selected(this);
        if (target == null) { toast("先添加电脑"); return; }
        if (updateProgress >= 0) return;
        AppUpdate.Release update = AppUpdate.availableFor(RuntimeClient.selected(this));
        if (update == null) {
            toast("正在问电脑有没有新版…");
            AppUpdate.check(this, target, (newer, error) -> {
                if (error != null) toast("没问到：" + error);
                else if (newer == null) toast("已经是最新的了");
                else toast("有新版 " + newer.label() + "，再点一次安装");
                mineKey = null; drawerKey = null; onHubChanged();
            });
            return;
        }
        chooseSource(update.downloadUrl.isEmpty() ? null : "安装包 " + mb(update.size), external -> {
            updateProgress = 0; mineKey = null; refreshMine();
            downloadApp(target, update, external);
        });
    }

    private void downloadApp(RuntimeClient.Target target, AppUpdate.Release update, boolean external) {
        AppUpdate.download(this, target, update, external, percent -> {
            updateProgress = percent; mineKey = null; refreshMine();
        }, (apk, error) -> {
            updateProgress = -1; mineKey = null; refreshMine();
            if (error != null) { toast("没下载成：" + error); return; }
            if (!AppUpdate.install(this, apk)) toast("请允许 Pocket Hub 安装应用，回来再点一次「安装」");
        });
    }

    /** 打开 App 时顺便问一次电脑有没有新版，每次重新打开检查，短时间内合并重复检查；发现新版只提示一回。 */
    private void checkForUpdate() {
        RuntimeClient.Target target = RuntimeClient.selected(this);
        if (target == null) return;
        long now = System.currentTimeMillis();
        if (target.url.equals(updateTarget) && now < nextUpdateCheck) return;
        updateTarget = target.url; nextUpdateCheck = now + 60000L;
        AppUpdate.check(this, target, (newer, error) -> {
            RuntimeClient.Target current = RuntimeClient.selected(this);
            if (isFinishing() || current == null || !current.url.equals(target.url)) return;
            if (error != null) { nextUpdateCheck = System.currentTimeMillis() + 15000L; return; }
            if (newer == null) return;
            android.content.SharedPreferences prefs = getSharedPreferences("ds_ui", MODE_PRIVATE);
            if (!newer.sha256.equals(prefs.getString("update_told", ""))) {
                prefs.edit().putString("update_told", newer.sha256).apply();
                toast("电脑上有新版 App，去「连接与设置」更新");
            }
            mineKey = null; drawerKey = null; onHubChanged();
        });
    }

    private TextView groupHeading(String label) {
        TextView view = text(label, 12, false); view.setTextColor(HINT);
        view.setPadding(dp(4), dp(18), dp(4), dp(8));
        return view;
    }

    private LinearLayout group() {
        LinearLayout box = column(); box.setPadding(dp(16), 0, dp(16), 0);
        box.setBackground(surface(Color.WHITE, Color.WHITE, 18));
        return box;
    }

    private View groupRow(String icon, String title, String subtitle, String value, int valueColor,
                          View.OnClickListener action, boolean divided) {
        LinearLayout row = new LinearLayout(this); row.setGravity(Gravity.CENTER_VERTICAL);
        row.setMinimumHeight(dp(subtitle == null ? 52 : 60));
        if (divided) {
            android.graphics.drawable.LayerDrawable under = new android.graphics.drawable.LayerDrawable(new Drawable[] {surfaceColor(LINE), surfaceColor(Color.WHITE)});
            under.setLayerInset(1, 0, 0, 0, dp(1)); row.setBackground(under);
        }
        if (icon != null) {
            NavIconView image = new NavIconView(this, icon); image.color(INK);
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(dp(22), dp(22));
            params.setMargins(0, 0, dp(12), 0); row.addView(image, params);
        }
        LinearLayout words = column();
        TextView name = text(title, 15, false); name.setTextColor(INK); words.addView(name);
        if (subtitle != null) { TextView sub = text(subtitle, 12, false); sub.setTextColor(HINT); sub.setSingleLine(true);
            sub.setEllipsize(TextUtils.TruncateAt.END); words.addView(sub); }
        row.addView(words, new LinearLayout.LayoutParams(0, -2, 1));
        TextView end = text(value, 13, false); end.setTextColor(valueColor); row.addView(end);
        row.setOnClickListener(action); row.setFocusable(true);
        row.setContentDescription(title + (subtitle == null ? "" : "，" + subtitle) + "，" + value);
        return row;
    }

    private Drawable surfaceColor(int color) { GradientDrawable shape = new GradientDrawable(); shape.setColor(color); return shape; }

    /** 伴侣形象只决定小幽在聊天里长什么样；交给谁做始终由小幽安排。 */
    private void refreshCompanions() {
        if (companionPane == null) return;
        companionPane.removeAllViews();
        TextView intro = text("选一个陪你聊天的形象。它只改变聊天里的样子，事情还是由小幽安排给合适的帮手。", 13, false);
        intro.setPadding(dp(4), dp(8), dp(4), dp(16)); companionPane.addView(intro);
        LinearLayout current = column(); current.setGravity(Gravity.CENTER);
        current.setPadding(dp(16), dp(18), dp(16), dp(18));
        current.setBackground(surface(AppPresentation.tint(companionChoice), AppPresentation.tint(companionChoice), 18));
        current.addView(new AvatarView(this, companionChoice, "happy"), new LinearLayout.LayoutParams(dp(150), dp(150)));
        current.addView(text(AppPresentation.name(companionChoice), 22, true));
        TextView using = text("正在使用", 12, false); using.setTextColor(HINT); current.addView(using);
        companionPane.addView(current);
        companionPane.addView(groupHeading("其他形象"));
        List<String> roles = new ArrayList<>(); roles.add("ds"); roles.add("codex"); roles.add("claude");
        LinearLayout row = null;
        int count = 0;
        for (String role : roles) {
            if (role.equals(companionChoice)) continue;
            if (count % 2 == 0) { row = new LinearLayout(this); companionPane.addView(row); }
            LinearLayout choice = column(); choice.setGravity(Gravity.CENTER);
            choice.setPadding(dp(10), dp(14), dp(10), dp(16));
            choice.setBackground(pressable(AppPresentation.tint(role), 18));
            choice.addView(new AvatarView(this, role, "idle"), new LinearLayout.LayoutParams(dp(104), dp(104)));
            choice.addView(text(AppPresentation.name(role), 17, true));
            choice.setOnClickListener(view -> {
                companionChoice = role;
                getSharedPreferences("ds_ui", MODE_PRIVATE).edit()
                        .putString("companion_" + ArtifactDownload.key(runtimeUrl()), role).apply();
                chatKey = null; drawerKey = null;
                lead.set(role, "happy"); refreshMine(); refreshCompanions(); applyTab();
            });
            choice.setFocusable(true); choice.setContentDescription("换成 " + AppPresentation.name(role));
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(0, -2, 1);
            params.setMargins(count % 2 == 0 ? 0 : dp(6), dp(8), count % 2 == 0 ? dp(6) : 0, dp(6));
            row.addView(choice, params); count++;
        }
        if (count % 2 != 0 && row != null) row.addView(new View(this), new LinearLayout.LayoutParams(0, 1, 1));
    }

    @Override protected void onSaveInstanceState(Bundle state) {
        saveUiContext();
        state.putInt("tab", tab); state.putString("openTask", openTask); state.putString("openAgent", openAgent);
        state.putInt("settingsSection", settingsSection);
        Bundle savedDrafts = new Bundle();
        for (Map.Entry<String, String> entry : drafts.entrySet()) savedDrafts.putString(entry.getKey(), entry.getValue());
        state.putBundle("drafts", savedDrafts);
        super.onSaveInstanceState(state);
    }

    @Override
    protected void onResume() {
        super.onResume();
        HubStore.get().addListener(this);
        refreshSources();
        onHubChanged();
        checkForUpdate();
        uiHandler.removeCallbacks(tick);
        uiHandler.post(tick);
    }

    @Override
    protected void onPause() {
        HubStore.get().removeListener(this);
        uiHandler.removeCallbacks(tick);
        if (approvalDialog != null) approvalDialog.dismiss();
        if (listening || transcribing) {
            // 切走了就不算说完：丢掉这段，不发出去。
            ++voiceSession; listening = false; transcribing = false; heard = "";
            mic.cancel(); plainMic.cancel();
            micButton.setBackground(surface(BLUE, BLUE, 42)); micButton.setScaleX(1); micButton.setScaleY(1);
            micHint.setText("按住说话");
        }
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
        RuntimeClient.Target target = RuntimeClient.selected(this);
        String runtimeUrl = target == null ? "" : target.url;
        if (!lastRuntime.equals(runtimeUrl)) {
            // Task ids may be reused on another computer; never carry the opened target across.
            openTask = null; openAgent = null; taskKey = null; chatKey = null; agentKey = null; drawerKey = null;
            if (tab == TAB_TASKS || tab == TAB_AGENT) tab = TAB_HOME;
            seenDispatched = null; activeDispatched.clear(); doneAt.clear(); doneCard.clear();
            letterFor.clear(); pendingLetter = null; busyKey = null;
            lastRuntime = runtimeUrl;
            companionChoice = getSharedPreferences("ds_ui", MODE_PRIVATE)
                    .getString("companion_" + ArtifactDownload.key(runtimeUrl), "ds");
            refreshCompanions();
        }
        lead.set(companionChoice, !store.runtimeConnected() ? "idle" : !store.approvals().isEmpty() ? "ask" : store.busy() ? "think" : "happy");
        if (!helpersTarget.equals(runtimeUrl)) {
            helpersTarget = runtimeUrl;
            helpers = new ArrayList<>();
            RuntimeClient.fetchAgents(this, result -> runOnUiThread(() -> {
                RuntimeClient.Target current = RuntimeClient.selected(this);
                if (!isFinishing() && current != null && current.url.equals(runtimeUrl)) { helpers = result; drawerKey = null; refreshDrawer(); }
            }));
        }

        if (pendingTask != null && CardViews.find(cards, pendingTask) != null) {
            String next = pendingTask; pendingTask = null; openTask = next; tab = TAB_TASKS;
        }
        if (pendingApproval != null && store.approval(pendingApproval) != null) {
            String next = pendingApproval; pendingApproval = null; showApproval(next);
        }
        if (openTask != null && CardViews.find(cards, openTask) == null) {
            // 打开的那件事没了（换了一台电脑，或者它那边的记录换了）：回到聊天。
            openTask = null;
            if (tab == TAB_TASKS) tab = TAB_HOME;
        }
        // 同步过来的电脑对话可能在打开后才收到其来源；每次更新都重新判断输入权限。
        applyTab();
        boolean atBottom = scroll.getScrollY() + scroll.getHeight() >= body.getHeight() - dp(48);
        refreshAsks(store.approvals());
        boolean grew = refreshChat(cards, store) && tab == TAB_CHAT;
        grew |= refreshTasks(cards) && tab == TAB_TASKS;
        refreshAgent(cards);
        refreshHome(store, cards);
        refreshDevice(store);
        refreshDrawer();
        if (tab == TAB_MINE) refreshMine();
        if (grew && atBottom) {
            scrollDown();
        }

        // 输入框上面那一行：这句话现在怎么样了。
        StringBuilder talk = new StringBuilder();
        HubStore.Turn turn = store.turn();
        nowJump = null;
        String name = companionName();
        if (store.busy()) {
            if (!turn.stage.isEmpty()) {
                talk.append(turn.stage).append("\n");
            }
            talk.append(!turn.agent.isEmpty() ? name + "在等 " + AppPresentation.name(turn.agent) + " 做完…"
                    : store.streaming().isEmpty() ? name + "正在想…" : name + "正在说…");
        } else if (!cards.isEmpty() && turn.phase.equals("failed") && turn.card.isEmpty()) {
            // 没能变成卡的那一句（连不上、没收到声音）：原因写在这里。
            talk.append("没发出去：").append(turn.reply);
        } else if (tab == TAB_CHAT) {
            // 最新的话归到了一件任务上：对话页里看不到内容，给一条过去的路。
            Card filed = CardViews.find(cards, turn.card);
            if (filed != null && AppPresentation.dispatched(filed)) {
                nowJump = filed.id;
                talk.append("查看「").append(filed.title).append("」的原文与附件 ›");
            }
        }
        chatView.setText(talk.toString().trim());
        chatView.setTextColor(nowJump != null ? BLUE : HINT);
        chatView.setVisibility(talk.length() == 0 ? View.GONE : View.VISIBLE);
        updateSendState();
        refreshUsage(store);
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
        if (message.isEmpty() || HubStore.get().busy()) return;
        if (RuntimeClient.selected(this) == null) { toast("先到侧边栏「连接与设置」添加电脑"); return; }
        Card opened = CardViews.find(HubStore.get().cards(), openTask);
        if (tab == TAB_TASKS && opened != null && opened.monitoringOnly && desktopBusy(opened)) {
            toast("电脑上这一轮还在跑，等它停下再发"); return;
        }
        String helper = tab == TAB_AGENT ? agentName(openAgent) : null;
        if (tab == TAB_AGENT && helper == null) return;
        String targetCard = tab == TAB_TASKS ? openTask : null;
        chatInput.setText("");
        drafts.remove(composerContext);
        if (helper != null) {
            // 在帮手页里说的：不经小幽安排，直接交给它，聊天里会出现这件事的回执。
            RuntimeClient.send(this, message, null, null, helper);
            show(TAB_CHAT); scrollDown(); return;
        }
        if (opened != null && opened.monitoringOnly)
            toast("交给 " + AppPresentation.name(AppPresentation.owner(opened)) + " 接着电脑上的会话做");
        // 交给谁、用哪一档都由小幽安排；想指定就直接说出来。
        RuntimeClient.send(this, message, targetCard);
        scrollDown();
    }

    // ---- Places: chat, one task, one helper, and the utility pages under 连接与设置 ----

    private void show(int next) {
        saveUiContext();
        if (next == TAB_TASKS && openTask == null) next = TAB_HOME;
        if (next == TAB_AGENT && openAgent == null) next = TAB_HOME;
        tab = next;
        taskKey = null; agentKey = null; drawerKey = null; mineKey = null;
        if (next == TAB_MINE) refreshMine();
        applyTab(); onHubChanged();
        if (next == TAB_HOME) pokeLine = null;
        restoreScroll(next == TAB_CHAT || next == TAB_TASKS);
    }

    private void openSettings(int section) {
        saveUiContext(); settingsSection = section; show(TAB_SETTINGS);
    }

    /** All task entries lead to the same source-scoped task detail. */
    private void open(String card) {
        saveUiContext();
        openTask = card; tab = TAB_TASKS;
        taskKey = null; drawerKey = null; applyTab(); onHubChanged(); restoreScroll(true);
    }

    private void openAgent(String role) {
        saveUiContext();
        if (!AppPresentation.role(role).equals(openAgent)) agentDesktopOpen = false;
        openAgent = AppPresentation.role(role); tab = TAB_AGENT;
        agentKey = null; drawerKey = null; applyTab(); onHubChanged(); restoreScroll(false);
    }

    /** 电脑上那一轮还在跑（或在等电脑上的确认）：这时从手机插话会和电脑上抢同一个会话。 */
    private static boolean desktopBusy(Card card) {
        return !card.stale && (card.desktopStatus.equals("running") || card.desktopStatus.equals("waiting"));
    }

    /** 这位帮手在 Runtime 上的名字（帮手页按角色显示，发消息要用 Runtime 的代理名）；没有就是 null。 */
    private String agentName(String role) {
        if (role == null) return null;
        for (String name : offeredHelpers()) if (AppPresentation.role(name).equals(role)) return name;
        return null;
    }

    private String runtimeUrl() {
        RuntimeClient.Target target = RuntimeClient.selected(this);
        return target == null ? "" : target.url;
    }

    private String scrollContext() { return runtimeUrl() + "\n" + tab + ":" + openTask + ":" + openAgent + ":" + settingsSection; }

    private void saveUiContext() {
        if (chatInput != null && !composerContext.isEmpty()) drafts.put(composerContext, chatInput.getText().toString());
        if (scroll != null) scrollPositions.put(scrollContext(), scroll.getScrollY());
    }

    private void restoreScroll(boolean latest) {
        Integer y = scrollPositions.get(scrollContext());
        scroll.post(() -> scroll.scrollTo(0, y == null ? (latest ? body.getHeight() : 0) : y));
    }

    private void applyTab() {
        homePane.setVisibility(tab == TAB_HOME ? View.VISIBLE : View.GONE);
        homeBar.setVisibility(tab == TAB_HOME ? View.VISIBLE : View.GONE);
        devicePane.setVisibility(tab == TAB_DEVICE ? View.VISIBLE : View.GONE);
        chatPane.setVisibility(tab == TAB_CHAT ? View.VISIBLE : View.GONE);
        taskPane.setVisibility(tab == TAB_TASKS ? View.VISIBLE : View.GONE);
        agentPane.setVisibility(tab == TAB_AGENT ? View.VISIBLE : View.GONE);
        minePane.setVisibility(tab == TAB_MINE ? View.VISIBLE : View.GONE);
        companionPane.setVisibility(tab == TAB_COMPANIONS ? View.VISIBLE : View.GONE);
        usagePane.setVisibility(tab == TAB_USAGE ? View.VISIBLE : View.GONE);
        settingsPane.setVisibility(tab == TAB_SETTINGS ? View.VISIBLE : View.GONE);
        connectionSettingsPane.setVisibility(settingsSection == 0 ? View.VISIBLE : View.GONE);
        notificationSettingsPane.setVisibility(settingsSection == 1 ? View.VISIBLE : View.GONE);
        preferenceSettingsPane.setVisibility(settingsSection == 2 ? View.VISIBLE : View.GONE);
        boolean inTask = tab == TAB_TASKS && openTask != null;
        Card opened = inTask ? CardViews.find(HubStore.get().cards(), openTask) : null;
        boolean toHelper = tab == TAB_AGENT && agentName(openAgent) != null;
        composer.setVisibility(tab == TAB_CHAT || (inTask && opened != null) || toHelper ? View.VISIBLE : View.GONE);
        String nextContext = AppPresentation.draftKey(runtimeUrl(),
                inTask ? openTask : toHelper ? "agent:" + openAgent : null, companionChoice);
        if (!nextContext.equals(composerContext)) {
            if (!composerContext.isEmpty()) drafts.put(composerContext, chatInput.getText().toString());
            composerContext = nextContext; chatInput.setText(drafts.getOrDefault(nextContext, ""));
        }
        chatInput.setHint(inTask && opened != null && opened.monitoringOnly ? "接着电脑上的会话说…"
                : inTask ? "补充要求…" : toHelper ? "直接交给 " + AppPresentation.name(openAgent) + "…"
                : "跟" + companionName() + "说…");
        updateHeader(); updateSendState();
    }

    private void updateHeader() {
        boolean chat = tab == TAB_CHAT;
        lead.setVisibility(chat ? View.VISIBLE : View.GONE);
        Card opened = tab == TAB_TASKS ? CardViews.find(HubStore.get().cards(), openTask) : null;
        moreButton.setVisibility(opened != null ? View.VISIBLE : View.GONE);
        devicePill.setVisibility(tab == TAB_HOME ? View.VISIBLE : View.GONE);
        String title = tab == TAB_HOME ? "" : tab == TAB_DEVICE ? "随身小幽" : chat ? "对话记录"
                : opened != null ? opened.title
                : tab == TAB_AGENT ? "" : tab == TAB_MINE ? "连接与设置"
                : tab == TAB_COMPANIONS ? "伴侣形象" : tab == TAB_USAGE ? "用量"
                : settingsSection == 1 ? "通知" : settingsSection == 2 ? "偏好与运行记录" : "设备与电脑";
        connectionTitle.setText(title);
    }

    private void updateSendState() {
        if (sendButton == null) return;
        boolean sending = HubStore.get().busy();
        boolean canSend = !chatInput.getText().toString().trim().isEmpty() && !sending;
        sendButton.setEnabled(canSend); sendButton.setAlpha(canSend ? 1f : .45f);
        sendButton.setContentDescription(sending ? "上一条消息正在处理，请稍候" : "发送消息");
    }

    private void scrollDown() {
        scroll.post(() -> scroll.scrollTo(0, body.getHeight()));
    }

    /** 来源和连接状态始终可见；断线时保留已收的数，并明确说明它是缓存。 */
    private void refreshUsage(HubStore store) {
        usageUiAtMs = System.currentTimeMillis();
        RuntimeClient.Target target = RuntimeClient.selected(this);
        Usage usage = store.usage();
        if (usageRefreshingUrl != null && (target == null || !usageRefreshingUrl.equals(target.url)
                || usageRefreshingEpoch != store.cardsEpoch())) {
            usageRefreshingUrl = null;
            ++usageRefreshAttempt;
            usageRefreshStatus.setText("");
        }
        usageSource.setText(target == null ? "还没有连接电脑"
                : "来源：" + target.name + "\n" + (store.runtimeConnected() ? "电脑在线"
                : usage == null ? "电脑离线 · 等待连接" : "电脑离线 · 显示上次收到的缓存，额度可能已变化"));
        if (target == null) {
            usageView.setText("到「连接与设置」添加电脑，连接后即可查看用量。\n5 小时额度：未知\n本周额度：未知");
        } else if (usage == null || usage.empty()) {
            usageView.setText("这台电脑还没有报告用量。连接正常后可点刷新；旧版 Runtime 可能不支持。"
                    + "\n5 小时额度：未知\n本周额度：未知\n模型和档位：等待电脑报告");
        } else {
            StringBuilder said = new StringBuilder();
            for (String part : usage.describe(System.currentTimeMillis() / 1000L,
                    store.usageAgeSeconds(), java.util.TimeZone.getDefault())) {
                said.append(said.length() == 0 ? "" : "\n\n").append(part);
            }
            usageView.setText(said.toString());
        }
        usageRefresh.setEnabled(target != null && usageRefreshingUrl == null);
    }

    private void fetchUsage() {
        RuntimeClient.Target target = RuntimeClient.selected(this);
        if (target == null) return;
        long epoch = HubStore.get().cardsEpoch();
        long attempt = ++usageRefreshAttempt;
        usageRefreshingUrl = target.url;
        usageRefreshingEpoch = epoch;
        usageRefresh.setEnabled(false);
        usageRefreshStatus.setText("正在刷新 " + target.name + " 的用量…");
        RuntimeClient.fetchUsage(this, (problem, applied) -> runOnUiThread(() -> {
            if (isFinishing() || attempt != usageRefreshAttempt) return;
            usageRefreshingUrl = null;
            RuntimeClient.Target current = RuntimeClient.selected(this);
            if (current == null || !current.url.equals(target.url) || HubStore.get().cardsEpoch() != epoch) {
                usageRefreshStatus.setText("");
            } else {
                usageRefreshStatus.setText(problem == null && applied ? "已刷新" : problem == null ? "已切换电脑，忽略旧来源的结果" : "刷新失败：" + problem);
            }
            refreshUsage(HubStore.get());
        }));
    }

    @Override
    public void onBackPressed() {
        if (drawerOpen) {
            setDrawer(false);
        } else if (tab == TAB_COMPANIONS || tab == TAB_USAGE || tab == TAB_SETTINGS) {
            show(TAB_MINE);
        } else if (tab != TAB_HOME) {
            show(TAB_HOME);
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

    /** 等你点头的操作：顶栏下面一条，有才出现；谁要做什么写在里面。 */
    private void refreshAsks(List<Card.Approval> approvals) {
        approvalBar.setVisibility(approvals.isEmpty() || tab == TAB_SETTINGS || tab == TAB_USAGE
                || tab == TAB_COMPANIONS ? View.GONE : View.VISIBLE);
        if (approvals.isEmpty()) return;
        Card.Approval first = approvals.get(0);
        String who = AppPresentation.name(first.agent.isEmpty() ? "assistant" : first.agent);
        approvalText.setText(approvals.size() == 1 ? who + " 等你确认：" + first.tool
                : approvals.size() + " 件事等你确认");
        approvalBar.setContentDescription(approvalText.getText() + "，去确认");
    }

    private void showApprovals() {
        List<Card.Approval> pending = HubStore.get().approvals();
        if (pending.isEmpty()) { toast("目前没有待确认的操作"); return; }
        if (pending.size() == 1) { showApproval(pending.get(0).id); return; }
        String[] labels = new String[pending.size()];
        for (int i = 0; i < labels.length; ++i) labels[i] = pending.get(i).runtimeName + " · " + pending.get(i).tool;
        new AlertDialog.Builder(this).setTitle("待确认的操作")
                .setItems(labels, (dialog, index) -> showApproval(pending.get(index).id)).setNegativeButton("返回", null).show();
    }

    /**
     * Main conversation messages stay in chronological order, including hand-offs and summaries.
     * Only the visible window is built; streaming updates its existing text views.
     */
    private boolean refreshChat(List<Card> all, HubStore store) {
        List<ChatHistory.Message> messages = store.mainMessages();
        int first = Math.max(0, messages.size() - chatWindow);
        StringBuilder layout = new StringBuilder(companionChoice).append(first);
        for (int index = first; index < messages.size(); index++) {
            ChatHistory.Message message = messages.get(index);
            Card card = CardViews.find(all, message.card);
            layout.append(message.id).append(message.status.equals("failed")).append(hasBubble(message)).append('|');
            if (card != null && AppPresentation.dispatched(card))
                layout.append(card.title).append(AppPresentation.stateWord(card, store.runtimeConnected())).append(card.resultReady);
        }
        boolean rebuilt = !layout.toString().equals(chatKey);
        if (rebuilt) {
            chatKey = layout.toString(); chatList.removeAllViews(); chatWords.clear(); chatStatus.clear();
            if (first > 0) chatList.addView(button("查看更早的对话", view -> {
                int y = scroll.getScrollY(), height = body.getHeight();
                chatWindow += 80; chatKey = null; refreshChat(HubStore.get().cards(), HubStore.get());
                scroll.post(() -> scroll.scrollTo(0, y + body.getHeight() - height));
            }));
            if (messages.isEmpty()) chatList.addView(emptyState("陪你聊，也帮你安排", "说说你今天的想法，或交给小幽一件事。"));
            for (int index = first; index < messages.size(); index++) {
                ChatHistory.Message message = messages.get(index);
                boolean mine = message.role.equals("you");
                if (hasBubble(message)) {
                    LinearLayout bubble = bubbleShell(mine);
                    TextView words = bubbleWords(mine, ""); words.setTextIsSelectable(true);
                    bubble.addView(words); chatWords.put(message.id, words);
                    chatList.addView(bubble, bubbleParams(mine));
                }
                TextView caption = statusCaption(mine);
                chatList.addView(caption, captionParams(mine)); chatStatus.put(message.id, caption);
                Card card = CardViews.find(all, message.card);
                if (!mine && card != null && AppPresentation.dispatched(card)) chatList.addView(receipt(card));
                if (message.status.equals("failed") && mine && !message.synced) {
                    chatList.addView(button("重试发送", view -> RuntimeClient.retry(this, message.id, message.text,
                            message.scope.equals("task") ? message.card : null)));
                }
            }
        }
        boolean changed = rebuilt;
        for (int index = first; index < messages.size(); index++) {
            ChatHistory.Message message = messages.get(index);
            TextView words = chatWords.get(message.id);
            String shown = messageText(message);
            if (words != null && !shown.contentEquals(words.getText())) { words.setText(shown); changed = true; }
            changed |= showStatus(chatStatus.get(message.id), statusText(message));
        }
        return changed;
    }

    /** 气泡里只放说的话；发送中、正在想这类状态放在气泡下面的小字里（statusText）。 */
    private String messageText(ChatHistory.Message message) {
        if (message.role.equals("you")) return message.text;
        if (message.status.equals("streaming")) return message.text + " ▍";
        if (message.status.equals("interrupted") && message.text.isEmpty()) return "回复尚未完成";
        return message.text;
    }

    /** 还在想、一个字都没有的回复不画空气泡，只留下面那行“正在想…”。 */
    private static boolean hasBubble(ChatHistory.Message message) {
        return message.role.equals("you") || !(message.status.equals("thinking") && message.text.isEmpty());
    }

    private String statusText(ChatHistory.Message message) {
        switch (message.status) {
            case "sending": return message.role.equals("you") ? "发送中…" : "";
            case "failed": return message.role.equals("you") ? "发送未成功" : "";
            case "thinking": return companionName() + " 正在想…";
            case "interrupted": return "连接中断，请重新发送或查看任务";
            default: return "";
        }
    }

    private TextView statusCaption(boolean mine) {
        TextView caption = text("", 11, false); caption.setTextColor(HINT);
        caption.setGravity(mine ? Gravity.END : Gravity.START); caption.setVisibility(View.GONE);
        return caption;
    }

    private LinearLayout.LayoutParams captionParams(boolean mine) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-2, -2);
        params.gravity = mine ? Gravity.END : Gravity.START;
        params.setMargins(mine ? dp(48) : dp(6), 0, mine ? dp(6) : dp(40), dp(2));
        return params;
    }

    /** 换了才动；返回是否变了。 */
    private static boolean showStatus(TextView caption, String status) {
        if (caption == null || status.contentEquals(caption.getText())) return false;
        caption.setText(status); caption.setVisibility(status.isEmpty() ? View.GONE : View.VISIBLE);
        return true;
    }

    /** 聊天里的一行回执：谁在做哪件事、现在到哪了。点了去这件事。 */
    private View receipt(Card card) {
        boolean connected = HubStore.get().runtimeConnected();
        String owner = AppPresentation.owner(card);
        LinearLayout row = new LinearLayout(this); row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(dp(12), dp(10), dp(12), dp(10));
        row.setBackground(pressable(Color.WHITE, 14, 0xffe6ebf2));
        row.addView(avatar(owner, DsState.pose(card, connected), 30));
        LinearLayout words = column(); words.setPadding(dp(10), 0, dp(8), 0);
        TextView title = text(card.title, 14, true); title.setMaxLines(2); title.setEllipsize(TextUtils.TruncateAt.END);
        words.addView(title);
        String word = AppPresentation.stateWord(card, connected);
        TextView state = word.isEmpty() ? text(AppPresentation.name(owner) + " · " + AppPresentation.stateLabel(card, connected), 12, false)
                : stateText(AppPresentation.name(owner) + " · " + word, 12);
        if (word.isEmpty()) state.setTextColor(HINT);
        words.addView(state);
        row.addView(words, new LinearLayout.LayoutParams(0, -2, 1));
        TextView more = text("›", 18, false); more.setTextColor(0xff9aa7b8); row.addView(more);
        row.setOnClickListener(view -> open(card.id)); row.setFocusable(true);
        row.setContentDescription(AppPresentation.name(owner) + "，" + card.title + "，" + (word.isEmpty() ? AppPresentation.stateLabel(card, connected) : word));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
        params.setMargins(0, dp(4), dp(40), dp(10)); row.setLayoutParams(params);
        return row;
    }

    /** 任务页：上面是这件事经过的几步，下面是这件事里的对话。重建了返回 true。 */
    private boolean refreshTasks(List<Card> cards) {
        Card opened = CardViews.find(cards, openTask);
        boolean connected = HubStore.get().runtimeConnected();
        StringBuilder key = new StringBuilder(opened == null ? "" : opened.id).append('>').append(connected);
        if (opened != null) key.append('#').append(opened.seq).append(':').append(opened.state).append(opened.queued);
        for (ChatHistory.Message message : HubStore.get().pendingMessages(openTask))
            key.append(message.id).append(message.status).append(message.text);
        if (key.toString().equals(taskKey)) return false;
        taskKey = key.toString(); taskPane.removeAllViews();
        if (opened == null) return true;
        String owner = AppPresentation.owner(opened);
        String state = DsState.state(opened, connected);
        String word = AppPresentation.stateWord(opened, connected);
        LinearLayout steps = column(); steps.setPadding(dp(16), dp(4), dp(16), dp(4));
        steps.setBackground(surface(Color.WHITE, Color.WHITE, 18));
        String latest = opened.progress.isEmpty() ? "" : opened.progress.get(opened.progress.size() - 1);
        if (opened.monitoringOnly) {
            steps.addView(step(owner, AppPresentation.name(owner) + " · 电脑上的会话", latest,
                    word.isEmpty() ? AppPresentation.stateLabel(opened, connected) : word, !word.isEmpty(), false,
                    view -> openAgent(owner)));
        } else {
            boolean ended = !opened.outstanding();
            steps.addView(step("ds", "小幽 拆分需求", null, "✓", false, true, null));
            steps.addView(step(owner, AppPresentation.name(owner) + " 处理", ended ? opened.brief : latest,
                    word.isEmpty() ? AppPresentation.stateLabel(opened, connected) : word, !word.isEmpty(), true,
                    view -> openAgent(owner)));
            steps.addView(step("ds", "小幽 告诉你结果", null, state.equals("done") ? "✓" : "", false, false, null));
        }
        taskPane.addView(steps);
        if (opened.monitoringOnly) {
            TextView notice = text(desktopBusy(opened)
                    ? "电脑上这一轮还在跑。等它停下，就能在这里接着说，" + AppPresentation.name(owner) + " 会接着这个会话做。"
                    : "在这里说的话，" + AppPresentation.name(owner) + " 会接着这个会话做，回复也会出现在电脑上。", 12, false);
            notice.setTextColor(HINT); notice.setPadding(dp(4), dp(12), dp(4), 0); taskPane.addView(notice);
            if (!CardViews.titleNotice(opened).isEmpty()) taskPane.addView(text(CardViews.titleNotice(opened), 12, false));
            taskPane.addView(text(CardViews.contentNotice(opened), 12, false));
        }
        View gap = new View(this); taskPane.addView(gap, new LinearLayout.LayoutParams(-1, dp(12)));
        addThread(taskPane, opened);
        for (ChatHistory.Message message : HubStore.get().pendingMessages(openTask)) {
            boolean mine = message.role.equals("you");
            if (hasBubble(message)) {
                LinearLayout bubble = bubbleShell(mine); bubble.addView(bubbleWords(mine, messageText(message)));
                taskPane.addView(bubble, bubbleParams(mine));
            }
            TextView caption = statusCaption(mine); showStatus(caption, statusText(message));
            taskPane.addView(caption, captionParams(mine));
            if (mine && message.status.equals("failed")) taskPane.addView(button("重试发送",
                    view -> RuntimeClient.retry(this, message.id, message.text, message.card)));
        }
        return true;
    }

    /** 任务里的一步：谁、做什么、最近一步进展，右边一个状态。点了去这位帮手。 */
    private View step(String role, String title, String detail, String state, boolean live, boolean divided,
                      View.OnClickListener action) {
        LinearLayout row = new LinearLayout(this); row.setGravity(Gravity.CENTER_VERTICAL);
        row.setMinimumHeight(dp(56)); row.setPadding(0, dp(10), 0, dp(10));
        if (divided) {
            android.graphics.drawable.LayerDrawable under = new android.graphics.drawable.LayerDrawable(new Drawable[] {surfaceColor(LINE), surfaceColor(Color.WHITE)});
            under.setLayerInset(1, 0, 0, 0, dp(1)); row.setBackground(under);
        }
        AvatarView portrait = avatar(role, live ? "focus" : "idle", 30);
        boolean pending = state.isEmpty();
        portrait.setAlpha(pending ? .55f : 1f);
        row.addView(portrait);
        LinearLayout words = column(); words.setPadding(dp(12), 0, dp(8), 0);
        TextView name = text(title, 15, live); name.setTextColor(live ? INK : MUTED); words.addView(name);
        if (detail != null && !detail.isEmpty()) {
            TextView sub = text(CardViews.oneLine(detail, 60), 12, false); sub.setTextColor(HINT); words.addView(sub);
        }
        row.addView(words, new LinearLayout.LayoutParams(0, -2, 1));
        if (state.equals("✓")) {
            TextView done = text("✓", 16, true); done.setTextColor(0xff2f9a5b); done.setContentDescription("完成"); row.addView(done);
        } else if (!pending) {
            TextView end = live ? stateText(state, 13) : text(state, 13, false);
            if (!live) end.setTextColor(HINT);
            row.addView(end);
        }
        if (action != null) { row.setOnClickListener(action); row.setFocusable(true); }
        return row;
    }

    /** 帮手页：小幽派给它的事（各自能回到所属任务），以及电脑上开的会话。 */
    private void refreshAgent(List<Card> cards) {
        if (openAgent == null) { if (agentPane.getChildCount() > 0) { agentPane.removeAllViews(); agentKey = null; } return; }
        boolean connected = HubStore.get().runtimeConnected();
        AppPresentation.Helper helper = null;
        for (AppPresentation.Helper item : AppPresentation.helpers(cards, offeredHelpers()))
            if (item.role.equals(openAgent)) helper = item;
        if (helper == null) helper = new AppPresentation.Helper(openAgent);
        String energy = energyText(openAgent);
        StringBuilder key = new StringBuilder(openAgent).append(connected).append(agentDesktopOpen).append(energy);
        for (Card card : helper.dispatched) key.append(card.id).append(card.state).append(card.queued).append(card.seq).append('|');
        for (Card card : helper.desktop) key.append(card.id).append(card.state).append('|');
        if (key.toString().equals(agentKey)) return;
        agentKey = key.toString(); agentPane.removeAllViews();
        Card now = helper.current(connected);
        LinearLayout hero = column(); hero.setGravity(Gravity.CENTER_HORIZONTAL); hero.setPadding(0, dp(4), 0, dp(20));
        AvatarView portrait = avatar(helper.role, now != null ? "focus" : "idle", 112);
        hero.addView(portrait);
        TextView name = text(AppPresentation.name(helper.role), 22, true); name.setPadding(0, dp(10), 0, dp(2)); hero.addView(name);
        TextView line = now != null ? stateText(DsState.state(now, connected).equals("waiting")
                ? "等你确认「" + now.title + "」" : "正在做「" + now.title + "」", 13) : text(helper.status(connected), 13, false);
        line.setGravity(Gravity.CENTER); hero.addView(line);
        TextView energyView = text(energy, 13, false); energyView.setGravity(Gravity.CENTER);
        energyView.setPadding(0, dp(10), 0, 0); hero.addView(energyView);
        agentPane.addView(hero);
        agentPane.addView(groupHeading("小幽派给它的"));
        if (helper.dispatched.isEmpty()) {
            TextView none = text("还没有。在聊天里说「让" + AppPresentation.name(helper.role) + "做……」，小幽会安排。", 13, false);
            none.setTextColor(HINT); none.setPadding(dp(4), 0, dp(4), 0); agentPane.addView(none);
        } else {
            LinearLayout list = group();
            for (int index = 0; index < helper.dispatched.size(); index++) {
                Card card = helper.dispatched.get(index);
                String word = AppPresentation.stateWord(card, connected);
                String detail = card.outstanding() && !card.progress.isEmpty()
                        ? card.progress.get(card.progress.size() - 1) : AppPresentation.stateLabel(card, connected);
                list.addView(groupRow(null, card.title, CardViews.oneLine(detail, 40), word.isEmpty() ? "›" : word,
                        word.isEmpty() ? HINT : word.contains("确认") ? AMBER : BLUE, view -> open(card.id),
                        index < helper.dispatched.size() - 1));
            }
            agentPane.addView(list);
        }
        if (!helper.desktop.isEmpty()) {
            View gap = new View(this); agentPane.addView(gap, new LinearLayout.LayoutParams(-1, dp(12)));
            LinearLayout desktop = group();
            desktop.addView(groupRow("computer", "电脑上的会话", "打开一个会话，就能从手机接着聊",
                    helper.desktop.size() + (agentDesktopOpen ? " ⌃" : " ›"), HINT,
                    view -> { agentDesktopOpen = !agentDesktopOpen; agentKey = null; refreshAgent(HubStore.get().cards()); },
                    agentDesktopOpen));
            if (agentDesktopOpen) {
                for (int index = 0; index < helper.desktop.size(); index++) {
                    Card card = helper.desktop.get(index);
                    String word = AppPresentation.stateWord(card, connected);
                    desktop.addView(groupRow(null, card.title, null, word.isEmpty() ? AppPresentation.stateLabel(card, connected) : word,
                            word.isEmpty() ? HINT : BLUE, view -> open(card.id), index < helper.desktop.size() - 1));
                }
            }
            agentPane.addView(desktop);
        }
    }

    // ---- 首页：小幽本人 ----

    private View devicePill() {
        LinearLayout pill = new LinearLayout(this); pill.setGravity(Gravity.CENTER_VERTICAL);
        pill.setPadding(dp(12), 0, dp(12), 0);
        pill.setBackground(pressable(Color.WHITE, 17));
        View dot = new View(this); dot.setTag("dot");
        pill.addView(dot, new LinearLayout.LayoutParams(dp(7), dp(7)));
        TextView label = text("", 12, false); label.setTag("label"); label.setTextColor(0xff33465e);
        label.setPadding(dp(6), 0, 0, 0); pill.addView(label);
        pill.setOnClickListener(view -> show(TAB_DEVICE)); pill.setFocusable(true);
        return pill;
    }

    private LinearLayout buildHome() {
        LinearLayout pane = column(); pane.setGravity(Gravity.CENTER_HORIZONTAL);
        stage = new CompanionStage(this, 210);
        stage.setOnClickListener(view -> poke()); stage.setFocusable(true);
        stage.setContentDescription("摸摸" + companionName());
        LinearLayout.LayoutParams stageParams = new LinearLayout.LayoutParams(-2, -2);
        stageParams.setMargins(0, dp(8), 0, dp(4));
        pane.addView(stage, stageParams);
        homeSpeech = text("", 16, false); homeSpeech.setTextColor(INK);
        homeSpeech.setGravity(Gravity.CENTER); homeSpeech.setLineSpacing(dp(4), 1f);
        homeSpeech.setPadding(dp(18), dp(14), dp(18), dp(14));
        homeSpeech.setBackground(surface(Color.WHITE, Color.WHITE, 20));
        homeSpeech.setMaxLines(8); homeSpeech.setEllipsize(TextUtils.TruncateAt.END);
        homeSpeech.setOnClickListener(view -> show(TAB_CHAT));
        LinearLayout.LayoutParams speechParams = new LinearLayout.LayoutParams(-2, -2);
        speechParams.setMargins(dp(16), dp(8), dp(16), dp(14));
        pane.addView(homeSpeech, speechParams);
        // 帮手多了也排成一行，左右滑着看；少的时候居中。
        busyScroll = new HorizontalScrollView(this);
        busyScroll.setHorizontalScrollBarEnabled(false); busyScroll.setFillViewport(true);
        busyScroll.setClipToPadding(false); busyScroll.setPadding(dp(11), dp(6), dp(11), dp(6));
        busyRow = new LinearLayout(this); busyRow.setGravity(Gravity.CENTER);
        busyScroll.addView(busyRow, new FrameLayout.LayoutParams(-2, -2));
        pane.addView(busyScroll, new LinearLayout.LayoutParams(-1, -2));
        homeActions = new LinearLayout(this); homeActions.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams actionParams = new LinearLayout.LayoutParams(-1, -2);
        actionParams.setMargins(0, dp(24), 0, dp(8));
        pane.addView(homeActions, actionParams);
        return pane;
    }

    private LinearLayout buildHomeBar() {
        LinearLayout bar = new LinearLayout(this); bar.setGravity(Gravity.BOTTOM);
        bar.setPadding(dp(28), dp(6), dp(28), dp(22)); bar.setBackgroundColor(BACKGROUND);
        View keyboard = roundIcon("keyboard", "打字", view -> { show(TAB_CHAT); chatInput.requestFocus();
            InputMethodManager keys = (InputMethodManager) getSystemService(INPUT_METHOD_SERVICE);
            if (keys != null) keys.showSoftInput(chatInput, InputMethodManager.SHOW_IMPLICIT); });
        LinearLayout.LayoutParams side = new LinearLayout.LayoutParams(dp(48), dp(48)); side.setMargins(0, 0, 0, dp(24));
        bar.addView(keyboard, side);
        LinearLayout middle = column(); middle.setGravity(Gravity.CENTER_HORIZONTAL);
        LinearLayout button = column(); button.setGravity(Gravity.CENTER);
        button.setBackground(surface(BLUE, BLUE, 42));
        NavIconView icon = new NavIconView(this, "mic"); icon.color(Color.WHITE);
        button.addView(icon, new LinearLayout.LayoutParams(dp(32), dp(32)));
        button.setContentDescription("按住说话"); button.setFocusable(true); button.setClickable(true);
        button.setOnTouchListener((view, event) -> {
            switch (event.getActionMasked()) {
                case android.view.MotionEvent.ACTION_DOWN:
                    view.getParent().requestDisallowInterceptTouchEvent(true);
                    startListening(); return true;
                case android.view.MotionEvent.ACTION_UP:
                    view.getParent().requestDisallowInterceptTouchEvent(false);
                    stopListening(); return true;
                case android.view.MotionEvent.ACTION_CANCEL:
                    view.getParent().requestDisallowInterceptTouchEvent(false);
                    cancelListening(); return true;
                default: return true;
            }
        });
        micButton = button;
        middle.addView(button, new LinearLayout.LayoutParams(dp(84), dp(84)));
        micHint = text("按住说话", 12, false); micHint.setPadding(0, dp(8), 0, 0);
        micHint.setMaxWidth(dp(260)); micHint.setMaxLines(3); micHint.setGravity(Gravity.CENTER);
        middle.addView(micHint, new LinearLayout.LayoutParams(-2, -2));
        bar.addView(middle, new LinearLayout.LayoutParams(0, -2, 1));
        bar.addView(roundIcon("history", "对话记录", view -> show(TAB_CHAT)), side);
        return bar;
    }

    private View roundIcon(String icon, String label, View.OnClickListener action) {
        LinearLayout box = column(); box.setGravity(Gravity.CENTER);
        box.setBackground(pressable(Color.WHITE, 24)); box.setContentDescription(label);
        box.setFocusable(true); box.setOnClickListener(action);
        NavIconView view = new NavIconView(this, icon); view.color(INK);
        box.addView(view, new LinearLayout.LayoutParams(dp(22), dp(22)));
        return box;
    }

    /** Recognize on the phone; only released, final text goes to Runtime. */
    private void startListening() {
        if (RuntimeClient.selected(this) == null) { toast("先连上电脑，小幽才能回答"); show(TAB_MINE); return; }
        if (listening || mic.recording() || plainMic.recording()) return;
        if (HubStore.get().busy() || transcribing) { toast(companionName() + "还在回上一句"); return; }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            micHint.setText("请先允许麦克风权限");
            requestPermissions(new String[] {Manifest.permission.RECORD_AUDIO}, 4); return;
        }
        android.media.AudioManager audio = (android.media.AudioManager) getSystemService(AUDIO_SERVICE);
        if (audio != null && audio.isMicrophoneMute()) {
            micHint.setText("系统麦克风已关闭"); toast("请开启系统麦克风开关后，再按住说话"); return;
        }
        final long session = ++voiceSession;
        heard = ""; listening = true; pokeLine = null;
        micButton.setBackground(surface(0xffe2564a, 0xffe2564a, 42));
        micButton.animate().scaleX(1.12f).scaleY(1.12f).setDuration(150).start();
        micHint.setText("正在打开手机麦克风…");
        if (!AppPacks.ready(this, AppPacks.SPEECH, AppPacks.SPEECH_FILES)) { recordForComputer(session); return; }
        final byte[][] recorded = new byte[1][];
        mic.start(this, new OnDeviceSpeech.Callback() {
            @Override public void recording(byte[] wav) { if (session == voiceSession) recorded[0] = wav; }
            @Override public void ready() {
                if (session != voiceSession || !listening) return;
                micHint.setText("手机正在录音 · 松开发送文字");
                refreshHome(HubStore.get(), HubStore.get().cards());
            }
            @Override public void level(float value) { if (session == voiceSession) micLevel(value); }
            @Override public void partial(String text) {
                if (session != voiceSession) return;
                heard = text; refreshHome(HubStore.get(), HubStore.get().cards());
            }
            @Override public void ended() {
                if (session == voiceSession && listening) micHint.setText("已录满 30 秒 · 松开发送文字");
            }
            @Override public void finished(String text, String error) {
                if (session != voiceSession || isFinishing() || isDestroyed()) return;
                releaseMic(); transcribing = false;
                byte[] wav = recorded[0]; recorded[0] = null;
                boolean online = HubStore.get().runtimeConnected();
                if (wav != null && online && (voiceOnComputer() || error != null)) {
                    // 开了“交给电脑识别”，或者手机没认出来：把这段录音交给电脑，用和设备录音同一个识别。
                    HubStore.get().log(error == null ? "手机录音交给电脑识别" : "手机识别没成功（" + error + "），交给电脑识别");
                    RuntimeClient.sendVoice(MainActivity.this, wav, null, false);
                } else if (error != null) {
                    micHint.setText(error); HubStore.get().log(error); toast(error);
                } else if (text != null && !text.isEmpty()) {
                    HubStore.get().log("手机已完成离线转录，仅发送文字");
                    RuntimeClient.send(MainActivity.this, text);
                }
                refreshHome(HubStore.get(), HubStore.get().cards());
            }
        });
        refreshHome(HubStore.get(), HubStore.get().cards());
    }

    /** 手机上还没有语音模型：只录音，松开后把录音交给电脑识别（和设备录音同一个识别）。 */
    private void recordForComputer(long session) {
        plainMic.start(new VoiceCapture.Callback() {
            @Override public void ready() { runOnUiThread(() -> {
                if (session == voiceSession && listening) micHint.setText("手机正在录音 · 松开交给电脑识别");
            }); }
            @Override public void level(float value) { runOnUiThread(() -> { if (session == voiceSession) micLevel(value); }); }
            @Override public void samples(byte[] bytes, int count) { }
            @Override public void limitReached() { runOnUiThread(() -> {
                if (session == voiceSession && listening) micHint.setText("已录满 30 秒 · 松开交给电脑识别");
            }); }
            @Override public void finished(byte[] wav, String error, boolean cancelled) { runOnUiThread(() -> {
                if (session != voiceSession || isFinishing() || isDestroyed() || cancelled) return;
                releaseMic(); transcribing = false;
                if (error != null) { micHint.setText(error); toast(error); }
                else if (wav != null && HubStore.get().runtimeConnected()) {
                    HubStore.get().log("手机语音模型还没下载，录音交给电脑识别");
                    RuntimeClient.sendVoice(MainActivity.this, wav, null, false);
                } else {
                    String why = "电脑没连上，手机语音模型也还没下载：去「连接与设置 → App 更新」下载后可离线识别";
                    micHint.setText(why); toast(why);
                }
                refreshHome(HubStore.get(), HubStore.get().cards());
            }); }
        });
        refreshHome(HubStore.get(), HubStore.get().cards());
    }

    private boolean voiceOnComputer() {
        return getSharedPreferences("ds_ui", MODE_PRIVATE).getBoolean("voice_on_computer", false);
    }

    private void cancelListening() {
        ++voiceSession; mic.cancel(); plainMic.cancel(); releaseMic(); transcribing = false; heard = "";
        refreshHome(HubStore.get(), HubStore.get().cards());
    }

    private void releaseMic() {
        listening = false;
        micButton.setBackground(surface(BLUE, BLUE, 42));
        micButton.animate().scaleX(1f).scaleY(1f).setDuration(150).start();
        micHint.setText("按住说话");
    }

    private void micLevel(float level) {
        if (!listening) return;
        float scale = 1.12f + .08f * level; micButton.setScaleX(scale); micButton.setScaleY(scale);
    }

    private void stopListening() {
        if (!listening) return;
        releaseMic(); transcribing = true;
        boolean computer = plainMic.recording() || voiceOnComputer() && HubStore.get().runtimeConnected();
        micHint.setText(computer ? "正在交给电脑识别…" : "正在手机上转成文字…");
        mic.stop(); plainMic.stop();
        refreshHome(HubStore.get(), HubStore.get().cards());
    }

    /** 摸她一下：蹦一下，回一句。只在这台手机上，不发给电脑。 */
    private void poke() {
        stage.hop();
        HubStore store = HubStore.get();
        if (listening || store.busy() || !store.streaming().isEmpty()) return;
        String[] lines = {"嘿嘿，在呢～", "别戳啦，痒～", "今天也一起加油！", "有事就按住麦克风跟我说"};
        pokeLine = lines[(int) (System.currentTimeMillis() / 1000 % lines.length)];
        refreshHome(store, store.cards());
    }

    private String greeting() {
        int hour = java.util.Calendar.getInstance().get(java.util.Calendar.HOUR_OF_DAY);
        return hour < 5 ? "这么晚还没睡呀" : hour < 11 ? "早呀～" : hour < 14 ? "中午好～" : hour < 18 ? "下午好～" : "晚上好～";
    }

    /**
     * 首页跟着真实状态变：听、想、说、等帮手、刚做完各有自己的样子。派活时一封信飞到帮手那里，
     * 帮手做完它的胶囊变绿、小幽蹦一下。
     */
    private String energyText(String role) {
        HubStore store = HubStore.get();
        Usage usage = store.usage();
        String value = usage == null ? "精力值：未知" : usage.energy(role,
                System.currentTimeMillis() / 1000L, store.usageAgeSeconds(), java.util.TimeZone.getDefault());
        return value + (!store.runtimeConnected() && usage != null ? " · 离线缓存" : "");
    }

    private String latestMainReply(HubStore store) {
        List<ChatHistory.Message> messages = store.mainMessages();
        for (int index = messages.size() - 1; index >= 0; index--) {
            ChatHistory.Message message = messages.get(index);
            if (!message.role.equals("you") && !message.text.isEmpty()) return message.text;
        }
        return "";
    }

    private void refreshHome(HubStore store, List<Card> cards) {
        if (homePane == null) return;
        boolean device = store.linkState().equals("已连接");
        ((View) devicePill.findViewWithTag("dot")).setBackground(surface(device ? 0xff2f9a5b : 0xffc3ccd8, device ? 0xff2f9a5b : 0xffc3ccd8, 4));
        ((TextView) devicePill.findViewWithTag("label")).setText(device ? "随身小幽在身边" : "随身小幽没连上");
        boolean connected = store.runtimeConnected();
        RuntimeClient.Target target = RuntimeClient.selected(this);
        List<AppPresentation.Helper> roster = AppPresentation.helpers(cards, offeredHelpers());

        // 新交出去的事飞信；做完的事让帮手变绿。第一次认卡时只记下，不飞。
        Set<String> nowActive = new HashSet<>();
        String flyTo = null;
        for (Card card : cards) {
            if (!AppPresentation.dispatched(card)) continue;
            if (card.outstanding()) nowActive.add(card.id);
            if (seenDispatched != null && !seenDispatched.contains(card.id) && card.outstanding()) flyTo = AppPresentation.owner(card);
            if (activeDispatched.contains(card.id) && !card.outstanding() && card.state.equals("done")) {
                String role = AppPresentation.owner(card);
                doneAt.put(role, System.currentTimeMillis()); doneCard.put(role, card.id);
                letterFor.remove(role); if (role.equals(pendingLetter)) pendingLetter = null;
                if (tab == TAB_HOME) stage.hop();
                uiHandler.postDelayed(() -> refreshHome(HubStore.get(), HubStore.get().cards()), 8200);
            }
        }
        if (seenDispatched == null && store.cardsEpoch() > 0 && !cards.isEmpty()) seenDispatched = new HashSet<>();
        if (seenDispatched != null) for (Card card : cards) if (AppPresentation.dispatched(card)) seenDispatched.add(card.id);
        activeDispatched.clear(); activeDispatched.addAll(nowActive);

        String streaming = store.streaming();
        HubStore.Turn turn = store.turn();
        String name = companionName();
        String state, mood, line;
        if (target == null) {
            state = "idle"; mood = "happy";
            line = "你好呀，我是" + name + "。\n先让我住进你的电脑，\n我就能帮你安排事情啦。";
        } else if (listening) {
            state = "listening"; mood = "happy"; line = heard.isEmpty() ? "我在听…" : heard;
        } else if (transcribing) {
            state = "thinking"; mood = "think"; line = heard.isEmpty() ? "嗯…" : heard;
        } else if (!streaming.isEmpty()) {
            state = "talking"; mood = "happy"; line = streaming + " ▍";
        } else if (store.busy()) {
            boolean waiting = !turn.agent.isEmpty();
            state = waiting ? "working" : "thinking"; mood = waiting ? "focus" : "think";
            line = !turn.stage.isEmpty() ? turn.stage : "正在回应…";
        } else if (pokeLine != null) {
            state = "happy"; mood = "happy"; line = pokeLine;
        } else if (!latestMainReply(store).isEmpty()) {
            state = "idle"; mood = "happy"; line = CardViews.oneLine(latestMainReply(store), 180);
        } else if (turn.phase.equals("failed")) {
            state = "idle"; mood = "idle"; line = "刚才那句没发出去：" + CardViews.oneLine(turn.reply, 60);
        } else if (turn.phase.equals("done") && !turn.reply.isEmpty()) {
            state = "idle"; mood = "happy"; line = CardViews.oneLine(turn.reply, 180);
        } else {
            Card busy = null;
            for (AppPresentation.Helper helper : roster) if (busy == null) busy = helper.current(connected);
            state = "idle"; mood = connected ? "happy" : "idle";
            line = !connected ? greeting() + "电脑那边好像没连上。"
                    : busy != null ? greeting() + AppPresentation.name(AppPresentation.owner(busy)) + " 在忙「" + CardViews.oneLine(busy.title, 16) + "」，我帮你盯着。"
                    : greeting() + "今天想先做点什么？";
        }
        stage.set(companionChoice, mood, state);
        if (!line.contentEquals(homeSpeech.getText())) homeSpeech.setText(line);

        // 谁在忙：每位帮手一个常驻胶囊，空闲白、在做转圈、等你确认橙、刚做完绿一会儿。
        // 新交出去的事先等她把话说完，再飞一封信过去；信落下胶囊才开始转。
        if (flyTo != null) {
            if (tab == TAB_HOME && stage.moving()) { pendingLetter = flyTo; letterFor.add(flyTo); }
        }
        if (pendingLetter != null && streaming.isEmpty() && (!store.busy() || !turn.agent.isEmpty())) {
            final String role = pendingLetter; pendingLetter = null;
            busyRow.post(() -> flyLetter(role));
        }
        refreshHelperRow(roster, connected);

        if (homeActions.getChildCount() == 0 || (target == null) != (homeActions.getTag() != null)) {
            homeActions.removeAllViews(); homeActions.setTag(target == null ? "setup" : null);
            if (target == null) {
                Button setup = button("连接电脑", view -> openSettings(0));
                setup.setBackground(pressable(BLUE, 24)); setup.setTextColor(Color.WHITE);
                homeActions.addView(setup, new LinearLayout.LayoutParams(dp(200), dp(48)));
            } else {
                for (String said : new String[] {"今天要做什么", "大家在忙什么", "陪我聊会儿"}) {
                    TextView chip = text(said, 13, false); chip.setTextColor(INK);
                    chip.setGravity(Gravity.CENTER); chip.setPadding(dp(14), 0, dp(14), 0);
                    chip.setBackground(pressable(Color.WHITE, 18, 0xffe3e9f1));
                    chip.setOnClickListener(view -> {
                        if (HubStore.get().busy()) { toast(companionName() + "还在回上一句"); return; }
                        pokeLine = null; RuntimeClient.send(this, said);
                    });
                    LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-2, dp(36));
                    params.setMargins(dp(4), 0, dp(4), 0);
                    homeActions.addView(chip, params);
                }
            }
        }
    }

    /** 首页上一位帮手的胶囊：头像、转圈、一句状态。状态变了原地换颜色和字，不重建。 */
    private final class HelperChip {
        final String role;
        final LinearLayout view;
        final AvatarView portrait;
        final NavIconView ring;
        final UsageRing usage;
        final TextView words;
        final GradientDrawable fill = new GradientDrawable();
        android.animation.ObjectAnimator spin;
        String mode = "";
        int color = Color.WHITE;

        HelperChip(String role) {
            this.role = role;
            view = new LinearLayout(MainActivity.this); view.setGravity(Gravity.CENTER_VERTICAL);
            view.setPadding(dp(8), 0, dp(14), 0); view.setTag(role);
            fill.setCornerRadius(dp(22)); fill.setColor(color);
            view.setBackground(new android.graphics.drawable.RippleDrawable(
                    android.content.res.ColorStateList.valueOf(0x1f2f6fe0), fill, null));
            FrameLayout face = new FrameLayout(MainActivity.this);
            portrait = new AvatarView(MainActivity.this, role, "idle");
            portrait.setBackground(surface(AppPresentation.tint(role), AppPresentation.tint(role), 9));
            face.addView(portrait, new FrameLayout.LayoutParams(dp(30), dp(30), Gravity.CENTER));
            // 外圈是还剩的额度：满是绿的，用掉多少就缺多少，快用完变橙、变红。
            usage = new UsageRing(MainActivity.this);
            face.addView(usage, new FrameLayout.LayoutParams(dp(37), dp(37), Gravity.CENTER));
            ring = new NavIconView(MainActivity.this, "spinner"); ring.color(0xff6a5fd0); ring.setAlpha(0f);
            face.addView(ring, new FrameLayout.LayoutParams(dp(40), dp(40), Gravity.CENTER));
            face.setClipChildren(false);
            view.addView(face, new LinearLayout.LayoutParams(dp(40), dp(40)));
            words = text("", 13, false); words.setPadding(dp(6), 0, 0, 0);
            view.addView(words);
            view.setFocusable(true);
            view.setOnClickListener(click -> {
                Card now = null;
                for (AppPresentation.Helper helper : AppPresentation.helpers(HubStore.get().cards(), offeredHelpers()))
                    if (helper.role.equals(role)) now = helper.current(HubStore.get().runtimeConnected());
                if (now != null) open(now.id);
                else if (mode.equals("done") && doneCard.containsKey(role)) open(doneCard.get(role));
                else openAgent(role);
            });
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-2, dp(44));
            params.setMargins(dp(5), 0, dp(5), 0); view.setLayoutParams(params);
        }

        void quota(int percent) {
            usage.set(percent);
            String label = words.getText().toString();
            view.setContentDescription(percent < 0 ? label : label + "，额度还剩百分之" + percent);
        }

        /** idle / working / desktop（电脑上自己开的会话在跑）/ waiting / done。 */
        void show(String next, boolean animate) {
            if (next.equals(mode)) return;
            String before = mode; mode = next;
            String name = AppPresentation.name(role);
            boolean busy = next.equals("working") || next.equals("desktop");
            String label = next.equals("working") ? name + " 在做…" : next.equals("desktop") ? name + " 电脑上在跑"
                    : next.equals("waiting") ? name + " 等你确认"
                    : next.equals("done") ? name + " 好了 ✓" : name + " 空闲";
            int text = busy ? 0xff4e43b0 : next.equals("waiting") ? AMBER
                    : next.equals("done") ? GREEN : HINT;
            int target = busy ? 0xfff0eefb : next.equals("waiting") ? 0xfffff4e0
                    : next.equals("done") ? 0xffe6f5ec : Color.WHITE;
            words.setText(label); words.setTextColor(text); quota(usage.left());
            portrait.set(role, next.equals("idle") ? "idle" : next.equals("done") ? "happy" : "focus");
            if (animate) {
                android.animation.ValueAnimator tint = android.animation.ValueAnimator.ofArgb(color, target);
                tint.setDuration(300); tint.addUpdateListener(a -> fill.setColor((int) a.getAnimatedValue())); tint.start();
            } else fill.setColor(target);
            color = target;
            boolean spinning = busy;
            if (spinning && spin == null) {
                spin = android.animation.ObjectAnimator.ofFloat(ring, "rotation", 0f, 360f);
                spin.setDuration(900); spin.setRepeatCount(android.animation.ValueAnimator.INFINITE);
                spin.setInterpolator(new android.view.animation.LinearInterpolator()); spin.start();
            }
            ring.animate().alpha(spinning ? 1f : 0f).setDuration(animate ? 250 : 0).withEndAction(() -> {
                if (!mode.equals("working") && !mode.equals("desktop") && spin != null) { spin.cancel(); spin = null; }
            }).start();
            // 开始干活、做完时弹一下；回到空闲只是安静地褪色。
            if (animate && !before.isEmpty() && (busy || next.equals("done"))) pop(next.equals("done") ? 1.18f : 1.12f);
        }

        void pop(float scale) {
            view.animate().cancel();
            view.animate().scaleX(scale).scaleY(scale).setDuration(140)
                    .setInterpolator(new android.view.animation.DecelerateInterpolator())
                    .withEndAction(() -> view.animate().scaleX(1f).scaleY(1f).setDuration(260)
                            .setInterpolator(new android.view.animation.OvershootInterpolator(3f)).start()).start();
        }
    }

    private void refreshHelperRow(List<AppPresentation.Helper> roster, boolean connected) {
        StringBuilder key = new StringBuilder();
        for (AppPresentation.Helper helper : roster) key.append(helper.role).append('|');
        boolean rebuilt = !key.toString().equals(busyKey);
        if (rebuilt) {
            busyKey = key.toString(); busyRow.removeAllViews();
            Map<String, HelperChip> kept = new HashMap<>(helperChips); helperChips.clear();
            for (AppPresentation.Helper helper : roster) {
                HelperChip chip = kept.get(helper.role);
                if (chip == null) chip = new HelperChip(helper.role);
                helperChips.put(helper.role, chip); busyRow.addView(chip.view);
            }
            busyRow.setVisibility(roster.isEmpty() ? View.GONE : View.VISIBLE);
        }
        long now = System.currentTimeMillis();
        boolean animate = tab == TAB_HOME && stage.moving();
        Usage usage = HubStore.get().usage();
        for (AppPresentation.Helper helper : roster) {
            // 和侧边栏、帮手页用同一个判断；只多两样首页自己的动画状态：信还在飞、刚做完。
            String mode = helper.mode(connected);
            Long done = doneAt.get(helper.role);
            if (letterFor.contains(helper.role)) mode = "idle";
            else if (mode.equals("idle") && done != null && now - done < 8000) mode = "done";
            HelperChip chip = helperChips.get(helper.role);
            chip.show(mode, animate);
            chip.quota(usage == null ? -1 : usage.left(helper.role, now / 1000L));
        }
    }

    /** 一封小信沿弧线从小幽飞到接活的帮手胶囊上；落下时胶囊弹一下开始转。 */
    private void flyLetter(String role) {
        HelperChip chip = helperChips.get(role);
        if (chip == null || frame == null || tab != TAB_HOME || !chip.view.isShown()) {
            letterFor.remove(role); refreshHome(HubStore.get(), HubStore.get().cards()); return;
        }
        View target = chip.view;
        // 帮手在行里滑出了屏幕：先把它滑进来，信才有地方落。
        android.graphics.Rect seen = new android.graphics.Rect();
        if (!target.getGlobalVisibleRect(seen) || seen.width() < target.getWidth())
            busyScroll.scrollTo(Math.max(0, target.getLeft() - (busyScroll.getWidth() - target.getWidth()) / 2), 0);
        int[] from = new int[2], to = new int[2], base = new int[2];
        stage.center(from); target.getLocationInWindow(to); frame.getLocationInWindow(base);
        to[0] += dp(28); to[1] += target.getHeight() / 2;
        View letter = new View(this) {
            final android.graphics.Paint paint = new android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG);
            @Override protected void onDraw(android.graphics.Canvas canvas) {
                float w = getWidth(), h = getHeight(), line = dp(2), r = dp(3);
                android.graphics.RectF box = new android.graphics.RectF(line / 2, line / 2, w - line / 2, h - line / 2);
                paint.setStyle(android.graphics.Paint.Style.FILL); paint.setColor(Color.WHITE);
                canvas.drawRoundRect(box, r, r, paint);
                paint.setStyle(android.graphics.Paint.Style.STROKE); paint.setStrokeWidth(line); paint.setColor(BLUE);
                paint.setStrokeJoin(android.graphics.Paint.Join.ROUND); paint.setStrokeCap(android.graphics.Paint.Cap.ROUND);
                canvas.drawRoundRect(box, r, r, paint);
                android.graphics.Path flap = new android.graphics.Path();
                flap.moveTo(line * 1.5f, line * 2); flap.lineTo(w / 2, h * .58f); flap.lineTo(w - line * 1.5f, line * 2);
                canvas.drawPath(flap, paint);
            }
        };
        letter.setElevation(dp(3));
        int width = dp(30), height = dp(22);
        frame.addView(letter, new FrameLayout.LayoutParams(width, height));
        float x0 = from[0] - base[0] - width / 2f, y0 = from[1] - base[1] - height / 2f;
        float x1 = to[0] - base[0] - width / 2f, y1 = to[1] - base[1] - height / 2f;
        // 先往反方向荡一下再落下，像被轻轻抛出去。
        float cx = x0 + (x0 - x1) * .6f, cy = Math.min(y0, y1) - dp(70);
        letter.setTranslationX(x0); letter.setTranslationY(y0);
        letter.setAlpha(0f); letter.setScaleX(.4f); letter.setScaleY(.4f);
        android.animation.ValueAnimator path = android.animation.ValueAnimator.ofFloat(0f, 1f);
        path.setDuration(900); path.setInterpolator(new android.view.animation.AccelerateDecelerateInterpolator());
        path.addUpdateListener(animation -> {
            float t = (float) animation.getAnimatedValue(), u = 1 - t;
            letter.setTranslationX(u * u * x0 + 2 * u * t * cx + t * t * x1);
            letter.setTranslationY(u * u * y0 + 2 * u * t * cy + t * t * y1);
            letter.setRotation(x1 < x0 ? 18 * u - 6 * t : -18 * u + 6 * t);
            float grow = Math.min(1f, t * 5f);
            float land = t > .85f ? 1f - (t - .85f) / .15f * .5f : 1f;
            letter.setAlpha(Math.min(grow, t > .85f ? land * 1.4f - .4f : 1f));
            letter.setScaleX((.4f + .6f * grow) * land); letter.setScaleY((.4f + .6f * grow) * land);
        });
        path.addListener(new android.animation.AnimatorListenerAdapter() {
            @Override public void onAnimationEnd(android.animation.Animator animation) {
                frame.removeView(letter);
                letterFor.remove(role);
                refreshHome(HubStore.get(), HubStore.get().cards());
            }
        });
        path.start();
    }

    // ---- 随身小幽 ----

    private void refreshDevice(HubStore store) {
        if (devicePane == null) return;
        boolean near = store.linkState().equals("已连接");
        HubStore.Turn turn = store.turn();
        String screen = store.busy() ? (turn.stage.isEmpty() ? "在想…" : CardViews.oneLine(turn.stage, 10)) : near ? "在身边" : "睡着了";
        String key = store.linkState() + "|" + store.firmwareLine() + "|" + screen;
        if (key.equals(deviceKey)) return;
        deviceKey = key; devicePane.removeAllViews();
        LinearLayout mirror = column(); mirror.setGravity(Gravity.CENTER_HORIZONTAL); mirror.setPadding(0, dp(8), 0, dp(18));
        LinearLayout shell = column(); shell.setGravity(Gravity.CENTER);
        shell.setBackground(surface(0xffdfe6ef, 0xffdfe6ef, 36));
        LinearLayout lcd = column(); lcd.setGravity(Gravity.CENTER);
        lcd.setBackground(surface(0xff14243a, 0xff14243a, 18));
        lcd.addView(new AvatarView(this, "ds", near ? "happy" : "idle"), new LinearLayout.LayoutParams(dp(64), dp(64)));
        TextView line = text(screen, 11, false); line.setTextColor(0xffcfe0ff); line.setPadding(0, dp(4), 0, 0); lcd.addView(line);
        shell.addView(lcd, new LinearLayout.LayoutParams(dp(124), dp(124)));
        mirror.addView(shell, new LinearLayout.LayoutParams(dp(156), dp(196)));
        TextView status = text(near ? "● 在身边 · 屏幕和手机同步" : "● " + store.linkState(), 13, false);
        status.setTextColor(near ? GREEN : AMBER); status.setPadding(0, dp(12), 0, 0); mirror.addView(status);
        devicePane.addView(mirror);
        LinearLayout list = group();
        list.addView(groupRow("device", near ? "断开设备" : "连接设备", null, "›", HINT, view -> {
            if (near) { rememberConnect(false); LinkService.stop(this); } else connect();
        }, true));
        list.addView(groupRow(null, "固件", CardViews.oneLine(store.firmwareLine(), 30), "›", HINT, view -> openSettings(0), false));
        devicePane.addView(list);
        TextView later = text("更多玩法 · 随固件更新到来\n按键怎么用、提醒、表盘……设备能做什么，这里就会多出什么。", 12, false);
        later.setTextColor(HINT); later.setLineSpacing(dp(3), 1f);
        later.setPadding(dp(16), dp(14), dp(16), dp(14));
        GradientDrawable dashed = new GradientDrawable(); dashed.setCornerRadius(dp(18));
        dashed.setStroke(dp(1), 0xffd5dde8, dp(5), dp(4)); later.setBackground(dashed);
        LinearLayout.LayoutParams laterParams = new LinearLayout.LayoutParams(-1, -2); laterParams.setMargins(0, dp(18), 0, 0);
        devicePane.addView(later, laterParams);
    }

    @Override protected void onDestroy() {
        ++voiceSession; mic.dispose(); plainMic.cancel();
        super.onDestroy();
    }

    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        String card = intent.getStringExtra("card");
        if (card != null) open(card);
        String approval = intent.getStringExtra("approval");
        if (approval != null) {
            if (HubStore.get().approval(approval) != null) showApproval(approval);
            else { pendingApproval = approval; android.widget.Toast.makeText(this, "正在核实来源审批，请稍候", android.widget.Toast.LENGTH_SHORT).show(); }
        }
    }

    private GradientDrawable surface(int color, int border) {
        return surface(color, border, 14);
    }

    private GradientDrawable surface(int color, int border, int radius) {
        GradientDrawable result = new GradientDrawable(); result.setColor(color);
        result.setCornerRadius(dp(radius)); result.setStroke(dp(1), border); return result;
    }

    /** 能点的面：按下时有水波，没有阴影。 */
    private Drawable pressable(int color, int radius) {
        return pressable(color, radius, color);
    }

    private Drawable pressable(int color, int radius, int border) {
        GradientDrawable mask = new GradientDrawable(); mask.setColor(Color.WHITE); mask.setCornerRadius(dp(radius));
        return new RippleDrawable(ColorStateList.valueOf(0x1a1f3149), surface(color, border, radius), mask);
    }

    /** 侧边栏和列表里的一行；selected 是现在打开的那一处。 */
    private LinearLayout listRow(int height, boolean selected) {
        LinearLayout row = new LinearLayout(this); row.setGravity(Gravity.CENTER_VERTICAL);
        row.setMinimumHeight(dp(height)); row.setPadding(dp(10), dp(4), dp(10), dp(4));
        row.setBackground(pressable(selected ? 0xffeef4ff : Color.TRANSPARENT, 12));
        row.setFocusable(true); row.setClickable(true);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
        params.setMargins(0, dp(1), 0, dp(1)); row.setLayoutParams(params);
        return row;
    }

    private AvatarView avatar(String role, String mood, int size) {
        AvatarView view = new AvatarView(this, role, mood);
        int radius = Math.max(6, size * 3 / 10);
        view.setBackground(surface(AppPresentation.tint(role), AppPresentation.tint(role), radius));
        view.setLayoutParams(new LinearLayout.LayoutParams(dp(size), dp(size)));
        return view;
    }

    /** 状态只用文字颜色：等你确认是橙色，其余进行中的是蓝色，未知是灰色。 */
    private TextView stateText(String word, int size) {
        TextView view = text(word, size, false);
        view.setTextColor(word.contains("确认") ? AMBER : word.contains("未知") ? HINT : BLUE);
        view.setSingleLine(true);
        return view;
    }

    private void confirmStop(Card card) {
        Card latest = CardViews.find(HubStore.get().cards(), card.id);
        if (latest == null || latest.monitoringOnly || !latest.active()) return;
        RuntimeClient.Target target = RuntimeClient.selected(this);
        String runtime = target == null ? "" : target.url;
        long epoch = HubStore.get().cardsEpoch();
        new AlertDialog.Builder(this).setTitle("停止「" + card.title + "」？").setMessage("正在做的帮手会停下，已经做好的部分保留。")
                .setNegativeButton("继续", null).setPositiveButton("停止", (dialog, which) -> {
                    RuntimeClient.Target current = RuntimeClient.selected(this);
                    if (current != null && current.url.equals(runtime) && epoch == HubStore.get().cardsEpoch()) RuntimeClient.cancel(this, card.id);
                }).show();
    }

    private void showApproval(String id) {
        Card.Approval approval = HubStore.get().approval(id);
        if (approval == null) { toast("这次请求已经处理或撤回"); return; }
        if (approvalDialog != null) approvalDialog.dismiss();
        Dialog dialog = new Dialog(this); approvalDialog = dialog;
        LinearLayout page = column(); page.setPadding(dp(22), dp(12), dp(22), dp(12));
        page.setBackgroundColor(BACKGROUND);
        Button back = button("← 确认这次操作", view -> dialog.dismiss()); page.addView(back);
        ScrollView reader = new ScrollView(this);
        LinearLayout contents = column(); contents.setPadding(0, dp(14), 0, dp(20));
        LinearLayout hero = column(); hero.setGravity(Gravity.CENTER);
        hero.addView(new AvatarView(this, approval.agent, "ask"), new LinearLayout.LayoutParams(dp(144), dp(148)));
        TextView title = text(AppPresentation.name(approval.agent) + " 等你确认", 22, true);
        title.setPadding(0, dp(12), 0, dp(10)); hero.addView(title);
        hero.addView(text("来源电脑 · " + approval.runtimeName, 12, false));
        contents.addView(hero);
        TextView tool = text(approval.tool, 17, true); tool.setPadding(0, dp(22), 0, dp(12)); contents.addView(tool);
        TextView detail = text(approval.detail, 14, false); detail.setTextColor(INK);
        detail.setTextIsSelectable(true); detail.setTypeface(Typeface.MONOSPACE);
        detail.setPadding(dp(14), dp(14), dp(14), dp(14)); detail.setBackground(surface(Color.WHITE, 0xffe6ebf2));
        contents.addView(detail);
        contents.addView(button("查看来源任务", view -> { dialog.dismiss(); viewApprovalTask(approval); }));
        reader.addView(contents); page.addView(reader, new LinearLayout.LayoutParams(-1, 0, 1));
        TextView status = text(HubStore.get().monitoringApproval(id) ? "请在电脑端确认，这里保留操作原文。" : "决定仅用于这一次操作，回复会送回来源电脑。", 12, false);
        status.setPadding(0, dp(12), 0, dp(12)); page.addView(status);
        LinearLayout actions = new LinearLayout(this);
        if (!HubStore.get().monitoringApproval(id)) {
            Button deny = button("拒绝本次", null), allow = button("允许本次", null);
            allow.setBackground(surface(BLUE, BLUE)); allow.setTextColor(Color.WHITE);
            deny.setOnClickListener(view -> submitApproval(approval, false, dialog, actions, status));
            allow.setOnClickListener(view -> submitApproval(approval, true, dialog, actions, status));
            LinearLayout.LayoutParams left = new LinearLayout.LayoutParams(0, dp(48), 1); left.setMargins(0, 0, dp(6), 0);
            LinearLayout.LayoutParams right = new LinearLayout.LayoutParams(0, dp(48), 1); right.setMargins(dp(6), 0, 0, 0);
            actions.addView(deny, left); actions.addView(allow, right); page.addView(actions);
        }
        dialog.setContentView(page); dialog.show();
        dialog.getWindow().setBackgroundDrawableResource(android.R.color.transparent);
        dialog.getWindow().setLayout(-1, -1);
    }

    private void submitApproval(Card.Approval original, boolean allow, Dialog dialog,
                                LinearLayout actions, TextView status) {
        Card.Approval current = HubStore.get().approval(original.id);
        if (current == null || current.createdAt != original.createdAt || !current.tool.equals(original.tool)
                || !current.detail.equals(original.detail) || HubStore.get().monitoringApproval(original.id)) {
            status.setText("请求已变化或不再可回答，请重新打开核实。");
            for (int i=0;i<actions.getChildCount();i++) actions.getChildAt(i).setEnabled(false);
            return;
        }
        String claimed = HubStore.get().takeApproval(original.id);
        if (claimed == null) { status.setText("请求已在其他端处理，请等待状态同步。"); return; }
        for (int i=0;i<actions.getChildCount();i++) actions.getChildAt(i).setEnabled(false);
        status.setText("正在提交到 " + original.runtimeName + "…");
        RuntimeClient.approve(this, claimed, allow, problem -> {
            if (isFinishing() || isDestroyed() || !dialog.isShowing()) return;
            if (problem == null) { dialog.dismiss(); toast(allow ? "已允许本次操作" : "已拒绝本次操作"); }
            else {
                status.setText(problem);
                boolean retry = HubStore.get().approvals().stream().anyMatch(item -> item.id.equals(original.id));
                for (int i=0;i<actions.getChildCount();i++) actions.getChildAt(i).setEnabled(retry);
            }
        });
    }

    private void viewApprovalTask(Card.Approval approval) {
        RuntimeClient.Target current = RuntimeClient.selected(this);
        if (current != null && current.url.equals(approval.runtimeUrl)) {
            open(approval.card); return;
        }
        new AlertDialog.Builder(this).setTitle("查看 " + approval.runtimeName + " 上的任务？")
                .setMessage("将切换任务列表的来源电脑，已输入的文字会保留。")
                .setNegativeButton("留在当前电脑", null).setPositiveButton("切换并查看", (dialog, which) -> {
                    RuntimeClient.Target origin = RuntimeClient.target(this, approval.runtimeUrl);
                    if (origin != null) { RuntimeClient.select(this, origin.url); pendingTask = approval.card; show(TAB_TASKS); }
                }).show();
    }

    private void addThread(LinearLayout parent, Card card) {
        Set<String> rendered = new HashSet<>();
        for (Card.Entry entry : card.entries) rendered.addAll(addBubble(parent, card, entry));
        for (Attachment item : card.attachments) if (!rendered.contains(item.id)) parent.addView(attachmentView(item));
    }

    /** 你说的靠右、蓝底；对方说的靠左、白底。只有说话的不是小幽时才写名字。返回画过的附件编号。 */
    private Set<String> addBubble(LinearLayout parent, Card card, Card.Entry entry) {
        Set<String> rendered = new HashSet<>();
        boolean mine = entry.role.equals("you");
        LinearLayout bubble = bubbleShell(mine);
        String author = entry.author.isEmpty() ? AppPresentation.author(card, entry) : entry.author;
        if (entry.kind.equals("worker_result")) {
            TextView original = text(AppPresentation.name(author) + " · 结果原文", 12, true);
            original.setTextColor(HINT); bubble.addView(original);
        }
        if (!mine && !AppPresentation.role(author).equals("ds")) {
            LinearLayout heading = new LinearLayout(this); heading.setGravity(Gravity.CENTER_VERTICAL);
            heading.setPadding(0, 0, 0, dp(6));
            heading.addView(avatar(author, "idle", 22));
            TextView name = text(AppPresentation.name(author) + (card.monitoringOnly ? " · 电脑" : ""), 12, true);
            name.setPadding(dp(6), 0, 0, 0); heading.addView(name);
            bubble.addView(heading);
        }
        TextView words = bubbleWords(mine, entry.text.length() > 16000 ? entry.text.substring(0, 16000) + "\n\n较长内容已折叠，请打开完整方案附件。" : entry.text);
        words.setTextIsSelectable(true); bubble.addView(words);
        for (Attachment item : entry.attachments) { bubble.addView(attachmentView(item)); rendered.add(item.id); }
        parent.addView(bubble, bubbleParams(mine));
        return rendered;
    }

    /** 气泡的底：你说的蓝底、尖角在右下，对方说的白底、尖角在左上。 */
    private LinearLayout bubbleShell(boolean mine) {
        LinearLayout bubble = column(); bubble.setPadding(dp(14), dp(10), dp(14), dp(10));
        GradientDrawable shape = surface(mine ? BLUE : Color.WHITE, mine ? BLUE : Color.WHITE, 18);
        float big = dp(18), small = dp(6);
        shape.setCornerRadii(mine ? new float[] {big, big, big, big, small, small, big, big}
                : new float[] {small, small, big, big, big, big, big, big});
        bubble.setBackground(shape);
        return bubble;
    }

    private TextView bubbleWords(boolean mine, String value) {
        TextView words = text(value, 15, false);
        words.setTextColor(mine ? Color.WHITE : INK); words.setLineSpacing(dp(3), 1f);
        return words;
    }

    private LinearLayout.LayoutParams bubbleParams(boolean mine) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-2, -2);
        params.gravity = mine ? Gravity.END : Gravity.START;
        params.setMargins(mine ? dp(48) : 0, dp(5), mine ? 0 : dp(40), dp(5));
        return params;
    }

    private View attachmentView(Attachment item) {
        LinearLayout card = column(); card.setPadding(dp(10), dp(10), dp(10), dp(10));
        card.setBackground(surface(0xfff3f7ff, 0xffdce6f7));
        card.addView(text((item.image() ? "图片 · " : "文件 · ") + item.label() + "\n" + item.sizeLabel() + " · " + item.mime, 13, true));
        RuntimeClient.Target target = RuntimeClient.selected(this);
        long epoch = HubStore.get().cardsEpoch();
        if (!item.ready()) card.addView(text(item.error.isEmpty() ? "附件还未准备好" : item.error, 13, false));
        if (item.image() && item.ready()) {
            ImageView image = new ImageView(this); image.setScaleType(ImageView.ScaleType.FIT_CENTER);
            card.addView(image, new LinearLayout.LayoutParams(-1, dp(180)));
            TextView hint = text("正在加载预览…", 12, false); card.addView(hint);
            Runnable load = () -> ArtifactClient.fetch(this, target, item, item.thumbnail, (file, error) -> {
                if (epoch != HubStore.get().cardsEpoch() || isFinishing()) return;
                if (file != null) {
                    Bitmap bitmap = scaledBitmap(file, 900); image.setImageBitmap(bitmap);
                    hint.setText(bitmap == null ? "无法解码预览，可下载原文件" : "点图片查看 · 已缓存，可离线打开");
                } else hint.setText((error == null ? "未取得预览" : error) + " · 点击重试");
            });
            hint.setOnClickListener(view -> load.run()); load.run();
            image.setContentDescription(item.label()); image.setOnClickListener(view -> openAttachment(item, target));
        }
        Button open = button(item.image() ? "查看原图 / 保存" : "打开 / 保存文件", view -> openAttachment(item, target));
        open.setEnabled(item.ready()); card.addView(open);
        return card;
    }

    private Bitmap scaledBitmap(File file, int max) {
        BitmapFactory.Options options = new BitmapFactory.Options(); options.inJustDecodeBounds = true;
        BitmapFactory.decodeFile(file.getAbsolutePath(), options);
        options.inSampleSize = 1;
        while (options.outWidth / options.inSampleSize > max || options.outHeight / options.inSampleSize > max) options.inSampleSize *= 2;
        options.inJustDecodeBounds = false;
        try { return BitmapFactory.decodeFile(file.getAbsolutePath(), options); } catch (OutOfMemoryError exhausted) { return null; }
    }

    private void openAttachment(Attachment item, RuntimeClient.Target target) {
        long epoch = HubStore.get().cardsEpoch();
        android.widget.Toast.makeText(this, "正在打开附件…", android.widget.Toast.LENGTH_SHORT).show();
        ArtifactClient.fetch(this, target, item, false, (file, error) -> {
            if (isFinishing() || epoch != HubStore.get().cardsEpoch()) return;
            if (file == null) {
                new AlertDialog.Builder(this).setTitle("暂时无法打开").setMessage(error).setNegativeButton("关闭", null)
                        .setPositiveButton("重试", (dialog, which) -> openAttachment(item, target)).show(); return;
            }
            if (item.image()) {
                LinearLayout content = column();
                ImageView image = new ImageView(this); image.setScaleType(ImageView.ScaleType.FIT_CENTER);
                image.setImageBitmap(scaledBitmap(file, 2048));
                content.addView(image, new LinearLayout.LayoutParams(-1, 0, 1));
                content.addView(button("保存到手机", view -> saveAttachment(file, item)));
                content.addView(button("用其他应用打开", view -> externalAttachment(file, item)));
                Dialog preview = new Dialog(this); preview.setContentView(content); preview.show();
                preview.getWindow().setLayout(-1, -1);
            } else if (item.mime.startsWith("text/") || item.label().endsWith(".md") || item.label().endsWith(".txt")) {
                ArtifactClient.readText(file, (body, failure) -> {
                    if (isFinishing()) return;
                    LinearLayout content = column(); content.setPadding(dp(12), dp(12), dp(12), dp(8));
                    content.addView(text(item.label(), 17, true));
                    TextView document = text(body == null ? failure : body, 15, false); document.setTextIsSelectable(true);
                    ScrollView reader = new ScrollView(this); reader.addView(document);
                    content.addView(reader, new LinearLayout.LayoutParams(-1, 0, 1));
                    content.addView(button("保存完整文件", view -> saveAttachment(file, item)));
                    content.addView(button("用其他应用打开", view -> externalAttachment(file, item)));
                    Dialog preview = new Dialog(this); preview.setContentView(content); preview.show(); preview.getWindow().setLayout(-1, -1);
                });
            } else new AlertDialog.Builder(this).setTitle(item.label()).setItems(new String[] {"用手机应用打开", "保存到手机"},
                    (dialog, index) -> { if (index == 0) externalAttachment(file, item); else saveAttachment(file, item); }).show();
        });
    }

    private void externalAttachment(File file, Attachment item) {
        Intent intent = new Intent(Intent.ACTION_VIEW).setDataAndType(ArtifactProvider.uri(this, file, item), item.mime.isEmpty() ? "application/octet-stream" : item.mime)
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        try { startActivity(Intent.createChooser(intent, "打开 " + item.label())); }
        catch (android.content.ActivityNotFoundException missing) { new AlertDialog.Builder(this).setMessage("手机没有可打开此格式的应用，可以先保存文件。").setPositiveButton("保存", (dialog, which) -> saveAttachment(file, item)).setNegativeButton("关闭", null).show(); }
    }

    private void saveAttachment(File file, Attachment item) {
        exportFile = file;
        startActivityForResult(new Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
                .setType(item.mime.isEmpty() ? "application/octet-stream" : item.mime).putExtra(Intent.EXTRA_TITLE, item.label()), 3);
    }

    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == 3 && resultCode == RESULT_OK && data != null && data.getData() != null && exportFile != null) {
            ArtifactClient.save(this, exportFile, data.getData(), (file, error) -> android.widget.Toast.makeText(this,
                    file == null ? "保存失败：" + error : "已保存到所选位置", android.widget.Toast.LENGTH_LONG).show());
        }
        if (requestCode == 3) exportFile = null;
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
                HubStore.get().log("现在由 " + target.name + " 上的ds回答");
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
            needed.add(Manifest.permission.ACCESS_COARSE_LOCATION);
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
        if (requestCode == 4) {
            boolean allowed = grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED;
            micHint.setText(allowed ? "按住说话" : "麦克风权限未允许");
            toast(allowed ? "麦克风已允许，再按住说话就可以了" : "需要麦克风权限才能按住说话");
            if (!allowed && !shouldShowRequestPermissionRationale(Manifest.permission.RECORD_AUDIO))
                new AlertDialog.Builder(this).setTitle("开启麦克风权限")
                        .setMessage("系统没有允许录音。请在应用权限中开启麦克风，再回来按住说话。")
                        .setNegativeButton("稍后", null).setPositiveButton("打开设置", (dialog, which) ->
                                startActivity(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                                        Uri.parse("package:" + getPackageName())))).show();
        }
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
        view.setTextColor(bold ? INK : MUTED);
        view.setLineSpacing(dp(2), 1f);
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

    /** 平的按钮：没有系统阴影和按下浮起，字体不带额外上下留白，文字正中。 */
    private Button button(String label, View.OnClickListener action) {
        Button button = new Button(this);
        button.setText(label);
        button.setAllCaps(false);
        button.setTextColor(BLUE); button.setTextSize(14);
        button.setStateListAnimator(null); button.setElevation(0);
        button.setIncludeFontPadding(false); button.setGravity(Gravity.CENTER);
        button.setMinWidth(0); button.setMinimumWidth(0);
        button.setMinHeight(dp(44)); button.setMinimumHeight(dp(44));
        button.setBackground(pressable(Color.WHITE, 12, 0xffe6ebf2));
        button.setPadding(dp(14), dp(10), dp(14), dp(10));
        button.setOnClickListener(action);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.setMargins(0, dp(5), 0, dp(5));
        button.setLayoutParams(params);
        return button;
    }

    /** 只放一个符号的方形按钮：不留内边距，符号在 44×44 里正中。 */
    private Button iconButton(String glyph, String label, View.OnClickListener action) {
        Button button = button(glyph, action);
        button.setTextSize(20); button.setPadding(0, 0, 0, 0);
        button.setContentDescription(label);
        button.setLayoutParams(new LinearLayout.LayoutParams(dp(44), dp(44)));
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
