#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "buddy_types.h"
/* App-task owned; JSON is validated by buddy_protocol before this module runs.
 * Pixel storage is allocated only while viewing, at most 8,192 bytes. */
void pocket_images_focus(const char *card, bool enabled, uint32_t generation, uint64_t now);
bool pocket_images_key(const buddy_event_t *event);
bool pocket_images_line(const char *line, size_t length);
const uint8_t *pocket_images_pixels(void);
const char *pocket_images_caption(void);
bool pocket_images_open(void);
bool pocket_images_available(void);

#define POCKET_IMAGES_STATIC_BYTES 512U
typedef struct {
    size_t (*free_heap)(void);
    bool (*send)(const char *data, size_t length, uint32_t generation);
} pocket_images_ops_t;
void pocket_images_init(const pocket_images_ops_t *ops);
