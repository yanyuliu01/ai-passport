#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define BUDDY_NAME_MAX 32
#define BUDDY_OWNER_MAX 32
#define BUDDY_MESSAGE_MAX 160
#define BUDDY_ENTRY_MAX 96
#define BUDDY_ENTRY_COUNT 4
#define BUDDY_PROMPT_ID_MAX 96
#define BUDDY_TOOL_MAX 48
#define BUDDY_COMMAND_MAX 32
#define BUDDY_HINT_MAX 320
#define BUDDY_JSON_LINE_MAX 4096
/* Latest assistant reply kept for display; bytes, cut on a UTF-8 boundary.
 * About 320 Chinese characters: the home page shows the beginning, the reader
 * page scrolls through all of it. */
#define BUDDY_REPLY_MAX 960
/* The conversation with Xiaoyou as a hub host reports it (see buddy_chat_t). */
#define BUDDY_AGENT_MAX 24
#define BUDDY_STAGE_MAX 160
#define BUDDY_HELPER_COUNT 4
#define BUDDY_HELPER_ABOUT_MAX 64

typedef enum {
    BUDDY_CONNECTION_OFFLINE,
    BUDDY_CONNECTION_CONNECTED,
    BUDDY_CONNECTION_PAIRING,
    BUDDY_CONNECTION_CONFIRMING,
} buddy_connection_t;

typedef enum {
    BUDDY_CHARACTER_SLEEP,
    BUDDY_CHARACTER_IDLE,
    BUDDY_CHARACTER_BUSY,
    BUDDY_CHARACTER_ATTENTION,
    BUDDY_CHARACTER_DIZZY,
    BUDDY_CHARACTER_HEART,
    BUDDY_CHARACTER_CELEBRATE,
    BUDDY_CHARACTER_PAIRING,
    BUDDY_CHARACTER_CONFIRMATION,
} buddy_character_t;

typedef enum {
    BUDDY_PAGE_HOME,    /* the conversation: Xiaoyou, what was said, her answer */
    BUDDY_PAGE_READER,  /* the whole answer, scrolled with UP and DOWN */
    BUDDY_PAGE_MENU,    /* long press UP */
    BUDDY_PAGE_NOTICES, /* recent entries from the host */
    BUDDY_PAGE_HELPERS, /* the agents Xiaoyou can hand work to */
    BUDDY_PAGE_MORE,    /* rarely used and destructive settings */
    BUDDY_PAGE_GUIDE,
    BUDDY_PAGE_COUNT,
} buddy_page_t;

typedef enum {
    BUDDY_CONFIRM_NONE,
    BUDDY_CONFIRM_UNPAIR,
    BUDDY_CONFIRM_FACTORY_RESET,
} buddy_confirmation_t;

typedef enum {
    BUDDY_MENU_NOTICES,
    BUDDY_MENU_HELPERS,
    BUDDY_MENU_BRIGHTNESS,
    BUDDY_MENU_BLE,
    BUDDY_MENU_SCREEN_OFF,
    BUDDY_MENU_MORE,
    BUDDY_MENU_BACK,
    BUDDY_MENU_COUNT,
} buddy_menu_item_t;

typedef enum {
    BUDDY_MORE_GUIDE,
    BUDDY_MORE_UNPAIR,
    BUDDY_MORE_FACTORY_RESET,
    BUDDY_MORE_BACK,
    BUDDY_MORE_COUNT,
} buddy_more_item_t;

/* Where a turn of the conversation with Xiaoyou stands. */
typedef enum {
    BUDDY_CHAT_NONE,     /* nothing has been said on this connection */
    BUDDY_CHAT_SENT,     /* a recording left the device; the host has not said anything yet */
    BUDDY_CHAT_THINKING, /* Xiaoyou has the question */
    BUDDY_CHAT_HELPER,   /* she handed work to another agent (chat.agent) */
    BUDDY_CHAT_DONE,     /* her answer is in reply */
    BUDDY_CHAT_FAILED,   /* the turn did not work; reply says why */
} buddy_chat_phase_t;

typedef enum {
    BUDDY_MOOD_IDLE,
    BUDDY_MOOD_BUSY,
    BUDDY_MOOD_ASK,
    BUDDY_MOOD_HAPPY,
    BUDDY_MOOD_OOPS,
} buddy_mood_t;

#define BUDDY_BRIGHTNESS_LEVELS 5U

typedef enum {
    BUDDY_KEY_NONE,
    BUDDY_KEY_UP,
    BUDDY_KEY_DOWN,
    BUDDY_KEY_OK,
    BUDDY_KEY_BACK,
} buddy_key_t;

typedef enum {
    BUDDY_EVENT_NONE,
    BUDDY_EVENT_HEARTBEAT,
    BUDDY_EVENT_PROMPT,
    BUDDY_EVENT_TIME,
    BUDDY_EVENT_NAME,
    BUDDY_EVENT_OWNER,
    BUDDY_EVENT_STATUS,
    BUDDY_EVENT_STATUS_REQUEST,
    BUDDY_EVENT_UNPAIR_CONFIRMATION,
    BUDDY_EVENT_BLE_CONNECTED,
    BUDDY_EVENT_BLE_DISCONNECTED,
    BUDDY_EVENT_BLE_PASSKEY,
    BUDDY_EVENT_BLE_ENCRYPTION,
    BUDDY_EVENT_BOND_DELETE_RESULT,
    BUDDY_EVENT_PERMISSION_SEND_RESULT,
    BUDDY_EVENT_KEY_CLICK,
    BUDDY_EVENT_KEY_LONG,
    BUDDY_EVENT_TICK,
    BUDDY_EVENT_TURN,
    BUDDY_EVENT_KEY_RELEASE,
    BUDDY_EVENT_HOST_HELLO,
    BUDDY_EVENT_VOICE,
    BUDDY_EVENT_CHAT,
    BUDDY_EVENT_HELPERS,
} buddy_event_type_t;

typedef enum {
    BUDDY_ACTION_NONE,
    BUDDY_ACTION_UI_REFRESH,
    BUDDY_ACTION_PERMISSION,
    BUDDY_ACTION_SETTINGS,
    BUDDY_ACTION_STATUS,
    BUDDY_ACTION_UNPAIR_CONFIRMED,
    BUDDY_ACTION_FACTORY_RESET_CONFIRMED,
    BUDDY_ACTION_BLE_TOGGLE,
    BUDDY_ACTION_UI_SCROLL,
    BUDDY_ACTION_DISPLAY_BACKLIGHT,
    BUDDY_ACTION_SCREEN_OFF,
    BUDDY_ACTION_VOICE_START,
    BUDDY_ACTION_VOICE_STOP,
} buddy_action_type_t;

/* Push-to-talk: what the device is doing with the microphone right now. */
typedef enum {
    BUDDY_VOICE_IDLE,
    BUDDY_VOICE_PREPARING, /* key held, microphone starting */
    BUDDY_VOICE_LISTENING, /* recording and streaming */
    BUDDY_VOICE_SENDING,   /* key released, flushing the tail */
} buddy_voice_phase_t;

/* Reports from the voice worker. */
typedef enum {
    BUDDY_VOICE_STARTED,     /* microphone is live */
    BUDDY_VOICE_FINISHED,    /* whole recording handed to the host */
    BUDDY_VOICE_LIMIT,       /* stopped at the length limit, then handed over */
    BUDDY_VOICE_TOO_SHORT,   /* released almost at once; nothing sent */
    BUDDY_VOICE_CANCELLED,   /* stopped on request; nothing more to report */
    BUDDY_VOICE_FAILED_MIC,  /* audio hardware did not start or stopped working */
    BUDDY_VOICE_FAILED_LINK, /* connection lost or too slow */
} buddy_voice_status_t;

typedef enum {
    BUDDY_PERMISSION_NONE,
    BUDDY_PERMISSION_ONCE,
    BUDDY_PERMISSION_ALWAYS,
    BUDDY_PERMISSION_DENY,
} buddy_permission_decision_t;

typedef enum {
    BUDDY_PERMISSION_DELIVERY_NONE,
    BUDDY_PERMISSION_DELIVERY_SENDING,
    BUDDY_PERMISSION_DELIVERY_SENT,
    BUDDY_PERMISSION_DELIVERY_FAILED,
} buddy_permission_delivery_t;

typedef struct {
    char id[BUDDY_PROMPT_ID_MAX];
    char tool[BUDDY_TOOL_MAX];
    char hint[BUDDY_HINT_MAX];
    size_t id_length;
    unsigned running;
    bool id_truncated;
    bool tool_truncated;
    bool hint_truncated;
    bool connected;
} buddy_prompt_t;

typedef struct {
    char message[BUDDY_MESSAGE_MAX];
    char entries[BUDDY_ENTRY_COUNT][BUDDY_ENTRY_MAX];
    unsigned total;
    unsigned running;
    unsigned waiting;
    uint64_t tokens;
    uint64_t tokens_today;
    bool connected;
    bool message_truncated;
    bool entries_truncated[BUDDY_ENTRY_COUNT];
    buddy_prompt_t prompt;
} buddy_heartbeat_t;

typedef struct {
    int64_t epoch_seconds;
    int32_t timezone_offset_seconds;
} buddy_time_sync_t;

/* One turn with Xiaoyou, as a hub host reports it. Her words travel in the
 * event's (and the state's) reply field. */
typedef struct {
    buddy_chat_phase_t phase;
    buddy_mood_t mood;
    char said[BUDDY_MESSAGE_MAX];  /* what the owner said; empty until transcribed */
    char agent[BUDDY_AGENT_MAX];   /* who is working on it, when it is not Xiaoyou herself */
    char stage[BUDDY_STAGE_MAX];   /* what she said when she handed the work over */
} buddy_chat_t;

/* An agent Xiaoyou can hand work to. */
typedef struct {
    char name[BUDDY_AGENT_MAX];
    char about[BUDDY_HELPER_ABOUT_MAX];
} buddy_helper_t;

typedef struct {
    char name[BUDDY_COMMAND_MAX];
    char value[BUDDY_MESSAGE_MAX];
    bool value_truncated;
} buddy_command_t;

typedef struct {
    char name[BUDDY_NAME_MAX];
    char owner[BUDDY_OWNER_MAX];
    uint64_t approval_count;
    uint64_t denial_count;
    uint64_t highest_celebrated_level;
    bool ble_enabled;
} buddy_settings_snapshot_t;

typedef struct {
    uint32_t passkey;
    uint32_t connection_generation;
    int status;
    bool secure;
    bool success;
} buddy_ble_state_event_t;

typedef struct {
    char id[BUDDY_PROMPT_ID_MAX];
    size_t id_length;
    buddy_permission_decision_t decision;
    bool success;
} buddy_permission_result_event_t;

typedef struct {
    buddy_event_type_t type;
    buddy_key_t key;
    buddy_heartbeat_t heartbeat;
    buddy_prompt_t prompt;
    buddy_time_sync_t time;
    buddy_command_t command;
    buddy_ble_state_event_t ble;
    buddy_permission_result_event_t permission_result;
    char observed_prompt_id[BUDDY_PROMPT_ID_MAX];
    size_t observed_prompt_id_length;
    bool has_observed_prompt_id;
    bool observed_prompt_id_truncated;
    char reply[BUDDY_REPLY_MAX];
    bool reply_truncated;
    buddy_voice_status_t voice_status;
    bool host_voice;
    buddy_chat_t chat;
    buddy_helper_t helpers[BUDDY_HELPER_COUNT];
    unsigned helper_count;
} buddy_event_t;

typedef struct {
    char id[BUDDY_PROMPT_ID_MAX];
    char tool[BUDDY_TOOL_MAX];
    char hint[BUDDY_HINT_MAX];
    buddy_permission_decision_t decision;
    uint32_t connection_generation;
} buddy_permission_action_t;

typedef struct {
    buddy_action_type_t type;
    buddy_permission_action_t permission;
    buddy_settings_snapshot_t settings;
    char message[BUDDY_MESSAGE_MAX];
    int scroll_delta;
    uint8_t brightness_percent;
    uint32_t connection_generation;
    bool ble_enabled;
    bool confirmation_acknowledge;
    bool voice_cancel;
} buddy_action_t;

typedef struct {
    buddy_connection_t connection;
    buddy_character_t character;
    buddy_page_t page;
    char name[BUDDY_NAME_MAX];
    char owner[BUDDY_OWNER_MAX];
    char time[BUDDY_MESSAGE_MAX];
    char message[BUDDY_MESSAGE_MAX];
    char entries[BUDDY_ENTRY_COUNT][BUDDY_ENTRY_MAX];
    char prompt_id[BUDDY_PROMPT_ID_MAX];
    char prompt_tool[BUDDY_TOOL_MAX];
    char prompt_hint[BUDDY_HINT_MAX];
    unsigned total;
    unsigned running;
    unsigned waiting;
    uint64_t tokens;
    uint64_t tokens_today;
    int64_t epoch_seconds;
    int32_t timezone_offset_seconds;
    uint64_t time_received_ms;
    bool heartbeat_stale;
    bool confirmation_pending;
    buddy_confirmation_t confirmation;
    buddy_menu_item_t menu_selection;
    buddy_more_item_t more_selection;
    bool screen_off;
    uint8_t brightness_level;
    char reply[BUDDY_REPLY_MAX];
    bool reply_truncated;
    buddy_chat_t chat;
    /* When the current phase of the turn began; the home page counts up from it. */
    uint64_t chat_since_ms;
    buddy_helper_t helpers[BUDDY_HELPER_COUNT];
    unsigned helper_count;
    /* The host introduced itself as a hub (the phone), not the Claude desktop app. */
    bool host_hub;
    /* The hub reports the conversation itself; heartbeat counters are then only
     * about notifications and say nothing about Xiaoyou being busy. */
    bool host_chat;
    /* When the text in message last changed. */
    uint64_t message_since_ms;
    bool prompt_hint_truncated;
    uint64_t approval_count;
    uint64_t denial_count;
    uint64_t uptime_ms;
    bool approval_locked;
    buddy_permission_delivery_t permission_delivery;
    buddy_permission_decision_t permission_decision;
    bool ble_connected;
    bool ble_encrypted;
    bool ble_enabled;
    bool battery_available;
    bool passkey_visible;
    uint32_t prompt_connection_generation;
    uint32_t confirmation_connection_generation;
    uint32_t passkey;
    uint8_t battery_percent;
    uint16_t battery_mv;
    buddy_voice_phase_t voice_phase;
    uint64_t voice_listening_since_ms;
    /* Microphone level right now, 0 to 100; filled in by the application task. */
    uint8_t voice_level;
} buddy_ui_snapshot_t;
