package com.yanyuliu.pockethub;

import org.junit.Test;
import java.io.IOException;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.atomic.AtomicInteger;
import static org.junit.Assert.*;

public class TurnAdmissionTest {
    @Test public void transport_cannot_start_before_durable_intent_and_current_identity() throws Exception {
        AtomicInteger writes=new AtomicInteger();
        CompletableFuture<Boolean> pending=new CompletableFuture<>();
        try { TurnAdmission.await(pending,5,()->true,()->true);writes.incrementAndGet();fail(); }
        catch(IOException expected){assertEquals(0,writes.get());}
        pending.complete(false);
        try { TurnAdmission.await(pending,5,()->true,()->true);writes.incrementAndGet();fail(); }
        catch(IOException expected){assertEquals(0,writes.get());}
        CompletableFuture<Boolean> done=CompletableFuture.completedFuture(true);
        try { TurnAdmission.await(done,5,()->false,()->true);writes.incrementAndGet();fail(); }
        catch(IOException expected){assertEquals(0,writes.get());}
        try { TurnAdmission.await(done,5,()->true,()->false);writes.incrementAndGet();fail(); }
        catch(IOException expected){assertEquals(0,writes.get());}
        TurnAdmission.await(done,5,()->true,()->true);writes.incrementAndGet();assertEquals(1,writes.get());
    }
    @Test public void composer_is_cleared_only_after_persistence_for_the_unchanged_context_and_text() {
        assertFalse(ComposerAdmission.mayClear(false,"account-A/chat","account-A/chat","hello","hello"));
        assertFalse(ComposerAdmission.mayClear(true,"account-A/chat","account-B/chat","hello","hello"));
        assertFalse(ComposerAdmission.mayClear(true,"account-A/chat","account-A/chat","hello","hello again"));
        assertTrue(ComposerAdmission.mayClear(true,"account-A/chat","account-A/chat","hello","hello"));
    }
}
