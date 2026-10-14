// DS pixel head portraits compiled from the retained avatar-whale-v1 artwork.
#pragma once

#include <stdint.h>

#define POCKET_PET_GRID 60
// Existing screen footprints remain 120, 40 and 60 display pixels.
#define POCKET_PET_LAYOUT_GRID 20
// Eight existing 450 ms timer ticks form a gentle 3.6 second breathing cycle.
#define POCKET_PET_BREATH_FRAMES 8U

typedef enum {
    POCKET_PET_SLEEP,  // 闭眼 + Zz，轻轻呼吸：没连上
    POCKET_PET_IDLE,   // 睁眼，轻轻上下浮动
    POCKET_PET_BUSY,   // 低头 + 头顶跳动的三个点 + 汗滴
    POCKET_PET_ASK,    // 大眼睛 + 感叹号：有事找你
    POCKET_PET_HAPPY,  // 笑眼 + 爱心
    POCKET_PET_OOPS,   // 叉叉眼 + 汗滴：失败
    POCKET_PET_MOOD_COUNT,
} pocket_pet_mood_t;

// 调色板下标；0 表示透明。
typedef enum {
    POCKET_PET_CLEAR,
    POCKET_PET_OUTLINE,
    POCKET_PET_BODY,
    POCKET_PET_SHADE,
    POCKET_PET_SHINE,
    POCKET_PET_CHEEK,
    POCKET_PET_EYE,
    POCKET_PET_GRAY,    // Zz、未点亮的点
    POCKET_PET_ACCENT,  // 点亮的点
    POCKET_PET_WATER,   // 汗滴
    POCKET_PET_ALERT,   // 感叹号
    POCKET_PET_HEART,
    POCKET_PET_SKIN,
    POCKET_PET_DRESS,
    POCKET_PET_FIN,
    POCKET_PET_SOURCE_FIRST = 16,
    POCKET_PET_COLOR_COUNT = POCKET_PET_SOURCE_FIRST + 64,
} pocket_pet_color_t;

// (x, y) 处的调色板下标，坐标越界返回 POCKET_PET_CLEAR。frame 是动画帧号，
// 任意无符号数都可以（内部取模）。
pocket_pet_color_t pocket_pet_pixel(pocket_pet_mood_t mood, unsigned frame, int x, int y);
// 调色板下标对应的 0xRRGGBB；透明和越界返回 0。
uint32_t pocket_pet_rgb(pocket_pet_color_t color);
