// main/pocket_ui.c —— 小幽界面实现（240×320 竖屏，三键操作）。
//
// 三屏，确认键短按轮着切：
//   第一屏 形象：大的小幽和她此刻在干什么。
//   第二屏 对话：一屏一件事——你说的那句和小幽最新的话；上下键在这件事里翻，翻到头
//               再按就换到上一件 / 下一件。
//   第三屏 任务：正在做的事，和选中那件的最近两步。
//   第四屏 用量：每个订阅账号还剩多少、每个帮手用的是哪个模型。中枢发过用量才有这一屏。
// 低频的东西（通知、帮手、设置）都收在双击确认键的菜单里。
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

#ifdef ESP_PLATFORM
#include "esp_system.h"
#endif

#include "buddy_cards.h"
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

// 小幽出现的地方：第一屏（大的）、第二屏顶上那一条（小的），和五个占满屏幕的场景。
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

// 顶栏中间的小点：现在在第几屏。三屏三个；有用量那一屏时四个。
#define DOT_SIZE        5
#define DOT_GAP         5
#define DOT_MAX         4
#define DOT_X(count)    (SCREEN_W / 2 - ((count) * DOT_SIZE + ((count) - 1) * DOT_GAP) / 2)
#define DOT_Y           15

// 第一屏。小幽下面是标题，再下面最多三行。她把活交给帮手时，第一行换成
// “小幽 ···✉··· [帮手]”，下面是这件事的标题和计时。
#define HOME_TITLE_Y    132
#define HOME_SUB_Y      164
#define HOME_ROW_X      40
#define LINK_W          34
#define TAG_PAD         8
#define TAG_H           22

// 第二屏。顶上一条：左边是缩小的小幽，右边第一行是“小幽 ···✉··· [帮手]”，第二行是
// 这件事现在怎么样和计时。一条细线下面一行小字：第几件、几点开始的、是谁做的；
// 再下面是这件事的话，一屏八行。
#define HEAD_PET_X      14
#define HEAD_PET_Y      2
#define HEAD_TEXT_X     62
#define HEAD_TEXT_W     (SCREEN_W - SIDE - HEAD_TEXT_X)
#define HEAD_ROW2_Y     23
#define HEAD_LINK_X     (HEAD_TEXT_X + 34)
#define HEAD_TAG_X      (HEAD_LINK_X + LINK_W + 6)
// 名牌里名字最宽能占多少：再长就打省略号。
#define HEAD_TAG_TEXT_W (SCREEN_W - SIDE - HEAD_TAG_X - 2 * TAG_PAD - 4)
#define HEAD_ELAPSED_W  48
#define HEAD_RULE_Y     46
#define META_Y          (HEAD_RULE_Y + 3)

// 一件事的话比窗口长时在里面翻：窗口八行高，翻一次走七行，留一行让眼睛接得上。
// 两段字：你说的（暗一些，左边一条竖线）和她说的。所有高度都是整行，所以窗口的
// 上下沿不会切到半行字。
#define TL_Y            (META_Y + 19)
#define TL_LINES        8
#define TL_H            (TL_LINES * LINE_16)
#define TL_STEP         ((TL_LINES - 1) * LINE_16)
#define TL_TEXT_W       (INNER_W - 8)

// 第三屏。上面四行，每行一件事：名牌、标题、做了多久。细线下面是选中那件：
// 完整一点的标题一行，最近两步——前一步一行，最新一步两行。
#define TASK_ROW_H      28
#define TASK_ROW_GAP    3
#define TASK_ROW_Y      2
#define TASK_TAG_TEXT_W 52
// 单子是一个五行高的窗口，里面的行是固定的几个控件轮着用（开机时就建好，
// 不随单子变长再要内存）：一件事一行，做完的事每换一天多一行小标题。放不下
// 整行的不画；单子有多长、现在在第几件，写在下面标题那一行的右边。
#define TASK_STEP       (TASK_ROW_H + TASK_ROW_GAP)
#define TASK_DAY_STEP   (LINE_16 + 1)
#define TASK_LIST_ROWS  5
#define TASK_LIST_H     (TASK_LIST_ROWS * TASK_STEP)
// 窗口里最多同时有几行、几个小标题。
#define TASK_POOL       TASK_LIST_ROWS
#define TASK_DAY_POOL   3
#define TASK_COUNT_W    44
#define TASK_RULE_Y     (TASK_ROW_Y + TASK_LIST_H + 1)
#define TASK_LINE_Y     (TASK_RULE_Y + 5)
#define TASK_HINT_Y     (TASK_LINE_Y + 3 * LINE_16 + 1)

// 第四屏。一个账号一块：每个帮手一行（名牌，后面是模型名），下面两行是两个窗口各还剩
// 多少（一条格子、一个数），再一行小字是什么时候重置。没有订阅的帮手只有名牌那一行。
#define USAGE_WHO_H     24
#define USAGE_ROW_H     18
#define USAGE_GAP       8
#define USAGE_LABEL_W   46
#define USAGE_CELLS     12
#define USAGE_CELL_W    6
#define USAGE_CELL_GAP  2
#define USAGE_BAR_W     (USAGE_CELLS * (USAGE_CELL_W + USAGE_CELL_GAP) - USAGE_CELL_GAP)
#define USAGE_BAR_H     8
#define USAGE_LEFT_W    64
// 名牌里名字最宽能占多少（档位那个字另算）。
#define USAGE_TAG_TEXT_W 84

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

// 名牌：一个带描边的小圆角框，里面是帮手的名字，后面可以跟着档位（“codex 高”）。
typedef struct {
    lv_obj_t *box;
    lv_obj_t *label;
    char name[BUDDY_AGENT_MAX + 12];  // 现在写在上面的字
    char who[BUDDY_AGENT_MAX];        // 这是照着哪个名字、哪一档、多宽摆的
    uint8_t effort;
    int16_t fit;
} tag_t;

// 第四屏的一块，用到的时候才建。
typedef struct {
    lv_obj_t *box;
    tag_t tags[BUDDY_USAGE_WHO];
    lv_obj_t *models[BUDDY_USAGE_WHO];
    uintptr_t model_shown[BUDDY_USAGE_WHO];  // 上次量的是哪段字、名牌多宽
    bool model_below[BUDDY_USAGE_WHO];       // 名牌旁边放不下，写在下一行
    lv_obj_t *names[2];   // “5 小时”“本周”
    lv_obj_t *bars[2];
    lv_obj_t *lefts[2];
    lv_obj_t *reset;
} usage_block_t;

static struct {
    // 顶栏
    lv_obj_t *link_dot;
    lv_obj_t *clock;
    lv_obj_t *page_dots[DOT_MAX];
    int dot_count;
    lv_obj_t *doing;
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
    // 第一屏：形象
    lv_obj_t *home_title;
    lv_obj_t *home_row;
    lv_obj_t *home_link;
    tag_t home_tag;
    lv_obj_t *home_sub;
    lv_obj_t *home_timer;
    // 第二屏：对话。顶上一条
    lv_obj_t *talk;
    lv_obj_t *talk_empty;
    lv_obj_t *head_link;
    tag_t head_tag;
    lv_obj_t *head_status;
    lv_obj_t *head_elapsed;
    uint32_t link_color;
    // 第二屏：这件事的那行小字和它的话
    lv_obj_t *meta_left;
    lv_obj_t *meta_right;
    lv_obj_t *tl_scroll;
    lv_obj_t *tl_said;
    lv_obj_t *tl_reply;
    uint32_t tl_serial;    // 上次是照着哪一次“从头开始”摆的
    uintptr_t tl_shown;    // 上次摆的是什么（内容的哈希）
    int tl_content;        // 这件事的话总共多高
    int tl_y;              // 窗口现在在哪儿
    // 第三屏：任务
    lv_obj_t *task_list;
    lv_obj_t *task_rows[TASK_POOL];
    tag_t task_tags[TASK_POOL];
    lv_obj_t *task_titles[TASK_POOL];
    lv_obj_t *task_rights[TASK_POOL];
    lv_obj_t *task_days[TASK_DAY_POOL];
    lv_obj_t *task_place;  // 第几件 / 一共几件
    unsigned task_top;     // 窗口从单子的第几行画起
    lv_obj_t *task_detail;
    lv_obj_t *task_title;
    lv_obj_t *task_lines[2];
    lv_obj_t *task_result;
    lv_obj_t *tasks_empty;
    // 第四屏：用量
    lv_obj_t *usage_scroll;
    lv_obj_t *usage_empty;
    lv_obj_t *usage_age;
    usage_block_t usage_blocks[BUDDY_USAGE_COUNT];
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

// 一段对面发来的字（16 号字）写成一行有多宽。
static int text_width(const char *text)
{
    lv_point_t size;

    lv_text_get_size(&size, text, &pocket_font_16, 0, LINE_SPACE, LV_COORD_MAX,
                     LV_TEXT_FLAG_NONE);
    return (int)size.x;
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
    lv_obj_invalidate(s.home_link);
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

// 第四屏的一条额度：USAGE_CELLS 个格子，还剩多少就亮多少。剩得不多了是黄的，
// 一点不剩时全灭。还剩百分之几存在 user_data 里（加一，零表示还没给过）。
static void usage_bar_draw_cb(lv_event_t *event)
{
    lv_obj_t *obj = lv_event_get_current_target(event);
    lv_layer_t *layer = lv_event_get_layer(event);
    lv_draw_rect_dsc_t dsc;
    lv_area_t coords;
    int left = (int)(intptr_t)lv_obj_get_user_data(obj) - 1;
    int lit = pocket_usage_cells(left, USAGE_CELLS);
    int index;

    lv_obj_get_coords(obj, &coords);
    lv_draw_rect_dsc_init(&dsc);
    dsc.bg_opa = LV_OPA_COVER;
    for (index = 0; index < USAGE_CELLS; ++index) {
        draw_block(layer, &dsc, coords.x1 + index * (USAGE_CELL_W + USAGE_CELL_GAP), coords.y1,
                   USAGE_CELL_W, USAGE_BAR_H,
                   index < lit ? (left < 20 ? C_WARN : C_OK) : C_LINE);
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

    {
        int index;

        for (index = 0; index < DOT_MAX; ++index) {
            s.page_dots[index] = make_box(root, DOT_X(3) + index * (DOT_SIZE + DOT_GAP), DOT_Y,
                                          DOT_SIZE, DOT_SIZE);
            fill(s.page_dots[index], C_LINE, LV_RADIUS_CIRCLE);
            lv_obj_add_flag(s.page_dots[index], LV_OBJ_FLAG_HIDDEN);
        }
        s.dot_count = 3;
    }
    // 后台在做几件事：没有就不显示。
    s.doing = make_label(root, &pocket_font_14, C_XIAOYOU, "");
    lv_obj_set_pos(s.doing, DOT_X(3) + 3 * (DOT_SIZE + DOT_GAP) + 4, 8);
    lv_obj_add_flag(s.doing, LV_OBJ_FLAG_HIDDEN);

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

// 名牌：先建一个空的，名字和颜色在刷新时给。
static void make_tag(lv_obj_t *parent, tag_t *tag, int x, int y)
{
    tag->box = make_box(parent, x, y, LV_SIZE_CONTENT, TAG_H);
    outline(tag->box, C_XIAOYOU, TAG_H / 2, 2);
    lv_obj_set_style_pad_hor(tag->box, TAG_PAD, 0);
    tag->label = make_label(tag->box, &pocket_font_16, C_XIAOYOU, "");
    lv_obj_align(tag->label, LV_ALIGN_LEFT_MID, 0, 0);
    tag->name[0] = '\0';
    tag->who[0] = '\0';
    tag->effort = (uint8_t)BUDDY_EFFORT_NONE;
    tag->fit = 0;
}

static void build_home(lv_obj_t *page)
{
    lv_obj_t *name;

    make_pet(page, PET_SOLO, (SCREEN_W - POCKET_PET_GRID * PET_SOLO_SCALE) / 2, 6,
             PET_SOLO_SCALE);
    s.home_title = make_label(page, &pocket_font_22, C_TEXT, PT_HOME_WAITING);
    lv_obj_set_width(s.home_title, INNER_W);
    lv_obj_set_style_text_align(s.home_title, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_pos(s.home_title, SIDE, HOME_TITLE_Y);
    // 她把活交给了帮手：小幽 ···✉··· [帮手]
    s.home_row = make_box(page, HOME_ROW_X, HOME_SUB_Y - 2, SCREEN_W - HOME_ROW_X - SIDE, TAG_H);
    name = make_label(s.home_row, &pocket_font_14, C_XIAOYOU, PT_XIAOYOU);
    lv_obj_set_pos(name, 0, 3);
    s.home_link = make_box(s.home_row, 34, 5, LINK_W, 13);
    lv_obj_add_event_cb(s.home_link, link_draw_cb, LV_EVENT_DRAW_MAIN, NULL);
    make_tag(s.home_row, &s.home_tag, 34 + LINK_W + 6, 0);
    lv_obj_add_flag(s.home_row, LV_OBJ_FLAG_HIDDEN);
    s.home_sub = make_text_block(page, &pocket_font_16, C_DIM, SIDE, HOME_SUB_Y, INNER_W,
                                 LINES_16(3));
    lv_obj_set_style_text_align(s.home_sub, LV_TEXT_ALIGN_CENTER, 0);
    s.home_timer = make_label(page, &pocket_font_16, C_XIAOYOU, "");
    lv_obj_set_width(s.home_timer, INNER_W);
    lv_obj_set_style_text_align(s.home_timer, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_pos(s.home_timer, SIDE, HOME_SUB_Y);
    lv_obj_add_flag(s.home_timer, LV_OBJ_FLAG_HIDDEN);
    s.link_color = C_XIAOYOU;
}

static lv_obj_t *make_talk_text(lv_obj_t *parent, uint32_t color, bool quote)
{
    lv_obj_t *label = make_label(parent, &pocket_font_16, color, "");

    lv_label_set_long_mode(label, LV_LABEL_LONG_MODE_WRAP);
    lv_obj_set_width(label, TL_TEXT_W);
    if (quote) {
        // 你说的话：左边一条小幽颜色的竖线，像引用。
        lv_obj_set_style_border_side(label, LV_BORDER_SIDE_LEFT, 0);
        lv_obj_set_style_border_width(label, 3, 0);
        lv_obj_set_style_border_color(label, lv_color_hex(C_XIAOYOU), 0);
        lv_obj_set_style_border_opa(label, LV_OPA_COVER, 0);
        lv_obj_set_style_pad_left(label, 6, 0);
    }
    lv_obj_add_flag(label, LV_OBJ_FLAG_HIDDEN);
    return label;
}

static void build_talk(lv_obj_t *page)
{
    lv_obj_t *name;
    lv_obj_t *label;

    // 还没有任何一件事。
    s.talk_empty = make_box(page, 0, 0, SCREEN_W, AREA_H);
    label = make_label(s.talk_empty, &pocket_font_22, C_DIM, PT_TALK_EMPTY);
    lv_obj_set_width(label, INNER_W);
    lv_obj_set_style_text_align(label, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_pos(label, SIDE, 84);
    label = make_label(s.talk_empty, &pocket_font_16, C_DIM, PT_TALK_EMPTY_SUB);
    lv_obj_set_width(label, INNER_W);
    lv_obj_set_style_text_align(label, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_pos(label, SIDE, 122);

    // 顶上一条：缩小的小幽，谁在做（小幽 ···✉··· [帮手]），这件事现在怎么样。
    s.talk = make_box(page, 0, 0, SCREEN_W, AREA_H);
    make_pet(s.talk, PET_TALK, HEAD_PET_X, HEAD_PET_Y, PET_TALK_SCALE);
    name = make_label(s.talk, &pocket_font_14, C_XIAOYOU, PT_XIAOYOU);
    lv_obj_set_pos(name, HEAD_TEXT_X, 3);
    s.head_link = make_box(s.talk, HEAD_LINK_X, 5, LINK_W, 13);
    lv_obj_add_event_cb(s.head_link, link_draw_cb, LV_EVENT_DRAW_MAIN, NULL);
    make_tag(s.talk, &s.head_tag, HEAD_TAG_X, 0);
    s.head_status = make_text_block(s.talk, &pocket_font_16, C_DIM, HEAD_TEXT_X, HEAD_ROW2_Y,
                                    HEAD_TEXT_W, LINES_16(1));
    s.head_elapsed = make_label(s.talk, &pocket_font_16, C_XIAOYOU, "");
    lv_obj_set_width(s.head_elapsed, HEAD_ELAPSED_W);
    lv_obj_set_style_text_align(s.head_elapsed, LV_TEXT_ALIGN_RIGHT, 0);
    lv_obj_set_pos(s.head_elapsed, SCREEN_W - SIDE - HEAD_ELAPSED_W, HEAD_ROW2_Y);
    fill(make_box(s.talk, SIDE, HEAD_RULE_Y, INNER_W, 1), C_LINE, 0);

    // 这件事的一行小字：左边第几件和几点，右边是谁做的、现在怎么样。
    s.meta_left = make_label(s.talk, &pocket_font_14, C_DIM, "");
    lv_obj_set_pos(s.meta_left, SIDE, META_Y);
    s.meta_right = make_label(s.talk, &pocket_font_16, C_DIM, "");
    lv_obj_set_width(s.meta_right, 130);
    lv_obj_set_style_text_align(s.meta_right, LV_TEXT_ALIGN_RIGHT, 0);
    lv_label_set_long_mode(s.meta_right, LV_LABEL_LONG_MODE_CLIP);
    lv_obj_set_height(s.meta_right, LINES_16(1));
    lv_obj_set_pos(s.meta_right, SCREEN_W - SIDE - 130, META_Y - 3);

    // 这件事的话：窗口八行高，里面两段字。
    s.tl_scroll = make_box(s.talk, SIDE, TL_Y, INNER_W, TL_H);
    lv_obj_add_flag(s.tl_scroll, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_scroll_dir(s.tl_scroll, LV_DIR_VER);
    // 每段字后面都留一个行间距，最后一段也一样：总高才是整行的倍数。
    lv_obj_set_style_pad_bottom(s.tl_scroll, LINE_SPACE, 0);
    style_scrollbar(s.tl_scroll);
    s.tl_said = make_talk_text(s.tl_scroll, C_DIM, true);
    s.tl_reply = make_talk_text(s.tl_scroll, C_TEXT, false);
    lv_obj_add_flag(s.talk, LV_OBJ_FLAG_HIDDEN);
}

static void build_tasks(lv_obj_t *page)
{
    int index;

    // 行都是这个窗口的孩子：露在窗口外面的那一截不画。
    s.task_list = make_box(page, SIDE, TASK_ROW_Y, INNER_W, TASK_LIST_H);
    for (index = 0; index < TASK_POOL; ++index) {
        lv_obj_t *row = make_box(s.task_list, 0, index * TASK_STEP, INNER_W, TASK_ROW_H);

        fill(row, C_CARD, 9);
        lv_obj_set_style_border_color(row, lv_color_hex(C_XIAOYOU), 0);
        lv_obj_set_style_border_width(row, 2, 0);
        lv_obj_set_style_border_opa(row, LV_OPA_TRANSP, 0);
        s.task_rows[index] = row;
        make_tag(row, &s.task_tags[index], 2, 1);
        s.task_titles[index] = make_text_block(row, &pocket_font_16, C_TEXT, 0, 2, 10,
                                               LINES_16(1));
        // 右边那一项有多宽占多宽，剩下的都给标题。
        s.task_rights[index] = make_label(row, &pocket_font_16, C_DIM, "");
        lv_obj_align(s.task_rights[index], LV_ALIGN_RIGHT_MID, -6, 0);
        lv_obj_add_flag(row, LV_OBJ_FLAG_HIDDEN);
    }
    // 哪天做完的：对面发来的字（今天、昨天、10-08），用全字库的 16 号字。
    for (index = 0; index < TASK_DAY_POOL; ++index) {
        s.task_days[index] = make_label(s.task_list, &pocket_font_16, C_DIM, "");
        lv_obj_set_x(s.task_days[index], 4);
        lv_obj_add_flag(s.task_days[index], LV_OBJ_FLAG_HIDDEN);
    }
    // 选中那件的最近两步：工具名和命令原样，不由小幽转述。
    s.task_detail = make_box(page, 0, 0, SCREEN_W, AREA_H);
    fill(make_box(s.task_detail, SIDE, TASK_RULE_Y, INNER_W, 1), C_LINE, 0);
    s.task_title = make_text_block(s.task_detail, &pocket_font_16, C_XIAOYOU, SIDE + 2,
                                   TASK_LINE_Y, INNER_W - 4 - TASK_COUNT_W, LINES_16(1));
    s.task_place = make_label(s.task_detail, &pocket_font_14, C_DIM, "");
    lv_obj_set_width(s.task_place, TASK_COUNT_W);
    lv_obj_set_style_text_align(s.task_place, LV_TEXT_ALIGN_RIGHT, 0);
    lv_obj_set_pos(s.task_place, SIDE + INNER_W - 2 - TASK_COUNT_W, TASK_LINE_Y + 3);
    s.task_lines[0] = make_text_block(s.task_detail, &pocket_font_16, C_DIM, SIDE + 2,
                                      TASK_LINE_Y + LINE_16, INNER_W - 4, LINES_16(1));
    s.task_lines[1] = make_text_block(s.task_detail, &pocket_font_16, C_TEXT, SIDE + 2,
                                      TASK_LINE_Y + 2 * LINE_16, INNER_W - 4, LINES_16(1));
    // 选中的是做完的事：这两行是它的结论，或者它是哪天、怎么结束的。
    s.task_result = make_text_block(s.task_detail, &pocket_font_16, C_TEXT, SIDE + 2,
                                    TASK_LINE_Y + LINE_16, INNER_W - 4, LINES_16(2));
    {
        lv_obj_t *hint = make_label(s.task_detail, &pocket_font_14, C_DIM, PT_TASKS_HINT);

        lv_obj_set_width(hint, INNER_W);
        lv_obj_set_style_text_align(hint, LV_TEXT_ALIGN_CENTER, 0);
        lv_obj_set_pos(hint, SIDE, TASK_HINT_Y);
    }
    lv_obj_add_flag(s.task_detail, LV_OBJ_FLAG_HIDDEN);
    s.tasks_empty = make_label(page, &pocket_font_16, C_DIM, PT_TASKS_EMPTY);
    lv_obj_set_width(s.tasks_empty, INNER_W);
    lv_obj_set_style_text_align(s.tasks_empty, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_pos(s.tasks_empty, SIDE, 104);
}

// 第四屏：一开始只有空着时的两行字和最下面那行“多久之前更新的”；每一块用到时才建
// （usage_block），这样没有用量可看的时候不占内存。
static void build_usage(lv_obj_t *page)
{
    lv_obj_t *label;

    s.usage_scroll = make_box(page, 0, 0, SCREEN_W, AREA_H);
    lv_obj_add_flag(s.usage_scroll, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_scroll_dir(s.usage_scroll, LV_DIR_VER);
    style_scrollbar(s.usage_scroll);
    s.usage_age = make_label(s.usage_scroll, &pocket_font_14, C_DIM, "");
    lv_obj_set_width(s.usage_age, INNER_W);
    lv_obj_set_style_text_align(s.usage_age, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_x(s.usage_age, SIDE);

    s.usage_empty = make_box(page, 0, 0, SCREEN_W, AREA_H);
    label = make_label(s.usage_empty, &pocket_font_22, C_DIM, PT_USAGE_EMPTY);
    lv_obj_set_width(label, INNER_W);
    lv_obj_set_style_text_align(label, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_pos(label, SIDE, 84);
    label = make_label(s.usage_empty, &pocket_font_16, C_DIM, PT_USAGE_EMPTY_SUB);
    lv_obj_set_width(label, INNER_W);
    lv_obj_set_style_text_align(label, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_pos(label, SIDE, 122);
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
        [BUDDY_PAGE_HOME] = build_home,       [BUDDY_PAGE_TALK] = build_talk,
        [BUDDY_PAGE_TASKS] = build_tasks,     [BUDDY_PAGE_USAGE] = build_usage,
        [BUDDY_PAGE_MENU] = build_menu,
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
        s.pages[index] = make_box(root, 0, AREA_Y, SCREEN_W, AREA_H);
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

// screen：现在在第几屏（0 起）；不在这几屏上是 -1。
static void render_top_bar(const buddy_ui_snapshot_t *snap, int screen)
{
    char text[24];
    uint32_t color = C_DIM;
    int count = pocket_screen_count(snap);
    int index;

    if (count > DOT_MAX) {
        count = DOT_MAX;
    }
    if (s.dot_count != count) {
        // 多了（或者少了）用量那一屏：小点重新排，仍然居中。
        s.dot_count = count;
        for (index = 0; index < DOT_MAX; ++index) {
            lv_obj_set_x(s.page_dots[index], DOT_X(count) + index * (DOT_SIZE + DOT_GAP));
        }
        lv_obj_set_x(s.doing, DOT_X(count) + count * (DOT_SIZE + DOT_GAP) + 4);
    }

    // 圆点：灰是没连上，黄是正在配对，绿是连好了。
    if (snap->ble_enabled && snap->ble_connected) {
        color = snap->ble_encrypted ? C_OK : C_WARN;
    }
    set_bg_color(s.link_dot, color);

    (void)pocket_format_clock(snap->epoch_seconds, snap->timezone_offset_seconds,
                              snap->time_received_ms, snap->uptime_ms, text, sizeof(text));
    set_text(s.clock, text);

    // 小点：只在这几屏上显示，亮着的那个是现在这一屏。
    for (index = 0; index < DOT_MAX; ++index) {
        set_visible(s.page_dots[index], screen >= 0 && index < count);
        set_bg_color(s.page_dots[index], index == screen ? C_XIAOYOU : C_LINE);
    }
    set_visible(s.doing, screen >= 0 && snap->doing > 0U);
    if (snap->doing > 0U) {
        (void)snprintf(text, sizeof(text), PT_TOP_DOING, snap->doing > 99U ? 99U : snap->doing);
        set_text(s.doing, text);
    }

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

static uintptr_t text_hash(uintptr_t hash, const char *text)
{
    const unsigned char *cursor;

    for (cursor = (const unsigned char *)text; *cursor != '\0'; ++cursor) {
        hash = (hash ^ *cursor) * 16777619u;
    }
    return (hash ^ 0xFFu) * 16777619u;  // 两段字之间的分隔
}

// 给名牌换名字和颜色。名牌跟着名字伸缩；名字比 max_width 宽就定宽，打省略号。
static void set_tag(tag_t *tag, const char *name, uint32_t color, int max_width)
{
    if (strcmp(tag->name, name) != 0 || lv_label_get_text(tag->label)[0] == '\0') {
        (void)snprintf(tag->name, sizeof(tag->name), "%s", name);
        lv_label_set_long_mode(tag->label, LV_LABEL_LONG_MODE_WRAP);
        lv_obj_set_size(tag->label, LV_SIZE_CONTENT, LV_SIZE_CONTENT);
        lv_label_set_text(tag->label, name);
        lv_obj_update_layout(tag->label);
        if (lv_obj_get_width(tag->label) > max_width) {
            lv_label_set_long_mode(tag->label, LV_LABEL_LONG_MODE_DOTS);
            lv_obj_set_size(tag->label, max_width, LINES_16(1));
            lv_label_set_text(tag->label, name);
        }
        lv_obj_update_layout(tag->box);
    }
    set_text_color(tag->label, color);
    set_border_color(tag->box, color);
}

// 档位写在名牌上的那个字；没说档位时是 NULL。
static const char *effort_text(uint8_t effort)
{
    switch ((buddy_effort_t)effort) {
    case BUDDY_EFFORT_MINIMAL:
        return PT_EFFORT_MINIMAL;
    case BUDDY_EFFORT_LOW:
        return PT_EFFORT_LOW;
    case BUDDY_EFFORT_MEDIUM:
        return PT_EFFORT_MEDIUM;
    case BUDDY_EFFORT_HIGH:
        return PT_EFFORT_HIGH;
    case BUDDY_EFFORT_XHIGH:
        return PT_EFFORT_XHIGH;
    case BUDDY_EFFORT_MAX:
        return PT_EFFORT_MAX;
    case BUDDY_EFFORT_NONE:
        break;
    }
    return NULL;
}

// 名牌上写名字，后面跟着这件事用的档位：“codex 高”。放不下时截的是名字，档位留着。
// 没说档位就只有名字。字和描边的颜色分开给：实心的名牌字是深色的。
static void set_tag_tier_colors(tag_t *tag, const char *name, uint8_t effort,
                                uint32_t text_color, uint32_t border_color, int max_width)
{
    static char text[sizeof(tag->name)];
    const char *level = effort_text(effort);
    size_t length;

    if (level == NULL) {
        if (tag->who[0] != '\0' || tag->effort != (uint8_t)BUDDY_EFFORT_NONE ||
            strcmp(tag->name, name) != 0) {
            tag->effort = (uint8_t)BUDDY_EFFORT_NONE;
            tag->who[0] = '\0';
            tag->name[0] = '\0';
            set_tag(tag, name, border_color, max_width);
        }
    } else if (tag->effort != effort || tag->fit != max_width || strcmp(tag->who, name) != 0) {
        // 量字宽不便宜，所以只在名字、档位或者能占的宽度变了的时候重排。
        length = strlen(name);
        (void)snprintf(text, sizeof(text), "%s %s", name, level);
        while (length > 0U && text_width(text) > max_width) {
            do {
                --length;
            } while (length > 0U && ((unsigned char)name[length] & 0xC0U) == 0x80U);
            (void)snprintf(text, sizeof(text), "%.*s%s %s", (int)length, name, PT_ELLIPSIS,
                           level);
        }
        (void)snprintf(tag->who, sizeof(tag->who), "%s", name);
        tag->effort = effort;
        tag->fit = (int16_t)max_width;
        tag->name[0] = '\0';  // 让 set_tag 重新摆一次
        set_tag(tag, text, border_color, max_width);
    }
    set_text_color(tag->label, text_color);
    set_border_color(tag->box, border_color);
}

static void set_tag_tier(tag_t *tag, const char *name, uint8_t effort, uint32_t color,
                         int max_width)
{
    set_tag_tier_colors(tag, name, effort, color, color, max_width);
}

static void set_link_color(lv_obj_t *link, uint32_t color)
{
    if (s.link_color != color) {
        s.link_color = color;
        lv_obj_invalidate(link);
    }
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

// ---- 第一屏：形象 ----

static void render_home(const buddy_ui_snapshot_t *snap)
{
    static char line[BUDDY_MESSAGE_MAX + BUDDY_NAME_MAX + 8];
    pocket_home_t home = pocket_home_for(snap);
    bool hub = snap->chat.phase != BUDDY_CHAT_NONE;
    bool notice = pocket_notice_visible(snap);
    bool helper = home == POCKET_HOME_HELPER;
    bool timer = false;
    const char *title = PT_HOME_QUIET;
    const char *sub = "";
    uint32_t title_color = C_TEXT;
    uint32_t sub_color = C_DIM;
    uint32_t color = C_XIAOYOU;
    int sub_lines = 3;
    int sub_y = HOME_SUB_Y;
    char elapsed[12];

    switch (home) {
    case POCKET_HOME_BLE_OFF:
        title = PT_HOME_BLE_OFF;
        title_color = C_DIM;
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
    case POCKET_HOME_QUIET:
        // 连着手机中枢才能按住说话；别的连接只能等对面有事。
        sub = snap->host_hub ? PT_HOME_SUB_QUIET : PT_HOME_SUB_NO_VOICE;
        if (snap->doing > 0U) {
            (void)snprintf(line, sizeof(line), PT_HOME_SUB_DOING, snap->doing);
            sub = line;
        } else if (snap->host_chat && snap->waiting > 0U) {
            // 别的 App 有通知没看：不打断，只在这儿提一句。
            (void)snprintf(line, sizeof(line), PT_HOME_SUB_NOTICES, (unsigned)snap->waiting);
            sub = line;
        }
        break;
    case POCKET_HOME_SENT:
        title = PT_HEAD_SENT;
        timer = true;
        sub_lines = 0;
        break;
    case POCKET_HOME_THINKING:
        title = hub ? PT_HEAD_THINKING : PT_DESKTOP_BUSY;
        // 你说的那句，一行，放不下打省略号。
        sub = hub ? snap->chat.said : (snap->message[0] != '\0' ? snap->message
                                                                 : PT_DESKTOP_WORKING);
        timer = hub;
        sub_lines = hub ? 1 : 3;
        break;
    case POCKET_HOME_HELPER:
        title = PT_HOME_HANDED;
        color = pocket_helper_color(snap->chat.agent);
        // 这件事的标题。
        sub = snap->chat.stage;
        sub_color = C_TEXT;
        sub_lines = 1;
        sub_y = HOME_SUB_Y + TAG_H + 4;
        timer = true;
        break;
    case POCKET_HOME_ANSWERED:
        title = hub ? PT_HEAD_DONE : PT_DESKTOP_REPLY;
        // 她的简报（桌面端是回复的开头），最多三行。
        sub = snap->reply;
        sub_color = C_TEXT;
        break;
    case POCKET_HOME_FAILED:
        title = PT_HEAD_FAILED;
        title_color = C_DANGER;
        sub = snap->reply[0] != '\0' ? snap->reply : PT_TALK_FAILED;
        break;
    }
    // 设备或对面有一句话要说时，下面的位置让给它。
    if (notice && !pocket_home_is_talk(home)) {
        sub = snap->message;
        sub_color = C_WARN;
        sub_lines = 3;
    }

    pet_set(PET_SOLO, pocket_pet_for(snap));
    set_text(s.home_title, title);
    set_text_color(s.home_title, title_color);
    set_visible(s.home_row, helper);
    if (helper) {
        set_link_color(s.home_link, color);
        set_tag_tier(&s.home_tag, snap->chat.agent, snap->chat.effort, color,
                     SCREEN_W - SIDE - HOME_ROW_X - (34 + LINK_W + 6) - 2 * TAG_PAD - 4);
    }
    set_visible(s.home_sub, sub_lines > 0);
    if (sub_lines > 0) {
        if (lv_obj_get_y(s.home_sub) != sub_y ||
            lv_obj_get_height(s.home_sub) != LINES_16(sub_lines)) {
            lv_obj_set_pos(s.home_sub, SIDE, sub_y);
            lv_obj_set_height(s.home_sub, LINES_16(sub_lines));
            lv_obj_set_user_data(s.home_sub, NULL);
        }
        set_block_text(s.home_sub, sub);
        set_text_color(s.home_sub, sub_color);
    }
    set_visible(s.home_timer, timer);
    if (timer) {
        int timer_y = sub_lines > 0 ? sub_y + sub_lines * LINE_16 : HOME_SUB_Y;

        if (lv_obj_get_y(s.home_timer) != timer_y) {
            lv_obj_set_y(s.home_timer, timer_y);
        }
        (void)pocket_format_elapsed(snap->chat_since_ms, snap->uptime_ms, elapsed,
                                    sizeof(elapsed));
        set_text(s.home_timer, elapsed);
        set_text_color(s.home_timer, color);
    }
}

// ---- 第二屏：对话，一屏一件事 ----

// 第三屏那张单子上的一件事：还在做的（task，带最近两步）或者做完的（past）。
typedef struct {
    const char *id;
    const char *agent;
    const char *title;
    const buddy_task_t *task;  // 还在做的那一类才有
    const buddy_past_t *past;  // 做完的那一类才有
    buddy_task_state_t state;
    uint8_t effort;
} thing_t;

static unsigned past_count_of(const buddy_ui_snapshot_t *snap)
{
    if (snap->past == NULL) {
        return 0;
    }
    return snap->past_count < BUDDY_PAST_COUNT ? snap->past_count : BUDDY_PAST_COUNT;
}

static unsigned thing_count(const buddy_ui_snapshot_t *snap)
{
    return (snap->task_count < BUDDY_TASK_COUNT ? snap->task_count : BUDDY_TASK_COUNT) +
           past_count_of(snap);
}

static bool thing_at(const buddy_ui_snapshot_t *snap, unsigned index, thing_t *thing)
{
    unsigned tasks = snap->task_count < BUDDY_TASK_COUNT ? snap->task_count : BUDDY_TASK_COUNT;

    memset(thing, 0, sizeof(*thing));
    if (index < tasks) {
        const buddy_task_t *task = &snap->tasks[index];

        thing->id = task->id;
        thing->agent = task->agent;
        thing->title = task->title;
        thing->task = task;
        thing->state = task->state;
        thing->effort = task->effort;
        return true;
    }
    index -= tasks;
    if (index < past_count_of(snap)) {
        const buddy_past_t *past = &snap->past[index];

        thing->id = past->id;
        thing->agent = past->agent;
        thing->title = past->title;
        thing->past = past;
        thing->state = (buddy_task_state_t)past->state;
        thing->effort = past->effort;
        return true;
    }
    thing->id = thing->agent = thing->title = "";
    return false;
}

// 第三屏那张单子里有没有这件事；有就是它在单子里的位置。
static int task_index_of(const buddy_ui_snapshot_t *snap, const char *id)
{
    return buddy_thing_find(snap->tasks,
                            snap->task_count < BUDDY_TASK_COUNT ? snap->task_count
                                                                : BUDDY_TASK_COUNT,
                            snap->past, past_count_of(snap), id);
}

static bool thing_active(const thing_t *thing)
{
    return thing->state == BUDDY_TASK_WORKING || thing->state == BUDDY_TASK_WAITING ||
           thing->state == BUDDY_TASK_QUEUED;
}

// 做完的事怎么结束的；还在做的返回 NULL。
static const char *thing_ended_text(const thing_t *thing, uint32_t *color)
{
    switch (thing->state) {
    case BUDDY_TASK_DONE:
        *color = C_DIM;
        return PT_TASK_DONE;
    case BUDDY_TASK_FAILED:
        *color = C_DANGER;
        return PT_CARD_FAILED;
    case BUDDY_TASK_CANCELLED:
        *color = C_DIM;
        return PT_CARD_CANCELLED;
    case BUDDY_TASK_WORKING:
    case BUDDY_TASK_WAITING:
    case BUDDY_TASK_QUEUED:
        break;
    }
    return NULL;
}

static void format_task_elapsed(const buddy_ui_snapshot_t *snap, const buddy_task_t *task,
                                char *out, size_t size)
{
    uint32_t seconds = pocket_task_seconds(task->seconds, snap->tasks_since_ms, snap->uptime_ms);

    (void)pocket_format_elapsed(0, (uint64_t)seconds * 1000U, out, size);
}

// 把这件事的两段字摆好。内容换了（另一件事、新的一轮）从头看；只是多了几句就留在原处。
static void talk_layout(const char *said, const char *reply, uint32_t reply_color,
                        uint32_t serial, const char *identity, bool from_end)
{
    uintptr_t shown = text_hash(text_hash(text_hash(2166136261u, identity), said), reply) | 1u;
    bool fresh = s.tl_serial != serial || s.tl_shown == 0U;
    int y = 0;

    set_text_color(s.tl_reply, reply_color);
    if (!fresh && s.tl_shown == shown) {
        return;
    }
    set_visible(s.tl_said, said[0] != '\0');
    set_visible(s.tl_reply, reply[0] != '\0');
    lv_label_set_text(s.tl_said, said);
    lv_label_set_text(s.tl_reply, reply);
    lv_obj_update_layout(s.tl_scroll);
    if (said[0] != '\0') {
        lv_obj_set_pos(s.tl_said, 0, y);
        y += lv_obj_get_height(s.tl_said) + LINE_SPACE;
    }
    if (reply[0] != '\0') {
        lv_obj_set_pos(s.tl_reply, 0, y);
        y += lv_obj_get_height(s.tl_reply) + LINE_SPACE;
    }
    s.tl_content = y;
    // 一件任务的来回从最新的那头看起；对话从头看起。
    s.tl_y = pocket_card_scroll(fresh ? (from_end ? y : 0) : s.tl_y, 0, y, TL_H, TL_STEP, NULL);
    s.tl_serial = serial;
    s.tl_shown = shown;
    // 位置是刚设的，先让 LVGL 算好，滚动的范围才是对的。
    lv_obj_update_layout(s.tl_scroll);
    lv_obj_scroll_to_y(s.tl_scroll, s.tl_y, LV_ANIM_OFF);
}

// 返回这一屏上有没有东西（一件事，或者正在进行的一轮）。
static bool render_talk(const buddy_ui_snapshot_t *snap)
{
    static char text[BUDDY_REPLY_MAX + 64];
    static char note[BUDDY_AGENT_MAX + 32];
    static char handed[BUDDY_AGENT_MAX + 48];
    static char meta[40];
    const buddy_cards_t *cards = snap->cards;
    bool live = snap->card_live;
    bool thread = snap->card_thread && snap->task_selected < thing_count(snap);
    bool hub = snap->chat.phase != BUDDY_CHAT_NONE;
    bool has_card = !live && cards != NULL && snap->card_index >= 0 &&
                    snap->card_index < (int)cards->count;
    const buddy_card_t *card = has_card ? &cards->cards[snap->card_index] : NULL;
    const char *agent = "";
    uint8_t effort = (uint8_t)BUDDY_EFFORT_NONE;
    const char *status = "";
    const char *said = "";
    const char *reply = "";
    const char *identity = "";
    uint32_t status_color = C_DIM;
    uint32_t note_color = C_DIM;
    uint32_t reply_color = C_TEXT;
    uint32_t color = C_XIAOYOU;
    pocket_pet_mood_t mood = POCKET_PET_IDLE;
    bool working = false;
    bool timer = false;
    char elapsed[12] = "";

    if (thread && !has_card) {
        // 这件任务的卡不在设备上：单子上那几行还在，先看这些。做完的事没有那几行，
        // 写它是怎么结束的；卡已经向手机要了，来了就换成卡。
        thing_t picked_thing;
        const thing_t *picked = &picked_thing;
        const char *words = "";
        uint32_t ended_color = C_DIM;

        (void)thing_at(snap, snap->task_selected, &picked_thing);
        if (picked->task != NULL) {
            words = picked->task->line2[0] != '\0' ? picked->task->line2 : picked->task->line1;
        } else {
            words = thing_ended_text(picked, &ended_color);
            words = words != NULL ? words : "";
        }

        pet_set(PET_TALK, POCKET_PET_IDLE);
        set_visible(s.talk_empty, false);
        set_visible(s.talk, true);
        set_visible(s.head_link, false);
        set_visible(s.head_tag.box, false);
        set_visible(s.head_elapsed, false);
        set_block_text(s.head_status, picked->agent);
        set_text_color(s.head_status, C_DIM);
        (void)snprintf(meta, sizeof(meta), "%u / %u", snap->task_selected + 1U,
                       thing_count(snap));
        set_text(s.meta_left, meta);
        set_text(s.meta_right, "");
        talk_layout(picked->title, words, picked->task != NULL ? C_TEXT : ended_color,
                    snap->card_serial, picked->id, true);
        return true;
    }
    set_visible(s.talk_empty, !live && !has_card);
    set_visible(s.talk, live || has_card);
    if (!live && !has_card) {
        s.tl_shown = 0;
        return false;
    }
    note[0] = '\0';
    meta[0] = '\0';
    if (live) {
        // 正在进行的这一轮：还没有卡，或者不会有卡。
        pocket_home_t home = pocket_home_for(snap);

        mood = pocket_pet_for(snap);
        said = hub ? snap->chat.said : "";
        (void)snprintf(note, sizeof(note), "%s", hub ? PT_CARD_NEW : "");
        switch (home) {
        case POCKET_HOME_SENT:
            status = PT_HEAD_SENT;
            said = PT_SAID_PENDING;
            reply = PT_TALK_SENT;
            reply_color = C_DIM;
            timer = true;
            break;
        case POCKET_HOME_THINKING:
            status = hub ? PT_HEAD_THINKING : PT_DESKTOP_BUSY;
            if (hub && said[0] == '\0') {
                said = PT_SAID_PENDING;
            }
            reply = hub ? PT_TALK_THINKING
                        : (snap->message[0] != '\0' ? snap->message : PT_DESKTOP_WORKING);
            reply_color = C_DIM;
            timer = hub;
            break;
        case POCKET_HOME_HELPER:
            status = PT_HEAD_WORKING;
            agent = snap->chat.agent;
            effort = snap->chat.effort;
            working = true;
            reply = snap->chat.stage;
            timer = true;
            break;
        case POCKET_HOME_FAILED:
            status = PT_HEAD_FAILED;
            status_color = C_DANGER;
            reply = snap->reply[0] != '\0' ? snap->reply : PT_TALK_FAILED;
            break;
        default:
            status = hub ? PT_HEAD_DONE : PT_DESKTOP_REPLY;
            reply = with_cut_note(text, sizeof(text), snap->reply, snap->reply_truncated);
            break;
        }
        if (timer) {
            (void)pocket_format_elapsed(snap->chat_since_ms, snap->uptime_ms, elapsed,
                                        sizeof(elapsed));
        }
        identity = "\x01";
    } else {
        int task = task_index_of(snap, card->id);
        bool agent_fits;

        agent = card->agent;
        effort = card->effort;
        agent_fits = text_width(agent) <= 72;
        identity = card->id;
        said = buddy_cards_said(cards, (unsigned)snap->card_index);
        reply = with_cut_note(text, sizeof(text),
                              buddy_cards_reply(cards, (unsigned)snap->card_index), card->cut);
        if (thread) {
            // 从第三屏进来的：这件任务自己的来回，第几件数的是单子上的。
            (void)snprintf(meta, sizeof(meta), "%u / %u  %s", snap->task_selected + 1U,
                           thing_count(snap), card->at);
        } else if (agent[0] != '\0') {
            // 交给了帮手的事不在这一屏：只说它去了哪，内容在第三屏。
            (void)snprintf(handed, sizeof(handed), PT_TALK_HANDED,
                           agent_fits ? agent : PT_CARD_HELPER);
            reply = handed;
            reply_color = C_DIM;
            (void)snprintf(meta, sizeof(meta), "%s", card->at);
        } else {
            // 第几句对话：只数小幽自己答的。
            unsigned number = 0;
            unsigned total = 0;
            unsigned each;

            for (each = 0; each < cards->count; ++each) {
                if (cards->cards[each].agent[0] == '\0') {
                    ++total;
                    if ((int)each <= snap->card_index) {
                        ++number;
                    }
                }
            }
            (void)snprintf(meta, sizeof(meta), "%u / %u  %s", number, total, card->at);
        }
        switch ((buddy_card_state_t)card->state) {
        case BUDDY_CARD_TALKING:
            status = PT_HEAD_THINKING;
            mood = POCKET_PET_BUSY;
            break;
        case BUDDY_CARD_WORKING:
            status = PT_HEAD_WORKING;
            mood = POCKET_PET_BUSY;
            working = agent[0] != '\0';
            // 名字太长这一行放不下：谁在做，顶上的名牌里有。
            (void)snprintf(note, sizeof(note), PT_CARD_WORKING, agent_fits ? agent : PT_CARD_HELPER);
            break;
        case BUDDY_CARD_WAITING:
            status = PT_CARD_WAITING;
            status_color = C_WARN;
            note_color = C_WARN;
            mood = POCKET_PET_ASK;
            working = agent[0] != '\0';
            (void)snprintf(note, sizeof(note), "%s", PT_CARD_WAITING);
            break;
        case BUDDY_CARD_DONE:
            status = PT_HEAD_DONE;
            if (card->edits > 0U) {
                (void)snprintf(note, sizeof(note), PT_CARD_EDITS, (unsigned)card->edits);
            } else if (agent[0] != '\0') {
                (void)snprintf(note, sizeof(note), PT_CARD_DONE, agent_fits ? agent : PT_CARD_HELPER);
            } else {
                (void)snprintf(note, sizeof(note), "%s", PT_CARD_SELF);
            }
            break;
        case BUDDY_CARD_FAILED:
            status = PT_HEAD_FAILED;
            status_color = C_DANGER;
            note_color = C_DANGER;
            mood = POCKET_PET_OOPS;
            if (reply[0] == '\0') {
                reply = PT_TALK_FAILED;  // 没成，也没说为什么
            }
            (void)snprintf(note, sizeof(note), "%s", PT_CARD_FAILED);
            break;
        case BUDDY_CARD_CANCELLED:
            status = PT_CARD_CANCELLED;
            reply_color = C_DIM;
            (void)snprintf(note, sizeof(note), "%s", PT_CARD_CANCELLED);
            break;
        }
        if (working && task >= 0 && task < (int)snap->task_count && task < BUDDY_TASK_COUNT &&
            snap->tasks[task].state == BUDDY_TASK_WORKING) {
            // 做了多久，第三屏那张单子里有。
            format_task_elapsed(snap, &snap->tasks[task], elapsed, sizeof(elapsed));
            timer = true;
        }
    }
    if (working) {
        color = pocket_helper_color(agent);
    }

    pet_set(PET_TALK, mood);
    set_visible(s.head_link, working);
    set_visible(s.head_tag.box, working);
    if (working) {
        set_link_color(s.head_link, color);
        set_tag_tier(&s.head_tag, agent, effort, color, HEAD_TAG_TEXT_W);
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
        set_text(s.head_elapsed, elapsed);
        set_text_color(s.head_elapsed, color);
    }
    set_text(s.meta_left, meta);
    set_text(s.meta_right, note);
    set_text_color(s.meta_right, note_color);
    talk_layout(said, reply, reply_color, snap->card_serial, identity, thread);
    return true;
}

// ---- 第三屏：任务 ----

static void set_y(lv_obj_t *obj, int y);

// 窗口里的一行：把第 slot 个行控件摆到 y，写上这件事。
static void render_task_row(const buddy_ui_snapshot_t *snap, unsigned slot, int y,
                            const thing_t *thing, bool selected)
{
    uint32_t color = pocket_helper_color(thing->agent);
    uint32_t right_color = C_DIM;
    const char *right = thing_ended_text(thing, &right_color);
    char elapsed[12];
    int title_x = 6;
    int title_w;

    set_visible(s.task_rows[slot], true);
    set_y(s.task_rows[slot], y);
    set_visible(s.task_tags[slot].box, thing->agent[0] != '\0');
    if (thing->agent[0] != '\0') {
        // 带着档位时名牌宽一个字，标题让出来。
        set_tag_tier(&s.task_tags[slot], thing->agent, thing->effort, color,
                     TASK_TAG_TEXT_W + (thing->effort != BUDDY_EFFORT_NONE ? 22 : 0));
        title_x = lv_obj_get_x(s.task_tags[slot].box) +
                  lv_obj_get_width(s.task_tags[slot].box) + 6;
    }
    if (thing->state == BUDDY_TASK_WAITING) {
        right = PT_TASK_WAITING;
        right_color = C_WARN;
    } else if (thing->state == BUDDY_TASK_QUEUED) {
        right = PT_TASK_QUEUED;
    } else if (right == NULL) {
        elapsed[0] = '\0';
        if (thing->task != NULL) {
            format_task_elapsed(snap, thing->task, elapsed, sizeof(elapsed));
        }
        right = elapsed;
        right_color = color;
    }
    set_text(s.task_rights[slot], right);
    set_text_color(s.task_rights[slot], right_color);
    // 做完的事标题暗一些：一眼分得出哪些还在做。
    set_text_color(s.task_titles[slot], thing_active(thing) ? C_TEXT : C_DIM);
    lv_obj_update_layout(s.task_rights[slot]);
    title_w = INNER_W - 4 - 6 - lv_obj_get_width(s.task_rights[slot]) - 6 - title_x;
    if (title_w < 16) {
        title_w = 16;
    }
    if (lv_obj_get_x(s.task_titles[slot]) != title_x ||
        lv_obj_get_width(s.task_titles[slot]) != title_w) {
        lv_obj_set_x(s.task_titles[slot], title_x);
        lv_obj_set_width(s.task_titles[slot], title_w);
        lv_obj_set_user_data(s.task_titles[slot], NULL);
    }
    set_block_text(s.task_titles[slot], thing->title);
    // 选中的那行有描边。
    if (lv_obj_get_style_border_opa(s.task_rows[slot], LV_PART_MAIN) !=
        (selected ? LV_OPA_COVER : LV_OPA_TRANSP)) {
        lv_obj_set_style_border_opa(s.task_rows[slot], selected ? LV_OPA_COVER : LV_OPA_TRANSP,
                                    0);
    }
}

static void render_tasks(const buddy_ui_snapshot_t *snap)
{
    static pocket_task_line_t lines[POCKET_TASK_LINES_MAX];
    static char ended[BUDDY_DAY_MAX + 24];
    unsigned count = thing_count(snap);
    unsigned selected = snap->task_selected < count ? snap->task_selected : 0U;
    unsigned line_count = pocket_task_lines(snap, lines, POCKET_TASK_LINES_MAX);
    unsigned rows = 0;
    unsigned days = 0;
    unsigned index;
    thing_t thing;
    int y = 0;

    // 单子比窗口长时只画窗口里的那几行，选中的那件总在里面。
    s.task_top = pocket_task_window(lines, line_count, selected, s.task_top, TASK_STEP,
                                    TASK_DAY_STEP, TASK_LIST_H);
    for (index = s.task_top; index < line_count; ++index) {
        int height = lines[index].header ? TASK_DAY_STEP : TASK_STEP;

        // 一行的间隔在它下面：最后一行不用留。
        if (y + height - (lines[index].header ? 1 : TASK_ROW_GAP) > TASK_LIST_H) {
            break;
        }
        if (lines[index].header && index + 1U < line_count &&
            y + TASK_DAY_STEP + TASK_ROW_H > TASK_LIST_H) {
            break; // 小标题下面那件事放不下：只剩一个标题不如不画
        }
        (void)thing_at(snap, lines[index].item, &thing);
        if (lines[index].header) {
            if (days < TASK_DAY_POOL) {
                set_visible(s.task_days[days], true);
                set_y(s.task_days[days], y);
                set_text(s.task_days[days], thing.past != NULL ? thing.past->day : "");
                ++days;
            }
            y += TASK_DAY_STEP;
        } else {
            if (rows < TASK_POOL) {
                render_task_row(snap, rows, y, &thing, lines[index].item == selected);
                ++rows;
            }
            y += TASK_STEP;
        }
    }
    for (; rows < TASK_POOL; ++rows) {
        set_visible(s.task_rows[rows], false);
    }
    for (; days < TASK_DAY_POOL; ++days) {
        set_visible(s.task_days[days], false);
    }
    set_visible(s.tasks_empty, count == 0U);
    set_visible(s.task_detail, count > 0U);
    if (count == 0U) {
        return;
    }
    (void)thing_at(snap, selected, &thing);
    set_block_text(s.task_title, thing.title);
    {
        char place[24];

        // 单子比窗口长才写：不然一眼就数得出来。
        place[0] = '\0';
        if (line_count > TASK_LIST_ROWS) {
            (void)snprintf(place, sizeof(place), "%u/%u", selected + 1U, count);
        }
        set_text(s.task_place, place);
    }
    set_visible(s.task_lines[0], thing_active(&thing));
    set_visible(s.task_lines[1], thing_active(&thing));
    set_visible(s.task_result, !thing_active(&thing));
    if (!thing_active(&thing)) {
        uint32_t color = C_DIM;
        const char *how = thing_ended_text(&thing, &color);

        if (thing.task != NULL && thing.task->line1[0] != '\0') {
            // 结论是单子上那一行（简报）；整件事的来回按确认键进去看。
            set_block_text(s.task_result, thing.task->line1);
            set_text_color(s.task_result, thing.state == BUDDY_TASK_FAILED ? C_DANGER : C_TEXT);
        } else {
            // 做完的事在单子上只有标题：写它是哪天、怎么结束的，经过按确认键进去看。
            (void)snprintf(ended, sizeof(ended), "%s%s%s",
                           thing.past != NULL ? thing.past->day : "",
                           thing.past != NULL && thing.past->day[0] != '\0' ? "  " : "",
                           how != NULL ? how : "");
            set_block_text(s.task_result, ended);
            set_text_color(s.task_result, color);
        }
        return;
    }
    if (thing.task != NULL) {
        const buddy_task_t *task = thing.task;
        bool any = task->line1[0] != '\0' || task->line2[0] != '\0';

        // 两步里靠后的那一步更亮；只有一步时它就是最新的。
        set_block_text(s.task_lines[0], any ? (task->line2[0] != '\0' ? task->line1 : "")
                                            : PT_TASK_NO_STEPS);
        set_block_text(s.task_lines[1], task->line2[0] != '\0' ? task->line2 : task->line1);
    }
}

// ---- 第四屏：用量 ----

// 第四屏的控件用到时才建，一块十几个控件、几 KB。这块板没有外接内存：剩得不多时
// 就不建了（这一屏照没有数据那样显示，数在手机上看），好过建到一半要不到内存、
// 整台设备卡死重启——那时固件已经被认可，不会自己退回上一版。
#define UI_GROW_RESERVE_BYTES (20U * 1024U)

static bool ui_room_to_grow(void)
{
#ifdef ESP_PLATFORM
    return esp_get_free_heap_size() >= UI_GROW_RESERVE_BYTES;
#else
    return true;
#endif
}

// 第 index 块；第一次用到时才把它的控件建出来。
static usage_block_t *usage_block(unsigned index)
{
    static const char *const names[2] = {PT_USAGE_SHORT, PT_USAGE_WEEK};
    usage_block_t *block = &s.usage_blocks[index];
    int each;

    if (block->box != NULL) {
        return block;
    }
    block->box = make_box(s.usage_scroll, SIDE, 0, INNER_W, USAGE_WHO_H);
    for (each = 0; each < BUDDY_USAGE_WHO; ++each) {
        make_tag(block->box, &block->tags[each], 0, each * USAGE_WHO_H);
        // 模型名是对面发来的字，用正文那套字库。
        block->models[each] = make_text_block(block->box, &pocket_font_16, C_DIM, 0,
                                              each * USAGE_WHO_H, 10, LINES_16(1));
    }
    for (each = 0; each < 2; ++each) {
        block->names[each] = make_label(block->box, &pocket_font_14, C_DIM, names[each]);
        block->bars[each] = make_box(block->box, USAGE_LABEL_W, 0, USAGE_BAR_W, USAGE_BAR_H);
        lv_obj_add_event_cb(block->bars[each], usage_bar_draw_cb, LV_EVENT_DRAW_MAIN, NULL);
        block->lefts[each] = make_label(block->box, &pocket_font_14, C_TEXT, "");
        lv_obj_set_width(block->lefts[each], USAGE_LEFT_W);
        lv_obj_set_style_text_align(block->lefts[each], LV_TEXT_ALIGN_RIGHT, 0);
        lv_obj_set_x(block->lefts[each], INNER_W - USAGE_LEFT_W);
    }
    block->reset = make_text_block(block->box, &pocket_font_14, C_DIM, 2, 0, INNER_W - 4, 16);
    return block;
}

static void set_y(lv_obj_t *obj, int y)
{
    if (lv_obj_get_y(obj) != y) {
        lv_obj_set_y(obj, y);
    }
}

// 一个重置时间怎么写：今天之内只写几点，别的带星期。写不出来（不知道）时是空串。
static void format_reset(const buddy_ui_snapshot_t *snap, uint32_t at, char *out, size_t size)
{
    static const char *const weekdays[7] = {PT_WEEKDAY_1, PT_WEEKDAY_2, PT_WEEKDAY_3,
                                            PT_WEEKDAY_4, PT_WEEKDAY_5, PT_WEEKDAY_6,
                                            PT_WEEKDAY_7};
    char clock[8];
    int weekday = pocket_format_reset(at, snap->timezone_offset_seconds, pocket_now_epoch(snap),
                                      clock, sizeof(clock));

    if (weekday < 0) {
        out[0] = '\0';
    } else if (weekday == 0) {
        (void)snprintf(out, size, "%s", clock);
    } else {
        (void)snprintf(out, size, "%s %s", weekdays[weekday - 1], clock);
    }
}

// 返回这一屏的内容比窗口高不高（高就能上下翻）。
static bool render_usage(const buddy_ui_snapshot_t *snap)
{
    static char text[BUDDY_MODEL_MAX + 24];
    unsigned count = snap->usage_count < BUDDY_USAGE_COUNT ? snap->usage_count
                                                           : BUDDY_USAGE_COUNT;
    uint32_t oldest = 0;
    bool timed = false;
    unsigned built = 0;
    unsigned index;
    int y = 2;

    set_visible(s.usage_empty, count == 0U);
    set_visible(s.usage_scroll, count > 0U);
    for (index = 0; index < BUDDY_USAGE_COUNT; ++index) {
        const buddy_usage_t *usage = &snap->usage[index];
        usage_block_t *block = &s.usage_blocks[index];
        const int8_t lefts[2] = {usage->left_short, usage->left_week};
        bool metered = usage->state != (uint8_t)BUDDY_QUOTA_NONE;
        bool known = lefts[0] >= 0 || lefts[1] >= 0;
        unsigned who;
        int row = 0;
        int each;

        if (index >= count) {
            if (block->box != NULL) {
                set_visible(block->box, false);
            }
            continue;
        }
        if (block->box == NULL && !ui_room_to_grow()) {
            continue; // 内存不够再建一块：这一条不显示
        }
        ++built;
        block = usage_block(index);
        set_visible(block->box, true);
        for (who = 0; who < BUDDY_USAGE_WHO; ++who) {
            const buddy_usage_who_t *one = &usage->who[who];
            bool present = who < usage->who_count;
            uint32_t color = pocket_helper_color(one->name);
            const char *model = one->model[0] != '\0' ? one->model : PT_USAGE_NO_MODEL;
            int x;

            set_visible(block->tags[who].box, present);
            set_visible(block->models[who], present);
            if (!present) {
                continue;
            }
            // 正在干活的那个：名牌是实心的，模型名是亮的——一眼看得出现在是谁、用哪个模型在干。
            set_tag_tier_colors(&block->tags[who], one->name, one->effort,
                                one->running > 0U ? C_ON_COLOR : color, color,
                                USAGE_TAG_TEXT_W);
            set_bg_color(block->tags[who].box, color);
            set_bg_opa(block->tags[who].box, one->running > 0U ? LV_OPA_COVER : LV_OPA_TRANSP);
            set_y(block->tags[who].box, row);
            x = lv_obj_get_width(block->tags[who].box) + 6;
            if (metered) {
                (void)snprintf(text, sizeof(text), "%s", model);
            } else {
                (void)snprintf(text, sizeof(text), "%s%s%s", model, PT_KEY_SEPARATOR,
                               PT_USAGE_PAYG);
            }
            {
                // 名牌旁边放不下就写到下一行，不截模型名。量字宽只在字或名牌变了的时候做。
                uintptr_t shown = text_hash((uintptr_t)x, text);
                bool below = block->model_below[who];

                if (block->model_shown[who] != shown) {
                    block->model_shown[who] = shown;
                    below = text_width(text) > INNER_W - x;
                    block->model_below[who] = below;
                    lv_obj_set_x(block->models[who], below ? 2 : x);
                    lv_obj_set_width(block->models[who], below ? INNER_W - 4 : INNER_W - x);
                    lv_obj_set_user_data(block->models[who], NULL);
                }
                set_y(block->models[who], below ? row + USAGE_WHO_H - 1 : row);
                row += below ? USAGE_WHO_H + LINE_16 : USAGE_WHO_H;
            }
            set_block_text(block->models[who], text);
            set_text_color(block->models[who], one->running > 0U ? C_TEXT : C_DIM);
        }
        // 两个窗口：格子和数。一个数都没有时只写一句“还不知道”。
        for (each = 0; each < 2; ++each) {
            bool shown = metered && known;
            int left = lefts[each];

            set_visible(block->names[each], shown);
            set_visible(block->bars[each], shown && left >= 0);
            set_visible(block->lefts[each], shown);
            if (!shown) {
                continue;
            }
            set_y(block->names[each], row);
            set_y(block->bars[each], row + 5);
            set_y(block->lefts[each], row);
            if ((int)(intptr_t)lv_obj_get_user_data(block->bars[each]) != left + 1) {
                lv_obj_set_user_data(block->bars[each], (void *)(intptr_t)(left + 1));
                lv_obj_invalidate(block->bars[each]);
            }
            if (left < 0) {
                set_text(block->lefts[each], PT_NO_VALUE);
                set_text_color(block->lefts[each], C_DIM);
            } else if (left == 0) {
                set_text(block->lefts[each], PT_USAGE_GONE);
                set_text_color(block->lefts[each], C_DANGER);
            } else {
                (void)snprintf(text, sizeof(text), PT_USAGE_LEFT, (unsigned)left);
                set_text(block->lefts[each], text);
                set_text_color(block->lefts[each], left < 20 ? C_WARN : C_TEXT);
            }
            row += USAGE_ROW_H;
        }
        set_visible(block->reset, metered);
        if (metered) {
            char first[16];
            char second[16];

            format_reset(snap, usage->reset_short, first, sizeof(first));
            format_reset(snap, usage->reset_week, second, sizeof(second));
            if (!known) {
                (void)snprintf(text, sizeof(text), "%s", PT_USAGE_UNKNOWN);
            } else if (first[0] != '\0' && second[0] != '\0') {
                (void)snprintf(text, sizeof(text), PT_USAGE_RESET_BOTH, first, second);
            } else if (first[0] != '\0' || second[0] != '\0') {
                (void)snprintf(text, sizeof(text), PT_USAGE_RESET,
                               first[0] != '\0' ? first : second);
            } else {
                text[0] = '\0';
            }
            set_block_text(block->reset, text);
            set_y(block->reset, row);
            if (text[0] != '\0') {
                row += USAGE_ROW_H;
            }
            // 最下面那行说的是最旧的那个账号的数。
            if (!timed || usage->age > oldest) {
                oldest = usage->age;
                timed = true;
            }
        }
        set_y(block->box, y);
        if (lv_obj_get_height(block->box) != row) {
            lv_obj_set_height(block->box, row);
        }
        y += row + USAGE_GAP;
    }
    if (count > 0U && built == 0U) {
        // 一块都没建成（内存不够）：照没有数据那样显示。
        set_visible(s.usage_empty, true);
        set_visible(s.usage_scroll, false);
        return false;
    }
    set_visible(s.usage_age, count > 0U && timed);
    if (count > 0U && timed) {
        uint32_t minutes = pocket_usage_minutes(oldest, snap->usage_since_ms, snap->uptime_ms);

        if (minutes == 0U) {
            (void)snprintf(text, sizeof(text), "%s", PT_USAGE_AGE_NOW);
        } else if (minutes < 60U) {
            (void)snprintf(text, sizeof(text), PT_USAGE_AGE_MIN, (unsigned)minutes);
        } else {
            (void)snprintf(text, sizeof(text), PT_USAGE_AGE_HOUR,
                           (unsigned)(minutes / 60U > 99U ? 99U : minutes / 60U));
        }
        set_text(s.usage_age, text);
        // 半小时以上的数只能当个大概：暗下去。
        set_text_color(s.usage_age, minutes >= 30U ? C_LINE : C_DIM);
        set_y(s.usage_age, y - 2);
        y += USAGE_ROW_H;
    }
    return count > 0U && y > AREA_H;
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
    if (text_width(tool) > BUBBLE_W - 2 * BUBBLE_PAD_X) {
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
    bool can_talk;
    int count = 0;
    int index;

    if (!s.ready || snap == NULL) {
        return;
    }
    // 连着手机中枢、连接也好着，才能按住说话。
    can_talk = snap->host_hub && pocket_home_for(snap) >= POCKET_HOME_QUIET;
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

    // 顶栏的小点：从第三屏进到一件任务里时，亮的还是第三个。
    render_top_bar(snap, pocket_screen_index(snap));
    if (view == POCKET_VIEW_PAGE && page == BUDDY_PAGE_USAGE && entered) {
        lv_obj_scroll_to_y(s.usage_scroll, 0, LV_ANIM_OFF);
    }
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

            render_home(snap);
            // 对话正在屏幕上时，设备自己要说的一句话临时占用提示这一行。
            notice = pocket_home_is_talk(home) && pocket_notice_visible(snap) && snap->host_hub;
            hints[count++] = (hint_item_t){KEY_OK, can_talk ? PT_HINT_HOME : PT_HINT_HOME_MUTE};
            break;
        }
        case BUDDY_PAGE_TALK:
            if (render_talk(snap)) {
                hints[count++] = (hint_item_t){KEY_UP, PT_HINT_PREV};
                hints[count++] = (hint_item_t){KEY_DOWN, PT_HINT_NEXT};
            }
            notice = pocket_notice_visible(snap) && snap->host_hub;
            hints[count++] = (hint_item_t){
                KEY_OK, !can_talk ? PT_HINT_SCREEN
                                  : (snap->card_thread ? PT_HINT_SCREEN_ADD : PT_HINT_SCREEN_TALK)};
            break;
        case BUDDY_PAGE_TASKS:
            render_tasks(snap);
            if (thing_count(snap) > 1U) {
                hints[count++] = (hint_item_t){KEY_UP, PT_HINT_PREV};
                hints[count++] = (hint_item_t){KEY_DOWN, PT_HINT_NEXT};
            }
            notice = pocket_notice_visible(snap) && snap->host_hub;
            // 有选中的事时，按住说的话是对它的补充。
            hints[count++] = (hint_item_t){
                KEY_OK, thing_count(snap) > 0U ? (can_talk ? PT_HINT_OPEN_ADD : PT_HINT_ENTER)
                                              : (can_talk ? PT_HINT_SCREEN_TALK : PT_HINT_SCREEN)};
            break;
        case BUDDY_PAGE_USAGE:
            if (render_usage(snap)) {
                hints[count++] = (hint_item_t){KEY_UP, PT_HINT_PREV};
                hints[count++] = (hint_item_t){KEY_DOWN, PT_HINT_NEXT};
            }
            notice = pocket_notice_visible(snap) && snap->host_hub;
            hints[count++] =
                (hint_item_t){KEY_OK, can_talk ? PT_HINT_SCREEN_TALK : PT_HINT_SCREEN};
            break;
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

int pocket_ui_scroll(int delta)
{
    int edge = 0;

    if (!s.ready || delta == 0) {
        return 0;
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
    } else if (s.view == POCKET_VIEW_PAGE && s.page == BUDDY_PAGE_USAGE) {
        // 一次翻三块名牌那么高。
        lv_obj_scroll_by_bounded(s.usage_scroll, 0,
                                 delta > 0 ? -3 * USAGE_WHO_H : 3 * USAGE_WHO_H, LV_ANIM_OFF);
    } else if (s.view == POCKET_VIEW_PAGE && s.page == BUDDY_PAGE_TALK) {
        // 在这件事里翻七行；已经到头了，告诉调用方该换到上一件 / 下一件。
        s.tl_y = pocket_card_scroll(s.tl_y, delta, s.tl_content, TL_H, TL_STEP, &edge);
        if (edge == 0) {
            lv_obj_scroll_to_y(s.tl_scroll, s.tl_y, LV_ANIM_OFF);
        }
    }
    return edge;
}
