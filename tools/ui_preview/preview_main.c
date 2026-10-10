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
#include "pocket_text.h"
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

/* A thing that ended, for the lower part of the third screen. */
static void put_past(buddy_ui_snapshot_t *snap, const char *id, const char *agent,
                     const char *title, buddy_task_state_t state, const char *day,
                     buddy_effort_t effort)
{
    static buddy_past_t s_past[BUDDY_PAST_COUNT];
    buddy_past_t *past = &s_past[snap->past_count++];

    snap->past = s_past;
    (void)snprintf(past->id, sizeof(past->id), "%s", id);
    (void)snprintf(past->agent, sizeof(past->agent), "%s", agent);
    (void)snprintf(past->title, sizeof(past->title), "%s", title);
    (void)snprintf(past->day, sizeof(past->day), "%s", day);
    past->state = (uint8_t)state;
    past->effort = (uint8_t)effort;
}

/* One entry of the usage screen with one helper; add_who() adds a second one. */
static buddy_usage_t *put_usage(buddy_ui_snapshot_t *snap, buddy_quota_t state, int left_short,
                                int left_week, const char *name, const char *model,
                                buddy_effort_t effort, unsigned running)
{
    buddy_usage_t *usage = &snap->usage[snap->usage_count++];
    /* The snapshot's clock is 2026-09-21 21:33 in Beijing: the short window resets
     * later the same evening, the weekly one on Thursday morning. */
    const uint32_t now = (uint32_t)snap->epoch_seconds;

    memset(usage, 0, sizeof(*usage));
    usage->state = (uint8_t)state;
    usage->left_short = (int8_t)left_short;
    usage->left_week = (int8_t)left_week;
    if (state != BUDDY_QUOTA_NONE) {
        usage->reset_short = now + 107U * 60U;
        usage->reset_week = now + 3U * 86400U - 12U * 3600U - 33U * 60U;
    }
    usage->age = 200;
    usage->who_count = 1;
    (void)snprintf(usage->who[0].name, sizeof(usage->who[0].name), "%s", name);
    (void)snprintf(usage->who[0].model, sizeof(usage->who[0].model), "%s", model);
    usage->who[0].effort = (uint8_t)effort;
    usage->who[0].running = (uint8_t)running;
    snap->usage_known = true;
    snap->usage_since_ms = snap->uptime_ms;
    return usage;
}

static void add_who(buddy_usage_t *usage, const char *name, const char *model,
                    buddy_effort_t effort, unsigned running)
{
    buddy_usage_who_t *who = &usage->who[usage->who_count++];

    (void)snprintf(who->name, sizeof(who->name), "%s", name);
    (void)snprintf(who->model, sizeof(who->model), "%s", model);
    who->effort = (uint8_t)effort;
    who->running = (uint8_t)running;
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
    /* Things that ended stay on the list, after the ones in progress; the one
     * picked shows how it ended (from its card, else from the list's own line). */
    (void)snprintf(snap.tasks[2].id, sizeof(snap.tasks[2].id), "%s", "c4");
    (void)snprintf(snap.tasks[2].agent, sizeof(snap.tasks[2].agent), "%s", "claude");
    set_text_field(snap.tasks[2].title, sizeof(snap.tasks[2].title), "把周报改成三段");
    snap.tasks[2].state = BUDDY_TASK_DONE;
    (void)snprintf(snap.tasks[3].id, sizeof(snap.tasks[3].id), "%s", "c5");
    set_text_field(snap.tasks[3].title, sizeof(snap.tasks[3].title), "跑一遍测试");
    snap.tasks[3].state = BUDDY_TASK_FAILED;
    snap.doing = 2;
    snap.task_selected = 2;
    show("48_tasks_ended_done", &snap);
    snap.task_selected = 3;
    show("49_tasks_ended_failed", &snap);
    (void)snprintf(snap.tasks[3].id, sizeof(snap.tasks[3].id), "%s", "c99");
    set_text_field(snap.tasks[3].line1, sizeof(snap.tasks[3].line1), "取消了");
    snap.tasks[3].state = BUDDY_TASK_CANCELLED;
    show("50_tasks_ended_cancelled_no_card", &snap);
    /* OK on a picked thing: that thing's own page, opened at its newest end. */
    put_card("c4", "14:31", BUDDY_CARD_DONE, "claude", 2, "把周报改成三段",
             "小幽：交给 claude 了\n\n你：每段不要超过一百字\n\n小幽：好，告诉 claude 了\n\n"
             "你：标题也改一下，叫十月第一周\n\n"
             "小幽：改好了：进展、风险、下周计划各一段，一共 280 字，标题是十月第一周。",
             false);
    snap.page = BUDDY_PAGE_TALK;
    snap.card_thread = true;
    snap.task_selected = 2;
    snap.card_index = buddy_cards_find(&s_cards, "c4");
    snap.card_serial = 20;
    show("51_task_page_done", &snap);
    printf("scroll up inside a task: edge %d\n", pocket_ui_scroll(-60));
    show("52_task_page_scrolled_up", &snap);
    snap.task_selected = 0;
    snap.card_index = buddy_cards_find(&s_cards, "c2");
    snap.card_serial = 21;
    show("53_task_page_working", &snap);
    snap.task_selected = 3;
    snap.card_index = -1;
    snap.card_serial = 22;
    show("54_task_page_no_card", &snap);
    snap.page = BUDDY_PAGE_TASKS;
    snap.card_thread = false;
    snap.task_selected = 2;
    show("55_tasks_hint_open", &snap);
    memset(&s_cards, 0, sizeof(s_cards));
    /* The history: things that ended stay under the ones in progress, grouped by
     * the day they ended on. The list is a window that follows the selection. */
    snap = hub_snapshot();
    snap.cards = &s_cards;
    snap.page = BUDDY_PAGE_TASKS;
    snap.doing = 2;
    put_task(&snap, "c61", "codex", "看 retry 的重试次数", BUDDY_TASK_WORKING, 48, "git diff",
             "pytest -q tests/test_retry.py");
    snap.tasks[0].effort = (uint8_t)BUDDY_EFFORT_HIGH;
    put_task(&snap, "c62", "claude", "整理上周的会议记录", BUDDY_TASK_WAITING, 300,
             "Read notes.md", "");
    {
        static const struct {
            const char *id;
            const char *agent;
            const char *title;
            buddy_task_state_t state;
            const char *day;
            buddy_effort_t effort;
        } ended[] = {
            {"c60", "deepseek", "查一下明天的天气", BUDDY_TASK_DONE, "今天", BUDDY_EFFORT_NONE},
            {"c59", "tailor", "把字调大一号", BUDDY_TASK_DONE, "今天", BUDDY_EFFORT_LOW},
            {"c58", "codex", "跑一遍全部测试", BUDDY_TASK_FAILED, "今天", BUDDY_EFFORT_MEDIUM},
            {"c57", "claude", "把周报改成三段", BUDDY_TASK_DONE, "昨天", BUDDY_EFFORT_MEDIUM},
            {"c56", "codex", "看看登录为什么慢", BUDDY_TASK_CANCELLED, "昨天",
             BUDDY_EFFORT_HIGH},
            {"c55", "deepseek", "查 Notion 的登录接法", BUDDY_TASK_DONE, "昨天",
             BUDDY_EFFORT_NONE},
            {"c54", "claude", "把上个月的报销单据整理成一张表", BUDDY_TASK_DONE, "10-08",
             BUDDY_EFFORT_MEDIUM},
            {"c53", "codex", "给接口补测试", BUDDY_TASK_DONE, "10-08", BUDDY_EFFORT_LOW},
            {"c52", "tailor", "待机文案改成待命中", BUDDY_TASK_DONE, "10-07",
             BUDDY_EFFORT_NONE},
            {"c51", "codex", "第一次试 app-server", BUDDY_TASK_FAILED, "10-06",
             BUDDY_EFFORT_MEDIUM},
            {"c50", "claude", "看一下蓝牙换固件的方案", BUDDY_TASK_DONE, "10-06",
             BUDDY_EFFORT_HIGH},
            {"c49", "claude", "语音识别用哪个引擎", BUDDY_TASK_DONE, "10-05",
             BUDDY_EFFORT_MEDIUM},
            {"c48", "codex", "把构建搬到本机", BUDDY_TASK_DONE, "10-05", BUDDY_EFFORT_MEDIUM},
            {"c47", "deepseek", "查任务为什么串行", BUDDY_TASK_DONE, "10-04",
             BUDDY_EFFORT_NONE},
            {"c46", "claude", "最早的一件", BUDDY_TASK_CANCELLED, "10-03", BUDDY_EFFORT_LOW},
        };
        unsigned index;

        for (index = 0; index < sizeof(ended) / sizeof(ended[0]); ++index) {
            put_past(&snap, ended[index].id, ended[index].agent, ended[index].title,
                     ended[index].state, ended[index].day, ended[index].effort);
        }
    }
    snap.task_selected = 0;
    show("56_history_top", &snap);
    snap.task_selected = 2;
    show("57_history_first_ended", &snap);
    snap.task_selected = 4;
    show("58_history_failed", &snap);
    snap.task_selected = 5;
    show("59_history_next_day", &snap);
    snap.task_selected = 9;
    show("60_history_scrolled", &snap);
    snap.task_selected = 16;
    show("61_history_last", &snap);
    snap.task_selected = 6;
    show("62_history_back_up", &snap);
    /* Opened, and its card is not on the device: what the list knows, until the
     * hub sends the card it was asked for. */
    snap.page = BUDDY_PAGE_TALK;
    snap.card_thread = true;
    snap.card_index = -1;
    snap.card_serial = 30;
    show("63_history_page_waiting_for_card", &snap);
    put_card("c56", "09:12", BUDDY_CARD_CANCELLED, "codex", 0, "看看登录为什么慢",
             "小幽：交给 codex 了\n\n你：先不看了\n\n小幽：好，停掉了。", false);
    snap.card_index = buddy_cards_find(&s_cards, "c56");
    snap.card_serial = 31;
    show("64_history_page_card", &snap);
    /* No things in progress: the history starts at the top. */
    snap.page = BUDDY_PAGE_TASKS;
    snap.card_thread = false;
    snap.task_count = 0;
    snap.doing = 0;
    snap.task_selected = 0;
    show("65_history_only", &snap);
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

    /* ---- a new firmware image arriving over Bluetooth ---- */
    snap = hub_snapshot();
    snap.update_phase = POCKET_UPDATE_RECEIVING;
    snap.update_percent = 0;
    show("71_update_start", &snap);
    snap.update_percent = 37;
    show("72_update_receiving", &snap);
    /* 这时来了要点头的事，也不打断进度。 */
    set_text_field(snap.prompt_id, sizeof(snap.prompt_id), "req-update");
    snap.update_percent = 99;
    show("73_update_almost", &snap);
    snap.prompt_id[0] = '\0';
    snap.update_phase = POCKET_UPDATE_CHECKING;
    show("74_update_checking", &snap);
    snap.update_phase = POCKET_UPDATE_RESTARTING;
    show("75_update_restarting", &snap);
    /* 没换成：回到平常的画面，底下那一行说一声。 */
    snap = hub_snapshot();
    snap.chat.phase = BUDDY_CHAT_DONE;
    set_text_field(snap.chat.said, sizeof(snap.chat.said), "把顶上那条改细一点");
    set_text_field(snap.reply, sizeof(snap.reply), "好，我去改。");
    set_text_field(snap.message, sizeof(snap.message), PT_UPDATE_FAILED);
    snap.message_since_ms = snap.uptime_ms;
    show("76_update_failed_notice", &snap);

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

    /* The fourth screen: what is left of each subscription and which model each
     * helper runs on. Only a hub that sends usage has it; the dots show four. */
    snap = hub_snapshot();
    snap.cards = &s_cards;
    snap.page = BUDDY_PAGE_USAGE;
    snap.usage_known = true;
    show("90_usage_empty", &snap);
    put_usage(&snap, BUDDY_QUOTA_OK, 58, 81, "codex", "gpt-6.1-sol", BUDDY_EFFORT_MEDIUM, 0);
    add_who(put_usage(&snap, BUDDY_QUOTA_OK, 76, 64, "claude", "sonnet-5-5", BUDDY_EFFORT_MEDIUM, 0),
            "tailor", "", BUDDY_EFFORT_HIGH, 0);
    put_usage(&snap, BUDDY_QUOTA_NONE, -1, -1, "deepseek", "deepseek-v4-pro", BUDDY_EFFORT_NONE, 0);
    show("91_usage", &snap);
    /* Somebody is working: its nameplate is filled and its model lights up. */
    snap.usage[0].who[0].running = 1;
    snap.usage[2].who[0].running = 1;
    snap.doing = 2;
    snap.uptime_ms += 9ULL * 60000ULL;
    show("92_usage_working", &snap);
    /* Nearly used up, used up, and a number that is not known. */
    snap.usage[0].who[0].running = 0;
    snap.usage[2].who[0].running = 0;
    snap.doing = 0;
    snap.usage[0].state = (uint8_t)BUDDY_QUOTA_WARN;
    snap.usage[0].left_short = 12;
    snap.usage[1].state = (uint8_t)BUDDY_QUOTA_OUT;
    snap.usage[1].left_short = 0;
    snap.usage[1].left_week = -1;
    snap.usage[1].reset_week = 0;
    snap.uptime_ms += 40ULL * 60000ULL;
    show("93_usage_low", &snap);
    /* A login nothing is known about yet, a long model name, levels at both ends. */
    snap.usage_count = 0;
    put_usage(&snap, BUDDY_QUOTA_UNKNOWN, -1, -1, "codex", "", BUDDY_EFFORT_XHIGH, 0);
    snap.usage[0].reset_short = 0;
    snap.usage[0].reset_week = 0;
    put_usage(&snap, BUDDY_QUOTA_OK, 100, 3, "一个名字很长的帮手", "a-model-with-a-long",
              BUDDY_EFFORT_MINIMAL, 1);
    snap.uptime_ms += 3ULL * 3600000ULL;
    show("94_usage_unknown", &snap);
    /* More than fits: four entries, two of them shared. The screen scrolls. */
    snap.usage_count = 0;
    add_who(put_usage(&snap, BUDDY_QUOTA_OK, 58, 81, "claude", "opus-5-5", BUDDY_EFFORT_HIGH, 1),
            "tailor", "sonnet-5-5", BUDDY_EFFORT_MEDIUM, 0);
    add_who(put_usage(&snap, BUDDY_QUOTA_WARN, 9, 40, "codex", "gpt-6.1-sol", BUDDY_EFFORT_MEDIUM, 0),
            "reviewer", "gpt-6.1-sol", BUDDY_EFFORT_LOW, 0);
    put_usage(&snap, BUDDY_QUOTA_NONE, -1, -1, "deepseek", "deepseek-v4-pro", BUDDY_EFFORT_NONE, 0);
    put_usage(&snap, BUDDY_QUOTA_NONE, -1, -1, "local", "qwen-9", BUDDY_EFFORT_NONE, 0);
    show("95_usage_long", &snap);
    (void)pocket_ui_scroll(1);
    show("96_usage_long_scrolled", &snap);

    /* The level a thing runs at, after the helper's name on the three nameplates. */
    snap = hub_snapshot();
    snap.cards = &s_cards;
    snap.usage_known = true;
    snap.chat.phase = BUDDY_CHAT_HELPER;
    snap.chat.mood = BUDDY_MOOD_BUSY;
    snap.chat.effort = (uint8_t)BUDDY_EFFORT_HIGH;
    snap.chat.doing = 2;
    set_text_field(snap.chat.said, sizeof(snap.chat.said), SAID);
    set_text_field(snap.chat.agent, sizeof(snap.chat.agent), "codex");
    set_text_field(snap.chat.stage, sizeof(snap.chat.stage), "看 retry 的重试次数");
    snap.chat_since_ms = snap.uptime_ms - 48000ULL;
    show("97_tier_home", &snap);
    snap.page = BUDDY_PAGE_TALK;
    snap.card_live = true;
    snap.card_index = -1;
    snap.card_serial = 40;
    show("98_tier_talk", &snap);
    snap.page = BUDDY_PAGE_TASKS;
    snap.card_live = false;
    put_task(&snap, "c21", "codex", "看 retry 的重试次数", BUDDY_TASK_WORKING, 48, "git diff",
             "pytest -q tests/test_retry.py");
    snap.tasks[0].effort = (uint8_t)BUDDY_EFFORT_HIGH;
    put_task(&snap, "c22", "claude", "整理上周的会议记录", BUDDY_TASK_WAITING, 300, "Read notes.md",
             "Write summary.md");
    snap.tasks[1].effort = (uint8_t)BUDDY_EFFORT_XHIGH;
    put_task(&snap, "c20", "deepseek", "查一下明天的天气", BUDDY_TASK_DONE, 0, "明天多云，18 到 24 度", "");
    put_task(&snap, "c19", "tailor", "把字调大一号", BUDDY_TASK_DONE, 0, "改好了，已经推到设备上", "");
    snap.tasks[3].effort = (uint8_t)BUDDY_EFFORT_LOW;
    show("99_tier_tasks", &snap);

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
