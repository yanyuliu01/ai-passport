#include "pocket_voice_core.h"

#include <string.h>

static const int16_t s_step_table[89] = {
    7,     8,     9,     10,    11,    12,    13,    14,    16,    17,    19,    21,    23,
    25,    28,    31,    34,    37,    41,    45,    50,    55,    60,    66,    73,    80,
    88,    97,    107,   118,   130,   143,   157,   173,   190,   209,   230,   253,   279,
    307,   337,   371,   408,   449,   494,   544,   598,   658,   724,   796,   876,   963,
    1060,  1166,  1282,  1411,  1552,  1707,  1878,  2066,  2272,  2499,  2749,  3024,  3327,
    3660,  4026,  4428,  4871,  5358,  5894,  6484,  7132,  7845,  8630,  9493,  10442, 11487,
    12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767,
};
static const int8_t s_index_table[16] = {-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8};

// 编码器和解码器共用的“按 4 位码更新状态”，保证两边永远算出同一个预测值。
static void adpcm_step(pocket_adpcm_state_t *state, unsigned code)
{
    int step = s_step_table[state->index];
    int diff = step >> 3;
    int predictor = state->predictor;
    int index = state->index;

    if (code & 4U) {
        diff += step;
    }
    if (code & 2U) {
        diff += step >> 1;
    }
    if (code & 1U) {
        diff += step >> 2;
    }
    predictor += (code & 8U) ? -diff : diff;
    if (predictor > 32767) {
        predictor = 32767;
    } else if (predictor < -32768) {
        predictor = -32768;
    }
    index += s_index_table[code & 15U];
    if (index < 0) {
        index = 0;
    } else if (index > 88) {
        index = 88;
    }
    state->predictor = (int16_t)predictor;
    state->index = (uint8_t)index;
}

static unsigned adpcm_code(const pocket_adpcm_state_t *state, int16_t sample)
{
    int step = s_step_table[state->index];
    int diff = (int)sample - (int)state->predictor;
    unsigned code = 0;

    if (diff < 0) {
        code = 8U;
        diff = -diff;
    }
    if (diff >= step) {
        code |= 4U;
        diff -= step;
    }
    if (diff >= step >> 1) {
        code |= 2U;
        diff -= step >> 1;
    }
    if (diff >= step >> 2) {
        code |= 1U;
    }
    return code;
}

static void adpcm_sanitize(pocket_adpcm_state_t *state)
{
    if (state->index > 88U) {
        state->index = 88U;
    }
}

size_t pocket_adpcm_encode(pocket_adpcm_state_t *state, const int16_t *pcm, size_t samples,
                           uint8_t *out)
{
    size_t bytes = samples / 2U;
    size_t index;

    if (state == NULL || pcm == NULL || out == NULL) {
        return 0;
    }
    adpcm_sanitize(state);
    for (index = 0; index < bytes; ++index) {
        unsigned low = adpcm_code(state, pcm[2U * index]);
        unsigned high;

        adpcm_step(state, low);
        high = adpcm_code(state, pcm[2U * index + 1U]);
        adpcm_step(state, high);
        out[index] = (uint8_t)(low | (high << 4));
    }
    return bytes;
}

size_t pocket_adpcm_decode(pocket_adpcm_state_t *state, const uint8_t *data, size_t bytes,
                           int16_t *pcm)
{
    size_t index;

    if (state == NULL || data == NULL || pcm == NULL) {
        return 0;
    }
    adpcm_sanitize(state);
    for (index = 0; index < bytes; ++index) {
        adpcm_step(state, data[index] & 15U);
        pcm[2U * index] = state->predictor;
        adpcm_step(state, (unsigned)data[index] >> 4);
        pcm[2U * index + 1U] = state->predictor;
    }
    return bytes * 2U;
}

size_t pocket_voice_frame_size(size_t notify_payload)
{
    if (notify_payload < POCKET_VOICE_FRAME_MIN) {
        return 0;
    }
    return notify_payload > POCKET_VOICE_FRAME_MAX ? POCKET_VOICE_FRAME_MAX : notify_payload;
}

bool pocket_voice_packer_init(pocket_voice_packer_t *packer, size_t frame_size)
{
    if (packer == NULL || frame_size < POCKET_VOICE_FRAME_MIN ||
        frame_size > POCKET_VOICE_FRAME_MAX) {
        return false;
    }
    memset(packer, 0, sizeof(*packer));
    packer->frame_size = frame_size;
    return true;
}

static void packer_emit(pocket_voice_packer_t *packer, pocket_voice_emit_t emit, void *context)
{
    if (packer->fill <= POCKET_VOICE_HEADER_BYTES) {
        packer->fill = 0;
        return;
    }
    if (emit != NULL) {
        emit(packer->frame, packer->fill, context);
    }
    ++packer->seq;
    ++packer->frames;
    packer->fill = 0;
}

void pocket_voice_packer_push(pocket_voice_packer_t *packer, const int16_t *pcm, size_t samples,
                              pocket_voice_emit_t emit, void *context)
{
    size_t offset = 0;

    if (packer == NULL || pcm == NULL || packer->frame_size == 0U) {
        return;
    }
    samples -= samples % 2U;
    while (offset < samples) {
        size_t room;
        size_t bytes;

        if (packer->fill == 0U) {
            packer->frame[0] = (uint8_t)POCKET_VOICE_MAGIC;
            packer->frame[1] = packer->seq;
            packer->frame[2] = (uint8_t)((uint16_t)packer->codec.predictor & 0xFFU);
            packer->frame[3] = (uint8_t)((uint16_t)packer->codec.predictor >> 8);
            packer->frame[4] = packer->codec.index;
            packer->fill = POCKET_VOICE_HEADER_BYTES;
        }
        room = packer->frame_size - packer->fill;
        bytes = (samples - offset) / 2U;
        if (bytes > room) {
            bytes = room;
        }
        packer->fill += pocket_adpcm_encode(&packer->codec, pcm + offset, bytes * 2U,
                                            packer->frame + packer->fill);
        offset += bytes * 2U;
        if (packer->fill == packer->frame_size) {
            packer_emit(packer, emit, context);
        }
    }
}

void pocket_voice_packer_flush(pocket_voice_packer_t *packer, pocket_voice_emit_t emit,
                               void *context)
{
    if (packer != NULL) {
        packer_emit(packer, emit, context);
    }
}

void pocket_voice_fifo_init(pocket_voice_fifo_t *fifo, uint8_t *storage, size_t capacity)
{
    if (fifo == NULL) {
        return;
    }
    memset(fifo, 0, sizeof(*fifo));
    fifo->data = storage;
    fifo->capacity = storage != NULL ? capacity : 0U;
}

static void fifo_write(pocket_voice_fifo_t *fifo, size_t position, const uint8_t *bytes,
                       size_t length)
{
    size_t first = fifo->capacity - position;

    if (first > length) {
        first = length;
    }
    memcpy(fifo->data + position, bytes, first);
    memcpy(fifo->data, bytes + first, length - first);
}

bool pocket_voice_fifo_push(pocket_voice_fifo_t *fifo, const uint8_t *frame, size_t length)
{
    uint8_t size;
    size_t tail;

    if (fifo == NULL || frame == NULL || length == 0U || length > POCKET_VOICE_FRAME_MAX) {
        return false;
    }
    if (fifo->capacity - fifo->used < length + 1U) {
        ++fifo->dropped;
        return false;
    }
    size = (uint8_t)length;
    tail = (fifo->head + fifo->used) % fifo->capacity;
    fifo_write(fifo, tail, &size, 1U);
    fifo_write(fifo, (tail + 1U) % fifo->capacity, frame, length);
    fifo->used += length + 1U;
    return true;
}

size_t pocket_voice_fifo_peek(const pocket_voice_fifo_t *fifo, uint8_t *out)
{
    size_t length;
    size_t position;
    size_t first;

    if (fifo == NULL || out == NULL || fifo->used == 0U) {
        return 0;
    }
    length = fifo->data[fifo->head];
    position = (fifo->head + 1U) % fifo->capacity;
    first = fifo->capacity - position;
    if (first > length) {
        first = length;
    }
    memcpy(out, fifo->data + position, first);
    memcpy(out + first, fifo->data, length - first);
    return length;
}

void pocket_voice_fifo_pop(pocket_voice_fifo_t *fifo)
{
    size_t length;

    if (fifo == NULL || fifo->used == 0U) {
        return;
    }
    length = (size_t)fifo->data[fifo->head] + 1U;
    fifo->head = (fifo->head + length) % fifo->capacity;
    fifo->used -= length;
}
