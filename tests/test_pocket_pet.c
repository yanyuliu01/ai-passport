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
    assert(pocket_pet_rgb(POCKET_PET_BODY) == 0xF6F8FF);
}

static void test_every_mood_preserves_the_whale_character(void)
{
    int mood;

    for (mood = 0; mood < POCKET_PET_MOOD_COUNT; ++mood) {
        unsigned frame;

        for (frame = 0; frame < POCKET_PET_BREATH_FRAMES; ++frame) {
            unsigned source_pixels = 0;
            unsigned blue_pixels = 0;
            unsigned skin_pixels = 0;
            for (int y = 0; y < POCKET_PET_GRID; ++y) {
                for (int x = 0; x < POCKET_PET_GRID; ++x) {
                    pocket_pet_color_t colour = pocket_pet_pixel((pocket_pet_mood_t)mood, frame, x, y);
                    assert(colour < POCKET_PET_COLOR_COUNT);
                    if (colour < POCKET_PET_SOURCE_FIRST) continue;
                    ++source_pixels;
                    uint32_t rgb = pocket_pet_rgb(colour);
                    unsigned red = rgb >> 16, green = (rgb >> 8) & 255, blue = rgb & 255;
                    if (blue > red + 30 && blue > green) ++blue_pixels;
                    if (red > 180 && green > 120 && red > blue + 15) ++skin_pixels;
                }
            }
            /* A complete blue-haired, peach-faced source portrait in every mood. */
            assert(source_pixels > 1200);
            assert(blue_pixels > 500);
            assert(skin_pixels > 150);
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
    assert(count(POCKET_PET_SLEEP, 0, POCKET_PET_GRAY) == 90);
    assert(count(POCKET_PET_ASK, 0, POCKET_PET_ALERT) == 0);  /* 感叹号已拿掉 */
    assert(count(POCKET_PET_HAPPY, 0, POCKET_PET_HEART) == 99);
    assert(count(POCKET_PET_OOPS, 0, POCKET_PET_WATER) == 27);
    assert(count(POCKET_PET_IDLE, 0, POCKET_PET_ALERT) == 0);
    assert(count(POCKET_PET_IDLE, 0, POCKET_PET_HEART) == 0);
}

static void test_animation(void)
{
    unsigned char even[POCKET_PET_GRID * POCKET_PET_GRID];
    unsigned char odd[POCKET_PET_GRID * POCKET_PET_GRID];

    /* 忙碌：头顶的点 1→2→3 个依次点亮，然后循环。 */
    assert(count(POCKET_PET_BUSY, 0, POCKET_PET_ACCENT) == 9);
    assert(count(POCKET_PET_BUSY, 1, POCKET_PET_ACCENT) == 18);
    assert(count(POCKET_PET_BUSY, 2, POCKET_PET_ACCENT) == 27);
    assert(count(POCKET_PET_BUSY, 3, POCKET_PET_ACCENT) == 9);
    assert(count(POCKET_PET_BUSY, 0, POCKET_PET_GRAY) == 18);

    /* 失败保持稳定，忙碌头像也不浮动。 */
    snapshot(POCKET_PET_OOPS, 0, even);
    for (unsigned frame = 1; frame < 2 * POCKET_PET_BREATH_FRAMES; ++frame) {
        snapshot(POCKET_PET_OOPS, frame, odd);
        assert(memcmp(even, odd, sizeof(even)) == 0);
    }
    snapshot(POCKET_PET_BUSY, 0, even);
    for (unsigned frame = 1; frame < 3; ++frame) {
        snapshot(POCKET_PET_BUSY, frame, odd);
        for (unsigned pixel = 0; pixel < sizeof(even); ++pixel) {
            if (even[pixel] >= POCKET_PET_SOURCE_FIRST) {
                assert(even[pixel] == odd[pixel]);
            }
        }
    }
}

static void test_breathing_cycle_preserves_pixels_and_status_marks(void)
{
    static const pocket_pet_mood_t moods[] = {
        POCKET_PET_SLEEP, POCKET_PET_IDLE, POCKET_PET_ASK, POCKET_PET_HAPPY,
    };
    unsigned char baseline[POCKET_PET_GRID * POCKET_PET_GRID];
    unsigned char current[POCKET_PET_GRID * POCKET_PET_GRID];

    assert(POCKET_PET_BREATH_FRAMES * 450U == 3600U);
    for (unsigned mood = 0; mood < sizeof(moods) / sizeof(moods[0]); ++mood) {
        unsigned reference_counts[POCKET_PET_COLOR_COUNT] = {0};
        int baseline_top = POCKET_PET_GRID;
        int highest_top = POCKET_PET_GRID;

        snapshot(moods[mood], 0, baseline);
        for (unsigned pixel = 0; pixel < sizeof(baseline); ++pixel) {
            ++reference_counts[baseline[pixel]];
            if (baseline[pixel] >= POCKET_PET_SOURCE_FIRST &&
                (int)(pixel / POCKET_PET_GRID) < baseline_top) {
                baseline_top = (int)(pixel / POCKET_PET_GRID);
            }
        }
        for (unsigned frame = 0; frame < 2 * POCKET_PET_BREATH_FRAMES; ++frame) {
            unsigned current_counts[POCKET_PET_COLOR_COUNT] = {0};
            int top = POCKET_PET_GRID;

            snapshot(moods[mood], frame, current);
            for (unsigned pixel = 0; pixel < sizeof(current); ++pixel) {
                ++current_counts[current[pixel]];
                if (current[pixel] >= POCKET_PET_SOURCE_FIRST &&
                    (int)(pixel / POCKET_PET_GRID) < top) {
                    top = (int)(pixel / POCKET_PET_GRID);
                }
                /* Zz, exclamation and heart marks stay at exactly the same coordinates. */
                if ((baseline[pixel] > POCKET_PET_CLEAR &&
                     baseline[pixel] < POCKET_PET_SOURCE_FIRST) ||
                    (current[pixel] > POCKET_PET_CLEAR &&
                     current[pixel] < POCKET_PET_SOURCE_FIRST)) {
                    assert(baseline[pixel] == current[pixel]);
                }
            }
            assert(memcmp(reference_counts, current_counts, sizeof(reference_counts)) == 0);
            assert(top <= baseline_top && baseline_top - top <= 2);
            if (top < highest_top) highest_top = top;
            if (frame % POCKET_PET_BREATH_FRAMES == 0) {
                assert(memcmp(baseline, current, sizeof(baseline)) == 0);
            }
            if (frame % POCKET_PET_BREATH_FRAMES == 3) {
                assert(memcmp(baseline, current, sizeof(baseline)) != 0);
            }
        }
        assert(baseline_top - highest_top == 2);
    }
}

static void test_motions_stay_small_and_the_hop_lands(void)
{
    int dx;
    int dy;

    for (unsigned tick = 0; tick < 32U; ++tick) {
        pocket_pet_offset(POCKET_MOTION_LISTEN, tick, &dx, &dy);
        assert(dy == 0 && dx >= -2 && dx <= 2);
        pocket_pet_offset(POCKET_MOTION_THINK, tick, &dx, &dy);
        assert(dx == 0 && dy <= 0 && dy >= -3);
        pocket_pet_offset(POCKET_MOTION_REST, tick, &dx, &dy);
        assert(dx == 0 && dy == 0);
    }
    pocket_pet_offset(POCKET_MOTION_HOP, 2, &dx, &dy);
    assert(dy < -4);
    pocket_pet_offset(POCKET_MOTION_HOP, POCKET_PET_HOP_TICKS, &dx, &dy);
    assert(dx == 0 && dy == 0);
    assert(pocket_pet_moving(POCKET_MOTION_HOP, 0));
    assert(!pocket_pet_moving(POCKET_MOTION_HOP, POCKET_PET_HOP_TICKS));
    assert(pocket_pet_moving(POCKET_MOTION_LISTEN, 100));
    assert(!pocket_pet_moving(POCKET_MOTION_REST, 0));
    assert(POCKET_PET_MOTION_MS * POCKET_PET_TICKS_PER_FRAME == 450U);
}

int main(void)
{
    test_motions_stay_small_and_the_hop_lands();
    test_out_of_range_is_clear();
    test_every_mood_preserves_the_whale_character();
    test_moods_are_distinguishable();
    test_animation();
    test_breathing_cycle_preserves_pixels_and_status_marks();
    puts("pocket_pet: PASS");
    return 0;
}
