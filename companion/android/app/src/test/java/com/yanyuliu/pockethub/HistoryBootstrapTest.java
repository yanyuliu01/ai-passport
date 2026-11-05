package com.yanyuliu.pockethub;

import org.junit.Test;
import java.util.HashMap;
import java.util.Map;
import static org.junit.Assert.*;

public class HistoryBootstrapTest {
    @Test public void delayed_discovery_initializes_archive_and_new_message_can_be_saved() {
        ChatHistory saved=new ChatHistory();saved.begin("old",null,"old message",1);
        ChatHistory initial=new ChatHistory();
        HistoryBootstrap.Plan plan=HistoryBootstrap.plan(false,"",initial,"account-route",saved);
        assertNull(plan.previous);assertFalse(plan.merged);
        plan.current.begin("new",null,"new message",2);
        Map<String,String> preferences=new HashMap<>();HistoryWriteOrder writer=new HistoryWriteOrder();
        assertTrue(writer.write("account-key",writer.next(),plan.current.json(),true,
                (value,durable)->{preferences.put("account-key",value);return true;}));
        ChatHistory reopened=ChatHistory.restore(preferences.get("account-key"));
        assertTrue(reopened.main().stream().anyMatch(m->m.id.equals("old:you")));
        assertTrue(reopened.main().stream().anyMatch(m->m.id.equals("new:you")));
        assertFalse(preferences.containsKey(""));
    }

    @Test public void same_target_merges_pending_memory_without_overwriting_newer_status() {
        ChatHistory stored=new ChatHistory();stored.begin("same",null,"older",1);stored.finish("same",null,"old answer",false,null);
        ChatHistory live=new ChatHistory();live.begin("same",null,"newer",2);live.begin("pending",null,"waiting",3);
        HistoryBootstrap.Plan plan=HistoryBootstrap.plan(true,"route",live,"route",stored);
        assertNull(plan.previous);assertTrue(plan.merged);
        assertTrue(plan.current.main().stream().anyMatch(m->m.id.equals("same:you") && m.text.equals("newer") && m.status.equals("sending")));
        assertTrue(plan.current.main().stream().anyMatch(m->m.id.equals("pending:you")));
    }

    @Test public void different_target_keeps_previous_messages_in_their_original_archive() {
        ChatHistory live=new ChatHistory();live.begin("private",null,"account A",1);
        ChatHistory saved=new ChatHistory();saved.begin("other",null,"account B",2);
        HistoryBootstrap.Plan plan=HistoryBootstrap.plan(true,"account-A",live,"account-B",saved);
        assertEquals("account-A",plan.previousKey);assertNotNull(plan.previous);assertFalse(plan.merged);
        assertTrue(plan.previous.main().stream().anyMatch(m->m.id.equals("private:you")));
        assertTrue(plan.current.main().stream().noneMatch(m->m.id.equals("private:you")));
        Map<String,String> preferences=new HashMap<>();
        preferences.put(plan.previousKey,plan.previous.json());preferences.put("account-B",plan.current.json());
        assertTrue(ChatHistory.restore(preferences.get("account-A")).main().stream().anyMatch(m->m.id.equals("private:you")));
        assertFalse(preferences.containsKey(""));
    }

    @Test public void old_disk_history_and_early_messages_survive_switch_back_before_merge() {
        ChatHistory disk=new ChatHistory();disk.begin("old",null,"stored earlier",1);
        ChatHistory early=new ChatHistory();early.begin("early",null,"before init",2);
        Map<String,String> prefs=new HashMap<>();prefs.put("A",disk.json());
        HistoryWriteOrder writes=new HistoryWriteOrder();
        long pending=writes.next();
        // A is reselected before its background merge: load includes pending overlay.
        ChatHistory reselected=HistoryBootstrap.merge(prefs.get("A"),early);
        reselected.begin("after",null,"after switch",3);
        long newer=writes.next();
        assertTrue(writes.write("A",newer,reselected.json(),false,
                (value,durable)->{prefs.put("A",value);return true;}));
        // The older background merge cannot overwrite the newer complete snapshot.
        assertTrue(writes.write("A",pending,"",false,(value,durable)->{
            prefs.put("A",HistoryBootstrap.merge(prefs.get("A"),early).json());return true;
        }));
        ChatHistory reopened=ChatHistory.restore(prefs.get("A"));
        assertTrue(reopened.main().stream().anyMatch(m->m.id.equals("old:you")));
        assertTrue(reopened.main().stream().anyMatch(m->m.id.equals("early:you")));
        assertTrue(reopened.main().stream().anyMatch(m->m.id.equals("after:you")));
    }

    @Test public void malformed_old_archive_is_never_replaced_and_can_be_retried() {
        ChatHistory early=new ChatHistory();early.begin("early",null,"waiting",2);
        Map<String,String> prefs=new HashMap<>();prefs.put("A","not valid JSON");
        try { prefs.put("A",HistoryBootstrap.merge(prefs.get("A"),early).json());fail(); }
        catch(IllegalArgumentException expected){assertEquals("not valid JSON",prefs.get("A"));}
        ChatHistory recovered=new ChatHistory();recovered.begin("old",null,"restored",1);
        prefs.put("A",recovered.json());
        prefs.put("A",HistoryBootstrap.merge(prefs.get("A"),early).json());
        ChatHistory result=ChatHistory.restore(prefs.get("A"));
        assertTrue(result.main().stream().anyMatch(m->m.id.equals("old:you")));
        assertTrue(result.main().stream().anyMatch(m->m.id.equals("early:you")));
    }

    @Test public void malformed_rows_and_duplicate_ids_fail_closed() {
        ChatHistory early=new ChatHistory();early.begin("pending",null,"pending",1);
        for(String raw:new String[]{"{\"messages\":[null]}","{\"messages\":[{},{}]}",
                "{\"messages\":[{\"id\":\"same\"},{\"id\":\"same\"}]}",
                "{\"messages\":[],\"erased\":[1]}"}) {
            try {HistoryBootstrap.merge(raw,early);fail(raw);}catch(IllegalArgumentException expected){/* Original stays untouched. */}
        }
    }
}
