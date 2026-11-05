package com.yanyuliu.pockethub;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Explicit actions only. Archive work never runs on the chat or UI worker. */
final class LegacyArchiveController {
    private static final String QUERY_ID="00000000000000000000000000000000";
    interface Callback<T> { void done(T value,String error); }
    static final class Detail {final String text;final int attachments;Detail(String text,int attachments){this.text=text;this.attachments=attachments;}}
    static final class Scope {
        final Context app;final RuntimeClient.Target target;final String account,runtime,subject,key;final long generation;
        Scope(Context app,RuntimeClient.Target target,String account,String runtime,String subject,String key,long generation){
            this.app=app;this.target=target;this.account=account;this.runtime=runtime;this.subject=subject;this.key=key;this.generation=generation;
        }
        LegacyHistoryImport.Binding binding(String transaction,String source){return new LegacyHistoryImport.Binding(transaction,account,runtime,subject,source,key);}
        boolean current(){
            RuntimeClient.Target now=RuntimeClient.selected(app);
            return generation==AccountManager.historyGeneration() && account.equals(AccountManager.accountId()) && now!=null
                    && now.url.equals(target.url) && AccountManager.allowed(now.url) && runtime.equals(RuntimeClient.runtimeId(app,now.url))
                    && subject.equals(RuntimeClient.subjectId(app,now.url)) && key.equals(RuntimeClient.historyKey(app,now));
        }
        LegacyArchiveStore.Fence fence(){
            return (binding,sourceVersion,targetVersion,action)->{
                // Hashing and file IO must never hold the account fence or block the UI.
                if(!current())return false;
                if(!sourceVersion.isEmpty() && (!Long.toString(generation).equals(targetVersion)
                        || !sourceVersion.equals(currentSourceVersion(app,binding.source))))return false;
                boolean[] ran={false};Exception[] failure={null};
                boolean accepted=AccountManager.installHistory(generation,account,target.url,()->{
                    if(!current() || !account.equals(binding.account) || !runtime.equals(binding.runtime)
                            || !subject.equals(binding.subject) || !key.equals(binding.target))return;
                    try{
                        if(!sourceVersion.isEmpty() && !RuntimeClient.legacySourceMatches(target.url,binding.source,runtime,subject,
                                RuntimeClient.runtimeId(app,binding.source),RuntimeClient.subjectId(app,binding.source)))return;
                        action.run();ran[0]=true;
                    }catch(Exception error){failure[0]=error;}
                });
                if(failure[0]!=null)throw failure[0];return accepted && ran[0];
            };
        }
    }
    private static final ExecutorService WORKER=Executors.newSingleThreadExecutor();
    private static final Handler MAIN=new Handler(Looper.getMainLooper());
    static Scope scope(Context context){
        Context app=context.getApplicationContext();RuntimeClient.Target target=RuntimeClient.selected(app);
        if(target==null)return null;
        long generation=AccountManager.historyGeneration();String account=AccountManager.accountId();
        String runtime=RuntimeClient.runtimeId(app,target.url),subject=RuntimeClient.subjectId(app,target.url),key=RuntimeClient.historyKey(app,target);
        if(!account.matches("[a-f0-9]{32}") || !runtime.matches("[a-f0-9]{32}") || !subject.matches("[a-f0-9]{32}"))return null;
        Scope result=new Scope(app,target,account,runtime,subject,key,generation);return result.current()?result:null;
    }
    private static <T> void reply(Scope scope,Callback<T> callback,T value,String error){
        MAIN.post(()->{if(scope.current())callback.done(value,error);else callback.done(null,"账号或电脑已变化，请重新打开");});
    }
    static void sources(Scope scope,Callback<List<RuntimeClient.Target>> callback){
        WORKER.execute(()->{try{reply(scope,callback,RuntimeClient.legacyHistoryCandidates(scope.app,scope.target),null);}
            catch(RuntimeException error){reply(scope,callback,null,"无法读取旧连接，原记录保留");}});
    }
    static void list(Scope scope,Callback<List<LegacyArchiveStore.ArchiveRef>> callback){
        WORKER.execute(()->{try(LegacyArchiveAndroidDb db=new LegacyArchiveAndroidDb(scope.app)){
            LegacyArchiveStore store=new LegacyArchiveStore(db);reply(scope,callback,store.list(scope.binding(QUERY_ID,"listing"),scope.fence()),null);
        }catch(Exception error){reply(scope,callback,null,"无法读取旧对话归档，原记录保留");}});
    }
    static void importSource(Scope scope,RuntimeClient.Target source,Callback<Boolean> callback){
        WORKER.execute(()->{try{
            if(!scope.current() || !RuntimeClient.legacyHistoryCandidates(scope.app,scope.target).stream().anyMatch(x->x.url.equals(source.url)))
                throw new IllegalStateException("来源已变化");
            String key=ArtifactDownload.key(source.url);
            String history=scope.app.getSharedPreferences("conversation_history",Context.MODE_PRIVATE).getString(key,null);
            File syncFile=new File(new File(scope.app.getFilesDir(),"sync"),key+".json");
            if(history==null || !syncFile.isFile())throw new IllegalStateException("来源不完整");
            if(history.length()>LegacyArchiveStore.MAX_HISTORY_BYTES || syncFile.length()>LegacyArchiveStore.MAX_SYNC_BYTES){
                reply(scope,callback,false,"旧记录较大，暂无法关联；原记录仍保留");return;
            }
            byte[] sourceHistory=history.getBytes(StandardCharsets.UTF_8),sourceSync=Files.readAllBytes(syncFile.toPath());
            if(sourceHistory.length>LegacyArchiveStore.MAX_HISTORY_BYTES || sourceSync.length>LegacyArchiveStore.MAX_SYNC_BYTES){
                reply(scope,callback,false,"旧记录较大，暂无法关联；原记录仍保留");return;
            }
            String sourceVersion=hash(sourceHistory)+hash(sourceSync),targetVersion=Long.toString(scope.generation);
            try(LegacyArchiveAndroidDb db=new LegacyArchiveAndroidDb(scope.app)){
                LegacyArchiveStore store=new LegacyArchiveStore(db);
                List<LegacyArchiveStore.ArchiveRef> existing=store.list(scope.binding(QUERY_ID,"listing"),scope.fence());
                if(existing.stream().anyMatch(item->item.source.equals(source.url))){reply(scope,callback,false,"这份旧记录已关联，可直接查看");return;}
                if(existing.size()>=64){reply(scope,callback,false,"已关联的旧记录已达上限，请先撤销不需要的关联；原记录保留");return;}
                String transaction=UUID.randomUUID().toString().replace("-","");
                LegacyHistoryImport.Binding binding=scope.binding(transaction,source.url);
                boolean stored=store.archive(binding,true,sourceVersion,targetVersion,sourceHistory,sourceSync,scope.fence());
                reply(scope,callback,stored,stored?null:"账号或电脑已变化，原记录保留");
            }
        }catch(Exception error){reply(scope,callback,false,"关联未完成，原记录保留");}});
    }
    static void page(Scope scope,LegacyArchiveStore.ArchiveRef ref,long before,Callback<LegacyArchiveStore.Page> callback){
        WORKER.execute(()->{try(LegacyArchiveAndroidDb db=new LegacyArchiveAndroidDb(scope.app)){
            HubStore hub=HubStore.get();Set<String> ids=hub.archiveMessageIds();
            Set<String> deleted=hub.archiveConversationDeleted("default")?Collections.singleton("default"):Collections.emptySet();
            LegacyArchiveStore store=new LegacyArchiveStore(db);
            LegacyArchiveStore.Page page=store.page(scope.binding(ref.transaction,ref.source),"default",before,80,ids,deleted,scope.fence());
            reply(scope,callback,page,null);
        }catch(Exception error){reply(scope,callback,null,"旧对话暂时无法读取，原记录保留");}});
    }
    static void detail(Scope scope,LegacyArchiveStore.ArchiveRef ref,String id,Callback<Detail> callback){
        WORKER.execute(()->{try(LegacyArchiveAndroidDb db=new LegacyArchiveAndroidDb(scope.app)){
            HubStore hub=HubStore.get();Set<String> deleted=hub.archiveConversationDeleted("default")?Collections.singleton("default"):Collections.emptySet();
            String message=new LegacyArchiveStore(db).detail(scope.binding(ref.transaction,ref.source),"default",id,hub.archiveMessageIds(),deleted,scope.fence());
            Map<String,Object> parsed=message==null?null:Json.parseObject(message);
            Detail result=parsed==null?null:new Detail(Json.string(parsed,"text"),Json.list(parsed,"attachments").size());
            reply(scope,callback,result,result==null?"消息已在当前聊天中、已删除或暂不可读":null);
        }catch(java.io.IOException tooLarge){reply(scope,callback,null,"旧消息过长或校验未通过，暂不能完整打开；原记录仍保留");}
        catch(Exception error){reply(scope,callback,null,"旧消息暂时无法读取，原记录保留");}});
    }
    static void rollback(Scope scope,LegacyArchiveStore.ArchiveRef ref,Callback<Boolean> callback){
        WORKER.execute(()->{try(LegacyArchiveAndroidDb db=new LegacyArchiveAndroidDb(scope.app)){
            boolean ok=new LegacyArchiveStore(db).rollback(scope.binding(ref.transaction,ref.source),scope.fence());
            reply(scope,callback,ok,ok?null:"账号或电脑已变化，原记录保留");
        }catch(Exception error){reply(scope,callback,false,"暂时无法撤销关联，原记录保留");}});
    }
    private static String hash(byte[] value) throws Exception{byte[] bytes=MessageDigest.getInstance("SHA-256").digest(value);StringBuilder out=new StringBuilder();for(byte b:bytes)out.append(String.format(Locale.ROOT,"%02x",b&255));return out.toString();}
    private static String currentSourceVersion(Context app,String source) throws Exception{
        String key=ArtifactDownload.key(source);String history=app.getSharedPreferences("conversation_history",Context.MODE_PRIVATE).getString(key,null);
        File sync=new File(new File(app.getFilesDir(),"sync"),key+".json");
        if(history==null || !sync.isFile() || history.length()>LegacyArchiveStore.MAX_HISTORY_BYTES || sync.length()>LegacyArchiveStore.MAX_SYNC_BYTES)return "";
        byte[] historyBytes=history.getBytes(StandardCharsets.UTF_8),syncBytes=Files.readAllBytes(sync.toPath());
        if(historyBytes.length>LegacyArchiveStore.MAX_HISTORY_BYTES || syncBytes.length>LegacyArchiveStore.MAX_SYNC_BYTES)return "";
        return hash(historyBytes)+hash(syncBytes);
    }
    private LegacyArchiveController(){}
}
