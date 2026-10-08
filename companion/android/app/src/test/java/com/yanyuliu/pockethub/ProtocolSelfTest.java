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
    }

    public static void main(String[] args) {
        run();
        System.out.println("BuddyProtocol: PASS");
    }
}
