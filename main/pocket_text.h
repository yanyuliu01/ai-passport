// main/pocket_text.h —— 界面上所有固定文案。
//
// 字库子集由 tools/gen_pocket_fonts.py 扫描本文件里的字符串字面量生成，
// tests/test_pocket_fonts.py 会核对每个字符在各字号字库中都有字形。
// 所以：新增或修改文案后要重新生成字库；不要在别处写死界面中文。
// 约定：这里的字面量只用普通 UTF-8 字符和 \n，不用其他转义。
#pragma once

// ---- 顶栏连接状态 ----
#define PT_LINK_OFF        "蓝牙关"
#define PT_LINK_WAITING    "未连接"
#define PT_LINK_PAIRING    "配对中"
#define PT_LINK_ON         "已连接"

// ---- 首页：状态词与说明 ----
#define PT_HOME_BLE_OFF    "蓝牙关着呢"
#define PT_HOME_WAITING    "睡着啦"
#define PT_HOME_LINKING    "连上啦"
#define PT_HOME_IDLE       "待命中"
#define PT_HOME_BUSY       "干活中"
#define PT_HOME_PENDING    "等你点头"
#define PT_HOME_APPROVED   "好耶"
#define PT_HOME_MILESTONE  "新纪录"

#define PT_HOME_SUB_BLE_OFF   "去设置里把蓝牙打开吧"
#define PT_HOME_SUB_WAITING   "等电脑上的 Claude 来叫我"
#define PT_HOME_SUB_LINKING   "正在等 Claude 的消息"
#define PT_HOME_SUB_IDLE      "现在没活儿，随时叫我"
#define PT_HOME_SUB_PENDING   "电脑上有请求在等你"
#define PT_HOME_SUB_MILESTONE "输出量又上了一个台阶"

#define PT_STAT_TOTAL      "会话"
#define PT_STAT_RUNNING    "运行"
#define PT_STAT_WAITING    "等待"

// ---- 最新回复 ----
#define PT_REPLY_TITLE     "最新回复"
#define PT_REPLY_EMPTY     "还没有回复哦\nClaude 说完一轮就显示在这里"
#define PT_REPLY_OFFLINE   "连上电脑再来看吧"
#define PT_ELLIPSIS        "…"

// ---- 最近动态 ----
#define PT_ACTIVITY_TITLE  "最近动态"
#define PT_ACTIVITY_EMPTY  "还没有动态哦"

// ---- 用量 ----
#define PT_USAGE_TITLE     "用量"
#define PT_USAGE_TODAY     "今日输出"
#define PT_USAGE_SESSION   "本次累计"
#define PT_USAGE_APPROVED  "已允许"
#define PT_USAGE_DENIED    "已拒绝"
#define PT_USAGE_UPTIME    "已运行"
#define PT_USAGE_BATTERY   "电池"
#define PT_USAGE_TIMES     "次"
#define PT_USAGE_NO_BATTERY "未检测到"
#define PT_NO_VALUE        "--"
// 数量和时长单位（pocket_view.c 的格式化函数使用）
#define PT_UNIT_WAN        "万"
#define PT_UNIT_YI         "亿"
#define PT_UPTIME_UNDER_MINUTE "不到1分钟"
#define PT_UNIT_MINUTES    "分钟"
#define PT_UNIT_HOURS      "小时"
#define PT_UNIT_MINUTE_SHORT "分"
#define PT_UNIT_DAYS       "天"

// ---- 设置 ----
#define PT_SETTINGS_TITLE  "设置"
#define PT_SET_BRIGHTNESS  "屏幕亮度"
#define PT_SET_BLE         "蓝牙"
#define PT_SET_GUIDE       "连接指引"
#define PT_SET_SCREEN_OFF  "熄灭屏幕"
#define PT_SET_UNPAIR      "取消配对"
#define PT_SET_FACTORY     "恢复出厂设置"
#define PT_SET_BACK        "返回"
#define PT_ON              "开"
#define PT_OFF             "关"

// ---- 连接指引 ----
#define PT_GUIDE_TITLE     "连接指引"
#define PT_GUIDE_NAME      "本机名称"
#define PT_GUIDE_BODY \
    "1. 在电脑上打开 Claude 桌面端\n" \
    "2. 菜单 Help → Troubleshooting → Enable Developer Mode\n" \
    "3. 菜单 Developer → Open Hardware Buddy…\n" \
    "4. 点 Connect，选择上面的本机名称\n" \
    "5. 在电脑的蓝牙提示框输入本机显示的 6 位配对码\n" \
    "之后会自动重连。熄屏时有权限请求会自动亮屏。"

// ---- 小幽的对话：权限审批 ----
// 气泡里每行最多 8 个汉字宽（136 像素），所以这里手动分行。
#define PT_ASK_APPROVAL    "Claude 想用这个\n可以吗？"
#define PT_APPROVAL_NO_HINT "（没有更多说明）"
#define PT_APPROVAL_FULL_TOOL "完整工具名："
#define PT_APPROVAL_CUT    "（太长了，后面被截断）"
#define PT_APPROVAL_ALLOW  "可以"
#define PT_APPROVAL_DENY   "不行"
#define PT_APPROVAL_SENDING "正在告诉 Claude…"
#define PT_APPROVAL_SENT_YES "好耶，已经告诉 Claude 啦"
#define PT_APPROVAL_SENT_NO "好的，已经拦下啦"
#define PT_APPROVAL_FAILED "没发出去，这次不算数"

// ---- 小幽的对话：配对 ----
#define PT_ASK_PASSKEY     "有电脑想认识我！\n在它的蓝牙提示框\n输入这串数字"
#define PT_PAIR_SECURING   "正在握手\n稍等一下…"

// ---- 小幽的对话：二次确认 ----
#define PT_ASK_UNPAIR      "要取消配对吗？\n下次得重新输入\n配对码哦"
#define PT_ASK_FACTORY     "要恢复出厂吗？\n名字、统计、配对\n都会清空哦"
#define PT_CONFIRM_YES     "确定"
#define PT_CONFIRM_NO      "算了"

// ---- 小幽的对话：按住说话 ----
#define PT_VOICE_PREPARING "等一下下\n我竖起耳朵…"
#define PT_VOICE_LISTENING "我在听\n说吧！"
#define PT_VOICE_SENDING   "收到！\n正在交给手机…"
#define PT_VOICE_STATE_PREPARING "准备中"
#define PT_VOICE_STATE_SENDING   "发送中"
#define PT_VOICE_RELEASE   "松开发送"
// 结果和提示（显示在首页说明行，每行最多 13 个汉字）
#define PT_VOICE_SENT        "发出去啦，等小幽回话"
#define PT_VOICE_LIMIT       "说得有点久，先发这些"
#define PT_VOICE_TOO_SHORT   "按住确认键再说话哦"
#define PT_VOICE_NEED_LINK   "先连上手机才能说话哦"
#define PT_VOICE_NO_HOST     "这个连接不能传语音\n要连手机上的小幽中枢"
#define PT_VOICE_FAILED_MIC  "麦克风没准备好\n再试一次吧"
#define PT_VOICE_FAILED_LINK "没发出去，连接不太稳"

// ---- 底部按键提示 ----
#define PT_KEY_SEPARATOR   " · "
#define PT_KEY_UP          "上键"
#define PT_KEY_DOWN        "下键"
#define PT_KEY_OK          "确认键"
#define PT_HINT_PAGES      "按住确认键说话 · 长按上键设置"
#define PT_HINT_SETTINGS   "上/下 选择 · 确认键 执行"
#define PT_HINT_GUIDE      "上/下 滚动 · 确认键 返回"
#define PT_HINT_APPROVAL   "上键 翻看全文"
#define PT_HINT_CONFIRM    "确认键 确定 · 下键 取消"

// ---- 操作结果（显示在首页说明行）----
#define PT_MSG_BLE_SAVE_FAILED   "蓝牙设置保存失败"
#define PT_MSG_BLE_FAILED        "蓝牙开关失败"
#define PT_MSG_BLE_RESTORED      "蓝牙关闭失败，已恢复"
#define PT_MSG_BLE_RESTART_FAILED "蓝牙重启失败"
#define PT_MSG_FACTORY_FAILED    "恢复出厂失败"
#define PT_MSG_FACTORY_DONE      "已恢复出厂设置"
#define PT_MSG_UNPAIR_FAILED     "取消配对失败"
#define PT_MSG_UNPAIRING         "正在取消配对"
#define PT_MSG_UNPAIRED          "已取消配对"
