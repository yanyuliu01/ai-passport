package com.yanyuliu.pockethub;

import org.junit.Test;
import java.util.List;
import static org.junit.Assert.*;

public class ChatHistoryTest {
    private static java.util.Map<String,Object> mixedMessage(String id, double at, Double projectedAt) {
        java.util.Map<String,Object> message = new java.util.LinkedHashMap<>(java.util.Map.of(
                "id", id, "at", at, "role", "xiaoyou", "scope", "main", "status", "done", "text", id));
        if (projectedAt != null) message.put("presentation", java.util.Map.of("version", 1, "placement", "chat",
                "order", java.util.List.of(projectedAt, id, 1, id)));
        return message;
    }
    @Test public void mixedLegacyAndProjectedMessagesHaveOneOrderForEveryArrivalSequence() {
        var a=mixedMessage("a",30,10.0); var b=mixedMessage("b",20,null); var c=mixedMessage("c",10,30.0);
        for (var permutation : List.of(List.of(a,b,c),List.of(a,c,b),List.of(b,a,c),List.of(b,c,a),List.of(c,a,b),List.of(c,b,a))) {
            ChatHistory history=ChatHistory.restore(UsageEvents.json(java.util.Map.of("messages",permutation)));
            assertEquals(List.of("a","b","c"),history.main().stream().map(m->m.id).collect(java.util.stream.Collectors.toList()));
            assertEquals(history.json(),ChatHistory.restore(history.json()).json());
        }
    }
    @Test public void longMixedHistoryDoesNotCrashOrLoseMessagesOnRestore() {
        var records=new java.util.ArrayList<java.util.Map<String,Object>>();
        for(int i=0;i<1200;i++)records.add(mixedMessage("m"+i,1200-i,i%3==0?null:(double)i));
        List<String> expected=null;
        for(int seed=0;seed<12;seed++) {
            java.util.Collections.shuffle(records,new java.util.Random(seed));
            var history=ChatHistory.restore(UsageEvents.json(java.util.Map.of("messages",records)));
            var ids=history.main().stream().map(m->m.id).collect(java.util.stream.Collectors.toList());
            assertEquals(1200,ids.size());assertEquals(1200,new java.util.HashSet<>(ids).size());
            if(expected==null)expected=ids;else assertEquals(expected,ids);
        }
    }

    @Test public void finishedMainTurnsAreWhatGoesToAnotherComputer() {
        ChatHistory history = new ChatHistory();
        history.begin("l1", null, "第一句", 10); history.accepted("l1", "m1"); history.finish("m1", "c1", "第一句的回答", false, null);
        history.begin("l2", null, "还没答完", 20); history.accepted("l2", "m2");
        history.begin("l3", "c9", "任务里的话", 30); history.accepted("l3", "m3"); history.finish("m3", "c9", "好", false, null);
        history.begin("l4", null, RuntimeClient.VOICE_PLACEHOLDER, 40); history.accepted("l4", "m4");
        history.finish("m4", null, "没听清", false, null);
        List<ChatTurn> turns = history.turns();
        assertEquals(1, turns.size());
        assertEquals("m1", turns.get(0).id);
        assertEquals("第一句", turns.get(0).text);
        assertEquals("第一句的回答", turns.get(0).reply);
        // Survives saving: the same turns come back from the stored history of any computer.
        assertEquals("m1", ChatHistory.restore(history.json()).turns().get(0).id);
    }

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

    @Test public void aRetoldResultBeingWrittenIsOneMessageNotTwo() {
        ChatHistory history = new ChatHistory();
        String handed = "[{\"role\":\"xiaoyou\",\"text\":\"交给 project 了\",\"at\":11,\"id\":\"m1:reply\",\"scope\":\"main\"}]";
        history.ingest(card(handed, "\"state\":\"working\",\"summary_partial\":\"我\""));
        // Older Runtimes: summary_partial is the only copy of the words being written.
        assertEquals(2, history.main().size());
        assertEquals("我", history.main().get(1).text);
        // Newer Runtimes also send it as a message: that one is drawn, the card's copy goes.
        history.pending(new SyncCache.Pending("r1-summary:reply", "c1", "main", "xiaoyou", "我回顾了", "streaming", 20));
        history.ingest(card(handed, "\"state\":\"working\",\"summary_partial\":\"我回顾了一下\""));
        List<ChatHistory.Message> main = history.main();
        assertEquals(2, main.size());
        assertEquals("r1-summary:reply", main.get(1).id);
        // Final: the card entry with the same id replaces the message being written.
        history.ingest(card("[{\"role\":\"xiaoyou\",\"text\":\"交给 project 了\",\"at\":11,\"id\":\"m1:reply\",\"scope\":\"main\"},"
                + "{\"role\":\"xiaoyou\",\"text\":\"我回顾了一下，好了\",\"at\":30,\"id\":\"r1-summary:reply\",\"kind\":\"summary\",\"scope\":\"main\"}]",
                "\"state\":\"done\""));
        main = history.main();
        assertEquals(2, main.size());
        assertEquals("我回顾了一下，好了", main.get(1).text);
        assertEquals("done", main.get(1).status);
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
    @Test public void aFinalReplyArrivingBySyncKeepsTheLocalSendKey() {
        ChatHistory history = new ChatHistory(); history.begin("client", null, "hello", 10);
        history.accepted("client", "server");
        SyncCache.Pending reply = new SyncCache.Pending("server:reply", "", "main", "xiaoyou", "OK", "final", 11);
        reply.presentation = Presentation.restore("{\"version\":1,\"complete\":true,\"status\":\"final\",\"placement\":\"chat\","
                + "\"blocks\":[{\"text\":\"OK\",\"complete\":true}]}");
        assertTrue(history.pending(reply));
        ChatHistory.Message shown = history.main().get(1);
        assertEquals("server:reply", shown.id);
        assertEquals("client", shown.clientId);  // Same chat row; the reply is not drawn as a new one.
        assertEquals("done", shown.status);
    }
    @Test public void aCopyStoresTheSameJsonAndLaterChangesDoNotReachIt() {
        ChatHistory history = new ChatHistory(); history.begin("client", null, "hello", 10);
        history.accepted("client", "server"); history.finish("server", null, "OK", false, null);
        ChatHistory copy = history.copy();
        assertEquals(history.json(), copy.json());
        history.begin("next", null, "again", 20); history.streaming("server", "changed");
        assertEquals(2, copy.main().size());
        assertEquals("OK", copy.main().get(1).text);
        assertEquals("client", ChatHistory.restore(copy.json()).main().get(1).clientId);
    }
    @Test public void onlySettledSyncMessagesAreWorthStoring() {
        SyncCache.Pending fragment = new SyncCache.Pending("s:reply", "", "main", "xiaoyou", "half", "streaming", 1);
        fragment.presentation = Presentation.restore("{\"version\":1,\"complete\":false,\"status\":\"streaming\",\"blocks\":[]}");
        assertFalse(ChatHistory.settled(fragment));
        SyncCache.Pending done = new SyncCache.Pending("s:reply", "", "main", "xiaoyou", "all", "final", 1);
        done.presentation = Presentation.restore("{\"version\":1,\"complete\":true,\"status\":\"final\",\"blocks\":[]}");
        assertTrue(ChatHistory.settled(done));
        assertTrue(ChatHistory.settled(new SyncCache.Pending("o:you", "", "main", "you", "hi", "queued", 1)));
        assertFalse(ChatHistory.settled(new SyncCache.Pending("l:reply", "", "main", "xiaoyou", "x", "streaming", 1)));
        assertTrue(ChatHistory.settled(new SyncCache.Pending("l:reply", "", "main", "xiaoyou", "x", "failed", 1)));
    }
    @Test public void anotherClientsMessageHasNoLocalSendKey() {
        ChatHistory history = new ChatHistory();
        assertTrue(history.pending(new SyncCache.Pending("other:you", "", "main", "you", "hi", "final", 11)));
        assertEquals("", history.main().get(0).clientId);
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
