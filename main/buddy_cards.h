/* main/buddy_cards.h - the cards the hub has sent, kept in RAM.
 *
 * Every request to Xiaoyou is one "thing" with a card: what the owner first said
 * and the latest thing Xiaoyou said about it. Cards stay in the order they were
 * opened. Their words share a fixed byte budget; when a card does not fit, the
 * oldest are dropped. A card that arrives again under the same id is updated in
 * place. Header-only and free of ESP-IDF and LVGL so the state machine, the
 * interface and the host tests share it. */
#pragma once

#include <string.h>

#include "buddy_types.h"

static inline const char *buddy_cards_said(const buddy_cards_t *cards, unsigned index)
{
    return cards != NULL && index < cards->count ? cards->text + cards->cards[index].said : "";
}

static inline const char *buddy_cards_reply(const buddy_cards_t *cards, unsigned index)
{
    return cards != NULL && index < cards->count ? cards->text + cards->cards[index].reply : "";
}

/* Index of the card with this id, or -1. */
static inline int buddy_cards_find(const buddy_cards_t *cards, const char *id)
{
    unsigned index;

    if (cards == NULL || id == NULL || id[0] == '\0') {
        return -1;
    }
    for (index = 0; index < cards->count; ++index) {
        if (strncmp(cards->cards[index].id, id, BUDDY_CARD_ID_MAX) == 0) {
            return (int)index;
        }
    }
    return -1;
}

/* A card is a task when it was handed to a helper; the rest is conversation,
 * which Xiaoyou answered herself. The conversation screen pages through the
 * conversation only; tasks have the task screen. */
static inline bool buddy_cards_is_task(const buddy_cards_t *cards, int index)
{
    return cards != NULL && index >= 0 && index < (int)cards->count &&
           cards->cards[index].agent[0] != '\0';
}

/* The nearest conversation card from index on, going in direction (1 or -1);
 * -1 when there is none. */
static inline int buddy_cards_talk_from(const buddy_cards_t *cards, int index, int direction)
{
    for (; cards != NULL && index >= 0 && index < (int)cards->count; index += direction) {
        if (cards->cards[index].agent[0] == '\0') {
            return index;
        }
    }
    return -1;
}

static inline bool buddy_task_active(const buddy_task_t *task)
{
    return task->state == BUDDY_TASK_WORKING || task->state == BUDDY_TASK_WAITING ||
           task->state == BUDDY_TASK_QUEUED;
}

/* Takes the NUL-terminated string at offset out of the pool and closes the gap.
 * Offsets that pointed behind it move down with their strings. */
static inline void buddy_cards_release(buddy_cards_t *cards, uint16_t offset)
{
    uint16_t length = (uint16_t)(strlen(cards->text + offset) + 1U);
    unsigned index;

    memmove(cards->text + offset, cards->text + offset + length,
            (size_t)(cards->used - offset - length));
    cards->used = (uint16_t)(cards->used - length);
    for (index = 0; index < cards->count; ++index) {
        if (cards->cards[index].said > offset) {
            cards->cards[index].said = (uint16_t)(cards->cards[index].said - length);
        }
        if (cards->cards[index].reply > offset) {
            cards->cards[index].reply = (uint16_t)(cards->cards[index].reply - length);
        }
    }
}

/* Frees the words of one card; the card itself stays in the list. */
static inline void buddy_cards_release_words(buddy_cards_t *cards, unsigned index)
{
    uint16_t said = cards->cards[index].said;
    uint16_t reply = cards->cards[index].reply;

    /* The one further back first, so the other offset is still right. */
    buddy_cards_release(cards, said > reply ? said : reply);
    buddy_cards_release(cards, said > reply ? reply : said);
}

static inline void buddy_cards_remove(buddy_cards_t *cards, unsigned index)
{
    buddy_cards_release_words(cards, index);
    memmove(&cards->cards[index], &cards->cards[index + 1U],
            (size_t)(cards->count - index - 1U) * sizeof(cards->cards[0]));
    --cards->count;
}

static inline bool buddy_cards_clear(buddy_cards_t *cards)
{
    if (cards == NULL || cards->count == 0U) {
        return false;
    }
    cards->count = 0;
    cards->used = 0;
    ++cards->revision;
    return true;
}

static inline void buddy_cards_copy(char *destination, size_t size, const char *source)
{
    size_t length = 0;

    while (source != NULL && length + 1U < size && source[length] != '\0') {
        ++length;
    }
    if (length != 0U) {
        memcpy(destination, source, length);
    }
    destination[length] = '\0';
}

/* Adds a card, or updates the one with the same id where it stands. A card
 * without an id is not kept. Returns whether anything changed. */
static inline bool buddy_cards_put(buddy_cards_t *cards, const buddy_card_update_t *update,
                                   const char *said, const char *reply, bool cut)
{
    size_t said_size;
    size_t reply_size;
    buddy_card_t *card;
    int found;
    unsigned index;

    if (cards == NULL || update == NULL || said == NULL || reply == NULL ||
        update->id[0] == '\0') {
        return false;
    }
    said_size = strlen(said) + 1U;
    reply_size = strlen(reply) + 1U;
    if (said_size + reply_size > sizeof(cards->text)) {
        return false;
    }
    found = buddy_cards_find(cards, update->id);
    if (found >= 0) {
        index = (unsigned)found;
        card = &cards->cards[index];
        if (card->state == (uint8_t)update->state && card->edits == update->edits &&
            card->cut == cut && strcmp(card->at, update->at) == 0 &&
            strcmp(card->agent, update->agent) == 0 &&
            strcmp(buddy_cards_said(cards, index), said) == 0 &&
            strcmp(buddy_cards_reply(cards, index), reply) == 0) {
            return false; /* the hub repeats what is already here */
        }
        buddy_cards_release_words(cards, index);
    } else {
        if (cards->count >= BUDDY_CARD_COUNT) {
            buddy_cards_remove(cards, 0);
        }
        index = cards->count++;
        /* No words yet: both offsets point at the end, where nothing is released. */
        cards->cards[index].said = cards->used;
        cards->cards[index].reply = cards->used;
    }
    /* Make room by dropping the oldest of the others. */
    while (cards->used + said_size + reply_size > sizeof(cards->text) && cards->count > 1U) {
        unsigned oldest = index == 0U ? 1U : 0U;

        /* The card being written has no words in the pool right now. */
        cards->cards[index].said = cards->used;
        cards->cards[index].reply = cards->used;
        buddy_cards_remove(cards, oldest);
        if (oldest < index) {
            --index;
        }
    }
    card = &cards->cards[index];
    buddy_cards_copy(card->id, sizeof(card->id), update->id);
    buddy_cards_copy(card->at, sizeof(card->at), update->at);
    buddy_cards_copy(card->agent, sizeof(card->agent), update->agent);
    card->state = (uint8_t)update->state;
    card->edits = update->edits;
    card->cut = cut;
    card->said = cards->used;
    memcpy(cards->text + cards->used, said, said_size);
    cards->used = (uint16_t)(cards->used + said_size);
    card->reply = cards->used;
    memcpy(cards->text + cards->used, reply, reply_size);
    cards->used = (uint16_t)(cards->used + reply_size);
    ++cards->revision;
    return true;
}
