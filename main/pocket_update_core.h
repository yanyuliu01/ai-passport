// main/pocket_update_core.h —— 经蓝牙换固件里不依赖硬件的部分：
// 数据帧的格式、按偏移收数据、什么时候向手机报进度、回话的文本。
// 不依赖 ESP-IDF / LVGL，可在主机上直接测试。
//
// 线上顺序（手机 → 设备的文本行，设备的回话都是一层的 JSON）：
//
//   {"cmd":"fw","op":"info"}
//     ← {"ack":"fw","ok":true,"op":"info","build":"…","ver":"…","state":"valid",
//        "slot":"ota_0","prev":"…","max":4128768}
//   {"cmd":"fw","op":"begin","size":N,"sha256":"<64 位十六进制>"}
//     ← {"ack":"fw","ok":true,"op":"begin","offset":K,"chunk":C,"window":W}
//   数据帧 ×N（见下）；设备每写入一段就报一次：
//     ← {"evt":"fw","got":G,"done":D}            G 下一个要的偏移，D 已写进闪存的字节数
//     ← {"evt":"fw","got":G,"done":D,"rewind":true}  中间缺了：请从 G 重新发
//   {"cmd":"fw","op":"end"}
//     ← {"ack":"fw","ok":true,"op":"end"}        校验通过，约一秒后重启进新固件
//     ← {"ack":"fw","ok":false,"op":"end","error":"incomplete","got":G}  还没收全，从 G 接着发
//   {"cmd":"fw","op":"abort"}                     放弃这一次
//   {"cmd":"fw","op":"confirm"}                   新固件重启后手机连上来说“认可”，否则到时间自动退回
//   {"cmd":"fw","op":"rollback"}                  切回另一个槽位里的上一版并重启
//
// 数据帧（手机 → 设备，一次蓝牙写入就是一帧，不跨写入）：
//
//   [0xFE][偏移，4 字节小端][固件数据…]
//
// * 0xFE 在 UTF-8 里永远不会出现，所以设备看一次写入的第一个字节就能把数据帧和
//   文本行分开；和语音帧用 0xFF 是同一个办法，方向相反。
// * 每帧自带偏移：重复的帧直接丢掉，缺了一段就报 rewind 让手机从缺的地方重发。
// * 手机发出去的数据不能超过 done + window：设备的缓冲区按这个大小准备。
// * 连接断了不算失败：重新连上后用同样的 size 和 sha256 再发一次 begin，
//   设备在 offset 里说从哪儿接着发。
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define POCKET_UPDATE_MAGIC 0xFEU
#define POCKET_UPDATE_HEADER_BYTES 5U
// 一次写入最多带这么多字节（含帧头），和语音帧、文本分片的上限一致。
#define POCKET_UPDATE_FRAME_MAX 244U
// 写入载荷小于这个值（MTU 没协商上来）就不收固件：太慢，开销比数据还大。
#define POCKET_UPDATE_FRAME_MIN 20U
// 手机最多可以领先“已写进闪存”这么多字节。设备没有多余的内存，窗口开得小；
// 闪存写得比蓝牙传得快，这个大小不会让手机等。
#define POCKET_UPDATE_WINDOW_BYTES 2048U
// 设备缓冲区的大小：一个窗口再加一帧，手机守规矩时不会满。
#define POCKET_UPDATE_BUFFER_BYTES (POCKET_UPDATE_WINDOW_BYTES + POCKET_UPDATE_FRAME_MAX)
// 每写进闪存这么多字节报一次进度。
#define POCKET_UPDATE_REPORT_BYTES 1024U
// 没有新进度时也每隔这么久报一次，手机据此发现卡住了。
#define POCKET_UPDATE_REPORT_IDLE_MS 1000U
// 应用镜像不可能比这个小；比它小的 size 一定是写错了。
#define POCKET_UPDATE_IMAGE_MIN 4096U
#define POCKET_UPDATE_SHA256_BYTES 32U
// build 是固件 ELF 的 SHA-256 的前 8 字节，写成 16 位十六进制。
#define POCKET_UPDATE_BUILD_HEX 16U
#define POCKET_UPDATE_VERSION_MAX 32U
#define POCKET_UPDATE_SLOT_MAX 17U
// 一行回话的上限。
#define POCKET_UPDATE_LINE_MAX 224U
// 新固件重启后，这么久没等到手机的“认可”就退回上一版。
#define POCKET_UPDATE_CONFIRM_MS (180U * 1000U)
// 校验通过后隔这么久重启：先让“好了”这句回话发出去。
#define POCKET_UPDATE_RESTART_MS 1500U
// 断线后把收了一半的固件留这么久，等手机回来接着发。
#define POCKET_UPDATE_RESUME_MS (5U * 60U * 1000U)
// 写在固件里的记号：电脑上的固件仓库靠它认出“这一版能经蓝牙再换下一版”。
#define POCKET_UPDATE_MARKER "pocket-fw-update/1"

typedef enum {
    POCKET_UPDATE_OP_NONE,
    POCKET_UPDATE_OP_INFO,
    POCKET_UPDATE_OP_BEGIN,
    POCKET_UPDATE_OP_END,
    POCKET_UPDATE_OP_ABORT,
    POCKET_UPDATE_OP_CONFIRM,
    POCKET_UPDATE_OP_ROLLBACK,
} pocket_update_op_t;

// 界面关心的阶段。
typedef enum {
    POCKET_UPDATE_IDLE,
    POCKET_UPDATE_RECEIVING,   // 正在收
    POCKET_UPDATE_PAUSED,      // 收到一半连接断了，等手机回来
    POCKET_UPDATE_CHECKING,    // 收全了，正在核对
    POCKET_UPDATE_RESTARTING,  // 核对通过，马上重启
} pocket_update_phase_t;

typedef struct {
    pocket_update_op_t op;
    uint32_t size;
    uint8_t sha256[POCKET_UPDATE_SHA256_BYTES];
} pocket_update_command_t;

// op 的名字（"info"、"begin"…）换成枚举；不认识返回 POCKET_UPDATE_OP_NONE。
pocket_update_op_t pocket_update_op_from_name(const char *name);
const char *pocket_update_op_name(pocket_update_op_t op);
// 64 位十六进制（大小写都行）换成 32 字节；格式不对返回 false。
bool pocket_update_parse_sha256(const char *hex, uint8_t out[POCKET_UPDATE_SHA256_BYTES]);
// bytes 写成小写十六进制，out 至少 2 * length + 1 字节。
void pocket_update_hex(const uint8_t *bytes, size_t length, char *out);

// ---- 数据帧 ----

// 这一次写入是不是数据帧（只看第一个字节）。
bool pocket_update_is_frame(const uint8_t *data, size_t length);
// 拆出偏移和数据；帧头不完整或者没有数据返回 false。
bool pocket_update_frame_parse(const uint8_t *frame, size_t length, uint32_t *offset,
                               const uint8_t **data, size_t *data_length);
// 一帧里能放多少字节的固件数据：写入载荷和 POCKET_UPDATE_FRAME_MAX 取小再减帧头；太小返回 0。
size_t pocket_update_chunk_size(size_t write_payload);
// 打一帧（主机测试和文档里的例子用；固件自己只拆不打）。返回帧长，放不下返回 0。
size_t pocket_update_frame_build(uint8_t *frame, size_t capacity, uint32_t offset,
                                 const uint8_t *data, size_t data_length);

// ---- 按偏移收数据 ----

typedef enum {
    POCKET_UPDATE_FRAME_ACCEPT,     // 正是下一段：调用方把数据收下
    POCKET_UPDATE_FRAME_DUPLICATE,  // 已经收过，丢掉
    POCKET_UPDATE_FRAME_GAP,        // 中间缺了，或者缓冲区放不下：丢掉并请手机重发
    POCKET_UPDATE_FRAME_REJECT,     // 没在收，或者超出了声明的大小
} pocket_update_frame_result_t;

typedef struct {
    uint32_t size;      // 整个镜像的字节数
    uint32_t received;  // 下一个要的偏移
    bool active;        // 正在收
    bool gap;           // 上一段按顺序的数据之后，来过对不上的帧
} pocket_update_rx_t;

// 开始或者接着收：下一段从 offset 开始。
void pocket_update_rx_begin(pocket_update_rx_t *rx, uint32_t size, uint32_t offset);
void pocket_update_rx_stop(pocket_update_rx_t *rx);
// room 是缓冲区现在还放得下的字节数。返回 ACCEPT 时 received 已经往前走了。
pocket_update_frame_result_t pocket_update_rx_offer(pocket_update_rx_t *rx, uint32_t offset,
                                                    size_t length, size_t room);

// ---- 向手机报进度 ----

typedef struct {
    uint32_t done;         // 上次报的“已写入”
    uint32_t got;          // 上次报的“下一个要的偏移”
    bool gap;              // 上次报的时候是不是在请求重发
    bool reported;         // 这一次传输里报过没有
    uint64_t reported_ms;
} pocket_update_reporter_t;

void pocket_update_reporter_reset(pocket_update_reporter_t *reporter);
// 现在该不该报一次。该报时把这次的数字记下来。
bool pocket_update_should_report(pocket_update_reporter_t *reporter, uint32_t got,
                                 uint32_t done, uint32_t size, bool gap, uint64_t now_ms);

// done / size 换成 0 到 100；size 为 0 返回 0。
uint8_t pocket_update_percent(uint32_t done, uint32_t size);

// ---- 回话 ----
// 都返回写入的字节数（含结尾的换行）；放不下或者参数不对返回 0。

typedef struct {
    char build[POCKET_UPDATE_BUILD_HEX + 1U];  // 正在运行的固件
    char prev[POCKET_UPDATE_BUILD_HEX + 1U];   // 另一个槽位里的固件；没有就是空串
    char version[POCKET_UPDATE_VERSION_MAX];
    char slot[POCKET_UPDATE_SLOT_MAX];
    bool pending;                              // 新固件还没被认可
    uint32_t max_size;                         // 一个槽位放得下多大的镜像
} pocket_update_info_t;

int pocket_update_info_json(char *out, size_t size, const pocket_update_info_t *info);
int pocket_update_begin_json(char *out, size_t size, uint32_t offset, size_t chunk);
// op 的应答。ok 为 false 时 error 必须非空；with_got 为 true 时带上 "got"。
int pocket_update_ack_json(char *out, size_t size, pocket_update_op_t op, bool ok,
                           const char *error, bool with_got, uint32_t got);
int pocket_update_progress_json(char *out, size_t size, uint32_t got, uint32_t done,
                                bool rewind);
