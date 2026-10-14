package com.yanyuliu.pockethub;

import org.junit.Test;
import static org.junit.Assert.*;
import java.util.Arrays;
import java.util.List;

public class AppPresentationTest {
    private Card card(String fields) { return Card.from(Json.parseObject("{" + fields + "}")); }

    @Test public void aliasesUseTheSamePortraitWithoutChangingWireIdentity() {
        assertEquals("claude", AppPresentation.role("cc"));
        assertEquals("claude", AppPresentation.role("CLAUDE"));
        assertEquals("ds", AppPresentation.role("DeepSeek"));
        assertEquals("codex", AppPresentation.role("Codex"));
        assertEquals("my-helper", AppPresentation.role("my-helper"));
        Card raw = card("\"id\":\"c1\",\"agent\":\"cc\",\"state\":\"working\"");
        assertEquals("cc", raw.agent);
    }

    @Test public void responsibleHelperIsNotTheAuthorOfDsRelayReplies() {
        Card runtime = card("\"id\":\"c1\",\"agent\":\"codex\",\"state\":\"working\"");
        Card desktop = card("\"id\":\"c2\",\"agent\":\"cc\",\"source\":\"desktop\"");
        assertEquals("ds", AppPresentation.author(runtime, new Card.Entry("xiaoyou", "relay")));
        assertEquals("claude", AppPresentation.author(desktop, new Card.Entry("xiaoyou", "reply")));
        assertEquals("you", AppPresentation.author(desktop, new Card.Entry("you", "question")));
        Card unknown = card("\"id\":\"c3\",\"source\":\"desktop\"");
        assertEquals("assistant", AppPresentation.author(unknown, new Card.Entry("xiaoyou", "reply")));
    }

    @Test public void sidebarOrderIgnoresStateAndKeepsPinnedFirst() {
        Card old = card("\"id\":\"c1\",\"agent\":\"codex\",\"state\":\"done\",\"created_at\":10");
        Card mid = card("\"id\":\"c2\",\"agent\":\"cc\",\"state\":\"working\",\"created_at\":20");
        Card fresh = card("\"id\":\"c3\",\"agent\":\"codex\",\"state\":\"waiting\",\"created_at\":30");
        Card desktop = card("\"id\":\"c4\",\"agent\":\"codex\",\"source\":\"desktop\",\"state\":\"working\",\"created_at\":40");
        Card chat = card("\"id\":\"c5\",\"state\":\"done\",\"created_at\":50");
        java.util.Set<String> none = new java.util.HashSet<>();
        List<Card> all = Arrays.asList(old, mid, fresh, desktop, chat);
        assertEquals(Arrays.asList(fresh, mid, old), AppPresentation.sidebarTasks(all, none, none));
        Card midDone = card("\"id\":\"c2\",\"agent\":\"cc\",\"state\":\"done\",\"created_at\":20");
        assertEquals(Arrays.asList(fresh, midDone, old),
                AppPresentation.sidebarTasks(Arrays.asList(old, midDone, fresh), none, none));
        java.util.Set<String> pinned = new java.util.HashSet<>(Arrays.asList("c1"));
        java.util.Set<String> archived = new java.util.HashSet<>(Arrays.asList("c3"));
        assertEquals(Arrays.asList(old, mid), AppPresentation.sidebarTasks(all, pinned, archived));
    }

    @Test public void chatLeavesDesktopSessionsToTheirHelper() {
        Card desktop = card("\"id\":\"c1\",\"agent\":\"codex\",\"source\":\"desktop\"");
        Card task = card("\"id\":\"c2\",\"agent\":\"codex\",\"state\":\"working\"");
        Card chat = card("\"id\":\"c3\",\"state\":\"done\"");
        assertEquals(Arrays.asList(task, chat), AppPresentation.chatCards(Arrays.asList(desktop, task, chat)));
        assertTrue(AppPresentation.dispatched(task));
        assertFalse(AppPresentation.dispatched(desktop));
        assertFalse(AppPresentation.dispatched(chat));
    }

    @Test public void helpersHaveFixedOrderAndSplitDispatchedFromDesktop() {
        Card desktop = card("\"id\":\"c1\",\"agent\":\"codex\",\"source\":\"desktop\",\"state\":\"working\",\"created_at\":1");
        Card review = card("\"id\":\"c2\",\"agent\":\"cc\",\"state\":\"done\",\"created_at\":2");
        Card draft = card("\"id\":\"c3\",\"agent\":\"codex\",\"state\":\"working\",\"created_at\":3");
        Card chat = card("\"id\":\"c4\",\"state\":\"done\",\"created_at\":4");
        List<AppPresentation.Helper> helpers = AppPresentation.helpers(
                Arrays.asList(desktop, review, draft, chat), Arrays.asList("tailor", "dsh", "cc"));
        assertEquals(3, helpers.size());
        assertEquals("codex", helpers.get(0).role);
        assertEquals("claude", helpers.get(1).role);
        assertEquals("tailor", helpers.get(2).role);
        assertEquals(Arrays.asList(draft), helpers.get(0).dispatched);
        assertEquals(Arrays.asList(desktop), helpers.get(0).desktop);
        assertEquals(draft, helpers.get(0).current(true));
        assertEquals("工作中", helpers.get(0).status(true));
        assertEquals("空闲", helpers.get(1).status(true));
        assertEquals("空闲", helpers.get(2).status(true));
    }

    @Test public void stateWordsOnlyForOutstandingWork() {
        assertEquals("进行中", AppPresentation.stateWord(card("\"id\":\"c1\",\"agent\":\"codex\",\"state\":\"working\""), true));
        assertEquals("等你确认", AppPresentation.stateWord(card("\"id\":\"c1\",\"agent\":\"codex\",\"state\":\"waiting\""), true));
        assertEquals("排队", AppPresentation.stateWord(card("\"id\":\"c1\",\"agent\":\"codex\",\"state\":\"working\",\"queued\":true"), true));
        assertEquals("", AppPresentation.stateWord(card("\"id\":\"c1\",\"agent\":\"codex\",\"state\":\"done\""), true));
        assertEquals("状态未知", AppPresentation.stateWord(card("\"id\":\"c1\",\"agent\":\"codex\",\"state\":\"working\""), false));
    }

    @Test public void draftsAreScopedToRuntimeAndTask() {
        assertNotEquals(AppPresentation.draftKey("one", "c1"), AppPresentation.draftKey("two", "c1"));
        assertNotEquals(AppPresentation.draftKey("one", "c1"), AppPresentation.draftKey("one", "c2"));
        assertNotEquals(AppPresentation.draftKey("one", null), AppPresentation.draftKey("one", "c1"));
        assertNotEquals(AppPresentation.draftKey("one", null, "ds"), AppPresentation.draftKey("one", null, "codex"));
        assertEquals(AppPresentation.draftKey("one", "c1", "ds"), AppPresentation.draftKey("one", "c1", "claude"));
    }
}
