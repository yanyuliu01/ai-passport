package com.yanyuliu.pockethub;

import android.content.Context;
import android.content.SharedPreferences;
import java.io.IOException;
import java.util.*;
import java.util.concurrent.*;

/** Fixed-server account login. Old pairing records remain sealed and inactive. */
final class AccountManager {
    static final String SERVER="wss://150.158.176.23";
    static final String PIN="9c5e10134c225cf6d19e566532ee8464f85f1be97ccff4ccf8aa3653ba1f442b";
    private static final ScheduledExecutorService worker=Executors.newSingleThreadScheduledExecutor();
    private static volatile Set<String> allowed=Collections.emptySet();
    private static volatile String message="登录小鱼账号，连接自己的电脑",username="";
    private static volatile boolean busy,signedIn;
    private static Context app;
    private static SharedPreferences storage;
    private static volatile Map<String,Object> session;
    private static RelayCrypto crypto;
    private static AccountAPI api;
    interface Callback {void done(String problem);}
    static synchronized void init(Context context) throws Exception {
        if(app!=null)return;
        Context next=context.getApplicationContext();
        SharedPreferences prefs=next.getSharedPreferences("xiaoyu-account-v1",Context.MODE_PRIVATE);
        String sealed=prefs.getString("device","");
        RelayCrypto identity=new RelayCrypto(android.os.Build.MODEL,sealed.isEmpty()?null:RelayManager.decrypt(sealed));
        if(!prefs.edit().putString("device",RelayManager.encrypt(identity.seed)).commit())throw new IOException("登录身份未能保存");
        AccountAPI nextAPI=new AccountAPI(SERVER,PIN);
        crypto=identity;api=nextAPI;storage=prefs;app=next;
        String saved=prefs.getString("session","");
        if(!saved.isEmpty())try{session=Json.parseObject(RelayManager.decrypt(saved));validate(session);signedIn=true;username=Json.string(session,"username");}
        catch(Exception failure){session=null;message="登录已过期或暂不可恢复，请重新登录；原记录保留";}
        worker.scheduleWithFixedDelay(()->{try{refresh();}catch(Exception ignored){}},0,5,TimeUnit.SECONDS);
    }
    static String status(){return message;}
    static String username(){return username;}
    static String accountId(){Map<String,Object> current=session;return signedIn && current!=null?Json.string(current,"id"):"";}
    static boolean signedIn(){return signedIn;}
    static boolean busy(){return busy;}
    static boolean allowed(String url){return signedIn && allowed.contains(url);}
    static void login(Context context,String user,String password,Callback callback){
        if(busy){callback.done("正在连接，请稍候");return;}busy=true;
        worker.execute(()->{String problem=null;try{
            RelayManager.init(context);init(context);
            if(signedIn)throw new IOException("请先退出当前账号");
            Map<String,Object> value=call("login",RelayCrypto.map("username",user,"password",password,"device",crypto.identity),null);validate(value);
            @SuppressWarnings("unchecked") Map<String,Object> vault=(Map<String,Object>)value.get("vault");
            AccountIdentity.verify(password,Json.string(value,"id"),Json.string(value,"space"),vault);
            value.put("seed",crypto.seed);
            if(!storage.edit().putString("session",RelayManager.encrypt(RelayCrypto.json(value))).commit())throw new IOException("登录凭据未能保存");
            session=value;signedIn=true;username=Json.string(value,"username");message="已登录，正在发现自己的电脑";refresh();
        }catch(Exception failure){problem="登录未完成："+safe(failure);}finally{busy=false;}deliver(callback,problem);});
    }
    static void logout(Callback callback){
        if(busy){callback.done("正在连接，请稍候");return;}busy=true;
        worker.execute(()->{String problem=null;try{
            if(session!=null)call("logout",Collections.emptyMap(),Json.string(session,"token"));
            if(storage!=null && !storage.edit().remove("session").commit())throw new IOException("退出状态未能保存");
            pause();session=null;username="";message="已退出，原记录和未完成请求保留";
        }catch(Exception failure){problem="退出未完成："+safe(failure);}finally{busy=false;}deliver(callback,problem);});
    }
    private static void deliver(Callback callback,String problem){new android.os.Handler(android.os.Looper.getMainLooper()).post(()->callback.done(problem));}
    private static void validate(Map<String,Object> value) throws Exception {
        if(value==null || !Json.string(value,"id").matches("[a-f0-9]{32}") || !Json.string(value,"token").matches("[A-Za-z0-9_-]{43}")
            || Json.number(value,"expires")<=System.currentTimeMillis() || Json.string(value,"relayToken").length()<32)throw new IOException("登录凭据无效或已过期");
    }
    private static void refresh(){
        if(!signedIn || session==null)return;
        try{
            validate(session);
            Map<String,Object> value=call("presence",Collections.emptyMap(),Json.string(session,"token"));
            String space=Json.string(session,"space");if(!space.matches("[a-f0-9]{32}") || !space.equals(value.get("space")))throw new AccountAPI.AuthExpired();
            Map<String,Map<String,Object>> devices=new LinkedHashMap<>();
            for(Object raw:Json.list(value,"devices")){
                @SuppressWarnings("unchecked") Map<String,Object> device=(Map<String,Object>)((Map<?,?>)raw).get("device");
                RelayCrypto.validateDevice(device);if(!device.get("id").equals(crypto.identity.get("id")))devices.put("relay://"+Json.string(device,"id")+"."+Json.string(session,"id"),device);
            }
            Set<String> urls=Collections.unmodifiableSet(new HashSet<>(devices.keySet()));allowed=urls;
            RelayManager.retainAccount(urls);
            for(Map.Entry<String,Map<String,Object>> item:devices.entrySet())RelayManager.accountPeer(Json.string(session,"id"),RelayCrypto.map(
                "device",item.getValue(),"relay",RelayCrypto.map("url",SERVER,"tlsFingerprint",PIN,"token",session.get("relayToken"),"room",session.get("id")),
                "space",space,"seed",crypto.seed,"name",android.os.Build.MODEL,"paired",true));
            message=devices.isEmpty()?"已登录，等待电脑上线；电脑登录同一账号即可互通":"已登录，通过服务器连接自己的电脑";
        }catch(AccountAPI.AuthExpired failure){pause();message="登录已过期，请重新登录；原记录保留";}
        catch(Exception failure){
            if(Json.number(session,"expires")<=System.currentTimeMillis()){pause();message="登录已过期，请重新登录；原记录保留";}
            else message="服务器连接暂未恢复，正在重试；原记录保留";
        }
    }
    private static void pause(){signedIn=false;allowed=Collections.emptySet();RelayManager.retainAccount(allowed);RelayManager.cancelReads();}
    private static Map<String,Object> call(String path,Map<String,Object> body,String token) throws Exception {return api.call(path,body,token);}
    private static String safe(Exception failure){return failure instanceof IOException?failure.getMessage():"账号互通身份验证失败，请检查账号和密码；原配置保留";}
}
