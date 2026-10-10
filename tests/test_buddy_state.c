#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "buddy_protocol.h"
#include "buddy_cards.h"
#include "buddy_state.h"
#include "pocket_text.h"

static buddy_event_t test_prompt_event(const char *id, const char *tool,
                                       const char *hint, unsigned running,
                                       unsigned connected)
{
    buddy_event_t event = {0};
    size_t id_length = strlen(id);

    event.type = BUDDY_EVENT_PROMPT;
    event.prompt.id_length = id_length;
    event.prompt.id_truncated = id_length >= sizeof(event.prompt.id);
    snprintf(event.prompt.id, sizeof(event.prompt.id), "%s", id);
    snprintf(event.prompt.tool, sizeof(event.prompt.tool), "%s", tool);
    snprintf(event.prompt.hint, sizeof(event.prompt.hint), "%s", hint);
    event.prompt.running = running;
    event.prompt.connected = connected != 0;
    return event;
}

static void test_set_observed_prompt_id(buddy_event_t *event, const char *id)
{
    size_t id_length = strlen(id);

    event->has_observed_prompt_id = true;
    event->observed_prompt_id_length = id_length;
    event->observed_prompt_id_truncated = id_length >= sizeof(event->observed_prompt_id);
    snprintf(event->observed_prompt_id, sizeof(event->observed_prompt_id), "%s", id);
}

static buddy_event_t test_permission_result_event(const char *id,
                                                   buddy_permission_decision_t decision,
                                                   bool success)
{
    buddy_event_t event = {0};

    event.type = BUDDY_EVENT_PERMISSION_SEND_RESULT;
    event.permission_result.id_length = strlen(id);
    snprintf(event.permission_result.id, sizeof(event.permission_result.id), "%s", id);
    event.permission_result.decision = decision;
    event.permission_result.success = success;
    return event;
}

static buddy_event_t test_heartbeat_event(uint64_t tokens, unsigned running)
{
    buddy_event_t event = {0};

    event.type = BUDDY_EVENT_HEARTBEAT;
    event.heartbeat.connected = true;
    event.heartbeat.total = running + 2U;
    event.heartbeat.running = running;
    event.heartbeat.waiting = 1U;
    event.heartbeat.tokens = tokens;
    event.heartbeat.tokens_today = tokens / 2U;
    snprintf(event.heartbeat.message, sizeof(event.heartbeat.message), "%s", "Working");
    snprintf(event.heartbeat.entries[0], sizeof(event.heartbeat.entries[0]), "%s", "entry");
    return event;
}

static void test_offline_initialization(void)
{
    buddy_state_t state;
    buddy_ui_snapshot_t snapshot;

    buddy_state_init(&state, NULL);
    buddy_state_snapshot(&state, &snapshot);

    assert(state.connection == BUDDY_CONNECTION_OFFLINE);
    assert(snapshot.character == BUDDY_CHARACTER_SLEEP);
    assert(snapshot.page == BUDDY_PAGE_HOME);
}

static void test_heartbeat_mapping(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_event_t heartbeat = test_heartbeat_event(1234, 2);
    buddy_ui_snapshot_t snapshot;

    buddy_state_init(&state, NULL);
    buddy_state_reduce(&state, &heartbeat, 1000, &action);
    buddy_state_snapshot(&state, &snapshot);

    assert(action.type == BUDDY_ACTION_UI_REFRESH);
    assert(state.connection == BUDDY_CONNECTION_CONNECTED);
    assert(state.tokens == 1234);
    assert(state.running == 2);
    assert(snapshot.total == 4);
    assert(snapshot.waiting == 1);
    assert(snapshot.character == BUDDY_CHARACTER_BUSY);
}

static void test_character_priority(void)
{
    struct priority_case {
        buddy_connection_t connection;
        bool has_prompt;
        bool temporary;
        unsigned running;
        buddy_character_t want;
    } cases[] = {
        {BUDDY_CONNECTION_PAIRING, true, true, 1, BUDDY_CHARACTER_PAIRING},
        {BUDDY_CONNECTION_CONFIRMING, true, true, 1, BUDDY_CHARACTER_CONFIRMATION},
        {BUDDY_CONNECTION_CONNECTED, true, true, 1, BUDDY_CHARACTER_ATTENTION},
        {BUDDY_CONNECTION_CONNECTED, false, true, 1, BUDDY_CHARACTER_HEART},
        {BUDDY_CONNECTION_CONNECTED, false, false, 1, BUDDY_CHARACTER_BUSY},
        {BUDDY_CONNECTION_CONNECTED, false, false, 0, BUDDY_CHARACTER_IDLE},
        {BUDDY_CONNECTION_OFFLINE, false, false, 0, BUDDY_CHARACTER_SLEEP},
    };
    size_t index;

    for (index = 0; index < sizeof(cases) / sizeof(cases[0]); ++index) {
        buddy_state_t state;
        buddy_ui_snapshot_t snapshot;

        buddy_state_init(&state, NULL);
        state.connection = cases[index].connection;
        state.connected = cases[index].connection == BUDDY_CONNECTION_CONNECTED;
        state.heartbeat_stale = false;
        state.running = cases[index].running;
        if (cases[index].has_prompt) {
            snprintf(state.prompt.id, sizeof(state.prompt.id), "%s", "req-1");
        }
        if (cases[index].temporary) {
            state.temporary_character = BUDDY_CHARACTER_HEART;
            state.temporary_until_ms = 1001;
        }

        buddy_event_t tick = {.type = BUDDY_EVENT_TICK};
        buddy_state_reduce(&state, &tick, 1000, NULL);
        buddy_state_snapshot(&state, &snapshot);
        assert(snapshot.character == cases[index].want);
    }
}

static void test_timeout_clears_prompt(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_event_t heartbeat = test_heartbeat_event(1200, 0);
    buddy_event_t tick = {.type = BUDDY_EVENT_TICK};

    buddy_state_init(&state, NULL);
    buddy_state_reduce(&state, &heartbeat, 1000, &action);
    buddy_event_t prompt = test_prompt_event("req-1", "Bash", "git push", 0, 1);
    buddy_state_reduce(&state, &prompt, 1001, &action);
    memset(&action, 0, sizeof(action));
    buddy_state_reduce(&state, &tick, 31001, &action);

    assert(action.type == BUDDY_ACTION_UI_REFRESH);
    assert(state.heartbeat_stale);
    assert(state.prompt.id[0] == '\0');
    assert(!state.connected);
    assert(state.connection == BUDDY_CONNECTION_OFFLINE);
    assert(state.total == 0);
    assert(state.running == 0);
    assert(state.waiting == 0);
    assert(state.tokens == 0);
    assert(state.tokens_today == 0);
    assert(state.message[0] == '\0');
    assert(state.entries[0][0] == '\0');
    assert(state.character == BUDDY_CHARACTER_SLEEP);
}

static void test_heartbeat_does_not_overwrite_persisted_identity(void)
{
    buddy_settings_snapshot_t settings = {0};
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_event_t heartbeat = test_heartbeat_event(1, 0);

    snprintf(settings.name, sizeof(settings.name), "%s", "Clawd");
    snprintf(settings.owner, sizeof(settings.owner), "%s", "Felix");
    buddy_state_init(&state, &settings);
    buddy_state_reduce(&state, &heartbeat, 1000, &action);

    assert(strcmp(state.name, "Clawd") == 0);
    assert(strcmp(state.owner, "Felix") == 0);
}

static void test_token_boundaries_celebrate_once(void)
{
    buddy_state_t state;
    buddy_settings_snapshot_t settings = {.highest_celebrated_level = 0};
    buddy_action_t action = {0};
    buddy_event_t heartbeat = test_heartbeat_event(49999, 0);

    buddy_state_init(&state, &settings);
    buddy_state_reduce(&state, &heartbeat, 1000, &action);
    assert(action.type == BUDDY_ACTION_UI_REFRESH);

    heartbeat = test_heartbeat_event(50000, 0);
    buddy_state_reduce(&state, &heartbeat, 1001, &action);
    assert(action.type == BUDDY_ACTION_SETTINGS);
    assert(action.settings.highest_celebrated_level == 1);
    assert(state.character == BUDDY_CHARACTER_CELEBRATE);

    memset(&action, 0, sizeof(action));
    buddy_state_reduce(&state, &heartbeat, 1002, &action);
    assert(action.type == BUDDY_ACTION_UI_REFRESH);
    assert(state.highest_celebrated_level == 1);

    heartbeat = test_heartbeat_event(100000, 0);
    buddy_state_reduce(&state, &heartbeat, 1003, &action);
    assert(action.type == BUDDY_ACTION_SETTINGS);
    assert(action.settings.highest_celebrated_level == 2);
}

static void test_persisted_celebration_level_is_not_replayed(void)
{
    buddy_state_t state;
    buddy_settings_snapshot_t settings = {.highest_celebrated_level = 2};
    buddy_action_t action = {0};
    buddy_event_t heartbeat = test_heartbeat_event(100000, 0);

    buddy_state_init(&state, &settings);
    buddy_state_reduce(&state, &heartbeat, 1000, &action);

    assert(action.type == BUDDY_ACTION_UI_REFRESH);
    assert(state.highest_celebrated_level == 2);
}

static void test_approval_locks_until_a_new_prompt(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_ui_snapshot_t snapshot;
    buddy_event_t prompt;
    buddy_event_t approve = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_OK};

    buddy_state_init(&state, NULL);

    prompt = test_prompt_event("req-1", "Bash", "git push", 1, 1);
    buddy_state_reduce(&state, &prompt, 1000, &action);
    assert(state.character == BUDDY_CHARACTER_ATTENTION);

    buddy_state_reduce(&state, &approve, 2000, &action);
    assert(action.type == BUDDY_ACTION_PERMISSION);
    assert(strcmp(action.permission.id, "req-1") == 0);
    assert(action.permission.decision == BUDDY_PERMISSION_ONCE);
    assert(state.character != BUDDY_CHARACTER_HEART);
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.approval_locked);
    assert(strcmp(snapshot.prompt_id, "req-1") == 0);

    memset(&action, 0, sizeof(action));
    buddy_state_reduce(&state, &approve, 2100, &action);
    assert(action.type == BUDDY_ACTION_NONE);

    prompt = test_prompt_event("req-2", "Bash", "git status", 1, 1);
    buddy_state_reduce(&state, &prompt, 2200, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(!snapshot.approval_locked);
    buddy_state_reduce(&state, &approve, 2201, &action);
    assert(action.type == BUDDY_ACTION_PERMISSION);
    assert(strcmp(action.permission.id, "req-2") == 0);
}

static void test_decision_is_remembered_until_the_prompt_goes_away(void)
{
    buddy_state_t state;
    buddy_ui_snapshot_t snapshot;
    buddy_action_t action = {0};
    buddy_event_t prompt = test_prompt_event("req-keep", "Bash", "ls", 0, 1);
    buddy_event_t deny = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_DOWN};
    buddy_event_t next = test_prompt_event("req-next", "Bash", "pwd", 0, 1);

    buddy_state_init(&state, NULL);
    buddy_state_reduce(&state, &prompt, 1000, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.permission_decision == BUDDY_PERMISSION_NONE);

    buddy_state_reduce(&state, &deny, 1001, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.approval_locked);
    assert(snapshot.permission_decision == BUDDY_PERMISSION_DENY);

    /* A new request starts unanswered: the old reply must not be shown for it. */
    buddy_state_reduce(&state, &next, 1002, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(!snapshot.approval_locked);
    assert(snapshot.permission_decision == BUDDY_PERMISSION_NONE);
}

static void test_denial_locks_and_emits_exactly_once(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_event_t prompt = test_prompt_event("req-deny", "Bash", "rm guarded", 1, 1);
    buddy_event_t deny = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_DOWN};

    buddy_state_init(&state, NULL);
    buddy_state_reduce(&state, &prompt, 1000, &action);
    buddy_state_reduce(&state, &deny, 1001, &action);

    assert(action.type == BUDDY_ACTION_PERMISSION);
    assert(strcmp(action.permission.id, "req-deny") == 0);
    assert(action.permission.decision == BUDDY_PERMISSION_DENY);
    assert(state.approval_locked);

    buddy_state_reduce(&state, &deny, 1002, &action);
    assert(action.type == BUDDY_ACTION_NONE);
}

static void test_permission_action_is_bound_to_the_prompt_connection(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_event_t prompt = test_prompt_event("req-generation", "Bash", "git push", 1, 1);
    buddy_event_t approve = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_OK};

    prompt.ble.connection_generation = 7;
    buddy_state_init(&state, NULL);
    buddy_state_reduce(&state, &prompt, 1000, &action);
    buddy_state_reduce(&state, &approve, 1001, &action);

    assert(action.type == BUDDY_ACTION_PERMISSION);
    assert(action.permission.connection_generation == 7);
}

static void test_permission_success_moves_from_attempted_to_successful_state(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_ui_snapshot_t snapshot;
    buddy_event_t prompt = test_prompt_event("req-success", "Bash", "git push", 1, 1);
    buddy_event_t approve = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_OK};
    buddy_event_t result = test_permission_result_event(
        "req-success", BUDDY_PERMISSION_ONCE, true);

    buddy_state_init(&state, NULL);
    buddy_state_reduce(&state, &prompt, 1000, &action);
    buddy_state_reduce(&state, &approve, 1001, &action);
    assert(action.type == BUDDY_ACTION_PERMISSION);
    assert(strcmp(state.last_attempted_prompt_id, "req-success") == 0);
    assert(state.last_successful_decision_id[0] == '\0');
    assert(state.permission_delivery == BUDDY_PERMISSION_DELIVERY_SENDING);
    assert(state.character != BUDDY_CHARACTER_HEART);

    buddy_state_reduce(&state, &result, 1002, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(action.type == BUDDY_ACTION_UI_REFRESH);
    assert(strcmp(state.last_successful_decision_id, "req-success") == 0);
    assert(snapshot.permission_delivery == BUDDY_PERMISSION_DELIVERY_SENT);
    assert(state.character == BUDDY_CHARACTER_HEART);
}

static void test_permission_failure_is_visible_and_same_id_replay_stays_locked(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_ui_snapshot_t snapshot;
    buddy_event_t prompt = test_prompt_event("req-failed", "Bash", "git push", 1, 1);
    buddy_event_t approve = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_OK};
    buddy_event_t failure = test_permission_result_event(
        "req-failed", BUDDY_PERMISSION_ONCE, false);

    buddy_state_init(&state, NULL);
    buddy_state_reduce(&state, &prompt, 1000, &action);
    buddy_state_reduce(&state, &approve, 1001, &action);
    buddy_state_reduce(&state, &failure, 1002, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.permission_delivery == BUDDY_PERMISSION_DELIVERY_FAILED);
    assert(strcmp(state.last_attempted_prompt_id, "req-failed") == 0);
    assert(state.last_successful_decision_id[0] == '\0');
    assert(state.approval_locked);
    assert(state.character != BUDDY_CHARACTER_HEART);

    buddy_state_reduce(&state, &prompt, 1003, &action);
    buddy_state_reduce(&state, &approve, 1004, &action);
    assert(action.type == BUDDY_ACTION_NONE);
    assert(state.permission_delivery == BUDDY_PERMISSION_DELIVERY_FAILED);
}

static void test_denial_success_and_ticks_never_show_heart(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_event_t prompt = test_prompt_event("req-denied", "Bash", "rm guarded", 1, 1);
    buddy_event_t deny = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_DOWN};
    buddy_event_t result = test_permission_result_event(
        "req-denied", BUDDY_PERMISSION_DENY, true);
    buddy_event_t tick = {.type = BUDDY_EVENT_TICK};

    buddy_state_init(&state, NULL);
    buddy_state_reduce(&state, &prompt, 1000, &action);
    buddy_state_reduce(&state, &deny, 1001, &action);
    assert(state.permission_delivery == BUDDY_PERMISSION_DELIVERY_SENDING);
    assert(state.character != BUDDY_CHARACTER_HEART);
    buddy_state_reduce(&state, &result, 1002, &action);
    buddy_state_reduce(&state, &tick, 4000, &action);
    assert(state.permission_delivery == BUDDY_PERMISSION_DELIVERY_SENT);
    assert(state.character == BUDDY_CHARACTER_BUSY);
}

static void test_successful_approval_heart_expires_at_five_seconds(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_event_t prompt = test_prompt_event("req-heart", "Read", "README", 1, 1);
    buddy_event_t approve = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_OK};
    buddy_event_t result = test_permission_result_event(
        "req-heart", BUDDY_PERMISSION_ONCE, true);
    buddy_event_t tick = {.type = BUDDY_EVENT_TICK};

    buddy_state_init(&state, NULL);
    buddy_state_reduce(&state, &prompt, 1000, &action);
    buddy_state_reduce(&state, &approve, 1001, &action);
    buddy_state_reduce(&state, &result, 1002, &action);
    buddy_state_reduce(&state, &tick, 6001, &action);
    assert(state.character == BUDDY_CHARACTER_HEART);
    buddy_state_reduce(&state, &tick, 6002, &action);
    assert(state.character == BUDDY_CHARACTER_BUSY);
}

static void test_heartbeat_prompt_snapshot_clears_or_preserves_approval_lock(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_ui_snapshot_t snapshot;
    buddy_event_t heartbeat = test_heartbeat_event(0, 0);
    buddy_event_t prompt = test_prompt_event("req-1", "Bash", "git push", 0, 1);
    buddy_event_t approve = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_OK};

    buddy_state_init(&state, NULL);
    buddy_state_reduce(&state, &prompt, 1000, &action);
    buddy_state_reduce(&state, &approve, 1001, &action);
    assert(state.approval_locked);

    heartbeat.heartbeat.connected = true;
    buddy_state_reduce(&state, &heartbeat, 1002, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(!snapshot.approval_locked);
    assert(snapshot.prompt_id[0] == '\0');

    heartbeat.heartbeat.prompt = prompt.prompt;
    buddy_state_reduce(&state, &heartbeat, 1003, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.prompt_id[0] == '\0');
    /* No request on screen: OK only goes on to the next screen. */
    buddy_state_reduce(&state, &approve, 1004, &action);
    assert(action.type == BUDDY_ACTION_UI_REFRESH && !state.approval_locked);
    assert(state.page == BUDDY_PAGE_TALK);
    state.page = BUDDY_PAGE_HOME;

    heartbeat.heartbeat.prompt = test_prompt_event("req-2", "Read", "README", 0, 1).prompt;
    buddy_state_reduce(&state, &heartbeat, 1005, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(!snapshot.approval_locked);
    assert(strcmp(snapshot.prompt_id, "req-2") == 0);
    buddy_state_reduce(&state, &approve, 1006, &action);
    assert(action.type == BUDDY_ACTION_PERMISSION);
    assert(strcmp(action.permission.id, "req-2") == 0);
}

static void test_ui_snapshot_runtime_indicators_default_off(void)
{
    buddy_state_t state;
    buddy_ui_snapshot_t snapshot;

    buddy_state_init(&state, NULL);
    buddy_state_snapshot(&state, &snapshot);

    assert(!snapshot.ble_connected);
    assert(!snapshot.ble_encrypted);
    assert(!snapshot.battery_available);
    assert(snapshot.battery_percent == 0);
    assert(snapshot.battery_mv == 0);

    state.ble_connected = true;
    state.ble_encrypted = true;
    state.battery_available = true;
    state.battery_percent = 73;
    state.battery_mv = 3875;
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.ble_connected);
    assert(snapshot.ble_encrypted);
    assert(snapshot.battery_available);
    assert(snapshot.battery_percent == 73);
    assert(snapshot.battery_mv == 3875);
}

static void test_absent_prompt_is_ignored(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_event_t approve = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_OK};

    buddy_state_init(&state, NULL);
    buddy_state_reduce(&state, &approve, 1001, &action);
    assert(action.type != BUDDY_ACTION_PERMISSION);
}

static void test_timeout_stale_prompt_is_ignored(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_event_t heartbeat = test_heartbeat_event(0, 0);
    buddy_event_t prompt = test_prompt_event("req-1", "Bash", "git push", 0, 1);
    buddy_event_t tick = {.type = BUDDY_EVENT_TICK};
    buddy_event_t approve = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_OK};

    buddy_state_init(&state, NULL);
    buddy_state_reduce(&state, &heartbeat, 1000, &action);
    buddy_state_reduce(&state, &prompt, 1001, &action);
    buddy_state_reduce(&state, &tick, 31001, &action);
    buddy_state_reduce(&state, &approve, 31002, &action);
    assert(action.type != BUDDY_ACTION_PERMISSION);
}

static void test_standalone_prompt_refreshes_liveness_clock(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_event_t prompt = test_prompt_event("req-late", "Bash", "git status", 0, 1);
    buddy_event_t approve = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_OK};

    buddy_state_init(&state, NULL);
    buddy_state_reduce(&state, &prompt, 60000, &action);
    buddy_state_reduce(&state, &approve, 60001, &action);

    assert(action.type == BUDDY_ACTION_PERMISSION);
    assert(strcmp(action.permission.id, "req-late") == 0);
}

static void test_disconnect_then_reconnect_does_not_restore_prompt(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_event_t heartbeat = test_heartbeat_event(0, 0);
    buddy_event_t prompt = test_prompt_event("req-1", "Bash", "git push", 0, 1);
    buddy_event_t approve = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_OK};

    buddy_state_init(&state, NULL);
    buddy_state_reduce(&state, &heartbeat, 1000, &action);
    buddy_state_reduce(&state, &prompt, 1001, &action);

    heartbeat.heartbeat.connected = false;
    buddy_state_reduce(&state, &heartbeat, 1002, &action);
    heartbeat.heartbeat.connected = true;
    buddy_state_reduce(&state, &heartbeat, 1003, &action);
    buddy_state_reduce(&state, &approve, 1004, &action);

    assert(state.prompt.id[0] == '\0');
    assert(action.type != BUDDY_ACTION_PERMISSION);
}

static void test_mismatched_observed_prompt_is_ignored(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_event_t prompt = test_prompt_event("req-1", "Bash", "git push", 0, 1);
    buddy_event_t approve = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_OK};

    buddy_state_init(&state, NULL);
    buddy_state_reduce(&state, &prompt, 1000, &action);
    test_set_observed_prompt_id(&approve, "req-2");
    buddy_state_reduce(&state, &approve, 1001, &action);

    assert(action.type == BUDDY_ACTION_NONE);
    assert(strcmp(state.prompt.id, "req-1") == 0);
}

static void test_nonterminated_prompt_id_is_ignored(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_event_t prompt = test_prompt_event("req-1", "Bash", "git push", 0, 1);
    buddy_event_t approve = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_OK};

    memset(prompt.prompt.id, 'a', sizeof(prompt.prompt.id));
    prompt.prompt.id_length = sizeof(prompt.prompt.id);
    buddy_state_init(&state, NULL);
    buddy_state_reduce(&state, &prompt, 1000, &action);
    buddy_state_reduce(&state, &approve, 1001, &action);

    assert(action.type != BUDDY_ACTION_PERMISSION);
}

static void test_truncated_prompt_id_is_ignored(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_event_t prompt = test_prompt_event("req-1", "Bash", "git push", 0, 1);
    buddy_event_t approve = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_OK};

    prompt.prompt.id_truncated = true;
    buddy_state_init(&state, NULL);
    buddy_state_reduce(&state, &prompt, 1000, &action);
    buddy_state_reduce(&state, &approve, 1001, &action);

    assert(action.type != BUDDY_ACTION_PERMISSION);
}

static void test_double_ok_opens_the_menu(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_event_t double_ok = {.type = BUDDY_EVENT_KEY_DOUBLE, .key = BUDDY_KEY_OK};
    buddy_event_t long_up = {.type = BUDDY_EVENT_KEY_LONG, .key = BUDDY_KEY_UP};
    buddy_event_t long_ok = {.type = BUDDY_EVENT_KEY_LONG, .key = BUDDY_KEY_OK};
    buddy_event_t long_down = {.type = BUDDY_EVENT_KEY_LONG, .key = BUDDY_KEY_DOWN};
    buddy_event_t prompt = test_prompt_event("req-double", "Bash", "ls", 0, 1);
    static const buddy_page_t screens[] = {BUDDY_PAGE_HOME, BUDDY_PAGE_TALK, BUDDY_PAGE_TASKS};
    static const buddy_page_t under_the_menu[] = {
        BUDDY_PAGE_MENU, BUDDY_PAGE_NOTICES, BUDDY_PAGE_HELPERS, BUDDY_PAGE_MORE,
        BUDDY_PAGE_GUIDE,
    };
    size_t index;

    buddy_state_init(&state, NULL);
    /* UP and DOWN have no long press any more. */
    buddy_state_reduce(&state, &long_up, 998, &action);
    assert(state.page == BUDDY_PAGE_HOME && action.type == BUDDY_ACTION_NONE);
    buddy_state_reduce(&state, &long_down, 999, &action);
    assert(state.page == BUDDY_PAGE_HOME && action.type == BUDDY_ACTION_NONE);

    /* Two quick presses on OK open the menu from each of the three screens... */
    for (index = 0; index < sizeof(screens) / sizeof(screens[0]); ++index) {
        state.page = screens[index];
        state.menu_selection = BUDDY_MENU_BACK;
        buddy_state_reduce(&state, &double_ok, 1000, &action);
        assert(state.page == BUDDY_PAGE_MENU && state.menu_selection == BUDDY_MENU_NOTICES);
        assert(action.type == BUDDY_ACTION_UI_REFRESH);
    }
    /* ...and from the menu and the pages under it, two presses or a long press go
     * back to the first screen; OK never starts talking there. */
    for (index = 0; index < sizeof(under_the_menu) / sizeof(under_the_menu[0]); ++index) {
        state.page = under_the_menu[index];
        buddy_state_reduce(&state, &double_ok, 1001, &action);
        assert(state.page == BUDDY_PAGE_HOME && action.type == BUDDY_ACTION_UI_REFRESH);
        state.page = under_the_menu[index];
        buddy_state_reduce(&state, &long_ok, 1002, &action);
        assert(state.page == BUDDY_PAGE_HOME && state.voice_phase == BUDDY_VOICE_IDLE);
        assert(action.type == BUDDY_ACTION_UI_REFRESH);
    }

    /* With something to decide on screen, two presses count as one press: the
     * request is answered once, and the menu does not open over it. */
    buddy_state_reduce(&state, &prompt, 1003, &action);
    test_set_observed_prompt_id(&double_ok, "req-double");
    buddy_state_reduce(&state, &double_ok, 1004, &action);
    assert(action.type == BUDDY_ACTION_PERMISSION &&
           action.permission.decision == BUDDY_PERMISSION_ONCE);
    assert(state.page == BUDDY_PAGE_HOME && state.approval_locked);
    buddy_state_reduce(&state, &double_ok, 1005, &action);
    assert(action.type == BUDDY_ACTION_NONE && state.page == BUDDY_PAGE_HOME);

    /* On a dark screen they only wake it. */
    buddy_state_init(&state, NULL);
    state.screen_off = true;
    double_ok.has_observed_prompt_id = false;
    buddy_state_reduce(&state, &double_ok, 1006, &action);
    assert(!state.screen_off && state.page == BUDDY_PAGE_HOME);
    assert(action.type == BUDDY_ACTION_DISPLAY_BACKLIGHT);
}

static void voice_ready_state(buddy_state_t *state)
{
    buddy_action_t action = {0};
    buddy_event_t connected = {.type = BUDDY_EVENT_BLE_CONNECTED};
    buddy_event_t hello = {.type = BUDDY_EVENT_HOST_HELLO, .host_voice = true};

    connected.ble.connection_generation = 7;
    hello.ble.connection_generation = 7;
    buddy_state_init(state, NULL);
    buddy_state_reduce(state, &connected, 10, &action);
    state->ble_encrypted = true;
    state->connection = BUDDY_CONNECTION_OFFLINE;
    buddy_state_reduce(state, &hello, 11, &action);
    assert(state->host_voice);
}

static buddy_event_t voice_event(buddy_voice_status_t status, uint32_t generation)
{
    buddy_event_t event = {.type = BUDDY_EVENT_VOICE, .voice_status = status};

    event.ble.connection_generation = generation;
    return event;
}

static void test_hold_ok_talks_and_release_sends(void)
{
    buddy_state_t state;
    buddy_ui_snapshot_t snapshot;
    buddy_action_t action = {0};
    buddy_event_t long_ok = {.type = BUDDY_EVENT_KEY_LONG, .key = BUDDY_KEY_OK};
    buddy_event_t release = {.type = BUDDY_EVENT_KEY_RELEASE, .key = BUDDY_KEY_OK};
    buddy_event_t click = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_DOWN};
    buddy_event_t event;

    voice_ready_state(&state);
    assert(state.host_hub);
    buddy_state_reduce(&state, &long_ok, 1000, &action);
    assert(action.type == BUDDY_ACTION_VOICE_START && action.connection_generation == 7);
    assert(state.voice_phase == BUDDY_VOICE_PREPARING && state.page == BUDDY_PAGE_HOME);

    /* A second long press or a click while talking changes nothing. */
    buddy_state_reduce(&state, &long_ok, 1001, &action);
    assert(action.type == BUDDY_ACTION_NONE);
    buddy_state_reduce(&state, &click, 1002, &action);
    assert(action.type == BUDDY_ACTION_NONE && state.page == BUDDY_PAGE_HOME);

    /* A report from an older connection is ignored. */
    event = voice_event(BUDDY_VOICE_STARTED, 6);
    buddy_state_reduce(&state, &event, 1100, &action);
    assert(state.voice_phase == BUDDY_VOICE_PREPARING);
    event = voice_event(BUDDY_VOICE_STARTED, 7);
    buddy_state_reduce(&state, &event, 1200, &action);
    assert(state.voice_phase == BUDDY_VOICE_LISTENING);
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.voice_phase == BUDDY_VOICE_LISTENING &&
           snapshot.voice_listening_since_ms == 1200);

    buddy_state_reduce(&state, &release, 4000, &action);
    assert(action.type == BUDDY_ACTION_VOICE_STOP && !action.voice_cancel);
    assert(state.voice_phase == BUDDY_VOICE_SENDING);
    buddy_state_reduce(&state, &release, 4001, &action);
    assert(action.type == BUDDY_ACTION_NONE);

    event = voice_event(BUDDY_VOICE_FINISHED, 7);
    buddy_state_reduce(&state, &event, 4300, &action);
    assert(state.voice_phase == BUDDY_VOICE_IDLE);
    assert(action.type == BUDDY_ACTION_UI_REFRESH);
    /* The recording is on its way: the home page says so until the host answers,
     * and the timer counts from here. */
    assert(state.chat.phase == BUDDY_CHAT_SENT && state.chat.mood == BUDDY_MOOD_BUSY);
    assert(state.chat_since_ms == 4300 && state.reply[0] == '\0');
    assert(state.message[0] == '\0');

    /* Releasing before the microphone is live: the late "started" must not reopen it. */
    buddy_state_reduce(&state, &long_ok, 5000, &action);
    assert(action.type == BUDDY_ACTION_VOICE_START);
    buddy_state_reduce(&state, &release, 5100, &action);
    assert(action.type == BUDDY_ACTION_VOICE_STOP);
    event = voice_event(BUDDY_VOICE_STARTED, 7);
    buddy_state_reduce(&state, &event, 5200, &action);
    assert(state.voice_phase == BUDDY_VOICE_SENDING);
    event = voice_event(BUDDY_VOICE_TOO_SHORT, 7);
    buddy_state_reduce(&state, &event, 5300, &action);
    assert(state.voice_phase == BUDDY_VOICE_IDLE);
    assert(strcmp(state.message, PT_VOICE_TOO_SHORT) == 0);

    /* Other endings each leave their own message. */
    buddy_state_reduce(&state, &long_ok, 6000, &action);
    event = voice_event(BUDDY_VOICE_FAILED_MIC, 7);
    buddy_state_reduce(&state, &event, 6100, &action);
    assert(state.voice_phase == BUDDY_VOICE_IDLE);
    assert(strcmp(state.message, PT_VOICE_FAILED_MIC) == 0);
    buddy_state_reduce(&state, &long_ok, 7000, &action);
    event = voice_event(BUDDY_VOICE_LIMIT, 7);
    buddy_state_reduce(&state, &event, 7100, &action);
    assert(strcmp(state.message, PT_VOICE_LIMIT) == 0);
    /* A release with nothing in progress is ignored. */
    buddy_state_reduce(&state, &release, 7200, &action);
    assert(action.type == BUDDY_ACTION_NONE);
}

static void test_talking_needs_a_voice_capable_host(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_event_t long_ok = {.type = BUDDY_EVENT_KEY_LONG, .key = BUDDY_KEY_OK};
    buddy_event_t disconnected = {.type = BUDDY_EVENT_BLE_DISCONNECTED};
    buddy_event_t connected = {.type = BUDDY_EVENT_BLE_CONNECTED};
    buddy_event_t hello = {.type = BUDDY_EVENT_HOST_HELLO, .host_voice = true};
    buddy_event_t prompt = test_prompt_event("req-voice", "Bash", "ls", 0, 1);

    /* Nobody connected. */
    buddy_state_init(&state, NULL);
    buddy_state_reduce(&state, &long_ok, 1, &action);
    assert(action.type == BUDDY_ACTION_UI_REFRESH && state.voice_phase == BUDDY_VOICE_IDLE);
    assert(strcmp(state.message, PT_VOICE_NEED_LINK) == 0);

    /* Connected to a host that never said it takes voice (the Claude desktop app). */
    connected.ble.connection_generation = 3;
    buddy_state_reduce(&state, &connected, 2, &action);
    state.ble_encrypted = true;
    buddy_state_reduce(&state, &long_ok, 3, &action);
    assert(action.type == BUDDY_ACTION_UI_REFRESH && state.voice_phase == BUDDY_VOICE_IDLE);
    assert(strcmp(state.message, PT_VOICE_NO_HOST) == 0);

    /* A hello for another connection does not count; the right one does. */
    hello.ble.connection_generation = 2;
    buddy_state_reduce(&state, &hello, 4, &action);
    assert(!state.host_voice);
    hello.ble.connection_generation = 3;
    buddy_state_reduce(&state, &hello, 5, &action);
    assert(state.host_voice);

    /* An approval on screen keeps OK for approving. */
    prompt.ble.connection_generation = 3;
    buddy_state_reduce(&state, &prompt, 6, &action);
    buddy_state_reduce(&state, &long_ok, 7, &action);
    assert(action.type == BUDDY_ACTION_NONE && state.voice_phase == BUDDY_VOICE_IDLE);
    memset(&state.prompt, 0, sizeof(state.prompt));

    /* Losing the link while talking stops the worker and forgets the capability. */
    buddy_state_reduce(&state, &long_ok, 8, &action);
    assert(action.type == BUDDY_ACTION_VOICE_START);
    disconnected.ble.connection_generation = 3;
    buddy_state_reduce(&state, &disconnected, 9, &action);
    assert(action.type == BUDDY_ACTION_VOICE_STOP && action.voice_cancel);
    assert(state.voice_phase == BUDDY_VOICE_IDLE && !state.host_voice);
    assert(strcmp(state.message, PT_VOICE_FAILED_LINK) == 0);
    connected.ble.connection_generation = 4;
    buddy_state_reduce(&state, &connected, 10, &action);
    assert(!state.host_voice);
}

static void test_ok_goes_round_the_three_screens(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_event_t up = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_UP};
    buddy_event_t down = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_DOWN};
    buddy_event_t ok = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_OK};

    buddy_state_init(&state, NULL);
    /* On the first screen UP and DOWN do nothing. */
    buddy_state_reduce(&state, &down, 5, &action);
    assert(state.page == BUDDY_PAGE_HOME && action.type == BUDDY_ACTION_NONE);
    buddy_state_reduce(&state, &up, 6, &action);
    assert(state.page == BUDDY_PAGE_HOME && action.type == BUDDY_ACTION_NONE);

    /* A short press on OK: the conversation, the tasks, and round again. */
    buddy_state_reduce(&state, &ok, 7, &action);
    assert(state.page == BUDDY_PAGE_TALK && action.type == BUDDY_ACTION_UI_REFRESH);
    /* On the conversation UP and DOWN move within the thing on screen. */
    buddy_state_reduce(&state, &down, 8, &action);
    assert(state.page == BUDDY_PAGE_TALK);
    assert(action.type == BUDDY_ACTION_UI_SCROLL && action.scroll_delta > 0);
    buddy_state_reduce(&state, &up, 9, &action);
    assert(action.type == BUDDY_ACTION_UI_SCROLL && action.scroll_delta < 0);
    buddy_state_reduce(&state, &ok, 10, &action);
    assert(state.page == BUDDY_PAGE_TASKS && action.type == BUDDY_ACTION_UI_REFRESH);
    /* Nothing in progress: nothing to pick. */
    buddy_state_reduce(&state, &down, 11, &action);
    assert(state.page == BUDDY_PAGE_TASKS && action.type == BUDDY_ACTION_NONE);
    buddy_state_reduce(&state, &ok, 12, &action);
    assert(state.page == BUDDY_PAGE_HOME && action.type == BUDDY_ACTION_UI_REFRESH);
}

static void test_guide_scrolls_and_returns_to_more(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_event_t up = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_UP};
    buddy_event_t down = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_DOWN};
    buddy_event_t ok = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_OK};

    buddy_state_init(&state, NULL);
    state.page = BUDDY_PAGE_MORE;
    state.more_selection = BUDDY_MORE_GUIDE;
    buddy_state_reduce(&state, &ok, 1, &action);
    assert(state.page == BUDDY_PAGE_GUIDE);
    buddy_state_reduce(&state, &down, 2, &action);
    assert(state.page == BUDDY_PAGE_GUIDE);
    assert(action.type == BUDDY_ACTION_UI_SCROLL && action.scroll_delta > 0);
    buddy_state_reduce(&state, &up, 3, &action);
    assert(action.type == BUDDY_ACTION_UI_SCROLL && action.scroll_delta < 0);
    buddy_state_reduce(&state, &ok, 4, &action);
    assert(state.page == BUDDY_PAGE_MORE);
    assert(state.more_selection == BUDDY_MORE_GUIDE);
}

static void test_screen_off_wakes_on_key_and_on_attention(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_event_t ok = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_OK};
    buddy_event_t long_ok = {.type = BUDDY_EVENT_KEY_LONG, .key = BUDDY_KEY_OK};
    buddy_event_t prompt = test_prompt_event("req-wake", "Bash", "ls", 0, 1);
    buddy_event_t passkey = {.type = BUDDY_EVENT_BLE_PASSKEY};

    buddy_state_init(&state, NULL);
    assert(buddy_state_backlight_percent(&state) == 100);
    state.page = BUDDY_PAGE_MENU;
    state.menu_selection = BUDDY_MENU_SCREEN_OFF;
    buddy_state_reduce(&state, &ok, 1, &action);
    assert(state.screen_off);
    assert(action.type == BUDDY_ACTION_SCREEN_OFF);
    assert(state.page == BUDDY_PAGE_HOME);
    assert(buddy_state_backlight_percent(&state) == 0);

    /* A long press on a dark screen wakes it without navigating, whichever key
     * it is. Nobody is connected here, so holding OK cannot start talking either. */
    buddy_state_reduce(&state, &long_ok, 2, &action);
    assert(!state.screen_off && state.page == BUDDY_PAGE_HOME);
    assert(state.voice_phase == BUDDY_VOICE_IDLE);
    state.screen_off = true;
    {
        buddy_event_t long_up = {.type = BUDDY_EVENT_KEY_LONG, .key = BUDDY_KEY_UP};

        buddy_state_reduce(&state, &long_up, 2, &action);
        assert(!state.screen_off && state.page == BUDDY_PAGE_HOME);
    }
    state.screen_off = true;

    /* The waking click is consumed: it never approves or navigates. */
    buddy_state_reduce(&state, &ok, 3, &action);
    assert(!state.screen_off);
    assert(action.type == BUDDY_ACTION_DISPLAY_BACKLIGHT);
    assert(action.brightness_percent == 100);
    assert(state.page == BUDDY_PAGE_HOME);

    state.screen_off = true;
    buddy_state_reduce(&state, &prompt, 4, &action);
    assert(!state.screen_off);

    buddy_state_init(&state, NULL);
    state.screen_off = true;
    buddy_state_reduce(&state, &passkey, 5, &action);
    assert(!state.screen_off);
}

static void test_assistant_turn_is_kept_only_while_connected(void)
{
    buddy_state_t state;
    buddy_ui_snapshot_t snapshot;
    buddy_action_t action = {0};
    buddy_event_t heartbeat = test_heartbeat_event(0, 1);
    buddy_event_t turn = {.type = BUDDY_EVENT_TURN};
    buddy_event_t disconnected = {.type = BUDDY_EVENT_BLE_DISCONNECTED};

    snprintf(turn.reply, sizeof(turn.reply), "%s", "done");
    turn.reply_truncated = true;
    buddy_state_init(&state, NULL);
    buddy_state_reduce(&state, &turn, 1, &action);
    assert(state.reply[0] == '\0');

    buddy_state_reduce(&state, &heartbeat, 2, &action);
    buddy_state_reduce(&state, &turn, 3, &action);
    assert(action.type == BUDDY_ACTION_UI_REFRESH);
    buddy_state_snapshot(&state, &snapshot);
    assert(strcmp(snapshot.reply, "done") == 0);
    assert(snapshot.reply_truncated);

    buddy_state_reduce(&state, &disconnected, 4, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.reply[0] == '\0');
    assert(!snapshot.reply_truncated);
}

static void test_protocol_command_events_refresh_the_display(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_event_t event = {.type = BUDDY_EVENT_NAME};

    buddy_state_init(&state, NULL);
    snprintf(event.command.value, sizeof(event.command.value), "%s", "Buddy");
    buddy_state_reduce(&state, &event, 1000, &action);
    assert(strcmp(state.name, "Buddy") == 0);
    assert(action.type == BUDDY_ACTION_UI_REFRESH);

    event.type = BUDDY_EVENT_OWNER;
    snprintf(event.command.value, sizeof(event.command.value), "%s", "Claude");
    buddy_state_reduce(&state, &event, 1001, &action);
    assert(strcmp(state.owner, "Claude") == 0);

    event.type = BUDDY_EVENT_STATUS;
    snprintf(event.command.value, sizeof(event.command.value), "%s", "Ready");
    buddy_state_reduce(&state, &event, 1002, &action);
    assert(strcmp(state.message, "Ready") == 0);

    event.type = BUDDY_EVENT_TIME;
    event.time.epoch_seconds = 1775731234;
    event.time.timezone_offset_seconds = -25200;
    buddy_state_reduce(&state, &event, 1003, &action);
    assert(state.epoch_seconds == 1775731234);
    assert(state.timezone_offset_seconds == -25200);
    assert(state.time_received_ms == 1003);

    buddy_ui_snapshot_t time_snapshot;
    buddy_state_snapshot(&state, &time_snapshot);
    assert(time_snapshot.time_received_ms == 1003);

    event.type = BUDDY_EVENT_UNPAIR_CONFIRMATION;
    buddy_state_reduce(&state, &event, 1004, &action);
    assert(state.connection == BUDDY_CONNECTION_CONFIRMING);
    assert(state.character == BUDDY_CHARACTER_CONFIRMATION);
}

static void test_status_request_does_not_overwrite_message(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_event_t event = {.type = BUDDY_EVENT_STATUS_REQUEST};

    buddy_state_init(&state, NULL);
    event.ble.connection_generation = 11;
    snprintf(state.message, sizeof(state.message), "%s", "Keep this");
    buddy_state_reduce(&state, &event, 1000, &action);

    assert(action.type == BUDDY_ACTION_STATUS);
    assert(action.connection_generation == 11);
    assert(strcmp(state.message, "Keep this") == 0);
}

static void test_unpair_confirmation_survives_a_heartbeat(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_event_t unpair = {.type = BUDDY_EVENT_UNPAIR_CONFIRMATION};
    buddy_event_t heartbeat = test_heartbeat_event(0, 0);

    buddy_state_init(&state, NULL);
    buddy_state_reduce(&state, &unpair, 1000, &action);
    buddy_state_reduce(&state, &heartbeat, 1001, &action);

    assert(state.confirmation_pending);
    assert(state.connection == BUDDY_CONNECTION_CONNECTED);
    assert(state.character == BUDDY_CHARACTER_CONFIRMATION);
}

static void test_unpair_confirmation_ok_emits_explicit_action(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_event_t unpair = {.type = BUDDY_EVENT_UNPAIR_CONFIRMATION};
    buddy_event_t heartbeat = test_heartbeat_event(0, 0);
    buddy_event_t ok = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_OK};

    buddy_state_init(&state, NULL);
    buddy_state_reduce(&state, &unpair, 1000, &action);
    buddy_state_reduce(&state, &heartbeat, 1001, &action);
    buddy_state_reduce(&state, &ok, 1002, &action);

    assert(!state.confirmation_pending);
    assert(action.type == BUDDY_ACTION_UNPAIR_CONFIRMED);
}

static void test_unpair_confirmation_down_cancels_without_action(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_event_t unpair = {.type = BUDDY_EVENT_UNPAIR_CONFIRMATION};
    buddy_event_t down = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_DOWN};

    buddy_state_init(&state, NULL);
    buddy_state_reduce(&state, &unpair, 1000, &action);
    buddy_state_reduce(&state, &down, 1001, &action);

    assert(!state.confirmation_pending);
    assert(action.type == BUDDY_ACTION_UI_REFRESH);
}

static void test_parsed_heartbeat_approval_serializes_permission(void)
{
    const char *json =
        "{\"total\":1,\"running\":1,\"waiting\":1,\"msg\":\"approve\","
        "\"entries\":[],\"tokens\":0,\"tokens_today\":0,\"prompt\":{"
        "\"id\":\"req-e2e\",\"tool\":\"Bash\",\"hint\":\"git status\"}}";
    buddy_state_t state;
    buddy_event_t heartbeat = {0};
    buddy_event_t approve = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_OK};
    buddy_action_t action = {0};
    char output[160];

    assert(buddy_protocol_parse(json, strlen(json), &heartbeat) == BUDDY_EVENT_HEARTBEAT);
    buddy_state_init(&state, NULL);
    buddy_state_reduce(&state, &heartbeat, 1000, &action);
    buddy_state_reduce(&state, &approve, 1001, &action);

    assert(action.type == BUDDY_ACTION_PERMISSION);
    assert(buddy_protocol_permission_json(output, sizeof(output), action.permission.id,
                                          action.permission.decision) > 0);
    assert(strcmp(output,
                  "{\"cmd\":\"permission\",\"id\":\"req-e2e\",\"decision\":\"once\"}\n") ==
           0);
}

static void test_normal_navigation_and_approval_scroll_are_distinct(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_event_t down = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_DOWN};
    buddy_event_t up = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_UP};
    buddy_event_t prompt = test_prompt_event("req-scroll", "Read", "long hint", 0, 1);

    buddy_state_init(&state, NULL);
    state.page = BUDDY_PAGE_TALK;
    snprintf(state.reply, sizeof(state.reply), "%s", "something to read");
    buddy_state_reduce(&state, &down, 1000, &action);
    assert(action.type == BUDDY_ACTION_UI_SCROLL && action.scroll_delta == 60);

    /* With a request on screen the same keys belong to the request. */
    buddy_state_reduce(&state, &prompt, 1002, &action);
    buddy_state_reduce(&state, &up, 1003, &action);
    assert(state.page == BUDDY_PAGE_TALK);
    assert(action.type == BUDDY_ACTION_UI_SCROLL);
    assert(action.scroll_delta == -48);
}

static void test_settings_actions_have_separate_confirmations(void)
{
    buddy_settings_snapshot_t settings = {.ble_enabled = true};
    buddy_state_t state;
    buddy_ui_snapshot_t snapshot;
    buddy_action_t action = {0};
    buddy_event_t open_menu = {.type = BUDDY_EVENT_KEY_DOUBLE, .key = BUDDY_KEY_OK};
    buddy_event_t click_ok = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_OK};
    buddy_event_t click_down = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_DOWN};

    buddy_state_init(&state, &settings);
    buddy_state_reduce(&state, &open_menu, 1000, &action);
    assert(state.page == BUDDY_PAGE_MENU);
    state.menu_selection = BUDDY_MENU_BLE;
    buddy_state_reduce(&state, &click_ok, 1002, &action);
    assert(action.type == BUDDY_ACTION_BLE_TOGGLE);
    assert(!action.ble_enabled);
    buddy_state_snapshot(&state, &snapshot);
    assert(!snapshot.ble_enabled);

    /* The destructive ones sit one level down, and each asks again. */
    state.menu_selection = BUDDY_MENU_MORE;
    state.more_selection = BUDDY_MORE_BACK;
    buddy_state_reduce(&state, &click_ok, 1003, &action);
    assert(state.page == BUDDY_PAGE_MORE && state.more_selection == BUDDY_MORE_GUIDE);
    state.more_selection = BUDDY_MORE_UNPAIR;
    buddy_state_reduce(&state, &click_ok, 1004, &action);
    assert(state.confirmation == BUDDY_CONFIRM_UNPAIR);
    assert(!state.confirmation_acknowledge);
    buddy_state_reduce(&state, &click_down, 1005, &action);
    assert(state.confirmation == BUDDY_CONFIRM_NONE);
    assert(action.type == BUDDY_ACTION_UI_REFRESH);
    assert(state.page == BUDDY_PAGE_MORE);

    state.more_selection = BUDDY_MORE_FACTORY_RESET;
    buddy_state_reduce(&state, &click_ok, 1010, &action);
    assert(state.confirmation == BUDDY_CONFIRM_FACTORY_RESET);
    buddy_state_reduce(&state, &click_ok, 1011, &action);
    assert(action.type == BUDDY_ACTION_FACTORY_RESET_CONFIRMED);
}

static void test_menu_surface_is_complete_and_bounded(void)
{
    buddy_state_t state;
    buddy_ui_snapshot_t snapshot;
    buddy_action_t action = {0};
    buddy_event_t ok = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_OK};
    buddy_event_t up = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_UP};
    buddy_event_t down = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_DOWN};
    unsigned press;

    assert(BUDDY_MENU_COUNT == 7 && BUDDY_MORE_COUNT == 4);
    buddy_state_init(&state, NULL);
    state.page = BUDDY_PAGE_MENU;
    state.menu_selection = BUDDY_MENU_BRIGHTNESS;
    for (press = 0; press < 2U * BUDDY_BRIGHTNESS_LEVELS; ++press) {
        buddy_state_reduce(&state, &ok, press + 1U, &action);
        assert(action.type == BUDDY_ACTION_DISPLAY_BACKLIGHT);
        assert(action.brightness_percent >= 20 && action.brightness_percent <= 100);
        assert(action.brightness_percent == buddy_state_backlight_percent(&state));
    }
    /* The selection wraps in both directions. */
    state.menu_selection = BUDDY_MENU_NOTICES;
    buddy_state_reduce(&state, &up, 100, &action);
    assert(state.menu_selection == BUDDY_MENU_BACK);
    buddy_state_reduce(&state, &down, 101, &action);
    assert(state.menu_selection == BUDDY_MENU_NOTICES);

    /* Notices and helpers are pages of their own; OK comes back to the same row. */
    buddy_state_reduce(&state, &ok, 102, &action);
    assert(state.page == BUDDY_PAGE_NOTICES && action.type == BUDDY_ACTION_UI_REFRESH);
    buddy_state_reduce(&state, &down, 103, &action);
    assert(state.page == BUDDY_PAGE_NOTICES && action.type == BUDDY_ACTION_NONE);
    buddy_state_reduce(&state, &ok, 104, &action);
    assert(state.page == BUDDY_PAGE_MENU && state.menu_selection == BUDDY_MENU_NOTICES);
    buddy_state_reduce(&state, &down, 105, &action);
    assert(state.menu_selection == BUDDY_MENU_HELPERS);
    buddy_state_reduce(&state, &ok, 106, &action);
    assert(state.page == BUDDY_PAGE_HELPERS);
    buddy_state_reduce(&state, &ok, 107, &action);
    assert(state.page == BUDDY_PAGE_MENU && state.menu_selection == BUDDY_MENU_HELPERS);

    /* More settings: wraps, and "back" returns to the menu, not to home. */
    state.menu_selection = BUDDY_MENU_MORE;
    buddy_state_reduce(&state, &ok, 108, &action);
    assert(state.page == BUDDY_PAGE_MORE && state.more_selection == BUDDY_MORE_GUIDE);
    buddy_state_reduce(&state, &up, 109, &action);
    assert(state.more_selection == BUDDY_MORE_BACK);
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.page == BUDDY_PAGE_MORE && snapshot.more_selection == BUDDY_MORE_BACK &&
           snapshot.menu_selection == BUDDY_MENU_MORE);
    buddy_state_reduce(&state, &ok, 110, &action);
    assert(state.page == BUDDY_PAGE_MENU && state.menu_selection == BUDDY_MENU_MORE);

    state.menu_selection = BUDDY_MENU_BACK;
    buddy_state_reduce(&state, &ok, 111, &action);
    assert(state.page == BUDDY_PAGE_HOME);
}

static void test_remote_unpair_confirmation_remembers_ack(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_event_t unpair = {.type = BUDDY_EVENT_UNPAIR_CONFIRMATION};
    buddy_event_t ok = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_OK};

    buddy_state_init(&state, NULL);
    unpair.ble.connection_generation = 13;
    buddy_state_reduce(&state, &unpair, 1000, &action);
    assert(state.confirmation == BUDDY_CONFIRM_UNPAIR);
    assert(state.confirmation_acknowledge);
    buddy_state_reduce(&state, &ok, 1001, &action);
    assert(action.type == BUDDY_ACTION_UNPAIR_CONFIRMED);
    assert(action.confirmation_acknowledge);
    assert(action.connection_generation == 13);
}

static void test_remote_unpair_cannot_replace_a_local_confirmation(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_event_t remote_unpair = {.type = BUDDY_EVENT_UNPAIR_CONFIRMATION};

    buddy_state_init(&state, NULL);
    state.confirmation = BUDDY_CONFIRM_FACTORY_RESET;
    state.confirmation_pending = true;
    remote_unpair.ble.connection_generation = 17;
    buddy_state_reduce(&state, &remote_unpair, 1000, &action);

    assert(state.confirmation == BUDDY_CONFIRM_FACTORY_RESET);
    assert(!state.confirmation_acknowledge);
    assert(action.type == BUDDY_ACTION_NONE);
}

static void test_ble_security_events_update_owned_state_and_clear_sensitive_prompt(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_event_t connected = {.type = BUDDY_EVENT_BLE_CONNECTED};
    buddy_event_t passkey = {.type = BUDDY_EVENT_BLE_PASSKEY};
    buddy_event_t encrypted = {.type = BUDDY_EVENT_BLE_ENCRYPTION};
    buddy_event_t disconnected = {.type = BUDDY_EVENT_BLE_DISCONNECTED};
    buddy_event_t prompt = test_prompt_event("req-secret", "Bash", "secret", 0, 1);

    passkey.ble.passkey = 123456;
    encrypted.ble.secure = true;
    buddy_state_init(&state, NULL);
    buddy_state_reduce(&state, &connected, 1000, &action);
    assert(state.ble_connected);
    assert(!state.ble_encrypted);
    buddy_state_reduce(&state, &passkey, 1001, &action);
    assert(state.passkey_visible);
    assert(state.passkey == 123456);
    buddy_state_reduce(&state, &encrypted, 1002, &action);
    assert(state.ble_encrypted);
    assert(!state.passkey_visible);

    buddy_state_reduce(&state, &prompt, 1003, &action);
    buddy_state_reduce(&state, &disconnected, 1004, &action);
    assert(!state.ble_connected);
    assert(!state.ble_encrypted);
    assert(state.heartbeat_stale);
    assert(state.prompt.id[0] == '\0');
}

static void test_stale_security_mailbox_event_cannot_override_latest_link(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_event_t connected = {.type = BUDDY_EVENT_BLE_CONNECTED};
    buddy_event_t passkey = {.type = BUDDY_EVENT_BLE_PASSKEY};
    buddy_event_t encrypted = {.type = BUDDY_EVENT_BLE_ENCRYPTION};

    connected.ble.connection_generation = 8;
    passkey.ble.connection_generation = 7;
    passkey.ble.passkey = 123456;
    encrypted.ble.connection_generation = 7;
    encrypted.ble.secure = true;
    buddy_state_init(&state, NULL);
    buddy_state_reduce(&state, &connected, 1000, &action);
    assert(state.ble_connection_generation == 8);

    buddy_state_reduce(&state, &passkey, 1001, &action);
    buddy_state_reduce(&state, &encrypted, 1002, &action);
    assert(!state.passkey_visible);
    assert(!state.ble_encrypted);

    passkey.ble.connection_generation = 8;
    buddy_state_reduce(&state, &passkey, 1003, &action);
    assert(state.passkey_visible);
}

static void test_new_link_generation_invalidates_sensitive_state_when_disconnect_was_coalesced(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_event_t connected = {.type = BUDDY_EVENT_BLE_CONNECTED};
    buddy_event_t prompt = test_prompt_event("old-link", "Bash", "deploy", 1, 1);
    buddy_event_t unpair = {.type = BUDDY_EVENT_UNPAIR_CONFIRMATION};

    buddy_state_init(&state, NULL);
    connected.ble.connection_generation = 7;
    buddy_state_reduce(&state, &connected, 1000, &action);
    prompt.ble.connection_generation = 7;
    buddy_state_reduce(&state, &prompt, 1001, &action);

    connected.ble.connection_generation = 8;
    buddy_state_reduce(&state, &connected, 1002, &action);
    assert(state.prompt.id[0] == '\0');
    assert(!state.connected);
    assert(state.heartbeat_stale);

    unpair.ble.connection_generation = 8;
    buddy_state_reduce(&state, &unpair, 1003, &action);
    assert(state.confirmation == BUDDY_CONFIRM_UNPAIR);
    connected.ble.connection_generation = 9;
    buddy_state_reduce(&state, &connected, 1004, &action);
    assert(state.confirmation == BUDDY_CONFIRM_NONE);
    assert(!state.confirmation_pending);
}

static buddy_event_t chat_event(buddy_chat_phase_t phase, const char *said, const char *reply,
                                const char *agent, uint32_t generation)
{
    buddy_event_t event = {.type = BUDDY_EVENT_CHAT};

    event.chat.phase = phase;
    event.chat.mood = phase == BUDDY_CHAT_DONE ? BUDDY_MOOD_HAPPY : BUDDY_MOOD_BUSY;
    snprintf(event.chat.said, sizeof(event.chat.said), "%s", said);
    snprintf(event.chat.agent, sizeof(event.chat.agent), "%s", agent);
    snprintf(event.reply, sizeof(event.reply), "%s", reply);
    event.ble.connection_generation = generation;
    return event;
}

static buddy_event_t card_event(const char *id, buddy_card_state_t card_state,
                                const char *agent, const char *said, const char *reply,
                                uint32_t generation)
{
    buddy_event_t event = {.type = BUDDY_EVENT_CARD};

    snprintf(event.card.id, sizeof(event.card.id), "%s", id);
    snprintf(event.card.at, sizeof(event.card.at), "%s", "14:02");
    snprintf(event.card.agent, sizeof(event.card.agent), "%s", agent);
    event.card.state = card_state;
    snprintf(event.card_said, sizeof(event.card_said), "%s", said);
    snprintf(event.reply, sizeof(event.reply), "%s", reply);
    event.ble.connection_generation = generation;
    return event;
}

static void test_the_first_screen_follows_the_hub(void)
{
    buddy_state_t state;
    buddy_ui_snapshot_t snapshot;
    buddy_action_t action = {0};
    buddy_event_t event;

    voice_ready_state(&state);
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.host_hub && snapshot.chat.phase == BUDDY_CHAT_NONE);
    assert(!snapshot.host_chat && snapshot.doing == 0U && snapshot.card_index == -1);

    /* A phone app from before "chat" says hub and still reports replies as turn
     * events; until the hub reports the conversation itself, those count. */
    {
        buddy_event_t heartbeat = test_heartbeat_event(0, 0);
        buddy_event_t turn = {.type = BUDDY_EVENT_TURN};

        heartbeat.heartbeat.waiting = 0;
        buddy_state_reduce(&state, &heartbeat, 900, &action);
        snprintf(turn.reply, sizeof(turn.reply), "%s", "from an older app");
        buddy_state_reduce(&state, &turn, 901, &action);
        assert(strcmp(state.reply, "from an older app") == 0);
        turn.reply[0] = '\0';
        buddy_state_reduce(&state, &turn, 902, &action);
        assert(state.reply[0] == '\0');
    }

    /* A report from another connection is not this conversation. */
    event = chat_event(BUDDY_CHAT_THINKING, "hello", "", "", 6);
    buddy_state_reduce(&state, &event, 1000, &action);
    assert(state.chat.phase == BUDDY_CHAT_NONE && action.type == BUDDY_ACTION_NONE);
    assert(!state.host_chat);

    event = chat_event(BUDDY_CHAT_THINKING, "hello", "", "", 7);
    buddy_state_reduce(&state, &event, 1000, &action);
    assert(action.type == BUDDY_ACTION_UI_REFRESH);
    assert(state.chat.phase == BUDDY_CHAT_THINKING && state.chat_since_ms == 1000);
    assert(strcmp(state.chat.said, "hello") == 0);
    /* The same step reported again does not restart the clock. */
    buddy_state_reduce(&state, &event, 3000, &action);
    assert(state.chat_since_ms == 1000);

    /* She hands the work over: the clock starts again for the helper, and the top
     * bar knows how many things are going on in the background. */
    event = chat_event(BUDDY_CHAT_HELPER, "hello", "", "codex", 7);
    snprintf(event.chat.stage, sizeof(event.chat.stage), "%s", "review retry()");
    snprintf(event.chat.card, sizeof(event.chat.card), "%s", "c3");
    event.chat.doing = 2;
    buddy_state_reduce(&state, &event, 5000, &action);
    assert(state.chat.phase == BUDDY_CHAT_HELPER && state.chat_since_ms == 5000);
    buddy_state_snapshot(&state, &snapshot);
    assert(strcmp(snapshot.chat.agent, "codex") == 0 &&
           strcmp(snapshot.chat.stage, "review retry()") == 0 && snapshot.chat_since_ms == 5000);
    assert(snapshot.doing == 2U && strcmp(snapshot.chat.card, "c3") == 0);
    /* "Handed over" without saying to whom is just thinking. */
    event = chat_event(BUDDY_CHAT_HELPER, "hello", "", "", 7);
    buddy_state_reduce(&state, &event, 6000, &action);
    assert(state.chat.phase == BUDDY_CHAT_THINKING);

    /* The answer arrives while the screen is dark: it lights up, and the screen
     * the owner was on stays. */
    state.screen_off = true;
    state.page = BUDDY_PAGE_TASKS;
    event = chat_event(BUDDY_CHAT_DONE, "hello", "the brief", "", 7);
    event.reply_truncated = true;
    buddy_state_reduce(&state, &event, 9000, &action);
    assert(!state.screen_off && state.page == BUDDY_PAGE_TASKS);
    assert(state.chat.phase == BUDDY_CHAT_DONE && state.chat.mood == BUDDY_MOOD_HAPPY);
    assert(strcmp(state.reply, "the brief") == 0 && state.reply_truncated);
    /* The same answer repeated later does not wake a screen the owner turned off. */
    state.screen_off = true;
    buddy_state_reduce(&state, &event, 9500, &action);
    assert(state.screen_off);
    state.screen_off = false;
    state.page = BUDDY_PAGE_HOME;

    /* A hub that reports the conversation owns the reply: a turn event must not
     * overwrite her words. */
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.host_chat);
    event = chat_event(BUDDY_CHAT_FAILED, "next question", "runtime is asleep", "", 7);
    buddy_state_reduce(&state, &event, 9800, &action);
    {
        buddy_event_t heartbeat = test_heartbeat_event(0, 0);
        buddy_event_t turn = {.type = BUDDY_EVENT_TURN};

        heartbeat.heartbeat.waiting = 0;
        buddy_state_reduce(&state, &heartbeat, 9900, &action);
        snprintf(turn.reply, sizeof(turn.reply), "%s", "from a turn event");
        buddy_state_reduce(&state, &turn, 9901, &action);
        assert(strcmp(state.reply, "runtime is asleep") == 0);
        assert(state.chat.phase == BUDDY_CHAT_FAILED);
    }

    /* "Nothing is being said" clears the turn and may still say how many things
     * are going on. */
    event = chat_event(BUDDY_CHAT_NONE, "", "", "", 7);
    event.chat.doing = 1;
    buddy_state_reduce(&state, &event, 9950, &action);
    assert(state.chat.phase == BUDDY_CHAT_NONE && state.reply[0] == '\0');
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.doing == 1U);

    /* The link goes away: nobody reports a turn or things in progress any more. */
    event = (buddy_event_t){.type = BUDDY_EVENT_BLE_DISCONNECTED};
    event.ble.connection_generation = 8;
    buddy_state_reduce(&state, &event, 10000, &action);
    assert(state.chat.phase == BUDDY_CHAT_NONE && state.chat.said[0] == '\0');
    assert(state.reply[0] == '\0' && state.page == BUDDY_PAGE_HOME);
    assert(!state.host_hub && !state.host_voice && !state.host_chat);
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.doing == 0U);
}

static void test_cards_are_kept_in_order_and_updated_in_place(void)
{
    static buddy_cards_t cards;
    static char long_reply[BUDDY_REPLY_MAX];
    buddy_card_update_t update = {0};
    unsigned index;
    char id[BUDDY_CARD_ID_MAX];

    memset(&cards, 0, sizeof(cards));
    /* A card needs an id. */
    assert(!buddy_cards_put(&cards, &update, "q", "a", false));
    assert(!buddy_cards_clear(&cards));

    snprintf(update.id, sizeof(update.id), "%s", "c1");
    snprintf(update.at, sizeof(update.at), "%s", "09:30");
    update.state = BUDDY_CARD_DONE;
    assert(buddy_cards_put(&cards, &update, "what time is it", "half past nine", false));
    snprintf(update.id, sizeof(update.id), "%s", "c2");
    snprintf(update.agent, sizeof(update.agent), "%s", "codex");
    update.state = BUDDY_CARD_WORKING;
    assert(buddy_cards_put(&cards, &update, "review retry()", "handed to codex", false));
    snprintf(update.id, sizeof(update.id), "%s", "c3");
    update.agent[0] = '\0';
    update.state = BUDDY_CARD_DONE;
    assert(buddy_cards_put(&cards, &update, "", "an answer without a question", true));
    assert(cards.count == 3 && cards.revision == 3U);
    assert(buddy_cards_find(&cards, "c2") == 1 && buddy_cards_find(&cards, "c9") == -1);
    assert(buddy_cards_find(&cards, "") == -1 && buddy_cards_find(NULL, "c1") == -1);
    assert(buddy_cards_said(&cards, 2)[0] == '\0' && cards.cards[2].cut);
    assert(buddy_cards_said(&cards, 9)[0] == '\0' && buddy_cards_reply(NULL, 0)[0] == '\0');

    /* The same card again changes nothing; a new state or new words update it
     * where it stands, and the others keep theirs. */
    snprintf(update.id, sizeof(update.id), "%s", "c2");
    snprintf(update.agent, sizeof(update.agent), "%s", "codex");
    update.state = BUDDY_CARD_WORKING;
    assert(!buddy_cards_put(&cards, &update, "review retry()", "handed to codex", false));
    assert(cards.revision == 3U);
    update.state = BUDDY_CARD_DONE;
    update.edits = 2;
    assert(buddy_cards_put(&cards, &update, "review retry()",
                           "codex found the problem on line 42, and it is a long story", false));
    assert(cards.count == 3 && buddy_cards_find(&cards, "c2") == 1);
    assert(cards.cards[1].state == BUDDY_CARD_DONE && cards.cards[1].edits == 2);
    assert(strcmp(cards.cards[1].agent, "codex") == 0 && strcmp(cards.cards[1].at, "09:30") == 0);
    assert(strcmp(buddy_cards_said(&cards, 0), "what time is it") == 0);
    assert(strcmp(buddy_cards_reply(&cards, 0), "half past nine") == 0);
    assert(strcmp(buddy_cards_said(&cards, 1), "review retry()") == 0);
    assert(strncmp(buddy_cards_reply(&cards, 1), "codex found", 11) == 0);
    assert(strcmp(buddy_cards_reply(&cards, 2), "an answer without a question") == 0);
    /* Shorter words free their room again. */
    {
        uint16_t used = cards.used;

        assert(buddy_cards_put(&cards, &update, "review retry()", "ok", false));
        assert(cards.used < used);
        assert(strcmp(buddy_cards_reply(&cards, 1), "ok") == 0);
        assert(strcmp(buddy_cards_reply(&cards, 2), "an answer without a question") == 0);
    }

    /* More cards than there are places: the oldest go. */
    assert(buddy_cards_clear(&cards) && cards.count == 0 && cards.used == 0);
    update = (buddy_card_update_t){.state = BUDDY_CARD_DONE};
    for (index = 0; index < BUDDY_CARD_COUNT + 3U; ++index) {
        snprintf(update.id, sizeof(update.id), "c%u", index);
        assert(buddy_cards_put(&cards, &update, "q", "a", false));
    }
    assert(cards.count == BUDDY_CARD_COUNT);
    assert(strcmp(cards.cards[0].id, "c3") == 0);
    snprintf(id, sizeof(id), "c%u", BUDDY_CARD_COUNT + 2U);
    assert(buddy_cards_find(&cards, id) == (int)BUDDY_CARD_COUNT - 1);

    /* More words than there is room for: again the oldest go, never the card
     * being written, and the words of the rest stay whole. */
    memset(long_reply, 'x', sizeof(long_reply) - 1U);
    for (index = 0; index < 8U; ++index) {
        snprintf(update.id, sizeof(update.id), "long%u", index);
        assert(buddy_cards_put(&cards, &update, "a long one", long_reply, false));
    }
    assert(cards.used <= sizeof(cards.text));
    assert(cards.count >= 3 && cards.count < BUDDY_CARD_COUNT);
    assert(strcmp(cards.cards[cards.count - 1U].id, "long7") == 0);
    for (index = 0; index < cards.count; ++index) {
        assert(strcmp(buddy_cards_said(&cards, index), "a long one") == 0 ||
               strcmp(buddy_cards_said(&cards, index), "q") == 0);
        assert(strlen(buddy_cards_reply(&cards, index)) == sizeof(long_reply) - 1U ||
               strcmp(buddy_cards_reply(&cards, index), "a") == 0);
    }
    /* Updating the oldest card when everything is full drops its neighbours. */
    snprintf(update.id, sizeof(update.id), "%s", cards.cards[0].id);
    update.state = BUDDY_CARD_FAILED;
    assert(buddy_cards_put(&cards, &update, "a long one", long_reply, false));
    assert(buddy_cards_find(&cards, update.id) >= 0 && cards.used <= sizeof(cards.text));
}

static void test_the_conversation_screen_shows_one_card_at_a_time(void)
{
    buddy_state_t state;
    buddy_ui_snapshot_t snapshot;
    buddy_action_t action = {0};
    buddy_event_t step_up = {.type = BUDDY_EVENT_CARD_STEP, .key = BUDDY_KEY_UP};
    buddy_event_t step_down = {.type = BUDDY_EVENT_CARD_STEP, .key = BUDDY_KEY_DOWN};
    buddy_event_t event;
    uint32_t serial;

    voice_ready_state(&state);
    state.page = BUDDY_PAGE_TALK;
    /* Cards of another connection are not taken. */
    event = card_event("c1", BUDDY_CARD_DONE, "", "one", "first", 6);
    buddy_state_reduce(&state, &event, 100, &action);
    assert(state.cards.count == 0 && action.type == BUDDY_ACTION_NONE);
    event = card_event("c1", BUDDY_CARD_DONE, "", "one", "first", 7);
    buddy_state_reduce(&state, &event, 101, &action);
    assert(action.type == BUDDY_ACTION_UI_REFRESH);
    event = card_event("c2", BUDDY_CARD_WORKING, "codex", "two", "handed to codex", 7);
    buddy_state_reduce(&state, &event, 102, &action);
    event = card_event("c3", BUDDY_CARD_DONE, "", "three", "third", 7);
    event.reply_truncated = true;
    buddy_state_reduce(&state, &event, 103, &action);
    /* The same card again is nothing new. */
    buddy_state_reduce(&state, &event, 104, &action);
    assert(action.type == BUDDY_ACTION_NONE);
    buddy_state_snapshot(&state, &snapshot);
    /* Nothing picked and no turn in progress: the newest. */
    assert(snapshot.cards == &state.cards && snapshot.cards->count == 3);
    assert(snapshot.card_index == 2 && !snapshot.card_live && snapshot.cards->cards[2].cut);

    /* Past the top of a card: the one before in the conversation; the screen
     * starts at its top. The thing handed to codex is a task and is skipped. */
    serial = state.card_serial;
    buddy_state_reduce(&state, &step_up, 200, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.card_index == 0 && snapshot.card_serial == serial + 1U);
    assert(action.type == BUDDY_ACTION_UI_REFRESH);
    buddy_state_reduce(&state, &step_up, 201, &action);
    buddy_state_reduce(&state, &step_up, 202, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.card_index == 0 && action.type == BUDDY_ACTION_NONE);
    /* A card that arrives or changes meanwhile does not take the screen away. */
    event = card_event("c4", BUDDY_CARD_DONE, "", "four", "fourth", 7);
    buddy_state_reduce(&state, &event, 203, &action);
    event = card_event("c2", BUDDY_CARD_DONE, "codex", "two", "codex is done", 7);
    buddy_state_reduce(&state, &event, 204, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.card_index == 0 && snapshot.cards->count == 4);
    buddy_state_reduce(&state, &step_down, 205, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.card_index == 2);
    assert(strcmp(buddy_cards_reply(snapshot.cards, 1), "codex is done") == 0);
    /* Steps only count on the conversation screen. */
    state.page = BUDDY_PAGE_HOME;
    buddy_state_reduce(&state, &step_down, 206, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.card_index == 2 && action.type == BUDDY_ACTION_NONE);
    state.page = BUDDY_PAGE_TALK;

    /* Something new is said: the screen goes with it. While it has no card the
     * turn itself is shown; once the hub says which card it went onto, that card. */
    serial = state.card_serial;
    event = chat_event(BUDDY_CHAT_THINKING, "and another thing", "", "", 7);
    buddy_state_reduce(&state, &event, 300, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.card_live && snapshot.card_serial == serial + 1U);
    /* From the turn in progress the way up leads to the newest card, and down
     * from there comes back to the turn. */
    buddy_state_reduce(&state, &step_up, 301, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(!snapshot.card_live && snapshot.card_index == 3);
    buddy_state_reduce(&state, &step_down, 302, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.card_live);
    /* The sentence turned out to belong to an existing thing. */
    event = chat_event(BUDDY_CHAT_HELPER, "and another thing", "", "codex", 7);
    snprintf(event.chat.card, sizeof(event.chat.card), "%s", "c2");
    buddy_state_reduce(&state, &event, 303, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(!snapshot.card_live && snapshot.card_index == 1);
    /* That thing is a task: the screen only says where the sentence went.
     * Paging from there goes through the conversation, and past its newest
     * card comes back to the sentence. */
    assert(buddy_cards_is_task(snapshot.cards, snapshot.card_index));
    buddy_state_reduce(&state, &step_up, 310, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.card_index == 0);
    buddy_state_reduce(&state, &step_down, 311, &action);
    buddy_state_reduce(&state, &step_down, 312, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.card_index == 3);
    buddy_state_reduce(&state, &step_down, 313, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.card_index == 1 && action.type == BUDDY_ACTION_UI_REFRESH);
    /* A new card for a new thing. */
    event = chat_event(BUDDY_CHAT_THINKING, "a new thing", "", "", 7);
    buddy_state_reduce(&state, &event, 304, &action);
    event = chat_event(BUDDY_CHAT_DONE, "a new thing", "brief", "", 7);
    snprintf(event.chat.card, sizeof(event.chat.card), "%s", "c5");
    buddy_state_reduce(&state, &event, 305, &action);
    buddy_state_snapshot(&state, &snapshot);
    /* Its card has not arrived yet: still the turn. */
    assert(snapshot.card_live);
    event = card_event("c5", BUDDY_CARD_DONE, "", "a new thing", "the whole answer", 7);
    buddy_state_reduce(&state, &event, 306, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(!snapshot.card_live && snapshot.card_index == 4);
    /* A turn that never gets a card (the recording was not understood). */
    event = chat_event(BUDDY_CHAT_THINKING, "", "", "", 7);
    buddy_state_reduce(&state, &event, 307, &action);
    event = chat_event(BUDDY_CHAT_FAILED, "", "did not catch that", "", 7);
    buddy_state_reduce(&state, &event, 308, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.card_live && strcmp(snapshot.reply, "did not catch that") == 0);

    /* The link goes away: the cards stay readable, the turn is gone. */
    event = (buddy_event_t){.type = BUDDY_EVENT_BLE_DISCONNECTED};
    event.ble.connection_generation = 8;
    buddy_state_reduce(&state, &event, 400, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.cards->count == 5 && !snapshot.card_live && snapshot.card_index == 4);

    /* Back again, the hub clears them before it sends them anew. */
    event = (buddy_event_t){.type = BUDDY_EVENT_BLE_CONNECTED};
    event.ble.connection_generation = 9;
    buddy_state_reduce(&state, &event, 401, &action);
    assert(state.cards.count == 5);
    event = (buddy_event_t){.type = BUDDY_EVENT_CARD};
    event.card.clear = true;
    event.ble.connection_generation = 9;
    buddy_state_reduce(&state, &event, 402, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.cards->count == 0 && snapshot.card_index == -1 && !snapshot.card_live);
    assert(action.type == BUDDY_ACTION_UI_REFRESH);
    buddy_state_reduce(&state, &event, 403, &action);
    assert(action.type == BUDDY_ACTION_NONE);
}

static buddy_event_t tasks_event(unsigned count, uint32_t generation)
{
    static const char *const ids[] = {"c2", "c5", "c7", "c8"};
    static const char *const agents[] = {"codex", "claude", "codex", "local"};
    buddy_event_t event = {.type = BUDDY_EVENT_TASKS};
    unsigned index;

    for (index = 0; index < count && index < BUDDY_TASK_COUNT; ++index) {
        snprintf(event.tasks[index].id, sizeof(event.tasks[index].id), "%s", ids[index]);
        snprintf(event.tasks[index].agent, sizeof(event.tasks[index].agent), "%s", agents[index]);
        snprintf(event.tasks[index].title, sizeof(event.tasks[index].title), "thing %u", index);
        event.tasks[index].seconds = 10U * (index + 1U);
    }
    event.task_count = count;
    event.ble.connection_generation = generation;
    return event;
}

static void test_the_third_screen_lists_the_things_in_progress(void)
{
    buddy_state_t state;
    buddy_ui_snapshot_t snapshot;
    buddy_action_t action = {0};
    buddy_event_t up = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_UP};
    buddy_event_t down = {.type = BUDDY_EVENT_KEY_CLICK, .key = BUDDY_KEY_DOWN};
    buddy_event_t long_ok = {.type = BUDDY_EVENT_KEY_LONG, .key = BUDDY_KEY_OK};
    buddy_event_t release = {.type = BUDDY_EVENT_KEY_RELEASE, .key = BUDDY_KEY_OK};
    buddy_event_t event;

    voice_ready_state(&state);
    event = card_event("c2", BUDDY_CARD_WORKING, "codex", "two", "handed to codex", 7);
    buddy_state_reduce(&state, &event, 90, &action);
    event = card_event("c5", BUDDY_CARD_WORKING, "claude", "five", "handed to claude", 7);
    buddy_state_reduce(&state, &event, 91, &action);
    event = card_event("c6", BUDDY_CARD_DONE, "", "six", "sixth", 7);
    buddy_state_reduce(&state, &event, 92, &action);

    /* A list from another connection is not taken. */
    event = tasks_event(3, 6);
    buddy_state_reduce(&state, &event, 100, &action);
    assert(state.task_count == 0 && action.type == BUDDY_ACTION_NONE);
    event = tasks_event(3, 7);
    buddy_state_reduce(&state, &event, 100, &action);
    assert(action.type == BUDDY_ACTION_UI_REFRESH);
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.task_count == 3 && snapshot.task_selected == 0);
    assert(snapshot.tasks_since_ms == 100 && snapshot.tasks[1].seconds == 20U);
    /* The top bar counts them even when the hub's last word on the turn did not. */
    assert(snapshot.doing == 3U);

    /* On the third screen UP and DOWN pick a thing, without wrapping round. */
    state.page = BUDDY_PAGE_TASKS;
    buddy_state_reduce(&state, &up, 110, &action);
    assert(state.task_selected == 0 && action.type == BUDDY_ACTION_NONE);
    buddy_state_reduce(&state, &down, 111, &action);
    assert(state.task_selected == 1 && action.type == BUDDY_ACTION_UI_REFRESH);
    /* The conversation screen stays on the conversation: tasks are not it. */
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.card_index == 2 && !snapshot.card_live);
    buddy_state_reduce(&state, &down, 112, &action);
    buddy_state_reduce(&state, &down, 113, &action);
    assert(state.task_selected == 2 && action.type == BUDDY_ACTION_NONE);
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.card_index == 2);

    /* Holding OK here talks inside the thing that is picked: it stays on it. */
    buddy_state_reduce(&state, &long_ok, 120, &action);
    assert(action.type == BUDDY_ACTION_VOICE_START && strcmp(action.voice_card, "c7") == 0);
    assert(action.voice_pin);
    buddy_state_reduce(&state, &release, 121, &action);
    event = voice_event(BUDDY_VOICE_TOO_SHORT, 7);
    buddy_state_reduce(&state, &event, 122, &action);
    /* On the conversation screen: about the card that is showing, as a hint. */
    state.page = BUDDY_PAGE_TALK;
    buddy_state_reduce(&state, &up, 123, &action);
    state.page = BUDDY_PAGE_TASKS;
    buddy_state_reduce(&state, &up, 124, &action);
    state.page = BUDDY_PAGE_TALK;
    buddy_state_reduce(&state, &long_ok, 125, &action);
    assert(action.type == BUDDY_ACTION_VOICE_START && strcmp(action.voice_card, "c6") == 0);
    assert(!action.voice_pin);
    buddy_state_reduce(&state, &release, 126, &action);
    event = voice_event(BUDDY_VOICE_TOO_SHORT, 7);
    buddy_state_reduce(&state, &event, 127, &action);
    /* On the first screen: about nothing in particular. */
    state.page = BUDDY_PAGE_HOME;
    buddy_state_reduce(&state, &long_ok, 128, &action);
    assert(action.type == BUDDY_ACTION_VOICE_START && action.voice_card[0] == '\0');
    buddy_state_reduce(&state, &release, 129, &action);
    event = voice_event(BUDDY_VOICE_FINISHED, 7);
    buddy_state_reduce(&state, &event, 130, &action);
    /* The recording is a turn without a card yet: holding OK on the conversation
     * screen now is not about the card that happened to be showing before. */
    state.page = BUDDY_PAGE_TALK;
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.card_live);
    buddy_state_reduce(&state, &long_ok, 131, &action);
    assert(action.type == BUDDY_ACTION_VOICE_START && action.voice_card[0] == '\0');
    buddy_state_reduce(&state, &release, 132, &action);
    event = voice_event(BUDDY_VOICE_TOO_SHORT, 7);
    buddy_state_reduce(&state, &event, 133, &action);

    /* The list changes: the selection stays on the same thing, or at the same
     * place when that thing is gone. */
    state.page = BUDDY_PAGE_TASKS;
    assert(state.task_selected == 1);
    event = tasks_event(4, 7);
    {
        buddy_task_t first = event.tasks[0];

        event.tasks[0] = event.tasks[1];
        event.tasks[1] = first;
    }
    buddy_state_reduce(&state, &event, 200, &action);
    assert(state.task_count == 4 && state.task_selected == 0);
    assert(strcmp(state.tasks[0].id, "c5") == 0 && state.tasks_since_ms == 200);
    state.task_selected = 3;
    event = tasks_event(2, 7);
    buddy_state_reduce(&state, &event, 201, &action);
    assert(state.task_count == 2 && state.task_selected == 1);
    event = tasks_event(0, 7);
    buddy_state_reduce(&state, &event, 202, &action);
    assert(state.task_count == 0 && state.task_selected == 0);
    buddy_state_reduce(&state, &long_ok, 203, &action);
    assert(action.type == BUDDY_ACTION_VOICE_START && action.voice_card[0] == '\0');

    /* Things that ended stay on the list but are not "in progress". */
    event = tasks_event(3, 7);
    event.tasks[1].state = BUDDY_TASK_DONE;
    event.tasks[2].state = BUDDY_TASK_FAILED;
    buddy_state_reduce(&state, &event, 250, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.task_count == 3 && snapshot.doing == 1U);
    assert(buddy_task_active(&snapshot.tasks[0]) && !buddy_task_active(&snapshot.tasks[1]));
    /* One of those can be picked and talked into as well. */
    buddy_state_reduce(&state, &release, 251, &action);
    event = voice_event(BUDDY_VOICE_TOO_SHORT, 7);
    buddy_state_reduce(&state, &event, 252, &action);
    buddy_state_reduce(&state, &down, 253, &action);
    buddy_state_reduce(&state, &long_ok, 254, &action);
    assert(action.type == BUDDY_ACTION_VOICE_START && strcmp(action.voice_card, "c5") == 0 &&
           action.voice_pin);
    buddy_state_reduce(&state, &release, 255, &action);
    event = voice_event(BUDDY_VOICE_TOO_SHORT, 7);
    buddy_state_reduce(&state, &event, 256, &action);

    /* The link goes away: nobody is reporting them any more. */
    event = tasks_event(3, 7);
    buddy_state_reduce(&state, &event, 300, &action);
    event = (buddy_event_t){.type = BUDDY_EVENT_BLE_DISCONNECTED};
    event.ble.connection_generation = 8;
    buddy_state_reduce(&state, &event, 301, &action);
    assert(state.task_count == 0);
}

static void test_a_host_without_cards_still_shows_its_latest_reply(void)
{
    buddy_state_t state;
    buddy_ui_snapshot_t snapshot;
    buddy_action_t action = {0};
    buddy_event_t heartbeat = test_heartbeat_event(0, 0);
    buddy_event_t turn = {.type = BUDDY_EVENT_TURN};
    uint32_t serial;

    /* The Claude desktop app: no cards, no chat; replies arrive as turn events. */
    buddy_state_init(&state, NULL);
    heartbeat.heartbeat.waiting = 0;
    buddy_state_reduce(&state, &heartbeat, 10, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(!snapshot.card_live && snapshot.card_index == -1);
    snprintf(turn.reply, sizeof(turn.reply), "%s", "first reply");
    buddy_state_reduce(&state, &turn, 20, &action);
    buddy_state_snapshot(&state, &snapshot);
    /* The conversation screen shows it in place of a card. */
    assert(snapshot.card_live && strcmp(snapshot.reply, "first reply") == 0);
    serial = snapshot.card_serial;
    /* The same reply again is not a new turn; another one is, and the screen
     * starts at its top. */
    buddy_state_reduce(&state, &turn, 30, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.card_serial == serial);
    snprintf(turn.reply, sizeof(turn.reply), "%s", "second reply");
    buddy_state_reduce(&state, &turn, 40, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.card_serial == serial + 1U && strcmp(snapshot.reply, "second reply") == 0);
    assert(state.cards.count == 0);
}

static void test_a_recording_on_its_way_is_not_wiped_by_an_idle_report(void)
{
    buddy_state_t state;
    buddy_action_t action = {0};
    buddy_event_t long_ok = {.type = BUDDY_EVENT_KEY_LONG, .key = BUDDY_KEY_OK};
    buddy_event_t release = {.type = BUDDY_EVENT_KEY_RELEASE, .key = BUDDY_KEY_OK};
    buddy_event_t event;

    voice_ready_state(&state);
    event = chat_event(BUDDY_CHAT_DONE, "before", "an earlier answer", "", 7);
    buddy_state_reduce(&state, &event, 500, &action);

    buddy_state_reduce(&state, &long_ok, 1000, &action);
    event = voice_event(BUDDY_VOICE_STARTED, 7);
    buddy_state_reduce(&state, &event, 1100, &action);
    buddy_state_reduce(&state, &release, 3000, &action);
    event = voice_event(BUDDY_VOICE_FINISHED, 7);
    buddy_state_reduce(&state, &event, 3200, &action);
    /* The earlier turn is gone from the screen the moment the new one leaves. */
    assert(state.chat.phase == BUDDY_CHAT_SENT && state.reply[0] == '\0');
    assert(state.chat.said[0] == '\0' && state.chat_since_ms == 3200);

    /* The hub has not noticed yet and still says "nothing going on". */
    event = chat_event(BUDDY_CHAT_NONE, "", "", "", 7);
    buddy_state_reduce(&state, &event, 3300, &action);
    assert(state.chat.phase == BUDDY_CHAT_SENT);
    /* Or it reports the end of something else: the turn before, or a thing in the
     * background. That is not the answer to this one; only the count is taken. */
    event = chat_event(BUDDY_CHAT_DONE, "before", "an earlier answer", "", 7);
    event.chat.doing = 1;
    buddy_state_reduce(&state, &event, 3400, &action);
    assert(state.chat.phase == BUDDY_CHAT_SENT && state.reply[0] == '\0');
    assert(state.chat.doing == 1U);
    /* Then it starts on it; for the owner it is the same wait. */
    event = chat_event(BUDDY_CHAT_THINKING, "", "", "", 7);
    buddy_state_reduce(&state, &event, 3600, &action);
    assert(state.chat.phase == BUDDY_CHAT_THINKING && state.chat_since_ms == 3200);
    event = chat_event(BUDDY_CHAT_THINKING, "what it heard", "", "", 7);
    buddy_state_reduce(&state, &event, 4100, &action);
    assert(strcmp(state.chat.said, "what it heard") == 0 && state.chat_since_ms == 3200);

    /* A recording that was too short leaves the previous turn alone. */
    event = chat_event(BUDDY_CHAT_DONE, "what it heard", "the answer", "", 7);
    buddy_state_reduce(&state, &event, 5000, &action);
    buddy_state_reduce(&state, &long_ok, 6000, &action);
    buddy_state_reduce(&state, &release, 6100, &action);
    event = voice_event(BUDDY_VOICE_TOO_SHORT, 7);
    buddy_state_reduce(&state, &event, 6200, &action);
    assert(state.chat.phase == BUDDY_CHAT_DONE && strcmp(state.reply, "the answer") == 0);
    /* And an idle report now does clear the turn: nothing is in flight. */
    event = chat_event(BUDDY_CHAT_NONE, "", "", "", 7);
    buddy_state_reduce(&state, &event, 6300, &action);
    assert(state.chat.phase == BUDDY_CHAT_NONE && state.reply[0] == '\0');

    /* A recording that was not understood ends the wait: that report says
     * nothing about what was said, because nobody knows. */
    buddy_state_reduce(&state, &long_ok, 7000, &action);
    event = voice_event(BUDDY_VOICE_STARTED, 7);
    buddy_state_reduce(&state, &event, 7100, &action);
    buddy_state_reduce(&state, &release, 8000, &action);
    event = voice_event(BUDDY_VOICE_FINISHED, 7);
    buddy_state_reduce(&state, &event, 8100, &action);
    assert(state.chat.phase == BUDDY_CHAT_SENT);
    event = chat_event(BUDDY_CHAT_FAILED, "", "did not catch that", "", 7);
    buddy_state_reduce(&state, &event, 8200, &action);
    assert(state.chat.phase == BUDDY_CHAT_FAILED &&
           strcmp(state.reply, "did not catch that") == 0);
}

static void test_helpers_belong_to_the_connection(void)
{
    buddy_state_t state;
    buddy_ui_snapshot_t snapshot;
    buddy_action_t action = {0};
    buddy_event_t helpers = {.type = BUDDY_EVENT_HELPERS, .helper_count = 2};
    buddy_event_t connected = {.type = BUDDY_EVENT_BLE_CONNECTED};

    voice_ready_state(&state);
    snprintf(helpers.helpers[0].name, sizeof(helpers.helpers[0].name), "%s", "claude");
    snprintf(helpers.helpers[1].name, sizeof(helpers.helpers[1].name), "%s", "codex");
    snprintf(helpers.helpers[1].about, sizeof(helpers.helpers[1].about), "%s", "reviews code");
    helpers.ble.connection_generation = 6;
    buddy_state_reduce(&state, &helpers, 100, &action);
    assert(state.helper_count == 0);
    helpers.ble.connection_generation = 7;
    buddy_state_reduce(&state, &helpers, 101, &action);
    assert(action.type == BUDDY_ACTION_UI_REFRESH);
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.helper_count == 2 && strcmp(snapshot.helpers[1].name, "codex") == 0 &&
           strcmp(snapshot.helpers[1].about, "reviews code") == 0);
    /* A count larger than the table is not trusted. */
    helpers.helper_count = 99;
    buddy_state_reduce(&state, &helpers, 102, &action);
    assert(state.helper_count == BUDDY_HELPER_COUNT);
    /* An empty list clears them, and so does a different host connecting. */
    helpers.helper_count = 0;
    memset(helpers.helpers, 0, sizeof(helpers.helpers));
    buddy_state_reduce(&state, &helpers, 103, &action);
    assert(state.helper_count == 0);
    helpers.helper_count = 1;
    snprintf(helpers.helpers[0].name, sizeof(helpers.helpers[0].name), "%s", "claude");
    buddy_state_reduce(&state, &helpers, 104, &action);
    connected.ble.connection_generation = 8;
    buddy_state_reduce(&state, &connected, 105, &action);
    assert(state.helper_count == 0 && state.helpers[0].name[0] == '\0' && !state.host_hub);
}

static void test_a_notice_remembers_when_it_appeared(void)
{
    buddy_state_t state;
    buddy_ui_snapshot_t snapshot;
    buddy_action_t action = {0};
    buddy_event_t long_ok = {.type = BUDDY_EVENT_KEY_LONG, .key = BUDDY_KEY_OK};
    buddy_event_t tick = {.type = BUDDY_EVENT_TICK};

    buddy_state_init(&state, NULL);
    buddy_state_reduce(&state, &tick, 100, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.message[0] == '\0');
    /* Holding OK with nobody connected leaves a line saying why nothing happened. */
    buddy_state_reduce(&state, &long_ok, 5000, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(strcmp(snapshot.message, PT_VOICE_NEED_LINK) == 0);
    assert(snapshot.message_since_ms == 5000);
    /* The same line later is still the same notice. */
    buddy_state_reduce(&state, &tick, 9000, &action);
    buddy_state_reduce(&state, &long_ok, 9500, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.message_since_ms == 5000);
    /* Text written from outside the state machine is noticed on the next event. */
    snprintf(state.message, sizeof(state.message), "%s", PT_MSG_BLE_FAILED);
    buddy_state_reduce(&state, &tick, 12000, &action);
    buddy_state_snapshot(&state, &snapshot);
    assert(snapshot.message_since_ms == 12000);
}

int main(void)
{
    test_offline_initialization();
    test_heartbeat_mapping();
    test_character_priority();
    test_timeout_clears_prompt();
    test_heartbeat_does_not_overwrite_persisted_identity();
    test_token_boundaries_celebrate_once();
    test_persisted_celebration_level_is_not_replayed();
    test_approval_locks_until_a_new_prompt();
    test_decision_is_remembered_until_the_prompt_goes_away();
    test_denial_locks_and_emits_exactly_once();
    test_permission_action_is_bound_to_the_prompt_connection();
    test_permission_success_moves_from_attempted_to_successful_state();
    test_permission_failure_is_visible_and_same_id_replay_stays_locked();
    test_denial_success_and_ticks_never_show_heart();
    test_successful_approval_heart_expires_at_five_seconds();
    test_heartbeat_prompt_snapshot_clears_or_preserves_approval_lock();
    test_ui_snapshot_runtime_indicators_default_off();
    test_absent_prompt_is_ignored();
    test_timeout_stale_prompt_is_ignored();
    test_standalone_prompt_refreshes_liveness_clock();
    test_disconnect_then_reconnect_does_not_restore_prompt();
    test_mismatched_observed_prompt_is_ignored();
    test_nonterminated_prompt_id_is_ignored();
    test_truncated_prompt_id_is_ignored();
    test_double_ok_opens_the_menu();
    test_hold_ok_talks_and_release_sends();
    test_talking_needs_a_voice_capable_host();
    test_ok_goes_round_the_three_screens();
    test_guide_scrolls_and_returns_to_more();
    test_screen_off_wakes_on_key_and_on_attention();
    test_assistant_turn_is_kept_only_while_connected();
    test_protocol_command_events_refresh_the_display();
    test_status_request_does_not_overwrite_message();
    test_unpair_confirmation_survives_a_heartbeat();
    test_unpair_confirmation_ok_emits_explicit_action();
    test_unpair_confirmation_down_cancels_without_action();
    test_parsed_heartbeat_approval_serializes_permission();
    test_normal_navigation_and_approval_scroll_are_distinct();
    test_settings_actions_have_separate_confirmations();
    test_menu_surface_is_complete_and_bounded();
    test_remote_unpair_confirmation_remembers_ack();
    test_remote_unpair_cannot_replace_a_local_confirmation();
    test_ble_security_events_update_owned_state_and_clear_sensitive_prompt();
    test_stale_security_mailbox_event_cannot_override_latest_link();
    test_new_link_generation_invalidates_sensitive_state_when_disconnect_was_coalesced();
    test_the_first_screen_follows_the_hub();
    test_cards_are_kept_in_order_and_updated_in_place();
    test_the_conversation_screen_shows_one_card_at_a_time();
    test_the_third_screen_lists_the_things_in_progress();
    test_a_host_without_cards_still_shows_its_latest_reply();
    test_a_recording_on_its_way_is_not_wiped_by_an_idle_report();
    test_helpers_belong_to_the_connection();
    test_a_notice_remembers_when_it_appeared();
    return 0;
}
