#include "pocket_view.h"

#include <inttypes.h>
#include <stdio.h>

#include "pocket_text.h"

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
    if (snapshot->character == BUDDY_CHARACTER_HEART) {
        return POCKET_HOME_APPROVED;
    }
    if (snapshot->character == BUDDY_CHARACTER_CELEBRATE) {
        return POCKET_HOME_MILESTONE;
    }
    if (snapshot->waiting > 0U) {
        return POCKET_HOME_PENDING;
    }
    if (snapshot->running > 0U) {
        return POCKET_HOME_BUSY;
    }
    return POCKET_HOME_IDLE;
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
    case POCKET_HOME_BUSY:
        return POCKET_PET_BUSY;
    case POCKET_HOME_PENDING:
        return POCKET_PET_ASK;
    case POCKET_HOME_APPROVED:
    case POCKET_HOME_MILESTONE:
        return POCKET_PET_HAPPY;
    case POCKET_HOME_LINKING:
    case POCKET_HOME_IDLE:
        break;
    }
    return POCKET_PET_IDLE;
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

size_t pocket_format_tokens(uint64_t tokens, char *out, size_t size)
{
    int written;

    if (out == NULL || size == 0U) {
        return 0;
    }
    if (tokens < 1000U) {
        written = snprintf(out, size, "%" PRIu64, tokens);
    } else if (tokens < 10000U) {
        written = snprintf(out, size, "%" PRIu64 ",%03" PRIu64, tokens / 1000U,
                           tokens % 1000U);
    } else if (tokens < 100000000ULL) {
        /* 向下取整到 0.1 万，避免 99,999 显示成“10.0万”。 */
        uint64_t tenths = tokens / 1000U;

        written = snprintf(out, size, "%" PRIu64 ".%" PRIu64 PT_UNIT_WAN, tenths / 10U,
                           tenths % 10U);
    } else {
        uint64_t tenths = tokens / 10000000ULL;

        written = snprintf(out, size, "%" PRIu64 ".%" PRIu64 PT_UNIT_YI, tenths / 10U,
                           tenths % 10U);
    }
    return pocket_finish(written, out, size);
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

size_t pocket_format_uptime(uint64_t uptime_ms, char *out, size_t size)
{
    uint64_t minutes = uptime_ms / 60000U;
    int written;

    if (out == NULL || size == 0U) {
        return 0;
    }
    if (minutes == 0U) {
        written = snprintf(out, size, "%s", PT_UPTIME_UNDER_MINUTE);
    } else if (minutes < 60U) {
        written = snprintf(out, size, "%" PRIu64 PT_UNIT_MINUTES, minutes);
    } else if (minutes < 1440U) {
        written = snprintf(out, size, "%" PRIu64 PT_UNIT_HOURS "%02" PRIu64 PT_UNIT_MINUTE_SHORT, minutes / 60U, minutes % 60U);
    } else {
        written = snprintf(out, size, "%" PRIu64 PT_UNIT_DAYS "%" PRIu64 PT_UNIT_HOURS, minutes / 1440U,
                           minutes % 1440U / 60U);
    }
    return pocket_finish(written, out, size);
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
