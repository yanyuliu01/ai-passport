/* Protocol/lifecycle tests. The codec shim supplies fixed zero-pixel fixtures;
 * actual mbedTLS decoding is linked and exercised by the firmware build. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "pocket_images.h"
#include "cJSON.h"
static char sent[1024];
static size_t heap = 100000;
static uint32_t expected_generation = 7;
static size_t free_heap(void) { return heap; }
static bool send_line(const char *line, size_t length, uint32_t gen)
{
    assert(gen == expected_generation); assert(length < sizeof(sent));
    memcpy(sent, line, length); sent[length] = '\0'; return true;
}
int mbedtls_base64_decode(unsigned char *out, size_t max, size_t *count,
                         const unsigned char *data, size_t length)
{
    if ((length != 512U && length != 172U) || max < 384U) return -1;
    size_t bytes = length == 512U ? 384U : 128U;
    for (size_t i = 0; i < length; ++i) if (data[i] != 'A' && !(i == length - 1U && data[i] == '=')) return -1;
    memset(out, 0, bytes); *count = bytes; return 0;
}
static void line(const char *text) { assert(pocket_images_line(text, strlen(text))); }
static unsigned number(const char *key)
{
    cJSON *root=cJSON_Parse(sent); assert(root);
    cJSON *item=cJSON_GetObjectItemCaseSensitive(root,key); assert(cJSON_IsNumber(item));
    unsigned n=(unsigned)item->valuedouble; cJSON_Delete(root); return n;
}
static unsigned open_image(void)
{
    const buddy_event_t up={.type=BUDDY_EVENT_KEY_LONG,.key=BUDDY_KEY_UP};
    assert(pocket_images_key(&up)); assert(pocket_images_open()); return number("token");
}
int main(void)
{
    const pocket_images_ops_t ops={free_heap,send_line}; pocket_images_init(&ops);
    pocket_images_focus("c1",true,7,100);
    assert(strstr(sent,"catalog"));
    line("{\"cmd\":\"image\",\"op\":\"list\",\"card\":\"c1\",\"ids\":[\"bad\"]}");
    assert(!pocket_images_available());
    line("{\"cmd\":\"image\",\"op\":\"list\",\"card\":\"c1\",\"ids\":[\"a11111111111111111111111111111111\"]}");
    assert(pocket_images_available());
    line("{\"cmd\":\"image\",\"op\":\"list\",\"card\":\"c2\",\"ids\":[]}");
    assert(pocket_images_available()); /* unrelated catalogs cannot replace the focused one */
    unsigned token=open_image(); char json[1024], zeros[513]; memset(zeros,'A',512); zeros[512]='\0';
    snprintf(json,sizeof(json),"{\"cmd\":\"image\",\"op\":\"begin\",\"id\":\"a11111111111111111111111111111111\",\"token\":%u,\"size\":8192,\"crc\":3639908756}",token); line(json);
    for (unsigned offset=0;offset<8192U;offset+=384U) {
        unsigned count = offset + 384U <= 8192U ? 384U : 128U;
        memset(zeros, 'A',512); zeros[512]='\0';
        if (count == 128U) { zeros[171]='='; zeros[172]='\0'; }
        snprintf(json,sizeof(json),"{\"cmd\":\"image\",\"op\":\"chunk\",\"id\":\"a11111111111111111111111111111111\",\"token\":%u,\"offset\":%u,\"data\":\"%s\"}",token,offset,zeros);
        line(json); assert(number("next")==offset+count);
        line(json); assert(number("next")==offset+count); /* duplicate delivery */
    }
    snprintf(json,sizeof(json),"{\"cmd\":\"image\",\"op\":\"end\",\"id\":\"a11111111111111111111111111111111\",\"token\":%u}",token); line(json);
    assert(pocket_images_pixels()); assert(strstr(sent,"done"));
    const buddy_event_t ok={.type=BUDDY_EVENT_KEY_CLICK,.key=BUDDY_KEY_OK};
    assert(pocket_images_key(&ok)); assert(!pocket_images_open()); assert(!pocket_images_pixels());
    token=open_image(); pocket_images_focus("c1",true,7,16000);
    assert(!pocket_images_pixels()); assert(strstr(pocket_images_caption(),"超时"));
    assert(pocket_images_key(&ok));
    heap=20000; (void)open_image(); assert(!pocket_images_pixels());
    assert(strstr(pocket_images_caption(),"内存不足"));
    assert(pocket_images_key(&ok)); heap=100000;
    token=open_image();
    snprintf(json,sizeof(json),"{\"cmd\":\"image\",\"op\":\"begin\",\"id\":\"a11111111111111111111111111111111\",\"token\":%u,\"size\":999999,\"crc\":0}",token); line(json);
    assert(pocket_images_open()); assert(!pocket_images_pixels());
    assert(pocket_images_key(&ok));
    (void)open_image(); expected_generation = 8; pocket_images_focus("c1",true,8,16001);
    assert(!pocket_images_open()); assert(!pocket_images_available());
    puts("Image protocol/lifecycle: PASS");
    return 0;
}
