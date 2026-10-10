#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "pocket_update_core.h"

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
 * About 320 Chinese characters; the home page scrolls through all of it. */
#define BUDDY_REPLY_MAX 960
/* The conversation with Xiaoyou as a hub host reports it (see buddy_chat_t). */
#define BUDDY_AGENT_MAX 24
#define BUDDY_STAGE_MAX 160
#define BUDDY_HELPER_COUNT 4
#define BUDDY_HELPER_ABOUT_MAX 64
/* Cards the hub has sent, kept in RAM: every request to Xiaoyou is one "thing"
 * with a card of its own. A byte budget for the words and a cap on the number of
 * cards. One card is at most BUDDY_MESSAGE_MAX + BUDDY_REPLY_MAX bytes of words,
 * so the newest always fits. */
#define BUDDY_CARD_BYTES 4096
#define BUDDY_CARD_COUNT 12
#define BUDDY_CARD_ID_MAX 12
#define BUDDY_CARD_AT_MAX 6 /* "HH:MM" */
/* Things in progress, as the hub lists them for the third screen. */
#define BUDDY_TASK_COUNT 4
#define BUDDY_TASK_TITLE_MAX 48
#define BUDDY_TASK_LINE_MAX 64
/* Usage, as the hub lists it for the fourth screen: one entry per subscription
 * login (what is left of its two windows) with the helpers that share it, and
 * one per helper that has no subscription behind it (only its model). */
#define BUDDY_USAGE_COUNT 4
#define BUDDY_USAGE_WHO 2
#define BUDDY_MODEL_MAX 20

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
    BUDDY_PAGE_HOME,    /* first screen: Xiaoyou herself and what she is doing */
    BUDDY_PAGE_TALK,    /* second screen: the conversation, one thing at a time */
    BUDDY_PAGE_TASKS,   /* third screen: the things in progress */
    BUDDY_PAGE_USAGE,   /* fourth screen: what is left of each subscription, and
                         * which model each helper runs on; only in the round
                         * once the hub has sent usage */
    BUDDY_PAGE_MENU,    /* double press OK */
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

/* Where a thing stands (see the runtime's cards). */
typedef enum {
    BUDDY_CARD_TALKING,
    BUDDY_CARD_WORKING,
    BUDDY_CARD_WAITING, /* for the owner's approval */
    BUDDY_CARD_DONE,
    BUDDY_CARD_FAILED,
    BUDDY_CARD_CANCELLED,
} buddy_card_state_t;

typedef enum {
    BUDDY_TASK_WORKING,
    BUDDY_TASK_WAITING, /* for the owner's approval */
    BUDDY_TASK_QUEUED,
    /* Finished things the hub keeps on the list (see buddy_task_active). */
    BUDDY_TASK_DONE,
    BUDDY_TASK_FAILED,
    BUDDY_TASK_CANCELLED,
} buddy_task_state_t;

/* How hard a helper works on a thing (the runtime's effort). */
typedef enum {
    BUDDY_EFFORT_NONE, /* not said: the helper has no levels, or an older hub */
    BUDDY_EFFORT_MINIMAL,
    BUDDY_EFFORT_LOW,
    BUDDY_EFFORT_MEDIUM,
    BUDDY_EFFORT_HIGH,
    BUDDY_EFFORT_XHIGH,
    BUDDY_EFFORT_MAX,
} buddy_effort_t;

/* Where a subscription login stands. */
typedef enum {
    BUDDY_QUOTA_UNKNOWN,
    BUDDY_QUOTA_OK,
    BUDDY_QUOTA_WARN, /* a window is nearly used up */
    BUDDY_QUOTA_OUT,  /* a window is used up */
    BUDDY_QUOTA_NONE, /* no subscription: paid by use, nothing to count down */
} buddy_quota_t;

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
    BUDDY_EVENT_KEY_DOUBLE, /* two quick presses; only OK is reported this way */
    BUDDY_EVENT_CARD,
    BUDDY_EVENT_TASKS,
    /* The conversation screen was scrolled past the end of a card: go to the
     * previous (key UP) or next (key DOWN) one. Raised by the interface. */
    BUDDY_EVENT_CARD_STEP,
    BUDDY_EVENT_FIRMWARE,   /* {"cmd":"fw",…}: goes to the updater, not to the state machine */
    BUDDY_EVENT_USAGE,
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
    char stage[BUDDY_STAGE_MAX];   /* the title of the thing she handed over */
    char card[BUDDY_CARD_ID_MAX];  /* the card this turn went onto; empty when there is none */
    unsigned doing;                /* how many things are in progress in the background */
    uint8_t effort;                /* buddy_effort_t the helper works at; NONE when not said */
} buddy_chat_t;

/* One card. Its words live in buddy_cards_t.text. */
typedef struct {
    char id[BUDDY_CARD_ID_MAX];
    char at[BUDDY_CARD_AT_MAX];    /* when it began, "HH:MM" */
    char agent[BUDDY_AGENT_MAX];   /* who is or was on it; empty: Xiaoyou answered herself */
    uint16_t said;                 /* offset of the first thing the owner said; may be empty */
    uint16_t reply;                /* offset of the latest thing Xiaoyou said */
    uint8_t state;                 /* buddy_card_state_t */
    uint8_t edits;                 /* how many times it was added to or changed */
    bool cut;                      /* reply was longer than the device keeps */
    uint8_t effort;                /* buddy_effort_t the helper works or worked at */
} buddy_card_t;

/* Cards in the order they were opened, oldest first. See buddy_cards.h. */
typedef struct {
    char text[BUDDY_CARD_BYTES];
    buddy_card_t cards[BUDDY_CARD_COUNT];
    uint16_t used;
    uint8_t count;
    uint32_t revision; /* goes up whenever anything above changes */
} buddy_cards_t;

/* A card as it arrives from the hub; said travels in card_said and the reply in
 * the event's reply field. */
typedef struct {
    char id[BUDDY_CARD_ID_MAX];
    char at[BUDDY_CARD_AT_MAX];
    char agent[BUDDY_AGENT_MAX];
    buddy_card_state_t state;
    uint8_t edits;
    bool clear; /* forget every card (the hub is about to send them again) */
    uint8_t effort; /* buddy_effort_t */
} buddy_card_update_t;

/* A thing handed to a helper: in progress, or one of the latest that ended. */
typedef struct {
    char id[BUDDY_CARD_ID_MAX];
    char agent[BUDDY_AGENT_MAX];
    char title[BUDDY_TASK_TITLE_MAX];
    char line1[BUDDY_TASK_LINE_MAX]; /* the latest two steps, as they are; for a
                                      * thing that ended, line1 is how it ended */
    char line2[BUDDY_TASK_LINE_MAX];
    buddy_task_state_t state;
    uint32_t seconds; /* how long it had been going when the hub said so */
    uint8_t effort;   /* buddy_effort_t */
} buddy_task_t;

/* A helper on the usage screen. */
typedef struct {
    char name[BUDDY_AGENT_MAX];
    char model[BUDDY_MODEL_MAX]; /* the model it runs on; empty until it has run once */
    uint8_t effort;              /* buddy_effort_t it usually works at */
    uint8_t running;             /* how many things it is working on right now */
} buddy_usage_who_t;

/* One entry of the usage screen. left_*: percent that remains, -1 when unknown.
 * reset_*: when the window starts over, Unix seconds, 0 when unknown. */
typedef struct {
    buddy_usage_who_t who[BUDDY_USAGE_WHO];
    uint32_t reset_short;
    uint32_t reset_week;
    uint32_t age; /* how many seconds old the numbers were when the hub sent them */
    int8_t left_short;
    int8_t left_week;
    uint8_t state; /* buddy_quota_t */
    uint8_t who_count;
} buddy_usage_t;

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
    buddy_card_update_t card;
    char card_said[BUDDY_MESSAGE_MAX];
    buddy_task_t tasks[BUDDY_TASK_COUNT];
    unsigned task_count;
    pocket_update_command_t firmware;
    buddy_usage_t usage[BUDDY_USAGE_COUNT];
    unsigned usage_count;
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
    /* VOICE_START: the thing on screen when the key went down; empty for none. */
    char voice_card[BUDDY_CARD_ID_MAX];
    /* VOICE_START: said from inside that thing (the task screen), so it belongs
     * to it whatever the words are. */
    bool voice_pin;
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
    /* When the current phase of the turn began; the first screen counts up from it. */
    uint64_t chat_since_ms;
    /* The cards. Points into the state the snapshot was taken from and is only
     * valid on the task that owns that state; may be NULL. */
    const buddy_cards_t *cards;
    /* Which card the conversation screen shows: an index into cards, or -1 when
     * there is none. card_live: instead of a card, show the turn in progress
     * (chat and reply above), because it has no card yet or never gets one. */
    int card_index;
    bool card_live;
    /* The talk page shows the selected task's own exchange (entered from the
     * third screen), not the conversation: card_index is that task's card, or
     * -1 when the device does not hold it. */
    bool card_thread;
    /* Goes up whenever the conversation screen should start again from the top
     * of what it shows (another card, or a new turn). */
    uint32_t card_serial;
    /* The task screen's list: things in progress first, then the latest that
     * ended; tasks_since_ms is when the list arrived. */
    buddy_task_t tasks[BUDDY_TASK_COUNT];
    unsigned task_count;
    unsigned task_selected;
    uint64_t tasks_since_ms;
    /* The usage screen: usage_known says the hub sends usage at all (the screen
     * is then in the round), usage_since_ms when the list arrived. */
    buddy_usage_t usage[BUDDY_USAGE_COUNT];
    unsigned usage_count;
    uint64_t usage_since_ms;
    bool usage_known;
    /* How many things are in progress in the background, for the top bar. */
    unsigned doing;
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
    /* A new firmware image is arriving over Bluetooth; filled in by the
     * application task. Percent is how much of it is in flash. */
    pocket_update_phase_t update_phase;
    uint8_t update_percent;
} buddy_ui_snapshot_t;
