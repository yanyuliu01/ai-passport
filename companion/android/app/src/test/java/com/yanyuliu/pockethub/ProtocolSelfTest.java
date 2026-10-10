package com.yanyuliu.pockethub;

import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.List;

/** BuddyProtocol 的自检。单元测试和电脑上的命令行都调用 run()。 */
public final class ProtocolSelfTest {
    private ProtocolSelfTest() {
    }

    private static void check(boolean condition, String what) {
        if (!condition) {
            throw new AssertionError(what);
        }
    }

    public static void run() {
        String beat = BuddyProtocol.heartbeat(2, 0, 1, "Claude：任务完成",
                Arrays.asList("10:42 Claude 任务完成", "10:40 Codex \"done\""), 0, 0, null);
        check(beat.equals("{\"total\":2,\"running\":0,\"waiting\":1,\"msg\":\"Claude：任务完成\","
                + "\"entries\":[\"10:42 Claude 任务完成\",\"10:40 Codex \\\"done\\\"\"],"
                + "\"tokens\":0,\"tokens_today\":0}\n"), "heartbeat shape: " + beat);

        // 固件要求的五个数字字段、msg、entries 必须始终存在。
        String empty = BuddyProtocol.heartbeat(0, 0, 0, null, null, 0, 0, null);
        check(empty.equals("{\"total\":0,\"running\":0,\"waiting\":0,\"msg\":\"\",\"entries\":[],"
                + "\"tokens\":0,\"tokens_today\":0}\n"), "empty heartbeat: " + empty);

        String asked = BuddyProtocol.heartbeat(1, 0, 1, "m", null, 0, 0,
                new BuddyProtocol.Prompt("n-1", "Claude", "run\nls"));
        check(asked.contains("\"prompt\":{\"id\":\"n-1\",\"tool\":\"Claude\",\"hint\":\"run\\nls\"}"),
                "prompt: " + asked);

        // 过长的请求编号会被固件拒收整条心跳，所以干脆不带 prompt。
        StringBuilder longId = new StringBuilder();
        for (int index = 0; index < 96; index++) {
            longId.append('x');
        }
        check(!BuddyProtocol.heartbeat(1, 0, 1, "m", null, 0, 0,
                        new BuddyProtocol.Prompt(longId.toString(), "t", "h")).contains("prompt"),
                "oversized prompt id must be dropped");

        // 截断不切断汉字（每个 3 字节）。
        check(BuddyProtocol.clip("中文字", 7).equals("中文"), "clip on char boundary");
        check(BuddyProtocol.clip("a中", 3).equals("a"), "clip mixed");
        check(BuddyProtocol.clip(null, 5).isEmpty(), "clip null");
        check(BuddyProtocol.clip("ok\uD83D\uDE00!", 6).equals("ok\uD83D\uDE00"), "clip emoji");
        check(BuddyProtocol.clip("a\uD800b", 9).equals("ab"), "lone surrogate dropped");
        StringBuilder many = new StringBuilder();
        for (int index = 0; index < 200; index++) {
            many.append('中');
        }
        String clipped = BuddyProtocol.heartbeat(1, 0, 0, many.toString(),
                Arrays.asList(many.toString(), "b", "c", "d", "e"), 0, 0, null);
        check(BuddyProtocol.utf8Length(clipped) < BuddyProtocol.LINE_MAX, "line fits");
        check(clipped.indexOf("\"e\"") < 0, "at most four entries");

        String turn = BuddyProtocol.turn("第一行\n\"第二行\"\u0001");
        check(turn.equals("{\"evt\":\"turn\",\"role\":\"assistant\",\"content\":[{\"type\":\"text\","
                + "\"text\":\"第一行\\n\\\"第二行\\\"\\u0001\"}]}\n"), "turn: " + turn);
        check(BuddyProtocol.time(1790000000L, 28800).equals("{\"time\":[1790000000,28800]}\n"),
                "time");

        BuddyProtocol.Decision yes = BuddyProtocol.parseDecision(
                "{\"cmd\":\"permission\",\"id\":\"n-1\",\"decision\":\"once\"}");
        check(yes != null && yes.allow && yes.id.equals("n-1"), "decision once");
        BuddyProtocol.Decision no = BuddyProtocol.parseDecision(
                " { \"decision\" : \"deny\" , \"id\":\"a\\\"b\", \"cmd\":\"permission\" } ");
        check(no != null && !no.allow && no.id.equals("a\"b"), "decision deny");
        check(BuddyProtocol.parseDecision("{\"ack\":\"status\",\"ok\":true,\"data\":{\"name\":\"x\"}}")
                == null, "ack is not a decision");
        check(BuddyProtocol.parseDecision("{\"cmd\":\"permission\",\"id\":\"n\",\"decision\":\"maybe\"}")
                == null, "unknown decision");
        check(BuddyProtocol.parseDecision("{\"cmd\":\"permission\",\"id\":\"\",\"decision\":\"once\"}")
                == null, "empty id");
        check(BuddyProtocol.parseDecision("not json") == null, "garbage");
        check(BuddyProtocol.parseDecision("{\"cmd\":\"permission\",\"id\":\"n\"") == null, "cut off");
        check(BuddyProtocol.parseDecision(null) == null, "null");

        List<byte[]> parts = BuddyProtocol.chunk("abcdefg\n", 3);
        check(parts.size() == 3 && parts.get(0).length == 3 && parts.get(2).length == 2, "chunk");

        BuddyProtocol.LineAssembler assembler = new BuddyProtocol.LineAssembler();
        check(assembler.feed("{\"a\":".getBytes(StandardCharsets.UTF_8)).isEmpty(), "partial line");
        List<String> lines = assembler.feed("1}\n{\"b\":2}\n{\"c".getBytes(StandardCharsets.UTF_8));
        check(lines.size() == 2 && lines.get(0).equals("{\"a\":1}") && lines.get(1).equals("{\"b\":2}"),
                "two lines");
        // 汉字被拆在两包里也能拼回来。
        byte[] han = "中\n".getBytes(StandardCharsets.UTF_8);
        assembler.reset();
        check(assembler.feed(Arrays.copyOfRange(han, 0, 2)).isEmpty(), "split char");
        check(assembler.feed(Arrays.copyOfRange(han, 2, 4)).get(0).equals("中"), "joined char");
        // 超长行整行丢弃，下一行不受影响。
        assembler.reset();
        byte[] huge = new byte[BuddyProtocol.LINE_MAX + 10];
        Arrays.fill(huge, (byte) 'x');
        check(assembler.feed(huge).isEmpty(), "oversized pending");
        List<String> after = assembler.feed("\nok\n".getBytes(StandardCharsets.UTF_8));
        check(after.size() == 1 && after.get(0).equals("ok"), "recover after oversized");

        voice();
        history();
        conversation();
    }

    /** 和小幽的对话、她的帮手：发给设备的两行，以及读 Runtime 回来的记录。 */
    private static void conversation() {
        String chat = BuddyProtocol.chat("helper", "看看 \"retry\"", "", "codex", "我让 codex 看看", "busy");
        check(chat.equals("{\"cmd\":\"chat\",\"phase\":\"helper\",\"said\":\"看看 \\\"retry\\\"\","
                + "\"reply\":\"\",\"agent\":\"codex\",\"stage\":\"我让 codex 看看\",\"mood\":\"busy\"}\n"),
                "chat shape: " + chat);
        check(BuddyProtocol.chat(null, null, null, null, null, null).equals(
                "{\"cmd\":\"chat\",\"phase\":\"idle\",\"said\":\"\",\"reply\":\"\",\"agent\":\"\","
                        + "\"stage\":\"\",\"mood\":\"idle\"}\n"), "empty chat");
        // 每个字段都按固件的上限截断，整行放得进一行。
        StringBuilder many = new StringBuilder();
        for (int index = 0; index < 2000; index++) {
            many.append('长');
        }
        String longChat = BuddyProtocol.chat("done", many.toString(), many.toString(),
                many.toString(), many.toString(), "happy");
        check(BuddyProtocol.utf8Length(longChat) < BuddyProtocol.LINE_MAX, "chat line fits");
        check(BuddyProtocol.utf8Length(longChat) > BuddyProtocol.REPLY_MAX, "reply is kept long");

        // 小屏幕上先看结论：简报在前，完整回复在后；重复的不写两遍。
        check(BuddyProtocol.chatReply("结论", "完整的说明").equals("结论\n\n完整的说明"), "brief first");
        check(BuddyProtocol.chatReply("结论", "结论，以及更多").equals("结论，以及更多"), "no repeat");
        check(BuddyProtocol.chatReply("", "只有回复").equals("只有回复"), "reply only");
        check(BuddyProtocol.chatReply("只有简报", null).equals("只有简报"), "brief only");
        check(BuddyProtocol.chatReply(null, null).isEmpty(), "nothing");

        String helpers = BuddyProtocol.helpers(Arrays.asList(
                new String[] {"claude", "日常问答"}, new String[] {"", "没有名字"}, null,
                new String[] {"codex"}, new String[] {"c"}, new String[] {"d"}, new String[] {"e"}));
        check(helpers.equals("{\"cmd\":\"helpers\",\"list\":[{\"name\":\"claude\",\"about\":\"日常问答\"},"
                + "{\"name\":\"codex\",\"about\":\"\"},{\"name\":\"c\",\"about\":\"\"},"
                + "{\"name\":\"d\",\"about\":\"\"}]}\n"), "helpers: " + helpers);
        check(BuddyProtocol.helpers(null).equals("{\"cmd\":\"helpers\",\"list\":[]}\n"), "no helpers");

        // 设备的应答里声明了 chat，才按新的方式发对话。
        check(BuddyProtocol.hubAckHasChat("{\"ack\":\"hub\",\"ok\":true,\"chat\":true}"), "chat ack");
        check(Boolean.TRUE.equals(BuddyProtocol.parseHubAck("{\"ack\":\"hub\",\"ok\":true,\"chat\":true}")),
                "chat ack is still a hub ack");
        check(!BuddyProtocol.hubAckHasChat("{\"ack\":\"hub\",\"ok\":true}"), "older firmware");
        check(!BuddyProtocol.hubAckHasChat("{\"ack\":\"hub\",\"ok\":false,\"chat\":true}"), "refused");
        check(!BuddyProtocol.hubAckHasChat("{\"ack\":\"name\",\"ok\":true,\"chat\":true}"), "other ack");
        check(!BuddyProtocol.hubAckHasChat("garbage"), "garbage ack");

        // Runtime 的消息记录：数字和 true / false 也读得到，null 读不到。
        java.util.Map<String, String> record = BuddyProtocol.parseFlatObject(
                "{\"id\":\"m1\",\"status\":\"running\",\"reply\":null,\"helper\":\"codex\","
                        + "\"stage\":\"我去问 codex\",\"events\":[{\"kind\":\"route\",\"text\":\"a,b]}\"}],"
                        + "\"hop\":0,\"rev\":12,\"created_at\":1790000000.25,\"ok\":true}");
        check(record != null && "12".equals(record.get("rev")) && "0".equals(record.get("hop")), "numbers");
        check("codex".equals(record.get("helper")) && "true".equals(record.get("ok")), "helper and bool");
        check(!record.containsKey("reply") && !record.containsKey("events"), "null and arrays skipped");
        check("1790000000.25".equals(record.get("created_at")), "float kept as written");

        // /v1/agents：一层对象里的一个对象数组。
        java.util.List<java.util.Map<String, String>> agents = BuddyProtocol.parseObjectArray(
                "{\"default\":\"claude\",\"agents\":[{\"name\":\"claude\",\"type\":\"claude_code\","
                        + "\"description\":\"问答 [和] {搜索}\",\"speaks\":true,\"default\":true},"
                        + " {\"name\":\"codex\",\"description\":\"\",\"speaks\":false}],\"more\":1}", "agents");
        check(agents.size() == 2 && "claude".equals(agents.get(0).get("name"))
                && "问答 [和] {搜索}".equals(agents.get(0).get("description"))
                && "true".equals(agents.get(0).get("speaks")), "first agent");
        check("codex".equals(agents.get(1).get("name")) && "false".equals(agents.get(1).get("speaks")),
                "second agent");
        check(BuddyProtocol.parseObjectArray("{\"agents\":[]}", "agents").isEmpty(), "empty list");
        check(BuddyProtocol.parseObjectArray("{\"default\":\"x\"}", "agents").isEmpty(), "no key");
        check(BuddyProtocol.parseObjectArray("{\"agents\":\"claude\"}", "agents").isEmpty(), "not a list");
        check(BuddyProtocol.parseObjectArray("{\"agents\":[{\"name\":\"a\"}", "agents").size() <= 1, "cut off");
        check(BuddyProtocol.parseObjectArray("not json", "agents").isEmpty(), "garbage");
        check(BuddyProtocol.parseObjectArray(null, "agents").isEmpty(), "null");
    }

    /** 换 Runtime 时带过去的对话：只带最近的若干轮，旧的在前，内容原样转义。 */
    private static void history() {
        java.util.List<ChatTurn> turns = new java.util.ArrayList<>();
        check(ChatTurn.historyJson(turns).equals("{\"turns\":[]}"), "empty history");
        turns.add(new ChatTurn("m1", "说\"你好\"", "好\n的", 100));
        check(ChatTurn.historyJson(turns).equals(
                "{\"turns\":[{\"id\":\"m1\",\"text\":\"说\\\"你好\\\"\",\"reply\":\"好\\n的\",\"at\":100}]}"),
                "one turn: " + ChatTurn.historyJson(turns));
        for (int index = 2; index <= 20; index++) {
            turns.add(new ChatTurn("m" + index, "问" + index, "答" + index, 100 + index));
        }
        String body = ChatTurn.historyJson(turns);
        check(!body.contains("\"m8\"") && body.contains("\"m9\"") && body.contains("\"m20\""),
                "only the most recent turns");
        check(body.indexOf("\"m9\"") < body.indexOf("\"m20\""), "oldest first");
        StringBuilder huge = new StringBuilder();
        for (int index = 0; index < ChatTurn.TEXT_LIMIT + 50; index++) {
            huge.append('长');
        }
        turns.clear();
        turns.add(new ChatTurn("big", huge.toString(), "ok", 1));
        check(ChatTurn.historyJson(turns).length() < ChatTurn.TEXT_LIMIT + 80, "long text is cut");
    }

    private static byte[] hex(String text) {
        byte[] bytes = new byte[text.length() / 2];
        for (int index = 0; index < bytes.length; index++) {
            bytes[index] = (byte) Integer.parseInt(text.substring(2 * index, 2 * index + 2), 16);
        }
        return bytes;
    }

    private static int sampleAt(byte[] wav, int index) {
        int offset = 44 + 2 * index;
        return (short) ((wav[offset] & 0xFF) | (wav[offset + 1] << 8));
    }

    /** 语音：控制行、帧的识别，以及和固件编码器逐个采样对得上的解码。 */
    private static void voice() {
        check(BuddyProtocol.hubHello().equals("{\"cmd\":\"hub\",\"voice\":true}\n"), "hub hello");
        check("start".equals(BuddyProtocol.parseVoiceState(
                "{\"cmd\":\"voice\",\"state\":\"start\",\"rate\":16000,\"codec\":\"ima-adpcm\"}")),
                "voice start");
        check("end".equals(BuddyProtocol.parseVoiceState(
                "{\"cmd\":\"voice\",\"state\":\"end\",\"frames\":34,\"dropped\":0,\"ms\":1000}")),
                "voice end");
        check("cancel".equals(BuddyProtocol.parseVoiceState("{\"cmd\":\"voice\",\"state\":\"cancel\"}")),
                "voice cancel");
        check(BuddyProtocol.parseVoiceState("{\"cmd\":\"voice\",\"state\":\"dance\"}") == null,
                "unknown voice state");
        check(BuddyProtocol.parseVoiceState("{\"cmd\":\"permission\",\"state\":\"start\"}") == null,
                "not a voice line");
        check(Boolean.TRUE.equals(BuddyProtocol.parseHubAck("{\"ack\":\"hub\",\"ok\":true}")), "hub ack");
        check(Boolean.FALSE.equals(BuddyProtocol.parseHubAck(
                "{\"ack\":\"hub\",\"ok\":false,\"error\":\"unknown command\"}")), "old firmware ack");
        check(BuddyProtocol.parseHubAck("{\"ack\":\"owner\",\"ok\":true}") == null, "other ack");

        check(VoiceRecording.isFrame(new byte[] {(byte) 0xFF, 0}), "frame marker");
        check(!VoiceRecording.isFrame("{\"cmd\"".getBytes(StandardCharsets.UTF_8)), "text is not a frame");
        check(!VoiceRecording.isFrame(new byte[0]) && !VoiceRecording.isFrame(null), "empty");

        // 这两帧和期望的采样值由固件的编码器（main/pocket_voice_core.c）在电脑上生成。
        byte[] first = hex("ff00000000fff7777f2fc5c2222c2cc2b2321d3b");
        byte[] second = hex("ff0120054cc2c2222c1b");
        int[] expected = {
            -11, -41, 22, -114, 179, 810, -547, 2363, -3873, 584, 9499, -1180, 5998, -5749, 2147,
            9325, -2422, 5474, -7448, 1238, 9134, -3788, 4898, -6156, 1022, 10158, -2894, 2317,
            -8737, 1312, 7838, -2841, 4337, -7410, 486, 7664, -4083, 3813, -6236, -2321,
        };
        VoiceRecording recording = new VoiceRecording();
        check(recording.add(first) && recording.add(second), "frames accepted");
        check(!recording.add(new byte[] {(byte) 0xFF, 2, 0, 0, 0}), "header-only frame refused");
        check(!recording.add("text".getBytes(StandardCharsets.UTF_8)), "text refused");
        byte[] wav = recording.toWav();
        check(wav.length == 44 + 2 * expected.length, "wav size " + wav.length);
        check(new String(wav, 0, 4, StandardCharsets.US_ASCII).equals("RIFF")
                && new String(wav, 8, 8, StandardCharsets.US_ASCII).equals("WAVEfmt "), "wav header");
        check((wav[24] & 0xFF) == 0x80 && (wav[25] & 0xFF) == 0x3E && wav[22] == 1 && wav[34] == 16,
                "16 kHz mono 16-bit");
        for (int index = 0; index < expected.length; index++) {
            check(sampleAt(wav, index) == expected[index], "sample " + index);
        }
        check(recording.frames() == 2 && recording.lostFrames() == 0, "frame count");

        // 丢一帧：缺的那段补静音，后面的帧照常解码（每帧自带解码起点）。
        byte[] third = second.clone();
        third[1] = 3;
        VoiceRecording gap = new VoiceRecording();
        gap.add(first);
        gap.add(second);
        gap.add(third);
        check(gap.lostFrames() == 1, "one lost frame");
        byte[] gapped = gap.toWav();
        check(gapped.length == 44 + 2 * (30 + 10 + 10 + 10), "silence fills the gap");
        check(sampleAt(gapped, 45) == 0 && sampleAt(gapped, 50) == 7838
                && sampleAt(gapped, 59) == -2321, "decoding resumes after the gap");
        // 序号从 255 回到 0 不算丢帧。
        VoiceRecording wrap = new VoiceRecording();
        byte[] last = second.clone();
        last[1] = (byte) 255;
        byte[] zero = second.clone();
        zero[1] = 0;
        wrap.add(last);
        wrap.add(zero);
        check(wrap.lostFrames() == 0, "sequence wraps");
        runCards();
    }

    /** 卡、任务单子和 Runtime 的 feed。 */
    static void runCards() {
        String chat = BuddyProtocol.chat("helper", "查一下", "", "codex", "查日志", "busy", "c12", 2);
        check(chat.endsWith("\"mood\":\"busy\",\"card\":\"c12\",\"doing\":2}\n"), "chat card: " + chat);
        // 不带卡的那种写法和以前一字不差：上一版固件照样认。
        check(BuddyProtocol.chat("idle", "", "", "", "", "idle")
                .equals("{\"cmd\":\"chat\",\"phase\":\"idle\",\"said\":\"\",\"reply\":\"\","
                        + "\"agent\":\"\",\"stage\":\"\",\"mood\":\"idle\"}\n"), "plain chat");
        check(BuddyProtocol.chat("idle", "", "", "", "", "idle", "", 0).endsWith("\"doing\":0}\n"),
                "empty card id is left out");

        String card = BuddyProtocol.card("c12", "14:02", "working", "codex", 1, "查一下\n日志", "好");
        check(card.equals("{\"cmd\":\"card\",\"id\":\"c12\",\"at\":\"14:02\",\"state\":\"working\","
                + "\"agent\":\"codex\",\"edits\":1,\"said\":\"查一下\\n日志\",\"reply\":\"好\"}\n"),
                "card: " + card);
        check(BuddyProtocol.card("c12345678901", "", "done", "", 0, "", "") == null,
                "a card id longer than the firmware keeps is not sent");
        check(BuddyProtocol.card("", "", "done", "", 0, "", "") == null, "no id, no card");
        StringBuilder longReply = new StringBuilder();
        for (int index = 0; index < 500; index++) {
            longReply.append('字');
        }
        String clipped = BuddyProtocol.card("c1", "09:00", "done", "", 0, "x", longReply.toString());
        check(BuddyProtocol.utf8Length(clipped) < 1100 && clipped.endsWith("字\"}\n"),
                "reply is cut on a character boundary");
        check(BuddyProtocol.cardClear().equals("{\"cmd\":\"card\",\"clear\":true}\n"), "clear");

        List<BuddyProtocol.Task> tasks = new java.util.ArrayList<>();
        for (int index = 1; index <= 6; index++) {
            tasks.add(new BuddyProtocol.Task("c" + index, "codex", "事 " + index,
                    index == 2 ? "waiting" : "working", index * 10, "Bash ls", ""));
        }
        tasks.add(1, new BuddyProtocol.Task("c-too-long-id-x", "a", "t", "working", 0, "", ""));
        String list = BuddyProtocol.tasks(tasks);
        check(list.startsWith("{\"cmd\":\"tasks\",\"list\":[{\"id\":\"c1\",\"agent\":\"codex\","
                + "\"title\":\"事 1\",\"state\":\"working\",\"secs\":10,\"p1\":\"Bash ls\",\"p2\":\"\"},"
                + "{\"id\":\"c2\""), "tasks: " + list);
        check(list.contains("\"c4\"") && !list.contains("\"c5\"") && !list.contains("too-long"),
                "four tasks at most, bad ids skipped");
        check(BuddyProtocol.tasks(null).equals("{\"cmd\":\"tasks\",\"list\":[]}\n"), "no tasks");

        check("c12".equals(BuddyProtocol.parseVoiceCard(
                "{\"cmd\":\"voice\",\"state\":\"start\",\"rate\":16000,\"codec\":\"ima-adpcm\","
                        + "\"card\":\"c12\"}")), "voice card");
        check(BuddyProtocol.parseVoiceCard(
                "{\"cmd\":\"voice\",\"state\":\"start\",\"rate\":16000}") == null, "no voice card");
        check(BuddyProtocol.parseVoiceCard(
                "{\"cmd\":\"voice\",\"state\":\"end\",\"card\":\"c12\"}") == null, "only on start");
        check(BuddyProtocol.hubAckHasCards("{\"ack\":\"hub\",\"ok\":true,\"chat\":true,\"cards\":true}"),
                "cards ack");
        check(!BuddyProtocol.hubAckHasCards("{\"ack\":\"hub\",\"ok\":true,\"chat\":true}"),
                "older firmware has no cards");

        // Runtime 的 /v1/feed：嵌套的对象、数组、null、转义、小数。
        String feed = "{\"seq\": 41, \"cards\": [{\"id\": \"c7\", \"conversation\": \"default\","
                + " \"title\": \"查日志\", \"state\": \"waiting\", \"agent\": \"codex\","
                + " \"created_at\": 1760000000.25, \"updated_at\": 1760000050.5,"
                + " \"entries\": [{\"role\": \"you\", \"text\": \"查一下\\\"日志\\\"\", \"at\": 1.0},"
                + " {\"role\": \"xiaoyou\", \"text\": \"好，交给 codex\", \"at\": 2.0},"
                + " {\"role\": \"you\", \"text\": \"只看今天的\", \"at\": 3.0},"
                + " {\"role\": \"other\", \"text\": \"x\"}],"
                + " \"brief\": \"交给 codex 了\", \"mood\": \"busy\","
                + " \"progress\": [\"Read a.log\", \"Bash grep -c \\u9519 a.log\"],"
                + " \"started_at\": 1760000001.0, \"edits\": 1, \"approval\": \"a3\","
                + " \"queued\": false, \"seq\": 41}, {\"title\": \"no id\"}],"
                + " \"approvals\": [{\"id\": \"a3\", \"card\": \"c7\", \"conversation\": \"default\","
                + " \"agent\": \"codex\", \"tool\": \"codex · Bash\", \"detail\": \"rm -rf build\","
                + " \"created_at\": 1760000049.9}]}";
        java.util.Map<String, Object> root = Json.parseObject(feed);
        check(root != null && Json.number(root, "seq") == 41, "feed parses");
        List<Object> rawCards = Json.list(root, "cards");
        check(rawCards.size() == 2 && Card.from(Json.object(rawCards.get(1))) == null,
                "a card without an id is dropped");
        Card parsed = Card.from(Json.object(rawCards.get(0)));
        check(parsed.id.equals("c7") && parsed.order() == 7 && parsed.seq == 41, "card id and order");
        check(parsed.active() && parsed.state.equals("waiting") && parsed.agent.equals("codex")
                && parsed.edits == 1 && !parsed.queued, "card state");
        check(parsed.entries.size() == 3 && parsed.said().equals("查一下\"日志\"")
                && parsed.lastSay().equals("好，交给 codex"), "first sentence and latest words");
        check(parsed.progress.size() == 2 && parsed.progress.get(1).equals("Bash grep -c 错 a.log"),
                "progress keeps commands verbatim");
        check(parsed.createdAt == 1760000000.25 && parsed.startedAt == 1760000001.0, "times");
        check(parsed.stateLabel().equals("等你点头"), "state label");
        Card.Approval approval = Card.Approval.from(Json.object(Json.list(root, "approvals").get(0)));
        check(approval.id.equals("a3") && approval.card.equals("c7")
                && approval.tool.equals("codex · Bash") && approval.detail.equals("rm -rf build")
                && approval.createdAt == 1760000049L, "approval");
        // 小幽自己答的：agent 是 null，没有进展。
        Card plain = Card.from(Json.parseObject("{\"id\":\"c8\",\"state\":\"done\",\"agent\":null,"
                + "\"title\":\"几点了\",\"entries\":[],\"started_at\":null}"));
        check(plain.agent.isEmpty() && !plain.active() && plain.said().equals("几点了")
                && plain.lastSay().isEmpty() && plain.startedAt == 0, "plain card");

        // 对话和任务分开：交给过帮手的是任务，小幽自己答的是对话。
        check(CardViews.isTask(parsed) && !CardViews.isTask(plain), "task or talk");
        Card finished = Card.from(Json.parseObject("{\"id\":\"c9\",\"state\":\"done\","
                + "\"agent\":\"claude\",\"title\":\"写周报\",\"brief\":\"写好了\\n在桌面\","
                + "\"entries\":[{\"role\":\"you\",\"text\":\"写周报\"},"
                + "{\"role\":\"xiaoyou\",\"text\":\"好了\"}]}"));
        Card older = Card.from(Json.parseObject("{\"id\":\"c2\",\"state\":\"failed\","
                + "\"agent\":\"codex\",\"title\":\"旧的\"}"));
        List<Card> all = Arrays.asList(older, parsed, plain, finished);
        List<Card> split = CardViews.tasks(all, 10);
        check(split.size() == 3 && split.get(0) == parsed && split.get(1) == finished
                && split.get(2) == older, "tasks: going first, then newest first");
        check(CardViews.tasks(all, 2).size() == 2 && CardViews.doing(all) == 1, "limit and count");
        check(CardViews.find(all, "c9") == finished && CardViews.find(all, "c1") == null
                && CardViews.find(all, null) == null, "find");
        check(CardViews.status(parsed).equals("c7 · codex · 等你点头（改过 1 次）")
                && CardViews.status(plain).equals("c8 · 好了"), "status line");
        check(CardViews.pointer(finished).equals("↪ 任务 c9 · claude · 好了\n写周报"), "pointer");
        check(CardViews.listItem(parsed).equals(
                "c7 · codex · 等你点头（改过 1 次）\n查日志\n› Bash grep -c 错 a.log"),
                "a going task shows its latest step: " + CardViews.listItem(parsed));
        check(CardViews.listItem(finished).equals("c9 · claude · 好了\n写周报\n写好了 在桌面")
                && CardViews.listItem(older).equals("c2 · codex · 没成\n旧的"), "list item");
        check(CardViews.thread(finished).equals("我：写周报\n\n小幽：好了")
                && CardViews.thread(plain).isEmpty(), "thread");
        check(CardViews.oneLine("一二三四五", 3).equals("一二三…")
                && CardViews.oneLine("ab\uD83D\uDE00c", 3).equals("ab…"), "preview is cut whole");
        check(CardViews.messageJson("改成\"蓝\"的", "k1", "c9").equals(
                "{\"text\":\"改成\\\"蓝\\\"的\",\"client_id\":\"k1\",\"card\":\"c9\",\"pin\":true}")
                && CardViews.messageJson("你好", "k2", null).equals(
                "{\"text\":\"你好\",\"client_id\":\"k2\"}"), "said inside a task: pinned to it");

        // 设备：第三屏的单子是在做的任务，分开显示的固件上后面接着最近做完的。
        List<Card> onDevice = CardViews.deviceTasks(all, 4, true);
        check(onDevice.size() == 3 && onDevice.get(0) == parsed && onDevice.get(1) == finished
                && onDevice.get(2) == older, "device list: going, then the latest that ended");
        check(CardViews.deviceTasks(all, 4, false).size() == 1
                && CardViews.deviceTasks(all, 2, true).size() == 2, "older firmware: going only");
        BuddyProtocol.Task going = CardViews.deviceTask(parsed, 1760000061L);
        check(going.state.equals("waiting") && going.seconds == 60 && going.p1.equals("Read a.log")
                && going.p2.equals("Bash grep -c 错 a.log"), "a going task keeps its steps");
        BuddyProtocol.Task ended = CardViews.deviceTask(finished, 1760000061L);
        check(ended.state.equals("done") && ended.seconds == 0 && ended.p1.equals("写好了 在桌面")
                && ended.p2.isEmpty() && CardViews.deviceTask(older, 0).state.equals("failed"),
                "an ended task says how it ended");
        // 任务在设备上有自己的页面：第一句之后的来回，旧的在前，做完的最后一句是简报。
        check(CardViews.deviceReply(finished, true).equals("小幽：写好了 在桌面")
                && CardViews.deviceReply(finished, false).equals("写好了\n在桌面\n\n好了")
                && CardViews.deviceReply(plain, true).isEmpty(), "a task sends its exchange");
        check(CardViews.deviceThread(parsed).equals("小幽：好，交给 codex\n\n你：只看今天的"),
                "thread of a going task: " + CardViews.deviceThread(parsed));
        StringBuilder many = new StringBuilder("{\"id\":\"c3\",\"state\":\"working\",\"agent\":\"codex\","
                + "\"entries\":[{\"role\":\"you\",\"text\":\"开头\"}");
        for (int index = 0; index < 20; index++) {
            many.append(",{\"role\":\"xiaoyou\",\"text\":\"第").append(index)
                    .append("句回答回答回答回答回答回答回答回答回答回答回答回答回答回答回答\"}");
        }
        String tail = CardViews.deviceThread(Card.from(Json.parseObject(many + "]}")));
        check(tail.getBytes(StandardCharsets.UTF_8).length <= CardViews.THREAD_BYTES
                && tail.startsWith("小幽：第") && tail.contains("第19句") && !tail.contains("第0句"),
                "a long thread keeps its newest end");
        String pinned = "{\"cmd\":\"voice\",\"state\":\"start\",\"rate\":16000,\"card\":\"c12\",\"pin\":true}";
        check(BuddyProtocol.parseVoicePin(pinned) && "c12".equals(BuddyProtocol.parseVoiceCard(pinned))
                && !BuddyProtocol.parseVoicePin(
                        "{\"cmd\":\"voice\",\"state\":\"start\",\"card\":\"c12\"}")
                && !BuddyProtocol.parseVoicePin("{\"cmd\":\"voice\",\"state\":\"start\",\"pin\":true}"),
                "voice said inside a task");
        check(BuddyProtocol.hubAckHasThreads(
                "{\"ack\":\"hub\",\"ok\":true,\"chat\":true,\"cards\":true,\"threads\":true}")
                && !BuddyProtocol.hubAckHasThreads(
                        "{\"ack\":\"hub\",\"ok\":true,\"chat\":true,\"cards\":true}"), "threads ack");

        check(Json.parseObject("[1]") == null && Json.parseObject("{\"a\":") == null
                && Json.parseObject("{\"a\":1} x") == null && Json.parseObject("") == null
                && Json.parseObject(null) == null && Json.parseObject("{\"a\":tru}") == null,
                "malformed JSON gives null");
        StringBuilder deep = new StringBuilder("{\"a\":");
        for (int index = 0; index < 5000; index++) {
            deep.append('[');
        }
        check(Json.parseObject(deep.toString()) == null, "deep nesting is refused, not a crash");
        check(Json.parseObject("{}").isEmpty(), "empty object");
        historyAndUsage();
    }

    private static Card card(String json) {
        return Card.from(Json.parseObject(json));
    }

    /** 做完的任务另列一张单子、用量和档位：发给设备的每一行，和它们是从卡里怎么算出来的。 */
    private static void historyAndUsage() {
        java.util.TimeZone zone = java.util.TimeZone.getTimeZone("Asia/Shanghai");
        // 2026-10-10 15:00 +08:00
        long now = 1791615600L;

        // ---- 握手：新固件多声明两样 ----
        String ack = "{\"ack\":\"hub\",\"ok\":true,\"chat\":true,\"cards\":true,\"threads\":true,"
                + "\"usage\":true,\"past\":true}";
        String older = "{\"ack\":\"hub\",\"ok\":true,\"chat\":true,\"cards\":true,\"threads\":true}";
        check(BuddyProtocol.hubAckHasUsage(ack) && BuddyProtocol.hubAckHasPast(ack)
                && !BuddyProtocol.hubAckHasUsage(older) && !BuddyProtocol.hubAckHasPast(older)
                && !BuddyProtocol.hubAckHasPast(
                        "{\"ack\":\"hub\",\"ok\":true,\"chat\":true,\"cards\":true,\"past\":true}"),
                "usage and past in the ack");

        // ---- 卡上多出来的几项 ----
        Card done = card("{\"id\":\"c41\",\"state\":\"done\",\"agent\":\"codex\",\"title\":\"任务历史\","
                + "\"created_at\":1791600000,\"finished_at\":1791612000,\"effort\":\"high\","
                + "\"model\":\"gpt-6.1-sol\",\"cost\":{\"in\":9000,\"out\":3000,\"cached\":500,"
                + "\"d5\":3,\"d7\":1}}");
        check(done.finishedAt == 1791612000d && done.effort.equals("high")
                && done.model.equals("gpt-6.1-sol") && done.tokens == 12500L && done.spent5h == 3
                && done.spent7d == 1, "what a card says about its tier and cost");
        check(done.spentLine().equals("codex · gpt-6.1-sol · 高档 · 约用了 5 小时额度的 3%"),
                "spent: " + done.spentLine());
        check(CardViews.status(done).equals("c41 · codex 高档 · 好了"), "status with the tier");
        Card bare = card("{\"id\":\"c40\",\"state\":\"failed\",\"agent\":\"tailor\",\"title\":\"推固件\","
                + "\"created_at\":1791530000,\"cost\":null}");
        check(bare.finishedAt == 0 && bare.effort.isEmpty() && bare.tokens == -1
                && bare.spent5h == -1 && bare.spentLine().isEmpty(), "an older runtime says none of it");
        Card tokensOnly = card("{\"id\":\"c39\",\"state\":\"done\",\"agent\":\"deepseek\","
                + "\"model\":\"deepseek-v4-pro\",\"cost\":{\"in\":40000,\"out\":2000,\"cached\":0}}");
        check(tokensOnly.spentLine().equals("deepseek · deepseek-v4-pro · 42k token"),
                "spent, pay as you go: " + tokensOnly.spentLine());
        check(card("{\"id\":\"c1\",\"state\":\"done\",\"model\":\"x\"}").spentLine().isEmpty(),
                "Xiaoyou's own answers have no helper line");
        check(Card.effortLabel("xhigh").equals("特高档") && Card.effortLabel("odd").equals("odd")
                && Card.effortLabel(null).isEmpty(), "tier words");

        // ---- 档位跟着 chat、card、tasks 走；不认识的词不发 ----
        check(BuddyProtocol.chat("helper", "看看", "", "codex", "交给 codex 了", "busy", "c41", 1, "high")
                .endsWith(",\"card\":\"c41\",\"doing\":1,\"eff\":\"high\"}\n"), "chat with a tier");
        check(!BuddyProtocol.chat("helper", "", "", "codex", "", "busy", "c41", 1, "turbo")
                .contains("eff") && !BuddyProtocol.chat("idle", "", "", "", "", "idle").contains("eff"),
                "no tier, no field");
        check(BuddyProtocol.card("c41", "14:00", "done", "codex", 0, "任务历史", "好了", "medium")
                .endsWith(",\"reply\":\"好了\",\"eff\":\"medium\"}\n"), "card with a tier");
        check(BuddyProtocol.card("c41", "14:00", "done", "codex", 0, "任务历史", "好了")
                .endsWith(",\"reply\":\"好了\"}\n"), "card without");
        String listed = BuddyProtocol.tasks(Arrays.asList(
                new BuddyProtocol.Task("c41", "codex", "t", "working", 5, "a", "b", "low"),
                new BuddyProtocol.Task("c42", "claude", "u", "waiting", 6, "", "")));
        check(listed.contains("\"p2\":\"b\",\"eff\":\"low\"},{\"id\":\"c42\"")
                && listed.endsWith("\"p1\":\"\",\"p2\":\"\"}]}\n"), "tasks with a tier: " + listed);
        check(CardViews.deviceTask(done, now).effort.equals("high"), "the list line carries it");
        check(CardViews.messageJson("仔细看", "abc", null, "high")
                        .equals("{\"text\":\"仔细看\",\"client_id\":\"abc\",\"effort\":\"high\"}")
                && CardViews.messageJson("x", "abc", "c3", "low")
                        .equals("{\"text\":\"x\",\"client_id\":\"abc\",\"card\":\"c3\",\"pin\":true,"
                                + "\"effort\":\"low\"}")
                && CardViews.messageJson("x", "abc", null, "turbo")
                        .equals(CardViews.messageJson("x", "abc", null)), "a message that picks a tier");

        // ---- 做完的事：哪天做完的 ----
        check(CardViews.dayLabel(1791612000d, now, zone).equals("今天")
                && CardViews.dayLabel(1791561600d, now, zone).equals("今天")      // 今天 00:00
                && CardViews.dayLabel(1791561599d, now, zone).equals("昨天")
                && CardViews.dayLabel(1791475200d, now, zone).equals("昨天")      // 昨天 00:00
                && CardViews.dayLabel(1791475199d, now, zone).equals("10-08")
                && CardViews.dayLabel(1790000000d, now, zone).equals("09-21")
                && CardViews.dayLabel(0, now, zone).isEmpty(), "which day a thing ended on");
        // 时区不同，同一刻可以是不同的一天。
        check(CardViews.dayLabel(1791565200d, now, zone).equals("今天")
                && CardViews.dayLabel(1791565200d, now, java.util.TimeZone.getTimeZone("UTC"))
                        .equals("昨天"), "the day is the phone's day");

        // ---- 做完的事：另一张单子，最近做完的在前 ----
        List<Card> cards = new java.util.ArrayList<>();
        cards.add(card("{\"id\":\"c1\",\"state\":\"done\",\"agent\":\"\",\"title\":\"闲聊\"}"));
        cards.add(bare);                                         // c40，没记完成时间
        cards.add(done);                                         // c41，今天
        cards.add(card("{\"id\":\"c42\",\"state\":\"working\",\"agent\":\"codex\",\"title\":\"在做\","
                + "\"created_at\":1791614000,\"started_at\":1791614000}"));
        cards.add(card("{\"id\":\"c43\",\"state\":\"cancelled\",\"agent\":\"claude\",\"title\":\"不要了\","
                + "\"created_at\":1791500000,\"finished_at\":1791615000}"));
        List<Card> past = CardViews.devicePast(cards, BuddyProtocol.PAST_COUNT);
        check(past.size() == 3 && past.get(0).id.equals("c43") && past.get(1).id.equals("c41")
                && past.get(2).id.equals("c40"), "ended tasks, the latest to end first");
        check(CardViews.devicePast(cards, 2).size() == 2
                && CardViews.devicePast(cards, 2).get(1).id.equals("c41"), "the oldest go first");
        // 上半张单子这时只有在做的。
        List<Card> going = CardViews.deviceTasks(cards, BuddyProtocol.TASK_COUNT, false);
        check(going.size() == 1 && going.get(0).id.equals("c42"), "in progress only");
        BuddyProtocol.Past item = CardViews.devicePastItem(past.get(2), now, zone);
        check(item.id.equals("c40") && item.state.equals("failed") && item.day.equals("昨天")
                && item.effort.isEmpty(), "without a finish time the day it was started");

        // ---- past：一条最多五件，带着从第几件起、一共几件 ----
        List<BuddyProtocol.Past> many = new java.util.ArrayList<>();
        for (int index = 0; index < 13; index++) {
            many.add(new BuddyProtocol.Past("c" + (60 - index), "codex", "事 " + index,
                    index == 1 ? "failed" : (index == 2 ? "cancelled" : "done"),
                    index < 3 ? "今天" : "10-08", index == 0 ? "high" : null));
        }
        many.add(4, new BuddyProtocol.Past("an-id-that-is-too-long", "x", "y", "done", "今天", null));
        many.add(null);
        List<String> parts = BuddyProtocol.past(many);
        check(parts.size() == 3, "thirteen in three parts: " + parts.size());
        check(parts.get(0).startsWith("{\"cmd\":\"past\",\"at\":0,\"n\":13,\"list\":[{\"id\":\"c60\","
                + "\"agent\":\"codex\",\"title\":\"事 0\",\"state\":\"done\",\"day\":\"今天\","
                + "\"eff\":\"high\"},{\"id\":\"c59\",") && parts.get(0).contains("\"state\":\"failed\"")
                && parts.get(0).contains("\"state\":\"cancelled\""), "first part: " + parts.get(0));
        check(parts.get(1).startsWith("{\"cmd\":\"past\",\"at\":5,\"n\":13,\"list\":[{\"id\":\"c55\",")
                && parts.get(2).startsWith("{\"cmd\":\"past\",\"at\":10,\"n\":13,\"list\":[{\"id\":\"c50\",")
                && parts.get(2).endsWith("\"day\":\"10-08\"}]}\n"), "later parts say where they go");
        for (String part : parts) {
            check(count(part, "{\"id\"") <= BuddyProtocol.PAST_CHUNK
                    && BuddyProtocol.utf8Length(part) < 1024, "a part stays small");
        }
        // 空单子也要说一声，设备才会把旧的清掉；超过十五件的不发。
        check(BuddyProtocol.past(null).equals(Arrays.asList(
                        "{\"cmd\":\"past\",\"at\":0,\"n\":0,\"list\":[]}\n"))
                && BuddyProtocol.past(new java.util.ArrayList<BuddyProtocol.Past>()).size() == 1,
                "an empty history is still said");
        for (int index = 0; index < 10; index++) {
            many.add(new BuddyProtocol.Past("d" + index, "", "", "done", "", null));
        }
        parts = BuddyProtocol.past(many);
        check(parts.size() == 3 && parts.get(0).contains("\"n\":15")
                && parts.get(2).contains("\"id\":\"d1\"") && !parts.get(2).contains("\"id\":\"d2\""),
                "fifteen at most");
        // 最长的一件：标题、名字和日子都顶到上限，一条也远不到一行的上限。
        StringBuilder wide = new StringBuilder();
        for (int index = 0; index < 40; index++) {
            wide.append('中');
        }
        List<BuddyProtocol.Past> widest = new java.util.ArrayList<>();
        for (int index = 0; index < 5; index++) {
            widest.add(new BuddyProtocol.Past("c1234567890", wide.toString(), wide.toString(),
                    "cancelled", wide.toString(), "medium"));
        }
        check(BuddyProtocol.utf8Length(BuddyProtocol.past(widest).get(0)) < 1024,
                "the longest part there can be");

        // ---- want：设备来要一张卡 ----
        check("c41".equals(BuddyProtocol.parseWant("{\"evt\":\"want\",\"card\":\"c41\"}"))
                && BuddyProtocol.parseWant("{\"evt\":\"want\"}") == null
                && BuddyProtocol.parseWant("{\"evt\":\"fw\",\"card\":\"c41\"}") == null
                && BuddyProtocol.parseWant("{\"evt\":\"want\",\"card\":\"an-id-that-is-too-long\"}") == null
                && BuddyProtocol.parseWant("not json") == null, "the device asks for a card");

        // ---- 用量：Runtime 给的那份 ----
        String feed = "{\"usage\":{\"rev\":7,\"at\":1791615000,\"accounts\":["
                + "{\"id\":\"codex:/home/u/.codex-xiaoyou\",\"kind\":\"codex\",\"agents\":[\"codex\"],"
                + "\"state\":\"ok\",\"windows\":[{\"kind\":\"5h\",\"left\":58,\"resets_at\":1791620400},"
                + "{\"kind\":\"7d\",\"left\":81.4,\"resets_at\":1791939600}],\"plan\":\"pro\","
                + "\"source\":\"app_server\",\"updated_at\":1791614880,\"note\":\"\"},"
                + "{\"id\":\"claude:/home/u/.claude-xiaoyou\",\"kind\":\"claude\","
                + "\"agents\":[\"claude\",\"tailor\",\"third\"],\"state\":\"warn\","
                + "\"windows\":[{\"kind\":\"5h\",\"left\":12,\"resets_at\":null},"
                + "{\"kind\":\"7d\",\"left\":null,\"resets_at\":1791939600},{\"kind\":\"opus\",\"left\":3}],"
                + "\"plan\":null,\"source\":\"events\",\"updated_at\":null,\"note\":\"精确的数取不到\"}],"
                + "\"agents\":["
                + "{\"name\":\"claude\",\"type\":\"claude_code\",\"model\":\"claude-sonnet-5-5-20260301\","
                + "\"effort\":\"medium\",\"efforts\":[\"low\",\"medium\",\"high\"],\"models\":[],"
                + "\"account\":\"claude:/home/u/.claude-xiaoyou\",\"running\":0,\"now\":null},"
                + "{\"name\":\"tailor\",\"type\":\"claude_code\",\"model\":null,\"effort\":\"high\","
                + "\"efforts\":[\"high\"],\"models\":[],\"account\":\"claude:/home/u/.claude-xiaoyou\","
                + "\"running\":0,\"now\":null},"
                + "{\"name\":\"codex\",\"type\":\"codex\",\"model\":\"gpt-6.1-sol\",\"effort\":\"medium\","
                + "\"efforts\":[\"low\",\"medium\",\"high\"],\"models\":[\"gpt-6.1-mini\"],"
                + "\"account\":\"codex:/home/u/.codex-xiaoyou\",\"running\":2,\"now\":\"gpt-6.1-mini\"},"
                + "{\"name\":\"deepseek\",\"type\":\"claude_code\",\"model\":\"deepseek-v4-pro\","
                + "\"effort\":null,\"efforts\":[],\"models\":[],\"account\":null,\"running\":1,\"now\":null},"
                + "{\"name\":\"echo\",\"type\":\"echo\",\"model\":null,\"effort\":null,\"efforts\":[],"
                + "\"models\":[],\"account\":null,\"running\":0,\"now\":null}]}}";
        Usage usage = Usage.from(Json.parseObject(feed).get("usage"));
        check(usage != null && usage.rev == 7 && usage.accounts.size() == 2
                && usage.agents.size() == 5 && !usage.empty(), "usage as the runtime reports it");
        check(usage.accounts.get(0).left5h == 58 && usage.accounts.get(0).left7d == 81
                && usage.accounts.get(0).reset5h == 1791620400L
                && usage.accounts.get(1).left7d == -1 && usage.accounts.get(1).reset5h == 0
                && usage.accounts.get(1).updatedAt == 0, "what is unknown stays unknown");
        check(usage.agent("codex").model.equals("gpt-6.1-mini") && usage.agent("codex").running == 2
                && usage.agent("tailor").model.isEmpty() && usage.agent("deepseek").account.isEmpty()
                && usage.agent("nobody") == null, "a helper: the model of the round in progress");
        check(Usage.from(null) == null && Usage.from("x") == null
                && Usage.from(Json.parseObject("{}")).empty(), "an older runtime sends no usage");

        check(Usage.shortModel("claude-sonnet-5-5-20260301", 19).equals("sonnet-5-5")
                && Usage.shortModel("claude-opus-5-5", 19).equals("opus-5-5")
                && Usage.shortModel("gpt-6.1-sol", 19).equals("gpt-6.1-sol")
                && Usage.shortModel("deepseek-v4-pro", 19).equals("deepseek-v4-pro")
                && Usage.shortModel("a-model-with-a-very-long-name", 19).equals("a-model-with-a-very")
                && Usage.shortModel("model-12345678", 19).equals("model")
                && Usage.shortModel("model-1234567x", 19).equals("model-1234567x")
                && Usage.shortModel(null, 19).isEmpty(), "model names made short");

        // ---- 用量：发给设备的那一行（和固件测试里的写法一致：run 是数字） ----
        String line = BuddyProtocol.usage(usage.deviceEntries(40));
        check(line.equals("{\"cmd\":\"usage\",\"list\":["
                + "{\"st\":\"ok\",\"w5\":58,\"r5\":1791620400,\"w7\":81,\"r7\":1791939600,\"age\":160,"
                + "\"who\":[{\"n\":\"codex\",\"m\":\"gpt-6.1-mini\",\"eff\":\"medium\",\"run\":2}]},"
                + "{\"st\":\"warn\",\"w5\":12,\"r7\":1791939600,"
                + "\"who\":[{\"n\":\"claude\",\"m\":\"sonnet-5-5\",\"eff\":\"medium\"},"
                + "{\"n\":\"tailor\",\"eff\":\"high\"}]},"
                + "{\"st\":\"na\",\"who\":[{\"n\":\"deepseek\",\"m\":\"deepseek-v4-pro\",\"run\":1}]}"
                + "]}\n"), "usage for the device: " + line);
        check(!BuddyProtocol.usage(usage.deviceEntries(-1)).contains("\"age\""),
                "without the age, to tell whether anything changed");
        check(BuddyProtocol.usage(null).equals("{\"cmd\":\"usage\",\"list\":[]}\n")
                && BuddyProtocol.usage(Usage.from(Json.parseObject("{}")).deviceEntries(0))
                        .equals("{\"cmd\":\"usage\",\"list\":[]}\n"), "nothing to show is an empty list");
        check(BuddyProtocol.utf8Length(line) < BuddyProtocol.LINE_MAX, "fits a line");

        // ---- 用量：给人看的几段 ----
        List<String> said = usage.describe(now, 40, zone);
        check(said.size() == 3, "one paragraph per account, then pay as you go");
        check(said.get(0).equals("codex（gpt-6.1-mini · 中档） 正在做 2 件 — 正常\n"
                + "5 小时 剩 58%，16:20 重置\n本周 剩 81%，周三 09:00 重置\n2 分钟前更新"),
                "account: " + said.get(0));
        check(said.get(1).equals("claude（claude-sonnet-5-5-20260301 · 中档）\ntailor（还没跑过 · 高档）"
                + " — 快用完了\n5 小时 剩 12%\n本周：还不知道剩多少，周三 09:00 重置\n精确的数取不到"),
                "account without numbers: " + said.get(1));
        check(said.get(2).equals("deepseek（deepseek-v4-pro） 正在做 1 件 — 按量计费"),
                "pay as you go: " + said.get(2));
        check(Usage.resetText(0, now, zone).isEmpty() && Usage.resetText(now - 1, now, zone).isEmpty()
                && Usage.resetText(now + 60, now, zone).equals("15:01 重置"), "when a window resets");
    }

    private static int count(String text, String piece) {
        int found = 0;
        for (int at = text.indexOf(piece); at >= 0; at = text.indexOf(piece, at + 1)) {
            found++;
        }
        return found;
    }

    public static void main(String[] args) {
        run();
        System.out.println("BuddyProtocol: PASS");
    }
}
