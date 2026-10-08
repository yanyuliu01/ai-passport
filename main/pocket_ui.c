// main/pocket_ui.c —— Claude Pocket 界面实现（240×320 竖屏，三键操作）。
//
// 结构：一块屏幕上常驻三层——顶栏、内容区、底部提示。内容区里每个页面和
// 每个浮层各有一个容器，render 时只切换显隐并更新变化的文字。
// 屏幕四角有半径 30 的圆角遮罩（BSP_LVGL_SCREEN_RADIUS），所以顶栏和底栏的
// 内容都向内收，不贴边。

#include "pocket_ui.h"

#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "lvgl.h"

#include "pocket_fonts.h"
#include "pocket_pet.h"
#include "pocket_text.h"
#include "pocket_view.h"

// ---- 配色：暖色深底，陶土色强调 ----
#define C_BG       0x1B1A18
#define C_CARD     0x2A2825
#define C_LINE     0x3A3733
#define C_TEXT     0xF3EFE6
#define C_DIM      0xA39E93
#define C_ACCENT   0xE08A63
#define C_OK       0x86C48A
#define C_WARN     0xEDB95E
#define C_DANGER   0xEA6F63
#define C_ON_COLOR 0x1B1A18  // 彩色底上的文字

// ---- 布局 ----
#define SCREEN_W   240
#define SCREEN_H   320
#define AREA_Y     34
#define AREA_H     248
#define SIDE       14
#define INNER_W    (SCREEN_W - 2 * SIDE)
#define LINE_SPACE 2
#define LINE_16    23   // pocket_font_16 行距：字高 21 + 行间 2
#define LINES_16(n) ((n) * LINE_16 - LINE_SPACE)  // n 行正文占的高度
#define DOT_Y      286
#define HINT_Y     297
// 底部圆角在提示这一行往里收约 12 像素，所以提示最宽 212。
#define HINT_W     212

#define SETTINGS_ROW_H 30
#define ENTRY_ROW_H    50
#define ENTRY_ROW_GAP  3
#define USAGE_ROWS     4

// 小幽在四个地方出现：首页的大个子，和三个对话场景里的头像。
enum {
    PET_HOME,
    PET_APPROVAL,
    PET_PAIRING,
    PET_CONFIRM,
    PET_VOICE,
    PET_COUNT,
};
#define PET_HOME_SCALE  6
#define PET_CHAT_SCALE  3
#define PET_FRAME_MS    450

// 对话场景的版式：左边头像，右边气泡。气泡里每行 136 像素宽。
#define CHAT_PET_X      8
#define CHAT_PET_Y      10
#define BUBBLE_X        72
#define BUBBLE_W        154
#define BUBBLE_PAD_X    9
#define BUBBLE_PAD_Y    8
#define BUBBLE_H        (LINES_16(3) + 2 * BUBBLE_PAD_Y)
#define ACTION_Y1       192
#define ACTION_Y2       226
// 气泡一行大约能放下的 ASCII 字符数（按最宽的字母估，宁可多写一遍）。
#define TOOL_FITS_IN_BUBBLE 13

typedef struct {
    lv_obj_t *obj;
    pocket_pet_mood_t mood;
    uint8_t scale;
} pet_t;

static struct {
    // 顶栏
    lv_obj_t *link_dot;
    lv_obj_t *link_label;
    lv_obj_t *clock;
    lv_obj_t *battery;
    lv_obj_t *battery_fill;
    lv_obj_t *battery_nub;
    lv_obj_t *battery_label;
    // 底部
    lv_obj_t *dots[BUDDY_PAGE_CAROUSEL_COUNT];
    lv_obj_t *hint;
    // 页面容器，下标为 buddy_page_t
    lv_obj_t *pages[BUDDY_PAGE_GUIDE + 1];
    // 首页
    lv_obj_t *home_title;
    lv_obj_t *home_sub;
    lv_obj_t *chips;
    lv_obj_t *chip_labels[3];
    // 小幽
    pet_t pets[PET_COUNT];
    unsigned pet_frame;
    // 最新回复
    lv_obj_t *reply_body;
    // 最近动态
    lv_obj_t *entry_rows[BUDDY_ENTRY_COUNT];
    lv_obj_t *entry_labels[BUDDY_ENTRY_COUNT];
    lv_obj_t *activity_empty;
    // 用量
    lv_obj_t *usage_today;
    lv_obj_t *usage_session;
    lv_obj_t *usage_values[USAGE_ROWS];
    // 设置
    lv_obj_t *setting_rows[BUDDY_SETTINGS_COUNT];
    lv_obj_t *setting_labels[BUDDY_SETTINGS_COUNT];
    lv_obj_t *setting_values[BUDDY_SETTINGS_COUNT];
    // 连接指引
    lv_obj_t *guide_name;
    lv_obj_t *guide_scroll;
    // 审批
    lv_obj_t *approval;
    lv_obj_t *approval_tool;
    lv_obj_t *approval_scroll;
    lv_obj_t *approval_hint;
    lv_obj_t *approval_allow;
    lv_obj_t *approval_deny;
    lv_obj_t *approval_status;
    lv_obj_t *approval_reply;
    lv_obj_t *approval_reply_label;
    char approval_id[BUDDY_PROMPT_ID_MAX];
    char approval_tool_text[BUDDY_TOOL_MAX];
    // 配对
    lv_obj_t *pairing;
    lv_obj_t *pairing_code;
    lv_obj_t *pairing_text;
    // 二次确认
    lv_obj_t *confirm;
    lv_obj_t *confirm_text;
    // 按住说话
    lv_obj_t *voice;
    lv_obj_t *voice_text;
    lv_obj_t *voice_timer;
    lv_obj_t *voice_state;
    lv_obj_t *voice_release;

    pocket_view_t view;
    buddy_page_t page;
    bool ready;
} s;

// ---------------------------------------------------------------------------
// 小工具
// ---------------------------------------------------------------------------

static lv_obj_t *make_box(lv_obj_t *parent, int x, int y, int w, int h)
{
    lv_obj_t *box = lv_obj_create(parent);

    lv_obj_remove_style_all(box);
    lv_obj_set_pos(box, x, y);
    lv_obj_set_size(box, w, h);
    lv_obj_remove_flag(box, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE);
    return box;
}

static void fill(lv_obj_t *obj, uint32_t color, int radius)
{
    lv_obj_set_style_bg_color(obj, lv_color_hex(color), 0);
    lv_obj_set_style_bg_opa(obj, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(obj, radius, 0);
}

static void outline(lv_obj_t *obj, uint32_t color, int radius)
{
    lv_obj_set_style_bg_opa(obj, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_color(obj, lv_color_hex(color), 0);
    lv_obj_set_style_border_width(obj, 1, 0);
    lv_obj_set_style_border_opa(obj, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(obj, radius, 0);
}

// 每个标签都显式指定字库：界面不依赖主题默认字体（默认 Montserrat 没有中文）。
static lv_obj_t *make_label(lv_obj_t *parent, const lv_font_t *font, uint32_t color,
                            const char *text)
{
    lv_obj_t *label = lv_label_create(parent);

    lv_obj_set_style_text_font(label, font, 0);
    lv_obj_set_style_text_color(label, lv_color_hex(color), 0);
    lv_obj_set_style_text_line_space(label, LINE_SPACE, 0);
    lv_obj_set_style_text_letter_space(label, 0, 0);
    lv_label_set_text(label, text != NULL ? text : "");
    return label;
}

// 固定宽高、超出打省略号的多行标签。
static lv_obj_t *make_text_block(lv_obj_t *parent, const lv_font_t *font, uint32_t color,
                                 int x, int y, int w, int h)
{
    lv_obj_t *label = make_label(parent, font, color, "");

    lv_label_set_long_mode(label, LV_LABEL_LONG_MODE_DOTS);
    lv_obj_set_pos(label, x, y);
    lv_obj_set_size(label, w, h);
    return label;
}

static void set_text(lv_obj_t *label, const char *text)
{
    if (text == NULL) {
        text = "";
    }
    if (strcmp(lv_label_get_text(label), text) != 0) {
        lv_label_set_text(label, text);
    }
}

// 省略号模式的标签在文字放不下时，LVGL 会直接改写标签里保存的文本（把结尾换成
// “...”），lv_label_get_text() 拿回来的已经不是原文，不能再用来比较。这类标签
// 改为记住上次原文的 FNV-1a 哈希（存在 user_data 里）。哈希只用于“要不要重绘”，
// 审批页的工具名另有逐字比较，不走这里。
static void set_block_text(lv_obj_t *label, const char *text)
{
    uintptr_t hash = 2166136261u;
    const unsigned char *cursor;

    if (text == NULL) {
        text = "";
    }
    for (cursor = (const unsigned char *)text; *cursor != '\0'; ++cursor) {
        hash = (hash ^ *cursor) * 16777619u;
    }
    hash = (hash & 0xFFFFFFFFu) | 1u;  // 非零，和“从未设置过”区分开
    if ((uintptr_t)lv_obj_get_user_data(label) != hash) {
        lv_label_set_text(label, text);
        lv_obj_set_user_data(label, (void *)hash);
    }
}

// LVGL 设置样式时不比较新旧值，哪怕值没变也会让控件重绘。render 每 100 ms 被调用
// 一次，所以这里所有 set_* 都先比较，保证画面静止时不产生任何刷屏。
static void set_text_color(lv_obj_t *obj, uint32_t color)
{
    lv_color_t next = lv_color_hex(color);

    if (!lv_color_eq(lv_obj_get_style_text_color(obj, LV_PART_MAIN), next)) {
        lv_obj_set_style_text_color(obj, next, 0);
    }
}

static void set_bg_color(lv_obj_t *obj, uint32_t color)
{
    lv_color_t next = lv_color_hex(color);

    if (!lv_color_eq(lv_obj_get_style_bg_color(obj, LV_PART_MAIN), next)) {
        lv_obj_set_style_bg_color(obj, next, 0);
    }
}

static void set_bg_opa(lv_obj_t *obj, lv_opa_t opa)
{
    if (lv_obj_get_style_bg_opa(obj, LV_PART_MAIN) != opa) {
        lv_obj_set_style_bg_opa(obj, opa, 0);
    }
}

static void set_visible(lv_obj_t *obj, bool visible)
{
    if (visible == lv_obj_has_flag(obj, LV_OBJ_FLAG_HIDDEN)) {
        if (visible) {
            lv_obj_remove_flag(obj, LV_OBJ_FLAG_HIDDEN);
        } else {
            lv_obj_add_flag(obj, LV_OBJ_FLAG_HIDDEN);
        }
    }
}

static lv_obj_t *make_title(lv_obj_t *parent, const char *text, uint32_t color)
{
    lv_obj_t *label = make_label(parent, &pocket_font_22, color, text);

    lv_obj_set_pos(label, SIDE + 2, 0);
    return label;
}

// 可滚动的文字卡片：返回容器，*label_out 为其中自动换行的正文标签。
static lv_obj_t *make_scroll_card(lv_obj_t *parent, int y, int h, lv_obj_t **label_out)
{
    lv_obj_t *card = make_box(parent, SIDE, y, INNER_W, h);
    lv_obj_t *label;

    fill(card, C_CARD, 10);
    lv_obj_set_style_pad_hor(card, 8, 0);
    lv_obj_set_style_pad_ver(card, 4, 0);
    lv_obj_add_flag(card, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_scroll_dir(card, LV_DIR_VER);
    lv_obj_set_scrollbar_mode(card, LV_SCROLLBAR_MODE_OFF);
    label = make_label(card, &pocket_font_16, C_TEXT, "");
    lv_label_set_long_mode(label, LV_LABEL_LONG_MODE_WRAP);
    lv_obj_set_width(label, INNER_W - 16);
    *label_out = label;
    return card;
}

// 按钮样式的操作说明条（没有触摸，只是把“哪个键做什么”画成按钮的样子）。
static lv_obj_t *make_action_bar(lv_obj_t *parent, int y, uint32_t color, bool solid,
                                 const char *key, const char *action)
{
    lv_obj_t *bar = make_box(parent, SIDE, y, INNER_W, 30);
    lv_obj_t *label;

    if (solid) {
        fill(bar, color, 15);
    } else {
        outline(bar, color, 15);
    }
    label = make_label(bar, &pocket_font_16, solid ? C_ON_COLOR : color, "");
    lv_label_set_text_fmt(label, "%s%s%s", key, PT_KEY_SEPARATOR, action);
    lv_obj_center(label);
    return bar;
}

// ---------------------------------------------------------------------------
// 小幽：把 20×20 的像素造型按整数倍画成方块
// ---------------------------------------------------------------------------

static void pet_draw_cb(lv_event_t *event)
{
    const pet_t *pet = lv_event_get_user_data(event);
    lv_layer_t *layer = lv_event_get_layer(event);
    lv_draw_rect_dsc_t dsc;
    lv_area_t coords;
    int x;
    int y;

    lv_obj_get_coords(pet->obj, &coords);
    lv_draw_rect_dsc_init(&dsc);
    dsc.bg_opa = LV_OPA_COVER;
    for (y = 0; y < POCKET_PET_GRID; ++y) {
        lv_area_t area;

        area.y1 = coords.y1 + y * pet->scale;
        area.y2 = area.y1 + pet->scale - 1;
        // 屏幕是一条一条刷新的：这一行不在本次刷新范围里就不用画。
        if (area.y2 < layer->_clip_area.y1 || area.y1 > layer->_clip_area.y2) {
            continue;
        }
        for (x = 0; x < POCKET_PET_GRID;) {
            pocket_pet_color_t color = pocket_pet_pixel(pet->mood, s.pet_frame, x, y);
            int run = 1;

            if (color == POCKET_PET_CLEAR) {
                ++x;
                continue;
            }
            // 同色的相邻格子合并成一个矩形，减少绘制任务。
            while (x + run < POCKET_PET_GRID &&
                   pocket_pet_pixel(pet->mood, s.pet_frame, x + run, y) == color) {
                ++run;
            }
            area.x1 = coords.x1 + x * pet->scale;
            area.x2 = area.x1 + run * pet->scale - 1;
            dsc.bg_color = lv_color_hex(pocket_pet_rgb(color));
            lv_draw_rect(layer, &dsc, &area);
            x += run;
        }
    }
}

static void make_pet(lv_obj_t *parent, int which, int x, int y, int scale)
{
    pet_t *pet = &s.pets[which];

    pet->scale = (uint8_t)scale;
    pet->mood = POCKET_PET_SLEEP;
    pet->obj = make_box(parent, x, y, POCKET_PET_GRID * scale, POCKET_PET_GRID * scale);
    lv_obj_add_event_cb(pet->obj, pet_draw_cb, LV_EVENT_DRAW_MAIN, pet);
}

static void pet_set(int which, pocket_pet_mood_t mood)
{
    pet_t *pet = &s.pets[which];

    if (pet->mood != mood) {
        pet->mood = mood;
        lv_obj_invalidate(pet->obj);
    }
}

static void pet_timer_cb(lv_timer_t *timer)
{
    int which;

    (void)timer;
    ++s.pet_frame;
    for (which = 0; which < PET_COUNT; ++which) {
        // 睡觉和失败的造型不动，不用重画；隐藏的对象 LVGL 自己会忽略。
        if (s.pets[which].mood != POCKET_PET_SLEEP && s.pets[which].mood != POCKET_PET_OOPS) {
            lv_obj_invalidate(s.pets[which].obj);
        }
    }
}

// 对话场景的上半部分：左边小幽的头像，右边它说话的气泡。返回气泡容器，
// *text_out 是气泡里的文字标签（固定文案，最多三行）。
static lv_obj_t *make_speech(lv_obj_t *parent, int which, lv_obj_t **text_out)
{
    lv_obj_t *tail = make_box(parent, BUBBLE_X - 5, 34, 12, 12);
    lv_obj_t *bubble = make_box(parent, BUBBLE_X, 0, BUBBLE_W, BUBBLE_H);

    make_pet(parent, which, CHAT_PET_X, CHAT_PET_Y, PET_CHAT_SCALE);
    fill(tail, C_CARD, 3);
    fill(bubble, C_CARD, 14);
    *text_out = make_label(bubble, &pocket_font_16, C_TEXT, "");
    lv_obj_set_pos(*text_out, BUBBLE_PAD_X, BUBBLE_PAD_Y);
    lv_obj_set_width(*text_out, BUBBLE_W - 2 * BUBBLE_PAD_X);
    return bubble;
}

// ---------------------------------------------------------------------------
// 构建
// ---------------------------------------------------------------------------

static void build_top_bar(lv_obj_t *root)
{
    s.link_dot = make_box(root, 24, 15, 8, 8);
    fill(s.link_dot, C_DIM, LV_RADIUS_CIRCLE);
    s.link_label = make_label(root, &pocket_font_14, C_DIM, PT_LINK_WAITING);
    lv_obj_set_pos(s.link_label, 37, 10);

    s.clock = make_label(root, &pocket_font_14, C_TEXT, "");
    lv_obj_align(s.clock, LV_ALIGN_TOP_MID, 0, 10);

    s.battery = make_box(root, SCREEN_W - 24 - 24, 13, 22, 12);
    outline(s.battery, C_DIM, 3);
    s.battery_fill = make_box(s.battery, 2, 2, 16, 6);
    fill(s.battery_fill, C_OK, 1);
    s.battery_nub = make_box(root, SCREEN_W - 24 - 2, 17, 2, 4);
    fill(s.battery_nub, C_DIM, 1);
    s.battery_label = make_label(root, &pocket_font_14, C_DIM, "");
    lv_obj_align_to(s.battery_label, s.battery, LV_ALIGN_OUT_LEFT_MID, -4, 0);
}

static void build_bottom(lv_obj_t *root)
{
    int index;
    const int gap = 12;
    const int x0 = (SCREEN_W - (BUDDY_PAGE_CAROUSEL_COUNT - 1) * gap - 6) / 2;

    for (index = 0; index < BUDDY_PAGE_CAROUSEL_COUNT; ++index) {
        s.dots[index] = make_box(root, x0 + index * gap, DOT_Y, 6, 6);
        fill(s.dots[index], C_LINE, LV_RADIUS_CIRCLE);
    }
    s.hint = make_label(root, &pocket_font_14, C_DIM, PT_HINT_PAGES);
    lv_obj_set_width(s.hint, HINT_W);
    lv_obj_set_style_text_align(s.hint, LV_TEXT_ALIGN_CENTER, 0);
    lv_label_set_long_mode(s.hint, LV_LABEL_LONG_MODE_DOTS);
    lv_obj_set_height(s.hint, 17);
    lv_obj_set_pos(s.hint, (SCREEN_W - HINT_W) / 2, HINT_Y);
}

static void build_home(lv_obj_t *page)
{
    static const char *const names[3] = {PT_STAT_TOTAL, PT_STAT_RUNNING, PT_STAT_WAITING};
    int index;

    make_pet(page, PET_HOME, (SCREEN_W - POCKET_PET_GRID * PET_HOME_SCALE) / 2, 0,
             PET_HOME_SCALE);

    s.home_title = make_label(page, &pocket_font_22, C_TEXT, PT_HOME_WAITING);
    lv_obj_set_width(s.home_title, INNER_W);
    lv_obj_set_style_text_align(s.home_title, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_pos(s.home_title, SIDE, 122);

    s.home_sub = make_text_block(page, &pocket_font_16, C_DIM, SIDE, 154, INNER_W,
                                 LINES_16(2));
    lv_obj_set_style_text_align(s.home_sub, LV_TEXT_ALIGN_CENTER, 0);

    s.chips = make_box(page, SIDE, 208, INNER_W, 30);
    for (index = 0; index < 3; ++index) {
        lv_obj_t *chip = make_box(s.chips, index * 73, 0, 66, 30);

        fill(chip, C_CARD, 15);
        s.chip_labels[index] = make_label(chip, &pocket_font_14, C_TEXT, names[index]);
        lv_obj_center(s.chip_labels[index]);
    }
}

static void build_reply(lv_obj_t *page)
{
    make_title(page, PT_REPLY_TITLE, C_TEXT);
    s.reply_body = make_text_block(page, &pocket_font_16, C_TEXT, SIDE + 2, 36,
                                   INNER_W - 4, LINES_16(9));
}

static void build_activity(lv_obj_t *page)
{
    int index;

    make_title(page, PT_ACTIVITY_TITLE, C_TEXT);
    for (index = 0; index < BUDDY_ENTRY_COUNT; ++index) {
        s.entry_rows[index] = make_box(page, SIDE, 36 + index * (ENTRY_ROW_H + ENTRY_ROW_GAP),
                                       INNER_W, ENTRY_ROW_H);
        fill(s.entry_rows[index], C_CARD, 10);
        s.entry_labels[index] = make_text_block(s.entry_rows[index], &pocket_font_16, C_TEXT,
                                                8, 3, INNER_W - 16, LINES_16(2));
    }
    s.activity_empty = make_label(page, &pocket_font_16, C_DIM, PT_ACTIVITY_EMPTY);
    lv_obj_align(s.activity_empty, LV_ALIGN_TOP_MID, 0, 100);
}

static lv_obj_t *build_usage_card(lv_obj_t *page, int x, const char *caption)
{
    lv_obj_t *card = make_box(page, x, 36, 102, 64);
    lv_obj_t *label;
    lv_obj_t *value;

    fill(card, C_CARD, 12);
    label = make_label(card, &pocket_font_14, C_DIM, caption);
    lv_obj_set_pos(label, 10, 7);
    value = make_text_block(card, &pocket_font_22, C_ACCENT, 10, 28, 88, 27);
    return value;
}

static void build_usage(lv_obj_t *page)
{
    static const char *const captions[USAGE_ROWS] = {
        PT_USAGE_APPROVED, PT_USAGE_DENIED, PT_USAGE_UPTIME, PT_USAGE_BATTERY,
    };
    int index;

    make_title(page, PT_USAGE_TITLE, C_TEXT);
    s.usage_today = build_usage_card(page, SIDE, PT_USAGE_TODAY);
    s.usage_session = build_usage_card(page, SIDE + 110, PT_USAGE_SESSION);
    for (index = 0; index < USAGE_ROWS; ++index) {
        int y = 112 + index * 32;
        lv_obj_t *caption = make_label(page, &pocket_font_16, C_DIM, captions[index]);
        lv_obj_t *rule;

        lv_obj_set_pos(caption, SIDE + 4, y);
        s.usage_values[index] = make_label(page, &pocket_font_16, C_TEXT, PT_NO_VALUE);
        lv_obj_set_width(s.usage_values[index], 130);
        lv_obj_set_style_text_align(s.usage_values[index], LV_TEXT_ALIGN_RIGHT, 0);
        lv_obj_set_pos(s.usage_values[index], SCREEN_W - SIDE - 4 - 130, y);
        if (index + 1 < USAGE_ROWS) {
            rule = make_box(page, SIDE + 4, y + 26, INNER_W - 8, 1);
            fill(rule, C_LINE, 0);
        }
    }
}

static void build_settings(lv_obj_t *page)
{
    static const char *const names[BUDDY_SETTINGS_COUNT] = {
        [BUDDY_SETTINGS_BRIGHTNESS] = PT_SET_BRIGHTNESS,
        [BUDDY_SETTINGS_BLE] = PT_SET_BLE,
        [BUDDY_SETTINGS_GUIDE] = PT_SET_GUIDE,
        [BUDDY_SETTINGS_SCREEN_OFF] = PT_SET_SCREEN_OFF,
        [BUDDY_SETTINGS_UNPAIR] = PT_SET_UNPAIR,
        [BUDDY_SETTINGS_FACTORY_RESET] = PT_SET_FACTORY,
        [BUDDY_SETTINGS_BACK] = PT_SET_BACK,
    };
    int index;

    make_title(page, PT_SETTINGS_TITLE, C_TEXT);
    for (index = 0; index < BUDDY_SETTINGS_COUNT; ++index) {
        lv_obj_t *row = make_box(page, SIDE, 34 + index * SETTINGS_ROW_H, INNER_W,
                                 SETTINGS_ROW_H - 2);

        lv_obj_set_style_radius(row, 9, 0);
        lv_obj_set_style_bg_color(row, lv_color_hex(C_ACCENT), 0);
        lv_obj_set_style_bg_opa(row, LV_OPA_TRANSP, 0);
        s.setting_rows[index] = row;
        s.setting_labels[index] = make_label(row, &pocket_font_16, C_TEXT, names[index]);
        lv_obj_align(s.setting_labels[index], LV_ALIGN_LEFT_MID, 10, 0);
        s.setting_values[index] = make_label(row, &pocket_font_16, C_DIM, "");
        lv_obj_set_width(s.setting_values[index], 70);
        lv_obj_set_style_text_align(s.setting_values[index], LV_TEXT_ALIGN_RIGHT, 0);
        lv_obj_align(s.setting_values[index], LV_ALIGN_RIGHT_MID, -10, 0);
    }
}

static void build_guide(lv_obj_t *page)
{
    lv_obj_t *body;

    make_title(page, PT_GUIDE_TITLE, C_TEXT);
    s.guide_name = make_text_block(page, &pocket_font_16, C_ACCENT, SIDE + 2, 32,
                                   INNER_W - 4, LINES_16(1));
    s.guide_scroll = make_scroll_card(page, 58, LINES_16(8) + 8, &body);
    lv_label_set_text(body, PT_GUIDE_BODY);
}

static void build_approval(lv_obj_t *root)
{
    lv_obj_t *bubble;
    lv_obj_t *question;

    s.approval = make_box(root, 0, AREA_Y, SCREEN_W, AREA_H + 12);
    bubble = make_speech(s.approval, PET_APPROVAL, &question);
    lv_label_set_text(question, PT_ASK_APPROVAL);
    // 工具名占气泡的第三行、用强调色：这是判断要不要放行时最先要看的信息。
    s.approval_tool = make_text_block(bubble, &pocket_font_16, C_ACCENT, BUBBLE_PAD_X,
                                      BUBBLE_PAD_Y + 2 * LINE_16,
                                      BUBBLE_W - 2 * BUBBLE_PAD_X, LINES_16(1));
    // 参数原文：照原样显示，不由小幽转述。
    s.approval_scroll = make_scroll_card(s.approval, BUBBLE_H + 6, LINES_16(4) + 8,
                                         &s.approval_hint);
    s.approval_allow = make_action_bar(s.approval, ACTION_Y1, C_OK, true, PT_KEY_OK,
                                       PT_APPROVAL_ALLOW);
    s.approval_deny = make_action_bar(s.approval, ACTION_Y2, C_DANGER, false, PT_KEY_DOWN,
                                      PT_APPROVAL_DENY);

    // 按键之后：你的回答变成右边的气泡，小幽在下面回一句。
    s.approval_reply = make_box(s.approval, 0, ACTION_Y1, LV_SIZE_CONTENT, 32);
    fill(s.approval_reply, C_OK, 14);
    lv_obj_set_style_pad_hor(s.approval_reply, 14, 0);
    lv_obj_align(s.approval_reply, LV_ALIGN_TOP_RIGHT, -SIDE, ACTION_Y1);
    s.approval_reply_label = make_label(s.approval_reply, &pocket_font_16, C_ON_COLOR,
                                        PT_APPROVAL_ALLOW);
    lv_obj_align(s.approval_reply_label, LV_ALIGN_LEFT_MID, 0, 0);
    s.approval_status = make_label(s.approval, &pocket_font_16, C_TEXT, "");
    lv_obj_set_width(s.approval_status, INNER_W);
    lv_obj_set_style_text_align(s.approval_status, LV_TEXT_ALIGN_CENTER, 0);
    lv_label_set_long_mode(s.approval_status, LV_LABEL_LONG_MODE_WRAP);
    lv_obj_set_pos(s.approval_status, SIDE, ACTION_Y2 + 4);
}

static void build_pairing(lv_obj_t *root)
{
    s.pairing = make_box(root, 0, AREA_Y, SCREEN_W, AREA_H + 12);
    (void)make_speech(s.pairing, PET_PAIRING, &s.pairing_text);
    s.pairing_code = make_label(s.pairing, &pocket_font_num_44, C_ACCENT, "");
    lv_obj_align(s.pairing_code, LV_ALIGN_TOP_MID, 0, BUBBLE_H + 40);
}

static void build_confirm(lv_obj_t *root)
{
    s.confirm = make_box(root, 0, AREA_Y, SCREEN_W, AREA_H + 12);
    (void)make_speech(s.confirm, PET_CONFIRM, &s.confirm_text);
    make_action_bar(s.confirm, ACTION_Y1, C_DANGER, true, PT_KEY_OK, PT_CONFIRM_YES);
    make_action_bar(s.confirm, ACTION_Y2, C_DIM, false, PT_KEY_DOWN, PT_CONFIRM_NO);
}

static void build_voice(lv_obj_t *root)
{
    s.voice = make_box(root, 0, AREA_Y, SCREEN_W, AREA_H + 12);
    (void)make_speech(s.voice, PET_VOICE, &s.voice_text);
    // 录音时长：变成绿色的大数字就是“现在说话听得到”。
    s.voice_timer = make_label(s.voice, &pocket_font_num_44, C_OK, "");
    lv_obj_align(s.voice_timer, LV_ALIGN_TOP_MID, 0, BUBBLE_H + 34);
    s.voice_state = make_label(s.voice, &pocket_font_22, C_WARN, PT_VOICE_STATE_PREPARING);
    lv_obj_set_width(s.voice_state, INNER_W);
    lv_obj_set_style_text_align(s.voice_state, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_pos(s.voice_state, SIDE, BUBBLE_H + 46);
    s.voice_release = make_action_bar(s.voice, ACTION_Y2, C_OK, false, PT_KEY_OK,
                                      PT_VOICE_RELEASE);
}

void pocket_ui_init(void)
{
    static void (*const builders[BUDDY_PAGE_GUIDE + 1])(lv_obj_t *) = {
        [BUDDY_PAGE_HOME] = build_home,
        [BUDDY_PAGE_REPLY] = build_reply,
        [BUDDY_PAGE_ACTIVITY] = build_activity,
        [BUDDY_PAGE_USAGE] = build_usage,
        [BUDDY_PAGE_SETTINGS] = build_settings,
        [BUDDY_PAGE_GUIDE] = build_guide,
    };
    lv_obj_t *root = lv_obj_create(NULL);
    int index;

    lv_obj_remove_style_all(root);
    lv_obj_remove_flag(root, LV_OBJ_FLAG_SCROLLABLE);
    fill(root, C_BG, 0);

    build_top_bar(root);
    for (index = 0; index <= BUDDY_PAGE_GUIDE; ++index) {
        s.pages[index] = make_box(root, 0, AREA_Y, SCREEN_W, AREA_H);
        builders[index](s.pages[index]);
        lv_obj_add_flag(s.pages[index], LV_OBJ_FLAG_HIDDEN);
    }
    build_approval(root);
    build_pairing(root);
    build_confirm(root);
    build_voice(root);
    lv_obj_add_flag(s.voice, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(s.approval, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(s.pairing, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(s.confirm, LV_OBJ_FLAG_HIDDEN);
    build_bottom(root);

    s.view = POCKET_VIEW_PAGE;
    s.page = BUDDY_PAGE_HOME;
    lv_obj_remove_flag(s.pages[BUDDY_PAGE_HOME], LV_OBJ_FLAG_HIDDEN);
    s.ready = true;
    (void)lv_timer_create(pet_timer_cb, PET_FRAME_MS, NULL);
    lv_screen_load(root);
}

// ---------------------------------------------------------------------------
// 刷新
// ---------------------------------------------------------------------------

static void render_top_bar(const buddy_ui_snapshot_t *snap)
{
    char text[16];
    uint32_t color = C_DIM;
    const char *link = PT_LINK_WAITING;

    if (!snap->ble_enabled) {
        link = PT_LINK_OFF;
    } else if (snap->ble_connected && snap->ble_encrypted) {
        link = PT_LINK_ON;
        color = C_OK;
    } else if (snap->ble_connected) {
        link = PT_LINK_PAIRING;
        color = C_WARN;
    }
    set_text(s.link_label, link);
    set_bg_color(s.link_dot, color);

    (void)pocket_format_clock(snap->epoch_seconds, snap->timezone_offset_seconds,
                              snap->time_received_ms, snap->uptime_ms, text, sizeof(text));
    set_text(s.clock, text);

    set_visible(s.battery, snap->battery_available);
    set_visible(s.battery_nub, snap->battery_available);
    set_visible(s.battery_label, snap->battery_available);
    if (snap->battery_available) {
        unsigned percent = snap->battery_percent > 100U ? 100U : snap->battery_percent;
        int width = (int)(16U * percent / 100U);

        (void)snprintf(text, sizeof(text), "%u%%", percent);
        if (strcmp(lv_label_get_text(s.battery_label), text) != 0) {
            lv_label_set_text(s.battery_label, text);
            lv_obj_align_to(s.battery_label, s.battery, LV_ALIGN_OUT_LEFT_MID, -4, 0);
            lv_obj_set_width(s.battery_fill, width < 2 ? 2 : width);
        }
        set_bg_color(s.battery_fill, percent <= 20U ? C_DANGER : C_OK);
    }
}

static void render_home(const buddy_ui_snapshot_t *snap)
{
    static const char *const names[3] = {PT_STAT_TOTAL, PT_STAT_RUNNING, PT_STAT_WAITING};
    static char sub[BUDDY_MESSAGE_MAX + BUDDY_NAME_MAX + 8];
    const unsigned counts[3] = {snap->total, snap->running, snap->waiting};
    pocket_home_t home = pocket_home_for(snap);
    const char *title = PT_HOME_IDLE;
    uint32_t title_color = C_TEXT;
    bool live = false;
    int index;

    sub[0] = '\0';
    switch (home) {
    case POCKET_HOME_BLE_OFF:
        title = PT_HOME_BLE_OFF;
        title_color = C_DIM;
        (void)snprintf(sub, sizeof(sub), "%s",
                       snap->message[0] != '\0' ? snap->message : PT_HOME_SUB_BLE_OFF);
        break;
    case POCKET_HOME_WAITING:
        title = PT_HOME_WAITING;
        (void)snprintf(sub, sizeof(sub), "%s\n%s",
                       snap->message[0] != '\0' ? snap->message : PT_HOME_SUB_WAITING,
                       snap->name);
        break;
    case POCKET_HOME_LINKING:
        title = PT_HOME_LINKING;
        (void)snprintf(sub, sizeof(sub), "%s", PT_HOME_SUB_LINKING);
        break;
    case POCKET_HOME_IDLE:
        live = true;
        title = PT_HOME_IDLE;
        (void)snprintf(sub, sizeof(sub), "%s",
                       snap->message[0] != '\0' ? snap->message : PT_HOME_SUB_IDLE);
        break;
    case POCKET_HOME_BUSY:
        live = true;
        title = PT_HOME_BUSY;
        title_color = C_ACCENT;
        (void)snprintf(sub, sizeof(sub), "%s", snap->message);
        break;
    case POCKET_HOME_PENDING:
        live = true;
        title = PT_HOME_PENDING;
        title_color = C_WARN;
        (void)snprintf(sub, sizeof(sub), "%s",
                       snap->message[0] != '\0' ? snap->message : PT_HOME_SUB_PENDING);
        break;
    case POCKET_HOME_APPROVED:
        live = true;
        title = PT_HOME_APPROVED;
        title_color = C_OK;
        (void)snprintf(sub, sizeof(sub), "%s", snap->message);
        break;
    case POCKET_HOME_MILESTONE:
        live = true;
        title = PT_HOME_MILESTONE;
        title_color = C_ACCENT;
        (void)snprintf(sub, sizeof(sub), "%s", PT_HOME_SUB_MILESTONE);
        break;
    }
    set_text(s.home_title, title);
    set_text_color(s.home_title, title_color);
    // 没有会话数字要显示时，把那一行的位置让给说明文字（设备名需要第三行）。
    if (lv_obj_get_height(s.home_sub) != (live ? LINES_16(2) : LINES_16(3))) {
        lv_obj_set_height(s.home_sub, live ? LINES_16(2) : LINES_16(3));
        lv_obj_set_user_data(s.home_sub, NULL);
    }
    set_block_text(s.home_sub, sub);
    pet_set(PET_HOME, pocket_pet_for(snap));

    set_visible(s.chips, live);
    if (live) {
        for (index = 0; index < 3; ++index) {
            char text[24];

            (void)snprintf(text, sizeof(text), "%s %u", names[index],
                           counts[index] > 99U ? 99U : counts[index]);
            set_text(s.chip_labels[index], text);
            set_text_color(s.chip_labels[index],
                           index == 2 && counts[2] > 0U ? C_WARN : C_TEXT);
        }
    }
}

static bool link_is_live(const buddy_ui_snapshot_t *snap)
{
    return snap->ble_connected && snap->ble_encrypted && !snap->heartbeat_stale;
}

static void render_reply(const buddy_ui_snapshot_t *snap)
{
    static char text[BUDDY_REPLY_MAX + 8];

    if (snap->reply[0] == '\0') {
        set_block_text(s.reply_body, link_is_live(snap) ? PT_REPLY_EMPTY : PT_REPLY_OFFLINE);
        set_text_color(s.reply_body, C_DIM);
        return;
    }
    (void)snprintf(text, sizeof(text), "%s%s", snap->reply,
                   snap->reply_truncated ? PT_ELLIPSIS : "");
    set_block_text(s.reply_body, text);
    set_text_color(s.reply_body, C_TEXT);
}

static void render_activity(const buddy_ui_snapshot_t *snap)
{
    bool any = false;
    int index;

    for (index = 0; index < BUDDY_ENTRY_COUNT; ++index) {
        bool present = snap->entries[index][0] != '\0';

        any = any || present;
        set_visible(s.entry_rows[index], present);
        if (present) {
            set_block_text(s.entry_labels[index], snap->entries[index]);
            // 最新一条用正文色，其余稍暗，方便一眼找到最新。
            set_text_color(s.entry_labels[index], index == 0 ? C_TEXT : C_DIM);
        }
    }
    set_visible(s.activity_empty, !any);
}

static void render_usage(const buddy_ui_snapshot_t *snap)
{
    char text[40];

    (void)pocket_format_tokens(snap->tokens_today, text, sizeof(text));
    set_block_text(s.usage_today, text);
    (void)pocket_format_tokens(snap->tokens, text, sizeof(text));
    set_block_text(s.usage_session, text);

    (void)snprintf(text, sizeof(text), "%" PRIu64 " %s", snap->approval_count, PT_USAGE_TIMES);
    set_text(s.usage_values[0], text);
    (void)snprintf(text, sizeof(text), "%" PRIu64 " %s", snap->denial_count, PT_USAGE_TIMES);
    set_text(s.usage_values[1], text);
    (void)pocket_format_uptime(snap->uptime_ms, text, sizeof(text));
    set_text(s.usage_values[2], text);
    if (snap->battery_available) {
        (void)snprintf(text, sizeof(text), "%u%%  %u.%02uV", (unsigned)snap->battery_percent,
                       (unsigned)(snap->battery_mv / 1000U),
                       (unsigned)(snap->battery_mv % 1000U / 10U));
        set_text(s.usage_values[3], text);
    } else {
        set_text(s.usage_values[3], PT_USAGE_NO_BATTERY);
    }
}

static void render_settings(const buddy_ui_snapshot_t *snap)
{
    char text[16];
    int index;

    for (index = 0; index < BUDDY_SETTINGS_COUNT; ++index) {
        bool selected = index == (int)snap->settings_selection;
        bool destructive = index == BUDDY_SETTINGS_UNPAIR ||
                           index == BUDDY_SETTINGS_FACTORY_RESET;

        set_bg_opa(s.setting_rows[index], selected ? LV_OPA_COVER : LV_OPA_TRANSP);
        set_text_color(s.setting_labels[index],
                       selected ? C_ON_COLOR : (destructive ? C_DANGER : C_TEXT));
        set_text_color(s.setting_values[index], selected ? C_ON_COLOR : C_DIM);
    }
    (void)snprintf(text, sizeof(text), "%u%%",
                   20U + (unsigned)(snap->brightness_level >= BUDDY_BRIGHTNESS_LEVELS
                                        ? BUDDY_BRIGHTNESS_LEVELS - 1U
                                        : snap->brightness_level) * 20U);
    set_text(s.setting_values[BUDDY_SETTINGS_BRIGHTNESS], text);
    set_text(s.setting_values[BUDDY_SETTINGS_BLE], snap->ble_enabled ? PT_ON : PT_OFF);
}

static void render_guide(const buddy_ui_snapshot_t *snap)
{
    static char text[BUDDY_NAME_MAX + 32];

    (void)snprintf(text, sizeof(text), "%s  %s", PT_GUIDE_NAME, snap->name);
    set_block_text(s.guide_name, text);
}

static void render_approval(const buddy_ui_snapshot_t *snap)
{
    static char hint[BUDDY_HINT_MAX + BUDDY_TOOL_MAX + 80];
    int used;
    const char *status = NULL;
    const char *tool;
    uint32_t status_color = C_TEXT;

    if (strcmp(s.approval_id, snap->prompt_id) != 0) {
        // 新的请求：从头显示，绝不沿用上一条的滚动位置。
        (void)snprintf(s.approval_id, sizeof(s.approval_id), "%s", snap->prompt_id);
        lv_obj_scroll_to_y(s.approval_scroll, 0, LV_ANIM_OFF);
    }
    tool = snap->prompt_tool[0] != '\0' ? snap->prompt_tool : PT_NO_VALUE;
    if (strcmp(s.approval_tool_text, tool) != 0 ||
        lv_label_get_text(s.approval_tool)[0] == '\0') {
        (void)snprintf(s.approval_tool_text, sizeof(s.approval_tool_text), "%s", tool);
        lv_label_set_text(s.approval_tool, tool);
    }
    // 气泡里的工具名只有一行；放不下时在下面的卡片开头再完整写一遍，
    // 保证放行之前能看到完整的名字。
    used = 0;
    hint[0] = '\0';
    if (strlen(tool) > TOOL_FITS_IN_BUBBLE) {
        used = snprintf(hint, sizeof(hint), "%s%s\n", PT_APPROVAL_FULL_TOOL, tool);
    }
    if (used < 0 || (size_t)used >= sizeof(hint)) {
        used = 0;
    }
    (void)snprintf(hint + used, sizeof(hint) - (size_t)used, "%s%s%s",
                   snap->prompt_hint[0] != '\0' ? snap->prompt_hint : PT_APPROVAL_NO_HINT,
                   snap->prompt_hint_truncated ? "\n" : "",
                   snap->prompt_hint_truncated ? PT_APPROVAL_CUT : "");
    set_text(s.approval_hint, hint);

    pet_set(PET_APPROVAL, pocket_pet_for(snap));
    if (snap->approval_locked) {
        bool denied = snap->permission_decision == BUDDY_PERMISSION_DENY;

        switch (snap->permission_delivery) {
        case BUDDY_PERMISSION_DELIVERY_SENT:
            status = denied ? PT_APPROVAL_SENT_NO : PT_APPROVAL_SENT_YES;
            status_color = C_TEXT;
            break;
        case BUDDY_PERMISSION_DELIVERY_FAILED:
            status = PT_APPROVAL_FAILED;
            status_color = C_DANGER;
            break;
        case BUDDY_PERMISSION_DELIVERY_SENDING:
        case BUDDY_PERMISSION_DELIVERY_NONE:
            status = PT_APPROVAL_SENDING;
            status_color = C_DIM;
            break;
        }
        if (strcmp(lv_label_get_text(s.approval_reply_label),
                   denied ? PT_APPROVAL_DENY : PT_APPROVAL_ALLOW) != 0) {
            lv_label_set_text(s.approval_reply_label,
                              denied ? PT_APPROVAL_DENY : PT_APPROVAL_ALLOW);
            lv_obj_align(s.approval_reply, LV_ALIGN_TOP_RIGHT, -SIDE, ACTION_Y1);
        }
        set_bg_color(s.approval_reply, denied ? C_DANGER : C_OK);
    }
    set_visible(s.approval_allow, status == NULL);
    set_visible(s.approval_deny, status == NULL);
    set_visible(s.approval_reply, status != NULL);
    set_visible(s.approval_status, status != NULL);
    if (status != NULL) {
        set_text(s.approval_status, status);
        set_text_color(s.approval_status, status_color);
    }
}

static void render_pairing(const buddy_ui_snapshot_t *snap)
{
    char code[12];

    pet_set(PET_PAIRING, pocket_pet_for(snap));
    set_visible(s.pairing_code, snap->passkey_visible);
    if (snap->passkey_visible) {
        (void)pocket_format_passkey(snap->passkey, code, sizeof(code));
        if (strcmp(lv_label_get_text(s.pairing_code), code) != 0) {
            lv_label_set_text(s.pairing_code, code);
            lv_obj_align(s.pairing_code, LV_ALIGN_TOP_MID, 0, BUBBLE_H + 40);
        }
        set_text(s.pairing_text, PT_ASK_PASSKEY);
    } else {
        set_text(s.pairing_text, PT_PAIR_SECURING);
    }
}

static void render_confirm(const buddy_ui_snapshot_t *snap)
{
    pet_set(PET_CONFIRM, pocket_pet_for(snap));
    set_text(s.confirm_text, snap->confirmation == BUDDY_CONFIRM_FACTORY_RESET
                                 ? PT_ASK_FACTORY
                                 : PT_ASK_UNPAIR);
}

static void render_voice(const buddy_ui_snapshot_t *snap)
{
    bool listening = snap->voice_phase == BUDDY_VOICE_LISTENING;
    bool sending = snap->voice_phase == BUDDY_VOICE_SENDING;
    char timer[12];

    pet_set(PET_VOICE, pocket_pet_for(snap));
    set_text(s.voice_text, listening ? PT_VOICE_LISTENING
                                     : (sending ? PT_VOICE_SENDING : PT_VOICE_PREPARING));
    set_visible(s.voice_timer, listening);
    set_visible(s.voice_state, !listening);
    set_visible(s.voice_release, !sending);
    if (listening) {
        uint64_t seconds = snap->uptime_ms > snap->voice_listening_since_ms
                               ? (snap->uptime_ms - snap->voice_listening_since_ms) / 1000U
                               : 0U;

        (void)snprintf(timer, sizeof(timer), "%u:%02u", (unsigned)(seconds / 60U % 10U),
                       (unsigned)(seconds % 60U));
        if (strcmp(lv_label_get_text(s.voice_timer), timer) != 0) {
            lv_label_set_text(s.voice_timer, timer);
            lv_obj_align(s.voice_timer, LV_ALIGN_TOP_MID, 0, BUBBLE_H + 34);
        }
    } else {
        set_text(s.voice_state, sending ? PT_VOICE_STATE_SENDING : PT_VOICE_STATE_PREPARING);
        set_text_color(s.voice_state, sending ? C_ACCENT : C_WARN);
    }
}

void pocket_ui_render(const buddy_ui_snapshot_t *snap)
{
    pocket_view_t view;
    buddy_page_t page;
    const char *hint = "";
    bool carousel;
    int index;

    if (!s.ready || snap == NULL) {
        return;
    }
    view = pocket_view_for(snap);
    page = snap->page <= BUDDY_PAGE_GUIDE ? snap->page : BUDDY_PAGE_HOME;
    carousel = view == POCKET_VIEW_PAGE && (int)page < BUDDY_PAGE_CAROUSEL_COUNT;

    for (index = 0; index <= BUDDY_PAGE_GUIDE; ++index) {
        set_visible(s.pages[index], view == POCKET_VIEW_PAGE && index == (int)page);
    }
    set_visible(s.approval, view == POCKET_VIEW_APPROVAL);
    set_visible(s.pairing, view == POCKET_VIEW_PAIRING);
    set_visible(s.confirm, view == POCKET_VIEW_CONFIRM);
    set_visible(s.voice, view == POCKET_VIEW_VOICE);
    if (view == POCKET_VIEW_PAGE && page == BUDDY_PAGE_GUIDE &&
        (s.view != view || s.page != page)) {
        lv_obj_scroll_to_y(s.guide_scroll, 0, LV_ANIM_OFF);
    }
    if (view != POCKET_VIEW_APPROVAL) {
        s.approval_id[0] = '\0';
    }
    s.view = view;
    s.page = page;

    render_top_bar(snap);
    switch (view) {
    case POCKET_VIEW_CONFIRM:
        render_confirm(snap);
        break;
    case POCKET_VIEW_PAIRING:
        render_pairing(snap);
        break;
    case POCKET_VIEW_VOICE:
        render_voice(snap);
        break;
    case POCKET_VIEW_APPROVAL:
        render_approval(snap);
        hint = snap->approval_locked ? "" : PT_HINT_APPROVAL;
        break;
    case POCKET_VIEW_PAGE:
        switch (page) {
        case BUDDY_PAGE_HOME:
            render_home(snap);
            hint = PT_HINT_PAGES;
            break;
        case BUDDY_PAGE_REPLY:
            render_reply(snap);
            hint = PT_HINT_PAGES;
            break;
        case BUDDY_PAGE_ACTIVITY:
            render_activity(snap);
            hint = PT_HINT_PAGES;
            break;
        case BUDDY_PAGE_USAGE:
            render_usage(snap);
            hint = PT_HINT_PAGES;
            break;
        case BUDDY_PAGE_SETTINGS:
            render_settings(snap);
            hint = PT_HINT_SETTINGS;
            break;
        case BUDDY_PAGE_GUIDE:
            render_guide(snap);
            hint = PT_HINT_GUIDE;
            break;
        }
        break;
    }
    set_block_text(s.hint, hint);
    for (index = 0; index < BUDDY_PAGE_CAROUSEL_COUNT; ++index) {
        set_visible(s.dots[index], carousel);
        set_bg_color(s.dots[index], index == (int)page ? C_ACCENT : C_LINE);
    }
}

void pocket_ui_scroll(int delta)
{
    if (!s.ready || delta == 0) {
        return;
    }
    if (s.view == POCKET_VIEW_APPROVAL) {
        // 只有“上键”可用来翻看，所以单向前进，翻到底后回到开头。
        if (lv_obj_get_scroll_bottom(s.approval_scroll) <= 0) {
            lv_obj_scroll_to_y(s.approval_scroll, 0, LV_ANIM_OFF);
        } else {
            lv_obj_scroll_by_bounded(s.approval_scroll, 0, -3 * LINE_16, LV_ANIM_OFF);
        }
    } else if (s.view == POCKET_VIEW_PAGE && s.page == BUDDY_PAGE_GUIDE) {
        // 按整行滚动，避免卡片上下沿出现被切掉一半的字。
        lv_obj_scroll_by_bounded(s.guide_scroll, 0, delta > 0 ? -3 * LINE_16 : 3 * LINE_16,
                                 LV_ANIM_OFF);
    }
}
