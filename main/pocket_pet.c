#include "pocket_pet.h"

#include <stdbool.h>
#include <stddef.h>

#define BODY_SIZE 16
#define BODY_X 2
#define BODY_Y 4

// o 轮廓  b 身体  s 阴影  h 高光  c 腮红  . 透明
static const char *const BODY[BODY_SIZE] = {
    "................",
    ".....oooooo.....",
    "...oobbbbbboo...",
    "..obbhhbbbbbbo..",
    ".obbhbbbbbbbbbo.",
    ".obbbbbbbbbbbbo.",
    ".obbbbbbbbbbbbo.",
    ".obbbbbbbbbbbbo.",
    ".obcbbbbbbbbcbo.",
    ".obbbbbbbbbbbbo.",
    ".obbbbbbbbbbbbo.",
    ".obbbbbbbbbbbso.",
    ".obbbbbbbbbbsso.",
    ".obsobbsobbsobo.",
    ".oo.ooo.ooo.oo..",
    "................",
};

typedef struct {
    int8_t x;
    int8_t y;
    uint8_t color;
} dot_t;

// 脸部坐标相对身体左上角；左眼在 (5,7)，右眼在 (10,7)，嘴在 (7,9)。
#define E POCKET_PET_EYE
static const dot_t FACE_SLEEP[] = {
    {4, 8, E}, {5, 8, E}, {6, 8, E}, {9, 8, E}, {10, 8, E}, {11, 8, E},
};
static const dot_t FACE_IDLE[] = {
    {5, 7, E}, {5, 8, E}, {10, 7, E}, {10, 8, E}, {7, 9, E}, {8, 9, E},
};
static const dot_t FACE_BUSY[] = {
    {5, 8, E}, {6, 8, E}, {10, 8, E}, {11, 8, E}, {7, 9, E}, {8, 9, E},
};
static const dot_t FACE_ASK[] = {
    {5, 7, POCKET_PET_SHINE}, {6, 7, E}, {5, 8, E}, {6, 8, E},
    {10, 7, POCKET_PET_SHINE}, {11, 7, E}, {10, 8, E}, {11, 8, E},
    {7, 9, E}, {8, 9, E}, {7, 10, E}, {8, 10, E},
};
static const dot_t FACE_HAPPY[] = {
    {4, 8, E}, {5, 7, E}, {6, 8, E}, {9, 8, E}, {10, 7, E}, {11, 8, E},
    {6, 9, E}, {7, 10, E}, {8, 10, E}, {9, 9, E},
};
static const dot_t FACE_OOPS[] = {
    {4, 7, E}, {6, 7, E}, {5, 8, E}, {4, 9, E}, {6, 9, E},
    {9, 7, E}, {11, 7, E}, {10, 8, E}, {9, 9, E}, {11, 9, E},
    {6, 11, E}, {7, 10, E}, {8, 10, E}, {9, 11, E},
};
#undef E

// 小符号的坐标是整个 20×20 格子里的绝对位置，不随身体浮动。
#define G POCKET_PET_GRAY
static const dot_t FX_SLEEP[] = {
    {15, 3, G}, {16, 3, G}, {17, 3, G}, {16, 4, G}, {15, 5, G}, {16, 5, G}, {17, 5, G},
    {18, 0, G}, {19, 0, G}, {18, 1, G},
};
#undef G
static const dot_t FX_BUSY[] = {
    {17, 7, POCKET_PET_WATER}, {17, 8, POCKET_PET_WATER},
};
#define A POCKET_PET_ALERT
static const dot_t FX_ASK[] = {
    {17, 0, A}, {18, 0, A}, {17, 1, A}, {18, 1, A}, {17, 2, A}, {18, 2, A},
    {17, 3, A}, {18, 3, A}, {17, 5, A}, {18, 5, A},
};
#undef A
#define H POCKET_PET_HEART
static const dot_t FX_HAPPY[] = {
    {16, 1, H}, {18, 1, H}, {15, 2, H}, {16, 2, H}, {17, 2, H}, {18, 2, H}, {19, 2, H},
    {16, 3, H}, {17, 3, H}, {18, 3, H}, {17, 4, H},
};
#undef H
static const dot_t FX_OOPS[] = {
    {1, 8, POCKET_PET_WATER}, {1, 9, POCKET_PET_WATER}, {0, 9, POCKET_PET_WATER},
};

typedef struct {
    const dot_t *face;
    uint8_t face_count;
    const dot_t *fx;
    uint8_t fx_count;
    bool bobs;  // 奇数帧整体上移一格
} mood_t;

#define COUNT(a) ((uint8_t)(sizeof(a) / sizeof((a)[0])))
static const mood_t MOODS[POCKET_PET_MOOD_COUNT] = {
    [POCKET_PET_SLEEP] = {FACE_SLEEP, COUNT(FACE_SLEEP), FX_SLEEP, COUNT(FX_SLEEP), false},
    [POCKET_PET_IDLE] = {FACE_IDLE, COUNT(FACE_IDLE), NULL, 0, true},
    [POCKET_PET_BUSY] = {FACE_BUSY, COUNT(FACE_BUSY), FX_BUSY, COUNT(FX_BUSY), false},
    [POCKET_PET_ASK] = {FACE_ASK, COUNT(FACE_ASK), FX_ASK, COUNT(FX_ASK), true},
    [POCKET_PET_HAPPY] = {FACE_HAPPY, COUNT(FACE_HAPPY), FX_HAPPY, COUNT(FX_HAPPY), true},
    [POCKET_PET_OOPS] = {FACE_OOPS, COUNT(FACE_OOPS), FX_OOPS, COUNT(FX_OOPS), false},
};

static pocket_pet_color_t find(const dot_t *dots, uint8_t count, int x, int y)
{
    uint8_t index;

    for (index = 0; index < count; ++index) {
        if (dots[index].x == x && dots[index].y == y) {
            return (pocket_pet_color_t)dots[index].color;
        }
    }
    return POCKET_PET_CLEAR;
}

pocket_pet_color_t pocket_pet_pixel(pocket_pet_mood_t mood, unsigned frame, int x, int y)
{
    const mood_t *look;
    pocket_pet_color_t color;
    int bx;
    int by;

    if (x < 0 || y < 0 || x >= POCKET_PET_GRID || y >= POCKET_PET_GRID ||
        (unsigned)mood >= POCKET_PET_MOOD_COUNT) {
        return POCKET_PET_CLEAR;
    }
    look = &MOODS[mood];

    color = find(look->fx, look->fx_count, x, y);
    if (color != POCKET_PET_CLEAR) {
        return color;
    }
    if (mood == POCKET_PET_BUSY && y == 1 && (x == 7 || x == 10 || x == 13)) {
        // 头顶三个点依次点亮：第 0 帧亮一个，第 1 帧亮两个，第 2 帧全亮。
        return (unsigned)((x - 7) / 3) <= frame % 3U ? POCKET_PET_ACCENT : POCKET_PET_GRAY;
    }

    bx = x - BODY_X;
    by = y - BODY_Y + ((look->bobs && (frame & 1U) != 0U) ? 1 : 0);
    if (bx < 0 || by < 0 || bx >= BODY_SIZE || by >= BODY_SIZE) {
        return POCKET_PET_CLEAR;
    }
    color = find(look->face, look->face_count, bx, by);
    if (color != POCKET_PET_CLEAR) {
        return color;
    }
    switch (BODY[by][bx]) {
    case 'o':
        return POCKET_PET_OUTLINE;
    case 'b':
        return POCKET_PET_BODY;
    case 's':
        return POCKET_PET_SHADE;
    case 'h':
        return POCKET_PET_SHINE;
    case 'c':
        return POCKET_PET_CHEEK;
    default:
        return POCKET_PET_CLEAR;
    }
}

uint32_t pocket_pet_rgb(pocket_pet_color_t color)
{
    static const uint32_t PALETTE[POCKET_PET_COLOR_COUNT] = {
        [POCKET_PET_CLEAR] = 0x000000,
        [POCKET_PET_OUTLINE] = 0x606078,
        [POCKET_PET_BODY] = 0xF0EEE6,
        [POCKET_PET_SHADE] = 0xC8C6CD,
        [POCKET_PET_SHINE] = 0xFFFFFF,
        [POCKET_PET_CHEEK] = 0xF09696,
        [POCKET_PET_EYE] = 0x221A18,
        [POCKET_PET_GRAY] = 0x7D786F,
        [POCKET_PET_ACCENT] = 0xE08A63,
        [POCKET_PET_WATER] = 0x78BEF0,
        [POCKET_PET_ALERT] = 0xEDB95E,
        [POCKET_PET_HEART] = 0xEA6F63,
    };

    return (unsigned)color < POCKET_PET_COLOR_COUNT ? PALETTE[color] : 0;
}
