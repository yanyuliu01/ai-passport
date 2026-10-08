// main/pocket_fonts.h —— 界面字库声明。字库源码在 assets/fonts/，由
// tools/gen_pocket_fonts.py 生成；覆盖范围记录在 assets/fonts/pocket_fonts.json。
#pragma once

#include "lvgl.h"

LV_FONT_DECLARE(pocket_font_14);      // 固定文案子集：按键提示、标签
LV_FONT_DECLARE(pocket_font_16);      // GB2312 全集 + ASCII：正文和电脑发来的任意文本
LV_FONT_DECLARE(pocket_font_22);      // 固定文案子集：标题、状态词
LV_FONT_DECLARE(pocket_font_num_44);  // 数字：配对码
