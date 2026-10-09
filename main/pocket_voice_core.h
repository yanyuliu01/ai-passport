// main/pocket_voice_core.h —— 对讲功能里不依赖硬件的部分：
// IMA ADPCM 编解码、语音帧打包、发送前的帧队列。
// 不依赖 ESP-IDF / LVGL，可在主机上直接测试。
//
// 线上格式（设备 → 手机，一条蓝牙通知就是一帧，不跨通知）：
//
//   [0xFF][seq][predictor 低字节][predictor 高字节][step index][ADPCM 数据…]
//
// * 0xFF 在 UTF-8 里永远不会出现，所以手机能把语音帧和 JSON 文本行分开：
//   首字节是 0xFF 的通知是语音帧，其余是文本。
// * seq 每帧加一，到 255 回到 0，手机据此统计丢帧。
// * predictor / step index 是编码这一帧【之前】的编码器状态。每帧自带状态，
//   丢掉一帧只损失这一帧的声音，后面的帧照常解码。
// * ADPCM 每字节两个采样，低 4 位在前；16 kHz 单声道，约 8 KB/s。
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define POCKET_VOICE_MAGIC 0xFFU
#define POCKET_VOICE_HEADER_BYTES 5U
#define POCKET_VOICE_SAMPLE_RATE 16000U
#define POCKET_VOICE_FRAME_MAX 244U
// 通知载荷小于这个值（MTU 没协商上来）就不发语音：帧太碎，开销比数据还大。
#define POCKET_VOICE_FRAME_MIN 20U

typedef struct {
    int16_t predictor;
    uint8_t index;
} pocket_adpcm_state_t;

// 编码 samples 个采样（必须是偶数），写出 samples / 2 字节，返回写出的字节数。
size_t pocket_adpcm_encode(pocket_adpcm_state_t *state, const int16_t *pcm, size_t samples,
                           uint8_t *out);
// 解码 bytes 字节，写出 bytes * 2 个采样，返回采样数。固件自己不用，测试用它核对编码。
size_t pocket_adpcm_decode(pocket_adpcm_state_t *state, const uint8_t *data, size_t bytes,
                           int16_t *pcm);

// 这一段采样有多响，0 到 100，给界面画音量条用。按峰值取对数：安静的房间接近 0，
// 正常说话在 40 到 80 之间，贴着麦克风喊接近 100。
uint8_t pocket_voice_level(const int16_t *pcm, size_t samples);

// 一帧能用的总字节数：通知载荷和 POCKET_VOICE_FRAME_MAX 取小；太小返回 0。
size_t pocket_voice_frame_size(size_t notify_payload);

typedef void (*pocket_voice_emit_t)(const uint8_t *frame, size_t length, void *context);

typedef struct {
    pocket_adpcm_state_t codec;
    uint8_t frame[POCKET_VOICE_FRAME_MAX];
    size_t frame_size;  // 一帧的总字节数（含帧头）
    size_t fill;        // 当前帧已写入的字节数；0 表示还没开始这一帧
    uint8_t seq;
    uint32_t frames;    // 已交出的帧数
} pocket_voice_packer_t;

// frame_size 来自 pocket_voice_frame_size()，为 0 时返回 false。
bool pocket_voice_packer_init(pocket_voice_packer_t *packer, size_t frame_size);
// 送入 PCM（samples 必须是偶数）；每攒满一帧调用一次 emit。
void pocket_voice_packer_push(pocket_voice_packer_t *packer, const int16_t *pcm, size_t samples,
                              pocket_voice_emit_t emit, void *context);
// 把没攒满的最后一帧交出去（没有数据时什么也不做）。
void pocket_voice_packer_flush(pocket_voice_packer_t *packer, pocket_voice_emit_t emit,
                               void *context);

// 发送前的帧队列：蓝牙一时发不动时先存着。存储由调用方提供。
// 每帧存成 [长度:1 字节][帧]。满了就丢新来的帧并计数：已经排队的声音在前，先保它。
typedef struct {
    uint8_t *data;
    size_t capacity;
    size_t head;   // 读位置
    size_t used;   // 已占用字节数
    uint32_t dropped;
} pocket_voice_fifo_t;

void pocket_voice_fifo_init(pocket_voice_fifo_t *fifo, uint8_t *storage, size_t capacity);
bool pocket_voice_fifo_push(pocket_voice_fifo_t *fifo, const uint8_t *frame, size_t length);
// 取出队首的一帧放进 out（至少 POCKET_VOICE_FRAME_MAX 字节）但不删除；队列空返回 0。
size_t pocket_voice_fifo_peek(const pocket_voice_fifo_t *fifo, uint8_t *out);
// 删除队首的一帧（peek 之后、确认发出去了再调用）。
void pocket_voice_fifo_pop(pocket_voice_fifo_t *fifo);
