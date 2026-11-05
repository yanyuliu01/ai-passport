package com.yanyuliu.pockethub;

import java.io.IOException;
import java.net.URI;
import java.net.SocketTimeoutException;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;
import okhttp3.*;

/** Outbound-only relay connection. The relay never receives Runtime tokens or chat plaintext. */
final class RelayConnection implements AutoCloseable {
    interface Observer {
        void save(Map<String,Object> state); void ready(String url,String name); void state(String message);
        default void accepted(String url, Map<String,Object> receipt, java.util.function.Consumer<Boolean> completed) { completed.accept(false); }
        default void uncertain(String url, Map<String,Object> receipt, java.util.function.Consumer<Boolean> completed) { completed.accept(false); }
    }
    private final Map<String,Object> config;
    private final Map<String,Object> host;
    private final Map<String,Object> relay;
    private final String space;
    private final PeerAvailability availability=new PeerAvailability();
    private final RelayCrypto crypto;
    private final Observer observer;
    private final OkHttpClient http;
    private final ScheduledExecutorService timer=Executors.newSingleThreadScheduledExecutor();
    private final ExecutorService incoming=Executors.newSingleThreadExecutor();
    private final java.util.concurrent.atomic.AtomicInteger queuedIncoming=new java.util.concurrent.atomic.AtomicInteger();
    private final Map<String,Map<String,Object>> packets=new ConcurrentHashMap<>();
    private final Map<String,String> packetRequests=new ConcurrentHashMap<>();
    private final Map<String,Waiter> waiters=new ConcurrentHashMap<>();
    private final Set<String> received=Collections.synchronizedSet(new LinkedHashSet<>());
    private volatile WebSocket socket;
    private volatile boolean opened,closed,paired,identityMismatch;
    private volatile long authRejectedAt;
    private final Map<String,Map<String,Object>> durableWrites=new ConcurrentHashMap<>();
    private final Map<String,Map<String,Object>> acceptedReceipts=new ConcurrentHashMap<>();
    private final Set<String> uncertainWrites=ConcurrentHashMap.newKeySet();
    private final Set<String> uncertainNotified=ConcurrentHashMap.newKeySet(), deliveringUncertain=ConcurrentHashMap.newKeySet();
    private final Set<String> deliveringReceipts=ConcurrentHashMap.newKeySet();
    /** When each unacknowledged packet last went out, and how large it is on the wire. */
    private final Map<String,long[]> sent=new ConcurrentHashMap<>();
    private long lastReplay;
    private long nextConnect; private int failures;
    private String relayProof;
    private long lastProofNanos;
    private volatile String state="正在连接加密入口";
    private static final class Waiter { final CompletableFuture<Map<String,Object>> answer=new CompletableFuture<>(); String packet; boolean read, sync; }
    @SuppressWarnings("unchecked")
    RelayConnection(Map<String,Object> saved,Observer observer) throws Exception {
        config=new LinkedHashMap<>(saved);this.observer=observer;
        host=(Map<String,Object>)config.get("device");relay=(Map<String,Object>)config.get("relay");space=Json.string(config,"space");
        RelayCrypto.validateDevice(host);validateRelay(relay);
        OkHttpClient.Builder builder=new OkHttpClient.Builder().followRedirects(false).followSslRedirects(false).pingInterval(20,TimeUnit.SECONDS).readTimeout(0,TimeUnit.MILLISECONDS);
        PinnedRelayTLS.configure(builder,relay);http=builder.build();
        if(!space.matches("[a-f0-9]{32}"))throw new Exception("空间无效");
        crypto=new RelayCrypto(Json.string(config,"name"),Json.string(config,"seed").isEmpty()?null:Json.string(config,"seed"));config.put("seed",crypto.seed);
        paired=Json.flag(config,"paired");
        for(Object value:Json.list(config,"pending_writes"))if(value instanceof Map){Map<String,Object> write=(Map<String,Object>)value;durableWrites.put(Json.string(write,"request"),write);}
        for(Object value:Json.list(config,"uncertain_writes"))if(value instanceof String)uncertainWrites.add((String)value);
        for(Object value:Json.list(config,"accepted_receipts"))if(value instanceof Map){Map<String,Object> receipt=(Map<String,Object>)value;String id=Json.string(receipt,"request");if(!id.isEmpty())acceptedReceipts.put(id,receipt);}
        persist();
        timer.scheduleWithFixedDelay(()->{try{tick();}catch(Exception ignored){}},0,1,TimeUnit.SECONDS);
    }
    static Map<String,Object> invitation(String text,String name) throws Exception {
        if(text.length()>16384 || !text.startsWith("xiaoyu://join#"))throw new Exception("请粘贴小鱼邀请");
        Map<String,Object> value=Json.parseObject(new String(RelayCrypto.decode(text.substring(15),-1),StandardCharsets.UTF_8));
        if(value==null || Json.number(value,"v")!=1 || Json.number(value,"expires")<System.currentTimeMillis() || Json.number(value,"expires")>System.currentTimeMillis()+150000 || Json.string(value,"token").length()<32)throw new Exception("邀请无效或已过期");
        value.put("name",name);return value;
    }
    static void validateRelay(Map<String,Object> relay) throws Exception {
        if(relay==null)throw new Exception("缺少通信入口");
        URI uri=URI.create(Json.string(relay,"url"));
        boolean local=Arrays.asList("127.0.0.1","localhost","[::1]").contains(uri.getHost());
        if(!("wss".equals(uri.getScheme()) || "ws".equals(uri.getScheme())&&local) || uri.getUserInfo()!=null || uri.getQuery()!=null || uri.getFragment()!=null || !(uri.getPath().isEmpty() || uri.getPath().equals("/")) || !Json.string(relay,"room").matches("[a-f0-9]{32}") || Json.string(relay,"token").length()<32 || Json.string(relay,"token").length()>256)throw new Exception("通信入口必须使用有效 WSS 地址");
        if(relay.containsKey("tlsFingerprint")&&(!Json.string(relay,"tlsFingerprint").matches("[a-f0-9]{64}")||!"wss".equals(uri.getScheme())))throw new Exception("节点证书指纹无效");
    }
    String fingerprint(){return Json.string(crypto.identity,"id");}
    static String address(Map<String,Object> config) {
        @SuppressWarnings("unchecked") Map<String,Object> device=(Map<String,Object>)config.get("device");
        String account=Json.string(config,"account");
        if(!account.isEmpty() && !account.matches("[a-f0-9]{32}"))throw new IllegalArgumentException("账号身份无效");
        return "relay://"+Json.string(device,"id")+(account.isEmpty()?"":"."+account);
    }
    /** Endpoint IDs are opaque identifiers, not DNS names; a numeric account label is valid. */
    static String target(URI uri) throws IOException {
        String authority=uri.getRawAuthority();
        if(!"relay".equals(uri.getScheme()) || authority==null || uri.getRawFragment()!=null
            || !authority.matches("[a-f0-9]{32}(?:\\.[a-f0-9]{32})?"))throw new IOException("加密连接地址无效");
        return "relay://"+authority;
    }
    String url(){return address(config);}
    String status(){return state;}
    boolean online(){return availability.online(opened && paired && !closed && !identityMismatch);}
    boolean isPaired(){return paired;}
    boolean isClosed(){return closed;}
    boolean canRecover(Map<String,Object> refreshedRelay){
        if(!closed)return false;
        for(String key:Arrays.asList("url","room","token","tlsFingerprint"))
            if(!Objects.equals(relay.get(key),refreshedRelay.get(key)))return true;
        return authRejectedAt==0 || System.currentTimeMillis()-authRejectedAt>=60000;
    }
    boolean isActive(){return !closed && (paired || Json.number(config,"expires")>=System.currentTimeMillis());}
    private void status(String message){state=message;observer.state(message);}
    private synchronized void persist(){config.put("paired",paired);config.put("pending_writes",new ArrayList<>(durableWrites.values()));config.put("uncertain_writes",new ArrayList<>(uncertainWrites));config.put("accepted_receipts",new ArrayList<>(acceptedReceipts.values()));observer.save(new LinkedHashMap<>(config));}
    private void deliverReceipts(){
        for(Map.Entry<String,Map<String,Object>> entry:acceptedReceipts.entrySet()){
            String id=entry.getKey();Map<String,Object> receipt=entry.getValue();
            if(!deliveringReceipts.add(id))continue;
            try{observer.accepted(url(),new LinkedHashMap<>(receipt),applied->{
                try{if(applied)synchronized(RelayConnection.this){
                    if(acceptedReceipts.remove(id,receipt))try{persist();}catch(RuntimeException failure){acceptedReceipts.put(id,receipt);}
                }}finally{deliveringReceipts.remove(id);}
            });}catch(RuntimeException failure){deliveringReceipts.remove(id);}
        }
    }
    private static boolean restrictedReplay(Map<String,Object> write){
        if(!"POST".equals(Json.string(write,"method")))return false;
        String path=Json.string(write,"path");
        return path.startsWith("/v1/voice?") || path.matches("/v1/sessions/[^/?]+/continue");
    }
    private void discardRequestPackets(String request){
        boolean removed=false;
        for(Map.Entry<String,String> entry:packetRequests.entrySet())if(request.equals(entry.getValue())){
            String packet=entry.getKey();packets.remove(packet);sent.remove(packet);packetRequests.remove(packet,request);removed=true;
        }
        // OkHttp can still hold unsent bytes after send() returned. Drop that transport so
        // they cannot drain later; all other durable packets retain their original identities.
        WebSocket current=socket;
        if(removed && current!=null && current.queueSize()>0){
            if(current instanceof SharedRelaySocket)((SharedRelaySocket)current).abortPhysical();
            networkChanged();
        }
    }
    /** An orphaned voice/session write has no trustworthy proof that the remote receipt remains. */
    private void pauseOrphaned() {
        List<String> added=new ArrayList<>();
        for(Map.Entry<String,Map<String,Object>> entry:durableWrites.entrySet())
            if(restrictedReplay(entry.getValue()) && !waiters.containsKey(entry.getKey()) && uncertainWrites.add(entry.getKey()))added.add(entry.getKey());
        for(String request:added)discardRequestPackets(request);
        if(!added.isEmpty())try{persist();}catch(RuntimeException failure){
            // Keep the in-memory pause even if disk is unavailable; restart rechecks before replay.
            throw failure;
        }
    }
    private void deliverUncertain(){
        for(String id:uncertainWrites){
            Map<String,Object> write=durableWrites.get(id),identity=AcceptedReceipt.identity(write,Json.string(config,"runtime_id"));
            if(identity==null || uncertainNotified.contains(id) || !deliveringUncertain.add(id))continue;
            try{observer.uncertain(url(),identity,applied->{
                if(applied)uncertainNotified.add(id);
                deliveringUncertain.remove(id);
            });}catch(RuntimeException failure){deliveringUncertain.remove(id);}
        }
    }
    private synchronized void tick() throws Exception {
        if(closed)return;
        pauseOrphaned();
        deliverReceipts();
        deliverUncertain();
        if(socket==null && System.currentTimeMillis()>=nextConnect)connect();
        if(!opened)return;
        if(!paired){
            if(Json.number(config,"expires")<System.currentTimeMillis()){close();status("邀请已过期，请重新配对");return;}
            if(!packets.values().stream().anyMatch(p->"join".equals(p.get("kind"))))send("join",RelayCrypto.map("token",config.get("token"),"device",crypto.identity,"expires",config.get("expires")),true);
        }
        // Resend only once the socket has flushed what it already holds. Re-queueing every second
        // stacked copies of large bodies (images, voice) faster than the uplink could drain them.
        WebSocket current=socket;long now=System.currentTimeMillis();boolean flushed=current!=null&&current.queueSize()==0;
        boolean peerReady=online();
        if(flushed && peerReady && !Json.string(config,"runtime_id").isEmpty() && now-lastReplay>5000){
            lastReplay=now;
            for(Map<String,Object> payload:durableWrites.values())if(!uncertainWrites.contains(Json.string(payload,"request")) && !waiters.containsKey(Json.string(payload,"request")))send("request",payload,true);
        }
        for(Map<String,Object> packet:packets.values()){
            String id=Json.string(packet,"id");long[] last=sent.get(id);
            String request=packetRequests.get(id);
            if(request!=null && uncertainWrites.contains(request)){packets.remove(id);sent.remove(id);packetRequests.remove(id);}
            else if(((Number)packet.get("expires")).longValue()<now){packets.remove(id);sent.remove(id);packetRequests.remove(id);}
            else if(flushed && (!"request".equals(packet.get("kind")) || peerReady) && (last==null || now-last[0]>=(last[1]>64*1024?10000:2000))){
                String wire=RelayCrypto.json(RelayCrypto.map("type","packet","packet",packet));
                if(current.send(wire)){sent.put(id,new long[]{now,wire.length()});if(request!=null)traceRelay(durableWrites.get(request),TurnTiming.Stage.RELAY_READY);}
            }
        }
    }
    private synchronized void connect(){
        if(closed)return;
        status("正在连接加密入口");String base=Json.string(relay,"url").replaceAll("/$","");
        String proof=relayProof;relayProof=null;
        Request.Builder builder=new Request.Builder().url(base+"/v1/connect?device="+crypto.identity.get("id")+"&room="+relay.get("room")+"&proof=1&key="+crypto.identity.get("publicKey"))
                .header("Authorization","Bearer "+Json.string(relay,"token"));
        if(proof!=null)builder.header("X-Xiaoyu-Relay-Response",proof);
        Request request=builder.build();
        socket=SharedRelaySocket.open(http,request,new WebSocketListener(){
            @Override public void onOpen(WebSocket ws,Response response){synchronized(RelayConnection.this){if(socket!=ws || closed){ws.cancel();return;}opened=true;failures=0;status(paired?"加密入口已连接，等待电脑上线":"等待电脑确认配对");}}
            @Override public void onMessage(WebSocket ws,String text){if(socket!=ws || closed || text.length()>8*1024*1024)return;if(queuedIncoming.incrementAndGet()>16){queuedIncoming.decrementAndGet();return;}incoming.execute(()->{try{synchronized(RelayConnection.this){if(socket==ws && !closed)receive(text);}}catch(Exception ignored){}finally{queuedIncoming.decrementAndGet();}});}
            private void lost(WebSocket ws,Response response){synchronized(RelayConnection.this){
                if(socket!=ws)return;opened=false;socket=null;availability.clear();
                if(response!=null&&(response.code()==401||response.code()==403)){authRejectedAt=System.currentTimeMillis();closed=true;status("通信入口凭据失效，请重新配对");return;}
                long now=System.nanoTime();
                if(response!=null&&response.code()==409&&"v1".equals(response.header("X-Xiaoyu-Relay-Proof"))
                        &&(lastProofNanos==0||now-lastProofNanos>=1_000_000_000L)){
                    try{
                        relayProof=crypto.relayProof(Json.string(relay,"room"),response.header("X-Xiaoyu-Relay-Challenge"));
                        lastProofNanos=now;nextConnect=0;status("正在验证设备并恢复连接");
                        try{timer.execute(()->{try{tick();}catch(Exception ignored){}});}catch(RejectedExecutionException ignored){}
                        return;
                    }catch(Exception invalid){relayProof=null;}
                }
                nextConnect=System.currentTimeMillis()+Math.min(15000,500L<<Math.min(failures++,5));status("连接中断，正在自动恢复");
            }}
            @Override public void onFailure(WebSocket ws,Throwable failure,Response response){lost(ws,response);}
            @Override public void onClosing(WebSocket ws,int code,String reason){ws.close(code,null);}
            @Override public void onClosed(WebSocket ws,int code,String reason){lost(ws,null);}
        });
    }
    private Map<String,Object> send(String kind,Map<String,Object> body,boolean durable) throws Exception {
        Map<String,Object> packet=crypto.seal(host,space,kind,body);String id=Json.string(packet,"id");
        synchronized(this){
            String request="request".equals(kind)?Json.string(body,"request"):"";
            if(!request.isEmpty() && uncertainWrites.contains(request))return packet;
            if(durable && packets.size()>=128)throw new IOException("加密队列繁忙，请稍后再试");
            if(durable){packets.put(id,packet);if(!request.isEmpty())packetRequests.put(id,request);}
            WebSocket current=socket;
            if(opened&&current!=null&&(!"request".equals(kind)||online())){String wire=RelayCrypto.json(RelayCrypto.map("type","packet","packet",packet));if(current.send(wire)){if(durable)sent.put(id,new long[]{System.currentTimeMillis(),wire.length()});if("request".equals(kind))traceRelay(body,TurnTiming.Stage.RELAY_READY);}}
        }
        return packet;
    }
    @SuppressWarnings("unchecked") private void receive(String text) throws Exception {
        Map<String,Object> envelope=Json.parseObject(text);if(envelope==null||!"packet".equals(envelope.get("type")))return;
        Map<String,Object> packet=(Map<String,Object>)envelope.get("packet");if(packet==null||!space.equals(packet.get("space")))return;
        Map<String,Object> body=crypto.open(packet,host);String kind=Json.string(packet,"kind"),id=Json.string(packet,"id");
        if("ack".equals(kind)){packets.remove(Json.string(body,"id"));sent.remove(Json.string(body,"id"));packetRequests.remove(Json.string(body,"id"));return;}
        if("decline".equals(kind)&&!paired&&Json.string(config,"token").equals(Json.string(body,"token"))){
            config.put("expires",0);persist();close();status("连接已取消，请重新扫码");return;
        }
        if("welcome".equals(kind)){
            if(!paired && Json.number(config,"expires")<System.currentTimeMillis())return;
            boolean found=false;for(Object raw:Json.list(body,"devices"))if(raw instanceof Map && Json.string(host,"id").equals(((Map<?,?>)raw).get("id")))found=true;
            if(!found)return;paired=true;config.remove("token");config.remove("expires");packets.entrySet().removeIf(entry->"join".equals(entry.getValue().get("kind")));persist();status("电脑已批准，正在核对服务");
        }
        if(!paired)return;
        if("hello".equals(kind)){
            if(!space.equals(body.get("subject_id")) || !Json.string(body,"runtime_id").matches("[a-f0-9]{32}"))return;
            String known=Json.string(config,"runtime_id");if(!known.isEmpty()&&!known.equals(body.get("runtime_id"))){identityMismatch=true;status("电脑服务身份变化，需要重新配对");return;}
            if(known.isEmpty()){config.put("runtime_id",body.get("runtime_id"));persist();}availability.observed();status("加密连接已建立");observer.ready(url(),Json.string(body,"name"));
            try{timer.execute(()->{try{tick();}catch(Exception ignored){}});}catch(java.util.concurrent.RejectedExecutionException ignored){}return;
        }
        if(!received.add(id)){send("ack",RelayCrypto.map("id",id),false);return;}
        synchronized(received){while(received.size()>2500)received.remove(received.iterator().next());}
        if("response".equals(kind)){
            String request=Json.string(body,"request");Map<String,Object> write=durableWrites.get(request);
            traceRelay(write,TurnTiming.Stage.RELAY_RESPONSE);
            @SuppressWarnings("unchecked") Map<String,Object> value=body.get("value") instanceof Map?(Map<String,Object>)body.get("value"):null;
            Map<String,Object> receipt=AcceptedReceipt.from(write,value,Json.string(config,"runtime_id"));
            if(receipt==null)receipt=AcceptedReceipt.rejected(write,body,Json.string(config,"runtime_id"));
            boolean uncertain=write!=null && receipt==null && AcceptedReceipt.supports(write,Json.string(config,"runtime_id"));
            if(uncertain){
                boolean added=uncertainWrites.add(request);
                try{persist();}catch(RuntimeException failure){if(added)uncertainWrites.remove(request);received.remove(id);throw failure;}
                discardRequestPackets(request);
            }
            if(receipt!=null && acceptedReceipts.size()>=128 && !acceptedReceipts.containsKey(request)){
                received.remove(id);throw new IOException("确认记录繁忙，请等待恢复");
            }
            if(write!=null && !uncertain){
                if(receipt!=null){receipt.put("request",request);acceptedReceipts.put(request,receipt);}
                boolean wasUncertain=uncertainWrites.remove(request);
                durableWrites.remove(request);
                try{persist();}catch(RuntimeException failure){durableWrites.put(request,write);if(wasUncertain)uncertainWrites.add(request);if(receipt!=null)acceptedReceipts.remove(request,receipt);received.remove(id);throw failure;}
            }
            Waiter waiting=waiters.get(request);
            if(waiting!=null){if(uncertain)waiting.answer.completeExceptionally(new RelayOutcomeUnknownException());else if(body.containsKey("error"))waiting.answer.completeExceptionally(new IOException("远端返回 "+(int)Json.number(body,"status")+"："+Json.string(body,"error")));else if(body.get("value") instanceof Map)waiting.answer.complete((Map<String,Object>)body.get("value"));}
            if(receipt!=null)deliverReceipts();
        }
        send("ack",RelayCrypto.map("id",id),false);
    }
    Map<String,Object> request(String method,String path,Map<String,Object> body,int seconds) throws IOException {
        return request(method,path,body,seconds,null);
    }
    Map<String,Object> request(String method,String path,Map<String,Object> body,int seconds,
                               java.util.function.BooleanSupplier current) throws IOException {
        if(!paired || closed || identityMismatch)throw new IOException(state);
        if(waiters.size()>=32)throw new IOException("连接繁忙，请稍后再试");
        // Desktop RPC has a 30s deadline. The phone's former 50s long polls must fit that budget.
        java.util.regex.Matcher waits=java.util.regex.Pattern.compile("([?&]wait=)([0-9]+)").matcher(path);
        StringBuffer clamped=new StringBuffer();while(waits.find())waits.appendReplacement(clamped,waits.group(1)+Math.min(20,Integer.parseInt(waits.group(2))));waits.appendTail(clamped);path=clamped.toString();
        String id=UUID.randomUUID().toString().replace("-","");Waiter waiting=new Waiter();waiting.read="GET".equals(method);
        waiting.sync=current!=null && waiting.read && (path.equals("/v1/sync") || path.startsWith("/v1/sync?"));waiters.put(id,waiting);
        boolean durableRegistered=false;
        try{
            if(current!=null && !current.getAsBoolean())throw new IOException("同步订阅已改变");
            Map<String,Object> payload=RelayCrypto.map("request",id,"method",method,"path",path,"runtime_id",config.get("runtime_id"));if(body!=null)payload.put("body",body);
            // An image upload registers new content and is never retried implicitly: when it times
            // out, the owner retries it. Keeping it durable replayed megabytes every few seconds.
            boolean durable="POST".equals(method)&&!path.startsWith("/v1/images");
            if(durable){
                if(durableWrites.size()>=16)throw new IOException("仍有未核实请求，请等待连接恢复");
                synchronized(this){
                    durableWrites.put(id,payload);
                    try { if(RelayCrypto.json(new ArrayList<>(durableWrites.values())).length()>8*1024*1024)throw new IOException("待发内容过多，请等待恢复");persist();durableRegistered=true; }
                    catch(Exception failure){durableWrites.remove(id);throw failure;}
                } // Seal durable intent before any possible remote side effect.
            }
            traceRelay(payload,TurnTiming.Stage.RELAY_QUEUED);
            waiting.packet=Json.string(send("request",payload,true),"id");return waiting.answer.get(Math.min(30,Math.max(3,seconds)),TimeUnit.SECONDS);
        }catch(TimeoutException error){throw new SocketTimeoutException("电脑尚未确认，连接恢复后可用原消息编号重试");}
        catch(InterruptedException error){Thread.currentThread().interrupt();throw new IOException("连接等待已取消",error);}
        catch(ExecutionException error){
            if(error.getCause() instanceof RelayOutcomeUnknownException)throw (RelayOutcomeUnknownException)error.getCause();
            String message=String.valueOf(error.getCause().getMessage());
            if(durableRegistered && !message.startsWith("远端返回 "))throw new SocketTimeoutException("结果待确认，连接恢复后自动核对");
            throw new IOException(message);
        }
        catch(Exception error){
            if(durableRegistered)throw new SocketTimeoutException("结果待确认，连接恢复后自动核对");
            throw new IOException("加密请求失败："+error.getMessage(),error);
        }
        finally{synchronized(this){
            waiters.remove(id);
            if(restrictedReplay(durableWrites.get(id)) && uncertainWrites.add(id)){
                discardRequestPackets(id);
                try{persist();}catch(RuntimeException ignored){/* In-memory pause stays; restart checks before replay. */}
            }
            if(waiting.packet!=null){packets.remove(waiting.packet);sent.remove(waiting.packet);packetRequests.remove(waiting.packet);}
        }}
    }
    /** Only message requests with a random client ID may contribute timing; never log payloads. */
    private static void traceRelay(Map<String,Object> payload,TurnTiming.Stage phase){
        if(payload==null||!"POST".equals(payload.get("method"))||!"/v1/messages".equals(payload.get("path")))return;
        Object raw=payload.get("body");if(!(raw instanceof Map))return;
        @SuppressWarnings("unchecked") Map<String,Object> body=(Map<String,Object>)raw;
        String id=Json.string(body,"client_id");if(id.matches("[a-f0-9]{32}"))TurnTiming.stage(id,phase);
    }
    void cancelReads(){for(Waiter waiting:waiters.values())if(waiting.read)waiting.answer.completeExceptionally(new IOException("已切换服务"));}
    void cancelSyncReads(){for(Waiter waiting:waiters.values())if(waiting.sync)waiting.answer.completeExceptionally(new IOException("同步订阅已改变"));}
    /** A network switch can leave a TCP socket apparently open on the old route.
     * Keep the original durable writes and packet IDs; a fresh peer hello is required. */
    synchronized void networkChanged(){
        if(closed || identityMismatch)return;
        WebSocket previous=socket;socket=null;opened=false;availability.clear();
        relayProof=null;nextConnect=0;failures=0;sent.clear();
        if(previous!=null)previous.cancel();
        status("网络已变化，正在自动恢复连接");
        try{timer.execute(()->{try{tick();}catch(Exception ignored){}});}catch(RejectedExecutionException ignored){}
    }
    @Override public synchronized void close(){closed=true;if(socket!=null)socket.cancel();socket=null;timer.shutdownNow();incoming.shutdownNow();for(Waiter waiter:waiters.values())waiter.answer.completeExceptionally(new IOException("连接已关闭"));http.dispatcher().executorService().shutdown();http.connectionPool().evictAll();}
}
