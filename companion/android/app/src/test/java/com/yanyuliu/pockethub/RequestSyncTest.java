package com.yanyuliu.pockethub;

import org.junit.Test;
import static org.junit.Assert.*;
import java.io.IOException;
import java.util.Arrays;
import java.util.Map;

public class RequestSyncTest {
    @Test public void newerEmptyPartialRetractsThePreviousText() throws Exception {
        RequestSync sync = new RequestSync();sync.open("m1","default",1,accepted());
        Map<String,Object> partial=page("m1",2,"running","default");
        Json.object(Json.list(partial,"requests").get(0)).put("partial","old fragment");
        sync.apply(partial,Arrays.asList("m1"),1);
        Map<String,Object> empty=page("m1",3,"running","default");
        Json.object(Json.list(empty,"requests").get(0)).put("partial","");
        sync.apply(empty,Arrays.asList("m1"),1);
        assertEquals("",sync.next("m1",1,"2",1).get("partial"));
    }
    @Test public void anotherRuntimeOrSubjectCannotBeAppliedToTheConfirmedSource() throws Exception {
        Map<String,Object> page = Json.parseObject("{\"runtime_id\":\"r\",\"subject_id\":\"s\"}");
        RequestSync.verifySource(page, "r", "s");
        for (String[] expected : new String[][]{{"other", "s"}, {"r", "other"}}) {
            try { RequestSync.verifySource(page, expected[0], expected[1]); fail(); }
            catch (IOException mismatch) { assertTrue(mismatch.getMessage().contains("原记录")); }
        }
    }
    private static Map<String, String> accepted() {
        return Presentation.snapshot("{\"id\":\"m1\",\"conversation\":\"default\",\"rev\":1,\"status\":\"queued\"}");
    }
    private static Map<String, Object> page(String id, int rev, String state, String conversation) {
        return Json.parseObject("{\"requests\":[{\"id\":\"" + id + "\",\"conversation\":\"" + conversation
                + "\",\"rev\":" + rev + ",\"status\":\"" + state + "\",\"reply\":\"same canonical reply\"}]}");
    }
    @Test public void terminalReplyRejectsOlderAndLateNonterminalUpdates() throws Exception {
        RequestSync sync = new RequestSync(); sync.open("m1", "default", 1, accepted());
        sync.apply(page("m1", 4, "done", "default"), Arrays.asList("m1"), 1);
        Map<String,Object> duplicate = page("m1", 4, "done", "default");
        Json.object(Json.list(duplicate,"requests").get(0)).put("reply","different text at the same revision");
        sync.apply(duplicate, Arrays.asList("m1"), 1);
        sync.apply(page("m1", 3, "running", "default"), Arrays.asList("m1"), 1);
        sync.apply(page("m1", 5, "running", "default"), Arrays.asList("m1"), 1);
        Map<String,String> result = sync.next("m1", 1, "1", 1);
        assertEquals("done", result.get("status")); assertEquals("same canonical reply", result.get("reply"));
        sync.close("m1", 1); assertTrue(sync.ids(1).isEmpty());
    }
    @Test public void connectionGenerationsAndConversationScopesStaySeparate() throws Exception {
        RequestSync sync = new RequestSync(); sync.open("m1", "default", 1, accepted()); sync.open("m1", "side", 2, accepted());
        sync.apply(page("m1", 4, "done", "default"), Arrays.asList("m1"), 1);
        assertEquals("queued", sync.next("m1", 2, "0", 1).get("status"));
        sync.close("m1", 1); assertEquals(1, sync.ids(2).size());
        try { sync.apply(page("m1", 4, "done", "default"), Arrays.asList("m1"), 2); fail(); }
        catch (IOException expected) { assertTrue(expected.getMessage().contains("对话")); }
    }
    @Test public void missingRequestsAreNotResubmittedAndRollbackAllowsLegacyReads() throws Exception {
        RequestSync sync = new RequestSync(); sync.open("m1", "default", 1, accepted());
        sync.apply(Json.parseObject("{\"requests\":[],\"requests_missing\":[\"m1\"]}"), Arrays.asList("m1"), 1);
        try { sync.next("m1", 1, "1", 1); fail(); } catch (IOException expected) { assertTrue(expected.getMessage().contains("原编号")); }
        sync.open("m1", "default", 1, accepted());
        sync.apply(Json.parseObject("{\"messages\":[]}"), Arrays.asList("m1"), 1);
        assertEquals("true", sync.next("m1", 1, "1", 1).get("legacy"));
    }
    @Test public void overflowAndDeferredRequestsAreRotatedWithoutBeingDropped() throws Exception {
        RequestSync sync = new RequestSync();
        for (int index=0;index<40;index++) sync.open("m"+index,"default",1,accepted());
        assertEquals(32,sync.ids(1).size()); assertTrue(sync.more(1));
        sync.apply(page("m0",2,"running","default"),sync.ids(1),1);
        assertEquals("m1",sync.ids(1).get(0));
    }
}
