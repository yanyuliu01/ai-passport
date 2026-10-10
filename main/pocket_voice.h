// main/pocket_voice.h —— 按住说话：采集麦克风，编码，经蓝牙发给手机。
//
// 一轮对讲的线上顺序（设备 → 手机）：
//   {"cmd":"voice","state":"start","rate":16000,"codec":"ima-adpcm","card":"c12"}\n
//   （card 只在按下时屏幕上有一件事时才有：这句话是对它说的）
//   语音帧 ×N（格式见 pocket_voice_core.h）
//   {"cmd":"voice","state":"end","frames":N,"dropped":D,"ms":M}\n
// 中途放弃时以 {"cmd":"voice","state":"cancel"}\n 结束，手机丢弃已收到的部分。
//
// 所有入口都不阻塞，可以在应用任务里直接调用；采集、编码和发送在自己的任务里。
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#include "buddy_types.h"

// 运行在语音任务里，只能入队或做同样轻的事。
typedef void (*pocket_voice_event_cb_t)(buddy_voice_status_t status,
                                        uint32_t connection_generation, void *context);

esp_err_t pocket_voice_init(pocket_voice_event_cb_t callback, void *context);
// 开始一轮。generation 是按下时的蓝牙连接代号，连接换了这一轮就作废。card 是按下
// 时屏幕上那件事的编号，没有就传 NULL 或空串；pin 表示这句话是在那件事里面说的
// （第三屏选中的那件），手机会让它一定归到这件事上。
// 上一轮还没收尾时返回 ESP_ERR_INVALID_STATE。
esp_err_t pocket_voice_start(uint32_t connection_generation, const char *card, bool pin);
// 结束当前这一轮。cancel 为 true 时丢弃录音，不交给手机。没有在录时什么也不做。
void pocket_voice_stop(bool cancel);
// 麦克风此刻的音量，0 到 100；没有在录时是 0。任何任务都可以调用。
uint8_t pocket_voice_level_now(void);
