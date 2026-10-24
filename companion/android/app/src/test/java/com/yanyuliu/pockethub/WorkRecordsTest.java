package com.yanyuliu.pockethub;

import org.junit.Test;
import java.time.ZoneId;
import static org.junit.Assert.*;

public class WorkRecordsTest {
    private static final double NOW = 1792623600;
    private final ZoneId zone = ZoneId.of("Asia/Shanghai");
    private Card card(String fields) { return Card.from(Json.parseObject("{\"id\":\"c1\"," + fields + "}")); }

    @Test public void screenshotStaleDesktopRecordsNeverBecomeRunningOrCompleted() {
        Card old = card("\"state\":\"unknown\",\"source\":\"desktop\",\"stale\":true,\"observed_at\":1792381140");
        assertTrue(old.outstanding());
        assertEquals(WorkRecords.Group.UNCERTAIN, WorkRecords.group(old, true, NOW));
        assertTrue(WorkRecords.status(old, true, NOW, zone).startsWith("状态待确认 · 上次记录 "));
        assertFalse(WorkRecords.status(old, true, NOW, zone).contains("进展"));
        Card staleWaiting = card("\"state\":\"waiting\",\"source\":\"desktop\",\"stale\":true");
        assertEquals(WorkRecords.Group.UNCERTAIN, WorkRecords.group(staleWaiting, true, NOW));
    }

    @Test public void realActivityAndApprovalStayVisibleButExpiredSnapshotsDoNot() {
        Card active = card("\"state\":\"working\",\"source\":\"desktop\",\"observed_at\":" + (NOW - 2));
        assertEquals(WorkRecords.Group.RUNNING, WorkRecords.group(active, true, NOW));
        assertEquals(WorkRecords.Group.UNCERTAIN, WorkRecords.group(active, true, NOW + 301));
        assertEquals(WorkRecords.Group.UNCERTAIN, WorkRecords.group(active, false, NOW));
        Card waiting = card("\"state\":\"waiting\",\"agent\":\"codex\"");
        assertEquals(WorkRecords.Group.WAITING, WorkRecords.group(waiting, true, NOW));
        Card working = card("\"state\":\"working\",\"agent\":\"codex\"");
        assertEquals(WorkRecords.Group.RUNNING, WorkRecords.group(working, true, NOW));
        assertEquals("等待第一条进展", WorkRecords.status(working, true, NOW, zone));
    }

    @Test public void queuedAdditionDoesNotInventProgressAndTerminalStatesKeepTheirMeaning() {
        Card queued = card("\"state\":\"working\",\"agent\":\"codex\",\"activity\":[{\"at\":" + NOW + ",\"kind\":\"relay_queued\",\"text\":\"extra\"}]");
        assertEquals("等待第一条进展", WorkRecords.status(queued, true, NOW, zone));
        Card ended = card("\"state\":\"turn_ended\",\"source\":\"desktop\"");
        assertEquals(WorkRecords.Group.HISTORY, WorkRecords.group(ended, true, NOW));
        assertEquals("本轮结束", WorkRecords.status(ended, true, NOW, zone));
        assertEquals("未成功", WorkRecords.status(card("\"state\":\"failed\""), true, NOW, zone));
    }

    @Test public void technicalFallbackIsNotTheTitleAndSourceExplainsTheDirectory() {
        Card old = card("\"title\":\"codex workdir 25c33f\",\"title_source\":\"fallback\",\"source\":\"desktop\",\"agent\":\"codex\",\"project\":\"workdir\",\"origin_name\":\"Windows\"");
        assertEquals("未命名 Codex 会话", WorkRecords.title(old));
        assertEquals("Windows · Codex · 目录：workdir", WorkRecords.source(old, "Mac"));
        Card named = card("\"title\":\"修复图片上传\",\"title_source\":\"upstream\",\"agent\":\"cc\",\"worker_cwd\":\"C:\\\\repos\\\\ai-passport\"");
        assertEquals("修复图片上传", WorkRecords.title(named));
        assertEquals("Mac · Claude · ai-passport", WorkRecords.source(named, "Mac"));
    }

    @Test public void sharedAliasAndClassificationNeverReplaceNativeBindingOrInventProgress() {
        Card c=card("\"title\":\"统一名称\",\"original_title\":\"native original\",\"title_source\":\"owner_alias\",\"directory_id\":\"task-source\",\"metadata_revision\":3,\"shared_pinned\":true,\"shared_archived\":true,\"display_project\":\"项目分类\",\"agent\":\"codex\",\"worker_session\":\"native-session\",\"worker_cwd\":\"/actual/workdir\",\"native_sources\":[\"native-source\"],\"state\":\"done\"");
        assertEquals("统一名称",WorkRecords.title(c));assertEquals("native original",c.originalTitle);
        assertEquals("/actual/workdir",c.workerCwd);assertEquals("native-session",c.workerSession);
        assertTrue(c.sharedPinned);assertTrue(c.sharedArchived);assertEquals(3,c.metadataRevision);
        assertEquals("Mac · Codex · 项目分类",WorkRecords.source(c,"Mac"));
        assertEquals(WorkRecords.Group.HISTORY,WorkRecords.group(c,true,NOW));
        assertEquals("native-source",c.nativeSources.get(0));
    }

    @Test public void workListShowsOnlyHandedOffWorkInThreeSections() {
        Card observed = card("\"state\":\"working\",\"source\":\"desktop\",\"observed_at\":" + (NOW - 2));
        assertFalse(WorkRecords.listed(observed));
        Card chat = card("\"state\":\"done\"");
        assertFalse(WorkRecords.listed(chat));
        Card working = card("\"state\":\"working\",\"agent\":\"codex\"");
        assertTrue(WorkRecords.listed(working));
        assertEquals(WorkRecords.Section.RUNNING, WorkRecords.section(working, true, NOW));
        assertEquals(WorkRecords.Section.RUNNING, WorkRecords.section(working, false, NOW));
        assertEquals(WorkRecords.Section.WAITING, WorkRecords.section(card("\"state\":\"waiting\",\"agent\":\"codex\""), true, NOW));
        assertEquals(WorkRecords.Section.ENDED, WorkRecords.section(card("\"state\":\"cancelled\",\"agent\":\"cc\""), true, NOW));
    }
}
