// main/pocket_update.c —— 经蓝牙换固件的硬件那一半。协议和线程约定见 pocket_update.h。
#include "pocket_update.h"

#include <inttypes.h>
#include <stdatomic.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/stream_buffer.h"
#include "freertos/task.h"

#include "esp_app_desc.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_partition.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "mbedtls/sha256.h"

#include "buddy_ble.h"

// 镜像收全后的核对（esp_ota_end）要用不少栈；收尾时会在日志里报这个任务栈的最低余量。
#define UPDATE_TASK_STACK 5120U
#define UPDATE_TASK_PRIORITY 4U
#define UPDATE_QUEUE_DEPTH 4U
#define UPDATE_BLOCK_BYTES 512U
// 平时每一圈最多写这么多块就回去看看有没有命令、该不该报进度。
#define UPDATE_BLOCKS_PER_PASS 4U
#define UPDATE_DRAIN_ALL 0U
// 没在收数据时，隔这么久醒一次，看看“认可”和“等手机回来”的期限到了没有。
#define UPDATE_IDLE_WAIT_MS 250U
#define UPDATE_STREAM_WAIT_MS 50U

typedef struct {
    pocket_update_command_t command;
    uint32_t generation;
} update_request_t;

static const char *const TAG = "update";
// 电脑上的固件仓库靠这个记号认出“这一版能经蓝牙再换下一版”。
static const char MARKER[] = POCKET_UPDATE_MARKER;

static QueueHandle_t s_requests;
static StreamBufferHandle_t s_stream;
static SemaphoreHandle_t s_rx_lock;
// 以下两项由 s_rx_lock 保护：NimBLE 任务按它们决定收不收一帧。
static pocket_update_rx_t s_rx;
static uint32_t s_rx_generation;

// 以下只在换固件任务里读写。
static bool s_session;
static esp_ota_handle_t s_handle;
static const esp_partition_t *s_target;
static mbedtls_sha256_context s_sha;
static uint8_t s_sha256[POCKET_UPDATE_SHA256_BYTES];
static uint32_t s_size;
static uint32_t s_done;
static uint32_t s_generation;
static uint64_t s_paused_ms;
static pocket_update_reporter_t s_reporter;
static bool s_pending_verify;
static uint64_t s_confirm_deadline_ms;
static uint64_t s_restart_ms;
static uint8_t s_block[UPDATE_BLOCK_BYTES];

static atomic_int s_phase;
static atomic_uchar s_percent;
static atomic_bool s_failed;
static atomic_bool s_restart;

static uint64_t update_now_ms(void)
{
    return (uint64_t)esp_timer_get_time() / 1000ULL;
}

static void update_set_phase(pocket_update_phase_t phase)
{
    atomic_store(&s_phase, (int)phase);
}

static void update_send(const char *line, int length, uint32_t generation)
{
    if (length > 0) {
        (void)buddy_ble_send_for_generation(line, (size_t)length, generation);
    }
}

static void update_ack(pocket_update_op_t op, bool ok, const char *error, uint32_t generation)
{
    char line[POCKET_UPDATE_LINE_MAX];

    update_send(line, pocket_update_ack_json(line, sizeof(line), op, ok, error, false, 0),
                generation);
}

static void update_rx_stop(void)
{
    xSemaphoreTake(s_rx_lock, portMAX_DELAY);
    pocket_update_rx_stop(&s_rx);
    xSemaphoreGive(s_rx_lock);
}

// 把已经收下、还在缓冲区里的数据写进闪存：最多 max_blocks 块，UPDATE_DRAIN_ALL 表示
// 写到缓冲区空为止。出错返回 false。
static bool update_drain(TickType_t wait, unsigned max_blocks)
{
    unsigned blocks;

    for (blocks = 0; max_blocks == UPDATE_DRAIN_ALL || blocks < max_blocks; ++blocks) {
        size_t count = xStreamBufferReceive(s_stream, s_block, sizeof(s_block), wait);

        if (count == 0U) {
            return true;
        }
        wait = 0;
        if (!s_session) {
            continue;
        }
        if (esp_ota_write(s_handle, s_block, count) != ESP_OK ||
            mbedtls_sha256_update(&s_sha, s_block, count) != 0) {
            return false;
        }
        s_done += (uint32_t)count;
        atomic_store(&s_percent, pocket_update_percent(s_done, s_size));
    }
    return true;
}

// 镜像已经核对通过（或者要切回上一版），正等着重启：这时不再接新的活。
static bool update_restarting(void)
{
    return atomic_load(&s_phase) == (int)POCKET_UPDATE_RESTARTING || atomic_load(&s_restart);
}

// 放弃这一次：另一个槽位里写了一半的东西作废，正在运行的固件不受影响。
static void update_abort_session(bool failed)
{
    update_rx_stop();
    if (s_session) {
        (void)esp_ota_abort(s_handle);
        mbedtls_sha256_free(&s_sha);
        s_session = false;
    }
    // 收的那一头已经停了，这里把缓冲区里剩下的倒掉。
    while (s_stream != NULL &&
           xStreamBufferReceive(s_stream, s_block, sizeof(s_block), 0) != 0U) {
    }
    s_done = 0;
    s_size = 0;
    atomic_store(&s_percent, 0);
    update_set_phase(POCKET_UPDATE_IDLE);
    if (failed) {
        atomic_store(&s_failed, true);
    }
}

static void update_build_of(const esp_app_desc_t *description, char *out)
{
    pocket_update_hex(description->app_elf_sha256, POCKET_UPDATE_BUILD_HEX / 2U, out);
}

static void update_handle_info(uint32_t generation)
{
    char line[POCKET_UPDATE_LINE_MAX];
    pocket_update_info_t info = {0};
    const esp_app_desc_t *running_description = esp_app_get_description();
    const esp_partition_t *running = esp_ota_get_running_partition();
    const esp_partition_t *other = esp_ota_get_next_update_partition(NULL);
    esp_app_desc_t other_description;

    update_build_of(running_description, info.build);
    strlcpy(info.version, running_description->version, sizeof(info.version));
    if (running != NULL) {
        strlcpy(info.slot, running->label, sizeof(info.slot));
    }
    if (other != NULL && other != running) {
        info.max_size = other->size;
        if (esp_ota_get_partition_description(other, &other_description) == ESP_OK) {
            update_build_of(&other_description, info.prev);
        }
    }
    info.pending = s_pending_verify;
    info.heap_free = esp_get_free_heap_size();
    info.heap_low = esp_get_minimum_free_heap_size();
    update_send(line, pocket_update_info_json(line, sizeof(line), &info), generation);
}

static void update_handle_begin(const update_request_t *request)
{
    char line[POCKET_UPDATE_LINE_MAX];
    size_t chunk =
        pocket_update_chunk_size(buddy_ble_notify_payload_for_generation(request->generation));
    const esp_partition_t *running = esp_ota_get_running_partition();
    const esp_partition_t *target = esp_ota_get_next_update_partition(NULL);
    uint32_t offset = 0;
    const char *error = NULL;

    if (chunk == 0U) {
        error = "link";
    } else if (update_restarting()) {
        error = "restarting";
    } else if (s_pending_verify) {
        // 正在试用的新固件还没被认可，这时不能再换：先发 confirm。
        error = "unconfirmed";
    } else if (target == NULL || target == running) {
        error = "no slot";
    } else if (request->command.size < POCKET_UPDATE_IMAGE_MIN ||
               request->command.size > target->size) {
        error = "size";
    } else if (s_stream == NULL) {
        // 缓冲区第一次用到才申请，平时不占内存；申请到之后就一直留着。
        StreamBufferHandle_t stream = xStreamBufferCreate(POCKET_UPDATE_BUFFER_BYTES, 1);

        if (stream == NULL) {
            error = "memory";
        } else {
            s_stream = stream;
        }
    }
    if (error != NULL) {
        update_ack(POCKET_UPDATE_OP_BEGIN, false, error, request->generation);
        return;
    }

    if (s_session && s_size == request->command.size &&
        memcmp(s_sha256, request->command.sha256, sizeof(s_sha256)) == 0) {
        // 同一份镜像：从已经写进闪存的地方接着收。
        update_rx_stop();
        if (!update_drain(0, UPDATE_DRAIN_ALL)) {
            update_abort_session(true);
            update_ack(POCKET_UPDATE_OP_BEGIN, false, "flash", request->generation);
            return;
        }
        offset = s_done;
    } else {
        esp_err_t err;

        update_abort_session(false);
        err = esp_ota_begin(target, OTA_WITH_SEQUENTIAL_WRITES, &s_handle);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "esp_ota_begin: %s", esp_err_to_name(err));
            update_ack(POCKET_UPDATE_OP_BEGIN, false, "flash", request->generation);
            return;
        }
        mbedtls_sha256_init(&s_sha);
        if (mbedtls_sha256_starts(&s_sha, 0) != 0) {
            (void)esp_ota_abort(s_handle);
            mbedtls_sha256_free(&s_sha);
            update_ack(POCKET_UPDATE_OP_BEGIN, false, "flash", request->generation);
            return;
        }
        s_session = true;
        s_target = target;
        s_size = request->command.size;
        s_done = 0;
        memcpy(s_sha256, request->command.sha256, sizeof(s_sha256));
        ESP_LOGI(TAG, "receiving %" PRIu32 " bytes into %s", s_size, target->label);
    }

    s_generation = request->generation;
    pocket_update_reporter_reset(&s_reporter);
    atomic_store(&s_failed, false);
    atomic_store(&s_percent, pocket_update_percent(s_done, s_size));
    update_set_phase(POCKET_UPDATE_RECEIVING);
    xSemaphoreTake(s_rx_lock, portMAX_DELAY);
    pocket_update_rx_begin(&s_rx, s_size, offset);
    s_rx_generation = request->generation;
    xSemaphoreGive(s_rx_lock);
    update_send(line, pocket_update_begin_json(line, sizeof(line), offset, chunk),
                request->generation);
}

static void update_handle_end(const update_request_t *request)
{
    char line[POCKET_UPDATE_LINE_MAX];
    uint8_t digest[POCKET_UPDATE_SHA256_BYTES];
    uint32_t got;
    esp_err_t err;

    if (!s_session) {
        update_ack(POCKET_UPDATE_OP_END, false, "no transfer", request->generation);
        return;
    }
    if (!update_drain(0, UPDATE_DRAIN_ALL)) {
        update_abort_session(true);
        update_ack(POCKET_UPDATE_OP_END, false, "flash", request->generation);
        return;
    }
    xSemaphoreTake(s_rx_lock, portMAX_DELAY);
    got = s_rx.received;
    xSemaphoreGive(s_rx_lock);
    if (s_done != s_size || got != s_size) {
        update_send(line,
                    pocket_update_ack_json(line, sizeof(line), POCKET_UPDATE_OP_END, false,
                                           "incomplete", true, got),
                    request->generation);
        return;
    }

    update_rx_stop();
    update_set_phase(POCKET_UPDATE_CHECKING);
    if (mbedtls_sha256_finish(&s_sha, digest) != 0 ||
        memcmp(digest, s_sha256, sizeof(digest)) != 0) {
        ESP_LOGE(TAG, "image hash does not match");
        update_abort_session(true);
        update_ack(POCKET_UPDATE_OP_END, false, "sha256", request->generation);
        return;
    }
    mbedtls_sha256_free(&s_sha);
    // esp_ota_end 会自己核对镜像的格式和校验和；不管成不成，它都会把句柄收回去。
    s_session = false;
    err = esp_ota_end(s_handle);
    if (err == ESP_OK) {
        err = esp_ota_set_boot_partition(s_target);
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "new image rejected: %s", esp_err_to_name(err));
        update_abort_session(true);
        update_ack(POCKET_UPDATE_OP_END, false, "image", request->generation);
        return;
    }
    ESP_LOGI(TAG, "image accepted; restarting into %s (task stack left: %u bytes)",
             s_target->label, (unsigned)uxTaskGetStackHighWaterMark(NULL));
    update_ack(POCKET_UPDATE_OP_END, true, NULL, request->generation);
    update_set_phase(POCKET_UPDATE_RESTARTING);
    s_restart_ms = update_now_ms() + POCKET_UPDATE_RESTART_MS;
}

static void update_handle_rollback(const update_request_t *request)
{
    const esp_partition_t *running = esp_ota_get_running_partition();
    const esp_partition_t *other = esp_ota_get_next_update_partition(NULL);
    esp_app_desc_t description;
    esp_ota_img_states_t state = ESP_OTA_IMG_UNDEFINED;
    const char *error = NULL;

    if (s_session || update_restarting()) {
        error = "busy";
    } else if (other == NULL || other == running ||
               esp_ota_get_partition_description(other, &description) != ESP_OK) {
        error = "no previous";
    } else if (esp_ota_get_state_partition(other, &state) == ESP_OK &&
               (state == ESP_OTA_IMG_INVALID || state == ESP_OTA_IMG_ABORTED)) {
        // 另一个槽位里是一版没能启动成功的固件，不往回切。
        error = "previous invalid";
    } else if (esp_ota_set_boot_partition(other) != ESP_OK) {
        error = "image";
    }
    if (error != NULL) {
        update_ack(POCKET_UPDATE_OP_ROLLBACK, false, error, request->generation);
        return;
    }
    ESP_LOGI(TAG, "switching back to %s", other->label);
    update_ack(POCKET_UPDATE_OP_ROLLBACK, true, NULL, request->generation);
    update_set_phase(POCKET_UPDATE_RESTARTING);
    s_restart_ms = update_now_ms() + POCKET_UPDATE_RESTART_MS;
}

static void update_handle(const update_request_t *request)
{
    switch (request->command.op) {
    case POCKET_UPDATE_OP_INFO:
        update_handle_info(request->generation);
        break;
    case POCKET_UPDATE_OP_BEGIN:
        update_handle_begin(request);
        break;
    case POCKET_UPDATE_OP_END:
        update_handle_end(request);
        break;
    case POCKET_UPDATE_OP_ABORT:
        if (update_restarting()) {
            // 新固件已经定下来了，这时说放弃太晚：想回去就等重启后发 rollback。
            update_ack(POCKET_UPDATE_OP_ABORT, false, "restarting", request->generation);
            break;
        }
        update_abort_session(false);
        update_ack(POCKET_UPDATE_OP_ABORT, true, NULL, request->generation);
        break;
    case POCKET_UPDATE_OP_CONFIRM:
        if (s_pending_verify) {
            if (esp_ota_mark_app_valid_cancel_rollback() != ESP_OK) {
                update_ack(POCKET_UPDATE_OP_CONFIRM, false, "flash", request->generation);
                break;
            }
            s_pending_verify = false;
            ESP_LOGI(TAG, "new firmware confirmed");
        }
        update_ack(POCKET_UPDATE_OP_CONFIRM, true, NULL, request->generation);
        break;
    case POCKET_UPDATE_OP_ROLLBACK:
        update_handle_rollback(request);
        break;
    case POCKET_UPDATE_OP_NONE:
        break;
    }
}

static void update_report(uint64_t now_ms)
{
    char line[POCKET_UPDATE_LINE_MAX];
    uint32_t got;
    bool gap;

    xSemaphoreTake(s_rx_lock, portMAX_DELAY);
    got = s_rx.received;
    gap = s_rx.gap;
    xSemaphoreGive(s_rx_lock);
    if (pocket_update_should_report(&s_reporter, got, s_done, s_size, gap, now_ms)) {
        update_send(line, pocket_update_progress_json(line, sizeof(line), got, s_done, gap),
                    s_generation);
    }
}

static void update_task(void *context)
{
    (void)context;
    for (;;) {
        bool receiving = atomic_load(&s_phase) == (int)POCKET_UPDATE_RECEIVING;
        update_request_t request;
        TickType_t wait = receiving ? 0 : pdMS_TO_TICKS(UPDATE_IDLE_WAIT_MS);
        uint64_t now_ms;

        while (xQueueReceive(s_requests, &request, wait) == pdTRUE) {
            update_handle(&request);
            wait = 0;
        }
        now_ms = update_now_ms();
        if (atomic_load(&s_phase) == (int)POCKET_UPDATE_RECEIVING) {
            if (!update_drain(pdMS_TO_TICKS(UPDATE_STREAM_WAIT_MS), UPDATE_BLOCKS_PER_PASS)) {
                ESP_LOGE(TAG, "flash write failed at %" PRIu32, s_done);
                update_abort_session(true);
                update_ack(POCKET_UPDATE_OP_END, false, "flash", s_generation);
            } else if (!buddy_ble_is_generation_secure(s_generation)) {
                // 连接断了：已经写进去的留着，等手机回来用同一份镜像接着发。
                update_rx_stop();
                (void)update_drain(0, UPDATE_DRAIN_ALL);
                s_paused_ms = update_now_ms();
                update_set_phase(POCKET_UPDATE_PAUSED);
                ESP_LOGW(TAG, "link lost at %" PRIu32 " of %" PRIu32, s_done, s_size);
            } else {
                update_report(update_now_ms());
            }
        } else if (atomic_load(&s_phase) == (int)POCKET_UPDATE_PAUSED &&
                   now_ms - s_paused_ms >= POCKET_UPDATE_RESUME_MS) {
            ESP_LOGW(TAG, "nobody came back for the rest; giving up");
            update_abort_session(true);
        } else if (atomic_load(&s_phase) == (int)POCKET_UPDATE_RESTARTING &&
                   now_ms >= s_restart_ms) {
            atomic_store(&s_restart, true);
        }
        if (s_pending_verify && now_ms >= s_confirm_deadline_ms) {
            ESP_LOGE(TAG, "new firmware was not confirmed in time; rolling back");
            (void)esp_ota_mark_app_invalid_rollback_and_reboot();
            // 走到这里说明退不回去（另一个槽位不能用）：那就留在这一版。
            s_pending_verify = false;
        }
    }
}

void pocket_update_unusable(const char *why)
{
    const esp_partition_t *running = esp_ota_get_running_partition();
    esp_ota_img_states_t state = ESP_OTA_IMG_UNDEFINED;

    if (running == NULL || esp_ota_get_state_partition(running, &state) != ESP_OK ||
        state != ESP_OTA_IMG_PENDING_VERIFY) {
        return;
    }
    // 还在试用期的新固件连蓝牙都起不来：手机不可能连上来说“认可”，等满三分钟
    // 没有意义，现在就退回上一版。
    ESP_LOGE(TAG, "new firmware cannot work (%s); rolling back now", why != NULL ? why : "?");
    (void)esp_ota_mark_app_invalid_rollback_and_reboot();
}

esp_err_t pocket_update_init(void)
{
    const esp_partition_t *running = esp_ota_get_running_partition();
    esp_ota_img_states_t state = ESP_OTA_IMG_UNDEFINED;

    if (s_requests != NULL) {
        return ESP_OK;
    }
    s_requests = xQueueCreate(UPDATE_QUEUE_DEPTH, sizeof(update_request_t));
    s_rx_lock = xSemaphoreCreateMutex();
    if (s_requests == NULL || s_rx_lock == NULL) {
        return ESP_ERR_NO_MEM;
    }
    if (running != NULL && esp_ota_get_state_partition(running, &state) == ESP_OK &&
        state == ESP_OTA_IMG_PENDING_VERIFY) {
        s_pending_verify = true;
        s_confirm_deadline_ms = update_now_ms() + POCKET_UPDATE_CONFIRM_MS;
    }
    ESP_LOGI(TAG, "%s slot=%s%s", MARKER, running != NULL ? running->label : "?",
             s_pending_verify ? " (new firmware, waiting to be confirmed)" : "");
    if (xTaskCreate(update_task, "pocket_update", UPDATE_TASK_STACK, NULL, UPDATE_TASK_PRIORITY,
                    NULL) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

void pocket_update_command(const pocket_update_command_t *command,
                           uint32_t connection_generation)
{
    update_request_t request;

    if (command == NULL || s_requests == NULL || command->op == POCKET_UPDATE_OP_NONE) {
        return;
    }
    request.command = *command;
    request.generation = connection_generation;
    if (xQueueSend(s_requests, &request, 0) != pdTRUE) {
        ESP_LOGW(TAG, "command dropped: queue full");
    }
}

void pocket_update_frame(const uint8_t *frame, size_t length, uint32_t connection_generation)
{
    const uint8_t *data;
    size_t data_length;
    uint32_t offset;

    // 缓冲区在第一次进入“正在收”之前就申请好了，所以先看阶段再用它。
    if (atomic_load(&s_phase) != (int)POCKET_UPDATE_RECEIVING || s_stream == NULL ||
        !pocket_update_frame_parse(frame, length, &offset, &data, &data_length)) {
        return;
    }
    xSemaphoreTake(s_rx_lock, portMAX_DELAY);
    if (s_rx_generation == connection_generation &&
        pocket_update_rx_offer(&s_rx, offset, data_length,
                               xStreamBufferSpacesAvailable(s_stream)) ==
            POCKET_UPDATE_FRAME_ACCEPT) {
        (void)xStreamBufferSend(s_stream, data, data_length, 0);
    }
    xSemaphoreGive(s_rx_lock);
}

pocket_update_phase_t pocket_update_phase(void)
{
    return (pocket_update_phase_t)atomic_load(&s_phase);
}

uint8_t pocket_update_percent_now(void)
{
    return atomic_load(&s_percent);
}

bool pocket_update_take_failure(void)
{
    return atomic_exchange(&s_failed, false);
}

bool pocket_update_restart_due(void)
{
    return atomic_load(&s_restart);
}
