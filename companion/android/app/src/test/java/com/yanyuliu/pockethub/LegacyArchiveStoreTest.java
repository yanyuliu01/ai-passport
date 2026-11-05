package com.yanyuliu.pockethub;

import org.junit.Test;
import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.*;
import java.util.*;
import static org.junit.Assert.*;

public class LegacyArchiveStoreTest {
    static final class Sqlite implements LegacyArchiveStore.Database {
        final Connection db;boolean success,corruptAfterStage;int failAfter=-1;
        Sqlite(Path path) throws Exception {db=DriverManager.getConnection("jdbc:sqlite:"+path);exec("PRAGMA journal_mode=WAL");exec("PRAGMA synchronous=FULL");}
        private PreparedStatement prepare(String sql,Object... args) throws Exception {
            PreparedStatement s=db.prepareStatement(sql);for(int i=0;i<args.length;i++)s.setObject(i+1,args[i]);return s;
        }
        public void exec(String sql,Object... args) throws Exception {
            if(sql.startsWith("INSERT INTO legacy_message") && failAfter==0)throw new SQLException("injected failure");
            if(sql.startsWith("INSERT INTO legacy_message") && failAfter>0)failAfter--;
            try(PreparedStatement s=prepare(sql,args)){s.execute();}
        }
        public LegacyArchiveStore.Rows query(String sql,Object... args) throws Exception {
            PreparedStatement s=prepare(sql,args);ResultSet r=s.executeQuery();
            return new LegacyArchiveStore.Rows(){
                public boolean next() throws Exception{return r.next();}
                public String string(int i) throws Exception{return r.getString(i+1);}
                public long number(int i) throws Exception{return r.getLong(i+1);}
                public byte[] bytes(int i) throws Exception{return r.getBytes(i+1);}
                public void close() throws Exception{r.close();s.close();}
            };
        }
        public void begin() throws Exception{success=false;db.setAutoCommit(false);}
        public void success(){success=true;}
        public void end() throws Exception{
            try{if(success)db.commit();else db.rollback();}finally{db.setAutoCommit(true);}
            if(success && corruptAfterStage){corruptAfterStage=false;exec("DELETE FROM legacy_chunk WHERE kind=? AND part=0","history");}
        }
        public void close() throws Exception{db.close();}
    }
    private static LegacyHistoryImport.Binding binding(){return new LegacyHistoryImport.Binding("d".repeat(32),"a".repeat(32),"b".repeat(32),"c".repeat(32),"old-direct","new-account");}
    private static final LegacyArchiveStore.Fence ALLOW=(binding,source,target,action)->{action.run();return true;};
    private static long count(Sqlite db,String table) throws Exception{try(LegacyArchiveStore.Rows r=db.query("SELECT count(*) FROM "+table)){assertTrue(r.next());return r.number(0);}}
    private static String backup(Sqlite db,String id,String kind) throws Exception {
        ByteArrayOutputStream out=new ByteArrayOutputStream();
        try(LegacyArchiveStore.Rows r=db.query("SELECT data FROM legacy_chunk WHERE archive_id=? AND kind=? ORDER BY part",id,kind)){
            while(r.next())out.write(r.bytes(0));
        }return out.toString(StandardCharsets.UTF_8.name());
    }
    @Test public void explicitArchiveIsDurableIdempotentPagedAndReversible() throws Exception {
        Path dir=Files.createTempDirectory("legacy-archive-test"),path=dir.resolve("archive.sqlite");
        ChatHistory history=new ChatHistory();for(int i=0;i<120;i++){history.begin("m"+i,null,"synthetic "+i,i);history.finish("m"+i,null,"answer",false,null);}
        history.begin("hidden",null,"private",1);history.scope("hidden","deleted",true);history.eraseConversation("deleted");
        String original=history.json(),sync="{\"cursor\":\"independent\",\"payload\":\""+"中".repeat(150000)+"\"}";
        LegacyHistoryImport.Binding b=binding();
        try(Sqlite db=new Sqlite(path)){
            LegacyArchiveStore store=new LegacyArchiveStore(db);
            assertFalse(store.archive(b,false,"s1","t1",original,sync,ALLOW));assertEquals(0,count(db,"legacy_archive"));
            assertTrue(store.archive(b,true,"s1","t1",original,sync,ALLOW));
            assertTrue(store.archive(b,true,"s1","t1",original,sync,ALLOW));assertEquals(1,count(db,"legacy_archive"));
            assertEquals(original,backup(db,b.transaction,"history"));assertEquals(sync,backup(db,b.transaction,"sync"));
            assertTrue(store.verifyBackup(b,"history",ALLOW));assertTrue(store.verifyBackup(b,"sync",ALLOW));
            Set<String> current=Set.of("m0:you","m0:reply"),ids=new HashSet<>();long before=-1;int pages=0;
            do{
                LegacyArchiveStore.Page page=store.page(b,"default",before,80,current,Set.of(),ALLOW);pages++;
                for(String item:page.messages){String id=Json.string(Json.parseObject(item),"id");assertTrue(ids.add(id));}
                before=page.before;if(!page.more)break;
            }while(pages<10);
            assertEquals(238,ids.size());assertTrue(pages>=3);
            assertTrue(store.page(b,"default",-1,80,Set.of(),Set.of("default"),ALLOW).messages.isEmpty());
            LegacyHistoryImport.Binding wrong=new LegacyHistoryImport.Binding(b.transaction,"e".repeat(32),b.runtime,b.subject,b.source,b.target);
            assertTrue(store.page(wrong,"default",-1,80,Set.of(),Set.of(),ALLOW).messages.isEmpty());
        }
        try(Sqlite db=new Sqlite(path)){
            LegacyArchiveStore store=new LegacyArchiveStore(db);assertEquals(original,backup(db,b.transaction,"history"));
            assertTrue(store.rollback(b,ALLOW));assertTrue(store.rollback(b,ALLOW));assertTrue(store.page(b,"default",-1,80,Set.of(),Set.of(),ALLOW).messages.isEmpty());
            assertEquals(sync,backup(db,b.transaction,"sync"));assertTrue(store.verifyBackup(b,"sync",ALLOW));
            db.exec("DELETE FROM legacy_chunk WHERE archive_id=? AND kind=? AND part=0",b.transaction,"sync");
            assertFalse(store.verifyBackup(b,"sync",ALLOW));
        }
    }
    @Test public void fenceOrMidTransactionFailureCannotLeavePartialArchive() throws Exception {
        Path path=Files.createTempDirectory("legacy-archive-failure").resolve("archive.sqlite");ChatHistory history=new ChatHistory();
        history.begin("one",null,"synthetic",1);LegacyHistoryImport.Binding b=binding();
        try(Sqlite db=new Sqlite(path)){
            LegacyArchiveStore store=new LegacyArchiveStore(db);
            assertFalse(store.archive(b,true,"s1","t1",history.json(),"{}",(x,s,t,a)->false));
            db.failAfter=1;
            try{store.archive(b,true,"s1","t1",history.json(),"{}",ALLOW);fail("injected write failure");}catch(SQLException expected){}
            assertEquals(0,count(db,"legacy_archive"));assertEquals(0,count(db,"legacy_chunk"));assertEquals(0,count(db,"legacy_message"));
            db.failAfter=-1;assertTrue(store.archive(b,true,"s1","t1",history.json(),"{}",ALLOW));
            try{store.archive(b,true,"s1","t1",history.json(),"changed",ALLOW);fail("same transaction cannot change contents");}catch(IllegalStateException expected){}
            assertEquals("{}",backup(db,b.transaction,"sync"));
        }
    }
    @Test public void largeSingleMessageUsesChunksAndStillPagesExactly() throws Exception {
        Path path=Files.createTempDirectory("legacy-archive-large").resolve("archive.sqlite");ChatHistory history=new ChatHistory();
        String body="中".repeat(750000);history.begin("large",null,body,1);history.finish("large",null,"answer",false,null);
        String smaller="小".repeat(100000);history.begin("small",null,smaller,2);history.finish("small",null,"answer",false,null);
        LegacyHistoryImport.Binding b=binding();
        try(Sqlite db=new Sqlite(path)){
            LegacyArchiveStore store=new LegacyArchiveStore(db);assertTrue(store.archive(b,true,"s1","t1",history.json(),"{}",ALLOW));
            LegacyArchiveStore.Page page=store.page(b,"default",-1,80,Set.of(),Set.of(),ALLOW);
            assertEquals(4,page.messages.size());
            assertTrue(page.messages.stream().map(Json::parseObject).allMatch(m->Json.string(m,"text").length()<=120));
            try{store.detail(b,"default","large:you",Set.of(),Set.of(),ALLOW);fail("oversize detail must be bounded");}
            catch(java.io.IOException expected){}
            assertEquals(smaller,Json.string(Json.parseObject(store.detail(b,"default","small:you",Set.of(),Set.of(),ALLOW)),"text"));
            db.exec("DELETE FROM legacy_chunk WHERE archive_id=? AND kind=? AND part=0",b.transaction,"message:small:you");
            try{store.detail(b,"default","small:you",Set.of(),Set.of(),ALLOW);fail("damaged chunks must not be shown as valid");}
            catch(java.io.IOException expected){}
        }
    }
    @Test public void accountSwitchBeforeActivationOrAfterPageReadIsFailClosed() throws Exception {
        Path path=Files.createTempDirectory("legacy-archive-fence").resolve("archive.sqlite");ChatHistory history=new ChatHistory();history.begin("one",null,"one",1);
        LegacyHistoryImport.Binding b=binding();
        try(Sqlite db=new Sqlite(path)){
            LegacyArchiveStore store=new LegacyArchiveStore(db);int[] calls={0};
            LegacyArchiveStore.Fence switched=(binding,source,target,action)->{if(++calls[0]==2)return false;action.run();return true;};
            assertFalse(store.archive(b,true,"s1","t1",history.json(),"{}",switched));
            assertEquals(0,count(db,"legacy_archive"));assertEquals(0,count(db,"legacy_chunk"));
            assertTrue(store.archive(b,true,"s1","t1",history.json(),"{}",ALLOW));calls[0]=0;
            assertTrue(store.page(b,"default",-1,80,Set.of(),Set.of(),switched).messages.isEmpty());
        }
    }
    @Test(timeout=60000) public void largeArchiveIndexesInBackgroundAndReturnsOnlyOnePage() throws Exception {
        Path path=Files.createTempDirectory("legacy-archive-scale").resolve("archive.sqlite");ChatHistory history=new ChatHistory();
        String body="样".repeat(1300);for(int i=0;i<1900;i++){history.begin("m"+i,null,body,i);history.finish("m"+i,null,"reply",false,null);}
        String sync="x".repeat(14*1024*1024);LegacyHistoryImport.Binding b=binding();
        try(Sqlite db=new Sqlite(path)){
            LegacyArchiveStore store=new LegacyArchiveStore(db);
            assertTrue(store.archive(b,true,"s1","t1",history.json(),sync,ALLOW));
            LegacyArchiveStore.Page page=store.page(b,"default",-1,80,Set.of(),Set.of(),ALLOW);
            assertEquals(80,page.messages.size());assertTrue(page.more);
        }
    }
    @Test public void byteInputPreservesOpaqueSyncAndRejectsInvalidHistoryEncoding() throws Exception {
        Path path=Files.createTempDirectory("legacy-archive-bytes").resolve("archive.sqlite");ChatHistory history=new ChatHistory();history.begin("one",null,"ok",1);
        byte[] sync={(byte)0xff,0,1,(byte)0xfe};LegacyHistoryImport.Binding b=binding();
        try(Sqlite db=new Sqlite(path)){
            LegacyArchiveStore store=new LegacyArchiveStore(db);
            assertFalse(store.archive(b,true,"s1","t1",new byte[]{(byte)0xff},sync,ALLOW));assertEquals(0,count(db,"legacy_archive"));
            assertTrue(store.archive(b,true,"s1","t1",history.json().getBytes(StandardCharsets.UTF_8),sync,ALLOW));
            try(LegacyArchiveStore.Rows rows=db.query("SELECT data FROM legacy_chunk WHERE archive_id=? AND kind=? ORDER BY part",b.transaction,"sync")){
                assertTrue(rows.next());assertArrayEquals(sync,rows.bytes(0));assertFalse(rows.next());
            }
        }
    }
    @Test public void corruptedStagingNeverBecomesVisible() throws Exception {
        Path path=Files.createTempDirectory("legacy-archive-check").resolve("archive.sqlite");ChatHistory history=new ChatHistory();history.begin("one",null,"one",1);
        try(Sqlite db=new Sqlite(path)){
            LegacyArchiveStore store=new LegacyArchiveStore(db);db.corruptAfterStage=true;
            assertFalse(store.archive(binding(),true,"s1","t1",history.json(),"{}",ALLOW));
            assertEquals(0,count(db,"legacy_archive"));assertEquals(0,count(db,"legacy_message"));
        }
    }
    @Test public void restartDiscardsOnlyUnactivatedStaging() throws Exception {
        Path path=Files.createTempDirectory("legacy-archive-crash").resolve("archive.sqlite");LegacyHistoryImport.Binding b=binding();
        try(Sqlite db=new Sqlite(path)){
            new LegacyArchiveStore(db);
            db.exec("INSERT INTO legacy_archive VALUES(?,?,?,?,?,?,?,?,?,?,0)",b.transaction,b.account,b.runtime,b.subject,b.source,b.target,"s","t","h","h");
            db.exec("INSERT INTO legacy_chunk VALUES(?,?,?,?)",b.transaction,"history",0,new byte[]{1});
            db.exec("INSERT INTO legacy_message VALUES(?,?,?,?,?,?,?,?)",b.transaction,0,"default","one","you","one",0,"{}");
        }
        try(Sqlite db=new Sqlite(path)){
            LegacyArchiveStore store=new LegacyArchiveStore(db);
            assertEquals(0,count(db,"legacy_archive"));assertEquals(0,count(db,"legacy_chunk"));assertEquals(0,count(db,"legacy_message"));
            assertTrue(store.page(b,"default",-1,80,Set.of(),Set.of(),ALLOW).messages.isEmpty());
        }
    }
    @Test public void oversizedInputFailsWithoutPartialArchive() throws Exception {
        Path path=Files.createTempDirectory("legacy-archive-cap").resolve("archive.sqlite");
        try(Sqlite db=new Sqlite(path)){
            LegacyArchiveStore store=new LegacyArchiveStore(db);
            assertFalse(store.archive(binding(),true,"s1","t1",new byte[LegacyArchiveStore.MAX_HISTORY_BYTES+1],new byte[0],ALLOW));
            assertEquals(0,count(db,"legacy_archive"));assertEquals(0,count(db,"legacy_chunk"));
        }
    }
}
