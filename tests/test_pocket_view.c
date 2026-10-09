#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "buddy_history.h"
#include "pocket_view.h"

static buddy_ui_snapshot_t connected_snapshot(void)
{
    buddy_ui_snapshot_t snapshot = {0};

    snapshot.ble_enabled = true;
    snapshot.ble_connected = true;
    snapshot.ble_encrypted = true;
    snapshot.connection = BUDDY_CONNECTION_CONNECTED;
    snapshot.character = BUDDY_CHARACTER_IDLE;
    return snapshot;
}

static void test_view_priority(void)
{
    buddy_ui_snapshot_t snapshot = connected_snapshot();

    assert(pocket_view_for(NULL) == POCKET_VIEW_PAGE);
    assert(pocket_view_for(&snapshot) == POCKET_VIEW_PAGE);
    snprintf(snapshot.prompt_id, sizeof(snapshot.prompt_id), "%s", "req-1");
    assert(pocket_view_for(&snapshot) == POCKET_VIEW_APPROVAL);
    /* 已经按过键（锁定）的请求仍留在审批视图，用来显示发送结果。 */
    snapshot.approval_locked = true;
    assert(pocket_view_for(&snapshot) == POCKET_VIEW_APPROVAL);
    snapshot.passkey_visible = true;
    assert(pocket_view_for(&snapshot) == POCKET_VIEW_PAIRING);
    snapshot.passkey_visible = false;
    snapshot.connection = BUDDY_CONNECTION_PAIRING;
    assert(pocket_view_for(&snapshot) == POCKET_VIEW_PAIRING);
    snapshot.confirmation = BUDDY_CONFIRM_UNPAIR;
    assert(pocket_view_for(&snapshot) == POCKET_VIEW_CONFIRM);
}

static void test_voice_view(void)
{
    buddy_ui_snapshot_t snapshot = connected_snapshot();

    /* 正在说话时，说话的画面压过普通页面和新来的审批请求。 */
    snapshot.voice_phase = BUDDY_VOICE_PREPARING;
    assert(pocket_view_for(&snapshot) == POCKET_VIEW_VOICE);
    assert(pocket_pet_for(&snapshot) == POCKET_PET_BUSY);
    snprintf(snapshot.prompt_id, sizeof(snapshot.prompt_id), "%s", "req-1");
    snapshot.voice_phase = BUDDY_VOICE_LISTENING;
    assert(pocket_view_for(&snapshot) == POCKET_VIEW_VOICE);
    assert(pocket_pet_for(&snapshot) == POCKET_PET_ASK);
    snapshot.voice_phase = BUDDY_VOICE_SENDING;
    assert(pocket_pet_for(&snapshot) == POCKET_PET_HAPPY);
    /* 配对和二次确认仍然更优先。 */
    snapshot.passkey_visible = true;
    assert(pocket_view_for(&snapshot) == POCKET_VIEW_PAIRING);
    snapshot.passkey_visible = false;
    snapshot.voice_phase = BUDDY_VOICE_IDLE;
    assert(pocket_view_for(&snapshot) == POCKET_VIEW_APPROVAL);
}

static void test_update_view(void)
{
    buddy_ui_snapshot_t snapshot = connected_snapshot();

    /* 正在换固件时，别的都让路：审批、配对、二次确认、说话都不显示。 */
    snprintf(snapshot.prompt_id, sizeof(snapshot.prompt_id), "%s", "req-1");
    snapshot.confirmation = BUDDY_CONFIRM_UNPAIR;
    snapshot.passkey_visible = true;
    snapshot.voice_phase = BUDDY_VOICE_LISTENING;
    snapshot.update_phase = POCKET_UPDATE_RECEIVING;
    assert(pocket_view_for(&snapshot) == POCKET_VIEW_UPDATE);
    assert(pocket_pet_for(&snapshot) == POCKET_PET_BUSY);
    snapshot.update_phase = POCKET_UPDATE_CHECKING;
    assert(pocket_view_for(&snapshot) == POCKET_VIEW_UPDATE);
    assert(pocket_pet_for(&snapshot) == POCKET_PET_BUSY);
    snapshot.update_phase = POCKET_UPDATE_RESTARTING;
    assert(pocket_view_for(&snapshot) == POCKET_VIEW_UPDATE);
    assert(pocket_pet_for(&snapshot) == POCKET_PET_HAPPY);
    /* 收到一半连接断了、等手机回来的时候不占屏幕，没在换的时候当然也不占。 */
    snapshot.update_phase = POCKET_UPDATE_PAUSED;
    assert(pocket_view_for(&snapshot) == POCKET_VIEW_CONFIRM);
    snapshot.update_phase = POCKET_UPDATE_IDLE;
    assert(pocket_view_for(&snapshot) == POCKET_VIEW_CONFIRM);
    assert(pocket_update_shown(POCKET_UPDATE_RECEIVING));
    assert(pocket_update_shown(POCKET_UPDATE_CHECKING));
    assert(pocket_update_shown(POCKET_UPDATE_RESTARTING));
    assert(!pocket_update_shown(POCKET_UPDATE_PAUSED));
    assert(!pocket_update_shown(POCKET_UPDATE_IDLE));
}

static void test_home_status(void)
{
    buddy_ui_snapshot_t snapshot = {0};

    assert(pocket_home_for(NULL) == POCKET_HOME_BLE_OFF);
    assert(pocket_home_for(&snapshot) == POCKET_HOME_BLE_OFF);
    snapshot.ble_enabled = true;
    assert(pocket_home_for(&snapshot) == POCKET_HOME_WAITING);
    snapshot.ble_connected = true;
    assert(pocket_home_for(&snapshot) == POCKET_HOME_LINKING);
    snapshot.ble_encrypted = true;
    snapshot.heartbeat_stale = true;
    snapshot.connection = BUDDY_CONNECTION_OFFLINE;
    assert(pocket_home_for(&snapshot) == POCKET_HOME_LINKING);

    /* 连着手机中枢：首页跟着和小幽的对话走。 */
    snapshot = connected_snapshot();
    snapshot.host_hub = true;
    snapshot.host_chat = true;
    assert(pocket_home_for(&snapshot) == POCKET_HOME_QUIET);
    assert(!pocket_home_is_talk(POCKET_HOME_QUIET));
    /* 中枢心跳里的数字说的是别的 App 的通知，不是小幽在忙。 */
    snapshot.waiting = 2;
    snapshot.running = 1;
    assert(pocket_home_for(&snapshot) == POCKET_HOME_QUIET);
    /* 不报告对话的旧版中枢：只能像桌面端那样按数字来。 */
    snapshot.host_chat = false;
    assert(pocket_home_for(&snapshot) == POCKET_HOME_THINKING);
    snapshot.host_chat = true;
    snapshot.waiting = 0;
    snapshot.running = 0;
    snapshot.chat.phase = BUDDY_CHAT_SENT;
    assert(pocket_home_for(&snapshot) == POCKET_HOME_SENT);
    assert(pocket_home_is_talk(POCKET_HOME_SENT));
    snapshot.chat.phase = BUDDY_CHAT_THINKING;
    assert(pocket_home_for(&snapshot) == POCKET_HOME_THINKING);
    snapshot.chat.phase = BUDDY_CHAT_HELPER;
    /* 说“交给别人了”却没说是谁，只能当作还在想。 */
    assert(pocket_home_for(&snapshot) == POCKET_HOME_THINKING);
    snprintf(snapshot.chat.agent, sizeof(snapshot.chat.agent), "%s", "codex");
    assert(pocket_home_for(&snapshot) == POCKET_HOME_HELPER);
    snapshot.chat.phase = BUDDY_CHAT_DONE;
    assert(pocket_home_for(&snapshot) == POCKET_HOME_ANSWERED);
    snapshot.chat.phase = BUDDY_CHAT_FAILED;
    assert(pocket_home_for(&snapshot) == POCKET_HOME_FAILED);
    assert(pocket_home_is_talk(POCKET_HOME_FAILED));
    /* 心跳一旦过期，任何“刚刚发生”的状态都不能继续显示。 */
    snapshot.heartbeat_stale = true;
    assert(pocket_home_for(&snapshot) == POCKET_HOME_LINKING);
    assert(!pocket_home_is_talk(POCKET_HOME_LINKING));

    /* 连着 Claude 桌面端：没有“对话”，按心跳里的数字和最新回复来。 */
    snapshot = connected_snapshot();
    assert(pocket_home_for(&snapshot) == POCKET_HOME_QUIET);
    snapshot.running = 2;
    assert(pocket_home_for(&snapshot) == POCKET_HOME_THINKING);
    snapshot.running = 0;
    snapshot.waiting = 1;
    assert(pocket_home_for(&snapshot) == POCKET_HOME_THINKING);
    snapshot.waiting = 0;
    snprintf(snapshot.reply, sizeof(snapshot.reply), "%s", "done");
    assert(pocket_home_for(&snapshot) == POCKET_HOME_ANSWERED);
    snapshot.running = 1;
    assert(pocket_home_for(&snapshot) == POCKET_HOME_THINKING);
}

static void test_pet_mood(void)
{
    buddy_ui_snapshot_t snapshot = {0};

    assert(pocket_pet_for(NULL) == POCKET_PET_SLEEP);
    assert(pocket_pet_for(&snapshot) == POCKET_PET_SLEEP);
    snapshot.ble_enabled = true;
    assert(pocket_pet_for(&snapshot) == POCKET_PET_SLEEP);

    snapshot = connected_snapshot();
    assert(pocket_pet_for(&snapshot) == POCKET_PET_IDLE);
    snapshot.running = 1;
    assert(pocket_pet_for(&snapshot) == POCKET_PET_BUSY);
    snapshot.running = 0;
    snapshot.character = BUDDY_CHARACTER_HEART;
    assert(pocket_pet_for(&snapshot) == POCKET_PET_HAPPY);

    /* 和小幽的对话：在等的时候忙，答完时是她自己说的表情，没成是沮丧。 */
    snapshot = connected_snapshot();
    snapshot.chat.phase = BUDDY_CHAT_SENT;
    assert(pocket_pet_for(&snapshot) == POCKET_PET_BUSY);
    snapshot.chat.phase = BUDDY_CHAT_HELPER;
    snprintf(snapshot.chat.agent, sizeof(snapshot.chat.agent), "%s", "codex");
    assert(pocket_pet_for(&snapshot) == POCKET_PET_BUSY);
    snapshot.chat.phase = BUDDY_CHAT_DONE;
    snapshot.chat.mood = BUDDY_MOOD_HAPPY;
    assert(pocket_pet_for(&snapshot) == POCKET_PET_HAPPY);
    snapshot.chat.mood = BUDDY_MOOD_ASK;
    assert(pocket_pet_for(&snapshot) == POCKET_PET_ASK);
    snapshot.chat.mood = BUDDY_MOOD_IDLE;
    assert(pocket_pet_for(&snapshot) == POCKET_PET_IDLE);
    snapshot.chat.phase = BUDDY_CHAT_FAILED;
    snapshot.chat.mood = BUDDY_MOOD_HAPPY;
    assert(pocket_pet_for(&snapshot) == POCKET_PET_OOPS);

    /* 审批：提问 → 发送中 → 结果；拒绝之后不摆出开心的脸。 */
    snapshot = connected_snapshot();
    snprintf(snapshot.prompt_id, sizeof(snapshot.prompt_id), "%s", "req-1");
    assert(pocket_pet_for(&snapshot) == POCKET_PET_ASK);
    snapshot.approval_locked = true;
    snapshot.permission_decision = BUDDY_PERMISSION_ONCE;
    snapshot.permission_delivery = BUDDY_PERMISSION_DELIVERY_SENDING;
    assert(pocket_pet_for(&snapshot) == POCKET_PET_BUSY);
    snapshot.permission_delivery = BUDDY_PERMISSION_DELIVERY_FAILED;
    assert(pocket_pet_for(&snapshot) == POCKET_PET_OOPS);
    snapshot.permission_delivery = BUDDY_PERMISSION_DELIVERY_SENT;
    assert(pocket_pet_for(&snapshot) == POCKET_PET_HAPPY);
    snapshot.permission_decision = BUDDY_PERMISSION_DENY;
    assert(pocket_pet_for(&snapshot) == POCKET_PET_IDLE);

    snapshot = connected_snapshot();
    snapshot.connection = BUDDY_CONNECTION_PAIRING;
    assert(pocket_pet_for(&snapshot) == POCKET_PET_BUSY);
    snapshot.passkey_visible = true;
    assert(pocket_pet_for(&snapshot) == POCKET_PET_ASK);
    snapshot.confirmation = BUDDY_CONFIRM_FACTORY_RESET;
    assert(pocket_pet_for(&snapshot) == POCKET_PET_ASK);
}

static void test_notice(void)
{
    buddy_ui_snapshot_t snapshot = connected_snapshot();

    assert(!pocket_notice_visible(NULL));
    assert(!pocket_notice_visible(&snapshot));
    /* Claude 桌面端的状态行一直显示。 */
    snprintf(snapshot.message, sizeof(snapshot.message), "%s", "approve: Bash");
    snapshot.message_since_ms = 1000;
    snapshot.uptime_ms = 1000 + 3600000;
    assert(pocket_notice_visible(&snapshot));
    /* 连着手机中枢时它只是一条提示：出现六秒就收起。 */
    snapshot.host_hub = true;
    assert(!pocket_notice_visible(&snapshot));
    snapshot.uptime_ms = 1000 + 5999;
    assert(pocket_notice_visible(&snapshot));
    snapshot.uptime_ms = 1000 + 6000;
    assert(!pocket_notice_visible(&snapshot));
    /* 计时器回绕时宁可多显示一会儿。 */
    snapshot.uptime_ms = 500;
    assert(pocket_notice_visible(&snapshot));
}

static void test_helper_colour(void)
{
    /* 认识的名字颜色固定，不分大小写。 */
    assert(pocket_helper_color("claude") == 0xE08A63u);
    assert(pocket_helper_color("Claude") == 0xE08A63u);
    assert(pocket_helper_color("CODEX") == 0x6FD3B0u);
    /* 没有名字就是小幽自己。 */
    assert(pocket_helper_color(NULL) == POCKET_COLOR_XIAOYOU);
    assert(pocket_helper_color("") == POCKET_COLOR_XIAOYOU);
    /* 不认识的名字：同一个名字永远同一个颜色，而且不会撞上小幽和认识的那两个。 */
    {
        static const char *const names[] = {
            "local", "gemini", "pc", "\xE4\xB9\xA6\xE6\x88\xBF\xE7\x94\xB5\xE8\x84\x91",
            "claude-code", "codex2", "a", "b",
        };
        size_t index;

        for (index = 0; index < sizeof(names) / sizeof(names[0]); ++index) {
            uint32_t color = pocket_helper_color(names[index]);

            assert(color == pocket_helper_color(names[index]));
            assert(color != POCKET_COLOR_XIAOYOU && color != 0xE08A63u && color != 0x6FD3B0u);
        }
        assert(pocket_helper_color("PC") == pocket_helper_color("pc"));
    }
}

static void test_clock(void)
{
    char text[8] = "x";

    assert(pocket_format_clock(0, 28800, 0, 0, text, sizeof(text)) == 0);
    assert(text[0] == '\0');
    /* 1790000000 = 2026-09-21 14:13:20 UTC；东八区是 22:13。 */
    assert(pocket_format_clock(1790000000, 28800, 1000, 1000, text, sizeof(text)) == 5);
    assert(strcmp(text, "22:13") == 0);
    /* 同步之后设备自己走时：过 7 分钟。 */
    assert(pocket_format_clock(1790000000, 28800, 1000, 1000 + 7 * 60000, text,
                               sizeof(text)) == 5);
    assert(strcmp(text, "22:20") == 0);
    /* 负时区跨日。 */
    assert(pocket_format_clock(3600, -7200, 0, 0, text, sizeof(text)) == 5);
    assert(strcmp(text, "23:00") == 0);
    /* now 早于同步时刻（计时器回绕保护）时不前进。 */
    assert(pocket_format_clock(1790000000, 0, 5000, 1000, text, sizeof(text)) == 5);
    assert(strcmp(text, "14:13") == 0);
    /* 东八区跨过午夜。 */
    assert(pocket_format_clock(1790000000 + 2 * 3600, 28800, 0, 0, text, sizeof(text)) == 5);
    assert(strcmp(text, "00:13") == 0);
}

static void test_elapsed_and_passkey(void)
{
    char text[16];
    char tiny[4];

    assert(pocket_format_elapsed(1000, 1000, text, sizeof(text)) == 4);
    assert(strcmp(text, "0:00") == 0);
    assert(pocket_format_elapsed(1000, 13400, text, sizeof(text)) == 4);
    assert(strcmp(text, "0:12") == 0);
    assert(pocket_format_elapsed(0, 75000, text, sizeof(text)) == 4);
    assert(strcmp(text, "1:15") == 0);
    assert(pocket_format_elapsed(0, 12 * 60000ULL + 5000, text, sizeof(text)) == 5);
    assert(strcmp(text, "12:05") == 0);
    /* 封顶，不会越写越宽。 */
    assert(pocket_format_elapsed(0, 5ULL * 3600000ULL, text, sizeof(text)) == 5);
    assert(strcmp(text, "99:59") == 0);
    /* 起点在“现在”之后（计时器回绕）按 0 算。 */
    assert(pocket_format_elapsed(5000, 1000, text, sizeof(text)) == 4);
    assert(strcmp(text, "0:00") == 0);
    /* 缓冲区不够时截断但保持 NUL 结尾。 */
    assert(pocket_format_elapsed(0, 75000, tiny, sizeof(tiny)) == 3);
    assert(tiny[3] == '\0');
    assert(pocket_format_elapsed(0, 1, NULL, 8) == 0);
    assert(pocket_format_elapsed(0, 1, tiny, 0) == 0);

    assert(pocket_format_passkey(123456, text, sizeof(text)) == 7);
    assert(strcmp(text, "123 456") == 0);
    assert(pocket_format_passkey(42, text, sizeof(text)) == 7);
    assert(strcmp(text, "000 042") == 0);
    assert(pocket_format_passkey(1234567, text, sizeof(text)) == 7);
    assert(strcmp(text, "234 567") == 0);
}

static void test_home_layout(void)
{
    static buddy_history_t history;
    buddy_ui_snapshot_t snapshot = connected_snapshot();

    memset(&history, 0, sizeof(history));
    assert(!pocket_home_shows_talk(NULL));
    /* 没聊过：小幽独占画面。 */
    snapshot.host_hub = true;
    snapshot.host_chat = true;
    assert(!pocket_home_shows_talk(&snapshot));
    snapshot.history = &history;
    assert(!pocket_home_shows_talk(&snapshot));
    /* 有一轮在屏幕上。 */
    snapshot.chat.phase = BUDDY_CHAT_THINKING;
    assert(pocket_home_shows_talk(&snapshot));
    /* 没有正在进行的，但这一段里有说过的话可以回看。 */
    snapshot.chat.phase = BUDDY_CHAT_NONE;
    assert(buddy_history_push(&history, "q", "a", 0));
    assert(pocket_home_shows_talk(&snapshot));
    /* 断开了也一样：说过的话留着可以读。 */
    snapshot.ble_connected = false;
    assert(pocket_home_for(&snapshot) == POCKET_HOME_WAITING);
    assert(pocket_home_shows_talk(&snapshot));
    /* 收起来之后回到小幽独占画面。 */
    assert(buddy_history_fold(&history));
    assert(!pocket_home_shows_talk(&snapshot));
}

static void test_timeline_window(void)
{
    /* 窗口九行高（207），一行 23。 */
    /* 内容不满一屏：窗口不动。 */
    assert(pocket_timeline_anchor(0, 0, 207) == 0);
    assert(pocket_timeline_anchor(92, 46, 207) == 0);
    /* 最新一轮放得下：贴着底，前面的对话也露出来。 */
    assert(pocket_timeline_anchor(460, 345, 207) == 253);
    /* 最新一轮自己就比窗口高：从它的开头读起。 */
    assert(pocket_timeline_anchor(690, 230, 207) == 230);
    assert(pocket_timeline_anchor(690, -5, 207) == 0);
    /* 正好一屏。 */
    assert(pocket_timeline_anchor(437, 230, 207) == 230);

    /* 上下翻：不滚出长卷。 */
    assert(pocket_timeline_step(230, 1, 690, 207, 184) == 414);
    assert(pocket_timeline_step(414, 1, 690, 207, 184) == 483);
    assert(pocket_timeline_step(483, 1, 690, 207, 184) == 483);
    assert(pocket_timeline_step(230, -1, 690, 207, 184) == 46);
    assert(pocket_timeline_step(46, -1, 690, 207, 184) == 0);
    assert(pocket_timeline_step(0, -1, 690, 207, 184) == 0);
    /* 内容变短了：位置跟着收回来。 */
    assert(pocket_timeline_step(483, 0, 300, 207, 184) == 93);
    assert(pocket_timeline_step(50, 0, 100, 207, 184) == 0);
    assert(pocket_timeline_step(50, 1, 100, 207, 184) == 0);
}

int main(void)
{
    test_home_layout();
    test_timeline_window();
    test_view_priority();
    test_voice_view();
    test_update_view();
    test_home_status();
    test_pet_mood();
    test_notice();
    test_helper_colour();
    test_clock();
    test_elapsed_and_passkey();
    puts("pocket_view: PASS");
    return 0;
}
