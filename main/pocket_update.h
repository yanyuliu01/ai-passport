// main/pocket_update.h —— 经蓝牙换固件：把手机发来的镜像写进另一个槽位，核对后切过去；
// 新固件重启后等手机“认可”，等不到就自己退回上一版。
// 线上格式见 pocket_update_core.h。
//
// 线程：命令从应用任务进来，数据帧从 NimBLE 任务进来，两边都只是把东西交给换固件
// 任务；闪存、核对和回话都在那个任务里做。所有入口都不阻塞。
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#include "pocket_update_core.h"

// 启动换固件任务；如果这次是新固件第一次启动，开始等“认可”的倒计时。
// 分区表里没有第二个槽位时也返回 ESP_OK：命令照常应答，只是 begin 会被拒绝。
esp_err_t pocket_update_init(void);
// 一条 {"cmd":"fw",…}。generation 是收到它的那条蓝牙连接的代号，回话发回同一条连接。
void pocket_update_command(const pocket_update_command_t *command,
                           uint32_t connection_generation);
// 一帧固件数据（一次蓝牙写入）。在 NimBLE 任务里调用，数据在返回前拷走。
void pocket_update_frame(const uint8_t *frame, size_t length, uint32_t connection_generation);

// 以下任何任务都可以调用。
pocket_update_phase_t pocket_update_phase(void);
uint8_t pocket_update_percent_now(void);
// 上一次没换成，而且还没有人问过：返回 true 并清掉这个记号。
bool pocket_update_take_failure(void);
// 到了该重启的时候（核对通过，或者要切回上一版）。应用任务看到后存好设置再重启。
bool pocket_update_restart_due(void);
