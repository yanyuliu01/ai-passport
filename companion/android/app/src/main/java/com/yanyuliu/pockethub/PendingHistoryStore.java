package com.yanyuliu.pockethub;

/** Account/source-scoped durable recovery state for an archive that cannot yet be merged. */
final class PendingHistoryStore {
    enum Mode { ORIGINAL, PENDING, FINALIZE, STALE }
    interface AtomicWrite { boolean write(String key,String value,String remove); }
    static final class Recovery {
        final ChatHistory history; final boolean blocked, pending;
        Recovery(ChatHistory history,boolean blocked,boolean pending){this.history=history;this.blocked=blocked;this.pending=pending;}
    }
    static String key(String archive) {
        if(archive==null || !archive.matches("[a-f0-9]{64}"))throw new IllegalArgumentException("无效历史键");
        return "bootstrap-pending:"+archive;
    }
    static Recovery recover(String stored,String pending) {
        // Invalid pending data must not silently replace either original.
        ChatHistory early=pending==null?new ChatHistory():HistoryBootstrap.merge(pending,new ChatHistory());
        try { return new Recovery(HistoryBootstrap.merge(stored,early),false,pending!=null); }
        catch(IllegalArgumentException invalid){return new Recovery(early,true,pending!=null);}
    }
    static boolean stage(String archive,String value,AtomicWrite write){return write.write(key(archive),value,null);}
    static boolean finish(String archive,String value,AtomicWrite write){return write.write(archive,value,key(archive));}
    static Mode mode(boolean blockedAtCapture,boolean blockedNow,boolean recoveredAtCapture,boolean recoveredNow){
        if(blockedAtCapture || blockedNow)return Mode.PENDING;
        if(recoveredNow && !recoveredAtCapture)return Mode.STALE;
        return recoveredNow?Mode.FINALIZE:Mode.ORIGINAL;
    }
    private PendingHistoryStore(){}
}
