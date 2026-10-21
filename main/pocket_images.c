#include "pocket_text.h"
/* Additive JSON protocol; the firmware update binary channel stays untouched. */
#include "pocket_images.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cJSON.h"
#include "mbedtls/base64.h"

#define SIDE 64U
#define BYTES (SIDE * SIDE * 2U)
#define COUNT 4U
#define KEEP 1U /* Fetch the focused card only; do not duplicate the card archive. */
#define RESERVE (16U * 1024U)
static struct {
    char card[BUDDY_CARD_ID_MAX];
    char ids[COUNT][34];
    unsigned count;
} lists[KEEP];
_Static_assert(sizeof(lists) + 256U <= POCKET_IMAGES_STATIC_BYTES, "image metadata budget");
static pocket_images_ops_t ops;
static unsigned selected;
static char focus[BUDDY_CARD_ID_MAX], active[34];
static uint32_t generation, token, checksum;
static size_t received;
static uint64_t clock_ms, last_packet;
static uint8_t *pixels;
static bool opened, ready, begun, catalog_wait;
static const char *caption = "";

static bool valid_id(const char *id)
{
    if (!id || strlen(id) != 33U || id[0] != 'a') return false;
    for (unsigned i = 1; i < 33U; ++i)
        if (!((id[i] >= '0' && id[i] <= '9') || (id[i] >= 'a' && id[i] <= 'f'))) return false;
    return true;
}
static bool valid_card(const char *card)
{
    if (!card || !card[0] || strlen(card) >= BUDDY_CARD_ID_MAX) return false;
    for (const char *p = card; *p; ++p)
        if (!((*p >= 'a' && *p <= 'z') || (*p >= 'A' && *p <= 'Z') ||
              (*p >= '0' && *p <= '9') || *p == '_' || *p == '-')) return false;
    return true;
}
static int find(const char *card)
{
    for (unsigned i = 0; i < KEEP; ++i)
        if (card[0] && strcmp(lists[i].card, card) == 0) return (int)i;
    return -1;
}
static void send_ack(const char *status)
{
    char line[160];
    int n = snprintf(line, sizeof(line), "{\"cmd\":\"image\",\"op\":\"ack\",\"token\":%lu,\"next\":%u,\"status\":\"%s\"}\n",
                     (unsigned long)token, (unsigned)received, status);
    if (n > 0 && n < (int)sizeof(line))
        if (ops.send) (void)ops.send(line, (size_t)n, generation);
}
static void close_image(void)
{
    /* The app detaches LVGL/cache references under its lock before any call
     * that can release this buffer. */
    free(pixels); pixels = NULL;
    opened = ready = begun = catalog_wait = false; active[0] = '\0'; received = 0;
}
static void request_catalog(void)
{
    if (catalog_wait || !focus[0]) return;
    char line[100];
    int n = snprintf(line, sizeof(line), "{\"cmd\":\"image\",\"op\":\"catalog\",\"card\":\"%s\"}\n", focus);
    catalog_wait = n > 0 && n < (int)sizeof(line) && ops.send && ops.send(line, (size_t)n, generation);
    last_packet = clock_ms;
    if (!catalog_wait) caption = PT_IMAGE_REQUEST_FAILED;
}
static void request_image(void)
{
    int index = find(focus);
    free(pixels); pixels = NULL; ready = begun = false; received = 0;
    caption = PT_IMAGE_LOADING;
    if (index < 0) { request_catalog(); return; }
    if (!lists[index].count) { caption = PT_IMAGE_EMPTY; return; }
    selected %= lists[index].count;
    (void)snprintf(active, sizeof(active), "%s", lists[index].ids[selected]);
    if (!ops.free_heap || ops.free_heap() < BYTES + RESERVE || !(pixels = malloc(BYTES))) {
        caption = PT_IMAGE_MEMORY; return;
    }
    memset(pixels, 0, BYTES);
    ++token; if (!token) ++token;
    char line[180];
    int n = snprintf(line, sizeof(line), "{\"cmd\":\"image\",\"op\":\"get\",\"card\":\"%s\",\"id\":\"%s\",\"token\":%lu}\n",
                     focus, active, (unsigned long)token);
    if (n <= 0 || n >= (int)sizeof(line) ||
        !ops.send || !ops.send(line, (size_t)n, generation)) {
        free(pixels); pixels = NULL; caption = PT_IMAGE_REQUEST_FAILED;
    }
    last_packet = clock_ms;
}
void pocket_images_focus(const char *card, bool enabled, uint32_t gen, uint64_t now)
{
    clock_ms = now;
    if (generation != gen) {
        close_image(); memset(lists, 0, sizeof(lists)); generation = gen; focus[0] = '\0';
    }
    const char *next = enabled && card ? card : "";
    bool changed = strcmp(focus, next) != 0;
    if (!enabled || changed) {
        close_image(); selected = 0; memset(lists, 0, sizeof(lists));
    }
    (void)snprintf(focus, sizeof(focus), "%s", next);
    if (changed && focus[0]) request_catalog();
    if ((catalog_wait || (opened && pixels && !ready)) && now - last_packet > 15000U) {
        free(pixels); pixels = NULL; catalog_wait = false; caption = PT_IMAGE_TIMEOUT;
    }
}
bool pocket_images_key(const buddy_event_t *event)
{
    if (opened && (event->type == BUDDY_EVENT_KEY_CLICK || event->type == BUDDY_EVENT_KEY_DOUBLE ||
                   event->type == BUDDY_EVENT_KEY_LONG)) {
        if (event->key == BUDDY_KEY_OK) close_image();
        else if (event->type == BUDDY_EVENT_KEY_CLICK) {
            int i = find(focus);
            if (i >= 0 && lists[i].count) {
                unsigned n = lists[i].count;
                selected = (selected + (event->key == BUDDY_KEY_UP ? n - 1U : 1U)) % n;
                request_image();
            }
        }
        return true;
    }
    if (focus[0] && event->type == BUDDY_EVENT_KEY_LONG && event->key == BUDDY_KEY_UP) {
        opened = true; request_image(); return true;
    }
    return false;
}
static const char *string(const cJSON *root, const char *name)
{
    const cJSON *value = cJSON_GetObjectItemCaseSensitive(root, name);
    return cJSON_IsString(value) ? value->valuestring : "";
}
static bool number(const cJSON *root, const char *name, uint32_t *out)
{
    const cJSON *value = cJSON_GetObjectItemCaseSensitive(root, name);
    if (!cJSON_IsNumber(value) || value->valuedouble < 0 || value->valuedouble > 4294967295.0 ||
        value->valuedouble != (double)(uint32_t)value->valuedouble) return false;
    *out = (uint32_t)value->valuedouble; return true;
}
static uint32_t crc32(const uint8_t *data, size_t length)
{
    uint32_t crc = 0xffffffffU;
    for (size_t i = 0; i < length; ++i) {
        crc ^= data[i];
        for (unsigned b = 0; b < 8U; ++b)
            crc = (crc >> 1) ^ (0xedb88320U & (0U - (crc & 1U)));
    }
    return ~crc;
}
bool pocket_images_line(const char *line, size_t length)
{
    cJSON *root = cJSON_ParseWithLength(line, length);
    if (!root) return false;
    bool ours = strcmp(string(root, "cmd"), "image") == 0;
    const char *op = string(root, "op");
    if (!ours) { cJSON_Delete(root); return false; }
    if (strcmp(op, "clear") == 0) {
        close_image(); memset(lists, 0, sizeof(lists)); focus[0] = '\0';
    } else if (strcmp(op, "catalog_error") == 0 && strcmp(string(root, "card"), focus) == 0) {
        free(pixels); pixels = NULL; ready = begun = catalog_wait = false;
        memset(lists, 0, sizeof(lists)); caption = PT_IMAGE_UNAVAILABLE;
    }
    else if (strcmp(op, "list") == 0) {
        const char *card = string(root, "card");
        const cJSON *ids = cJSON_GetObjectItemCaseSensitive(root, "ids");
        unsigned n = cJSON_IsArray(ids) ? (unsigned)cJSON_GetArraySize(ids) : COUNT + 1U;
        bool valid = valid_card(card) && strcmp(card, focus) == 0 && n <= COUNT;
        for (unsigned i = 0; valid && i < n; ++i) {
            const cJSON *id = cJSON_GetArrayItem(ids, (int)i);
            valid = cJSON_IsString(id) && valid_id(id->valuestring);
        }
        if (valid) {
            int i = find(card);
            if (i < 0) i = 0;
            bool resume = opened;
            close_image();
            memset(&lists[i], 0, sizeof(lists[i]));
            (void)snprintf(lists[i].card, sizeof(lists[i].card), "%s", card);
            lists[i].count = n;
            for (unsigned j = 0; j < n; ++j)
                (void)snprintf(lists[i].ids[j], sizeof(lists[i].ids[j]), "%s", cJSON_GetArrayItem(ids, (int)j)->valuestring);
            if (resume) { opened = true; request_image(); }
        }
    } else {
        uint32_t incoming;
        if (!opened || !pixels || !number(root, "token", &incoming) || incoming != token ||
            strcmp(string(root, "id"), active) != 0) { cJSON_Delete(root); return true; }
        last_packet = clock_ms;
        if (strcmp(op, "begin") == 0) {
            uint32_t size;
            if (!number(root, "size", &size) || size != BYTES || !number(root, "crc", &checksum)) {
                send_ack("error"); free(pixels); pixels = NULL; begun = false; caption = PT_IMAGE_CHECK_FAILED;
            } else { begun = true; send_ack("ready"); }
        } else if (strcmp(op, "chunk") == 0 && !ready && begun) {
            uint32_t offset;
            uint8_t decoded[384]; size_t count = 0;
            const char *data = string(root, "data");
            if (!number(root, "offset", &offset) || strlen(data) > 512U ||
                mbedtls_base64_decode(decoded, sizeof(decoded), &count, (const unsigned char *)data, strlen(data)) != 0 ||
                !count || offset > BYTES || count > BYTES - offset) {
                send_ack("error"); free(pixels); pixels = NULL; caption = PT_IMAGE_CHECK_FAILED;
            } else {
                if (offset == received) { memcpy(pixels + received, decoded, count); received += count; }
                send_ack("ready");
            }
        } else if (strcmp(op, "end") == 0) {
            if (begun && received == BYTES && crc32(pixels, BYTES) == checksum) {
                ready = true; caption = PT_IMAGE_KEYS; send_ack("done");
            } else { send_ack("error"); free(pixels); pixels = NULL; caption = PT_IMAGE_CHECK_FAILED; }
        } else if (strcmp(op, "error") == 0) {
            free(pixels); pixels = NULL; caption = PT_IMAGE_UNAVAILABLE;
        }
    }
    cJSON_Delete(root); return true;
}
const uint8_t *pocket_images_pixels(void) { return opened && ready ? pixels : NULL; }
const char *pocket_images_caption(void) { return caption; }
bool pocket_images_open(void) { return opened; }
bool pocket_images_available(void) { int i = find(focus); return i >= 0 && lists[i].count; }

void pocket_images_init(const pocket_images_ops_t *provided)
{
    close_image();
    memset(lists, 0, sizeof(lists)); focus[0] = '\0';
    selected = 0;
    if (provided) ops = *provided;
    else memset(&ops, 0, sizeof(ops));
}
