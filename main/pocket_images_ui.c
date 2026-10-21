#include "pocket_text.h"
#include "pocket_images_ui.h"
#include "lvgl.h"
#include "src/misc/cache/instance/lv_image_cache.h"
#include "pocket_fonts.h"
static lv_obj_t *panel, *image, *words, *hint;
static lv_image_dsc_t descriptor;
static const uint8_t *last;
void pocket_images_ui(bool open, const uint8_t *pixels, const char *caption)
{
    if (!panel) {
        panel = lv_obj_create(lv_layer_top());
        lv_obj_remove_style_all(panel);
        lv_obj_set_pos(panel, 0, 34);
        lv_obj_set_size(panel, 240, 286);
        lv_obj_set_style_bg_color(panel, lv_color_hex(0x15172a), 0);
        lv_obj_set_style_bg_opa(panel, LV_OPA_COVER, 0);
        image = lv_image_create(panel);
        lv_obj_set_pos(image, 88, 72);
        lv_image_set_scale(image, 512);
        words = lv_label_create(panel);
        lv_obj_set_style_text_font(words, &pocket_font_16, 0);
        lv_obj_set_style_text_color(words, lv_color_hex(0xf2f0fa), 0);
        lv_obj_set_width(words, 210);
        lv_obj_set_pos(words, 15, 202);
        lv_obj_set_style_text_align(words, LV_TEXT_ALIGN_CENTER, 0);
        hint = lv_label_create(lv_layer_top());
        lv_obj_set_style_text_font(hint, &pocket_font_16, 0);
        lv_obj_set_style_text_color(hint, lv_color_hex(0x86d19a), 0);
        lv_obj_set_style_bg_color(hint, lv_color_hex(0x15172a), 0);
        lv_obj_set_style_bg_opa(hint, LV_OPA_COVER, 0);
        lv_obj_set_pos(hint, 16, 287);
    }
    if (pixels != last) {
        lv_image_cache_drop(&descriptor);
        descriptor.header.magic = LV_IMAGE_HEADER_MAGIC;
        descriptor.header.cf = LV_COLOR_FORMAT_RGB565;
        descriptor.header.w = descriptor.header.h = 64;
        descriptor.header.stride = 128;
        descriptor.data_size = 8192;
        descriptor.data = pixels;
        if (pixels) lv_image_set_src(image, &descriptor);
        last = pixels;
    }
    if (pixels && open) lv_obj_remove_flag(image, LV_OBJ_FLAG_HIDDEN);
    else lv_obj_add_flag(image, LV_OBJ_FLAG_HIDDEN);
    if (open) {
        lv_label_set_text(words, pixels ? PT_IMAGE_READY : caption);
        lv_obj_remove_flag(panel, LV_OBJ_FLAG_HIDDEN);
    } else lv_obj_add_flag(panel, LV_OBJ_FLAG_HIDDEN);
    if (open) {
        lv_label_set_text(hint, PT_IMAGE_KEYS);
        lv_obj_remove_flag(hint, LV_OBJ_FLAG_HIDDEN);
    } else lv_obj_add_flag(hint, LV_OBJ_FLAG_HIDDEN);
}

void pocket_images_ui_detach(void)
{
    if (image) {
        lv_obj_add_flag(image, LV_OBJ_FLAG_HIDDEN);
        lv_image_cache_drop(&descriptor);
        lv_image_set_src(image, NULL);
    }
    descriptor.data = NULL;
    last = NULL;
}
