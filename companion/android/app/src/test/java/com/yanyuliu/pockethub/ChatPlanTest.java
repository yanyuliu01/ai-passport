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

    @Test public void composerStates() {
        assertEquals("idle", AppPresentation.composerState(false, false, false, false));
        assertEquals("ready", AppPresentation.composerState(true, false, false, false));
        // 正在做的事不挡补充要求：有字就能发。
        assertEquals("ready", AppPresentation.composerState(true, false, true, false));
        assertEquals("stop", AppPresentation.composerState(false, false, true, false));
        // 回话中有字也不能再发；能停就给停，不能停就等。
        assertEquals("stop", AppPresentation.composerState(true, true, true, false));
        assertEquals("wait", AppPresentation.composerState(true, true, false, false));
        assertEquals("stopping", AppPresentation.composerState(false, true, true, true));
        // 已经停下了，不再显示「停止中」。
        assertEquals("idle", AppPresentation.composerState(false, false, false, true));
    }
}
