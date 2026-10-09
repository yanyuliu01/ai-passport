// tools/ui_preview/preview_main.c —— 在主机上用真实的 LVGL 和字库把每个界面
// 渲染成图片，并导出各字库实际能取到字形的码点，供开发时目检和核对覆盖率。
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "lvgl.h"

#include "buddy_history.h"
#include "buddy_types.h"
#include "pocket_fonts.h"
#include "pocket_ui.h"

#define W 240
#define H 320
#define CORNER 30  // BSP_LVGL_SCREEN_RADIUS：设备上圆角以外是黑的

static uint16_t s_frame[W * H];
static uint32_t s_now_ms;
static const char *s_out_dir = ".";

static uint32_t tick_cb(void)
{
    return s_now_ms;
}

static unsigned s_flush_count;

static void flush_cb(lv_display_t *display, const lv_area_t *area, uint8_t *pixels)
{
    (void)area;
    (void)pixels;
    ++s_flush_count;
    lv_display_flush_ready(display);
}

static void run(uint32_t ms)
{
    uint32_t end = s_now_ms + ms;

    while (s_now_ms < end) {
        s_now_ms += 5;
        lv_timer_handler();
    }
}

static bool outside_corner(int x, int y)
{
    int cx = x < CORNER ? CORNER : (x >= W - CORNER ? W - CORNER - 1 : x);
    int cy = y < CORNER ? CORNER : (y >= H - CORNER ? H - CORNER - 1 : y);
    int dx = x - cx;
    int dy = y - cy;

    return dx * dx + dy * dy > CORNER * CORNER;
}

static void save(const char *name)
{
    char path[512];
    FILE *file;
    int x;
    int y;

    lv_obj_invalidate(lv_screen_active());
    run(40);
    (void)snprintf(path, sizeof(path), "%s/%s.ppm", s_out_dir, name);
    file = fopen(path, "wb");
    if (file == NULL) {
        perror(path);
        exit(1);
    }
    (void)fprintf(file, "P6\n%d %d\n255\n", W, H);
    for (y = 0; y < H; ++y) {
        for (x = 0; x < W; ++x) {
            uint16_t pixel = outside_corner(x, y) ? 0 : s_frame[y * W + x];
            uint8_t rgb[3] = {
                (uint8_t)(((pixel >> 11) & 0x1F) * 255 / 31),
                (uint8_t)(((pixel >> 5) & 0x3F) * 255 / 63),
                (uint8_t)((pixel & 0x1F) * 255 / 31),
            };

            (void)fwrite(rgb, 1, sizeof(rgb), file);
        }
    }
    (void)fclose(file);
}

static void show(const char *name, const buddy_ui_snapshot_t *snapshot)
{
    int repeat;

    pocket_ui_render(snapshot);
    save(name);
    /* 同一份快照反复渲染不应该带来额外的刷屏。小幽自己会动（每 450 ms 一帧），
     * 所以先量一段“不渲染”时的刷屏次数作基线，再量同样时长“每 50 ms 渲染一次”。 */
    {
        unsigned baseline;

        s_flush_count = 0;
        run(900);
        baseline = s_flush_count;
        s_flush_count = 0;
        for (repeat = 0; repeat < 18; ++repeat) {
            pocket_ui_render(snapshot);
            run(50);
        }
        if (s_flush_count > baseline) {
            printf("redraw without change: %-34s %u flushes (baseline %u)\n", name,
                   s_flush_count, baseline);
        }
    }
}

static void dump_coverage(const char *name, const lv_font_t *font)
{
    char path[512];
    FILE *file;
    uint32_t codepoint;

    (void)snprintf(path, sizeof(path), "%s/%s.coverage.txt", s_out_dir, name);
    file = fopen(path, "w");
    if (file == NULL) {
        perror(path);
        exit(1);
    }
    for (codepoint = 0x20; codepoint <= 0xFFFF; ++codepoint) {
        lv_font_glyph_dsc_t glyph = {0};

        if (lv_font_get_glyph_dsc(font, &glyph, codepoint, 0) && !glyph.is_placeholder) {
            (void)fprintf(file, "%04X\n", (unsigned)codepoint);
        }
    }
    (void)fclose(file);
}

static buddy_ui_snapshot_t base_snapshot(void)
{
    buddy_ui_snapshot_t snap = {0};

    snap.ble_enabled = true;
    snap.brightness_level = 4;
    snap.battery_available = true;
    snap.battery_percent = 87;
    snap.battery_mv = 4012;
    snap.uptime_ms = 3ULL * 3600000ULL + 5ULL * 60000ULL;
    (void)snprintf(snap.name, sizeof(snap.name), "%s", "Claude-A1B2C3");
    return snap;
}

static buddy_ui_snapshot_t live_snapshot(void)
{
    buddy_ui_snapshot_t snap = base_snapshot();

    snap.ble_connected = true;
    snap.ble_encrypted = true;
    snap.connection = BUDDY_CONNECTION_CONNECTED;
    snap.character = BUDDY_CHARACTER_IDLE;
    snap.epoch_seconds = 1790000000;
    snap.timezone_offset_seconds = 28800;
    snap.time_received_ms = snap.uptime_ms;
    return snap;
}

/* Connected to the phone hub: Xiaoyou is on the other end. */
static buddy_ui_snapshot_t hub_snapshot(void)
{
    buddy_ui_snapshot_t snap = live_snapshot();

    snap.host_hub = true;
    snap.host_chat = true;
    snap.helper_count = 3;
    (void)snprintf(snap.helpers[0].name, BUDDY_AGENT_MAX, "%s", "claude");
    (void)snprintf(snap.helpers[0].about, BUDDY_HELPER_ABOUT_MAX, "%s", "日常问答、查资料");
    (void)snprintf(snap.helpers[1].name, BUDDY_AGENT_MAX, "%s", "codex");
    (void)snprintf(snap.helpers[1].about, BUDDY_HELPER_ABOUT_MAX, "%s",
                   "看代码、给第二意见，默认只读");
    (void)snprintf(snap.helpers[2].name, BUDDY_AGENT_MAX, "%s", "书房电脑");
    (void)snprintf(snap.helpers[2].about, BUDDY_HELPER_ABOUT_MAX, "%s", "那台电脑上的事");
    return snap;
}

#define SAID "retry 那个函数重试次数老不对，帮我看看"
#define ANSWER \
    "找到了：每次失败计数加了 2，所以 n=3 只会试 2 次。把 attempts += 2 改成 += 1 就好。" \
    "另外最后抛错时把原始异常丢了，排查会看不出原因，要不要我一起改？\n\n" \
    "改法也简单：把 raise RuntimeError('gave up') 接上 from 原来的异常就行。" \
    "我没有真的运行过，只是读代码得出的结论。如果你想保险一点，我可以让 codex 在只读模式下" \
    "把调用这个函数的地方都过一遍，看看有没有哪里依赖了现在这个“只试两次”的行为。" \
    "那样大概要一两分钟，结果我会总结给你。"

static buddy_history_t s_history;

static void set_text_field(char *field, size_t size, const char *text)
{
    (void)snprintf(field, size, "%s", text);
}

int main(int argc, char **argv)
{
    lv_display_t *display;
    buddy_ui_snapshot_t snap;
    int index;

    if (argc > 1) {
        s_out_dir = argv[1];
    }
    lv_init();
    lv_tick_set_cb(tick_cb);
    display = lv_display_create(W, H);
    lv_display_set_buffers(display, s_frame, NULL, sizeof(s_frame),
                           LV_DISPLAY_RENDER_MODE_DIRECT);
    lv_display_set_flush_cb(display, flush_cb);

    dump_coverage("pocket_font_14", &pocket_font_14);
    dump_coverage("pocket_font_16", &pocket_font_16);
    dump_coverage("pocket_font_22", &pocket_font_22);
    dump_coverage("pocket_font_num_44", &pocket_font_num_44);

    pocket_ui_init();

    /* ---- not connected, pairing ---- */
    snap = base_snapshot();
    show("01_home_waiting", &snap);
    snap.ble_enabled = false;
    snap.battery_percent = 12;
    show("02_home_ble_off", &snap);

    snap = base_snapshot();
    snap.ble_connected = true;
    snap.connection = BUDDY_CONNECTION_PAIRING;
    show("03_pairing_securing", &snap);
    snap.passkey_visible = true;
    snap.passkey = 123456;
    show("04_pairing_passkey", &snap);

    snap = base_snapshot();
    snap.ble_connected = true;
    snap.ble_encrypted = true;
    snap.heartbeat_stale = true;
    show("05_home_linking", &snap);

    /* ---- the conversation with Xiaoyou (phone hub) ---- */
    snap = hub_snapshot();
    show("06_home_quiet", &snap);
    /* Notifications from other apps do not interrupt; the page only mentions them. */
    snap.waiting = 2;
    show("06b_home_notices", &snap);
    snap.waiting = 0;

    snap.voice_phase = BUDDY_VOICE_PREPARING;
    show("07_voice_preparing", &snap);
    snap.voice_phase = BUDDY_VOICE_LISTENING;
    snap.voice_listening_since_ms = snap.uptime_ms - 7300U;
    {
        static const uint8_t levels[] = {8, 20, 35, 60, 42, 75, 55, 30, 62, 88, 48, 22, 40, 15, 6};
        size_t step;

        /* The level bars fill from the right as samples arrive. */
        for (step = 0; step < sizeof(levels); ++step) {
            snap.voice_level = levels[step];
            snap.uptime_ms += 100U;
            pocket_ui_render(&snap);
        }
    }
    show("08_voice_listening", &snap);
    snap.voice_phase = BUDDY_VOICE_SENDING;
    show("09_voice_sending", &snap);

    /* The first turn of a conversation: nothing above it yet. */
    snap = hub_snapshot();
    snap.history = &s_history;
    snap.turn_serial = 1;
    snap.chat.phase = BUDDY_CHAT_SENT;
    snap.chat.mood = BUDDY_MOOD_BUSY;
    snap.chat_since_ms = snap.uptime_ms - 1200U;
    show("10_home_sent", &snap);
    snap.chat.phase = BUDDY_CHAT_THINKING;
    set_text_field(snap.chat.said, sizeof(snap.chat.said), SAID);
    snap.chat_since_ms = snap.uptime_ms - 5300U;
    show("11_home_thinking", &snap);
    snap.chat.phase = BUDDY_CHAT_HELPER;
    set_text_field(snap.chat.agent, sizeof(snap.chat.agent), "codex");
    set_text_field(snap.chat.stage, sizeof(snap.chat.stage), "这个我让 codex 仔细看一遍，稍等");
    snap.chat_since_ms = snap.uptime_ms - 12400U;
    show("12_home_helper", &snap);
    set_text_field(snap.chat.agent, sizeof(snap.chat.agent), "书房电脑");
    snap.chat.stage[0] = '\0';
    snap.chat_since_ms = snap.uptime_ms - 75000U;
    show("13_home_helper_other", &snap);
    set_text_field(snap.chat.agent, sizeof(snap.chat.agent), "a-very-long-agent-name");
    show("13b_home_helper_long_name", &snap);

    /* A long answer starts at its beginning; DOWN reads on, eight lines at a time. */
    snap = hub_snapshot();
    snap.history = &s_history;
    snap.turn_serial = 1;
    snap.chat.phase = BUDDY_CHAT_DONE;
    snap.chat.mood = BUDDY_MOOD_HAPPY;
    set_text_field(snap.chat.said, sizeof(snap.chat.said), SAID);
    set_text_field(snap.reply, sizeof(snap.reply), ANSWER);
    snap.reply_truncated = true;
    show("14_home_answered", &snap);
    pocket_ui_scroll(60);
    show("15_home_answered_down", &snap);
    pocket_ui_scroll(60);
    show("16_home_answered_end", &snap);
    pocket_ui_scroll(-60);
    pocket_ui_scroll(BUDDY_SCROLL_LATEST);
    show("17_home_answered_back", &snap);

    /* The conversation goes on: earlier turns move up, the newest stays in view. */
    (void)buddy_history_push(&s_history, SAID, ANSWER, BUDDY_TURN_CUT);
    snap.turn_serial = 2;
    snap.reply_truncated = false;
    snap.chat.phase = BUDDY_CHAT_THINKING;
    snap.chat.mood = BUDDY_MOOD_BUSY;
    set_text_field(snap.chat.said, sizeof(snap.chat.said), "好，一起改了吧");
    snap.reply[0] = '\0';
    snap.chat_since_ms = snap.uptime_ms - 3000U;
    show("18_talk_second_turn", &snap);
    snap.chat.phase = BUDDY_CHAT_DONE;
    snap.chat.mood = BUDDY_MOOD_ASK;
    set_text_field(snap.reply, sizeof(snap.reply), "改好了。要不要我顺手把测试也跑一遍？");
    show("19_talk_second_answer", &snap);
    pocket_ui_scroll(-60);
    show("20_talk_scrolled_up", &snap);
    pocket_ui_scroll(BUDDY_SCROLL_LATEST);
    set_text_field(snap.message, sizeof(snap.message), "按住确认键再说话哦");
    snap.message_since_ms = snap.uptime_ms - 1000U;
    show("21_talk_notice", &snap);
    snap.message[0] = '\0';

    (void)buddy_history_push(&s_history, "好，一起改了吧", "改好了。要不要我顺手把测试也跑一遍？", 0);
    snap.turn_serial = 3;
    snap.chat.phase = BUDDY_CHAT_FAILED;
    snap.chat.mood = BUDDY_MOOD_OOPS;
    set_text_field(snap.chat.said, sizeof(snap.chat.said), "跑一遍");
    set_text_field(snap.reply, sizeof(snap.reply), "连不上 Runtime：电脑可能睡着了");
    show("22_talk_failed", &snap);
    (void)buddy_history_push(&s_history, "跑一遍", "连不上 Runtime：电脑可能睡着了",
                             BUDDY_TURN_FAILED);
    snap.turn_serial = 4;
    snap.chat.phase = BUDDY_CHAT_DONE;
    snap.chat.mood = BUDDY_MOOD_HAPPY;
    set_text_field(snap.chat.said, sizeof(snap.chat.said), "现在呢");
    set_text_field(snap.reply, sizeof(snap.reply),
                   "Done. All 42 tests pass (pytest -q). 龘 is outside the font.");
    show("23_talk_latin_and_missing_glyph", &snap);

    /* The link drops: no turn is in progress, but what was said stays readable. */
    (void)buddy_history_push(&s_history, snap.chat.said, snap.reply, 0);
    snap.chat = (buddy_chat_t){0};
    snap.reply[0] = '\0';
    snap.ble_connected = false;
    snap.ble_encrypted = false;
    snap.connection = BUDDY_CONNECTION_OFFLINE;
    snap.host_hub = false;
    snap.host_chat = false;
    show("24_talk_offline", &snap);

    /* Half an hour later the conversation is folded away; a double press on UP
     * brings it back, stopping at its last turn. */
    snap = hub_snapshot();
    snap.history = &s_history;
    snap.turn_serial = 4;
    (void)buddy_history_fold(&s_history);
    show("25_home_folded", &snap);
    snap.recalled = buddy_history_hidden(&s_history);
    (void)buddy_history_reveal(&s_history);
    snap.recall_serial = 1;
    show("26_talk_recalled", &snap);
    pocket_ui_scroll(-60);
    show("27_talk_recalled_up", &snap);

    /* A new conversation with the earlier one folded away: the hint says how to get it. */
    (void)buddy_history_fold(&s_history);
    pocket_ui_render(&snap);
    snap.turn_serial = 5;
    snap.chat.phase = BUDDY_CHAT_DONE;
    snap.chat.mood = BUDDY_MOOD_HAPPY;
    set_text_field(snap.chat.said, sizeof(snap.chat.said), "明天几点的会");
    set_text_field(snap.reply, sizeof(snap.reply), "上午十点，在三楼。");
    show("28_talk_new_with_earlier", &snap);
    memset(&s_history, 0, sizeof(s_history));
    snap = hub_snapshot();
    pocket_ui_render(&snap);

    /* ---- something needs a yes or no ---- */
    snap = hub_snapshot();
    (void)snprintf(snap.prompt_id, sizeof(snap.prompt_id), "%s", "req_abc123");
    (void)snprintf(snap.prompt_tool, sizeof(snap.prompt_tool), "%s", "Bash");
    (void)snprintf(snap.prompt_hint, sizeof(snap.prompt_hint), "%s", "rm -rf /tmp/foo");
    show("30_approval_short", &snap);
    (void)snprintf(snap.prompt_id, sizeof(snap.prompt_id), "%s", "req_long");
    (void)snprintf(snap.prompt_tool, sizeof(snap.prompt_tool), "%s",
                   "mcp__filesystem__write_file_with_a_long_name");
    (void)snprintf(snap.prompt_hint, sizeof(snap.prompt_hint), "%s",
                   "cd /Users/me/project && git fetch --all && git rebase origin/main && "
                   "yarn install --frozen-lockfile && yarn build && yarn test --coverage && "
                   "把构建产物上传到 s3://releases/2026-10/ 并通知 #deploy 频道，完成后清理临时目录");
    snap.prompt_hint_truncated = true;
    show("31_approval_long", &snap);
    pocket_ui_scroll(-48);
    save("32_approval_long_scrolled");
    snap.approval_locked = true;
    snap.permission_decision = BUDDY_PERMISSION_ONCE;
    snap.permission_delivery = BUDDY_PERMISSION_DELIVERY_SENDING;
    show("33_approval_sending", &snap);
    snap.permission_delivery = BUDDY_PERMISSION_DELIVERY_SENT;
    show("34_approval_sent", &snap);
    snap.permission_delivery = BUDDY_PERMISSION_DELIVERY_FAILED;
    show("35_approval_failed", &snap);
    snap.permission_decision = BUDDY_PERMISSION_DENY;
    snap.permission_delivery = BUDDY_PERMISSION_DELIVERY_SENT;
    show("36_approval_denied", &snap);

    /* ---- the menu and what is under it ---- */
    snap = hub_snapshot();
    (void)snprintf(snap.entries[0], BUDDY_ENTRY_MAX, "%s", "10:42 Claude：部署完成，3 个服务已更新");
    (void)snprintf(snap.entries[1], BUDDY_ENTRY_MAX, "%s", "10:36 Codex：评审完成，2 条建议");
    snap.page = BUDDY_PAGE_MENU;
    for (index = 0; index < BUDDY_MENU_COUNT; index += 3) {
        char name[32];

        snap.menu_selection = (buddy_menu_item_t)index;
        (void)snprintf(name, sizeof(name), "37_menu_%d", index);
        show(name, &snap);
    }
    snap.page = BUDDY_PAGE_NOTICES;
    show("38_notices", &snap);
    memset(snap.entries, 0, sizeof(snap.entries));
    show("39_notices_empty", &snap);
    snap.page = BUDDY_PAGE_HELPERS;
    show("40_helpers", &snap);
    snap.helper_count = 0;
    show("41_helpers_empty", &snap);
    snap.page = BUDDY_PAGE_MORE;
    snap.more_selection = BUDDY_MORE_GUIDE;
    show("42_more", &snap);
    snap.more_selection = BUDDY_MORE_FACTORY_RESET;
    show("43_more_destructive", &snap);
    snap.page = BUDDY_PAGE_GUIDE;
    show("44_guide_top", &snap);
    pocket_ui_scroll(60);
    pocket_ui_scroll(60);
    save("45_guide_scrolled");

    snap = hub_snapshot();
    snap.page = BUDDY_PAGE_MORE;
    snap.confirmation = BUDDY_CONFIRM_UNPAIR;
    snap.confirmation_pending = true;
    show("46_confirm_unpair", &snap);
    snap.confirmation = BUDDY_CONFIRM_FACTORY_RESET;
    show("47_confirm_factory", &snap);

    /* ---- connected to the Claude desktop app instead of the hub ---- */
    snap = live_snapshot();
    show("48_desktop_quiet", &snap);
    snap.running = 1;
    snap.character = BUDDY_CHARACTER_BUSY;
    set_text_field(snap.message, sizeof(snap.message),
                   "正在重构支付模块的重试逻辑，并补充单元测试覆盖边界情况");
    show("49_desktop_busy", &snap);
    snap.running = 0;
    snap.message[0] = '\0';
    set_text_field(snap.reply, sizeof(snap.reply),
                   "已经把重试逻辑改成指数退避，最多 5 次，并为超时、限流、网络中断三种情况各补了一个测试。"
                   "全部 42 个用例通过。");
    show("50_desktop_reply", &snap);
    (void)buddy_history_push(&s_history, "", snap.reply, 0);
    snap.history = &s_history;
    snap.turn_serial = 1;
    set_text_field(snap.reply, sizeof(snap.reply), "顺手把 README 里的示例也更新了。");
    show("50b_desktop_second_reply", &snap);
    snap.history = NULL;
    snap.reply[0] = '\0';
    set_text_field(snap.message, sizeof(snap.message), "这个连接不能传语音");
    show("51_desktop_notice", &snap);

    {
        lv_mem_monitor_t monitor;

        lv_mem_monitor(&monitor);
        /* 主机是 64 位，对象比固件里大；这个峰值可当作固件 LVGL 内存池的保守上限。 */
        printf("ui_preview: LVGL heap peak %u bytes (host, 64-bit)\n",
               (unsigned)monitor.max_used);
    }
    puts("ui_preview: done");
    return 0;
}
