package com.yanyuliu.pockethub;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Handler;
import android.os.Looper;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.widget.Toast;
import java.io.IOException;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import javax.crypto.*;
import javax.crypto.spec.GCMParameterSpec;

/** Android lifecycle and OS-sealed pairing storage, separate from the protocol engine. */
final class RelayManager {
    private static final Map<String,RelayConnection> links=new ConcurrentHashMap<>();
    private static Context app;
    private static SharedPreferences storage;
    private static final String ALIAS="xiaoyou-relay-v1";
    static synchronized void init(Context context) throws Exception {
        if(app!=null){AccountManager.init(context);return;}app=context.getApplicationContext();storage=app.getSharedPreferences("relay-v1",Context.MODE_PRIVATE);
        ArtifactDownload.relayBytes=RelayManager::binary;
        AccountManager.init(context);
    }
    static synchronized String join(Context context,String invitation) {
        return "连接已统一，请登录小幽账号；原配对记录保留";
    }
    static synchronized void retainAccount(Set<String> urls) {
        for(String url:new ArrayList<>(links.keySet()))if(!urls.contains(url)){RelayConnection link=links.remove(url);if(link!=null)link.close();}
    }
    static synchronized void accountPeer(String account,Map<String,Object> config) throws Exception {
        String id=Json.string(cast(config.get("device")),"id");config.put("account",account);String url=RelayConnection.address(config);
        if(links.containsKey(url))return;
        String key="account:"+account+":peer:"+id;
        String sealed=storage.getString(key,"");
        if(!sealed.isEmpty()){
            Map<String,Object> old=Json.parseObject(decrypt(sealed));
            if(old==null || !Objects.equals(old.get("space"),config.get("space")) || !Objects.equals(old.get("seed"),config.get("seed"))
                || !Objects.equals(cast(old.get("device")).get("publicKey"),cast(config.get("device")).get("publicKey")))throw new IOException("原电脑身份不一致，原记录保留");
            old.put("relay",config.get("relay"));old.put("device",config.get("device"));config=old;
        }
        open(config,key);
    }
    @SuppressWarnings("unchecked") private static Map<String,Object> cast(Object value){return (Map<String,Object>)value;}
    private static synchronized void open(Map<String,Object> config,String key) throws Exception {
        if(config==null)throw new IOException("配对存储无法读取，原记录未删除");
        RelayConnection connection=new RelayConnection(config,new RelayConnection.Observer(){
            public void save(Map<String,Object> state){try{if(!storage.edit().putString(key,encrypt(RelayCrypto.json(state))).commit())throw new IOException("配对未能持久化");}catch(Exception failure){throw new IllegalStateException("系统安全存储不可用");}}
            public void ready(String url,String name){new Handler(Looper.getMainLooper()).post(()->RuntimeClient.registerRelay(app,url,name));}
            public void state(String message){ if(message.contains("已过期") && RuntimeClient.selected(app)==null)app.stopService(new android.content.Intent(app,RuntimeWatchService.class)); }
        });links.put(connection.url(),connection);
    }
    static boolean online(String url){RelayConnection link=links.get(url);return AccountManager.allowed(url) && link!=null && link.online();}
    static boolean hasConnections(){return links.values().stream().anyMatch(RelayConnection::isActive);}
    static String request(String method,String address,byte[] bytes,String contentType,int seconds) throws IOException {
        return request(method,address,bytes,contentType,seconds,null);
    }
    static String request(String method,String address,byte[] bytes,String contentType,int seconds,
                          java.util.function.BooleanSupplier current) throws IOException {
        URI uri=URI.create(address);String target=RelayConnection.target(uri);if(!AccountManager.allowed(target))throw new IOException("请登录小幽账号，等待这台电脑恢复连接");RelayConnection link=links.get(target);if(link==null)throw new IOException("加密连接尚未恢复，请重新打开 App");
        Map<String,Object> body=null;
        if(bytes!=null){
            if(contentType!=null && contentType.startsWith("application/json")){body=Json.parseObject(new String(bytes,StandardCharsets.UTF_8));if(body==null)throw new IOException("请求格式无效");}
            else {if(bytes.length>2_800_000)throw new IOException("语音或图片超过加密通道单次上限，请缩短或压缩后重试");body=RelayCrypto.map("_relay_binary",RelayCrypto.encode(bytes),"content_type",contentType);}
        }
        String path=uri.getRawPath()+(uri.getRawQuery()==null?"":"?"+uri.getRawQuery());
        return RelayCrypto.json(link.request(method,path,body,seconds,current));
    }
    static byte[] binary(String address) throws Exception {
        Map<String,Object> response=Json.parseObject(request("GET",address,null,null,30));
        return RelayCrypto.decode(Json.string(response,"_relay_binary"),-1);
    }
    static void cancelReads(){for(RelayConnection link:links.values())link.cancelReads();}
    static void cancelSyncReads(){for(RelayConnection link:links.values())link.cancelSyncReads();}
    static void remove(String url){RelayConnection link=links.remove(url);if(link!=null){link.close();String authority=URI.create(url).getRawAuthority();if(authority!=null && authority.matches("[a-f0-9]{32}"))storage.edit().remove("peer:"+authority).commit();}}
    private static void toast(String text){new Handler(Looper.getMainLooper()).post(()->Toast.makeText(app,text,Toast.LENGTH_LONG).show());}
    private static SecretKey key() throws Exception {
        KeyStore store=KeyStore.getInstance("AndroidKeyStore");store.load(null);
        if(!store.containsAlias(ALIAS)){KeyGenerator generator=KeyGenerator.getInstance("AES","AndroidKeyStore");generator.init(new KeyGenParameterSpec.Builder(ALIAS,KeyProperties.PURPOSE_ENCRYPT|KeyProperties.PURPOSE_DECRYPT).setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());generator.generateKey();}
        return (SecretKey)store.getKey(ALIAS,null);
    }
    static String encrypt(String text) throws Exception {Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.ENCRYPT_MODE,key());return RelayCrypto.encode(cipher.getIV())+"."+RelayCrypto.encode(cipher.doFinal(text.getBytes(StandardCharsets.UTF_8)));}
    static String decrypt(String text) throws Exception {String[] parts=text.split("\\.",2);Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.DECRYPT_MODE,key(),new GCMParameterSpec(128,RelayCrypto.decode(parts[0],12)));return new String(cipher.doFinal(RelayCrypto.decode(parts[1],-1)),StandardCharsets.UTF_8);}
}
