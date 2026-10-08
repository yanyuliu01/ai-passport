// Host tests for the hardware-independent voice code: ADPCM, frame packing, frame queue.
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "pocket_voice_core.h"

#define SAMPLES 16000

static int16_t s_pcm[SAMPLES];
static int16_t s_decoded[SAMPLES + 512];
static size_t s_decoded_count;
static unsigned s_frames;
static unsigned s_expected_seq;
static size_t s_last_length;
static pocket_voice_fifo_t *s_fifo;

static void make_speech_like(void)
{
    int index;

    /* A swept tone with an envelope: loud, quiet and silent stretches. */
    for (index = 0; index < SAMPLES; ++index) {
        double t = (double)index / 16000.0;
        double envelope = index < 2000 ? 0.0 : 0.5 + 0.5 * sin(2.0 * 3.14159265 * 3.0 * t);
        double tone = sin(2.0 * 3.14159265 * (200.0 + 600.0 * t) * t);

        s_pcm[index] = (int16_t)(envelope * tone * 12000.0);
    }
}

static double snr_db(const int16_t *reference, const int16_t *decoded, size_t count)
{
    double signal = 0.0;
    double noise = 0.0;
    size_t index;

    for (index = 0; index < count; ++index) {
        double error = (double)reference[index] - (double)decoded[index];

        signal += (double)reference[index] * (double)reference[index];
        noise += error * error;
    }
    return 10.0 * log10(signal / (noise + 1e-9));
}

static void test_adpcm_round_trip(void)
{
    static uint8_t encoded[SAMPLES / 2];
    pocket_adpcm_state_t encoder = {0};
    pocket_adpcm_state_t decoder = {0};

    assert(pocket_adpcm_encode(&encoder, s_pcm, SAMPLES, encoded) == SAMPLES / 2);
    assert(pocket_adpcm_decode(&decoder, encoded, SAMPLES / 2, s_decoded) == SAMPLES);
    /* Encoder and decoder must end in the same state, or frames would drift. */
    assert(encoder.predictor == decoder.predictor && encoder.index == decoder.index);
    assert(snr_db(s_pcm, s_decoded, SAMPLES) > 20.0);
}

static void test_adpcm_extremes(void)
{
    int16_t loud[64];
    int16_t back[64];
    uint8_t encoded[32];
    pocket_adpcm_state_t encoder = {.predictor = 0, .index = 200};  /* invalid index */
    pocket_adpcm_state_t decoder = {.predictor = 0, .index = 88};
    int index;

    for (index = 0; index < 64; ++index) {
        loud[index] = index % 2 ? 32767 : -32768;
    }
    assert(pocket_adpcm_encode(&encoder, loud, 64, encoded) == 32);
    assert(encoder.index <= 88);
    assert(pocket_adpcm_decode(&decoder, encoded, 32, back) == 64);
    assert(encoder.predictor == decoder.predictor);
    assert(pocket_adpcm_encode(NULL, loud, 64, encoded) == 0);
    assert(pocket_adpcm_encode(&encoder, loud, 1, encoded) == 0);  /* odd tail is not encoded */
}

/* Decode a frame the way the phone does: state from the header, then the data. */
static void collect_frame(const uint8_t *frame, size_t length, void *context)
{
    pocket_adpcm_state_t state;

    (void)context;
    assert(length > POCKET_VOICE_HEADER_BYTES && length <= POCKET_VOICE_FRAME_MAX);
    assert(frame[0] == POCKET_VOICE_MAGIC);
    assert(frame[1] == (uint8_t)s_expected_seq);
    ++s_expected_seq;
    state.predictor = (int16_t)(uint16_t)(frame[2] | (frame[3] << 8));
    state.index = frame[4];
    s_decoded_count += pocket_adpcm_decode(&state, frame + POCKET_VOICE_HEADER_BYTES,
                                           length - POCKET_VOICE_HEADER_BYTES,
                                           s_decoded + s_decoded_count);
    ++s_frames;
    s_last_length = length;
}

static void run_packer(size_t frame_size, size_t chunk)
{
    pocket_voice_packer_t packer;
    size_t offset;

    s_decoded_count = 0;
    s_frames = 0;
    s_expected_seq = 0;
    assert(pocket_voice_packer_init(&packer, frame_size));
    for (offset = 0; offset < SAMPLES; offset += chunk) {
        size_t count = SAMPLES - offset < chunk ? SAMPLES - offset : chunk;

        pocket_voice_packer_push(&packer, s_pcm + offset, count, collect_frame, NULL);
    }
    pocket_voice_packer_flush(&packer, collect_frame, NULL);
    pocket_voice_packer_flush(&packer, collect_frame, NULL);  /* nothing left: no extra frame */
    assert(packer.frames == s_frames);
    assert(s_decoded_count == SAMPLES);
    assert(snr_db(s_pcm, s_decoded, SAMPLES) > 20.0);
}

static void test_packer(void)
{
    assert(pocket_voice_frame_size(19) == 0);
    assert(pocket_voice_frame_size(20) == 20);
    assert(pocket_voice_frame_size(244) == 244);
    assert(pocket_voice_frame_size(509) == 244);
    assert(!pocket_voice_packer_init(NULL, 244));

    run_packer(244, 320);  /* 20 ms chunks, full-size frames */
    /* 8000 ADPCM bytes at 239 per frame: 33 full frames and one short one. */
    assert(s_frames == 34 && s_last_length == 5 + 8000 - 33 * 239);
    run_packer(244, 2);    /* two samples at a time */
    assert(s_frames == 34);
    run_packer(20, 320);   /* smallest link: frames span and split chunks */
    assert(s_frames == (8000 + 14) / 15);
    run_packer(101, 222);  /* odd sizes on both sides */
}

/* Losing a frame must only lose that frame: every later sample still matches. */
static void drop_third_frame(const uint8_t *frame, size_t length, void *context)
{
    unsigned *seen = context;
    pocket_adpcm_state_t state;
    size_t data = length - POCKET_VOICE_HEADER_BYTES;

    if ((*seen)++ == 2U) {
        memset(s_decoded + s_decoded_count, 0, data * 2U * sizeof(int16_t));
        s_decoded_count += data * 2U;
        return;
    }
    state.predictor = (int16_t)(uint16_t)(frame[2] | (frame[3] << 8));
    state.index = frame[4];
    s_decoded_count += pocket_adpcm_decode(&state, frame + POCKET_VOICE_HEADER_BYTES, data,
                                           s_decoded + s_decoded_count);
}

static void test_frame_loss_does_not_spread(void)
{
    static int16_t reference[SAMPLES + 512];
    pocket_voice_packer_t packer;
    unsigned seen = 0;
    size_t after = 3U * 239U * 2U;

    run_packer(244, 320);
    memcpy(reference, s_decoded, sizeof(reference));
    s_decoded_count = 0;
    assert(pocket_voice_packer_init(&packer, 244));
    pocket_voice_packer_push(&packer, s_pcm, SAMPLES, drop_third_frame, &seen);
    pocket_voice_packer_flush(&packer, drop_third_frame, &seen);
    assert(s_decoded_count == SAMPLES);
    assert(memcmp(reference + after, s_decoded + after,
                  (SAMPLES - after) * sizeof(int16_t)) == 0);
}

static void queue_frame(const uint8_t *frame, size_t length, void *context)
{
    (void)context;
    (void)pocket_voice_fifo_push(s_fifo, frame, length);
}

static void test_fifo(void)
{
    static uint8_t storage[1000];
    uint8_t frame[POCKET_VOICE_FRAME_MAX];
    uint8_t out[POCKET_VOICE_FRAME_MAX];
    pocket_voice_fifo_t fifo;
    pocket_voice_packer_t packer;
    unsigned round;
    size_t length;

    pocket_voice_fifo_init(&fifo, storage, sizeof(storage));
    assert(pocket_voice_fifo_peek(&fifo, out) == 0);
    pocket_voice_fifo_pop(&fifo);  /* popping an empty queue is harmless */

    /* Push and pop frames of varying length many times so the ring wraps repeatedly. */
    for (round = 0; round < 500; ++round) {
        size_t size = 6U + (round * 37U) % 239U;
        size_t index;

        for (index = 0; index < size; ++index) {
            frame[index] = (uint8_t)(round + index);
        }
        assert(pocket_voice_fifo_push(&fifo, frame, size));
        if (round % 3U == 0U) {
            assert(pocket_voice_fifo_push(&fifo, frame, size));
            assert(pocket_voice_fifo_peek(&fifo, out) == size);
            pocket_voice_fifo_pop(&fifo);
        }
        assert(pocket_voice_fifo_peek(&fifo, out) == size);
        assert(memcmp(out, frame, size) == 0);
        pocket_voice_fifo_pop(&fifo);
        assert(fifo.used == 0);
    }
    assert(fifo.dropped == 0);

    /* When full, new frames are refused and counted; queued ones stay intact. */
    memset(frame, 0xAB, sizeof(frame));
    assert(pocket_voice_fifo_push(&fifo, frame, 244));
    assert(pocket_voice_fifo_push(&fifo, frame, 244));
    assert(pocket_voice_fifo_push(&fifo, frame, 244));
    assert(pocket_voice_fifo_push(&fifo, frame, 244));
    assert(!pocket_voice_fifo_push(&fifo, frame, 244));
    assert(fifo.dropped == 1 && fifo.used == 4 * 245);
    assert(pocket_voice_fifo_push(&fifo, frame, 19));  /* exactly fills the ring */
    assert(fifo.used == sizeof(storage));
    assert(!pocket_voice_fifo_push(&fifo, frame, 6));
    assert(!pocket_voice_fifo_push(&fifo, frame, 0) && !pocket_voice_fifo_push(&fifo, frame, 245));
    for (round = 0; round < 4; ++round) {
        assert(pocket_voice_fifo_peek(&fifo, out) == 244 && out[243] == 0xAB);
        pocket_voice_fifo_pop(&fifo);
    }
    assert(pocket_voice_fifo_peek(&fifo, out) == 19);
    pocket_voice_fifo_pop(&fifo);
    assert(fifo.used == 0 && fifo.dropped == 2);

    /* Packer -> queue -> decode keeps every sample when the queue is drained in time. */
    s_fifo = &fifo;
    s_decoded_count = 0;
    s_frames = 0;
    s_expected_seq = 0;
    assert(pocket_voice_packer_init(&packer, 244));
    for (round = 0; round < SAMPLES / 320; ++round) {
        pocket_voice_packer_push(&packer, s_pcm + round * 320, 320, queue_frame, NULL);
        while ((length = pocket_voice_fifo_peek(&fifo, out)) != 0U) {
            collect_frame(out, length, NULL);
            pocket_voice_fifo_pop(&fifo);
        }
    }
    pocket_voice_packer_flush(&packer, queue_frame, NULL);
    while ((length = pocket_voice_fifo_peek(&fifo, out)) != 0U) {
        collect_frame(out, length, NULL);
        pocket_voice_fifo_pop(&fifo);
    }
    assert(s_decoded_count == SAMPLES && fifo.dropped == 2);
}

int main(void)
{
    make_speech_like();
    test_adpcm_round_trip();
    test_adpcm_extremes();
    test_packer();
    test_frame_loss_does_not_spread();
    test_fifo();
    puts("Pocket voice core tests: PASS");
    return 0;
}
