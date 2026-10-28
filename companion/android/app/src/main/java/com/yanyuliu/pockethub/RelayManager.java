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
    private static final Map<String,java.lang.ref.WeakReference<android.app.AlertDialog>> waiting=new HashMap<>();
    private static final String ALIAS="xiaoyou-relay-v1";
    static synchronized void init(Context context) throws Exception {
        if(app!=null)return;app=context.getApplicationContext();storage=app.getSharedPreferences("relay-v1",Context.MODE_PRIVATE);
        ArtifactDownload.relayBytes=RelayManager::binary;
        boolean failed=false;
        for(Map.Entry<String,?> entry:storage.getAll().entrySet())if(entry.getKey().startsWith("peer:") && entry.getValue() instanceof String) {
            try { open(Json.parseObject(decrypt((String)entry.getValue()))); } catch(Exception failure) { failed=true; }
        }
        if(failed)toast("部分加密配对暂未恢复，原记录已保留，请检查系统安全存储。");
    }
    static synchronized String join(Context context,String invitation) {
        try {
            init(context);Map<String,Object> config=RelayConnection.invitation(invitation,android.os.Build.MODEL);
            @SuppressWarnings("unchecked") Map<String,Object> host=(Map<String,Object>)config.get("device");RelayCrypto.validateDevice(host);RelayConnection.validateRelay(cast(config.get("relay")));
            String url="relay://"+Json.string(host,"id");if(links.containsKey(url) && !links.get(url).isPaired()) remove(url);
            if(links.containsKey(url))return "这台电脑已有配对记录，请选择已有连接；不会覆盖设备密钥";
            if(RuntimeClient.targets(context).size()>=RuntimeClient.MAX_TARGETS)return "已达到电脑连接上限，请先移除旧连接";
            open(config);
            if(context instanceof android.app.Activity) {
                android.app.Activity activity=(android.app.Activity)context;
                android.app.AlertDialog popup=new android.app.AlertDialog.Builder(activity)
                    .setTitle("请在电脑上确认连接").setMessage("确认后会自动连接。")
                    .setNegativeButton("取消",(dialog,which)->cancelPending(url)).create();
                waiting.put(url,new java.lang.ref.WeakReference<>(popup));
                popup.setOnCancelListener(dialog->cancelPending(url));
                popup.setOnDismissListener(dialog->{synchronized(RelayManager.class){waiting.remove(url);}});
                popup.setOwnerActivity(activity);popup.show();
            } else toast("请在电脑上确认连接");
            return null;
        }catch(Exception error){return "未能请求加密配对："+error.getMessage();}
    }
    private static synchronized void cancelPending(String url) {
        RelayConnection link=links.get(url);if(link!=null&&!link.isPaired())remove(url);
    }
    private static void finishWaiting(String url,String message) {
        new Handler(Looper.getMainLooper()).post(()->{
            android.app.AlertDialog dialog;
            synchronized(RelayManager.class){java.lang.ref.WeakReference<android.app.AlertDialog> ref=waiting.remove(url);dialog=ref==null?null:ref.get();}
            if(dialog!=null&&dialog.isShowing()) {
                android.app.Activity owner=dialog.getOwnerActivity();dialog.dismiss();toast(message);
                if("已连接".equals(message)&&owner instanceof MainActivity&&!owner.isFinishing()&&!owner.isDestroyed())((MainActivity)owner).onPairingConnected(url);
            }
        });
    }
    @SuppressWarnings("unchecked") private static Map<String,Object> cast(Object value){return (Map<String,Object>)value;}
    private static synchronized void open(Map<String,Object> config) throws Exception {
        if(config==null)throw new IOException("配对存储无法读取，原记录未删除");
        final String key="peer:"+Json.string(cast(config.get("device")),"id");
        final String address="relay://"+Json.string(cast(config.get("device")),"id");
        RelayConnection connection=new RelayConnection(config,new RelayConnection.Observer(){
            public void save(Map<String,Object> state){try{if(!storage.edit().putString(key,encrypt(RelayCrypto.json(state))).commit())throw new IOException("配对未能持久化");}catch(Exception failure){throw new IllegalStateException("系统安全存储不可用");}}
            public void ready(String url,String name){new Handler(Looper.getMainLooper()).post(()->{RuntimeClient.registerRelay(app,url,name);finishWaiting(url,"已连接");});}
            public void state(String message){ if(message.contains("已过期")||message.contains("连接已取消"))finishWaiting(address,message.contains("已过期")?"二维码已过期，请重新扫码":"连接已取消"); if(message.contains("已过期") && RuntimeClient.selected(app)==null)app.stopService(new android.content.Intent(app,RuntimeWatchService.class)); }
        });links.put(connection.url(),connection);
    }
    static boolean hasConnections(){return links.values().stream().anyMatch(RelayConnection::isActive);}
    static String request(String method,String address,byte[] bytes,String contentType,int seconds) throws IOException {
        return request(method,address,bytes,contentType,seconds,null);
    }
    static String request(String method,String address,byte[] bytes,String contentType,int seconds,
                          java.util.function.BooleanSupplier current) throws IOException {
        URI uri=URI.create(address);RelayConnection link=links.get("relay://"+uri.getHost());if(link==null)throw new IOException("加密连接尚未恢复，请重新打开 App");
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
    static void remove(String url){RelayConnection link=links.remove(url);if(link!=null){link.close();storage.edit().remove("peer:"+URI.create(url).getHost()).commit();}}
    private static void toast(String text){new Handler(Looper.getMainLooper()).post(()->Toast.makeText(app,text,Toast.LENGTH_LONG).show());}
    private static SecretKey key() throws Exception {
        KeyStore store=KeyStore.getInstance("AndroidKeyStore");store.load(null);
        if(!store.containsAlias(ALIAS)){KeyGenerator generator=KeyGenerator.getInstance("AES","AndroidKeyStore");generator.init(new KeyGenParameterSpec.Builder(ALIAS,KeyProperties.PURPOSE_ENCRYPT|KeyProperties.PURPOSE_DECRYPT).setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());generator.generateKey();}
        return (SecretKey)store.getKey(ALIAS,null);
    }
    private static String encrypt(String text) throws Exception {Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.ENCRYPT_MODE,key());return RelayCrypto.encode(cipher.getIV())+"."+RelayCrypto.encode(cipher.doFinal(text.getBytes(StandardCharsets.UTF_8)));}
    private static String decrypt(String text) throws Exception {String[] parts=text.split("\\.",2);Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.DECRYPT_MODE,key(),new GCMParameterSpec(128,RelayCrypto.decode(parts[0],12)));return new String(cipher.doFinal(RelayCrypto.decode(parts[1],-1)),StandardCharsets.UTF_8);}
}
