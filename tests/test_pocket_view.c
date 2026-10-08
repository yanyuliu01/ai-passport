#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "pocket_view.h"

static buddy_ui_snapshot_t connected_snapshot(void)
{
    buddy_ui_snapshot_t snapshot = {0};

    snapshot.ble_enabled = true;
    snapshot.ble_connected = true;
    snapshot.ble_encrypted = true;
    snapshot.connection = BUDDY_CONNECTION_CONNECTED;
    snapshot.character = BUDDY_CHARACTER_IDLE;
    return snapshot;
}

static void test_view_priority(void)
{
    buddy_ui_snapshot_t snapshot = connected_snapshot();

    assert(pocket_view_for(NULL) == POCKET_VIEW_PAGE);
    assert(pocket_view_for(&snapshot) == POCKET_VIEW_PAGE);
    snprintf(snapshot.prompt_id, sizeof(snapshot.prompt_id), "%s", "req-1");
    assert(pocket_view_for(&snapshot) == POCKET_VIEW_APPROVAL);
    /* 已经按过键（锁定）的请求仍留在审批视图，用来显示发送结果。 */
    snapshot.approval_locked = true;
    assert(pocket_view_for(&snapshot) == POCKET_VIEW_APPROVAL);
    snapshot.passkey_visible = true;
    assert(pocket_view_for(&snapshot) == POCKET_VIEW_PAIRING);
    snapshot.passkey_visible = false;
    snapshot.connection = BUDDY_CONNECTION_PAIRING;
    assert(pocket_view_for(&snapshot) == POCKET_VIEW_PAIRING);
    snapshot.confirmation = BUDDY_CONFIRM_UNPAIR;
    assert(pocket_view_for(&snapshot) == POCKET_VIEW_CONFIRM);
}

static void test_voice_view(void)
{
    buddy_ui_snapshot_t snapshot = connected_snapshot();

    /* 正在说话时，说话的画面压过普通页面和新来的审批请求。 */
    snapshot.voice_phase = BUDDY_VOICE_PREPARING;
    assert(pocket_view_for(&snapshot) == POCKET_VIEW_VOICE);
    assert(pocket_pet_for(&snapshot) == POCKET_PET_BUSY);
    snprintf(snapshot.prompt_id, sizeof(snapshot.prompt_id), "%s", "req-1");
    snapshot.voice_phase = BUDDY_VOICE_LISTENING;
    assert(pocket_view_for(&snapshot) == POCKET_VIEW_VOICE);
    assert(pocket_pet_for(&snapshot) == POCKET_PET_ASK);
    snapshot.voice_phase = BUDDY_VOICE_SENDING;
    assert(pocket_pet_for(&snapshot) == POCKET_PET_HAPPY);
    /* 配对和二次确认仍然更优先。 */
    snapshot.passkey_visible = true;
    assert(pocket_view_for(&snapshot) == POCKET_VIEW_PAIRING);
    snapshot.passkey_visible = false;
    snapshot.voice_phase = BUDDY_VOICE_IDLE;
    assert(pocket_view_for(&snapshot) == POCKET_VIEW_APPROVAL);
}

static void test_home_status(void)
{
    buddy_ui_snapshot_t snapshot = {0};

    assert(pocket_home_for(NULL) == POCKET_HOME_BLE_OFF);
    assert(pocket_home_for(&snapshot) == POCKET_HOME_BLE_OFF);
    snapshot.ble_enabled = true;
    assert(pocket_home_for(&snapshot) == POCKET_HOME_WAITING);
    snapshot.ble_connected = true;
    assert(pocket_home_for(&snapshot) == POCKET_HOME_LINKING);
    snapshot.ble_encrypted = true;
    snapshot.heartbeat_stale = true;
    snapshot.connection = BUDDY_CONNECTION_OFFLINE;
    assert(pocket_home_for(&snapshot) == POCKET_HOME_LINKING);

    snapshot = connected_snapshot();
    assert(pocket_home_for(&snapshot) == POCKET_HOME_IDLE);
    snapshot.running = 2;
    assert(pocket_home_for(&snapshot) == POCKET_HOME_BUSY);
    snapshot.waiting = 1;
    assert(pocket_home_for(&snapshot) == POCKET_HOME_PENDING);
    snapshot.character = BUDDY_CHARACTER_CELEBRATE;
    assert(pocket_home_for(&snapshot) == POCKET_HOME_MILESTONE);
    snapshot.character = BUDDY_CHARACTER_HEART;
    assert(pocket_home_for(&snapshot) == POCKET_HOME_APPROVED);
    /* 心跳一旦过期，任何“刚刚发生”的状态都不能继续显示。 */
    snapshot.heartbeat_stale = true;
    assert(pocket_home_for(&snapshot) == POCKET_HOME_LINKING);
}

static void test_pet_mood(void)
{
    buddy_ui_snapshot_t snapshot = {0};

    assert(pocket_pet_for(NULL) == POCKET_PET_SLEEP);
    assert(pocket_pet_for(&snapshot) == POCKET_PET_SLEEP);
    snapshot.ble_enabled = true;
    assert(pocket_pet_for(&snapshot) == POCKET_PET_SLEEP);

    snapshot = connected_snapshot();
    assert(pocket_pet_for(&snapshot) == POCKET_PET_IDLE);
    snapshot.running = 1;
    assert(pocket_pet_for(&snapshot) == POCKET_PET_BUSY);
    snapshot.waiting = 1;
    assert(pocket_pet_for(&snapshot) == POCKET_PET_ASK);
    snapshot.waiting = 0;
    snapshot.character = BUDDY_CHARACTER_HEART;
    assert(pocket_pet_for(&snapshot) == POCKET_PET_HAPPY);

    /* 审批：提问 → 发送中 → 结果；拒绝之后不摆出开心的脸。 */
    snapshot = connected_snapshot();
    snprintf(snapshot.prompt_id, sizeof(snapshot.prompt_id), "%s", "req-1");
    assert(pocket_pet_for(&snapshot) == POCKET_PET_ASK);
    snapshot.approval_locked = true;
    snapshot.permission_decision = BUDDY_PERMISSION_ONCE;
    snapshot.permission_delivery = BUDDY_PERMISSION_DELIVERY_SENDING;
    assert(pocket_pet_for(&snapshot) == POCKET_PET_BUSY);
    snapshot.permission_delivery = BUDDY_PERMISSION_DELIVERY_FAILED;
    assert(pocket_pet_for(&snapshot) == POCKET_PET_OOPS);
    snapshot.permission_delivery = BUDDY_PERMISSION_DELIVERY_SENT;
    assert(pocket_pet_for(&snapshot) == POCKET_PET_HAPPY);
    snapshot.permission_decision = BUDDY_PERMISSION_DENY;
    assert(pocket_pet_for(&snapshot) == POCKET_PET_IDLE);

    snapshot = connected_snapshot();
    snapshot.connection = BUDDY_CONNECTION_PAIRING;
    assert(pocket_pet_for(&snapshot) == POCKET_PET_BUSY);
    snapshot.passkey_visible = true;
    assert(pocket_pet_for(&snapshot) == POCKET_PET_ASK);
    snapshot.confirmation = BUDDY_CONFIRM_FACTORY_RESET;
    assert(pocket_pet_for(&snapshot) == POCKET_PET_ASK);
}

static void expect_tokens(uint64_t tokens, const char *expected)
{
    char text[24];

    assert(pocket_format_tokens(tokens, text, sizeof(text)) == strlen(expected));
    assert(strcmp(text, expected) == 0);
}

static void test_tokens(void)
{
    char tiny[4];

    expect_tokens(0, "0");
    expect_tokens(999, "999");
    expect_tokens(1000, "1,000");
    expect_tokens(9999, "9,999");
    expect_tokens(10000, "1.0\xE4\xB8\x87");
    expect_tokens(31200, "3.1\xE4\xB8\x87");
    expect_tokens(99999, "9.9\xE4\xB8\x87");
    expect_tokens(184502, "18.4\xE4\xB8\x87");
    expect_tokens(99999999, "9999.9\xE4\xB8\x87");
    expect_tokens(100000000, "1.0\xE4\xBA\xBF");
    expect_tokens(1234567890, "12.3\xE4\xBA\xBF");
    /* 缓冲区不够时截断但保持 NUL 结尾。 */
    assert(pocket_format_tokens(1234567, tiny, sizeof(tiny)) == 3);
    assert(tiny[3] == '\0');
    assert(pocket_format_tokens(1, NULL, 8) == 0);
    assert(pocket_format_tokens(1, tiny, 0) == 0);
}

static void test_clock(void)
{
    char text[8] = "x";

    assert(pocket_format_clock(0, 28800, 0, 0, text, sizeof(text)) == 0);
    assert(text[0] == '\0');
    /* 1790000000 = 2026-09-21 14:13:20 UTC；东八区是 22:13。 */
    assert(pocket_format_clock(1790000000, 28800, 1000, 1000, text, sizeof(text)) == 5);
    assert(strcmp(text, "22:13") == 0);
    /* 同步之后设备自己走时：过 7 分钟。 */
    assert(pocket_format_clock(1790000000, 28800, 1000, 1000 + 7 * 60000, text,
                               sizeof(text)) == 5);
    assert(strcmp(text, "22:20") == 0);
    /* 负时区跨日。 */
    assert(pocket_format_clock(3600, -7200, 0, 0, text, sizeof(text)) == 5);
    assert(strcmp(text, "23:00") == 0);
    /* now 早于同步时刻（计时器回绕保护）时不前进。 */
    assert(pocket_format_clock(1790000000, 0, 5000, 1000, text, sizeof(text)) == 5);
    assert(strcmp(text, "14:13") == 0);
    /* 东八区跨过午夜。 */
    assert(pocket_format_clock(1790000000 + 2 * 3600, 28800, 0, 0, text, sizeof(text)) == 5);
    assert(strcmp(text, "00:13") == 0);
}

static void expect_uptime(uint64_t ms, const char *expected)
{
    char text[32];

    assert(pocket_format_uptime(ms, text, sizeof(text)) == strlen(expected));
    assert(strcmp(text, expected) == 0);
}

static void test_uptime_and_passkey(void)
{
    char text[8];

    expect_uptime(0, "\xE4\xB8\x8D\xE5\x88\xB0" "1" "\xE5\x88\x86\xE9\x92\x9F");
    expect_uptime(59999, "\xE4\xB8\x8D\xE5\x88\xB0" "1" "\xE5\x88\x86\xE9\x92\x9F");
    expect_uptime(12 * 60000ULL, "12\xE5\x88\x86\xE9\x92\x9F");
    expect_uptime(185 * 60000ULL, "3\xE5\xB0\x8F\xE6\x97\xB6" "05\xE5\x88\x86");
    expect_uptime((2 * 1440ULL + 4 * 60 + 30) * 60000ULL,
                  "2\xE5\xA4\xA9" "4\xE5\xB0\x8F\xE6\x97\xB6");

    assert(pocket_format_passkey(123456, text, sizeof(text)) == 7);
    assert(strcmp(text, "123 456") == 0);
    assert(pocket_format_passkey(42, text, sizeof(text)) == 7);
    assert(strcmp(text, "000 042") == 0);
    assert(pocket_format_passkey(1234567, text, sizeof(text)) == 7);
    assert(strcmp(text, "234 567") == 0);
}

int main(void)
{
    test_view_priority();
    test_voice_view();
    test_home_status();
    test_pet_mood();
    test_tokens();
    test_clock();
    test_uptime_and_passkey();
    puts("pocket_view: PASS");
    return 0;
}
