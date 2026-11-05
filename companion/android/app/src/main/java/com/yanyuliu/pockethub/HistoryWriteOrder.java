package com.yanyuliu.pockethub;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/** Serialize each archive's writes even when a Runtime switch overtakes the background writer. */
final class HistoryWriteOrder {
    interface Writer { boolean write(String value, boolean durable); }
    private static final class Latest { long version; boolean written; }
    private final java.util.concurrent.atomic.AtomicLong next=new java.util.concurrent.atomic.AtomicLong();
    private final Map<String,Latest> latest=new ConcurrentHashMap<>();
    long next() { return next.incrementAndGet(); }
    boolean isCurrent(String key, long version) {
        Latest known=latest.get(key);
        if(known==null)return false;
        synchronized(known){return known.written && known.version==version;}
    }
    void ifCurrent(String key, long version, Runnable action) {
        Latest known=latest.get(key);
        if(known==null)return;
        synchronized(known){if(known.written && known.version==version)action.run();}
    }
    boolean write(String key, long version, String value, boolean durable, Writer writer) {
        Latest known=latest.computeIfAbsent(key,ignored->new Latest());
        synchronized(known){
            if(known.written && known.version>version) {
                // Null asks the caller to flush the current preference, never this older snapshot.
                return !durable || writer.write(null,true);
            }
            if(!writer.write(value,durable))return false;
            known.version=version;known.written=true;return true;
        }
    }
}
