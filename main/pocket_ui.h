// main/pocket_ui.h —— Claude Pocket 的 LVGL 界面。
//
// 界面只消费 buddy_ui_snapshot_t，不直接碰蓝牙、存储或按键。
// 所有入口都要求调用方已持有 bsp_lvgl_lock()。
#pragma once

#include "buddy_types.h"

// 创建全部界面对象并加载屏幕。只调用一次。
void pocket_ui_init(void);
// 用最新快照刷新界面。可以每个 tick 调用：内容没变的控件不会重绘。
void pocket_ui_render(const buddy_ui_snapshot_t *snapshot);
// 滚动当前视图里的长文本：审批页为“向下翻一屏，到底回到开头”；首页的对话和连接
// 指引页按 delta 的方向滚动（各自一次滚几行），首页上 BUDDY_SCROLL_LATEST 表示回到
// 最新一轮。其他视图忽略。
void pocket_ui_scroll(int delta);
