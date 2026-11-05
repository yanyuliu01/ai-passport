package com.yanyuliu.pockethub;

import org.junit.Test;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicReference;
import static org.junit.Assert.*;

public class HistoryWriteOrderTest {
    @Test public void switchOvertakingReceiptCannotReplaceNewerArchive() throws Exception {
        HistoryWriteOrder order=new HistoryWriteOrder();long old=order.next(),newer=order.next();
        AtomicReference<String> memory=new AtomicReference<>(),disk=new AtomicReference<>();
        HistoryWriteOrder.Writer writer=(value,durable)->{if(value==null)value=memory.get();memory.set(value);if(durable)disk.set(value);return true;};
        CountDownLatch captured=new CountDownLatch(1),release=new CountDownLatch(1);
        ExecutorService executor=Executors.newSingleThreadExecutor();
        try {
            Future<Boolean> receipt=executor.submit(()->{captured.countDown();release.await();return order.write("runtime-a",old,"receipt snapshot",true,writer);});
            assertTrue(captured.await(1,TimeUnit.SECONDS));
            assertTrue(order.write("runtime-a",newer,"later snapshot including receipt",false,writer));
            release.countDown();assertTrue(receipt.get(1,TimeUnit.SECONDS));
            assertFalse(order.isCurrent("runtime-a",old));assertTrue(order.isCurrent("runtime-a",newer));
            assertEquals("later snapshot including receipt",memory.get());
            assertEquals("later snapshot including receipt",disk.get());
        } finally {release.countDown();executor.shutdownNow();}
    }
    @Test public void failedDurableFlushDoesNotAcknowledgeReceiptOrMixKeys() {
        HistoryWriteOrder order=new HistoryWriteOrder();long first=order.next(),second=order.next();
        AtomicReference<String> disk=new AtomicReference<>(),memory=new AtomicReference<>();
        assertTrue(order.write("a",second,"new a",false,(v,d)->{memory.set(v);return true;}));
        assertFalse(order.write("a",first,"old a",true,(v,d)->false));
        assertTrue(order.write("b",first,"b",true,(v,d)->{disk.set(v);return true;}));
        assertEquals("b",disk.get());
        assertTrue(order.write("a",first,"old a",true,(v,d)->{disk.set(v==null?memory.get():v);return true;}));
        assertEquals("new a",disk.get());
    }
    @Test public void slowFlushDoesNotLockOtherArchive() throws Exception {
        HistoryWriteOrder order=new HistoryWriteOrder();long a=order.next(),b=order.next();
        CountDownLatch writing=new CountDownLatch(1),release=new CountDownLatch(1);
        ExecutorService executor=Executors.newFixedThreadPool(2);
        try {
            Future<Boolean> slow=executor.submit(()->order.write("a",a,"a",true,(v,d)->{
                writing.countDown();try{release.await();}catch(InterruptedException e){Thread.currentThread().interrupt();return false;}return true;
            }));
            assertTrue(writing.await(1,TimeUnit.SECONDS));
            Future<Boolean> other=executor.submit(()->order.write("b",b,"b",false,(v,d)->true));
            assertTrue(other.get(1,TimeUnit.SECONDS));release.countDown();assertTrue(slow.get(1,TimeUnit.SECONDS));
        } finally {release.countDown();executor.shutdownNow();}
    }
    @Test public void journal_cleanup_and_superseding_archive_write_are_serialized() throws Exception {
        HistoryWriteOrder order=new HistoryWriteOrder();long old=order.next(),newer=order.next();
        assertTrue(order.write("a",old,"old",true,(v,d)->true));
        CountDownLatch cleanup=new CountDownLatch(1),release=new CountDownLatch(1);
        ExecutorService executor=Executors.newFixedThreadPool(2);
        try {
            Future<?> held=executor.submit(()->order.ifCurrent("a",old,()->{
                cleanup.countDown();try{release.await();}catch(InterruptedException e){Thread.currentThread().interrupt();}
            }));
            assertTrue(cleanup.await(1,TimeUnit.SECONDS));
            Future<Boolean> later=executor.submit(()->order.write("a",newer,"new",true,(v,d)->true));
            try { later.get(30,TimeUnit.MILLISECONDS);fail("new archive overtook cleanup"); }
            catch(TimeoutException expected) { /* The same archive stays ordered. */ }
            release.countDown();held.get(1,TimeUnit.SECONDS);assertTrue(later.get(1,TimeUnit.SECONDS));
            assertFalse(order.isCurrent("a",old));assertTrue(order.isCurrent("a",newer));
        } finally {release.countDown();executor.shutdownNow();}
    }
}
