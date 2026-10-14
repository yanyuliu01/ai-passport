#include "pocket_pet.h"

#include <stdbool.h>
#include <stddef.h>
#include "../assets/images/ai-companions/pocket_whale_pixels.h"

typedef struct {
    int8_t x;
    int8_t y;
    uint8_t color;
} dot_t;

// Status marks retain their 20x20 layout coordinates, scaled into the atlas.
#define G POCKET_PET_GRAY
static const dot_t FX_SLEEP[] = {
    {15, 3, G}, {16, 3, G}, {17, 3, G}, {16, 4, G}, {15, 5, G}, {16, 5, G}, {17, 5, G},
    {18, 0, G}, {19, 0, G}, {18, 1, G},
};
#undef G
static const dot_t FX_BUSY[] = {
    {17, 7, POCKET_PET_WATER}, {17, 8, POCKET_PET_WATER},
};
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
    const dot_t *fx;
    uint8_t fx_count;
    bool bobs;  // 头像轻缓呼吸，状态符号固定
} mood_t;

#define COUNT(a) ((uint8_t)(sizeof(a) / sizeof((a)[0])))
static const mood_t MOODS[POCKET_PET_MOOD_COUNT] = {
    [POCKET_PET_SLEEP] = {FX_SLEEP, COUNT(FX_SLEEP), true},
    [POCKET_PET_IDLE] = {NULL, 0, true},
    [POCKET_PET_BUSY] = {FX_BUSY, COUNT(FX_BUSY), false},
    [POCKET_PET_ASK] = {NULL, 0, true},  // 不再画感叹号
    [POCKET_PET_HAPPY] = {FX_HAPPY, COUNT(FX_HAPPY), true},
    [POCKET_PET_OOPS] = {FX_OOPS, COUNT(FX_OOPS), false},
};
// The atlas stays intact; move only the head by at most two logical pixels.
static const uint8_t BREATH_OFFSETS[POCKET_PET_BREATH_FRAMES] = {0, 0, 1, 2, 2, 1, 1, 0};

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
    int by;
    int fx = x / 3;
    int fy = y / 3;

    if (x < 0 || y < 0 || x >= POCKET_PET_GRID || y >= POCKET_PET_GRID ||
        (unsigned)mood >= POCKET_PET_MOOD_COUNT) {
        return POCKET_PET_CLEAR;
    }
    look = &MOODS[mood];

    color = find(look->fx, look->fx_count, fx, fy);
    if (color != POCKET_PET_CLEAR) {
        return color;
    }
    if (mood == POCKET_PET_BUSY && fy == 1 && (fx == 7 || fx == 10 || fx == 13)) {
        // 头顶三个点依次点亮：第 0 帧亮一个，第 1 帧亮两个，第 2 帧全亮。
        return (unsigned)((fx - 7) / 3) <= frame % 3U ? POCKET_PET_ACCENT : POCKET_PET_GRAY;
    }

    by = y + (look->bobs ? BREATH_OFFSETS[frame % POCKET_PET_BREATH_FRAMES] : 0);
    if (by >= POCKET_PET_GRID) {
        return POCKET_PET_CLEAR;
    }
    return (pocket_pet_color_t)POCKET_WHALE_PIXELS[mood][by * POCKET_PET_GRID + x];
}

// Sway and float stay within the existing frame; the hop lands back at 0.
static const int8_t LISTEN_DX[8] = {0, 1, 2, 1, 0, -1, -2, -1};
static const int8_t THINK_DY[8] = {0, -1, -2, -3, -3, -2, -1, 0};
static const int8_t HOP_DY[POCKET_PET_HOP_TICKS] = {-2, -5, -6, -5, -2, 1, 1, 0};

void pocket_pet_offset(pocket_pet_motion_t motion, unsigned ticks, int *dx, int *dy)
{
    int x = 0;
    int y = 0;

    switch (motion) {
    case POCKET_MOTION_LISTEN:
        x = LISTEN_DX[ticks % 8U];
        break;
    case POCKET_MOTION_THINK:
        y = THINK_DY[ticks % 8U];
        break;
    case POCKET_MOTION_HOP:
        y = ticks < POCKET_PET_HOP_TICKS ? HOP_DY[ticks] : 0;
        break;
    case POCKET_MOTION_REST:
        break;
    }
    if (dx != NULL) *dx = x;
    if (dy != NULL) *dy = y;
}

bool pocket_pet_moving(pocket_pet_motion_t motion, unsigned ticks)
{
    return motion == POCKET_MOTION_LISTEN || motion == POCKET_MOTION_THINK ||
           (motion == POCKET_MOTION_HOP && ticks < POCKET_PET_HOP_TICKS);
}

uint32_t pocket_pet_rgb(pocket_pet_color_t color)
{
    static const uint32_t PALETTE[POCKET_PET_COLOR_COUNT] = {
        [POCKET_PET_CLEAR] = 0x000000,
        [POCKET_PET_OUTLINE] = 0xCFE3FF,
        [POCKET_PET_BODY] = 0xF6F8FF,
        [POCKET_PET_SHADE] = 0x2F5FC4,
        [POCKET_PET_SHINE] = 0xFFFFFF,
        [POCKET_PET_CHEEK] = 0xF09696,
        [POCKET_PET_EYE] = 0x1B2A55,
        [POCKET_PET_GRAY] = 0x7D786F,
        [POCKET_PET_ACCENT] = 0x7CC8F2,
        [POCKET_PET_WATER] = 0x78BEF0,
        [POCKET_PET_ALERT] = 0xEDB95E,
        [POCKET_PET_HEART] = 0xEA6F63,
        [POCKET_PET_SKIN] = 0xFFE6D6,
        [POCKET_PET_DRESS] = 0x22356E,
        [POCKET_PET_FIN] = 0x7CC8F2,
    };

    if ((unsigned)color >= POCKET_PET_COLOR_COUNT) return 0;
    if ((unsigned)color >= POCKET_PET_SOURCE_FIRST)
        return POCKET_WHALE_PALETTE[color - POCKET_PET_SOURCE_FIRST];
    return PALETTE[color];
}
