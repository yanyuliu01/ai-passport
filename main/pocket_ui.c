// main/pocket_ui.c —— 小幽界面实现（240×320 竖屏，三键操作）。
//
// 首页就是和小幽的对话：顶上一条是她和她此刻的状态（在想、在找谁帮忙），下面是一条
// 往下长的对话，上下键翻看这一段里说过的每一轮。低频的东西（通知、帮手、设置）都收在
// 长按上键的菜单里。
//
// 结构：一块屏幕上常驻三层——顶栏、内容区、底部按键提示。内容区里每个页面和每个
// 浮层各有一个容器，render 时只切换显隐并更新变化的内容。
// 屏幕四角有半径 30 的圆角遮罩（BSP_LVGL_SCREEN_RADIUS），所以顶栏和底栏的内容都
// 向内收，不贴边。

#include "pocket_ui.h"

#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "lvgl.h"

#include "buddy_history.h"
#include "pocket_fonts.h"
#include "pocket_pet.h"
#include "pocket_text.h"
#include "pocket_view.h"

// ---- 配色：夜色。小幽是住在口袋里的小幽灵，底色是夜空的墨蓝，她自己是月白。
// 彩色只回答一个问题——现在是谁在做事：小幽是淡紫，每个帮手有自己的颜色
// （pocket_helper_color）。绿、红、黄只表示可以、不行、要你留意。
#define C_BG       0x15172A
#define C_CARD     0x232646
#define C_LINE     0x3A3E68
#define C_TEXT     0xF2F0FA
#define C_DIM      0x8E90B5
#define C_XIAOYOU  POCKET_COLOR_XIAOYOU
#define C_OK       0x86D19A
#define C_WARN     0xF2C265
#define C_DANGER   0xF0736A
#define C_ON_COLOR 0x15172A  // 彩色底上的文字

// ---- 布局 ----
#define SCREEN_W   240
#define SCREEN_H   320
#define AREA_Y     34
#define AREA_H     252
#define SIDE       14
#define INNER_W    (SCREEN_W - 2 * SIDE)
#define LINE_SPACE 2
#define LINE_16    23   // pocket_font_16 行距：字高 21 + 行间 2
#define LINES_16(n) ((n) * LINE_16 - LINE_SPACE)  // n 行正文占的高度
#define HINT_Y     296
// 底部圆角在提示这一行往里收约 12 像素，所以提示最宽 212。
#define HINT_W     212
#define HINT_ITEMS 3

#define ROW_H          30
#define ENTRY_ROW_H    50
#define ENTRY_ROW_GAP  3

// 小幽出现的地方：首页的两种版式（独占画面、对话顶上那一条），和五个占满屏幕的场景。
enum {
    PET_SOLO,
    PET_TALK,
    PET_APPROVAL,
    PET_PAIRING,
    PET_CONFIRM,
    PET_VOICE,
    PET_UPDATE,
    PET_COUNT,
};
#define PET_SOLO_SCALE  6
#define PET_TALK_SCALE  2
#define PET_CHAT_SCALE  3
#define PET_FRAME_MS    450

// 首页的对话版式。顶上一条：左边是缩小的小幽，右边第一行是“小幽 ···✉··· [帮手]”，
// 第二行是她此刻在干什么和计时。一条细线下面是对话，一屏九行。
#define HEAD_PET_X      14
#define HEAD_PET_Y      2
#define HEAD_TEXT_X     62
#define HEAD_TEXT_W     (SCREEN_W - SIDE - HEAD_TEXT_X)
#define HEAD_ROW2_Y     23
#define HEAD_LINK_X     (HEAD_TEXT_X + 34)
#define HEAD_LINK_W     34
#define HEAD_TAG_X      (HEAD_LINK_X + HEAD_LINK_W + 6)
#define HEAD_TAG_PAD    8
// 名牌里名字最宽能占多少：再长就打省略号。
#define HEAD_TAG_TEXT_W (SCREEN_W - SIDE - HEAD_TAG_X - 2 * HEAD_TAG_PAD - 4)
#define HEAD_ELAPSED_W  48
#define HEAD_RULE_Y     46

// 对话是一条往下长的长卷，屏幕是它上面九行高的窗口；翻一次走八行，留一行让眼睛接得上。
// 每一轮是两段字：你说的（暗一些，左边一条竖线）和她说的。所有高度都是整行，
// 所以窗口的上下沿不会切到半行字。
#define TL_Y            (HEAD_RULE_Y + 2)
#define TL_LINES        9
#define TL_H            (TL_LINES * LINE_16)
#define TL_STEP         ((TL_LINES - 1) * LINE_16)
#define TL_TEXT_W       (INNER_W - 8)
#define TL_SLOTS        (BUDDY_HISTORY_TURNS + 1)
#define TL_LIVE         BUDDY_HISTORY_TURNS
// 首页比别的页面高一点：对话一直铺到按键提示上面。
#define HOME_H          (TL_Y + TL_H)

// 说话时的音量条。
#define LEVEL_BARS      15
#define LEVEL_SAMPLE_MS 90U
// 换固件：进度条由一格一格的方块排成，和别处的像素风一致。
#define UPDATE_CELLS    20
#define UPDATE_CELL_W   8
#define UPDATE_CELL_GAP 2
#define UPDATE_BAR_W    (UPDATE_CELLS * (UPDATE_CELL_W + UPDATE_CELL_GAP) - UPDATE_CELL_GAP)
#define UPDATE_BAR_H    12

// 审批、配对、确认这三个场景的版式：左边头像，右边气泡。气泡里每行 136 像素宽。
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

// 按键图标：上、下、确认。
typedef enum {
    KEY_NONE,
    KEY_UP,
    KEY_DOWN,
    KEY_OK,
} key_icon_t;

typedef struct {
    key_icon_t key;
    const char *text;
} hint_item_t;

static struct {
    // 顶栏
    lv_obj_t *link_dot;
    lv_obj_t *clock;
    lv_obj_t *battery;
    lv_obj_t *battery_fill;
    lv_obj_t *battery_nub;
    // 底部按键提示
    lv_obj_t *hint_icons[HINT_ITEMS];
    lv_obj_t *hint_labels[HINT_ITEMS];
    key_icon_t hint_keys[HINT_ITEMS];
    const char *hint_texts[HINT_ITEMS];
    lv_obj_t *notice;
    // 页面容器，下标为 buddy_page_t
    lv_obj_t *pages[BUDDY_PAGE_COUNT];
    // 小幽
    pet_t pets[PET_COUNT];
    unsigned pet_frame;
    // 首页：她独占画面
    lv_obj_t *solo;
    lv_obj_t *solo_title;
    lv_obj_t *solo_sub;
    // 首页：对话。顶上一条
    lv_obj_t *talk;
    lv_obj_t *head_name;
    lv_obj_t *head_link;
    lv_obj_t *head_tag;
    lv_obj_t *head_tag_label;
    char head_tag_name[BUDDY_AGENT_MAX];
    lv_obj_t *head_status;
    lv_obj_t *head_elapsed;
    uint32_t link_color;
    // 首页：对话。长卷：每一轮两段字，用到才创建；最后一格是正在进行的这一轮
    lv_obj_t *tl_scroll;
    lv_obj_t *tl_said[TL_SLOTS];
    lv_obj_t *tl_reply[TL_SLOTS];
    lv_style_t tl_style_said;
    lv_style_t tl_style_reply;
    bool tl_valid;         // 长卷里现在有东西
    bool tl_follow;        // 窗口跟着最新一轮走
    uint32_t tl_revision;  // 上次照着哪一版历史摆的
    uint32_t tl_serial;
    uint32_t tl_recall;
    int tl_content;        // 长卷总高
    int tl_last_top;       // 最新一轮从哪儿开始
    int tl_y;              // 窗口现在在哪儿
    // 通知
    lv_obj_t *entry_rows[BUDDY_ENTRY_COUNT];
    lv_obj_t *entry_labels[BUDDY_ENTRY_COUNT];
    lv_obj_t *notices_empty;
    // 帮手
    lv_obj_t *helper_rows[BUDDY_HELPER_COUNT];
    lv_obj_t *helper_tags[BUDDY_HELPER_COUNT];
    lv_obj_t *helper_names[BUDDY_HELPER_COUNT];
    lv_obj_t *helper_abouts[BUDDY_HELPER_COUNT];
    lv_obj_t *helpers_empty;
    // 菜单和更多设置
    lv_obj_t *menu_rows[BUDDY_MENU_COUNT];
    lv_obj_t *menu_labels[BUDDY_MENU_COUNT];
    lv_obj_t *menu_values[BUDDY_MENU_COUNT];
    lv_obj_t *more_rows[BUDDY_MORE_COUNT];
    lv_obj_t *more_labels[BUDDY_MORE_COUNT];
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
    lv_obj_t *voice_title;
    lv_obj_t *voice_sub;
    lv_obj_t *voice_bars;
    lv_obj_t *voice_timer;
    uint8_t levels[LEVEL_BARS];
    uint64_t level_sampled_ms;
    // 经蓝牙换固件
    lv_obj_t *update;
    lv_obj_t *update_title;
    lv_obj_t *update_bar;
    lv_obj_t *update_percent;
    lv_obj_t *update_sub;
    uint8_t update_shown_percent;

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

static void outline(lv_obj_t *obj, uint32_t color, int radius, int width)
{
    lv_obj_set_style_bg_opa(obj, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_color(obj, lv_color_hex(color), 0);
    lv_obj_set_style_border_width(obj, width, 0);
    lv_obj_set_style_border_opa(obj, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(obj, radius, 0);
}

// 每个标签都显式指定字库：界面不依赖主题默认字体（默认 Montserrat 没有中文）。
// pocket_font_14 和 pocket_font_22 只有固定文案里用到的字，对面发来的文字一律用
// pocket_font_16。
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

static void set_border_color(lv_obj_t *obj, uint32_t color)
{
    lv_color_t next = lv_color_hex(color);

    if (!lv_color_eq(lv_obj_get_style_border_color(obj, LV_PART_MAIN), next)) {
        lv_obj_set_style_border_color(obj, next, 0);
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

static lv_obj_t *make_title(lv_obj_t *parent, const char *text)
{
    lv_obj_t *label = make_label(parent, &pocket_font_22, C_TEXT, text);

    lv_obj_set_pos(label, SIDE + 2, 0);
    return label;
}

static void style_scrollbar(lv_obj_t *obj)
{
    lv_obj_set_scrollbar_mode(obj, LV_SCROLLBAR_MODE_AUTO);
    lv_obj_set_style_bg_color(obj, lv_color_hex(C_XIAOYOU), LV_PART_SCROLLBAR);
    lv_obj_set_style_bg_opa(obj, LV_OPA_COVER, LV_PART_SCROLLBAR);
    lv_obj_set_style_width(obj, 3, LV_PART_SCROLLBAR);
    lv_obj_set_style_radius(obj, 2, LV_PART_SCROLLBAR);
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
        outline(bar, color, 15, 2);
    }
    label = make_label(bar, &pocket_font_16, solid ? C_ON_COLOR : color, "");
    lv_label_set_text_fmt(label, "%s%s%s", key, PT_KEY_SEPARATOR, action);
    lv_obj_center(label);
    return bar;
}

// 画一个实心小方块。所有像素风的东西（小幽、按键图标、信封、等待的点）都用它拼。
static void draw_block(lv_layer_t *layer, lv_draw_rect_dsc_t *dsc, int x, int y, int w, int h,
                       uint32_t color)
{
    lv_area_t area;

    area.x1 = x;
    area.y1 = y;
    area.x2 = x + w - 1;
    area.y2 = y + h - 1;
    dsc->bg_color = lv_color_hex(color);
    lv_draw_rect(layer, dsc, &area);
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
        int top = coords.y1 + y * pet->scale;

        // 屏幕是一条一条刷新的：这一行不在本次刷新范围里就不用画。
        if (top + pet->scale - 1 < layer->_clip_area.y1 || top > layer->_clip_area.y2) {
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
            draw_block(layer, &dsc, coords.x1 + x * pet->scale, top, run * pet->scale,
                       pet->scale, pocket_pet_rgb(color));
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
    // 递信的那条路也跟着这个节拍走。
    lv_obj_invalidate(s.head_link);
}

// 审批、配对、确认的上半部分：左边小幽的头像，右边它说话的气泡。返回气泡容器，
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
// 像素风的小图：按键图标、小幽递出去的信、音量条
// ---------------------------------------------------------------------------

// 11×11 的格子里画一个键：上、下是三角，确认是一个圈。
static void key_draw_cb(lv_event_t *event)
{
    lv_obj_t *obj = lv_event_get_current_target(event);
    // user_data 里放的是这个图标是第几项，实际画哪个键看那一项现在的值。
    key_icon_t key = s.hint_keys[(uintptr_t)lv_event_get_user_data(event)];
    lv_layer_t *layer = lv_event_get_layer(event);
    lv_draw_rect_dsc_t dsc;
    lv_area_t coords;
    int row;

    lv_obj_get_coords(obj, &coords);
    lv_draw_rect_dsc_init(&dsc);
    dsc.bg_opa = LV_OPA_COVER;
    if (key == KEY_OK) {
        // 圈：外面一圈 11×11，四角各缺一块。
        static const uint8_t rows[11][2] = {
            {3, 5}, {1, 9}, {1, 9}, {0, 11}, {0, 11}, {0, 11}, {0, 11}, {0, 11}, {1, 9}, {1, 9},
            {3, 5},
        };

        for (row = 0; row < 11; ++row) {
            draw_block(layer, &dsc, coords.x1 + rows[row][0], coords.y1 + row, rows[row][1], 1,
                       C_DIM);
        }
        for (row = 3; row < 8; ++row) {
            int inset = row == 3 || row == 7 ? 3 : 2;

            draw_block(layer, &dsc, coords.x1 + inset, coords.y1 + row, 11 - 2 * inset, 1, C_BG);
        }
    } else if (key == KEY_UP || key == KEY_DOWN) {
        for (row = 0; row < 6; ++row) {
            int width = key == KEY_UP ? 1 + 2 * row : 11 - 2 * row;

            draw_block(layer, &dsc, coords.x1 + (11 - width) / 2, coords.y1 + 2 + row, width, 1,
                       C_DIM);
        }
    }
}

// 她在跟帮手通信：一条点线，一封信在线上走。颜色是帮手的颜色。
static void link_draw_cb(lv_event_t *event)
{
    // 13×9 的小信封，一行一个 13 位的图案。
    static const uint16_t envelope[9] = {
        0x1FFF, 0x1803, 0x1405, 0x1209, 0x11F1, 0x1001, 0x1001, 0x1001, 0x1FFF,
    };
    lv_obj_t *obj = lv_event_get_current_target(event);
    lv_layer_t *layer = lv_event_get_layer(event);
    lv_draw_rect_dsc_t dsc;
    lv_area_t coords;
    int width;
    int travel;
    int letter_x;
    int x;
    int row;

    lv_obj_get_coords(obj, &coords);
    width = coords.x2 - coords.x1 + 1;
    travel = width - 13;
    // 信走四步到头，再从头来。
    letter_x = travel <= 0 ? 0 : (int)(s.pet_frame % 4U) * travel / 3;
    lv_draw_rect_dsc_init(&dsc);
    dsc.bg_opa = LV_OPA_COVER;
    for (x = 0; x + 3 <= width; x += 7) {
        if (x + 3 > letter_x - 2 && x < letter_x + 15) {
            continue;  // 信封两边留一点空
        }
        draw_block(layer, &dsc, coords.x1 + x, coords.y1 + 5, 3, 3, C_LINE);
    }
    for (row = 0; row < 9; ++row) {
        int bit;

        for (bit = 0; bit < 13; ++bit) {
            if ((envelope[row] >> (12 - bit)) & 1U) {
                draw_block(layer, &dsc, coords.x1 + letter_x + bit, coords.y1 + 2 + row, 1, 1,
                           s.link_color);
            }
        }
    }
}

// 说话时的音量条：最近 LEVEL_BARS 次采样，新的在右边。
static void bars_draw_cb(lv_event_t *event)
{
    lv_obj_t *obj = lv_event_get_current_target(event);
    lv_layer_t *layer = lv_event_get_layer(event);
    lv_draw_rect_dsc_t dsc;
    lv_area_t coords;
    int height;
    int index;

    lv_obj_get_coords(obj, &coords);
    height = coords.y2 - coords.y1 + 1;
    lv_draw_rect_dsc_init(&dsc);
    dsc.bg_opa = LV_OPA_COVER;
    for (index = 0; index < LEVEL_BARS; ++index) {
        int bar = 4 + (int)s.levels[index] * (height - 4) / 100;

        draw_block(layer, &dsc, coords.x1 + index * 9, coords.y1 + (height - bar) / 2, 5, bar,
                   C_OK);
    }
}

// 换固件的进度：UPDATE_CELLS 个格子，写进闪存多少就点亮多少。
static void update_bar_draw_cb(lv_event_t *event)
{
    lv_obj_t *obj = lv_event_get_current_target(event);
    lv_layer_t *layer = lv_event_get_layer(event);
    lv_draw_rect_dsc_t dsc;
    lv_area_t coords;
    int lit = (int)s.update_shown_percent * UPDATE_CELLS / 100;
    int index;

    lv_obj_get_coords(obj, &coords);
    lv_draw_rect_dsc_init(&dsc);
    dsc.bg_opa = LV_OPA_COVER;
    for (index = 0; index < UPDATE_CELLS; ++index) {
        draw_block(layer, &dsc, coords.x1 + index * (UPDATE_CELL_W + UPDATE_CELL_GAP), coords.y1,
                   UPDATE_CELL_W, UPDATE_BAR_H, index < lit ? C_XIAOYOU : C_LINE);
    }
}

// ---------------------------------------------------------------------------
// 构建
// ---------------------------------------------------------------------------

static void build_top_bar(lv_obj_t *root)
{
    s.link_dot = make_box(root, 20, 14, 7, 7);
    fill(s.link_dot, C_DIM, LV_RADIUS_CIRCLE);
    s.clock = make_label(root, &pocket_font_14, C_DIM, "");
    lv_obj_set_pos(s.clock, 33, 8);

    s.battery = make_box(root, SCREEN_W - 22 - 24, 12, 22, 12);
    outline(s.battery, C_DIM, 3, 1);
    s.battery_fill = make_box(s.battery, 2, 2, 16, 6);
    fill(s.battery_fill, C_OK, 1);
    s.battery_nub = make_box(root, SCREEN_W - 22 - 2, 16, 2, 4);
    fill(s.battery_nub, C_DIM, 1);
}

static void build_bottom(lv_obj_t *root)
{
    int index;

    for (index = 0; index < HINT_ITEMS; ++index) {
        s.hint_icons[index] = make_box(root, 0, HINT_Y + 3, 11, 11);
        lv_obj_add_event_cb(s.hint_icons[index], key_draw_cb, LV_EVENT_DRAW_MAIN,
                            (void *)(uintptr_t)index);
        lv_obj_add_flag(s.hint_icons[index], LV_OBJ_FLAG_HIDDEN);
        s.hint_labels[index] = make_label(root, &pocket_font_14, C_DIM, "");
        lv_obj_set_pos(s.hint_labels[index], 0, HINT_Y);
        lv_obj_add_flag(s.hint_labels[index], LV_OBJ_FLAG_HIDDEN);
    }
    // 设备自己要说的一句话（没发出去、蓝牙开关失败……）出现时，临时占用这一行。
    s.notice = make_text_block(root, &pocket_font_16, C_WARN, (SCREEN_W - HINT_W) / 2,
                               HINT_Y - 3, HINT_W, LINES_16(1));
    lv_obj_set_style_text_align(s.notice, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_add_flag(s.notice, LV_OBJ_FLAG_HIDDEN);
}

static void build_home(lv_obj_t *page)
{
    // 她独占画面：没连上、刚连上、还没聊过。
    s.solo = make_box(page, 0, 0, SCREEN_W, AREA_H);
    make_pet(s.solo, PET_SOLO, (SCREEN_W - POCKET_PET_GRID * PET_SOLO_SCALE) / 2, 6,
             PET_SOLO_SCALE);
    s.solo_title = make_label(s.solo, &pocket_font_22, C_TEXT, PT_HOME_WAITING);
    lv_obj_set_width(s.solo_title, INNER_W);
    lv_obj_set_style_text_align(s.solo_title, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_pos(s.solo_title, SIDE, 136);
    s.solo_sub = make_text_block(s.solo, &pocket_font_16, C_DIM, SIDE, 170, INNER_W,
                                 LINES_16(3));
    lv_obj_set_style_text_align(s.solo_sub, LV_TEXT_ALIGN_CENTER, 0);

    // 对话。顶上一条：缩小的小幽，她在跟谁通信（小幽 ···✉··· [帮手]），她在干什么。
    s.talk = make_box(page, 0, 0, SCREEN_W, HOME_H);
    make_pet(s.talk, PET_TALK, HEAD_PET_X, HEAD_PET_Y, PET_TALK_SCALE);
    s.head_name = make_label(s.talk, &pocket_font_14, C_XIAOYOU, PT_XIAOYOU);
    lv_obj_set_pos(s.head_name, HEAD_TEXT_X, 3);
    s.head_link = make_box(s.talk, HEAD_LINK_X, 5, HEAD_LINK_W, 13);
    lv_obj_add_event_cb(s.head_link, link_draw_cb, LV_EVENT_DRAW_MAIN, NULL);
    s.head_tag = make_box(s.talk, HEAD_TAG_X, 0, LV_SIZE_CONTENT, 22);
    outline(s.head_tag, C_XIAOYOU, 11, 2);
    lv_obj_set_style_pad_hor(s.head_tag, HEAD_TAG_PAD, 0);
    s.head_tag_label = make_label(s.head_tag, &pocket_font_16, C_XIAOYOU, "");
    lv_obj_align(s.head_tag_label, LV_ALIGN_LEFT_MID, 0, 0);
    s.head_status = make_text_block(s.talk, &pocket_font_16, C_DIM, HEAD_TEXT_X, HEAD_ROW2_Y,
                                    HEAD_TEXT_W, LINES_16(1));
    s.head_elapsed = make_label(s.talk, &pocket_font_16, C_XIAOYOU, "");
    lv_obj_set_width(s.head_elapsed, HEAD_ELAPSED_W);
    lv_obj_set_style_text_align(s.head_elapsed, LV_TEXT_ALIGN_RIGHT, 0);
    lv_obj_set_pos(s.head_elapsed, SCREEN_W - SIDE - HEAD_ELAPSED_W, HEAD_ROW2_Y);
    fill(make_box(s.talk, SIDE, HEAD_RULE_Y, INNER_W, 1), C_LINE, 0);
    s.link_color = C_XIAOYOU;

    // 对话的长卷。里面的字用到才创建（tl_label），这里只准备窗口和两种字的样式。
    s.tl_scroll = make_box(s.talk, SIDE, TL_Y, INNER_W, TL_H);
    lv_obj_add_flag(s.tl_scroll, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_scroll_dir(s.tl_scroll, LV_DIR_VER);
    // 每段字后面都留一个行间距，最后一段也一样：长卷的总高才是整行的倍数。
    lv_obj_set_style_pad_bottom(s.tl_scroll, LINE_SPACE, 0);
    style_scrollbar(s.tl_scroll);
    lv_style_init(&s.tl_style_reply);
    lv_style_set_text_font(&s.tl_style_reply, &pocket_font_16);
    lv_style_set_text_color(&s.tl_style_reply, lv_color_hex(C_TEXT));
    lv_style_set_text_line_space(&s.tl_style_reply, LINE_SPACE);
    lv_style_set_text_letter_space(&s.tl_style_reply, 0);
    lv_style_init(&s.tl_style_said);
    lv_style_set_text_font(&s.tl_style_said, &pocket_font_16);
    lv_style_set_text_color(&s.tl_style_said, lv_color_hex(C_DIM));
    lv_style_set_text_line_space(&s.tl_style_said, LINE_SPACE);
    lv_style_set_text_letter_space(&s.tl_style_said, 0);
    // 你说的话：左边一条小幽颜色的竖线，像引用。
    lv_style_set_border_side(&s.tl_style_said, LV_BORDER_SIDE_LEFT);
    lv_style_set_border_width(&s.tl_style_said, 3);
    lv_style_set_border_color(&s.tl_style_said, lv_color_hex(C_XIAOYOU));
    lv_style_set_border_opa(&s.tl_style_said, LV_OPA_COVER);
    lv_style_set_pad_left(&s.tl_style_said, 6);
    lv_obj_add_flag(s.talk, LV_OBJ_FLAG_HIDDEN);
}

static void build_notices(lv_obj_t *page)
{
    int index;

    make_title(page, PT_NOTICES_TITLE);
    for (index = 0; index < BUDDY_ENTRY_COUNT; ++index) {
        s.entry_rows[index] = make_box(page, SIDE, 36 + index * (ENTRY_ROW_H + ENTRY_ROW_GAP),
                                       INNER_W, ENTRY_ROW_H);
        fill(s.entry_rows[index], C_CARD, 10);
        s.entry_labels[index] = make_text_block(s.entry_rows[index], &pocket_font_16, C_TEXT,
                                                8, 3, INNER_W - 16, LINES_16(2));
    }
    s.notices_empty = make_label(page, &pocket_font_16, C_DIM, PT_NOTICES_EMPTY);
    lv_obj_align(s.notices_empty, LV_ALIGN_TOP_MID, 0, 100);
}

static void build_helpers(lv_obj_t *page)
{
    int index;

    make_title(page, PT_HELPERS_TITLE);
    for (index = 0; index < BUDDY_HELPER_COUNT; ++index) {
        lv_obj_t *row = make_box(page, SIDE, 36 + index * (ENTRY_ROW_H + ENTRY_ROW_GAP),
                                 INNER_W, ENTRY_ROW_H);

        fill(row, C_CARD, 10);
        s.helper_rows[index] = row;
        s.helper_tags[index] = make_box(row, 8, 4, LV_SIZE_CONTENT, 22);
        outline(s.helper_tags[index], C_XIAOYOU, 11, 2);
        lv_obj_set_style_pad_hor(s.helper_tags[index], 8, 0);
        s.helper_names[index] =
            make_label(s.helper_tags[index], &pocket_font_16, C_XIAOYOU, "");
        lv_obj_align(s.helper_names[index], LV_ALIGN_LEFT_MID, 0, 0);
        s.helper_abouts[index] = make_text_block(row, &pocket_font_16, C_DIM, 9, 27,
                                                 INNER_W - 18, LINES_16(1));
    }
    s.helpers_empty = make_label(page, &pocket_font_16, C_DIM, PT_HELPERS_EMPTY);
    lv_obj_set_style_text_align(s.helpers_empty, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_align(s.helpers_empty, LV_ALIGN_TOP_MID, 0, 96);
}

// 一行菜单：左边名字，右边可选的当前值。选中时整行铺小幽的颜色。
static void make_row(lv_obj_t *page, int index, const char *name, lv_obj_t **row_out,
                     lv_obj_t **label_out, lv_obj_t **value_out)
{
    lv_obj_t *row = make_box(page, SIDE, 36 + index * ROW_H, INNER_W, ROW_H - 2);

    lv_obj_set_style_radius(row, 9, 0);
    lv_obj_set_style_bg_color(row, lv_color_hex(C_XIAOYOU), 0);
    lv_obj_set_style_bg_opa(row, LV_OPA_TRANSP, 0);
    *row_out = row;
    *label_out = make_label(row, &pocket_font_16, C_TEXT, name);
    lv_obj_align(*label_out, LV_ALIGN_LEFT_MID, 10, 0);
    if (value_out != NULL) {
        *value_out = make_label(row, &pocket_font_16, C_DIM, "");
        lv_obj_set_width(*value_out, 70);
        lv_obj_set_style_text_align(*value_out, LV_TEXT_ALIGN_RIGHT, 0);
        lv_obj_align(*value_out, LV_ALIGN_RIGHT_MID, -10, 0);
    }
}

static void build_menu(lv_obj_t *page)
{
    static const char *const names[BUDDY_MENU_COUNT] = {
        [BUDDY_MENU_NOTICES] = PT_MENU_NOTICES,
        [BUDDY_MENU_HELPERS] = PT_MENU_HELPERS,
        [BUDDY_MENU_BRIGHTNESS] = PT_MENU_BRIGHTNESS,
        [BUDDY_MENU_BLE] = PT_MENU_BLE,
        [BUDDY_MENU_SCREEN_OFF] = PT_MENU_SCREEN_OFF,
        [BUDDY_MENU_MORE] = PT_MENU_MORE,
        [BUDDY_MENU_BACK] = PT_MENU_BACK,
    };
    int index;

    make_title(page, PT_MENU_TITLE);
    for (index = 0; index < BUDDY_MENU_COUNT; ++index) {
        make_row(page, index, names[index], &s.menu_rows[index], &s.menu_labels[index],
                 &s.menu_values[index]);
    }
}

static void build_more(lv_obj_t *page)
{
    static const char *const names[BUDDY_MORE_COUNT] = {
        [BUDDY_MORE_GUIDE] = PT_MORE_GUIDE,
        [BUDDY_MORE_UNPAIR] = PT_MORE_UNPAIR,
        [BUDDY_MORE_FACTORY_RESET] = PT_MORE_FACTORY,
        [BUDDY_MORE_BACK] = PT_MENU_BACK,
    };
    int index;

    make_title(page, PT_MORE_TITLE);
    for (index = 0; index < BUDDY_MORE_COUNT; ++index) {
        make_row(page, index, names[index], &s.more_rows[index], &s.more_labels[index], NULL);
    }
}

static void build_guide(lv_obj_t *page)
{
    lv_obj_t *body;

    make_title(page, PT_GUIDE_TITLE);
    s.guide_name = make_text_block(page, &pocket_font_16, C_XIAOYOU, SIDE + 2, 32,
                                   INNER_W - 4, LINES_16(1));
    s.guide_scroll = make_scroll_card(page, 58, LINES_16(8) + 8, &body);
    lv_label_set_text(body, PT_GUIDE_BODY);
}

static void build_approval(lv_obj_t *root)
{
    lv_obj_t *bubble;
    lv_obj_t *question;

    s.approval = make_box(root, 0, AREA_Y, SCREEN_W, AREA_H + 8);
    bubble = make_speech(s.approval, PET_APPROVAL, &question);
    lv_label_set_text(question, PT_ASK_APPROVAL);
    // 是谁、要做什么，占气泡的第三行：这是判断要不要放行时最先要看的信息。
    s.approval_tool = make_text_block(bubble, &pocket_font_16, C_WARN, BUBBLE_PAD_X,
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
    s.pairing = make_box(root, 0, AREA_Y, SCREEN_W, AREA_H + 8);
    (void)make_speech(s.pairing, PET_PAIRING, &s.pairing_text);
    s.pairing_code = make_label(s.pairing, &pocket_font_num_44, C_XIAOYOU, "");
    lv_obj_align(s.pairing_code, LV_ALIGN_TOP_MID, 0, BUBBLE_H + 40);
}

static void build_confirm(lv_obj_t *root)
{
    s.confirm = make_box(root, 0, AREA_Y, SCREEN_W, AREA_H + 8);
    (void)make_speech(s.confirm, PET_CONFIRM, &s.confirm_text);
    make_action_bar(s.confirm, ACTION_Y1, C_DANGER, true, PT_KEY_OK, PT_CONFIRM_YES);
    make_action_bar(s.confirm, ACTION_Y2, C_DIM, false, PT_KEY_DOWN, PT_CONFIRM_NO);
}

static void build_voice(lv_obj_t *root)
{
    s.voice = make_box(root, 0, AREA_Y, SCREEN_W, AREA_H);
    make_pet(s.voice, PET_VOICE, (SCREEN_W - POCKET_PET_GRID * PET_SOLO_SCALE) / 2, 0,
             PET_SOLO_SCALE);
    s.voice_title = make_label(s.voice, &pocket_font_22, C_OK, PT_VOICE_LISTENING);
    lv_obj_set_width(s.voice_title, INNER_W);
    lv_obj_set_style_text_align(s.voice_title, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_pos(s.voice_title, SIDE, 128);
    s.voice_sub = make_label(s.voice, &pocket_font_16, C_DIM, "");
    lv_obj_set_width(s.voice_sub, INNER_W);
    lv_obj_set_style_text_align(s.voice_sub, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_pos(s.voice_sub, SIDE, 166);
    // 音量条在动，就是“现在说话听得到”。
    s.voice_bars = make_box(s.voice, (SCREEN_W - (LEVEL_BARS * 9 - 4)) / 2, 166,
                            LEVEL_BARS * 9 - 4, 44);
    lv_obj_add_event_cb(s.voice_bars, bars_draw_cb, LV_EVENT_DRAW_MAIN, NULL);
    s.voice_timer = make_label(s.voice, &pocket_font_16, C_TEXT, "");
    lv_obj_set_width(s.voice_timer, INNER_W);
    lv_obj_set_style_text_align(s.voice_timer, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_pos(s.voice_timer, SIDE, 218);
}

static void build_update(lv_obj_t *root)
{
    s.update = make_box(root, 0, AREA_Y, SCREEN_W, AREA_H + 8);
    make_pet(s.update, PET_UPDATE, (SCREEN_W - POCKET_PET_GRID * PET_SOLO_SCALE) / 2, 0,
             PET_SOLO_SCALE);
    s.update_title = make_label(s.update, &pocket_font_22, C_XIAOYOU, PT_UPDATE_RECEIVING);
    lv_obj_set_width(s.update_title, INNER_W);
    lv_obj_set_style_text_align(s.update_title, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_pos(s.update_title, SIDE, 128);
    s.update_bar = make_box(s.update, (SCREEN_W - UPDATE_BAR_W) / 2, 170, UPDATE_BAR_W,
                            UPDATE_BAR_H);
    lv_obj_add_event_cb(s.update_bar, update_bar_draw_cb, LV_EVENT_DRAW_MAIN, NULL);
    s.update_percent = make_label(s.update, &pocket_font_16, C_TEXT, "");
    lv_obj_set_width(s.update_percent, INNER_W);
    lv_obj_set_style_text_align(s.update_percent, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_pos(s.update_percent, SIDE, 192);
    s.update_sub = make_label(s.update, &pocket_font_16, C_DIM, "");
    lv_obj_set_width(s.update_sub, INNER_W);
    lv_obj_set_style_text_align(s.update_sub, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_pos(s.update_sub, SIDE, 226);
}

void pocket_ui_init(void)
{
    static void (*const builders[BUDDY_PAGE_COUNT])(lv_obj_t *) = {
        [BUDDY_PAGE_HOME] = build_home,       [BUDDY_PAGE_MENU] = build_menu,
        [BUDDY_PAGE_NOTICES] = build_notices, [BUDDY_PAGE_HELPERS] = build_helpers,
        [BUDDY_PAGE_MORE] = build_more,       [BUDDY_PAGE_GUIDE] = build_guide,
    };
    lv_obj_t *root = lv_obj_create(NULL);
    int index;

    lv_obj_remove_style_all(root);
    lv_obj_remove_flag(root, LV_OBJ_FLAG_SCROLLABLE);
    fill(root, C_BG, 0);

    build_top_bar(root);
    for (index = 0; index < BUDDY_PAGE_COUNT; ++index) {
        s.pages[index] = make_box(root, 0, AREA_Y, SCREEN_W,
                                  index == BUDDY_PAGE_HOME ? HOME_H : AREA_H);
        builders[index](s.pages[index]);
        lv_obj_add_flag(s.pages[index], LV_OBJ_FLAG_HIDDEN);
    }
    build_approval(root);
    build_pairing(root);
    build_confirm(root);
    build_voice(root);
    build_update(root);
    lv_obj_add_flag(s.update, LV_OBJ_FLAG_HIDDEN);
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

// 底部的按键提示：最多三项，每项是一个键的图标加几个字，整体居中。
static void set_hints(const hint_item_t *items, int count)
{
    bool same = true;
    int widths[HINT_ITEMS] = {0};
    int total = 0;
    int index;
    int x;

    for (index = 0; index < HINT_ITEMS; ++index) {
        key_icon_t key = index < count ? items[index].key : KEY_NONE;
        const char *text = index < count ? items[index].text : NULL;

        same = same && s.hint_keys[index] == key && s.hint_texts[index] == text;
    }
    if (same) {
        return;
    }
    for (index = 0; index < HINT_ITEMS; ++index) {
        bool used = index < count;

        s.hint_keys[index] = used ? items[index].key : KEY_NONE;
        s.hint_texts[index] = used ? items[index].text : NULL;
        set_visible(s.hint_icons[index], used);
        set_visible(s.hint_labels[index], used);
        if (used) {
            lv_label_set_text(s.hint_labels[index], items[index].text);
            lv_obj_update_layout(s.hint_labels[index]);
            widths[index] = 11 + 4 + lv_obj_get_width(s.hint_labels[index]);
            total += widths[index] + (index > 0 ? 14 : 0);
            lv_obj_invalidate(s.hint_icons[index]);
        }
    }
    x = (SCREEN_W - total) / 2;
    for (index = 0; index < count && index < HINT_ITEMS; ++index) {
        lv_obj_set_x(s.hint_icons[index], x);
        lv_obj_set_x(s.hint_labels[index], x + 15);
        x += widths[index] + 14;
    }
}

static void render_top_bar(const buddy_ui_snapshot_t *snap)
{
    char text[16];
    uint32_t color = C_DIM;

    // 圆点：灰是没连上，黄是正在配对，绿是连好了。
    if (snap->ble_enabled && snap->ble_connected) {
        color = snap->ble_encrypted ? C_OK : C_WARN;
    }
    set_bg_color(s.link_dot, color);

    (void)pocket_format_clock(snap->epoch_seconds, snap->timezone_offset_seconds,
                              snap->time_received_ms, snap->uptime_ms, text, sizeof(text));
    set_text(s.clock, text);

    set_visible(s.battery, snap->battery_available);
    set_visible(s.battery_nub, snap->battery_available);
    if (snap->battery_available) {
        unsigned percent = snap->battery_percent > 100U ? 100U : snap->battery_percent;
        int width = (int)(16U * percent / 100U);

        if (width < 2) {
            width = 2;
        }
        if (lv_obj_get_width(s.battery_fill) != width) {
            lv_obj_set_width(s.battery_fill, width);
        }
        set_bg_color(s.battery_fill, percent <= 20U ? C_DANGER : C_OK);
    }
}

// ---- 首页的对话长卷 ----

static uintptr_t text_hash(const char *text)
{
    uintptr_t hash = 2166136261u;
    const unsigned char *cursor;

    for (cursor = (const unsigned char *)text; *cursor != '\0'; ++cursor) {
        hash = (hash ^ *cursor) * 16777619u;
    }
    return (hash & 0xFFFFFFFFu) | 1u;  // 非零，和“从未设置过”区分开
}

// 长卷里的一段字：第 slot 轮里你说的（said）或她说的。用到才创建。
static lv_obj_t *tl_label(int slot, bool said)
{
    lv_obj_t **ref = said ? &s.tl_said[slot] : &s.tl_reply[slot];

    if (*ref == NULL) {
        lv_obj_t *label = lv_label_create(s.tl_scroll);

        if (label == NULL) {
            return NULL;
        }
        lv_obj_add_style(label, said ? &s.tl_style_said : &s.tl_style_reply, 0);
        lv_label_set_long_mode(label, LV_LABEL_LONG_MODE_WRAP);
        lv_obj_set_width(label, TL_TEXT_W);
        lv_label_set_text(label, "");
        lv_obj_add_flag(label, LV_OBJ_FLAG_HIDDEN);
        *ref = label;
    }
    return *ref;
}

// 给这一段字换内容；text 为空就是这一段不出现。返回有没有变（变了要重新排）。
static bool tl_set(int slot, bool said, const char *text)
{
    lv_obj_t *label = said ? s.tl_said[slot] : s.tl_reply[slot];
    uintptr_t hash;
    bool changed;

    if (text == NULL || text[0] == '\0') {
        if (label == NULL || lv_obj_has_flag(label, LV_OBJ_FLAG_HIDDEN)) {
            return false;
        }
        // 不显示的字不留着：内存留给别的。
        lv_label_set_text(label, "");
        lv_obj_set_user_data(label, NULL);
        lv_obj_add_flag(label, LV_OBJ_FLAG_HIDDEN);
        return true;
    }
    label = tl_label(slot, said);
    if (label == NULL) {
        return false;
    }
    hash = text_hash(text);
    changed = lv_obj_has_flag(label, LV_OBJ_FLAG_HIDDEN) ||
              (uintptr_t)lv_obj_get_user_data(label) != hash;
    if ((uintptr_t)lv_obj_get_user_data(label) != hash) {
        lv_label_set_text(label, text);
        lv_obj_set_user_data(label, (void *)hash);
    }
    lv_obj_remove_flag(label, LV_OBJ_FLAG_HIDDEN);
    return changed;
}

static void tl_clear(void)
{
    int slot;

    for (slot = 0; slot < TL_SLOTS; ++slot) {
        (void)tl_set(slot, true, NULL);
        (void)tl_set(slot, false, NULL);
    }
    s.tl_valid = false;
    s.tl_content = 0;
    s.tl_last_top = 0;
    s.tl_y = 0;
}

// 把各段字从上到下排好，再决定窗口停在哪。reveal_slot >= 0 表示之前收起来的刚被
// 接回来，窗口停在接回来的最后一轮上。
static void tl_layout(int reveal_slot)
{
    int y = 0;
    int last_top = 0;
    int target = -1;
    int slot;

    lv_obj_update_layout(s.tl_scroll);
    for (slot = 0; slot < TL_SLOTS; ++slot) {
        lv_obj_t *const parts[2] = {s.tl_said[slot], s.tl_reply[slot]};
        int top = y;
        int part;

        // 没有“你说的”那一段的轮次（电脑上的 Claude 自己说的话）靠空一行和上一轮分开。
        if (y > 0 && (parts[0] == NULL || lv_obj_has_flag(parts[0], LV_OBJ_FLAG_HIDDEN)) &&
            parts[1] != NULL && !lv_obj_has_flag(parts[1], LV_OBJ_FLAG_HIDDEN)) {
            y += LINE_16;
            top = y;
        }
        for (part = 0; part < 2; ++part) {
            if (parts[part] != NULL && !lv_obj_has_flag(parts[part], LV_OBJ_FLAG_HIDDEN)) {
                lv_obj_set_pos(parts[part], 0, y);
                y += lv_obj_get_height(parts[part]) + LINE_SPACE;
            }
        }
        if (y != top) {
            last_top = top;
            if (slot == reveal_slot) {
                target = top;
            }
        }
    }
    s.tl_content = y;
    s.tl_last_top = last_top;
    if (target >= 0) {
        s.tl_y = pocket_timeline_step(target, 0, y, TL_H, 0);
        s.tl_follow = false;
    } else if (s.tl_follow) {
        s.tl_y = pocket_timeline_anchor(y, last_top, TL_H);
    } else {
        s.tl_y = pocket_timeline_step(s.tl_y, 0, y, TL_H, 0);
    }
    // 位置是刚设的，先让 LVGL 算好，滚动的范围才是对的。
    lv_obj_update_layout(s.tl_scroll);
    lv_obj_scroll_to_y(s.tl_scroll, s.tl_y, LV_ANIM_OFF);
}

// 她的话后面要不要跟一句“太长了”。
static const char *with_cut_note(char *buffer, size_t size, const char *reply, bool cut)
{
    if (!cut) {
        return reply;
    }
    (void)snprintf(buffer, size, "%s%s\n%s", reply, PT_ELLIPSIS, PT_READER_CUT);
    return buffer;
}

static void render_timeline(const buddy_ui_snapshot_t *snap, pocket_home_t home)
{
    static char text[BUDDY_REPLY_MAX + 64];
    static char line[BUDDY_AGENT_MAX + 48];
    const buddy_history_t *history = snap->history;
    unsigned visible = buddy_history_visible(history);
    uint32_t revision = history != NULL ? history->revision : 0U;
    bool hub = snap->chat.phase != BUDDY_CHAT_NONE;
    bool changed = !s.tl_valid;
    int reveal_slot = -1;
    const char *said = "";
    const char *body = "";
    uint32_t body_color = C_TEXT;
    int slot;

    if (!s.tl_valid) {
        s.tl_follow = true;
        s.tl_recall = snap->recall_serial;
    }
    // 之前的各轮：历史没变就不用再看一遍。
    if (!s.tl_valid || s.tl_revision != revision) {
        for (slot = 0; slot < BUDDY_HISTORY_TURNS; ++slot) {
            unsigned index = (history != NULL ? history->floor : 0U) + (unsigned)slot;
            bool present = (unsigned)slot < visible;
            uint8_t flags = present ? history->turns[index].flags : 0U;
            const char *reply = present ? buddy_history_reply(history, index) : "";

            changed = tl_set(slot, true, present ? buddy_history_said(history, index) : NULL) ||
                      changed;
            if (present && reply[0] == '\0') {
                reply = PT_TALK_FAILED;  // 没成，也没说为什么
            }
            changed = tl_set(slot, false,
                             present ? with_cut_note(text, sizeof(text), reply,
                                                     (flags & BUDDY_TURN_CUT) != 0U)
                                     : NULL) ||
                      changed;
            if (present && s.tl_reply[slot] != NULL) {
                // 没成的那几轮暗一些。
                set_text_color(s.tl_reply[slot],
                               (flags & BUDDY_TURN_FAILED) != 0U ? C_DIM : C_TEXT);
            }
        }
        s.tl_revision = revision;
    }
    if (s.tl_recall != snap->recall_serial) {
        // 收起来的刚被接回来：它们排在最前面，停在其中最后一轮上。
        s.tl_recall = snap->recall_serial;
        reveal_slot = snap->recalled > 0U ? (int)snap->recalled - 1 : -1;
        changed = true;
    }

    // 正在进行（或刚说完）的这一轮。
    switch (home) {
    case POCKET_HOME_SENT:
        said = PT_SAID_PENDING;
        body = PT_TALK_SENT;
        body_color = C_DIM;
        break;
    case POCKET_HOME_THINKING:
        if (hub) {
            said = snap->chat.said[0] != '\0' ? snap->chat.said : PT_SAID_PENDING;
            body = snap->chat.stage[0] != '\0' ? snap->chat.stage : PT_TALK_THINKING;
        } else {
            body = snap->message[0] != '\0' ? snap->message : PT_DESKTOP_WORKING;
        }
        body_color = C_DIM;
        break;
    case POCKET_HOME_HELPER:
        said = snap->chat.said;
        if (snap->chat.stage[0] != '\0') {
            body = snap->chat.stage;
        } else {
            (void)snprintf(line, sizeof(line), PT_HELPER_ASKED, snap->chat.agent);
            body = line;
        }
        break;
    case POCKET_HOME_FAILED:
        said = snap->chat.said;
        body = snap->reply[0] != '\0' ? snap->reply : PT_TALK_FAILED;
        break;
    case POCKET_HOME_ANSWERED:
        said = hub ? snap->chat.said : "";
        body = with_cut_note(text, sizeof(text), snap->reply, snap->reply_truncated);
        break;
    default:
        break;  // 这一段里只有之前的对话
    }
    changed = tl_set(TL_LIVE, true, said) || changed;
    changed = tl_set(TL_LIVE, false, body) || changed;
    if (s.tl_reply[TL_LIVE] != NULL) {
        set_text_color(s.tl_reply[TL_LIVE], body_color);
    }
    if (s.tl_serial != snap->turn_serial) {
        // 新的一轮开始了：窗口回到它身上。
        s.tl_serial = snap->turn_serial;
        s.tl_follow = true;
        changed = true;
    }
    s.tl_valid = true;
    if (changed) {
        tl_layout(reveal_slot);
    }
}

// 对话版式顶上那一条：小幽的表情，她在跟谁通信，她在干什么。
static void render_head(const buddy_ui_snapshot_t *snap, pocket_home_t home)
{
    bool hub = snap->chat.phase != BUDDY_CHAT_NONE;
    bool helper = home == POCKET_HOME_HELPER;
    bool timer = false;
    const char *status = "";
    uint32_t status_color = C_DIM;
    uint32_t color = C_XIAOYOU;
    char elapsed[12];

    pet_set(PET_TALK, pocket_pet_for(snap));
    switch (home) {
    case POCKET_HOME_BLE_OFF:
        status = PT_HOME_BLE_OFF;
        break;
    case POCKET_HOME_WAITING:
        status = PT_HOME_WAITING;
        break;
    case POCKET_HOME_LINKING:
        status = PT_HOME_LINKING;
        break;
    case POCKET_HOME_QUIET:
        status = PT_HOME_QUIET;
        break;
    case POCKET_HOME_SENT:
        status = PT_HEAD_SENT;
        timer = true;
        break;
    case POCKET_HOME_THINKING:
        status = hub ? PT_HEAD_THINKING : PT_DESKTOP_BUSY;
        timer = hub;
        break;
    case POCKET_HOME_HELPER:
        status = PT_HEAD_WORKING;
        timer = true;
        color = pocket_helper_color(snap->chat.agent);
        break;
    case POCKET_HOME_ANSWERED:
        status = hub ? PT_HEAD_DONE : PT_DESKTOP_REPLY;
        break;
    case POCKET_HOME_FAILED:
        status = PT_HEAD_FAILED;
        status_color = C_DANGER;
        break;
    }

    set_visible(s.head_link, helper);
    set_visible(s.head_tag, helper);
    if (helper) {
        if (s.link_color != color) {
            s.link_color = color;
            lv_obj_invalidate(s.head_link);
        }
        if (strcmp(s.head_tag_name, snap->chat.agent) != 0 ||
            lv_label_get_text(s.head_tag_label)[0] == '\0') {
            // 名牌跟着名字伸缩；名字太长就定宽，打省略号。
            (void)snprintf(s.head_tag_name, sizeof(s.head_tag_name), "%s", snap->chat.agent);
            lv_label_set_long_mode(s.head_tag_label, LV_LABEL_LONG_MODE_WRAP);
            lv_obj_set_size(s.head_tag_label, LV_SIZE_CONTENT, LV_SIZE_CONTENT);
            lv_label_set_text(s.head_tag_label, snap->chat.agent);
            lv_obj_update_layout(s.head_tag_label);
            if (lv_obj_get_width(s.head_tag_label) > HEAD_TAG_TEXT_W) {
                lv_label_set_long_mode(s.head_tag_label, LV_LABEL_LONG_MODE_DOTS);
                lv_obj_set_size(s.head_tag_label, HEAD_TAG_TEXT_W, LINES_16(1));
                lv_label_set_text(s.head_tag_label, snap->chat.agent);
            }
        }
        set_text_color(s.head_tag_label, color);
        set_border_color(s.head_tag, color);
    }
    // 计时在右边占一块；没有计时的时候这一行都给状态。
    if (lv_obj_get_width(s.head_status) != (timer ? HEAD_TEXT_W - HEAD_ELAPSED_W - 2 : HEAD_TEXT_W)) {
        lv_obj_set_width(s.head_status, timer ? HEAD_TEXT_W - HEAD_ELAPSED_W - 2 : HEAD_TEXT_W);
        lv_obj_set_user_data(s.head_status, NULL);
    }
    set_block_text(s.head_status, status);
    set_text_color(s.head_status, status_color);
    set_visible(s.head_elapsed, timer);
    if (timer) {
        (void)pocket_format_elapsed(snap->chat_since_ms, snap->uptime_ms, elapsed,
                                    sizeof(elapsed));
        set_text(s.head_elapsed, elapsed);
        set_text_color(s.head_elapsed, color);
    }
}

// 首页。返回用的是不是对话版式。
static bool render_home(const buddy_ui_snapshot_t *snap)
{
    static char line[BUDDY_MESSAGE_MAX + BUDDY_NAME_MAX + 8];
    pocket_home_t home = pocket_home_for(snap);
    bool talk = pocket_home_shows_talk(snap);

    set_visible(s.solo, !talk);
    set_visible(s.talk, talk);
    if (!talk) {
        const char *title = PT_HOME_QUIET;
        const char *sub = PT_HOME_SUB_QUIET;
        bool notice = pocket_notice_visible(snap);

        if (s.tl_valid) {
            tl_clear();
        }
        switch (home) {
        case POCKET_HOME_BLE_OFF:
            title = PT_HOME_BLE_OFF;
            sub = PT_HOME_SUB_BLE_OFF;
            break;
        case POCKET_HOME_WAITING:
            title = PT_HOME_WAITING;
            (void)snprintf(line, sizeof(line), "%s\n%s", PT_HOME_SUB_WAITING, snap->name);
            sub = line;
            break;
        case POCKET_HOME_LINKING:
            title = PT_HOME_LINKING;
            sub = PT_HOME_SUB_LINKING;
            break;
        default:
            // 连着手机中枢才能按住说话；别的连接只能等对面有事。
            sub = snap->host_hub ? PT_HOME_SUB_QUIET : PT_HOME_SUB_NO_VOICE;
            if (snap->host_chat && snap->waiting > 0U) {
                // 别的 App 有通知没看：不打断对话，只在这儿提一句。
                (void)snprintf(line, sizeof(line), PT_HOME_SUB_NOTICES,
                               (unsigned)snap->waiting);
                sub = line;
            }
            break;
        }
        set_text(s.solo_title, title);
        set_text_color(s.solo_title, home == POCKET_HOME_BLE_OFF ? C_DIM : C_TEXT);
        // 设备或对面有一句话要说时，说明的位置让给它。
        set_block_text(s.solo_sub, notice ? snap->message : sub);
        set_text_color(s.solo_sub, notice ? C_WARN : C_DIM);
        pet_set(PET_SOLO, pocket_pet_for(snap));
        return false;
    }
    render_head(snap, home);
    render_timeline(snap, home);
    return true;
}

static void render_notices(const buddy_ui_snapshot_t *snap)
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
    set_visible(s.notices_empty, !any);
}

static void render_helpers(const buddy_ui_snapshot_t *snap)
{
    unsigned count = snap->helper_count < BUDDY_HELPER_COUNT ? snap->helper_count
                                                              : BUDDY_HELPER_COUNT;
    unsigned index;

    for (index = 0; index < BUDDY_HELPER_COUNT; ++index) {
        bool present = index < count && snap->helpers[index].name[0] != '\0';

        set_visible(s.helper_rows[index], present);
        if (present) {
            uint32_t color = pocket_helper_color(snap->helpers[index].name);

            set_text(s.helper_names[index], snap->helpers[index].name);
            set_text_color(s.helper_names[index], color);
            set_border_color(s.helper_tags[index], color);
            set_block_text(s.helper_abouts[index], snap->helpers[index].about);
        }
    }
    set_visible(s.helpers_empty, count == 0U);
}

static void render_rows(lv_obj_t *const *rows, lv_obj_t *const *labels, lv_obj_t *const *values,
                        int count, int selected, int first_destructive, int last_destructive)
{
    int index;

    for (index = 0; index < count; ++index) {
        bool chosen = index == selected;
        bool destructive = index >= first_destructive && index <= last_destructive;

        set_bg_opa(rows[index], chosen ? LV_OPA_COVER : LV_OPA_TRANSP);
        set_text_color(labels[index], chosen ? C_ON_COLOR : (destructive ? C_DANGER : C_TEXT));
        if (values != NULL) {
            set_text_color(values[index], chosen ? C_ON_COLOR : C_DIM);
        }
    }
}

static void render_menu(const buddy_ui_snapshot_t *snap)
{
    char text[16];
    unsigned notices = 0;
    unsigned index;

    render_rows(s.menu_rows, s.menu_labels, s.menu_values, BUDDY_MENU_COUNT,
                (int)snap->menu_selection, -1, -2);
    for (index = 0; index < BUDDY_ENTRY_COUNT; ++index) {
        notices += snap->entries[index][0] != '\0' ? 1U : 0U;
    }
    (void)snprintf(text, sizeof(text), "%u", notices);
    set_text(s.menu_values[BUDDY_MENU_NOTICES], notices > 0U ? text : "");
    (void)snprintf(text, sizeof(text), "%u",
                   snap->helper_count < BUDDY_HELPER_COUNT ? snap->helper_count
                                                           : (unsigned)BUDDY_HELPER_COUNT);
    set_text(s.menu_values[BUDDY_MENU_HELPERS], snap->helper_count > 0U ? text : "");
    (void)snprintf(text, sizeof(text), "%u%%",
                   20U + (unsigned)(snap->brightness_level >= BUDDY_BRIGHTNESS_LEVELS
                                        ? BUDDY_BRIGHTNESS_LEVELS - 1U
                                        : snap->brightness_level) * 20U);
    set_text(s.menu_values[BUDDY_MENU_BRIGHTNESS], text);
    set_text(s.menu_values[BUDDY_MENU_BLE], snap->ble_enabled ? PT_ON : PT_OFF);
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
    // 气泡里的名字只有一行；放不下时在下面的卡片开头再完整写一遍，
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

static void render_voice(const buddy_ui_snapshot_t *snap, bool entered)
{
    bool listening = snap->voice_phase == BUDDY_VOICE_LISTENING;
    bool sending = snap->voice_phase == BUDDY_VOICE_SENDING;
    char timer[12];

    pet_set(PET_VOICE, pocket_pet_for(snap));
    set_text(s.voice_title, listening ? PT_VOICE_LISTENING
                                      : (sending ? PT_VOICE_SENDING : PT_VOICE_PREPARING));
    set_text_color(s.voice_title, listening ? C_OK : (sending ? C_XIAOYOU : C_WARN));
    set_visible(s.voice_sub, !listening);
    set_visible(s.voice_bars, listening);
    set_visible(s.voice_timer, listening);
    if (!listening) {
        set_text(s.voice_sub, sending ? PT_VOICE_SUB_SENDING : PT_VOICE_SUB_PREPARING);
        memset(s.levels, 0, sizeof(s.levels));
        s.level_sampled_ms = 0;
        return;
    }
    if (entered || s.level_sampled_ms == 0U || snap->uptime_ms < s.level_sampled_ms ||
        snap->uptime_ms - s.level_sampled_ms >= LEVEL_SAMPLE_MS) {
        memmove(s.levels, s.levels + 1, sizeof(s.levels) - 1U);
        s.levels[LEVEL_BARS - 1] = snap->voice_level > 100U ? 100U : snap->voice_level;
        s.level_sampled_ms = snap->uptime_ms != 0U ? snap->uptime_ms : 1U;
        lv_obj_invalidate(s.voice_bars);
    }
    (void)pocket_format_elapsed(snap->voice_listening_since_ms, snap->uptime_ms, timer,
                                sizeof(timer));
    set_text(s.voice_timer, timer);
}

static void render_update(const buddy_ui_snapshot_t *snap)
{
    bool restarting = snap->update_phase == POCKET_UPDATE_RESTARTING;
    bool checking = snap->update_phase == POCKET_UPDATE_CHECKING;
    uint8_t percent = restarting || checking
                          ? 100U
                          : (snap->update_percent > 100U ? 100U : snap->update_percent);
    char text[8];

    pet_set(PET_UPDATE, pocket_pet_for(snap));
    set_text(s.update_title, restarting ? PT_UPDATE_RESTARTING
                                        : (checking ? PT_UPDATE_CHECKING : PT_UPDATE_RECEIVING));
    set_text_color(s.update_title, restarting ? C_OK : C_XIAOYOU);
    set_text(s.update_sub, restarting ? PT_UPDATE_SUB_RESTARTING
                                      : (checking ? PT_UPDATE_SUB_CHECKING
                                                  : PT_UPDATE_SUB_RECEIVING));
    (void)snprintf(text, sizeof(text), "%u%%", (unsigned)percent);
    set_text(s.update_percent, text);
    if (percent != s.update_shown_percent) {
        s.update_shown_percent = percent;
        lv_obj_invalidate(s.update_bar);
    }
}

void pocket_ui_render(const buddy_ui_snapshot_t *snap)
{
    static const hint_item_t hint_none[1] = {{KEY_NONE, NULL}};
    hint_item_t hints[HINT_ITEMS];
    pocket_view_t view;
    buddy_page_t page;
    bool entered;
    bool notice = false;
    int count = 0;
    int index;

    if (!s.ready || snap == NULL) {
        return;
    }
    view = pocket_view_for(snap);
    page = snap->page < BUDDY_PAGE_COUNT ? snap->page : BUDDY_PAGE_HOME;
    entered = s.view != view || s.page != page;

    for (index = 0; index < BUDDY_PAGE_COUNT; ++index) {
        set_visible(s.pages[index], view == POCKET_VIEW_PAGE && index == (int)page);
    }
    set_visible(s.approval, view == POCKET_VIEW_APPROVAL);
    set_visible(s.pairing, view == POCKET_VIEW_PAIRING);
    set_visible(s.confirm, view == POCKET_VIEW_CONFIRM);
    set_visible(s.voice, view == POCKET_VIEW_VOICE);
    set_visible(s.update, view == POCKET_VIEW_UPDATE);
    if (view == POCKET_VIEW_PAGE && page == BUDDY_PAGE_GUIDE && entered) {
        lv_obj_scroll_to_y(s.guide_scroll, 0, LV_ANIM_OFF);
    }
    if (view != POCKET_VIEW_APPROVAL) {
        s.approval_id[0] = '\0';
    }
    s.view = view;
    s.page = page;

    render_top_bar(snap);
    switch (view) {
    case POCKET_VIEW_UPDATE:
        // 这时按键都不管用，所以底下不写提示。
        render_update(snap);
        break;
    case POCKET_VIEW_CONFIRM:
        render_confirm(snap);
        break;
    case POCKET_VIEW_PAIRING:
        render_pairing(snap);
        break;
    case POCKET_VIEW_VOICE:
        render_voice(snap, entered);
        if (snap->voice_phase != BUDDY_VOICE_SENDING) {
            hints[count++] = (hint_item_t){KEY_OK, PT_HINT_RELEASE};
        }
        break;
    case POCKET_VIEW_APPROVAL:
        render_approval(snap);
        if (!snap->approval_locked) {
            hints[count++] = (hint_item_t){KEY_UP, PT_HINT_FULL};
        }
        break;
    case POCKET_VIEW_PAGE:
        switch (page) {
        case BUDDY_PAGE_HOME: {
            pocket_home_t home = pocket_home_for(snap);
            bool can_talk = snap->host_hub && home >= POCKET_HOME_QUIET;
            bool busy = home == POCKET_HOME_SENT || home == POCKET_HOME_THINKING ||
                        home == POCKET_HOME_HELPER;
            // 之前的对话收起来了：双击上键接回来。
            bool earlier = buddy_history_hidden(snap->history) > 0U;
            bool talk = render_home(snap);

            // 对话版式里，设备自己要说的一句话临时占用提示这一行。
            notice = talk && pocket_notice_visible(snap) && snap->host_hub;
            if (talk) {
                bool up = s.tl_y > 0;
                bool down = s.tl_y < s.tl_content - TL_H;

                if (up) {
                    hints[count++] = (hint_item_t){KEY_UP, PT_HINT_UP};
                } else if (earlier) {
                    hints[count++] = (hint_item_t){KEY_UP, PT_HINT_EARLIER};
                }
                // “双击看之前”比较长，和“往下”挤不下，这时让给它。
                if (down && (up || !earlier)) {
                    hints[count++] = (hint_item_t){KEY_DOWN, PT_HINT_DOWN};
                }
                if (can_talk) {
                    hints[count++] =
                        (hint_item_t){KEY_OK, busy ? PT_HINT_CUT_IN : PT_HINT_TALK};
                } else if (count == 0) {
                    hints[count++] = (hint_item_t){KEY_UP, PT_HINT_MENU};
                }
            } else {
                hints[count++] = can_talk ? (hint_item_t){KEY_OK, PT_HINT_TALK}
                                          : (hint_item_t){KEY_UP, PT_HINT_MENU};
                if (earlier) {
                    hints[count++] = (hint_item_t){KEY_UP, PT_HINT_EARLIER};
                }
            }
            break;
        }
        case BUDDY_PAGE_MENU:
            render_menu(snap);
            hints[count++] = (hint_item_t){KEY_UP, PT_HINT_PREV};
            hints[count++] = (hint_item_t){KEY_DOWN, PT_HINT_NEXT};
            hints[count++] = (hint_item_t){KEY_OK, PT_HINT_ENTER};
            break;
        case BUDDY_PAGE_MORE:
            render_rows(s.more_rows, s.more_labels, NULL, BUDDY_MORE_COUNT,
                        (int)snap->more_selection, BUDDY_MORE_UNPAIR, BUDDY_MORE_FACTORY_RESET);
            hints[count++] = (hint_item_t){KEY_UP, PT_HINT_PREV};
            hints[count++] = (hint_item_t){KEY_DOWN, PT_HINT_NEXT};
            hints[count++] = (hint_item_t){KEY_OK, PT_HINT_ENTER};
            break;
        case BUDDY_PAGE_NOTICES:
            render_notices(snap);
            hints[count++] = (hint_item_t){KEY_OK, PT_HINT_BACK};
            break;
        case BUDDY_PAGE_HELPERS:
            render_helpers(snap);
            hints[count++] = (hint_item_t){KEY_OK, PT_HINT_BACK};
            break;
        case BUDDY_PAGE_GUIDE:
            render_guide(snap);
            hints[count++] = (hint_item_t){KEY_DOWN, PT_HINT_SCROLL};
            hints[count++] = (hint_item_t){KEY_OK, PT_HINT_BACK};
            break;
        case BUDDY_PAGE_COUNT:
            break;
        }
        break;
    }
    set_visible(s.notice, notice);
    if (notice) {
        set_block_text(s.notice, snap->message);
        set_hints(hint_none, 0);
    } else {
        set_hints(hints, count);
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
    } else if (s.view == POCKET_VIEW_PAGE && s.page == BUDDY_PAGE_HOME && s.tl_valid) {
        int anchor = pocket_timeline_anchor(s.tl_content, s.tl_last_top, TL_H);

        // 确认键：回到最新一轮。上下键：翻八行；翻回最新一轮该在的位置就继续跟着它。
        s.tl_y = delta == BUDDY_SCROLL_LATEST
                     ? anchor
                     : pocket_timeline_step(s.tl_y, delta, s.tl_content, TL_H, TL_STEP);
        s.tl_follow = s.tl_y == anchor;
        lv_obj_scroll_to_y(s.tl_scroll, s.tl_y, LV_ANIM_OFF);
    }
}
