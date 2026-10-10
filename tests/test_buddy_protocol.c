#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "buddy_protocol.h"
#include "official_reference_fixtures.h"

static int parse(const char *json, buddy_event_t *event)
{
    return buddy_protocol_parse(json, strlen(json), event);
}

static void test_official_heartbeat_maps_documented_fields(void)
{
    buddy_event_t event = {0};
    const char *json = OFFICIAL_HEARTBEAT_JSON;

    assert(parse(json, &event) == BUDDY_EVENT_HEARTBEAT);
    assert(event.type == BUDDY_EVENT_HEARTBEAT);
    assert(event.heartbeat.connected);
    assert(event.heartbeat.total == 3);
    assert(event.heartbeat.running == 1);
    assert(event.heartbeat.waiting == 1);
    assert(strcmp(event.heartbeat.message, "approve: Bash") == 0);
    assert(event.heartbeat.tokens == 184502);
    assert(event.heartbeat.tokens_today == 31200);
    assert(strcmp(event.heartbeat.entries[0], "10:42 git push") == 0);
    assert(strcmp(event.heartbeat.entries[1], "10:41 yarn test") == 0);
    assert(strcmp(event.heartbeat.prompt.id, "req_abc123") == 0);
}

static void test_heartbeat_optional_prompt_stays_in_heartbeat_snapshot(void)
{
    buddy_event_t event = {0};
    const char *json = OFFICIAL_HEARTBEAT_JSON;

    assert(parse(json, &event) == BUDDY_EVENT_HEARTBEAT);
    assert(event.type == BUDDY_EVENT_HEARTBEAT);
    assert(event.heartbeat.prompt.connected);
    assert(event.heartbeat.prompt.running == 1);
    assert(event.heartbeat.prompt.id_length == strlen("req_abc123"));
    assert(!event.heartbeat.prompt.id_truncated);
    assert(strcmp(event.heartbeat.prompt.id, "req_abc123") == 0);
    assert(strcmp(event.heartbeat.prompt.tool, "Bash") == 0);
    assert(strcmp(event.heartbeat.prompt.hint, "rm -rf /tmp/foo") == 0);
}

static void test_hub_hello_reports_voice_support(void)
{
    buddy_event_t event;

    assert(parse("{\"cmd\":\"hub\",\"voice\":true}", &event) == BUDDY_EVENT_HOST_HELLO);
    assert(event.host_voice);
    assert(parse("{\"cmd\":\"hub\",\"voice\":false}", &event) == BUDDY_EVENT_HOST_HELLO);
    assert(!event.host_voice);
    /* Only a JSON true counts; anything else means "no voice". */
    assert(parse("{\"cmd\":\"hub\"}", &event) == BUDDY_EVENT_HOST_HELLO);
    assert(!event.host_voice);
    assert(parse("{\"cmd\":\"hub\",\"voice\":\"yes\"}", &event) == BUDDY_EVENT_HOST_HELLO);
    assert(!event.host_voice);
}

static void test_unpair_maps_confirmation_event(void)
{
    buddy_event_t event = {0};

    assert(parse("{\"cmd\":\"unpair\"}", &event) == BUDDY_EVENT_UNPAIR_CONFIRMATION);
    assert(event.type == BUDDY_EVENT_UNPAIR_CONFIRMATION);
}

static void test_file_transfer_commands_are_unsupported(void)
{
    static const char *const commands[] = {
        "char_begin", "file", "chunk", "file_end", "char_end", "folder_begin",
    };
    buddy_event_t event = {0};
    size_t index;

    for (index = 0; index < sizeof(commands) / sizeof(commands[0]); ++index) {
        char json[64];

        snprintf(json, sizeof(json), "{\"cmd\":\"%s\"}", commands[index]);
        assert(parse(json, &event) == BUDDY_EVENT_UNSUPPORTED_COMMAND);
        assert(event.type == BUDDY_EVENT_NONE);
    }
}

static void test_unknown_command_is_rejected(void)
{
    buddy_event_t event = {0};

    assert(parse("{\"cmd\":\"mystery\"}", &event) == BUDDY_EVENT_UNKNOWN_COMMAND);
    assert(event.type == BUDDY_EVENT_NONE);
}

static void test_malformed_or_nonobject_json_is_rejected(void)
{
    buddy_event_t event = {0};

    assert(parse("{\"cmd\":", &event) == BUDDY_EVENT_MALFORMED);
    assert(parse("[]", &event) == BUDDY_EVENT_MALFORMED);
}

static void test_oversized_prompt_id_is_rejected(void)
{
    buddy_event_t event = {0};
    char id[BUDDY_PROMPT_ID_MAX + 1];
    char json[BUDDY_JSON_LINE_MAX];

    memset(id, 'x', sizeof(id) - 1);
    id[sizeof(id) - 1] = '\0';
    snprintf(json, sizeof(json),
             "{\"total\":1,\"running\":0,\"waiting\":1,\"msg\":\"approve\","
             "\"entries\":[],\"tokens\":0,\"tokens_today\":0,"
             "\"prompt\":{\"id\":\"%s\"}}", id);

    assert(parse(json, &event) == BUDDY_EVENT_MALFORMED);
    assert(event.type == BUDDY_EVENT_NONE);
}

static void test_oversized_nested_prompt_id_is_rejected(void)
{
    buddy_event_t event = {0};
    char id[BUDDY_PROMPT_ID_MAX + 1];
    char json[BUDDY_JSON_LINE_MAX];

    memset(id, 'x', sizeof(id) - 1);
    id[sizeof(id) - 1] = '\0';
    snprintf(json, sizeof(json),
             "{\"total\":1,\"running\":0,\"waiting\":1,\"msg\":\"approve\","
             "\"entries\":[],\"tokens\":0,\"tokens_today\":0,"
             "\"prompt\":{\"id\":\"%s\"}}", id);

    assert(parse(json, &event) == BUDDY_EVENT_MALFORMED);
    assert(event.type == BUDDY_EVENT_NONE);
}

static void test_prompt_id_must_be_a_nonempty_string(void)
{
    buddy_event_t event = {0};

    assert(parse("{\"total\":1,\"running\":0,\"waiting\":1,\"msg\":\"x\","
                 "\"entries\":[],\"tokens\":0,\"tokens_today\":0,\"prompt\":{}}",
                 &event) == BUDDY_EVENT_MALFORMED);
    assert(parse("{\"total\":1,\"running\":0,\"waiting\":1,\"msg\":\"x\","
                 "\"entries\":[],\"tokens\":0,\"tokens_today\":0,"
                 "\"prompt\":{\"id\":\"\"}}", &event) == BUDDY_EVENT_MALFORMED);
    assert(parse("{\"total\":1,\"running\":0,\"waiting\":1,\"msg\":\"x\","
                 "\"entries\":[],\"tokens\":0,\"tokens_today\":0,"
                 "\"prompt\":{\"id\":7}}", &event) == BUDDY_EVENT_MALFORMED);
}

static void test_prompt_id_rejects_an_embedded_nul(void)
{
    buddy_event_t event = {0};

    assert(parse("{\"total\":1,\"running\":0,\"waiting\":1,\"msg\":\"x\","
                 "\"entries\":[],\"tokens\":0,\"tokens_today\":0,"
                 "\"prompt\":{\"id\":\"req\\u0000other\"}}", &event) ==
           BUDDY_EVENT_MALFORMED);
}

static void test_parser_rejects_trailing_bytes(void)
{
    buddy_event_t event = {0};

    assert(parse("{\"cmd\":\"unpair\"} trailing", &event) == BUDDY_EVENT_MALFORMED);
}

static void test_heartbeat_rejects_nonintegral_counters(void)
{
    buddy_event_t event = {0};

    assert(parse("{\"total\":1,\"running\":1.5,\"waiting\":0,\"msg\":\"x\","
                 "\"entries\":[],\"tokens\":0,\"tokens_today\":0}", &event) ==
           BUDDY_EVENT_MALFORMED);
    assert(parse("{\"total\":1,\"running\":0,\"waiting\":0,\"msg\":\"x\","
                 "\"entries\":[],\"tokens\":1.5,\"tokens_today\":0}", &event) ==
           BUDDY_EVENT_MALFORMED);
}

static void test_display_strings_are_bounded(void)
{
    buddy_event_t event = {0};
    char status[BUDDY_MESSAGE_MAX + 4];
    char json[BUDDY_JSON_LINE_MAX];

    memset(status, 's', BUDDY_MESSAGE_MAX - 2);
    memcpy(status + BUDDY_MESSAGE_MAX - 2, "\xe4\xb8\xad", 3);
    status[BUDDY_MESSAGE_MAX + 1] = '\0';
    snprintf(json, sizeof(json),
             "{\"total\":0,\"running\":0,\"waiting\":0,\"msg\":\"%s\","
             "\"entries\":[],\"tokens\":0,\"tokens_today\":0}", status);

    assert(parse(json, &event) == BUDDY_EVENT_HEARTBEAT);
    assert(event.heartbeat.message[sizeof(event.heartbeat.message) - 1] == '\0');
    assert(event.heartbeat.message_truncated);
    assert(strlen(event.heartbeat.message) == BUDDY_MESSAGE_MAX - 2);
    assert(event.heartbeat.message[BUDDY_MESSAGE_MAX - 2] == '\0');
}

static void test_official_time_owner_and_name_payloads(void)
{
    struct command_case {
        const char *json;
        buddy_event_type_t type;
        const char *value;
    } cases[] = {
        {OFFICIAL_NAME_JSON, BUDDY_EVENT_NAME, "Clawd"},
        {OFFICIAL_OWNER_JSON, BUDDY_EVENT_OWNER, "Felix"},
    };
    size_t index;

    for (index = 0; index < sizeof(cases) / sizeof(cases[0]); ++index) {
        buddy_event_t event = {0};

        assert(parse(cases[index].json, &event) == (int)cases[index].type);
        assert(event.type == cases[index].type);
        assert(strcmp(event.command.value, cases[index].value) == 0);
        assert(!event.command.value_truncated);
    }
    buddy_event_t time_event = {0};
    assert(parse(OFFICIAL_TIME_JSON, &time_event) == BUDDY_EVENT_TIME);
    assert(time_event.time.epoch_seconds == 1775731234);
    assert(time_event.time.timezone_offset_seconds == -25200);
    assert(time_event.command.name[0] == '\0');
}

static void test_status_is_a_no_payload_request(void)
{
    buddy_event_t event = {0};

    assert(parse("{\"cmd\":\"status\"}", &event) == BUDDY_EVENT_STATUS_REQUEST);
    assert(event.type == BUDDY_EVENT_STATUS_REQUEST);
    assert(event.command.value[0] == '\0');
    assert(parse("{\"cmd\":\"status\",\"status\":\"Ready\"}", &event) == BUDDY_EVENT_MALFORMED);
}

static void test_name_command_truncates_at_a_utf8_codepoint_boundary(void)
{
    buddy_event_t event = {0};
    char name[BUDDY_NAME_MAX + 4];
    char json[BUDDY_NAME_MAX + 40];

    memset(name, 'n', BUDDY_NAME_MAX - 2);
    memcpy(name + BUDDY_NAME_MAX - 2, "\xe4\xb8\xad", 3);
    name[BUDDY_NAME_MAX + 1] = '\0';
    snprintf(json, sizeof(json), "{\"cmd\":\"name\",\"name\":\"%s\"}", name);

    assert(parse(json, &event) == BUDDY_EVENT_NAME);
    assert(event.command.value_truncated);
    assert(strlen(event.command.value) == BUDDY_NAME_MAX - 2);
    assert(event.command.value[BUDDY_NAME_MAX - 2] == '\0');
}

static void test_parser_rejects_oversized_or_invalid_utf8_lines(void)
{
    buddy_event_t event = {0};
    char oversized[BUDDY_JSON_LINE_MAX + 1];
    char invalid_utf8[] = "{\"cmd\":\"name\",\"name\":\"\xc0\xaf\"}";

    memset(oversized, 'x', sizeof(oversized));
    assert(buddy_protocol_parse(oversized, sizeof(oversized), &event) == BUDDY_EVENT_MALFORMED);
    assert(parse(invalid_utf8, &event) == BUDDY_EVENT_MALFORMED);
}

static void test_parser_rejects_raw_nul_and_control_bytes(void)
{
    buddy_event_t event = {0};
    static const char raw_nul[] = {
        '{', '"', 'c', 'm', 'd', '"', ':', '"', 'p', 'r', 'o', 'm', 'p', 't', '"', ',',
        '"', 'i', 'd', '"', ':', '"', 'r', 'e', 'q', '\0', 's', 'u', 'f', 'f', 'i', 'x',
        '"', '}',
    };
    static const char string_control[] = {
        '{', '"', 'c', 'm', 'd', '"', ':', '"', 'n', 'a', 'm', 'e', '"', ',',
        '"', 'n', 'a', 'm', 'e', '"', ':', '"', 'a', '\x1f', 'b', '"', '}',
    };
    static const char structural_control[] = {
        '{', '\x01', '"', 'c', 'm', 'd', '"', ':', '"', 's', 't', 'a', 't', 'u', 's', '"', '}',
    };

    assert(buddy_protocol_parse(raw_nul, sizeof(raw_nul), &event) == BUDDY_EVENT_MALFORMED);
    assert(buddy_protocol_parse(string_control, sizeof(string_control), &event) ==
           BUDDY_EVENT_MALFORMED);
    assert(buddy_protocol_parse(structural_control, sizeof(structural_control), &event) ==
           BUDDY_EVENT_MALFORMED);
    assert(parse("{\n\"cmd\":\"status\"\n}", &event) == BUDDY_EVENT_STATUS_REQUEST);
}

static void test_parser_rejects_excessive_json_nesting(void)
{
    buddy_event_t event = {0};
    char json[256];
    size_t length = 0;
    unsigned depth;

    for (depth = 0; depth < BUDDY_JSON_MAX_DEPTH + 1U; ++depth) {
        length += (size_t)snprintf(json + length, sizeof(json) - length, "{\"x\":");
    }
    length += (size_t)snprintf(json + length, sizeof(json) - length, "0");
    for (depth = 0; depth < BUDDY_JSON_MAX_DEPTH + 1U; ++depth) {
        length += (size_t)snprintf(json + length, sizeof(json) - length, "}");
    }

    assert(length < sizeof(json));
    assert(buddy_protocol_parse(json, length, &event) == BUDDY_EVENT_MALFORMED);
}

static void test_preflight_failure_clears_a_reused_event(void)
{
    buddy_event_t event = {0};

    assert(parse("{\"cmd\":\"status\"}", &event) == BUDDY_EVENT_STATUS_REQUEST);
    assert(event.command.name[0] != '\0');
    assert(parse("{\"cmd\":\"status\"}\x01", &event) == BUDDY_EVENT_MALFORMED);
    assert(event.type == BUDDY_EVENT_NONE);
    assert(event.command.name[0] == '\0');
}

static void test_serializers_write_documented_json(void)
{
    char json[192];

    assert(buddy_protocol_permission_json(json, sizeof(json), "req_abc123",
                                          BUDDY_PERMISSION_ONCE) > 0);
    assert(strcmp(json,
                  "{\"cmd\":\"permission\",\"id\":\"req_abc123\",\"decision\":\"once\"}\n") ==
           0);
}

static void test_serializers_fail_without_writing_past_the_output_bound(void)
{
    struct {
        char json[8];
        char guard;
    } output;

    memset(&output, 'x', sizeof(output));
    output.guard = 'g';
    assert(buddy_protocol_permission_json(output.json, sizeof(output.json), "req_abc123",
                                          BUDDY_PERMISSION_ONCE) == 0);
    assert(output.json[sizeof(output.json) - 1] == '\0');
    assert(output.guard == 'g');
}

static void test_serializers_reject_invalid_or_unterminated_inputs(void)
{
    char json[192];
    char id[BUDDY_PROMPT_ID_MAX];

    memset(id, 'x', sizeof(id));
    assert(buddy_protocol_permission_json(json, sizeof(json), id, BUDDY_PERMISSION_ONCE) == 0);
    assert(buddy_protocol_permission_json(json, sizeof(json), "\xc0\xaf", BUDDY_PERMISSION_ONCE) == 0);
}

static void test_command_ack_names_the_request_and_error(void)
{
    buddy_event_t event = {0};
    char json[160];

    assert(parse("{\"cmd\":\"char_begin\"}", &event) == BUDDY_EVENT_UNSUPPORTED_COMMAND);
    assert(strcmp(event.command.name, "char_begin") == 0);
    assert(buddy_protocol_command_ack_json(json, sizeof(json), event.command.name, false,
                                           "unsupported in phase 1") > 0);
    assert(strcmp(json,
                  "{\"ack\":\"char_begin\",\"ok\":false,"
                  "\"error\":\"unsupported in phase 1\"}\n") == 0);
}

static void test_device_status_omits_unavailable_battery_fields(void)
{
    buddy_status_report_t status = {
        .encrypted = true,
        .uptime_ms = 123456,
        .free_heap = 32000,
        .approval_count = 7,
        .denial_count = 2,
        .queue_overflow_count = 4,
        .battery_available = false,
    };
    char json[320];

    snprintf(status.name, sizeof(status.name), "%s", "Buddy");
    assert(buddy_protocol_device_status_json(json, sizeof(json), &status) > 0);
    assert(strcmp(json,
                  "{\"ack\":\"status\",\"ok\":true,\"data\":{"
                  "\"name\":\"Buddy\",\"sec\":true,"
                  "\"sys\":{\"up\":123,\"heap\":32000},"
                  "\"stats\":{\"appr\":7,\"deny\":2,\"lvl\":0}}}\n") == 0);
    assert(strstr(json, "\"bat\"") == NULL);

    status.battery_available = true;
    status.battery_percent = 73;
    status.battery_mv = 3875;
    assert(buddy_protocol_device_status_json(json, sizeof(json), &status) > 0);
    assert(strstr(json, "\"bat\":{\"pct\":73,\"mV\":3875}") != NULL);
    assert(strstr(json, "\"sys\":{\"up\":123,\"heap\":32000}") != NULL);
}

static void test_task_tx_capacity_handles_worst_case_escaping(void)
{
    char id[BUDDY_PROMPT_ID_MAX];
    buddy_status_report_t status = {0};
    char json[BUDDY_PROTOCOL_TX_MAX];
    size_t index;

    for (index = 0; index + 1U < sizeof(id); ++index) {
        id[index] = '\x01';
    }
    id[sizeof(id) - 1U] = '\0';
    memset(status.name, '\x01', sizeof(status.name) - 1U);
    status.encrypted = true;
    status.battery_available = true;
    status.battery_percent = 100;
    status.battery_mv = UINT16_MAX;
    status.uptime_ms = UINT64_MAX;
    status.free_heap = UINT64_MAX;
    status.approval_count = UINT64_MAX;
    status.denial_count = UINT64_MAX;
    status.queue_overflow_count = UINT64_MAX;

    assert(buddy_protocol_permission_json(json, sizeof(json), id, BUDDY_PERMISSION_ONCE) > 0);
    assert(buddy_protocol_device_status_json(json, sizeof(json), &status) > 0);
}

static int test_parse_literal(const char *json, buddy_event_t *event)
{
    return buddy_protocol_parse(json, strlen(json), event);
}

static void test_assistant_turn_keeps_text_blocks_only(void)
{
    buddy_event_t event;

    assert(test_parse_literal(
               "{\"evt\":\"turn\",\"role\":\"assistant\",\"content\":["
               "{\"type\":\"text\",\"text\":\"\xE5\xA5\xBD\xE7\x9A\x84\"},"
               "{\"type\":\"tool_use\",\"id\":\"t1\",\"name\":\"Bash\",\"input\":{\"command\":\"ls\"}},"
               "{\"type\":\"text\",\"text\":\"done\"}]}",
               &event) == BUDDY_EVENT_TURN);
    assert(event.type == BUDDY_EVENT_TURN);
    assert(strcmp(event.reply, "\xE5\xA5\xBD\xE7\x9A\x84\ndone") == 0);
    assert(!event.reply_truncated);
}

static void test_other_turns_and_events_are_ignored_not_rejected(void)
{
    buddy_event_t event;

    assert(test_parse_literal(
               "{\"evt\":\"turn\",\"role\":\"user\",\"content\":["
               "{\"type\":\"text\",\"text\":\"secret question\"}]}",
               &event) == BUDDY_EVENT_NONE);
    assert(event.type == BUDDY_EVENT_NONE && event.reply[0] == '\0');
    assert(test_parse_literal("{\"evt\":\"future\",\"x\":1}", &event) == BUDDY_EVENT_NONE);
    assert(test_parse_literal(
               "{\"evt\":\"turn\",\"role\":\"assistant\",\"content\":["
               "{\"type\":\"tool_use\",\"id\":\"t1\"}]}",
               &event) == BUDDY_EVENT_NONE);
    assert(test_parse_literal("{\"evt\":\"turn\",\"role\":\"assistant\"}", &event) ==
           BUDDY_EVENT_MALFORMED);
    assert(test_parse_literal("{\"evt\":7}", &event) == BUDDY_EVENT_MALFORMED);
    assert(test_parse_literal(
               "{\"evt\":\"turn\",\"role\":\"assistant\",\"content\":["
               "{\"type\":\"text\",\"text\":5}]}",
               &event) == BUDDY_EVENT_MALFORMED);
}

static void test_long_assistant_turn_is_cut_on_a_character_boundary(void)
{
    static char json[BUDDY_JSON_LINE_MAX];
    buddy_event_t event;
    size_t length;
    unsigned index;

    length = (size_t)snprintf(json, sizeof(json), "%s",
                              "{\"evt\":\"turn\",\"role\":\"assistant\",\"content\":["
                              "{\"type\":\"text\",\"text\":\"a");
    /* 'a' + 3-byte characters: the limit cannot land exactly on a boundary. */
    for (index = 0; index < 400U; ++index) {
        memcpy(json + length, "\xE4\xB8\xAD", 3);
        length += 3U;
    }
    length += (size_t)snprintf(json + length, sizeof(json) - length, "%s", "\"}]}");
    assert(buddy_protocol_parse(json, length, &event) == BUDDY_EVENT_TURN);
    assert(event.reply_truncated);
    assert(strlen(event.reply) < BUDDY_REPLY_MAX);
    assert(strlen(event.reply) == 1U + 3U * ((BUDDY_REPLY_MAX - 2U) / 3U));
    assert(memcmp(event.reply + strlen(event.reply) - 3U, "\xE4\xB8\xAD", 3) == 0);
}

static void test_chat_reports_the_conversation(void)
{
    static char line[BUDDY_JSON_LINE_MAX];
    buddy_event_t event;
    char output[64];
    size_t index;

    assert(parse("{\"cmd\":\"chat\",\"phase\":\"helper\",\"said\":\"\xE7\x9C\x8B\xE7\x9C\x8B\","
                 "\"reply\":\"\",\"agent\":\"codex\",\"stage\":\"asking codex\","
                 "\"mood\":\"busy\"}",
                 &event) == BUDDY_EVENT_CHAT);
    assert(event.type == BUDDY_EVENT_CHAT);
    assert(event.chat.phase == BUDDY_CHAT_HELPER && event.chat.mood == BUDDY_MOOD_BUSY);
    assert(strcmp(event.chat.said, "\xE7\x9C\x8B\xE7\x9C\x8B") == 0);
    assert(strcmp(event.chat.agent, "codex") == 0);
    assert(strcmp(event.chat.stage, "asking codex") == 0);
    assert(event.reply[0] == '\0' && !event.reply_truncated);

    /* Only the phase is required; everything else reads as empty or neutral. */
    assert(parse("{\"cmd\":\"chat\",\"phase\":\"idle\"}", &event) == BUDDY_EVENT_CHAT);
    assert(event.chat.phase == BUDDY_CHAT_NONE && event.chat.mood == BUDDY_MOOD_IDLE);
    assert(event.chat.said[0] == '\0' && event.chat.agent[0] == '\0');
    assert(parse("{\"cmd\":\"chat\",\"phase\":\"thinking\"}", &event) == BUDDY_EVENT_CHAT);
    assert(event.chat.phase == BUDDY_CHAT_THINKING);
    assert(parse("{\"cmd\":\"chat\",\"phase\":\"failed\",\"reply\":\"no\",\"mood\":\"oops\"}",
                 &event) == BUDDY_EVENT_CHAT);
    assert(event.chat.phase == BUDDY_CHAT_FAILED && event.chat.mood == BUDDY_MOOD_OOPS);
    /* A mood this firmware does not know is not worth refusing the update for. */
    assert(parse("{\"cmd\":\"chat\",\"phase\":\"done\",\"reply\":\"ok\",\"mood\":\"smug\"}",
                 &event) == BUDDY_EVENT_CHAT);
    assert(event.chat.phase == BUDDY_CHAT_DONE && event.chat.mood == BUDDY_MOOD_IDLE);
    assert(strcmp(event.reply, "ok") == 0);

    /* A phase it does not know, a missing phase or a field of the wrong type is refused,
     * and the command name survives so the host can be told which message it was. */
    assert(parse("{\"cmd\":\"chat\",\"phase\":\"dancing\"}", &event) == BUDDY_EVENT_MALFORMED);
    assert(strcmp(event.command.name, "chat") == 0 && event.chat.phase == BUDDY_CHAT_NONE);
    assert(parse("{\"cmd\":\"chat\"}", &event) == BUDDY_EVENT_MALFORMED);
    assert(parse("{\"cmd\":\"chat\",\"phase\":\"done\",\"reply\":5}", &event) ==
           BUDDY_EVENT_MALFORMED);
    assert(parse("{\"cmd\":\"chat\",\"phase\":\"done\",\"agent\":[]}", &event) ==
           BUDDY_EVENT_MALFORMED);

    /* A long answer is cut on a character boundary and says so; the short fields
     * are cut the same way without a flag. */
    index = (size_t)snprintf(line, sizeof(line),
                             "{\"cmd\":\"chat\",\"phase\":\"done\",\"said\":\"");
    while (index < 300U) {
        memcpy(line + index, "\xE4\xBD\xA0", 3);
        index += 3;
    }
    index += (size_t)snprintf(line + index, sizeof(line) - index, "\",\"reply\":\"");
    while (index < 300U + 3U * (BUDDY_REPLY_MAX / 3U + 20U)) {
        memcpy(line + index, "\xE5\xA5\xBD", 3);
        index += 3;
    }
    (void)snprintf(line + index, sizeof(line) - index, "\"}");
    assert(parse(line, &event) == BUDDY_EVENT_CHAT);
    assert(event.reply_truncated);
    assert(strlen(event.reply) == (BUDDY_REPLY_MAX - 1U) / 3U * 3U);
    assert(strlen(event.chat.said) == (BUDDY_MESSAGE_MAX - 1U) / 3U * 3U);
    assert(event.chat.said[strlen(event.chat.said) - 1] == '\xA0');

    /* Which card the turn went onto, and how many things are in the background. */
    assert(parse("{\"cmd\":\"chat\",\"phase\":\"helper\",\"agent\":\"codex\",\"card\":\"c12\","
                 "\"doing\":3}",
                 &event) == BUDDY_EVENT_CHAT);
    assert(strcmp(event.chat.card, "c12") == 0 && event.chat.doing == 3U);
    assert(parse("{\"cmd\":\"chat\",\"phase\":\"idle\",\"doing\":1}", &event) == BUDDY_EVENT_CHAT);
    assert(event.chat.card[0] == '\0' && event.chat.doing == 1U);
    /* An id this device could not hold names no card here; the rest still counts. */
    assert(parse("{\"cmd\":\"chat\",\"phase\":\"done\",\"reply\":\"ok\","
                 "\"card\":\"a-card-id-that-is-too-long\"}",
                 &event) == BUDDY_EVENT_CHAT);
    assert(event.chat.card[0] == '\0' && strcmp(event.reply, "ok") == 0);
    assert(parse("{\"cmd\":\"chat\",\"phase\":\"done\",\"doing\":-1}", &event) ==
           BUDDY_EVENT_MALFORMED);
    assert(parse("{\"cmd\":\"chat\",\"phase\":\"done\",\"doing\":\"2\"}", &event) ==
           BUDDY_EVENT_MALFORMED);

    /* The phone cuts the reply to the limit itself (959 bytes of 3-byte characters
     * is 957). It fits, but there is no room left, so it still says it was cut. */
    {
        size_t start = (size_t)snprintf(line, sizeof(line),
                                        "{\"cmd\":\"chat\",\"phase\":\"done\",\"reply\":\"");
        size_t at;

        for (at = start; at < start + (BUDDY_REPLY_MAX - 1U) / 3U * 3U;) {
            memcpy(line + at, "\xE5\xA5\xBD", 3);
            at += 3;
        }
        (void)snprintf(line + at, sizeof(line) - at, "\"}");
        assert(parse(line, &event) == BUDDY_EVENT_CHAT);
        assert(strlen(event.reply) == (BUDDY_REPLY_MAX - 1U) / 3U * 3U);
        assert(event.reply_truncated);
        /* A long answer that leaves room for more is taken as complete. */
        for (at = start; at < start + 600U;) {
            memcpy(line + at, "\xE5\xA5\xBD", 3);
            at += 3;
        }
        (void)snprintf(line + at, sizeof(line) - at, "\"}");
        assert(parse(line, &event) == BUDDY_EVENT_CHAT);
        assert(strlen(event.reply) == 600U && !event.reply_truncated);
    }

    {
        char ack[96];

        assert(buddy_protocol_hub_ack_json(ack, sizeof(ack)) > 0);
        assert(strcmp(ack, "{\"ack\":\"hub\",\"ok\":true,\"chat\":true,\"cards\":true,\"threads\":true}\n") == 0);
    }
    assert(buddy_protocol_hub_ack_json(output, 8) == 0);
}

static void test_card_carries_one_thing(void)
{
    static char line[BUDDY_JSON_LINE_MAX + 1U];
    buddy_event_t event;
    size_t index;

    assert(parse("{\"cmd\":\"card\",\"id\":\"c12\",\"at\":\"14:02\",\"state\":\"working\","
                 "\"agent\":\"codex\",\"edits\":1,\"said\":\"\xE7\x9C\x8B\xE7\x9C\x8B\","
                 "\"reply\":\"handed to codex\"}",
                 &event) == BUDDY_EVENT_CARD);
    assert(event.type == BUDDY_EVENT_CARD && !event.card.clear);
    assert(strcmp(event.card.id, "c12") == 0 && strcmp(event.card.at, "14:02") == 0);
    assert(event.card.state == BUDDY_CARD_WORKING && event.card.edits == 1);
    assert(strcmp(event.card.agent, "codex") == 0);
    assert(strcmp(event.card_said, "\xE7\x9C\x8B\xE7\x9C\x8B") == 0);
    assert(strcmp(event.reply, "handed to codex") == 0 && !event.reply_truncated);

    /* Only the id and the state are required. Every state is known. */
    {
        static const struct {
            const char *name;
            buddy_card_state_t state;
        } states[] = {
            {"talking", BUDDY_CARD_TALKING}, {"working", BUDDY_CARD_WORKING},
            {"waiting", BUDDY_CARD_WAITING}, {"done", BUDDY_CARD_DONE},
            {"failed", BUDDY_CARD_FAILED},   {"cancelled", BUDDY_CARD_CANCELLED},
        };

        for (index = 0; index < sizeof(states) / sizeof(states[0]); ++index) {
            (void)snprintf(line, sizeof(line), "{\"cmd\":\"card\",\"id\":\"c1\",\"state\":\"%s\"}",
                           states[index].name);
            assert(parse(line, &event) == BUDDY_EVENT_CARD);
            assert(event.card.state == states[index].state);
            assert(event.card.agent[0] == '\0' && event.card_said[0] == '\0' &&
                   event.reply[0] == '\0' && event.card.edits == 0);
        }
    }
    /* A very large count of edits is still just "many". */
    assert(parse("{\"cmd\":\"card\",\"id\":\"c1\",\"state\":\"done\",\"edits\":900}", &event) ==
           BUDDY_EVENT_CARD);
    assert(event.card.edits == 255);

    assert(parse("{\"cmd\":\"card\",\"clear\":true}", &event) == BUDDY_EVENT_CARD);
    assert(event.card.clear && event.card.id[0] == '\0');

    /* Without an id, with one that is too long to be kept whole, with an unknown
     * state or a field of the wrong type, it is refused. */
    assert(parse("{\"cmd\":\"card\",\"state\":\"done\"}", &event) == BUDDY_EVENT_MALFORMED);
    assert(strcmp(event.command.name, "card") == 0 && event.card.id[0] == '\0');
    assert(parse("{\"cmd\":\"card\",\"clear\":false}", &event) == BUDDY_EVENT_MALFORMED);
    assert(parse("{\"cmd\":\"card\",\"id\":\"\",\"state\":\"done\"}", &event) ==
           BUDDY_EVENT_MALFORMED);
    assert(parse("{\"cmd\":\"card\",\"id\":\"a-card-id-that-is-too-long\",\"state\":\"done\"}",
                 &event) == BUDDY_EVENT_MALFORMED);
    assert(parse("{\"cmd\":\"card\",\"id\":\"c1\"}", &event) == BUDDY_EVENT_MALFORMED);
    assert(parse("{\"cmd\":\"card\",\"id\":\"c1\",\"state\":\"paused\"}", &event) ==
           BUDDY_EVENT_MALFORMED);
    assert(parse("{\"cmd\":\"card\",\"id\":\"c1\",\"state\":\"done\",\"reply\":5}", &event) ==
           BUDDY_EVENT_MALFORMED);
    assert(parse("{\"cmd\":\"card\",\"id\":\"c1\",\"state\":\"done\",\"edits\":\"1\"}", &event) ==
           BUDDY_EVENT_MALFORMED);

    /* Long words are cut on a character boundary; only the reply says so. */
    index = (size_t)snprintf(line, sizeof(line),
                             "{\"cmd\":\"card\",\"id\":\"c1\",\"state\":\"done\",\"said\":\"");
    while (index < 300U) {
        memcpy(line + index, "\xE4\xBD\xA0", 3);
        index += 3;
    }
    index += (size_t)snprintf(line + index, sizeof(line) - index, "\",\"reply\":\"");
    while (index < 300U + 3U * (BUDDY_REPLY_MAX / 3U + 20U)) {
        memcpy(line + index, "\xE5\xA5\xBD", 3);
        index += 3;
    }
    (void)snprintf(line + index, sizeof(line) - index, "\"}");
    assert(parse(line, &event) == BUDDY_EVENT_CARD);
    assert(event.reply_truncated);
    assert(strlen(event.reply) == (BUDDY_REPLY_MAX - 1U) / 3U * 3U);
    assert(strlen(event.card_said) == (BUDDY_MESSAGE_MAX - 1U) / 3U * 3U);
}

static void test_tasks_lists_the_things_in_progress(void)
{
    buddy_event_t event;

    assert(parse("{\"cmd\":\"tasks\",\"list\":["
                 "{\"id\":\"c12\",\"agent\":\"codex\",\"title\":\"review retry()\","
                 "\"state\":\"working\",\"secs\":42,\"p1\":\"git diff\",\"p2\":\"pytest -q\"},"
                 "{\"id\":\"c13\",\"agent\":\"claude\",\"state\":\"waiting\"},"
                 "{\"title\":\"no id\"},"
                 "{\"id\":\"c14\",\"state\":\"queued\"},"
                 "{\"id\":\"c15\"},{\"id\":\"c16\"}]}",
                 &event) == BUDDY_EVENT_TASKS);
    /* Entries without an id are skipped; the list holds the first four that have one. */
    assert(event.type == BUDDY_EVENT_TASKS && event.task_count == BUDDY_TASK_COUNT);
    assert(strcmp(event.tasks[0].id, "c12") == 0 && strcmp(event.tasks[0].agent, "codex") == 0);
    assert(strcmp(event.tasks[0].title, "review retry()") == 0);
    assert(event.tasks[0].state == BUDDY_TASK_WORKING && event.tasks[0].seconds == 42U);
    assert(strcmp(event.tasks[0].line1, "git diff") == 0 &&
           strcmp(event.tasks[0].line2, "pytest -q") == 0);
    assert(event.tasks[1].state == BUDDY_TASK_WAITING && event.tasks[1].title[0] == '\0');
    assert(strcmp(event.tasks[2].id, "c14") == 0 && event.tasks[2].state == BUDDY_TASK_QUEUED);
    /* A state it does not know counts as working: the thing is in the list. */
    assert(strcmp(event.tasks[3].id, "c15") == 0 && event.tasks[3].state == BUDDY_TASK_WORKING);

    /* Things that ended stay on the list, each with how it ended. */
    assert(parse("{\"cmd\":\"tasks\",\"list\":[{\"id\":\"c1\",\"state\":\"done\",\"p1\":\"ok\"},"
                 "{\"id\":\"c2\",\"state\":\"failed\"},{\"id\":\"c3\",\"state\":\"cancelled\"}]}",
                 &event) == BUDDY_EVENT_TASKS);
    assert(event.task_count == 3 && event.tasks[0].state == BUDDY_TASK_DONE &&
           strcmp(event.tasks[0].line1, "ok") == 0);
    assert(event.tasks[1].state == BUDDY_TASK_FAILED &&
           event.tasks[2].state == BUDDY_TASK_CANCELLED);

    assert(parse("{\"cmd\":\"tasks\",\"list\":[]}", &event) == BUDDY_EVENT_TASKS);
    assert(event.task_count == 0);
    assert(parse("{\"cmd\":\"tasks\"}", &event) == BUDDY_EVENT_MALFORMED);
    assert(strcmp(event.command.name, "tasks") == 0);
    assert(parse("{\"cmd\":\"tasks\",\"list\":\"c1\"}", &event) == BUDDY_EVENT_MALFORMED);
    assert(parse("{\"cmd\":\"tasks\",\"list\":[\"c1\"]}", &event) == BUDDY_EVENT_MALFORMED);
    assert(parse("{\"cmd\":\"tasks\",\"list\":[{\"id\":7}]}", &event) == BUDDY_EVENT_MALFORMED);
    assert(parse("{\"cmd\":\"tasks\",\"list\":[{\"id\":\"c1\",\"secs\":\"9\"}]}", &event) ==
           BUDDY_EVENT_MALFORMED);
    assert(parse("{\"cmd\":\"tasks\",\"list\":[{\"id\":\"a-card-id-that-is-too-long\"}]}",
                 &event) == BUDDY_EVENT_MALFORMED);
    assert(event.task_count == 0);
}

static void test_helpers_lists_who_xiaoyou_can_ask(void)
{
    buddy_event_t event;

    assert(parse("{\"cmd\":\"helpers\",\"list\":[{\"name\":\"claude\",\"about\":\"answers\"},"
                 "{\"name\":\"codex\"},{\"about\":\"nameless\"},{\"name\":\"pc\"},"
                 "{\"name\":\"four\"},{\"name\":\"five\"}]}",
                 &event) == BUDDY_EVENT_HELPERS);
    /* Entries without a name are skipped; the table holds the first four that have one. */
    assert(event.helper_count == BUDDY_HELPER_COUNT);
    assert(strcmp(event.helpers[0].name, "claude") == 0 &&
           strcmp(event.helpers[0].about, "answers") == 0);
    assert(strcmp(event.helpers[1].name, "codex") == 0 && event.helpers[1].about[0] == '\0');
    assert(strcmp(event.helpers[2].name, "pc") == 0);
    assert(strcmp(event.helpers[3].name, "four") == 0);

    assert(parse("{\"cmd\":\"helpers\",\"list\":[]}", &event) == BUDDY_EVENT_HELPERS);
    assert(event.helper_count == 0);
    assert(parse("{\"cmd\":\"helpers\"}", &event) == BUDDY_EVENT_MALFORMED);
    assert(strcmp(event.command.name, "helpers") == 0);
    assert(parse("{\"cmd\":\"helpers\",\"list\":\"claude\"}", &event) == BUDDY_EVENT_MALFORMED);
    assert(parse("{\"cmd\":\"helpers\",\"list\":[\"claude\"]}", &event) == BUDDY_EVENT_MALFORMED);
    assert(parse("{\"cmd\":\"helpers\",\"list\":[{\"name\":7}]}", &event) ==
           BUDDY_EVENT_MALFORMED);
    assert(event.helper_count == 0);
}

static void test_firmware_commands(void)
{
    static const char begin[] =
        "{\"cmd\":\"fw\",\"op\":\"begin\",\"size\":1694896,\"sha256\":"
        "\"e50a237c00112233445566778899aabbccddeeff00112233445566778899aa7e\"}";
    buddy_event_t event;

    assert(parse(begin, &event) == BUDDY_EVENT_FIRMWARE);
    assert(event.firmware.op == POCKET_UPDATE_OP_BEGIN);
    assert(event.firmware.size == 1694896U);
    assert(event.firmware.sha256[0] == 0xe5 && event.firmware.sha256[3] == 0x7c &&
           event.firmware.sha256[31] == 0x7e);

    assert(parse("{\"cmd\":\"fw\",\"op\":\"info\"}", &event) == BUDDY_EVENT_FIRMWARE);
    assert(event.firmware.op == POCKET_UPDATE_OP_INFO && event.firmware.size == 0U);
    assert(parse("{\"cmd\":\"fw\",\"op\":\"end\"}", &event) == BUDDY_EVENT_FIRMWARE);
    assert(event.firmware.op == POCKET_UPDATE_OP_END);
    assert(parse("{\"cmd\":\"fw\",\"op\":\"abort\"}", &event) == BUDDY_EVENT_FIRMWARE);
    assert(event.firmware.op == POCKET_UPDATE_OP_ABORT);
    assert(parse("{\"cmd\":\"fw\",\"op\":\"confirm\"}", &event) == BUDDY_EVENT_FIRMWARE);
    assert(event.firmware.op == POCKET_UPDATE_OP_CONFIRM);
    assert(parse("{\"cmd\":\"fw\",\"op\":\"rollback\"}", &event) == BUDDY_EVENT_FIRMWARE);
    assert(event.firmware.op == POCKET_UPDATE_OP_ROLLBACK);

    /* No operation, an unknown one, or a "begin" without a usable size and
     * hash: refused by name, so the host hears about it. */
    assert(parse("{\"cmd\":\"fw\"}", &event) == BUDDY_EVENT_MALFORMED);
    assert(strcmp(event.command.name, "fw") == 0);
    assert(event.firmware.op == POCKET_UPDATE_OP_NONE);
    assert(parse("{\"cmd\":\"fw\",\"op\":\"erase\"}", &event) == BUDDY_EVENT_MALFORMED);
    assert(parse("{\"cmd\":\"fw\",\"op\":7}", &event) == BUDDY_EVENT_MALFORMED);
    assert(parse("{\"cmd\":\"fw\",\"op\":\"begin\"}", &event) == BUDDY_EVENT_MALFORMED);
    assert(parse("{\"cmd\":\"fw\",\"op\":\"begin\",\"size\":1694896}", &event) ==
           BUDDY_EVENT_MALFORMED);
    assert(parse("{\"cmd\":\"fw\",\"op\":\"begin\",\"size\":0,\"sha256\":"
                 "\"e50a237c00112233445566778899aabbccddeeff00112233445566778899aa7e\"}",
                 &event) == BUDDY_EVENT_MALFORMED);
    assert(parse("{\"cmd\":\"fw\",\"op\":\"begin\",\"size\":4294967296,\"sha256\":"
                 "\"e50a237c00112233445566778899aabbccddeeff00112233445566778899aa7e\"}",
                 &event) == BUDDY_EVENT_MALFORMED);
    assert(parse("{\"cmd\":\"fw\",\"op\":\"begin\",\"size\":1.5,\"sha256\":"
                 "\"e50a237c00112233445566778899aabbccddeeff00112233445566778899aa7e\"}",
                 &event) == BUDDY_EVENT_MALFORMED);
    assert(parse("{\"cmd\":\"fw\",\"op\":\"begin\",\"size\":4096,\"sha256\":\"e50a\"}",
                 &event) == BUDDY_EVENT_MALFORMED);
    assert(event.firmware.size == 0U);
}

int main(void)
{
    test_official_heartbeat_maps_documented_fields();
    test_assistant_turn_keeps_text_blocks_only();
    test_other_turns_and_events_are_ignored_not_rejected();
    test_long_assistant_turn_is_cut_on_a_character_boundary();
    test_heartbeat_optional_prompt_stays_in_heartbeat_snapshot();
    test_unpair_maps_confirmation_event();
    test_hub_hello_reports_voice_support();
    test_chat_reports_the_conversation();
    test_helpers_lists_who_xiaoyou_can_ask();
    test_card_carries_one_thing();
    test_tasks_lists_the_things_in_progress();
    test_firmware_commands();
    test_file_transfer_commands_are_unsupported();
    test_unknown_command_is_rejected();
    test_malformed_or_nonobject_json_is_rejected();
    test_oversized_prompt_id_is_rejected();
    test_oversized_nested_prompt_id_is_rejected();
    test_prompt_id_must_be_a_nonempty_string();
    test_prompt_id_rejects_an_embedded_nul();
    test_parser_rejects_trailing_bytes();
    test_heartbeat_rejects_nonintegral_counters();
    test_display_strings_are_bounded();
    test_official_time_owner_and_name_payloads();
    test_status_is_a_no_payload_request();
    test_name_command_truncates_at_a_utf8_codepoint_boundary();
    test_parser_rejects_oversized_or_invalid_utf8_lines();
    test_parser_rejects_raw_nul_and_control_bytes();
    test_parser_rejects_excessive_json_nesting();
    test_preflight_failure_clears_a_reused_event();
    test_serializers_write_documented_json();
    test_serializers_fail_without_writing_past_the_output_bound();
    test_serializers_reject_invalid_or_unterminated_inputs();
    test_command_ack_names_the_request_and_error();
    test_device_status_omits_unavailable_battery_fields();
    test_task_tx_capacity_handles_worst_case_escaping();
    return 0;
}
