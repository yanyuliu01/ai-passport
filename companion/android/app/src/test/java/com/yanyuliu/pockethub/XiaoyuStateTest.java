package com.yanyuliu.pockethub;

import org.junit.Test;
import static org.junit.Assert.*;
import java.util.Arrays;
import java.util.List;

public class XiaoyuStateTest {
    private Card card(String fields) { return Card.from(Json.parseObject("{" + fields + "}")); }

    @Test public void queuedIdleAndDisconnectedHaveDistinctMeaning() {
        Card queued = card("\"id\":\"c1\",\"agent\":\"codex\",\"state\":\"working\",\"queued\":true");
        assertEquals("queued", XiaoyuState.state(queued, true));
        assertEquals("idle", XiaoyuState.pose(queued, true));
        assertEquals("unknown", XiaoyuState.state(queued, false));
        Card desktop = card("\"id\":\"c2\",\"source\":\"desktop\",\"state\":\"done\"");
        assertEquals("idle", XiaoyuState.state(desktop, true));
        assertEquals("本轮结束", XiaoyuState.label(desktop, true));
        Card stale = desktop.unknown();
        assertEquals("unknown", XiaoyuState.state(stale, true));
        assertTrue(XiaoyuState.roster(Arrays.asList(stale)).contains(stale));
    }

    @Test public void olderApprovalIsSelectedBeforeLimitAndSameAgentTasksRemainSeparate() {
        Card approval = card("\"id\":\"c1\",\"agent\":\"codex\",\"state\":\"waiting\",\"started_at\":1");
        Card a = card("\"id\":\"c2\",\"agent\":\"codex\",\"state\":\"working\",\"started_at\":2");
        Card b = card("\"id\":\"c3\",\"agent\":\"codex\",\"state\":\"working\",\"started_at\":3");
        Card c = card("\"id\":\"c4\",\"agent\":\"codex\",\"state\":\"working\",\"started_at\":4");
        Card d = card("\"id\":\"c5\",\"agent\":\"codex\",\"state\":\"working\",\"started_at\":5");
        List<Card> all = Arrays.asList(d, c, b, a, approval);
        assertEquals(5, XiaoyuState.roster(all).size());
        assertEquals(Arrays.asList(approval, a, b, c), CardViews.deviceTasks(all, 4, false));
    }

    @Test public void textAgentSelectionNeverOverridesPinnedTask() {
        assertTrue(CardViews.messageJson("hello", "client", null, "high", "codex").contains("\"agent\":\"codex\""));
        String pinned = CardViews.messageJson("hello", "client", "c1", "high", "claude");
        assertTrue(pinned.contains("\"pin\":true"));
        assertFalse(pinned.contains("\"agent\""));
    }

    @Test public void desktopApprovalsAreIndependentFromReadonlyConversation() {
        Card.Approval yes = Card.Approval.from(Json.parseObject("{\"id\":\"d1\",\"source\":\"desktop\",\"remote_actionable\":true}"));
        Card.Approval no = Card.Approval.from(Json.parseObject("{\"id\":\"d2\",\"source\":\"desktop\",\"remote_actionable\":false}"));
        assertTrue(yes.actionable(true)); assertFalse(no.actionable(true));
        assertFalse(new Card.Approval("a", "c", "", "", "", 0).actionable(true));
        String heartbeat = BuddyProtocol.heartbeat(0, 0, 0, "", null, 0, 0, new BuddyProtocol.Prompt("d2", "电脑审批", "电脑端确认", false));
        assertTrue(heartbeat.contains("\"actionable\":false"));
        assertFalse(BuddyProtocol.hubAckHasDesktopApproval("{\"desktopApproval\":false}"));
        assertTrue(BuddyProtocol.hubAckHasDesktopApproval("{\"desktopApproval\":true}"));
    }

    @Test public void truncatedOperationCanStillBeApprovedOnHardware() {
        BuddyProtocol.Prompt full = new BuddyProtocol.Prompt("d1", "电脑 · Bash", "git status", true);
        assertTrue(full.actionable);
        BuddyProtocol.Prompt cut = new BuddyProtocol.Prompt("d2", "电脑 · Bash", "操作".repeat(100), true);
        assertTrue(cut.actionable);
        assertTrue(cut.hint.contains("完整内容在手机看"));
        assertTrue(BuddyProtocol.utf8Length(cut.hint) <= BuddyProtocol.HINT_MAX);
        assertFalse(BuddyProtocol.heartbeat(0, 0, 0, "", null, 0, 0, cut).contains("\"actionable\""));
        // 来源说了不能回答的，截不截短都不能回答。
        BuddyProtocol.Prompt readOnly = new BuddyProtocol.Prompt("d3", "电脑 · Bash", "操作".repeat(100), false);
        assertFalse(readOnly.actionable);
        assertTrue(BuddyProtocol.heartbeat(0, 0, 0, "", null, 0, 0, readOnly).contains("\"actionable\":false"));
    }

    @Test public void sameApprovalIdOnTwoComputersHasTwoRoutesAndFitsBlePrompt() {
        Card.Approval raw = Card.Approval.from(Json.parseObject("{\"id\":\"a1\",\"card\":\"c1\",\"created_at\":1791854400,\"source\":\"desktop\",\"remote_actionable\":true}"));
        Card.Approval one = raw.origin("http://one:7799", "电脑一");
        Card.Approval two = raw.origin("http://two:7799", "电脑二");
        assertNotEquals(one.id, two.id); assertEquals("a1", one.remoteId); assertEquals("a1", two.remoteId);
        assertEquals("http://one:7799", one.runtimeUrl); assertEquals("电脑二", two.runtimeName);
        String wireId = one.id + "." + one.createdAt + ".0";
        assertTrue(wireId.length() <= BuddyProtocol.PROMPT_ID_MAX);
        assertTrue(BuddyProtocol.heartbeat(0, 0, 0, "", null, 0, 0, new BuddyProtocol.Prompt(wireId, "电脑一 · tool", "原文", true)).contains(wireId));
        Card.Approval expired = Card.Approval.from(Json.parseObject("{\"id\":\"a2\",\"source\":\"desktop\",\"remote_actionable\":true,\"expires_at\":1}"));
        assertFalse(expired.actionable(true));
    }
}
