// tools/ui_preview/preview_main.c —— 在主机上用真实的 LVGL 和字库把每个界面
// 渲染成图片，并导出各字库实际能取到字形的码点，供开发时目检和核对覆盖率。
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "lvgl.h"

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
    snap.total = 3;
    snap.tokens = 184502;
    snap.tokens_today = 31200;
    snap.approval_count = 42;
    snap.denial_count = 3;
    return snap;
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

    snap = live_snapshot();
    show("06_home_idle", &snap);
    snap.running = 1;
    snap.character = BUDDY_CHARACTER_BUSY;
    (void)snprintf(snap.message, sizeof(snap.message), "%s",
                   "正在重构支付模块的重试逻辑，并补充单元测试覆盖边界情况");
    show("07_home_busy", &snap);
    snap.waiting = 1;
    (void)snprintf(snap.message, sizeof(snap.message), "%s", "approve: Bash");
    show("08_home_pending", &snap);
    snap.waiting = 0;
    snap.character = BUDDY_CHARACTER_HEART;
    show("09_home_approved", &snap);

    snap = live_snapshot();
    snap.page = BUDDY_PAGE_REPLY;
    show("10_reply_empty", &snap);
    (void)snprintf(snap.reply, sizeof(snap.reply), "%s",
                   "已经把重试逻辑改成指数退避，最多 5 次，并为超时、限流、网络中断三种情况各补了一个测试。"
                   "全部 42 个用例通过。下一步建议把 retry_policy 的配置项写进 README，"
                   "然后我可以继续处理 webhook 的幂等问题，需要我现在开始吗？");
    snap.reply_truncated = true;
    show("11_reply_long", &snap);
    (void)snprintf(snap.reply, sizeof(snap.reply), "%s",
                   "Done. All 42 tests pass (pytest -q). 龘 is outside the font.");
    snap.reply_truncated = false;
    show("12_reply_latin_and_missing_glyph", &snap);

    snap = live_snapshot();
    snap.page = BUDDY_PAGE_ACTIVITY;
    show("13_activity_empty", &snap);
    (void)snprintf(snap.entries[0], BUDDY_ENTRY_MAX, "%s", "10:42 git push origin feature/retry");
    (void)snprintf(snap.entries[1], BUDDY_ENTRY_MAX, "%s", "10:41 运行 yarn test，42 个用例全部通过");
    (void)snprintf(snap.entries[2], BUDDY_ENTRY_MAX, "%s", "10:39 编辑 src/payments/retry.ts");
    (void)snprintf(snap.entries[3], BUDDY_ENTRY_MAX, "%s", "10:36 读取 README.md");
    show("14_activity", &snap);

    snap.page = BUDDY_PAGE_USAGE;
    show("15_usage", &snap);
    snap.battery_available = false;
    snap.tokens = 123456789;
    snap.tokens_today = 0;
    show("16_usage_no_battery", &snap);

    snap = live_snapshot();
    snap.page = BUDDY_PAGE_SETTINGS;
    for (index = 0; index < BUDDY_SETTINGS_COUNT; index += 3) {
        char name[32];

        snap.settings_selection = (buddy_settings_item_t)index;
        (void)snprintf(name, sizeof(name), "17_settings_%d", index);
        show(name, &snap);
    }
    snap.page = BUDDY_PAGE_GUIDE;
    show("18_guide_top", &snap);
    pocket_ui_scroll(60);
    pocket_ui_scroll(60);
    save("19_guide_scrolled");

    snap = live_snapshot();
    snap.waiting = 1;
    (void)snprintf(snap.prompt_id, sizeof(snap.prompt_id), "%s", "req_abc123");
    (void)snprintf(snap.prompt_tool, sizeof(snap.prompt_tool), "%s", "Bash");
    (void)snprintf(snap.prompt_hint, sizeof(snap.prompt_hint), "%s", "rm -rf /tmp/foo");
    show("20_approval_short", &snap);
    (void)snprintf(snap.prompt_id, sizeof(snap.prompt_id), "%s", "req_long");
    (void)snprintf(snap.prompt_tool, sizeof(snap.prompt_tool), "%s",
                   "mcp__filesystem__write_file_with_a_long_name");
    (void)snprintf(snap.prompt_hint, sizeof(snap.prompt_hint), "%s",
                   "cd /Users/me/project && git fetch --all && git rebase origin/main && "
                   "yarn install --frozen-lockfile && yarn build && yarn test --coverage && "
                   "把构建产物上传到 s3://releases/2026-10/ 并通知 #deploy 频道，完成后清理临时目录");
    snap.prompt_hint_truncated = true;
    show("21_approval_long", &snap);
    pocket_ui_scroll(-48);
    save("22_approval_long_scrolled");
    snap.approval_locked = true;
    snap.permission_decision = BUDDY_PERMISSION_ONCE;
    snap.permission_delivery = BUDDY_PERMISSION_DELIVERY_SENDING;
    show("23_approval_sending", &snap);
    snap.permission_delivery = BUDDY_PERMISSION_DELIVERY_SENT;
    show("24_approval_sent", &snap);
    snap.permission_delivery = BUDDY_PERMISSION_DELIVERY_FAILED;
    show("25_approval_failed", &snap);
    snap.permission_decision = BUDDY_PERMISSION_DENY;
    snap.permission_delivery = BUDDY_PERMISSION_DELIVERY_SENT;
    show("25b_approval_denied", &snap);

    snap = live_snapshot();
    snap.page = BUDDY_PAGE_SETTINGS;
    snap.confirmation = BUDDY_CONFIRM_UNPAIR;
    snap.confirmation_pending = true;
    show("26_confirm_unpair", &snap);
    snap.confirmation = BUDDY_CONFIRM_FACTORY_RESET;
    show("27_confirm_factory", &snap);

    snap = live_snapshot();
    snap.voice_phase = BUDDY_VOICE_PREPARING;
    show("28_voice_preparing", &snap);
    snap.voice_phase = BUDDY_VOICE_LISTENING;
    snap.voice_listening_since_ms = snap.uptime_ms - 7300U;
    show("29_voice_listening", &snap);
    snap.voice_phase = BUDDY_VOICE_SENDING;
    show("30_voice_sending", &snap);
    snap = live_snapshot();
    (void)snprintf(snap.message, sizeof(snap.message), "%s",
                   "这个连接不能传语音\n要连手机上的小幽中枢");
    show("31_home_voice_no_host", &snap);
    (void)snprintf(snap.message, sizeof(snap.message), "%s", "发出去啦，等小幽回话");
    show("32_home_voice_sent", &snap);

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
