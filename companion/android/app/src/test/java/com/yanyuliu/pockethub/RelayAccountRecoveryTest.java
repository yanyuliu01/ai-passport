package com.yanyuliu.pockethub;

import java.io.IOException;
import java.lang.reflect.Field;
import java.util.*;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import org.junit.Test;
import static org.junit.Assert.*;

public class RelayAccountRecoveryTest {
    @SuppressWarnings("unchecked")
    @Test public void refreshPreservesPendingRequestIdentityAndRuntime() throws Exception {
        RelayCrypto host=new RelayCrypto("computer",null),phone=new RelayCrypto("phone",null);
        String account="a".repeat(32),space="b".repeat(32),runtime="c".repeat(32),clientId="d".repeat(32);
        Map<String,Object> oldRelay=RelayCrypto.map("url","ws://127.0.0.1:1","room",account,"token","o".repeat(43));
        Map<String,Object> newRelay=RelayCrypto.map("url","ws://127.0.0.1:1","room",account,"token","n".repeat(43));
        Map<String,Object> pending=RelayCrypto.map("request","same-request","method","POST","path","/v1/messages",
                "runtime_id",runtime,"body",RelayCrypto.map("client_id",clientId));
        Map<String,Object> saved=RelayCrypto.map("account",account,"device",host.identity,"space",space,"seed",phone.seed,
                "name","phone","paired",true,"runtime_id",runtime,"relay",oldRelay,"pending_writes",new ArrayList<>(Collections.singletonList(pending)));
        Map<String,Object> fresh=new LinkedHashMap<>(saved);fresh.put("relay",newRelay);fresh.remove("pending_writes");fresh.remove("runtime_id");
        Map<String,Object> merged=RelayAccountRecovery.merge(account,saved,fresh);
        assertSame(newRelay,merged.get("relay"));assertSame(oldRelay,saved.get("relay"));assertEquals(runtime,merged.get("runtime_id"));
        try(RelayConnection rebuilt=new RelayConnection(merged,new RelayConnection.Observer(){
            public void save(Map<String,Object> state){}public void ready(String url,String name){}public void state(String state){}
        })){
            Field timer=RelayConnection.class.getDeclaredField("timer");timer.setAccessible(true);
            ScheduledExecutorService executor=(ScheduledExecutorService)timer.get(rebuilt);executor.shutdownNow();executor.awaitTermination(5,TimeUnit.SECONDS);
            Field writes=RelayConnection.class.getDeclaredField("durableWrites");writes.setAccessible(true);
            Map<String,Map<String,Object>> restored=(Map<String,Map<String,Object>>)writes.get(rebuilt);
            assertEquals(1,restored.size());assertSame(pending,restored.get("same-request"));
            assertEquals(clientId,((Map<?,?>)restored.get("same-request").get("body")).get("client_id"));
            Field closed=RelayConnection.class.getDeclaredField("closed");closed.setAccessible(true);closed.set(rebuilt,true);
            Field rejected=RelayConnection.class.getDeclaredField("authRejectedAt");rejected.setAccessible(true);rejected.set(rebuilt,System.currentTimeMillis());
            assertFalse(rebuilt.canRecover(newRelay));assertTrue(rebuilt.canRecover(oldRelay));
            rejected.set(rebuilt,System.currentTimeMillis()-61000);assertTrue(rebuilt.canRecover(newRelay));
        }
    }
    @Test public void changedAccountSeedSpaceOrPeerCannotReusePendingWrites() throws Exception {
        RelayCrypto host=new RelayCrypto("computer",null),other=new RelayCrypto("other",null),phone=new RelayCrypto("phone",null);
        String account="a".repeat(32);
        Map<String,Object> saved=RelayCrypto.map("account",account,"device",host.identity,"space","b".repeat(32),"seed",phone.seed,
                "pending_writes",Collections.singletonList(RelayCrypto.map("request","keep")));
        for(String key:Arrays.asList("account","space","seed","device")){
            Map<String,Object> fresh=new LinkedHashMap<>(saved);
            fresh.put(key,key.equals("device")?other.identity:"changed");
            try{RelayAccountRecovery.merge(account,saved,fresh);fail("accepted changed "+key);}catch(IOException expected){}
        }
        assertEquals("keep",((Map<?,?>)((List<?>)saved.get("pending_writes")).get(0)).get("request"));
    }
}
