package com.yanyuliu.pockethub;

import java.lang.reflect.Field;
import java.util.*;
import java.util.concurrent.ScheduledExecutorService;
import okhttp3.Request;
import okhttp3.WebSocket;
import okio.ByteString;
import org.junit.Test;
import static org.junit.Assert.*;

public class RelayNetworkChangeTest {
    private static final class Socket implements WebSocket {
        boolean cancelled; int sends;
        public Request request(){return new Request.Builder().url("http://127.0.0.1/").build();}
        public long queueSize(){return 0;}
        public boolean send(String value){sends++;return true;}
        public boolean send(ByteString value){return true;}
        public boolean close(int code,String reason){return true;}
        public void cancel(){cancelled=true;}
    }
    private static Object field(Object target,String name)throws Exception{Field f=target.getClass().getDeclaredField(name);f.setAccessible(true);return f.get(target);}
    private static void field(Object target,String name,Object value)throws Exception{Field f=target.getClass().getDeclaredField(name);f.setAccessible(true);f.set(target,value);}

    @SuppressWarnings("unchecked")
    @Test public void networkChangeInvalidatesOldTransportButKeepsSamePendingWrite() throws Exception {
        RelayCrypto computer=new RelayCrypto("computer",null),phone=new RelayCrypto("phone",null);
        Map<String,Object> config=RelayCrypto.map("device",computer.identity,"space","a".repeat(32),"name","phone",
                "seed",phone.seed,"paired",true,"runtime_id","b".repeat(32),
                "relay",RelayCrypto.map("url","ws://127.0.0.1:1","room","c".repeat(32),"token","r".repeat(43)));
        try(RelayConnection link=new RelayConnection(config,new RelayConnection.Observer(){
            public void save(Map<String,Object> state){}public void ready(String url,String name){}public void state(String state){}
        })){
            ScheduledExecutorService timer=(ScheduledExecutorService)field(link,"timer");timer.shutdownNow();timer.awaitTermination(5,java.util.concurrent.TimeUnit.SECONDS);
            Socket socket=new Socket();field(link,"socket",socket);field(link,"opened",true);
            PeerAvailability availability=(PeerAvailability)field(link,"availability");availability.observed();assertTrue(link.online());
            Map<String,Map<String,Object>> pending=(Map<String,Map<String,Object>>)field(link,"durableWrites");
            Map<String,Map<String,Object>> packets=(Map<String,Map<String,Object>>)field(link,"packets");
            Map<String,long[]> sent=(Map<String,long[]>)field(link,"sent");
            Map<String,Object> request=RelayCrypto.map("request","r1","body",RelayCrypto.map("client_id","d".repeat(32)));
            pending.put("r1",request);packets.put("p1",RelayCrypto.map("id","p1"));sent.put("p1",new long[]{1,1});
            link.networkChanged();
            assertTrue(socket.cancelled);assertNull(field(link,"socket"));assertFalse(link.online());
            assertFalse(availability.online(true));assertSame(request,pending.get("r1"));assertTrue(packets.containsKey("p1"));
            assertTrue(sent.isEmpty());
        }
    }

    @Test public void requestsWaitForFreshAuthenticatedPeerBeforeReplay() throws Exception {
        RelayCrypto computer=new RelayCrypto("computer",null),phone=new RelayCrypto("phone",null);
        Map<String,Object> config=RelayCrypto.map("device",computer.identity,"space","a".repeat(32),"name","phone",
                "seed",phone.seed,"paired",true,"runtime_id","b".repeat(32),
                "relay",RelayCrypto.map("url","ws://127.0.0.1:1","room","c".repeat(32),"token","r".repeat(43)));
        try(RelayConnection link=new RelayConnection(config,new RelayConnection.Observer(){
            public void save(Map<String,Object> state){}public void ready(String url,String name){}public void state(String state){}
        })){
            ScheduledExecutorService timer=(ScheduledExecutorService)field(link,"timer");timer.shutdownNow();timer.awaitTermination(5,java.util.concurrent.TimeUnit.SECONDS);
            Socket socket=new Socket();field(link,"socket",socket);field(link,"opened",true);
            java.lang.reflect.Method send=RelayConnection.class.getDeclaredMethod("send",String.class,Map.class,boolean.class);send.setAccessible(true);
            java.lang.reflect.Method tick=RelayConnection.class.getDeclaredMethod("tick");tick.setAccessible(true);
            send.invoke(link,"request",RelayCrypto.map("request","same-id"),true);
            tick.invoke(link);assertEquals(0,socket.sends);
            ((PeerAvailability)field(link,"availability")).observed();assertTrue(link.online());
            tick.invoke(link);assertEquals(1,socket.sends);
        }
    }
}
