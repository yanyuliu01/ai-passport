// main/pocket_update_core.c —— 经蓝牙换固件里不依赖硬件的部分。说明见头文件。
#include "pocket_update_core.h"

#include <inttypes.h>
#include <stdio.h>
#include <string.h>

static const struct {
    const char *name;
    pocket_update_op_t op;
} OPS[] = {
    {"info", POCKET_UPDATE_OP_INFO},         {"begin", POCKET_UPDATE_OP_BEGIN},
    {"end", POCKET_UPDATE_OP_END},           {"abort", POCKET_UPDATE_OP_ABORT},
    {"confirm", POCKET_UPDATE_OP_CONFIRM},   {"rollback", POCKET_UPDATE_OP_ROLLBACK},
};

pocket_update_op_t pocket_update_op_from_name(const char *name)
{
    size_t index;

    if (name == NULL) {
        return POCKET_UPDATE_OP_NONE;
    }
    for (index = 0; index < sizeof(OPS) / sizeof(OPS[0]); ++index) {
        if (strcmp(name, OPS[index].name) == 0) {
            return OPS[index].op;
        }
    }
    return POCKET_UPDATE_OP_NONE;
}

const char *pocket_update_op_name(pocket_update_op_t op)
{
    size_t index;

    for (index = 0; index < sizeof(OPS) / sizeof(OPS[0]); ++index) {
        if (OPS[index].op == op) {
            return OPS[index].name;
        }
    }
    return "";
}

static int hex_value(char digit)
{
    if (digit >= '0' && digit <= '9') {
        return digit - '0';
    }
    if (digit >= 'a' && digit <= 'f') {
        return digit - 'a' + 10;
    }
    if (digit >= 'A' && digit <= 'F') {
        return digit - 'A' + 10;
    }
    return -1;
}

bool pocket_update_parse_sha256(const char *hex, uint8_t out[POCKET_UPDATE_SHA256_BYTES])
{
    size_t index;

    if (hex == NULL || out == NULL) {
        return false;
    }
    for (index = 0; index < POCKET_UPDATE_SHA256_BYTES; ++index) {
        int high = hex_value(hex[2U * index]);
        int low = high < 0 ? -1 : hex_value(hex[2U * index + 1U]);

        if (low < 0) {
            return false;
        }
        out[index] = (uint8_t)((high << 4) | low);
    }
    return hex[2U * POCKET_UPDATE_SHA256_BYTES] == '\0';
}

void pocket_update_hex(const uint8_t *bytes, size_t length, char *out)
{
    static const char DIGITS[] = "0123456789abcdef";
    size_t index;

    for (index = 0; index < length; ++index) {
        out[2U * index] = DIGITS[bytes[index] >> 4];
        out[2U * index + 1U] = DIGITS[bytes[index] & 0x0FU];
    }
    out[2U * length] = '\0';
}

bool pocket_update_is_frame(const uint8_t *data, size_t length)
{
    return data != NULL && length > 0U && data[0] == POCKET_UPDATE_MAGIC;
}

bool pocket_update_frame_parse(const uint8_t *frame, size_t length, uint32_t *offset,
                               const uint8_t **data, size_t *data_length)
{
    if (!pocket_update_is_frame(frame, length) || length <= POCKET_UPDATE_HEADER_BYTES ||
        offset == NULL || data == NULL || data_length == NULL) {
        return false;
    }
    *offset = (uint32_t)frame[1] | ((uint32_t)frame[2] << 8) | ((uint32_t)frame[3] << 16) |
              ((uint32_t)frame[4] << 24);
    *data = frame + POCKET_UPDATE_HEADER_BYTES;
    *data_length = length - POCKET_UPDATE_HEADER_BYTES;
    return true;
}

size_t pocket_update_chunk_size(size_t write_payload)
{
    if (write_payload < POCKET_UPDATE_FRAME_MIN) {
        return 0;
    }
    if (write_payload > POCKET_UPDATE_FRAME_MAX) {
        write_payload = POCKET_UPDATE_FRAME_MAX;
    }
    return write_payload - POCKET_UPDATE_HEADER_BYTES;
}

size_t pocket_update_frame_build(uint8_t *frame, size_t capacity, uint32_t offset,
                                 const uint8_t *data, size_t data_length)
{
    if (frame == NULL || data == NULL || data_length == 0U ||
        data_length > POCKET_UPDATE_FRAME_MAX - POCKET_UPDATE_HEADER_BYTES ||
        capacity < POCKET_UPDATE_HEADER_BYTES + data_length) {
        return 0;
    }
    frame[0] = POCKET_UPDATE_MAGIC;
    frame[1] = (uint8_t)offset;
    frame[2] = (uint8_t)(offset >> 8);
    frame[3] = (uint8_t)(offset >> 16);
    frame[4] = (uint8_t)(offset >> 24);
    memcpy(frame + POCKET_UPDATE_HEADER_BYTES, data, data_length);
    return POCKET_UPDATE_HEADER_BYTES + data_length;
}

void pocket_update_rx_begin(pocket_update_rx_t *rx, uint32_t size, uint32_t offset)
{
    if (rx == NULL) {
        return;
    }
    rx->size = size;
    rx->received = offset > size ? size : offset;
    rx->gap = false;
    rx->active = true;
}

void pocket_update_rx_stop(pocket_update_rx_t *rx)
{
    if (rx != NULL) {
        rx->active = false;
        rx->gap = false;
    }
}

pocket_update_frame_result_t pocket_update_rx_offer(pocket_update_rx_t *rx, uint32_t offset,
                                                    size_t length, size_t room)
{
    if (rx == NULL || !rx->active || length == 0U || offset > rx->size ||
        length > rx->size - offset) {
        return POCKET_UPDATE_FRAME_REJECT;
    }
    if (offset < rx->received) {
        // 手机重发时总是从我们报的那个偏移开始，所以落在前面的一定是还在路上的旧帧。
        return POCKET_UPDATE_FRAME_DUPLICATE;
    }
    if (offset > rx->received || length > room) {
        rx->gap = true;
        return POCKET_UPDATE_FRAME_GAP;
    }
    rx->received += (uint32_t)length;
    rx->gap = false;
    return POCKET_UPDATE_FRAME_ACCEPT;
}

void pocket_update_reporter_reset(pocket_update_reporter_t *reporter)
{
    if (reporter != NULL) {
        memset(reporter, 0, sizeof(*reporter));
    }
}

bool pocket_update_should_report(pocket_update_reporter_t *reporter, uint32_t got,
                                 uint32_t done, uint32_t size, bool gap, uint64_t now_ms)
{
    bool due;

    if (reporter == NULL) {
        return false;
    }
    due = !reporter->reported ||
          done - reporter->done >= POCKET_UPDATE_REPORT_BYTES ||
          (done == size && reporter->done != size) ||
          (gap && !reporter->gap) ||
          now_ms - reporter->reported_ms >= POCKET_UPDATE_REPORT_IDLE_MS;
    if (!due) {
        // 缺口补上了：下次再缺要重新报。
        if (!gap) {
            reporter->gap = false;
        }
        return false;
    }
    reporter->reported = true;
    reporter->done = done;
    reporter->got = got;
    reporter->gap = gap;
    reporter->reported_ms = now_ms;
    return true;
}

uint8_t pocket_update_percent(uint32_t done, uint32_t size)
{
    if (size == 0U) {
        return 0;
    }
    if (done >= size) {
        return 100;
    }
    return (uint8_t)(((uint64_t)done * 100U) / size);
}

// 只留下写进 JSON 字符串也不用转义的字符；其余换成下划线。
static void safe_copy(char *out, size_t size, const char *text)
{
    size_t index = 0;

    while (text != NULL && text[index] != '\0' && index + 1U < size) {
        char value = text[index];
        bool plain = (value >= '0' && value <= '9') || (value >= 'a' && value <= 'z') ||
                     (value >= 'A' && value <= 'Z') || value == '.' || value == '-' ||
                     value == '_' || value == '+';

        out[index] = plain ? value : '_';
        ++index;
    }
    out[index] = '\0';
}

static int finish(int written, size_t size)
{
    return written > 0 && (size_t)written < size ? written : 0;
}

int pocket_update_info_json(char *out, size_t size, const pocket_update_info_t *info)
{
    char build[POCKET_UPDATE_BUILD_HEX + 1U];
    char prev[POCKET_UPDATE_BUILD_HEX + 1U];
    char version[POCKET_UPDATE_VERSION_MAX];
    char slot[POCKET_UPDATE_SLOT_MAX];

    if (out == NULL || size == 0U || info == NULL) {
        return 0;
    }
    safe_copy(build, sizeof(build), info->build);
    safe_copy(prev, sizeof(prev), info->prev);
    safe_copy(version, sizeof(version), info->version);
    safe_copy(slot, sizeof(slot), info->slot);
    return finish(snprintf(out, size,
                           "{\"ack\":\"fw\",\"ok\":true,\"op\":\"info\",\"build\":\"%s\","
                           "\"ver\":\"%s\",\"state\":\"%s\",\"slot\":\"%s\",\"prev\":\"%s\","
                           "\"max\":%" PRIu32 ",\"heap\":%" PRIu32 ",\"low\":%" PRIu32
                           "}\n",
                           build, version, info->pending ? "pending" : "valid", slot, prev,
                           info->max_size, info->heap_free, info->heap_low),
                  size);
}

int pocket_update_begin_json(char *out, size_t size, uint32_t offset, size_t chunk)
{
    if (out == NULL || size == 0U || chunk == 0U) {
        return 0;
    }
    return finish(snprintf(out, size,
                           "{\"ack\":\"fw\",\"ok\":true,\"op\":\"begin\",\"offset\":%" PRIu32
                           ",\"chunk\":%u,\"window\":%u}\n",
                           offset, (unsigned)chunk, (unsigned)POCKET_UPDATE_WINDOW_BYTES),
                  size);
}

int pocket_update_ack_json(char *out, size_t size, pocket_update_op_t op, bool ok,
                           const char *error, bool with_got, uint32_t got)
{
    char reason[48];
    char tail[24] = "";
    const char *name = pocket_update_op_name(op);

    if (out == NULL || size == 0U || (!ok && (error == NULL || error[0] == '\0'))) {
        return 0;
    }
    if (with_got) {
        (void)snprintf(tail, sizeof(tail), ",\"got\":%" PRIu32, got);
    }
    if (ok) {
        return finish(snprintf(out, size, "{\"ack\":\"fw\",\"ok\":true,\"op\":\"%s\"%s}\n", name,
                               tail),
                      size);
    }
    // 错误原因是固件里写死的几个英文词，手机按它决定怎么办；空格也原样保留。
    {
        size_t index = 0;

        while (error[index] != '\0' && index + 1U < sizeof(reason)) {
            char value = error[index];

            reason[index] = (value == '"' || value == '\\' || (unsigned char)value < 0x20U)
                                ? '_'
                                : value;
            ++index;
        }
        reason[index] = '\0';
    }
    return finish(snprintf(out, size,
                           "{\"ack\":\"fw\",\"ok\":false,\"op\":\"%s\",\"error\":\"%s\"%s}\n",
                           name, reason, tail),
                  size);
}

int pocket_update_progress_json(char *out, size_t size, uint32_t got, uint32_t done,
                                bool rewind)
{
    if (out == NULL || size == 0U) {
        return 0;
    }
    return finish(snprintf(out, size,
                           "{\"evt\":\"fw\",\"got\":%" PRIu32 ",\"done\":%" PRIu32 "%s}\n", got,
                           done, rewind ? ",\"rewind\":true" : ""),
                  size);
}
