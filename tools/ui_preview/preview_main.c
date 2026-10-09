// tools/ui_preview/preview_main.c —— 在主机上用真实的 LVGL 和字库把每个界面
// 渲染成图片，并导出各字库实际能取到字形的码点，供开发时目检和核对覆盖率。
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "lvgl.h"

#include "buddy_cards.h"
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

static buddy_cards_t s_cards;

static void put_card(const char *id, const char *at, buddy_card_state_t state, const char *agent,
                     unsigned edits, const char *said, const char *reply, bool cut)
{
    buddy_card_update_t update = {.state = state, .edits = (uint8_t)edits};

    (void)snprintf(update.id, sizeof(update.id), "%s", id);
    (void)snprintf(update.at, sizeof(update.at), "%s", at);
    (void)snprintf(update.agent, sizeof(update.agent), "%s", agent);
    (void)buddy_cards_put(&s_cards, &update, said, reply, cut);
}

static void put_task(buddy_ui_snapshot_t *snap, const char *id, const char *agent,
                     const char *title, buddy_task_state_t state, unsigned seconds,
                     const char *line1, const char *line2)
{
    buddy_task_t *task = &snap->tasks[snap->task_count++];

    (void)snprintf(task->id, sizeof(task->id), "%s", id);
    (void)snprintf(task->agent, sizeof(task->agent), "%s", agent);
    (void)snprintf(task->title, sizeof(task->title), "%s", title);
    (void)snprintf(task->line1, sizeof(task->line1), "%s", line1);
    (void)snprintf(task->line2, sizeof(task->line2), "%s", line2);
    task->state = state;
    task->seconds = seconds;
    snap->tasks_since_ms = snap->uptime_ms;
}

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

    /* ---- first screen: Xiaoyou and what she is doing ---- */
    snap = hub_snapshot();
    snap.doing = 2;
    show("10_home_quiet_doing", &snap);
    snap.doing = 0;
    snap.chat.phase = BUDDY_CHAT_SENT;
    snap.chat.mood = BUDDY_MOOD_BUSY;
    snap.chat_since_ms = snap.uptime_ms - 1200U;
    show("11_home_sent", &snap);
    snap.chat.phase = BUDDY_CHAT_THINKING;
    set_text_field(snap.chat.said, sizeof(snap.chat.said), SAID);
    snap.chat_since_ms = snap.uptime_ms - 5300U;
    show("12_home_thinking", &snap);
    snap.chat.phase = BUDDY_CHAT_HELPER;
    set_text_field(snap.chat.agent, sizeof(snap.chat.agent), "codex");
    set_text_field(snap.chat.stage, sizeof(snap.chat.stage), "retry 的重试次数");
    snap.chat_since_ms = snap.uptime_ms - 12400U;
    snap.doing = 1;
    show("13_home_helper", &snap);
    set_text_field(snap.chat.agent, sizeof(snap.chat.agent), "书房电脑");
    set_text_field(snap.chat.stage, sizeof(snap.chat.stage),
                   "把上个月的报销单据整理成一张表，按日期排好再发给我");
    snap.chat_since_ms = snap.uptime_ms - 75000U;
    snap.doing = 3;
    show("14_home_helper_other", &snap);
    set_text_field(snap.chat.agent, sizeof(snap.chat.agent), "a-very-long-agent-name");
    show("15_home_helper_long_name", &snap);
    snap.chat.phase = BUDDY_CHAT_DONE;
    snap.chat.mood = BUDDY_MOOD_HAPPY;
    snap.chat.agent[0] = '\0';
    snap.doing = 2;
    set_text_field(snap.reply, sizeof(snap.reply),
                   "找到了：每次失败计数加了 2，所以 n=3 只会试 2 次。改成加 1 就好，要不要我一起改？");
    show("16_home_answered", &snap);
    snap.chat.mood = BUDDY_MOOD_ASK;
    set_text_field(snap.reply, sizeof(snap.reply), "改好了。要不要顺手把测试也跑一遍？");
    snap.doing = 0;
    show("17_home_answered_ask", &snap);
    snap.chat.phase = BUDDY_CHAT_FAILED;
    snap.chat.mood = BUDDY_MOOD_OOPS;
    set_text_field(snap.reply, sizeof(snap.reply), "连不上 Runtime：电脑可能睡着了");
    show("18_home_failed", &snap);
    set_text_field(snap.message, sizeof(snap.message), "按住确认键再说话哦");
    snap.message_since_ms = snap.uptime_ms - 1000U;
    show("19_home_notice", &snap);

    /* ---- second screen: the conversation, one thing at a time ---- */
    snap = hub_snapshot();
    snap.page = BUDDY_PAGE_TALK;
    snap.cards = &s_cards;
    snap.card_index = -1;
    show("20_talk_empty", &snap);
    /* Something was just said and has no card yet. */
    snap.card_live = true;
    snap.card_serial = 1;
    snap.chat.phase = BUDDY_CHAT_SENT;
    snap.chat.mood = BUDDY_MOOD_BUSY;
    snap.chat_since_ms = snap.uptime_ms - 1200U;
    show("21_talk_live_sent", &snap);
    snap.chat.phase = BUDDY_CHAT_THINKING;
    set_text_field(snap.chat.said, sizeof(snap.chat.said), SAID);
    snap.chat_since_ms = snap.uptime_ms - 5300U;
    show("22_talk_live_thinking", &snap);
    snap.chat.phase = BUDDY_CHAT_FAILED;
    snap.chat.mood = BUDDY_MOOD_OOPS;
    snap.chat.said[0] = '\0';
    set_text_field(snap.reply, sizeof(snap.reply), "没听清，再说一次吧");
    show("23_talk_live_failed", &snap);

    /* Its card arrives: a helper is on it. */
    snap = hub_snapshot();
    snap.page = BUDDY_PAGE_TALK;
    snap.cards = &s_cards;
    put_card("c1", "09:30", BUDDY_CARD_DONE, "", 0, "今天几号", "10 月 9 号，星期五。", false);
    put_card("c2", "14:02", BUDDY_CARD_WORKING, "codex", 0, SAID,
             "这个我让 codex 仔细看一遍，稍等", false);
    snap.card_index = 1;
    snap.card_serial = 2;
    snap.doing = 1;
    put_task(&snap, "c2", "codex", "retry 的重试次数", BUDDY_TASK_WORKING, 42,
             "rg -n \"attempts\" src/", "sed -n 30,60p src/retry.py");
    show("24_talk_card_working", &snap);
    put_card("c2", "14:02", BUDDY_CARD_WAITING, "codex", 0, SAID,
             "这个我让 codex 仔细看一遍，稍等", false);
    snap.tasks[0].state = BUDDY_TASK_WAITING;
    show("25_talk_card_waiting", &snap);
    /* Done: a long answer starts at its beginning; DOWN reads on, seven lines at a
     * time, and past its end asks for the next card. */
    put_card("c2", "14:02", BUDDY_CARD_DONE, "codex", 0, SAID, ANSWER, true);
    snap.task_count = 0;
    snap.doing = 0;
    show("26_talk_card_done", &snap);
    printf("scroll down: edge %d\n", pocket_ui_scroll(60));
    show("27_talk_card_done_down", &snap);
    printf("scroll down: edge %d\n", pocket_ui_scroll(60));
    show("28_talk_card_done_end", &snap);
    printf("scroll down at the end: edge %d (1 means next card)\n", pocket_ui_scroll(60));
    /* The next card: she answered herself. */
    put_card("c3", "14:20", BUDDY_CARD_DONE, "", 0, "一打鸡蛋是几个", "12 个。", false);
    snap.card_index = 2;
    snap.card_serial = 3;
    show("29_talk_card_self", &snap);
    printf("scroll up on a short card: edge %d (-1 means previous card)\n",
           pocket_ui_scroll(-60));
    put_card("c4", "14:31", BUDDY_CARD_DONE, "claude", 2, "把周报改成三段",
             "改好了：进展、风险、下周计划各一段，一共 280 字。", false);
    snap.card_index = 3;
    snap.card_serial = 4;
    show("30_talk_card_edited", &snap);
    put_card("c5", "14:40", BUDDY_CARD_FAILED, "codex", 0, "跑一遍测试",
             "codex 没做成：连不上 Runtime：电脑可能睡着了", false);
    snap.card_index = 4;
    snap.card_serial = 5;
    show("31_talk_card_failed", &snap);
    put_card("c6", "14:44", BUDDY_CARD_CANCELLED, "claude", 0, "帮我跑一下 sleep 60",
             "好，sleep 60 那件事我取消掉了", false);
    snap.card_index = 5;
    snap.card_serial = 6;
    show("32_talk_card_cancelled", &snap);
    put_card("c7", "14:50", BUDDY_CARD_DONE, "a-very-long-agent-name", 0, "现在呢",
             "Done. All 42 tests pass (pytest -q). 龘 is outside the font.", false);
    snap.card_index = 6;
    snap.card_serial = 7;
    show("33_talk_card_latin_and_missing_glyph", &snap);
    put_card("c8", "15:05", BUDDY_CARD_WORKING, "a-very-long-agent-name", 1,
             "再查一下上周的", "交给它了", false);
    snap.card_index = 7;
    snap.card_serial = 8;
    snap.doing = 1;
    show("34_talk_card_working_long_name", &snap);
    snap.doing = 0;
    set_text_field(snap.message, sizeof(snap.message), "按住确认键再说话哦");
    snap.message_since_ms = snap.uptime_ms - 1000U;
    show("35_talk_notice", &snap);
    snap.message[0] = '\0';
    /* The link drops: the cards stay readable. */
    snap.ble_connected = false;
    snap.ble_encrypted = false;
    snap.connection = BUDDY_CONNECTION_OFFLINE;
    snap.host_hub = false;
    snap.host_chat = false;
    snap.card_index = 1;
    snap.card_serial = 9;
    show("36_talk_offline", &snap);

    /* ---- third screen: the things in progress ---- */
    snap = hub_snapshot();
    snap.page = BUDDY_PAGE_TASKS;
    snap.cards = &s_cards;
    show("40_tasks_empty", &snap);
    put_task(&snap, "c2", "codex", "retry 的重试次数", BUDDY_TASK_WORKING, 42, "", "");
    snap.doing = 1;
    show("41_tasks_one_no_steps", &snap);
    snap.tasks[0].seconds = 95;
    set_text_field(snap.tasks[0].line1, sizeof(snap.tasks[0].line1), "rg -n \"attempts\" src/");
    show("42_tasks_one_step", &snap);
    set_text_field(snap.tasks[0].line2, sizeof(snap.tasks[0].line2),
                   "sed -n 30,60p src/retry.py");
    put_task(&snap, "c8", "claude", "把上个月的报销单据整理成一张表", BUDDY_TASK_WAITING, 310,
             "Read /Users/me/Documents/报销/2026-09.xlsx",
             "Write /Users/me/Documents/报销/汇总.md");
    put_task(&snap, "c9", "a-very-long-agent-name", "查上周的会议纪要", BUDDY_TASK_WORKING, 3725,
             "搜索 上周 会议纪要",
             "Bash cd /Users/me/notes && rg -l \"会议纪要\" --glob \"2026-10-0*\" | head -20");
    put_task(&snap, "c10", "codex", "跑一遍全部测试", BUDDY_TASK_QUEUED, 0, "", "");
    snap.doing = 4;
    show("43_tasks_four", &snap);
    snap.task_selected = 1;
    show("44_tasks_selected_waiting", &snap);
    snap.task_selected = 2;
    show("45_tasks_selected_long", &snap);
    snap.task_selected = 3;
    show("46_tasks_selected_queued", &snap);
    snap.doing = 6;
    snap.uptime_ms += 61000U;
    show("47_tasks_a_minute_later", &snap);
    memset(&s_cards, 0, sizeof(s_cards));
    snap = hub_snapshot();
    pocket_ui_render(&snap);

    /* ---- something needs a yes or no ---- */
    snap = hub_snapshot();
    (void)snprintf(snap.prompt_id, sizeof(snap.prompt_id), "%s", "req_abc123");
    (void)snprintf(snap.prompt_tool, sizeof(snap.prompt_tool), "%s", "claude · Bash");
    (void)snprintf(snap.prompt_hint, sizeof(snap.prompt_hint), "%s", "rm -rf /tmp/foo");
    show("50_approval_short", &snap);
    /* The prompt covers whichever screen was showing. */
    snap.page = BUDDY_PAGE_TASKS;
    (void)snprintf(snap.prompt_id, sizeof(snap.prompt_id), "%s", "a7");
    (void)snprintf(snap.prompt_tool, sizeof(snap.prompt_tool), "%s", "codex · 改文件");
    (void)snprintf(snap.prompt_hint, sizeof(snap.prompt_hint), "%s",
                   "update /Users/me/project/src/retry.py\n-    attempts += 2\n+    attempts += 1");
    show("50b_approval_file_change", &snap);
    snap.page = BUDDY_PAGE_HOME;
    (void)snprintf(snap.prompt_id, sizeof(snap.prompt_id), "%s", "req_long");
    (void)snprintf(snap.prompt_tool, sizeof(snap.prompt_tool), "%s",
                   "mcp__filesystem__write_file_with_a_long_name");
    (void)snprintf(snap.prompt_hint, sizeof(snap.prompt_hint), "%s",
                   "cd /Users/me/project && git fetch --all && git rebase origin/main && "
                   "yarn install --frozen-lockfile && yarn build && yarn test --coverage && "
                   "把构建产物上传到 s3://releases/2026-10/ 并通知 #deploy 频道，完成后清理临时目录");
    snap.prompt_hint_truncated = true;
    show("51_approval_long", &snap);
    pocket_ui_scroll(-48);
    save("52_approval_long_scrolled");
    snap.approval_locked = true;
    snap.permission_decision = BUDDY_PERMISSION_ONCE;
    snap.permission_delivery = BUDDY_PERMISSION_DELIVERY_SENDING;
    show("53_approval_sending", &snap);
    snap.permission_delivery = BUDDY_PERMISSION_DELIVERY_SENT;
    show("54_approval_sent", &snap);
    snap.permission_delivery = BUDDY_PERMISSION_DELIVERY_FAILED;
    show("55_approval_failed", &snap);
    snap.permission_decision = BUDDY_PERMISSION_DENY;
    snap.permission_delivery = BUDDY_PERMISSION_DELIVERY_SENT;
    show("56_approval_denied", &snap);

    /* ---- the menu and what is under it ---- */
    snap = hub_snapshot();
    (void)snprintf(snap.entries[0], BUDDY_ENTRY_MAX, "%s", "10:42 Claude：部署完成，3 个服务已更新");
    (void)snprintf(snap.entries[1], BUDDY_ENTRY_MAX, "%s", "10:36 Codex：评审完成，2 条建议");
    snap.page = BUDDY_PAGE_MENU;
    for (index = 0; index < BUDDY_MENU_COUNT; index += 3) {
        char name[32];

        snap.menu_selection = (buddy_menu_item_t)index;
        (void)snprintf(name, sizeof(name), "60_menu_%d", index);
        show(name, &snap);
    }
    snap.page = BUDDY_PAGE_NOTICES;
    show("61_notices", &snap);
    memset(snap.entries, 0, sizeof(snap.entries));
    show("62_notices_empty", &snap);
    snap.page = BUDDY_PAGE_HELPERS;
    show("63_helpers", &snap);
    snap.helper_count = 0;
    show("64_helpers_empty", &snap);
    snap.page = BUDDY_PAGE_MORE;
    snap.more_selection = BUDDY_MORE_GUIDE;
    show("65_more", &snap);
    snap.more_selection = BUDDY_MORE_FACTORY_RESET;
    show("66_more_destructive", &snap);
    snap.page = BUDDY_PAGE_GUIDE;
    show("67_guide_top", &snap);
    pocket_ui_scroll(60);
    pocket_ui_scroll(60);
    save("68_guide_scrolled");

    snap = hub_snapshot();
    snap.page = BUDDY_PAGE_MORE;
    snap.confirmation = BUDDY_CONFIRM_UNPAIR;
    snap.confirmation_pending = true;
    show("69_confirm_unpair", &snap);
    snap.confirmation = BUDDY_CONFIRM_FACTORY_RESET;
    show("70_confirm_factory", &snap);

    /* ---- connected to the Claude desktop app instead of the hub ---- */
    snap = live_snapshot();
    show("80_desktop_quiet", &snap);
    snap.running = 1;
    snap.character = BUDDY_CHARACTER_BUSY;
    set_text_field(snap.message, sizeof(snap.message),
                   "正在重构支付模块的重试逻辑，并补充单元测试覆盖边界情况");
    show("81_desktop_busy", &snap);
    snap.running = 0;
    snap.message[0] = '\0';
    set_text_field(snap.reply, sizeof(snap.reply),
                   "已经把重试逻辑改成指数退避，最多 5 次，并为超时、限流、网络中断三种情况各补了一个测试。"
                   "全部 42 个用例通过。");
    show("82_desktop_reply", &snap);
    /* No cards from this host: the conversation screen shows its latest reply,
     * and the third screen has nothing to list. */
    snap.page = BUDDY_PAGE_TALK;
    snap.card_live = true;
    snap.card_index = -1;
    snap.card_serial = 20;
    show("83_desktop_talk", &snap);
    snap.page = BUDDY_PAGE_TASKS;
    show("84_desktop_tasks", &snap);
    snap.page = BUDDY_PAGE_HOME;
    snap.card_live = false;
    snap.reply[0] = '\0';
    set_text_field(snap.message, sizeof(snap.message), "这个连接不能传语音");
    show("85_desktop_notice", &snap);

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
