#include "pocket_view.h"

#include <inttypes.h>
#include <stdio.h>

#define POCKET_NOTICE_MS 6000U

pocket_view_t pocket_view_for(const buddy_ui_snapshot_t *snapshot)
{
    if (snapshot == NULL) {
        return POCKET_VIEW_PAGE;
    }
    if (snapshot->confirmation != BUDDY_CONFIRM_NONE) {
        return POCKET_VIEW_CONFIRM;
    }
    if (snapshot->passkey_visible || snapshot->connection == BUDDY_CONNECTION_PAIRING) {
        return POCKET_VIEW_PAIRING;
    }
    if (snapshot->voice_phase != BUDDY_VOICE_IDLE) {
        return POCKET_VIEW_VOICE;
    }
    if (snapshot->prompt_id[0] != '\0') {
        return POCKET_VIEW_APPROVAL;
    }
    return POCKET_VIEW_PAGE;
}

pocket_home_t pocket_home_for(const buddy_ui_snapshot_t *snapshot)
{
    if (snapshot == NULL || !snapshot->ble_enabled) {
        return POCKET_HOME_BLE_OFF;
    }
    if (!snapshot->ble_connected) {
        return POCKET_HOME_WAITING;
    }
    if (!snapshot->ble_encrypted || snapshot->heartbeat_stale ||
        snapshot->connection != BUDDY_CONNECTION_CONNECTED) {
        return POCKET_HOME_LINKING;
    }
    switch (snapshot->chat.phase) {
    case BUDDY_CHAT_SENT:
        return POCKET_HOME_SENT;
    case BUDDY_CHAT_THINKING:
        return POCKET_HOME_THINKING;
    case BUDDY_CHAT_HELPER:
        return snapshot->chat.agent[0] != '\0' ? POCKET_HOME_HELPER : POCKET_HOME_THINKING;
    case BUDDY_CHAT_DONE:
        return POCKET_HOME_ANSWERED;
    case BUDDY_CHAT_FAILED:
        return POCKET_HOME_FAILED;
    case BUDDY_CHAT_NONE:
        break;
    }
    if (snapshot->host_chat) {
        // 中枢自己会说对话到哪了；心跳里的数字只是别的 App 的通知，不代表小幽在忙。
        return POCKET_HOME_QUIET;
    }
    // 没有“对话”可言：对面是 Claude 桌面端，或者不报告对话的旧版中枢。按心跳里的数字来。
    if (snapshot->running > 0U || snapshot->waiting > 0U) {
        return POCKET_HOME_THINKING;
    }
    if (snapshot->reply[0] != '\0') {
        return POCKET_HOME_ANSWERED;
    }
    return POCKET_HOME_QUIET;
}

bool pocket_home_is_talk(pocket_home_t home)
{
    return home >= POCKET_HOME_SENT;
}

int pocket_card_scroll(int y, int direction, int content, int view, int step, int *edge)
{
    int bottom = content > view ? content - view : 0;
    int before;

    if (y > bottom) {
        y = bottom;
    }
    if (y < 0) {
        y = 0;
    }
    before = y;
    if (direction > 0) {
        y += step;
    } else if (direction < 0) {
        y -= step;
    }
    if (y > bottom) {
        y = bottom;
    }
    if (y < 0) {
        y = 0;
    }
    if (edge != NULL) {
        *edge = direction != 0 && y == before ? (direction > 0 ? 1 : -1) : 0;
    }
    return y;
}

uint32_t pocket_task_seconds(uint32_t reported, uint64_t since_ms, uint64_t now_ms)
{
    uint64_t total = reported;

    if (now_ms > since_ms) {
        total += (now_ms - since_ms) / 1000U;
    }
    return total > UINT32_MAX ? UINT32_MAX : (uint32_t)total;
}

static pocket_pet_mood_t pocket_pet_from_mood(buddy_mood_t mood)
{
    switch (mood) {
    case BUDDY_MOOD_BUSY:
        return POCKET_PET_BUSY;
    case BUDDY_MOOD_ASK:
        return POCKET_PET_ASK;
    case BUDDY_MOOD_HAPPY:
        return POCKET_PET_HAPPY;
    case BUDDY_MOOD_OOPS:
        return POCKET_PET_OOPS;
    case BUDDY_MOOD_IDLE:
        break;
    }
    return POCKET_PET_IDLE;
}

pocket_pet_mood_t pocket_pet_for(const buddy_ui_snapshot_t *snapshot)
{
    switch (pocket_view_for(snapshot)) {
    case POCKET_VIEW_CONFIRM:
        return POCKET_PET_ASK;
    case POCKET_VIEW_PAIRING:
        return snapshot->passkey_visible ? POCKET_PET_ASK : POCKET_PET_BUSY;
    case POCKET_VIEW_VOICE:
        if (snapshot->voice_phase == BUDDY_VOICE_LISTENING) {
            return POCKET_PET_ASK;
        }
        return snapshot->voice_phase == BUDDY_VOICE_SENDING ? POCKET_PET_HAPPY
                                                            : POCKET_PET_BUSY;
    case POCKET_VIEW_APPROVAL:
        if (!snapshot->approval_locked) {
            return POCKET_PET_ASK;
        }
        if (snapshot->permission_delivery == BUDDY_PERMISSION_DELIVERY_FAILED) {
            return POCKET_PET_OOPS;
        }
        if (snapshot->permission_delivery != BUDDY_PERMISSION_DELIVERY_SENT) {
            return POCKET_PET_BUSY;
        }
        return snapshot->permission_decision == BUDDY_PERMISSION_DENY ? POCKET_PET_IDLE
                                                                      : POCKET_PET_HAPPY;
    case POCKET_VIEW_PAGE:
        break;
    }
    switch (pocket_home_for(snapshot)) {
    case POCKET_HOME_BLE_OFF:
    case POCKET_HOME_WAITING:
        return POCKET_PET_SLEEP;
    case POCKET_HOME_SENT:
    case POCKET_HOME_THINKING:
    case POCKET_HOME_HELPER:
        return POCKET_PET_BUSY;
    case POCKET_HOME_FAILED:
        return POCKET_PET_OOPS;
    case POCKET_HOME_ANSWERED:
        // 她答完时自己说了是什么表情；桌面端没有这个说法，按平常算。
        return snapshot->chat.phase == BUDDY_CHAT_DONE
                   ? pocket_pet_from_mood(snapshot->chat.mood)
                   : POCKET_PET_IDLE;
    case POCKET_HOME_LINKING:
    case POCKET_HOME_QUIET:
        break;
    }
    // 刚在设备上点过头，或者到了一个里程碑：高兴一会儿。
    if (snapshot->character == BUDDY_CHARACTER_HEART ||
        snapshot->character == BUDDY_CHARACTER_CELEBRATE) {
        return POCKET_PET_HAPPY;
    }
    return POCKET_PET_IDLE;
}

bool pocket_notice_visible(const buddy_ui_snapshot_t *snapshot)
{
    if (snapshot == NULL || snapshot->message[0] == '\0') {
        return false;
    }
    if (!snapshot->host_hub) {
        return true;
    }
    return snapshot->uptime_ms < snapshot->message_since_ms ||
           snapshot->uptime_ms - snapshot->message_since_ms < POCKET_NOTICE_MS;
}

static char pocket_lower(char value)
{
    return value >= 'A' && value <= 'Z' ? (char)(value - 'A' + 'a') : value;
}

static bool pocket_name_is(const char *name, const char *known)
{
    while (*name != '\0' && *known != '\0') {
        if (pocket_lower(*name) != *known) {
            return false;
        }
        ++name;
        ++known;
    }
    return *name == '\0' && *known == '\0';
}

uint32_t pocket_helper_color(const char *name)
{
    // 不认识的名字从这几个颜色里按名字选一个；都和小幽的淡紫、以及表示
    // 可以 / 不行 / 留意的绿、红、黄分得开。
    static const uint32_t others[] = {0x7FB6FFu, 0xF2A7C8u, 0x8FD6E8u, 0xC9B458u};
    uint32_t hash = 2166136261u;

    if (name == NULL || name[0] == '\0') {
        return POCKET_COLOR_XIAOYOU;
    }
    if (pocket_name_is(name, "claude")) {
        return 0xE08A63u;
    }
    if (pocket_name_is(name, "codex")) {
        return 0x6FD3B0u;
    }
    for (; *name != '\0'; ++name) {
        hash = (hash ^ (unsigned char)pocket_lower(*name)) * 16777619u;
    }
    return others[hash % (sizeof(others) / sizeof(others[0]))];
}

static size_t pocket_finish(int written, char *out, size_t size)
{
    if (size == 0U) {
        return 0;
    }
    if (written < 0) {
        out[0] = '\0';
        return 0;
    }
    return (size_t)written < size ? (size_t)written : size - 1U;
}

size_t pocket_format_clock(int64_t epoch_seconds, int32_t timezone_offset_seconds,
                           uint64_t time_received_ms, uint64_t now_ms,
                           char *out, size_t size)
{
    int64_t local;
    int64_t seconds_of_day;

    if (out == NULL || size == 0U) {
        return 0;
    }
    out[0] = '\0';
    if (epoch_seconds <= 0) {
        return 0;
    }
    local = epoch_seconds + timezone_offset_seconds;
    if (now_ms > time_received_ms) {
        local += (int64_t)((now_ms - time_received_ms) / 1000U);
    }
    seconds_of_day = local % 86400;
    if (seconds_of_day < 0) {
        seconds_of_day += 86400;
    }
    return pocket_finish(snprintf(out, size, "%02d:%02d", (int)(seconds_of_day / 3600),
                                  (int)(seconds_of_day % 3600 / 60)),
                         out, size);
}

size_t pocket_format_elapsed(uint64_t since_ms, uint64_t now_ms, char *out, size_t size)
{
    uint64_t seconds = now_ms > since_ms ? (now_ms - since_ms) / 1000U : 0U;

    if (out == NULL || size == 0U) {
        return 0;
    }
    if (seconds > 99U * 60U + 59U) {
        seconds = 99U * 60U + 59U;
    }
    return pocket_finish(snprintf(out, size, "%u:%02u", (unsigned)(seconds / 60U),
                                  (unsigned)(seconds % 60U)),
                         out, size);
}

size_t pocket_format_passkey(uint32_t passkey, char *out, size_t size)
{
    if (out == NULL || size == 0U) {
        return 0;
    }
    passkey %= 1000000U;
    return pocket_finish(snprintf(out, size, "%03u %03u", (unsigned)(passkey / 1000U),
                                  (unsigned)(passkey % 1000U)),
                         out, size);
}
