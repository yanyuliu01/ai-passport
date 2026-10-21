package com.yanyuliu.pockethub;

import org.junit.Test;
import static org.junit.Assert.*;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;

public class ChatPlanTest {
    private static List<String> list(String... items) { return Arrays.asList(items); }

    @Test public void newMessagesAreAppendedWithoutTouchingOldRows() {
        ChatPlan plan = ChatPlan.of("0", list("a", "b"), list("x", "x"), "0", list("a", "b", "c", "d"), list("x", "x", "y", "y"));
        assertFalse(plan.rebuild);
        assertTrue(plan.replace.isEmpty());
        assertEquals(2, plan.appendFrom);
        assertFalse(plan.unchanged(4));
    }

    @Test public void aChangedRowIsReplacedInPlace() {
        ChatPlan plan = ChatPlan.of("0", list("a", "b"), list("x", "thinking"), "0", list("a", "b"), list("x", "bubble"));
        assertFalse(plan.rebuild);
        assertEquals(Collections.singletonList(1), plan.replace);
        assertEquals(2, plan.appendFrom);
    }

    @Test public void sameRowsAreUnchanged() {
        assertTrue(ChatPlan.of("0", list("a"), list("x"), "0", list("a"), list("x")).unchanged(1));
    }

    @Test public void windowMovesRemovalsAndReordersRebuild() {
        assertTrue(ChatPlan.of("0", list("a"), list("x"), "80", list("a"), list("x")).rebuild);
        assertTrue(ChatPlan.of("0", list("a", "b"), list("x", "x"), "0", list("a"), list("x")).rebuild);
        assertTrue(ChatPlan.of("0", list("a", "b"), list("x", "x"), "0", list("b", "a"), list("x", "x")).rebuild);
        // 第一次画（之前什么都没有）也算整个重画。
        assertTrue(ChatPlan.of("", list(), list(), "0", list("a"), list("x")).rebuild);
    }

    @Test public void sheSplitsMessagesHerselfWithALineOfDashes() {
        // 段落留在同一个气泡里；她单独写一行 --- 才另起一条。
        assertEquals(Arrays.asList("好。\n\n改完了。"), AppPresentation.bubbles("好。\n\n改完了。"));
        assertEquals(Arrays.asList("好了。", "另外说一句"), AppPresentation.bubbles("好了。\n\n---\n\n另外说一句"));
        assertEquals(Arrays.asList("要点：\n- 一\n- 二"), AppPresentation.bubbles("要点：\n- 一\n- 二"));
        assertTrue(AppPresentation.bubbles("").isEmpty());
        assertTrue(AppPresentation.bubbles(null).isEmpty());
    }

    @Test public void whileSheIsTypingOnlyFinishedMessagesShow() {
        assertTrue(AppPresentation.settledBubbles("好了，我看", false).isEmpty());
        assertEquals(Arrays.asList("好了。"), AppPresentation.settledBubbles("好了。\n---\n另外说", false));
        assertEquals(Arrays.asList("好了。", "另外"), AppPresentation.settledBubbles("好了。\n---\n另外\n---", false));
        assertEquals(Arrays.asList("好了。", "另外说一句"), AppPresentation.settledBubbles("好了。\n---\n另外说一句", true));
        assertTrue(AppPresentation.settledBubbles(null, false).isEmpty());
    }

    @Test public void theDeviceGetsPlainWords() {
        assertEquals("结论\n· 快一点\n\n另外", Markdown.plain("**结论**\n- 快一点\n---\n另外"));
        assertEquals("", Markdown.plain(null));
    }

    @Test public void composerStates() {
        assertEquals("idle", AppPresentation.composerState(false, false, false, false));
        assertEquals("ready", AppPresentation.composerState(true, false, false, false));
        // 正在做的事不挡补充要求：有字就能发。
        assertEquals("ready", AppPresentation.composerState(true, false, true, false));
        assertEquals("stop", AppPresentation.composerState(false, false, true, false));
        // 回话中有字也能发（插话）；没字时能停就给停，不能停就等。
        assertEquals("ready", AppPresentation.composerState(true, true, true, false));
        assertEquals("ready", AppPresentation.composerState(true, true, false, false));
        assertEquals("stop", AppPresentation.composerState(false, true, true, false));
        assertEquals("wait", AppPresentation.composerState(false, true, false, false));
        assertEquals("stopping", AppPresentation.composerState(false, true, true, true));
        // 已经停下了，不再显示「停止中」。
        assertEquals("idle", AppPresentation.composerState(false, false, false, true));
    }
}
