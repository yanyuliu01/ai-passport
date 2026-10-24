package com.yanyuliu.pockethub;

import org.junit.Test;
import static org.junit.Assert.*;
import java.util.Arrays;
import java.util.List;

public class AppPresentationTest {
    private Card card(String fields) { return Card.from(Json.parseObject("{" + fields + "}")); }

    @Test public void tasksUseLocalCreationDayAndKeepPinnedSeparate() {
        java.time.ZoneId zone = java.time.ZoneId.of("Asia/Shanghai");
        long now = java.time.ZonedDateTime.of(2026, 10, 14, 0, 5, 0, 0, zone).toInstant().toEpochMilli();
        Card today = card("\"id\":\"c3\",\"agent\":\"codex\",\"created_at\":" + now / 1000);
        Card yesterday = card("\"id\":\"c2\",\"agent\":\"codex\",\"created_at\":" + (now / 1000 - 600));
        Card unknown = card("\"id\":\"c1\",\"agent\":\"codex\"");
        java.util.Set<String> pins = new java.util.HashSet<>(Arrays.asList("c2"));
        List<AppPresentation.TaskSection> groups = AppPresentation.taskSections(Arrays.asList(today, yesterday, unknown), pins,
                new java.util.HashSet<>(), now, zone);
        assertEquals("置顶", groups.get(0).title); assertEquals("今天", groups.get(1).title);
        assertEquals("时间未知", groups.get(2).title);
        assertEquals("昨天", AppPresentation.taskDay(yesterday, now, zone));
        assertEquals("23:55", AppPresentation.taskTime(yesterday, false, zone));
        assertEquals("2026-10-13 23:55", AppPresentation.taskTime(yesterday, true, zone));
        assertEquals("今天", AppPresentation.taskDay(today, now - 300000, zone));
        assertEquals("昨天", AppPresentation.taskDay(today, now + 86400000, zone));
    }

    @Test public void calendarDaysHandleDstYearBoundariesAndStateChanges() {
        java.time.ZoneId zone = java.time.ZoneId.of("America/New_York");
        long now = java.time.ZonedDateTime.of(2026, 3, 9, 0, 5, 0, 0, zone).toInstant().toEpochMilli();
        long created = java.time.ZonedDateTime.of(2026, 3, 8, 0, 5, 0, 0, zone).toEpochSecond();
        Card working = card("\"id\":\"c1\",\"agent\":\"codex\",\"state\":\"working\",\"created_at\":" + created);
        Card done = card("\"id\":\"c1\",\"agent\":\"codex\",\"state\":\"done\",\"started_at\":" + now / 1000 + ",\"created_at\":" + created);
        assertEquals("昨天", AppPresentation.taskDay(working, now, zone));
        assertEquals(AppPresentation.taskDay(working, now, zone), AppPresentation.taskDay(done, now, zone));
        assertEquals("2026-03-08", AppPresentation.taskDay(done, now + 3 * 86400000L, zone));
        Card old = card("\"id\":\"c0\",\"agent\":\"codex\",\"created_at\":1735689600");
        assertEquals("2024-12-31", AppPresentation.taskDay(old, now, zone));
    }

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
        assertEquals("在做", helpers.get(0).status(true));
        assertEquals("空闲", helpers.get(1).status(true));
        assertEquals("空闲", helpers.get(2).status(true));
    }

    @Test public void homeAndSidebarShareOneHelperMode() {
        // Codex only busy in a session on the computer: not idle anywhere (the home chip used to say idle).
        Card desktop = card("\"id\":\"c1\",\"agent\":\"codex\",\"source\":\"desktop\",\"state\":\"working\"");
        Card ended = card("\"id\":\"c2\",\"agent\":\"cc\",\"source\":\"desktop\",\"state\":\"done\"");
        Card asking = card("\"id\":\"c3\",\"agent\":\"tailor\",\"state\":\"waiting\"");
        List<AppPresentation.Helper> helpers = AppPresentation.helpers(
                Arrays.asList(desktop, ended, asking), Arrays.asList("codex", "cc", "tailor"));
        assertEquals("desktop", helpers.get(0).mode(true));
        assertEquals("电脑上在跑", helpers.get(0).status(true));
        assertEquals("idle", helpers.get(1).mode(true));
        assertEquals("waiting", helpers.get(2).mode(true));
        assertEquals("等你确认", helpers.get(2).status(true));
        // Offline: nothing is claimed to be running.
        assertEquals("idle", helpers.get(0).mode(false));
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

    @Test public void connectionHasThreeWordsAndEmptySideChatsAreNotListed() {
        assertEquals("需要配对", AppPresentation.connection(false, false));
        assertEquals("已连接", AppPresentation.connection(true, true));
        assertEquals("连不上，正在重试", AppPresentation.connection(true, false));
        java.util.Map<String, Object> empty = Json.parseObject("{\"id\":\"s1\",\"kind\":\"side\",\"title\":\"新旁聊\",\"title_source\":\"pending\"}");
        assertFalse(AppPresentation.listedSideChat(empty, "default"));
        assertTrue(AppPresentation.listedSideChat(empty, "s1"));
        assertTrue(AppPresentation.listedSideChat(Json.parseObject("{\"id\":\"s2\",\"kind\":\"side\",\"title_source\":\"request\"}"), "default"));
        assertFalse(AppPresentation.listedSideChat(Json.parseObject("{\"id\":\"s3\",\"kind\":\"side\",\"archived\":true}"), "s3"));
        assertFalse(AppPresentation.listedSideChat(Json.parseObject("{\"id\":\"default\",\"kind\":\"main\"}"), "default"));
    }
}
