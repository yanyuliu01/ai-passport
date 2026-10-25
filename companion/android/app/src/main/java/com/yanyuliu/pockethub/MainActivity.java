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
    /** 设置: the home of every utility page. */
    private static final int TAB_MINE = 4;
    private static final int TAB_COMPANIONS = 5;
    /** One helper's page; openAgent names it. */
    private static final int TAB_AGENT = 6;
    /** 首页：小幽本人。打开 App 先到这里。 */
    private static final int TAB_HOME = 7;
    private static final int TAB_DEVICE = 8;
    private static final int TAB_WORK = 9, TAB_NATIVE = 10;
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
    // Usage events for the runtime's auto-iteration (UsageEvents): screen dwell, repeated taps.
    private long screenSince = System.currentTimeMillis();
    private long eventsSentAt;
    private UsageEvents.RageTap rageTap;
    // Names of tappable controls for usage events. Only App-authored names, never on-screen content.
    private final java.util.WeakHashMap<View, String> controls = new java.util.WeakHashMap<>();
    private float downX, downY;
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
    /** 这次按住说话是从聊天页输入框按的（否则是首页的大按钮）。 */
    private boolean micFromComposer;
    private long micPressedAt;
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
    /** 页面顶上的小提示（代替 Toast）。 */
    private TextView noticeView;
    private final Runnable noticeHide = this::hideNotice;
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
    private NavIconView sendIcon;
    private android.animation.ObjectAnimator sendSpin;
    /** 输入框右边按钮现在的样子（AppPresentation.composerState）；叫停的那件事和叫停的时间。 */
    private String composerShown = "";
    private static final String COMPOSER_VOICE = "voice";
    private String stoppingCard = null;
    private long stoppingAt = 0;
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
    /** 聊天列表现在画着的：窗口、每行的消息编号和样子、每行的 View（ChatPlan 拿它们比）。 */
    private String chatHeader = "";
    private List<String> chatIds = new ArrayList<>(), chatLooks = new ArrayList<>();
    private final List<View> chatRows = new ArrayList<>();
    private int chatHeaderViews = 0;
    private LinearLayout markerRow;
    private ReplyMarker marker;
    private TextView markerLabel;
    private LinearLayout replyActions;
    private final Runnable markerTick = this::refreshMarker;
    private int chatWindow = 80;
    /** 聊天窗口里第一条消息的编号（见 refreshChat）。 */
    private String chatAnchor = null;
    private final Map<String, TextView> chatWords = new HashMap<>();
    /** 小幽每条回复的气泡组（一段一个气泡）。 */
    private final Map<String, LinearLayout> chatGroups = new HashMap<>();
    /** 挂着任务卡的那几行：一件事只在它最新的一条回复下面挂一张（见 refreshChat）。 */
    private final Set<String> chatReceipts = new HashSet<>();
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
            if (HubStore.get().runtimeConnected() && System.currentTimeMillis() - eventsSentAt >= 60000L) {
                eventsSentAt = System.currentTimeMillis();
                UsageEvents.flush(RuntimeClient.selected(MainActivity.this));
            }
            uiHandler.postDelayed(this, 1000);
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        String versionName = "";
        try { versionName = getPackageManager().getPackageInfo(getPackageName(), 0).versionName; } catch (Exception ignored) { }
        UsageEvents.init(getFilesDir(), versionName, "android-" + android.os.Build.VERSION.SDK_INT);
        UsageEvents.installCrashHandler();
        rageTap = new UsageEvents.RageTap(dp(48));
        getWindow().setStatusBarColor(BACKGROUND);
        getWindow().setNavigationBarColor(BACKGROUND);
        getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR
                | View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR);
        LinearLayout page = column();
        page.setBackgroundColor(BACKGROUND);
        header = new LinearLayout(this);
        header.setPadding(dp(8), dp(8), dp(8), dp(8));
        header.setGravity(Gravity.CENTER_VERTICAL);
        navigationButton = iconAction("menu", "打开侧边栏", view -> {
            if (topLevelScreen()) setDrawer(true); else onBackPressed();
        });
        header.addView(navigationButton, new LinearLayout.LayoutParams(dp(44), dp(44)));
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
        voiceButton = iconAction("mic", "切到语音", view -> mainAs(false, false));
        header.addView(voiceButton, new LinearLayout.LayoutParams(dp(44), dp(44)));
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
        approvalBar.setOnClickListener(view -> showApprovals()); ctl(approvalBar, "approval-bar");
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
        // 通知：只有两个系统开关。
        notificationSettingsPane.addView(button("开启审批通知", view -> {
            if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
                requestPermissions(new String[] {Manifest.permission.POST_NOTIFICATIONS}, 2);
            else startActivity(new Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, getPackageName()));
        }));
        notificationSettingsPane.addView(button("开启通知读取", view -> startActivity(
                new Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))));

        // 开发者：地址、连接串、固件版本、通知来源、测试和日志。正常使用不用打开。
        LinearLayout root = preferenceSettingsPane;
        TextView developerNote = text("排查问题时用。正常使用不需要打开这一页。", 13, false);
        developerNote.setPadding(0, dp(8), 0, 0); root.addView(developerNote);
        status = text("", 13, false);
        status.setPadding(0, dp(8), 0, dp(4));
        root.addView(status);

        root.addView(heading("已登记的电脑"));
        runtimeList = new LinearLayout(this);
        runtimeList.setOrientation(LinearLayout.VERTICAL);
        root.addView(runtimeList);
        runtimeStatus = text("", 13, false);
        root.addView(runtimeStatus);
        pairingInput = input("粘贴连接串");
        root.addView(pairingInput);
        root.addView(button("用连接串添加", view -> savePairing()));
        root.addView(button("统一会话目录", view -> SharedDirectoryPane.show(this)));
        root.addView(button("同步本机整理", view -> previewSharedMarks()));

        root.addView(heading("设备固件"));
        firmwareStatus = text("", 13, false);
        root.addView(firmwareStatus);
        firmwareList = new LinearLayout(this);
        firmwareList.setOrientation(LinearLayout.VERTICAL);
        root.addView(firmwareList);
        root.addView(button("看看电脑上留着哪些版本", view -> fetchFirmware()));
        root.addView(button("不换了（取消还没开始的）", view ->
                RuntimeClient.chooseFirmware(this, null, "")));
        root.addView(button("授权蓝牙", view -> requestBluetooth()));

        root.addView(heading("语音"));
        CheckBox computerVoice = new CheckBox(this);
        computerVoice.setText("手机录音交给电脑识别");
        computerVoice.setChecked(voiceOnComputer());
        computerVoice.setOnCheckedChangeListener((view, checked) ->
                getSharedPreferences("ds_ui", MODE_PRIVATE).edit().putBoolean("voice_on_computer", checked).apply());
        root.addView(computerVoice);

        root.addView(heading("通知来源"));
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
        workPane = column(); nativePane = column();
        body.addView(workPane); body.addView(nativePane);
        body.addView(homePane); body.addView(devicePane);
        body.addView(chatPane); body.addView(taskPane); body.addView(agentPane); body.addView(minePane);
        body.addView(companionPane); body.addView(usagePane); body.addView(settingsPane);
        scroll = new ScrollView(this); scroll.setFillViewport(true);
        scroll.setVerticalScrollBarEnabled(false); scroll.addView(body);
        page.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
        composer = column(); composer.setPadding(dp(12), dp(6), dp(12), dp(16));
        composer.setBackgroundColor(BACKGROUND);
        chatView = text("", 12, false); chatView.setPadding(dp(8), dp(2), dp(8), dp(6));
        chatView.setOnClickListener(view -> { if (nowJump != null) open(nowJump); }); ctl(chatView, "chat-now");
        chatView.setVisibility(View.GONE);
        composer.addView(chatView);
        supplementHint = text("", 12, false); supplementHint.setTextColor(BLUE); supplementHint.setVisibility(View.GONE);
        supplementHint.setPadding(dp(10), dp(6), dp(10), dp(6));
        supplementHint.setOnClickListener(v -> { supplementCard = null; supplementHint.setVisibility(View.GONE); chatInput.setHint("跟小幽说…"); });
        composer.addView(supplementHint);
        LinearLayout inputBox = column(); inputBox.setPadding(dp(4), dp(4), dp(4), dp(4));
        inputBox.setBackground(surface(Color.WHITE, 0xffe6ebf2, 22));
        // 选好的几张图横排在输入框上方，各自一选就开始上传，发送时只报编号。
        draftPanel = column(); draftPanel.setPadding(dp(8), dp(4), dp(8), dp(2)); draftPanel.setVisibility(View.GONE);
        HorizontalScrollView strip = new HorizontalScrollView(this); strip.setHorizontalScrollBarEnabled(false);
        draftStrip = new LinearLayout(this); strip.addView(draftStrip);
        draftPanel.addView(strip, new LinearLayout.LayoutParams(-1, dp(96)));
        LinearLayout draftInfo = new LinearLayout(this); draftInfo.setGravity(Gravity.CENTER_VERTICAL);
        draftStatus = text("正在读取图片…", 12, false); draftInfo.addView(draftStatus, new LinearLayout.LayoutParams(0, -2, 1));
        retryImage = button("重试上传", view -> retryImageUploads()); retryImage.setVisibility(View.GONE);
        draftInfo.addView(retryImage, new LinearLayout.LayoutParams(-2, dp(44)));
        draftPanel.addView(draftInfo);
        inputBox.addView(draftPanel);
        LinearLayout row = new LinearLayout(this); row.setGravity(Gravity.BOTTOM);
        View photos = iconAction("plus", "添加照片或文件", v -> attachmentMenu());
        addImage = photos;
        chatInput = input("跟小幽说…");
        chatInput.setSingleLine(false); chatInput.setMaxLines(4);
        chatInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE);
        chatInput.setTextSize(15); chatInput.setPadding(dp(16), dp(10), dp(16), dp(10));
        chatInput.setTextColor(INK); chatInput.setHintTextColor(HINT);
        chatInput.setMinHeight(dp(44)); chatInput.setMinimumHeight(dp(44));
        chatInput.setIncludeFontPadding(false);
        chatInput.setBackgroundColor(Color.TRANSPARENT);
        row.addView(chatInput, new LinearLayout.LayoutParams(0, -2, 1));
        LinearLayout send = column(); send.setGravity(Gravity.CENTER);
        send.setBackground(pressable(BLUE, 22)); send.setFocusable(true);
        send.setOnClickListener(view -> composerAction()); ctl(send, "send");
        send.setOnTouchListener(this::composerTouch);
        NavIconView arrow = new NavIconView(this, "send"); arrow.color(Color.WHITE);
        send.addView(arrow, new LinearLayout.LayoutParams(dp(22), dp(22)));
        sendButton = send; sendIcon = arrow;
        LinearLayout.LayoutParams sendParams = new LinearLayout.LayoutParams(dp(44), dp(44));
        sendParams.setMargins(dp(8), 0, 0, 0); row.addView(sendButton, sendParams);
        row.addView(addImage, Math.max(0, row.getChildCount() - 1), new LinearLayout.LayoutParams(dp(44), dp(44)));
        inputBox.addView(row); composer.addView(inputBox); page.addView(composer);
        homeBar = buildHomeBar(); page.addView(homeBar);
        chatInput.addTextChangedListener(new TextWatcher() {
            public void beforeTextChanged(CharSequence value, int start, int count, int after) { }
            public void onTextChanged(CharSequence value, int start, int before, int count) { updateSendState(); }
            public void afterTextChanged(Editable value) { }
        });

        scrim = new View(this); scrim.setBackgroundColor(0x52142032);
        scrim.setOnClickListener(view -> setDrawer(false)); ctl(scrim, "drawer-close"); scrim.setVisibility(View.GONE);
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
        TextView footerLabel = text("设置", 14, false); footerLabel.setTextColor(INK);
        footerLabel.setPadding(dp(12), 0, dp(8), 0);
        footer.addView(footerLabel, new LinearLayout.LayoutParams(0, -2, 1));
        drawerState = text("", 12, false); footer.addView(drawerState);
        footer.setOnClickListener(view -> { navigation.clear(); setDrawer(false); show(TAB_MINE); }); ctl(footer, "drawer:settings");
        drawer.addView(footer, new LinearLayout.LayoutParams(-1, -2));
        frame = new SwipeFrame();
        frame.addView(page, new FrameLayout.LayoutParams(-1, -1));
        frame.addView(scrim, new FrameLayout.LayoutParams(-1, -1));
        frame.addView(drawer, new FrameLayout.LayoutParams(dp(312), -1));
        noticeView = text("", 13, false); noticeView.setTextColor(Color.WHITE);
        noticeView.setPadding(dp(16), dp(10), dp(16), dp(10)); noticeView.setMaxWidth(dp(320));
        noticeView.setGravity(Gravity.CENTER); noticeView.setVisibility(View.GONE); noticeView.setElevation(dp(6));
        noticeView.setOnClickListener(view -> hideNotice());
        FrameLayout.LayoutParams noticeParams = new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.CENTER_HORIZONTAL);
        noticeParams.setMargins(dp(16), dp(64), dp(16), 0);
        frame.addView(noticeView, noticeParams);
        if (savedInstanceState != null) {
            tab = savedInstanceState.getInt("tab", TAB_HOME);
            currentConversation = savedInstanceState.getString("conversation", "default");
            supplementCard = savedInstanceState.getString("supplementCard");
            HubStore.get().activeConversation(currentConversation);
            RuntimeClient.Target selectedConversationTarget = RuntimeClient.selected(this);
            conversationRuntime = selectedConversationTarget == null ? "" : selectedConversationTarget.url;
            settingsSection = savedInstanceState.getInt("settingsSection", 0);
            openTask = savedInstanceState.getString("openTask");
            openAgent = savedInstanceState.getString("openAgent");
            Bundle savedDrafts = savedInstanceState.getBundle("drafts");
            if (savedDrafts != null) for (String key : savedDrafts.keySet()) drafts.put(key, savedDrafts.getString(key, ""));
        }
        if (savedInstanceState != null) {
            RuntimeClient.Target selected = RuntimeClient.selected(this);
            ArrayList<String> saved = savedInstanceState.getStringArrayList("draftImages");
            if (saved != null && selected != null && selected.url.equals(savedInstanceState.getString("draftImageRuntime"))) {
                // 已传好的接着用；没传完的留着本地图，等主人点重试。
                for (String value : saved) {
                    DraftImage draft;
                    if (value.startsWith("{")) {
                        Map<String, Object> raw = Json.parseObject(value);
                        Attachment item = raw == null ? null : Attachment.from(raw);
                        if (item == null) continue;
                        draft = new DraftImage(null); draft.item = item;
                        ArtifactClient.fetch(this, selected, item, true, (file, detail) -> {
                            if (file == null || !draftImages.contains(draft)) return;
                            draft.preview = scaledBitmap(file, 400); renderDraft();
                        });
                    } else draft = new DraftImage(Uri.parse(value));
                    draftImages.add(draft);
                }
                if (!draftImages.isEmpty()) imageTarget = selected;
                renderDraft();
            }
        }
        if (savedInstanceState != null && RuntimeClient.selected(this) != null
                && RuntimeClient.selected(this).url.equals(savedInstanceState.getString("imagePickerRuntime")))
            imagePickerTarget = RuntimeClient.selected(this);
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

    /** An ordinary hint (progress, confirmation, guidance). Recorded as a notice, not an error. */
    private void toast(String message) {
        UsageEvents.notice(currentScreen(), message);
        notice(message, false);
    }

    /** Something the owner asked for did not work. Recorded as an error. */
    private void fail(String message) {
        UsageEvents.error(currentScreen(), message);
        notice(message, true);
    }

    /**
     * 页面顶上一条小提示，代替系统 Toast：从上面滑下来，停一会儿自己收起，点一下立刻收起。
     * 出错的是红底，其余深色底；读屏会念出来。
     */
    private void notice(String message, boolean error) {
        if (noticeView == null || message == null || message.isEmpty()) return;
        noticeView.removeCallbacks(noticeHide);
        noticeView.setText(message);
        noticeView.setBackground(surface(error ? DANGER : INK, error ? DANGER : INK, 20));
        noticeView.setContentDescription(message);
        noticeView.announceForAccessibility(message);
        noticeView.animate().cancel();
        if (noticeView.getVisibility() != View.VISIBLE) {
            noticeView.setVisibility(View.VISIBLE);
            if (Motion.enabled()) {
                noticeView.setAlpha(0f); noticeView.setTranslationY(-dp(12));
                noticeView.animate().alpha(1f).translationY(0).setDuration(Motion.PAGE)
                        .setInterpolator(new android.view.animation.DecelerateInterpolator(1.6f)).start();
            }
        } else { noticeView.setAlpha(1f); noticeView.setTranslationY(0); }
        noticeView.postDelayed(noticeHide, Math.min(6000, 2200 + message.length() * 60L));
    }

    private void hideNotice() {
        if (noticeView == null || noticeView.getVisibility() != View.VISIBLE) return;
        noticeView.removeCallbacks(noticeHide);
        noticeView.animate().cancel();
        noticeView.animate().alpha(0f).translationY(-dp(12)).setDuration(Motion.QUICK)
                .withEndAction(() -> noticeView.setVisibility(View.GONE)).start();
    }

    private <T extends View> T ctl(T view, String name) {
        controls.put(view, name);
        return view;
    }

    private String currentScreen() {
        return tab == TAB_SETTINGS ? screenName(tab) + ":" + settingsName(settingsSection) : screenName(tab);
    }

    static String settingsName(int section) {
        return section == 1 ? "notifications" : section == 2 ? "developer" : "connection";
    }

    private static String screenName(int tab) {
        switch (tab) {
            case TAB_CHAT: return "chat";
            case TAB_TASKS: return "task";
            case TAB_USAGE: return "usage";
            case TAB_SETTINGS: return "settings";
            case TAB_MINE: return "mine";
            case TAB_COMPANIONS: return "companions";
            case TAB_AGENT: return "agent";
            case TAB_HOME: return "home";
            case TAB_DEVICE: return "device";
            case TAB_WORK: return "work";
            case TAB_NATIVE: return "native";
            default: return "tab" + tab;
        }
    }

    private void leaveScreen() {
        long now = System.currentTimeMillis();
        UsageEvents.screen(currentScreen(), now - screenSince);
        screenSince = now;
    }

    @Override
    public boolean dispatchTouchEvent(android.view.MotionEvent event) {
        int action = event.getActionMasked();
        if (action == android.view.MotionEvent.ACTION_DOWN) { downX = event.getRawX(); downY = event.getRawY(); }
        if (action == android.view.MotionEvent.ACTION_UP) {
            View root = getWindow().getDecorView();
            String region = UsageEvents.RageTap.region(event.getX(), event.getY(), root.getWidth(), root.getHeight());
            boolean tap = Math.hypot(event.getRawX() - downX, event.getRawY() - downY)
                    < android.view.ViewConfiguration.get(this).getScaledTouchSlop();
            String control = tap ? controlAt(root, (int) event.getRawX(), (int) event.getRawY()) : null;
            if (control != null) UsageEvents.tap(currentScreen(), control, region);
            if (rageTap != null) {
                int count = rageTap.tap(event.getX(), event.getY(), event.getEventTime());
                if (count > 0) UsageEvents.rageTap(currentScreen(), region, count, control == null ? "" : control);
            }
        }
        return super.dispatchTouchEvent(event);
    }

    /** The named control under a tap, by walking up from the deepest view there; never reads its text. */
    private String controlAt(View root, int x, int y) {
        View hit = deepestAt(root, x, y);
        for (View view = hit; view != null; view = view.getParent() instanceof View ? (View) view.getParent() : null) {
            String name = controls.get(view);
            if (name != null) return name;
            if (view.isClickable()) return "unlabeled:" + view.getClass().getSimpleName();
        }
        return "none";
    }

    private static View deepestAt(View view, int x, int y) {
        android.graphics.Rect bounds = new android.graphics.Rect();
        if (view.getVisibility() != View.VISIBLE || !view.getGlobalVisibleRect(bounds) || !bounds.contains(x, y)) return null;
        if (view instanceof ViewGroup) {
            ViewGroup group = (ViewGroup) view;
            for (int index = group.getChildCount() - 1; index >= 0; index--) {
                View found = deepestAt(group.getChildAt(index), x, y);
                if (found != null) return found;
            }
        }
        return view;
    }

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
        container.setOnClickListener(action); ctl(container, label);
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
        if (open) prepareDrawer();
        settleDrawer(open);
    }

    /** 侧边栏要露出来之前：收起键盘、画好内容、摆到屏幕左边外面。 */
    private void prepareDrawer() {
        drawer.animate().cancel(); scrim.animate().cancel();
        if (drawer.getVisibility() == View.VISIBLE) return;
        InputMethodManager keyboard = (InputMethodManager) getSystemService(INPUT_METHOD_SERVICE);
        if (keyboard != null) keyboard.hideSoftInputFromWindow(chatInput.getWindowToken(), 0);
        boolean was = drawerOpen; drawerOpen = true; drawerKey = null; refreshDrawer(); drawerOpen = was;
        drawer.setTranslationX(-dp(312)); scrim.setAlpha(0f);
        drawer.setVisibility(View.VISIBLE); scrim.setVisibility(View.VISIBLE);
    }

    /** 跟着手拖：shown 是露出来的宽度。 */
    private void dragDrawer(float shown) {
        float width = dp(312), clamped = Math.max(0, Math.min(width, shown));
        drawer.setTranslationX(clamped - width); scrim.setAlpha(clamped / width);
    }

    /** 从现在的位置滑到开或关；剩下的路越短越快。 */
    private void settleDrawer(boolean open) {
        drawerOpen = open;
        float width = dp(312);
        float left = open ? -drawer.getTranslationX() : width + drawer.getTranslationX();
        long duration = Motion.enabled() ? Math.max(120, Math.round(220 * left / width)) : 0;
        drawer.animate().cancel(); scrim.animate().cancel();
        android.view.animation.Interpolator ease = new android.view.animation.DecelerateInterpolator(1.6f);
        drawer.animate().translationX(open ? 0 : -width).setDuration(duration).setInterpolator(ease)
                .withEndAction(() -> { if (!drawerOpen) drawer.setVisibility(View.GONE); }).start();
        scrim.animate().alpha(open ? 1f : 0f).setDuration(duration).setInterpolator(ease)
                .withEndAction(() -> { if (!drawerOpen) scrim.setVisibility(View.GONE); }).start();
    }

    /**
     * 根布局：在首页、聊天、设备这几页往右划就拉出侧边栏，侧边栏开着时往左划收回去，都跟着手走，
     * 松手时看拖过一半没有、甩得快不快。竖着滑、在能横着滚的帮手胶囊上划，都不拦。
     */
    private final class SwipeFrame extends FrameLayout {
        private final int slop = android.view.ViewConfiguration.get(MainActivity.this).getScaledTouchSlop();
        private float downX, downY;
        private boolean dragging, opening, blocked;
        private android.view.VelocityTracker tracker;

        SwipeFrame() { super(MainActivity.this); }

        @Override public boolean onInterceptTouchEvent(android.view.MotionEvent event) {
            switch (event.getActionMasked()) {
                case android.view.MotionEvent.ACTION_DOWN:
                    downX = event.getX(); downY = event.getY(); dragging = false;
                    blocked = listening || (!drawerOpen && (depth(tab) != 0 || insideScroller(event)));
                    return false;
                case android.view.MotionEvent.ACTION_MOVE:
                    if (blocked || dragging) return dragging;
                    float dx = event.getX() - downX, dy = event.getY() - downY;
                    boolean sideways = Math.abs(dx) > slop * 2 && Math.abs(dx) > Math.abs(dy) * 2;
                    if (sideways && (drawerOpen ? dx < 0 : dx > 0)) {
                        dragging = true; opening = !drawerOpen; downX = event.getX();
                        prepareDrawer();
                        tracker = android.view.VelocityTracker.obtain();
                        getParent().requestDisallowInterceptTouchEvent(true);
                        return true;
                    }
                    return false;
                default:
                    return false;
            }
        }

        @Override public boolean onTouchEvent(android.view.MotionEvent event) {
            if (!dragging) return super.onTouchEvent(event);
            tracker.addMovement(event);
            float width = dp(312), moved = event.getX() - downX;
            switch (event.getActionMasked()) {
                case android.view.MotionEvent.ACTION_MOVE:
                    dragDrawer(opening ? moved : width + moved);
                    return true;
                case android.view.MotionEvent.ACTION_UP:
                case android.view.MotionEvent.ACTION_CANCEL:
                    tracker.computeCurrentVelocity(1000);
                    float speed = tracker.getXVelocity(); tracker.recycle(); tracker = null; dragging = false;
                    float shown = width + drawer.getTranslationX();
                    boolean open = Math.abs(speed) > dp(600) ? speed > 0 : shown > width / 2;
                    settleDrawer(open);
                    return true;
                default:
                    return true;
            }
        }

        private boolean insideScroller(android.view.MotionEvent event) {
            if (busyScroll == null || busyScroll.getVisibility() != View.VISIBLE || !busyScroll.canScrollHorizontally(-1)) return false;
            int[] at = new int[2], mine = new int[2];
            busyScroll.getLocationInWindow(at); getLocationInWindow(mine);
            float x = event.getX() + mine[0], y = event.getY() + mine[1];
            return x >= at[0] && x <= at[0] + busyScroll.getWidth() && y >= at[1] && y <= at[1] + busyScroll.getHeight();
        }
    }

    private String marksKey(String kind) { return kind + "_" + ArtifactDownload.key(runtimeUrl()); }

    /** Shared preferences supersede legacy local marks after their first shared revision. */
    private Set<String> marks(String kind) {
        Set<String> result = new HashSet<>(getSharedPreferences("ds_ui", MODE_PRIVATE).getStringSet(marksKey(kind), new HashSet<>()));
        for (Card card : HubStore.get().cards()) if (!card.directoryId.isEmpty()) {
            boolean value = kind.equals("pinned") ? card.sharedPinned : card.sharedArchived;
            if (value) result.add(card.id); else if (card.metadataRevision > 0) result.remove(card.id);
        }
        return result;
    }

    private void previewSharedMarks() {
        Set<String> pins=new HashSet<>(getSharedPreferences("ds_ui",MODE_PRIVATE).getStringSet(marksKey("pinned"),new HashSet<>()));
        Set<String> archives=new HashSet<>(getSharedPreferences("ds_ui",MODE_PRIVATE).getStringSet(marksKey("archived"),new HashSet<>()));
        List<Card> candidates=new ArrayList<>(); StringBuilder preview=new StringBuilder(); int conflicts=0;
        for(Card card:HubStore.get().cards())if(!card.directoryId.isEmpty()&&(pins.contains(card.id)||archives.contains(card.id))){
            if(card.metadataRevision>0){conflicts++;continue;}candidates.add(card);
            if(candidates.size()<=30)preview.append(card.title).append(pins.contains(card.id)?" · 置顶":"").append(archives.contains(card.id)?" · 归档":"").append("\n");
        }
        if(candidates.isEmpty()){android.widget.Toast.makeText(this,"没有需要同步的本机整理；已有共享设置保持原值。",android.widget.Toast.LENGTH_LONG).show();return;}
        preview.append("\n共 ").append(candidates.size()).append(" 条。共享设置已有变更的 ").append(conflicts).append(" 条保持原值。归档不会删除记录或停止任务。");
        new AlertDialog.Builder(this).setTitle("同步本机整理到其他端").setMessage(preview.toString()).setNegativeButton("取消",null).setPositiveButton("同步",(d,w)->{
            for(Card card:candidates)RuntimeClient.directoryEdit(this,card.directoryId,0,"{\"pinned\":"+pins.contains(card.id)+",\"archived\":"+archives.contains(card.id)+"}",(result,error)->{
                if(error!=null){android.widget.Toast.makeText(this,error,android.widget.Toast.LENGTH_LONG).show();return;}
                if(result!=null&&Json.string(result,"status").equals("applied")){
                    for(String kind:new String[]{"pinned","archived"}){Set<String> ids=new HashSet<>(getSharedPreferences("ds_ui",MODE_PRIVATE).getStringSet(marksKey(kind),new HashSet<>()));ids.remove(card.id);getSharedPreferences("ds_ui",MODE_PRIVATE).edit().putStringSet(marksKey(kind),ids).apply();}
                }
            });
        }).show();
    }

    private void mark(String kind, String id, boolean on) {
        Card card = CardViews.find(HubStore.get().cards(), id);
        if (card != null && !card.directoryId.isEmpty()) {
            sharedEdit(card.directoryId, card.metadataRevision, "{\"" + kind + "\":" + on + "}"); return;
        }
        Set<String> ids = marks(kind);
        if (on) ids.add(id); else ids.remove(id);
        getSharedPreferences("ds_ui", MODE_PRIVATE).edit().putStringSet(marksKey(kind), ids).apply();
        drawerKey = null; refreshDrawer();
    }

    private void sharedEdit(String id, long revision, String fields) {
        RuntimeClient.directoryEdit(this, id, revision, fields, (result, error) -> {
            if (error != null || result == null) { toast(error == null ? "保存失败" : error); return; }
            toast(Json.string(result, "status").equals("applied") ? "已保存到统一目录" : "已排队，等待来源电脑应用");
            workKey = ""; drawerKey = null;
        });
    }

    private void sharedRename(String id, long revision, String title) {
        EditText name = input("统一显示名称"); name.setText(title);
        new AlertDialog.Builder(this).setTitle("统一命名").setMessage("各端共用此显示名称；保留原生原名和真实执行目录。")
                .setView(name).setNegativeButton("取消", null).setPositiveButton("保存", (dialog, which) -> {
                    StringBuilder value = new StringBuilder(); BuddyProtocol.quote(value, name.getText().toString().trim());
                    sharedEdit(id, revision, "{\"title\":" + value + "}");
                }).show();
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

    /** 整体连接状态只有三种说法；自己的话不重复电脑名和地址。 */
    private String connectionWord() {
        return AppPresentation.connection(RuntimeClient.selected(this) != null && !HubStore.get().pairingProblem(),
                HubStore.get().runtimeConnected());
    }

    /** 主对话是一个入口、两种样子：语音（首页）和文字。记住上次用的是哪种。 */
    private boolean mainAsText;

    private void openMainChat() { mainAs(mainAsText, false); }

    /** 在主对话的两种样子之间切换；切换不算往里走一层，左上角仍是 ☰。 */
    private void mainAs(boolean text, boolean typing) {
        if (text) {
            enterConversation("default", true);
            if (!typing) return;
            chatInput.requestFocus();
            InputMethodManager keys = (InputMethodManager) getSystemService(INPUT_METHOD_SERVICE);
            if (keys != null) keys.showSoftInput(chatInput, InputMethodManager.SHOW_IMPLICIT);
        } else {
            if (listening || transcribing) cancelListening();
            navigation.clear(); setDrawer(false); show(TAB_HOME);
        }
    }

    /** 侧边栏五处：小幽（只显示状态）、主对话、在做的事、旁聊、帮手，最后是设置。内容没变时不重建。 */
    private void refreshDrawer() {
        HubStore store = HubStore.get();
        drawerState.setText(connectionWord());
        drawerState.setTextColor(store.runtimeConnected() ? GREEN : AMBER);
        if (!drawerOpen) return;
        String key = tab + ":" + currentConversation + ":" + store.runtimeConnected() + ":" + store.cards().size();
        for (Map<String, Object> item : store.conversations()) key += item.toString();
        for (Card card : store.cards()) key += card.state;
        if (key.equals(drawerKey)) return; drawerKey = key; drawerList.removeAllViews();
        LinearLayout identity = new LinearLayout(this); identity.setGravity(Gravity.CENTER_VERTICAL);
        identity.setPadding(dp(10), dp(4), dp(10), dp(10)); identity.addView(avatar(companionChoice, "happy", 36));
        TextView name = text(companionName() + "\n" + connectionWord(), 14, true);
        name.setPadding(dp(12), 0, 0, 0); identity.addView(name); drawerList.addView(identity);
        drawerList.addView(groupRow("chat", "主对话", null, "", BLUE, v -> openMainChat(), false));
        int waiting = 0, doing = 0;
        for (Card card : store.cards()) if (WorkRecords.listed(card)) {
            WorkRecords.Section section = WorkRecords.section(card, store.runtimeConnected(), System.currentTimeMillis() / 1000.0);
            if (section == WorkRecords.Section.WAITING) waiting++; else if (section == WorkRecords.Section.RUNNING) doing++;
        }
        drawerList.addView(groupRow("tasks", "在做的事", null, waiting > 0 ? String.valueOf(waiting) : doing > 0 ? String.valueOf(doing) : "",
                waiting > 0 ? AMBER : BLUE, v -> topWork(), false));
        LinearLayout side = new LinearLayout(this); side.setGravity(Gravity.CENTER_VERTICAL);
        TextView label = text("旁聊", 12, false); label.setTextColor(HINT); label.setPadding(dp(10), dp(14), 0, dp(6));
        side.addView(label, new LinearLayout.LayoutParams(0, -2, 1));
        side.addView(iconAction("plus", "新旁聊", v -> createSideChat(null)), new LinearLayout.LayoutParams(dp(44), dp(44))); drawerList.addView(side);
        List<Map<String,Object>> sideChats = new ArrayList<>(store.conversations());
        sideChats.sort((a,b) -> { int pin=Boolean.compare(Json.flag(b,"pinned"),Json.flag(a,"pinned")); return pin!=0?pin:Double.compare(Json.number(b,"updated_at"),Json.number(a,"updated_at")); });
        for (Map<String, Object> item : sideChats) {
            String id = Json.string(item, "id"); if (!AppPresentation.listedSideChat(item, tab == TAB_CHAT ? currentConversation : "")) continue;
            LinearLayout row = listRow(46, tab == TAB_CHAT && id.equals(currentConversation));
            TextView title = text(Json.string(item, "title"), 14, false); title.setSingleLine(true); title.setEllipsize(TextUtils.TruncateAt.END);
            row.addView(title, new LinearLayout.LayoutParams(0, -2, 1));
            double at = Json.number(item, "updated_at");
            TextView time = text(at > 0 ? new SimpleDateFormat("HH:mm", Locale.getDefault()).format(new Date((long) (at * 1000))) : "", 11, false);
            long read = getSharedPreferences("conversation_read", MODE_PRIVATE).getLong(runtimeUrl() + ":" + id, 0);
            if (at * 1000 > read && !(tab == TAB_CHAT && id.equals(currentConversation))) { time.setText("●"); time.setTextColor(BLUE); } else time.setTextColor(HINT);
            row.addView(time); row.setOnClickListener(v -> enterConversation(id, true));
            row.setOnLongClickListener(v -> { renameSideChat(id); return true; }); drawerList.addView(row);
        }
        drawerList.addView(drawerHeading("帮手"));
        for (AppPresentation.Helper helper : AppPresentation.helpers(store.cards(), offeredHelpers())) {
            LinearLayout row = listRow(48, false); FrameLayout face = new FrameLayout(this); face.addView(avatar(helper.role, helper.current(store.runtimeConnected()) != null ? "focus" : "idle", 30), new FrameLayout.LayoutParams(dp(30), dp(30), Gravity.CENTER));
            UsageRing ring = new UsageRing(this); Usage data = store.usage(); ring.set(data == null || !store.runtimeConnected() ? -1 : data.fiveHourLeft(helper.role, System.currentTimeMillis() / 1000L));
            face.addView(ring, new FrameLayout.LayoutParams(dp(38), dp(38), Gravity.CENTER)); row.addView(face, new LinearLayout.LayoutParams(dp(42), dp(42)));
            TextView title = text(AppPresentation.name(helper.role), 14, false); title.setPadding(dp(12), 0, 0, 0); row.addView(title, new LinearLayout.LayoutParams(0, -2, 1));
            row.addView(stateText(helper.status(store.runtimeConnected()), 12)); row.setOnClickListener(v -> { setDrawer(false); showQuota(helper.role); }); drawerList.addView(row);
        }
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
        Sheet sheet = new Sheet(this, card.title, false);
        if (!card.directoryId.isEmpty()) sheet.option("统一命名", "各端使用同一显示名称", false,
                () -> sharedRename(card.directoryId, card.metadataRevision, card.title));
        sheet.option(pinned ? "取消置顶" : "置顶", null, false, () -> mark("pinned", card.id, !pinned));
        sheet.option("归档", "从任务列表收起，可在侧边栏「已归档」找回", false, () -> {
            mark("archived", card.id, true); mark("pinned", card.id, false);
            if (card.id.equals(openTask)) show(TAB_HOME);
            toast("已归档，可在侧边栏「已归档」找回");
        });
        if (stoppable) sheet.option("停止", "正在做的帮手会停下，已经做好的部分保留", true, () -> confirmStop(card, () -> {
            stoppingCard = card.id; stoppingAt = System.currentTimeMillis(); taskKey = null; onHubChanged();
        }));
        sheet.show();
    }

    private void showArchived() {
        Set<String> archived = marks("archived");
        List<Card> found = new ArrayList<>();
        for (Card card : HubStore.get().cards()) if (archived.contains(card.id)) found.add(card);
        if (found.isEmpty()) {
            getSharedPreferences("ds_ui", MODE_PRIVATE).edit().remove(marksKey("archived")).apply();
            drawerKey = null; refreshDrawer(); return;
        }
        Sheet sheet = new Sheet(this, "点一件放回任务列表", false);
        for (Card card : found) sheet.option(card.title, AppPresentation.name(AppPresentation.owner(card)), false,
                () -> mark("archived", card.id, false));
        sheet.show();
    }

    // ---- 设置 ----

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
        boolean animate = getSharedPreferences("ds_ui", MODE_PRIVATE).getBoolean("animate", true);
        key += animate;
        if (key.equals(mineKey)) return;
        mineKey = key;
        minePane.removeAllViews();
        // 小幽配对：手机只连小幽，不选由哪台电脑回答；在哪台电脑是小幽自己安排的。
        minePane.addView(groupHeading("小幽"));
        LinearLayout links = group();
        links.addView(groupRow("chat", companionName(), target == null ? "扫电脑上的二维码，再在电脑上确认"
                        : running > 0 ? running + " 件事在做" : null,
                connectionWord(), target != null && store.runtimeConnected() ? GREEN : AMBER,
                view -> { if (target == null) scanPairing(); }, true));
        links.addView(groupRow("computer", "添加电脑", target == null ? null : "在另一台电脑上也装好小幽后扫码", "›", HINT,
                view -> scanPairing(), false));
        minePane.addView(links);
        minePane.addView(groupHeading("随身设备"));
        LinearLayout devices = group();
        devices.addView(groupRow("device", "随身设备", null, device ? "已连接 ›" : "未连接 ›",
                device ? GREEN : AMBER, view -> show(TAB_DEVICE), false));
        minePane.addView(devices);
        minePane.addView(groupHeading("偏好"));
        LinearLayout settings = group();
        settings.addView(groupRow(null, "伴侣形象", null, AppPresentation.name(companionChoice) + " ›", HINT,
                view -> { refreshCompanions(); show(TAB_COMPANIONS); }, true));
        settings.addView(groupRow(null, "形象呼吸动画", null, animate ? "开" : "关", animate ? BLUE : HINT, view -> {
            getSharedPreferences("ds_ui", MODE_PRIVATE).edit().putBoolean("animate", !animate).apply();
            refreshAvatarAnimations(getWindow().getDecorView()); mineKey = null; refreshMine();
        }, true));
        settings.addView(groupRow(null, "快速审核", null, "›", HINT,
                view -> QuickReviewDialog.show(this, this::showIndividualApprovals), true));
        settings.addView(groupRow(null, "通知", null, "›", HINT, view -> openSettings(1), true));
        String installed = "";
        try { installed = getPackageManager().getPackageInfo(getPackageName(), 0).versionName; } catch (Exception ignored) { }
        settings.addView(speechPackRow());
        settings.addView(groupRow(null, "App 更新",
                update == null ? "现在是 " + installed : update.label() + (update.note.isEmpty() ? "" : " · " + update.note),
                updateProgress >= 0 ? "下载中 " + updateProgress + "%" : update != null ? "安装 ›" : "检查 ›",
                update != null ? BLUE : HINT, view -> updateApp(), false));
        minePane.addView(settings);
        LinearLayout developer = group();
        developer.addView(groupRow(null, "开发者", null, "›", HINT, view -> openSettings(2), false));
        LinearLayout.LayoutParams gap = new LinearLayout.LayoutParams(-1, -2); gap.setMargins(0, dp(18), 0, 0);
        minePane.addView(developer, gap);
        TextView note = text("FoloToy AI Passport", 11, false); note.setTextColor(HINT);
        note.setGravity(Gravity.CENTER); note.setPadding(0, dp(28), 0, dp(14)); minePane.addView(note);
    }

    /** 添加电脑只有一种方式：扫电脑上的一次性二维码，再在电脑上确认这部手机。 */
    private void scanPairing() {
        new com.google.zxing.integration.android.IntentIntegrator(this)
                .setDesiredBarcodeFormats(java.util.Collections.singletonList("QR_CODE"))
                .setPrompt("扫描电脑小幽上的二维码，再在电脑上确认这部手机")
                .setBeepEnabled(false).setBarcodeImageEnabled(false).setOrientationLocked(false).initiateScan();
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
        new Sheet(this, "从哪里下载" + what + "？", false)
                .option("优先从网上下载", "中断时自动从电脑续传", false, () -> choice.picked(true))
                .option("从电脑下载", null, false, () -> choice.picked(false))
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
                if (error != null) fail("没问到：" + error);
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
                if (error == null) toast("手机语音模型已就绪"); else fail("没下载成：" + error);
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
            UsageEvents.update("check", "");
            AppUpdate.check(this, target, (newer, error) -> {
                if (error != null) { fail("没问到：" + error); UsageEvents.update("check_failed", ""); }
                else if (newer == null) { toast("已经是最新的了"); UsageEvents.update("up_to_date", ""); }
                else { toast("有新版 " + newer.label() + "，再点一次安装"); UsageEvents.update("available", newer.label()); }
                mineKey = null; drawerKey = null; onHubChanged();
            });
            return;
        }
        chooseSource(update.downloadUrl.isEmpty() ? null : "安装包 " + mb(update.size), external -> {
            UsageEvents.update(external ? "download_internet" : "download_computer", update.label());
            updateProgress = 0; mineKey = null; refreshMine();
            downloadApp(target, update, external);
        });
    }

    private void downloadApp(RuntimeClient.Target target, AppUpdate.Release update, boolean external) {
        AppUpdate.download(this, target, update, external, percent -> {
            updateProgress = percent; mineKey = null; refreshMine();
        }, (apk, error) -> {
            updateProgress = -1; mineKey = null; refreshMine();
            if (error != null) { fail("没下载成：" + error); UsageEvents.update("download_failed", update.label()); return; }
            if (AppUpdate.install(this, apk)) UsageEvents.update("install_prompted", update.label());
            else { toast("请允许 Pocket Hub 安装应用，回来再点一次「安装」"); UsageEvents.update("install_blocked", update.label()); }
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
                toast("有新版 App，去「设置 › App 更新」安装");
                UsageEvents.update("auto_available", newer.label());
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

    /** Settings rows with fixed App wording; any other row (task titles, computer names, logs) is recorded as "row". */
    private static final java.util.Set<String> STATIC_ROWS = new java.util.HashSet<>(java.util.Arrays.asList(
            "随身设备", "伴侣形象", "形象呼吸动画", "快速审核", "通知", "开发者", "添加电脑", "App 更新",
            "主对话", "在做的事",
            "手机语音模型", "断开设备", "连接设备", "固件", "电脑上的会话"));

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
        ctl(row, STATIC_ROWS.contains(title) ? "row:" + title : "row");
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
                chatHeader = ""; drawerKey = null;
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
        state.putString("conversation", currentConversation); state.putString("supplementCard", supplementCard);
        Bundle savedDrafts = new Bundle();
        for (Map.Entry<String, String> entry : drafts.entrySet()) savedDrafts.putString(entry.getKey(), entry.getValue());
        state.putBundle("drafts", savedDrafts);
        if (!draftImages.isEmpty() && imageTarget != null) {
            ArrayList<String> saved = new ArrayList<>();
            for (DraftImage draft : draftImages) {
                if (draft.item != null) saved.add(draft.item.json());
                else if (draft.uri != null) saved.add(draft.uri.toString());
            }
            state.putStringArrayList("draftImages", saved); state.putString("draftImageRuntime", imageTarget.url);
        }
        if (imagePickerTarget != null) state.putString("imagePickerRuntime", imagePickerTarget.url);
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
        leaveScreen();
        UsageEvents.flush(RuntimeClient.selected(this));
        HubStore.get().removeListener(this);
        uiHandler.removeCallbacks(tick);
        if (approvalDialog != null) approvalDialog.dismiss();
        if (listening || transcribing) {
            // 切走了就不算说完：丢掉这段，不发出去。
            ++voiceSession; listening = false; transcribing = false; heard = "";
            mic.cancel(); plainMic.cancel();
            micActive(false);
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
        if (imageTarget != null && !sameImageTarget(target, imageTarget)) clearImageDraft();
        if (imagePickerTarget != null && !sameImageTarget(target, imagePickerTarget)) imagePickerTarget = null;
        if (!lastRuntime.equals(runtimeUrl)) {
            // Task ids may be reused on another computer; never carry the opened target across.
            openTask = null; openAgent = null; taskKey = null; chatHeader = ""; agentKey = null; drawerKey = null;
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
        RuntimeClient.Target conversationTarget = RuntimeClient.selected(this);
        String conversationUrl = conversationTarget == null ? "" : conversationTarget.url;
        if (!conversationRuntime.equals(conversationUrl)) {
            if (listening || transcribing) cancelListening();
            conversationRuntime = conversationUrl; currentConversation = "default"; store.activeConversation("default");
            supplementCard = null; navigation.clear(); chatHeader = ""; workKey = ""; nativeKey = "";
        }
        boolean grew = refreshChat(cards, store) && tab == TAB_CHAT;
        grew |= refreshTasks(cards) && tab == TAB_TASKS;
        refreshWork(cards); refreshNative(cards);
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
        if (store.busy() && tab == TAB_CHAT) {
            // 聊天页里在想、在写、在等帮手都由最后一条回复下面的标记说，这里不再重复。
        } else if (store.busy()) {
            if (!turn.stage.isEmpty()) {
                talk.append(turn.stage).append("\n");
            }
            talk.append(!turn.agent.isEmpty() ? name + "在等 " + AppPresentation.name(turn.agent) + " 做完…"
                    : store.streaming().isEmpty() ? name + "正在想…" : name + "正在说…");
        } else if (!cards.isEmpty() && turn.phase.equals("failed") && turn.card.isEmpty()) {
            // 没能变成卡的那一句（连不上、没收到声音）：原因写在这里。
            talk.append("没发出去：").append(turn.reply);
        }
        // 交出去的事由那条回复下面的一行轻状态带过去，输入框上面不再另挂一条链接。
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

    /** 选中的一张图：一选好就开始上传，发送时只报 Runtime 已收下的编号。 */
    private static final class DraftImage {
        final Uri uri;  // 从保存的状态恢复、已经传好的图没有本地来源
        final ImageDraftState state = new ImageDraftState();
        ImageUpload.Task upload;
        Attachment item;
        Bitmap preview;
        String status = "读取中…", error;
        TextView badge;
        DraftImage(Uri uri) { this.uri = uri; }
    }
    /** Runtime 每条消息最多收 4 张（image_inputs.MAX_IMAGES）。 */
    private static final int MAX_DRAFT_IMAGES = 4;
    private final List<DraftImage> draftImages = new ArrayList<>();
    private RuntimeClient.Target imageTarget, imagePickerTarget;
    private String sendWhenUploaded;  // 还在传时按了发送：传完自动发到这个会话
    private View addImage;
    private LinearLayout draftPanel, draftStrip;
    private TextView draftStatus;
    private Button retryImage;

    private static boolean sameImageTarget(RuntimeClient.Target first, RuntimeClient.Target second) {
        return first != null && second != null && first.url.equals(second.url) && first.token.equals(second.token);
    }

    private boolean imagesUploading() {
        for (DraftImage draft : draftImages) if (draft.state.uploading()) return true;
        return false;
    }

    private boolean imagesFailed() {
        for (DraftImage draft : draftImages) if (draft.item == null && !draft.state.uploading()) return true;
        return false;
    }

    private void clearImageDraft() {
        for (DraftImage draft : draftImages) { draft.state.clear(); if (draft.upload != null) draft.upload.cancel(); }
        draftImages.clear(); imageTarget = null; sendWhenUploaded = null;
        renderDraft(); updateSendState();
    }

    private void removeDraftImage(DraftImage draft) {
        draft.state.clear(); if (draft.upload != null) draft.upload.cancel();
        draftImages.remove(draft);
        if (draftImages.isEmpty()) { clearImageDraft(); return; }
        renderDraft(); updateSendState(); sendIfUploaded();
    }

    private void renderDraft() {
        draftStrip.removeAllViews();
        for (DraftImage draft : draftImages) draftStrip.addView(draftTile(draft), new LinearLayout.LayoutParams(dp(96), dp(96)));
        draftPanel.setVisibility(tab == TAB_CHAT && !draftImages.isEmpty() ? View.VISIBLE : View.GONE);
        int ready = 0, failed = 0; String error = null;
        for (DraftImage draft : draftImages) {
            if (draft.item != null) ready++;
            else if (!draft.state.uploading()) { failed++; if (error == null) error = draft.error; }
        }
        boolean many = draftImages.size() > 1;
        String status;
        if (failed > 0) status = (many ? failed + " 张" : "") + "上传失败 · " + (error == null ? "请重试" : error);
        else if (ready < draftImages.size()) status = sendWhenUploaded != null ? "传完就发…" : "上传中 · 可以先写说明";
        else status = (many ? ready + " 张" : "") + "已就绪 · 可添加说明后发送";
        draftStatus.setText(status); draftStatus.setTextColor(failed > 0 ? DANGER : HINT);
        retryImage.setVisibility(failed > 0 ? View.VISIBLE : View.GONE);
    }

    private View draftTile(DraftImage draft) {
        FrameLayout tile = new FrameLayout(this);
        ImageView image = new ImageView(this); image.setScaleType(ImageView.ScaleType.FIT_CENTER);
        image.setBackground(surface(0xfff3f5f8, 0xffe6ebf2, 12));
        if (draft.preview != null) image.setImageBitmap(draft.preview);
        image.setContentDescription("预览所选图片"); image.setOnClickListener(view -> previewDraft(draft));
        tile.addView(image, new FrameLayout.LayoutParams(dp(80), dp(80), Gravity.START | Gravity.BOTTOM));
        draft.badge = null;
        if (draft.item == null) {
            boolean uploading = draft.state.uploading();
            TextView badge = text(uploading ? draft.status : "失败", 10, false);
            badge.setTextColor(Color.WHITE); badge.setGravity(Gravity.CENTER);
            badge.setBackgroundColor(uploading ? 0x99000000 : DANGER);
            tile.addView(badge, new FrameLayout.LayoutParams(dp(80), -2, Gravity.START | Gravity.BOTTOM));
            if (uploading) draft.badge = badge;
        }
        FrameLayout remove = new FrameLayout(this);
        NavIconView removeIcon = new NavIconView(this, "close"); removeIcon.color(INK);
        remove.addView(removeIcon, new FrameLayout.LayoutParams(dp(18), dp(18), Gravity.CENTER));
        remove.setFocusable(true);
        remove.setBackground(pressable(0xffe9edf3, 22)); remove.setContentDescription("移除这张图片");
        remove.setOnClickListener(view -> removeDraftImage(draft)); ctl(remove, "image-remove");
        tile.addView(remove, new FrameLayout.LayoutParams(dp(44), dp(44), Gravity.END | Gravity.TOP));
        return tile;
    }

    private void previewDraft(DraftImage draft) {
        if (draft.item != null) { openAttachment(draft.item, imageTarget); return; }
        if (draft.preview == null) return;
        ImageView image = new ImageView(this); image.setScaleType(ImageView.ScaleType.FIT_CENTER);
        image.setImageBitmap(draft.preview);
        LinearLayout previewContent = column();
        previewContent.addView(image, new LinearLayout.LayoutParams(-1, 0, 1));
        Dialog preview = new Dialog(this); preview.setContentView(previewContent);
        previewContent.addView(button("关闭", close -> preview.dismiss())); preview.show(); preview.getWindow().setLayout(-1, -1);
    }

    private void chooseImage() {
        if (tab != TAB_CHAT) { toast("请在主对话添加图片，任务续聊暂不支持带图"); return; }
        imagePickerTarget = RuntimeClient.selected(this);
        if (imagePickerTarget == null) { toast("先连接电脑，再添加图片"); return; }
        if (imageTarget != null && !sameImageTarget(imagePickerTarget, imageTarget)) clearImageDraft();
        int room = MAX_DRAFT_IMAGES - draftImages.size();
        if (room <= 0) { toast("一条消息最多带 " + MAX_DRAFT_IMAGES + " 张图片"); imagePickerTarget = null; return; }
        Intent picker;
        if (Build.VERSION.SDK_INT >= 33) {
            picker = new Intent("android.provider.action.PICK_IMAGES").setType("image/*");
            // 只剩一个位置时不能带这个参数（要求大于 1），照片选择器就是单选。
            if (room > 1) picker.putExtra("android.provider.extra.PICK_IMAGES_MAX", room);
        } else picker = documentPicker();
        try { startActivityForResult(picker, 54); }
        catch (android.content.ActivityNotFoundException unavailable) { startActivityForResult(documentPicker(), 54); }
    }

    private static Intent documentPicker() {
        return new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("image/*")
                .putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
    }

    private void addPickedImages(List<Uri> picked, RuntimeClient.Target target) {
        int room = MAX_DRAFT_IMAGES - draftImages.size();
        if (picked.size() > room) toast("一条消息最多带 " + MAX_DRAFT_IMAGES + " 张，多出的没加上");
        imageTarget = target;
        for (Uri uri : picked.subList(0, Math.max(0, Math.min(room, picked.size())))) {
            DraftImage draft = new DraftImage(uri); draftImages.add(draft);
            startImageUpload(draft);
        }
        renderDraft(); updateSendState();
    }

    private void retryImageUploads() {
        for (DraftImage draft : new ArrayList<>(draftImages))
            if (draft.item == null && !draft.state.uploading()) {
                if (draft.uri == null) removeDraftImage(draft); else startImageUpload(draft);
            }
        renderDraft(); updateSendState();
    }

    private void startImageUpload(DraftImage draft) {
        if (draft.uri == null || imageTarget == null || draft.state.uploading()) return;
        RuntimeClient.Target target = imageTarget;
        if (!sameImageTarget(RuntimeClient.selected(this), target)) { clearImageDraft(); toast("电脑已切换，请重新选图"); return; }
        long selection = draft.state.begin();
        draft.item = null; draft.error = null; draft.status = "读取中…";
        draft.upload = ImageUpload.start(this, draft.uri, target, new ImageUpload.Callback() {
            private boolean current() {
                return draft.state.accepts(selection) && !isFinishing() && !isDestroyed()
                        && sameImageTarget(RuntimeClient.selected(MainActivity.this), target);
            }
            public void preview(Bitmap bitmap) {
                if (!current()) { bitmap.recycle(); return; }
                draft.preview = bitmap; renderDraft();
            }
            public void progress(String status) {
                if (!current()) return;
                draft.status = status.replace("正在等待电脑确认…", "确认中…").replace("正在优化照片…", "压缩中…");
                if (draft.badge != null) draft.badge.setText(draft.status);
            }
            public void ready(Attachment item, String error) {
                if (!current()) return;
                draft.state.finish(selection); draft.upload = null;
                draft.item = item; draft.error = item == null ? error : null;
                renderDraft(); updateSendState(); sendIfUploaded();
            }
        });
    }

    /** 按发送时还有图在传：等这批都传完再发，期间换了会话或页面就不替主人发了。 */
    private void sendIfUploaded() {
        if (sendWhenUploaded == null || imagesUploading()) return;
        String conversation = sendWhenUploaded; sendWhenUploaded = null;
        if (imagesFailed()) { renderDraft(); toast("有图片没传上，消息还没发出"); return; }
        if (tab == TAB_CHAT && conversation.equals(currentConversation)) sendChat();
        else renderDraft();
    }

    private void sendChat() {
        String message = chatInput.getText().toString().trim();
        if (tab == TAB_CHAT && imagesFailed()) { toast("请先重试上传，或移除没传上的图片"); return; }
        boolean withImage = !draftImages.isEmpty() && tab == TAB_CHAT;
        if (message.isEmpty() && !withImage) return;
        if (withImage && supplementCard != null) { toast("任务补充暂不支持图片，请先取消任务关联再发图"); return; }
        if (withImage) {
            RuntimeClient.Target selected = RuntimeClient.selected(this);
            if (!sameImageTarget(selected, imageTarget)) { toast("电脑已切换，请移除图片后重新选择"); return; }
            if (imagesUploading()) { sendWhenUploaded = currentConversation; renderDraft(); return; }
            List<Attachment> images = new ArrayList<>();
            for (DraftImage draft : draftImages) images.add(draft.item);
            RuntimeClient.sendChat(this, message, currentConversation, null, images);
            clearImageDraft();
            chatInput.setText(""); drafts.remove(composerContext); updateSendState(); scrollDown(); return;
        }
        if (RuntimeClient.selected(this) == null) { toast("需要配对：去「设置」添加电脑"); return; }
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
        // 交给谁、用哪一档都由小幽安排；想指定就直接说出来。在电脑上的会话里说的也先给小幽
        // （Runtime 0.10.1 起）：点名能接它的帮手，或者小幽判断要它接着干活，才接着那个会话做。
        RuntimeClient.sendChat(this, message, currentConversation, supplementCard, null);
        supplementCard = null; supplementHint.setVisibility(View.GONE);
        performHaptic(false);
        scrollDown();
    }

    // ---- Places: chat, one task, one helper, and the utility pages under 设置 ----

    private void show(int next) {
        if (next != tab) leaveScreen();
        saveUiContext();
        if (next == TAB_TASKS && openTask == null) next = TAB_HOME;
        if (next == TAB_AGENT && openAgent == null) next = TAB_HOME;
        if (next != tab) transition(tab, next);
        tab = next;
        taskKey = null; agentKey = null; drawerKey = null; mineKey = null;
        if (next == TAB_MINE) refreshMine();
        applyTab(); onHubChanged();
        if (next == TAB_HOME) { pokeLine = null; mainAsText = false; }
        restoreScroll(next == TAB_CHAT || next == TAB_TASKS);
    }

    private void openSettings(int section) {
        if (tab == TAB_SETTINGS && section != settingsSection) leaveScreen();
        saveUiContext(); settingsSection = section; show(TAB_SETTINGS);
    }

    /** All task entries lead to the same source-scoped task detail. */
    private void open(String card) {
        saveUiContext();
        if (tab != TAB_TASKS || !card.equals(openTask)) rememberPage();
        if (tab != TAB_TASKS || !card.equals(openTask)) transition(tab, TAB_TASKS);
        openTask = card; tab = TAB_TASKS;
        taskKey = null; drawerKey = null; applyTab(); onHubChanged(); restoreScroll(true);
    }

    private void openAgent(String role) {
        saveUiContext();
        if (!AppPresentation.role(role).equals(openAgent)) agentDesktopOpen = false;
        if (tab != TAB_AGENT || !AppPresentation.role(role).equals(openAgent)) transition(tab, TAB_AGENT);
        openAgent = AppPresentation.role(role); tab = TAB_AGENT;
        agentKey = null; drawerKey = null; applyTab(); onHubChanged(); restoreScroll(false);
    }

    /** 页面有三层：主对话（语音和文字）、旁聊、在做的事；一件事、一位帮手、设置；设置下面的子页。往里走从右边推进来。 */
    private static int depth(int screen) {
        if (screen == TAB_TASKS || screen == TAB_AGENT || screen == TAB_MINE || screen == TAB_NATIVE) return 1;
        if (screen == TAB_DEVICE) return 2;
        if (screen == TAB_COMPANIONS || screen == TAB_USAGE || screen == TAB_SETTINGS) return 2;
        return 0;
    }

    private void transition(int from, int to) {
        if (scroll == null) return;
        int direction = Integer.compare(depth(to), depth(from));
        Motion.enter(scroll, direction > 0 ? Motion.FORWARD : direction < 0 ? Motion.BACK : Motion.SAME);
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

    private String scrollContext() { return runtimeUrl() + "\n" + currentConversation + ":" + tab + ":" + openTask + ":" + openAgent + ":" + settingsSection; }

    private void saveUiContext() {
        if (chatInput != null && !composerContext.isEmpty()) drafts.put(composerContext, chatInput.getText().toString());
        if (scroll != null) scrollPositions.put(scrollContext(), scroll.getScrollY());
    }

    private void restoreScroll(boolean latest) {
        Integer y = scrollPositions.get(scrollContext());
        scroll.post(() -> scroll.scrollTo(0, y == null ? (latest ? body.getHeight() : 0) : y));
    }

    private void applyTab() {
        workPane.setVisibility(tab == TAB_WORK ? View.VISIBLE : View.GONE);
        nativePane.setVisibility(tab == TAB_NATIVE ? View.VISIBLE : View.GONE);
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
        connectionSettingsPane.setVisibility(View.GONE);
        notificationSettingsPane.setVisibility(settingsSection == 1 ? View.VISIBLE : View.GONE);
        preferenceSettingsPane.setVisibility(settingsSection != 1 ? View.VISIBLE : View.GONE);
        boolean inTask = tab == TAB_TASKS && openTask != null;
        Card opened = inTask ? CardViews.find(HubStore.get().cards(), openTask) : null;
        boolean toHelper = tab == TAB_AGENT && agentName(openAgent) != null;
        // 别处上报来的会话（不在任何一台电脑上）只能看，不给输入框。
        boolean reported = opened != null && opened.source.equals("external");
        composer.setVisibility(tab == TAB_CHAT ? View.VISIBLE : View.GONE);
        addImage.setVisibility(tab == TAB_CHAT ? View.VISIBLE : View.GONE);
        draftPanel.setVisibility(tab == TAB_CHAT && !draftImages.isEmpty() ? View.VISIBLE : View.GONE);
        String nextContext = currentConversation + ":" + AppPresentation.draftKey(runtimeUrl(),
                inTask ? openTask : toHelper ? "agent:" + openAgent : null, companionChoice);
        if (!nextContext.equals(composerContext)) {
            if (!composerContext.isEmpty()) drafts.put(composerContext, chatInput.getText().toString());
            composerContext = nextContext; chatInput.setText(drafts.getOrDefault(nextContext, ""));
        }
        if (!(micFromComposer && (listening || transcribing))) chatInput.setHint(inTask && opened != null && opened.monitoringOnly ? "问" + companionName() + "这个会话…"
                : inTask ? "补充要求…" : toHelper ? "直接交给 " + AppPresentation.name(openAgent) + "…"
                : "跟" + companionName() + "说…");
        updateHeader(); updateSendState();
    }

    private void updateHeader() {
        boolean chat = tab == TAB_CHAT;
        lead.setVisibility(chat ? View.VISIBLE : View.GONE);
        Card opened = tab == TAB_TASKS ? CardViews.find(HubStore.get().cards(), openTask) : null;
        moreButton.setVisibility(opened != null ? View.VISIBLE : View.GONE);
        voiceButton.setVisibility(chat && currentConversation.equals("default") && supplementCard == null ? View.VISIBLE : View.GONE);
        devicePill.setVisibility(tab == TAB_HOME ? View.VISIBLE : View.GONE);
        String title = tab == TAB_HOME ? "" : tab == TAB_DEVICE ? "随身小幽" : chat ? "对话记录"
                : opened != null ? opened.title
                : tab == TAB_AGENT ? "" : tab == TAB_MINE ? "设置"
                : tab == TAB_COMPANIONS ? "伴侣形象" : tab == TAB_USAGE ? "用量"
                : settingsSection == 1 ? "通知" : "开发者";
        if (tab == TAB_CHAT) title = conversationTitle(currentConversation);
        if (tab == TAB_WORK) title = "在做的事";
        if (tab == TAB_NATIVE) title = "原会话 · 只读";
        if (navigationButton instanceof ViewGroup) {
            NavIconView glyph = (NavIconView) ((ViewGroup) navigationButton).getChildAt(0);
            glyph.icon(topLevelScreen() ? "menu" : "back");
            navigationButton.setContentDescription(topLevelScreen() ? "打开侧边栏" : "返回上一层");
        }
        if (tab == TAB_CHAT && supplementCard != null) chatInput.setHint("补充这件事的要求…");
        if (supplementHint != null) supplementHint.setVisibility(tab == TAB_CHAT && supplementCard != null ? View.VISIBLE : View.GONE);
        connectionTitle.setText(title);
    }

    /**
     * 输入框右边的按钮：没字时是淡的箭头；有字变成实心的发送；小幽在回话、或这件事在做时变成停止；
     * 点了停止、还没停下时转圈。样子变了才换，换的时候缩一下再弹回来。
     */
    private void updateSendState() {
        if (sendButton == null) return;
        HubStore store = HubStore.get();
        Card stoppable = stoppableCard(store);
        if (stoppingCard != null) {
            Card stopping = CardViews.find(store.cards(), stoppingCard);
            // 停下了，或者等了 20 秒还没回音（请求没送到）：不再转圈。
            if (stopping == null || !stopping.active() || System.currentTimeMillis() - stoppingAt > 20000) stoppingCard = null;
        }
        boolean hasText = !chatInput.getText().toString().trim().isEmpty() || (!draftImages.isEmpty() && tab == TAB_CHAT);
        String next = AppPresentation.composerState(hasText, tab == TAB_CHAT ? store.busyIn(currentConversation) : store.busy(), stoppable != null, stoppingCard != null);
        if (tab == TAB_CHAT && imagesFailed()) next = AppPresentation.COMPOSER_IDLE;
        // 聊天页没字时这个按钮就是「按住说话」；别的页说的话要归到那件事，语音还做不到，仍是箭头。
        if (next.equals(AppPresentation.COMPOSER_IDLE) && tab == TAB_CHAT && draftImages.isEmpty()) next = COMPOSER_VOICE;
        if (micFromComposer && (listening || transcribing)) return;
        if (next.equals(composerShown)) return;
        boolean first = composerShown.isEmpty();
        boolean bigChange = !(composerShown.equals(AppPresentation.COMPOSER_IDLE) && next.equals(AppPresentation.COMPOSER_READY)
                || composerShown.equals(AppPresentation.COMPOSER_READY) && next.equals(AppPresentation.COMPOSER_IDLE));
        String shown = next;
        composerShown = next;
        Runnable apply = () -> paintSendButton(shown);
        if (first || !bigChange) apply.run(); else Motion.swap(sendButton, apply);
        if (stoppingCard != null) sendButton.postDelayed(this::updateSendState, 1000);
    }

    private void paintSendButton(String state) {
        if (state.equals(COMPOSER_VOICE)) {
            if (sendSpin != null) { sendSpin.cancel(); sendSpin = null; }
            sendIcon.setRotation(0);
            sendButton.setBackground(pressable(0xffe9edf3, 22));
            sendIcon.icon("mic"); sendIcon.color(INK);
            sendButton.setEnabled(true); sendButton.setContentDescription("按住说话");
            return;
        }
        boolean ready = state.equals(AppPresentation.COMPOSER_READY);
        boolean stop = state.equals(AppPresentation.COMPOSER_STOP);
        boolean waiting = state.equals(AppPresentation.COMPOSER_WAIT) || state.equals(AppPresentation.COMPOSER_STOPPING);
        int fill = ready ? BLUE : stop ? INK : 0xffe9edf3;
        sendButton.setBackground(pressable(fill, 22));
        sendIcon.icon(stop ? "stop" : waiting ? "spinner" : "send");
        sendIcon.color(ready || stop ? Color.WHITE : 0xff8796a8);
        if (sendSpin != null) { sendSpin.cancel(); sendSpin = null; }
        sendIcon.setRotation(0);
        if (waiting && Motion.enabled()) {
            sendSpin = android.animation.ObjectAnimator.ofFloat(sendIcon, "rotation", 0f, 360f);
            sendSpin.setDuration(900); sendSpin.setRepeatCount(android.animation.ValueAnimator.INFINITE);
            sendSpin.setInterpolator(new android.view.animation.LinearInterpolator()); sendSpin.start();
        }
        sendButton.setEnabled(ready || stop);
        sendButton.setContentDescription(ready ? "发送消息" : stop ? "停止"
                : state.equals(AppPresentation.COMPOSER_STOPPING) ? "正在停止"
                : waiting ? companionName() + "正在回上一句，请稍候" : "先输入要说的话");
    }

    /** 现在能从输入框停下的那件事：任务页里是这件正在做的事，聊天里是这一轮小幽交办出去、还在做的事。 */
    private Card stoppableCard(HubStore store) {
        Card card = null;
        if (tab == TAB_TASKS) card = CardViews.find(store.cards(), openTask);
        else if (store.busyIn(currentConversation)) card = CardViews.find(store.cards(), store.turn().card);
        if (card != null && !card.conversation.equals(currentConversation)) card = null;
        return card != null && card.active() && !card.monitoringOnly ? card : null;
    }

    /** 聊天页输入框没字时：按住这个按钮说话，松开发出去，滑出按钮外取消（和首页大按钮一样）。 */
    private boolean composerTouch(View view, android.view.MotionEvent event) {
        boolean voice = composerShown.equals(COMPOSER_VOICE) || micFromComposer && listening;
        if (!voice) return false;
        switch (event.getActionMasked()) {
            case android.view.MotionEvent.ACTION_DOWN:
                view.getParent().requestDisallowInterceptTouchEvent(true);
                if (!listening) micFromComposer = true;
                micPressedAt = android.os.SystemClock.uptimeMillis();
                startListening(); return true;
            case android.view.MotionEvent.ACTION_UP:
                view.getParent().requestDisallowInterceptTouchEvent(false);
                boolean inside = event.getX() >= 0 && event.getY() >= 0 && event.getX() <= view.getWidth() && event.getY() <= view.getHeight();
                // 点一下就松开录不到话：不发、说清楚怎么用，而不是什么都不发生。
                boolean tapped = android.os.SystemClock.uptimeMillis() - micPressedAt < 400;
                if (inside && !tapped) stopListening();
                else { cancelListening(); if (tapped) toast("按住说话，松开发送；要打字点左边的输入框"); }
                return true;
            case android.view.MotionEvent.ACTION_CANCEL:
                view.getParent().requestDisallowInterceptTouchEvent(false);
                cancelListening(); return true;
            default: return true;
        }
    }

    private void composerAction() {
        if (composerShown.equals(AppPresentation.COMPOSER_STOP)) {
            Card card = stoppableCard(HubStore.get());
            if (card != null) confirmStop(card, () -> {
                stoppingCard = card.id; stoppingAt = System.currentTimeMillis(); updateSendState();
            });
            return;
        }
        sendChat();
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
        } else if (!navigation.isEmpty()) {
            String[] previous = navigation.pop(); openTask = previous[1].isEmpty() ? null : previous[1];
            currentConversation = previous[2]; HubStore.get().activeConversation(currentConversation);
            chatHeader = ""; show(Integer.parseInt(previous[0]));
        } else if (tab == TAB_COMPANIONS || tab == TAB_USAGE || tab == TAB_SETTINGS || tab == TAB_DEVICE) {
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
            runtimeStatus.setText(problem); fail(problem);
            return;
        }
        boolean encryptedPair = pairingInput.getText().toString().trim().startsWith("xiaoyou://");
        pairingInput.setText("");
        runtimeStatus.setText(encryptedPair ? "等待电脑确认配对，已有电脑连接保持可用" : "已保存连接串");
        toast(encryptedPair ? "请在电脑上确认这部手机" : "已保存连接串");
        HubStore.get().log(encryptedPair ? "已请求加密配对" : "已保存连接串");
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
        QuickReviewDialog.show(this, this::showIndividualApprovals, true);
    }

    private void showIndividualApprovals() {
        List<Card.Approval> pending = HubStore.get().approvals();
        if (pending.isEmpty()) { toast("目前没有待确认的操作"); return; }
        if (pending.size() == 1) { showApproval(pending.get(0).id); return; }
        Sheet sheet = new Sheet(this, "待确认的操作", false);
        for (Card.Approval item : pending)
            sheet.option(AppPresentation.name(item.agent) + " · " + item.tool, "来源电脑 · " + item.runtimeName, false,
                    () -> showApproval(item.id));
        sheet.show();
    }

    /**
     * Main conversation messages stay in chronological order, including hand-offs and summaries.
     * Only the visible window is built. Each message is one row: new rows are appended with a short
     * rise, a row whose look changed is swapped in place, and streaming updates the existing text
     * view. Only a moved window, a removed row or a new order rebuilds the list (ChatPlan).
     */
    private boolean refreshChat(List<Card> all, HubStore store) {
        List<ChatHistory.Message> messages = store.mainMessages();
        // 窗口的起点钉在一条消息上：新消息来了窗口只往下长，不往前挪（挪一格就是整页重画、闪一下）。
        // 长出很多（多 160 条）以后才在下一次重画时收回到最近 chatWindow 条。
        int first = -1;
        if (chatAnchor != null) for (int index = 0; index < messages.size(); index++)
            if (messages.get(index).id.equals(chatAnchor)) { first = index; break; }
        if (first < 0 || messages.size() - first > chatWindow + 160) {
            first = Math.max(0, messages.size() - chatWindow);
            chatAnchor = messages.isEmpty() ? null : messages.get(first).id;
        }
        boolean loading = messages.isEmpty() && store.catchingUp();
        String header = currentConversation + ":" + companionChoice + ":" + chatAnchor + ":" + (first > 0) + ":" + messages.isEmpty() + ":" + loading;
        // 一件事的卡只挂在它最新的那条回复下面：交出去时那句话下面有卡，结果出来卡就跟着挪到结果下面，
        // 不在每条相关的话下面各挂一张。
        chatReceipts.clear();
        Set<String> carded = new HashSet<>();
        for (int index = messages.size() - 1; index >= first; index--) {
            ChatHistory.Message message = messages.get(index);
            if (!message.role.equals("you") && !message.card.isEmpty() && carded.add(message.card))
                chatReceipts.add(rowKey(message));
        }
        List<String> ids = new ArrayList<>(), looks = new ArrayList<>();
        for (int index = first; index < messages.size(); index++) {
            ChatHistory.Message message = messages.get(index);
            ids.add(rowKey(message)); looks.add(chatLook(message, CardViews.find(all, message.card), store));
        }
        ChatPlan plan = ChatPlan.of(chatHeader, chatIds, chatLooks, header, ids, looks);
        // 第一次同步时历史一页一页地到，中间的消息会插进来：先不重画，拉完再一次画好。
        if (plan.rebuild && store.catchingUp() && !chatRows.isEmpty()) return false;
        boolean changed = !plan.unchanged(ids.size());
        // 换掉的行（比如卡的状态变了、卡挪到了后面那条）里的话早就在屏幕上了：重新填进去时不再淡入一遍，
        // 不然第二个气泡要隔 450ms 才出来，看着像那句话没了又冒出来。
        Set<String> redrawn = new HashSet<>();
        if (plan.rebuild) {
            chatHeader = header; chatList.removeAllViews(); chatWords.clear(); chatStatus.clear(); chatRows.clear(); chatGroups.clear();
            if (first > 0) chatList.addView(button("查看更早的对话", view -> {
                int y = scroll.getScrollY(), height = body.getHeight();
                chatWindow += 80; chatAnchor = null; chatHeader = ""; refreshChat(HubStore.get().cards(), HubStore.get());
                scroll.post(() -> scroll.scrollTo(0, y + body.getHeight() - height));
            }));
            if (loading) chatList.addView(chatSkeleton());
            else if (messages.isEmpty()) chatList.addView(emptyState(currentConversation.equals("default") ? "陪你聊，也帮你安排" : "想聊什么直接说",
                    currentConversation.equals("default") ? "说说你今天的想法，或交给小幽一件事。" : "标题会按你的第一句话自动起。"));
            for (Map<String, Object> item : store.conversations()) if (currentConversation.equals(Json.string(item, "id")) && item.get("context") instanceof Map) {
                Map<String, Object> context = Json.object(item.get("context"));
                chatList.addView(text("引用背景 · 来自" + conversationTitle(Json.string(context, "conversation")) + "\n" + Json.string(context, "text"), 13, false));
            }
            chatHeaderViews = chatList.getChildCount();
            for (int index = first; index < messages.size(); index++) {
                ChatHistory.Message message = messages.get(index);
                View row = chatRow(message, CardViews.find(all, message.card));
                chatRows.add(row); chatList.addView(row);
            }
            chatList.addView(markerRow());
        } else {
            for (int at : plan.replace) {
                ChatHistory.Message message = messages.get(first + at);
                View row = chatRow(message, CardViews.find(all, message.card));
                chatList.removeView(chatRows.get(at)); chatList.addView(row, chatHeaderViews + at);
                chatRows.set(at, row); redrawn.add(ids.get(at));
                // 「正在想」变成第一句话的那一刻：气泡淡进来，而不是突然冒出来。
                if (tab == TAB_CHAT && chatLooks.get(at).charAt(0) == '-' && looks.get(at).charAt(0) == 'B') Motion.appear(row);
            }
            for (int index = first + plan.appendFrom; index < messages.size(); index++) {
                ChatHistory.Message message = messages.get(index);
                View row = chatRow(message, CardViews.find(all, message.card));
                chatRows.add(row); chatList.addView(row, chatHeaderViews + chatRows.size() - 1);
                // 你发的那句从右下角弹出来；她的话由 setBubbles 一条条弹。
                TextView words = chatWords.get(rowKey(message));
                if (tab == TAB_CHAT && words != null) Motion.pop((View) words.getParent(), true, 0);
            }
        }
        chatIds = ids; chatLooks = looks;
        boolean animate = !plan.rebuild && tab == TAB_CHAT;
        for (int index = first; index < messages.size(); index++) {
            ChatHistory.Message message = messages.get(index);
            LinearLayout group = chatGroups.get(rowKey(message));
            if (group != null) {
                changed |= setBubbles(group, shownBubbles(message), animate && !redrawn.contains(rowKey(message)), message.presentation);
                changed |= showStatus(chatStatus.get(rowKey(message)), message.status.equals("thinking") ? "" : statusText(message));
                continue;
            }
            TextView words = chatWords.get(rowKey(message));
            String shown = message.role.equals("you") || !message.status.equals("streaming") ? messageText(message) : message.text;
            if (words != null && !shown.contentEquals(words.getText())) { words.setText(shown); changed = true; }
            String status = message.status.equals("thinking") ? "" : statusText(message);
            changed |= showStatus(chatStatus.get(rowKey(message)), status);
        }
        changed |= refreshMarker();
        return changed;
    }

    /** 第一次拉历史时的占位：几条灰色气泡一明一暗地闪，拉完换成真的对话。 */
    private View chatSkeleton() {
        LinearLayout box = column(); box.setPadding(0, dp(12), 0, 0);
        box.setContentDescription("正在同步对话记录");
        int[][] shapes = {{0, 200, 44}, {1, 150, 36}, {0, 240, 64}, {1, 120, 36}, {0, 180, 44}};
        for (int[] shape : shapes) {
            View bar = new View(this);
            bar.setBackground(surface(shape[0] == 1 ? 0xffdfe7f4 : 0xffe9edf3, shape[0] == 1 ? 0xffdfe7f4 : 0xffe9edf3, 18));
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(dp(shape[1]), dp(shape[2]));
            params.gravity = shape[0] == 1 ? Gravity.END : Gravity.START; params.setMargins(0, 0, 0, dp(12));
            box.addView(bar, params);
        }
        if (Motion.enabled()) {
            android.animation.ObjectAnimator shimmer = android.animation.ObjectAnimator.ofFloat(box, "alpha", 1f, .45f);
            shimmer.setDuration(800); shimmer.setRepeatMode(android.animation.ValueAnimator.REVERSE);
            shimmer.setRepeatCount(android.animation.ValueAnimator.INFINITE); shimmer.start();
            box.addOnAttachStateChangeListener(new View.OnAttachStateChangeListener() {
                @Override public void onViewAttachedToWindow(View view) { }
                @Override public void onViewDetachedFromWindow(View view) { shimmer.cancel(); }
            });
        }
        return box;
    }

    /** 她这条回复现在该露出来的几条：还在写时只露写完的（见 AppPresentation.settledBubbles）。 */
    private List<String> shownBubbles(ChatHistory.Message message) {
        if (message.presentation != null) {
            if (message.presentation.supported) return message.presentation.blocks();
            return message.status.equals("streaming") || message.text.isEmpty() ? java.util.Collections.emptyList()
                    : java.util.Collections.singletonList(message.text);
        }
        if (message.status.equals("streaming")) return AppPresentation.settledBubbles(message.text, false);
        return AppPresentation.bubbles(messageText(message));
    }

    /**
     * 把小幽的一条回复画成几个气泡（她单独写一行 --- 另起一条）。已有的气泡只改字；新多出来的
     * 整条从左上角弹出来，几条同时到时一条接一条地弹，中间隔一小会儿，像对方在接着发。
     * 返回是否变了。
     */
    private boolean setBubbles(LinearLayout group, List<String> parts, boolean animate, Presentation presentation) {
        int had = group.getChildCount();
        boolean changed = false;
        while (group.getChildCount() > parts.size()) { group.removeViewAt(group.getChildCount() - 1); changed = true; }
        for (int index = 0; index < parts.size(); index++) {
            if (index < had) {
                TextView words = (TextView) ((ViewGroup) group.getChildAt(index)).getChildAt(0);
                if (!parts.get(index).equals(words.getTag())) {
                    words.setTag(parts.get(index)); words.setText(presentation != null && presentation.supported
                            ? Markdown.projected(presentation,index) : Markdown.render(parts.get(index), dp(16))); changed = true;
                }
                continue;
            }
            LinearLayout bubble = bubbleShell(false);
            TextView words = bubbleWords(false, ""); words.setTextIsSelectable(true);
            words.setOnClickListener(v -> { if (group.getTag() instanceof String) showMessageActions((String) group.getTag(), words.getText()); });
            words.setTag(parts.get(index)); words.setText(presentation != null && presentation.supported
                    ? Markdown.projected(presentation,index) : Markdown.render(parts.get(index), dp(16)));
            bubble.addView(words); group.addView(bubble, bubbleParams(false)); changed = true;
            if (animate) Motion.pop(bubble, false, (index - had) * 380L);
        }
        return changed;
    }

    /**
     * 聊天列表里一条消息的编号：发出去以后，聊天记录里的编号会先换成 Runtime 给的、同步到了又换成
     * 记录里的；用一直不变的那个（App 发出时起的 clientId），列表才认得出还是同一条——不然新回复
     * 找不到自己的气泡，第一句话的回复要退出再进来才看得到。
     */
    private static String rowKey(ChatHistory.Message message) {
        if (message.clientId == null || message.clientId.isEmpty()) return message.id;
        return message.clientId + (message.role.equals("you") ? ":you" : ":reply");
    }

    /** 一行消息的样子：换了才重画这一行（字的变化不算，直接改字）。 */
    private String chatLook(ChatHistory.Message message, Card card, HubStore store) {
        // 第一个字符记有没有气泡：换行时只有气泡刚出现才做淡入。小幽的话气泡在行里自己长（G），
        // 从“在想”到“开始说”不用换掉整行。
        StringBuilder look = new StringBuilder().append(!message.role.equals("you") ? 'G' : hasBubble(message) ? 'B' : '-')
                .append(message.status.equals("failed"));
        if (message.presentation != null) look.append(message.presentation.author());
        if (card != null && AppPresentation.dispatched(card) && chatReceipts.contains(rowKey(message)))
            look.append(card.title).append(AppPresentation.stateWord(card, store.runtimeConnected())).append(card.resultReady);
        for (Attachment item : message.attachments) look.append(item.id).append(item.status);
        return look.toString();
    }

    private View chatRow(ChatHistory.Message message, Card card) {
        LinearLayout row = column();
        boolean mine = message.role.equals("you");
        if (!mine) {
            if (message.presentation != null && !message.presentation.author().isEmpty()) {
                TextView author = text(message.presentation.author(), 12, false);
                author.setTextColor(HINT); row.addView(author);
            }
            // 小幽的话像聊天那样一条一个气泡；写完一条弹出一条（setBubbles）。
            LinearLayout group = column(); group.setTag(message.id); chatGroups.put(rowKey(message), group); row.addView(group);
        } else if (hasBubble(message)) {
            LinearLayout bubble = bubbleShell(mine);
            TextView words = bubbleWords(mine, ""); words.setTextIsSelectable(true);
            words.setOnClickListener(v -> showMessageActions(message.id));
            bubble.addView(words); chatWords.put(rowKey(message), words);
            row.addView(bubble, bubbleParams(mine));
        }
        for (Attachment item : message.attachments) row.addView(attachmentView(item));
        TextView caption = statusCaption(mine);
        row.addView(caption, captionParams(mine)); chatStatus.put(rowKey(message), caption);
        if (!mine && card != null && card.outstanding() && AppPresentation.dispatched(card) && chatReceipts.contains(rowKey(message)))
            row.addView(receipt(card));
        if (message.status.equals("failed") && mine && !message.synced) {
            row.addView(button("重试发送", view -> {
                if (!message.attachments.isEmpty()) RuntimeClient.retryImages(this, message.id, message.text, message.attachments);
                else RuntimeClient.retry(this, message.id, message.text, message.scope.equals("task") ? message.card : null);
            }));
        }
        row.setTag(message.id); row.setFocusable(true);
        if (message.id.equals(highlightMessage)) row.setBackground(surface(0xffe9f1ff, 0xffe9f1ff, 12));
        row.setOnClickListener(v -> showMessageActions(message.id));
        return row;
    }

    private View markerRow() {
        if (markerRow == null) {
            markerRow = new LinearLayout(this); markerRow.setGravity(Gravity.CENTER_VERTICAL);
            markerRow.setPadding(0, dp(5), dp(40), dp(6));
            marker = new ReplyMarker(this, BLUE);
            markerRow.addView(marker, new LinearLayout.LayoutParams(dp(54), dp(34)));
            markerLabel = text("", 13, false); markerLabel.setTextColor(HINT);
            markerLabel.setTypeface(Typeface.defaultFromStyle(Typeface.ITALIC));
            markerLabel.setPadding(dp(8), 0, 0, 0);
            markerRow.addView(markerLabel, new LinearLayout.LayoutParams(0, -2, 1));
            replyActions = new LinearLayout(this); replyActions.setGravity(Gravity.CENTER_VERTICAL);
            replyActions.addView(replyAction("copy", "复制这条回复", this::copyLastReply));
            replyActions.addView(replyAction("retry", "把上一句再问一次", this::askAgain));
            replyActions.setVisibility(View.GONE);
            markerRow.addView(replyActions);
            marker.state(ReplyMarker.HIDDEN);
        }
        if (markerRow.getParent() instanceof ViewGroup) ((ViewGroup) markerRow.getParent()).removeView(markerRow);
        return markerRow;
    }

    private View replyAction(String icon, String label, Runnable action) {
        LinearLayout box = column(); box.setGravity(Gravity.CENTER);
        box.setBackground(pressable(Color.TRANSPARENT, 16)); box.setContentDescription(label); box.setFocusable(true);
        box.setOnClickListener(view -> { performHaptic(false); action.run(); }); ctl(box, "reply:" + icon);
        NavIconView glyph = new NavIconView(this, icon); glyph.color(0xff8796a8);
        box.addView(glyph, new LinearLayout.LayoutParams(dp(18), dp(18)));
        box.setLayoutParams(new LinearLayout.LayoutParams(dp(36), dp(36)));
        return box;
    }

    /** 最后一条说完的回复。 */
    private ChatHistory.Message lastReply() {
        List<ChatHistory.Message> messages = HubStore.get().mainMessages();
        for (int index = messages.size() - 1; index >= 0; index--) {
            ChatHistory.Message message = messages.get(index);
            if (!message.role.equals("you") && message.status.equals("done") && !message.text.isEmpty()) return message;
        }
        return null;
    }

    private void copyLastReply() {
        ChatHistory.Message reply = lastReply();
        android.content.ClipboardManager clipboard = (android.content.ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
        if (reply == null || clipboard == null) return;
        clipboard.setPrimaryClip(android.content.ClipData.newPlainText(companionName() + "的回复", Presentation.plain(reply.presentation, reply.text)));
        toast("已复制");
    }

    /** 把最后那条回复之前你说的那句原样再发一次，让小幽重新回答。语音发的那句没有原文，不重问。 */
    private void askAgain() {
        List<ChatHistory.Message> messages = HubStore.get().mainMessages();
        for (int index = messages.size() - 1; index >= 0; index--) {
            ChatHistory.Message message = messages.get(index);
            if (!message.role.equals("you")) continue;
            if (message.text.isEmpty() || message.text.equals(RuntimeClient.VOICE_PLACEHOLDER)) break;
            if (HubStore.get().busy()) { toast(companionName() + "还在回上一句"); return; }
            RuntimeClient.sendChat(this, message.text, currentConversation, null, null);
            scrollDown(); return;
        }
        toast("没有可以重问的那句话");
    }

    /** 轻震一下：strong 用在按住说话开始、审批这类「确定了」的时刻，其余是轻触。 */
    private void performHaptic(boolean strong) {
        if (frame == null) return;
        int kind = strong ? android.view.HapticFeedbackConstants.LONG_PRESS
                : android.view.HapticFeedbackConstants.KEYBOARD_TAP;
        if (strong && android.os.Build.VERSION.SDK_INT >= 30) kind = android.view.HapticFeedbackConstants.CONFIRM;
        frame.performHapticFeedback(kind);
    }

    /**
     * 最后一条回复下面的标记：在想、在写都是“正在输入”的三个点，等了 8 秒以上旁边写一句在干什么；
     * 说完了换成一个淡淡的星芒。返回是否变了。
     */
    private boolean refreshMarker() {
        if (marker == null) return false;
        HubStore store = HubStore.get();
        List<ChatHistory.Message> messages = store.mainMessages();
        ChatHistory.Message last = messages.isEmpty() ? null : messages.get(messages.size() - 1);
        String state = ReplyMarker.HIDDEN, label = "";
        if (last != null && !last.role.equals("you")) {
            if (last.status.equals("thinking")) {
                state = ReplyMarker.TYPING;
                double waited = System.currentTimeMillis() / 1000.0 - last.at;
                HubStore.Turn turn = store.turn();
                if (!turn.agent.isEmpty()) label = "等 " + AppPresentation.name(turn.agent) + " 做完…";
                else if (waited >= 8) label = turn.stage.isEmpty() ? "还在想，稍等…" : turn.stage;
            } else if (last.status.equals("streaming")) state = ReplyMarker.TYPING;
            else if (last.status.equals("done")) state = ReplyMarker.RESTING;
        } else if (last != null && last.status.equals("sending")) state = ReplyMarker.TYPING;
        boolean changed = !state.equals(marker.state()) || !label.contentEquals(markerLabel.getText());
        marker.state(state);
        markerRow.setVisibility(state.equals(ReplyMarker.HIDDEN) ? View.GONE : View.VISIBLE);
        if (!label.contentEquals(markerLabel.getText())) {
            markerLabel.setText(label);
            if (!label.isEmpty() && tab == TAB_CHAT) Motion.appear(markerLabel);
        }
        markerLabel.setVisibility(label.isEmpty() ? View.GONE : View.VISIBLE);
        boolean resting = state.equals(ReplyMarker.RESTING) && !store.busy();
        if (resting != (replyActions.getVisibility() == View.VISIBLE)) {
            replyActions.setVisibility(resting ? View.VISIBLE : View.GONE);
            if (resting && tab == TAB_CHAT) Motion.appear(replyActions);
        }
        // 在想的时候每秒看一眼，到 8 秒时换上那句话。
        markerRow.removeCallbacks(markerTick);
        if (state.equals(ReplyMarker.TYPING) && label.isEmpty()) markerRow.postDelayed(markerTick, 1000);
        return changed;
    }

    /** 气泡里只放说的话；发送中、正在想这类状态放在气泡下面的小字里（statusText）。 */
    private String messageText(ChatHistory.Message message) {
        if (message.role.equals("you")) return message.text;
        if (message.status.equals("interrupted") && message.text.isEmpty()) return "回复尚未完成";
        return message.text;
    }

    /** 还在想、或者还没有一条写完的回复不画空气泡，只留下面那个“正在输入”。 */
    private static boolean hasBubble(ChatHistory.Message message) {
        if (message.role.equals("you")) return true;
        if (message.presentation != null && message.presentation.supported) return !message.presentation.blocks().isEmpty();
        if (message.status.equals("streaming")) return !AppPresentation.settledBubbles(message.text, false).isEmpty();
        return !(message.status.equals("thinking") && message.text.isEmpty());
    }

    private String statusText(ChatHistory.Message message) {
        if (message.presentation != null && !message.status.equals("interrupted"))
            return message.presentation.complete() && !message.status.equals("failed")
                    && !message.presentation.value("status").equals("cancelled") ? "" : message.presentation.label();
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
        LinearLayout row = new LinearLayout(this); row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(dp(4), dp(5), dp(8), dp(5));
        row.addView(avatar(AppPresentation.owner(card), "idle", 22));
        TextView label = stateText(card.state.equals("waiting") ? "等你确认" : HubStore.get().runtimeConnected() ? "进行中" : "连不上", 12);
        label.setPadding(dp(8), 0, dp(12), 0); row.addView(label);
        NavIconView next = new NavIconView(this, "chevron"); next.color(HINT); row.addView(next, new LinearLayout.LayoutParams(dp(16), dp(16)));
        row.setOnClickListener(v -> open(card.id)); row.setFocusable(true); row.setContentDescription(card.title + "，查看进展");
        return row;
    }

    /** 任务页：上面是这件事经过的几步，下面是这件事里的对话。重建了返回 true。 */
    /**
     * 任务页分三段，各自比较、各自更新：上面的步骤和执行记录（这件事的状态变了才重画）、
     * 中间的对话（ChatPlan：新的一条接在后面浮上来，旧的不动）、最下面还在发送或回复中的话
     * （流式时只改字，末尾是和聊天页一样的状态标记）。整页只在换了一件事时重画。
     */
    private boolean refreshTasks(List<Card> cards) {
        if (tab != TAB_TASKS) return false;
        Card opened = CardViews.find(cards, openTask); if (opened == null) return false;
        String key = opened.id + ":" + opened.seq + ":" + HubStore.get().runtimeConnected() + ":" + progressAge(opened);
        if (key.equals(taskKey)) return false;
        taskKey = key; taskPane.removeAllViews(); fillWorkDetail(taskPane, opened, HubStore.get().runtimeConnected()); return true;
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
        pill.setOnClickListener(view -> { rememberPage(); show(TAB_DEVICE); }); pill.setFocusable(true); ctl(pill, "device-pill");
        return pill;
    }

    private LinearLayout buildHome() {
        LinearLayout pane = column(); pane.setGravity(Gravity.CENTER_HORIZONTAL);
        stage = new CompanionStage(this, 210);
        stage.setOnClickListener(view -> poke()); stage.setFocusable(true); ctl(stage, "home-avatar");
        stage.setContentDescription("摸摸" + companionName());
        LinearLayout.LayoutParams stageParams = new LinearLayout.LayoutParams(-2, -2);
        stageParams.setMargins(0, dp(8), 0, dp(4));
        pane.addView(stage, stageParams);
        homeSpeech = text("", 16, false); homeSpeech.setTextColor(INK);
        homeSpeech.setGravity(Gravity.CENTER); homeSpeech.setLineSpacing(dp(4), 1f);
        homeSpeech.setPadding(dp(18), dp(14), dp(18), dp(14));
        homeSpeech.setBackground(surface(Color.WHITE, Color.WHITE, 20));
        homeSpeech.setMaxLines(8); homeSpeech.setEllipsize(TextUtils.TruncateAt.END);
        homeSpeech.setOnClickListener(view -> mainAs(true, false)); ctl(homeSpeech, "home-speech");
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
        View keyboard = roundIcon("keyboard", "打字", view -> mainAs(true, true));
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
                    if (!listening) micFromComposer = false;
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
        micButton = button; ctl(button, "mic");
        middle.addView(button, new LinearLayout.LayoutParams(dp(84), dp(84)));
        micHint = text("按住说话", 12, false); micHint.setPadding(0, dp(8), 0, 0);
        micHint.setMaxWidth(dp(260)); micHint.setMaxLines(3); micHint.setGravity(Gravity.CENTER);
        middle.addView(micHint, new LinearLayout.LayoutParams(-2, -2));
        bar.addView(middle, new LinearLayout.LayoutParams(0, -2, 1));
        bar.addView(roundIcon("plus", "添加照片或文件", view -> attachmentMenu()), side);
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
        if (transcribing) { toast("上一句还在转成文字，稍等一下"); return; }  // 她在回话时也能按住插话
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            micSay("请先允许麦克风权限");
            requestPermissions(new String[] {Manifest.permission.RECORD_AUDIO}, 4); return;
        }
        android.media.AudioManager audio = (android.media.AudioManager) getSystemService(AUDIO_SERVICE);
        if (audio != null && audio.isMicrophoneMute()) {
            micSay("系统麦克风已关闭"); toast("请开启系统麦克风开关后，再按住说话"); return;
        }
        voiceConversation = micFromComposer ? currentConversation : "default";
        voiceSupplement = micFromComposer ? supplementCard : null;
        final long session = ++voiceSession;
        heard = ""; listening = true; pokeLine = null;
        micActive(true);
        micSay("正在打开手机麦克风…");
        if (!AppPacks.ready(this, AppPacks.SPEECH, AppPacks.SPEECH_FILES)) { recordForComputer(session); return; }
        final byte[][] recorded = new byte[1][];
        mic.start(this, new OnDeviceSpeech.Callback() {
            @Override public void recording(byte[] wav) { if (session == voiceSession) recorded[0] = wav; }
            @Override public void ready() {
                if (session != voiceSession || !listening) return;
                micSay("手机正在录音 · 松开发送文字");
                refreshHome(HubStore.get(), HubStore.get().cards());
            }
            @Override public void level(float value) { if (session == voiceSession) micLevel(value); }
            @Override public void partial(String text) {
                if (session != voiceSession) return;
                heard = text; refreshHome(HubStore.get(), HubStore.get().cards());
                if (micFromComposer && !text.isEmpty()) chatInput.setHint(text);
            }
            @Override public void ended() {
                if (session == voiceSession && listening) micSay("已录满 30 秒 · 松开发送文字");
            }
            @Override public void finished(String text, String error) {
                if (session != voiceSession || isFinishing() || isDestroyed()) return;
                releaseMic(); transcribing = false;
                byte[] wav = recorded[0]; recorded[0] = null;
                boolean online = HubStore.get().runtimeConnected();
                if (wav != null && online && (voiceOnComputer() || error != null)) {
                    // 开了“交给电脑识别”，或者手机没认出来：把这段录音交给电脑，用和设备录音同一个识别。
                    HubStore.get().log(error == null ? "手机录音交给电脑识别" : "手机识别没成功（" + error + "），交给电脑识别");
                    RuntimeClient.sendVoiceChat(MainActivity.this, wav, voiceConversation, voiceSupplement);
                } else if (error != null) {
                    micSay(error); HubStore.get().log(error); fail(error);
                } else if (text != null && !text.isEmpty()) {
                    HubStore.get().log("手机已完成离线转录，仅发送文字");
                    RuntimeClient.sendChat(MainActivity.this, text, voiceConversation, voiceSupplement, null);
                } else {
                    // 没认出字也没出错（太短、太轻）：原来这里什么都不做，看起来像发出去了没回音。
                    micSay("没听清，再按住说一次"); toast("没听清，什么都没发出去，再按住说一次");
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
                if (session == voiceSession && listening) micSay("手机正在录音 · 松开交给电脑识别");
            }); }
            @Override public void level(float value) { runOnUiThread(() -> { if (session == voiceSession) micLevel(value); }); }
            @Override public void samples(byte[] bytes, int count) { }
            @Override public void limitReached() { runOnUiThread(() -> {
                if (session == voiceSession && listening) micSay("已录满 30 秒 · 松开交给电脑识别");
            }); }
            @Override public void finished(byte[] wav, String error, boolean cancelled) { runOnUiThread(() -> {
                if (session != voiceSession || isFinishing() || isDestroyed() || cancelled) return;
                releaseMic(); transcribing = false;
                if (error != null) { micSay(error); fail(error); }
                else if (wav != null && HubStore.get().runtimeConnected()) {
                    HubStore.get().log("手机语音模型还没下载，录音交给电脑识别");
                    RuntimeClient.sendVoiceChat(MainActivity.this, wav, voiceConversation, voiceSupplement);
                } else {
                    String why = "连不上小幽，手机语音模型也还没下载：去「设置 › 手机语音模型」下载后可离线识别";
                    micSay(why); fail(why);
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
        micActive(false);
    }

    /**
     * 按住说话的那个按钮变红放大或者复原。从聊天页输入框按的，变的是输入框右边那个按钮，
     * 提示写在输入框里；从首页按的，变的是首页的大按钮。
     */
    private void micActive(boolean on) {
        if (micFromComposer) {
            if (on) {
                sendButton.setBackground(surface(0xffe2564a, 0xffe2564a, 22));
                sendIcon.icon("mic"); sendIcon.color(Color.WHITE);
                sendButton.animate().scaleX(1.1f).scaleY(1.1f).setDuration(Motion.QUICK).start();
                performHaptic(true);
            } else {
                sendButton.animate().scaleX(1f).scaleY(1f).setDuration(Motion.QUICK).start();
                composerShown = ""; updateSendState(); applyTab();
            }
            micHint.setText("按住说话");
            return;
        }
        if (on) {
            micButton.setBackground(surface(0xffe2564a, 0xffe2564a, 42));
            micButton.animate().scaleX(1.12f).scaleY(1.12f).setDuration(150).start();
            performHaptic(true);
        } else {
            micButton.setBackground(surface(BLUE, BLUE, 42));
            micButton.animate().scaleX(1f).scaleY(1f).setDuration(150).start();
            micHint.setText("按住说话");
        }
    }

    /** 录音时的一句提示：首页写在大按钮下面，聊天页写在输入框里。 */
    private void micSay(String line) {
        micHint.setText(line);
        if (micFromComposer && chatInput != null) chatInput.setHint(line);
    }

    private void micLevel(float level) {
        if (!listening) return;
        View target = micFromComposer ? sendButton : micButton;
        float scale = (micFromComposer ? 1.1f : 1.12f) + .08f * level; target.setScaleX(scale); target.setScaleY(scale);
    }

    private void stopListening() {
        if (!listening) return;
        performHaptic(false);
        releaseMic(); transcribing = true;
        boolean computer = plainMic.recording() || voiceOnComputer() && HubStore.get().runtimeConnected();
        micSay(computer ? "正在交给电脑识别…" : "正在手机上转成文字…");
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
        List<ChatHistory.Message> messages = store.mainMessages("default");
        for (int index = messages.size() - 1; index >= 0; index--) {
            ChatHistory.Message message = messages.get(index);
            if (message.role.equals("you")) continue;
            String plain = message.presentation == null ? null : message.presentation.plain();
            if (plain != null && !plain.isEmpty()) return plain;
            if (!message.text.isEmpty()) return Markdown.plain(message.text);
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

        String streaming = store.streamingFor("default");
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
            // 首页也不再一个字一个字地出：写完的那几条先出来，没有就是“…”。
            List<String> said = AppPresentation.settledBubbles(streaming, false);
            state = "talking"; mood = "happy"; line = said.isEmpty() ? "…" : Markdown.plain(String.join("\n---\n", said));
        } else if (store.busyIn("default")) {
            boolean waiting = !turn.agent.isEmpty();
            state = waiting ? "working" : "thinking"; mood = waiting ? "focus" : "think";
            line = !turn.stage.isEmpty() ? turn.stage : "正在回应…";
        } else if (pokeLine != null) {
            state = "happy"; mood = "happy"; line = pokeLine;
        } else if (!latestMainReply(store).isEmpty()) {
            state = "idle"; mood = "happy"; line = CardViews.oneLine(HomeMessage.display(latestMainReply(store)), 180);
        } else if (turn.phase.equals("failed")) {
            state = "idle"; mood = "idle"; line = "刚才那句没发出去：" + CardViews.oneLine(HomeMessage.display(Markdown.plain(turn.reply)), 60);
        } else if (turn.phase.equals("done") && !turn.reply.isEmpty()) {
            state = "idle"; mood = "happy"; line = CardViews.oneLine(HomeMessage.display(Markdown.plain(turn.reply)), 180);
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
        if (pendingLetter != null && streaming.isEmpty() && (!store.busyIn("default") || !turn.agent.isEmpty())) {
            final String role = pendingLetter; pendingLetter = null;
            busyRow.post(() -> flyLetter(role));
        }
        refreshHelperRow(roster, connected);

        if (homeActions.getChildCount() == 0 || (target == null) != (homeActions.getTag() != null)) {
            homeActions.removeAllViews(); homeActions.setTag(target == null ? "setup" : null);
            if (target == null) {
                Button setup = button("添加电脑", view -> scanPairing());
                setup.setBackground(pressable(BLUE, 24)); setup.setTextColor(Color.WHITE);
                homeActions.addView(setup, new LinearLayout.LayoutParams(dp(200), dp(48)));

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
            face.setContentDescription(AppPresentation.name(role) + " 的用量详情");
            face.setOnClickListener(click -> showQuota(role)); ctl(face, "usage-face");
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
                else showQuota(role);
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

    private void showQuota(String role) {
        HubStore current = HubStore.get();
        Usage data = current.usage();
        String details = data == null ? "还没有收到用量数据。"
                : data.windowSummary(role, System.currentTimeMillis() / 1000L, java.util.TimeZone.getDefault());
        if (!current.runtimeConnected() && data != null) details += "\n连不上小幽，以上是上次收到的数据。";
        new Sheet(this, AppPresentation.name(role) + " 的用量", false)
                .message(details).action("刷新用量", false, this::fetchUsage).show();
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
            chip.quota(usage == null || !connected ? -1 : usage.fiveHourLeft(helper.role, now / 1000L));
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
        list.addView(groupRow(null, "固件", null, "开发者 ›", HINT, view -> openSettings(2), false));
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
        for (DraftImage draft : draftImages) { draft.state.clear(); if (draft.upload != null) draft.upload.cancel(); }
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
            else { pendingApproval = approval; toast("正在核实来源审批，请稍候"); }
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

    private void confirmStop(Card card) { confirmStop(card, null); }

    /** stopped：点了「停止」、请求发出去之后调用（输入框按钮借它进入「停止中」）。 */
    private void confirmStop(Card card, Runnable stopped) {
        Card latest = CardViews.find(HubStore.get().cards(), card.id);
        if (latest == null || latest.monitoringOnly || !latest.active()) return;
        RuntimeClient.Target target = RuntimeClient.selected(this);
        String runtime = target == null ? "" : target.url;
        long epoch = HubStore.get().cardsEpoch();
        new AlertDialog.Builder(this).setTitle("停止「" + card.title + "」？").setMessage("正在做的帮手会停下，已经做好的部分保留。")
                .setNegativeButton("继续", null).setPositiveButton("停止", (dialog, which) -> {
                    RuntimeClient.Target current = RuntimeClient.selected(this);
                    if (current != null && current.url.equals(runtime) && epoch == HubStore.get().cardsEpoch()) {
                        RuntimeClient.cancel(this, card.id);
                        if (stopped != null) stopped.run();
                    }
                }).show();
    }

    private void showApproval(String id) {
        Card.Approval approval = HubStore.get().approval(id);
        if (approval == null) { toast("这次请求已经处理或撤回"); return; }
        if (approvalDialog != null) approvalDialog.dismiss();
        Sheet dialog = new Sheet(this, "确认这次操作", true); approvalDialog = dialog;
        LinearLayout page = column();
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
        dialog.add(page); page.setLayoutParams(new LinearLayout.LayoutParams(-1, -1)); dialog.show();
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
            if (problem == null) { performHaptic(true); dialog.dismiss(); toast(allow ? "已允许本次操作" : "已拒绝本次操作"); }
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

    private static String activityLabel(Card.Entry event) {
        switch (event.kind) {
            case "dispatch": return "开始执行";
            case "supplement_received": return "收到补充";
            case "relay_queued": return "等待转交补充";
            case "relayed": return "补充已转交";
            case "tool_start": return "开始操作";
            case "tool_result": return "操作结果";
            case "completed": return "本轮结束";
            case "failed": return "执行失败";
            case "cancelled": return "本轮停止";
            default: return "进展说明";
        }
    }

    private String activityTitle(Card.Entry event) {
        String at = new java.text.SimpleDateFormat("MM-dd HH:mm:ss", java.util.Locale.CHINA)
                .format(new java.util.Date((long) (event.at * 1000)));
        return at + " · " + AppPresentation.name(event.author) + " · " + activityLabel(event);
    }

    private void activityDetail(Card.Entry event) {
        new Sheet(this, activityTitle(event), false).message(event.text).show();
    }

    private void showActivity(Card card, int page) {
        int start = page * 50, end = Math.min(card.activity.size(), start + 50);
        Sheet sheet = new Sheet(this, "执行记录 · 第 " + (page + 1) + " 页（新到旧）", false);
        if (start > 0) sheet.action("较新记录", false, () -> showActivity(card, page - 1));
        for (int index = start; index < end; index++) {
            Card.Entry event = card.activity.get(card.activity.size() - 1 - index);
            sheet.option(activityTitle(event), CardViews.oneLine(event.text, 100), false, () -> activityDetail(event));
        }
        if (end < card.activity.size()) sheet.action("更早记录", false, () -> showActivity(card, page + 1));
        sheet.show();
    }

    /** 你说的靠右、蓝底；对方说的靠左、白底。只有说话的不是小幽时才写名字。返回画过的附件编号。 */
    private Set<String> addBubble(LinearLayout parent, Card card, Card.Entry entry) {
        Set<String> rendered = new HashSet<>();
        boolean mine = entry.role.equals("you");
        List<String> parts = entry.presentation != null && entry.presentation.supported ? entry.presentation.blocks()
                : java.util.Collections.singletonList(entry.text);
        if (parts.isEmpty()) {
            for (Attachment item : entry.attachments) { parent.addView(attachmentView(item)); rendered.add(item.id); }
        }
        for (int partIndex = 0; partIndex < parts.size(); partIndex++) {
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
            TextView name = text(entry.presentation != null && entry.presentation.supported ? entry.presentation.author()
                    : AppPresentation.name(author) + (card.monitoringOnly ? " · 电脑" : ""), 12, true);
            name.setPadding(dp(6), 0, 0, 0); heading.addView(name);
            bubble.addView(heading);
        }
        String said = parts.get(partIndex);
        if (entry.presentation == null && said.length() > 16000) said = said.substring(0, 16000) + "\n\n较长内容已折叠，请打开完整方案附件。";
        // 任务里的对话一条一个气泡：小幽分成几条的，段落之间空一行；加粗和列表照样画。
        TextView words = bubbleWords(mine, "");
        words.setText(entry.presentation != null && entry.presentation.supported ? Markdown.projected(entry.presentation,partIndex)
                : mine ? said : Markdown.render(entry.presentation != null ? said : String.join("\n\n", Markdown.messages(said)), dp(16)));
        words.setTextIsSelectable(true); bubble.addView(words);
        if (partIndex == parts.size() - 1)
            for (Attachment item : entry.attachments) { bubble.addView(attachmentView(item)); rendered.add(item.id); }
        parent.addView(bubble, bubbleParams(mine));
        }
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
        try {
            Bitmap bitmap = BitmapFactory.decodeFile(file.getAbsolutePath(), options);
            if (bitmap == null) return null;
            try {
                int orientation = new android.media.ExifInterface(file.getAbsolutePath()).getAttributeInt(
                        android.media.ExifInterface.TAG_ORIENTATION, android.media.ExifInterface.ORIENTATION_NORMAL);
                android.graphics.Matrix transform = new android.graphics.Matrix();
                switch (orientation) {
                    case 2: transform.setScale(-1, 1); break;
                    case 3: transform.setRotate(180); break;
                    case 4: transform.setScale(1, -1); break;
                    case 5: transform.setRotate(90); transform.postScale(-1, 1); break;
                    case 6: transform.setRotate(90); break;
                    case 7: transform.setRotate(270); transform.postScale(-1, 1); break;
                    case 8: transform.setRotate(270); break;
                    default: return bitmap;
                }
                Bitmap corrected = Bitmap.createBitmap(bitmap, 0, 0, bitmap.getWidth(), bitmap.getHeight(), transform, true);
                if (corrected != bitmap) bitmap.recycle();
                return corrected;
            } catch (java.io.IOException ignored) { return bitmap; }
        } catch (OutOfMemoryError exhausted) { return null; }
    }

    private void openAttachment(Attachment item, RuntimeClient.Target target) {
        long epoch = HubStore.get().cardsEpoch();
        toast("正在打开附件…");
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
            } else new Sheet(this, item.label(), false)
                    .option("用手机应用打开", null, false, () -> externalAttachment(file, item))
                    .option("保存到手机", null, false, () -> saveAttachment(file, item)).show();
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
        com.google.zxing.integration.android.IntentResult scanned = com.google.zxing.integration.android.IntentIntegrator.parseActivityResult(requestCode, resultCode, data);
        if (scanned != null) {
            if (scanned.getContents() != null) {
                String link = scanned.getContents().trim();
                if (!link.startsWith("xiaoyou://join#")) { fail("这不是小幽的二维码，请扫电脑小幽「添加手机」里的二维码"); return; }
                pairingInput.setText(link); savePairing();
            }
            return;
        }
        if (requestCode == 54) {
            RuntimeClient.Target target = imagePickerTarget; imagePickerTarget = null;
            if (resultCode == RESULT_OK && data != null && target != null) {
                if (!sameImageTarget(RuntimeClient.selected(this), target)) { toast("电脑已切换，请重新选图"); return; }
                List<Uri> picked = new ArrayList<>();
                android.content.ClipData clip = data.getClipData();
                if (clip != null) {
                    for (int i = 0; i < clip.getItemCount(); i++)
                        if (clip.getItemAt(i).getUri() != null) picked.add(clip.getItemAt(i).getUri());
                } else if (data.getData() != null) picked.add(data.getData());
                for (Uri selected : picked) {
                    try { getContentResolver().takePersistableUriPermission(selected, Intent.FLAG_GRANT_READ_URI_PERMISSION); }
                    catch (SecurityException ignored) { /* Some photo providers grant access only for this session. */ }
                }
                if (!picked.isEmpty()) addPickedImages(picked, target);
            }
        }
        if (requestCode == 3 && resultCode == RESULT_OK && data != null && data.getData() != null && exportFile != null) {
            ArtifactClient.save(this, exportFile, data.getData(), (file, error) -> {
                if (file == null) fail("保存失败：" + error); else toast("已保存到所选位置");
            });
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
                ? "还没有登记电脑。正常添加请用「设置 › 添加电脑」扫码。"
                : "这里列出的是线路，平时由 App 自己选。点一下强制改用这条，长按删除。");
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
            micSay(allowed ? "按住说话" : "麦克风权限未允许");
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
        ctl(button, "button:" + UsageEvents.controlName(label));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.setMargins(0, dp(5), 0, dp(5));
        button.setLayoutParams(params);
        return button;
    }

    private EditText input(String hint) {
        EditText view = new EditText(this);
        view.setHint(hint);
        view.setSingleLine(true);
        view.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        return view;
    }

    private String currentConversation = "default";
    private String conversationRuntime = "";
    private String voiceConversation = "default", voiceSupplement;
    private String supplementCard;
    private String highlightMessage;
    private LinearLayout workPane, nativePane;
    private TextView supplementHint;
    private View navigationButton, voiceButton;
    private boolean showCompletedWork;
    private String workKey = "", nativeKey = "";
    private final java.util.ArrayDeque<String[]> navigation = new java.util.ArrayDeque<>();

    private String conversationTitle(String id) {
        if ("default".equals(id)) return "主对话";
        for (Map<String, Object> item : HubStore.get().conversations())
            if (id.equals(Json.string(item, "id"))) return Json.string(item, "title");
        return "旁聊";
    }

    private void enterConversation(String id, boolean top) {
        if (listening || transcribing) cancelListening();
        saveUiContext();
        if (top) navigation.clear(); else rememberPage();
        currentConversation = id; HubStore.get().activeConversation(id);
        if (id.equals("default")) mainAsText = true;
        getSharedPreferences("conversation_read", MODE_PRIVATE).edit().putLong(runtimeUrl() + ":" + id, System.currentTimeMillis()).apply();
        chatAnchor = null; chatHeader = ""; supplementCard = null;
        setDrawer(false); show(TAB_CHAT);
    }

    private boolean topLevelScreen() { return navigation.isEmpty() && (tab == TAB_HOME || tab == TAB_CHAT || tab == TAB_WORK || tab == TAB_MINE); }

    private void rememberPage() {
        navigation.push(new String[]{String.valueOf(tab), openTask == null ? "" : openTask, currentConversation});
    }

    private void renameSideChat(String id) {
        for (Map<String, Object> item : HubStore.get().conversations()) if (id.equals(Json.string(item, "id")) && !Json.string(item, "directory_id").isEmpty()) {
            String key = Json.string(item, "directory_id"); long revision = (long) Json.number(item, "metadata_revision");
            new Sheet(this, conversationTitle(id), false)
                .option("统一命名", null, false, () -> sharedRename(key, revision, conversationTitle(id)))
                .option(Json.flag(item, "pinned") ? "取消置顶" : "置顶", null, false, () -> sharedEdit(key, revision, "{\"pinned\":" + !Json.flag(item, "pinned") + "}"))
                .option("归档", "保留全部记录，可从归档入口恢复", false, () -> sharedEdit(key, revision, "{\"archived\":true}"))
                .option("查看共享历史", null, false, () -> SharedHistoryPane.show(this, key, conversationTitle(id))).show();
            return;
        }
        EditText name = input("旁聊名称"); name.setText(conversationTitle(id));
        new AlertDialog.Builder(this).setTitle("重命名旁聊").setView(name).setNegativeButton("取消", null)
                .setPositiveButton("保存", (dialog, which) -> RuntimeClient.renameConversation(this, id, name.getText().toString().trim(), (result, error) -> {
                    if (error != null || result == null) { toast(error == null ? "保存失败" : error); return; }
                    List<Object> list = new ArrayList<>(); for (Map<String, Object> item : HubStore.get().conversations())
                        list.add(id.equals(Json.string(item, "id")) ? result : item);
                    HubStore.get().conversations(list); drawerKey = null; refreshDrawer(); updateHeader();
                })).show();
    }

    private void showMessageActions(String id) { showMessageActions(id, null); }

    /** 点一条消息：开旁聊；回复里有链接时，链接也列在这里打开（气泡要能选字，不直接响应点链接）。 */
    private void showMessageActions(String id, CharSequence shown) {
        Sheet sheet = new Sheet(this, "消息", false).option("开旁聊", "引用这句话，主对话保持不变", false, () -> createSideChat(id));
        String request = EffectReview.requestId(id);
        if (!request.isEmpty() && HubStore.get().can("request_effect_review") && requestFailed(request))
            sheet.option("核对这次操作", "结果不明时，由你确认它到底有没有生效", false, () -> reviewEffects("messages/" + request));
        if (shown instanceof android.text.Spanned) {
            android.text.Spanned text = (android.text.Spanned) shown;
            for (android.text.style.URLSpan link : text.getSpans(0, text.length(), android.text.style.URLSpan.class)) {
                String url = link.getURL();
                String label = text.subSequence(text.getSpanStart(link), text.getSpanEnd(link)).toString();
                sheet.option("打开链接：" + label, url, false, () -> {
                    try { startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url))); }
                    catch (android.content.ActivityNotFoundException none) { fail("没有能打开这个链接的应用"); }
                });
            }
        }
        sheet.show();
    }

    /** 这轮请求在聊天里是没成的（你的话或回复标着失败）：只有这时才可能有结果不明的操作。 */
    private boolean requestFailed(String request) {
        for (ChatHistory.Message message : HubStore.get().mainMessages(currentConversation))
            if (request.equals(EffectReview.requestId(message.id)) && message.status.equals("failed")) return true;
        return false;
    }

    /** record 是 "messages/<请求编号>" 或 "timers/<定时编号>"。 */
    private String reviewKey(String record) { return "effect_review:" + runtimeUrl() + ":" + record; }

    /**
     * 人工核对：先读原操作，主人逐项选“已生效 / 未生效”再提交。提交的请求体存下来，回执丢了或 503
     * 时用同一份重试；409 说明记录变了，重新读取。这只记下主人的确认，不重做、不撤销任何操作。
     */
    private void reviewEffects(String request) {
        RuntimeClient.effects(this, request, null, (view, status, error) -> {
            EffectReview review = EffectReview.from(view);
            if (review == null) { fail(error == null ? "这轮请求没有可核对的操作" : error); return; }
            showEffectReview(request, review);
        });
    }

    private void showEffectReview(String request, EffectReview review) {
        android.content.SharedPreferences prefs = getSharedPreferences("ds_ui", MODE_PRIVATE);
        if (review.status.equals("reviewed")) prefs.edit().remove(reviewKey(request)).apply();
        String saved = prefs.getString(reviewKey(request), null);
        Sheet sheet = new Sheet(this, "核对这次操作", false);
        LinearLayout page = column(); page.setPadding(0, dp(4), 0, dp(8));
        TextView state = text(EffectReview.label(review.status), 14, true); page.addView(state);
        TextView note = text("这是你的人工确认，记录在原对话里；不会重做、重试或撤销任何操作。", 12, false);
        note.setPadding(0, dp(4), 0, dp(10)); page.addView(note);
        Map<String, String> chosen = new HashMap<>(review.recorded);
        Button submit = button(review.status.equals("needs_review") && saved == null ? "提交确认" : "重试发出说明", null);
        submit.setBackground(pressable(BLUE, 12)); submit.setTextColor(Color.WHITE);
        boolean editable = review.status.equals("needs_review") && saved == null;
        for (EffectReview.Item item : review.items) {
            LinearLayout row = column(); row.setPadding(dp(12), dp(10), dp(12), dp(10));
            row.setBackground(surface(Color.WHITE, 0xffe6ebf2, 14));
            row.addView(text(item.name.isEmpty() ? "一项操作" : item.name, 14, true));
            if (!item.detail.isEmpty()) { TextView detail = text(item.detail, 12, false); detail.setTypeface(Typeface.MONOSPACE); row.addView(detail); }
            if (!item.canApply()) row.addView(text("还没开始执行，只能确认为未生效", 12, false));
            LinearLayout choices = new LinearLayout(this);
            Button applied = button("已生效", null), notApplied = button("未生效", null);
            Runnable paint = () -> {
                String value = chosen.get(item.id);
                applied.setBackground(pressable(EffectReview.APPLIED.equals(value) ? 0xffe9f1ff : Color.WHITE, 12, 0xffe6ebf2));
                notApplied.setBackground(pressable(EffectReview.NOT_APPLIED.equals(value) ? 0xffe9f1ff : Color.WHITE, 12, 0xffe6ebf2));
                submit.setEnabled(!editable || review.missing(chosen) == null);
            };
            applied.setEnabled(editable && item.canApply()); notApplied.setEnabled(editable);
            applied.setOnClickListener(v -> { chosen.put(item.id, EffectReview.APPLIED); paint.run(); });
            notApplied.setOnClickListener(v -> { chosen.put(item.id, EffectReview.NOT_APPLIED); paint.run(); });
            LinearLayout.LayoutParams left = new LinearLayout.LayoutParams(0, dp(44), 1); left.setMargins(0, 0, dp(6), 0);
            LinearLayout.LayoutParams right = new LinearLayout.LayoutParams(0, dp(44), 1); right.setMargins(dp(6), 0, 0, 0);
            choices.addView(applied, left); choices.addView(notApplied, right); row.addView(choices);
            paint.run();
            LinearLayout.LayoutParams gap = new LinearLayout.LayoutParams(-1, -2); gap.setMargins(0, 0, 0, dp(8));
            page.addView(row, gap);
        }
        TextView problem = text("", 12, false); problem.setTextColor(DANGER); problem.setVisibility(View.GONE);
        if (!review.status.equals("reviewed")) { page.addView(submit); page.addView(problem); }
        submit.setOnClickListener(v -> {
            String body = prefs.getString(reviewKey(request), null);
            if (body == null) {
                try { body = review.body(reviewId(), chosen); }
                catch (IllegalArgumentException missing) { problem.setText(missing.getMessage()); problem.setVisibility(View.VISIBLE); return; }
                prefs.edit().putString(reviewKey(request), body).apply();
            }
            submit.setEnabled(false); problem.setVisibility(View.GONE);
            String sent = body;
            RuntimeClient.effects(this, request, sent, (view, status, error) -> {
                EffectReview next = EffectReview.from(view);
                if (next != null) {
                    sheet.dismiss();
                    if (next.status.equals("reviewed")) {
                        prefs.edit().remove(reviewKey(request)).apply(); toast("已记下你的确认，说明已回到原对话");
                        if (request.startsWith("timers/")) loadTimerReviews();
                    }
                    else showEffectReview(request, next);
                    return;
                }
                if (status == 409) {
                    // 原操作或负责人变了：这份确认作废，重新读取后再决定。
                    prefs.edit().remove(reviewKey(request)).apply(); sheet.dismiss();
                    fail(error == null ? "记录已变化，请重新核对" : error); reviewEffects(request); return;
                }
                problem.setText((status == 503 ? "已保存，说明还没发出：" : "没提交成：") + (error == null ? "请重试" : error)
                        + "。再点一次会用同一份确认重试。");
                problem.setVisibility(View.VISIBLE); submit.setText("重试发出说明"); submit.setEnabled(true);
            });
        });
        ScrollView reader = new ScrollView(this); reader.addView(page);
        sheet.add(reader); sheet.show();
    }

    private static String reviewId() { return "review-" + java.util.UUID.randomUUID().toString().replace("-", ""); }

    private void createSideChat(String message) {
        RuntimeClient.Target original = RuntimeClient.selected(this);
        RuntimeClient.catalog(this, message, (result, error) -> {
            if (original == null || RuntimeClient.selected(this) == null
                    || !original.url.equals(RuntimeClient.selected(this).url)) return;
            if (error != null || result == null) { toast(error == null ? "旁聊创建失败，请重试" : error); return; }
            String id = Json.string(result, "id"); if (id.isEmpty()) return;
            List<Object> items = new ArrayList<>(HubStore.get().conversations()); items.add(result);
            HubStore.get().conversations(items); enterConversation(id, true);
            chatInput.requestFocus();
        });
    }

    private void topWork() {
        navigation.clear(); setDrawer(false); workKey = ""; show(TAB_WORK);
        loadTimerReviews();
    }

    /** 等主人核对的定时操作摘要（timer_effect_review）；只在打开「在做的事」和核对之后读一次。 */
    private List<Map<String, Object>> timerReviews = new ArrayList<>();
    private boolean timerReviewsMore;

    private void loadTimerReviews() {
        if (!HubStore.get().can("timer_effect_review")) { timerReviews = new ArrayList<>(); return; }
        RuntimeClient.timerReviews(this, null, (view, status, error) -> {
            List<Map<String, Object>> rows = new ArrayList<>();
            for (Object row : Json.list(view, "timers")) {
                Map<String, Object> item = Json.object(row);
                if (item != null && !Json.string(item, "status").equals("reviewed")) rows.add(item);
            }
            timerReviews = rows; timerReviewsMore = view != null && view.get("next_cursor") instanceof String;
            workKey = ""; refreshWork(HubStore.get().cards());
        });
    }

    private View timerReviewRow(Map<String, Object> row) {
        boolean reviewable = Json.flag(row, "reviewable");
        String kind = Json.string(row, "kind").equals("promise") ? "提醒" : "记忆整理";
        String detail = reviewable ? EffectReview.label(Json.string(row, "status")) : Json.string(row, "reason");
        LinearLayout box = column(); box.setPadding(dp(14), dp(12), dp(14), dp(12));
        box.setBackground(pressable(0xfffff4e0, 18));
        box.addView(text(kind + "结果不明", 14, true));
        TextView sub = text(detail.isEmpty() ? "暂时不能核对" : detail, 12, false); sub.setTextColor(0xff8a5413); box.addView(sub);
        String timer = Json.string(row, "timer_id");
        if (reviewable && !timer.isEmpty()) {
            box.setFocusable(true); ctl(box, "work:timer-review");
            box.setOnClickListener(v -> reviewEffects("timers/" + timer));
        }
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(-1, -2); p.setMargins(0, 0, 0, dp(8)); box.setLayoutParams(p);
        return box;
    }

    private void openNative(Card card) {
        rememberPage(); openTask = card.id; nativeKey = ""; show(TAB_NATIVE);
    }

    private void supplement(Card card) {
        if (card.monitoringOnly) {
            toast("这条是独立电脑会话。请在来源电脑继续，避免另建任务或抢占正在执行的会话。"); return;
        }
        rememberPage(); currentConversation = card.conversation;
        HubStore.get().activeConversation(currentConversation); supplementCard = card.id;
        chatAnchor = null; chatHeader = ""; show(TAB_CHAT);
        supplementHint.setText("补充：" + card.title + "  ×"); supplementHint.setVisibility(View.VISIBLE);
        chatInput.requestFocus(); scrollDown();
    }

    private void attachmentMenu() {
        Sheet sheet = new Sheet(this, "添加附件", false);
        sheet.option("照片", "一次可选多张（最多 4 张），预览后再发送", false, () -> {
            if (tab == TAB_HOME) mainAs(true, false);
            chooseImage();
        });
        sheet.option("文件", "文件上传尚未由当前服务提供，升级支持后会在这里启用", false,
                () -> toast("当前服务暂不支持文件上传，照片可正常发送"));
        sheet.show();
    }

    private String progressAge(Card card) {
        return WorkRecords.status(card, HubStore.get().runtimeConnected(), System.currentTimeMillis() / 1000.0, java.time.ZoneId.systemDefault());
    }

    private void refreshWork(List<Card> cards) {
        if (tab != TAB_WORK) return;
        String key = HubStore.get().runtimeConnected() + ":" + showCompletedWork + ":" + timerReviews;
        for (Card card : cards) key += ":" + card.id + ":" + card.seq + ":" + progressAge(card);
        for (Card.Approval approval : HubStore.get().approvals()) key += ":" + approval.id;
        if (key.equals(workKey)) return; workKey = key;
        workPane.removeAllViews();
        boolean connected = HubStore.get().runtimeConnected();
        Set<String> archived = marks("archived");
        List<Card> waiting = new ArrayList<>(), running = new ArrayList<>(), done = new ArrayList<>();
        double now = System.currentTimeMillis() / 1000.0;
        for (Card card : cards) {
            if (!WorkRecords.listed(card)) continue;
            WorkRecords.Section section = WorkRecords.section(card, connected, now);
            if (archived.contains(card.id) && section != WorkRecords.Section.WAITING) continue;
            (section == WorkRecords.Section.WAITING ? waiting : section == WorkRecords.Section.RUNNING ? running : done).add(card);
        }
        Set<String> pinned = marks("pinned");
        java.util.Comparator<Card> order = java.util.Comparator.comparing((Card c) -> !pinned.contains(c.id)).thenComparing(java.util.Comparator.comparingDouble((Card c) -> c.createdAt).reversed());
        waiting.sort(order); running.sort(order); done.sort(order);
        if (!timerReviews.isEmpty()) {
            // 结果不明的定时操作也是“等你确认”的一种：同一页里先列出来，不另开入口。
            workPane.addView(groupHeading("需要你核对"));
            for (Map<String, Object> row : timerReviews) workPane.addView(timerReviewRow(row));
            if (timerReviewsMore) { TextView more = text("还有更多，处理完这些后会接着列出", 12, false); more.setTextColor(HINT); workPane.addView(more); }
        }
        if (!waiting.isEmpty()) {
            workPane.addView(groupHeading("等你确认"));
            for (Card card : waiting) workPane.addView(decisionCard(card));
        }
        if (!running.isEmpty()) workPane.addView(groupHeading("进行中"));
        for (Card card : running) workPane.addView(workRow(card));
        if (waiting.isEmpty() && running.isEmpty()) {
            TextView none = text("现在没有在做的事。在聊天里交代一件事，小幽会安排帮手。", 13, false);
            none.setPadding(dp(4), dp(18), dp(4), dp(6)); workPane.addView(none);
        }
        if (!done.isEmpty()) {
            TextView toggle = text((showCompletedWork ? "⌃ " : "› ") + "已结束 " + done.size(), 13, false);
            toggle.setTextColor(HINT); toggle.setPadding(dp(4), dp(18), dp(4), dp(8));
            toggle.setOnClickListener(v -> { showCompletedWork = !showCompletedWork; workKey = ""; refreshWork(HubStore.get().cards()); });
            ctl(toggle, "work:ended"); workPane.addView(toggle);
            if (showCompletedWork) for (Card card : done) workPane.addView(workRow(card));
        }
        if (!archived.isEmpty()) workPane.addView(button("已归档 " + archived.size(), v -> showArchived()));
    }

    /** 列表里的一行：帮手头像、标题和尾部一个指示（转圈；沉默太久是橙色时钟和时长；结束了是结果）。 */
    private View workRow(Card card) {
        boolean connected = HubStore.get().runtimeConnected();
        LinearLayout row = listRow(56, false);
        row.setPadding(dp(12), dp(6), dp(12), dp(6)); row.setBackground(pressable(Color.WHITE, 16));
        row.addView(avatar(AppPresentation.owner(card), DsState.pose(card, connected), 30));
        TextView title = text(WorkRecords.title(card), 14, true); title.setMaxLines(2); title.setEllipsize(TextUtils.TruncateAt.END);
        title.setPadding(dp(12), 0, dp(8), 0); row.addView(title, new LinearLayout.LayoutParams(0, -2, 1));
        row.addView(workIndicator(card, connected));
        row.setOnClickListener(v -> open(card.id)); row.setOnLongClickListener(v -> { taskActions(card); return true; });
        row.setContentDescription(WorkRecords.title(card) + "，" + progressAge(card));
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(-1, -2); p.setMargins(0, 0, 0, dp(8)); row.setLayoutParams(p);
        return row;
    }

    private View workIndicator(Card card, boolean connected) {
        double now = System.currentTimeMillis() / 1000.0;
        WorkRecords.Group group = WorkRecords.group(card, connected, now);
        if (group == WorkRecords.Group.HISTORY) {
            TextView end = text(card.state.equals("failed") ? "未成功" : card.state.equals("cancelled") ? "已停止" : "✓", 13, false);
            end.setTextColor(card.state.equals("done") ? GREEN : HINT); return end;
        }
        double last = card.lastMeaningfulAt > 0 ? card.lastMeaningfulAt : card.startedAt;
        if (group == WorkRecords.Group.UNCERTAIN || last > 0 && now - last > 300) {
            long minutes = last > 0 ? Math.max(1, (long) (now - last) / 60) : 0;
            TextView quiet = text(group == WorkRecords.Group.UNCERTAIN ? "待确认" : minutes < 60 ? minutes + " 分钟" : minutes / 60 + " 小时", 12, false);
            quiet.setTextColor(group == WorkRecords.Group.UNCERTAIN ? HINT : AMBER); return quiet;
        }
        NavIconView spinner = new NavIconView(this, "spinner"); spinner.color(BLUE);
        if (Motion.enabled()) {
            android.animation.ObjectAnimator spin = android.animation.ObjectAnimator.ofFloat(spinner, "rotation", 0f, 360f);
            spin.setDuration(900); spin.setRepeatCount(android.animation.ValueAnimator.INFINITE);
            spin.setInterpolator(new android.view.animation.LinearInterpolator()); spin.start();
            // 列表重建时旧行被拿掉：跟着停下，不留一个一直在转的动画。
            spinner.addOnAttachStateChangeListener(new View.OnAttachStateChangeListener() {
                public void onViewAttachedToWindow(View view) { if (!spin.isStarted()) spin.start(); }
                public void onViewDetachedFromWindow(View view) { spin.cancel(); }
            });
        }
        spinner.setLayoutParams(new LinearLayout.LayoutParams(dp(18), dp(18)));
        return spinner;
    }

    /** 等你确认的一件事：手机上是一张卡，问题和两个按钮原地作答；点卡的其余部分看详情。 */
    private View decisionCard(Card card) {
        LinearLayout box = column(); box.setPadding(dp(14), dp(12), dp(14), dp(12));
        box.setBackground(pressable(0xfffff4e0, 18)); box.setFocusable(true);
        box.setOnClickListener(v -> open(card.id)); ctl(box, "work:decision");
        LinearLayout top = new LinearLayout(this); top.setGravity(Gravity.CENTER_VERTICAL);
        top.addView(avatar(AppPresentation.owner(card), "ask", 28));
        TextView title = text(WorkRecords.title(card), 14, true); title.setPadding(dp(10), 0, 0, 0);
        title.setMaxLines(2); title.setEllipsize(TextUtils.TruncateAt.END); top.addView(title, new LinearLayout.LayoutParams(0, -2, 1));
        box.addView(top);
        Card.Approval approval = approvalFor(card);
        if (approval != null) box.addView(decisionButtons(approval));
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(-1, -2); p.setMargins(0, 0, 0, dp(8)); box.setLayoutParams(p);
        return box;
    }

    private Card.Approval approvalFor(Card card) {
        for (Card.Approval approval : HubStore.get().approvals()) if (card.id.equals(approval.card)) return approval;
        return null;
    }

    /** 一个决定的问题和两个按钮。聊天、通知和在做的事里答的都是同一个决定。 */
    private View decisionButtons(Card.Approval approval) {
        LinearLayout box = column();
        TextView ask = text(approval.tool, 13, false); ask.setTextColor(0xff8a5413); ask.setMaxLines(3);
        ask.setEllipsize(TextUtils.TruncateAt.END); ask.setPadding(0, dp(8), 0, dp(4)); box.addView(ask);
        if (HubStore.get().monitoringApproval(approval.id)) {
            TextView there = text("请在电脑上确认", 12, false); there.setTextColor(HINT); box.addView(there); return box;
        }
        LinearLayout actions = new LinearLayout(this);
        Button deny = button("拒绝", null), allow = button("允许这次", null);
        allow.setBackground(pressable(BLUE, 12)); allow.setTextColor(Color.WHITE);
        TextView status = text("", 12, false); status.setVisibility(View.GONE);
        deny.setOnClickListener(v -> answerInline(approval, false, actions, status));
        allow.setOnClickListener(v -> answerInline(approval, true, actions, status));
        LinearLayout.LayoutParams left = new LinearLayout.LayoutParams(0, dp(44), 1); left.setMargins(0, 0, dp(6), 0);
        LinearLayout.LayoutParams right = new LinearLayout.LayoutParams(0, dp(44), 1); right.setMargins(dp(6), 0, 0, 0);
        actions.addView(deny, left); actions.addView(allow, right);
        box.addView(actions); box.addView(status);
        return box;
    }

    private void answerInline(Card.Approval original, boolean allow, LinearLayout actions, TextView status) {
        Card.Approval current = HubStore.get().approval(original.id);
        status.setVisibility(View.VISIBLE);
        if (current == null || current.createdAt != original.createdAt || !current.detail.equals(original.detail)) {
            status.setText("这个请求变了，请打开详情再看一次"); return;
        }
        String claimed = HubStore.get().takeApproval(original.id);
        if (claimed == null) { status.setText("已经在别处答过了"); return; }
        for (int i = 0; i < actions.getChildCount(); i++) actions.getChildAt(i).setEnabled(false);
        status.setText("正在提交…");
        RuntimeClient.approve(this, claimed, allow, problem -> {
            if (isFinishing() || isDestroyed()) return;
            if (problem == null) { performHaptic(true); toast(allow ? "已允许" : "已拒绝"); workKey = ""; taskKey = null; onHubChanged(); return; }
            status.setText(problem);
            for (int i = 0; i < actions.getChildCount(); i++) actions.getChildAt(i).setEnabled(true);
        });
    }

    private void fillWorkDetail(LinearLayout head, Card card, boolean connected) {
        LinearLayout title = new LinearLayout(this); title.setGravity(Gravity.CENTER_VERTICAL);
        title.addView(avatar(AppPresentation.owner(card), DsState.pose(card, connected), 34));
        TextView name = text(WorkRecords.title(card), 19, true); name.setPadding(dp(12), 0, 0, 0); title.addView(name); head.addView(title);
        if (!CardViews.titleNotice(card).isEmpty()) head.addView(text(CardViews.titleNotice(card), 12, false));
        LinearLayout status = column(); status.setPadding(dp(16), dp(16), dp(16), dp(16)); status.setBackground(surface(Color.WHITE, Color.WHITE, 20));
        WorkRecords.Group group = WorkRecords.group(card, connected, System.currentTimeMillis() / 1000.0);
        // 停止请求发出、还没有执行方确认的终态：如实说“正在停止”，不提前说已停止。
        boolean stopping = card.id.equals(stoppingCard) && card.active();
        String state = stopping ? "正在停止…" : group == WorkRecords.Group.UNCERTAIN ? "状态待确认" : AppPresentation.stateLabel(card, connected);
        status.addView(text(state, 17, true));
        status.addView(text(connected ? progressAge(card) : "连不上，显示上次的记录", 13, false));
        String latest = WorkRecords.progress(card);
        if (!latest.isEmpty()) status.addView(text((group == WorkRecords.Group.UNCERTAIN ? "最后记录：" : "") + latest, 14, false));
        String relay = WorkRecords.relayLabel(card.relayState);
        if (!relay.isEmpty()) status.addView(text(relay, 13, false));
        if (!card.pendingSupplement.isEmpty()) status.addView(text("待转交：" + CardViews.oneLine(card.pendingSupplement, 80), 13, false));
        if (group == WorkRecords.Group.UNCERTAIN && !card.monitoringOnly && !card.runId.isEmpty()
                && HubStore.get().can("execution_reconciliation")) {
            status.addView(button("核对结果", v -> {
                v.setEnabled(false);
                RuntimeClient.reconcile(this, card.id, card.runId, (result, error) -> {
                    v.setEnabled(true);
                    if (error != null) { fail(error.replaceFirst("^Runtime 返回 \\d+：", "")); return; }
                    // 只读核对：有完成证据才算 confirmed；证据不足保持待确认，不替你判定。
                    toast(result != null && Json.string(result, "status").equals("confirmed")
                            ? "核对好了，结果已回到原对话" : "没找到足够的完成证据，保持待确认");
                    taskKey = null; onHubChanged();
                });
            }));
        }
        Card.Approval approval = group == WorkRecords.Group.WAITING ? approvalFor(card) : null;
        if (approval != null) status.addView(decisionButtons(approval));
        LinearLayout.LayoutParams sp = new LinearLayout.LayoutParams(-1, -2); sp.setMargins(0, dp(16), 0, dp(12)); head.addView(status, sp);
        if (!card.monitoringOnly) {
            Button more = button("补充要求", v -> supplement(card));
            more.setBackground(pressable(BLUE, 12)); more.setTextColor(Color.WHITE); head.addView(more);
        }
        // 最近三步，图标、动作、时间；全部的在「全部 N」里。
        LinearLayout steps = new LinearLayout(this); steps.setGravity(Gravity.CENTER_VERTICAL);
        TextView recent = text("最近进展", 15, true); steps.addView(recent, new LinearLayout.LayoutParams(0, -2, 1));
        int total = card.activity.isEmpty() ? card.progress.size() : card.activity.size();
        if (total > 0) {
            TextView all = text("全部 " + total + " ›", 13, false); all.setTextColor(BLUE); all.setPadding(dp(8), dp(10), 0, dp(10));
            all.setOnClickListener(v -> showDurableActivity(card, null)); ctl(all, "work:all-steps"); steps.addView(all);
        }
        steps.setPadding(0, dp(18), 0, dp(4)); head.addView(steps);
        int first = Math.max(0, card.activity.size() - 3);
        for (int i = first; i < card.activity.size(); i++) {
            Card.Entry event = card.activity.get(i);
            head.addView(groupRow(null, CardViews.oneLine(event.text, 60), stepLabel(event), stepTime(event.at), HINT, v -> activityDetail(event), i < card.activity.size() - 1));
        }
        if (card.activity.isEmpty()) for (int i = Math.max(0, card.progress.size() - 3); i < card.progress.size(); i++) head.addView(text(card.progress.get(i), 13, false));
        if (total == 0) head.addView(text("还没有进展", 13, false));
        // 一行安静的信息：电脑、来源聊天、原会话；目录、会话编号这些收在 ⓘ 里。
        LinearLayout info = new LinearLayout(this); info.setGravity(Gravity.CENTER_VERTICAL); info.setPadding(0, dp(18), 0, dp(8));
        RuntimeClient.Target source = RuntimeClient.selected(this);
        String host = !card.originHost.isEmpty() ? card.originHost : source == null ? "" : source.name;
        if (!host.isEmpty()) { TextView computer = text(host, 12, false); computer.setTextColor(HINT); computer.setPadding(0, 0, dp(12), 0); info.addView(computer); }
        if (!card.monitoringOnly) info.addView(quietLink("来源聊天 ›", v -> {
            enterConversation(card.conversation, false); highlightMessage = card.originMessage;
            chatWindow = Math.max(chatWindow, HubStore.get().mainMessages().size()); chatAnchor = null; chatHeader = "";
            refreshChat(HubStore.get().cards(), HubStore.get());
            scroll.post(() -> { View sourceRow = chatList.findViewWithTag(card.originMessage); if (sourceRow != null) scroll.smoothScrollTo(0, sourceRow.getTop()); });
        }));
        info.addView(quietLink("原会话 ›", v -> openNative(card)));
        info.addView(new View(this), new LinearLayout.LayoutParams(0, 1, 1));
        info.addView(iconAction("info", "执行信息", v -> {
            Sheet sheet = new Sheet(this, "执行信息", false);
            sheet.option("目录", card.workerCwd.isEmpty() ? "来源未提供" : card.workerCwd, false, () -> {});
            sheet.option("会话", card.workerSession.isEmpty() ? card.sessionId : card.workerSession, false, () -> {});
            sheet.option("来源", WorkRecords.source(card, source == null ? "" : source.name), false, () -> {});
            if (!card.directoryId.isEmpty()) sheet.option("共享历史", null, false, () -> SharedHistoryPane.show(this, card.directoryId, card.title));
            for (String nativeSource : card.nativeSources) sheet.option("原生完整记录", null, false, () -> SharedHistoryPane.show(this, nativeSource, card.title));
            sheet.show();
        }), new LinearLayout.LayoutParams(dp(44), dp(44)));
        head.addView(info);
    }

    private TextView quietLink(String label, View.OnClickListener action) {
        TextView link = text(label, 12, false); link.setTextColor(BLUE); link.setPadding(0, dp(12), dp(14), dp(12));
        link.setOnClickListener(action); link.setFocusable(true); ctl(link, "work:" + label);
        return link;
    }

    private static String stepLabel(Card.Entry event) {
        switch (event.kind) {
            case "dispatch": return "开始";
            case "supplement_received": return "收到补充";
            case "relay_queued": return "等待转交";
            case "relayed": return "已转交";
            case "tool_start": case "tool_result": return "操作";
            case "completed": return "本轮结束";
            case "failed": return "没成功";
            case "cancelled": return "已停止";
            default: return "进展";
        }
    }

    private static String stepTime(double at) {
        if (!(at > 0)) return "";
        return new SimpleDateFormat("HH:mm", Locale.getDefault()).format(new Date((long) (at * 1000)));
    }

    private void refreshNative(List<Card> cards) {
        if (tab != TAB_NATIVE) return;
        Card card = CardViews.find(cards, openTask); if (card == null) return;
        String key = card.id + ":" + card.seq; if (key.equals(nativeKey)) return; nativeKey = key;
        nativePane.removeAllViews(); nativePane.addView(heading(WorkRecords.title(card)));
        nativePane.addView(text("只读 · 已同步的公开片段，完整对话请在来源电脑的 " + AppPresentation.name(AppPresentation.owner(card)) + " 查看", 12, false));
        for (Card.Entry entry : card.entries) {
            if (!card.monitoringOnly && entry.role.equals("xiaoyou") && !entry.kind.equals("worker_result")) continue;
            if (entry.role.equals("you") && !card.monitoringOnly) nativePane.addView(text("小幽转交的原话", 12, false));
            addBubble(nativePane, card, entry);
        }
        nativePane.addView(button("补充要求", v -> supplement(card)));
    }

    private void showDurableActivity(Card card, String before) {
        RuntimeClient.audit(this, card.id, before, (result, error) -> {
            if (error != null || result == null) { showActivity(card, 0); return; }
            Sheet sheet = new Sheet(this, "执行记录 · 已持久保存", false);
            List<Object> rows = Json.list(result, "events");
            for (int i = rows.size() - 1; i >= 0; i--) {
                Map<String, Object> e = Json.object(rows.get(i));
                Card.Entry event = new Card.Entry("xiaoyou", Json.string(e, "text"), java.util.Collections.emptyList(),
                        Json.string(e, "id"), "task", Json.string(e, "phase"), "", Json.number(e, "created_at"));
                sheet.option(activityTitle(event), CardViews.oneLine(event.text, 100), false, () -> activityDetail(event));
            }
            if (Json.flag(result, "more")) sheet.action("更早记录", false, () -> showDurableActivity(card, String.valueOf((long) Json.number(result, "before"))));
            if (rows.isEmpty()) sheet.option("暂无持久记录", "升级前的历史可在最近记录里查看", false, () -> showActivity(card, 0));
            sheet.show();
        });
    }

}
