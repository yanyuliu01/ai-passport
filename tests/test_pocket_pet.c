#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "pocket_pet.h"

static unsigned count(pocket_pet_mood_t mood, unsigned frame, pocket_pet_color_t color)
{
    unsigned total = 0;
    int x;
    int y;

    for (y = 0; y < POCKET_PET_GRID; ++y) {
        for (x = 0; x < POCKET_PET_GRID; ++x) {
            if (pocket_pet_pixel(mood, frame, x, y) == color) {
                ++total;
            }
        }
    }
    return total;
}

static void snapshot(pocket_pet_mood_t mood, unsigned frame,
                     unsigned char out[POCKET_PET_GRID * POCKET_PET_GRID])
{
    int x;
    int y;

    for (y = 0; y < POCKET_PET_GRID; ++y) {
        for (x = 0; x < POCKET_PET_GRID; ++x) {
            out[y * POCKET_PET_GRID + x] = (unsigned char)pocket_pet_pixel(mood, frame, x, y);
        }
    }
}

static void test_out_of_range_is_clear(void)
{
    assert(pocket_pet_pixel(POCKET_PET_IDLE, 0, -1, 5) == POCKET_PET_CLEAR);
    assert(pocket_pet_pixel(POCKET_PET_IDLE, 0, 5, -1) == POCKET_PET_CLEAR);
    assert(pocket_pet_pixel(POCKET_PET_IDLE, 0, POCKET_PET_GRID, 5) == POCKET_PET_CLEAR);
    assert(pocket_pet_pixel(POCKET_PET_IDLE, 0, 5, POCKET_PET_GRID) == POCKET_PET_CLEAR);
    assert(pocket_pet_pixel(POCKET_PET_MOOD_COUNT, 0, 9, 9) == POCKET_PET_CLEAR);
    assert(pocket_pet_rgb(POCKET_PET_COLOR_COUNT) == 0);
    assert(pocket_pet_rgb(POCKET_PET_BODY) == 0xF0EEE6);
}

static void test_every_mood_is_a_whole_ghost_with_a_face(void)
{
    int mood;

    for (mood = 0; mood < POCKET_PET_MOOD_COUNT; ++mood) {
        unsigned frame;

        for (frame = 0; frame < 6; ++frame) {
            /* 身体和轮廓一直都在，眼睛至少有 4 个格子。 */
            assert(count((pocket_pet_mood_t)mood, frame, POCKET_PET_OUTLINE) >= 40);
            assert(count((pocket_pet_mood_t)mood, frame, POCKET_PET_BODY) >= 100);
            assert(count((pocket_pet_mood_t)mood, frame, POCKET_PET_EYE) >= 4);
        }
    }
}

static void test_moods_are_distinguishable(void)
{
    unsigned char left[POCKET_PET_GRID * POCKET_PET_GRID];
    unsigned char right[POCKET_PET_GRID * POCKET_PET_GRID];
    int a;
    int b;

    for (a = 0; a < POCKET_PET_MOOD_COUNT; ++a) {
        for (b = a + 1; b < POCKET_PET_MOOD_COUNT; ++b) {
            snapshot((pocket_pet_mood_t)a, 0, left);
            snapshot((pocket_pet_mood_t)b, 0, right);
            assert(memcmp(left, right, sizeof(left)) != 0);
        }
    }
    /* 每种情绪的标志性符号。 */
    assert(count(POCKET_PET_SLEEP, 0, POCKET_PET_GRAY) == 10);
    assert(count(POCKET_PET_ASK, 0, POCKET_PET_ALERT) == 10);
    assert(count(POCKET_PET_HAPPY, 0, POCKET_PET_HEART) == 11);
    assert(count(POCKET_PET_OOPS, 0, POCKET_PET_WATER) == 3);
    assert(count(POCKET_PET_IDLE, 0, POCKET_PET_ALERT) == 0);
    assert(count(POCKET_PET_IDLE, 0, POCKET_PET_HEART) == 0);
}

static void test_animation(void)
{
    unsigned char even[POCKET_PET_GRID * POCKET_PET_GRID];
    unsigned char odd[POCKET_PET_GRID * POCKET_PET_GRID];

    /* 忙碌：头顶的点 1→2→3 个依次点亮，然后循环。 */
    assert(count(POCKET_PET_BUSY, 0, POCKET_PET_ACCENT) == 1);
    assert(count(POCKET_PET_BUSY, 1, POCKET_PET_ACCENT) == 2);
    assert(count(POCKET_PET_BUSY, 2, POCKET_PET_ACCENT) == 3);
    assert(count(POCKET_PET_BUSY, 3, POCKET_PET_ACCENT) == 1);
    assert(count(POCKET_PET_BUSY, 0, POCKET_PET_GRAY) == 2);

    /* 待命：奇数帧整体上移一格，但格子总数不变。 */
    snapshot(POCKET_PET_IDLE, 0, even);
    snapshot(POCKET_PET_IDLE, 1, odd);
    assert(memcmp(even, odd, sizeof(even)) != 0);
    assert(memcmp(even + POCKET_PET_GRID, odd, sizeof(even) - POCKET_PET_GRID) == 0);
    snapshot(POCKET_PET_IDLE, 2, odd);
    assert(memcmp(even, odd, sizeof(even)) == 0);

    /* 睡觉和失败不动。 */
    snapshot(POCKET_PET_SLEEP, 0, even);
    snapshot(POCKET_PET_SLEEP, 1, odd);
    assert(memcmp(even, odd, sizeof(even)) == 0);
    snapshot(POCKET_PET_OOPS, 0, even);
    snapshot(POCKET_PET_OOPS, 5, odd);
    assert(memcmp(even, odd, sizeof(even)) == 0);
}

int main(void)
{
    test_out_of_range_is_clear();
    test_every_mood_is_a_whole_ghost_with_a_face();
    test_moods_are_distinguishable();
    test_animation();
    puts("pocket_pet: PASS");
    return 0;
}
