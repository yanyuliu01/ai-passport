package com.yanyuliu.pockethub;

import org.junit.Test;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.*;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;
import okhttp3.Request;
import okhttp3.WebSocket;
import okio.ByteString;
import static org.junit.Assert.*;

public class RelayReceiptTest {
    private static final class Observer implements RelayConnection.Observer {
        Map<String,Object> saved,uncertain; Consumer<Boolean> completion; boolean fail;
        public void save(Map<String,Object> state) {
            if (fail) throw new IllegalStateException("storage failure");
            saved = Json.parseObject(RelayCrypto.json(state));
        }
        public void ready(String url,String name) {} public void state(String message) {}
        public void accepted(String url,Map<String,Object> receipt,Consumer<Boolean> done) { completion = done; }
        public void uncertain(String url,Map<String,Object> receipt,Consumer<Boolean> done) { uncertain=receipt;done.accept(true); }
    }
    private static Object field(Object object,String name) throws Exception {
        Field field=RelayConnection.class.getDeclaredField(name);field.setAccessible(true);return field.get(object);
    }
    private static void stopTimer(RelayConnection link) throws Exception {
        ScheduledExecutorService timer=(ScheduledExecutorService)field(link,"timer");timer.shutdownNow();timer.awaitTermination(5,TimeUnit.SECONDS);
    }
    private static void receive(RelayConnection link,String packet) throws Exception {
        Method method=RelayConnection.class.getDeclaredMethod("receive",String.class);method.setAccessible(true);method.invoke(link,packet);
    }
    private static void tick(RelayConnection link) throws Exception {
        Method method=RelayConnection.class.getDeclaredMethod("tick");method.setAccessible(true);method.invoke(link);
    }
    private static void send(RelayConnection link,Map<String,Object> body) throws Exception {
        Method method=RelayConnection.class.getDeclaredMethod("send",String.class,Map.class,boolean.class);method.setAccessible(true);method.invoke(link,"request",body,true);
    }
    private static void field(Object object,String name,Object value) throws Exception {
        Field field=RelayConnection.class.getDeclaredField(name);field.setAccessible(true);field.set(object,value);
    }
    private static final class Socket implements WebSocket {
        int sent; long queued; boolean cancelled;
        public Request request(){return new Request.Builder().url("http://127.0.0.1/").build();}
        public long queueSize(){return queued;}
        public boolean send(String value){sent++;return true;}
        public boolean send(ByteString value){return true;}
        public boolean close(int code,String reason){return true;}
        public void cancel(){cancelled=true;}
    }
    @Test public void receiptSurvivesRestartUntilHistoryReportsDurableCommit() throws Exception {
        RelayCrypto host=new RelayCrypto("computer",null),phone=new RelayCrypto("phone",null);
        String account="a".repeat(32),space="b".repeat(32),runtime="c".repeat(32),client="d".repeat(32),remote="e".repeat(32);
        Map<String,Object> write=RelayCrypto.map("request","request1","method","POST","path","/v1/messages","runtime_id",runtime,
                "body",RelayCrypto.map("client_id",client,"conversation","default","text","private"));
        Map<String,Object> config=RelayCrypto.map("account",account,"device",host.identity,"space",space,"name","phone","seed",phone.seed,
                "paired",true,"runtime_id",runtime,"pending_writes",new ArrayList<>(List.of(write)),
                "relay",RelayCrypto.map("url","ws://127.0.0.1:1","room",account,"token","r".repeat(43)));
        Map<String,Object> response=RelayCrypto.map("request","request1","value",RelayCrypto.map("id",remote,"client_id",client,"conversation","default"));
        String packet=RelayCrypto.json(RelayCrypto.map("type","packet","packet",host.seal(phone.identity,space,"response",response)));
        Observer first=new Observer();Map<String,Object> saved;
        try(RelayConnection link=new RelayConnection(config,first)){
            stopTimer(link); first.fail=true;
            try { receive(link,packet);fail("storage failure must prevent acknowledgement"); }
            catch (java.lang.reflect.InvocationTargetException expected) { assertTrue(expected.getCause() instanceof IllegalStateException); }
            assertEquals(1,((Map<?,?>)field(link,"durableWrites")).size());
            assertEquals(0,((Map<?,?>)field(link,"acceptedReceipts")).size());
            first.fail=false;receive(link,packet);
            assertEquals(0,Json.list(first.saved,"pending_writes").size());
            assertEquals(1,Json.list(first.saved,"accepted_receipts").size());
            assertNotNull(first.completion);first.completion.accept(false);
            assertEquals(1,Json.list(first.saved,"accepted_receipts").size());
            saved=first.saved;
        }
        Observer second=new Observer();
        try(RelayConnection link=new RelayConnection(saved,second)){
            stopTimer(link);tick(link);assertNotNull(second.completion);
            second.completion.accept(true);
            assertEquals(0,Json.list(second.saved,"accepted_receipts").size());
        }
    }
    @Test public void serverErrorAfterSideEffectKeepsOriginalRequestAndPausesReplayAfterRestart() throws Exception {
        for(int status:new int[]{409,500,502}) {
            RelayCrypto host=new RelayCrypto("computer",null),phone=new RelayCrypto("phone",null);
            String account="a".repeat(32),space="b".repeat(32),runtime="c".repeat(32),client="d".repeat(32);
            Map<String,Object> write=RelayCrypto.map("request","request1","method","POST","path","/v1/messages","runtime_id",runtime,
                    "body",RelayCrypto.map("client_id",client,"conversation","default","text","private"));
            Map<String,Object> config=RelayCrypto.map("account",account,"device",host.identity,"space",space,"name","phone","seed",phone.seed,
                    "paired",true,"runtime_id",runtime,"pending_writes",new ArrayList<>(List.of(write)),
                    "relay",RelayCrypto.map("url","ws://127.0.0.1:1","room",account,"token","r".repeat(43)));
            Map<String,Object> response=RelayCrypto.map("request","request1","status",status,"error","outcome unknown");
            String packet=RelayCrypto.json(RelayCrypto.map("type","packet","packet",host.seal(phone.identity,space,"response",response)));
            Observer first=new Observer();Map<String,Object> saved;
            try(RelayConnection link=new RelayConnection(config,first)){
                stopTimer(link);
                Class<?> waiterType=Class.forName("com.yanyuliu.pockethub.RelayConnection$Waiter");
                java.lang.reflect.Constructor<?> ctor=waiterType.getDeclaredConstructor();ctor.setAccessible(true);Object waiter=ctor.newInstance();
                @SuppressWarnings("unchecked") Map<String,Object> waiters=(Map<String,Object>)field(link,"waiters");waiters.put("request1",waiter);
                Field answer=waiterType.getDeclaredField("answer");answer.setAccessible(true);
                @SuppressWarnings("unchecked") java.util.concurrent.CompletableFuture<Map<String,Object>> future=(java.util.concurrent.CompletableFuture<Map<String,Object>>)answer.get(waiter);
                receive(link,packet);
                try{future.get();fail("unknown outcome must not be reported as failure");}
                catch(java.util.concurrent.ExecutionException expected){assertTrue(expected.getCause() instanceof RelayOutcomeUnknownException);}
                assertEquals(1,Json.list(first.saved,"pending_writes").size());
                assertEquals(List.of("request1"),Json.list(first.saved,"uncertain_writes"));
                assertEquals(0,Json.list(first.saved,"accepted_receipts").size());saved=first.saved;
            }
            Observer second=new Observer();
            try(RelayConnection link=new RelayConnection(saved,second)){
                stopTimer(link);Socket socket=new Socket();field(link,"socket",socket);field(link,"opened",true);
                ((PeerAvailability)field(link,"availability")).observed();tick(link);
                assertEquals(0,socket.sent);
                assertEquals(1,Json.list(second.saved,"pending_writes").size());
            }
        }
    }
    @Test public void orphanedVoiceAndSessionWritesPauseWithoutTrustedReceiptLifetimeButTextReplays() throws Exception {
        for(String path:new String[]{"/v1/voice?conversation=default&client_id="+"d".repeat(32),"/v1/sessions/card/continue","/v1/messages"}){
            RelayCrypto host=new RelayCrypto("computer",null),phone=new RelayCrypto("phone",null);
            String account="a".repeat(32),space="b".repeat(32),runtime="c".repeat(32),client="d".repeat(32);
            Map<String,Object> write=RelayCrypto.map("request","request1","method","POST","path",path,"runtime_id",runtime,
                    "body",RelayCrypto.map("client_id",client,"conversation","default"));
            // A legacy pending write has no trusted time or boot metadata.
            Map<String,Object> config=RelayCrypto.map("account",account,"device",host.identity,"space",space,"name","phone","seed",phone.seed,
                    "paired",true,"runtime_id",runtime,"pending_writes",new ArrayList<>(List.of(write)),
                    "relay",RelayCrypto.map("url","ws://127.0.0.1:1","room",account,"token","r".repeat(43)));
            Observer observer=new Observer();
            try(RelayConnection link=new RelayConnection(config,observer)){
                stopTimer(link);Socket socket=new Socket();field(link,"socket",socket);field(link,"opened",true);
                ((PeerAvailability)field(link,"availability")).observed();tick(link);
                assertEquals(1,Json.list(observer.saved,"pending_writes").size());
                if(path.equals("/v1/messages")){
                    assertEquals(1,socket.sent);assertEquals(0,Json.list(observer.saved,"uncertain_writes").size());
                }else{
                    assertEquals(0,socket.sent);assertEquals(List.of("request1"),Json.list(observer.saved,"uncertain_writes"));
                    assertNotNull(observer.uncertain);assertEquals(client,observer.uncertain.get("client_id"));
                }
            }
        }
    }
    @Test public void sealedPacketsNeverReplayAfterUncertainReceiptOrOrphanedVoice() throws Exception {
        for(boolean voice:new boolean[]{false,true}){
            RelayCrypto host=new RelayCrypto("computer",null),phone=new RelayCrypto("phone",null);
            String account="a".repeat(32),space="b".repeat(32),runtime="c".repeat(32),client="d".repeat(32);
            String path=voice?"/v1/voice?conversation=default&client_id="+client:"/v1/messages";
            Map<String,Object> write=RelayCrypto.map("request","request1","method","POST","path",path,"runtime_id",runtime,
                    "body",RelayCrypto.map("client_id",client,"conversation","default"));
            Map<String,Object> config=RelayCrypto.map("account",account,"device",host.identity,"space",space,"name","phone","seed",phone.seed,
                    "paired",true,"runtime_id",runtime,"pending_writes",new ArrayList<>(List.of(write)),
                    "relay",RelayCrypto.map("url","ws://127.0.0.1:1","room",account,"token","r".repeat(43)));
            Observer observer=new Observer();
            try(RelayConnection link=new RelayConnection(config,observer)){
                stopTimer(link);Socket socket=new Socket();field(link,"socket",socket);field(link,"opened",true);
                ((PeerAvailability)field(link,"availability")).observed();
                send(link,write);send(link,write);
                assertEquals(2,((Map<?,?>)field(link,"packets")).size());
                socket.queued=1;
                if(!voice){
                    Map<String,Object> response=RelayCrypto.map("request","request1","status",500,"error","unknown");
                    receive(link,RelayCrypto.json(RelayCrypto.map("type","packet","packet",host.seal(phone.identity,space,"response",response))));
                }
                socket.sent=0;tick(link);
                assertEquals(0,socket.sent);
                assertEquals(0,((Map<?,?>)field(link,"packets")).size());
                assertEquals(0,((Map<?,?>)field(link,"packetRequests")).size());
                assertTrue(socket.cancelled);
            }
        }
    }
    @Test public void malformedResponsesNeverClearOriginalWrite() throws Exception {
        for(Object value:new Object[]{"absent",null,"wrong-type",RelayCrypto.map("id","e".repeat(32),"client_id","wrong","conversation","default")}){
            RelayCrypto host=new RelayCrypto("computer",null),phone=new RelayCrypto("phone",null);
            String account="a".repeat(32),space="b".repeat(32),runtime="c".repeat(32),client="d".repeat(32);
            Map<String,Object> write=RelayCrypto.map("request","request1","method","POST","path","/v1/messages","runtime_id",runtime,
                    "body",RelayCrypto.map("client_id",client,"conversation","default"));
            Map<String,Object> config=RelayCrypto.map("account",account,"device",host.identity,"space",space,"name","phone","seed",phone.seed,
                    "paired",true,"runtime_id",runtime,"pending_writes",new ArrayList<>(List.of(write)),
                    "relay",RelayCrypto.map("url","ws://127.0.0.1:1","room",account,"token","r".repeat(43)));
            Map<String,Object> response=RelayCrypto.map("request","request1");
            if(!"absent".equals(value))response.put("value",value);
            Observer observer=new Observer();
            try(RelayConnection link=new RelayConnection(config,observer)){
                stopTimer(link);receive(link,RelayCrypto.json(RelayCrypto.map("type","packet","packet",host.seal(phone.identity,space,"response",response))));
                assertEquals(1,Json.list(observer.saved,"pending_writes").size());
                assertEquals(List.of("request1"),Json.list(observer.saved,"uncertain_writes"));
            }
        }
    }
    @Test public void pausedQueueAtCapacityPreservesAllWritesAndReportsLimit() throws Exception {
        RelayCrypto host=new RelayCrypto("computer",null),phone=new RelayCrypto("phone",null);
        String account="a".repeat(32),space="b".repeat(32),runtime="c".repeat(32),client="d".repeat(32);
        List<Map<String,Object>> writes=new ArrayList<>();List<String> uncertain=new ArrayList<>();
        for(int i=0;i<16;i++){
            String id="request"+i;uncertain.add(id);
            writes.add(RelayCrypto.map("request",id,"method","POST","path","/v1/messages","runtime_id",runtime,
                    "body",RelayCrypto.map("client_id",client+i,"conversation","default")));
        }
        Map<String,Object> config=RelayCrypto.map("account",account,"device",host.identity,"space",space,"name","phone","seed",phone.seed,
                "paired",true,"runtime_id",runtime,"pending_writes",writes,"uncertain_writes",uncertain,
                "relay",RelayCrypto.map("url","ws://127.0.0.1:1","room",account,"token","r".repeat(43)));
        Observer observer=new Observer();
        try(RelayConnection link=new RelayConnection(config,observer)){
            stopTimer(link);
            try{link.request("POST","/v1/messages",RelayCrypto.map("client_id","new","conversation","default"),3);fail("queue must stay bounded");}
            catch(java.io.IOException expected){assertTrue(expected.getMessage().contains("仍有未核实请求"));}
            assertEquals(16,((Map<?,?>)field(link,"durableWrites")).size());
        }
    }
}
