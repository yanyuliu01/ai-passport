package com.yanyuliu.pockethub;

import org.junit.Test;
import java.util.LinkedHashMap;
import java.util.Map;
import static org.junit.Assert.*;

public class PendingIntentsTest {
    private static final String A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    private static final String B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    private static final String CLIENT = "1234567890abcdef1234567890abcdef";

    static final class Disk implements PendingIntents.Store {
        final Map<String,Object> values = new LinkedHashMap<>(); boolean fail, memoryWriteOnFailure;
        public Map<String,?> all(){return new LinkedHashMap<>(values);}
        public boolean put(String key,String value){if(fail){if(memoryWriteOnFailure)values.put(key,value);return false;}values.put(key,value);return true;}
        public boolean remove(String key){if(fail)return false;values.remove(key);return true;}
    }
    private ChatHistory turn(String client,String text,double time) {
        ChatHistory h = new ChatHistory();h.begin(client,null,text,time);h.scope(client,"default",true);return h;
    }
    @Test public void small_durable_intent_recovers_without_rewriting_old_history() {
        Disk disk=new Disk();ChatHistory history=new ChatHistory();
        String old=new String(new char[500000]).replace('\0','x');
        history.begin("old",null,old,1);history.finish("old",null,"old reply",false,null);
        String originalArchive=history.json();
        history.begin(CLIENT,null,"new",2);history.scope(CLIENT,"default",true);
        assertTrue(PendingIntents.save(disk,A,CLIENT,history.intentSnapshot(CLIENT,"default")));
        String journal=(String)disk.values.values().iterator().next();assertTrue(journal.length()<2000);assertFalse(journal.contains(old));
        ChatHistory afterCrash=ChatHistory.restore(originalArchive);
        assertEquals(1,PendingIntents.recover(afterCrash,PendingIntents.records(disk,A)));
        assertTrue(afterCrash.main().stream().anyMatch(m->m.id.equals("old:you") && m.text.equals(old)));
        assertTrue(afterCrash.main().stream().anyMatch(m->m.id.equals("old:reply") && m.text.equals("old reply")));
        assertTrue(afterCrash.main().stream().anyMatch(m->m.id.equals(CLIENT+":you") && m.text.equals("new") && m.status.equals("interrupted")));
    }
    @Test public void failed_write_cannot_authorize_send_and_old_record_is_retained() {
        Disk disk=new Disk();ChatHistory intent=turn(CLIENT,"first",1).intentSnapshot(CLIENT,"default");
        disk.fail=true;assertFalse(PendingIntents.save(disk,A,CLIENT,intent));assertTrue(disk.values.isEmpty());
        disk.fail=false;assertTrue(PendingIntents.save(disk,A,CLIENT,intent));
        assertTrue(PendingIntents.save(disk,A,CLIENT,turn(CLIENT,"first",2).intentSnapshot(CLIENT,"default")));
        assertFalse(PendingIntents.save(disk,A,CLIENT,turn(CLIENT,"changed",2).intentSnapshot(CLIENT,"default")));
        assertTrue(((String)disk.values.values().iterator().next()).contains("first"));
    }
    @Test public void retry_never_confuses_a_failed_in_memory_commit_with_durable_storage() {
        Disk disk=new Disk();ChatHistory intent=turn(CLIENT,"recover me",1).intentSnapshot(CLIENT,"default");
        disk.fail=true;disk.memoryWriteOnFailure=true;
        assertFalse(PendingIntents.save(disk,A,CLIENT,intent));assertEquals(1,disk.values.size());
        assertFalse(PendingIntents.save(disk,A,CLIENT,intent));
        disk.fail=false;assertTrue(PendingIntents.save(disk,A,CLIENT,intent));
        assertEquals(1,PendingIntents.recover(new ChatHistory(),PendingIntents.records(disk,A)));
    }
    @Test public void acknowledgements_and_tombstones_do_not_duplicate_or_revive_messages() {
        Disk disk=new Disk();ChatHistory intent=turn(CLIENT,"hello",1).intentSnapshot(CLIENT,"default");
        assertTrue(PendingIntents.save(disk,A,CLIENT,intent));Map<String,String> captured=PendingIntents.records(disk,A);
        ChatHistory complete=turn(CLIENT,"hello",1);assertTrue(complete.reconcileAccepted(CLIENT,"server","default"));
        assertEquals(0,PendingIntents.recover(complete,captured));assertEquals(2,complete.main().size());
        PendingIntents.clearCovered(disk,A,complete,captured);assertTrue(disk.values.isEmpty());
        assertTrue(PendingIntents.save(disk,A,CLIENT,intent));captured=PendingIntents.records(disk,A);
        ChatHistory erased=new ChatHistory();erased.eraseConversation("default");
        assertEquals(0,PendingIntents.recover(erased,captured));assertTrue(erased.main().isEmpty());
        PendingIntents.clearCovered(disk,A,erased,captured);assertTrue(disk.values.isEmpty());
    }
    @Test public void cleanup_never_removes_uncovered_newer_other_account_or_corrupt_data() {
        Disk disk=new Disk();ChatHistory intent=turn(CLIENT,"hello",1).intentSnapshot(CLIENT,"default");
        assertTrue(PendingIntents.save(disk,A,CLIENT,intent));assertTrue(PendingIntents.save(disk,B,CLIENT,intent));
        Map<String,String> captured=PendingIntents.records(disk,A);
        PendingIntents.clearCovered(disk,A,new ChatHistory(),captured);assertEquals(2,disk.values.size());
        String key=A+":"+CLIENT;disk.values.put(key,turn(CLIENT,"newer",2).intentSnapshot(CLIENT,"default").json());
        PendingIntents.clearCovered(disk,A,turn(CLIENT,"hello",1),captured);assertEquals(2,disk.values.size());
        disk.values.put(key,"broken");captured=PendingIntents.records(disk,A);
        assertEquals(-1,PendingIntents.recover(new ChatHistory(),captured));assertTrue(PendingIntents.hasInvalid(captured));
        PendingIntents.clearCovered(disk,A,turn(CLIENT,"hello",1),captured);assertEquals("broken",disk.values.get(key));
        assertEquals(1,PendingIntents.records(disk,B).size());
    }
    @Test public void conflicting_conversation_keeps_both_the_archive_and_pending_record() {
        Disk disk=new Disk();ChatHistory intent=turn(CLIENT,"original",1).intentSnapshot(CLIENT,"default");
        assertTrue(PendingIntents.save(disk,A,CLIENT,intent));
        ChatHistory existing=turn(CLIENT,"other",2);existing.scope(CLIENT,"side-other",true);
        assertEquals(-1,PendingIntents.recover(existing,PendingIntents.records(disk,A)));
        assertEquals("other",existing.main("side-other").get(0).text);
        assertEquals(1,disk.values.size());
    }
    @Test public void crash_during_initial_snapshot_restores_the_original_side_chat() {
        Disk disk=new Disk();ChatHistory live=turn(CLIENT,"side message",1);
        String earlyArchive=live.json();live.scope(CLIENT,"side-chat",true);
        Attachment image=Attachment.from(Json.parseObject("{\"id\":\"a1234567890abcdef1234567890abcdef\",\"kind\":\"image\",\"mime\":\"image/png\"}"));
        assertNotNull(image);live.images(CLIENT,java.util.List.of(image));
        assertTrue(PendingIntents.save(disk,A,CLIENT,live.intentSnapshot(CLIENT,"side-chat")));
        Map<String,String> pending=PendingIntents.records(disk,A);
        ChatHistory restored=ChatHistory.restore(earlyArchive);
        assertEquals(1,PendingIntents.recover(restored,pending));
        assertTrue(restored.main("default").isEmpty());assertEquals(2,restored.main("side-chat").size());
        assertTrue(restored.main("side-chat").stream().anyMatch(m->m.text.equals("side message")));
        assertEquals(1,restored.main("side-chat").get(0).attachments.size());
        restored=ChatHistory.restore(earlyArchive);restored.eraseConversation("side-chat");
        assertEquals(1,PendingIntents.recover(restored,pending));
        assertTrue(restored.main("default").isEmpty());assertTrue(restored.main("side-chat").isEmpty());
    }
}
