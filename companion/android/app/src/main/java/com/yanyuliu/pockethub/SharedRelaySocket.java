package com.yanyuliu.pockethub;

import java.util.*;
import okhttp3.*;
import okio.ByteString;

/** One outbound socket per endpoint identity; separate computer RPC engines share it. */
final class SharedRelaySocket implements WebSocket {
    private static final Map<String, Group> groups = new HashMap<>();
    private final Group group;
    private final WebSocketListener observer;
    private volatile boolean released;
    private SharedRelaySocket(Group group, WebSocketListener observer) { this.group=group; this.observer=observer; }
    static synchronized WebSocket open(OkHttpClient http, Request request, WebSocketListener observer) {
        String key=request.url().toString()+"\n"+request.header("Authorization");
        Group group=groups.get(key);
        if(group==null){ group=new Group(key,request); groups.put(key,group); }
        SharedRelaySocket member=new SharedRelaySocket(group,observer); group.members.add(member);
        if(group.socket==null)group.socket=http.newWebSocket(request,group);
        // Use the HTTP executor so callbacks cannot run before RelayConnection assigns its socket.
        if(group.response!=null){Group current=group;http.dispatcher().executorService().execute(()->{if(!member.released && current.response!=null)observer.onOpen(member,current.response);});}
        return member;
    }
    public Request request(){return group.request;}
    public long queueSize(){return group.socket.queueSize();}
    public boolean send(String text){return !released && group.socket.send(text);}
    public boolean send(ByteString bytes){return !released && group.socket.send(bytes);}
    public boolean close(int code,String reason){cancel();return true;}
    public void cancel(){synchronized(SharedRelaySocket.class){if(released)return;released=true;group.members.remove(this);if(group.members.isEmpty()){groups.remove(group.key,group);group.socket.cancel();}}}
    private static final class Group extends WebSocketListener {
        final String key; final Request request; final List<SharedRelaySocket> members=new ArrayList<>();
        WebSocket socket; volatile Response response;
        Group(String key,Request request){this.key=key;this.request=request;}
        private List<SharedRelaySocket> members(){synchronized(SharedRelaySocket.class){return new ArrayList<>(members);}}
        @Override public void onOpen(WebSocket ws,Response value){synchronized(SharedRelaySocket.class){response=value;}for(SharedRelaySocket m:members())m.observer.onOpen(m,value);}
        @Override public void onMessage(WebSocket ws,String text){for(SharedRelaySocket m:members())if(!m.released)m.observer.onMessage(m,text);}
        @Override public void onClosing(WebSocket ws,int code,String reason){ws.close(code,null);}
        @Override public void onClosed(WebSocket ws,int code,String reason){finish();for(SharedRelaySocket m:members())m.observer.onClosed(m,code,reason);}
        @Override public void onFailure(WebSocket ws,Throwable error,Response value){finish();for(SharedRelaySocket m:members())m.observer.onFailure(m,error,value);}
        private void finish(){synchronized(SharedRelaySocket.class){response=null;groups.remove(key,this);}}
    }
}
