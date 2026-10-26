package com.yanyuliu.pockethub;

import org.junit.Test;
import static org.junit.Assert.*;
import java.util.List;
import java.util.Map;

public class ApprovalSyncTest {
    private static Map<String,Object> page(String epoch, int seq, String rows) {
        return Json.parseObject("{\"epoch\":\""+epoch+"\",\"seq\":"+seq+",\"approvals\":["+rows+"]}");
    }
    private static String row(String id,int seq,String status) {
        return "{\"id\":\""+id+"\",\"seq\":"+seq+",\"status\":\""+status+"\",\"created_at\":1}";
    }
    @Test public void deltasKeepOtherPendingApprovalsAndRemoveAnsweredOnes() throws Exception {
        ApprovalSync sync=new ApprovalSync();
        assertEquals(2,sync.apply(page("e",2,row("a",1,"pending")+","+row("b",2,"pending")),"u","n",0).size());
        List<Card.Approval> pending=sync.apply(page("e",3,row("a",3,"allowed")),"u","n",0);
        assertEquals(1,pending.size());assertEquals("b",pending.get(0).remoteId);
        assertEquals(1,sync.apply(page("e",4,""),"u","n",0).size());
        assertTrue(sync.path().contains("/v1/sync?"));assertTrue(sync.path().contains("after=4"));
    }
    @Test public void reorderedAndDuplicatePendingCannotResurrectAnAnsweredApproval() throws Exception {
        ApprovalSync sync=new ApprovalSync();sync.apply(page("e",1,row("a",1,"pending")),"u","n",0);
        assertTrue(sync.apply(page("e",3,row("a",3,"denied")+","+row("a",2,"pending")),"u","n",0).isEmpty());
        assertTrue(sync.apply(page("e",4,row("a",2,"pending")),"u","n",0).isEmpty());
    }
    @Test public void epochAndSnapshotReplacePendingStateAndRetainOrigin() throws Exception {
        ApprovalSync sync=new ApprovalSync();sync.apply(page("e",2,row("a",2,"pending")),"u","n",0);
        List<Card.Approval> pending=sync.apply(page("new",1,row("b",1,"pending")),"other","Other",0);
        assertEquals(1,pending.size());assertEquals("other",pending.get(0).runtimeUrl);
        Map<String,Object> snapshot=page("new",5,"");snapshot.put("reset",true);
        assertTrue(sync.apply(snapshot,"other","Other",0).isEmpty());
    }
}
