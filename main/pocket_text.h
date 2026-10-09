// main/pocket_text.h —— 界面上所有固定文案。
//
// 字库子集由 tools/gen_pocket_fonts.py 扫描本文件里的字符串字面量生成，
// tests/test_pocket_fonts.py 会核对每个字符在各字号字库中都有字形。
// 所以：新增或修改文案后要重新生成字库；不要在别处写死界面中文。
// 约定：这里的字面量只用普通 UTF-8 字符和 \n，不用其他转义。
#pragma once

// ---- 首页：小幽独占画面的几种情形（标题 + 两三行说明）----
#define PT_HOME_BLE_OFF    "蓝牙关着呢"
#define PT_HOME_WAITING    "睡着啦"
#define PT_HOME_LINKING    "连上啦"
#define PT_HOME_QUIET      "待命中"

#define PT_HOME_SUB_BLE_OFF   "长按上键\n在菜单里把蓝牙打开"
#define PT_HOME_SUB_WAITING   "手机上打开小幽中枢\n我就醒了"
#define PT_HOME_SUB_LINKING   "等对面跟我打个招呼"
#define PT_HOME_SUB_QUIET     "有事按住确认键\n直接说"
// 通知的个数填在 %u 里。
#define PT_HOME_SUB_NOTICES   "有 %u 个新通知\n长按上键在菜单里看"
#define PT_HOME_SUB_NO_VOICE  "现在没活儿\n有事我会叫你"

// ---- 首页：对话版式（顶上一条是小幽和她此刻的状态，下面是一条往下长的对话）----
#define PT_XIAOYOU         "小幽"
// 顶上那一条里，小幽此刻在干什么
#define PT_HEAD_SENT       "在听写"
#define PT_HEAD_THINKING   "在想"
// 帮手在做：名字在上一行的名牌里
#define PT_HEAD_WORKING    "在做了"
#define PT_HEAD_DONE       "说完啦"
#define PT_HEAD_FAILED     "这次没成"
// 录音发出去了，还不知道听成了什么
#define PT_SAID_PENDING    "（正在听写）"
#define PT_TALK_SENT       "发出去啦，等我听清楚"
#define PT_TALK_THINKING   "听到了，我想想"
#define PT_TALK_FAILED     "这次没成，再说一次吧"
// 连的是 Claude 桌面端时，顶上那一条说的是谁在干活
#define PT_DESKTOP_BUSY    "电脑上 Claude 在忙"
#define PT_DESKTOP_REPLY   "电脑上 Claude 说的"
#define PT_DESKTOP_WORKING "它还在忙，好了这里会更新"
// 她去找帮手：%s 是帮手的名字
#define PT_HELPER_ASKED    "这件事我去找 %s 帮忙"
#define PT_ELLIPSIS        "…"

// 回答比设备留得下的长
#define PT_READER_CUT      "（太长了，后面的在手机上看）"

// ---- 通知 ----
#define PT_NOTICES_TITLE   "通知"
#define PT_NOTICES_EMPTY   "还没有通知哦"

// ---- 帮手 ----
#define PT_HELPERS_TITLE   "小幽的帮手"
#define PT_HELPERS_EMPTY   "还不知道有哪些帮手\n连上手机中枢就有了"

#define PT_NO_VALUE        "--"

// ---- 菜单 ----
#define PT_MENU_TITLE      "菜单"
#define PT_MENU_NOTICES    "通知"
#define PT_MENU_HELPERS    "帮手"
#define PT_MENU_BRIGHTNESS "屏幕亮度"
#define PT_MENU_BLE        "蓝牙"
#define PT_MENU_SCREEN_OFF "熄灭屏幕"
#define PT_MENU_MORE       "更多设置"
#define PT_MENU_BACK       "返回"
#define PT_ON              "开"
#define PT_OFF             "关"

// ---- 更多设置 ----
#define PT_MORE_TITLE      "更多设置"
#define PT_MORE_GUIDE      "连接指引"
#define PT_MORE_UNPAIR     "取消配对"
#define PT_MORE_FACTORY    "恢复出厂设置"

// ---- 连接指引 ----
#define PT_GUIDE_TITLE     "连接指引"
#define PT_GUIDE_NAME      "本机名称"
#define PT_GUIDE_BODY \
    "连手机（平时用这个）\n" \
    "1. 手机上打开小幽中枢\n" \
    "2. 点连接设备，选择上面的本机名称\n" \
    "3. 在手机的蓝牙提示框输入本机显示的 6 位配对码\n" \
    "连电脑上的 Claude 桌面端\n" \
    "1. 菜单 Help → Troubleshooting → Enable Developer Mode\n" \
    "2. 菜单 Developer → Open Hardware Buddy…\n" \
    "3. 点 Connect，选择上面的本机名称，输入配对码\n" \
    "之后会自动重连。熄屏时有事找你会自动亮屏。"

// ---- 小幽的对话：有件事要你点头 ----
// 气泡里每行最多 8 个汉字宽（136 像素），所以这里手动分行。
#define PT_ASK_APPROVAL    "有件事要你点头\n可以吗？"
#define PT_APPROVAL_NO_HINT "（没有更多说明）"
#define PT_APPROVAL_FULL_TOOL "完整名称："
#define PT_APPROVAL_CUT    "（太长了，后面被截断）"
#define PT_APPROVAL_ALLOW  "可以"
#define PT_APPROVAL_DENY   "不行"
#define PT_APPROVAL_SENDING "正在回话…"
#define PT_APPROVAL_SENT_YES "好耶，已经回话啦"
#define PT_APPROVAL_SENT_NO "好的，已经拦下啦"
#define PT_APPROVAL_FAILED "没发出去，这次不算数"

// ---- 小幽的对话：配对 ----
#define PT_ASK_PASSKEY     "有设备想认识我！\n在它的蓝牙提示框\n输入这串数字"
#define PT_PAIR_SECURING   "正在握手\n稍等一下…"

// ---- 小幽的对话：二次确认 ----
#define PT_ASK_UNPAIR      "要取消配对吗？\n下次得重新输入\n配对码哦"
#define PT_ASK_FACTORY     "要恢复出厂吗？\n名字、统计、配对\n都会清空哦"
#define PT_CONFIRM_YES     "确定"
#define PT_CONFIRM_NO      "算了"

// ---- 按住说话 ----
#define PT_VOICE_PREPARING "等一下下"
#define PT_VOICE_LISTENING "我在听"
#define PT_VOICE_SENDING   "发出去啦"
#define PT_VOICE_SUB_PREPARING "我竖起耳朵…"
#define PT_VOICE_SUB_SENDING   "正在交给手机…"
// 结果和提示（显示在底部提示那一行，或者小幽独占画面时的说明里；最多 13 个汉字一行）
#define PT_VOICE_LIMIT       "说得有点久，先发这些"
#define PT_VOICE_TOO_SHORT   "按住确认键再说话哦"
#define PT_VOICE_NEED_LINK   "先连上手机才能说话哦"
#define PT_VOICE_NO_HOST     "这个连接不能传语音"
#define PT_VOICE_FAILED_MIC  "麦克风没准备好，再试一次"
#define PT_VOICE_FAILED_LINK "没发出去，连接不太稳"

// ---- 经蓝牙换固件（小幽独占画面：标题、进度、一行说明）----
#define PT_UPDATE_RECEIVING   "在换新样子"
#define PT_UPDATE_CHECKING    "快好了"
#define PT_UPDATE_RESTARTING  "换好啦"
#define PT_UPDATE_SUB_RECEIVING  "别关机，马上就好"
#define PT_UPDATE_SUB_CHECKING   "照照镜子，看合不合身"
#define PT_UPDATE_SUB_RESTARTING "重启一下就来"
// 没换成（显示在底部提示那一行；最多 13 个汉字一行）
#define PT_UPDATE_FAILED      "新样子没换成，还是原来的"

// ---- 按键提示：图标后面跟的字 ----
#define PT_KEY_SEPARATOR   " · "
#define PT_KEY_DOWN        "下键"
#define PT_KEY_OK          "确认键"
#define PT_HINT_TALK       "按住说话"
#define PT_HINT_CUT_IN     "按住插一句"
#define PT_HINT_RELEASE    "松开就发送"
#define PT_HINT_UP         "往上"
#define PT_HINT_DOWN       "往下"
// 之前的对话收起来了：双击上键接回来
#define PT_HINT_EARLIER    "双击看之前"
#define PT_HINT_MENU       "长按 菜单"
#define PT_HINT_PREV       "上"
#define PT_HINT_NEXT       "下"
#define PT_HINT_ENTER      "进入"
#define PT_HINT_BACK       "返回"
#define PT_HINT_SCROLL     "滚动"
#define PT_HINT_FULL       "翻看全文"

// ---- 操作结果（显示在底部提示那一行）----
#define PT_MSG_BLE_SAVE_FAILED   "蓝牙设置保存失败"
#define PT_MSG_BLE_FAILED        "蓝牙开关失败"
#define PT_MSG_BLE_RESTORED      "蓝牙关闭失败，已恢复"
#define PT_MSG_BLE_RESTART_FAILED "蓝牙重启失败"
#define PT_MSG_FACTORY_FAILED    "恢复出厂失败"
#define PT_MSG_FACTORY_DONE      "已恢复出厂设置"
#define PT_MSG_UNPAIR_FAILED     "取消配对失败"
#define PT_MSG_UNPAIRING         "正在取消配对"
#define PT_MSG_UNPAIRED          "已取消配对"
