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
    interface Observer { void save(Map<String,Object> state); void ready(String url,String name); void state(String message); }
    private final Map<String,Object> config;
    private final Map<String,Object> host;
    private final Map<String,Object> relay;
    private final String space;
    private final RelayCrypto crypto;
    private final Observer observer;
    private final OkHttpClient http;
    private final ScheduledExecutorService timer=Executors.newSingleThreadScheduledExecutor();
    private final ExecutorService incoming=Executors.newSingleThreadExecutor();
    private final java.util.concurrent.atomic.AtomicInteger queuedIncoming=new java.util.concurrent.atomic.AtomicInteger();
    private final Map<String,Map<String,Object>> packets=new ConcurrentHashMap<>();
    private final Map<String,Waiter> waiters=new ConcurrentHashMap<>();
    private final Set<String> received=Collections.synchronizedSet(new LinkedHashSet<>());
    private volatile WebSocket socket;
    private volatile boolean opened,closed,paired,identityMismatch;
    private final Map<String,Map<String,Object>> durableWrites=new ConcurrentHashMap<>();
    private long lastReplay;
    private long nextConnect; private int failures;
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
        persist();
        timer.scheduleWithFixedDelay(()->{try{tick();}catch(Exception ignored){}},0,1,TimeUnit.SECONDS);
    }
    static Map<String,Object> invitation(String text,String name) throws Exception {
        if(text.length()>16384 || !text.startsWith("xiaoyou://join#"))throw new Exception("请粘贴小幽邀请");
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
    String url(){return "relay://"+Json.string(host,"id");}
    String status(){return state;}
    boolean isPaired(){return paired;}
    boolean isActive(){return !closed && (paired || Json.number(config,"expires")>=System.currentTimeMillis());}
    private void status(String message){state=message;observer.state(message);}
    private synchronized void persist(){config.put("paired",paired);config.put("pending_writes",new ArrayList<>(durableWrites.values()));observer.save(new LinkedHashMap<>(config));}
    private synchronized void tick() throws Exception {
        if(closed)return;
        if(socket==null && System.currentTimeMillis()>=nextConnect)connect();
        if(!opened)return;
        if(!paired){
            if(Json.number(config,"expires")<System.currentTimeMillis()){close();status("邀请已过期，请重新配对");return;}
            if(!packets.values().stream().anyMatch(p->"join".equals(p.get("kind"))))send("join",RelayCrypto.map("token",config.get("token"),"device",crypto.identity,"expires",config.get("expires")),true);
        }
        if(paired && !identityMismatch && !Json.string(config,"runtime_id").isEmpty() && System.currentTimeMillis()-lastReplay>5000){
            lastReplay=System.currentTimeMillis();
            for(Map<String,Object> payload:durableWrites.values())if(!waiters.containsKey(Json.string(payload,"request")))send("request",payload,true);
        }
        for(Map<String,Object> packet:packets.values()){
            if(((Number)packet.get("expires")).longValue()<System.currentTimeMillis())packets.remove(packet.get("id"));
            else socket.send(RelayCrypto.json(RelayCrypto.map("type","packet","packet",packet)));
        }
    }
    private synchronized void connect(){
        if(closed)return;
        status("正在连接加密入口");String base=Json.string(relay,"url").replaceAll("/$","");
        Request request=new Request.Builder().url(base+"/v1/connect?device="+crypto.identity.get("id")+"&room="+relay.get("room")).header("Authorization","Bearer "+Json.string(relay,"token")).build();
        socket=http.newWebSocket(request,new WebSocketListener(){
            @Override public void onOpen(WebSocket ws,Response response){synchronized(RelayConnection.this){if(socket!=ws || closed){ws.cancel();return;}opened=true;failures=0;status(paired?"加密入口已连接，等待电脑上线":"等待电脑确认配对");}}
            @Override public void onMessage(WebSocket ws,String text){if(socket!=ws || closed || text.length()>8*1024*1024)return;if(queuedIncoming.incrementAndGet()>16){queuedIncoming.decrementAndGet();return;}incoming.execute(()->{try{receive(text);}catch(Exception ignored){}finally{queuedIncoming.decrementAndGet();}});}
            private void lost(WebSocket ws,Response response){synchronized(RelayConnection.this){if(socket!=ws)return;opened=false;socket=null;if(response!=null&&(response.code()==401||response.code()==403)){closed=true;status("通信入口凭据失效，请重新配对");return;}nextConnect=System.currentTimeMillis()+Math.min(15000,500L<<Math.min(failures++,5));status("连接中断，正在自动恢复");}}
            @Override public void onFailure(WebSocket ws,Throwable failure,Response response){lost(ws,response);}
            @Override public void onClosing(WebSocket ws,int code,String reason){ws.close(code,null);}
            @Override public void onClosed(WebSocket ws,int code,String reason){lost(ws,null);}
        });
    }
    private Map<String,Object> send(String kind,Map<String,Object> body,boolean durable) throws Exception {
        if(durable && packets.size()>=128)throw new IOException("加密队列繁忙，请稍后再试");
        Map<String,Object> packet=crypto.seal(host,space,kind,body);if(durable)packets.put(Json.string(packet,"id"),packet);
        WebSocket current=socket;if(opened&&current!=null)current.send(RelayCrypto.json(RelayCrypto.map("type","packet","packet",packet)));return packet;
    }
    @SuppressWarnings("unchecked") private void receive(String text) throws Exception {
        Map<String,Object> envelope=Json.parseObject(text);if(envelope==null||!"packet".equals(envelope.get("type")))return;
        Map<String,Object> packet=(Map<String,Object>)envelope.get("packet");if(packet==null||!space.equals(packet.get("space")))return;
        Map<String,Object> body=crypto.open(packet,host);String kind=Json.string(packet,"kind"),id=Json.string(packet,"id");
        if("ack".equals(kind)){packets.remove(Json.string(body,"id"));return;}
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
            if(known.isEmpty()){config.put("runtime_id",body.get("runtime_id"));persist();}status("加密连接已建立");observer.ready(url(),Json.string(body,"name"));return;
        }
        if(!received.add(id)){send("ack",RelayCrypto.map("id",id),false);return;}
        synchronized(received){while(received.size()>2500)received.remove(received.iterator().next());}
        if("response".equals(kind)){
            if(durableWrites.remove(Json.string(body,"request"))!=null)persist();
            Waiter waiting=waiters.get(Json.string(body,"request"));
            if(waiting!=null){if(body.containsKey("error"))waiting.answer.completeExceptionally(new IOException("远端返回 "+(int)Json.number(body,"status")+"："+Json.string(body,"error")));else if(body.get("value") instanceof Map)waiting.answer.complete((Map<String,Object>)body.get("value"));}
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
        try{
            if(current!=null && !current.getAsBoolean())throw new IOException("同步订阅已改变");
            Map<String,Object> payload=RelayCrypto.map("request",id,"method",method,"path",path,"runtime_id",config.get("runtime_id"));if(body!=null)payload.put("body",body);
            if("POST".equals(method)){
                if(durableWrites.size()>=16)throw new IOException("仍有未核实请求，请等待连接恢复");
                synchronized(this){
                    durableWrites.put(id,payload);
                    try { if(RelayCrypto.json(new ArrayList<>(durableWrites.values())).length()>8*1024*1024)throw new IOException("待发内容过多，请等待恢复");persist(); }
                    catch(Exception failure){durableWrites.remove(id);throw failure;}
                } // Seal durable intent before any possible remote side effect.
            }
            waiting.packet=Json.string(send("request",payload,true),"id");return waiting.answer.get(Math.min(30,Math.max(3,seconds)),TimeUnit.SECONDS);
        }catch(TimeoutException error){throw new SocketTimeoutException("电脑尚未确认，连接恢复后可用原消息编号重试");}
        catch(InterruptedException error){Thread.currentThread().interrupt();throw new IOException("连接等待已取消",error);}
        catch(ExecutionException error){throw new IOException(error.getCause().getMessage());}
        catch(Exception error){throw new IOException("加密请求失败："+error.getMessage(),error);}
        finally{waiters.remove(id);if(waiting.packet!=null)packets.remove(waiting.packet);}
    }
    void cancelReads(){for(Waiter waiting:waiters.values())if(waiting.read)waiting.answer.completeExceptionally(new IOException("已切换服务"));}
    void cancelSyncReads(){for(Waiter waiting:waiters.values())if(waiting.sync)waiting.answer.completeExceptionally(new IOException("同步订阅已改变"));}
    @Override public synchronized void close(){closed=true;if(socket!=null)socket.cancel();socket=null;timer.shutdownNow();incoming.shutdownNow();for(Waiter waiter:waiters.values())waiter.answer.completeExceptionally(new IOException("连接已关闭"));http.dispatcher().executorService().shutdown();http.connectionPool().evictAll();}
}
