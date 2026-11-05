package com.yanyuliu.pockethub;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Map;
import java.util.concurrent.Executor;

/**
 * Explicit, account-bound migration engine. No app component invokes this until an
 * authenticated user approves a specific source and an atomic storage adapter exists.
 * The source sync document stays an opaque archive; its cursor never replaces the live cursor.
 */
final class LegacyHistoryImport {
    static final class Binding {
        final String transaction, account, runtime, subject, source, target;
        Binding(String transaction,String account,String runtime,String subject,String source,String target){
            this.transaction=transaction;this.account=account;this.runtime=runtime;this.subject=subject;this.source=source;this.target=target;
        }
        boolean valid(){
            return transaction!=null && transaction.matches("[a-f0-9]{32}") && account!=null && account.matches("[a-f0-9]{32}")
                    && runtime!=null && runtime.matches("[a-f0-9]{32}") && subject!=null && subject.matches("[a-f0-9]{32}")
                    && source!=null && !source.isEmpty() && target!=null && !target.isEmpty() && !source.equals(target);
        }
    }
    static final class Capture {
        final String targetVersion,sourceVersion,sourceHistory,sourceSync;
        final ChatHistory targetHistory;
        Capture(String targetVersion,String sourceVersion,String sourceHistory,String sourceSync,ChatHistory targetHistory){
            this.targetVersion=targetVersion;this.sourceVersion=sourceVersion;this.sourceHistory=sourceHistory;
            this.sourceSync=sourceSync;this.targetHistory=targetHistory;
        }
    }
    static final class Plan {
        final Binding binding;
        final String targetVersion,sourceVersion,history,sourceHistoryHash,sourceSyncHash;
        final ChatHistory.ImportCounts counts;
        Plan(Binding binding,Capture captured,String history,String historyHash,String syncHash,ChatHistory.ImportCounts counts){
            this.binding=binding;this.targetVersion=captured.targetVersion;this.sourceVersion=captured.sourceVersion;
            this.history=history;this.sourceHistoryHash=historyHash;this.sourceSyncHash=syncHash;this.counts=counts;
        }
    }
    interface Backend {
        /** Must verify the live signed-in account and source/target Runtime + subject. */
        boolean authorized(Binding binding);
        /** Captures target history and versions consistently without mutating either archive. */
        Capture capture(Binding binding) throws Exception;
        /**
         * Atomically verify the live identity and both captured versions, preserve the original
         * history AND sync bytes, then install candidate and binding; false leaves all unchanged.
         * Replaying the same transaction must be idempotent. No partial commit is permitted.
         */
        boolean commit(Plan plan) throws Exception;
    }
    interface Result { void complete(Plan plan, String error); }

    static void start(Binding binding,boolean explicitlyConfirmed,Backend backend,Executor background,Result done) {
        if(!explicitlyConfirmed || binding==null || !binding.valid()) { done.complete(null,"未确认历史归属"); return; }
        background.execute(()->{
            try{
                if(!backend.authorized(binding)){done.complete(null,"账号或 Runtime 已变化");return;}
                Capture input=backend.capture(binding);
                if(input==null || input.targetHistory==null || empty(input.sourceVersion) || empty(input.targetVersion)
                        || input.sourceHistory==null || input.sourceSync==null){done.complete(null,"原记录暂不可读");return;}
                Map<String,Object> raw=Json.parseObject(input.sourceHistory);
                if(raw==null || !(raw.get("messages") instanceof java.util.List)) {done.complete(null,"原历史格式无效");return;}
                ChatHistory candidate=input.targetHistory.copy();
                ChatHistory.ImportCounts counts=candidate.importLegacy(ChatHistory.restore(input.sourceHistory));
                Plan plan=new Plan(binding,input,candidate.json(),hash(input.sourceHistory),hash(input.sourceSync),counts);
                if(!backend.authorized(binding) || !backend.commit(plan)){done.complete(null,"账号或历史已变化，请重新核对");return;}
                done.complete(plan,null);
            }catch(Exception failure){done.complete(null,"导入未完成，原记录保留");}
        });
    }
    private static boolean empty(String value){return value==null || value.isEmpty();}
    private static String hash(String value) throws Exception {
        byte[] bytes=MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8));
        StringBuilder result=new StringBuilder(64);for(byte b:bytes)result.append(String.format(java.util.Locale.ROOT,"%02x",b&255));return result.toString();
    }
    private LegacyHistoryImport(){}
}
