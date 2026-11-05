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
        // A second logical member may bring a valid challenge response while an
        // unproved physical connection is still opening. Promote that *same*
        // group, rather than letting an unproved socket discard the response or
        // creating competing sockets for one device identity.
        if(group.socket!=null && group.response==null && request.header("X-Xiaoyu-Relay-Response")!=null
                && group.request.header("X-Xiaoyu-Relay-Response")==null){
            WebSocket old=group.socket;group.request=request;group.socket=http.newWebSocket(request,group);old.cancel();
        }
        if(group.socket==null)group.socket=http.newWebSocket(request,group);
        // Use the HTTP executor so callbacks cannot run before RelayConnection assigns its socket.
        if(group.response!=null){Group current=group;http.dispatcher().executorService().execute(()->{if(!member.released && current.response!=null)observer.onOpen(member,current.response);});}
        return member;
    }
    /** Retire every old-route group before any logical connection is allowed to rejoin it. */
    static synchronized void invalidateNetwork() {
        List<Group> old=new ArrayList<>(groups.values());groups.clear();
        for(Group group:old){
            for(SharedRelaySocket member:group.members)member.released=true;
            group.members.clear();group.response=null;
            if(group.socket!=null)group.socket.cancel();
        }
    }
    public Request request(){return group.request;}
    public long queueSize(){return group.socket.queueSize();}
    public boolean send(String text){return !released && group.socket.send(text);}
    public boolean send(ByteString bytes){return !released && group.socket.send(bytes);}
    public boolean close(int code,String reason){cancel();return true;}
    /** Drop queued physical bytes for every logical member; failure reconnects the others. */
    void abortPhysical(){synchronized(SharedRelaySocket.class){groups.remove(group.key,group);if(group.socket!=null)group.socket.cancel();}}
    public void cancel(){synchronized(SharedRelaySocket.class){if(released)return;released=true;group.members.remove(this);if(group.members.isEmpty()){groups.remove(group.key,group);group.socket.cancel();}}}
    private static final class Group extends WebSocketListener {
        final String key; volatile Request request; final List<SharedRelaySocket> members=new ArrayList<>();
        WebSocket socket; volatile Response response;
        Group(String key,Request request){this.key=key;this.request=request;}
        private List<SharedRelaySocket> members(){synchronized(SharedRelaySocket.class){return new ArrayList<>(members);}}
        private boolean current(WebSocket ws){synchronized(SharedRelaySocket.class){return socket==ws;}}
        @Override public void onOpen(WebSocket ws,Response value){synchronized(SharedRelaySocket.class){if(socket!=ws)return;response=value;}for(SharedRelaySocket m:members())if(!m.released)m.observer.onOpen(m,value);}
        @Override public void onMessage(WebSocket ws,String text){if(!current(ws))return;for(SharedRelaySocket m:members())if(!m.released)m.observer.onMessage(m,text);}
        @Override public void onClosing(WebSocket ws,int code,String reason){if(current(ws))ws.close(code,null);}
        @Override public void onClosed(WebSocket ws,int code,String reason){if(!finish(ws))return;for(SharedRelaySocket m:members())if(!m.released)m.observer.onClosed(m,code,reason);}
        @Override public void onFailure(WebSocket ws,Throwable error,Response value){if(!finish(ws))return;for(SharedRelaySocket m:members())if(!m.released)m.observer.onFailure(m,error,value);}
        private boolean finish(WebSocket ws){synchronized(SharedRelaySocket.class){if(socket!=ws)return false;response=null;groups.remove(key,this);return true;}}
    }
}
