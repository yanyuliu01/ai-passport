package com.yanyuliu.pockethub;

import org.junit.Test;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import static org.junit.Assert.*;

public class AccountHistoryFenceTest {
    @Test public void prepared_old_account_cannot_install_after_account_switch() throws Exception {
        AccountHistoryFence fence=new AccountHistoryFence();
        AtomicReference<String> account=new AtomicReference<>("A"), visible=new AtomicReference<>("");
        fence.change(()->account.set("A"));
        long captured=fence.capture();
        CountDownLatch prepared=new CountDownLatch(1), resume=new CountDownLatch(1);
        AtomicBoolean installed=new AtomicBoolean(true);
        Thread old=new Thread(()->{
            prepared.countDown();
            try { if(!resume.await(2,TimeUnit.SECONDS))throw new AssertionError("timeout"); }
            catch(InterruptedException interrupted){throw new AssertionError(interrupted);}
            installed.set(fence.install(captured,()->account.get().equals("A"),()->visible.set("A history")));
        });
        old.start();assertTrue(prepared.await(2,TimeUnit.SECONDS));
        fence.change(()->{account.set("B");visible.set("");});
        resume.countDown();old.join(2000);
        assertFalse(old.isAlive());assertFalse(installed.get());assertEquals("",visible.get());
    }

    @Test public void logout_serializes_with_install_and_invalidates_its_generation() throws Exception {
        AccountHistoryFence fence=new AccountHistoryFence();AtomicReference<String> state=new AtomicReference<>("A");
        long captured=fence.capture();CountDownLatch entered=new CountDownLatch(1),resume=new CountDownLatch(1);
        Thread install=new Thread(()->fence.install(captured,()->state.get().equals("A"),()->{
            entered.countDown();try {if(!resume.await(2,TimeUnit.SECONDS))throw new AssertionError("timeout");}
            catch(InterruptedException interrupted){throw new AssertionError(interrupted);}
            state.set("A visible");
        }));
        install.start();assertTrue(entered.await(2,TimeUnit.SECONDS));
        Thread logout=new Thread(()->fence.change(()->state.set("signed out")));logout.start();
        resume.countDown();install.join(2000);logout.join(2000);
        assertFalse(install.isAlive());assertFalse(logout.isAlive());assertEquals("signed out",state.get());
        assertFalse(fence.install(captured,()->true,()->state.set("stale")));
    }

    @Test public void old_callback_cannot_reuse_the_same_account_after_logout_and_login() {
        AccountHistoryFence fence=new AccountHistoryFence();AtomicReference<String> account=new AtomicReference<>("A");
        long original=fence.capture();fence.change(()->account.set("B"));fence.change(()->account.set("A"));
        AtomicBoolean cleared=new AtomicBoolean(false);
        assertFalse(fence.install(original,()->account.get().equals("A"),()->cleared.set(true)));
        assertFalse(cleared.get());
    }
}
