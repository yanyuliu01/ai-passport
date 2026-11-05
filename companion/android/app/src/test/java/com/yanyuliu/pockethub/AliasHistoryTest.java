package com.yanyuliu.pockethub;

import org.junit.Test;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;
import static org.junit.Assert.*;

public class AliasHistoryTest {
    @Test public void malformed_source_is_never_changed_and_does_not_trigger_target_commit() {
        Map<String,String> source=new HashMap<>();source.put("alias","damaged original bytes");
        ChatHistory target=new ChatHistory();target.begin("current",null,"current",1);
        String before=target.json();AtomicInteger writes=new AtomicInteger();
        try { AliasHistory.adopt(source.get("alias"),target,()->{writes.incrementAndGet();return true;});fail(); }
        catch(IllegalArgumentException expected){assertEquals("damaged original bytes",source.get("alias"));}
        assertEquals(before,target.json());assertEquals(0,writes.get());
    }

    @Test public void failed_target_commit_never_deletes_source_or_applies_old_tombstones() {
        ChatHistory alias=new ChatHistory();alias.begin("old",null,"old",1);alias.eraseConversation("active");
        String original=alias.json();Map<String,String> disk=new HashMap<>();disk.put("alias",original);
        ChatHistory target=new ChatHistory();target.begin("live",null,"live",2);target.scope("live","active",true);
        assertFalse(AliasHistory.adopt(disk.get("alias"),target,()->false));
        assertEquals(original,disk.get("alias"));
        assertTrue(target.main("active").stream().anyMatch(item->item.id.equals("live:you")));
        assertTrue(target.main().stream().anyMatch(item->item.id.equals("old:you")));
    }

    @Test public void retry_is_idempotent_by_message_id() {
        ChatHistory alias=new ChatHistory();alias.begin("old",null,"old",1);
        ChatHistory target=new ChatHistory();AtomicInteger writes=new AtomicInteger();
        assertTrue(AliasHistory.adopt(alias.json(),target,()->{writes.incrementAndGet();return true;}));
        assertTrue(AliasHistory.adopt(alias.json(),target,()->{writes.incrementAndGet();return true;}));
        assertEquals(1,writes.get());assertEquals(2,target.main().size());
    }
}
