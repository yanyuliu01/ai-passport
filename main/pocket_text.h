// main/pocket_text.h —— 界面上所有固定文案。
//
// 字库子集由 tools/gen_pocket_fonts.py 扫描本文件里的字符串字面量生成，
// tests/test_pocket_fonts.py 会核对每个字符在各字号字库中都有字形。
// 所以：新增或修改文案后要重新生成字库；不要在别处写死界面中文。
// 约定：这里的字面量只用普通 UTF-8 字符和 \n，不用其他转义。
#pragma once

// ---- 第一屏：形象。小幽、一个标题、下面两三行 ----
#define PT_HOME_BLE_OFF    "蓝牙关着呢"
#define PT_HOME_WAITING    "睡着啦"
#define PT_HOME_LINKING    "连上啦"
#define PT_HOME_QUIET      "待命中"

#define PT_HOME_SUB_BLE_OFF   "双击确认键\n在菜单里把蓝牙打开"
#define PT_HOME_SUB_WAITING   "手机上打开小幽中枢\n我就醒了"
#define PT_HOME_SUB_LINKING   "等对面跟我打个招呼"
#define PT_HOME_SUB_QUIET     "有事按住确认键\n直接说"
// 后台在做的事的个数填在 %u 里。
#define PT_HOME_SUB_DOING     "后台有 %u 件事在做\n有事按住确认键直接说"
// 通知的个数填在 %u 里。
#define PT_HOME_SUB_NOTICES   "有 %u 个新通知\n双击确认键在菜单里看"
#define PT_HOME_SUB_NO_VOICE  "现在没活儿\n有事我会叫你"
// 她把活交给了帮手：是谁写在下面那一行的名牌里
#define PT_HOME_HANDED     "交给帮手了"

// ---- 小幽此刻在干什么：第一屏的标题，也是第二屏顶上那一条 ----
#define PT_XIAOYOU         "小幽"
#define PT_HEAD_SENT       "在听写"
#define PT_HEAD_THINKING   "在想"
// 帮手在做：名字在旁边的名牌里
#define PT_HEAD_WORKING    "在做了"
#define PT_HEAD_DONE       "说完啦"
#define PT_HEAD_FAILED     "这次没成"
// 连的是 Claude 桌面端时，说的是谁在干活
#define PT_DESKTOP_BUSY    "电脑上 Claude 在忙"
#define PT_DESKTOP_REPLY   "电脑上 Claude 说的"
#define PT_DESKTOP_WORKING "它还在忙，好了这里会更新"

// ---- 第二屏：对话，一屏一件事 ----
// 录音发出去了，还不知道听成了什么
#define PT_SAID_PENDING    "（正在听写）"
#define PT_TALK_SENT       "发出去啦，等我听清楚"
#define PT_TALK_THINKING   "听到了，我想想"
#define PT_TALK_FAILED     "这次没成，再说一次吧"
#define PT_TALK_EMPTY      "还没聊过"
#define PT_TALK_EMPTY_SUB  "按住确认键\n跟我说说话"
// 这件事右上角的一句说明。%s 是帮手的名字，%u 是次数。
#define PT_CARD_NEW        "新的一件"
#define PT_CARD_SELF       "小幽答的"
#define PT_CARD_WORKING    "%s 在做"
#define PT_CARD_WAITING    "等你点头"
#define PT_CARD_DONE       "%s 做完"
#define PT_CARD_FAILED     "没成"
#define PT_CARD_CANCELLED  "取消了"
#define PT_CARD_EDITS      "改过 %u 次"
// 帮手的名字太长、那一行放不下时用这个代替
#define PT_CARD_HELPER     "帮手"
#define PT_ELLIPSIS        "…"

// 回答比设备留得下的长
#define PT_READER_CUT      "（太长了，后面的在手机上看）"

// 这一轮交给了帮手：对话屏只说它去了哪，内容在第三屏。%s 是帮手的名字。
#define PT_TALK_HANDED     "这件事交给 %s 了\n在下一屏看"

// ---- 第三屏：任务。在做的事在前，后面是最近做完的 ----
#define PT_TASKS_EMPTY     "还没有交给帮手的事"
#define PT_TASK_DONE       "好了"
#define PT_TASK_WAITING    "等你点头"
#define PT_TASK_QUEUED     "排队"
#define PT_TASK_NO_STEPS   "（还没有进展）"
#define PT_TASKS_HINT      "按住确认键 接着这件事说"

// ---- 努力程度：跟在帮手名字后面的那个字（“codex 高”）----
#define PT_EFFORT_MINIMAL  "最低"
#define PT_EFFORT_LOW      "低"
#define PT_EFFORT_MEDIUM   "中"
#define PT_EFFORT_HIGH     "高"
#define PT_EFFORT_XHIGH    "特高"
#define PT_EFFORT_MAX      "最高"

// ---- 第四屏：用量。每个订阅账号还剩多少，每个帮手用的是哪个模型 ----
#define PT_USAGE_EMPTY     "还没有用量数据"
#define PT_USAGE_EMPTY_SUB "帮手做过一件事\n这里就有了"
#define PT_USAGE_SHORT     "5 小时"
#define PT_USAGE_WEEK      "本周"
// 还剩百分之几填在 %u 里
#define PT_USAGE_LEFT      "剩 %u%%"
#define PT_USAGE_GONE      "用完了"
#define PT_USAGE_UNKNOWN   "额度还不知道"
// 没有订阅的帮手：模型名后面跟这个
#define PT_USAGE_PAYG      "按量"
// 这个帮手还没干过活，不知道它用的是哪个模型
#define PT_USAGE_NO_MODEL  "还没跑过"
// 什么时候重置。%s 是 “14:20”，或者不在今天时的 “周三 09:00”
#define PT_USAGE_RESET     "%s 重置"
#define PT_USAGE_RESET_BOTH "%s · %s 重置"
#define PT_WEEKDAY_1       "周一"
#define PT_WEEKDAY_2       "周二"
#define PT_WEEKDAY_3       "周三"
#define PT_WEEKDAY_4       "周四"
#define PT_WEEKDAY_5       "周五"
#define PT_WEEKDAY_6       "周六"
#define PT_WEEKDAY_7       "周日"
// 这些数是多久之前的
#define PT_USAGE_AGE_NOW   "刚刚更新"
#define PT_USAGE_AGE_MIN   "%u 分钟前更新"
#define PT_USAGE_AGE_HOUR  "%u 小时前更新"

// ---- 顶栏：后台在做几件事 ----
#define PT_TOP_DOING       "在做 %u"

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
#define PT_HINT_RELEASE    "松开就发送"
// 三屏：确认键短按到下一屏，按住说话，双击打开菜单
#define PT_HINT_HOME       "下一屏 按住说话 双击菜单"
#define PT_HINT_HOME_MUTE  "下一屏 双击菜单"
#define PT_HINT_SCREEN     "下一屏"
#define PT_HINT_SCREEN_TALK "下一屏 按住说话"
#define PT_HINT_SCREEN_ADD "下一屏 按住补充"
// 第三屏有选中的事：短按进到这件事自己的页面
#define PT_HINT_OPEN_ADD   "进入 按住补充"
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
