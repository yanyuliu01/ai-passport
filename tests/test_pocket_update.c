// Host tests for the hardware-independent half of the firmware updater: frames,
// receiving by offset, progress reports, the replies, and a whole transfer over a
// link that loses, repeats and reorders frames.
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "pocket_update_core.h"

static void test_ops_and_hex(void)
{
    uint8_t digest[POCKET_UPDATE_SHA256_BYTES];
    char hex[2 * POCKET_UPDATE_SHA256_BYTES + 1];
    const char *text = "00112233445566778899aabbccddeeff00112233445566778899AABBCCDDEEFF";

    assert(pocket_update_op_from_name("info") == POCKET_UPDATE_OP_INFO);
    assert(pocket_update_op_from_name("begin") == POCKET_UPDATE_OP_BEGIN);
    assert(pocket_update_op_from_name("end") == POCKET_UPDATE_OP_END);
    assert(pocket_update_op_from_name("abort") == POCKET_UPDATE_OP_ABORT);
    assert(pocket_update_op_from_name("confirm") == POCKET_UPDATE_OP_CONFIRM);
    assert(pocket_update_op_from_name("rollback") == POCKET_UPDATE_OP_ROLLBACK);
    assert(pocket_update_op_from_name("Begin") == POCKET_UPDATE_OP_NONE);
    assert(pocket_update_op_from_name("") == POCKET_UPDATE_OP_NONE);
    assert(pocket_update_op_from_name(NULL) == POCKET_UPDATE_OP_NONE);
    assert(strcmp(pocket_update_op_name(POCKET_UPDATE_OP_ROLLBACK), "rollback") == 0);
    assert(strcmp(pocket_update_op_name(POCKET_UPDATE_OP_NONE), "") == 0);

    assert(pocket_update_parse_sha256(text, digest));
    assert(digest[0] == 0x00 && digest[1] == 0x11 && digest[15] == 0xff && digest[31] == 0xff);
    pocket_update_hex(digest, sizeof(digest), hex);
    assert(strncmp(hex, "00112233445566778899aabbccddeeff00112233", 40) == 0);
    assert(strlen(hex) == 64);
    /* Too short, too long, and not hexadecimal are all refused. */
    assert(!pocket_update_parse_sha256("0011", digest));
    assert(!pocket_update_parse_sha256(
        "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff00", digest));
    assert(!pocket_update_parse_sha256(
        "0011223344556677889Zaabbccddeeff00112233445566778899aabbccddeeff", digest));
    assert(!pocket_update_parse_sha256(NULL, digest));
}

static void test_frames(void)
{
    uint8_t frame[POCKET_UPDATE_FRAME_MAX];
    uint8_t payload[POCKET_UPDATE_FRAME_MAX];
    const uint8_t *data;
    size_t data_length;
    uint32_t offset;
    size_t length;
    size_t index;

    for (index = 0; index < sizeof(payload); ++index) {
        payload[index] = (uint8_t)(index * 7U);
    }
    /* A write of 244 bytes carries 239 bytes of image; a default-MTU link is refused. */
    assert(pocket_update_chunk_size(244) == 239);
    assert(pocket_update_chunk_size(512) == 239);
    assert(pocket_update_chunk_size(100) == 95);
    assert(pocket_update_chunk_size(20) == 15);
    assert(pocket_update_chunk_size(19) == 0);
    assert(pocket_update_chunk_size(0) == 0);

    length = pocket_update_frame_build(frame, sizeof(frame), 0x01020304U, payload, 239);
    assert(length == 244);
    assert(frame[0] == POCKET_UPDATE_MAGIC);
    assert(frame[1] == 0x04 && frame[2] == 0x03 && frame[3] == 0x02 && frame[4] == 0x01);
    assert(pocket_update_is_frame(frame, length));
    assert(pocket_update_frame_parse(frame, length, &offset, &data, &data_length));
    assert(offset == 0x01020304U && data_length == 239 && memcmp(data, payload, 239) == 0);

    /* A header with nothing behind it is not a frame worth having. */
    assert(!pocket_update_frame_parse(frame, POCKET_UPDATE_HEADER_BYTES, &offset, &data,
                                      &data_length));
    assert(!pocket_update_frame_parse(frame, 3, &offset, &data, &data_length));
    /* JSON never starts with the magic byte, and the magic byte never appears in UTF-8. */
    assert(!pocket_update_is_frame((const uint8_t *)"{\"cmd\":\"fw\"}", 12));
    assert(!pocket_update_is_frame(frame, 0));
    assert(!pocket_update_is_frame(NULL, 4));
    assert(pocket_update_frame_build(frame, sizeof(frame), 0, payload, 240) == 0);
    assert(pocket_update_frame_build(frame, 10, 0, payload, 6) == 0);
    assert(pocket_update_frame_build(frame, sizeof(frame), 0, payload, 0) == 0);
}

static void test_receive_by_offset(void)
{
    pocket_update_rx_t rx = {0};

    /* Nothing is accepted before a transfer begins. */
    assert(pocket_update_rx_offer(&rx, 0, 100, 4096) == POCKET_UPDATE_FRAME_REJECT);

    pocket_update_rx_begin(&rx, 1000, 0);
    assert(rx.active && rx.received == 0 && !rx.gap);
    assert(pocket_update_rx_offer(&rx, 0, 239, 4096) == POCKET_UPDATE_FRAME_ACCEPT);
    assert(rx.received == 239);
    /* The same frame again, and an older one, are dropped without complaint. */
    assert(pocket_update_rx_offer(&rx, 0, 239, 4096) == POCKET_UPDATE_FRAME_DUPLICATE);
    assert(pocket_update_rx_offer(&rx, 100, 50, 4096) == POCKET_UPDATE_FRAME_DUPLICATE);
    assert(!rx.gap && rx.received == 239);
    /* A frame from further on means one in between was lost. */
    assert(pocket_update_rx_offer(&rx, 478, 239, 4096) == POCKET_UPDATE_FRAME_GAP);
    assert(rx.gap && rx.received == 239);
    /* The missing one arriving clears the complaint. */
    assert(pocket_update_rx_offer(&rx, 239, 239, 4096) == POCKET_UPDATE_FRAME_ACCEPT);
    assert(!rx.gap && rx.received == 478);
    /* No room in the buffer is the same as a gap: drop it and ask again. */
    assert(pocket_update_rx_offer(&rx, 478, 239, 238) == POCKET_UPDATE_FRAME_GAP);
    assert(rx.gap && rx.received == 478);
    assert(pocket_update_rx_offer(&rx, 478, 239, 239) == POCKET_UPDATE_FRAME_ACCEPT);
    /* Nothing may run past the declared size, and an empty frame is nothing. */
    assert(pocket_update_rx_offer(&rx, 717, 284, 4096) == POCKET_UPDATE_FRAME_REJECT);
    assert(pocket_update_rx_offer(&rx, 1001, 1, 4096) == POCKET_UPDATE_FRAME_REJECT);
    assert(pocket_update_rx_offer(&rx, 717, 0, 4096) == POCKET_UPDATE_FRAME_REJECT);
    assert(pocket_update_rx_offer(&rx, 0xFFFFFFF0U, 239, 4096) == POCKET_UPDATE_FRAME_REJECT);
    assert(pocket_update_rx_offer(&rx, 717, 283, 4096) == POCKET_UPDATE_FRAME_ACCEPT);
    assert(rx.received == 1000);

    pocket_update_rx_stop(&rx);
    assert(pocket_update_rx_offer(&rx, 0, 10, 4096) == POCKET_UPDATE_FRAME_REJECT);

    /* Picking up where an interrupted transfer stopped. */
    pocket_update_rx_begin(&rx, 1000, 478);
    assert(rx.received == 478);
    assert(pocket_update_rx_offer(&rx, 0, 239, 4096) == POCKET_UPDATE_FRAME_DUPLICATE);
    assert(pocket_update_rx_offer(&rx, 478, 239, 4096) == POCKET_UPDATE_FRAME_ACCEPT);
    pocket_update_rx_begin(&rx, 1000, 5000);
    assert(rx.received == 1000);
}

static void test_reports(void)
{
    pocket_update_reporter_t reporter;
    const uint32_t size = 100000;

    pocket_update_reporter_reset(&reporter);
    /* The first call always reports, so the host hears from the device at once. */
    assert(pocket_update_should_report(&reporter, 0, 0, size, false, 10));
    assert(!pocket_update_should_report(&reporter, 500, 500, size, false, 20));
    assert(!pocket_update_should_report(&reporter, 1023, 1023, size, false, 30));
    assert(pocket_update_should_report(&reporter, 1500, 1024, size, false, 40));
    assert(reporter.done == 1024 && reporter.got == 1500);
    /* A gap is reported once when it opens ... */
    assert(pocket_update_should_report(&reporter, 1500, 1100, size, true, 50));
    assert(!pocket_update_should_report(&reporter, 1500, 1200, size, true, 60));
    /* ... and again if it opens a second time after closing. */
    assert(!pocket_update_should_report(&reporter, 1700, 1300, size, false, 70));
    assert(pocket_update_should_report(&reporter, 1700, 1400, size, true, 80));
    /* With no progress at all there is still a report every second. */
    assert(!pocket_update_should_report(&reporter, 1700, 1400, size, true, 1079));
    assert(pocket_update_should_report(&reporter, 1700, 1400, size, true, 1080));
    /* The last byte is reported even though it is less than a full step. */
    assert(pocket_update_should_report(&reporter, size, size - 2000, size, false, 1100));
    assert(pocket_update_should_report(&reporter, size, size, size, false, 1110));
    assert(!pocket_update_should_report(&reporter, size, size, size, false, 1120));

    assert(pocket_update_percent(0, 1000) == 0);
    assert(pocket_update_percent(999, 1000) == 99);
    assert(pocket_update_percent(1000, 1000) == 100);
    assert(pocket_update_percent(5000, 1000) == 100);
    assert(pocket_update_percent(5, 0) == 0);
    assert(pocket_update_percent(0xFFFFFFF0U, 0xFFFFFFFFU) == 99);
}

static void test_replies(void)
{
    char line[POCKET_UPDATE_LINE_MAX];
    pocket_update_info_t info = {
        .build = "0123456789abcdef",
        .prev = "fedcba9876543210",
        .version = "v1.2-3-g403725c",
        .slot = "ota_0",
        .pending = true,
        .max_size = 4128768,
    };
    int length;

    length = pocket_update_info_json(line, sizeof(line), &info);
    assert(length > 0 && (size_t)length == strlen(line));
    assert(strcmp(line,
                  "{\"ack\":\"fw\",\"ok\":true,\"op\":\"info\",\"build\":\"0123456789abcdef\","
                  "\"ver\":\"v1.2-3-g403725c\",\"state\":\"pending\",\"slot\":\"ota_0\","
                  "\"prev\":\"fedcba9876543210\",\"max\":4128768}\n") == 0);
    /* Anything that would need escaping in JSON is replaced, never emitted. */
    snprintf(info.version, sizeof(info.version), "%s", "a\"b\\c d");
    info.prev[0] = '\0';
    info.pending = false;
    length = pocket_update_info_json(line, sizeof(line), &info);
    assert(length > 0);
    assert(strstr(line, "\"ver\":\"a_b_c_d\"") != NULL);
    assert(strstr(line, "\"state\":\"valid\"") != NULL);
    assert(strstr(line, "\"prev\":\"\"") != NULL);
    assert(pocket_update_info_json(line, 40, &info) == 0);
    assert(pocket_update_info_json(line, sizeof(line), NULL) == 0);

    assert(pocket_update_begin_json(line, sizeof(line), 4096, 239) > 0);
    assert(strcmp(line, "{\"ack\":\"fw\",\"ok\":true,\"op\":\"begin\",\"offset\":4096,"
                        "\"chunk\":239,\"window\":2048}\n") == 0);
    assert(pocket_update_begin_json(line, sizeof(line), 0, 0) == 0);

    assert(pocket_update_ack_json(line, sizeof(line), POCKET_UPDATE_OP_END, true, NULL, false,
                                  0) > 0);
    assert(strcmp(line, "{\"ack\":\"fw\",\"ok\":true,\"op\":\"end\"}\n") == 0);
    assert(pocket_update_ack_json(line, sizeof(line), POCKET_UPDATE_OP_END, false,
                                  "incomplete", true, 1195) > 0);
    assert(strcmp(line, "{\"ack\":\"fw\",\"ok\":false,\"op\":\"end\",\"error\":\"incomplete\","
                        "\"got\":1195}\n") == 0);
    assert(pocket_update_ack_json(line, sizeof(line), POCKET_UPDATE_OP_ROLLBACK, false,
                                  "no previous", false, 0) > 0);
    assert(strstr(line, "\"op\":\"rollback\",\"error\":\"no previous\"}") != NULL);
    /* A refusal always says why. */
    assert(pocket_update_ack_json(line, sizeof(line), POCKET_UPDATE_OP_BEGIN, false, NULL,
                                  false, 0) == 0);
    assert(pocket_update_ack_json(line, sizeof(line), POCKET_UPDATE_OP_BEGIN, false, "", false,
                                  0) == 0);

    assert(pocket_update_progress_json(line, sizeof(line), 4780, 4096, false) > 0);
    assert(strcmp(line, "{\"evt\":\"fw\",\"got\":4780,\"done\":4096}\n") == 0);
    assert(pocket_update_progress_json(line, sizeof(line), 4780, 4096, true) > 0);
    assert(strcmp(line, "{\"evt\":\"fw\",\"got\":4780,\"done\":4096,\"rewind\":true}\n") == 0);
    assert(pocket_update_progress_json(line, 8, 1, 1, false) == 0);
}

/* A whole transfer between a host that follows the documented rules and the
 * device's receive logic, across a link that drops, repeats and reorders
 * frames. The device side is the real core plus a byte buffer standing in for
 * the stream buffer and the flash. */

#define IMAGE_BYTES 70001U

typedef struct {
    pocket_update_rx_t rx;
    pocket_update_reporter_t reporter;
    uint8_t buffer[POCKET_UPDATE_BUFFER_BYTES];
    size_t buffered;
    uint8_t flash[IMAGE_BYTES];
    uint32_t done;
} fake_device_t;

static unsigned s_seed;

static unsigned roll(void)
{
    s_seed = s_seed * 1103515245U + 12345U;
    return (s_seed >> 16) & 0x7FFFU;
}

static void device_take(fake_device_t *device, const uint8_t *frame, size_t length)
{
    const uint8_t *data;
    size_t data_length;
    uint32_t offset;

    if (!pocket_update_frame_parse(frame, length, &offset, &data, &data_length)) {
        return;
    }
    if (pocket_update_rx_offer(&device->rx, offset, data_length,
                               sizeof(device->buffer) - device->buffered) ==
        POCKET_UPDATE_FRAME_ACCEPT) {
        memcpy(device->buffer + device->buffered, data, data_length);
        device->buffered += data_length;
    }
}

/* Writes up to limit bytes from the buffer to "flash". */
static void device_write(fake_device_t *device, size_t limit)
{
    size_t count = device->buffered < limit ? device->buffered : limit;

    memcpy(device->flash + device->done, device->buffer, count);
    memmove(device->buffer, device->buffer + count, device->buffered - count);
    device->buffered -= count;
    device->done += (uint32_t)count;
}

static void run_transfer(unsigned seed, unsigned loss_per_thousand, bool obey_window)
{
    static uint8_t image[IMAGE_BYTES];
    static fake_device_t device;
    const size_t chunk = pocket_update_chunk_size(244);
    uint8_t held[POCKET_UPDATE_FRAME_MAX];
    size_t held_length = 0;
    uint32_t next = 0;        /* host: next offset to send */
    uint32_t host_done = 0;   /* host: what the device last said is in flash */
    uint32_t last_got = 0;
    uint64_t now_ms = 0;
    unsigned stalls = 0;
    unsigned rounds = 0;
    size_t index;

    s_seed = seed;
    for (index = 0; index < IMAGE_BYTES; ++index) {
        image[index] = (uint8_t)(roll() & 0xFFU);
    }
    memset(&device, 0, sizeof(device));
    pocket_update_rx_begin(&device.rx, IMAGE_BYTES, 0);
    pocket_update_reporter_reset(&device.reporter);

    while (device.done < IMAGE_BYTES) {
        unsigned burst = 1U + roll() % 6U;

        assert(++rounds < 200000U);
        /* Host: send a few frames, never past done + window when it behaves. */
        while (burst-- > 0U && next < IMAGE_BYTES &&
               (!obey_window || next < host_done + POCKET_UPDATE_WINDOW_BYTES)) {
            uint8_t frame[POCKET_UPDATE_FRAME_MAX];
            size_t length = IMAGE_BYTES - next < chunk ? IMAGE_BYTES - next : chunk;
            size_t frame_length;

            if (obey_window && next + length > host_done + POCKET_UPDATE_WINDOW_BYTES) {
                length = host_done + POCKET_UPDATE_WINDOW_BYTES - next;
            }
            frame_length = pocket_update_frame_build(frame, sizeof(frame), next, image + next,
                                                     length);
            assert(frame_length == length + POCKET_UPDATE_HEADER_BYTES);
            next += (uint32_t)length;
            if (roll() % 1000U < loss_per_thousand) {
                continue; /* lost */
            }
            if (roll() % 1000U < loss_per_thousand && held_length == 0U) {
                /* Delayed: arrives after the next one. */
                memcpy(held, frame, frame_length);
                held_length = frame_length;
                continue;
            }
            device_take(&device, frame, frame_length);
            if (roll() % 1000U < loss_per_thousand) {
                device_take(&device, frame, frame_length); /* repeated */
            }
            if (held_length != 0U) {
                device_take(&device, held, held_length);
                held_length = 0;
            }
        }
        /* Device: write some of what it holds, then maybe report. */
        device_write(&device, 256U + roll() % 1024U);
        now_ms += 20U;
        if (pocket_update_should_report(&device.reporter, device.rx.received, device.done,
                                        IMAGE_BYTES, device.rx.gap, now_ms)) {
            /* Host: take the report. Asked to rewind, or two reports without the
             * device getting any further while frames are outstanding: resend
             * from where the device is. */
            bool rewind = device.rx.gap;

            host_done = device.done;
            if (!rewind && device.rx.received == last_got && next > device.rx.received) {
                rewind = ++stalls >= 2U;
            } else {
                stalls = 0;
            }
            if (rewind) {
                next = device.rx.received;
                stalls = 0;
            }
            last_got = device.rx.received;
        }
        if (next >= IMAGE_BYTES && device.rx.received < IMAGE_BYTES && device.buffered == 0U &&
            held_length == 0U) {
            /* Host sent everything and asked to finish; the device answers
             * "incomplete" with where it is. */
            next = device.rx.received;
        }
    }
    assert(device.rx.received == IMAGE_BYTES && device.buffered == 0U);
    assert(memcmp(device.flash, image, IMAGE_BYTES) == 0);
}

static void test_whole_transfers(void)
{
    unsigned seed;

    /* A clean link, then links that lose 2%, 10% and 30% of everything. */
    run_transfer(1, 0, true);
    for (seed = 2; seed < 40; ++seed) {
        run_transfer(seed, 20, true);
        run_transfer(seed + 100, 100, true);
        run_transfer(seed + 200, 300, true);
    }
    /* A host that ignores the window overruns the buffer; the image still
     * arrives intact, only slower. */
    for (seed = 500; seed < 520; ++seed) {
        run_transfer(seed, 0, false);
        run_transfer(seed, 50, false);
    }
}

int main(void)
{
    test_ops_and_hex();
    test_frames();
    test_receive_by_offset();
    test_reports();
    test_replies();
    test_whole_transfers();
    puts("Pocket update core tests: PASS");
    return 0;
}
