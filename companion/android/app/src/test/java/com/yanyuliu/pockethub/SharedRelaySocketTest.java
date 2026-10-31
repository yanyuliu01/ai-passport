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
        final Request request;
        FakeSocket(Request value) { request = value; }
        public Request request() { return request; }
        public long queueSize() { return 0; }
        public boolean send(String value) { return true; }
        public boolean send(ByteString value) { return true; }
        public boolean close(int code, String reason) { return true; }
        public void cancel() { }
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
}
