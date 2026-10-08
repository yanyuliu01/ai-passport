// main/pocket_voice.c —— 按住说话的采集与上行。协议和线程约定见 pocket_voice.h。
#include "pocket_voice.h"

#include <inttypes.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "esp_log.h"
#include "esp_timer.h"

#include "bsp_audio.h"
#include "buddy_ble.h"
#include "pocket_voice_core.h"

// 首次打开 codec 时要走一遍 I2C 初始化和日志输出，栈留宽一些。
#define VOICE_TASK_STACK 6144U
// 比应用任务（5）高一级：读麦克风不能被界面刷新拖住，否则 I2S 缓冲（约 90 ms）会溢出。
#define VOICE_TASK_PRIORITY 6U
#define VOICE_CHUNK_SAMPLES 320U  // 20 ms
// 蓝牙一时发不动时最多先存这么多（约 1.5 秒的声音）。
#define VOICE_FIFO_BYTES 12288U
#define VOICE_MAX_MS 30000U
#define VOICE_MIN_MS 300U
// 麦克风刚打开的头几十毫秒是上电的冲击声，不要。
#define VOICE_SETTLE_CHUNKS 3U
// 每读一块（20 ms）最多发这么多帧：足够追上积压，又不会把蓝牙协议栈的缓冲占满。
#define VOICE_FRAMES_PER_PASS 4U
// 蓝牙连续这么久一帧都发不出去，就认为这一轮发不完了。
#define VOICE_STALL_MS 3000U
#define VOICE_READ_FAILURES_MAX 5U

static const char *const TAG = "voice";

static TaskHandle_t s_task;
static pocket_voice_event_cb_t s_callback;
static void *s_callback_context;
static atomic_bool s_busy;
static atomic_bool s_stop;
static atomic_bool s_cancel;
static atomic_uint s_generation;
static bool s_audio_initialized;

static uint32_t voice_now_ms(void)
{
    return (uint32_t)(esp_timer_get_time() / 1000);
}

static void voice_report(buddy_voice_status_t status, uint32_t generation)
{
    if (s_callback != NULL) {
        s_callback(status, generation, s_callback_context);
    }
}

// 文本行很短，但协议栈可能正忙着发语音帧：忙就等一下再试。
static bool voice_send_line(const char *line, uint32_t generation)
{
    unsigned attempt;

    for (attempt = 0; attempt < 50U; ++attempt) {
        esp_err_t err = buddy_ble_send_for_generation(line, strlen(line), generation);

        if (err == ESP_OK) {
            return true;
        }
        if (err == ESP_ERR_INVALID_STATE) {
            return false;
        }
        vTaskDelay(pdMS_TO_TICKS(20));
    }
    return false;
}

static void voice_queue_frame(const uint8_t *frame, size_t length, void *context)
{
    (void)pocket_voice_fifo_push(context, frame, length);
}

typedef enum {
    VOICE_DRAIN_OK,
    VOICE_DRAIN_BUSY,
    VOICE_DRAIN_LINK_LOST,
} voice_drain_t;

// 把队列里的帧发出去，最多 limit 帧。
static voice_drain_t voice_drain(pocket_voice_fifo_t *fifo, uint32_t generation,
                                 unsigned limit, uint32_t *last_progress_ms)
{
    uint8_t frame[POCKET_VOICE_FRAME_MAX];
    unsigned sent;

    for (sent = 0; sent < limit; ++sent) {
        size_t length = pocket_voice_fifo_peek(fifo, frame);
        esp_err_t err;

        if (length == 0U) {
            *last_progress_ms = voice_now_ms();
            return VOICE_DRAIN_OK;
        }
        err = buddy_ble_notify_for_generation(frame, length, generation);
        if (err == ESP_ERR_INVALID_STATE || err == ESP_ERR_INVALID_SIZE) {
            return VOICE_DRAIN_LINK_LOST;
        }
        if (err != ESP_OK) {
            return voice_now_ms() - *last_progress_ms > VOICE_STALL_MS
                       ? VOICE_DRAIN_LINK_LOST
                       : VOICE_DRAIN_BUSY;
        }
        pocket_voice_fifo_pop(fifo);
        *last_progress_ms = voice_now_ms();
    }
    return VOICE_DRAIN_OK;
}

static esp_err_t voice_audio_open(void)
{
    esp_err_t err = s_audio_initialized ? bsp_audio_wake() : bsp_audio_init();

    if (err != ESP_OK) {
        return err;
    }
    s_audio_initialized = true;
    return bsp_audio_set_format(POCKET_VOICE_SAMPLE_RATE, 16, 1);
}

static buddy_voice_status_t voice_run(uint32_t generation, uint8_t *storage)
{
    static int16_t pcm[VOICE_CHUNK_SAMPLES];
    static pocket_voice_packer_t packer;
    pocket_voice_fifo_t fifo;
    char line[112];
    size_t frame_size =
        pocket_voice_frame_size(buddy_ble_notify_payload_for_generation(generation));
    uint32_t started_ms;
    uint32_t elapsed_ms = 0;
    uint32_t last_progress_ms;
    unsigned read_failures = 0;
    unsigned chunk;
    bool limit = false;

    if (frame_size == 0U || !pocket_voice_packer_init(&packer, frame_size)) {
        return BUDDY_VOICE_FAILED_LINK;
    }
    pocket_voice_fifo_init(&fifo, storage, VOICE_FIFO_BYTES);
    if (voice_audio_open() != ESP_OK) {
        ESP_LOGE(TAG, "microphone did not start");
        return BUDDY_VOICE_FAILED_MIC;
    }
    for (chunk = 0; chunk < VOICE_SETTLE_CHUNKS; ++chunk) {
        if (bsp_audio_read(pcm, sizeof(pcm)) != ESP_OK) {
            return BUDDY_VOICE_FAILED_MIC;
        }
    }
    if (atomic_load(&s_stop)) {
        /* Released before the microphone was even ready: a tap, not a message. */
        return atomic_load(&s_cancel) ? BUDDY_VOICE_CANCELLED : BUDDY_VOICE_TOO_SHORT;
    }
    (void)snprintf(line, sizeof(line),
                   "{\"cmd\":\"voice\",\"state\":\"start\",\"rate\":%u,"
                   "\"codec\":\"ima-adpcm\"}\n",
                   (unsigned)POCKET_VOICE_SAMPLE_RATE);
    if (!voice_send_line(line, generation)) {
        return BUDDY_VOICE_FAILED_LINK;
    }
    voice_report(BUDDY_VOICE_STARTED, generation);
    started_ms = voice_now_ms();
    last_progress_ms = started_ms;

    while (!atomic_load(&s_stop)) {
        if (bsp_audio_read(pcm, sizeof(pcm)) != ESP_OK) {
            if (++read_failures >= VOICE_READ_FAILURES_MAX) {
                (void)voice_send_line("{\"cmd\":\"voice\",\"state\":\"cancel\"}\n", generation);
                return BUDDY_VOICE_FAILED_MIC;
            }
            continue;
        }
        read_failures = 0;
        pocket_voice_packer_push(&packer, pcm, VOICE_CHUNK_SAMPLES, voice_queue_frame, &fifo);
        if (voice_drain(&fifo, generation, VOICE_FRAMES_PER_PASS, &last_progress_ms) ==
            VOICE_DRAIN_LINK_LOST) {
            return BUDDY_VOICE_FAILED_LINK;
        }
        elapsed_ms = voice_now_ms() - started_ms;
        if (elapsed_ms >= VOICE_MAX_MS) {
            limit = true;
            break;
        }
    }
    elapsed_ms = voice_now_ms() - started_ms;
    if (atomic_load(&s_cancel) || elapsed_ms < VOICE_MIN_MS) {
        (void)voice_send_line("{\"cmd\":\"voice\",\"state\":\"cancel\"}\n", generation);
        return atomic_load(&s_cancel) ? BUDDY_VOICE_CANCELLED : BUDDY_VOICE_TOO_SHORT;
    }

    /* 麦克风可以先歇了：剩下的只是把队列里的尾巴发完。 */
    pocket_voice_packer_flush(&packer, voice_queue_frame, &fifo);
    last_progress_ms = voice_now_ms();
    while (fifo.used != 0U) {
        voice_drain_t drained =
            voice_drain(&fifo, generation, VOICE_FRAMES_PER_PASS, &last_progress_ms);

        if (drained == VOICE_DRAIN_LINK_LOST) {
            (void)voice_send_line("{\"cmd\":\"voice\",\"state\":\"cancel\"}\n", generation);
            return BUDDY_VOICE_FAILED_LINK;
        }
        vTaskDelay(pdMS_TO_TICKS(drained == VOICE_DRAIN_BUSY ? 15 : 5));
    }
    (void)snprintf(line, sizeof(line),
                   "{\"cmd\":\"voice\",\"state\":\"end\",\"frames\":%" PRIu32
                   ",\"dropped\":%" PRIu32 ",\"ms\":%" PRIu32 "}\n",
                   packer.frames, fifo.dropped, elapsed_ms);
    if (!voice_send_line(line, generation)) {
        return BUDDY_VOICE_FAILED_LINK;
    }
    ESP_LOGI(TAG, "sent %" PRIu32 " frames, dropped %" PRIu32 ", %" PRIu32 " ms",
             packer.frames, fifo.dropped, elapsed_ms);
    return limit ? BUDDY_VOICE_LIMIT : BUDDY_VOICE_FINISHED;
}

static void voice_task(void *context)
{
    (void)context;
    for (;;) {
        uint32_t generation;
        uint8_t *storage;
        buddy_voice_status_t status;

        (void)ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        if (!atomic_load(&s_busy)) {
            continue;
        }
        generation = atomic_load(&s_generation);
        storage = malloc(VOICE_FIFO_BYTES);
        if (storage == NULL) {
            ESP_LOGE(TAG, "no memory for the voice queue");
            status = BUDDY_VOICE_FAILED_MIC;
        } else {
            status = voice_run(generation, storage);
            free(storage);
        }
        /* 每轮结束都让 codec 休眠；没初始化过时这个调用什么也不做。 */
        if (s_audio_initialized && bsp_audio_sleep() != ESP_OK) {
            ESP_LOGW(TAG, "codec did not go to sleep");
        }
        atomic_store(&s_busy, false);
        voice_report(status, generation);
    }
}

esp_err_t pocket_voice_init(pocket_voice_event_cb_t callback, void *context)
{
    if (s_task != NULL) {
        return ESP_OK;
    }
    s_callback = callback;
    s_callback_context = context;
    if (xTaskCreate(voice_task, "pocket_voice", VOICE_TASK_STACK, NULL, VOICE_TASK_PRIORITY,
                    &s_task) != pdPASS) {
        s_task = NULL;
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

esp_err_t pocket_voice_start(uint32_t connection_generation)
{
    bool idle = false;

    if (s_task == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    if (!atomic_compare_exchange_strong(&s_busy, &idle, true)) {
        return ESP_ERR_INVALID_STATE;
    }
    atomic_store(&s_stop, false);
    atomic_store(&s_cancel, false);
    atomic_store(&s_generation, connection_generation);
    xTaskNotifyGive(s_task);
    return ESP_OK;
}

void pocket_voice_stop(bool cancel)
{
    if (!atomic_load(&s_busy)) {
        return;
    }
    if (cancel) {
        atomic_store(&s_cancel, true);
    }
    atomic_store(&s_stop, true);
}
