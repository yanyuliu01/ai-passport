package com.yanyuliu.pockethub;

import org.junit.Test;
import static org.junit.Assert.*;

public class ChatHistoryTest {
    @Test public void anExplicitCompletionSummaryIsNotReplacedByTheDeviceBrief() {
        String summary = "详细总结".repeat(250);
        ChatHistory history = new ChatHistory();
        history.ingest(card("[{\"role\":\"xiaoyou\",\"text\":\"" + summary
                + "\",\"kind\":\"summary\",\"scope\":\"main\",\"at\":30}]", "\"state\":\"done\",\"brief\":\"小屏简报\""));
        assertEquals(summary, history.main().get(0).text);
    }
    private Card card(String entries, String fields) {
        return Card.from(Json.parseObject("{\"id\":\"c1\",\"created_at\":10,\"agent\":\"codex\","
                + "\"entries\":" + entries + "," + fields + "}"));
    }

    @Test public void userAppearsBeforeAnyReplyAndBeforeAnyCard() {
        ChatHistory history = new ChatHistory(); history.begin("local", null, "hello", 10);
        assertEquals(2, history.main().size());
        assertEquals("hello", history.main().get(0).text);
        assertEquals("sending", history.main().get(0).status);
        assertEquals("thinking", history.main().get(1).status);
        assertEquals("", history.main().get(1).text);
    }

    @Test public void streamAndFinalShareOneBubbleEvenWhenCardArrivesFirst() {
        ChatHistory history = new ChatHistory(); history.begin("local", null, "hello", 10);
        history.accepted("local", "remote"); history.streaming("remote", "work");
        Card card = card("[{\"role\":\"you\",\"text\":\"hello\",\"at\":11,\"id\":\"remote:you\"},"
                + "{\"role\":\"xiaoyou\",\"text\":\"working\",\"at\":11,\"id\":\"remote:reply\"}]", "\"state\":\"working\"");
        history.ingest(card); history.finish("remote", "c1", "working", false, card);
        assertEquals(2, history.main().size());
        assertEquals("working", history.main().get(1).text);
        assertEquals("done", history.main().get(1).status);
    }

    @Test public void oldRuntimePairIsBoundWithoutDuplicateBubbles() {
        ChatHistory history = new ChatHistory(); history.begin("local", null, "hello", 10);
        history.accepted("local", "remote");
        Card card = card("[{\"role\":\"you\",\"text\":\"hello\",\"at\":11},"
                + "{\"role\":\"xiaoyou\",\"text\":\"assigned\",\"at\":11}]", "\"state\":\"working\"");
        history.ingest(card); history.finish("remote", "c1", "assigned", false, card); history.ingest(card);
        assertEquals(2, history.main().size());
        assertEquals("assigned", history.main().get(1).text);
    }

    @Test public void replyBeforeFeedAlsoBindsAnOldRuntime() {
        ChatHistory history = new ChatHistory(); history.begin("local", "c1", "more", 10);
        history.accepted("local", "remote"); history.finish("remote", "c1", "ok", false, null);
        Card card = card("[{\"role\":\"you\",\"text\":\"more\",\"at\":11},"
                + "{\"role\":\"xiaoyou\",\"text\":\"ok\",\"at\":11}]", "\"state\":\"working\"");
        history.ingest(card);
        assertTrue(history.main().isEmpty()); assertTrue(history.pending("c1").isEmpty());
    }

    @Test public void taskReplyStaysInTaskAndCompletionSummaryStaysInMain() {
        ChatHistory history = new ChatHistory(); history.begin("local", "c1", "more", 10);
        history.accepted("local", "remote");
        history.ingest(card("[{\"role\":\"xiaoyou\",\"text\":\"raw\",\"kind\":\"worker_result\",\"scope\":\"task\",\"at\":20},"
                + "{\"role\":\"xiaoyou\",\"text\":\"summary\",\"kind\":\"summary\",\"scope\":\"main\",\"at\":21}]", "\"state\":\"done\""));
        assertEquals(1, history.main().size()); assertEquals("summary", history.main().get(0).text);
        assertEquals(2, history.pending("c1").size());
    }

    @Test public void twoCompletionsAndRepeatedTextRemainDistinctInTimeOrder() {
        ChatHistory history = new ChatHistory();
        history.ingest(card("[{\"role\":\"xiaoyou\",\"text\":\"done\",\"at\":30,\"id\":\"run2:reply\"},"
                + "{\"role\":\"xiaoyou\",\"text\":\"done\",\"at\":20,\"id\":\"run1:reply\"}]", "\"state\":\"done\""));
        assertEquals(2, history.main().size()); assertEquals("run1:reply", history.main().get(0).id);
        history.ingest(card("[]", "\"state\":\"working\""));
        assertEquals(2, history.main().size());
    }

    @Test public void restorationKeepsHistoryBeyondCardWindowAndMarksUnfinishedReplies() {
        ChatHistory history = new ChatHistory();
        for (int i = 0; i < 60; i++) {
            history.begin("m" + i, null, "hello", i + 1);
            history.finish("m" + i, null, "reply", false, null);
        }
        history.begin("latest", null, "waiting", 100);
        ChatHistory restored = ChatHistory.restore(history.json());
        assertEquals(122, restored.main().size());
        assertEquals("interrupted", restored.main().get(121).status);
    }

    @Test public void retryKeepsOriginalClientIdAfterAcceptanceAndRestore() {
        ChatHistory history = new ChatHistory(); history.begin("client", null, "hello", 10);
        history.accepted("client", "server"); history.finish("server", null, "offline", true, null);
        history = ChatHistory.restore(history.json());
        assertEquals("client", history.retry("server:you")); assertTrue(history.main().isEmpty());
    }

    @Test public void backgroundSummaryStreamDoesNotOverwriteCurrentReply() {
        ChatHistory history = new ChatHistory(); history.begin("current", null, "next", 10);
        history.streaming("current", "answer");
        history.ingest(card("[]", "\"state\":\"working\",\"summary_partial\":\"background\""));
        assertEquals(3, history.main().size());
        assertEquals("answer", history.main().get(1).text);
        history.ingest(card("[{\"role\":\"xiaoyou\",\"text\":\"complete\",\"kind\":\"summary\",\"at\":30}]", "\"state\":\"done\""));
        assertEquals(3, history.main().size()); assertEquals("complete", history.main().get(2).text);
    }

    @Test public void oldPhoneCacheAndFirstFeedMergeCompletePairsOnce() {
        ChatHistory history = new ChatHistory();
        history.importTurn(new ChatTurn("m1", "same", "first answer", 10));
        history.importTurn(new ChatTurn("m2", "same", "second answer", 20));
        Card card = card("[{\"role\":\"you\",\"text\":\"same\",\"at\":11},"
                + "{\"role\":\"xiaoyou\",\"text\":\"first answer\",\"at\":11}]", "\"state\":\"working\"");
        history.ingest(card); history.ingest(card);
        assertEquals(4, history.main().size());
        assertEquals("c1", history.main().get(0).card);
        assertEquals("", history.main().get(2).card);
    }

    @Test public void responseDoesNotMoveTheUserBubbleBehindLaterNotifications() {
        ChatHistory history = new ChatHistory(); history.begin("local", null, "hello", 10);
        history.accepted("local", "remote");
        Card card = card("[{\"role\":\"you\",\"text\":\"hello\",\"at\":100,\"id\":\"remote:you\"},"
                + "{\"role\":\"xiaoyou\",\"text\":\"reply\",\"at\":100,\"id\":\"remote:reply\"}]", "\"state\":\"working\"");
        history.finish("remote", "c1", "reply", false, card); history.ingest(card);
        assertEquals(10, history.main().get(0).at, 0);
    }
    @Test public void delayedStreamCannotReplaceAlreadySyncedFinalReply() {
        ChatHistory history = new ChatHistory(); history.begin("remote", null, "hello", 10);
        history.ingest(card("[{\"role\":\"xiaoyou\",\"text\":\"complete\",\"at\":11,"
                + "\"id\":\"remote:reply\"}]", "\"state\":\"working\""));
        history.streaming("remote", "partial");
        assertEquals("complete", history.main().get(1).text);
        assertEquals("done", history.main().get(1).status);
    }
}
