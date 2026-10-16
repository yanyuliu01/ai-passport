package com.yanyuliu.pockethub;

import org.junit.Test;
import static org.junit.Assert.*;
import java.util.List;
import java.util.Map;

public class SyncCacheTest {
    private static Map<String, Object> page(String body) { return Json.parseObject(body); }

    private static final String ASKED = "{\"id\":\"m1:you\",\"rev\":3,\"seq\":3,\"conversation\":\"default\",\"task\":\"c1\","
            + "\"scope\":\"main\",\"author\":{\"kind\":\"owner\",\"name\":\"you\"},\"kind\":\"user\",\"text\":\"查一下\","
            + "\"status\":\"final\",\"partial\":\"\",\"created_at\":100}";

    private static String reply(int rev, String status, String text, String partial) {
        return "{\"id\":\"m1:reply\",\"rev\":" + rev + ",\"seq\":" + rev + ",\"conversation\":\"default\",\"task\":\"c1\","
                + "\"scope\":\"main\",\"author\":{\"kind\":\"xiaoyou\",\"name\":\"xiaoyou\"},\"kind\":\"reply\",\"text\":\""
                + text + "\",\"status\":\"" + status + "\",\"partial\":\"" + partial + "\",\"created_at\":101}";
    }

    private static final String TASK = "{\"id\":\"c1\",\"seq\":4,\"title\":\"查一下\",\"state\":\"done\",\"conversation\":\"default\","
            + "\"members\":[\"m1:you\",\"m1:reply\"],\"created_at\":100}";

    @Test public void tasksBecomeCardsWithOnlyFinalMessagesAndPendingIsHandedOut() {
        SyncCache cache = new SyncCache("default");
        SyncCache.Result first = cache.apply(page("{\"epoch\":\"e1\",\"seq\":5,\"more\":false,\"messages\":["
                + ASKED + "," + reply(5, "streaming", "", "想到一半") + "],\"tasks\":[" + TASK + "]}"));
        assertFalse(first.replaced);
        assertEquals("5", cache.cursor());
        assertEquals(1, first.cards.size());
        Card card = first.cards.get(0);
        assertEquals("c1", card.id);
        // The reply is still being written: not on the card yet, but handed out as pending.
        assertEquals(1, card.entries.size());
        assertEquals("you", card.entries.get(0).role);
        assertEquals("m1:you", card.entries.get(0).id);
        assertEquals(1, first.pending.size());
        assertEquals("m1:reply", first.pending.get(0).id);
        assertEquals("想到一半", first.pending.get(0).text);

        SyncCache.Result done = cache.apply(page("{\"epoch\":\"e1\",\"seq\":7,\"messages\":["
                + reply(7, "final", "查好了", "") + "],\"tasks\":[]}"));
        assertEquals(1, done.cards.size());
        assertEquals(2, done.cards.get(0).entries.size());
        assertEquals("查好了", done.cards.get(0).entries.get(1).text);
        assertEquals("xiaoyou", done.cards.get(0).entries.get(1).role);
        assertTrue(done.pending.isEmpty());

        // An older revision arriving late never replaces the final text.
        SyncCache.Result late = cache.apply(page("{\"epoch\":\"e1\",\"seq\":8,\"messages\":["
                + reply(6, "streaming", "", "旧的") + "],\"tasks\":[]}"));
        assertTrue(late.pending.isEmpty());
        assertEquals("查好了", cache.card("c1").entries.get(1).text);
    }

    @Test public void acceptedButNotYetFiledMessagesArePending() {
        SyncCache cache = new SyncCache("default");
        SyncCache.Result result = cache.apply(page("{\"epoch\":\"e1\",\"seq\":2,\"messages\":["
                + "{\"id\":\"m9:you\",\"rev\":2,\"conversation\":\"default\",\"task\":null,\"author\":{\"kind\":\"owner\"},"
                + "\"kind\":\"user\",\"text\":\"别的手机说的\",\"status\":\"final\",\"created_at\":5},"
                + "{\"id\":\"d1\",\"rev\":1,\"conversation\":\"__desktop__\",\"task\":\"c7\",\"author\":{\"kind\":\"desktop\"},"
                + "\"kind\":\"reply\",\"text\":\"电脑上的\",\"status\":\"final\",\"created_at\":4}],\"tasks\":[]}"));
        List<SyncCache.Pending> pending = result.pending;
        assertEquals(1, pending.size());
        assertEquals("you", pending.get(0).role);
        assertEquals("别的手机说的", pending.get(0).text);
    }

    @Test public void anotherEpochReplacesEverythingAndResultsAndAgentsKeepTheirMarks() {
        SyncCache cache = new SyncCache("default");
        cache.apply(page("{\"epoch\":\"e1\",\"seq\":5,\"messages\":[" + ASKED + "],\"tasks\":[" + TASK + "]}"));
        SyncCache.Result next = cache.apply(page("{\"epoch\":\"e2\",\"reset\":true,\"seq\":2,\"messages\":["
                + "{\"id\":\"r1\",\"rev\":2,\"conversation\":\"default\",\"task\":\"c1\",\"author\":{\"kind\":\"agent\",\"name\":\"codex\"},"
                + "\"kind\":\"result\",\"text\":\"做完了\",\"status\":\"final\",\"created_at\":9,"
                + "\"attachments\":[{\"id\":\"a0123456789abcdef0123456789abcdef\",\"name\":\"图.png\",\"mime\":\"image/png\",\"size\":3}]}],"
                + "\"tasks\":[{\"id\":\"c1\",\"seq\":2,\"title\":\"新的\",\"members\":[\"r1\"]}]}"));
        assertTrue(next.replaced);
        assertEquals("e2", cache.epoch());
        assertEquals("2", cache.cursor());
        Card card = next.cards.get(0);
        assertEquals("新的", card.title);
        assertEquals(1, card.entries.size());  // the old m1:you belonged to the replaced records
        Card.Entry entry = card.entries.get(0);
        assertEquals("worker_result", entry.kind);
        assertEquals("codex", entry.author);
        assertEquals(1, entry.attachments.size());
    }

    @Test public void addressHistoriesFoldIntoTheSharedOneById() {
        ChatHistory shared = new ChatHistory(), lan = new ChatHistory();
        shared.begin("a", null, "在 Funnel 上说的", 1); shared.accepted("a", "m1");
        lan.begin("b", null, "在局域网上说的", 2); lan.accepted("b", "m2");
        lan.begin("c", null, "重复的", 3); lan.accepted("c", "m1");
        assertTrue(shared.absorb(ChatHistory.restore(lan.json())));
        assertEquals(4, shared.main().size());
        assertEquals("在 Funnel 上说的", shared.main().get(0).text);  // the shared copy wins on the same id
        assertFalse(shared.absorb(ChatHistory.restore(lan.json())));
    }

    @Test public void historyShowsAnotherPhonesMessageOnceAndLetsTheCardTakeOver() {
        ChatHistory history = new ChatHistory();
        SyncCache.Pending asked = new SyncCache.Pending("m2:you", "", "main", "you", "另一台手机", "final", 10);
        assertTrue(history.pending(asked));
        assertFalse(history.pending(asked));
        assertEquals("sent", history.main().get(0).status);
        // A local send keeps its own bubble: pending copies never replace it.
        history.begin("local", null, "我自己发的", 11);
        history.accepted("local", "m3");
        assertFalse(history.pending(new SyncCache.Pending("m3:you", "", "main", "you", "我自己发的", "final", 11)));
        assertFalse(history.pending(new SyncCache.Pending("m3:reply", "", "main", "xiaoyou", "半句", "streaming", 11)));
        assertEquals("thinking", history.main().get(2).status);
    }
}
