package com.yanyuliu.pockethub;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;

/** Transactional, account-bound read-only legacy archive. No automatic migration calls this. */
final class LegacyArchiveStore {
    interface Rows extends AutoCloseable { boolean next() throws Exception; String string(int column) throws Exception; long number(int column) throws Exception; byte[] bytes(int column) throws Exception; void close() throws Exception; }
    interface Database extends AutoCloseable {
        void exec(String sql,Object... args) throws Exception; Rows query(String sql,Object... args) throws Exception;
        void begin() throws Exception; void success() throws Exception; void end() throws Exception; void close() throws Exception;
    }
    interface Action { void run() throws Exception; }
    /** Return false without invoking action, or hold the account/selection generation through it and return true. */
    interface Fence { boolean run(LegacyHistoryImport.Binding binding, String sourceVersion, String targetVersion, Action action) throws Exception; }
    static final class Page {
        final List<String> messages; final long before; final boolean more;
        Page(List<String> messages,long before,boolean more){this.messages=messages;this.before=before;this.more=more;}
    }
    static final class ArchiveRef {
        final String transaction,source,target;
        ArchiveRef(String transaction,String source,String target){this.transaction=transaction;this.source=source;this.target=target;}
    }
    static final int MAX_HISTORY_BYTES=16*1024*1024, MAX_SYNC_BYTES=32*1024*1024;
    private static final int CHUNK=256*1024, MAX_PAGE=80;
    private static final Object STAGING_LOCK=new Object();
    private final Database db;
    LegacyArchiveStore(Database db) throws Exception {
        this.db=db;
        db.exec("CREATE TABLE IF NOT EXISTS legacy_archive (id TEXT PRIMARY KEY, account TEXT NOT NULL, runtime TEXT NOT NULL, subject TEXT NOT NULL, source TEXT NOT NULL, target TEXT NOT NULL, source_version TEXT NOT NULL, target_version TEXT NOT NULL, history_hash TEXT NOT NULL, sync_hash TEXT NOT NULL, active INTEGER NOT NULL)");
        db.exec("CREATE TABLE IF NOT EXISTS legacy_chunk (archive_id TEXT NOT NULL, kind TEXT NOT NULL, part INTEGER NOT NULL, data BLOB NOT NULL, PRIMARY KEY(archive_id,kind,part))");
        db.exec("CREATE TABLE IF NOT EXISTS legacy_message (archive_id TEXT NOT NULL, ordinal INTEGER NOT NULL, conversation TEXT NOT NULL, id TEXT NOT NULL, role TEXT NOT NULL, preview TEXT NOT NULL, attachments INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(archive_id,id))");
        db.exec("CREATE INDEX IF NOT EXISTS legacy_page ON legacy_message(archive_id,conversation,ordinal)");
        // A process crash may leave an unactivated staging snapshot; originals are elsewhere.
        synchronized(STAGING_LOCK){discardAbandoned();}
    }
    private static final class Entry {
        final String id,conversation,payload,role,preview;final int attachments;final double at;final Presentation presentation;
        Entry(Map<String,Object> row){id=Json.string(row,"id");conversation=Json.string(row,"conversation").isEmpty()?"default":Json.string(row,"conversation");
            payload=UsageEvents.json(row);role=Json.string(row,"role");at=Json.number(row,"at");presentation=Presentation.from(row.get("presentation"));
            String text=Json.string(row,"text");int end=Math.min(120,text.length());if(end<text.length() && end>0 && Character.isHighSurrogate(text.charAt(end-1)) && Character.isLowSurrogate(text.charAt(end)))end--;
            preview=text.substring(0,end);attachments=Json.list(row,"attachments").size();}
    }
    /** Source parsing/sorting runs before the fenced transaction; originals stay untouched. */
    synchronized boolean archive(LegacyHistoryImport.Binding binding,boolean confirmed,String sourceVersion,String targetVersion,
                                 String originalHistory,String originalSync,Fence fence) throws Exception {
        if(originalHistory==null || originalSync==null)return false;
        return archive(binding,confirmed,sourceVersion,targetVersion,originalHistory.getBytes(StandardCharsets.UTF_8),originalSync.getBytes(StandardCharsets.UTF_8),fence);
    }
    /** Byte overload retains the source files exactly, including an opaque sync cache. */
    synchronized boolean archive(LegacyHistoryImport.Binding binding,boolean confirmed,String sourceVersion,String targetVersion,
                                 byte[] history,byte[] sync,Fence fence) throws Exception {
        synchronized(STAGING_LOCK){return archiveLocked(binding,confirmed,sourceVersion,targetVersion,history,sync,fence);}
    }
    private boolean archiveLocked(LegacyHistoryImport.Binding binding,boolean confirmed,String sourceVersion,String targetVersion,
                                 byte[] history,byte[] sync,Fence fence) throws Exception {
        if(!confirmed || binding==null || !binding.valid() || empty(sourceVersion) || empty(targetVersion)
                || history==null || sync==null || history.length>MAX_HISTORY_BYTES || sync.length>MAX_SYNC_BYTES || fence==null)return false;
        if(!fence.run(binding,sourceVersion,targetVersion,()->{}))return false;
        final String originalHistory;
        try{originalHistory=StandardCharsets.UTF_8.newDecoder().onMalformedInput(java.nio.charset.CodingErrorAction.REPORT)
                .onUnmappableCharacter(java.nio.charset.CodingErrorAction.REPORT).decode(java.nio.ByteBuffer.wrap(history)).toString();}
        catch(java.nio.charset.CharacterCodingException invalid){return false;}
        Map<String,Object> root=Json.parseObject(originalHistory);
        if(root==null || !(root.get("messages") instanceof List))return false;
        if(root.containsKey("erased") && !(root.get("erased") instanceof List))return false;
        Set<String> sourceDeleted=new HashSet<>();for(Object x:Json.list(root,"erased")){if(!(x instanceof String))return false;sourceDeleted.add((String)x);}
        Map<String,Entry> unique=new LinkedHashMap<>();
        Set<String> allIds=new HashSet<>();
        for(Object raw:Json.list(root,"messages")){
            if(!(raw instanceof Map))return false;Map<String,Object> row=Json.object(raw);Entry entry=new Entry(row);
            if(entry.id.isEmpty() || !allIds.add(entry.id))return false;
            if(!"main".equals(Json.string(row,"scope")) && !"task".equals(Json.string(row,"scope")))return false;
            if(!"main".equals(Json.string(row,"scope")) || sourceDeleted.contains(entry.conversation)
                    || entry.presentation!=null && !entry.presentation.inChat())continue;
            unique.put(entry.id,entry);
        }
        List<Entry> entries=new ArrayList<>(unique.values());
        entries.sort((a,b)->{int order=Presentation.compareOrder(Presentation.order(a.presentation,a.at,a.role,a.id),Presentation.order(b.presentation,b.at,b.role,b.id));return order!=0?order:a.id.compareTo(b.id);});
        String historyHash=hash(history),syncHash=hash(sync);
        boolean exists=false;
        db.begin();try{
            try(Rows rows=db.query("SELECT account,runtime,subject,source,target,source_version,target_version,history_hash,sync_hash,active FROM legacy_archive WHERE id=?",binding.transaction)){
                if(rows.next()){
                    if(!binding.account.equals(rows.string(0)) || !binding.runtime.equals(rows.string(1)) || !binding.subject.equals(rows.string(2))
                            || !binding.source.equals(rows.string(3)) || !binding.target.equals(rows.string(4))
                            || !sourceVersion.equals(rows.string(5)) || !targetVersion.equals(rows.string(6))
                            || !historyHash.equals(rows.string(7)) || !syncHash.equals(rows.string(8)) || rows.number(9)!=1)
                        throw new IllegalStateException("归档事务冲突或已撤销");
                    exists=true;
                }
            }
            if(!exists){
                db.exec("INSERT INTO legacy_archive VALUES(?,?,?,?,?,?,?,?,?,?,0)",binding.transaction,binding.account,binding.runtime,binding.subject,binding.source,binding.target,sourceVersion,targetVersion,historyHash,syncHash);
                chunks(binding.transaction,"history",history);chunks(binding.transaction,"sync",sync);
                long ordinal=0;for(Entry entry:entries){
                    byte[] payload=entry.payload.getBytes(StandardCharsets.UTF_8);
                    String value=entry.payload;
                    if(payload.length>CHUNK){chunks(binding.transaction,"message:"+entry.id,payload);value="@chunk:"+hash(payload);}
                    db.exec("INSERT INTO legacy_message VALUES(?,?,?,?,?,?,?,?)",binding.transaction,ordinal++,entry.conversation,entry.id,entry.role,entry.preview,entry.attachments,value);
                }
            }
            db.success();
        }finally{db.end();}
        if(exists)return fence.run(binding,sourceVersion,targetVersion,()->{});
        boolean activated=false;
        try{
            // Only a fully verified staging snapshot may become visible after a crash.
            if(!verifyBackup(binding,"history",fence) || !verifyBackup(binding,"sync",fence))return false;
            activated=fence.run(binding,sourceVersion,targetVersion,()->{
                db.exec("UPDATE legacy_archive SET active=1 WHERE id=? AND active=0",binding.transaction);
                if(!active(binding))throw new IllegalStateException("旧记录关联未能确认");
            });
            return activated;
        }finally{if(!activated)discardStaged(binding.transaction);}
    }
    private void discardAbandoned() throws Exception {
        db.begin();try{
            db.exec("DELETE FROM legacy_message WHERE archive_id IN (SELECT id FROM legacy_archive WHERE active=0)");
            db.exec("DELETE FROM legacy_chunk WHERE archive_id IN (SELECT id FROM legacy_archive WHERE active=0)");
            db.exec("DELETE FROM legacy_archive WHERE active=0");db.success();
        }finally{db.end();}
    }
    private void discardStaged(String id) throws Exception {
        db.begin();try{
            db.exec("DELETE FROM legacy_message WHERE archive_id=? AND EXISTS (SELECT 1 FROM legacy_archive WHERE id=? AND active=0)",id,id);
            db.exec("DELETE FROM legacy_chunk WHERE archive_id=? AND EXISTS (SELECT 1 FROM legacy_archive WHERE id=? AND active=0)",id,id);
            db.exec("DELETE FROM legacy_archive WHERE id=? AND active=0",id);db.success();
        }finally{db.end();}
    }
    private void chunks(String id,String kind,byte[] bytes) throws Exception {
        int part=0;for(int start=0;start<bytes.length;start+=CHUNK)db.exec("INSERT INTO legacy_chunk VALUES(?,?,?,?)",id,kind,part++,Arrays.copyOfRange(bytes,start,Math.min(bytes.length,start+CHUNK)));
    }
    /** A page is scoped to the exact bound account, Runtime, subject and target. */
    synchronized Page page(LegacyHistoryImport.Binding binding,String conversation,long before,int limit,Set<String> currentIds,Set<String> deleted,Fence fence) throws Exception {
        if(binding==null || !binding.valid() || empty(conversation) || limit<1 || fence==null)return new Page(Collections.emptyList(),before,false);
        int count=Math.min(MAX_PAGE,limit);Set<String> ids=currentIds==null?Collections.emptySet():currentIds;
        if(deleted!=null && deleted.contains(conversation))return new Page(Collections.emptyList(),before,false);
        final List<String> found=new ArrayList<>();final long[] cursor={before<0?Long.MAX_VALUE:before};final boolean[] more={false};
        if(!fence.run(binding,"","",()->{}) || !active(binding))return new Page(Collections.emptyList(),before,false);
        int batches=0;
            while(found.size()<count && batches++<3){
                int scanned=0;
                try(Rows rows=db.query("SELECT ordinal,id,role,preview,attachments FROM legacy_message WHERE archive_id=? AND conversation=? AND ordinal<? ORDER BY ordinal DESC LIMIT ?",binding.transaction,conversation,cursor[0],count)){
                    while(rows.next()){
                        scanned++;cursor[0]=rows.number(0);String id=rows.string(1);
                        if(!ids.contains(id))found.add(UsageEvents.json(RelayCrypto.map("id",id,"role",rows.string(2),"text",rows.string(3),"attachments_count",rows.number(4))));
                        if(found.size()==count)break;
                    }
                }
                if(scanned<count)break;
                more[0]=true;
            }
        if(!fence.run(binding,"","",()->{}) || !active(binding))return new Page(Collections.emptyList(),before,false);
        Collections.reverse(found);return new Page(found,cursor[0],more[0]);
    }
    synchronized boolean rollback(LegacyHistoryImport.Binding binding,Fence fence) throws Exception {
        if(binding==null || !binding.valid() || fence==null)return false;
        final boolean[] changed={false};boolean allowed=fence.run(binding,"","",()->{
            db.begin();try{
                long state=state(binding);
                if(state==1){db.exec("UPDATE legacy_archive SET active=2 WHERE id=?",binding.transaction);changed[0]=true;}
                else if(state==2)changed[0]=true;
                db.success();
            }finally{db.end();}
        });return allowed && changed[0];
    }
    /** List only this authenticated target's active bindings, not other accounts' archives. */
    synchronized List<ArchiveRef> list(LegacyHistoryImport.Binding context,Fence fence) throws Exception {
        if(context==null || !context.valid() || fence==null)return Collections.emptyList();
        List<ArchiveRef> result=new ArrayList<>();
        if(!fence.run(context,"","",()->{}))return result;
            try(Rows rows=db.query("SELECT id,source,target FROM legacy_archive WHERE account=? AND runtime=? AND subject=? AND target=? AND active=1 ORDER BY rowid DESC LIMIT 64",
                    context.account,context.runtime,context.subject,context.target)){
                while(rows.next())result.add(new ArchiveRef(rows.string(0),rows.string(1),rows.string(2)));
            }
        return fence.run(context,"","",()->{})?result:Collections.emptyList();
    }
    /** Verify retained originals even after a binding is rolled back; never expose another account. */
    synchronized boolean verifyBackup(LegacyHistoryImport.Binding binding,String kind,Fence fence) throws Exception {
        if(binding==null || !binding.valid() || fence==null || !("history".equals(kind) || "sync".equals(kind)))return false;
        if(!fence.run(binding,"","",()->{}))return false;
            String expected;
            try(Rows row=db.query("SELECT history_hash,sync_hash FROM legacy_archive WHERE id=? AND account=? AND runtime=? AND subject=? AND source=? AND target=?",
                    binding.transaction,binding.account,binding.runtime,binding.subject,binding.source,binding.target)){
                if(!row.next())return false;expected=row.string("history".equals(kind)?0:1);
            }
            MessageDigest digest=MessageDigest.getInstance("SHA-256");long part=0;
            try(Rows rows=db.query("SELECT part,data FROM legacy_chunk WHERE archive_id=? AND kind=? ORDER BY part",binding.transaction,kind)){
                while(rows.next()){if(rows.number(0)!=part++)return false;digest.update(rows.bytes(1));}
            }
        return expected.equals(hex(digest.digest())) && fence.run(binding,"","",()->{});
    }
    /** Read one bounded message after a fresh identity check; backups remain untouched. */
    synchronized String detail(LegacyHistoryImport.Binding binding,String conversation,String id,Set<String> currentIds,Set<String> deleted,Fence fence) throws Exception {
        if(binding==null || !binding.valid() || empty(conversation) || empty(id) || fence==null
                || currentIds!=null && currentIds.contains(id) || deleted!=null && deleted.contains(conversation))return null;
        if(!fence.run(binding,"","",()->{}) || !active(binding))return null;
        String payload;
        try(Rows rows=db.query("SELECT payload FROM legacy_message WHERE archive_id=? AND conversation=? AND id=?",binding.transaction,conversation,id)){
            if(!rows.next())return null;payload=rows.string(0);
        }
        if(payload.startsWith("@chunk:")){
            try(Rows rows=db.query("SELECT coalesce(sum(length(data)),0) FROM legacy_chunk WHERE archive_id=? AND kind=?",binding.transaction,"message:"+id)){
                if(!rows.next() || rows.number(0)>1024*1024)throw new java.io.IOException("旧消息过长，暂不能完整打开；原记录仍保留");
            }
            payload=readChunks(binding.transaction,"message:"+id,payload.substring(7));
        }
        return fence.run(binding,"","",()->{}) && active(binding)?payload:null;
    }
    private boolean active(LegacyHistoryImport.Binding b) throws Exception {
        return state(b)==1;
    }
    private long state(LegacyHistoryImport.Binding b) throws Exception {
        try(Rows rows=db.query("SELECT active FROM legacy_archive WHERE id=? AND account=? AND runtime=? AND subject=? AND source=? AND target=?",b.transaction,b.account,b.runtime,b.subject,b.source,b.target)){return rows.next()?rows.number(0):-1;}
    }
    private String readChunks(String id,String kind,String expectedHash) throws Exception {
        java.io.ByteArrayOutputStream out=new java.io.ByteArrayOutputStream();
        try(Rows rows=db.query("SELECT data FROM legacy_chunk WHERE archive_id=? AND kind=? ORDER BY part",id,kind)){
            while(rows.next())out.write(rows.bytes(0));
        }
        byte[] value=out.toByteArray();
        if(!expectedHash.matches("[a-f0-9]{64}") || !expectedHash.equals(hash(value)))throw new java.io.IOException("历史归档消息校验失败");
        return new String(value,StandardCharsets.UTF_8);
    }
    private static boolean empty(String value){return value==null || value.isEmpty();}
    private static String hash(byte[] input) throws Exception {return hex(MessageDigest.getInstance("SHA-256").digest(input));}
    private static String hex(byte[] bytes){StringBuilder out=new StringBuilder();for(byte b:bytes)out.append(String.format(Locale.ROOT,"%02x",b&255));return out.toString();}
}
