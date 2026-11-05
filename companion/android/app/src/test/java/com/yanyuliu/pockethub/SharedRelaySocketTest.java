package com.yanyuliu.pockethub;

import java.util.*;
import okhttp3.*;
import okio.ByteString;
import org.junit.Test;
import static org.junit.Assert.*;

public class SharedRelaySocketTest {
    static final class Client extends OkHttpClient {
        WebSocketListener listener; FakeSocket socket; int opens;
        @Override public WebSocket newWebSocket(Request request, WebSocketListener observer) {
            opens++; listener = observer; return socket = new FakeSocket(request);
        }
    }
    static final class FakeSocket implements WebSocket {
        final Request request; boolean cancelled;
        FakeSocket(Request value) { request = value; }
        public Request request() { return request; }
        public long queueSize() { return 0; }
        public boolean send(String value) { return true; }
        public boolean send(ByteString value) { return true; }
        public boolean close(int code, String reason) { return true; }
        public void cancel() { cancelled=true; }
    }
    static final class Observer extends WebSocketListener {
        final List<WebSocket> callbacks = new ArrayList<>();
        public void onOpen(WebSocket socket, Response response) { callbacks.add(socket); }
        public void onMessage(WebSocket socket, String text) { callbacks.add(socket); }
        public void onClosed(WebSocket socket, int code, String reason) { callbacks.add(socket); }
        public void onFailure(WebSocket socket, Throwable error, Response response) { callbacks.add(socket); }
    }
    @Test public void sharedMessagesAndCloseKeepEachLogicalConnectionIdentity() {
        Client client = new Client(); Observer first = new Observer(), second = new Observer();
        Request request = new Request.Builder().url("https://fixture.invalid/" + UUID.randomUUID()).build();
        WebSocket a = SharedRelaySocket.open(client, request, first), b = SharedRelaySocket.open(client, request, second);
        assertEquals(1, client.opens);
        try {
            Response response = new Response.Builder().request(request).protocol(Protocol.HTTP_1_1).code(101).message("upgrade").build();
            client.listener.onOpen(client.socket, response);
            client.listener.onMessage(client.socket, "encrypted response");
            client.listener.onClosed(client.socket, 1000, "done");
            assertEquals(3, first.callbacks.size()); assertEquals(3, second.callbacks.size());
            for (WebSocket socket : first.callbacks) assertSame(a, socket);
            for (WebSocket socket : second.callbacks) assertSame(b, socket);
        } finally { a.cancel(); b.cancel(); }
    }
    @Test public void failureKeepsLogicalIdentitySoTheOwnerCanReconnect() {
        Client client = new Client(); Observer observer = new Observer();
        Request request = new Request.Builder().url("https://fixture.invalid/" + UUID.randomUUID()).build();
        WebSocket logical = SharedRelaySocket.open(client, request, observer);
        try {
            client.listener.onFailure(client.socket, new java.io.IOException("offline"), null);
            assertEquals(1, observer.callbacks.size()); assertSame(logical, observer.callbacks.get(0));
            Client recovered = new Client();
            WebSocket next = SharedRelaySocket.open(recovered, request, new Observer());
            assertEquals(1, recovered.opens); next.cancel();
        } finally { logical.cancel(); }
    }
    @Test public void networkResetRetiresWholeGroupBeforeConcurrentReconnect() throws Exception {
        Client client=new Client();Observer first=new Observer(),second=new Observer();
        Request request=new Request.Builder().url("https://fixture.invalid/"+UUID.randomUUID()).build();
        WebSocket a=SharedRelaySocket.open(client,request,first),b=SharedRelaySocket.open(client,request,second);
        FakeSocket old=client.socket;WebSocketListener oldListener=client.listener;
        java.util.concurrent.ExecutorService executor=java.util.concurrent.Executors.newSingleThreadExecutor();
        WebSocket next=null,other=null;
        try {
            SharedRelaySocket.invalidateNetwork();
            assertTrue(old.cancelled);assertFalse(a.send("stale"));assertFalse(b.send("stale"));
            next=executor.submit(()->SharedRelaySocket.open(client,request,new Observer())).get(5,java.util.concurrent.TimeUnit.SECONDS);
            assertEquals(2,client.opens);assertNotSame(old,client.socket);
            // The other old member and a late failure must not retire the new group.
            b.cancel();oldListener.onFailure(old,new java.io.IOException("old route"),null);
            other=SharedRelaySocket.open(client,request,new Observer());
            assertEquals(2,client.opens);assertTrue(first.callbacks.isEmpty());assertTrue(second.callbacks.isEmpty());
        } finally {a.cancel();b.cancel();if(next!=null)next.cancel();if(other!=null)other.cancel();executor.shutdownNow();}
    }
    @Test public void abortPhysicalDropsSharedQueueAndOtherMembersRecover() {
        Client client=new Client();Observer first=new Observer(),second=new Observer();
        Request request=new Request.Builder().url("https://fixture.invalid/"+UUID.randomUUID()).build();
        WebSocket a=SharedRelaySocket.open(client,request,first),b=SharedRelaySocket.open(client,request,second);
        FakeSocket old=client.socket;WebSocketListener oldListener=client.listener;WebSocket next=null;
        try {
            ((SharedRelaySocket)a).abortPhysical();a.cancel();assertTrue(old.cancelled);
            next=SharedRelaySocket.open(client,request,new Observer());assertEquals(2,client.opens);
            oldListener.onFailure(old,new java.io.IOException("cancelled"),null);
            assertTrue(first.callbacks.isEmpty());assertEquals(1,second.callbacks.size());assertSame(b,second.callbacks.get(0));
            WebSocket same=SharedRelaySocket.open(client,request,new Observer());assertEquals(2,client.opens);same.cancel();
        } finally {a.cancel();b.cancel();if(next!=null)next.cancel();}
    }
    @Test public void proofPromotesAnUnprovedPendingGroupWithoutLosingMembers() {
        Client client=new Client();Observer first=new Observer(),second=new Observer();
        Request original=new Request.Builder().url("https://fixture.invalid/"+UUID.randomUUID()).build();
        Request proved=original.newBuilder().header("X-Xiaoyu-Relay-Response","single-use-proof").build();
        WebSocket a=SharedRelaySocket.open(client,original,first),b=null;
        FakeSocket old=client.socket;WebSocketListener oldListener=client.listener;
        try {
            b=SharedRelaySocket.open(client,proved,second);
            assertEquals(2,client.opens);assertTrue(old.cancelled);
            assertEquals("single-use-proof",client.socket.request().header("X-Xiaoyu-Relay-Response"));
            // A late failure from the original physical socket cannot remove or
            // notify members of the promoted group.
            oldListener.onFailure(old,new java.io.IOException("old socket"),null);
            assertTrue(first.callbacks.isEmpty());assertTrue(second.callbacks.isEmpty());
            Response ok=new Response.Builder().request(proved).protocol(Protocol.HTTP_1_1).code(101).message("Switching Protocols").build();
            client.listener.onOpen(client.socket,ok);
            assertEquals(1,first.callbacks.size());assertEquals(1,second.callbacks.size());
            assertSame(a,first.callbacks.get(0));assertSame(b,second.callbacks.get(0));
            WebSocket joined=SharedRelaySocket.open(client,original,new Observer());assertEquals(2,client.opens);joined.cancel();
        } finally {a.cancel();if(b!=null)b.cancel();}
    }
}
