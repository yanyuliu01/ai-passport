#include "buddy_state.h"

#include <stddef.h>
#include <string.h>

#include "buddy_cards.h"
#include "pocket_text.h"

#define BUDDY_HEART_ANIMATION_MS 5000
#define BUDDY_CELEBRATION_ANIMATION_MS 1500
#define BUDDY_HEARTBEAT_TIMEOUT_MS 30000
#define BUDDY_TOKEN_CELEBRATION_STEP 50000

static void buddy_copy(char *destination, size_t destination_size, const char *source)
{
    size_t length = 0;

    if (destination_size == 0) {
        return;
    }
    if (source != NULL) {
        while (length + 1 < destination_size && source[length] != '\0') {
            ++length;
        }
        memcpy(destination, source, length);
    }
    destination[length] = '\0';
}

static void buddy_copy_entries(char destination[BUDDY_ENTRY_COUNT][BUDDY_ENTRY_MAX],
                               const char source[BUDDY_ENTRY_COUNT][BUDDY_ENTRY_MAX])
{
    unsigned index;

    for (index = 0; index < BUDDY_ENTRY_COUNT; ++index) {
        buddy_copy(destination[index], BUDDY_ENTRY_MAX, source[index]);
    }
}

static bool buddy_string_matches_length(const char *value, size_t value_size, size_t length)
{
    size_t index;

    if (length == 0 || length >= value_size) {
        return false;
    }
    for (index = 0; index < value_size; ++index) {
        if (value[index] == '\0') {
            return index == length;
        }
    }
    return false;
}

static bool buddy_prompt_id_is_valid(const buddy_prompt_t *prompt)
{
    return !prompt->id_truncated &&
           buddy_string_matches_length(prompt->id, sizeof(prompt->id), prompt->id_length);
}

static void buddy_invalidate_prompt(buddy_state_t *state)
{
    memset(&state->prompt, 0, sizeof(state->prompt));
    state->prompt_connection_generation = 0;
    state->approval_locked = false;
    state->permission_decision = BUDDY_PERMISSION_NONE;
}

static bool buddy_chat_in_progress(buddy_chat_phase_t phase)
{
    return phase == BUDDY_CHAT_SENT || phase == BUDDY_CHAT_THINKING ||
           phase == BUDDY_CHAT_HELPER;
}

static void buddy_clear_live_turn(buddy_state_t *state)
{
    state->reply[0] = '\0';
    state->reply_truncated = false;
    memset(&state->chat, 0, sizeof(state->chat));
    state->chat_since_ms = 0;
}

/* A new turn begins: the conversation screen goes with it. */
static void buddy_begin_turn(buddy_state_t *state)
{
    state->card_follow = true;
    state->card_current[0] = '\0';
    ++state->card_serial;
}

/* Which card the conversation screen shows: the one the owner paged to, the
 * one the turn in progress went onto, otherwise the newest of the conversation.
 * -1 when there is none. Only the followed turn can be a task (the screen then
 * says where it went instead of showing it); paging never lands on one. */
static int buddy_card_index(const buddy_state_t *state)
{
    int index = buddy_cards_find(&state->cards, state->card_current);

    if (index < 0 && state->card_follow) {
        index = buddy_cards_find(&state->cards, state->chat.card);
    }
    return index >= 0 ? index
                      : buddy_cards_talk_from(&state->cards, (int)state->cards.count - 1, -1);
}

/* Whether the conversation screen shows the turn in progress instead of a card:
 * it has no card yet (a recording being transcribed, Xiaoyou still thinking), it
 * never gets one (the recording was not understood), or the host does not send
 * cards at all (the Claude desktop app, an older phone app). */
static bool buddy_card_live(const buddy_state_t *state)
{
    bool turn = state->chat.phase != BUDDY_CHAT_NONE || state->reply[0] != '\0';

    if (!turn) {
        return false;
    }
    if (state->cards.count == 0U) {
        return true;
    }
    return state->card_follow && state->chat.phase != BUDDY_CHAT_NONE &&
           buddy_cards_find(&state->cards, state->chat.card) < 0;
}

static void buddy_clear_logical_session(buddy_state_t *state)
{
    state->connected = false;
    state->connection = BUDDY_CONNECTION_OFFLINE;
    state->total = 0;
    state->running = 0;
    state->waiting = 0;
    state->tokens = 0;
    state->tokens_today = 0;
    state->message[0] = '\0';
    memset(state->entries, 0, sizeof(state->entries));
    state->heartbeat.total = 0;
    state->heartbeat.running = 0;
    state->heartbeat.waiting = 0;
    state->heartbeat.tokens = 0;
    state->heartbeat.tokens_today = 0;
    state->heartbeat.message[0] = '\0';
    memset(state->heartbeat.entries, 0, sizeof(state->heartbeat.entries));
    /* The cards stay readable; the turn in progress and the list of things in
     * progress are gone, because nobody is reporting them any more. */
    buddy_clear_live_turn(state);
    memset(state->tasks, 0, sizeof(state->tasks));
    state->task_count = 0;
    state->task_selected = 0;
    buddy_invalidate_prompt(state);
}

/* What belongs to the host on the other end, not to the session it reports. */
static void buddy_forget_host(buddy_state_t *state)
{
    state->host_voice = false;
    state->host_hub = false;
    state->host_chat = false;
    memset(state->helpers, 0, sizeof(state->helpers));
    state->helper_count = 0;
}

static buddy_character_t buddy_character_for(const buddy_state_t *state, uint64_t now_ms)
{
    bool has_prompt = state->prompt.id[0] != '\0' && !state->heartbeat_stale &&
                      !state->approval_locked;

    if (state->confirmation_pending || state->connection == BUDDY_CONNECTION_CONFIRMING) {
        return BUDDY_CHARACTER_CONFIRMATION;
    }
    if (state->connection == BUDDY_CONNECTION_PAIRING) {
        return BUDDY_CHARACTER_PAIRING;
    }
    if (has_prompt) {
        return BUDDY_CHARACTER_ATTENTION;
    }
    if (state->temporary_until_ms > now_ms) {
        return state->temporary_character;
    }
    if (state->running > 0) {
        return BUDDY_CHARACTER_BUSY;
    }
    if (state->connected && !state->heartbeat_stale) {
        return BUDDY_CHARACTER_IDLE;
    }
    return BUDDY_CHARACTER_SLEEP;
}

static void buddy_refresh_character(buddy_state_t *state, uint64_t now_ms)
{
    state->character = buddy_character_for(state, now_ms);
}

static void buddy_clear_stale_prompt(buddy_state_t *state, uint64_t now_ms)
{
    if (!state->heartbeat_stale && now_ms - state->last_heartbeat_ms >= BUDDY_HEARTBEAT_TIMEOUT_MS) {
        state->heartbeat_stale = true;
        buddy_clear_logical_session(state);
    }
}

static void buddy_set_ui_refresh(buddy_action_t *action)
{
    if (action != NULL) {
        action->type = BUDDY_ACTION_UI_REFRESH;
    }
}

static bool buddy_has_actionable_prompt(const buddy_state_t *state)
{
    return state->prompt.id[0] != '\0' && !state->heartbeat_stale &&
           !state->approval_locked && buddy_prompt_id_is_valid(&state->prompt);
}

static bool buddy_has_prompt(const buddy_state_t *state)
{
    return state->prompt.id[0] != '\0';
}

static void buddy_open_confirmation(buddy_state_t *state, buddy_confirmation_t confirmation,
                                    bool acknowledge, uint32_t connection_generation,
                                    buddy_action_t *action)
{
    buddy_invalidate_prompt(state);
    state->confirmation = confirmation;
    state->confirmation_pending = confirmation != BUDDY_CONFIRM_NONE;
    state->confirmation_acknowledge = acknowledge;
    state->confirmation_connection_generation = connection_generation;
    state->connection = BUDDY_CONNECTION_CONFIRMING;
    buddy_set_ui_refresh(action);
}

static void buddy_close_confirmation(buddy_state_t *state)
{
    state->confirmation = BUDDY_CONFIRM_NONE;
    state->confirmation_pending = false;
    state->confirmation_acknowledge = false;
    state->confirmation_connection_generation = 0;
    state->connection = state->connected ? BUDDY_CONNECTION_CONNECTED
                                         : BUDDY_CONNECTION_OFFLINE;
}

static uint8_t buddy_brightness_percent(uint8_t level)
{
    if (level >= BUDDY_BRIGHTNESS_LEVELS) {
        level = BUDDY_BRIGHTNESS_LEVELS - 1U;
    }
    return (uint8_t)(20U + level * 20U);
}

static void buddy_menu_click(buddy_state_t *state, buddy_key_t key, buddy_action_t *action)
{
    if (key == BUDDY_KEY_UP) {
        state->menu_selection =
            (buddy_menu_item_t)((state->menu_selection + BUDDY_MENU_COUNT - 1) %
                                BUDDY_MENU_COUNT);
        buddy_set_ui_refresh(action);
        return;
    }
    if (key == BUDDY_KEY_DOWN) {
        state->menu_selection =
            (buddy_menu_item_t)((state->menu_selection + 1) % BUDDY_MENU_COUNT);
        buddy_set_ui_refresh(action);
        return;
    }
    if (key != BUDDY_KEY_OK) {
        return;
    }

    switch (state->menu_selection) {
    case BUDDY_MENU_NOTICES:
        state->page = BUDDY_PAGE_NOTICES;
        buddy_set_ui_refresh(action);
        break;
    case BUDDY_MENU_HELPERS:
        state->page = BUDDY_PAGE_HELPERS;
        buddy_set_ui_refresh(action);
        break;
    case BUDDY_MENU_BRIGHTNESS:
        state->brightness_level =
            (uint8_t)((state->brightness_level + 1U) % BUDDY_BRIGHTNESS_LEVELS);
        if (action != NULL) {
            action->type = BUDDY_ACTION_DISPLAY_BACKLIGHT;
            action->brightness_percent = buddy_brightness_percent(state->brightness_level);
        }
        break;
    case BUDDY_MENU_BLE:
        state->settings.ble_enabled = !state->settings.ble_enabled;
        if (action != NULL) {
            action->type = BUDDY_ACTION_BLE_TOGGLE;
            action->ble_enabled = state->settings.ble_enabled;
        }
        break;
    case BUDDY_MENU_SCREEN_OFF:
        state->screen_off = true;
        state->page = BUDDY_PAGE_HOME;
        if (action != NULL) {
            action->type = BUDDY_ACTION_SCREEN_OFF;
        }
        break;
    case BUDDY_MENU_MORE:
        state->page = BUDDY_PAGE_MORE;
        state->more_selection = BUDDY_MORE_GUIDE;
        buddy_set_ui_refresh(action);
        break;
    case BUDDY_MENU_BACK:
    case BUDDY_MENU_COUNT:
        state->page = BUDDY_PAGE_HOME;
        buddy_set_ui_refresh(action);
        break;
    }
}

/* The third screen: UP and DOWN pick a thing. What is held and said there is
 * said inside that thing. The conversation screen stays where it is. */
static void buddy_tasks_click(buddy_state_t *state, buddy_key_t key, buddy_action_t *action)
{
    unsigned selected = state->task_selected;

    if (state->task_count == 0U) {
        return;
    }
    if (key == BUDDY_KEY_UP && selected > 0U) {
        --selected;
    } else if (key == BUDDY_KEY_DOWN && selected + 1U < state->task_count) {
        ++selected;
    }
    if (selected != state->task_selected) {
        state->task_selected = selected;
        buddy_set_ui_refresh(action);
    }
}

static void buddy_more_click(buddy_state_t *state, buddy_key_t key, buddy_action_t *action)
{
    if (key == BUDDY_KEY_UP) {
        state->more_selection =
            (buddy_more_item_t)((state->more_selection + BUDDY_MORE_COUNT - 1) %
                                BUDDY_MORE_COUNT);
        buddy_set_ui_refresh(action);
        return;
    }
    if (key == BUDDY_KEY_DOWN) {
        state->more_selection =
            (buddy_more_item_t)((state->more_selection + 1) % BUDDY_MORE_COUNT);
        buddy_set_ui_refresh(action);
        return;
    }
    if (key != BUDDY_KEY_OK) {
        return;
    }

    switch (state->more_selection) {
    case BUDDY_MORE_GUIDE:
        state->page = BUDDY_PAGE_GUIDE;
        buddy_set_ui_refresh(action);
        break;
    case BUDDY_MORE_UNPAIR:
        buddy_open_confirmation(state, BUDDY_CONFIRM_UNPAIR, false, 0, action);
        break;
    case BUDDY_MORE_FACTORY_RESET:
        buddy_open_confirmation(state, BUDDY_CONFIRM_FACTORY_RESET, false, 0, action);
        break;
    case BUDDY_MORE_BACK:
    case BUDDY_MORE_COUNT:
        state->page = BUDDY_PAGE_MENU;
        buddy_set_ui_refresh(action);
        break;
    }
}

static void buddy_scroll(buddy_key_t key, buddy_action_t *action)
{
    if (action != NULL && (key == BUDDY_KEY_UP || key == BUDDY_KEY_DOWN)) {
        action->type = BUDDY_ACTION_UI_SCROLL;
        action->scroll_delta = key == BUDDY_KEY_DOWN ? 60 : -60;
    }
}

static void buddy_normal_click(buddy_state_t *state, buddy_key_t key,
                               buddy_action_t *action)
{
    switch (state->page) {
    case BUDDY_PAGE_MENU:
        buddy_menu_click(state, key, action);
        return;
    case BUDDY_PAGE_MORE:
        buddy_more_click(state, key, action);
        return;
    case BUDDY_PAGE_GUIDE:
        if (key == BUDDY_KEY_OK) {
            state->page = BUDDY_PAGE_MORE;
            buddy_set_ui_refresh(action);
        } else {
            buddy_scroll(key, action);
        }
        return;
    case BUDDY_PAGE_NOTICES:
    case BUDDY_PAGE_HELPERS:
        if (key == BUDDY_KEY_OK) {
            state->page = BUDDY_PAGE_MENU;
            buddy_set_ui_refresh(action);
        }
        return;
    case BUDDY_PAGE_HOME:
    case BUDDY_PAGE_TALK:
    case BUDDY_PAGE_TASKS:
    case BUDDY_PAGE_COUNT:
        break;
    }
    /* The three screens. A short press on OK goes to the next one, round and
     * round; UP and DOWN only act inside the screen that is showing. */
    if (key == BUDDY_KEY_OK) {
        state->page = state->page == BUDDY_PAGE_HOME
                          ? BUDDY_PAGE_TALK
                          : (state->page == BUDDY_PAGE_TALK ? BUDDY_PAGE_TASKS : BUDDY_PAGE_HOME);
        buddy_set_ui_refresh(action);
    } else if (state->page == BUDDY_PAGE_TALK) {
        /* Within the thing on screen; at its end the interface asks for the
         * previous or the next one (BUDDY_EVENT_CARD_STEP). */
        buddy_scroll(key, action);
    } else if (state->page == BUDDY_PAGE_TASKS) {
        buddy_tasks_click(state, key, action);
    }
}

static bool buddy_on_a_screen(const buddy_state_t *state)
{
    return state->page == BUDDY_PAGE_HOME || state->page == BUDDY_PAGE_TALK ||
           state->page == BUDDY_PAGE_TASKS;
}

static bool buddy_prompt_ids_match(const buddy_prompt_t *left, const buddy_prompt_t *right)
{
    return left->id_length == right->id_length &&
           left->id_length > 0 &&
           memcmp(left->id, right->id, left->id_length) == 0;
}

static bool buddy_prompt_was_attempted(const buddy_state_t *state,
                                       const buddy_prompt_t *prompt)
{
    return buddy_string_matches_length(state->last_attempted_prompt_id,
                                       sizeof(state->last_attempted_prompt_id),
                                       prompt->id_length) &&
           memcmp(state->last_attempted_prompt_id, prompt->id, prompt->id_length) == 0;
}

static void buddy_apply_heartbeat(buddy_state_t *state, const buddy_heartbeat_t *heartbeat,
                                  uint32_t connection_generation, uint64_t now_ms,
                                  buddy_action_t *action)
{
    uint64_t level = heartbeat->tokens / BUDDY_TOKEN_CELEBRATION_STEP;

    state->heartbeat = *heartbeat;
    state->connected = heartbeat->connected;
    state->connection = heartbeat->connected ? BUDDY_CONNECTION_CONNECTED : BUDDY_CONNECTION_OFFLINE;
    state->heartbeat_stale = !heartbeat->connected;
    if (!heartbeat->connected) {
        buddy_invalidate_prompt(state);
    } else if (heartbeat->prompt.id[0] == '\0') {
        buddy_invalidate_prompt(state);
    } else if (!buddy_prompt_id_is_valid(&heartbeat->prompt)) {
        buddy_invalidate_prompt(state);
    } else if (state->prompt.id[0] != '\0' &&
               buddy_prompt_ids_match(&state->prompt, &heartbeat->prompt)) {
        state->prompt = heartbeat->prompt;
        state->prompt_connection_generation = connection_generation;
    } else if (buddy_prompt_was_attempted(state, &heartbeat->prompt)) {
        buddy_invalidate_prompt(state);
    } else {
        state->prompt = heartbeat->prompt;
        state->prompt_connection_generation = connection_generation;
        state->approval_locked = false;
        state->permission_delivery = BUDDY_PERMISSION_DELIVERY_NONE;
        state->permission_decision = BUDDY_PERMISSION_NONE;
    }
    state->last_heartbeat_ms = now_ms;
    state->running = heartbeat->running;
    state->total = heartbeat->total;
    state->waiting = heartbeat->waiting;
    state->tokens = heartbeat->tokens;
    state->tokens_today = heartbeat->tokens_today;
    buddy_copy(state->message, sizeof(state->message), heartbeat->message);
    buddy_copy_entries(state->entries, heartbeat->entries);

    if (level > state->highest_celebrated_level) {
        state->highest_celebrated_level = level;
        state->settings.highest_celebrated_level = level;
        state->temporary_character = BUDDY_CHARACTER_CELEBRATE;
        state->temporary_until_ms = now_ms + BUDDY_CELEBRATION_ANIMATION_MS;
        if (action != NULL) {
            action->type = BUDDY_ACTION_SETTINGS;
            action->settings = state->settings;
        }
        return;
    }
    buddy_set_ui_refresh(action);
}

static void buddy_apply_prompt(buddy_state_t *state, const buddy_prompt_t *prompt,
                               uint32_t connection_generation, uint64_t now_ms,
                               buddy_action_t *action)
{
    if (!prompt->connected || !buddy_prompt_id_is_valid(prompt) ||
        buddy_prompt_was_attempted(state, prompt)) {
        return;
    }

    state->prompt = *prompt;
    state->prompt_connection_generation = connection_generation;
    state->approval_locked = false;
    state->permission_delivery = BUDDY_PERMISSION_DELIVERY_NONE;
    state->permission_decision = BUDDY_PERMISSION_NONE;
    state->connected = true;
    state->connection = BUDDY_CONNECTION_CONNECTED;
    state->heartbeat_stale = false;
    state->last_heartbeat_ms = now_ms;
    state->running = prompt->running;
    buddy_set_ui_refresh(action);
}

static bool buddy_observed_prompt_matches(const buddy_event_t *event, const buddy_prompt_t *prompt)
{
    if (!event->has_observed_prompt_id) {
        return true;
    }
    if (event->observed_prompt_id_truncated ||
        !buddy_string_matches_length(event->observed_prompt_id,
                                     sizeof(event->observed_prompt_id),
                                     event->observed_prompt_id_length)) {
        return false;
    }
    return event->observed_prompt_id_length == prompt->id_length &&
           memcmp(event->observed_prompt_id, prompt->id, prompt->id_length) == 0;
}

static void buddy_decide_prompt(buddy_state_t *state, const buddy_event_t *event,
                                buddy_permission_decision_t decision,
                                buddy_action_t *action)
{
    if (state->approval_locked || state->prompt.id[0] == '\0' || state->heartbeat_stale ||
        !buddy_prompt_id_is_valid(&state->prompt) ||
        !buddy_observed_prompt_matches(event, &state->prompt)) {
        return;
    }

    if (action != NULL) {
        action->type = BUDDY_ACTION_PERMISSION;
        buddy_copy(action->permission.id, sizeof(action->permission.id), state->prompt.id);
        buddy_copy(action->permission.tool, sizeof(action->permission.tool), state->prompt.tool);
        buddy_copy(action->permission.hint, sizeof(action->permission.hint), state->prompt.hint);
        action->permission.decision = decision;
        action->permission.connection_generation = state->prompt_connection_generation;
    }
    buddy_copy(state->last_attempted_prompt_id, sizeof(state->last_attempted_prompt_id),
               state->prompt.id);
    state->approval_locked = true;
    state->permission_delivery = BUDDY_PERMISSION_DELIVERY_SENDING;
    state->permission_decision = decision;
}

static void buddy_apply_permission_result(buddy_state_t *state,
                                          const buddy_permission_result_event_t *result,
                                          uint64_t now_ms, buddy_action_t *action)
{
    if (state->permission_delivery != BUDDY_PERMISSION_DELIVERY_SENDING ||
        !buddy_string_matches_length(result->id, sizeof(result->id), result->id_length) ||
        !buddy_string_matches_length(state->last_attempted_prompt_id,
                                     sizeof(state->last_attempted_prompt_id),
                                     result->id_length) ||
        memcmp(state->last_attempted_prompt_id, result->id, result->id_length) != 0) {
        return;
    }
    if (result->success) {
        buddy_copy(state->last_successful_decision_id,
                   sizeof(state->last_successful_decision_id), result->id);
        state->permission_delivery = BUDDY_PERMISSION_DELIVERY_SENT;
        if (result->decision == BUDDY_PERMISSION_ONCE) {
            state->temporary_character = BUDDY_CHARACTER_HEART;
            state->temporary_until_ms = now_ms + BUDDY_HEART_ANIMATION_MS;
        }
    } else {
        state->permission_delivery = BUDDY_PERMISSION_DELIVERY_FAILED;
    }
    buddy_set_ui_refresh(action);
}

/* The thing a recording started now is about: on the conversation screen the
 * card that is showing (a hint: Xiaoyou still decides), on the third screen the
 * thing that is selected (*pin: it is said inside that thing and stays on it). */
static void buddy_voice_card(const buddy_state_t *state, char *id, size_t size, bool *pin)
{
    id[0] = '\0';
    *pin = false;
    if (state->page == BUDDY_PAGE_TASKS) {
        if (state->task_selected < state->task_count) {
            buddy_cards_copy(id, size, state->tasks[state->task_selected].id);
            *pin = id[0] != '\0';
        }
    } else if (state->page == BUDDY_PAGE_TALK && !buddy_card_live(state)) {
        int index = buddy_card_index(state);

        if (index >= 0) {
            buddy_cards_copy(id, size, state->cards.cards[index].id);
        }
    }
}

/* Long press. On the three screens holding OK is push-to-talk. On the menu and
 * the pages under it, it goes back to the first screen. UP and DOWN have no
 * long press. */
static void buddy_long_press(buddy_state_t *state, buddy_key_t key, buddy_action_t *action)
{
    bool woke = state->screen_off;

    if (state->confirmation != BUDDY_CONFIRM_NONE || buddy_has_prompt(state) ||
        state->passkey_visible || state->voice_phase != BUDDY_VOICE_IDLE) {
        return;
    }
    if (key != BUDDY_KEY_OK) {
        /* Any key held on a dark screen wakes it; that is all UP and DOWN do. */
        if (woke) {
            state->screen_off = false;
            buddy_set_ui_refresh(action);
        }
        return;
    }
    /* Holding OK on a dark screen wakes it and goes straight on to talking. */
    state->screen_off = false;
    buddy_set_ui_refresh(action);
    if (!buddy_on_a_screen(state)) {
        if (!woke) {
            state->page = BUDDY_PAGE_HOME;
        }
        return;
    }
    if (!state->ble_connected || !state->ble_encrypted) {
        buddy_copy(state->message, sizeof(state->message), PT_VOICE_NEED_LINK);
        return;
    }
    if (!state->host_voice) {
        buddy_copy(state->message, sizeof(state->message), PT_VOICE_NO_HOST);
        return;
    }
    state->voice_phase = BUDDY_VOICE_PREPARING;
    state->voice_connection_generation = state->ble_connection_generation;
    state->voice_listening_since_ms = 0;
    if (action != NULL) {
        action->type = BUDDY_ACTION_VOICE_START;
        action->connection_generation = state->ble_connection_generation;
        buddy_voice_card(state, action->voice_card, sizeof(action->voice_card),
                         &action->voice_pin);
    }
}

/* Two quick presses on OK. On the three screens: the menu. On the menu and the
 * pages under it: back to the first screen. Returns false when something else
 * has the screen; the press then counts as one ordinary click, so that pressing
 * OK twice on a prompt neither does nothing nor decides twice. */
static bool buddy_double_press(buddy_state_t *state, const buddy_event_t *event,
                               buddy_action_t *action)
{
    if (event->key != BUDDY_KEY_OK || state->voice_phase != BUDDY_VOICE_IDLE ||
        state->screen_off || state->confirmation != BUDDY_CONFIRM_NONE ||
        buddy_has_prompt(state) || state->passkey_visible) {
        return false;
    }
    if (buddy_on_a_screen(state)) {
        state->page = BUDDY_PAGE_MENU;
        state->menu_selection = BUDDY_MENU_NOTICES;
    } else {
        state->page = BUDDY_PAGE_HOME;
    }
    buddy_set_ui_refresh(action);
    return true;
}

static void buddy_apply_voice(buddy_state_t *state, const buddy_event_t *event,
                              uint64_t now_ms, buddy_action_t *action)
{
    const char *message = NULL;

    if (state->voice_phase == BUDDY_VOICE_IDLE ||
        event->ble.connection_generation != state->voice_connection_generation) {
        return;
    }
    switch (event->voice_status) {
    case BUDDY_VOICE_STARTED:
        /* The key may already be up again; then the tail is on its way out. */
        if (state->voice_phase == BUDDY_VOICE_PREPARING) {
            state->voice_phase = BUDDY_VOICE_LISTENING;
            state->voice_listening_since_ms = now_ms;
        }
        buddy_set_ui_refresh(action);
        return;
    case BUDDY_VOICE_FINISHED:
        /* Nothing to add: the screen itself shows that the question is on its way. */
        break;
    case BUDDY_VOICE_LIMIT:
        message = PT_VOICE_LIMIT;
        break;
    case BUDDY_VOICE_TOO_SHORT:
        message = PT_VOICE_TOO_SHORT;
        break;
    case BUDDY_VOICE_CANCELLED:
        break;
    case BUDDY_VOICE_FAILED_MIC:
        message = PT_VOICE_FAILED_MIC;
        break;
    case BUDDY_VOICE_FAILED_LINK:
        message = PT_VOICE_FAILED_LINK;
        break;
    }
    state->voice_phase = BUDDY_VOICE_IDLE;
    if (message != NULL) {
        buddy_copy(state->message, sizeof(state->message), message);
    }
    if (event->voice_status == BUDDY_VOICE_FINISHED ||
        event->voice_status == BUDDY_VOICE_LIMIT) {
        /* The recording is with the host: a new turn. Until the host says what
         * it heard, the screen shows that a question is on its way. */
        unsigned doing = state->chat.doing;

        buddy_begin_turn(state);
        buddy_clear_live_turn(state);
        state->chat.phase = BUDDY_CHAT_SENT;
        state->chat.mood = BUDDY_MOOD_BUSY;
        state->chat.doing = doing;
        state->chat_since_ms = now_ms;
    }
    buddy_set_ui_refresh(action);
}

static void buddy_apply_chat(buddy_state_t *state, const buddy_event_t *event,
                             uint64_t now_ms, buddy_action_t *action)
{
    buddy_chat_phase_t phase = event->chat.phase;
    bool was_in_progress = buddy_chat_in_progress(state->chat.phase);
    bool answered = phase == BUDDY_CHAT_DONE || phase == BUDDY_CHAT_FAILED;
    bool same_step;

    if (event->ble.connection_generation != state->ble_connection_generation) {
        return;
    }
    state->host_chat = true;
    if (phase == BUDDY_CHAT_HELPER && event->chat.agent[0] == '\0') {
        /* "Somebody else is on it" without saying who is just thinking. */
        phase = BUDDY_CHAT_THINKING;
    }
    if (phase == BUDDY_CHAT_NONE) {
        /* Nothing is being said right now. A recording that has just left the
         * device and is still being transcribed is not wiped by that. */
        if (state->chat.phase != BUDDY_CHAT_SENT) {
            buddy_clear_live_turn(state);
        }
        state->chat.doing = event->chat.doing;
        buddy_set_ui_refresh(action);
        return;
    }
    if (state->chat.phase == BUDDY_CHAT_SENT && answered && event->chat.said[0] != '\0') {
        /* A recording has just left the device, and this is the end of something
         * else (a thing in the background, or the turn before). It is on its card;
         * the wait for what was just said stays on screen. */
        state->chat.doing = event->chat.doing;
        buddy_set_ui_refresh(action);
        return;
    }
    if (buddy_chat_in_progress(phase) && !was_in_progress) {
        /* Something new was said (typed on the phone, for example). */
        buddy_begin_turn(state);
    }
    /* A recording that left the device and the host starting to think about it
     * are one wait as far as the owner is concerned: keep counting. */
    same_step = strcmp(state->chat.agent, event->chat.agent) == 0 &&
                (state->chat.phase == phase ||
                 (state->chat.phase == BUDDY_CHAT_SENT && phase == BUDDY_CHAT_THINKING));
    state->chat = event->chat;
    state->chat.phase = phase;
    buddy_copy(state->reply, sizeof(state->reply), event->reply);
    state->reply_truncated = event->reply_truncated;
    if (!same_step) {
        state->chat_since_ms = now_ms;
    }
    if (answered && was_in_progress) {
        /* The answer to something just asked is worth lighting the screen for. */
        state->screen_off = false;
    }
    buddy_set_ui_refresh(action);
}

static void buddy_apply_card(buddy_state_t *state, const buddy_event_t *event,
                             buddy_action_t *action)
{
    if (event->ble.connection_generation != state->ble_connection_generation) {
        return;
    }
    if (event->card.clear) {
        if (buddy_cards_clear(&state->cards)) {
            state->card_current[0] = '\0';
            ++state->card_serial;
            buddy_set_ui_refresh(action);
        }
        return;
    }
    if (buddy_cards_put(&state->cards, &event->card, event->card_said, event->reply,
                        event->reply_truncated)) {
        buddy_set_ui_refresh(action);
    }
}

static void buddy_apply_tasks(buddy_state_t *state, const buddy_event_t *event,
                              uint64_t now_ms, buddy_action_t *action)
{
    unsigned count = event->task_count < BUDDY_TASK_COUNT ? event->task_count : BUDDY_TASK_COUNT;
    char selected[BUDDY_CARD_ID_MAX] = "";
    unsigned index;

    if (event->ble.connection_generation != state->ble_connection_generation) {
        return;
    }
    if (state->task_selected < state->task_count) {
        buddy_cards_copy(selected, sizeof(selected), state->tasks[state->task_selected].id);
    }
    memset(state->tasks, 0, sizeof(state->tasks));
    memcpy(state->tasks, event->tasks, count * sizeof(state->tasks[0]));
    state->task_count = count;
    state->tasks_since_ms = now_ms;
    /* The selection stays on the same thing when the list is reordered or
     * shortened; when that thing is gone it stays at the same place in the list. */
    if (state->task_selected >= count) {
        state->task_selected = count > 0U ? count - 1U : 0U;
    }
    for (index = 0; index < count; ++index) {
        if (selected[0] != '\0' && strcmp(state->tasks[index].id, selected) == 0) {
            state->task_selected = index;
        }
    }
    buddy_set_ui_refresh(action);
}

/* The conversation screen was scrolled past the end of the card it shows. */
static void buddy_card_step(buddy_state_t *state, buddy_key_t key, buddy_action_t *action)
{
    int index = buddy_card_index(state);
    int direction = key == BUDDY_KEY_DOWN ? 1 : -1;
    int next;
    int turn;

    if (state->page != BUDDY_PAGE_TALK) {
        return;
    }
    if (buddy_card_live(state)) {
        /* From the turn in progress the way back leads to the newest card. */
        if (key != BUDDY_KEY_UP) {
            return;
        }
        next = buddy_cards_talk_from(&state->cards, (int)state->cards.count - 1, -1);
    } else if (index < 0) {
        return;
    } else {
        /* Only through the conversation: things handed to helpers are skipped. */
        next = buddy_cards_talk_from(&state->cards, index + direction, direction);
    }
    turn = buddy_cards_find(&state->cards, state->chat.card);
    if (next < 0 && direction > 0 && !state->card_follow &&
        state->chat.phase != BUDDY_CHAT_NONE &&
        (turn < 0 || turn > index || buddy_cards_is_task(&state->cards, turn))) {
        /* Past the newest card there is the turn itself, which has no card or
         * went to a helper: back to it. */
        buddy_begin_turn(state);
        buddy_set_ui_refresh(action);
        return;
    }
    if (next < 0) {
        return;
    }
    buddy_cards_copy(state->card_current, sizeof(state->card_current),
                     state->cards.cards[next].id);
    state->card_follow = false;
    ++state->card_serial;
    buddy_set_ui_refresh(action);
}

static void buddy_key_click(buddy_state_t *state, const buddy_event_t *event,
                            buddy_action_t *action)
{
    if (state->voice_phase != BUDDY_VOICE_IDLE) {
        return;
    }
    if (state->screen_off) {
        state->screen_off = false;
        if (action != NULL) {
            action->type = BUDDY_ACTION_DISPLAY_BACKLIGHT;
            action->brightness_percent =
                buddy_brightness_percent(state->brightness_level);
        }
        return;
    }
    if (state->confirmation != BUDDY_CONFIRM_NONE && event->key == BUDDY_KEY_OK) {
        buddy_confirmation_t confirmation = state->confirmation;
        bool acknowledge = state->confirmation_acknowledge;
        uint32_t connection_generation = state->confirmation_connection_generation;

        buddy_close_confirmation(state);
        if (action != NULL) {
            action->type = confirmation == BUDDY_CONFIRM_UNPAIR
                               ? BUDDY_ACTION_UNPAIR_CONFIRMED
                               : BUDDY_ACTION_FACTORY_RESET_CONFIRMED;
            action->confirmation_acknowledge = acknowledge;
            action->connection_generation = connection_generation;
        }
        return;
    }
    if (state->confirmation != BUDDY_CONFIRM_NONE && event->key == BUDDY_KEY_DOWN) {
        buddy_close_confirmation(state);
        buddy_set_ui_refresh(action);
        return;
    }
    if (state->confirmation != BUDDY_CONFIRM_NONE) {
        return;
    }
    if (buddy_has_actionable_prompt(state) && event->key == BUDDY_KEY_OK) {
        buddy_decide_prompt(state, event, BUDDY_PERMISSION_ONCE, action);
    } else if (buddy_has_actionable_prompt(state) && event->key == BUDDY_KEY_DOWN) {
        buddy_decide_prompt(state, event, BUDDY_PERMISSION_DENY, action);
    } else if (buddy_has_actionable_prompt(state) && event->key == BUDDY_KEY_UP) {
        if (action != NULL) {
            action->type = BUDDY_ACTION_UI_SCROLL;
            action->scroll_delta = -48;
        }
    } else if (!buddy_has_prompt(state)) {
        buddy_normal_click(state, event->key, action);
    }
}

void buddy_state_init(buddy_state_t *state, const buddy_settings_snapshot_t *settings)
{
    if (state == NULL) {
        return;
    }

    memset(state, 0, sizeof(*state));
    state->connection = BUDDY_CONNECTION_OFFLINE;
    state->page = BUDDY_PAGE_HOME;
    state->heartbeat_stale = true;
    state->card_follow = true;
    state->brightness_level = BUDDY_BRIGHTNESS_LEVELS - 1U;
    if (settings != NULL) {
        state->settings = *settings;
        state->highest_celebrated_level = settings->highest_celebrated_level;
        buddy_copy(state->name, sizeof(state->name), settings->name);
        buddy_copy(state->owner, sizeof(state->owner), settings->owner);
    }
    buddy_refresh_character(state, 0);
}

void buddy_state_reduce(buddy_state_t *state, const buddy_event_t *event,
                        uint64_t now_ms, buddy_action_t *action)
{
    if (action != NULL) {
        memset(action, 0, sizeof(*action));
    }
    if (state == NULL || event == NULL) {
        return;
    }

    buddy_clear_stale_prompt(state, now_ms);

    switch (event->type) {
    case BUDDY_EVENT_HEARTBEAT:
        buddy_apply_heartbeat(state, &event->heartbeat, event->ble.connection_generation,
                              now_ms, action);
        break;
    case BUDDY_EVENT_PROMPT:
        buddy_apply_prompt(state, &event->prompt, event->ble.connection_generation,
                           now_ms, action);
        break;
    case BUDDY_EVENT_TIME:
        state->epoch_seconds = event->time.epoch_seconds;
        state->timezone_offset_seconds = event->time.timezone_offset_seconds;
        state->time_received_ms = now_ms;
        buddy_set_ui_refresh(action);
        break;
    case BUDDY_EVENT_NAME:
        buddy_copy(state->name, sizeof(state->name), event->command.value);
        buddy_set_ui_refresh(action);
        break;
    case BUDDY_EVENT_OWNER:
        buddy_copy(state->owner, sizeof(state->owner), event->command.value);
        buddy_set_ui_refresh(action);
        break;
    case BUDDY_EVENT_STATUS:
        buddy_copy(state->message, sizeof(state->message), event->command.value);
        buddy_set_ui_refresh(action);
        break;
    case BUDDY_EVENT_STATUS_REQUEST:
        if (action != NULL) {
            action->type = BUDDY_ACTION_STATUS;
            action->connection_generation = event->ble.connection_generation;
        }
        break;
    case BUDDY_EVENT_UNPAIR_CONFIRMATION:
        if (state->confirmation == BUDDY_CONFIRM_NONE) {
            buddy_open_confirmation(state, BUDDY_CONFIRM_UNPAIR, true,
                                    event->ble.connection_generation, action);
        }
        break;
    case BUDDY_EVENT_BLE_CONNECTED:
        if (state->ble_connection_generation != event->ble.connection_generation) {
            buddy_invalidate_prompt(state);
            state->passkey_visible = false;
            state->connected = false;
            state->heartbeat_stale = true;
            if (state->confirmation_acknowledge) {
                buddy_close_confirmation(state);
            }
        }
        if (state->ble_connection_generation != event->ble.connection_generation) {
            buddy_forget_host(state);
        }
        state->ble_connection_generation = event->ble.connection_generation;
        state->ble_connected = true;
        state->ble_encrypted = false;
        if (state->confirmation == BUDDY_CONFIRM_NONE) {
            state->connection = BUDDY_CONNECTION_PAIRING;
        }
        buddy_set_ui_refresh(action);
        break;
    case BUDDY_EVENT_BLE_DISCONNECTED:
        state->ble_connection_generation = event->ble.connection_generation;
        state->ble_connected = false;
        state->ble_encrypted = false;
        state->passkey_visible = false;
        state->connected = false;
        state->heartbeat_stale = true;
        buddy_clear_logical_session(state);
        if (state->confirmation_acknowledge) {
            buddy_close_confirmation(state);
        } else if (state->confirmation == BUDDY_CONFIRM_NONE) {
            state->connection = BUDDY_CONNECTION_OFFLINE;
        }
        buddy_forget_host(state);
        buddy_set_ui_refresh(action);
        if (state->voice_phase != BUDDY_VOICE_IDLE) {
            /* The worker notices the lost link itself; this makes sure it stops. */
            state->voice_phase = BUDDY_VOICE_IDLE;
            buddy_copy(state->message, sizeof(state->message), PT_VOICE_FAILED_LINK);
            if (action != NULL) {
                action->type = BUDDY_ACTION_VOICE_STOP;
                action->voice_cancel = true;
            }
        }
        break;
    case BUDDY_EVENT_BLE_PASSKEY:
        if (event->ble.connection_generation != state->ble_connection_generation) {
            break;
        }
        state->passkey = event->ble.passkey;
        state->passkey_visible = true;
        state->connection = BUDDY_CONNECTION_PAIRING;
        buddy_set_ui_refresh(action);
        break;
    case BUDDY_EVENT_BLE_ENCRYPTION:
        if (event->ble.connection_generation != state->ble_connection_generation) {
            break;
        }
        state->ble_encrypted = event->ble.secure;
        if (event->ble.secure) {
            state->passkey_visible = false;
            if (state->confirmation == BUDDY_CONFIRM_NONE) {
                state->connection = state->connected && !state->heartbeat_stale
                                        ? BUDDY_CONNECTION_CONNECTED
                                        : BUDDY_CONNECTION_OFFLINE;
            }
        } else if (state->ble_connected && state->confirmation == BUDDY_CONFIRM_NONE) {
            state->connection = BUDDY_CONNECTION_PAIRING;
        }
        buddy_set_ui_refresh(action);
        break;
    case BUDDY_EVENT_BOND_DELETE_RESULT:
        buddy_copy(state->message, sizeof(state->message),
                   event->ble.success ? PT_MSG_UNPAIRED : PT_MSG_UNPAIR_FAILED);
        buddy_set_ui_refresh(action);
        break;
    case BUDDY_EVENT_PERMISSION_SEND_RESULT:
        buddy_apply_permission_result(state, &event->permission_result, now_ms, action);
        break;
    case BUDDY_EVENT_KEY_CLICK:
        buddy_key_click(state, event, action);
        break;
    case BUDDY_EVENT_KEY_DOUBLE:
        if (!buddy_double_press(state, event, action)) {
            buddy_key_click(state, event, action);
        }
        break;
    case BUDDY_EVENT_CARD_STEP:
        buddy_card_step(state, event->key, action);
        break;
    case BUDDY_EVENT_CARD:
        buddy_apply_card(state, event, action);
        break;
    case BUDDY_EVENT_TASKS:
        buddy_apply_tasks(state, event, now_ms, action);
        break;
    case BUDDY_EVENT_KEY_LONG:
        buddy_long_press(state, event->key, action);
        break;
    case BUDDY_EVENT_KEY_RELEASE:
        if (event->key == BUDDY_KEY_OK &&
            (state->voice_phase == BUDDY_VOICE_PREPARING ||
             state->voice_phase == BUDDY_VOICE_LISTENING)) {
            state->voice_phase = BUDDY_VOICE_SENDING;
            if (action != NULL) {
                action->type = BUDDY_ACTION_VOICE_STOP;
                action->voice_cancel = false;
            }
        }
        break;
    case BUDDY_EVENT_HOST_HELLO:
        if (event->ble.connection_generation == state->ble_connection_generation) {
            state->host_voice = event->host_voice;
            state->host_hub = true;
            buddy_set_ui_refresh(action);
        }
        break;
    case BUDDY_EVENT_CHAT:
        buddy_apply_chat(state, event, now_ms, action);
        break;
    case BUDDY_EVENT_HELPERS:
        if (event->ble.connection_generation == state->ble_connection_generation) {
            unsigned count = event->helper_count < BUDDY_HELPER_COUNT ? event->helper_count
                                                                      : BUDDY_HELPER_COUNT;

            memcpy(state->helpers, event->helpers, sizeof(state->helpers));
            state->helper_count = count;
            buddy_set_ui_refresh(action);
        }
        break;
    case BUDDY_EVENT_VOICE:
        buddy_apply_voice(state, event, now_ms, action);
        break;
    case BUDDY_EVENT_TICK:
        buddy_set_ui_refresh(action);
        break;
    case BUDDY_EVENT_FIRMWARE:
        /* Handled by the orchestrator; nothing here depends on it. */
        break;
    case BUDDY_EVENT_TURN:
        /* The Claude desktop app, and a phone app from before "chat", report a
         * reply this way. A hub that reports the whole conversation with "chat"
         * owns the reply, and a stray turn event must not replace it. */
        if (state->connected && !state->heartbeat_stale && !state->host_chat) {
            if (strcmp(state->reply, event->reply) != 0) {
                /* A new reply: the conversation screen starts from its top. */
                buddy_begin_turn(state);
            }
            buddy_copy(state->reply, sizeof(state->reply), event->reply);
            state->reply_truncated = event->reply_truncated;
            buddy_set_ui_refresh(action);
        }
        break;
    case BUDDY_EVENT_NONE:
        break;
    }

    {
        /* FNV-1a over the notice line: remember when it last changed. */
        uint32_t hash = 2166136261u;
        const unsigned char *cursor;

        for (cursor = (const unsigned char *)state->message; *cursor != '\0'; ++cursor) {
            hash = (hash ^ *cursor) * 16777619u;
        }
        if (hash != state->message_hash) {
            state->message_hash = hash;
            state->message_since_ms = now_ms;
        }
    }
    /* Anything that needs the owner's eyes turns the screen back on. */
    if (state->screen_off &&
        (buddy_has_actionable_prompt(state) || state->passkey_visible ||
         state->confirmation != BUDDY_CONFIRM_NONE)) {
        state->screen_off = false;
    }
    buddy_refresh_character(state, now_ms);
}

uint8_t buddy_state_backlight_percent(const buddy_state_t *state)
{
    if (state == NULL || state->screen_off) {
        return 0;
    }
    return buddy_brightness_percent(state->brightness_level);
}

void buddy_state_snapshot(const buddy_state_t *state, buddy_ui_snapshot_t *snapshot)
{
    unsigned index;

    if (state == NULL || snapshot == NULL) {
        return;
    }

    memset(snapshot, 0, sizeof(*snapshot));
    snapshot->connection = state->connection;
    snapshot->character = state->character;
    snapshot->page = state->page;
    snapshot->running = state->running;
    snapshot->total = state->total;
    snapshot->waiting = state->waiting;
    snapshot->tokens = state->tokens;
    snapshot->tokens_today = state->tokens_today;
    snapshot->epoch_seconds = state->epoch_seconds;
    snapshot->timezone_offset_seconds = state->timezone_offset_seconds;
    snapshot->time_received_ms = state->time_received_ms;
    snapshot->heartbeat_stale = state->heartbeat_stale;
    snapshot->confirmation_pending = state->confirmation_pending;
    snapshot->confirmation = state->confirmation;
    snapshot->menu_selection = state->menu_selection;
    snapshot->more_selection = state->more_selection;
    snapshot->chat = state->chat;
    snapshot->chat_since_ms = state->chat_since_ms;
    snapshot->cards = &state->cards;
    snapshot->card_index = buddy_card_index(state);
    snapshot->card_live = buddy_card_live(state);
    snapshot->card_serial = state->card_serial;
    memcpy(snapshot->tasks, state->tasks, sizeof(snapshot->tasks));
    snapshot->task_count = state->task_count;
    snapshot->task_selected = state->task_selected;
    snapshot->tasks_since_ms = state->tasks_since_ms;
    /* The hub says how many things are in progress; the list shows at most a
     * few of them, so whichever says more is right. */
    snapshot->doing = 0;
    for (index = 0; index < state->task_count; ++index) {
        if (buddy_task_active(&state->tasks[index])) {
            ++snapshot->doing;
        }
    }
    if (state->chat.doing > snapshot->doing) {
        snapshot->doing = state->chat.doing;
    }
    memcpy(snapshot->helpers, state->helpers, sizeof(snapshot->helpers));
    snapshot->helper_count = state->helper_count;
    snapshot->host_hub = state->host_hub;
    snapshot->host_chat = state->host_chat;
    snapshot->message_since_ms = state->message_since_ms;
    snapshot->brightness_level = state->brightness_level;
    snapshot->screen_off = state->screen_off;
    snapshot->reply_truncated = state->reply_truncated;
    buddy_copy(snapshot->reply, sizeof(snapshot->reply), state->reply);
    snapshot->approval_locked = state->approval_locked;
    snapshot->permission_delivery = state->permission_delivery;
    snapshot->permission_decision = state->permission_decision;
    snapshot->ble_connected = state->ble_connected;
    snapshot->ble_encrypted = state->ble_encrypted;
    snapshot->ble_enabled = state->settings.ble_enabled;
    snapshot->battery_available = state->battery_available;
    snapshot->passkey_visible = state->passkey_visible;
    snapshot->prompt_connection_generation = state->prompt_connection_generation;
    snapshot->confirmation_connection_generation =
        state->confirmation_connection_generation;
    snapshot->passkey = state->passkey;
    snapshot->battery_percent = state->battery_percent;
    snapshot->battery_mv = state->battery_mv;
    buddy_copy(snapshot->name, sizeof(snapshot->name), state->name);
    buddy_copy(snapshot->owner, sizeof(snapshot->owner), state->owner);
    buddy_copy(snapshot->time, sizeof(snapshot->time), state->time);
    buddy_copy(snapshot->message, sizeof(snapshot->message), state->message);
    buddy_copy_entries(snapshot->entries, state->entries);
    buddy_copy(snapshot->prompt_id, sizeof(snapshot->prompt_id), state->prompt.id);
    buddy_copy(snapshot->prompt_tool, sizeof(snapshot->prompt_tool), state->prompt.tool);
    buddy_copy(snapshot->prompt_hint, sizeof(snapshot->prompt_hint), state->prompt.hint);
    snapshot->prompt_hint_truncated = state->prompt.hint_truncated;
    snapshot->voice_phase = state->voice_phase;
    snapshot->voice_listening_since_ms = state->voice_listening_since_ms;
}
