package com.yanyuliu.pockethub;

import org.junit.Test;
import java.util.HashMap;
import java.util.Map;
import static org.junit.Assert.*;

public class PendingHistoryStoreTest {
    private static final class Disk implements PendingHistoryStore.AtomicWrite {
        final Map<String,String> values=new HashMap<>();boolean fail;
        public boolean write(String key,String value,String remove){
            if(fail)return false;
            values.put(key,value);if(remove!=null)values.remove(remove);return true;
        }
    }
    private static ChatHistory one(String id){ChatHistory value=new ChatHistory();value.begin(id,null,id,1);return value;}

    @Test public void corrupted_original_and_pending_survive_restart_and_new_messages() {
        String key=ArtifactDownload.key("relay://"+"1".repeat(32)+"."+"a".repeat(32));
        Disk disk=new Disk();disk.values.put(key,"damaged original bytes");
        assertTrue(PendingHistoryStore.stage(key,one("early").json(),disk));
        PendingHistoryStore.Recovery recovered=PendingHistoryStore.recover(disk.values.get(key),disk.values.get(PendingHistoryStore.key(key)));
        assertTrue(recovered.blocked);assertTrue(recovered.pending);
        assertEquals("damaged original bytes",disk.values.get(key));
        assertTrue(recovered.history.main().stream().anyMatch(m->m.id.equals("early:you")));
        recovered.history.begin("new",null,"new",2);
        assertTrue(PendingHistoryStore.stage(key,recovered.history.json(),disk));
        recovered=PendingHistoryStore.recover(disk.values.get(key),disk.values.get(PendingHistoryStore.key(key)));
        assertTrue(recovered.blocked);
        assertTrue(recovered.history.main().stream().anyMatch(m->m.id.equals("early:you")));
        assertTrue(recovered.history.main().stream().anyMatch(m->m.id.equals("new:you")));
        assertEquals("damaged original bytes",disk.values.get(key));
    }

    @Test public void repaired_original_merges_atomically_and_failed_commit_retains_pending() {
        String key=ArtifactDownload.key("account A");Disk disk=new Disk();
        disk.values.put(key,one("old").json());assertTrue(PendingHistoryStore.stage(key,one("early").json(),disk));
        PendingHistoryStore.Recovery recovered=PendingHistoryStore.recover(disk.values.get(key),disk.values.get(PendingHistoryStore.key(key)));
        assertFalse(recovered.blocked);assertTrue(recovered.pending);
        disk.fail=true;assertFalse(PendingHistoryStore.finish(key,recovered.history.json(),disk));
        assertTrue(disk.values.containsKey(PendingHistoryStore.key(key)));
        assertTrue(ChatHistory.restore(disk.values.get(key)).main().stream().noneMatch(m->m.id.equals("early:you")));
        disk.fail=false;assertTrue(PendingHistoryStore.finish(key,recovered.history.json(),disk));
        assertFalse(disk.values.containsKey(PendingHistoryStore.key(key)));
        ChatHistory merged=ChatHistory.restore(disk.values.get(key));
        assertTrue(merged.main().stream().anyMatch(m->m.id.equals("old:you")));
        assertTrue(merged.main().stream().anyMatch(m->m.id.equals("early:you")));
    }

    @Test public void account_keys_are_distinct_and_failed_stage_is_reported() {
        String a=ArtifactDownload.key("relay://"+"1".repeat(32)+"."+"a".repeat(32));
        String b=ArtifactDownload.key("relay://"+"1".repeat(32)+"."+"b".repeat(32));
        Disk disk=new Disk();assertTrue(PendingHistoryStore.stage(a,one("A").json(),disk));
        assertNull(disk.values.get(PendingHistoryStore.key(b)));
        disk.fail=true;assertFalse(PendingHistoryStore.stage(b,one("B").json(),disk));
        assertNull(disk.values.get(PendingHistoryStore.key(b)));
        assertTrue(disk.values.containsKey(PendingHistoryStore.key(a)));
    }

    @Test public void damaged_pending_is_not_treated_as_empty_history() {
        try {PendingHistoryStore.recover(one("old").json(),"not JSON");fail();}
        catch(IllegalArgumentException expected) { /* Both inputs remain untouched. */ }
    }

    @Test public void old_snapshot_cannot_finalize_a_newer_recovery() {
        assertEquals(PendingHistoryStore.Mode.PENDING,PendingHistoryStore.mode(true,false,false,true));
        assertEquals(PendingHistoryStore.Mode.PENDING,PendingHistoryStore.mode(false,true,true,true));
        assertEquals(PendingHistoryStore.Mode.STALE,PendingHistoryStore.mode(false,false,false,true));
        assertEquals(PendingHistoryStore.Mode.FINALIZE,PendingHistoryStore.mode(false,false,true,true));
        assertEquals(PendingHistoryStore.Mode.ORIGINAL,PendingHistoryStore.mode(false,false,false,false));
    }
}
