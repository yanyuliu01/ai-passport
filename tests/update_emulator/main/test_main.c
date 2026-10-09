// Emulator test for the firmware updater (main/pocket_update.c of the product).
//
// The product's updater is compiled in unchanged. This file stands in for the
// Bluetooth link: it sends the commands and data frames a host would send and
// records what the updater answers. The image it transfers is this running
// application itself, copied into the other slot.
//
// Steps, remembered in NVS across restarts:
//   0  running ota_0: requests that must be refused; a complete transfer whose
//      hash does not match (refused, nothing switches); then a transfer with
//      dropped, repeated and reordered frames and one interruption that is
//      resumed; expect restart into ota_1
//   1  running ota_1, pending: do NOT confirm; expect the updater to roll back
//   2  running ota_0 again: transfer again, expect restart into ota_1
//   3  running ota_1, pending: confirm, then ask for "rollback" (switch slots)
//   4  running ota_0, pending: confirm; all done
//
// run.sh restarts the emulator when it hangs. A step whose transfer was cut
// short that way is done again. A pending image that is restarted before it
// was confirmed is rolled back by the bootloader; in step 1 that counts as the
// step's outcome (the deadline in the updater is then not what went back).
#include <inttypes.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "esp_app_desc.h"
#include "esp_image_format.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_partition.h"
#include "esp_system.h"
#include "mbedtls/sha256.h"
#include "nvs.h"
#include "nvs_flash.h"

#include "buddy_ble.h"
#include "pocket_update.h"

#define GENERATION 7U
#undef LINE_MAX
#define LINE_MAX 256

static const char *const TAG = "ota-test";
static SemaphoreHandle_t s_lock;
static char s_last_ack[LINE_MAX];
static uint32_t s_got;
static uint32_t s_done;
static bool s_rewind;
static unsigned s_progress_lines;
static bool s_link_up = true;

// ---- the three things the updater needs from the Bluetooth module ----

esp_err_t buddy_ble_send_for_generation(const char *data, size_t length, uint32_t generation)
{
    char line[LINE_MAX];

    if (!s_link_up || generation != GENERATION || length >= sizeof(line)) {
        return ESP_ERR_INVALID_STATE;
    }
    memcpy(line, data, length);
    line[length] = '\0';
    xSemaphoreTake(s_lock, portMAX_DELAY);
    if (strstr(line, "\"evt\":\"fw\"") != NULL) {
        const char *got = strstr(line, "\"got\":");
        const char *done = strstr(line, "\"done\":");

        if (got != NULL && done != NULL) {
            s_got = (uint32_t)strtoul(got + 6, NULL, 10);
            s_done = (uint32_t)strtoul(done + 7, NULL, 10);
            s_rewind = strstr(line, "\"rewind\":true") != NULL;
            ++s_progress_lines;
        }
    } else {
        strlcpy(s_last_ack, line, sizeof(s_last_ack));
    }
    xSemaphoreGive(s_lock);
    return ESP_OK;
}

size_t buddy_ble_notify_payload_for_generation(uint32_t generation)
{
    return s_link_up && generation == GENERATION ? 244U : 0U;
}

bool buddy_ble_is_generation_secure(uint32_t generation)
{
    return s_link_up && generation == GENERATION;
}

// ---- helpers ----

static void fail(const char *what)
{
    ESP_LOGE(TAG, "TEST FAILED: %s", what);
    vTaskDelay(portMAX_DELAY);
}

static void command(pocket_update_op_t op, uint32_t size, const uint8_t *sha256)
{
    pocket_update_command_t request = {.op = op, .size = size};

    if (sha256 != NULL) {
        memcpy(request.sha256, sha256, sizeof(request.sha256));
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    s_last_ack[0] = '\0';
    xSemaphoreGive(s_lock);
    pocket_update_command(&request, GENERATION);
}

// Waits for an acknowledgement and copies it out; false after timeout_ms.
static bool wait_ack(char *out, size_t size, uint32_t timeout_ms)
{
    uint32_t waited;

    for (waited = 0; waited < timeout_ms; waited += 10U) {
        bool have;

        xSemaphoreTake(s_lock, portMAX_DELAY);
        have = s_last_ack[0] != '\0';
        if (have) {
            strlcpy(out, s_last_ack, size);
        }
        xSemaphoreGive(s_lock);
        if (have) {
            return true;
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    return false;
}

static uint32_t field(const char *line, const char *key)
{
    const char *found = strstr(line, key);

    return found == NULL ? 0U : (uint32_t)strtoul(found + strlen(key), NULL, 10);
}

static int number_read(const char *key)
{
    nvs_handle_t handle;
    int32_t value = 0;

    if (nvs_open("otatest", NVS_READWRITE, &handle) == ESP_OK) {
        (void)nvs_get_i32(handle, key, &value);
        nvs_close(handle);
    }
    return (int)value;
}

static void number_write(const char *key, int value)
{
    nvs_handle_t handle;

    ESP_ERROR_CHECK(nvs_open("otatest", NVS_READWRITE, &handle));
    ESP_ERROR_CHECK(nvs_set_i32(handle, key, value));
    ESP_ERROR_CHECK(nvs_commit(handle));
    nvs_close(handle);
}

static int step_read(void)
{
    return number_read("step");
}

static void step_write(int step)
{
    number_write("step", step);
}

static const char *state_name(const esp_partition_t *partition)
{
    esp_ota_img_states_t state;

    if (esp_ota_get_state_partition(partition, &state) != ESP_OK) {
        return "none";
    }
    switch (state) {
    case ESP_OTA_IMG_NEW: return "new";
    case ESP_OTA_IMG_PENDING_VERIFY: return "pending";
    case ESP_OTA_IMG_VALID: return "valid";
    case ESP_OTA_IMG_INVALID: return "invalid";
    case ESP_OTA_IMG_ABORTED: return "aborted";
    default: return "undefined";
    }
}

// Sends this running image to the updater the way a host would, misbehaving on
// purpose: every 37th frame is dropped, every 53rd sent twice, every 71st held
// back until after the next one, and once, a third of the way in, the link
// drops and the transfer is resumed with a second "begin". With wrong_hash the
// announced SHA-256 is not the image's: everything arrives, "end" must be
// refused, and the function returns with nothing switched.
static void transfer_self(bool with_interruption, bool wrong_hash)
{
    static uint8_t frame[POCKET_UPDATE_FRAME_MAX];
    static uint8_t held[POCKET_UPDATE_FRAME_MAX];
    const esp_partition_t *running = esp_ota_get_running_partition();
    const esp_partition_pos_t position = {.offset = running->address, .size = running->size};
    esp_image_metadata_t metadata;
    mbedtls_sha256_context sha;
    uint8_t digest[32];
    const void *mapped;
    const uint8_t *image;
    esp_partition_mmap_handle_t map_handle;
    char ack[LINE_MAX];
    uint32_t size;
    uint32_t next;
    uint32_t chunk;
    uint32_t window;
    uint32_t last_got = UINT32_MAX;
    unsigned sent_frames = 0;
    unsigned stalls = 0;
    unsigned rewinds = 0;
    size_t held_length = 0;
    bool interrupted = !with_interruption;

    ESP_ERROR_CHECK(esp_image_verify(ESP_IMAGE_VERIFY_SILENT, &position, &metadata));
    size = metadata.image_len;
    // Read the image through a memory map, so that the only flash operations
    // during the transfer are the updater's own.
    ESP_ERROR_CHECK(esp_partition_mmap(running, 0, (size + 0xFFFFU) & ~0xFFFFU,
                                       ESP_PARTITION_MMAP_DATA, &mapped, &map_handle));
    image = mapped;
    mbedtls_sha256_init(&sha);
    mbedtls_sha256_starts(&sha, 0);
    mbedtls_sha256_update(&sha, image, size);
    mbedtls_sha256_finish(&sha, digest);
    mbedtls_sha256_free(&sha);
    if (wrong_hash) {
        digest[0] ^= 0x01U;
    }
    ESP_LOGI(TAG, "transferring this image: %" PRIu32 " bytes from %s", size, running->label);

    command(POCKET_UPDATE_OP_BEGIN, size, digest);
    if (!wait_ack(ack, sizeof(ack), 5000) || strstr(ack, "\"ok\":true") == NULL) {
        ESP_LOGE(TAG, "begin answered: %s", ack);
        fail("begin refused");
    }
    ESP_LOGI(TAG, "begin: %s", ack);
    next = field(ack, "\"offset\":");
    chunk = field(ack, "\"chunk\":");
    window = field(ack, "\"window\":");
    if (next != 0U || chunk != 239U || window != POCKET_UPDATE_WINDOW_BYTES) {
        fail("unexpected begin parameters");
    }
    if (pocket_update_phase() != POCKET_UPDATE_RECEIVING) {
        fail("not receiving after begin");
    }
    // Forget what an earlier transfer in this run reported.
    xSemaphoreTake(s_lock, portMAX_DELAY);
    s_got = 0;
    s_done = 0;
    s_rewind = false;
    xSemaphoreGive(s_lock);

    for (;;) {
        uint32_t got;
        uint32_t done;
        bool rewind;

        xSemaphoreTake(s_lock, portMAX_DELAY);
        got = s_got;
        done = s_done;
        rewind = s_rewind;
        s_rewind = false;
        xSemaphoreGive(s_lock);

        if (rewind && got < next) {
            next = got;
            ++rewinds;
            stalls = 0;
        }
        if (!interrupted && done > size / 3U) {
            // The link drops. The updater should keep what it has and wait.
            interrupted = true;
            s_link_up = false;
            vTaskDelay(pdMS_TO_TICKS(600));
            if (pocket_update_phase() != POCKET_UPDATE_PAUSED) {
                fail("not paused after the link dropped");
            }
            s_link_up = true;
            command(POCKET_UPDATE_OP_BEGIN, size, digest);
            if (!wait_ack(ack, sizeof(ack), 5000) || strstr(ack, "\"ok\":true") == NULL) {
                fail("resume refused");
            }
            next = field(ack, "\"offset\":");
            ESP_LOGI(TAG, "resumed at %" PRIu32 " of %" PRIu32, next, size);
            if (next == 0U || next > size) {
                fail("resume did not continue from the middle");
            }
            xSemaphoreTake(s_lock, portMAX_DELAY);
            s_got = next;
            s_done = next;
            xSemaphoreGive(s_lock);
            continue;
        }
        if (next < size && next < done + window) {
            uint32_t room = done + window - next;
            size_t length = size - next < chunk ? size - next : chunk;
            size_t frame_length;

            if (length > room) {
                length = room;
            }
            frame_length =
                pocket_update_frame_build(frame, sizeof(frame), next, image + next, length);
            next += (uint32_t)length;
            ++sent_frames;
            if (sent_frames % 37U == 0U) {
                continue; /* lost */
            }
            if (sent_frames % 71U == 0U && held_length == 0U) {
                memcpy(held, frame, frame_length); /* arrives late */
                held_length = frame_length;
                continue;
            }
            pocket_update_frame(frame, frame_length, GENERATION);
            if (sent_frames % 53U == 0U) {
                pocket_update_frame(frame, frame_length, GENERATION); /* repeated */
            }
            if (held_length != 0U) {
                pocket_update_frame(held, held_length, GENERATION);
                held_length = 0;
            }
            continue;
        }
        if (done >= size) {
            break;
        }
        // Nothing to send right now: wait for the updater to report.
        vTaskDelay(pdMS_TO_TICKS(20));
        {
            static unsigned idle_rounds;
            if (++idle_rounds % 50U == 0U) {
                ESP_LOGW(TAG, "waiting: next=%" PRIu32 " got=%" PRIu32 " done=%" PRIu32
                              " stalls=%u phase=%d lines=%u", next, got, done, stalls,
                         (int)pocket_update_phase(), s_progress_lines);
            }
        }
        if (got == last_got && next > got) {
            if (++stalls >= 60U) { /* about a second with no movement */
                next = got;
                ++rewinds;
                stalls = 0;
            }
        } else {
            stalls = 0;
        }
        last_got = got;
        if (next >= size && got < size && stalls == 0U && done == got) {
            next = got; /* the tail was lost; send it again */
        }
    }
    ESP_LOGI(TAG, "all %" PRIu32 " bytes written: %u frames sent, %u rewinds, %u progress lines",
             size, sent_frames, rewinds, s_progress_lines);
    if (pocket_update_percent_now() != 100U) {
        fail("percent is not 100 when everything is written");
    }
    command(POCKET_UPDATE_OP_END, 0, NULL);
    if (wrong_hash) {
        if (!wait_ack(ack, sizeof(ack), 20000) || strstr(ack, "\"error\":\"sha256\"") == NULL) {
            ESP_LOGE(TAG, "end answered: %s", ack);
            fail("an image with the wrong hash was not refused");
        }
        ESP_LOGI(TAG, "wrong hash refused: %s", ack);
        if (pocket_update_phase() != POCKET_UPDATE_IDLE || !pocket_update_take_failure() ||
            esp_ota_get_boot_partition() != running) {
            fail("the refused image left something behind");
        }
        esp_partition_munmap(map_handle);
        return;
    }
    if (!wait_ack(ack, sizeof(ack), 20000) || strstr(ack, "\"op\":\"end\"") == NULL ||
        strstr(ack, "\"ok\":true") == NULL) {
        ESP_LOGE(TAG, "end answered: %s", ack);
        fail("end refused");
    }
    ESP_LOGI(TAG, "end: %s", ack);
    if (pocket_update_phase() != POCKET_UPDATE_RESTARTING) {
        fail("not restarting after a good end");
    }
    // A second image must not start now, and it is too late to abort.
    command(POCKET_UPDATE_OP_BEGIN, size, digest);
    if (!wait_ack(ack, sizeof(ack), 2000) || strstr(ack, "restarting") == NULL) {
        fail("begin while restarting was not refused");
    }
    while (!pocket_update_restart_due()) {
        vTaskDelay(pdMS_TO_TICKS(50));
    }
}

void app_main(void)
{
    const esp_partition_t *running = esp_ota_get_running_partition();
    const esp_partition_t *other = esp_ota_get_next_update_partition(NULL);
    char ack[LINE_MAX];
    int step;
    bool again = false;

    ESP_ERROR_CHECK(nvs_flash_init());
    s_lock = xSemaphoreCreateMutex();
    step = step_read();
    ESP_LOGI(TAG, "==== step %d: running %s (%s), other %s (%s)", step, running->label,
             state_name(running), other->label, state_name(other));
    ESP_ERROR_CHECK(pocket_update_init());

    command(POCKET_UPDATE_OP_INFO, 0, NULL);
    if (!wait_ack(ack, sizeof(ack), 3000)) {
        fail("no answer to info");
    }
    ESP_LOGI(TAG, "info: %s", ack);

    if ((step == 1 || step == 3) && strcmp(running->label, "ota_0") == 0 &&
        strstr(ack, "\"state\":\"valid\"") != NULL) {
        // The emulator was restarted in the middle of the previous step (see run.sh).
        bool other_failed = strcmp(state_name(other), "aborted") == 0 ||
                            strcmp(state_name(other), "invalid") == 0;

        if (step == 1 && other_failed) {
            // The new image did start, and the restart came before it was
            // confirmed: the bootloader has gone back, which is step 1's point.
            ESP_LOGW(TAG, "the unconfirmed image was restarted and the bootloader went back");
            step = 2;
            step_write(2);
        } else {
            // The transfer was not through, so nothing was switched: do it again.
            ESP_LOGW(TAG, "step %d did not finish before a restart; doing it again", step - 1);
            step -= 1;
            again = true;
        }
    }
    switch (step) {
    case 0:
    case 2:
        if (strcmp(running->label, "ota_0") != 0 || strstr(ack, "\"state\":\"valid\"") == NULL) {
            fail("expected to run a valid ota_0");
        }
        // (When the step is repeated the other slot holds half of a new image
        // and has no state at all.)
        if (step == 2 && !again && strcmp(state_name(other), "aborted") != 0 &&
            strcmp(state_name(other), "invalid") != 0) {
            fail("the unconfirmed image was not marked as failed");
        }
        if (step == 0) {
            /* Refusals that must not start anything. */
            uint8_t zero[32] = {0};

            command(POCKET_UPDATE_OP_BEGIN, 100, zero);
            if (!wait_ack(ack, sizeof(ack), 2000) || strstr(ack, "\"error\":\"size\"") == NULL) {
                fail("a tiny image was not refused");
            }
            command(POCKET_UPDATE_OP_BEGIN, 0x3F0001, zero);
            if (!wait_ack(ack, sizeof(ack), 2000) || strstr(ack, "\"error\":\"size\"") == NULL) {
                fail("an oversized image was not refused");
            }
            command(POCKET_UPDATE_OP_END, 0, NULL);
            if (!wait_ack(ack, sizeof(ack), 2000) || strstr(ack, "no transfer") == NULL) {
                fail("end without a transfer was not refused");
            }
            command(POCKET_UPDATE_OP_ROLLBACK, 0, NULL);
            if (!wait_ack(ack, sizeof(ack), 2000) || strstr(ack, "\"ok\":false") == NULL) {
                fail("rollback with an empty other slot was not refused");
            }
            ESP_LOGI(TAG, "rollback with nothing to go back to: %s", ack);
            // Once is enough: when this step is repeated after an emulator
            // restart, the transfer with the wrong hash is not sent again.
            if (number_read("wronghash") == 0) {
                transfer_self(false, true);
                number_write("wronghash", 1);
            }
        }
        step_write(step + 1);
        transfer_self(step == 0, false);
        ESP_LOGI(TAG, "restarting into the new image");
        esp_restart();
        break;
    case 1:
        if (strcmp(running->label, "ota_1") != 0 || strstr(ack, "\"state\":\"pending\"") == NULL) {
            fail("expected to run a pending ota_1");
        }
        {
            uint8_t zero[32] = {0};

            command(POCKET_UPDATE_OP_BEGIN, 8192, zero);
            if (!wait_ack(ack, sizeof(ack), 2000) || strstr(ack, "unconfirmed") == NULL) {
                fail("an unconfirmed image accepted another transfer");
            }
        }
        step_write(2);
        ESP_LOGI(TAG, "not confirming; the updater should go back to ota_0 by itself in %u s",
                 (unsigned)(POCKET_UPDATE_CONFIRM_MS / 1000U));
        vTaskDelay(pdMS_TO_TICKS(POCKET_UPDATE_CONFIRM_MS + 30000U));
        fail("still running after the confirmation deadline");
        break;
    case 3:
        if (strcmp(running->label, "ota_1") != 0 || strstr(ack, "\"state\":\"pending\"") == NULL) {
            fail("expected to run a pending ota_1");
        }
        command(POCKET_UPDATE_OP_CONFIRM, 0, NULL);
        if (!wait_ack(ack, sizeof(ack), 3000) || strstr(ack, "\"ok\":true") == NULL) {
            fail("confirm refused");
        }
        command(POCKET_UPDATE_OP_INFO, 0, NULL);
        if (!wait_ack(ack, sizeof(ack), 3000) || strstr(ack, "\"state\":\"valid\"") == NULL ||
            strcmp(state_name(running), "valid") != 0) {
            fail("not valid after confirm");
        }
        ESP_LOGI(TAG, "confirmed: %s", ack);
        if (strstr(ack, "\"prev\":\"\"") != NULL) {
            fail("the other slot's build is not reported");
        }
        step_write(4);
        command(POCKET_UPDATE_OP_ROLLBACK, 0, NULL);
        if (!wait_ack(ack, sizeof(ack), 5000) || strstr(ack, "\"ok\":true") == NULL) {
            ESP_LOGE(TAG, "rollback answered: %s", ack);
            fail("switching back was refused");
        }
        while (!pocket_update_restart_due()) {
            vTaskDelay(pdMS_TO_TICKS(50));
        }
        ESP_LOGI(TAG, "restarting into the previous slot");
        esp_restart();
        break;
    case 4:
        if (strcmp(running->label, "ota_0") != 0 || strstr(ack, "\"state\":\"pending\"") == NULL) {
            fail("expected to run a pending ota_0 after switching back");
        }
        command(POCKET_UPDATE_OP_CONFIRM, 0, NULL);
        if (!wait_ack(ack, sizeof(ack), 3000) || strstr(ack, "\"ok\":true") == NULL ||
            strcmp(state_name(running), "valid") != 0) {
            fail("confirm after switching back failed");
        }
        step_write(5);
        ESP_LOGI(TAG, "ALL STEPS PASSED: transfer with loss and resume, restart into the new "
                      "slot, automatic rollback, confirm, switch back");
        break;
    default:
        ESP_LOGI(TAG, "nothing left to do");
        break;
    }
    vTaskDelay(portMAX_DELAY);
}
