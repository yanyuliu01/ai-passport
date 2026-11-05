package com.yanyuliu.pockethub;

import org.junit.Test;
import java.util.*;
import java.util.concurrent.Executor;
import static org.junit.Assert.*;

public class LegacyHistoryImportTest {
    static final String ACCOUNT="a".repeat(32),RUNTIME="b".repeat(32),SUBJECT="c".repeat(32),TRANSACTION="d".repeat(32);
    static LegacyHistoryImport.Binding binding(){return new LegacyHistoryImport.Binding(TRANSACTION,ACCOUNT,RUNTIME,SUBJECT,"old-direct","new-account");}
    static class Store implements LegacyHistoryImport.Backend {
        boolean allowed=true,changed;int captures,commits;String currentVersion="current-1",sourceVersion="source-1";
        ChatHistory current=new ChatHistory(),source=new ChatHistory();String sync="{\"cursor\":\"old-opaque\"}";
        LegacyHistoryImport.Plan installed;
        public boolean authorized(LegacyHistoryImport.Binding b){return allowed && b.account.equals(ACCOUNT) && b.runtime.equals(RUNTIME) && b.subject.equals(SUBJECT);}
        public LegacyHistoryImport.Capture capture(LegacyHistoryImport.Binding b){captures++;return new LegacyHistoryImport.Capture(currentVersion,sourceVersion,source.json(),sync,current.copy());}
        public boolean commit(LegacyHistoryImport.Plan p){
            commits++;if(changed || !allowed || !currentVersion.equals(p.targetVersion) || !sourceVersion.equals(p.sourceVersion))return false;
            installed=p;return true;
        }
    }
    @Test public void unapprovedAndWrongAccountNeverReadOrWrite() {
        Store store=new Store();List<Runnable> pending=new ArrayList<>();Executor executor=pending::add;
        LegacyHistoryImport.start(binding(),false,store,executor,(p,e)->assertNotNull(e));
        assertEquals(0,store.captures);assertTrue(pending.isEmpty());
        LegacyHistoryImport.Binding wrong=new LegacyHistoryImport.Binding(TRANSACTION,"e".repeat(32),RUNTIME,SUBJECT,"old","new");
        LegacyHistoryImport.start(wrong,true,store,executor,(p,e)->assertNotNull(e));pending.remove(0).run();
        assertEquals(0,store.captures);assertEquals(0,store.commits);
    }
    @Test public void backgroundPlanKeepsCurrentTombstonesAndSyncIndependent() {
        Store store=new Store();store.current.begin("current",null,"same text",10);
        store.current.eraseConversation("deleted");
        store.source.begin("old",null,"same text",1);
        store.source.begin("deleted-old",null,"private",2);store.source.scope("deleted-old","deleted",true);
        store.source.eraseConversation("source-only-delete");
        String before=store.source.json(),sync=store.sync;
        List<Runnable> pending=new ArrayList<>();
        LegacyHistoryImport.start(binding(),true,store,pending::add,(plan,error)->{assertNull(error);assertNotNull(plan);});
        assertEquals(0,store.captures);assertEquals(1,pending.size());pending.remove(0).run();
        assertEquals(before,store.source.json());assertEquals(sync,store.sync);assertNotNull(store.installed);
        assertEquals(2,store.installed.counts.added);assertEquals(2,store.installed.counts.deleted);
        ChatHistory merged=ChatHistory.restore(store.installed.history);
        assertEquals(4,merged.main().size());assertTrue(merged.main("deleted").isEmpty());
        merged.begin("new",null,"keep",3);merged.scope("new","source-only-delete",true);
        assertEquals(2,merged.main("source-only-delete").size());
        Map<String,Object> raw=Json.parseObject(store.installed.history);
        assertTrue(Json.list(raw,"messages").stream().map(Json::object).anyMatch(m->Json.string(m,"id").equals("current:you") && Json.string(m,"status").equals("sending")));
    }
    @Test public void versionChangeOrAuthorizationLossCannotCommit() {
        Store changed=new Store();changed.source.begin("old",null,"one",1);changed.changed=true;
        LegacyHistoryImport.start(binding(),true,changed,Runnable::run,(plan,error)->{assertNull(plan);assertNotNull(error);});
        assertNull(changed.installed);
        Store revoked=new Store(){ @Override public LegacyHistoryImport.Capture capture(LegacyHistoryImport.Binding b){LegacyHistoryImport.Capture result=super.capture(b);allowed=false;return result;} };
        LegacyHistoryImport.start(binding(),true,revoked,Runnable::run,(plan,error)->{assertNull(plan);assertNotNull(error);});
        assertEquals(0,revoked.commits);
    }
    @Test public void idConflictKeepsCurrentAndDoesNotUseTextForDeduplication() {
        ChatHistory target=new ChatHistory(),source=new ChatHistory();
        target.begin("same-id",null,"current",1);source.begin("same-id",null,"changed",2);source.begin("different-id",null,"current",3);
        ChatHistory.ImportCounts counts=target.importLegacy(source);
        assertEquals(2,counts.existing);assertEquals(1,counts.conflicts);assertEquals(2,counts.added);
        assertEquals(4,target.main().size());assertEquals("current",target.main().get(0).text);
    }
    @Test public void repeatedImportIsIdempotent() {
        ChatHistory target=new ChatHistory(),source=new ChatHistory();
        source.begin("old",null,"one",1);assertEquals(2,target.importLegacy(source).added);
        String before=target.json();ChatHistory.ImportCounts second=target.importLegacy(source);
        assertEquals(0,second.added);assertEquals(2,second.existing);assertEquals(before,target.json());
    }
}
