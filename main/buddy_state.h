#pragma once

#include "buddy_types.h"

typedef struct {
    buddy_connection_t connection;
    buddy_character_t character;
    buddy_page_t page;
    buddy_heartbeat_t heartbeat;
    buddy_prompt_t prompt;
    buddy_settings_snapshot_t settings;
    char last_attempted_prompt_id[BUDDY_PROMPT_ID_MAX];
    char last_successful_decision_id[BUDDY_PROMPT_ID_MAX];
    char name[BUDDY_NAME_MAX];
    char owner[BUDDY_OWNER_MAX];
    char time[BUDDY_MESSAGE_MAX];
    char message[BUDDY_MESSAGE_MAX];
    /* When the text in message last changed (and a hash to notice the change):
     * with a hub host the line is a passing notice, not a standing status. */
    uint32_t message_hash;
    uint64_t message_since_ms;
    char entries[BUDDY_ENTRY_COUNT][BUDDY_ENTRY_MAX];
    unsigned total;
    unsigned running;
    unsigned waiting;
    uint64_t tokens;
    uint64_t tokens_today;
    int64_t epoch_seconds;
    int32_t timezone_offset_seconds;
    uint64_t time_received_ms;
    uint64_t highest_celebrated_level;
    uint64_t last_heartbeat_ms;
    uint64_t temporary_until_ms;
    uint32_t prompt_connection_generation;
    uint32_t confirmation_connection_generation;
    uint32_t ble_connection_generation;
    buddy_character_t temporary_character;
    bool connected;
    bool heartbeat_stale;
    bool confirmation_pending;
    buddy_confirmation_t confirmation;
    bool confirmation_acknowledge;
    buddy_menu_item_t menu_selection;
    buddy_more_item_t more_selection;
    bool screen_off;
    uint8_t brightness_level;
    char reply[BUDDY_REPLY_MAX];
    bool reply_truncated;
    /* The conversation with Xiaoyou, when the host is a hub. */
    buddy_chat_t chat;
    uint64_t chat_since_ms;
    /* The cards the hub has sent. They outlive the connection: what was said
     * stays readable until the hub sends them again. */
    buddy_cards_t cards;
    /* The card the conversation screen shows; empty means the newest. */
    char card_current[BUDDY_CARD_ID_MAX];
    /* The conversation screen goes with the turn in progress: it shows that turn
     * until its card arrives, then that card. Ends when the owner moves to
     * another card himself; starts again with the next turn. */
    bool card_follow;
    /* Goes up whenever the conversation screen should start from the top again. */
    uint32_t card_serial;
    /* Things in progress, the one selected on the third screen, when the list came. */
    buddy_task_t tasks[BUDDY_TASK_COUNT];
    unsigned task_count;
    /* Counts through tasks and then past: the third screen is one list. */
    unsigned task_selected;
    uint64_t tasks_since_ms;
    /* Things that ended, newest first; the hub sends them in parts. */
    buddy_past_t past[BUDDY_PAST_COUNT];
    unsigned past_count;
    /* The thing the selection is on, by name: the lists are replaced while the
     * owner looks at them, and the selection stays with the thing. */
    char task_pick[BUDDY_CARD_ID_MAX];
    /* The talk page is open on a thing from the third screen (entered from
     * there with OK) instead of on the conversation; thread_id is that thing. */
    bool thread;
    char thread_id[BUDDY_CARD_ID_MAX];
    /* Usage as the hub last listed it, and when. usage_known: this hub sends
     * usage, so the fourth screen is in the round. */
    buddy_usage_t usage[BUDDY_USAGE_COUNT];
    unsigned usage_count;
    uint64_t usage_since_ms;
    bool usage_known;
    buddy_helper_t helpers[BUDDY_HELPER_COUNT];
    unsigned helper_count;
    bool host_hub;
    /* The hub reports the conversation with "chat" (a newer phone app). */
    bool host_chat;
    buddy_permission_delivery_t permission_delivery;
    /* What the owner answered to the prompt on screen; NONE until a key decides. */
    buddy_permission_decision_t permission_decision;
    bool approval_locked;
    bool ble_connected;
    bool ble_encrypted;
    bool battery_available;
    bool passkey_visible;
    uint32_t passkey;
    uint8_t battery_percent;
    uint16_t battery_mv;
    /* Push-to-talk. host_voice: the connected host said it accepts voice frames. */
    buddy_voice_phase_t voice_phase;
    uint32_t voice_connection_generation;
    uint64_t voice_listening_since_ms;
    bool host_voice;
} buddy_state_t;

void buddy_state_init(buddy_state_t *state, const buddy_settings_snapshot_t *settings);
void buddy_state_reduce(buddy_state_t *state, const buddy_event_t *event,
                        uint64_t now_ms, buddy_action_t *action);
/* Backlight percentage the display should currently show (0 when the screen is off). */
uint8_t buddy_state_backlight_percent(const buddy_state_t *state);
void buddy_state_snapshot(const buddy_state_t *state, buddy_ui_snapshot_t *snapshot);
