// tests/update_interop/peer.c —— the device's side of a firmware transfer, on a host.
//
// Built from the firmware's own protocol parser (buddy_protocol.c) and update
// core (pocket_update_core.c), with a byte array standing in for the flash and
// a small loop standing in for the updater task. The phone app's transfer code
// (FirmwarePush.java) talks to it through Interop.java, so a change to the wire
// format on one side that the other side does not follow fails a test.
//
// One record per line on standard input; after each record every line the
// device would send is printed, then a line with a single dot.
//   L <json>      a text line from the host
//   F <hex>       one write that is a binary frame
//   T <ms> <n>    time passes; write up to n buffered bytes to flash, maybe report
//   Q <path>      write the flash contents to path and exit
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "buddy_protocol.h"
#include "pocket_update_core.h"

#define FLASH_BYTES (4U * 1024U * 1024U)

static uint8_t s_flash[FLASH_BYTES];
static uint8_t s_buffer[POCKET_UPDATE_BUFFER_BYTES];
static size_t s_buffered;
static pocket_update_rx_t s_rx;
static pocket_update_reporter_t s_reporter;
static uint32_t s_size;
static uint32_t s_done;
static uint64_t s_now_ms;

static void say(const char *line, int length)
{
    if (length > 0) {
        fputs(line, stdout);
    }
}

static void write_some(size_t limit)
{
    size_t count = s_buffered < limit ? s_buffered : limit;

    memcpy(s_flash + s_done, s_buffer, count);
    memmove(s_buffer, s_buffer + count, s_buffered - count);
    s_buffered -= count;
    s_done += (uint32_t)count;
}

static void on_line(const char *json)
{
    static buddy_event_t event;
    char line[POCKET_UPDATE_LINE_MAX];
    int parsed = buddy_protocol_parse(json, strlen(json), &event);

    if (parsed != (int)BUDDY_EVENT_FIRMWARE) {
        /* What the orchestrator answers to a command it cannot use. */
        say(line, buddy_protocol_command_ack_json(line, sizeof(line),
                                                  event.command.name[0] != '\0'
                                                      ? event.command.name
                                                      : "?",
                                                  false, "invalid request"));
        return;
    }
    switch (event.firmware.op) {
    case POCKET_UPDATE_OP_INFO: {
        pocket_update_info_t info = {
            .build = "00112233aabbccdd", .version = "interop", .slot = "ota_0",
            .max_size = FLASH_BYTES,
        };

        say(line, pocket_update_info_json(line, sizeof(line), &info));
        break;
    }
    case POCKET_UPDATE_OP_BEGIN:
        if (event.firmware.size < POCKET_UPDATE_IMAGE_MIN || event.firmware.size > FLASH_BYTES) {
            say(line, pocket_update_ack_json(line, sizeof(line), POCKET_UPDATE_OP_BEGIN, false,
                                             "size", false, 0));
            break;
        }
        s_size = event.firmware.size;
        s_done = 0;
        s_buffered = 0;
        pocket_update_rx_begin(&s_rx, s_size, 0);
        pocket_update_reporter_reset(&s_reporter);
        say(line, pocket_update_begin_json(line, sizeof(line), 0, pocket_update_chunk_size(244)));
        break;
    case POCKET_UPDATE_OP_END:
        write_some(sizeof(s_buffer));
        if (s_done != s_size || s_rx.received != s_size) {
            say(line, pocket_update_ack_json(line, sizeof(line), POCKET_UPDATE_OP_END, false,
                                             "incomplete", true, s_rx.received));
        } else {
            pocket_update_rx_stop(&s_rx);
            say(line, pocket_update_ack_json(line, sizeof(line), POCKET_UPDATE_OP_END, true, NULL,
                                             false, 0));
        }
        break;
    default:
        say(line, pocket_update_ack_json(line, sizeof(line), event.firmware.op, true, NULL, false,
                                         0));
        break;
    }
}

static void on_frame(const char *hex)
{
    uint8_t frame[POCKET_UPDATE_FRAME_MAX + 16];
    const uint8_t *data;
    size_t length = strlen(hex) / 2U;
    size_t data_length;
    uint32_t offset;
    size_t index;

    if (length > sizeof(frame)) {
        return;
    }
    for (index = 0; index < length; ++index) {
        unsigned value;

        if (sscanf(hex + 2U * index, "%2x", &value) != 1) {
            return;
        }
        frame[index] = (uint8_t)value;
    }
    if (length > POCKET_UPDATE_FRAME_MAX ||
        !pocket_update_frame_parse(frame, length, &offset, &data, &data_length)) {
        return;
    }
    if (pocket_update_rx_offer(&s_rx, offset, data_length, sizeof(s_buffer) - s_buffered) ==
        POCKET_UPDATE_FRAME_ACCEPT) {
        memcpy(s_buffer + s_buffered, data, data_length);
        s_buffered += data_length;
    }
}

int main(void)
{
    static char record[8192];

    while (fgets(record, sizeof(record), stdin) != NULL) {
        size_t length = strlen(record);

        while (length > 0U && (record[length - 1U] == '\n' || record[length - 1U] == '\r')) {
            record[--length] = '\0';
        }
        if (record[0] == 'L' && record[1] == ' ') {
            on_line(record + 2);
        } else if (record[0] == 'F' && record[1] == ' ') {
            on_frame(record + 2);
        } else if (record[0] == 'T' && record[1] == ' ') {
            unsigned long ms = 0;
            unsigned long limit = 0;
            char line[POCKET_UPDATE_LINE_MAX];

            (void)sscanf(record + 2, "%lu %lu", &ms, &limit);
            s_now_ms += ms;
            write_some(limit);
            if (s_rx.active &&
                pocket_update_should_report(&s_reporter, s_rx.received, s_done, s_size, s_rx.gap,
                                            s_now_ms)) {
                say(line, pocket_update_progress_json(line, sizeof(line), s_rx.received, s_done,
                                                      s_rx.gap));
            }
        } else if (record[0] == 'Q' && record[1] == ' ') {
            FILE *file = fopen(record + 2, "wb");

            if (file == NULL || fwrite(s_flash, 1, s_done, file) != s_done || fclose(file) != 0) {
                return 1;
            }
            return 0;
        }
        puts(".");
        fflush(stdout);
    }
    return 0;
}
