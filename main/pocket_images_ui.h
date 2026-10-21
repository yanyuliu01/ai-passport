#pragma once
#include <stdbool.h>
#include <stdint.h>
/* All calls require bsp_lvgl_lock(), including pixel-buffer replacement/free. */
void pocket_images_ui(bool open, const uint8_t *pixels, const char *caption);
void pocket_images_ui_detach(void);
