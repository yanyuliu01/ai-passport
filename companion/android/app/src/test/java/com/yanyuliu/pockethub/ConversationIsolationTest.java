package com.yanyuliu.pockethub;

import org.junit.Test;
import static org.junit.Assert.*;

public class ConversationIsolationTest {
    @Test public void pendingAndRestoredMessagesKeepTheirConversation() {
        ChatHistory history = new ChatHistory();
        history.begin("one", null, "main message", 1); history.scope("one", "default", true);
        history.begin("two", "c2", "side addition", 2); history.scope("two", "side-one", true);
        assertEquals(2, history.main("default").size());
        assertEquals(2, history.main("side-one").size());
        ChatHistory restored = ChatHistory.restore(history.json());
        assertEquals("side addition", restored.main("side-one").get(0).text);
        assertFalse(restored.main().stream().anyMatch(m -> m.text.equals("side addition")));
    }
    @Test public void syncKeepsTheSameTaskAndConversationAcrossAllChatPages() {
        SyncCache cache = new SyncCache("*");
        SyncCache.Result result = cache.apply(Json.parseObject("{\"seq\":2,\"messages\":[{\"id\":\"message\",\"conversation\":\"side-one\",\"task\":\"\",\"scope\":\"main\",\"author\":{\"kind\":\"owner\"},\"kind\":\"user\",\"text\":\"hello\",\"status\":\"final\",\"created_at\":1}],\"tasks\":[]}"));
        assertEquals("side-one", result.pending.get(0).conversation);
        ChatHistory history = new ChatHistory(); history.pending(result.pending.get(0));
        assertEquals(0, history.main().size()); assertEquals(1, history.main("side-one").size());
    }
    @Test public void scopedSupplementKeepsOriginalTaskAndMainPresentation() {
        String json = RuntimeClient.scopedMessageJson("add", "id", "c7", null, null, null, "side-one", true);
        java.util.Map<String,Object> raw = Json.parseObject(json);
        assertEquals("c7", Json.string(raw, "card")); assertTrue(Json.flag(raw, "pin"));
        assertEquals("main", Json.string(raw, "reply_scope")); assertEquals("side-one", Json.string(raw, "conversation"));
    }
}
