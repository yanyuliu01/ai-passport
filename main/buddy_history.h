/* main/buddy_history.h - earlier turns of the conversation, kept in RAM.
 *
 * A fixed byte budget holds the words of past turns, oldest first; when a new
 * turn does not fit, the oldest are dropped. Header-only and free of ESP-IDF
 * and LVGL so the state machine, the interface and the host tests share it. */
#pragma once

#include <string.h>

#include "buddy_types.h"

static inline const char *buddy_history_said(const buddy_history_t *history, unsigned index)
{
    return history != NULL && index < history->count ? history->text + history->turns[index].said
                                                     : "";
}

static inline const char *buddy_history_reply(const buddy_history_t *history, unsigned index)
{
    return history != NULL && index < history->count ? history->text + history->turns[index].reply
                                                     : "";
}

/* How many turns belong to the conversation on screen. */
static inline unsigned buddy_history_visible(const buddy_history_t *history)
{
    return history != NULL && history->floor < history->count
               ? (unsigned)(history->count - history->floor)
               : 0U;
}

/* How many earlier turns are folded away. */
static inline unsigned buddy_history_hidden(const buddy_history_t *history)
{
    if (history == NULL) {
        return 0U;
    }
    return history->floor < history->count ? history->floor : history->count;
}

static inline bool buddy_history_last_is(const buddy_history_t *history, const char *said,
                                         const char *reply)
{
    return history != NULL && history->count > 0U &&
           strcmp(buddy_history_said(history, history->count - 1U), said) == 0 &&
           strcmp(buddy_history_reply(history, history->count - 1U), reply) == 0;
}

static inline void buddy_history_drop_oldest(buddy_history_t *history)
{
    uint16_t length;
    unsigned index;

    if (history->count == 0U) {
        return;
    }
    length = history->count > 1U ? history->turns[1].said : history->used;
    memmove(history->text, history->text + length, (size_t)(history->used - length));
    history->used = (uint16_t)(history->used - length);
    for (index = 1; index < history->count; ++index) {
        history->turns[index - 1U] = history->turns[index];
        history->turns[index - 1U].said = (uint16_t)(history->turns[index - 1U].said - length);
        history->turns[index - 1U].reply = (uint16_t)(history->turns[index - 1U].reply - length);
    }
    --history->count;
    if (history->floor > 0U) {
        --history->floor;
    }
}

/* Appends a finished turn. A turn without any words, or one equal to the turn
 * already at the end, is not added. Returns whether the history changed. */
static inline bool buddy_history_push(buddy_history_t *history, const char *said,
                                      const char *reply, uint8_t flags)
{
    size_t said_size;
    size_t reply_size;

    if (history == NULL || said == NULL || reply == NULL ||
        (said[0] == '\0' && reply[0] == '\0') || buddy_history_last_is(history, said, reply)) {
        return false;
    }
    said_size = strlen(said) + 1U;
    reply_size = strlen(reply) + 1U;
    if (said_size + reply_size > sizeof(history->text)) {
        return false;
    }
    while (history->count >= BUDDY_HISTORY_TURNS ||
           history->used + said_size + reply_size > sizeof(history->text)) {
        buddy_history_drop_oldest(history);
    }
    history->turns[history->count].said = history->used;
    memcpy(history->text + history->used, said, said_size);
    history->used = (uint16_t)(history->used + said_size);
    history->turns[history->count].reply = history->used;
    memcpy(history->text + history->used, reply, reply_size);
    history->used = (uint16_t)(history->used + reply_size);
    history->turns[history->count].flags = flags;
    ++history->count;
    ++history->revision;
    return true;
}

/* Folds away everything so far: the next turn starts a new conversation on screen. */
static inline bool buddy_history_fold(buddy_history_t *history)
{
    if (history == NULL || history->floor == history->count) {
        return false;
    }
    history->floor = history->count;
    ++history->revision;
    return true;
}

/* Brings the folded turns back. */
static inline bool buddy_history_reveal(buddy_history_t *history)
{
    if (history == NULL || history->floor == 0U) {
        return false;
    }
    history->floor = 0U;
    ++history->revision;
    return true;
}
