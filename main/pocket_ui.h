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
// 滚动当前视图里的长文本：审批页为“向下翻一屏，到底回到开头”；连接指引页和第二屏
// （对话）按 delta 的方向翻。其他视图忽略。
// 返回值只对第二屏有意义：这件事已经翻到头、还往那个方向按时返回 -1（上）或 1（下），
// 调用方据此换到上一件 / 下一件（BUDDY_EVENT_CARD_STEP）；其余情况返回 0。
int pocket_ui_scroll(int delta);
