/* Host preview configuration: mirrors the firmware where it affects rendering
 * (RGB565, UTF-8, placeholders on) and keeps everything else minimal. */
#ifndef LV_CONF_H
#define LV_CONF_H

#define LV_COLOR_DEPTH 16
#define LV_USE_STDLIB_MALLOC LV_STDLIB_BUILTIN
#define LV_USE_STDLIB_STRING LV_STDLIB_BUILTIN
#define LV_USE_STDLIB_SPRINTF LV_STDLIB_BUILTIN
/* Override with -DPREVIEW_LV_MEM_KB=48 to check that the UI fits the firmware's
 * LVGL pool even with 64-bit host pointers (a conservative bound for the device). */
#ifndef PREVIEW_LV_MEM_KB
#define PREVIEW_LV_MEM_KB 512
#endif
#define LV_MEM_SIZE (PREVIEW_LV_MEM_KB * 1024U)
#define LV_USE_OS LV_OS_NONE
#define LV_DEF_REFR_PERIOD 20
#define LV_USE_LOG 0
#define LV_USE_ASSERT_NULL 1
#define LV_USE_ASSERT_MALLOC 1

#define LV_FONT_MONTSERRAT_14 1
#define LV_FONT_DEFAULT &lv_font_montserrat_14
#define LV_TXT_ENC LV_TXT_ENC_UTF8
#define LV_USE_FONT_PLACEHOLDER 1

#define LV_BUILD_EXAMPLES 0
#define LV_BUILD_DEMOS 0

#endif
